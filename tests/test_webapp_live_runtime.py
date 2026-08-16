"""Ticket 06 same-page launch, exact-run handshake and authoritative clock tests."""

import io
import hashlib
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.webapp import live, views
from hoya_market_agents.webapp import launch as launch_module
from tests.test_webapp import (
    LIVE_RUN_ID,
    PageFixture,
    _evidence_card as evidence_record,
    append_events,
    ask_bar_submission,
    seat_message,
    write_live_run,
    write_run,
)


def write_final_manifest(
    run_dir, run_id=LIVE_RUN_ID, elapsed_ms=812_345, report=None, debate=None
):
    artifacts = {}
    for name, body, source in (
        ("report.html", report, "test final report"),
        ("debate.html", debate, "test public debate"),
    ):
        if body is None:
            continue
        path = Path(run_dir) / name
        path.write_text(body, encoding="utf-8")
        artifacts[name] = {
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": source,
        }
    manifest = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "provider_mode": "fake",
        "question": "BTC 會漲嗎",
        "started_at_utc": "2026-08-06T02:00:00Z",
        "completed_at_utc": "2026-08-06T02:13:32Z",
        "elapsed_ms": elapsed_ms,
        "assets": ["BTC"],
        "period_days": 7,
        "seats": [
            {"seat_id": seat_id, "attempt_ids": [seat_id + "-a1"]}
            for seat_id in SEAT_IDS
        ],
        "artifacts": artifacts,
    }
    (Path(run_dir) / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


class SamePageLaunchTest(PageFixture, unittest.TestCase):
    def submit(self):
        from urllib.parse import urlencode

        body = urlencode(ask_bar_submission("BTC 未來七天會不會漲"))
        return self.request(
            "POST /launch HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            "Accept: application/json\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: {}\r\n\r\n{}".format(len(body.encode("utf-8")), body)
        )

    def test_launch_needs_no_ready_certificate_and_returns_pending_token(self):
        response = self.submit()
        payload = json.loads(response.body)

        self.assertEqual(202, response.status)
        self.assertEqual("pending", payload["status"])
        self.assertTrue(payload["launch_token"])
        self.assertEqual(1, len(self.spawned))

    def test_busy_is_json_409_and_starts_nothing_twice(self):
        self.submit()
        response = self.submit()

        self.assertEqual(409, response.status)
        self.assertEqual("busy", json.loads(response.body)["status"])
        self.assertEqual(1, len(self.spawned))

    def test_unknown_launch_token_is_404(self):
        response = self.get("/launch/status?token=not-known")

        self.assertEqual(404, response.status)
        self.assertEqual({"status": "unknown"}, json.loads(response.body))

    def test_status_adopts_only_the_token_bound_exact_run(self):
        payload = json.loads(self.submit().body)
        token = payload["launch_token"]
        args, _ = self.spawned[0]
        handshake_arg = next(
            value for value in args if value.startswith("--launch-handshake=")
        )
        handshake = Path(handshake_arg.split("=", 1)[1])
        exact_id = "20260806T020001Z-btc-exact1"
        exact_dir = write_live_run(self.data_root, run_id=exact_id)
        write_live_run(
            self.data_root,
            run_id="20260806T020059Z-btc-newest",
            question="較新的 stale run",
        )
        (self.data_root / "runs" / "latest.json").write_text(
            json.dumps({"run_id": "20260806T020059Z-btc-newest"}),
            encoding="utf-8",
        )
        handshake.parent.mkdir(parents=True, exist_ok=True)
        handshake.write_text(
            json.dumps(
                {
                    "status": "LAUNCHED",
                    "launch_token": token,
                    "run_id": exact_id,
                    "run_dir": str(exact_dir),
                }
            ),
            encoding="utf-8",
        )

        status = json.loads(self.get("/launch/status?token=" + token).body)

        self.assertEqual({"status": "launched", "run_id": exact_id}, status)
        self.assertFalse(handshake.exists())

    def test_status_refuses_a_handshake_bound_to_another_token(self):
        """A handshake is one launch's answer, and only that launch may read it.

        Everything else about a handshake can be right — LAUNCHED, a run id that
        resolves, a directory that exists — and it still is not *this* token's
        run. Adopting it would hand the page a run this submission did not start,
        and the page would then bind its URL, its SSE stream and its whole clock
        to somebody else's analysis.

        The second half is what makes this a test of the token check rather than
        of the run-directory check: the same file, changed in one field, is
        adopted. So what refused it was the token and nothing else.
        """
        token = json.loads(self.submit().body)["launch_token"]
        args, _ = self.spawned[0]
        handshake = Path(
            next(
                value for value in args if value.startswith("--launch-handshake=")
            ).split("=", 1)[1]
        )
        run_dir = write_live_run(self.data_root)
        payload = {
            "status": "LAUNCHED",
            "launch_token": token + "-belongs-to-another-launch",
            "run_id": LIVE_RUN_ID,
            "run_dir": str(run_dir),
        }
        handshake.parent.mkdir(parents=True, exist_ok=True)
        handshake.write_text(json.dumps(payload), encoding="utf-8")

        refused = json.loads(self.get("/launch/status?token=" + token).body)

        self.assertEqual({"status": "pending"}, refused)
        self.assertNotIn("run_id", refused)
        self.assertTrue(handshake.exists())

        payload["launch_token"] = token
        handshake.write_text(json.dumps(payload), encoding="utf-8")

        self.assertEqual(
            {"status": "launched", "run_id": LIVE_RUN_ID},
            json.loads(self.get("/launch/status?token=" + token).body),
        )

    def test_finished_child_without_valid_handshake_is_failed(self):
        token = json.loads(self.submit().body)["launch_token"]
        self.processes[0].finish(7)

        status = json.loads(self.get("/launch/status?token=" + token).body)

        self.assertEqual("failed", status["status"])
        self.assertNotIn("\n", status["reason"])
        self.assertNotIn("7", status["reason"])
        self.assertNotIn("結束碼", status["reason"])

    def test_starting_a_later_launch_does_not_make_an_issued_token_unknown(self):
        first = json.loads(self.submit().body)["launch_token"]
        self.processes[0].finish(2)
        self.assertEqual(
            "failed", json.loads(self.get("/launch/status?token=" + first).body)["status"]
        )

        second = json.loads(self.submit().body)["launch_token"]

        self.assertNotEqual(first, second)
        self.assertEqual(
            "failed", json.loads(self.get("/launch/status?token=" + first).body)["status"]
        )

    def test_ready_bypass_is_scoped_to_webapp_child_and_restored(self):
        original = launch_module.launcher_module._load_ready_certificate
        observed = []

        def fake_run(_question, data_root, **_options):
            observed.append(
                launch_module.launcher_module._load_ready_certificate(data_root)
            )
            return 0

        with mock.patch.object(
            launch_module.launcher_module, "run_launch", side_effect=fake_run
        ):
            code = launch_module.webapp_run_launch("BTC?", self.data_root)

        self.assertEqual(0, code)
        self.assertEqual([{}], observed)
        self.assertIs(original, launch_module.launcher_module._load_ready_certificate)

    def test_child_writer_atomically_adds_token_to_the_exact_launched_handshake(self):
        run_dir = write_live_run(self.data_root)
        target = self.data_root / "logs" / "launch-handshakes" / "token.json"
        mirrored = io.StringIO()
        writer = launch_module.TokenHandshakeWriter(mirrored, "opaque-token", target)
        payload = {
            "status": "LAUNCHED",
            "run_id": LIVE_RUN_ID,
            "run_dir": str(run_dir),
        }

        text = json.dumps(payload) + "\n"
        writer.write(text[:9])
        self.assertFalse(target.exists())
        writer.write(text[9:])

        self.assertEqual(text, mirrored.getvalue())
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual("opaque-token", written["launch_token"])
        self.assertEqual(LIVE_RUN_ID, written["run_id"])

    def test_status_rejects_a_handshake_whose_recorded_directory_is_not_the_run(self):
        payload = json.loads(self.submit().body)
        token = payload["launch_token"]
        args, _ = self.spawned[0]
        target = Path(
            next(value for value in args if value.startswith("--launch-handshake="))
            .split("=", 1)[1]
        )
        write_live_run(self.data_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "status": "LAUNCHED",
                    "launch_token": token,
                    "run_id": LIVE_RUN_ID,
                    "run_dir": str(self.data_root),
                }
            ),
            encoding="utf-8",
        )

        status = json.loads(self.get("/launch/status?token=" + token).body)

        self.assertEqual({"status": "pending"}, status)


class AuthoritativeLiveClockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.created = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)

    def snapshot(self, run_id=LIVE_RUN_ID, now_ms=0):
        return live.live_snapshot(
            self.data_root,
            run_id,
            clock=lambda: self.created + timedelta(milliseconds=now_ms),
        )

    def test_elapsed_comes_from_question_created_at_not_last_message(self):
        run_dir = write_live_run(self.data_root)
        append_events(run_dir, [seat_message("spot-technical", "bullish", 12_000)])

        self.assertEqual(245_000, self.snapshot(now_ms=245_000)["elapsed_ms"])

    def test_valid_manifest_freezes_elapsed(self):
        run_dir = write_live_run(self.data_root)
        write_final_manifest(run_dir)

        self.assertEqual(812_345, self.snapshot(now_ms=900_000)["elapsed_ms"])

    def test_debate_opened_is_sticky_and_remaining_becomes_null(self):
        run_dir = write_live_run(self.data_root)
        append_events(
            run_dir,
            [
                {"event": "debate_opened", "seat_id": "spot-technical"},
                seat_message("spot-technical", "bullish", 1),
            ],
        )

        snapshot = self.snapshot(now_ms=1)
        self.assertTrue(snapshot["debate_started"])
        self.assertIsNone(snapshot["debate_start_remaining_ms"])

    def test_general_and_comparison_have_one_millisecond_remaining(self):
        for question_type in ("market_direction", "two_asset_comparison"):
            run_id = "20260806T020000Z-btc-{}".format(question_type[-6:])
            write_live_run(
                self.data_root,
                run_id=run_id,
                question_type=question_type,
                assets=("BTC", "ETH") if question_type == "two_asset_comparison" else ("BTC",),
            )
            seal_ms = research_deadlines(question_type).seal_ms
            snapshot = self.snapshot(run_id, now_ms=seal_ms - 1)
            self.assertEqual(1, snapshot["debate_start_remaining_ms"])

    def test_invalid_manifest_does_not_freeze_or_offer_completion(self):
        run_dir = write_live_run(self.data_root)
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": "another-run", "elapsed_ms": 1}), encoding="utf-8"
        )
        (run_dir / "report.html").write_text("report", encoding="utf-8")

        snapshot = self.snapshot(now_ms=123_000)

        self.assertEqual(123_000, snapshot["elapsed_ms"])
        self.assertEqual(live.STATUS_RUNNING, snapshot["state"])
        self.assertIsNone(snapshot["completion"])

    def test_partial_matching_manifest_does_not_freeze_or_offer_completion(self):
        run_dir = write_live_run(self.data_root)
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": LIVE_RUN_ID, "elapsed_ms": 1}), encoding="utf-8"
        )
        (run_dir / "report.html").write_text("report", encoding="utf-8")

        snapshot = self.snapshot(now_ms=123_000)

        self.assertEqual(123_000, snapshot["elapsed_ms"])
        self.assertEqual(live.STATUS_RUNNING, snapshot["state"])
        self.assertIsNone(snapshot["completion"])

    def test_tampered_report_does_not_offer_completion(self):
        run_dir = write_live_run(self.data_root)
        write_final_manifest(run_dir, report="original")
        (run_dir / "report.html").write_text("tampered", encoding="utf-8")

        snapshot = self.snapshot(now_ms=900_000)

        self.assertEqual(812_345, snapshot["elapsed_ms"])
        self.assertIsNone(snapshot["completion"])

    def test_empty_hash_bound_report_does_not_offer_completion(self):
        run_dir = write_live_run(self.data_root)
        write_final_manifest(run_dir, report="")

        snapshot = self.snapshot(now_ms=900_000)

        self.assertEqual(812_345, snapshot["elapsed_ms"])
        self.assertIsNone(snapshot["completion"])


class CompletionArtifactTest(unittest.TestCase):
    """Completion names the artifacts this run actually produced, and no others.

    A report-only run is a real ending: the room may offer its report and must
    not offer a full debate record that was never written, because the tab would
    open a 404 dressed as this run's conclusion.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.run_dir = write_live_run(self.data_root)

    def completion(self):
        return live.completion_for(self.run_dir, LIVE_RUN_ID)

    def test_report_only_completion_offers_no_debate_record(self):
        write_final_manifest(self.run_dir, report="report")

        completion = self.completion()

        self.assertEqual(
            "/run/{}/report.html".format(LIVE_RUN_ID), completion["report_href"]
        )
        self.assertIsNone(completion["debate_href"])

    def test_completion_offers_the_debate_record_the_manifest_binds(self):
        write_final_manifest(self.run_dir, report="report", debate="debate")

        completion = self.completion()

        self.assertEqual(
            "/run/{}/report.html".format(LIVE_RUN_ID), completion["report_href"]
        )
        self.assertEqual(
            "/run/{}/debate.html".format(LIVE_RUN_ID), completion["debate_href"]
        )

    def test_unbound_debate_file_is_not_offered(self):
        write_final_manifest(self.run_dir, report="report")
        (self.run_dir / "debate.html").write_text("stray", encoding="utf-8")

        self.assertIsNone(self.completion()["debate_href"])

    def test_tampered_debate_record_is_not_offered_but_the_report_still_is(self):
        write_final_manifest(self.run_dir, report="report", debate="original")
        (self.run_dir / "debate.html").write_text("tampered", encoding="utf-8")

        completion = self.completion()

        self.assertEqual(
            "/run/{}/report.html".format(LIVE_RUN_ID), completion["report_href"]
        )
        self.assertIsNone(completion["debate_href"])


class RunLocalResetSurfaceTest(PageFixture, unittest.TestCase):
    """Every surface a same-page run switch has to blank carries its own reset word.

    The SSE frames for the next run carry messages, seats, tally, the vote
    history, the rule timeline, the phase, the threshold, the focus bar, the
    sealed evidence, round and the clock; the question, the market, the
    confidence light and the tally note are the run-bound surfaces left that can
    only be corrected by the client blanking them. Even the ones a frame does
    rewrite still show the previous run until that first frame lands, so the
    blanking is what every switch starts with. The word it blanks to is the
    server's projection of a run nothing is known about yet, so the room never
    invents a second vocabulary for "not known".
    """

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.page = self.get("/live?run=" + LIVE_RUN_ID).body

    @staticmethod
    def expected_reset_words():
        timeline = live.rule_timeline()
        focus = live.focus_state([], live.next_milestone(0, timeline), False, None)
        return {
            "live-question": "等待新的市場題目",
            "live-round": "尚未進入辯論",
            "focus-asset": "市場",
            "focus-headline": focus["headline"],
            "focus-tally": focus["tally_text"],
            "focus-detail": "⚪ 信心尚未評估",
            "focus-action": focus["next_label"],
            "live-phase": live.phase_label(0, timeline, live.STATUS_RUNNING),
            "live-threshold": live.threshold_label(0),
            "live-tally": "",
            "tally-note": "尚未開始投票。",
            "live-seats": "",
            "rules-detail-body": "規則時間線將在新的一場開始後顯示。",
            "vote-history-detail-body": "尚未投票。",
            "evidence-panel-body": "證據將在證據快照封存後顯示。",
        }

    def tag_of(self, element_id):
        match = re.search(
            r'<[a-z0-9]+[^>]*\bid="{}"[^>]*>'.format(re.escape(element_id)), self.page
        )
        self.assertIsNotNone(match, "no element carries id={}".format(element_id))
        return match.group(0)

    def test_every_run_local_surface_carries_its_authoritative_reset_word(self):
        for element_id, word in self.expected_reset_words().items():
            with self.subTest(element=element_id):
                self.assertIn('data-reset="{}"'.format(word), self.tag_of(element_id))

    def test_the_reset_words_are_the_only_ones_the_client_is_offered(self):
        # The client blanks exactly what is marked, so a surface that must not be
        # blanked — the launch form, the run picker, the connection state — must
        # not carry the mark.
        marked = set(re.findall(r'id="([a-z0-9-]+)"[^>]*data-reset=', self.page))
        marked |= set(re.findall(r'data-reset="[^"]*"[^>]*id="([a-z0-9-]+)"', self.page))

        self.assertEqual(set(self.expected_reset_words()), marked)

    def test_the_watched_question_is_addressable_apart_from_the_launch_field(self):
        self.assertIn('id="live-question"', self.page)
        self.assertEqual(1, len(re.findall(r'\bid="question"', self.page)))


def sse_frames(body):
    """One event stream body, read back as ``(event name, payload)`` per frame."""
    parsed = []
    for block in body.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, separator, value = line.partition(": ")
            if separator:
                fields.setdefault(name, value)
        if "event" in fields:
            parsed.append(
                (
                    fields["event"],
                    json.loads(fields["data"]) if "data" in fields else None,
                )
            )
    return parsed


class WalkingLiveClock:
    """The run's wall clock, moved only when a test says so.

    Every frame is built from one reading of it, so a test that jumps it from
    the stream's ``sleeper`` seam decides exactly which pass sees which instant
    — which is how a milestone gets crossed with nothing being said.
    """

    def __init__(self, created, elapsed_ms=0):
        self.created = created
        self.elapsed_ms = elapsed_ms

    def __call__(self):
        return self.created + timedelta(milliseconds=self.elapsed_ms)

    def stepper(self, *jumps):
        """A ``sleeper`` that moves the clock on to the next listed instant."""
        remaining = list(jumps)

        def step(_seconds):
            if remaining:
                self.elapsed_ms = remaining.pop(0)

        return step


class RuleTimelineFrameTest(PageFixture, unittest.TestCase):
    """Where the run stands in its own rule timeline is the server's answer.

    The timeline, the phase, the threshold and the focus words all come from the
    :mod:`live` authorities and travel in the frame. The browser is not given a
    second way to work out which milestone is in force, because two ways is two
    answers and the page would eventually show both.
    """

    CREATED = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
    FIXED_ELAPSED_MS = 240_000

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    def frames(self, path="/live/events?run=" + LIVE_RUN_ID):
        return sse_frames(self.get(path).body)

    @staticmethod
    def standing(elapsed_ms, question_type="market_direction"):
        """What the authorities say about a run at ``elapsed_ms``."""
        timeline = live.rule_timeline(question_type)
        return (
            live.current_rule_index(elapsed_ms, timeline),
            live.phase_label(elapsed_ms, timeline, live.STATUS_RUNNING),
            live.threshold_label(elapsed_ms, question_type),
            live.next_milestone(elapsed_ms, timeline)["label"],
        )

    @staticmethod
    def frame_standing(payload):
        return (
            payload["current_rule_index"],
            payload["phase_label"],
            payload["threshold_label"],
            payload["focus"]["next_label"],
        )

    def test_the_first_frame_carries_the_timeline_and_where_the_run_stands(self):
        name, payload = self.frames()[0]

        self.assertEqual("snapshot", name)
        self.assertEqual(
            json.loads(json.dumps(live.rule_timeline("market_direction"))),
            payload["rules"],
        )
        self.assertEqual(
            self.standing(self.FIXED_ELAPSED_MS), self.frame_standing(payload)
        )

    def test_the_focus_words_are_the_authoritys_words_for_this_frames_tally(self):
        """焦點列的字樣沒有第二個來源。

        client 端不再自己把票數拼成一句話，所以 frame 帶的必須就是 ``focus_state``
        對這一幀自己的票數說的那一句 —— 否則畫面上會同時存在兩套講法。
        """
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 12_000),
                seat_message("news", "bullish", 30_000),
                seat_message("macro", "bearish", 42_000),
            ],
        )

        _, payload = self.frames()[0]
        timeline = live.rule_timeline("market_direction")
        expected = live.focus_state(
            payload["tally"],
            live.next_milestone(payload["elapsed_ms"], timeline),
            False,
            None,
        )

        self.assertEqual(expected["headline"], payload["focus"]["headline"])
        self.assertEqual(expected["tally_text"], payload["focus"]["tally_text"])
        self.assertEqual(expected["next_label"], payload["focus"]["next_label"])

    def test_a_crossed_milestone_reaches_the_client_with_nothing_being_said(self):
        """架構 §4.0.1：規則切換由伺服器推送，不靠瀏覽器自己算時間。

        這一場沒有任何新發言，只有時鐘往前走。跨過里程碑的那兩趟仍然發出 frame，
        而 current 索引、階段、門檻與「下一步」都往前走了一格。
        """
        clock = WalkingLiveClock(self.CREATED, self.FIXED_ELAPSED_MS)
        self.build_handler(
            stream=self.single_pass_stream(
                max_seconds=4, sleeper=clock.stepper(330_000, 430_000)
            ),
            live_clock=clock,
        )

        frames = self.frames()

        self.assertEqual(["snapshot", "append", "append"], [n for n, _ in frames])
        self.assertEqual([[], [], []], [p["messages"] for _, p in frames])
        self.assertEqual(
            [self.standing(ms) for ms in (self.FIXED_ELAPSED_MS, 330_000, 430_000)],
            [self.frame_standing(p) for _, p in frames],
        )
        indexes = [p["current_rule_index"] for _, p in frames]
        self.assertTrue(indexes[0] < indexes[1] < indexes[2], indexes)
        thresholds = [p["threshold_label"] for _, p in frames]
        self.assertEqual("尚未進入投票", thresholds[0])
        self.assertNotEqual(thresholds[0], thresholds[2])

    def test_the_timeline_is_sent_once_because_it_does_not_move(self):
        clock = WalkingLiveClock(self.CREATED, self.FIXED_ELAPSED_MS)
        self.build_handler(
            stream=self.single_pass_stream(
                max_seconds=4, sleeper=clock.stepper(330_000, 430_000)
            ),
            live_clock=clock,
        )

        frames = self.frames()

        self.assertIn("rules", frames[0][1])
        self.assertEqual([False, False], ["rules" in p for _, p in frames[1:]])

    def test_a_pass_that_moves_nothing_sends_no_frame(self):
        self.build_handler(stream=self.single_pass_stream(max_seconds=4))

        self.assertEqual(["snapshot"], [n for n, _ in self.frames()])

    def test_a_resumed_stream_carries_the_timeline_on_its_first_frame(self):
        offset = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 12_000)]
        )
        cursor = live.make_cursor(LIVE_RUN_ID, offset)

        name, payload = self.frames(
            "/live/events?run={}&after={}".format(LIVE_RUN_ID, quote(cursor))
        )[0]

        self.assertEqual("append", name)
        self.assertEqual(
            json.loads(json.dumps(live.rule_timeline("market_direction"))),
            payload["rules"],
        )

    def test_a_comparison_runs_frame_carries_its_own_later_timeline(self):
        run_id = "20260806T020000Z-btceth-99aa11"
        write_live_run(
            self.data_root,
            run_id=run_id,
            question="BTC 和 ETH 哪個未來七天表現較好",
            assets=("BTC", "ETH"),
            question_type="two_asset_comparison",
        )

        _, payload = self.frames("/live/events?run=" + run_id)[0]

        self.assertEqual(
            json.loads(json.dumps(live.rule_timeline("two_asset_comparison"))),
            payload["rules"],
        )
        self.assertEqual(
            30_000,
            research_deadlines("two_asset_comparison").seal_ms
            - research_deadlines("market_direction").seal_ms,
        )
        self.assertEqual(
            self.standing(self.FIXED_ELAPSED_MS, "two_asset_comparison"),
            self.frame_standing(payload),
        )

    @staticmethod
    def rendered(page, element_id):
        """What the server drew into one marked cell of the live room."""
        return re.search(
            r'id="{}"[^>]*>([^<]*)'.format(re.escape(element_id)), page
        ).group(1)

    def test_a_finished_runs_stream_never_speaks_as_a_running_one(self):
        """回看已完成 run：沒有任何一幀可以把定稿字樣降回進行中。

        直接開啟一場已經結束的 run，伺服器畫出來的就已經是「已完成」和它的共識
        結論。這條 stream 的第一幀若用進行中的語意組裝，client 會照著把那兩格改
        掉，再由隨後的 done 改回來 —— 讀者看到的是一次沒有發生過的倒退。所以
        「這一場結束了沒有」要在組第一幀之前就問，而不是下一趟才問。
        """
        run_id = "20260801T020000Z-btc-aaaa11"
        write_run(self.data_root, run_id, "BTC 未來七天會不會漲")

        page = self.get("/live?run=" + run_id).body
        frames = self.frames("/live/events?run=" + run_id)

        self.assertEqual(["snapshot", "done"], [name for name, _ in frames])
        # 這一場真的有共識結論，否則「不降級」會因為兩邊本來就一樣而空過。
        self.assertEqual("consensus", frames[-1][1]["outcome"]["consensus_status"])
        for name, payload in frames:
            with self.subTest(frame=name):
                self.assertEqual(
                    self.rendered(page, "live-phase"), payload["phase_label"]
                )
                self.assertEqual(
                    self.rendered(page, "focus-headline"),
                    payload["focus"]["headline"],
                )
                self.assertEqual(
                    self.rendered(page, "live-threshold"),
                    payload["threshold_label"],
                )

    def test_the_done_frame_speaks_as_the_finished_run_the_page_would_show(self):
        """定稿那一幀畫上去的字，必須和重新整理後伺服器畫的是同一句。

        client 收到 done 仍會照這一幀重畫（重置過的分頁隨後才交還伺服器重繪），
        所以這一幀若用「進行中」的講法作答，讀者會先看到一句和頁面矛盾的話。
        """
        write_final_manifest(self.run_dir, report="report")

        name, payload = self.frames()[-1]
        timeline = live.rule_timeline("market_direction")
        expected = live.focus_state(
            payload["tally"],
            live.next_milestone(payload["elapsed_ms"], timeline),
            True,
            payload["outcome"],
        )

        self.assertEqual("done", name)
        self.assertEqual(
            live.phase_label(
                payload["elapsed_ms"], timeline, live.STATUS_FINISHED
            ),
            payload["phase_label"],
        )
        self.assertEqual(expected["headline"], payload["focus"]["headline"])
        self.assertEqual(expected["next_label"], payload["focus"]["next_label"])


def sealed_card(seat_id, **overrides):
    """One line of ``evidence.jsonl`` for this run, with fields overridable."""
    card = dict(evidence_record(seat_id, LIVE_RUN_ID))
    card.update(overrides)
    return card


class EvidenceFrameTest(PageFixture, unittest.TestCase):
    """封存的證據卡由 frame 送達，一條 stream 只送一次。

    顯示閘門沿用既有的那一個（``views._read_evidence``）：檔案在、而且解析得出
    卡片，才算封存。封存後內容不可變，所以送達一次就是最終答案 —— 之後的每一幀
    都不再帶它，重連開的新 stream 才自然重送一次。

    來源可不可點是伺服器答的：``report_contract.is_safe_source_url`` 是這個專案
    唯一的判準，frame 帶的是判完的結果（``source_href``），瀏覽器不再判一次。
    """

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    def frames(self, path="/live/events?run=" + LIVE_RUN_ID):
        return sse_frames(self.get(path).body)

    def seal(self, *cards):
        (self.run_dir / views.EVIDENCE_RECORD).write_text(
            "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
            encoding="utf-8",
        )

    def test_a_run_with_no_sealed_snapshot_sends_no_evidence_field(self):
        name, payload = self.frames()[0]

        self.assertEqual("snapshot", name)
        self.assertNotIn("evidence", payload)

    def test_a_file_that_holds_no_card_is_not_a_seal_yet(self):
        """閘門是既有的那一個：檔案在還不夠，要解析得出卡片。"""
        (self.run_dir / views.EVIDENCE_RECORD).write_text("", encoding="utf-8")

        self.assertNotIn("evidence", self.frames()[0][1])

    def test_the_first_frame_of_a_stream_carries_every_sealed_card(self):
        self.seal(sealed_card("spot-technical"), sealed_card("news"))

        _, payload = self.frames()[0]

        self.assertEqual(
            ["spot-technical-01", "news-01"],
            [card["evidence_id"] for card in payload["evidence"]],
        )
        first = payload["evidence"][0]
        self.assertEqual("spot-technical", first["seat_id"])
        self.assertEqual("spot-technical 提交的證據陳述", first["statement"])
        self.assertEqual("spot-technical 的引文", first["excerpt"])
        self.assertEqual("1", first["source_tier"])
        self.assertEqual("example.invalid", first["source_origin"])
        self.assertEqual(
            "https://example.invalid/spot-technical", first["source_url"]
        )
        self.assertEqual(first["source_url"], first["source_href"])

    def test_the_pass_that_first_sees_the_seal_pushes_it_and_no_later_pass_repeats_it(self):
        """封存那一趟就發 frame，之後的每一幀都不再帶它。

        第二趟才有證據，第三趟只有新發言 —— 那一幀仍然要發（它有話說），但不能
        再帶一次封存內容：不可變的東西送兩次只是同一個答案的第二份。
        """
        def step(_seconds):
            if not steps:
                return
            steps.pop(0)()

        steps = [
            lambda: self.seal(sealed_card("spot-technical")),
            lambda: append_events(
                self.run_dir, [seat_message("spot-technical", "bullish", 12_000)]
            ),
        ]
        self.build_handler(
            stream=self.single_pass_stream(max_seconds=4, sleeper=step)
        )

        frames = self.frames()

        self.assertEqual(["snapshot", "append", "append"], [n for n, _ in frames])
        self.assertNotIn("evidence", frames[0][1])
        self.assertEqual([], frames[1][1]["messages"])
        self.assertEqual(
            ["spot-technical-01"],
            [card["evidence_id"] for card in frames[1][1]["evidence"]],
        )
        self.assertEqual(1, len(frames[2][1]["messages"]))
        self.assertNotIn("evidence", frames[2][1])

    def test_a_seal_first_seen_on_the_finishing_pass_rides_the_done_frame(self):
        """``done`` 是自己的一條分支，封存與結束落在同一趟時由它把卡片帶出去。

        那一趟不會再有下一幀：``_follow`` 送完 done 就 return。所以這條分支若不
        問「這條 stream 送過了沒有」，這一場的證據就永遠不會到達 client —— 而
        頁面上那一格會停在等待字樣，直到讀者自己重新整理。
        """
        def step(_seconds):
            if sealed_on_this_pass:
                return
            sealed_on_this_pass.append(True)
            self.seal(sealed_card("spot-technical"))
            write_final_manifest(self.run_dir, report="report")

        sealed_on_this_pass = []
        self.build_handler(
            stream=self.single_pass_stream(max_seconds=4, sleeper=step)
        )

        frames = self.frames()

        self.assertEqual(["snapshot", "done"], [n for n, _ in frames])
        self.assertNotIn("evidence", frames[0][1])
        self.assertEqual(
            ["spot-technical-01"],
            [card["evidence_id"] for card in frames[-1][1]["evidence"]],
        )

    def test_a_done_frame_does_not_repeat_a_seal_this_stream_already_sent(self):
        """同一條分支的另一半：已經送過的封存，定稿那一幀不再送第二次。

        封存內容不可變，所以第二份和第一份是同一個答案。這一案和上一案一起說完
        ``done`` 分支要問的那一個問題 —— 少了任一邊，「這條 stream 送過了沒有」
        都可以被拿掉而不被任何測試攔住。
        """
        self.seal(sealed_card("spot-technical"))
        write_final_manifest(self.run_dir, report="report")

        frames = self.frames()

        self.assertEqual(["snapshot", "done"], [n for n, _ in frames])
        self.assertEqual(
            ["spot-technical-01"],
            [card["evidence_id"] for card in frames[0][1]["evidence"]],
        )
        self.assertNotIn("evidence", frames[-1][1])

    def test_a_reconnecting_stream_is_sent_the_sealed_cards_again(self):
        """已送旗標是 per-stream 的：重連開的新 stream 從頭再送一次。"""
        offset = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 12_000)]
        )
        self.seal(sealed_card("news"))
        cursor = live.make_cursor(LIVE_RUN_ID, offset)

        name, payload = self.frames(
            "/live/events?run={}&after={}".format(LIVE_RUN_ID, quote(cursor))
        )[0]

        self.assertEqual("append", name)
        self.assertEqual(
            ["news-01"], [card["evidence_id"] for card in payload["evidence"]]
        )

    def test_the_cards_the_frame_carries_are_the_cards_the_page_renders(self):
        self.seal(sealed_card("spot-technical"), sealed_card("news"))

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        _, payload = self.frames()[0]

        self.assertEqual(2, len(payload["evidence"]))
        for card in payload["evidence"]:
            with self.subTest(card=card["evidence_id"]):
                self.assertIn(card["evidence_id"], page)
                self.assertIn(card["seat_id"], page)
                self.assertIn(card["statement"], page)
                self.assertIn(card["excerpt"], page)
                self.assertIn(
                    "來源等級 {}・{}".format(
                        card["source_tier"], card["source_origin"]
                    ),
                    page,
                )
                self.assertIn(
                    '<a class="source-link" href="{}"'.format(card["source_href"]),
                    page,
                )

    def test_an_unsafe_source_reaches_the_client_as_text_and_never_as_a_link(self):
        """fail closed：不是 http(s) 的來源，frame 不給可點連結，文字照樣送。"""
        self.seal(
            sealed_card("spot-technical", source_url="javascript:alert(1)"),
            sealed_card("news"),
        )

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        unsafe, safe = self.frames()[0][1]["evidence"]

        self.assertEqual("javascript:alert(1)", unsafe["source_url"])
        self.assertIsNone(unsafe["source_href"])
        self.assertEqual(safe["source_url"], safe["source_href"])
        self.assertNotIn('href="javascript:', page)
        self.assertIn("javascript:alert(1)", page)


class ProjectionConsistencyTest(PageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    frames = staticmethod(sse_frames)

    def test_initial_html_and_first_sse_snapshot_share_one_run_clock(self):
        elapsed_ms = 240_000
        remaining_ms = (
            research_deadlines("market_direction").seal_ms - elapsed_ms
        )

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        first_event, first_payload = self.frames(
            self.get("/live/events?run=" + LIVE_RUN_ID).body
        )[0]

        self.assertIn('data-elapsed-ms="{}"'.format(elapsed_ms), page)
        self.assertIn('data-remaining-ms="{}"'.format(remaining_ms), page)
        self.assertEqual("snapshot", first_event)
        self.assertEqual(elapsed_ms, first_payload["elapsed_ms"])
        self.assertEqual(
            remaining_ms, first_payload["debate_start_remaining_ms"]
        )

    VOTE_ROW = re.compile(r'<li class="(history-row[^"]*)">(.*?)</li>', re.S)
    ROW_TEXT = re.compile(r">([^<>]+)<")

    def rendered_vote_rows(self, page):
        """The 票數變化 rows the server drew, as ``(class, words)`` per row."""
        body = re.search(
            r'id="vote-history-detail-body"[^>]*>(.*?)</div>', page, re.S
        )
        self.assertIsNotNone(body, "the page has no 票數變化 panel body")
        return [
            (
                row_class,
                " ".join(
                    word.strip()
                    for word in self.ROW_TEXT.findall(inner)
                    if word.strip()
                ),
            )
            for row_class, inner in self.VOTE_ROW.findall(body.group(1))
        ]

    def frame_vote_rows(self, changes):
        """The same rows, written out from the frame in the Spec's row format."""
        rows = []
        for change in changes:
            seconds = max(0, int(change["elapsed_ms"] or 0) // 1000)
            stamp = "T+{:02d}:{:02d}".format(seconds // 60, seconds % 60)
            if change["before"] is None:
                rows.append(
                    (
                        "history-row",
                        "{} {} 首次表態：{}".format(
                            stamp, change["seat_label"], change["after_label"]
                        ),
                    )
                )
            else:
                rows.append(
                    (
                        "history-row changed",
                        "{} {} {} → {} 改票".format(
                            stamp,
                            change["seat_label"],
                            change["before_label"],
                            change["after_label"],
                        ),
                    )
                )
        return rows

    def test_the_frame_and_the_reloaded_page_show_the_same_vote_history(self):
        """即時追加的那一列，和重新整理同一場看到的那一列是同一列。

        面板整頁渲染時讀的是這一場累積的改票紀錄；frame 帶的必須是同一份，否則
        「不重新整理看到的」與「重新整理看到的」會變成兩套說法。
        """
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 12_000),
                seat_message("news", "bearish", 30_000),
                seat_message(
                    "spot-technical", "neutral", 90_000, change_reason="改看觀望"
                ),
            ],
        )

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        first_event, payload = self.frames(
            self.get("/live/events?run=" + LIVE_RUN_ID).body
        )[0]

        self.assertEqual("snapshot", first_event)
        drawn = self.rendered_vote_rows(page)
        self.assertEqual(3, len(drawn))
        self.assertEqual(
            ["history-row", "history-row", "history-row changed"],
            [row_class for row_class, _ in drawn],
        )
        self.assertEqual(drawn, self.frame_vote_rows(payload["changes"]))

    RULE_ROW = re.compile(
        r'<div class="(rule[^"]*)"><time>([^<]*)</time><span>([^<]*)</span></div>'
    )

    def frame_rule_rows(self, payload):
        """The 規則與時間線 rows the frame implies, in the Spec's row format."""
        rows = []
        current = payload["current_rule_index"]
        for index, rule in enumerate(payload["rules"]):
            if index == current:
                row_class = "rule current"
            elif index < current:
                row_class = "rule past"
            else:
                row_class = "rule"
            seconds = max(0, int(rule["at_ms"]) // 1000)
            votes = (
                "（門檻 {} 票）".format(rule["required_votes"])
                if rule["required_votes"]
                else ""
            )
            rows.append(
                (
                    row_class,
                    "T+{:02d}:{:02d}".format(seconds // 60, seconds % 60),
                    rule["label"] + votes,
                )
            )
        return rows

    def test_the_frame_and_the_rendered_page_draw_the_same_rule_timeline(self):
        """即時前進的那一列，和重新整理同一場看到的那一列是同一列。

        current 索引只有一個算法（``live.current_rule_index``），伺服器渲染與
        frame 都讀它，所以「不重新整理看到的」與「重新整理看到的」不會分家。
        """
        page = self.get("/live?run=" + LIVE_RUN_ID).body
        name, payload = self.frames(
            self.get("/live/events?run=" + LIVE_RUN_ID).body
        )[0]

        drawn = self.RULE_ROW.findall(page)

        self.assertEqual("snapshot", name)
        self.assertEqual(len(payload["rules"]), len(drawn))
        self.assertEqual(1, [row[0] for row in drawn].count("rule current"))
        self.assertEqual(self.frame_rule_rows(payload), drawn)

    def test_live_page_exposes_the_exact_run_surfaces_used_by_same_page_switch(self):
        page = self.get("/live?run=" + LIVE_RUN_ID).body

        self.assertIn(
            '<code id="live-run-id">{}</code>'.format(LIVE_RUN_ID), page
        )
        self.assertIn('id="live-report-link"', page)
        self.assertIn('id="live-debate-link"', page)
        self.assertIn(
            'data-duration-ms="{}"'.format(live.TOTAL_WINDOW_MS), page
        )

    def test_finalized_html_snapshot_and_done_all_share_manifest_elapsed(self):
        elapsed_ms = 812_345
        write_final_manifest(self.run_dir, elapsed_ms=elapsed_ms, report="report")

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        frames = self.frames(self.get("/live/events?run=" + LIVE_RUN_ID).body)

        self.assertIn('data-elapsed-ms="{}"'.format(elapsed_ms), page)
        self.assertEqual(["snapshot", "done"], [name for name, _ in frames])
        self.assertEqual(
            [elapsed_ms, elapsed_ms],
            [payload["elapsed_ms"] for _, payload in frames],
        )
        self.assertEqual(
            "/run/{}/report.html".format(LIVE_RUN_ID),
            frames[-1][1]["completion"]["report_href"],
        )

    def test_a_finalized_refresh_projects_the_remaining_time_done_freezes(self):
        """Reloading a finished run reads the clock the room was left showing.

        The room stops the 十七分鐘 countdown when ``done`` arrives and leaves it
        at the total window less the elapsed time that frame carried. A reload
        has to land on that same number, so both are projected from one place:
        the countdown cell says which total it is counting down from
        (``data-countdown-from``) and the elapsed value it subtracts is the
        manifest's frozen one. 13:32.345 of a 17:00 window leaves 03:27 —
        not 00:00, which would claim the window ran out on a run that ended
        early.
        """
        elapsed_ms = 812_345
        write_final_manifest(self.run_dir, elapsed_ms=elapsed_ms, report="report")

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        cell = re.search(
            r'<strong id="live-total-remaining"[^>]*>[^<]*</strong>', page
        ).group(0)

        self.assertIn('data-countdown-from="{}"'.format(live.TOTAL_WINDOW_MS), cell)
        self.assertIn('data-duration-ms="{}"'.format(live.TOTAL_WINDOW_MS), cell)
        self.assertTrue(cell.endswith(">03:27</strong>"), cell)
        self.assertIn('data-elapsed-ms="{}"'.format(elapsed_ms), page)

    def done_completion(self, **artifacts):
        write_final_manifest(self.run_dir, **artifacts)
        frames = self.frames(self.get("/live/events?run=" + LIVE_RUN_ID).body)
        self.assertEqual("done", frames[-1][0])
        return frames[-1][1]["completion"]

    def test_done_names_the_debate_record_a_run_with_both_produced(self):
        completion = self.done_completion(report="report", debate="debate")

        self.assertEqual(
            "/run/{}/debate.html".format(LIVE_RUN_ID), completion["debate_href"]
        )

    def test_done_and_the_html_tabs_agree_that_a_report_only_run_has_no_debate(self):
        completion = self.done_completion(report="report")
        page = self.get("/live?run=" + LIVE_RUN_ID).body

        self.assertIsNone(completion["debate_href"])
        self.assertNotIn(
            '"/run/{}/debate.html"'.format(LIVE_RUN_ID), page
        )

    def test_the_tabs_refuse_the_stray_debate_file_the_done_frame_refuses(self):
        """A file on disk this run's manifest does not bind is not this run's.

        ``done`` answers by hash and the tab used to answer by ``is_file()``, so
        a debate record left behind by something else — a stray, a rewrite —
        opened from the tab bar while the same page's ``done`` frame refused to
        name it. Both read the one answer now, and a reader who tabs to 完整辯論
        gets the inert tab rather than somebody else's debate dressed as this
        run's conclusion.
        """
        completion = self.done_completion(report="report")
        (self.run_dir / "debate.html").write_text("stray", encoding="utf-8")

        page = self.get("/live?run=" + LIVE_RUN_ID).body
        debate_tab = re.search(r'<[^>]*id="live-debate-link"[^>]*>', page).group(0)

        self.assertIsNone(completion["debate_href"])
        self.assertNotIn('"/run/{}/debate.html"'.format(LIVE_RUN_ID), page)
        self.assertIn('aria-disabled="true"', debate_tab)


if __name__ == "__main__":
    unittest.main()
