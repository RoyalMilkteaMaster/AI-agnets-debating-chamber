"""Ticket #10: the Core-authored report contract and its built-in fixtures."""

import json
import unittest
from dataclasses import replace
from unittest import mock

from hoya_market_agents import report_contract
from hoya_market_agents.debate_rules import (
    DebateRulesError,
    DowngradeRule,
    debate_rules,
    reload_debate_rules,
)
from hoya_market_agents.report_contract import (
    CONFIDENCE_ICONS,
    CONFIDENCE_LEVELS,
    ReportContractError,
    confidence_cap,
    confidence_scale,
    validate_market_report,
)
from hoya_market_agents.report_fixtures import FIXTURE_CASES, load_fixture
from hoya_market_agents.seats import SEAT_IDS
from tests.test_debate_rules import (
    ReloadTestCase,
    RulesVariantTestCase,
    filled_confidence,
    valid_document,
)

REQUIRED_FIELDS = (
    "market_status",
    "period",
    "confidence",
    "tally",
    "consensus_status",
    "judgement",
    "limitations",
    "invalidation_conditions",
)


class FixtureMatrixTests(unittest.TestCase):
    def test_five_report_states_are_available_as_fixtures(self):
        self.assertEqual(
            set(FIXTURE_CASES),
            {
                "consensus-6-1",
                "no-consensus-3-3-1",
                "insufficient-votes-3",
                "insufficient-data",
                "cross-reference-failure",
            },
        )

    def test_every_valid_fixture_passes_the_contract(self):
        for case in ("consensus-6-1", "no-consensus-3-3-1", "insufficient-votes-3", "insufficient-data"):
            with self.subTest(case=case):
                fixture = load_fixture(case)
                report = validate_market_report(fixture["report"], fixture["sources"])
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, report)

    def test_cross_reference_failure_fixture_is_rejected(self):
        fixture = load_fixture("cross-reference-failure")
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])
        self.assertTrue(
            any("evidence" in problem for problem in ctx.exception.problems),
            ctx.exception.problems,
        )

    def test_every_fixture_declares_an_approved_light(self):
        # 連「刻意違規」的 fixture 也要用核准燈號：它該因為 evidence 對不上被
        # 拒絕，不是因為燈號字串是退場的舊值。
        for case in FIXTURE_CASES:
            with self.subTest(case=case):
                confidence = load_fixture(case)["report"]["confidence"]
                self.assertIn(confidence["level"], CONFIDENCE_LEVELS)
                self.assertEqual(
                    CONFIDENCE_ICONS[confidence["level"]], confidence["icon"]
                )

    def test_the_cross_reference_fixture_fails_only_on_its_evidence(self):
        fixture = load_fixture("cross-reference-failure")
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])

        self.assertFalse(
            [problem for problem in ctx.exception.problems if "信心" in problem or "燈號" in problem],
            ctx.exception.problems,
        )

    def test_load_fixture_returns_independent_copies(self):
        first = load_fixture("consensus-6-1")
        first["report"]["judgement"] = "mutated"
        self.assertNotEqual(load_fixture("consensus-6-1")["report"]["judgement"], "mutated")


class SeatRowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture("consensus-6-1")
        self.report = self.fixture["report"]
        self.sources = self.fixture["sources"]

    def test_all_seven_seat_rows_are_preserved_with_lineage(self):
        rows = self.report["seats"]
        self.assertEqual([row["seat_id"] for row in rows], list(SEAT_IDS))
        for row in rows:
            self.assertIn("initial_stance", row)
            self.assertIn("final_stance", row)
            self.assertIn("stance_changed", row)
            self.assertTrue(row["public_reason"].strip())
            self.assertIsInstance(row["replacement_attempt_ids"], list)
            self.assertIsInstance(row["support_evidence_ids"], list)
            self.assertIsInstance(row["counter_evidence_ids"], list)

    def test_every_evidence_row_carries_id_and_url(self):
        for card in self.report["evidence"]:
            self.assertTrue(card["evidence_id"])
            self.assertTrue(card["url"].startswith("https://"))

    def test_missing_seat_row_is_rejected(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"] = broken["report"]["seats"][:-1]
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_tally_must_cross_reference_official_votes(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_seat_stance_must_cross_reference_official_votes(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"][0]["final_stance"] = "bearish"
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_replacement_lineage_must_cross_reference_attempt_ids(self):
        broken = load_fixture("consensus-6-1")
        broken["report"]["seats"][0]["replacement_attempt_ids"] = ["attempt-invented"]
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_report_and_all_source_records_must_share_the_official_run_id(self):
        for target in ("report", "evidence", "debate"):
            with self.subTest(target=target):
                broken = load_fixture("consensus-6-1")
                if target == "report":
                    broken["report"]["run_id"] = "other-run"
                else:
                    broken["sources"][target][0]["run_id"] = "other-run"
                with self.assertRaises(ReportContractError):
                    validate_market_report(broken["report"], broken["sources"])

    def test_source_urls_must_use_http_or_https(self):
        for unsafe in ("javascript:alert(1)", "data:text/html,bad", "file:///tmp/source"):
            with self.subTest(url=unsafe):
                broken = load_fixture("consensus-6-1")
                broken["sources"]["evidence"][0]["source_url"] = unsafe
                broken["report"]["evidence"][0]["url"] = unsafe
                with self.assertRaises(ReportContractError):
                    validate_market_report(broken["report"], broken["sources"])


class HonestFailureTests(unittest.TestCase):
    def test_no_consensus_report_states_no_direction(self):
        fixture = load_fixture("no-consensus-3-3-1")
        report = validate_market_report(fixture["report"], fixture["sources"])
        self.assertEqual(report["consensus_status"], "no_consensus")
        self.assertIsNone(report["adopted_stance"])
        self.assertFalse(report["direction_bearing"])

    def test_insufficient_votes_report_is_red_and_directionless(self):
        fixture = load_fixture("insufficient-votes-3")
        report = validate_market_report(fixture["report"], fixture["sources"])
        self.assertEqual(report["confidence"]["level"], "red")
        self.assertIsNone(report["adopted_stance"])

    def test_directionless_report_may_not_adopt_a_stance(self):
        broken = load_fixture("no-consensus-3-3-1")
        broken["report"]["adopted_stance"] = "bullish"
        broken["report"]["direction_bearing"] = True
        with self.assertRaises(ReportContractError):
            validate_market_report(broken["report"], broken["sources"])

    def test_confidence_levels_are_the_five_approved_lights(self):
        # ADR 0003：純票數五級制，由壞到好。順序就是「降一級」的方向。
        self.assertEqual(
            CONFIDENCE_LEVELS,
            ("red", "orange", "yellow", "green", "blue"),
        )

    def test_yellow_green_is_gone_from_the_approved_lights(self):
        self.assertNotIn("yellow_green", CONFIDENCE_LEVELS)
        self.assertNotIn("yellow_green", CONFIDENCE_ICONS)

    def test_every_approved_light_has_exactly_one_icon(self):
        self.assertEqual(tuple(CONFIDENCE_ICONS), CONFIDENCE_LEVELS)
        self.assertEqual(
            len(set(CONFIDENCE_ICONS.values())), len(CONFIDENCE_LEVELS)
        )

    def test_blue_is_the_new_best_light_and_carries_its_own_icon(self):
        self.assertEqual(CONFIDENCE_LEVELS[-1], "blue")
        self.assertEqual(CONFIDENCE_ICONS["blue"], "🔵")


class PublishedConfidenceTests(unittest.TestCase):
    """報告宣告的燈號本身也要被驗：級別、圖示與上限三者缺一不可。"""

    def problems_for(self, level, icon=None):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["confidence"] = {
            "level": level,
            "icon": CONFIDENCE_ICONS.get(level, "🟢") if icon is None else icon,
            "text": "六票採納。",
        }
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])
        return ctx.exception.problems

    def test_a_light_outside_the_approved_set_is_refused(self):
        for level in ("yellow_green", "grene", "", None, 1):
            with self.subTest(level=repr(level)):
                self.assertIn("confidence.level 不在核准燈號", self.problems_for(level))

    def test_a_light_paired_with_another_lights_icon_is_refused(self):
        problems = self.problems_for("green", icon=CONFIDENCE_ICONS["red"])

        self.assertIn("confidence icon 與 level 不一致", problems)

    def test_every_approved_light_pairs_with_exactly_one_icon(self):
        # 誤擋方向：對的圖示絕不能被判成不一致。
        for level in CONFIDENCE_LEVELS:
            with self.subTest(level=level):
                fixture = load_fixture("consensus-6-1")
                fixture["report"]["confidence"] = {
                    "level": level,
                    "icon": CONFIDENCE_ICONS[level],
                    "text": "六票採納。",
                }
                problems = []
                try:
                    validate_market_report(fixture["report"], fixture["sources"])
                except ReportContractError as exc:
                    problems = exc.problems
                self.assertNotIn("confidence icon 與 level 不一致", problems)

    def test_a_light_above_the_vote_count_is_refused(self):
        self.assertIn(
            "信心 blue 高於資料上限 green", self.problems_for("blue")
        )

    def test_a_light_at_or_below_the_vote_count_is_accepted(self):
        for level in ("green", "yellow", "orange", "red"):
            with self.subTest(level=level):
                fixture = load_fixture("consensus-6-1")
                fixture["report"]["confidence"] = {
                    "level": level,
                    "icon": CONFIDENCE_ICONS[level],
                    "text": "Core 自行下調的信心說明。",
                }
                self.assertIsNotNone(
                    validate_market_report(fixture["report"], fixture["sources"])
                )


class ConfidenceScaleConsistencyTests(RulesVariantTestCase):
    """Ticket 02 的載入器對 ``level`` 只驗字串；缺口在這個消費端關掉。

    設定檔擁有「幾票對哪一級」；``CONFIDENCE_LEVELS`` 擁有「有哪些級、誰比誰
    好」——圖示、CSS 類別與 Core 輸出 schema 的 enum 都綁在它身上。兩者必須
    講同一套詞彙，而且是同一個順序，否則 ``confidence_cap`` 算出的上限與
    ``_validate_confidence`` 的「不得高於上限」會沿著兩條不同的排序比較。
    """

    def confidence_of(self, light_scale):
        """Load a rule file whose ladder is ``light_scale``; return its block."""
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"] = light_scale
        return self.load(document).confidence

    def test_the_shipped_ladder_is_the_five_adr_0003_lights(self):
        self.assertEqual(
            tuple(step.level for step in confidence_scale()),
            tuple(reversed(CONFIDENCE_LEVELS)),
        )

    def test_the_complete_adr_0003_ladder_is_accepted(self):
        steps = confidence_scale(self.confidence_of(filled_confidence()["light_scale"]))

        self.assertEqual(
            [(step.min_votes, step.level) for step in steps],
            [(7, "blue"), (6, "green"), (5, "yellow"), (4, "orange"), (0, "red")],
        )

    def test_the_loader_still_accepts_an_unknown_level_string(self):
        # 這是 Ticket 02 刻意留下的缺口，本測試釘住「缺口在載入器仍然存在」，
        # 好讓下一個測試證明它是在消費端被關掉的，而不是碰巧沒人填錯。
        scale = filled_confidence()["light_scale"]
        scale[0]["level"] = "grene"

        self.assertTrue(self.confidence_of(scale).configured)

    def test_an_unknown_level_string_is_refused_by_the_contract(self):
        # 每一級都要驗，不是只驗第一級：未知字串放在階梯中段一樣得被指名。
        for index in range(len(CONFIDENCE_LEVELS)):
            with self.subTest(index=index):
                scale = filled_confidence()["light_scale"]
                scale[index]["level"] = "grene"
                with self.assertRaises(DebateRulesError) as caught:
                    confidence_scale(self.confidence_of(scale))
                self.assertIn("未核准燈號", str(caught.exception))
                self.assertIn("grene", str(caught.exception))

    def test_a_missing_light_is_named_and_refused(self):
        scale = [
            step
            for step in filled_confidence()["light_scale"]
            if step["level"] != "blue"
        ]

        with self.assertRaises(DebateRulesError) as caught:
            confidence_scale(self.confidence_of(scale))

        self.assertIn("缺少燈號", str(caught.exception))
        self.assertIn("blue", str(caught.exception))

    def test_every_single_light_is_individually_required(self):
        for missing in CONFIDENCE_LEVELS:
            with self.subTest(missing=missing):
                scale = [
                    step
                    for step in filled_confidence()["light_scale"]
                    if step["level"] != missing
                ]
                # 補回「最後一級必須從 0 票起算」，好讓載入器沒有話說：這樣
                # 拒絕的理由只能是缺級，不是階梯蓋不滿票數。
                scale[-1]["min_votes"] = 0
                with self.assertRaises(DebateRulesError) as caught:
                    confidence_scale(self.confidence_of(scale))
                # 訊息要說「缺少」而不是只是碰巧提到那個字——順序錯誤的訊息也
                # 會列出完整級別表，只比對級別名字分不出這兩種拒絕理由。
                self.assertIn("缺少燈號", str(caught.exception))
                self.assertIn(missing, str(caught.exception))

    def test_a_single_rung_ladder_may_not_replace_the_five_lights(self):
        # 載入器允許單級（末級 min_votes 為 0 就覆蓋所有票數），但單級無法表達
        # ADR 0003 的五級映射，也讓「降一級」沒有地方可以降。
        with self.assertRaises(DebateRulesError):
            confidence_scale(self.confidence_of([{"min_votes": 0, "level": "red"}]))

    def test_an_empty_ladder_is_refused(self):
        with self.assertRaises(DebateRulesError):
            confidence_scale(self.confidence_of([]))

    def test_the_ladder_must_run_from_the_best_light_to_the_worst(self):
        scale = filled_confidence()["light_scale"]
        scale[0]["level"], scale[1]["level"] = scale[1]["level"], scale[0]["level"]

        with self.assertRaises(DebateRulesError) as caught:
            confidence_scale(self.confidence_of(scale))

        self.assertIn("順序", str(caught.exception))

    def test_the_shipped_scale_is_read_from_the_repository_config(self):
        self.assertEqual(confidence_scale(), debate_rules().confidence.light_scale)


class DerivedScaleFollowsTheRulesTests(ReloadTestCase):
    """Ticket 11 B1：規則換了，從規則算出來的燈號階梯必須跟著換。

    ``confidence_scale()`` 是 ``debate_rules().confidence`` 的衍生值。它一旦另
    存一份，:func:`reload_debate_rules` 換掉規則之後系統就會進入「規則是新的、
    階梯是舊的」混合狀態——而且沒有任何地方會報錯，報告會沿著一條沒有人選過的
    階梯評燈。那比完全沒有 reload 更糟。

    這裡釘的不是「reload 記得也去清第二份快取」，而是**第二份快取不存在**：階
    梯每次都從現行規則算出來，混合狀態因此無法表達。
    """

    ORANGE_INDEX = 3

    def ladder_with_orange_at(self, min_votes):
        """Publish a legal ADR 0003 ladder whose orange rung moved."""
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][self.ORANGE_INDEX]["min_votes"] = min_votes
        return self.publish(document)

    def test_the_scale_follows_a_reload_without_a_second_call(self):
        self.ladder_with_orange_at(3)

        self.assertEqual(
            [(step.min_votes, step.level) for step in confidence_scale()],
            [(7, "blue"), (6, "green"), (5, "yellow"), (3, "orange"), (0, "red")],
        )

    def test_the_scale_follows_every_reload_not_only_the_first(self):
        """一次性快取只騙得過第一次；連續兩次 reload 會把它抓出來。"""
        self.ladder_with_orange_at(3)
        self.assertEqual(3, confidence_scale()[self.ORANGE_INDEX].min_votes)

        self.ladder_with_orange_at(2)

        self.assertEqual(2, confidence_scale()[self.ORANGE_INDEX].min_votes)

    def test_the_scale_is_the_very_tuple_the_published_rules_carry(self):
        """不是等值、是同一個物件：任何自存一份的實作都過不了這條。"""
        self.ladder_with_orange_at(3)

        self.assertIs(debate_rules().confidence.light_scale, confidence_scale())

    def test_a_reload_back_to_the_repository_config_restores_the_shipped_ladder(self):
        self.ladder_with_orange_at(3)

        reload_debate_rules()

        self.assertEqual(4, confidence_scale()[self.ORANGE_INDEX].min_votes)

    def test_an_explicit_ladder_neither_reads_nor_writes_the_published_rules(self):
        """FP 方向：測試接縫（傳入 rules）不得被全域污染，也不得污染全域。"""
        published = debate_rules()
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][self.ORANGE_INDEX]["min_votes"] = 3
        other = self.load(document).confidence

        self.assertEqual(3, confidence_scale(other)[self.ORANGE_INDEX].min_votes)
        self.assertIs(published, debate_rules())
        self.assertEqual(4, confidence_scale()[self.ORANGE_INDEX].min_votes)

    def test_a_reload_publishes_a_ladder_this_module_will_only_refuse_later(self):
        """已知邊界：詞彙檢查不在 reload 那一關，所以 reload 擋不下這種壞檔案。

        載入器只把 ``level`` 當成不重複的非空字串（Ticket 02 刻意留的缺口），
        關掉缺口的是本模組。結構合法、詞彙不合法的檔案因此會**發佈成功**，直到
        第一次評燈才被 :class:`DebateRulesError` 擋下來——擋是擋住了（不會產出一
        個錯的燈），但舊規則此時已經被換掉。這一條把邊界釘成寫得出來的行為，免
        得它變成沒人知道的洞；要把它一起變成 fail-closed 需要跨模組的發佈流程，
        不在 B1 範圍內。
        """
        document = valid_document()
        document["confidence"] = filled_confidence()
        document["confidence"]["light_scale"][0]["level"] = "grene"

        published = self.publish(document)

        self.assertEqual("grene", published.confidence.light_scale[0].level)
        self.assertIs(published, debate_rules())
        with self.assertRaises(DebateRulesError) as caught:
            confidence_scale()
        self.assertIn("未核准燈號", str(caught.exception))

    def test_a_refused_reload_leaves_the_scale_on_the_old_rules(self):
        """fail-closed 必須是整組的：規則留舊的，階梯也要留舊的。"""
        self.ladder_with_orange_at(3)
        broken = valid_document()
        del broken["confidence"]
        self.path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

        with self.assertRaises(DebateRulesError):
            reload_debate_rules(self.path)

        self.assertEqual(3, confidence_scale()[self.ORANGE_INDEX].min_votes)


class SingleRulesSnapshotTests(unittest.TestCase):
    """一次上限計算只准讀一次規則權威。

    ``confidence_cap`` 先用階梯查表，再用降級規則往下修。分兩次讀取權威的話，
    中間若有人 reload，就會拿**新規則的降級**去修**舊階梯**算出來的上限——一個
    兩份設定都沒有描述過的結果。這個窗口不靠鎖關掉，靠的是整段計算共用同一份
    快照。
    """

    def rules_without_downgrades(self):
        base = debate_rules()
        return replace(base, confidence=replace(base.confidence, downgrades=()))

    def rules_that_always_downgrade(self):
        base = debate_rules()
        return replace(
            base,
            confidence=replace(
                base.confidence,
                downgrades=(
                    DowngradeRule(
                        rule="few_independent_domains",
                        levels=1,
                        min_independent_domains=99,
                    ),
                ),
            ),
        )

    def test_one_cap_computation_reads_the_rules_authority_only_once(self):
        # 第一次讀到「沒有降級」，之後每一次都讀到「必定降級」。只讀一次就是
        # green；讀第二次必然拿到 yellow，兩者分得開。
        answers = [self.rules_without_downgrades(), self.rules_that_always_downgrade()]
        reads = []

        def next_answer():
            reads.append(None)
            return answers[min(len(reads) - 1, len(answers) - 1)]

        fixture = load_fixture("consensus-6-1")
        with mock.patch.object(report_contract, "debate_rules", next_answer):
            cap = confidence_cap(fixture["report"], fixture["sources"])

        self.assertEqual(1, len(reads), "第二次讀取＝一個 reload 插得進來的窗口")
        self.assertEqual("green", cap)

    def test_the_downgrade_still_fires_from_that_single_snapshot(self):
        """FP 方向：只讀一次不得退化成「不讀降級規則」。"""
        always = self.rules_that_always_downgrade()
        fixture = load_fixture("consensus-6-1")

        with mock.patch.object(report_contract, "debate_rules", lambda: always):
            cap = confidence_cap(fixture["report"], fixture["sources"])

        self.assertEqual("yellow", cap)

    def test_the_ladder_from_that_single_snapshot_is_the_one_used(self):
        """FP 方向：快照要真的被用在查表上，不是查完表再拿快照補一下。

        寬鬆階梯（6 票就給藍）是一份合法設定：min_votes 嚴格遞減、末級為 0、
        五級一個不少。同一份 fixture 的六張採納票在出貨階梯是 green，在這一份
        是 blue，所以「查表用的是哪一份階梯」分得出來。
        """
        base = self.rules_without_downgrades()
        loose = replace(
            base,
            confidence=replace(
                base.confidence,
                light_scale=tuple(
                    replace(step, min_votes=votes)
                    for step, votes in zip(base.confidence.light_scale, (6, 5, 4, 3, 0))
                ),
            ),
        )
        fixture = load_fixture("consensus-6-1")

        with mock.patch.object(report_contract, "debate_rules", lambda: loose):
            cap = confidence_cap(fixture["report"], fixture["sources"])

        self.assertEqual("blue", cap)


class RulesNotRecordedTests(unittest.TestCase):
    """Ticket 11 B2：``rules`` 的第三個值＝「這個 run 沒有記錄它跑的規則」。

    燈號上限是規則的函數。規則未知時，唯一誠實的答案是「這一項驗不了」——拿現
    行設定去算，等於用另一個時空的規則判這份資料，而且會判成一個有自信的失敗。

    這個值只關掉**上限比較**那一項。級別、圖示與說明文字的檢查與規則無關，一律
    照驗；報告契約的其餘部分（證據回查、票數交叉比對、時間戳……）更是完全不受
    影響。
    """

    def fixture_with(self, level, icon=None, text="六票採納。"):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["confidence"] = {
            "level": level,
            "icon": CONFIDENCE_ICONS.get(level, "🟢") if icon is None else icon,
            "text": text,
        }
        return fixture

    def problems_for(self, fixture, rules):
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"], rules=rules)
        return ctx.exception.problems

    # -- FP 方向：規則未知時不得判成失敗 -----------------------------------

    def test_a_light_above_the_cap_is_not_judged_when_the_rules_are_unknown(self):
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        fixture = self.fixture_with("blue")

        self.assertIsNotNone(
            validate_market_report(
                fixture["report"], fixture["sources"], rules=RULES_NOT_RECORDED
            )
        )

    def test_a_downgrade_tightened_afterwards_is_not_applied_to_an_unknown_run(self):
        """票面 bug 的形狀：改嚴降級規則不得把舊資料判成失敗。"""
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        harsher = replace(
            debate_rules().confidence,
            downgrades=(
                DowngradeRule(
                    rule="few_independent_domains",
                    levels=4,
                    min_independent_domains=len(SEAT_IDS),
                ),
            ),
        )
        fixture = self.fixture_with("green")

        self.assertIn(
            "信心 green 高於資料上限 red",
            self.problems_for(fixture, harsher),
        )
        self.assertIsNotNone(
            validate_market_report(
                fixture["report"], fixture["sources"], rules=RULES_NOT_RECORDED
            )
        )

    # -- FN 方向：關掉的只有上限那一項 -------------------------------------

    def test_the_light_cap_is_still_enforced_when_the_rules_are_known(self):
        """不得為了關掉假失敗而把整個檢查關掉。"""
        self.assertIn(
            "信心 blue 高於資料上限 green",
            self.problems_for(self.fixture_with("blue"), debate_rules().confidence),
        )

    def test_the_other_confidence_checks_still_run_when_the_rules_are_unknown(self):
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        cases = {
            "confidence.level 不在核准燈號": self.fixture_with("grene"),
            "confidence icon 與 level 不一致": self.fixture_with(
                "green", icon=CONFIDENCE_ICONS["red"]
            ),
            "confidence.text 不得為空": self.fixture_with("green", text="   "),
        }
        for expected, fixture in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(
                    expected, self.problems_for(fixture, RULES_NOT_RECORDED)
                )

    def test_the_rest_of_the_report_contract_still_runs_when_rules_are_unknown(self):
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        fixture = self.fixture_with("green")
        fixture["report"]["seats"][0]["support_evidence_ids"] = ["unknown-evidence"]

        problems = self.problems_for(fixture, RULES_NOT_RECORDED)

        self.assertTrue(
            any("unknown-evidence" in problem for problem in problems), problems
        )

    # -- 算不出來的東西就別假裝算得出來 ------------------------------------

    def test_asking_for_a_cap_without_rules_fails_loudly(self):
        """``confidence_cap`` 的問題在規則未知時沒有答案，不得回一個猜的。"""
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        fixture = load_fixture("consensus-6-1")

        for call in (
            lambda: confidence_cap(
                fixture["report"], fixture["sources"], RULES_NOT_RECORDED
            ),
            lambda: confidence_scale(RULES_NOT_RECORDED),
        ):
            with self.subTest(call=call):
                with self.assertRaises(DebateRulesError) as caught:
                    call()
                self.assertIn("規則未知", str(caught.exception))

    def test_the_marker_is_not_mistaken_for_a_real_rule_set(self):
        """它是一個獨一無二的哨兵，不是 None、不是空的 ConfidenceRules。"""
        from hoya_market_agents.report_contract import RULES_NOT_RECORDED

        self.assertIsNotNone(RULES_NOT_RECORDED)
        self.assertNotEqual(RULES_NOT_RECORDED, debate_rules().confidence)
        self.assertIn("RULES_NOT_RECORDED", repr(RULES_NOT_RECORDED))


if __name__ == "__main__":
    unittest.main()
