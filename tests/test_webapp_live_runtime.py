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

from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.webapp import live
from hoya_market_agents.webapp import launch as launch_module
from tests.test_webapp import (
    LIVE_RUN_ID,
    PageFixture,
    append_events,
    ask_bar_submission,
    seat_message,
    write_live_run,
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

    The SSE frames for the next run carry messages, seats, tally, round and the
    clock and nothing else, so every other run-bound surface on the page can only
    be corrected by the client blanking it. The word it blanks to is the server's
    projection of a run nothing is known about yet, so the room never invents a
    second vocabulary for "not known".
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


class ProjectionConsistencyTest(PageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    @staticmethod
    def frames(body):
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
