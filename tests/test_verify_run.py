"""Ticket #11 verifies a completed run from immutable artifacts."""

import hashlib
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hoya_market_agents.competition_drill import run_fake_competition_drill
from hoya_market_agents.debate_rules import debate_rules
from hoya_market_agents.debate_state_machine import UNANIMOUS_BLIND_PASS
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.run_verifier import (
    RunVerificationError,
    _require_attempt_artifact_path,
    _unreachable_bundle_link,
    _verify_shared_navigation,
    _reject_shipped_fake_markers,
    _verify_attempt_lineage,
    _verify_blind_pass_record,
    _verify_challenge_completed,
    _verify_first_round_lineage,
    _verify_message_timestamps,
    _verify_provider_matrix,
    _verify_real_run_receipts,
    _verify_stop_semantics,
    _verify_tally_enum,
    verify_run,
)
from hoya_market_agents.seats import SEAT_IDS
from tests.fakes import FixedClock, ScriptedTokenSource


QUESTION = "分析 BTC 過去 14 日市場狀態"


class VerifyRunTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        controller = RunController(
            store=RunStore(self.data_root),
            provider=FakeProvider(),
            clock=FixedClock(auto_advance_ms=250),
            token_source=ScriptedTokenSource(["ticket"]),
        )
        self.result = controller.execute(QUESTION)

    def test_valid_run_returns_machine_summary_and_required_artifact_hashes(self):
        summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertEqual(self.result.run_id, summary["run_id"])
        self.assertEqual(7, summary["seat_count"])
        self.assertEqual(
            {"manifest.json", "evidence.jsonl", "debate.jsonl", "votes.json", "report.md", "report.html"},
            set(summary["required_artifacts"]),
        )
        for digest in summary["required_artifacts"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_tampered_artifact_fails_closed(self):
        (self.result.run_dir / "evidence.jsonl").write_text("tampered\n", encoding="utf-8")

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    def test_path_traversal_run_id_is_rejected(self):
        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, "../outside")


class BlindPassStopSemanticsTest(unittest.TestCase):
    """Ticket 03：verify-run 認可盲投直過，也擋得住冒充它的 bundle。

    門檻與時點都由 ``config/debate_rules.json`` 推導：票數是
    ``vote_thresholds.unanimous_blind_pass``，時點是該 run 自己的第一輪牆
    （封存時刻 ＋ ``round_one_window``），和狀態機讀同一份規則。
    """

    QUESTION_TYPE = "single_asset_market_state"

    def setUp(self):
        self.rules = debate_rules()
        self.deadlines = research_deadlines(self.QUESTION_TYPE)
        self.wall_ms = self.rules.challenge_deadline_ms(self.deadlines.seal_ms)
        self.stop_ms = self.deadlines.seal_ms + 30_000

    def votes(self, **overrides):
        value = {
            "tally": {"bullish": len(SEAT_IDS), "bearish": 0, "neutral": 0},
            "adopted_stance": "bullish",
            "threshold_required": self.rules.unanimous_blind_pass_votes,
            "consensus_status": "consensus",
            "valid_vote_count": len(SEAT_IDS),
            "challenge_completed": False,
        }
        value.update(overrides)
        return value

    def verify(self, votes=None, stop_ms=None, deadlines=None):
        # 規則是輸入，不是環境：B2 之後這個參數沒有預設值，忘了傳會是 TypeError。
        _verify_stop_semantics(
            self.votes() if votes is None else votes,
            UNANIMOUS_BLIND_PASS,
            self.stop_ms if stop_ms is None else stop_ms,
            deadlines or self.deadlines,
            self.rules,
        )

    def test_a_blind_pass_inside_the_first_round_window_is_accepted(self):
        self.assertIsNone(self.verify())

    def test_both_ends_of_the_blind_pass_window_are_accepted(self):
        # 正向邊界：封存的那一刻與第一輪牆上都仍是開場階段。
        for stop_ms in (self.deadlines.seal_ms, self.wall_ms):
            with self.subTest(stop_ms=stop_ms):
                self.assertIsNone(self.verify(stop_ms=stop_ms))

    def test_a_stop_outside_the_blind_pass_window_is_refused(self):
        for stop_ms in (self.deadlines.seal_ms - 1, self.wall_ms + 1):
            with self.subTest(stop_ms=stop_ms):
                with self.assertRaisesRegex(RunVerificationError, "盲投直過"):
                    self.verify(stop_ms=stop_ms)

    def test_the_window_follows_the_run_s_own_seal_not_a_fixed_instant(self):
        # 比較題晚 30 秒封存，牆跟著平移；同一個 stop_ms 在兩種題型下結論相反。
        comparison = research_deadlines("two_asset_comparison")
        comparison_wall = self.rules.challenge_deadline_ms(comparison.seal_ms)
        self.assertGreater(comparison_wall, self.wall_ms)

        self.assertIsNone(
            self.verify(stop_ms=comparison_wall, deadlines=comparison)
        )
        with self.assertRaisesRegex(RunVerificationError, "盲投直過"):
            self.verify(stop_ms=comparison_wall)

    def test_a_bundle_that_does_not_match_the_blind_pass_is_refused(self):
        cases = {
            "門檻被改小": {"threshold_required": self.rules.initial_votes},
            "沒有採納立場": {"adopted_stance": None},
            "票數不足門檻": {
                "tally": {"bullish": len(SEAT_IDS) - 1, "bearish": 1, "neutral": 0}
            },
            "有效票少一席": {"valid_vote_count": len(SEAT_IDS) - 1},
            "共識狀態不符": {"consensus_status": "no_consensus"},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(RunVerificationError, "盲投直過"):
                    self.verify(votes=self.votes(**override))


class OrdinaryConsensusStopSemanticsTest(unittest.TestCase):
    """辯論後共識的停止語意：門檻、時點與票數。

    ``challenge_completed`` 刻意不在這裡驗——它的唯一權威是
    ``_verify_challenge_completed``（從公開紀錄重算並要求精確相等）。它是房間
    層級旗標，不是共識成立的必要條件：一席 provider 超時、其餘席位正常完成並
    成立共識是合法形狀，那時旗標就是 False。每一張有效票自己的第一輪則由
    ``_verify_first_round_lineage`` 逐席回查，那才是 §5.4 講的規則。
    """

    QUESTION_TYPE = "single_asset_market_state"

    def setUp(self):
        self.rules = debate_rules()
        self.deadlines = research_deadlines(self.QUESTION_TYPE)
        self.stop_reason = "consensus_{}_votes".format(self.rules.initial_votes)
        self.stop_ms = self.rules.reduced_threshold_from_ms - 1

    def votes(self, **overrides):
        value = {
            "tally": {"bullish": self.rules.initial_votes, "bearish": 1, "neutral": 0},
            "adopted_stance": "bullish",
            "threshold_required": self.rules.initial_votes,
            "consensus_status": "consensus",
            "valid_vote_count": len(SEAT_IDS),
            "challenge_completed": True,
        }
        value.update(overrides)
        return value

    def verify(self, **overrides):
        _verify_stop_semantics(
            self.votes(**overrides),
            self.stop_reason,
            self.stop_ms,
            self.deadlines,
            self.rules,
        )

    def test_both_honest_values_of_the_room_flag_reach_consensus(self):
        # 正反兩側：全員完成質詢是 True，有人掉隊是 False，兩者都成立共識。
        for value in (True, False):
            with self.subTest(challenge_completed=value):
                self.assertIsNone(self.verify(challenge_completed=value))

    def test_a_debated_run_may_not_claim_the_blind_pass_threshold(self):
        # 兩種停止原因的門檻欄位不得互串：辯論後的共識要的是階梯上的那個數字。
        with self.assertRaisesRegex(RunVerificationError, "辯論門檻"):
            self.verify(threshold_required=self.rules.unanimous_blind_pass_votes)


class FirstRoundLineageTest(unittest.TestCase):
    """§5.4 的逐席規則：每一張有效票都要在公開紀錄裡完成自己的第一輪。

    「完成」蘊含順序，所以這裡驗的不只是「三種訊息都出現過」，還包括它們在公
    開紀錄裡的先後與 round／elapsed_ms 是否合法。
    """

    OPENING_MS = 270_000
    FIRST_ROUND_MS = 320_000
    LATER_MS = 480_000

    def messages(self, seat_id, steps):
        return [
            {
                "event": "seat_message",
                "seat_id": seat_id,
                "kind": kind,
                "round": round_number,
                "elapsed_ms": elapsed_ms,
            }
            for kind, round_number, elapsed_ms in steps
        ]

    def complete(self, seat_id):
        return self.messages(
            seat_id,
            (
                ("position", 0, self.OPENING_MS),
                ("challenge", 1, self.FIRST_ROUND_MS),
                ("response", 1, self.FIRST_ROUND_MS),
                ("final_vote", 1, self.FIRST_ROUND_MS),
            ),
        )

    def debate(self, provisional=(), replaced=None):
        record = [{"event": "debate_opened"}]
        for seat_id in SEAT_IDS:
            if seat_id in provisional:
                record += self.messages(
                    seat_id, (("position", 0, self.OPENING_MS),)
                )
            elif replaced is not None and seat_id in replaced:
                record += replaced[seat_id]
            else:
                record += self.complete(seat_id)
        return record

    def votes(self, provisional=()):
        return {
            "votes": [
                {
                    "seat_id": seat_id,
                    "state": "provisional" if seat_id in provisional else "valid",
                }
                for seat_id in SEAT_IDS
            ]
        }

    def refuse(self, steps, seat_id="news"):
        record = self.debate(replaced={seat_id: self.messages(seat_id, steps)})
        with self.assertRaisesRegex(RunVerificationError, seat_id) as caught:
            _verify_first_round_lineage(record, self.votes())
        return str(caught.exception)

    def test_seven_complete_first_rounds_are_accepted(self):
        self.assertIsNone(_verify_first_round_lineage(self.debate(), self.votes()))

    def test_a_seat_left_provisional_does_not_block_the_other_votes(self):
        # B 的實測形狀：一席只發布開場、r1 超時，其餘六席的有效票仍然合法。
        provisional = ("news",)

        self.assertIsNone(
            _verify_first_round_lineage(
                self.debate(provisional), self.votes(provisional)
            )
        )

    def test_a_replacement_finishing_the_vote_in_a_later_attempt_is_accepted(self):
        """§5.2 的替補形狀不得被誤擋：a1 完成第一輪、a2 才投出正式票。"""
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 2, self.LATER_MS),
        )
        record = self.debate(replaced={"news": self.messages("news", steps)})

        self.assertIsNone(_verify_first_round_lineage(record, self.votes()))

    def test_a_valid_vote_without_its_first_round_is_refused(self):
        # 同一份公開紀錄，票表卻宣稱那一席是有效票：這正是 §5.4 禁止的。
        with self.assertRaisesRegex(RunVerificationError, "news"):
            _verify_first_round_lineage(self.debate(("news",)), self.votes())

    def test_each_missing_first_round_step_is_named(self):
        complete = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 1, self.FIRST_ROUND_MS),
        )
        expected = {
            "position": "缺少 round 0 開場",
            "challenge": "缺少 round 1 挑戰",
            "response": "缺少 round 1 回應",
            "final_vote": "缺少 round 1-3 的正式投票",
        }
        for kind, label in expected.items():
            with self.subTest(missing=kind):
                steps = tuple(step for step in complete if step[0] != kind)
                self.assertIn(label, self.refuse(steps))

    def test_a_first_round_message_from_a_later_round_does_not_count(self):
        # 第二輪的挑戰不能補第一輪的缺；狀態機要的是 round 1。
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 2, self.FIRST_ROUND_MS),
            ("response", 2, self.FIRST_ROUND_MS),
            ("final_vote", 2, self.FIRST_ROUND_MS),
        )

        self.assertIn("缺少 round 1 挑戰", self.refuse(steps))

    def test_a_vote_recorded_before_its_own_first_round_is_refused(self):
        """Reviewer B 的形狀：把 final vote 移到 challenge／response 之前。

        訊息集合完全沒變，只有順序變了——只看「三種訊息都有」的檢查抓不到。
        """
        steps = (
            ("position", 0, self.OPENING_MS),
            ("final_vote", 1, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
        )

        self.assertIn("正式投票排在它所依據的第一輪之前", self.refuse(steps))

    def test_a_first_round_message_stamped_before_the_opening_is_refused(self):
        """Reviewer A 的形狀：紀錄順序原封不動，只把 challenge 的時間戳前移。

        只比 final vote 與前置訊息的時間戳抓不到這一種——position 與
        challenge／response 之間本來沒有人在比。
        """
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.OPENING_MS - 1),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 1, self.FIRST_ROUND_MS),
        )

        self.assertIn("elapsed_ms 早於該席開場", self.refuse(steps))

    def test_a_response_stamped_before_the_opening_is_refused(self):
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.OPENING_MS - 1),
            ("final_vote", 1, self.FIRST_ROUND_MS),
        )

        self.assertIn("elapsed_ms 早於該席開場", self.refuse(steps))

    def test_a_first_round_message_stamped_with_the_opening_is_accepted(self):
        # 正向邊界：同一毫秒不算「早於」，狀態機本來就可能同批寫入。
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.OPENING_MS),
            ("response", 1, self.OPENING_MS),
            ("final_vote", 1, self.OPENING_MS),
        )
        record = self.debate(replaced={"news": self.messages("news", steps)})

        self.assertIsNone(_verify_first_round_lineage(record, self.votes()))

    def test_an_extra_challenge_before_the_opening_does_not_hide_a_valid_one(self):
        """第 3 輪自陳缺的 S11 回歸：一則 challenge 在開場前、另一則在後。

        檢查取的是「最早的 round 1 challenge」，所以前面那一則就是被檢查的那
        一則——這正是它該擋下來的。
        """
        steps = (
            ("challenge", 1, self.OPENING_MS - 1),
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 1, self.FIRST_ROUND_MS),
        )

        self.assertIn("第一輪訊息排在該席開場之前", self.refuse(steps))

    def test_a_vote_stamped_before_its_own_first_round_is_refused(self):
        # 順序對、時間戳不對：時鐘是單調的，票不可能早於它所依據的挑戰。
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 1, self.OPENING_MS),
        )

        self.assertIn("elapsed_ms 早於", self.refuse(steps))

    def test_a_round_zero_final_vote_is_refused(self):
        # Reviewer B 的第二種形狀：把正式票改成 round 0。
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", 1, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 0, self.LATER_MS),
        )

        self.assertIn("缺少 round 1-3 的正式投票", self.refuse(steps))

    def test_a_boolean_round_does_not_impersonate_round_one(self):
        # Python 的 True == 1；回合比對必須用精確型別，否則 true 會冒充第一輪。
        steps = (
            ("position", 0, self.OPENING_MS),
            ("challenge", True, self.FIRST_ROUND_MS),
            ("response", 1, self.FIRST_ROUND_MS),
            ("final_vote", 1, self.FIRST_ROUND_MS),
        )

        self.assertIn("缺少 round 1 挑戰", self.refuse(steps))


class MessageTimestampTest(unittest.TestCase):
    """公開訊息的時間戳必須落在該場的辯論時間窗內。

    狀態機讀單調時鐘、辯論室封存前開不了、停止後不再收件，所以每一則發言的
    ``elapsed_ms`` 必然在 ``[seal, stop]`` 之間。負值是最明顯的違反者——兩位
    Reviewer 都用它打穿了只比相對順序的檢查。
    """

    SEAL_MS = 240_000
    STOP_MS = 600_000

    def debate(self, elapsed):
        return [
            {"event": "debate_opened"},
            {
                "event": "seat_message",
                "seat_id": SEAT_IDS[0],
                "kind": "position",
                "elapsed_ms": elapsed,
            },
        ]

    def verify(self, elapsed):
        return _verify_message_timestamps(
            self.debate(elapsed), self.SEAL_MS, self.STOP_MS
        )

    def test_both_ends_of_the_debate_window_are_accepted(self):
        for elapsed in (self.SEAL_MS, self.STOP_MS):
            with self.subTest(elapsed_ms=elapsed):
                self.assertIsNone(self.verify(elapsed))

    def test_a_negative_timestamp_is_refused_without_any_timeline(self):
        """V3：非負整數是**所有**可驗證 run 的共同底線，不需要 timeline。

        兩位 Reviewer 的形狀：整席改成 -4/-3/-2/-1，相對順序維持不變。
        """
        for elapsed in (-1, -4, -600_000):
            with self.subTest(elapsed_ms=elapsed):
                with self.assertRaisesRegex(RunVerificationError, "非負整數毫秒"):
                    _verify_message_timestamps(self.debate(elapsed))
                with self.assertRaisesRegex(RunVerificationError, "非負整數毫秒"):
                    self.verify(elapsed)

    def test_a_non_integer_timestamp_is_refused_without_any_timeline(self):
        for elapsed in (None, "270000", 270_000.0, True):
            with self.subTest(elapsed_ms=elapsed):
                with self.assertRaisesRegex(RunVerificationError, "非負整數毫秒"):
                    _verify_message_timestamps(self.debate(elapsed))

    def test_a_zero_timestamp_is_accepted_without_a_timeline(self):
        # 正向邊界：沒有 timeline 時 0 是合法的（下界就是 0）。
        self.assertIsNone(_verify_message_timestamps(self.debate(0)))

    def test_a_timestamp_outside_the_window_is_refused(self):
        # 有 timeline 時才追加範圍檢查；這兩個值都是非負的，只違反窗。
        for elapsed in (self.SEAL_MS - 1, self.STOP_MS + 1):
            with self.subTest(elapsed_ms=elapsed):
                self.assertIsNone(_verify_message_timestamps(self.debate(elapsed)))
                with self.assertRaisesRegex(RunVerificationError, "辯論時間窗"):
                    self.verify(elapsed)

    def test_bookkeeping_entries_carry_no_timestamp_requirement(self):
        # debate_opened／debate_stopped 不是席位發言，不在這條規則的範圍內。
        record = [{"event": "debate_opened"}, {"event": "debate_stopped"}]

        self.assertIsNone(
            _verify_message_timestamps(record, self.SEAL_MS, self.STOP_MS)
        )


def _mirror_row(field, value):
    """Mirror one emptied opening field onto the vote row it is compared with."""
    return {
        "message_id": {"message_ids": [value]},
        "content_sha256": {"content_sha256": [value]},
        "stance": {"initial_stance": value, "final_stance": value},
        "public_reason": {
            "initial_public_reason": value,
            "final_public_reason": value,
        },
    }[field]


class AttemptLineageTest(unittest.TestCase):
    """票表的 attempt 清單必須等於公開紀錄重算出來的清單。

    兩個方向都要成立：幽靈 attempt 被拒，而 §5.2 的合法替補（a1 完成第一輪、
    a2 才投出正式票）仍然被接受。限制成單一 attempt 是錯的，完全不驗也是錯的。
    """

    def message(self, seat_id, kind, attempt_id):
        return {
            "event": "seat_message",
            "seat_id": seat_id,
            "kind": kind,
            "attempt_id": attempt_id,
        }

    def chain(self, record):
        """Stamp a public history chain and make every replay event quote it."""
        for index, entry in enumerate(record):
            entry.setdefault("public_history_sha256", "history-{}".format(index))
        for index, entry in enumerate(record):
            if entry.get("event") == "replacement_replayed_public_history":
                entry["replayed_history_sha256"] = record[index - 1][
                    "public_history_sha256"
                ]
        return record

    def replay_event(self, seat_id, number):
        return {
            "event": "replacement_replayed_public_history",
            "seat_id": seat_id,
            "attempt_id": "{}-a{}".format(seat_id, number),
            "replaced_attempt_id": "{}-a{}".format(seat_id, number - 1),
        }

    def debate(self, extra=()):
        record = [{"event": "debate_opened"}]
        for seat_id in SEAT_IDS:
            attempt = "{}-a1".format(seat_id)
            record += [
                self.message(seat_id, kind, attempt)
                for kind in ("position", "challenge", "response", "final_vote")
            ]
        return self.chain(record + list(extra))

    def replacement_record(self, seat, number=2, event=True, event_last=False):
        """A room where one seat really was replaced: a1 debates, a{n} votes."""
        record = [{"event": "debate_opened"}]
        for seat_id in SEAT_IDS:
            attempt = "{}-a1".format(seat_id)
            record += [
                self.message(seat_id, kind, attempt)
                for kind in ("position", "challenge", "response")
            ]
            if seat_id != seat:
                record.append(self.message(seat_id, "final_vote", attempt))
                continue
            vote = self.message(
                seat_id, "final_vote", "{}-a{}".format(seat_id, number)
            )
            if event and not event_last:
                record.append(self.replay_event(seat_id, number))
            record.append(vote)
            if event and event_last:
                record.append(self.replay_event(seat_id, number))
        return self.chain(record)

    def votes(self, overrides=None):
        overrides = overrides or {}
        return {
            "votes": [
                {
                    "seat_id": seat_id,
                    "attempt_ids": overrides.get(
                        seat_id, ["{}-a1".format(seat_id)]
                    ),
                }
                for seat_id in SEAT_IDS
            ]
        }

    def test_a_single_attempt_per_seat_is_accepted(self):
        self.assertIsNone(_verify_attempt_lineage(self.debate(), self.votes()))

    def test_a_replacement_that_really_spoke_is_accepted(self):
        """§5.2：a1 完成第一輪、a2 投出正式票——``[a1, a2]`` 必須通過。"""
        seat = SEAT_IDS[0]
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        self.assertIsNone(
            _verify_attempt_lineage(self.replacement_record(seat), votes)
        )

    def test_a_new_attempt_without_its_replay_event_is_refused(self):
        """Reviewer A 的方向①：有 a2 訊息、沒有 replacement replay event。"""
        seat = SEAT_IDS[0]
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "未一一對應"):
            _verify_attempt_lineage(
                self.replacement_record(seat, event=False), votes
            )

    def test_a_replay_event_without_a_new_attempt_is_refused(self):
        """Reviewer A 的方向②：孤立的 replay event，沒有任何新 attempt 訊息。"""
        seat = SEAT_IDS[0]
        record = self.chain(
            self.debate() + [self.replay_event(seat, 2)]
        )

        with self.assertRaisesRegex(RunVerificationError, "未一一對應"):
            _verify_attempt_lineage(record, self.votes())

    def test_a_replay_event_after_its_own_first_message_is_refused(self):
        seat = SEAT_IDS[0]
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "未緊鄰它自己的第一則訊息"):
            _verify_attempt_lineage(
                self.replacement_record(seat, event_last=True), votes
            )

    def test_an_entry_wedged_between_the_event_and_its_message_is_refused(self):
        """Reviewer A 的形狀：事件與新 attempt 首則訊息之間插入別席發言。

        狀態機是同步且連續地寫這兩筆，中間沒有插入視窗；只驗「事件比較早」擋
        不住這一種。
        """
        seat = SEAT_IDS[0]
        record = self.replacement_record(seat)
        event_index = next(
            index
            for index, entry in enumerate(record)
            if entry.get("event") == "replacement_replayed_public_history"
        )
        record.insert(
            event_index + 1,
            self.message(SEAT_IDS[-1], "final_vote", "{}-a1".format(SEAT_IDS[-1])),
        )
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "未緊鄰它自己的第一則訊息"):
            _verify_attempt_lineage(self.chain(record), votes)

    def test_a_replay_event_naming_the_wrong_predecessor_is_refused(self):
        seat = SEAT_IDS[0]
        record = self.replacement_record(seat)
        for entry in record:
            if entry.get("event") == "replacement_replayed_public_history":
                entry["replaced_attempt_id"] = "{}-a9".format(seat)
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "實際前一個 attempt"):
            _verify_attempt_lineage(record, votes)

    def test_a_replay_event_that_did_not_quote_the_history_is_refused(self):
        seat = SEAT_IDS[0]
        record = self.replacement_record(seat)
        for entry in record:
            if entry.get("event") == "replacement_replayed_public_history":
                entry["replayed_history_sha256"] = "0" * 64
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "未重播當下的完整公開歷史"):
            _verify_attempt_lineage(record, votes)

    def test_an_attempt_sequence_that_does_not_start_at_a1_is_refused(self):
        """Reviewer B 的方向①：第一則開場就用 a2。"""
        seat = SEAT_IDS[0]
        record = self.debate()
        for entry in record:
            if entry.get("seat_id") == seat:
                entry["attempt_id"] = "{}-a2".format(seat)
        votes = self.votes({seat: ["{}-a2".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "第 1 個 attempt 應為"):
            _verify_attempt_lineage(record, votes)

    def test_every_near_miss_spelling_of_the_canonical_name_is_refused(self):
        """編號規則不留任何命名體系的豁免。

        先前只比對「長得像 canonical 的名字」，兩位 Reviewer 證明那只擋住特定
        拼字：改一個字元就重新接受狀態機不可能產生的 lineage。這裡把他們造出
        的每一種近似拼法都釘住。
        """
        seat = SEAT_IDS[0]
        others = SEAT_IDS[1]
        for attempt_id in (
            "{}-a2".format(seat),
            "{}-a02".format(seat),
            "{}-a01".format(seat),
            "{}-b2".format(seat),
            "{}-A2".format(seat),
            "{}-a1x".format(seat),
            "{}-receipt-attempt-99".format(seat),
            "Spot-technical-a1",
            "{}-a1".format(others),
        ):
            with self.subTest(attempt_id=attempt_id):
                record = self.debate()
                for entry in record:
                    if entry.get("seat_id") == seat:
                        entry["attempt_id"] = attempt_id
                with self.assertRaisesRegex(
                    RunVerificationError, "第 1 個 attempt 應為"
                ):
                    _verify_attempt_lineage(record, self.votes({seat: [attempt_id]}))

    def test_an_attempt_sequence_that_skips_a_number_is_refused(self):
        """Reviewer B 的方向②：a1 之後直接跳到 a3。"""
        seat = SEAT_IDS[0]
        record = self.replacement_record(seat, number=3)
        votes = self.votes({seat: ["{}-a1".format(seat), "{}-a3".format(seat)]})

        with self.assertRaisesRegex(RunVerificationError, "第 2 個 attempt 應為"):
            _verify_attempt_lineage(record, votes)

    def test_an_attempt_the_public_record_never_saw_is_refused(self):
        """兩位 Reviewer 各自造出的形狀：票表追加一個幽靈 attempt。"""
        seat = SEAT_IDS[0]
        votes = self.votes(
            {seat: ["{}-a1".format(seat), "{}-a2-phantom".format(seat)]}
        )

        with self.assertRaisesRegex(RunVerificationError, "attempt lineage"):
            _verify_attempt_lineage(self.debate(), votes)

    def test_a_duplicated_reordered_or_missing_attempt_is_refused(self):
        seat = SEAT_IDS[0]
        cases = {
            "重複": ["{}-a1".format(seat)] * 2,
            "空清單": [],
            "換成別席的 attempt": ["{}-a1".format(SEAT_IDS[1])],
        }
        for label, attempt_ids in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(RunVerificationError, "attempt lineage"):
                    _verify_attempt_lineage(
                        self.debate(), self.votes({seat: attempt_ids})
                    )

    def test_the_recomputed_order_is_first_appearance_order(self):
        # 順序也釘住：先出現的 attempt 排前面，票表倒過來寫就不算相等。
        seat = SEAT_IDS[0]
        record = self.replacement_record(seat)
        forward = ["{}-a1".format(seat), "{}-a2".format(seat)]

        self.assertIsNone(
            _verify_attempt_lineage(record, self.votes({seat: forward}))
        )
        with self.assertRaisesRegex(RunVerificationError, "票表"):
            _verify_attempt_lineage(
                record, self.votes({seat: list(reversed(forward))})
            )

    def test_a_public_message_without_a_usable_attempt_id_is_refused(self):
        """``[None] == [None]`` 不得成立：狀態機禁止空的或非字串 attempt。"""
        for attempt_id in (None, "", "   ", 1):
            with self.subTest(attempt_id=attempt_id):
                seat = SEAT_IDS[0]
                record = self.debate()
                for entry in record:
                    if entry.get("seat_id") == seat:
                        entry["attempt_id"] = attempt_id
                with self.assertRaisesRegex(
                    RunVerificationError, "非空字串 attempt_id"
                ):
                    _verify_attempt_lineage(record, self.votes({seat: [attempt_id]}))


class ChallengeCompletedTest(unittest.TestCase):
    """房間層級旗標必須由公開紀錄重算出來，不能由 bundle 自己宣告。"""

    def messages(self, seat_id, kinds):
        return [
            {"event": "seat_message", "seat_id": seat_id, "kind": kind}
            for kind in kinds
        ]

    def debate(self, incomplete=(), silent=()):
        record = [{"event": "debate_opened"}]
        for seat_id in SEAT_IDS:
            if seat_id in silent:
                continue
            if seat_id in incomplete:
                record += self.messages(seat_id, ("position",))
            else:
                record += self.messages(
                    seat_id, ("position", "challenge", "response", "final_vote")
                )
        return record

    def test_a_fully_challenged_room_recomputes_to_true(self):
        self.assertIsNone(
            _verify_challenge_completed(self.debate(), {"challenge_completed": True})
        )

    def test_a_room_with_one_seat_short_recomputes_to_false(self):
        self.assertIsNone(
            _verify_challenge_completed(
                self.debate(incomplete=("news",)), {"challenge_completed": False}
            )
        )

    def test_a_debated_room_may_not_claim_it_never_challenged(self):
        """Reviewer A 的第一個方向：真的辯論過卻謊稱 False。"""
        with self.assertRaisesRegex(RunVerificationError, "challenge_completed"):
            _verify_challenge_completed(self.debate(), {"challenge_completed": False})

    def test_a_room_with_a_dropout_may_not_claim_a_complete_challenge_round(self):
        """Reviewer A 的第二個方向：一席掉隊卻謊稱 True。"""
        with self.assertRaisesRegex(RunVerificationError, "challenge_completed"):
            _verify_challenge_completed(
                self.debate(incomplete=("news",)), {"challenge_completed": True}
            )

    def test_a_blind_pass_record_recomputes_to_false(self):
        # 直過只有七則開場，沒有任何質詢，重算必然是 False。
        record = [{"event": "debate_opened"}] + [
            {"event": "seat_message", "seat_id": seat_id, "kind": "position"}
            for seat_id in SEAT_IDS
        ]

        self.assertIsNone(
            _verify_challenge_completed(record, {"challenge_completed": False})
        )
        with self.assertRaisesRegex(RunVerificationError, "challenge_completed"):
            _verify_challenge_completed(record, {"challenge_completed": True})

    def test_a_non_boolean_flag_is_refused(self):
        for value in (None, "true", 1, 0):
            with self.subTest(challenge_completed=value):
                with self.assertRaisesRegex(
                    RunVerificationError, "challenge_completed"
                ):
                    _verify_challenge_completed(
                        self.debate(), {"challenge_completed": value}
                    )

    def test_a_room_where_nobody_opened_recomputes_to_false(self):
        # 對齊狀態機的 ``bool(participants) and ...``：沒有參與者就不是完成。
        self.assertIsNone(
            _verify_challenge_completed(
                [{"event": "debate_opened"}], {"challenge_completed": False}
            )
        )


class TallyEnumTest(unittest.TestCase):
    """計票欄位必須恰好是該場自己記錄的立場 enum。"""

    STANCES = ["bullish", "bearish", "neutral"]

    def votes(self, **overrides):
        value = {
            "stances": list(self.STANCES),
            "tally": {stance: 0 for stance in self.STANCES},
        }
        value.update(overrides)
        return value

    def test_a_tally_matching_the_enum_is_accepted(self):
        self.assertIsNone(_verify_tally_enum(self.votes()))

    def test_a_fabricated_extra_column_is_refused(self):
        """Reviewer B 的 [建議]：多一個永遠是 0 的立場，重算照樣相等。"""
        tally = {stance: 0 for stance in self.STANCES}
        tally["fabricated"] = 0

        with self.assertRaisesRegex(RunVerificationError, "立場 enum"):
            _verify_tally_enum(self.votes(tally=tally))

    def test_a_missing_column_is_refused(self):
        with self.assertRaisesRegex(RunVerificationError, "立場 enum"):
            _verify_tally_enum(self.votes(tally={"bullish": 0, "bearish": 0}))

    def test_a_malformed_or_missing_enum_is_refused(self):
        cases = {
            "缺少 stances": {"stances": None},
            "空 enum": {"stances": [], "tally": {}},
            "重複立場": {"stances": ["bullish", "bullish", "bearish", "neutral"]},
            "tally 不是 object": {"tally": ["bullish"]},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(RunVerificationError, "立場 enum"):
                    _verify_tally_enum(self.votes(**override))


class BlindPassRecordTest(unittest.TestCase):
    """直過的公開紀錄必須恰好是七則開場，而且那七則開場就是七張正式票。

    空辯論不只是被允許，而是必要條件；「開場即最終票」也不能只靠實作自律，
    verifier 必須把兩份 artifact 對起來。
    """

    STANCE = "bullish"

    def opening(self, seat_id, **overrides):
        value = {
            "event": "seat_message",
            "seat_id": seat_id,
            "kind": "position",
            "round": 0,
            "attempt_id": "{}-a1".format(seat_id),
            "message_id": "{}-position".format(seat_id),
            "content_sha256": "sha-{}".format(seat_id),
            "stance": self.STANCE,
            "public_reason": "本席公開的開場理由：{}".format(seat_id),
            "evidence_ids": ["{}-01".format(seat_id)],
        }
        value.update(overrides)
        return value

    def openings(self):
        return [self.opening(seat_id) for seat_id in SEAT_IDS]

    def row(self, seat_id, **overrides):
        opening = self.opening(seat_id)
        value = {
            "seat_id": seat_id,
            "state": "valid",
            "attempt_ids": [opening["attempt_id"]],
            "initial_stance": opening["stance"],
            "final_stance": opening["stance"],
            "initial_public_reason": opening["public_reason"],
            "final_public_reason": opening["public_reason"],
            "initial_evidence_ids": list(opening["evidence_ids"]),
            "final_evidence_ids": list(opening["evidence_ids"]),
            "stance_changed": False,
            "stance_change_reason": None,
            "vote_changes": [],
            "message_ids": [opening["message_id"]],
            "content_sha256": [opening["content_sha256"]],
        }
        value.update(overrides)
        return value

    STANCES = ("bullish", "bearish", "neutral")

    def votes(self, rows=None, tally=None, stances=None):
        rows = self.rows() if rows is None else rows
        counts = {stance: 0 for stance in self.STANCES}
        for row in rows:
            # enum 以外的立場不計入：偽造案例會刻意寫進不存在的立場。
            if row["final_stance"] in counts:
                counts[row["final_stance"]] += 1
        return {
            "votes": rows,
            "tally": counts if tally is None else tally,
            "stances": list(self.STANCES if stances is None else stances),
        }

    def rows(self):
        return [self.row(seat_id) for seat_id in SEAT_IDS]

    def verify(self, debate=None, votes=None):
        return _verify_blind_pass_record(
            self.openings() if debate is None else debate,
            self.votes() if votes is None else votes,
        )

    def test_seven_openings_matching_the_vote_table_are_accepted(self):
        self.assertIsNone(self.verify())

    def test_bookkeeping_entries_beside_the_openings_are_ignored(self):
        # debate.jsonl 本來就含 debate_opened／debate_stopped，那些不是席位發言。
        record = (
            [{"event": "debate_opened"}]
            + self.openings()
            + [{"event": "debate_stopped", "stop_reason": UNANIMOUS_BLIND_PASS}]
        )

        self.assertIsNone(self.verify(debate=record))

    def test_any_debate_message_beside_the_openings_is_refused(self):
        for kind in ("challenge", "response", "final_vote"):
            with self.subTest(kind=kind):
                record = self.openings() + [
                    self.opening(SEAT_IDS[0], kind=kind, message_id="extra")
                ]
                with self.assertRaisesRegex(RunVerificationError, "七則開場"):
                    self.verify(debate=record)

    def test_a_debate_message_standing_in_for_an_opening_is_refused(self):
        # 同樣是七則席位發言，但其中一則不是開場：只數數量擋不住，要看種類。
        record = self.openings()[:-1] + [
            self.opening(SEAT_IDS[-1], kind="final_vote")
        ]

        with self.assertRaisesRegex(RunVerificationError, "七則開場"):
            self.verify(debate=record)

    def test_a_missing_or_duplicated_opening_is_refused(self):
        cases = {
            "少一則開場": self.openings()[:-1],
            "多一則開場": self.openings() + [self.opening(SEAT_IDS[0])],
        }
        for label, record in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(RunVerificationError, "七則開場"):
                    self.verify(debate=record)

    def test_a_duplicated_opening_covering_for_a_missing_seat_is_refused(self):
        # 七則、全部是開場，數量與種類都對——但其中一席交了兩次、另一席一次
        # 都沒有。只數數量與看種類都擋不住，要釘住「就是這七席」。
        record = self.openings()[:-1] + [
            self.opening(SEAT_IDS[0], message_id="{}-position-2".format(SEAT_IDS[0]))
        ]
        self.assertEqual(len(SEAT_IDS), len(record))

        with self.assertRaisesRegex(RunVerificationError, "七則開場"):
            self.verify(debate=record)

    def test_an_opening_stance_the_vote_table_contradicts_is_refused(self):
        """兩位 Reviewer 各自造出的偽造 bundle：開場投反方、票表記正方。

        只驗「七則開場」擋不住這一種——它的公開紀錄形狀完全合法，說謊的是兩
        份 artifact 之間的關係。
        """
        forged = self.openings()
        forged[-1] = self.opening(SEAT_IDS[-1], stance="bearish")

        # 錯誤訊息指名第一個對不上的欄位：開場立場改了，initial_stance 先炸。
        with self.assertRaisesRegex(
            RunVerificationError, "{}.*initial_stance".format(SEAT_IDS[-1])
        ):
            self.verify(debate=forged)

    def test_every_field_of_the_opening_must_match_its_official_vote(self):
        """逐欄釘住：開場原文的每一個欄位都要在票表上**原樣**出現。

        ``attempt_ids`` 不在這裡——它的唯一權威是 ``_verify_attempt_lineage``
        （見 :class:`AttemptLineageTest`），因為那條規則對直過與普通辯論是同
        一條：票表的 attempt 清單必須等於公開紀錄重算出來的清單。
        """
        seat = SEAT_IDS[0]
        cases = [
            ("state", "state", {"state": "provisional"}),
            ("initial_stance", "initial_stance", {"initial_stance": "bearish"}),
            ("final_stance", "final_stance", {"final_stance": "bearish"}),
            (
                "initial_public_reason",
                "initial_public_reason",
                {"initial_public_reason": "改寫過的理由"},
            ),
            (
                "final_public_reason",
                "final_public_reason",
                {"final_public_reason": "改寫過的理由"},
            ),
            (
                "initial_evidence_ids",
                "initial_evidence_ids",
                {"initial_evidence_ids": ["news-01"]},
            ),
            (
                "final_evidence_ids",
                "final_evidence_ids",
                {"final_evidence_ids": ["news-01"]},
            ),
            ("stance_changed", "stance_changed", {"stance_changed": True}),
            (
                "stance_change_reason",
                "stance_change_reason",
                {"stance_change_reason": "無中生有的改票原因"},
            ),
            ("vote_changes", "vote_changes", {"vote_changes": [{"before": "bullish"}]}),
            ("message_ids", "message_ids", {"message_ids": ["news-position"]}),
            (
                "message_ids 追加",
                "message_ids",
                {"message_ids": ["{}-position".format(seat), "phantom"]},
            ),
            ("content_sha256", "content_sha256", {"content_sha256": ["sha-news"]}),
            (
                "content_sha256 追加",
                "content_sha256",
                {"content_sha256": ["sha-{}".format(seat), "sha-phantom"]},
            ),
        ]
        for label, field, override in cases:
            with self.subTest(label=label):
                rows = self.rows()
                rows[0] = self.row(seat, **override)
                # tally 跟著票表走，好讓失敗確實來自逐欄比對而不是票數。
                with self.assertRaisesRegex(
                    RunVerificationError, "{}.*{}".format(seat, field)
                ):
                    self.verify(votes=self.votes(rows=rows))

    def test_an_opening_missing_its_own_identity_fields_is_refused(self):
        """``[None] == [None]`` 不得成立：殘缺的開場不能替自己背書。

        兩位 Reviewer 用 ``attempt_id`` 打穿的是同一個根因，所以這裡把開場自
        己的每一個識別欄位都掃過一遍，不只修被點名的那一個。
        """
        fields = ("message_id", "content_sha256", "stance", "public_reason")
        for field in fields:
            for empty in (None, "", "   "):
                with self.subTest(field=field, value=empty):
                    forged = self.openings()
                    forged[0] = self.opening(SEAT_IDS[0], **{field: empty})
                    rows = self.rows()
                    # 票列也一起改成同樣的空值，模擬「兩邊都缺」的相等陷阱。
                    rows[0] = self.row(SEAT_IDS[0], **_mirror_row(field, empty))
                    with self.assertRaisesRegex(
                        RunVerificationError, "開場的 {}".format(field)
                    ):
                        self.verify(debate=forged, votes=self.votes(rows=rows))

    def test_an_opening_without_usable_evidence_ids_is_refused(self):
        for value in (None, [], ["  "], [None], "ev-01"):
            with self.subTest(evidence_ids=value):
                forged = self.openings()
                forged[0] = self.opening(SEAT_IDS[0], evidence_ids=value)
                rows = self.rows()
                rows[0] = self.row(
                    SEAT_IDS[0],
                    initial_evidence_ids=value,
                    final_evidence_ids=value,
                )
                with self.assertRaisesRegex(
                    RunVerificationError, "開場的 evidence_ids"
                ):
                    self.verify(debate=forged, votes=self.votes(rows=rows))

    def test_a_tally_that_does_not_match_the_openings_is_refused(self):
        # 逐席欄位全部對得上，但 tally 被改大：由開場重算的票數要擋下它。
        votes = self.votes(tally={"bullish": 7, "bearish": 1, "neutral": 0})

        with self.assertRaisesRegex(RunVerificationError, "重算的票數"):
            self.verify(votes=votes)

    def test_an_opening_stance_outside_the_tally_is_refused(self):
        # 開場與票列一起改成同一個不存在的立場，好讓逐欄比對過關，真正被擋下
        # 來的是「這個立場不在本場的 enum 裡」。
        forged = self.openings()
        forged[0] = self.opening(SEAT_IDS[0], stance="unknown_stance")
        rows = self.rows()
        rows[0] = self.row(
            SEAT_IDS[0],
            initial_stance="unknown_stance",
            final_stance="unknown_stance",
        )

        with self.assertRaisesRegex(RunVerificationError, "不在 tally"):
            self.verify(debate=forged, votes=self.votes(rows=rows))


class ProviderMatrixVerificationTest(unittest.TestCase):
    def setUp(self):
        config_path = Path(__file__).parents[1] / "config" / "agent_roster.json"
        self.roster = json.loads(config_path.read_text(encoding="utf-8"))
        self.matrix = [{
            "seat_id": "core",
            "provider": "codex",
            "target_model": "gpt-5.6-sol",
            "actual_model": "gpt-5.6-sol",
            "model_confirmation_source": "operator_ui",
        }] + [
            {
                "seat_id": seat["seat_id"],
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": seat["target_model"],
            }
            for seat in self.roster["seats"]
        ]

    def test_operator_ui_core_model_confirmation_is_valid(self):
        self.assertEqual(7, len(_verify_provider_matrix(self.matrix, self.roster)))

    def test_unknown_core_model_confirmation_source_is_rejected(self):
        self.matrix[0]["model_confirmation_source"] = "prompt"

        with self.assertRaises(RunVerificationError):
            _verify_provider_matrix(self.matrix, self.roster)


class RealReceiptVerificationTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.run_dir = Path(self._temporary.name) / "runs" / "authorized-run"
        self.run_dir.mkdir(parents=True)
        self.started = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)
        self.challenge = "competition-challenge-1234567890"
        self.artifact_index = {}
        self.evidence = []
        self.debate = []
        self.votes = {"votes": []}
        providers = {
            "spot-technical": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "derivatives": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "onchain": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "official-events": ("claude", "opus", "claude-opus-5"),
            "news": ("claude", "opus", "claude-opus-5"),
            "social-macro": ("claude", "opus", "claude-opus-5"),
            "counter-evidence": (
                "antigravity",
                "gemini-3.1-pro-high",
                "gemini-3.1-pro-high",
            ),
        }
        self.manifest = {
            "run_id": "authorized-run",
            "started_at_utc": self._utc(0),
            "seats": [],
            "provider_receipts": [],
        }
        self.timeline = {"seat_completion_ms": {}}
        self.authorization = {
            "system_preflight_id": "provider-preflight-1",
            "run_id": "authorized-run",
            "competition_challenge": self.challenge,
        }
        self.receipts = {}
        for index, (seat_id, models) in enumerate(providers.items(), start=1):
            provider, target, actual = models
            attempt_id = "{}-attempt-1".format(seat_id)
            completion_ms = index * 1_000
            search_ms = completion_ms - 250
            evidence_record = {
                "schema_version": "1.0.0",
                "evidence_id": "{}-evidence-1".format(seat_id),
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "statement": "public evidence {}".format(index),
            }
            self.evidence.append(evidence_record)
            self.debate.append({
                "event": "seat_message",
                "seat_id": seat_id,
                "attempt_id": attempt_id,
            })
            self.votes["votes"].append({
                "seat_id": seat_id,
                "attempt_ids": [attempt_id],
            })
            self.timeline["seat_completion_ms"][seat_id] = completion_ms
            self.manifest["seats"].append({
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "target_model": target,
                "actual_model": actual,
            })
            attempt_root = "agents/{}/attempts/{}/".format(seat_id, attempt_id)
            raw_path = attempt_root + "public-transcript.jsonl"
            output_path = attempt_root + "structured-output.json"
            search_path = attempt_root + "search-receipt.json"
            self._write_artifact(raw_path, '{"public":"transcript"}\n')
            self._write_artifact(
                output_path,
                json.dumps([evidence_record]) + "\n",
            )
            search = {
                "schema_version": "1.0.0",
                "receipt_id": "search-{}".format(seat_id),
                "run_id": "authorized-run",
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "competition_challenge": self.challenge,
                "tool": {
                    "codex": "web_search",
                    "claude": "WebSearch",
                    "antigravity": "search_web",
                }[provider],
                "succeeded": True,
                "completed_at_utc": self._utc(search_ms),
                "elapsed_ms": search_ms,
            }
            self._write_artifact(search_path, json.dumps(search) + "\n")
            receipt = {
                "schema_version": "1.0.0",
                "receipt_id": "provider-{}".format(seat_id),
                "system_preflight_id": "provider-preflight-1",
                "run_id": "authorized-run",
                "competition_challenge": self.challenge,
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "target_model": target,
                "actual_model": actual,
                "dispatch": {
                    "receipt_id": "dispatch-{}".format(seat_id),
                    "at_utc": self._utc(0),
                    "elapsed_ms": 0,
                },
                "completion": {
                    "receipt_id": "completion-{}".format(seat_id),
                    "at_utc": self._utc(completion_ms),
                    "elapsed_ms": completion_ms,
                },
                "search_receipt_path": search_path,
                "search_receipt_sha256": self.artifact_index[search_path]["sha256"],
                "raw_transcript_path": raw_path,
                "raw_transcript_sha256": self.artifact_index[raw_path]["sha256"],
                "output_path": output_path,
                "output_sha256": self.artifact_index[output_path]["sha256"],
            }
            receipt_path = "provider-receipts/{}.json".format(seat_id)
            self._write_artifact(receipt_path, json.dumps(receipt) + "\n")
            self.receipts[seat_id] = (receipt_path, receipt)
            self.manifest["provider_receipts"].append({
                "seat_id": seat_id,
                "path": receipt_path,
                "sha256": self.artifact_index[receipt_path]["sha256"],
            })

    def _utc(self, elapsed_ms):
        value = self.started + timedelta(milliseconds=elapsed_ms)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _write_artifact(self, relative, content):
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        path.write_bytes(data)
        self.artifact_index[relative] = {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _rewrite_receipt(self, seat_id):
        relative, receipt = self.receipts[seat_id]
        self._write_artifact(relative, json.dumps(receipt) + "\n")
        next(item for item in self.manifest["provider_receipts"] if item["seat_id"] == seat_id)[
            "sha256"
        ] = self.artifact_index[relative]["sha256"]

    def verify(self):
        return _verify_real_run_receipts(
            self.run_dir,
            self.manifest,
            self.artifact_index,
            self.timeline,
            self.evidence,
            self.debate,
            self.votes,
            self.authorization,
        )

    def test_exact_seven_run_scoped_receipts_pass(self):
        self.assertEqual(28, len(self.verify()))

    def test_shipped_fake_markers_are_rejected_after_receipt_validation(self):
        with self.assertRaises(RunVerificationError):
            _reject_shipped_fake_markers(
                {"provider_mode": "real-subscription"},
                ["https://fake.invalid/source"],
            )

        _reject_shipped_fake_markers(
            {"provider_mode": "real-subscription"},
            ["https://example.com/live-public-evidence"],
        )

    def test_receipt_payload_cannot_escape_its_attempt_directory(self):
        root = "agents/news/attempts/news-attempt-1/"
        _require_attempt_artifact_path(root + "output.json", root, "output")
        for path in (
            root + "../../../other-seat/output.json",
            root + "..\\other-seat\\output.json",
            "/absolute/output.json",
        ):
            with self.subTest(path=path), self.assertRaises(RunVerificationError):
                _require_attempt_artifact_path(path, root, "output")

    def test_receipt_attempt_must_match_adopted_lineage(self):
        next(
            record for record in self.evidence if record["seat_id"] == "news"
        )["attempt_id"] = "news-adopted-a1"

        with self.assertRaisesRegex(RunVerificationError, "adopted evidence attempt"):
            self.verify()

    def test_structured_output_must_equal_formal_evidence(self):
        receipt = self.receipts["news"][1]
        output_path = receipt["output_path"]
        self._write_artifact(output_path, '[{"self_asserted":true}]\n')
        receipt["output_sha256"] = self.artifact_index[output_path]["sha256"]
        self._rewrite_receipt("news")

        with self.assertRaisesRegex(RunVerificationError, "structured output"):
            self.verify()

    def test_wrong_run_challenge_attempt_model_timing_search_or_hash_fails(self):
        cases = {
            "run": lambda receipt: receipt.update(run_id="wrong-run"),
            "challenge": lambda receipt: receipt.update(
                competition_challenge="wrong-challenge-123456789012"
            ),
            "attempt": lambda receipt: receipt.update(attempt_id="wrong-attempt"),
            "model": lambda receipt: receipt.update(actual_model="wrong-model"),
            "completion": lambda receipt: receipt["completion"].update(elapsed_ms=999),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.setUp()
                path, receipt = self.receipts["news"]
                mutate(receipt)
                self._rewrite_receipt("news")
                with self.assertRaises(RunVerificationError):
                    self.verify()

        self.setUp()
        search_path = self.receipts["news"][1]["search_receipt_path"]
        search = json.loads((self.run_dir / search_path).read_text())
        search["succeeded"] = False
        self._write_artifact(search_path, json.dumps(search) + "\n")
        self.receipts["news"][1]["search_receipt_sha256"] = self.artifact_index[search_path]["sha256"]
        self._rewrite_receipt("news")
        with self.assertRaises(RunVerificationError):
            self.verify()

        self.setUp()
        self.receipts["news"][1]["raw_transcript_sha256"] = "0" * 64
        self._rewrite_receipt("news")
        with self.assertRaises(RunVerificationError):
            self.verify()


class VerifierRulesSnapshotTest(unittest.TestCase):
    """Ticket 11 B1b：一趟 verify_run 只讀一次規則權威——B2 把它收緊成零次。

    B1b 要的是「途中換規則不得改變同一趟驗證的判斷」，做法是整趟共用一份現讀的
    快照。B2 把快照的來源換掉：規則改由該 run 自己的 ``manifest.json`` 提供，所
    以有記錄規則的 run 驗起來**完全不碰**現行設定檔，讀取次數從一次變成零次。
    零次蘊含一次能保證的一切（沒有讀就沒有交錯），B1b 的性質不減反增。

    B2 也**推翻**了 B1b 最後一條：當時要求「下一趟驗證要看到 reload 後的新規
    則」。那條在規則現讀的世界裡是對的，在 B2 之後是錯的——它正是票面要修的
    bug：使用者改一次設定，一場從頭到尾都正確的舊 run 就被新規則判成失敗。現在
    那條反過來要求「改設定不得改變舊 run 的驗證結果」。

    整個檔案的 import 區塊刻意不動（本票對本檔案只准新增），所以這一組需要的
    名字都在方法內就地 import。
    """

    def setUp(self):
        """Build a drill run — it is the fixture that carries a full timeline.

        ``RunController`` 的 run 沒有 ``competition_timeline``，而時間線與停止語
        意兩個檢查都掛在 ``if timeline is not None`` 底下；拿它當 fixture 的話這
        一組測試會全部空轉、每一條都「通過」而什麼都沒驗到。
        """
        from hoya_market_agents.competition_drill import run_fake_competition_drill
        from hoya_market_agents.debate_rules import debate_rules, reload_debate_rules

        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.result = run_fake_competition_drill(
            data_root=self.data_root, question=QUESTION, token="b1-snapshot"
        )
        self.shipped = debate_rules()
        self.addCleanup(reload_debate_rules)

    def test_the_fixture_really_exercises_the_timeline_checks(self):
        """先證明 fixture 帶得動那些檢查，否則下面每一條都是空轉。"""
        import json as json_module

        manifest = json_module.loads(
            (self.result.run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        timeline = manifest["competition_timeline"]

        self.assertIsInstance(timeline, dict)
        self.assertEqual(
            "consensus_{}_votes".format(self.shipped.initial_votes),
            timeline["debate_stop_reason"],
        )

    def lowered(self):
        """A legal 5/4/3 ladder as a plain object — nothing is published."""
        from dataclasses import replace

        return replace(
            self.shipped, initial_votes=5, reduced_votes=4, forced_stop_votes=3
        )

    def answering(self, *rulesets):
        """A stand-in authority handing out ``rulesets`` in order, then repeating."""
        reads = []

        def next_answer():
            reads.append(None)
            return rulesets[min(len(reads) - 1, len(rulesets) - 1)]

        return next_answer, reads

    def test_a_reload_landing_mid_verification_does_not_reject_a_legal_run(self):
        """B 給的交錯：舊 run ＋ 驗證途中換新規則，結果必須仍是 VERIFIED。

        B2 之後連換的機會都沒有：``run_verifier`` 一次也不查現行權威。
        """
        from unittest import mock

        from hoya_market_agents import run_verifier

        authority, reads = self.answering(self.shipped, self.lowered())

        with mock.patch.object(run_verifier, "debate_rules", authority, create=True):
            summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertEqual(0, len(reads), "零次讀取＝沒有任何 reload 插得進來的窗口")

    def test_the_whole_verification_never_reads_the_live_authority(self):
        """連報告信心上限也走該 run 記下的快照，不是自己再讀現行設定。

        ``report_contract`` 是另一個模組，所以它持有自己的一份 ``debate_rules``
        參照；兩個模組都換掉才數得到真正的讀取次數。
        """
        from unittest import mock

        from hoya_market_agents import report_contract, run_verifier

        authority, reads = self.answering(self.shipped, self.lowered())

        with mock.patch.object(run_verifier, "debate_rules", authority, create=True):
            with mock.patch.object(report_contract, "debate_rules", authority):
                summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertEqual(0, len(reads))

    def test_a_run_that_breaks_its_own_recorded_rules_is_still_rejected(self):
        """FP 方向：改讀 manifest ≠ 不驗證。

        把 manifest 記下的規則換成合法的 5/4/3（摘要一併重算，所以不是格式錯
        誤），這份 ``consensus_6_votes`` 的 run 就必須被拒——證明上面那些
        VERIFIED 是因為規則對得上，不是因為檢查被拿掉了。
        """
        self.rewrite_recorded_rules(self.lowered())

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    def rewrite_recorded_rules(self, rules):
        """Re-stamp the manifest with another *legal* rule set, digest included."""
        import json as json_module

        from hoya_market_agents.contract_validator import run_rules_record

        path = self.result.run_dir / "manifest.json"
        manifest = json_module.loads(path.read_text(encoding="utf-8"))
        manifest["debate_rules"] = run_rules_record(rules)
        path.write_text(
            json_module.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_the_shipped_rules_still_verify_the_run_without_any_patching(self):
        """FP 方向：整組改動不得讓正常路徑改變行為。"""
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

    def test_the_shared_inventory_tool_also_counts_zero_reads(self):
        """用共用的盤點工具量一次：它掃全 package 的持有者，不只 run_verifier。"""
        from tests.test_debate_rules import count_authority_reads

        self.assertEqual(
            0,
            count_authority_reads(
                lambda: verify_run(self.data_root, self.result.run_id)
            ),
        )

    def test_a_published_reload_does_not_change_the_verdict_on_an_old_run(self):
        """B2 推翻 B1b 的同一條：改設定不得動搖已經跑完的 run。

        B1b 當時要求「下一趟驗證要看到新規則」，因為那時規則只有一個來源。B2 之
        後該 run 自己就記著它遵守過的規則，拿新規則去判舊資料正是票面要修的誤
        判——同一份 5/4/3 在改動前後都必須得到 VERIFIED。
        """
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

        self.publish_lowered_ladder()

        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

    def publish_lowered_ladder(self):
        """Publish a legal 5/4/3 ladder system-wide, the way a settings page would."""
        import json as json_module

        from hoya_market_agents.debate_rules import RULES_PATH, reload_debate_rules

        document = json_module.loads(RULES_PATH.read_text(encoding="utf-8"))
        document["vote_thresholds"]["initial"] = 5
        document["vote_thresholds"]["reduced"] = 4
        document["vote_thresholds"]["forced_stop"] = 3
        path = self.data_root / "debate_rules.json"
        path.write_text(
            json_module.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        reload_debate_rules(path)
        return path


class RunRulesSnapshotVerificationTest(unittest.TestCase):
    """Ticket 11 B2：每個 run 保存自己的規則快照，verify-run 照那一份驗。

    票面驗收就是這一句：**改設定後驗舊 run 仍 PASS**。設定頁（Ticket 11）讓使用
    者隨時改 ``config/debate_rules.json``，而 verify-run 以前現讀那個檔，於是一
    場從頭到尾都正確的 run 會在使用者改完設定之後被判成失敗。

    fixture 用演練 run：只有它帶 ``competition_timeline``，而規則相關的檢查全部
    掛在 ``if timeline is not None`` 底下。
    """

    def setUp(self):
        from hoya_market_agents.competition_drill import run_fake_competition_drill
        from hoya_market_agents.debate_rules import debate_rules, reload_debate_rules

        # 有幾條測試會在子測試裡重跑 setUp；先回到出貨設定，否則第二輪的 run 會
        # 在上一輪發佈的規則底下跑，fixture 就不是它自稱的那一份了。
        reload_debate_rules()
        self.addCleanup(reload_debate_rules)
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.result = run_fake_competition_drill(
            data_root=self.data_root, question=QUESTION, token="b2-snapshot"
        )
        self.shipped = debate_rules()

    # -- helpers ------------------------------------------------------------

    def manifest(self):
        return json.loads(
            (self.result.run_dir / "manifest.json").read_text(encoding="utf-8")
        )

    def rewrite_manifest(self, manifest):
        (self.result.run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def publish(self, mutate):
        """Publish a mutated—but legal—rule file system-wide, then return it."""
        from hoya_market_agents.debate_rules import RULES_PATH, reload_debate_rules

        document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        mutate(document)
        path = self.data_root / "published_rules.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return reload_debate_rules(path)

    @staticmethod
    def lower_the_vote_ladder(document):
        document["vote_thresholds"]["initial"] = 5
        document["vote_thresholds"]["reduced"] = 4
        document["vote_thresholds"]["forced_stop"] = 3

    @staticmethod
    def tighten_the_domain_downgrade(document):
        """Make the light downgrade far harsher — a confidence-only change."""
        downgrade = document["confidence"]["downgrades"]["few_independent_domains"]
        downgrade["min_independent_domains"] = len(SEAT_IDS)
        downgrade["levels"] = 4

    def strip_the_recorded_rules(self):
        """Turn the fixture into a run from before this field existed."""
        manifest = self.manifest()
        manifest.pop("debate_rules")
        self.rewrite_manifest(manifest)

    # -- the run records what it ran under ----------------------------------

    def test_a_finished_run_records_the_rules_it_ran_under(self):
        from hoya_market_agents.contract_validator import run_rules_record

        self.assertEqual(run_rules_record(self.shipped), self.manifest()["debate_rules"])

    def test_the_summary_names_the_rules_the_run_was_verified_against(self):
        summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual(
            self.manifest()["debate_rules"]["sha256"], summary["rules_sha256"]
        )
        self.assertEqual([], summary["rule_checks_without_run_rules"])

    # -- the acceptance criterion -------------------------------------------

    def test_changing_the_vote_ladder_afterwards_still_verifies_the_old_run(self):
        """票面驗收：改設定 → 驗舊 run → PASS。"""
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

        published = self.publish(self.lower_the_vote_ladder)

        self.assertEqual(
            (5, 4, 3),
            (
                published.initial_votes,
                published.reduced_votes,
                published.forced_stop_votes,
            ),
        )
        self.assertEqual(
            "consensus_6_votes",
            self.manifest()["competition_timeline"]["debate_stop_reason"],
        )
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

    def test_changing_the_light_downgrades_afterwards_still_verifies_the_old_run(self):
        """信心上限那條路徑也要吃該 run 的快照，不是只有時間線與停止語意。"""
        self.publish(self.tighten_the_domain_downgrade)

        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

    # -- the snapshot is used, not merely stored ----------------------------

    def restamp(self, rules):
        """Re-stamp the manifest with another legal rule set, digest included."""
        from hoya_market_agents.contract_validator import run_rules_record

        manifest = self.manifest()
        manifest["debate_rules"] = run_rules_record(rules)
        self.rewrite_manifest(manifest)

    def test_a_run_whose_recorded_rules_forbid_its_own_stop_reason_is_rejected(self):
        """FP 方向：快照是拿來判的，不是拿來裝飾的。

        沒有這一條的話，「改設定仍 PASS」也可能是因為那幾項檢查根本被拿掉了。
        把記錄的規則換成合法的 5/4/3，這份 ``consensus_6_votes`` 的 run 就必須
        被拒——證明門檻階梯真的還在判。
        """
        from dataclasses import replace

        self.restamp(
            replace(self.shipped, initial_votes=5, reduced_votes=4, forced_stop_votes=3)
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    def test_a_run_whose_recorded_rules_forbid_its_own_light_is_rejected(self):
        """同一個 FP 方向，換信心上限那條路徑：降級規則也真的還在判。"""
        from dataclasses import replace

        from hoya_market_agents.debate_rules import DowngradeRule

        harsher = tuple(
            replace(rule, levels=4, min_independent_domains=len(SEAT_IDS))
            if rule.rule == "few_independent_domains"
            else rule
            for rule in self.shipped.confidence.downgrades
        )
        self.assertTrue(
            any(isinstance(rule, DowngradeRule) and rule.levels == 4 for rule in harsher)
        )
        self.restamp(
            replace(
                self.shipped,
                confidence=replace(self.shipped.confidence, downgrades=harsher),
            )
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    def test_a_snapshot_edited_without_its_digest_is_refused(self):
        manifest = self.manifest()
        manifest["debate_rules"]["document"]["vote_thresholds"]["initial"] = 5
        self.rewrite_manifest(manifest)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("規則快照", str(caught.exception))

    def test_a_snapshot_that_is_not_a_legal_rule_document_is_refused(self):
        """摘要對得上也沒用：快照要通過設定檔那一個載入器。"""
        from hoya_market_agents.contract_validator import _rules_document_digest

        manifest = self.manifest()
        document = manifest["debate_rules"]["document"]
        document["timeline_ms"]["force_stop"] = 0
        manifest["debate_rules"]["sha256"] = _rules_document_digest(document)
        self.rewrite_manifest(manifest)

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    # -- runs from before the field existed ---------------------------------

    def test_a_run_without_recorded_rules_is_verified_without_guessing_them(self):
        """舊 run 照實說「沒有記錄規則」，不假裝它跑的是預設規則。"""
        from hoya_market_agents.run_verifier import RULE_DEPENDENT_CHECKS

        self.strip_the_recorded_rules()

        summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertIsNone(summary["rules_sha256"])
        self.assertEqual(
            list(RULE_DEPENDENT_CHECKS), summary["rule_checks_without_run_rules"]
        )

    def test_a_run_without_recorded_rules_still_fails_the_rule_free_checks(self):
        """FN 方向：跳過的只有規則相關那幾項，不是整段時間線都不驗了。"""
        cases = {
            "dispatch": lambda timeline: timeline.update(all_seats_dispatched_at_ms=1),
            "snapshot": lambda timeline: timeline.update(
                evidence_snapshot_sha256="0" * 64
            ),
            "stop disagrees with votes": lambda timeline: timeline.update(
                debate_stop_at_ms=timeline["debate_stop_at_ms"] + 1
            ),
            "report deadline": lambda timeline: timeline.update(
                report_completed_at_ms=780_000
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                self.setUp()
                manifest = self.manifest()
                manifest.pop("debate_rules")
                mutate(manifest["competition_timeline"])
                self.rewrite_manifest(manifest)
                with self.assertRaises(RunVerificationError):
                    verify_run(self.data_root, self.result.run_id)

    def test_a_run_without_recorded_rules_still_needs_a_named_stop_reason(self):
        """門檻名稱驗不了，但「有一個字串」這件事與規則無關，仍然要驗。"""
        manifest = self.manifest()
        manifest.pop("debate_rules")
        manifest["competition_timeline"]["debate_stop_reason"] = None
        self.rewrite_manifest(manifest)

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, self.result.run_id)

    def test_a_legacy_run_is_not_judged_by_the_new_vote_ladder_either(self):
        """舊 run 遇上改過的門檻：不猜、也不拿新規則判，逐項說清楚跳過了什麼。"""
        from hoya_market_agents.run_verifier import RULE_DEPENDENT_CHECKS

        self.strip_the_recorded_rules()
        self.publish(self.lower_the_vote_ladder)

        summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertIn("stop_semantics", summary["rule_checks_without_run_rules"])
        self.assertEqual(
            list(RULE_DEPENDENT_CHECKS), summary["rule_checks_without_run_rules"]
        )

    def test_the_light_cap_of_a_legacy_run_is_skipped_like_the_other_three(self):
        """這一條原本釘的是相反的行為，現在改釘修好之後的行為。

        它上一版叫 ``..._is_still_read_from_the_current_rules``：當時燈號上限住
        在 ``report_contract``，而那個介面說不出「規則未知」，所以沒有記錄規則
        的 run 這一項仍然拿**現行**規則算，改嚴降級規則就會被判成失敗。那不是
        「我不知道」，是一個有自信的錯誤失敗宣稱——和票面要修的 bug 同一個形
        狀，只是換了一個檢查項目。

        ``report_contract.RULES_NOT_RECORDED`` 把第三個值補進 ``rules`` 的值域
        之後，四項規則相關檢查一致地誠實跳過。這條測試現在釘住那個一致性。
        """
        from hoya_market_agents.run_verifier import RULE_DEPENDENT_CHECKS

        self.assertIn("report_confidence_cap", RULE_DEPENDENT_CHECKS)
        self.strip_the_recorded_rules()
        self.publish(self.tighten_the_domain_downgrade)

        summary = verify_run(self.data_root, self.result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertIn(
            "report_confidence_cap", summary["rule_checks_without_run_rules"]
        )
        self.assertEqual(
            list(RULE_DEPENDENT_CHECKS), summary["rule_checks_without_run_rules"]
        )

    def test_a_legacy_run_still_fails_the_report_checks_that_need_no_rules(self):
        """FN 方向：略過的只有上限那一項，報告契約其餘部分照驗。

        沒有這一條的話，「舊 run 仍 VERIFIED」也可能是因為整段報告驗證被關掉。

        改 ``report.json`` 必須連 manifest 的 artifact index 一起改：index 的雜
        湊比報告契約更早檢查，只改檔案的話會被雜湊擋下，這條測試就會因為錯的理
        由通過、什麼都沒驗到。
        """
        import hashlib

        self.strip_the_recorded_rules()
        self.publish(self.tighten_the_domain_downgrade)

        report_path = self.result.run_dir / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["seats"][0]["support_evidence_ids"] = ["unknown-evidence"]
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        report_path.write_text(payload, encoding="utf-8")

        manifest = self.manifest()
        manifest["artifacts"]["report.json"]["sha256"] = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
        self.rewrite_manifest(manifest)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("unknown-evidence", str(caught.exception))


class SharedNavigationTest(unittest.TestCase):
    """導覽的每一個相對目標都必須是這份 bundle 真的帶著的檔案。

    原本的檢查是一份手寫清單，而清單的第一項 ``live.html`` **從來不是 run
    目錄裡的檔案**：舊的直播頁走的是伺服器 URL，Ticket 10 退役 live_dashboard
    之後連那條歷史脈絡都沒有了。於是驗證器不是漏看死連結，是反過來**強制**每
    一份離線報告帶著一個死連結出貨——一份 VERIFIED 的 bundle，點下去是 404。

    改法是換掉權威而不是放寬檢查：導覽該有哪幾頁由**這份 bundle 自己該帶的
    HTML artifact** 推導（:data:`REQUIRED_ARTIFACTS` 裡的 HTML，加上這個
    presentation version 追加的頁面），存不存在則直接問檔案系統。原本「三頁
    共用導覽」的用意（每一頁都連得到其他頁）一項不減，而且變成真的。
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.result = run_fake_competition_drill(
            data_root=self.data_root, question=QUESTION, token="navchk"
        )
        self.run_dir = self.result.run_dir

    def page(self, name):
        return (self.run_dir / name).read_text(encoding="utf-8")

    def republish(self, name, text):
        """Rewrite one page and repair the manifest index, as a forger would.

        不修 index 的話，雜湊檢查會比導覽檢查更早擋下來，測試就會因為錯的理由
        通過、什麼都沒驗到。
        """
        (self.run_dir / name).write_text(text, encoding="utf-8")
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][name]["sha256"] = hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def test_a_shipped_bundle_links_only_to_files_it_actually_carries(self):
        """這就是被回報的缺陷：VERIFIED 的 bundle 不得帶著死連結出貨。"""
        for name in ("report.html", "debate.html"):
            for target in re.findall(r'href="([^"]*)"', self.page(name)):
                if target.startswith(("#", "http://", "https://")):
                    continue
                with self.subTest(page=name, target=target):
                    self.assertTrue(
                        (self.run_dir / target).is_file(),
                        "{} 連到 bundle 沒有的 {}".format(name, target),
                    )

    def test_the_offline_bundle_never_advertises_a_live_room(self):
        """直播頁要跑伺服器，封存後的 bundle 打不開它——連結與標籤都不該在。

        也不接受把它換成 ``/live``：那只是把死連結換成「沒開伺服器就壞掉」的
        連結，離線 bundle 的契約仍然沒被滿足。
        """
        for name in ("report.html", "debate.html"):
            with self.subTest(page=name):
                self.assertNotIn('href="live.html"', self.page(name))
                self.assertNotIn('href="/live"', self.page(name))
                self.assertNotIn("即時辯論", self.page(name))

    def test_verify_run_rejects_a_navigation_target_the_bundle_does_not_carry(self):
        """FP 方向：下一個人加第四個 dead tab 必須過不去。

        斷言錯誤訊息指名那個連結，否則這條測試會被更後面的報告 lineage 檢查
        （重繪後逐位元組比對）順手擋下來而變成假綠：那條路徑擋的是「HTML 被改
        過」，不是「導覽連到不存在的檔案」，換一份沒有 timeline 的 bundle 就失
        效了。
        """
        tampered = self.page("report.html").replace(
            '<a href="report.html" aria-current="page">',
            '<a href="ghost.html">幽靈頁</a><a href="report.html" aria-current="page">',
        )
        self.assertIn('href="ghost.html"', tampered)
        self.republish("report.html", tampered)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("ghost.html", str(caught.exception))

    def test_verify_run_rejects_a_page_that_drops_a_sibling_from_the_navigation(self):
        """FN 方向：少連一頁，「頁面之間互相連得到」就不成立。

        錯誤訊息必須指名少的是哪一頁——清單從 artifact 權威推導出來之後，這件事
        才答得出來。
        """
        tampered = self.page("report.html").replace(
            '<a href="debate.html">完整辯論</a>', "", 1
        )
        self.assertNotIn('<a href="debate.html">完整辯論</a>', tampered)
        self.republish("report.html", tampered)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("debate.html", str(caught.exception))

    def test_verify_run_still_requires_each_page_to_mark_itself(self):
        """既有行為的回歸護欄：目前頁面仍必須標給輔助科技看。"""
        tampered = self.page("debate.html").replace(' aria-current="page"', "", 1)
        self.republish("debate.html", tampered)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("debate.html", str(caught.exception))

    def test_an_untouched_bundle_is_not_over_blocked(self):
        """FP 的另一面：正常的 bundle 不能被新檢查誤擋。"""
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

    def test_a_ghost_link_is_caught_whichever_way_its_attribute_is_quoted(self):
        """FN 方向：合法 HTML 的三種屬性寫法都要算連結。

        ``href="x"``、``href='x'``、``href=x`` 三種寫法瀏覽器一律照跑，而先前的
        檢查是 ``re.compile(r'href="([^"]*)"')``——只認雙引號。於是同一個死連結
        改個引號就整個消失，VERIFIED 照樣發出去。

        每一項都斷言錯誤訊息指名 ``ghost.html``。少了這一句，後面「重繪後逐位元
        組比對」的 lineage 檢查會順手擋下來而變成假綠：那條路徑擋的是「HTML 被改
        過」，不是「導覽連到不存在的檔案」。
        """
        for spelling in (
            '<a href="ghost.html">幽靈頁</a>',
            "<a href='ghost.html'>幽靈頁</a>",
            "<a href=ghost.html>幽靈頁</a>",
            '<a\nhref = "ghost.html"\n>幽靈頁</a>',
            '<img src="ghost.html">',
        ):
            with self.subTest(spelling):
                self.setUp()
                tampered = self.page("report.html").replace(
                    '<a href="report.html" aria-current="page">',
                    spelling + '<a href="report.html" aria-current="page">',
                )
                self.republish("report.html", tampered)

                with self.assertRaises(RunVerificationError) as caught:
                    verify_run(self.data_root, self.result.run_id)
                self.assertIn("ghost.html", str(caught.exception))

    def test_a_navigation_that_is_entirely_commented_out_does_not_count(self):
        """FN 方向：瀏覽器看不到的導覽，驗證器不准認它。

        導覽先前是 ``re.findall(r'<nav[^>]*class="page-tabs"[^>]*>(.*?)</nav>')``
        從整份文件挖出來、再把片段交給 parser 的。regex 不知道自己在不在註解裡，
        所以整段 ``<!-- <nav class="page-tabs">…</nav> -->`` 照樣被數成一份合格
        的導覽，裡面的 ``<a>`` 照樣算它連到了姊妹頁——一份共用導覽整個消失的
        bundle，VERIFIED 照發。

        較弱的工具先處理、較強的工具後接，較強的工具就只能看到較弱的工具准它看
        的東西。現在整份文件只解析一次。
        """
        page = self.page("report.html")
        start = page.index('<nav class="page-tabs"')
        end = page.index("</nav>", start) + len("</nav>")
        tampered = page[:start] + "<!-- " + page[start:end] + " -->" + page[end:]
        self.assertIn("<!-- <nav", tampered)
        self.republish("report.html", tampered)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("共用導覽", str(caught.exception))

    def test_a_comment_beside_the_real_navigation_is_not_a_second_one(self):
        """FP 方向：同一個成因的另一半，方向相反。

        舊 regex 把註解裡的 nav 也數進去，所以一份**正常**的頁面只要旁邊留著一段
        被註解掉的舊導覽，就會被判成「實際有 2 份」而整份 bundle 被退掉。認錯與誤
        擋是同一個成因的兩面，只修一面等於沒修。

        這一條直接問 :func:`_verify_shared_navigation`，不繞 ``verify_run``：放行
        方向的斷言在那條路上做不了，因為任何被改過的 ``report.html`` 都會先被後面
        「重繪後逐位元組比對」的 lineage 檢查擋下來，測到的就不是導覽檢查了。
        """
        page = self.page("report.html")
        start = page.index('<nav class="page-tabs"')
        end = page.index("</nav>", start) + len("</nav>")
        tampered = page[:start] + "<!-- " + page[start:end] + " -->" + page[start:]
        self.assertEqual(2, tampered.count('<nav class="page-tabs"'))

        _verify_shared_navigation(
            self.run_dir, "report.html", tampered, ("report.html", "debate.html")
        )

    def test_a_page_that_rewrites_its_own_link_base_is_refused(self):
        """``<base>`` 會讓上面那整套推論失效，所以它本身就不准出現。

        每一個相對連結都驗過「檔案真的在這個 run 目錄裡」之後，一個
        ``<base href="https://…">`` 就能讓瀏覽器改去別的地方取——檔案還在，連結
        已經不指向它了。這不是多列舉一種屬性，是補掉自己的前提。
        """
        tampered = self.page("report.html").replace(
            "</head>", '<base href="https://elsewhere.example/"></head>', 1
        )
        self.republish("report.html", tampered)

        with self.assertRaises(RunVerificationError) as caught:
            verify_run(self.data_root, self.result.run_id)
        self.assertIn("base", str(caught.exception))


class BundleLinkScopeTest(unittest.TestCase):
    """哪些連結目標算「bundle 打不開」，以 URL 規範而不是字串開頭來判。

    這一組直接問 :func:`_unreachable_bundle_link`，不繞 ``verify_run``：放行方向
    的斷言只能在這裡做，因為任何被改過的 ``report.html`` 都會先被後面的 lineage
    逐位元組比對擋下來，測到的就不是連結檢查了。
    """

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.run_dir = Path(self._temporary.name)
        (self.run_dir / "debate.html").write_text("<html></html>", encoding="utf-8")

    def unreachable(self, target):
        return _unreachable_bundle_link(self.run_dir, target)

    def test_an_external_link_is_allowed_whatever_case_its_scheme_is_in(self):
        """被回報的誤擋：URL scheme 規範上大小寫不敏感。

        ``report_contract.is_safe_source_url`` 用 ``urlsplit(...).scheme.lower()``
        判，所以 ``HTTPS://…`` 會被 renderer 輸出成一個 active link；這裡卻用
        ``startswith(("http://", "https://"))`` 判，於是同一個連結被當成「bundle
        裡不存在的檔案」退掉。同一件事兩套規則，其中一套必然是錯的。
        """
        for target in (
            "https://example.org/evidence",
            "HTTPS://UPPER.example/evidence",
            "HtTp://Mixed.example/evidence",
            "HTTP://UPPER.example/evidence",
        ):
            with self.subTest(target):
                self.assertIsNone(self.unreachable(target))

    def test_a_file_the_bundle_carries_is_reachable(self):
        self.assertIsNone(self.unreachable("debate.html"))
        self.assertIsNone(self.unreachable("debate.html#evidence-1"))
        self.assertIsNone(self.unreachable("#evidence-1"))

    def test_everything_that_needs_something_outside_the_bundle_is_refused(self):
        """FN 方向：只放行 http／https，其餘一律走相對路徑判定並且找不到檔案。

        ``mailto:``、``javascript:``、``data:`` 沒有被逐一列舉，也不需要——放行的
        是一份正面清單，不在清單上的就走下面那條路。
        """
        for target in (
            "ghost.html",
            "/live",
            "//elsewhere.example/evidence",
            "mailto:someone@example.org",
            "javascript:alert(1)",
            "data:text/html,<b>x</b>",
            "FILE:///etc/passwd",
        ):
            with self.subTest(target):
                self.assertIsNotNone(self.unreachable(target))

    def bundle_file(self, name):
        """在 run 目錄裡建一個叫 ``name`` 的檔案，建不出來就明說並跳過。

        這一組的重點就是「檔名剛好長得像那個連結目標」，而每一個 scheme 的寫法
        都含有 ``:``。POSIX 檔名接受它；不接受的檔案系統上這個攻擊面本來就不存
        在，所以跳過並說出原因，而不是讓一條什麼都沒建立的測試安靜地變綠。

        傳進來的名字都不含 ``/``，因為那會變成路徑而不是檔名——建不出來的原因就
        會是「父目錄不存在」，跟這條測試要問的事情無關。
        """
        assert "/" not in name, name
        path = self.run_dir / name
        try:
            path.write_text("x", encoding="utf-8")
        except OSError as exc:
            self.skipTest("此檔案系統不接受檔名 {!r}（{}）".format(name, exc))
        self.assertTrue(path.is_file())
        return path

    def test_a_scheme_is_refused_even_when_a_file_happens_to_have_that_name(self):
        """被回報的完整繞過：非 http 的 scheme 掉進了「當作 bundle 內檔名」那條路。

        上一輪只**正面放行** ``http``／``https``，沒被放行的非空 scheme 就往下走
        相對路徑判定。那條路問的是「run 目錄裡有沒有這個檔案」，於是在 run 目錄
        建一個名叫 ``javascript:alert(1)`` 的檔案，
        ``<a href="javascript:alert(1)">`` 就通過了，整份 bundle 照樣 VERIFIED。

        「離線讀者點下去會不會離開這份 bundle」和檔案系統上有沒有同名的檔案是兩
        個問題，任何非空 scheme 對前者的答案都是「會」。名字碰巧撞上就放行，等
        於讓檔名決定一個和檔案無關的問題。
        """
        for target in ("javascript:alert(1)", "mailto:someone@example.org", "data:x", "FILE:passwd"):
            with self.subTest(target):
                self.bundle_file(target)

                self.assertEqual(target, self.unreachable(target))

    def test_a_scheme_free_name_that_looks_odd_is_still_read_as_a_file(self):
        """FP 方向：拒絕的理由必須是 scheme，不是「檔名裡有奇怪的字」。

        ``a@b.example``、``alert(1)``、``text/html,<b>x</b>`` 都沒有 scheme，都是
        合法的 bundle 內相對路徑；把它們一起擋掉會是同一個錯誤換一個方向。
        """
        for target in ("a@b.example", "alert(1)", "plain-name.html"):
            with self.subTest(target):
                path = self.bundle_file(target)

                self.assertIsNone(self.unreachable(target))
                self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
