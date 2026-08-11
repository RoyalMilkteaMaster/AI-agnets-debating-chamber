"""Tickets 09, 10, 11 and 12: the resident local web app.

History, run detail and the log came from 09. The live chat room, the question
that starts a run, and the retirement of the old dashboard page came from 10.
The debate rules settings page came from 11. Ticket 12 added the statistics
page, the expiry sweep that checks a finished prediction against what the
market did, and the manual entry that stands in when no price can be had.
"""

import ast
import http.client
import io
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.parser import BytesParser
from html import escape
from html.parser import HTMLParser
from hashlib import sha256
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from fakes import FixedClock

from hoya_market_agents import design_tokens
from hoya_market_agents.debate_rules import (
    RULES_PATH,
    DebateRulesError,
    debate_rules,
    load_debate_rules,
    reload_debate_rules,
)
from hoya_market_agents.debate_state_machine import (
    required_votes_at,
)
from hoya_market_agents import prompt_builder
from hoya_market_agents.prompt_builder import market_scopes
from hoya_market_agents.question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.question_package import MARKET_STANCES
from hoya_market_agents.research_scheduler import research_deadlines
from hoya_market_agents.quote_api_client import Quote, QuoteUnavailableError
from hoya_market_agents.report_contract import CONFIDENCE_LEVELS
from hoya_market_agents.run_index import (
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_RECORD_NAME,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_VERDICTS,
    outcome_summary,
    outcome_verdict,
    query_runs,
    rebuild_index,
)
from hoya_market_agents.run_store import resolve_run_dir, run_dir_parts
from hoya_market_agents.seats import (
    SEAT_IDENTITIES,
    seat_display_names,
    seat_identities,
)
from hoya_market_agents.system_preflight import write_ready_certificate
from hoya_market_agents.webapp import launch as launch_module
from hoya_market_agents.webapp import live, outcome as outcome_module, pages, settings, views
from hoya_market_agents.webapp import server as server_module
from hoya_market_agents.webapp import log as log_module
from hoya_market_agents.webapp.log import (
    ACTIVE_LOG_NAME,
    RECORD_FIELDS,
    RETENTION_DAYS,
    WebappLogError,
    open_webapp_log,
    rotated_log_name,
)
from hoya_market_agents.webapp.server import (
    CONTENT_SECURITY_POLICY,
    DEFAULT_PORT,
    LIVE_CONTENT_SECURITY_POLICY,
    MAX_FORM_BYTES,
    StreamSettings,
    WebappError,
    create_webapp_server,
    webapp_handler_class,
)


# -- log --------------------------------------------------------------------


class LogFixture:
    """A Data Root with an injectable clock, shared by the log's tests."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.clock = FixedClock()

    def open_log(self):
        log = open_webapp_log(self.data_root, clock=self.clock)
        self.addCleanup(log.close)
        return log

    def records(self, name=ACTIVE_LOG_NAME):
        path = self.data_root / "logs" / name
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class WebappLogRecordTest(LogFixture, unittest.TestCase):
    """Every line is one JSON object with exactly the five approved fields."""

    def test_the_log_lands_in_the_data_root_logs_directory(self):
        self.open_log().info("server_start", "webapp.server", "已啟動")

        self.assertTrue((self.data_root / "logs" / "webapp.jsonl").is_file())

    def test_one_record_carries_exactly_the_five_approved_fields(self):
        self.open_log().info("server_start", "webapp.server", "已啟動")

        record = self.records()[0]

        self.assertEqual(set(RECORD_FIELDS), set(record))

    def test_a_record_reports_its_level_event_source_and_message(self):
        self.open_log().warning("request_not_found", "webapp.request", "/missing")

        record = self.records()[0]

        self.assertEqual("WARNING", record["level"])
        self.assertEqual("request_not_found", record["event"])
        self.assertEqual("webapp.request", record["source"])
        self.assertEqual("/missing", record["message"])

    def test_the_three_levels_are_written_as_the_approved_words(self):
        log = self.open_log()
        log.info("a", "s", "m")
        log.warning("b", "s", "m")
        log.error("c", "s", "m")

        self.assertEqual(
            ["INFO", "WARNING", "ERROR"], [r["level"] for r in self.records()]
        )

    def test_the_timestamp_is_the_injected_clock_not_the_wall_clock(self):
        self.open_log().error("server_start_failed", "webapp.server", "埠被占用")

        self.assertEqual(
            self.clock.utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            self.records()[0]["timestamp"],
        )

    def test_each_call_appends_a_line_rather_than_replacing_the_file(self):
        log = self.open_log()
        log.info("server_start", "webapp.server", "已啟動")
        log.info("server_stop", "webapp.server", "已停止")

        self.assertEqual(
            ["server_start", "server_stop"],
            [record["event"] for record in self.records()],
        )

    def test_a_later_process_appends_to_the_same_file(self):
        first = self.open_log()
        first.info("server_start", "webapp.server", "第一次")
        first.close()

        self.open_log().info("server_start", "webapp.server", "第二次")

        self.assertEqual(["第一次", "第二次"], [r["message"] for r in self.records()])

    def test_a_record_is_readable_before_the_log_is_closed(self):
        self.open_log().info("server_start", "webapp.server", "還在跑")

        self.assertEqual(["還在跑"], [record["message"] for record in self.records()])

    def test_a_log_directory_that_cannot_be_written_refuses_to_open(self):
        directory = self.data_root / "logs"
        directory.mkdir()
        directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(directory.chmod, stat.S_IRWXU)

        with self.assertRaises(WebappLogError) as refused:
            open_webapp_log(self.data_root, clock=self.clock).info("e", "s", "m")

        self.assertIn(str(directory / ACTIVE_LOG_NAME), str(refused.exception))


class WebappLogRotationTest(LogFixture, unittest.TestCase):
    """The active file is one day's records; older days keep their own name."""

    def written_yesterday(self):
        """Leave a ``webapp.jsonl`` whose newest record is one day old."""
        yesterday = self.clock.utc_now() - timedelta(days=1)
        log = open_webapp_log(self.data_root, clock=FixedClock(start_utc=yesterday))
        log.info("server_start", "webapp.server", "昨天的紀錄")
        log.close()
        return yesterday

    def test_yesterdays_records_move_to_a_file_named_for_their_own_day(self):
        yesterday = self.written_yesterday()

        self.open_log().info("server_start", "webapp.server", "今天的紀錄")

        rotated = self.records(rotated_log_name(yesterday.date()))
        self.assertEqual(["昨天的紀錄"], [record["message"] for record in rotated])

    def test_todays_file_holds_only_todays_records_after_a_rotation(self):
        self.written_yesterday()

        self.open_log().info("server_start", "webapp.server", "今天的紀錄")

        self.assertEqual(["今天的紀錄"], [r["message"] for r in self.records()])

    def test_a_file_written_today_is_not_rotated(self):
        first = self.open_log()
        first.info("server_start", "webapp.server", "稍早")
        first.close()

        self.open_log().info("server_start", "webapp.server", "稍晚")

        self.assertEqual(["稍早", "稍晚"], [r["message"] for r in self.records()])
        self.assertEqual([], list((self.data_root / "logs").glob("webapp-*.jsonl")))

    def test_the_day_rolling_over_while_the_server_runs_rotates_mid_flight(self):
        log = self.open_log()
        log.info("server_start", "webapp.server", "跨日前")
        started = self.clock.utc_now()

        self.clock.advance_ms(24 * 60 * 60 * 1000)
        log.info("request_not_found", "webapp.request", "跨日後")

        self.assertEqual(
            ["跨日前"],
            [r["message"] for r in self.records(rotated_log_name(started.date()))],
        )
        self.assertEqual(["跨日後"], [record["message"] for record in self.records()])

    def test_a_second_rotation_onto_the_same_day_keeps_both_batches(self):
        """Two runs of the server on one day must not erase each other."""
        yesterday = self.written_yesterday()
        self.open_log().close()

        again = open_webapp_log(
            self.data_root, clock=FixedClock(start_utc=yesterday + timedelta(hours=1))
        )
        again.info("server_start", "webapp.server", "昨天的第二批")
        again.close()
        self.open_log().info("server_start", "webapp.server", "今天")

        self.assertEqual(
            ["昨天的紀錄", "昨天的第二批"],
            [r["message"] for r in self.records(rotated_log_name(yesterday.date()))],
        )

    def test_a_file_holding_no_dated_record_is_left_where_it_is(self):
        directory = self.data_root / "logs"
        directory.mkdir()
        (directory / ACTIVE_LOG_NAME).write_text("有人手改過\n", encoding="utf-8")

        self.open_log().info("server_start", "webapp.server", "今天")

        self.assertEqual([], list(directory.glob("webapp-*.jsonl")))
        self.assertIn("有人手改過", (directory / ACTIVE_LOG_NAME).read_text("utf-8"))

    def rotate_with_a_competitor_in_the_gap(self):
        """Rotate twice, with the second finishing inside the first's decision.

        Two servers sharing a Data Root both notice the same finished day. The
        race is the gap between "this file belongs to yesterday" and acting on
        it, so the competitor is run from inside
        :func:`_recorded_day_and_identity` — the one read that answers both "which
        day is this" and "which file is this" — so that by the time the first
        caller acts, the day has already been moved by the other. Returns what
        the first caller ended as.
        """
        directory = self.data_root / "logs"
        today = self.clock.utc_now().astimezone(timezone.utc).date()
        real_read = log_module._recorded_day_and_identity
        state = {"raced": False}

        def read_then_a_competitor(path):
            answer = real_read(path)
            if not state["raced"]:
                state["raced"] = True
                log_module._rotate_finished_day(directory, today)
            return answer

        with mock.patch.object(
            log_module, "_recorded_day_and_identity", read_then_a_competitor
        ):
            try:
                log_module._rotate_finished_day(directory, today)
            except WebappLogError as exc:
                return "WebappLogError: {}".format(exc)
        self.assertTrue(state["raced"], "the competitor never ran")
        return "ok"

    def test_two_writers_rotating_the_same_day_move_it_exactly_once(self):
        """One copy of the records, and no writer told it failed.

        Read-then-append-then-unlink let both writers read the same bytes: the
        day's file ended up holding every record twice, and whichever unlinked
        second failed on a file the other had already dealt with. Neither of
        those is acceptable and one of them is data loss dressed as data.
        """
        yesterday = self.written_yesterday()

        result = self.rotate_with_a_competitor_in_the_gap()

        rotated = self.records(rotated_log_name(yesterday.date()))
        self.assertEqual("ok", result)
        self.assertEqual(["昨天的紀錄"], [record["message"] for record in rotated])

    def test_the_losing_writer_leaves_no_claim_behind_in_the_directory(self):
        self.written_yesterday()

        self.rotate_with_a_competitor_in_the_gap()

        left = sorted(p.name for p in (self.data_root / "logs").iterdir())
        self.assertEqual([rotated_log_name(self.clock.utc_now().date() - timedelta(days=1))], left)

    def test_one_writer_on_its_own_still_rotates(self):
        """FP direction: a rotation that refused everything would pass those two."""
        yesterday = self.written_yesterday()
        directory = self.data_root / "logs"

        log_module._rotate_finished_day(
            directory, self.clock.utc_now().astimezone(timezone.utc).date()
        )

        self.assertFalse((directory / ACTIVE_LOG_NAME).exists())
        self.assertEqual(
            ["昨天的紀錄"],
            [r["message"] for r in self.records(rotated_log_name(yesterday.date()))],
        )

    def rotate_after_the_active_name_was_reused(self):
        """Rotate, with the whole race finished before this caller's rename lands.

        The gap that matters is not "did somebody else rotate as well" — that one
        is already covered. It is that ``webapp.jsonl`` is a *name* today's writer
        recreates the instant it is free. So: this caller reads the file and
        decides it belongs to yesterday; the other writer rotates yesterday away
        and then puts a brand new file under the same name; and only then does
        this caller's ``os.rename`` run. It succeeds. What it moved is today's
        file, filed under a day it read off a file that is no longer there.

        The competitor is run from inside :func:`os.rename` because that is where
        the gap closes, and the patch lasts exactly one call.
        """
        directory = self.data_root / "logs"
        today = self.clock.utc_now().astimezone(timezone.utc).date()
        real_rename = log_module.os.rename
        state = {"raced": False}

        def rename_after_the_name_was_reused(source, target):
            if not state["raced"]:
                state["raced"] = True
                winner = open_webapp_log(self.data_root, clock=self.clock)
                winner.info("server_start", "webapp.server", "今天的紀錄")
                winner.close()
            return real_rename(source, target)

        with mock.patch.object(
            log_module.os, "rename", rename_after_the_name_was_reused
        ):
            try:
                log_module._rotate_finished_day(directory, today)
            except WebappLogError as exc:
                self.assertTrue(state["raced"], "the competitor never ran")
                return "WebappLogError: {}".format(exc)
        self.assertTrue(state["raced"], "the competitor never ran")
        return "ok"

    def test_a_rotation_that_lost_the_race_does_not_move_todays_file(self):
        """An atomic rename decides a pathname; it does not decide a file.

        Winning the rename proved the name was taken exactly once. It proved
        nothing about which file was under it, so the loser filed today's records
        into yesterday's log and left no active file at all — the two things a log
        exists to make impossible.
        """
        yesterday = self.written_yesterday()

        result = self.rotate_after_the_active_name_was_reused()

        self.assertEqual("ok", result)
        self.assertEqual(["今天的紀錄"], [r["message"] for r in self.records()])
        self.assertEqual(
            ["昨天的紀錄"],
            [r["message"] for r in self.records(rotated_log_name(yesterday.date()))],
        )

    def test_the_file_it_could_not_claim_is_handed_back_not_kept(self):
        """A claim on the wrong file is given up, and nothing is left lying about."""
        yesterday = self.written_yesterday()

        self.rotate_after_the_active_name_was_reused()

        self.assertEqual(
            sorted([ACTIVE_LOG_NAME, rotated_log_name(yesterday.date())]),
            sorted(entry.name for entry in (self.data_root / "logs").iterdir()),
        )

    def test_an_unraced_rotation_leaves_neither_a_claim_nor_the_active_file(self):
        """FP direction: checking identity must not refuse the file it should move.

        A rotation that handed every claim back would satisfy the two tests above
        and never rotate anything again.
        """
        yesterday = self.written_yesterday()
        directory = self.data_root / "logs"

        log_module._rotate_finished_day(
            directory, self.clock.utc_now().astimezone(timezone.utc).date()
        )

        self.assertEqual(
            [rotated_log_name(yesterday.date())],
            sorted(entry.name for entry in directory.iterdir()),
        )

    def test_a_claim_that_cannot_be_handed_back_says_where_the_records_are(self):
        """The declared failure: records are never lost, but somebody is told.

        Handing back is :func:`os.link` precisely so that it refuses a name a
        third writer has already taken rather than overwriting it. When it does
        refuse there is nothing safe left to do automatically — merging two
        writers' records by guesswork is not safer than stopping — so the message
        names the file the records are sitting in.
        """
        self.written_yesterday()

        with mock.patch.object(
            log_module.os, "link", mock.Mock(side_effect=FileExistsError("taken"))
        ):
            result = self.rotate_after_the_active_name_was_reused()

        self.assertIn("WebappLogError", result)
        self.assertIn(".rotating", result)

    def test_retention_never_deletes_a_claim_left_behind(self):
        """A ``.rotating`` file holds real records, so expiry must not reap it.

        It is not collected automatically either: a claim abandoned by a dead
        process and one in flight in a live process are indistinguishable from
        outside, and re-filing a live one would append the same records twice.
        Leaving it alone is the declared behaviour, and this is the part of that
        declaration a test can hold.
        """
        directory = self.data_root / "logs"
        directory.mkdir()
        orphan = directory / log_module._CLAIM_NAME_TEMPLATE.format(
            ACTIVE_LOG_NAME, 4242, "deadbeef"
        )
        orphan.write_text("{}\n", encoding="utf-8")

        self.open_log().info("server_start", "webapp.server", "今天")

        self.assertTrue(orphan.is_file())


class WebappLogRetentionTest(LogFixture, unittest.TestCase):
    """Opening the log deletes the days past the retention window, only those."""

    def rotated_day(self, days_ago, message="舊紀錄"):
        """Leave a rotated file dated ``days_ago`` days before the fixed today."""
        day = (self.clock.utc_now() - timedelta(days=days_ago)).date()
        directory = self.data_root / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / rotated_log_name(day)
        path.write_text(
            json.dumps({"message": message}, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return path

    def test_a_day_older_than_the_retention_window_is_deleted_on_open(self):
        expired = self.rotated_day(RETENTION_DAYS + 1)

        self.open_log()

        self.assertFalse(expired.exists())

    def test_the_oldest_day_still_inside_the_window_is_kept(self):
        kept = self.rotated_day(RETENTION_DAYS - 1)

        self.open_log()

        self.assertTrue(kept.is_file())

    def test_the_first_day_outside_the_window_is_the_one_after_that(self):
        """The boundary, from the other side: 30 days means 30 dated files."""
        expired = self.rotated_day(RETENTION_DAYS)

        self.open_log()

        self.assertFalse(expired.exists())

    def test_the_window_keeps_exactly_as_many_dated_files_as_it_says(self):
        """Counting is the assertion: an off-by-one keeps thirty-one of thirty."""
        for days_ago in range(RETENTION_DAYS + 3):
            self.rotated_day(days_ago)

        self.open_log()

        kept = sorted((self.data_root / "logs").glob("webapp-*.jsonl"))
        self.assertEqual(RETENTION_DAYS, len(kept), [p.name for p in kept])

    def test_files_this_module_never_named_are_left_alone(self):
        directory = self.data_root / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        strangers = [directory / "live-server.log", directory / "webapp-備份.jsonl"]
        for stranger in strangers:
            stranger.write_text("不是本模組命名的\n", encoding="utf-8")

        self.open_log()

        self.assertEqual(strangers, [path for path in strangers if path.is_file()])

    def test_a_name_shaped_right_but_dated_impossibly_is_left_alone(self):
        directory = self.data_root / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        impossible = directory / "webapp-2026-13-45.jsonl"
        impossible.write_text("不是日期\n", encoding="utf-8")

        self.open_log()

        self.assertTrue(impossible.is_file())

    def test_an_active_file_older_than_the_window_is_rotated_then_deleted(self):
        ancient = self.clock.utc_now() - timedelta(days=RETENTION_DAYS + 1)
        stale = open_webapp_log(self.data_root, clock=FixedClock(start_utc=ancient))
        stale.info("server_start", "webapp.server", "很久以前")
        stale.close()

        self.open_log()

        rotated = self.data_root / "logs" / rotated_log_name(ancient.date())
        self.assertFalse(rotated.exists())
        self.assertEqual([], self.records())


# -- fixtures for the pages -------------------------------------------------


SEAT_IDS = (
    "spot-technical",
    "derivatives",
    "onchain",
    "official-events",
    "news",
    "social-macro",
    "counter-evidence",
)


def write_run(
    data_root,
    run_id,
    question,
    *,
    assets=("BTC",),
    asset_class="crypto",
    level="green",
    adopted="affirmative",
    tally=None,
    consensus_status="consensus",
    artifacts=("report.html", "debate.html"),
    changed_seat=None,
    created_at_utc=None,
    period_days=None,
):
    """Write one finished run directory the index and the pages can both read.

    ``created_at_utc`` and ``period_days`` are written only when a caller asks
    for them, so every fixture predating Ticket 12 keeps exactly the records it
    had — and so the sweep's "this run does not say when it expires" branch has
    a real fixture rather than a doctored one.
    """
    date_dir, name = run_dir_parts(run_id, question)
    run_dir = Path(data_root) / "runs" / date_dir / name
    run_dir.mkdir(parents=True)
    tally = dict(tally or {"affirmative": 6, "negative_side": 1, "undecided": 0})
    stances = sorted(tally)

    def write(file_name, payload):
        (run_dir / file_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    write(
        "manifest.json",
        {
            "run_id": run_id,
            "question": question,
            "assets": list(assets),
            "question_type": "open_proposition",
            "completed_at_utc": "2026-08-01T02:15:00Z",
        },
    )
    question_record = {
        "run_id": run_id,
        "question": question,
        "asset_class": asset_class,
        "assets": list(assets),
        "question_type": "open_proposition",
        "proposition": question,
    }
    if created_at_utc is not None:
        question_record["created_at_utc"] = created_at_utc
    if period_days is not None:
        question_record["period_days"] = period_days
    write("question.json", question_record)
    write(
        "votes.json",
        {
            "run_id": run_id,
            "stances": stances,
            "tally": tally,
            "adopted_stance": adopted,
            "consensus_status": consensus_status,
            "seat_count": len(SEAT_IDS),
            "valid_vote_count": sum(tally.values()),
            "stop_reason": "threshold_met",
            "threshold_required": 6,
            "votes": [
                _seat_vote(seat_id, adopted, changed=seat_id == changed_seat)
                for seat_id in SEAT_IDS
            ],
        },
    )
    write(
        "report.json",
        {
            "run_id": run_id,
            "confidence": {"level": level, "icon": "🟢", "label": "綠燈"},
        },
    )
    (run_dir / "evidence.jsonl").write_text(
        "".join(
            json.dumps(_evidence_card(seat_id, run_id), ensure_ascii=False) + "\n"
            for seat_id in SEAT_IDS
        ),
        encoding="utf-8",
    )
    for artifact in artifacts:
        (run_dir / artifact).write_text(
            "<!doctype html><title>{0}</title><p>{0} 的內容</p>".format(artifact),
            encoding="utf-8",
        )
    return run_dir


def _seat_vote(seat_id, adopted, changed=False):
    return {
        "seat_id": seat_id,
        "state": "valid",
        "initial_stance": "negative_side" if changed else adopted,
        "initial_public_reason": "{} 的第一輪理由".format(seat_id),
        "initial_evidence_ids": ["{}-01".format(seat_id)],
        "final_stance": adopted,
        "final_public_reason": "{} 的最終理由".format(seat_id),
        "final_evidence_ids": ["{}-01".format(seat_id)],
        "stance_changed": changed,
        "stance_change_reason": "被反方證據說服" if changed else None,
        "vote_changes": [
            {
                "message_id": "{}-final".format(seat_id),
                "before": "negative_side" if changed else adopted,
                "after": adopted,
                "reason": "被反方證據說服" if changed else None,
                "public_reason": "{} 的最終理由".format(seat_id),
                "evidence_ids": ["{}-01".format(seat_id)],
                "elapsed_ms": 240000,
            }
        ],
    }


def _evidence_card(seat_id, run_id):
    return {
        "evidence_id": "{}-01".format(seat_id),
        "run_id": run_id,
        "seat_id": seat_id,
        "asset": "BTC",
        "category": seat_id,
        "statement": "{} 提交的證據陳述".format(seat_id),
        "direction": "support",
        "source_url": "https://example.invalid/{}".format(seat_id),
        "source_origin": "example.invalid",
        "source_tier": 1,
        "published_at_utc": "2026-08-01T01:00:00Z",
        "retrieved_at_utc": "2026-08-01T02:00:01Z",
        "excerpt": "{} 的引文".format(seat_id),
        "credibility_note": "測試資料。",
    }


class _Socket:
    """Just enough socket for one request through the real handler.

    ``break_after`` makes the client hang up: after that many writes every
    further one raises the error a real closed connection would, which is how
    the streaming routes are tested against a reader who closed the tab.
    """

    def __init__(self, request_bytes, break_after=None):
        self.incoming = io.BytesIO(request_bytes)
        self.outgoing = io.BytesIO()
        self.break_after = break_after
        self.writes = 0

    def makefile(self, mode, *_args, **_kwargs):
        return self.incoming if "r" in mode else self.outgoing

    def sendall(self, data):
        self.writes += 1
        if self.break_after is not None and self.writes > self.break_after:
            raise BrokenPipeError("讀者已離開")
        self.outgoing.write(data)

    def close(self):
        return None


class FakeProcess:
    """A launch that this test drives instead of forking."""

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def finish(self, returncode=0):
        self.returncode = returncode


class StepClock:
    """A monotonic clock that moves one step per reading, and no faster."""

    def __init__(self, step=1.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


class Response:
    """One parsed HTTP response."""

    def __init__(self, raw):
        head, _, body = raw.partition(b"\r\n\r\n")
        status_line, _, header_text = head.partition(b"\r\n")
        self.status = int(status_line.split()[1])
        self.headers = BytesParser().parsebytes(header_text)
        self.body_bytes = body
        self.body = body.decode("utf-8", errors="replace")


class PageFixture:
    """A Data Root, a log and a handler — no listening socket anywhere.

    Nothing here forks, sleeps or reads a wall clock: the launch process, the
    stream's pacing and the stream's clock are all injected, so every route
    including the streaming one runs to completion inside the test.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.clock = FixedClock()
        self.log = open_webapp_log(self.data_root, clock=self.clock)
        self.addCleanup(self.log.close)
        self.spawned = []
        self.processes = []
        self.lock = launch_module.LaunchLock()
        self.build_handler()

    def build_handler(self, stream=None, spawn=None):
        """Rebuild the handler; a test that needs different pacing calls this."""
        self.stream = stream or self.single_pass_stream()
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=spawn or self.spawn,
        )
        return self.handler

    def single_pass_stream(self, **overrides):
        """A stream that looks once and stops, so no test waits for a clock."""
        options = {
            "poll_seconds": 0,
            "heartbeat_seconds": 10 ** 6,
            "max_seconds": 0,
            "sleeper": lambda _seconds: None,
            "monotonic": StepClock(),
        }
        options.update(overrides)
        return StreamSettings(**options)

    def spawn(self, args, **options):
        self.spawned.append((args, options))
        process = FakeProcess()
        self.processes.append(process)
        return process

    def request(self, raw, break_after=None):
        connection = _Socket(raw.encode("utf-8"), break_after=break_after)
        self.handler(connection, ("127.0.0.1", 54321), None)
        return Response(connection.outgoing.getvalue())

    def get(self, path, headers=(), break_after=None):
        lines = ["GET {} HTTP/1.1".format(path), "Host: 127.0.0.1"]
        lines += ["{}: {}".format(name, value) for name, value in headers]
        return self.request("\r\n".join(lines) + "\r\n\r\n", break_after=break_after)

    def post(self, path, fields):
        body = urlencode(fields)
        raw = (
            "POST {} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: {}\r\n\r\n{}".format(
                path, len(body.encode("utf-8")), body
            )
        )
        return self.request(raw)

    def write_certificate(self):
        """A READY certificate ``launcher`` accepts, so a launch is not refused."""
        preflight_id = "20260806T005926Z-aaa111"
        manifest = {
            "schema_version": "1.0.0",
            "status": "READY",
            "provider_capabilities_ready": True,
            "generated_at_utc": "2026-08-06T00:59:26Z",
        }
        manifest_path = self.data_root / "preflight" / preflight_id / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return write_ready_certificate(
            self.data_root, preflight_id, manifest, manifest_path
        )

    def records(self):
        path = self.data_root / "logs" / ACTIVE_LOG_NAME
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def listed_run_ids(self, body):
        """The run ids the history list links to, in the order they appear."""
        return re.findall(r'href="/run/([^"/?]+)"', body)

    def complaints(self, body):
        """The announced region listing conditions the page could not use."""
        found = re.search(r'role="alert".*?</section>', body, re.DOTALL)
        return found.group(0) if found else ""

    def index_two_runs(self):
        """Two indexed runs that differ in every filterable column."""
        self.btc = write_run(
            self.data_root,
            "20260801T020000Z-btc-aaaa11",
            "BTC 未來七天會不會漲",
            assets=("BTC",),
            asset_class="crypto",
            level="green",
        )
        self.tsmc = write_run(
            self.data_root,
            "20260705T020000Z-2330-bbbb22",
            "2330 未來七天會不會漲",
            assets=("2330",),
            asset_class="tw_stock",
            level="blue",
            changed_seat="counter-evidence",
        )
        rebuild_index(self.data_root)


# -- history query ----------------------------------------------------------


class HistoryFilterTest(PageFixture, unittest.TestCase):
    """Filtering is `run_index`'s job; this page only translates the URL."""

    def setUp(self):
        super().setUp()
        self.index_two_runs()

    def test_with_no_filters_every_indexed_run_is_listed_newest_first(self):
        listed = self.listed_run_ids(self.get("/history").body)

        self.assertEqual(
            ["20260801T020000Z-btc-aaaa11", "20260705T020000Z-2330-bbbb22"], listed
        )

    def test_a_date_range_keeps_only_the_runs_inside_it(self):
        body = self.get("/history?date_from=2026-07-01&date_to=2026-07-31").body

        self.assertEqual(["20260705T020000Z-2330-bbbb22"], self.listed_run_ids(body))

    def test_an_asset_class_keeps_only_that_class(self):
        body = self.get("/history?asset_class=tw_stock").body

        self.assertEqual(["20260705T020000Z-2330-bbbb22"], self.listed_run_ids(body))

    def test_a_light_keeps_only_the_runs_that_earned_it(self):
        body = self.get("/history?confidence=blue").body

        self.assertEqual(["20260705T020000Z-2330-bbbb22"], self.listed_run_ids(body))

    def test_a_keyword_matches_a_substring_of_the_question(self):
        body = self.get("/history?keyword=2330").body

        self.assertEqual(["20260705T020000Z-2330-bbbb22"], self.listed_run_ids(body))

    def test_a_blank_field_is_not_applied_as_an_empty_value(self):
        """An untouched form field submits ``""``; that is not a filter."""
        body = self.get("/history?date_from=&date_to=&asset_class=&confidence=&keyword=").body

        self.assertEqual(2, len(self.listed_run_ids(body)))

    def test_a_field_holding_only_spaces_is_not_applied_either(self):
        body = self.get("/history?keyword=%20%20").body

        self.assertEqual(2, len(self.listed_run_ids(body)))

    def test_a_percent_sign_in_a_keyword_is_a_character_not_a_wildcard(self):
        body = self.get("/history?keyword=%25").body

        self.assertEqual([], self.listed_run_ids(body))
        self.assertNotIn("index-backfill", body)

    def test_a_class_no_configuration_declares_is_still_a_usable_filter(self):
        write_run(
            self.data_root,
            "20260801T030000Z-xau-cccc33",
            "黃金會不會漲",
            asset_class="commodity",
        )
        rebuild_index(self.data_root)

        body = self.get("/history?asset_class=commodity").body

        self.assertEqual(["20260801T030000Z-xau-cccc33"], self.listed_run_ids(body))

    def test_a_malformed_date_is_reported_and_not_used_as_a_filter(self):
        body = self.get("/history?date_from=8/1/2026").body

        self.assertIn("8/1/2026", self.complaints(body))
        self.assertEqual(2, len(self.listed_run_ids(body)))

    def test_a_well_formed_date_raises_no_complaint(self):
        body = self.get("/history?date_from=2026-07-01").body

        self.assertEqual("", self.complaints(body))

    def test_the_submitted_values_come_back_in_the_form(self):
        body = self.get("/history?keyword=2330&asset_class=tw_stock").body

        self.assertIn('value="2330"', body)
        self.assertIn('value="tw_stock"', body)

    def test_a_question_carrying_markup_is_shown_as_text(self):
        write_run(
            self.data_root,
            "20260801T040000Z-x-dddd44",
            "<script>alert(1)</script> 會不會漲",
        )
        rebuild_index(self.data_root)

        body = self.get("/history").body

        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)


class HistoryLimitTest(PageFixture, unittest.TestCase):
    """A row cap typed by a user is not the row cap `query_runs` accepts."""

    def setUp(self):
        super().setUp()
        self.index_two_runs()

    def test_a_negative_cap_is_refused_rather_than_lifting_the_cap(self):
        """``query_runs`` reads a negative limit as ``ValueError``, never as all."""
        response = self.get("/history?limit=-1")

        self.assertEqual(200, response.status)
        self.assertIn("-1", self.complaints(response.body))
        self.assertEqual(2, len(self.listed_run_ids(response.body)))

    def test_a_cap_that_is_not_a_number_is_refused_the_same_way(self):
        response = self.get("/history?limit=many")

        self.assertEqual(200, response.status)
        self.assertIn("many", self.complaints(response.body))
        self.assertEqual(2, len(self.listed_run_ids(response.body)))

    def test_a_cap_of_zero_is_a_real_answer_and_not_a_complaint(self):
        response = self.get("/history?limit=0")

        self.assertEqual([], self.listed_run_ids(response.body))
        self.assertEqual("", self.complaints(response.body))

    def test_a_cap_inside_the_range_is_honoured_exactly(self):
        body = self.get("/history?limit=1").body

        self.assertEqual(["20260801T020000Z-btc-aaaa11"], self.listed_run_ids(body))
        self.assertEqual("", self.complaints(body))

    def test_an_empty_cap_field_falls_back_to_the_default_without_complaint(self):
        body = self.get("/history?limit=").body

        self.assertEqual(2, len(self.listed_run_ids(body)))
        self.assertEqual("", self.complaints(body))

    def test_a_list_that_filled_its_cap_says_there_may_be_more(self):
        """A capped page and a complete one look identical otherwise."""
        body = self.get("/history?limit=1").body

        self.assertIn("已達本次筆數上限 1", body)

    def test_a_list_that_did_not_fill_its_cap_claims_nothing_of_the_sort(self):
        body = self.get("/history?limit=5").body

        self.assertNotIn("已達本次筆數上限", body)


class HistoryEmptyStateTest(PageFixture, unittest.TestCase):
    """A Data Root with no index is a first run, not a crash."""

    def test_a_missing_index_renders_an_explained_page_not_a_traceback(self):
        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertNotIn("Traceback", response.body)
        self.assertIn("index-backfill", response.body)
        self.assertIn(str(self.data_root), response.body)

    def test_a_missing_index_is_recorded_once_it_is_seen(self):
        self.get("/history")

        self.assertIn(
            "index_unavailable", [record["event"] for record in self.records()]
        )

    def test_an_index_that_exists_says_nothing_about_backfilling(self):
        self.index_two_runs()

        body = self.get("/history").body

        self.assertNotIn("index-backfill", body)

    def test_no_matching_run_is_told_apart_from_no_index_at_all(self):
        self.index_two_runs()

        body = self.get("/history?keyword=沒有這種題目").body

        self.assertEqual([], self.listed_run_ids(body))
        self.assertIn("沒有符合條件", body)
        self.assertNotIn("index-backfill", body)

    def test_an_unreadable_index_says_so_instead_of_tracebacking(self):
        self.index_two_runs()
        database = self.data_root / "runs" / "index.db"
        database.write_text("這不是 SQLite 檔", encoding="utf-8")

        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertNotIn("Traceback", response.body)
        self.assertIn("index-backfill", response.body)


# -- run detail -------------------------------------------------------------


class RunDetailTest(PageFixture, unittest.TestCase):
    """Report, seven seats and their changes, evidence, transcript."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def setUp(self):
        super().setUp()
        self.run_dir = write_run(
            self.data_root,
            self.RUN_ID,
            "BTC 未來七天會不會漲",
            changed_seat="counter-evidence",
        )

    def detail(self):
        return self.get("/run/{}".format(self.RUN_ID))

    def test_the_page_opens_for_a_run_that_was_never_indexed(self):
        """Detail reads the run's own artifacts; the index is only for finding."""
        self.assertEqual(200, self.detail().status)

    def test_the_asset_class_is_shown_in_chinese_not_as_its_stored_key(self):
        """crypto is 加密資產 on screen — the English leak the user complained of."""
        body = self.detail().body

        self.assertIn("加密資產", body)
        self.assertNotIn("<dd>crypto</dd>", body)

    def test_the_stored_question_type_and_stop_reason_are_visible_only_as_chinese(self):
        body = self.detail().body

        self.assertIn("<dt>題型</dt><dd>開放命題</dd>", body)
        self.assertIn("<dt>停止原因</dt><dd>達到票數門檻</dd>", body)
        self.assertNotIn("<dd>open_proposition</dd>", body)
        self.assertNotIn("<dd>threshold_met</dd>", body)

    def test_the_report_is_shown_from_the_runs_own_report_html(self):
        body = self.detail().body

        self.assertIn('src="/run/{}/report.html"'.format(self.RUN_ID), body)

    def test_the_report_route_serves_that_runs_bytes_unchanged(self):
        response = self.get("/run/{}/report.html".format(self.RUN_ID))

        self.assertEqual(200, response.status)
        self.assertEqual((self.run_dir / "report.html").read_bytes(), response.body_bytes)

    def test_all_seven_seats_appear_with_their_final_stance(self):
        body = self.detail().body

        for seat_id in SEAT_IDS:
            self.assertIn(seat_id, body, seat_id)

    def test_a_seat_that_changed_its_vote_shows_the_change_and_the_reason(self):
        body = self.detail().body

        self.assertIn("被反方證據說服", body)

    def test_a_seat_that_never_changed_is_not_reported_as_having_changed(self):
        body = write_run(
            self.data_root,
            "20260802T020000Z-btc-eeee55",
            "BTC 明天會不會漲",
        ) and self.get("/run/20260802T020000Z-btc-eeee55").body

        self.assertNotIn("被反方證據說服", body)
        self.assertIn("維持原立場", body)

    def test_every_evidence_card_is_shown_with_its_source(self):
        body = self.detail().body

        for seat_id in SEAT_IDS:
            self.assertIn("{}-01".format(seat_id), body)
            self.assertIn("https://example.invalid/{}".format(seat_id), body)

    def test_the_transcript_is_linked_and_the_link_serves_it(self):
        body = self.detail().body
        self.assertIn('href="/run/{}/debate.html"'.format(self.RUN_ID), body)

        response = self.get("/run/{}/debate.html".format(self.RUN_ID))

        self.assertEqual(200, response.status)
        self.assertEqual((self.run_dir / "debate.html").read_bytes(), response.body_bytes)

    def test_a_run_without_a_report_says_so_instead_of_framing_nothing(self):
        write_run(
            self.data_root,
            "20260803T020000Z-btc-ffff66",
            "BTC 下週會不會漲",
            artifacts=(),
        )

        body = self.get("/run/20260803T020000Z-btc-ffff66").body

        self.assertIn("尚未產生 report.html", body)
        self.assertNotIn('src="/run/20260803T020000Z-btc-ffff66/report.html"', body)

    def test_a_run_without_a_transcript_offers_no_link_to_one(self):
        write_run(
            self.data_root,
            "20260804T020000Z-btc-999977",
            "BTC 下下週會不會漲",
            artifacts=("report.html",),
        )

        body = self.get("/run/20260804T020000Z-btc-999977").body

        self.assertNotIn('href="/run/20260804T020000Z-btc-999977/debate.html"', body)
        self.assertIn("尚未產生 debate.html", body)

    def test_a_run_whose_votes_are_missing_says_so_rather_than_showing_none(self):
        (self.run_dir / "votes.json").unlink()

        body = self.detail().body

        self.assertIn("尚未產生 votes.json", body)

    def test_a_votes_file_that_records_no_seat_is_not_called_missing(self):
        """It was written and it read; saying it was never produced is false."""
        (self.run_dir / "votes.json").write_text(
            json.dumps({"votes": [], "stances": []}), encoding="utf-8"
        )

        body = self.detail().body

        self.assertNotIn("尚未產生 votes.json", body)
        self.assertIn("沒有任何席位紀錄", body)

    def test_an_evidence_file_holding_no_card_is_not_called_missing(self):
        (self.run_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        body = self.detail().body

        self.assertNotIn("尚未產生 evidence.jsonl", body)
        self.assertIn("沒有任何證據卡", body)

    def test_a_comparison_ballot_names_the_stances_with_the_runs_own_assets(self):
        """``votes.json`` carries no assets, so they have to be handed in."""
        write_run(
            self.data_root,
            "20260805T020000Z-btc-abab12",
            "BTC 和 ETH 哪個強",
            assets=("BTC", "ETH"),
            adopted="asset_a_stronger",
            tally={"asset_a_stronger": 6, "asset_b_stronger": 1, "no_clear_difference": 0},
        )

        body = self.get("/run/20260805T020000Z-btc-abab12").body

        self.assertIn("BTC較優", body)
        self.assertNotIn("前者較優", body)

    def test_a_report_whose_confidence_is_not_an_object_does_not_break_the_page(self):
        (self.run_dir / "report.json").write_text(
            json.dumps({"confidence": "green"}), encoding="utf-8"
        )

        self.assertEqual(200, self.detail().status)

    def test_a_run_id_that_names_nothing_is_a_404_page(self):
        response = self.get("/run/20261231T235959Z-btc-zzzz99")

        self.assertEqual(404, response.status)
        self.assertNotIn("Traceback", response.body)

    def test_a_run_id_that_is_not_a_run_id_is_a_404_page(self):
        response = self.get("/run/..%2f..%2fetc")

        self.assertEqual(404, response.status)
        self.assertNotIn("Traceback", response.body)

    def test_an_artifact_this_page_never_links_to_is_not_served(self):
        response = self.get("/run/{}/votes.json".format(self.RUN_ID))

        self.assertEqual(404, response.status)


# -- server, headers and log --------------------------------------------------


class RequestLogTest(PageFixture, unittest.TestCase):
    """Request errors are recorded; what a user typed is not."""

    def events(self):
        return [record["event"] for record in self.records()]

    def test_a_404_is_recorded_with_its_path(self):
        self.get("/no-such-page")

        recorded = [r for r in self.records() if r["event"] == "request_not_found"]
        self.assertEqual(1, len(recorded))
        self.assertIn("/no-such-page", recorded[0]["message"])

    def test_a_404_is_recorded_at_warning_level_from_the_request_source(self):
        self.get("/no-such-page")

        record = [r for r in self.records() if r["event"] == "request_not_found"][0]
        self.assertEqual("WARNING", record["level"])
        self.assertEqual("webapp.request", record["source"])

    def test_what_the_user_typed_into_the_form_is_not_copied_into_the_log(self):
        self.index_two_runs()

        self.get("/history?keyword=某個很私密的題目")

        self.assertNotIn("某個很私密的題目", json.dumps(self.records(), ensure_ascii=False))

    def test_a_page_that_worked_is_not_logged_as_an_error(self):
        self.index_two_runs()

        self.get("/")

        self.assertEqual([], [event for event in self.events() if "not_found" in event])

    def test_an_unexpected_failure_is_recorded_rather_than_dying_silently(self):
        with mock.patch.object(views, "history_data", side_effect=RuntimeError("壞了")):
            response = self.get("/history")

        self.assertEqual(500, response.status)
        failures = [r for r in self.records() if r["event"] == "request_failed"]
        self.assertEqual(1, len(failures))
        self.assertEqual("ERROR", failures[0]["level"])
        self.assertIn("RuntimeError", failures[0]["message"])

    def test_an_unexpected_failure_does_not_put_its_internals_on_the_page(self):
        with mock.patch.object(views, "history_data", side_effect=RuntimeError("壞了")):
            response = self.get("/history")

        self.assertNotIn("壞了", response.body)
        self.assertNotIn("Traceback", response.body)


class ResponseHeaderTest(PageFixture, unittest.TestCase):
    """The page depends on nothing it does not serve itself."""

    def setUp(self):
        super().setUp()
        self.index_two_runs()

    def test_the_page_is_utf8_html(self):
        self.assertEqual(
            "text/html; charset=utf-8", self.get("/history").headers["Content-Type"]
        )

    def test_the_policy_forbids_scripts_and_allows_only_same_origin_frames(self):
        policy = self.get("/history").headers["Content-Security-Policy"]

        self.assertIn("script-src 'none'", policy)
        self.assertIn("frame-src 'self'", policy)
        self.assertIn("default-src 'none'", policy)

    def test_no_page_this_module_renders_carries_a_script(self):
        for path in ("/history", "/run/20260801T020000Z-btc-aaaa11", "/nope",
                     "/settings"):
            self.assertNotIn("<script", self.get(path).body, path)


class ServerLifecycleTest(unittest.TestCase):
    """Binding is the step that can fail, and it must fail loudly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.log = open_webapp_log(self.data_root, clock=FixedClock())
        self.addCleanup(self.log.close)

    def records(self):
        path = self.data_root / "logs" / ACTIVE_LOG_NAME
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def occupied_port(self):
        taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        return taken.getsockname()[1]

    def test_a_free_port_binds_and_closes_without_leaving_a_thread(self):
        before = set(threading.enumerate())

        server = create_webapp_server(self.data_root, self.log, port=0)
        server.server_close()

        self.assertEqual(before, set(threading.enumerate()))

    def test_an_occupied_port_refuses_to_start_and_names_the_port(self):
        port = self.occupied_port()

        with self.assertRaises(WebappError) as refused:
            create_webapp_server(self.data_root, self.log, port=port)

        message = str(refused.exception)
        self.assertIn(str(port), message)
        self.assertIn("占用", message)

    def test_an_occupied_port_says_what_to_do_about_it(self):
        port = self.occupied_port()

        with self.assertRaises(WebappError) as refused:
            create_webapp_server(self.data_root, self.log, port=port)

        self.assertIn("--port", str(refused.exception))

    def test_the_failure_reaches_the_log_because_the_log_opened_first(self):
        port = self.occupied_port()

        with self.assertRaises(WebappError):
            create_webapp_server(self.data_root, self.log, port=port)

        failures = [r for r in self.records() if r["event"] == "server_start_failed"]
        self.assertEqual(1, len(failures))
        self.assertEqual("ERROR", failures[0]["level"])
        self.assertIn(str(port), failures[0]["message"])

    def test_a_port_that_bound_is_not_recorded_as_a_failure(self):
        server = create_webapp_server(self.data_root, self.log, port=0)
        self.addCleanup(server.server_close)

        self.assertEqual(
            [], [r for r in self.records() if r["event"] == "server_start_failed"]
        )

    def test_a_bind_that_failed_for_another_reason_does_not_claim_the_port_is_taken(self):
        """EACCES is not EADDRINUSE, and telling the user to close a program
        that does not exist is worse than saying what actually happened."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 1))
        except PermissionError:
            pass
        else:
            probe.close()
            self.skipTest("這個環境允許非特權程序綁定 port 1")
        probe.close()

        with self.assertRaises(WebappError) as refused:
            create_webapp_server(self.data_root, self.log, port=1)

        message = str(refused.exception)
        self.assertNotIn("占用", message)
        self.assertIn("--port", message)
        self.assertIn("PermissionError", message)

    def test_a_port_outside_the_range_is_refused_before_any_socket_is_made(self):
        with self.assertRaises(WebappError) as refused:
            create_webapp_server(self.data_root, self.log, port=70000)

        self.assertIn("70000", str(refused.exception))

    def test_the_default_port_is_the_one_the_ticket_names(self):
        self.assertEqual(8765, DEFAULT_PORT)

    def test_the_server_listens_on_the_loopback_address_only(self):
        server = create_webapp_server(self.data_root, self.log, port=0)
        self.addCleanup(server.server_close)

        self.assertEqual("127.0.0.1", server.server_address[0])


# -- read only ---------------------------------------------------------------


class ReadOnlyRunTest(PageFixture, unittest.TestCase):
    """Proved by taking write permission away, not by promising not to."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def setUp(self):
        super().setUp()
        write_run(
            self.data_root, self.RUN_ID, "BTC 未來七天會不會漲", changed_seat="news"
        )
        rebuild_index(self.data_root)
        self.runs_root = self.data_root / "runs"
        self.addCleanup(self._restore)
        for path in sorted(self.runs_root.rglob("*"), reverse=True):
            path.chmod(stat.S_IRUSR | stat.S_IXUSR if path.is_dir() else stat.S_IRUSR)
        self.runs_root.chmod(stat.S_IRUSR | stat.S_IXUSR)

    def _restore(self):
        self.runs_root.chmod(stat.S_IRWXU)
        for path in self.runs_root.rglob("*"):
            path.chmod(stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR)

    def fingerprint(self):
        return {
            str(path.relative_to(self.runs_root)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.runs_root.rglob("*"))
            if path.is_file()
        }

    def test_every_page_still_works_with_the_run_tree_read_only(self):
        for path in (
            "/",
            "/?keyword=BTC",
            "/run/{}".format(self.RUN_ID),
            "/run/{}/report.html".format(self.RUN_ID),
            "/run/{}/debate.html".format(self.RUN_ID),
        ):
            self.assertEqual(200, self.get(path).status, path)

    def test_nothing_under_runs_changes_while_the_pages_are_served(self):
        before = self.fingerprint()

        for path in (
            "/",
            "/?keyword=BTC",
            "/?limit=-1",
            "/run/{}".format(self.RUN_ID),
            "/run/{}/report.html".format(self.RUN_ID),
            "/run/{}/debate.html".format(self.RUN_ID),
            "/run/20261231T235959Z-btc-zzzz99",
            "/nope",
        ):
            self.get(path)

        self.assertEqual(before, self.fingerprint())


# -- accessibility -----------------------------------------------------------


def _channel(value):
    ratio = value / 255
    return ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4


def luminance(colour):
    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(first, second):
    darker, lighter = sorted((luminance(first), luminance(second)))
    return (lighter + 0.05) / (darker + 0.05)


class ContrastTest(unittest.TestCase):
    """Every colour pair the pages actually use, measured.

    Computed from :data:`pages.MEASURED_COLOURS` and
    :data:`pages.CONTRAST_REQUIREMENTS`, which is the whole point: there is no
    second copy of a colour here to fall out of date with the sheet, and the
    requirement list is generated from each token's role rather than typed out,
    so a colour added to the palette cannot arrive without a minimum.
    ``tests/test_design_tokens.py`` holds the checks on the *shape* of that
    table; this holds the measurement.

    One palette, since Spec R-004 retired dark mode. The colours are the
    *measured* ones — the palette with every translucent surface flattened over
    what sits behind it — because a ratio taken against ``rgba(...)`` would be a
    ratio of a colour nobody looks at.
    """

    def measured(self):
        for foreground, background, minimum in pages.CONTRAST_REQUIREMENTS:
            yield (
                foreground,
                background,
                contrast_ratio(
                    pages.MEASURED_COLOURS[foreground],
                    pages.MEASURED_COLOURS[background],
                ),
                minimum,
            )

    def test_each_declared_pair_meets_its_minimum(self):
        """Prints the whole measured table: a ratio that is only asserted is a
        ratio nobody can quote in a review.

        The comparison is on the measured ratio, never on a rounded copy of it:
        ``round(ratio, 2) >= minimum`` accepts everything from
        ``minimum - 0.005`` upwards, so two decimal places are for reading and
        the raw number is for deciding.
        """
        print(
            "\n".join(
                "{:>16} on {:<14} {:6.2f}:1  needs {}:1".format(
                    foreground, background, ratio, minimum
                )
                for foreground, background, ratio, minimum in self.measured()
            )
        )
        for foreground, background, ratio, minimum in self.measured():
            self.assertTrue(
                ratio >= minimum,
                "{} on {} = {:.4f}:1 (needs {}:1)".format(
                    foreground, background, ratio, minimum
                ),
            )

    def test_the_site_keeps_exactly_one_palette(self):
        """Spec R-004: the dark palette is gone, so there is one table of
        colours and one set of ratios to keep true."""
        self.assertFalse(hasattr(pages, "THEMES"))
        self.assertEqual(set(pages.PALETTE), set(pages.MEASURED_COLOURS))

    def test_every_requirement_names_tokens_that_exist(self):
        declared = set(pages.PALETTE)
        for foreground, background, _ in pages.CONTRAST_REQUIREMENTS:
            self.assertIn(foreground, declared)
            self.assertIn(background, declared)


class KeyboardAndSemanticsTest(PageFixture, unittest.TestCase):
    """Semantic HTML, labelled controls and a usable tab order."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def setUp(self):
        super().setUp()
        self.index_two_runs()
        self.history = self.get("/history").body
        self.detail = self.get("/run/{}".format(self.RUN_ID)).body

    def pages_under_test(self):
        return {
            "history": self.history,
            "detail": self.detail,
            "settings": self.get("/settings").body,
        }

    def test_every_page_declares_its_language(self):
        for name, body in self.pages_under_test().items():
            self.assertIn('<html lang="zh-Hant">', body, name)

    def test_every_page_has_exactly_one_top_level_heading(self):
        for name, body in self.pages_under_test().items():
            self.assertEqual(1, body.count("<h1"), name)

    def test_every_page_starts_with_a_skip_link_into_the_main_landmark(self):
        for name, body in self.pages_under_test().items():
            self.assertLess(body.index('href="#main"'), body.index("<header"), name)
            self.assertIn('<main id="main"', body, name)

    def test_every_form_control_is_named_by_a_label_that_points_at_it(self):
        controls = re.findall(r'<input[^>]*\bid="([^"]+)"', self.history)
        labelled = re.findall(r'<label[^>]*\bfor="([^"]+)"', self.history)

        self.assertTrue(controls)
        self.assertEqual(sorted(controls), sorted(set(labelled) & set(controls)))

    def test_every_table_names_its_columns_as_scoped_headers(self):
        for name, body in self.pages_under_test().items():
            for table in re.findall(r"<table.*?</table>", body, re.DOTALL):
                self.assertIn('<th scope="col"', table, name)

    def test_no_page_forces_an_element_ahead_of_the_natural_tab_order(self):
        for name, body in self.pages_under_test().items():
            forced = [value for value in re.findall(r'tabindex="(-?\d+)"', body)]
            self.assertEqual([], [value for value in forced if int(value) > 0], name)

    def test_the_focus_ring_is_drawn_where_the_browser_would_hide_it(self):
        self.assertIn(":focus-visible", self.get("/static/site.css").body)

    def test_the_framed_report_is_named_for_a_screen_reader(self):
        frame = re.search(r"<iframe[^>]*>", self.detail).group(0)

        self.assertIn("title=", frame)

    def test_each_light_the_contract_declares_has_a_word_of_its_own(self):
        self.assertEqual(set(CONFIDENCE_LEVELS), set(pages.CONFIDENCE_WORDS))

    def test_a_light_no_contract_declares_is_shown_as_it_was_recorded(self):
        write_run(
            self.data_root, "20260801T050000Z-btc-777788", "BTC 會漲嗎", level="teal"
        )
        rebuild_index(self.data_root)

        self.assertIn("teal", self.get("/history?confidence=teal").body)

    def test_the_class_suggestions_are_the_ones_the_intake_declares(self):
        suggested = re.findall(r'<option value="([^"]+)"', self.history)

        self.assertEqual(list(ASSET_CLASSES), [v for v in suggested if v in ASSET_CLASSES])


class FilterTranslationTest(unittest.TestCase):
    """The one place a URL becomes `query_runs` arguments."""

    def translate(self, query):
        return views.parse_filters(query)

    def test_an_absent_parameter_becomes_none_rather_than_an_empty_string(self):
        filters, problems = self.translate({})

        self.assertIsNone(filters["keyword"])
        self.assertEqual([], problems)

    def test_a_default_row_cap_is_applied_when_the_user_names_none(self):
        filters, _ = self.translate({})

        self.assertEqual(views.DEFAULT_ROW_LIMIT, filters["limit"])

    def test_a_negative_cap_never_reaches_the_query(self):
        filters, problems = self.translate({"limit": ["-5"]})

        self.assertEqual(views.DEFAULT_ROW_LIMIT, filters["limit"])
        self.assertEqual(1, len(problems))

    def test_zero_reaches_the_query_because_zero_is_an_answer(self):
        filters, problems = self.translate({"limit": ["0"]})

        self.assertEqual(0, filters["limit"])
        self.assertEqual([], problems)

    def test_the_translated_names_are_the_ones_query_runs_declares(self):
        filters, _ = self.translate({})

        self.assertEqual(
            {"date_from", "date_to", "asset_class", "confidence_level", "keyword", "limit"},
            set(filters),
        )


class AssetClassLabelTest(unittest.TestCase):
    """A stored asset class reaches a Chinese page as a Chinese word.

    The user's whole complaint was raw English on screen. ``asset_class`` was
    the one value still shown as it is stored — ``crypto`` rather than 加密資產.
    The words come from the authorities: the three market classes from
    ``prompt_builder.market_scopes()`` and the one non-market class from this
    module, the same split ``prompt_builder.MARKET_CLASSES`` already draws.
    """

    def test_every_declared_class_has_a_chinese_word_of_its_own(self):
        """A fifth class added to the intake fails here, not on a page in English."""
        for asset_class in ASSET_CLASSES:
            label = pages.asset_class_label(asset_class)
            self.assertTrue(label, asset_class)
            self.assertNotEqual(
                asset_class, label, "{} was shown as its English key".format(asset_class)
            )
            self.assertTrue(
                any("一" <= character <= "鿿" for character in label),
                "{} → {!r} has no Chinese".format(asset_class, label),
            )

    def test_the_three_market_classes_read_their_word_from_the_scope_authority(self):
        scopes = market_scopes()
        for asset_class in (ASSET_CLASS_CRYPTO, ASSET_CLASS_TW_STOCK, ASSET_CLASS_US_STOCK):
            self.assertEqual(
                scopes[asset_class].label, pages.asset_class_label(asset_class)
            )

    def test_crypto_is_the_authority_word_not_the_ticket_gloss(self):
        self.assertEqual("加密資產", pages.asset_class_label(ASSET_CLASS_CRYPTO))

    def test_the_non_market_class_is_named_here_because_no_scope_describes_it(self):
        self.assertEqual("開放題", pages.asset_class_label(ASSET_CLASS_OPEN))
        self.assertNotIn(ASSET_CLASS_OPEN, market_scopes())

    def test_a_class_no_authority_declares_is_shown_as_it_was_recorded(self):
        """An index may hold a class the config does not; inventing a word for it
        would be worse than showing what is really there."""
        self.assertEqual("commodity", pages.asset_class_label("commodity"))

    def test_an_absent_class_falls_back_to_the_empty_marker(self):
        self.assertEqual(pages._EMPTY, pages.asset_class_label(None))

    def test_a_scope_label_edited_in_the_config_reaches_the_page(self):
        """The word is read live, so there is no second copy to fall out of date."""
        scopes = market_scopes()
        edited = dict(scopes)
        edited[ASSET_CLASS_US_STOCK] = replace(
            scopes[ASSET_CLASS_US_STOCK], label="美國股市（改）"
        )
        with mock.patch.object(prompt_builder, "_CACHED_MARKET_SCOPES", edited):
            self.assertEqual(
                "美國股市（改）", pages.asset_class_label(ASSET_CLASS_US_STOCK)
            )


class RunDetailValueLabelTest(unittest.TestCase):
    """Stored question and stop values reach the page as honest Chinese labels."""

    QUESTION_TYPES = {
        "single_asset_market_state": "單一資產市場狀態",
        "two_asset_comparison": "兩資產比較",
        "overall_market_state": "整體市場狀態",
        "event_impact": "事件影響",
        "open_proposition": "開放命題",
    }

    def test_every_current_question_type_has_its_own_label(self):
        self.assertEqual(
            self.QUESTION_TYPES,
            {
                value: views.question_type_label(value)
                for value in self.QUESTION_TYPES
            },
        )

    def test_the_stored_compatibility_question_types_are_labelled_too(self):
        expected = {
            "single_asset": "單一資產",
            "comparison": "兩資產比較",
            "market": "整體市場",
            "event": "事件影響",
            "market_direction": "市場方向",
        }

        self.assertEqual(
            expected,
            {value: views.question_type_label(value) for value in expected},
        )

    def test_every_dynamic_consensus_reason_names_its_vote_count(self):
        for votes in range(1, 8):
            self.assertEqual(
                "達成共識（{} 票）".format(votes),
                views.stop_reason_label("consensus_{}_votes".format(votes)),
            )

    def test_every_dynamic_final_settle_adoption_names_its_vote_count(self):
        for votes in range(1, 8):
            self.assertEqual(
                "硬停結算（{} 票採納）".format(votes),
                views.stop_reason_label("forced_stop_{}_votes".format(votes)),
            )

    def test_the_named_stop_reasons_have_distinct_labels(self):
        expected = {
            "threshold_met": "達到票數門檻",
            "unanimous_blind_pass": "七席一致，提前結案",
            "forced_stop_no_consensus": "硬停結算（未達共識）",
            "forced_stop_insufficient_valid_votes": "硬停結算（有效票不足）",
        }

        self.assertEqual(
            expected,
            {value: views.stop_reason_label(value) for value in expected},
        )

    def test_an_unknown_value_is_marked_instead_of_given_an_invented_translation(self):
        self.assertEqual("coin_flip（尚未翻譯）", views.question_type_label("coin_flip"))
        self.assertEqual(
            "mystery_stop（尚未翻譯）",
            views.stop_reason_label("mystery_stop"),
        )

    def test_an_absent_value_stays_absent_for_the_pages_empty_marker(self):
        self.assertIsNone(views.question_type_label(None))
        self.assertIsNone(views.stop_reason_label(None))


# -- the live room ------------------------------------------------------------


LIVE_RUN_ID = "20260806T020000Z-btc-live01"
LIVE_QUESTION = "BTC 未來七天會不會漲"
BALLOT = ("bullish", "bearish", "neutral")


def write_live_run(
    data_root,
    run_id=LIVE_RUN_ID,
    question=LIVE_QUESTION,
    assets=("BTC",),
    question_type="market_direction",
    asset_class=None,
):
    """A run directory in the state it is in while the run is still going.

    ``asset_class`` is written only when it is given, so the default fixture
    stays a run recorded before the field existed — which is what the seat labels
    have to fall back for.
    """
    date_dir, name = run_dir_parts(run_id, question)
    run_dir = Path(data_root) / "runs" / date_dir / name
    run_dir.mkdir(parents=True)
    payload = {
        "run_id": run_id,
        "question": question,
        "assets": list(assets),
        "question_type": question_type,
        "created_at_utc": "2026-08-06T02:00:00Z",
    }
    if asset_class is not None:
        payload["asset_class"] = asset_class
    (run_dir / "question.json").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return run_dir


def seat_message(
    seat_id,
    stance,
    elapsed_ms,
    kind="position",
    round_number=1,
    reason=None,
    evidence_ids=(),
    change_reason=None,
    run_id=LIVE_RUN_ID,
):
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "phase": "debate",
        "event": "seat_message",
        "created_at_utc": "2026-08-06T02:05:00Z",
        "elapsed_ms": elapsed_ms,
        "seat_id": seat_id,
        "kind": kind,
        "round": round_number,
        "message_id": "{}-{}-{}".format(seat_id, kind, elapsed_ms),
        "stance": stance,
        "public_reason": reason or "{} 的公開理由".format(seat_id),
        "evidence_ids": list(evidence_ids),
        "responds_to": [],
        "stance_change_reason": change_reason,
    }


def append_events(run_dir, records):
    """Append whole lines the way ``run_store`` does, and return the new size."""
    path = run_dir / "events.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path.stat().st_size


class EventTailTest(unittest.TestCase):
    """`events.jsonl` is appended to, so it is read forward from an offset."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "events.jsonl"

    def write(self, text):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def test_a_file_that_is_not_there_reads_as_nothing_at_the_same_offset(self):
        """A run directory exists before its first event does."""
        self.assertEqual(([], 0), live.read_events(self.path, 0))

    def test_every_complete_line_comes_back_with_the_offset_after_it(self):
        self.write('{"a":1}\n{"a":2}\n')

        entries, offset = live.read_events(self.path, 0)

        self.assertEqual([{"a": 1}, {"a": 2}], [record for _, record in entries])
        self.assertEqual(self.path.stat().st_size, offset)

    def test_a_half_written_line_is_not_an_event_yet(self):
        self.write('{"a":1}\n{"a":2')

        entries, offset = live.read_events(self.path, 0)

        self.assertEqual([{"a": 1}], [record for _, record in entries])
        self.assertEqual(8, offset)

    def test_the_line_finished_later_is_read_on_the_next_pass(self):
        self.write('{"a":1}\n{"a":2')
        _, offset = live.read_events(self.path, 0)

        self.write("}\n")
        entries, _ = live.read_events(self.path, offset)

        self.assertEqual([{"a": 2}], [record for _, record in entries])

    def test_reading_from_the_returned_offset_reads_nothing_twice(self):
        self.write('{"a":1}\n')
        _, offset = live.read_events(self.path, 0)
        self.write('{"a":2}\n')

        entries, _ = live.read_events(self.path, offset)

        self.assertEqual([{"a": 2}], [record for _, record in entries])

    def test_nothing_new_reads_as_no_entries_at_the_same_offset(self):
        self.write('{"a":1}\n')
        _, offset = live.read_events(self.path, 0)

        self.assertEqual(([], offset), live.read_events(self.path, offset))

    def test_a_line_that_is_not_json_is_skipped_without_stalling_the_offset(self):
        self.write('{"a":1}\n這不是 JSON\n{"a":2}\n')

        entries, offset = live.read_events(self.path, 0)

        self.assertEqual([{"a": 1}, {"a": 2}], [record for _, record in entries])
        self.assertEqual(self.path.stat().st_size, offset)

    def test_a_json_line_that_is_not_an_object_is_not_an_event(self):
        self.write('[1,2]\n{"a":1}\n')

        entries, _ = live.read_events(self.path, 0)

        self.assertEqual([{"a": 1}], [record for _, record in entries])

    def test_each_entry_carries_the_offset_just_past_its_own_line(self):
        self.write('{"a":1}\n{"a":2}\n')

        entries, _ = live.read_events(self.path, 0)

        self.assertEqual([8, 16], [offset for offset, _ in entries])


class CursorTest(unittest.TestCase):
    """A cursor is honoured only when it can be, and refused every other way."""

    def test_a_cursor_names_the_run_and_the_offset(self):
        self.assertEqual("run-1@42", live.make_cursor("run-1", 42))

    def test_a_cursor_for_this_run_inside_the_file_resumes_there(self):
        self.assertEqual(42, live.resume_offset("run-1@42", "run-1", 100))

    def test_a_cursor_at_exactly_the_end_still_resumes(self):
        """FP direction: the common case is a client that is fully caught up."""
        self.assertEqual(100, live.resume_offset("run-1@100", "run-1", 100))

    def test_the_start_of_the_file_is_a_cursor_like_any_other(self):
        self.assertEqual(0, live.resume_offset("run-1@0", "run-1", 100))

    def test_no_cursor_at_all_is_not_a_resume(self):
        self.assertIsNone(live.resume_offset(None, "run-1", 100))

    def test_a_cursor_naming_another_run_is_not_resumed_from(self):
        self.assertIsNone(live.resume_offset("run-2@42", "run-1", 100))

    def test_a_cursor_past_the_end_of_this_file_is_not_resumed_from(self):
        self.assertIsNone(live.resume_offset("run-1@900", "run-1", 100))

    def test_a_negative_offset_is_not_resumed_from(self):
        self.assertIsNone(live.resume_offset("run-1@-1", "run-1", 100))

    def test_an_offset_that_is_not_a_number_is_not_resumed_from(self):
        self.assertIsNone(live.resume_offset("run-1@soon", "run-1", 100))

    def test_text_with_no_separator_is_not_a_cursor(self):
        self.assertIsNone(live.resume_offset("42", "run-1", 100))

    def test_something_that_is_not_text_is_not_a_cursor(self):
        self.assertIsNone(live.resume_offset(42, "run-1", 100))


class FirstSentenceTest(unittest.TestCase):
    """A message opens with its own first sentence and hides the rest.

    ``debate_driver`` requires every ``public_reason`` to open with a 30-60 字
    核心結論, so the first sentence is that conclusion and the fold is a
    sentence split rather than a character count. An ASCII full stop ends a
    sentence only when a space, a newline or the end of the text follows it:
    ``3.5%`` is one number, and a brief cut in half there would say something
    the reason does not.
    """

    def test_a_full_width_stop_ends_the_first_sentence(self):
        for reason, brief in (
            ("本席看多。理由在後面。", "本席看多。"),
            ("真的嗎？後面還有話。", "真的嗎？"),
            ("完全不同意！後面還有話。", "完全不同意！"),
        ):
            self.assertEqual(brief, live.first_sentence(reason), reason)

    def test_an_ascii_stop_ends_it_only_when_a_break_follows(self):
        self.assertEqual("Up first.", live.first_sentence("Up first. Then more."))
        self.assertEqual("Up first.", live.first_sentence("Up first.\nThen more."))
        self.assertEqual("Up first.", live.first_sentence("Up first.\r\nThen more."))
        self.assertEqual("Up first.", live.first_sentence("Up first.\tThen more."))

    def test_a_stop_inside_a_number_is_not_the_end_of_a_sentence(self):
        reason = "漲幅 3.5% 已被市場反映，本席看空。後面還有理由。"

        self.assertEqual("漲幅 3.5% 已被市場反映，本席看空。", live.first_sentence(reason))

    def test_a_stop_at_the_very_end_is_the_end_of_a_sentence(self):
        """Long enough that the character cap would answer differently, so what
        is pinned is the stop and not the length."""
        reason = "看空" * 35 + "3.5%."

        self.assertEqual(reason, live.first_sentence(reason))

    def test_a_reason_with_no_sentence_end_is_cut_at_the_cap(self):
        reason = "看空" * 40

        self.assertEqual("看空" * 30 + "…", live.first_sentence(reason))

    def test_a_reason_no_longer_than_the_cap_is_its_own_brief(self):
        for reason in ("", "看空", "看空" * 30):
            self.assertEqual(reason, live.first_sentence(reason), len(reason))


class ChatRoomTest(unittest.TestCase):
    """The room is what the events say, in the order they were written."""

    def room(self):
        labels = {"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"}
        return live.ChatRoom(BALLOT, labels)

    def test_the_seven_seats_are_on_the_roll_before_anybody_speaks(self):
        seats = self.room().seat_views()

        self.assertEqual(list(SEAT_IDENTITIES), [seat["seat_id"] for seat in seats])

    def test_a_seat_is_shown_under_the_identity_the_seats_module_holds(self):
        room = self.room()

        room.ingest([seat_message("spot-technical", "bullish", 240000)])

        message = room.messages[0]
        identity = SEAT_IDENTITIES["spot-technical"]
        self.assertEqual(identity.display_name, message["agent_name"])
        self.assertEqual(identity.avatar, message["avatar"])
        self.assertEqual(identity.agent_number, message["agent_number"])
        self.assertEqual(identity.provider, message["provider"])

    def test_a_message_carries_its_round_kind_stance_and_reason(self):
        room = self.room()

        room.ingest(
            [
                seat_message(
                    "news", "bearish", 300000, kind="challenge", round_number=2,
                    reason="這裡有反證", evidence_ids=["news-01"],
                )
            ]
        )

        message = room.messages[0]
        self.assertEqual(2, message["round"])
        self.assertEqual("反方挑戰", message["kind_label"])
        self.assertEqual("偏空", message["stance_label"])
        self.assertEqual("stance-oppose", message["stance_class"])
        self.assertEqual("這裡有反證", message["public_reason"])
        self.assertEqual(["news-01"], message["evidence_ids"])

    def test_a_message_carries_the_brief_it_opens_with_beside_the_whole_reason(self):
        """Computed here once, so the page and the stream show the same brief
        without either of them splitting a sentence of its own."""
        room = self.room()

        room.ingest(
            [
                seat_message(
                    "news", "bearish", 300000,
                    reason="本席看空。第一，資金流出。第二，量能萎縮。",
                )
            ]
        )

        message = room.messages[0]
        self.assertEqual("本席看空。", message["public_brief"])
        self.assertEqual(
            "本席看空。第一，資金流出。第二，量能萎縮。", message["public_reason"]
        )

    def test_a_reason_of_one_sentence_is_its_own_brief(self):
        room = self.room()

        room.ingest([seat_message("news", "bearish", 300000, reason="本席看空。")])

        message = room.messages[0]
        self.assertEqual(message["public_reason"], message["public_brief"])

    def test_a_seat_that_gave_no_reason_briefs_as_the_words_shown_for_that(self):
        room = self.room()
        record = seat_message("news", "bearish", 300000)
        record["public_reason"] = ""

        room.ingest([record])

        message = room.messages[0]
        self.assertEqual(live.NO_REASON_LABEL, message["public_reason"])
        self.assertEqual(live.NO_REASON_LABEL, message["public_brief"])

    def test_each_ballot_position_gets_a_colour_class_in_the_ballots_own_order(self):
        room = self.room()

        self.assertEqual(
            ["stance-affirm", "stance-oppose", "stance-abstain"],
            [entry["class"] for entry in room.tally_views()],
        )

    def test_the_tally_counts_each_seat_once_under_its_current_stance(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("derivatives", "bullish", 241000),
                seat_message("news", "bearish", 242000),
            ]
        )

        self.assertEqual(
            {"bullish": 2, "bearish": 1, "neutral": 0},
            {entry["stance"]: entry["count"] for entry in room.tally_views()},
        )

    def test_a_seat_speaking_twice_the_same_way_is_still_one_vote(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("spot-technical", "bullish", 300000, kind="final_vote"),
            ]
        )

        counts = {entry["stance"]: entry["count"] for entry in room.tally_views()}
        self.assertEqual(1, counts["bullish"])

    def test_a_seat_that_moves_is_counted_where_it_moved_to(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("spot-technical", "bearish", 300000, kind="final_vote"),
            ]
        )

        counts = {entry["stance"]: entry["count"] for entry in room.tally_views()}
        self.assertEqual({"bullish": 0, "bearish": 1}, {k: counts[k] for k in ("bullish", "bearish")})

    def test_a_first_stance_is_recorded_but_is_not_called_a_change(self):
        room = self.room()

        room.ingest([seat_message("spot-technical", "bullish", 240000)])

        self.assertEqual(1, len(room.changes))
        self.assertFalse(room.changes[0]["changed"])
        self.assertEqual("首次表態", room.messages[0]["change_label"])
        self.assertFalse(room.messages[0]["changed"])

    def test_a_real_change_names_both_sides_and_why(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message(
                    "spot-technical", "bearish", 300000, kind="final_vote",
                    change_reason="被反方證據說服",
                ),
            ]
        )

        change = room.changes[-1]
        self.assertTrue(change["changed"])
        self.assertEqual("偏多", change["before_label"])
        self.assertEqual("偏空", change["after_label"])
        self.assertEqual("被反方證據說服", change["reason"])
        self.assertEqual("偏多 → 偏空", room.messages[-1]["change_label"])
        self.assertTrue(room.messages[-1]["changed"])

    def test_speaking_again_without_moving_is_not_reported_as_a_change(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("spot-technical", "bullish", 300000, kind="final_vote"),
            ]
        )

        self.assertEqual(1, len(room.changes))
        self.assertIsNone(room.messages[-1]["change_label"])

    def test_a_stance_this_ballot_does_not_have_is_shown_but_never_counted(self):
        room = self.room()

        room.ingest([seat_message("spot-technical", "sideways", 240000)])

        self.assertEqual("尚未表態", room.messages[0]["stance_label"])
        self.assertEqual("stance-unknown", room.messages[0]["stance_class"])
        self.assertEqual([], room.changes)
        self.assertEqual(0, sum(e["count"] for e in room.tally_views()))

    def test_an_event_that_is_not_a_seat_message_is_not_a_message(self):
        room = self.room()

        room.ingest(
            [
                {"event": "attempt_started", "seat_id": "news", "elapsed_ms": 1000},
                seat_message("news", "bearish", 240000),
            ]
        )

        self.assertEqual(1, len(room.messages))

    def test_a_seat_id_outside_the_seven_never_reaches_the_room(self):
        room = self.room()

        room.ingest([seat_message("chief-analyst", "bullish", 240000)])

        self.assertEqual([], room.messages)
        self.assertEqual(0, sum(e["count"] for e in room.tally_views()))

    def test_the_latest_round_is_the_highest_any_message_declared(self):
        room = self.room()

        room.ingest(
            [
                seat_message("spot-technical", "bullish", 240000, round_number=1),
                seat_message("news", "bearish", 300000, round_number=2),
            ]
        )

        self.assertEqual(2, room.latest_round())

    def test_no_message_means_no_round_rather_than_round_zero(self):
        self.assertIsNone(self.room().latest_round())

    def test_a_seat_that_has_spoken_shows_what_it_last_did(self):
        room = self.room()

        room.ingest(
            [seat_message("news", "bearish", 300000, kind="final_vote")]
        )

        seats = {seat["seat_id"]: seat for seat in room.seat_views()}
        self.assertEqual("最終投票", seats["news"]["status"])
        self.assertEqual("偏空", seats["news"]["stance_label"])
        self.assertEqual("等待派工", seats["derivatives"]["status"])
        self.assertEqual("尚未表態", seats["derivatives"]["stance_label"])


class LiveSnapshotTest(unittest.TestCase):
    """Which run is on screen, and what it looks like at that moment."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def test_a_data_root_with_no_run_is_waiting_rather_than_broken(self):
        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(live.STATUS_WAITING, snapshot["state"])
        self.assertIsNone(snapshot["run_id"])
        self.assertEqual([], snapshot["messages"])
        self.assertEqual(7, len(snapshot["seats"]))

    def test_the_newest_run_directory_is_the_one_being_watched(self):
        write_live_run(self.data_root, "20260805T020000Z-btc-old001", "舊的題目")
        write_live_run(self.data_root, "20260806T020000Z-btc-new001", "新的題目")

        self.assertEqual(
            "20260806T020000Z-btc-new001", live.live_snapshot(self.data_root)["run_id"]
        )

    def test_a_named_run_is_watched_instead_of_the_newest_one(self):
        write_live_run(self.data_root, "20260805T020000Z-btc-old001", "舊的題目")
        write_live_run(self.data_root, "20260806T020000Z-btc-new001", "新的題目")

        snapshot = live.live_snapshot(self.data_root, "20260805T020000Z-btc-old001")

        self.assertEqual("20260805T020000Z-btc-old001", snapshot["run_id"])

    def test_a_run_id_that_names_nothing_is_the_waiting_state(self):
        write_live_run(self.data_root)

        snapshot = live.live_snapshot(self.data_root, "20261231T235959Z-btc-zzzz99")

        self.assertEqual(live.STATUS_WAITING, snapshot["state"])

    def test_a_folder_under_runs_that_is_not_a_date_holds_no_run(self):
        (self.data_root / "runs" / "snapshots").mkdir(parents=True)

        self.assertIsNone(live.newest_run_id(self.data_root))

    def test_a_directory_with_no_question_json_is_not_offered_as_a_run(self):
        (self.data_root / "runs" / "2026-08-06" / "0200-未寫入").mkdir(parents=True)

        self.assertIsNone(live.newest_run_id(self.data_root))

    def test_a_run_still_going_is_running_and_has_no_verdict_yet(self):
        run_dir = write_live_run(self.data_root)
        append_events(run_dir, [seat_message("spot-technical", "bullish", 240000)])

        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(live.STATUS_RUNNING, snapshot["state"])
        self.assertIsNone(snapshot["outcome"])
        self.assertEqual(1, len(snapshot["messages"]))
        self.assertEqual(240000, snapshot["elapsed_ms"])

    def test_a_finished_run_reports_its_light_and_where_to_read_it(self):
        run_dir = write_run(self.data_root, LIVE_RUN_ID, LIVE_QUESTION)

        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(live.STATUS_FINISHED, snapshot["state"])
        self.assertEqual("green", snapshot["outcome"]["confidence_level"])
        self.assertEqual("達成共識", snapshot["outcome"]["consensus_label"])
        self.assertEqual("/run/{}".format(LIVE_RUN_ID), snapshot["outcome"]["run_href"])
        self.assertTrue(run_dir.is_dir())

    def test_the_snapshot_carries_a_cursor_the_stream_can_resume_from(self):
        run_dir = write_live_run(self.data_root)
        size = append_events(run_dir, [seat_message("spot-technical", "bullish", 1)])

        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(live.make_cursor(LIVE_RUN_ID, size), snapshot["cursor"])

    def test_the_ballot_words_come_from_the_run_not_from_this_module(self):
        run_dir = write_live_run(
            self.data_root, question_type="comparison", assets=("BTC", "ETH")
        )
        append_events(
            run_dir, [seat_message("spot-technical", "asset_a_stronger", 240000)]
        )

        snapshot = live.live_snapshot(self.data_root)

        self.assertIn("BTC較優", [entry["label"] for entry in snapshot["tally"]])

    def test_a_file_sitting_under_runs_is_not_mistaken_for_a_run(self):
        """``latest.json`` and ``index.db`` live there; neither is a run."""
        write_live_run(self.data_root)
        (self.data_root / "runs" / "latest.json").write_text(
            json.dumps({"run_id": "20261231T235959Z-btc-zzzz99"}), encoding="utf-8"
        )
        (self.data_root / "runs" / "index.db").write_bytes(b"not a run either")

        self.assertEqual(LIVE_RUN_ID, live.newest_run_id(self.data_root))

    def test_a_run_id_that_tries_to_leave_the_runs_tree_watches_nothing(self):
        write_live_run(self.data_root)

        for attempt in ("../..", "..", ".", "", "a/b", "/etc/passwd"):
            self.assertEqual(
                (None, None),
                live.resolve_live_run(self.data_root, attempt),
                attempt,
            )

    def test_two_runs_in_the_same_minute_go_by_the_second_not_by_the_question(self):
        """A directory name carries ``HHMM``, so it cannot order these two.

        Ordering by the folder name fell through to the question slug for a
        same-minute pair, and ``zzz`` beat ``aaa`` — so the run that started 58
        seconds *earlier* was shown as the newest. No concurrency is needed:
        two runs started in the same minute is all it takes.
        """
        write_live_run(self.data_root, "20260806T020001Z-zed-ear001", "ZZZ 這題比較早")
        write_live_run(self.data_root, "20260806T020059Z-alpha-lat001", "AAA 這題比較晚")

        self.assertEqual(
            "20260806T020059Z-alpha-lat001", live.newest_run_id(self.data_root)
        )

    def test_the_same_pair_orders_the_same_way_whichever_was_written_first(self):
        """The answer is a property of the runs, not of the order on disk."""
        write_live_run(self.data_root, "20260806T020059Z-alpha-lat001", "AAA 這題比較晚")
        write_live_run(self.data_root, "20260806T020001Z-zed-ear001", "ZZZ 這題比較早")

        self.assertEqual(
            "20260806T020059Z-alpha-lat001", live.newest_run_id(self.data_root)
        )

    def test_runs_minutes_apart_are_still_ordered_by_time(self):
        """FN direction: the second-accurate order must not break the coarse one."""
        write_live_run(self.data_root, "20260806T020000Z-alpha-ear001", "AAA 較早")
        write_live_run(self.data_root, "20260806T031500Z-zed-lat001", "ZZZ 較晚")

        self.assertEqual(
            "20260806T031500Z-zed-lat001", live.newest_run_id(self.data_root)
        )

    def test_runs_in_different_date_folders_are_still_ordered_by_date(self):
        """The date folder is the outer key, so the pair has to straddle one.

        A run directory is filed under the **Taipei** date of its instant (ADR
        0005), and Taipei midnight is 16:00Z. This test used to use
        ``20260805T235959Z`` and ``20260806T000001Z`` — a pair that reads like it
        crosses a date and does not: in Taipei both are 2026-08-06, so both landed
        in one folder and the outer key was never exercised. 15:59Z and 16:01Z on
        the same UTC day are 23:59 and 00:01 in Taipei, which is the crossing the
        name claims.
        """
        earlier = "20260805T155900Z-zed-ear001"
        later = "20260805T160100Z-alpha-lat001"
        write_live_run(self.data_root, earlier, "ZZZ 前一天")
        write_live_run(self.data_root, later, "AAA 後一天")
        self.assertEqual(
            {"2026-08-05", "2026-08-06"},
            {path.name for path in (self.data_root / "runs").iterdir()},
        )

        self.assertEqual(later, live.newest_run_id(self.data_root))

    def test_the_room_and_the_in_progress_answer_name_the_same_run(self):
        """One authority: a same-minute pair must not split these two answers."""
        write_live_run(self.data_root, "20260806T020001Z-zed-ear001", "ZZZ 這題比較早")
        write_live_run(self.data_root, "20260806T020059Z-alpha-lat001", "AAA 這題比較晚")

        self.assertEqual(
            "20260806T020059Z-alpha-lat001", live.in_progress_run_id(self.data_root)
        )
        self.assertEqual(
            "20260806T020059Z-alpha-lat001",
            live.live_snapshot(self.data_root)["run_id"],
        )


class BallotVocabularyTest(unittest.TestCase):
    """Which stances exist, and what this run calls them."""

    def test_the_question_types_own_stances_are_the_ballot(self):
        options, labels = live.ballot_for({"question_type": "market_direction"})

        self.assertEqual(("bullish", "bearish", "neutral"), options)
        self.assertEqual("偏多", labels["bullish"])

    def test_a_question_recorded_with_no_type_reads_as_a_market_question(self):
        options, labels = live.ballot_for({"question": "BTC 會漲嗎"})

        self.assertEqual(("bullish", "bearish", "neutral"), options)
        self.assertEqual("偏多", labels["bullish"])

    def test_a_type_this_build_has_never_heard_of_falls_back_the_same_way(self):
        options, _ = live.ballot_for({"question_type": "coin_flip"})

        self.assertEqual(("bullish", "bearish", "neutral"), options)

    def test_something_that_is_not_a_question_record_still_yields_a_ballot(self):
        self.assertEqual(("bullish", "bearish", "neutral"), live.ballot_for(None)[0])

    def test_the_words_the_run_recorded_win_over_the_derived_ones(self):
        _, labels = live.ballot_for(
            {
                "question_type": "market_direction",
                "stance_labels": {
                    "bullish": "這一場說的偏多",
                    "bearish": "這一場說的偏空",
                    "neutral": "這一場說的方向不明",
                },
            }
        )

        self.assertEqual("這一場說的偏多", labels["bullish"])

    def test_a_half_recorded_vocabulary_is_not_mixed_with_the_derived_one(self):
        """``resolve_stance_labels`` takes the run's words all or none, and this
        room does not second-guess it: a ballot showing two recorded words and
        one derived one would read as three words the run chose."""
        _, labels = live.ballot_for(
            {
                "question_type": "market_direction",
                "stance_labels": {"bullish": "這一場說的偏多"},
            }
        )

        self.assertEqual({"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"}, labels)

    def test_a_comparison_ballot_is_named_with_this_runs_own_assets(self):
        _, labels = live.ballot_for(
            {"question_type": "comparison", "assets": ["BTC", "ETH"]}
        )

        self.assertEqual("BTC較優", labels["asset_a_stronger"])


class LivePageTest(PageFixture, unittest.TestCase):
    """The room is a whole page before its script runs."""

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 240000, round_number=1),
                seat_message("news", "bearish", 250000, round_number=1),
                seat_message(
                    "news", "bullish", 400000, kind="final_vote", round_number=2,
                    change_reason="被反方證據說服",
                ),
            ],
        )

    def test_the_room_opens(self):
        self.assertEqual(200, self.get("/live").status)

    def test_every_seat_is_on_the_page_under_its_own_name_and_avatar(self):
        body = self.get("/live").body

        for identity in SEAT_IDENTITIES.values():
            self.assertIn(identity.display_name, body, identity.seat_id)
            self.assertIn(identity.avatar, body, identity.seat_id)

    def test_each_public_message_is_a_bubble_with_its_reason(self):
        body = self.get("/live").body

        self.assertEqual(3, body.count('class="message '))
        self.assertIn("spot-technical 的公開理由", body)

    LONG_REASON = "鏈上籌碼持續流入。交易所餘額同步下降，賣壓有限。"

    def folded(self, body):
        """The one ``<details>`` reason on the page, tags and all."""
        return re.search(
            r'<details class="message-reason">.*?</details>', body, re.DOTALL
        ).group(0)

    def words_of(self, markup):
        """An opened reason as a reader sees it, without its fold controls."""
        without_hint = re.sub(
            r'<span class="reason-(?:hint|fold|ellipsis)">.*?</span>',
            "",
            markup,
            flags=re.DOTALL,
        )
        return re.sub(r"<[^>]+>", "", without_hint)

    def test_a_reason_with_more_to_say_is_folded_at_its_first_sentence(self):
        append_events(
            self.run_dir,
            [seat_message("onchain", "bullish", 500000, reason=self.LONG_REASON)],
        )

        folded = self.folded(self.get("/live").body)

        summary = re.search(r"<summary>.*?</summary>", folded, re.DOTALL).group(0)
        self.assertIn("鏈上籌碼持續流入。", summary)
        self.assertNotIn("交易所餘額", summary)
        self.assertIn("顯示全文", summary)

    def test_the_folded_reason_holds_the_whole_reason_once(self):
        """Opening it must read as the reason itself: the summary keeps its
        first sentence and the body carries exactly the rest."""
        append_events(
            self.run_dir,
            [seat_message("onchain", "bullish", 500000, reason=self.LONG_REASON)],
        )

        folded = self.folded(self.get("/live").body)

        self.assertEqual("判斷／挑戰理由：" + self.LONG_REASON, self.words_of(folded))

    def test_a_capped_brief_opens_to_exactly_the_original_reason(self):
        reason = "甲" * 61
        append_events(
            self.run_dir,
            [seat_message("onchain", "bullish", 500000, reason=reason)],
        )
        timeline = [
            {"at_ms": 0, "label": "開始", "required_votes": None},
            {"at_ms": 900000, "label": "結束", "required_votes": None},
        ]

        with (
            mock.patch.object(live, "rule_timeline", return_value=timeline),
            mock.patch.object(live, "threshold_label", return_value="尚未進入投票"),
        ):
            body = self.get("/live").body
            folded = self.folded(body)

        self.assertEqual("判斷／挑戰理由：" + reason, self.words_of(folded))
        self.assertIn('<span class="reason-ellipsis">…</span>', folded)
        self.assertIn(
            '.message-reason[open] .reason-ellipsis{display:none;}',
            self.get("/static/site.css").body,
        )
        self.assertIn("收合", folded)

    def test_a_folded_reason_holding_markup_is_shown_as_text_on_both_sides(self):
        """A fold escapes in two places — the summary and the body — so a
        reason carrying markup on each side of its first sentence is asserted
        on each side."""
        append_events(
            self.run_dir,
            [
                seat_message(
                    "onchain", "bullish", 500000,
                    reason="<b>第一句</b>。<script>alert(1)</script>後面還有。",
                )
            ],
        )

        body = self.get("/live").body

        self.assertNotIn("<b>第一句</b>", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;b&gt;第一句", body)
        self.assertIn("&lt;script&gt;alert(1)", body)

    def test_a_reason_that_says_it_all_at_once_is_not_folded(self):
        """The three messages in the fixture are each one sentence, so there is
        nothing to hide and no control to offer."""
        feed = re.search(
            r'<div class="feed".*?</div>\s*<button', self.get("/live").body, re.DOTALL
        ).group(0)

        self.assertEqual(3, feed.count('<p class="message-reason">'))
        self.assertNotIn("<details", feed)
        self.assertNotIn("顯示全文", feed)

    def test_a_change_of_vote_is_named_on_the_message_that_made_it(self):
        body = self.get("/live").body

        self.assertIn("是否變更立場：", body)
        self.assertIn("偏空 → 偏多", body)

    def test_the_tally_is_on_the_page_with_this_ballots_own_words(self):
        body = self.get("/live").body

        self.assertIn("偏多", body)
        self.assertIn("方向不明", body)

    def test_a_stance_is_named_as_well_as_coloured(self):
        """Colour is never the only signal a message carries."""
        badge = re.search(r'<span class="badge stance-affirm">([^<]+)</span>', self.get("/live").body)

        self.assertEqual("偏多", badge.group(1))

    def test_the_page_says_which_run_it_is_watching(self):
        body = self.get("/live").body

        self.assertIn(LIVE_RUN_ID, body)
        self.assertIn(LIVE_QUESTION, body)

    def test_a_finished_run_shows_its_light_and_links_to_the_run_detail(self):
        body = self.get("/live?run={}".format(self.finished_run())).body

        self.assertIn("綠燈", body)
        self.assertIn('href="/run/20260806T030000Z-btc-done01"', body)

    def finished_run(self):
        run_id = "20260806T030000Z-btc-done01"
        write_run(self.data_root, run_id, "已經結束的題目")
        return run_id

    def test_a_run_that_does_not_exist_still_renders_a_room_to_ask_in(self):
        body = self.get("/live?run=20261231T235959Z-btc-zzzz99").body

        self.assertIn("等待新的市場題目", body)
        self.assertIn('action="/launch"', body)

    def test_a_public_reason_holding_markup_is_shown_as_text(self):
        append_events(
            self.run_dir,
            [
                seat_message(
                    "onchain", "bullish", 500000, reason="<script>alert(1)</script>"
                )
            ],
        )

        body = self.get("/live").body

        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_the_room_declares_the_same_semantics_the_other_pages_do(self):
        body = self.get("/live").body

        self.assertIn('<html lang="zh-Hant">', body)
        self.assertEqual(1, body.count("<h1"))
        self.assertLess(body.index('href="#main"'), body.index("<header"))

    def test_the_feed_announces_additions_to_a_screen_reader(self):
        body = self.get("/live").body

        feed = re.search(r'<div class="feed"[^>]*>', body).group(0)
        self.assertIn('role="log"', feed)
        self.assertIn('aria-live="polite"', feed)

    def test_the_question_control_is_named_by_a_label_that_points_at_it(self):
        body = self.get("/live").body

        self.assertIn('<label for="question">', body)
        self.assertIn('id="question"', body)

    def test_the_room_never_shows_a_traceback_when_a_record_will_not_read(self):
        (self.run_dir / "question.json").write_text("{ 壞掉的", encoding="utf-8")

        response = self.get("/live")

        self.assertEqual(200, response.status)
        self.assertNotIn("Traceback", response.body)


# The two runs :meth:`PageFixture.index_two_runs` writes, newest first. Named
# here because the navigation tests have to say which of the two a tab opened.
INDEXED_BTC_RUN_ID = "20260801T020000Z-btc-aaaa11"
INDEXED_TSMC_RUN_ID = "20260705T020000Z-2330-bbbb22"


def page_header(body):
    """One rendered page's header, or ``""`` for the page that has none."""
    found = re.search(r"<header\b.*?</header>", body, re.DOTALL)
    return found.group(0) if found else ""


def header_navigation(body):
    """One page's whole header navigation: both groups, in document order.

    Since Spec R-003, 設定 sits in a group of its own beside the stop button, so
    the site's navigation is two ``<nav>`` elements rather than one — a reading
    that took the first would report the settings tab as missing from every page
    on the site.

    Only the header's own navigation is read. Nothing else on these pages carries
    a ``<nav>`` today, and taking it from inside the header is what keeps that
    true of the assertions if something ever does.
    """
    return "".join(re.findall(r"<nav\b.*?</nav>", page_header(body), re.DOTALL))


def tab_in(markup, label):
    """The tab carrying ``label``, whatever element it turned out to be, or ``""``.

    Element-agnostic on purpose: whether an unavailable report tab is a link at
    all is exactly what several tests below are about, so the way of finding one
    must not assume the answer.
    """
    found = re.search(r"<(a|span)\b[^>]*>{}</\1>".format(label), markup)
    return found.group(0) if found else ""


class HeaderFixture(PageFixture):
    """Reads one page's header the way a reader meets it: left to right."""

    BTC = INDEXED_BTC_RUN_ID
    TSMC = INDEXED_TSMC_RUN_ID

    # Spec R-002 and R-003: the four browsing tabs in this order, then 設定 on
    # its own, then the button. Anything a page puts in the header before them —
    # the room's connection indicator — sits left of the whole cluster and is not
    # part of this decision, so the reading is taken from the right.
    NAVIGATION = ["即時辯論", "歷史與命中率", "市場報告", "完整辯論", "設定"]

    # Each reading comes in two: by path for the pages a reader browses to, and
    # by body for the two that answer a submission — the launch refusal is a
    # ``POST``'s reply and cannot be fetched again by asking for a URL.
    def header(self, path):
        return self.header_of(self.get(path).body, path)

    def header_of(self, body, what):
        found = page_header(body)
        self.assertTrue(found, "no header on {}".format(what))
        return found

    def controls(self, path):
        return self.controls_of(self.get(path).body, path)

    def controls_of(self, body, what):
        """Every control in one header, in document order, as its own text."""
        return [
            text
            for _tag, text in re.findall(
                r"<(a|span|button)\b[^>]*>([^<]*)</\1>", self.header_of(body, what)
            )
        ]

    def tab(self, path, label):
        return self.tab_of(self.get(path).body, label, path)

    def tab_of(self, body, label, what):
        found = tab_in(self.header_of(body, what), label)
        self.assertTrue(found, "{} has no {} tab".format(what, label))
        return found

    def hrefs(self, path):
        return self.hrefs_of(self.get(path).body, path)

    def hrefs_of(self, body, what):
        """Where this header's two report tabs actually go."""
        return {
            label: re.search(
                r'href="([^"]*)"', self.tab_of(body, label, what)
            ).group(1)
            for label in ("市場報告", "完整辯論")
        }

    def refused_launch(self):
        """The page a launch this server will not start is answered with.

        No READY certificate is written, so the submission is refused with
        guidance — which is the page, and the only way to reach it is to make
        that request.
        """
        response = self.post("/launch", ask_bar_submission("BTC 未來七天會不會漲"))
        self.assertEqual(200, response.status)
        self.assertIn("這次沒有啟動", response.body)
        return response.body


class LiveSinglePageTest(HeaderFixture, unittest.TestCase):
    """The one page the user asked back for: tabs, run picker, focus, metrics, panels."""

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 240000, round_number=1),
                seat_message("derivatives", "bullish", 250000, round_number=1),
                seat_message("news", "bearish", 260000, round_number=1),
            ],
        )

    def tabs(self, body):
        return header_navigation(body)

    # -- the tab bar --------------------------------------------------------

    def test_the_top_right_carries_the_whole_site_navigation(self):
        """命中率 is a tab to another page, not a panel on the room — the user
        asked for it off the main page, not out of reach from it. Ticket 04 merged
        that page into the history one, so it is reached under the merged name."""
        tabs = self.tabs(self.get("/live").body)

        for label in ("即時辯論", "歷史與命中率", "設定", "市場報告", "完整辯論"):
            self.assertIn(label, tabs, label)

    def test_the_room_shows_no_hit_rate_figures_of_its_own(self):
        """FP direction: the tab must not have dragged the statistics onto the
        room, which is what the user actually asked to be rid of."""
        body = self.get("/live").body

        self.assertNotIn(pages.HIT_RATE_FORMULA, body)
        self.assertNotIn("整體命中率", body)

    def test_the_live_tab_is_the_current_one(self):
        tabs = self.tabs(self.get("/live").body)

        current = re.search(r'<a[^>]*aria-current="page"[^>]*>([^<]+)</a>', tabs)
        self.assertEqual("即時辯論", current.group(1))

    def test_the_settings_tab_reaches_the_settings_page(self):
        tabs = self.tabs(self.get("/live").body)

        self.assertRegex(tabs, r'<a[^>]*href="/settings"[^>]*>設定</a>')

    def tab_for(self, tabs, label):
        found = tab_in(tabs, label)
        self.assertTrue(found, "no {} tab".format(label))
        return found

    def test_report_and_debate_tabs_are_disabled_before_the_run_produces_them(self):
        tabs = self.tabs(self.get("/live").body)

        for label in ("市場報告", "完整辯論"):
            tab = self.tab_for(tabs, label)
            self.assertIn('aria-disabled="true"', tab, label)

    def test_a_disabled_artifact_tab_is_not_a_link_and_goes_nowhere(self):
        """It used to be ``<a href="/" aria-disabled="true">``: announced as
        disabled, and still navigable by keyboard, because the sheet's
        ``pointer-events:none`` only stops a mouse. A reader who tabbed to it and
        pressed Enter was taken off the room to the home page."""
        tabs = self.tabs(self.get("/live").body)

        for label in ("市場報告", "完整辯論"):
            tab = self.tab_for(tabs, label)
            self.assertNotIn("href", tab, label)
            self.assertNotIn("tabindex", tab, label)
            self.assertIn('role="link"', tab, label)
            self.assertTrue(tab.startswith("<span"), tab)

    def test_report_and_debate_tabs_open_once_the_run_has_produced_them(self):
        run_id = "20260806T030000Z-btc-done01"
        write_run(self.data_root, run_id, "已經結束的題目")

        tabs = self.tabs(self.get("/live?run={}".format(run_id)).body)

        report = self.tab_for(tabs, "市場報告")
        self.assertNotIn('aria-disabled="true"', report)
        self.assertTrue(report.startswith("<a "), report)
        self.assertIn("/run/{}/report.html".format(run_id), report)

    # -- the run picker (single data path: query_runs) ----------------------

    def test_the_run_picker_lists_the_indexed_history_runs(self):
        self.index_two_runs()

        body = self.get("/live").body
        picker = re.search(r'<select id="run-picker".*?</select>', body, re.DOTALL).group(0)

        self.assertIn("20260801T020000Z-btc-aaaa11", picker)
        self.assertIn("20260705T020000Z-2330-bbbb22", picker)

    def test_the_run_bar_names_the_run_being_watched(self):
        body = self.get("/live").body

        self.assertIn(LIVE_RUN_ID, body)

    def test_the_run_picker_survives_a_data_root_with_no_index(self):
        """A live run before the index exists must not blank the room."""
        response = self.get("/live")

        self.assertEqual(200, response.status)
        self.assertIn('id="run-picker"', response.body)

    # -- the focus bar ------------------------------------------------------

    def test_the_focus_bar_names_the_leading_stance(self):
        body = self.get("/live").body
        focus = re.search(r'<section class="focus-bar".*?</section>', body, re.DOTALL).group(0)

        self.assertIn("偏多", focus)

    # -- the four metrics ---------------------------------------------------

    def test_the_four_countdowns_and_gauges_are_all_on_the_page(self):
        body = self.get("/live").body

        for label in ("十五分鐘剩餘時間", "報告期限剩餘時間", "目前階段", "目前共識門檻"):
            self.assertIn(label, body, label)

    # -- the three collapsible panels --------------------------------------

    def test_the_three_detail_panels_are_present(self):
        body = self.get("/live").body

        self.assertEqual(3, body.count("<summary"))
        for label in ("規則與時間線", "票數變化", "可驗證證據"):
            self.assertIn(label, body, label)

    def test_the_rules_panel_shows_every_vote_round_with_its_threshold(self):
        body = self.get("/live").body

        rules = debate_rules()
        for index, vote_round in enumerate(rules.vote_rounds, start=1):
            self.assertIn("第 {} 輪開票".format(index), body)
            self.assertIn("門檻 {} 票".format(vote_round.threshold), body)

    # -- constraint A: the timeline reads the rule authority live -----------

    def test_the_rules_panel_reads_the_vote_counts_from_the_authority_at_render(self):
        """Not RULES = rules_for() frozen at import: a saved rule shows next render."""
        with mock.patch.object(live, "debate_rules", wraps=debate_rules) as spy:
            self.get("/live")

        self.assertTrue(spy.called)

    def test_a_round_threshold_edited_in_the_rules_reaches_the_next_render(self):
        rules = debate_rules()
        changed_rounds = list(rules.vote_rounds)
        changed_rounds[2] = replace(changed_rounds[2], threshold=3)
        changed_rounds[3] = replace(changed_rounds[3], threshold=2)
        stub = replace(rules, vote_rounds=tuple(changed_rounds))
        with mock.patch.object(live, "debate_rules", return_value=stub):
            body = self.get("/live").body

        self.assertRegex(body, r"第 3 輪開票.*門檻 3 票")

    # -- the tally is exactly one cell per ballot position ------------------

    def test_the_tally_has_exactly_one_cell_per_ballot_position(self):
        """The right-column tally is three cells; the other panels do not add
        stray cells that would double-count a stance."""
        body = self.get("/live").body

        cells = re.findall(r'<div class="stance-\w+"><span class="tally-label"', body)
        self.assertEqual(3, len(cells))


class LiveComparisonSealTest(PageFixture, unittest.TestCase):
    """The seal milestone moves with the run's question type.

    A two-asset comparison seals thirty seconds later than a single-asset run.
    The instant comes from the one authority for it — ``research_deadlines`` —
    and nowhere here copies 270_000. Both directions are pinned so a fix for the
    comparison case cannot quietly move the single-asset one.
    """

    def seal_at_ms(self, snapshot):
        seal = [rule for rule in snapshot["rules"] if rule["label"].startswith("封存")]
        self.assertEqual(1, len(seal), "exactly one seal milestone")
        return seal[0]["at_ms"]

    # -- FN: the comparison run seals at 4:30 -------------------------------

    def test_a_comparison_run_seals_where_research_deadlines_puts_it(self):
        write_live_run(
            self.data_root, question_type="two_asset_comparison", assets=("BTC", "ETH")
        )

        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(
            research_deadlines("two_asset_comparison").seal_ms,
            self.seal_at_ms(snapshot),
        )
        self.assertEqual(270000, self.seal_at_ms(snapshot))

    def test_the_comparison_seal_reads_four_thirty_on_the_rules_panel(self):
        write_live_run(
            self.data_root, question_type="two_asset_comparison", assets=("BTC", "ETH")
        )

        body = self.get("/live").body

        self.assertRegex(body, r"<time>T\+04:30</time><span>封存證據並整理開場票")

    # -- FP: the single-asset run is not moved by the fix above -------------

    def test_a_single_asset_run_still_seals_at_four_minutes(self):
        write_live_run(self.data_root)  # the default type is not a comparison

        snapshot = live.live_snapshot(self.data_root)

        self.assertEqual(research_deadlines(None).seal_ms, self.seal_at_ms(snapshot))
        self.assertEqual(240000, self.seal_at_ms(snapshot))

    def test_the_single_asset_seal_reads_four_minutes_on_the_rules_panel(self):
        write_live_run(self.data_root)

        body = self.get("/live").body

        self.assertRegex(body, r"<time>T\+04:00</time><span>封存證據並整理開場票")

    # -- the "not yet voting" gate follows the real seal -------------------

    def test_before_the_comparison_seal_there_is_still_no_threshold(self):
        """250s is past the single-asset seal but before the comparison one."""
        self.assertEqual(
            "尚未進入投票", live.threshold_label(250000, "two_asset_comparison")
        )

    def test_at_that_same_moment_a_single_asset_run_is_already_voting(self):
        self.assertEqual("7 票", live.threshold_label(250000, None))


class LiveVoteRoundTimelineTest(unittest.TestCase):
    """The public timeline is a direct projection of the schema-v2 rounds."""

    def test_every_configured_vote_round_is_one_reload_aware_milestone(self):
        rules = debate_rules()
        seal_ms = research_deadlines(None).seal_ms

        timeline = live.rule_timeline()
        rounds = [entry for entry in timeline if entry.get("round") is not None]

        self.assertEqual(
            [
                {
                    "at_ms": seal_ms + vote_round.open_offset_ms,
                    "label": "第 {} 輪開票".format(index),
                    "required_votes": vote_round.threshold,
                    "round": index,
                }
                for index, vote_round in enumerate(rules.vote_rounds, start=1)
            ],
            rounds,
        )

    def test_default_rounds_and_final_settle_land_at_the_approved_times(self):
        timeline = live.rule_timeline()
        vote_rounds = [entry for entry in timeline if entry.get("round")]
        final_settle = [entry for entry in timeline if entry["label"] == "硬停結算"]

        self.assertEqual(
            [(300_000, 7), (390_000, 6), (480_000, 5), (570_000, 4)],
            [(entry["at_ms"], entry["required_votes"]) for entry in vote_rounds],
        )
        self.assertEqual([(600_000, 4)], [
            (entry["at_ms"], entry["required_votes"]) for entry in final_settle
        ])

    def test_comparison_rounds_and_final_settle_all_move_thirty_seconds(self):
        single = [
            entry for entry in live.rule_timeline(None)
            if entry.get("round") or entry["label"] == "硬停結算"
        ]
        comparison = [
            entry for entry in live.rule_timeline("two_asset_comparison")
            if entry.get("round") or entry["label"] == "硬停結算"
        ]

        self.assertEqual(
            [entry["at_ms"] + 30_000 for entry in single],
            [entry["at_ms"] for entry in comparison],
        )

    def test_threshold_label_changes_only_at_the_reload_aware_round_walls(self):
        seal_ms = research_deadlines(None).seal_ms

        self.assertEqual("尚未進入投票", live.threshold_label(seal_ms - 1))
        self.assertEqual("7 票", live.threshold_label(seal_ms))
        self.assertEqual("7 票", live.threshold_label(seal_ms + 149_999))
        self.assertEqual("6 票", live.threshold_label(seal_ms + 150_000))


class RootIsTheDebateRoomTest(PageFixture, unittest.TestCase):
    """The front door opens onto the debate room — the user's project — not the
    history query it used to fall through to. History has its own path now, and
    the four ways back to it point there."""

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()  # gives /history something to list

    def test_the_front_door_is_the_debate_room_not_the_history_page(self):
        body = self.get("/").body

        self.assertIn('class="panel chat-panel"', body)  # the room
        self.assertIn("提問並啟動七席", body)
        self.assertNotIn('id="filters-heading"', body)  # not the query form

    def test_the_room_at_the_root_carries_the_rooms_own_policy(self):
        self.assertEqual(
            LIVE_CONTENT_SECURITY_POLICY,
            self.get("/").headers["Content-Security-Policy"],
        )

    def test_the_live_alias_still_serves_the_room(self):
        """launcher.LIVE_URL ends in /live and 回到目前 run points there."""
        self.assertIn('class="panel chat-panel"', self.get("/live").body)

    def test_history_now_lives_at_its_own_path(self):
        body = self.get("/history").body

        self.assertIn('id="filters-heading"', body)
        self.assertIn("20260801T020000Z-btc-aaaa11", body)

    def test_standing_on_the_front_door_the_live_tab_is_current(self):
        tabs = re.search(
            r'<nav class="page-tabs".*?</nav>', self.get("/").body, re.DOTALL
        ).group(0)
        current = re.search(r'<a[^>]*aria-current="page"[^>]*>([^<]+)</a>', tabs)

        self.assertEqual("即時辯論", current.group(1))
        self.assertRegex(tabs, r'<a href="/" aria-current="page">即時辯論</a>')

    def test_an_unknown_path_is_still_a_clean_404(self):
        response = self.get("/does-not-exist")

        self.assertEqual(404, response.status)
        self.assertIn("找不到", response.body)

    def test_the_four_ways_back_to_history_all_point_at_the_new_path(self):
        run_id = "20260801T020000Z-btc-aaaa11"
        # run bar (on the room), run detail, settings, and the not-found page
        self.assertIn('href="/history"', self.get("/").body)
        self.assertIn('href="/history"', self.get("/run/{}".format(run_id)).body)
        self.assertIn('href="/history"', self.get("/settings").body)
        self.assertIn('href="/history"', self.get("/does-not-exist").body)

    def test_the_history_form_and_its_reset_submit_to_history_not_the_room(self):
        body = self.get("/history").body

        self.assertIn('action="/history"', body)
        self.assertNotIn('action="/"', body)
        clear = re.search(r'<a class="secondary" href="([^"]*)">清除條件</a>', body)
        self.assertEqual("/history", clear.group(1))


class EveryPageCanReachTheRoomTest(HeaderFixture, unittest.TestCase):
    """The first acceptance condition: no page you can open and not get out of.

    Before this, the room had tabs and every other page had one stray link, so
    opening the settings page left a reader with no way back except editing the
    URL — which is why the settings page looked to the user like it was gone.
    """

    # ``/stats`` is not here: since Ticket 04 it is a redirect to ``/history``
    # rather than a page, and a redirect carries no navigation to check.
    PAGES = ("/", "/history", "/settings",
             "/run/20260801T020000Z-btc-aaaa11", "/does-not-exist")

    def setUp(self):
        super().setUp()
        run_dir = write_live_run(self.data_root)
        append_events(
            run_dir,
            [seat_message("spot-technical", "bullish", 240000)],
        )
        self.index_two_runs()

    def nav(self, path):
        found = header_navigation(self.get(path).body)
        self.assertTrue(found, "no site navigation on {}".format(path))
        return found

    def test_every_page_carries_the_site_navigation(self):
        for path in self.PAGES:
            nav = self.nav(path)
            for label in ("即時辯論", "歷史與命中率", "設定"):
                self.assertIn(label, nav, "{} missing {}".format(path, label))

    def test_every_page_can_get_back_to_the_debate_room_in_one_click(self):
        for path in self.PAGES:
            self.assertRegex(
                self.nav(path),
                r'<a href="/"(?: aria-current="page")?>即時辯論</a>',
                path,
            )

    def test_each_page_marks_its_own_tab_as_the_current_one(self):
        for path, label in (
            ("/", "即時辯論"),
            ("/history", "歷史與命中率"),
            ("/settings", "設定"),
        ):
            current = re.search(
                r'<a[^>]*aria-current="page"[^>]*>([^<]+)</a>', self.nav(path)
            )
            self.assertEqual(label, current.group(1), path)

    def test_a_page_that_is_not_a_tab_marks_none_of_them_current(self):
        """FP direction: run detail and 404 are not tabs, so nothing is current
        — marking one would tell the reader they are somewhere they are not."""
        for path in ("/run/20260801T020000Z-btc-aaaa11", "/does-not-exist"):
            self.assertNotIn('aria-current="page"', self.nav(path), path)

    def test_the_old_hand_rolled_back_links_are_gone(self):
        for path in self.PAGES:
            self.assertNotIn("← 回到歷史查詢", self.get(path).body, path)


class LatestReportRunTest(PageFixture, unittest.TestCase):
    """Which run the two report tabs point at from a page with no run of its own.

    Spec R-002: on the pages that are not about one run — the front door before
    anything has been asked, the history page, the settings page — 市場報告 and
    完整辯論 point at the newest run that really produced a report.

    The answer is folded here in Python out of the rows
    :func:`~hoya_market_agents.run_index.query_runs` already returns, which is
    the one run listing this package has: no statement is added anywhere and the
    index grows no column, so the nav cannot disagree with the history page about
    what has been run.
    """

    BTC = INDEXED_BTC_RUN_ID
    TSMC = INDEXED_TSMC_RUN_ID

    def test_a_data_root_with_no_index_yet_has_no_report_to_offer(self):
        """A first run has no index, and the pages must still render."""
        self.assertIsNone(views.latest_report_run(self.data_root))

    def test_an_index_that_will_not_read_offers_nothing_rather_than_raising(self):
        self.index_two_runs()

        with mock.patch.object(
            views, "query_runs", side_effect=views.RunIndexError("索引壞了。")
        ):
            self.assertIsNone(views.latest_report_run(self.data_root))

    def test_the_newest_indexed_run_with_a_report_is_the_one_offered(self):
        self.index_two_runs()

        self.assertEqual(self.BTC, views.latest_report_run(self.data_root)["run_id"])

    def test_it_says_which_of_that_runs_two_files_are_really_there(self):
        self.index_two_runs()

        self.assertEqual(
            {"report.html": True, "debate.html": True},
            views.latest_report_run(self.data_root)["artifacts"],
        )

    def test_a_newer_run_that_never_wrote_a_report_is_passed_over(self):
        """``report_path`` is a naming fact the index records whether or not the
        file was written, so "did this run produce a report" is answered by
        looking rather than by trusting the column."""
        self.index_two_runs()
        (self.btc / "report.html").unlink()

        self.assertEqual(self.TSMC, views.latest_report_run(self.data_root)["run_id"])

    def test_a_run_that_wrote_only_its_report_is_still_the_newest_one(self):
        """The two files are answered separately: the run is the one with a
        report, and its transcript is reported as the absence it is."""
        self.index_two_runs()
        (self.btc / "debate.html").unlink()

        nav = views.latest_report_run(self.data_root)

        self.assertEqual(self.BTC, nav["run_id"])
        self.assertEqual({"report.html": True, "debate.html": False}, nav["artifacts"])

    def test_a_data_root_where_nothing_produced_a_report_offers_nothing(self):
        write_run(self.data_root, self.BTC, "BTC 未來七天會不會漲", artifacts=())
        rebuild_index(self.data_root)

        self.assertIsNone(views.latest_report_run(self.data_root))

    def test_the_newest_report_is_found_however_many_runs_were_piled_on_it(self):
        """Regression: the search used to stop after the newest 200 rows, so a
        report with 200 reportless runs on top of it was answered as "no report
        anywhere" — the tabs went disabled on a site that has one, and the only
        way back to that report was the history page.

        "The newest run that has a report" is a claim about the whole Data Root
        or it is not the claim R-002 makes, so the reach may not be a number.
        """
        oldest = "20260101T020000Z-btc-old001"
        write_run(self.data_root, oldest, "最舊、但唯一有報告的一題")
        for index in range(200):
            write_run(
                self.data_root,
                "20260201T{:02d}{:02d}00Z-btc-{:06d}".format(
                    index // 60, index % 60, index
                ),
                "第 {} 題，沒有產出報告".format(index),
                artifacts=(),
            )
        rebuild_index(self.data_root)

        self.assertEqual(oldest, views.latest_report_run(self.data_root)["run_id"])


class FiveTabHeaderTest(HeaderFixture, unittest.TestCase):
    """Spec R-002 and R-003: five tabs on every page, and 設定 apart from four.

    Before this the room carried five and every other page carried three: the
    two report tabs were the room's alone, so a reader on the history page could
    not reach the newest report without going through the room first. 設定 sat
    inside the same tab group as the browsing links, which put the way out of
    the site next to the ways around it.
    """

    PAGES = (
        "/",
        "/live",
        "/history",
        "/settings",
        "/run/" + INDEXED_BTC_RUN_ID,
        "/does-not-exist",
    )

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def test_every_page_carries_the_five_tabs_in_the_one_order(self):
        for path in self.PAGES:
            self.assertEqual(
                self.NAVIGATION, self.controls(path)[-6:-1], path
            )

    def test_settings_is_the_control_immediately_left_of_the_stop_button(self):
        for path in self.PAGES:
            self.assertEqual(
                ["設定", pages.SHUTDOWN_LABEL], self.controls(path)[-2:], path
            )

    def test_the_browsing_tabs_and_settings_are_not_one_group(self):
        """R-003 is a separation, not an order: 設定 leaves the group the four
        browsing tabs share rather than sitting last inside it."""
        for path in self.PAGES:
            groups = re.findall(r"<nav\b[^>]*>.*?</nav>", self.header(path), re.DOTALL)
            self.assertEqual(2, len(groups), path)
            self.assertNotIn("設定", groups[0], path)
            self.assertIn("設定", groups[1], path)

    def test_each_navigation_group_says_what_it_is(self):
        """Two landmarks on one page need two names, or a screen reader offers
        the reader "navigation" twice and no way to tell them apart."""
        for path in self.PAGES:
            labels = re.findall(r'<nav\b[^>]*aria-label="([^"]*)"', self.header(path))
            self.assertEqual(2, len(labels), path)
            self.assertEqual(len(labels), len(set(labels)), path)

    def test_the_page_that_is_about_to_stop_still_carries_no_navigation(self):
        """The one exception, and it is not a new one: by the time the closed
        page is on screen there is no link on it that would reach anything."""
        body = self.post(pages.SHUTDOWN_PATH, {}).body

        self.assertNotIn("<nav", body)
        for label in self.NAVIGATION:
            self.assertNotIn(label, body, label)


class ReportTabsPointAtARunTest(HeaderFixture, unittest.TestCase):
    """Spec R-002: which run 市場報告 and 完整辯論 open, page by page.

    A page about one run points at that run. A page about no run in particular
    points at the newest run that has a report, so the reader reaches the latest
    one from anywhere rather than only from the room.
    """

    # The room follows the newest run whenever this Data Root has one, so the
    # front door is a page about one run nearly always. The state where it is
    # not is reachable and is here: a request naming a run that does not exist —
    # a stale bookmark, or an id typed wrong — leaves the room with nothing to
    # watch, which is the same page it shows before anything has been asked.
    ROOM_WITH_NOTHING_TO_WATCH = "/live?run=20260101T000000Z-btc-zzzz99"
    NO_RUN_PAGES = ("/history", "/settings", ROOM_WITH_NOTHING_TO_WATCH)

    def setUp(self):
        super().setUp()
        self.index_two_runs()

    def newest_run(self):
        return {
            "市場報告": "/run/{}/report.html".format(self.BTC),
            "完整辯論": "/run/{}/debate.html".format(self.BTC),
        }

    def test_a_page_about_no_run_opens_the_newest_run_that_has_a_report(self):
        for path in self.NO_RUN_PAGES:
            self.assertEqual(self.newest_run(), self.hrefs(path), path)

    def test_the_page_for_a_url_that_names_nothing_opens_it_too(self):
        """A 404 is a page about no run, not a page exempt from the site: a
        reader who mistyped a URL is exactly the reader who needs the way to the
        newest report to be one click rather than a trip through the room."""
        self.assertEqual(self.newest_run(), self.hrefs("/does-not-exist"))

    def test_the_page_that_refuses_a_launch_opens_it_too(self):
        self.assertEqual(
            self.newest_run(), self.hrefs_of(self.refused_launch(), "launch refusal")
        )

    def test_the_room_with_nothing_to_watch_is_still_the_page_it_says_it_is(self):
        """Discrimination: the case above is only the front door's own state if
        the room really has no run — otherwise it would be passing through the
        rule for a page that is about one."""
        body = self.get(self.ROOM_WITH_NOTHING_TO_WATCH).body

        self.assertIn("等待新的市場題目", body)
        self.assertIn('data-state="{}"'.format(live.STATUS_WAITING), body)

    def test_a_run_detail_page_opens_that_runs_own_files(self):
        self.assertEqual(
            {
                "市場報告": "/run/{}/report.html".format(self.TSMC),
                "完整辯論": "/run/{}/debate.html".format(self.TSMC),
            },
            self.hrefs("/run/" + self.TSMC),
        )

    def test_the_room_watching_one_run_opens_that_runs_own_files(self):
        self.assertEqual(
            {
                "市場報告": "/run/{}/report.html".format(self.TSMC),
                "完整辯論": "/run/{}/debate.html".format(self.TSMC),
            },
            self.hrefs("/live?run=" + self.TSMC),
        )

    def test_the_front_door_watching_a_run_with_no_report_yet_offers_neither(self):
        """FP direction, and the one that separates the two rules: this Data Root
        *has* a report, so a front door that fell back to the newest one would
        link here. The run being watched is the answer even when it has nothing
        to open — a room that quietly swapped in another run's report would be
        showing a reader a conclusion that is not the one on screen."""
        write_live_run(self.data_root)  # newer than either indexed run

        for path in ("/", "/live"):
            for label in ("市場報告", "完整辯論"):
                self.assertNotIn("href", self.tab(path, label), (path, label))

    def test_a_run_detail_page_of_a_run_with_no_files_offers_neither(self):
        run_id = "20260802T020000Z-btc-cccc33"
        write_run(self.data_root, run_id, "BTC 未來七天會不會漲", artifacts=())
        rebuild_index(self.data_root)

        for label in ("市場報告", "完整辯論"):
            self.assertNotIn("href", self.tab("/run/" + run_id, label), label)


class NoReportAnywhereTest(HeaderFixture, unittest.TestCase):
    """Spec R-002: with nothing to open, the two tabs are shown as disabled.

    The rendering is the room's own, unchanged: not an ``<a>`` at all, because a
    link that announces itself disabled and navigates anyway takes a reader who
    pressed Enter somewhere they did not ask to go.
    """

    PAGES = ("/", "/live", "/history", "/settings", "/does-not-exist")

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)

    def test_the_two_report_tabs_are_still_on_every_page(self):
        for path in self.PAGES:
            self.assertEqual(self.NAVIGATION, self.controls(path)[-6:-1], path)

    def test_neither_is_a_link_and_neither_is_in_the_tab_order(self):
        for path in self.PAGES:
            for label in ("市場報告", "完整辯論"):
                self.assert_disabled(self.tab(path, label), path)

    def test_the_launch_refusal_page_shows_them_disabled_as_well(self):
        """The other of the two pages assembled from a sentence. Both directions
        are pinned for both of them: with a report they open it, and with none
        they say so and go nowhere."""
        body = self.refused_launch()

        for label in ("市場報告", "完整辯論"):
            self.assert_disabled(self.tab_of(body, label, "launch refusal"), label)

    def assert_disabled(self, tab, what):
        self.assertTrue(tab.startswith("<span"), tab)
        self.assertNotIn("href", tab, what)
        self.assertNotIn("tabindex", tab, what)
        self.assertIn('role="link"', tab, what)
        self.assertIn('aria-disabled="true"', tab, what)


class RenderedFiveTabHeaderTest(HeaderFixture, unittest.TestCase):
    """The substitute for the screenshots this ticket asks for.

    No browser exists in this environment, so every page is rendered, written
    out for a reviewer to open, and the parts Spec R-002 and R-003 are about are
    asserted here rather than eyeballed. The same shape Ticket 04 used for the
    merged page.
    """

    KEPT = {
        "/": "t02-front-door.html",
        "/history": "t02-history.html",
        "/settings": "t02-settings.html",
        "/live?run=" + INDEXED_TSMC_RUN_ID: "t02-room-watching-a-run.html",
        "/run/" + INDEXED_TSMC_RUN_ID: "t02-run-detail.html",
        "/does-not-exist": "t02-not-found.html",
    }

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def keep(self, name, body):
        Path(tempfile.gettempdir()).joinpath(name).write_text(body, encoding="utf-8")

    def test_every_page_is_kept_with_its_five_tabs_and_the_button(self):
        for path, name in self.KEPT.items():
            self.keep(name, self.get(path).body)
            self.assertEqual(
                self.NAVIGATION + [pages.SHUTDOWN_LABEL],
                self.controls(path)[-6:],
                path,
            )

    def test_the_launch_refusal_page_is_kept_with_them_too(self):
        """It is a page like the others, and the only way to see it is to be
        refused a launch — so it is rendered by being refused one."""
        body = self.refused_launch()
        self.keep("t02-launch-refused.html", body)

        self.assertEqual(
            self.NAVIGATION + [pages.SHUTDOWN_LABEL],
            self.controls_of(body, "launch refusal")[-6:],
        )

    def test_the_closed_page_is_kept_with_none_of_it(self):
        body = self.post(pages.SHUTDOWN_PATH, {}).body
        self.keep("t02-server-closed.html", body)

        self.assertIn(pages.SHUTDOWN_PAGE_TITLE, body)
        self.assertNotIn("<nav", body)
        self.assertNotIn(pages.SHUTDOWN_LABEL, body)


class ProtectedZoneOuterwearTest(PageFixture, unittest.TestCase):
    """The frozen three got new paint and not one new class.

    Spec R2 freezes the chat room, the light badge and the three tallies:
    content, position and semantic colour class do not move, and only type,
    spacing and card style may. A repaint that reached for a new class would be a
    DOM change, so what is pinned here is each region's class vocabulary —
    derived from the ballot and the roster where it can be, listed where it is
    layout. The behaviour of those three is pinned by the tests that were already
    here, unchanged.
    """

    RUN_ID = "20260806T020000Z-x-cccc33"

    def setUp(self):
        super().setUp()
        run_dir = write_live_run(self.data_root, run_id=self.RUN_ID, question="這一題")
        (run_dir / "events.jsonl").write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n"
                # Every ballot position held by somebody at the end, every
                # provider family in the feed, and one seat that changed its
                # mind: between them they put every class the region can wear on
                # the page at once.
                for record in (
                    seat_message("spot-technical", "bullish", 60000, run_id=self.RUN_ID),
                    seat_message("derivatives", "bearish", 120000, run_id=self.RUN_ID),
                    seat_message("onchain", "neutral", 180000, run_id=self.RUN_ID),
                    seat_message(
                        "counter-evidence", "bullish", 240000, run_id=self.RUN_ID
                    ),
                    seat_message("social-macro", "bearish", 300000, run_id=self.RUN_ID),
                    seat_message(
                        "social-macro", "bullish", 360000, run_id=self.RUN_ID,
                        kind="final_vote", change_reason="改了",
                    ),
                )
            ),
            encoding="utf-8",
        )
        self.body = self.get("/").body

    def region(self, pattern):
        return re.search(pattern, self.body, re.DOTALL).group(0)

    def classes_in(self, markup):
        tokens = set()
        for value in re.findall(r'class="([^"]*)"', markup):
            tokens |= set(value.split())
        return tokens

    def stance_classes(self):
        """The three the ballot hands out; ``unknown`` only when a seat is
        silent, which the tally and the roll both show and the feed does not."""
        return set(live.STANCE_CLASSES)

    def provider_classes(self):
        return {identity.provider for identity in seat_identities().values()}

    def test_the_chat_room_carries_exactly_the_classes_it_carried(self):
        feed = self.region(r'<section class="panel chat-panel".*?</section>')

        self.assertEqual(
            {
                "panel", "chat-panel", "eyebrow", "feed", "feed-jump",
                "message", "message-head", "message-meta", "message-reason",
                "speaker", "speaker-avatar", "badge",
                "stance-change", "changed",
            }
            | self.stance_classes()
            | self.provider_classes(),
            self.classes_in(feed),
        )

    def test_the_three_tallies_carry_exactly_the_classes_they_carried(self):
        tally = self.region(
            r'<section class="panel" aria-labelledby="live-tally-heading">.*?</section>'
        )

        self.assertEqual(
            {"panel", "tally", "tally-label", "tally-note"} | self.stance_classes(),
            self.classes_in(tally),
        )

    def test_the_seat_roll_carries_exactly_the_classes_it_carried(self):
        """``agent-blurb`` is the one addition the roll has taken since: Spec
        R-005 asks for the roster's 白話說明 under each seat's name, so the card
        gained a line and nothing else did."""
        seats_panel = self.region(
            r'<section class="panel" aria-labelledby="live-seats-heading">.*?</section>'
        )

        self.assertEqual(
            {
                "panel", "agents", "agent", "agent-head", "agent-blurb",
                "avatar", "stance", "status",
            }
            | self.stance_classes()
            | {live.UNKNOWN_STANCE_CLASS}
            | self.provider_classes(),
            self.classes_in(seats_panel),
        )

    def test_a_light_badge_still_carries_nothing_but_light(self):
        """The five lights' colour is the authority's icon beside the word. A
        per-level class would be a new class in frozen markup and a second copy
        of a colour that already has an owner."""
        self.index_two_runs()

        # One page rather than two since Ticket 04: the light badge is shown by
        # the merged history and hit-rate page, and ``/stats`` is a redirect.
        found = re.findall(
            r'<span class="[^"]*\blight\b[^"]*"', self.get("/history").body
        )

        self.assertTrue(found)
        self.assertEqual({'<span class="light"'}, set(found))


class RoomSeatLabelsTest(PageFixture, unittest.TestCase):
    """A seat is named from the roster port, for **this run's** asset class.

    The room used to read the module-level view, which is the open set whatever
    the question was — so a 台股 run printed 幣圈 seat names. The expected values
    here come from :mod:`hoya_market_agents.seats` rather than being spelled out,
    because the roster is the authority and a name edited there must not need a
    test edited too.
    """

    def room_for(self, asset_class, run_id="20260806T020000Z-x-bbbb22"):
        write_live_run(
            self.data_root, run_id=run_id, question="這一題", asset_class=asset_class
        )
        return self.get("/").body

    def seat_panel(self, body):
        return re.search(
            r'<div class="agents" id="live-seats">.*?</section>', body, re.DOTALL
        ).group(0)

    def test_a_taiwan_stock_run_names_its_seats_from_the_stock_set(self):
        panel = self.seat_panel(self.room_for(ASSET_CLASS_TW_STOCK))

        for name in seat_display_names(ASSET_CLASS_TW_STOCK).values():
            self.assertIn(escape(name), panel, name)

    def test_a_us_stock_run_reads_the_same_stock_set(self):
        panel = self.seat_panel(self.room_for(ASSET_CLASS_US_STOCK))

        for name in seat_display_names(ASSET_CLASS_US_STOCK).values():
            self.assertIn(escape(name), panel, name)

    def test_a_crypto_run_names_its_seats_from_the_crypto_set(self):
        panel = self.seat_panel(self.room_for(ASSET_CLASS_CRYPTO))

        for name in seat_display_names(ASSET_CLASS_CRYPTO).values():
            self.assertIn(escape(name), panel, name)

    def test_a_run_recorded_before_the_field_existed_reads_the_open_set(self):
        panel = self.seat_panel(self.room_for(None))

        for name in seat_display_names(ASSET_CLASS_OPEN).values():
            self.assertIn(escape(name), panel, name)

    def test_a_stock_run_prints_no_name_that_is_only_in_the_crypto_set(self):
        """FP direction: the four assertions above would all pass on a page that
        printed every set. The sets differ, and only this run's is shown."""
        stock = set(seat_display_names(ASSET_CLASS_TW_STOCK).values())
        crypto = set(seat_display_names(ASSET_CLASS_CRYPTO).values())
        self.assertTrue(crypto - stock, "the fixture roster no longer discriminates")

        panel = self.seat_panel(self.room_for(ASSET_CLASS_TW_STOCK))

        for name in crypto - stock:
            self.assertNotIn(escape(name), panel, name)

    def test_the_byline_and_the_seat_label_both_move_with_the_set(self):
        """The two places a seat is named: ``Codex・名稱`` and ``Agent n｜名稱``."""
        panel = self.seat_panel(self.room_for(ASSET_CLASS_TW_STOCK))

        for seat_id, identity in seat_identities(ASSET_CLASS_TW_STOCK).items():
            self.assertIn(escape(identity.display_name), panel, seat_id)
            self.assertIn(
                "{}｜{}".format(
                    escape(identity.agent_number),
                    escape(seat_display_names(ASSET_CLASS_TW_STOCK)[seat_id]),
                ),
                panel,
                seat_id,
            )

    def test_a_speaker_in_the_chat_is_named_from_the_same_set(self):
        run_id = "20260806T020000Z-x-bbbb22"
        run_dir = write_live_run(
            self.data_root,
            run_id=run_id,
            question="這一題",
            asset_class=ASSET_CLASS_TW_STOCK,
        )
        (run_dir / "events.jsonl").write_text(
            json.dumps(seat_message("derivatives", "bullish", 60000, run_id=run_id),
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        body = self.get("/").body
        feed = re.search(
            r'<div class="feed" id="live-feed".*?</div>\n<button', body, re.DOTALL
        ).group(0)

        stock = seat_display_names(ASSET_CLASS_TW_STOCK)["derivatives"]
        crypto = seat_display_names(ASSET_CLASS_CRYPTO)["derivatives"]
        self.assertNotEqual(stock, crypto)
        self.assertIn(escape(stock), feed)
        self.assertNotIn(escape(crypto), feed)

    def test_the_web_app_holds_no_seat_name_of_its_own(self):
        """Spec R7: the labels come from the authority, so no module in the web
        app may spell one. A grep, because a second table is only ever found by
        looking for the words."""
        names = set()
        for asset_class in (ASSET_CLASS_TW_STOCK, ASSET_CLASS_CRYPTO, ASSET_CLASS_OPEN):
            names |= set(seat_display_names(asset_class).values())
        package = Path(pages.__file__).parent
        for module in sorted(package.glob("*.py")):
            source = module.read_text(encoding="utf-8")
            for name in names:
                self.assertNotIn(name, source, "{}: {}".format(module.name, name))


class SingleSiteStylesheetTest(PageFixture, unittest.TestCase):
    """One stylesheet paints every page, the debate room included.

    The room used to ship an isolated sheet with tokens of its own, so the site
    had two font stacks, two radius scales and two greens — and only one of the
    two sets was under the contrast test. What is pinned here is the replacement:
    every page's ``<style>`` is the same sheet, that sheet is
    :func:`pages.stylesheet`, and the room is inside the token system rather than
    exempt from it.
    """

    # ``/stats`` is a redirect since Ticket 04 and carries no sheet to compare.
    PAGES = ("/", "/live", "/history", "/settings", "/nope")

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def style_of(self, path):
        body = self.get(path).body
        self.assertIn('<link rel="stylesheet" href="/static/site.css">', body, path)
        self.assertNotIn("<style", body, path)
        return self.get("/static/site.css").body

    def test_every_page_carries_the_one_sheet_and_nothing_else(self):
        sheet = pages.stylesheet()
        for path in self.PAGES + ("/run/20260801T020000Z-btc-aaaa11",):
            body = self.get(path).body
            self.assertEqual(sheet, self.style_of(path), path)
            self.assertEqual(0, body.count("<style"), path)
            self.assertEqual(1, body.count('href="/static/site.css"'), path)

    def test_the_room_is_painted_by_the_token_table_and_by_one_palette(self):
        """Spec R-004 retires dark mode: the room reads the same tokens it
        always did, and the sheet no longer carries a second set of values for
        an operating system that prefers dark."""
        style = self.style_of("/")

        self.assertNotIn("prefers-color-scheme", style)
        for token in ("--page", "--surface", "--affirm", "--oppose", "--abstain"):
            self.assertIn("{}:".format(token), style, token)

    def test_the_rooms_own_tokens_are_gone_from_every_page(self):
        """The nine tokens the isolated sheet declared for itself. Their roles
        live on under the site's own names; a second name for the same green is
        what this stops coming back."""
        for path in self.PAGES:
            style = self.style_of(path)
            for token in ("--paper", "--ink", "--brand", "--wash", "--line",
                          "--bull", "--bear", "--neutral"):
                self.assertNotIn("{}:".format(token), style, "{} {}".format(path, token))

    def test_the_room_and_the_card_pages_share_the_same_surface_recipe(self):
        """The load-bearing claim of the merge: the room's panel and the other
        pages' card are one rule, so they cannot drift apart."""
        style = self.style_of("/")

        self.assertIn(
            ".card,.panel,.metric,.detail-panel,fieldset.settings-group{", style
        )

    def test_no_element_in_the_room_wears_a_card_class(self):
        """FP direction: the room shares the *sheet*, not the markup. Its DOM is
        frozen (Spec R2), so no element gained a class in the merge."""
        body = self.get("/").body
        body = body[body.index("<body>"):]

        self.assertEqual([], re.findall(r'class="[^"]*\bcard\b[^"]*"', body))

    def test_the_sheet_asks_the_network_for_nothing_on_any_page(self):
        for path in self.PAGES:
            style = self.style_of(path)
            for forbidden in ("url(", "@import", "http:", "https:", "//"):
                self.assertNotIn(forbidden, style, "{} {}".format(path, forbidden))

    def test_the_font_stacks_are_the_operating_systems_own(self):
        style = self.style_of("/")

        self.assertIn('--font-sans:"Microsoft JhengHei",system-ui,', style)
        self.assertIn("--font-mono:ui-monospace,", style)


# -- the white shell ---------------------------------------------------------

# Every element that never has a closing tag, so a walk of the document can tell
# "this tag opened a region" from "this tag was the whole element". Only the ones
# these pages actually emit would be needed; the list is HTML's own so that a
# page which grows an ``<img>`` does not silently unbalance the walk.
VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


class _Nesting(HTMLParser):
    """Every element in one page, with the ancestors it was found under.

    Written for one question — "what is this frosted panel sitting on?" — which
    is a question about the document and cannot be answered from the stylesheet
    alone.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open = []
        self.elements = []

    def handle_starttag(self, tag, attrs):
        classes = frozenset(dict(attrs).get("class", "").split())
        self.elements.append((tag, classes, tuple(self.open)))
        if tag not in VOID_ELEMENTS:
            self.open.append((tag, classes))

    def handle_startendtag(self, tag, attrs):
        classes = frozenset(dict(attrs).get("class", "").split())
        self.elements.append((tag, classes, tuple(self.open)))

    def handle_endtag(self, tag):
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                del self.open[index:]
                return


def elements_of(body):
    """``[(tag, its classes, its ancestors), ...]`` for one rendered page."""
    parser = _Nesting()
    parser.feed(body)
    return parser.elements


def declaration_blocks(sheet):
    """``[(selector, declarations), ...]`` for every leaf block in a sheet.

    A leaf block is one whose body holds no further braces, which is every rule
    that paints something — the ones inside a media query included, and the
    at-rule wrappers themselves excluded because they paint nothing.
    """
    return [
        (selector.strip(), declarations)
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet)
    ]


def classes_painted_with(sheet, value):
    """Every class the sheet reaches for when it paints with ``value``.

    Derived from the sheet rather than listed here on purpose: which selector
    wears the frost is a design decision this ticket is allowed to change, and a
    test that named them would have to be edited every time one moved. What must
    not change is *what* wears it, which is what the tests below ask.
    """
    painted = set()
    for selector, declarations in declaration_blocks(sheet):
        if value in declarations:
            painted |= set(re.findall(r"\.([\w-]+)", selector))
    return painted


def selector_words(selector):
    """The class names and element names one selector list can match on.

    Deliberately coarse: ``.rule.current`` yields both words rather than the
    compound, so an element is treated as painted if any rule could paint it.
    Erring towards "this is painted" is the safe direction for the backdrop walk
    below — the failure it must never make is missing a fill that is really
    there.
    """
    words = set()
    for part in selector.split(","):
        words |= set(re.findall(r"\.([\w-]+)", part))
        leading = re.match(r"\s*([a-z][\w-]*)", part)
        if leading:
            words.add(leading.group(1))
    return words


def background_fills(sheet):
    """``{class or element name: the palette token it fills with}``.

    Every rule in the sheet that paints a background, read out of the sheet
    itself. This is what lets a walk of a rendered page answer "what colour is
    actually behind this element" without anybody writing that answer down.
    """
    fills = {}
    for selector, declarations in declaration_blocks(sheet):
        found = re.search(r"background(?:-color)?:var\(--([\w-]+)\)", declarations)
        if not found:
            continue
        for word in selector_words(selector):
            fills[word] = found.group(1).replace("-", "_")
    return fills


def glass_surfaces(sheet):
    """``{class name: the glass token that class is filled with}``.

    Keyed on the palette's own glass tokens, so a surface frosted with a second
    translucent token would be picked up here and measured against *its* own
    backdrop rather than quietly assumed to want the first one's.
    """
    frosted = {}
    for selector, declarations in declaration_blocks(sheet):
        found = re.search(r"background-color:var\(--([\w-]+)\)", declarations)
        if not found:
            continue
        token = found.group(1).replace("-", "_")
        if token not in design_tokens.GLASS:
            continue
        for name in re.findall(r"\.([\w-]+)", selector):
            frosted[name] = token
    return frosted


def backdrop_of(ancestors, fills):
    """The palette tokens the nearest painting ancestor fills with.

    Walks outward and stops at the first ancestor that paints anything, which is
    what a browser composites a translucent surface against. Elements that paint
    nothing are stepped over rather than judged: a wrapper with no fill changes
    no colour, so it is not this walk's business how many of them there are.

    Returns a set because an element could in principle match two fill rules;
    the caller asserts on the set, so an ambiguous case is reported instead of
    being resolved by guesswork.
    """
    for tag, classes in reversed(ancestors):
        painted = {fills[word] for word in set(classes) | {tag} if word in fills}
        if painted:
            return painted
    return set()


class GoogleStyleShellTest(HeaderFixture, unittest.TestCase):
    """Spec R-004's white shell, on every kind of page this server sends.

    The requirement is one visual system for the whole site — white canvas,
    space, a frosted panel and 紅藍綠黃 as decoration — so what is checked here is
    coverage and honesty rather than any particular rule's text:

    * all six kinds of page get it, the two that answer a refusal and the last
      one the server ever sends included, because "全站" is a claim about the
      pages a reader can actually reach;
    * the frost is the browser's own ``backdrop-filter`` and fetches nothing;
    * every frosted surface is composited over the colour ``design_tokens``
      measured it on, which is the condition that makes its AA number the ratio
      a reader really receives;
    * the four hues decorate and never speak: no rule makes one of them the
      colour of a word, and none of them reaches inside the protected regions.

    No colour literal and no class name order is asserted anywhere here (Spec
    R-004 測試決策): the palette is ``design_tokens``' business, and this asks
    what the page does with it.
    """

    RUN_ID = INDEXED_BTC_RUN_ID

    GLASS_FILL = "background-color:var(--glass-surface);"
    GLASS_BLUR = "backdrop-filter:blur(var(--glass-blur));"

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def page_kinds(self):
        """The six kinds of page Ticket 03 names, each as its rendered HTML.

        The shutdown page is rendered rather than fetched: asking the server for
        it would stop the server, and it is the one page whose header is
        deliberately different, so leaving it out would leave out the only page
        this restyle could break without any other test noticing.
        """
        return {
            "即時辯論": self.get("/").body,
            "歷史與命中率": self.get("/history").body,
            "設定": self.get("/settings").body,
            "run 詳情": self.get("/run/{}".format(self.RUN_ID)).body,
            "錯誤頁": self.get("/nope").body,
            "關閉頁": pages.render_shutdown_page(),
        }

    def sheet_of(self, body):
        self.assertIn('<link rel="stylesheet" href="/static/site.css">', body)
        self.assertNotIn("<style", body)
        return self.get("/static/site.css").body

    def test_every_kind_of_page_is_built_from_the_one_shell(self):
        """The furniture of the redesign, on all six: one sheet, one skip link
        into one main landmark, one header and one footer. A page that kept an
        older layout would be a page missing one of them."""
        for name, body in self.page_kinds().items():
            self.assertEqual(pages.stylesheet(), self.sheet_of(body), name)
            self.assertEqual(0, body.count("<style"), name)
            self.assertEqual(1, body.count('href="/static/site.css"'), name)
            self.assertIn('<a class="skip-link" href="#main">', body, name)
            self.assertIn('<main id="main"', body, name)
            self.assertIn("<header", body, name)
            self.assertIn('<footer class="site-footer">', body, name)

    def test_every_kind_of_page_carries_a_frosted_surface(self):
        for name, body in self.page_kinds().items():
            sheet = self.sheet_of(body)
            self.assertIn(self.GLASS_FILL, sheet, name)
            self.assertIn(self.GLASS_BLUR, sheet, name)

    def test_the_frost_is_the_browsers_own_and_asks_for_nothing(self):
        """毛玻璃＝原生 ``backdrop-filter``: no image, no asset, nothing fetched —
        and the prefixed spelling beside it, because WebKit still needs it and a
        surface that frosts in one browser and not another is two designs."""
        sheet = pages.stylesheet()

        self.assertIn("-webkit-" + self.GLASS_BLUR, sheet)
        for forbidden in ("url(", "@import", "http:", "https:", "//"):
            self.assertNotIn(forbidden, sheet, forbidden)

    def test_every_frosted_surface_is_composited_over_the_colour_it_was_measured_on(self):
        """The condition the glass token's measured colour assumes.

        ``design_tokens.GLASS`` flattens each frosted fill over one named
        backdrop, and the contrast test holds every word on that surface to the
        composite. So the claim that has to be true of a *page* is that the
        colour really behind a frosted panel is that same backdrop: frost over a
        white card would be read against a colour nobody measured, and its AA
        number would be about a surface the site does not paint.

        **The colour, never the shape.** What is asserted is the effective
        background — the fill of the nearest ancestor that paints one, which is
        what a browser composites against — and both halves of it are read out of
        the sheet and the rendered page rather than written down here. How deep
        the panel sits, and how many wrappers are on the way, is not asked:
        an element that paints nothing changes no colour, so a layout that grows
        a plain wrapper stays green while one that grows a *painted* one goes
        red, which is the only distinction this can honestly draw (Spec R-004
        測試決策：不耦合 DOM 巢狀細節).
        """
        sheet = pages.stylesheet()
        frosted = glass_surfaces(sheet)
        fills = background_fills(sheet)
        self.assertTrue(frosted)

        for name, body in self.page_kinds().items():
            found = 0
            for _tag, classes, ancestors in elements_of(body):
                wearing = classes & set(frosted)
                if not wearing:
                    continue
                found += 1
                for glass in wearing:
                    self.assertEqual(
                        {design_tokens.GLASS[frosted[glass]]},
                        backdrop_of(ancestors, fills),
                        "{}: .{} is composited over the wrong colour".format(
                            name, glass
                        ),
                    )
            self.assertTrue(found, "{} wears no frost".format(name))

    def test_the_four_decorative_hues_reach_every_kind_of_page(self):
        """紅藍綠黃 as 點綴, everywhere — including the page that says the server
        has stopped, which is a page of this site like any other."""
        hues = ["var(--{})".format(t.replace("_", "-")) for t in pages.DECOR_TOKENS]
        self.assertEqual(4, len(hues))

        for name, body in self.page_kinds().items():
            sheet = self.sheet_of(body)
            for hue in hues:
                self.assertIn(hue, sheet, "{} {}".format(name, hue))

    def test_no_decorative_hue_is_ever_the_colour_of_a_word(self):
        """"裝飾色與語意色不得混用" is checkable: a decoration is a fill, a bar or
        a hairline, and the moment one becomes the colour of text it is saying
        something — which is the ballot's and the light's job, never 紅藍綠黃's."""
        sheet = pages.stylesheet()

        self.assertIsNone(re.search(r"(?<![\w-])color:var\(--google-", sheet))

    def test_the_protected_regions_wear_neither_frost_nor_decoration(self):
        """The chat room, the seat roll and the three tallies keep their own
        skin. 只換外衣 lets this design reach their type, spacing and card style;
        it does not let a decorative hue or a frosted fill inside a region whose
        colours mean something."""
        sheet = pages.stylesheet()
        reserved = set(glass_surfaces(sheet)) | classes_painted_with(
            sheet, "var(--google-"
        )
        room = self.get("/").body

        for heading in ("live-tally-heading", "live-seats-heading"):
            region = re.search(
                r'<section class="panel" aria-labelledby="{}".*?</section>'.format(
                    heading
                ), room, re.DOTALL,
            ).group(0)
            self.assertEqual(set(), self.classes_in(region) & reserved, heading)

        chat = re.search(
            r'<section class="panel chat-panel".*?</section>', room, re.DOTALL
        ).group(0)
        self.assertEqual(set(), self.classes_in(chat) & reserved, "chat")

    def classes_in(self, markup):
        found = set()
        for value in re.findall(r'class="([^"]*)"', markup):
            found |= set(value.split())
        return found

    def test_a_system_that_prefers_dark_is_still_served_the_white_site(self):
        """The only honest form of "深色模式退場" a reader of the output can
        check: there is no query on the preference anywhere on any page, and one
        palette to answer with — whose canvas is measured light here rather than
        compared against a hex, because the value is ``design_tokens``' to
        choose and the *lightness* is the requirement."""
        for name, body in self.page_kinds().items():
            sheet = self.sheet_of(body)
            self.assertNotIn("prefers-color-scheme", body, name)
            self.assertEqual(1, sheet.count(":root"), name)
            for token in ("--page", "--surface"):
                value = re.search(r"{}:(#[0-9a-f]{{6}});".format(token), sheet).group(1)
                self.assertGreater(luminance(value), 0.5, "{} {}".format(name, token))


class VisualRefreshTest(PageFixture, unittest.TestCase):
    """Ticket 09's polish, checked where a browser receives it.

    The fixture renders real page responses and fetches the routed stylesheet;
    it does not inspect the Python assemblers or the CSS source path directly.
    Component assertions first prove that the rendered DOM wears the selector
    being checked, so a dead rule cannot satisfy the visual contract.
    """

    def setUp(self):
        super().setUp()
        run_dir = write_live_run(self.data_root)
        append_events(
            run_dir,
            [seat_message("spot-technical", "bullish", 240000, round_number=1)],
        )
        self.index_two_runs()
        self.sheet = self.get("/static/site.css").body
        self.live_page = self.get("/live").body
        self.history_page = self.get("/history").body
        self.settings_page = self.get("/settings").body

    def declarations_for(self, selector_fragment):
        return "".join(
            declarations
            for selector, declarations in declaration_blocks(self.sheet)
            if selector_fragment in selector
        )

    def at_rule(self, heading):
        start = self.sheet.index(heading)
        opening = self.sheet.index("{", start)
        depth = 1
        for index in range(opening + 1, len(self.sheet)):
            if self.sheet[index] == "{":
                depth += 1
            elif self.sheet[index] == "}":
                depth -= 1
                if depth == 0:
                    return self.sheet[opening + 1 : index]
        self.fail("unclosed CSS at-rule: {}".format(heading))

    def test_rendered_controls_move_gently_and_keep_a_visible_keyboard_ring(self):
        for body in (self.live_page, self.history_page, self.settings_page):
            self.assertRegex(body, r"<(?:button|input|select)\b")

        controls = self.declarations_for("button.primary")
        fields = self.declarations_for(".field input")
        focus = self.declarations_for(":focus-visible")
        self.assertIn("transition:", controls)
        self.assertIn("transition:", fields)
        self.assertIn("outline:", focus)
        self.assertIn("outline-offset:", focus)

    def test_rendered_tables_have_a_scannable_header_and_row_hover(self):
        self.assertIn("<table", self.history_page)
        header = self.declarations_for("thead th")
        hover = self.declarations_for("tbody tr:hover")
        self.assertIn("text-transform:uppercase", header)
        self.assertIn("letter-spacing:", header)
        self.assertIn("background:var(--page)", hover)

    def test_chat_bubbles_and_avatars_share_a_top_edge(self):
        self.assertIn('class="message ', self.live_page)
        self.assertIn('class="speaker-avatar"', self.live_page)
        self.assertIn("align-items:flex-start", self.declarations_for(".message-head"))
        self.assertIn("align-items:flex-start", self.declarations_for(".speaker"))
        self.assertIn("flex:0 0 auto", self.declarations_for(".speaker-avatar"))

    def test_rendered_badges_and_seat_cards_receive_their_polish(self):
        self.assertIn('class="badge ', self.live_page)
        self.assertIn('class="agent ', self.live_page)
        badge = self.declarations_for(".badge")
        agent = self.declarations_for(".agent")
        hover = self.declarations_for(".agent:hover")
        self.assertIn("display:inline-flex", badge)
        self.assertIn("letter-spacing:", badge)
        self.assertIn("transition:", agent)
        self.assertIn("transform:translateY(", hover)

    def test_reduced_motion_preference_cancels_animation_and_transitions(self):
        reduced = self.at_rule("@media (prefers-reduced-motion:reduce)")
        self.assertIn("animation-duration:", reduced)
        self.assertIn("transition-duration:", reduced)
        self.assertIn("scroll-behavior:auto", reduced)

    def test_every_page_keeps_the_viewport_contract_and_small_screen_rules(self):
        for body in (self.live_page, self.history_page, self.settings_page):
            self.assertIn(
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                body,
            )
            self.assertIn('class="page-layout ', body)
        compact = self.at_rule("@media (max-width:38rem)")
        self.assertIn("width:100%", compact)
        self.assertIn("grid-template-columns:1fr", compact)


class LiveScriptTest(PageFixture, unittest.TestCase):
    """One script, served from this origin, and no inline block anywhere."""

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.script = (Path(live.__file__).parent / "static" / "live.js").read_text(
            encoding="utf-8"
        )

    def test_the_script_is_served_as_javascript(self):
        response = self.get("/live.js")

        self.assertEqual(200, response.status)
        self.assertEqual("text/javascript; charset=utf-8", response.headers["Content-Type"])

    def test_the_room_loads_that_script_and_writes_none_of_its_own(self):
        body = self.get("/live").body

        self.assertIn('<script src="/live.js" defer></script>', body)
        self.assertEqual(1, body.count("<script"))

    def test_no_other_page_carries_a_script_at_all(self):
        self.index_two_runs()

        for path in ("/history", "/run/20260801T020000Z-btc-aaaa11", "/nope"):
            self.assertNotIn("<script", self.get(path).body, path)

    def test_the_script_never_assembles_markup_from_run_data(self):
        """``innerHTML`` is how a public reason would become a page's markup."""
        self.assertNotIn("innerHTML", self.script)
        self.assertNotIn("insertAdjacentHTML", self.script)
        self.assertNotIn("document.write", self.script)

    def test_the_script_folds_a_streamed_reason_the_way_the_page_does(self):
        """Same two shapes as :func:`pages._message`, built from the brief the
        server already computed."""
        self.assertIn('el("details", "message-reason")', self.script)
        self.assertIn('el("p", "message-reason")', self.script)
        self.assertIn("item.public_brief", self.script)
        self.assertIn('el("span", "reason-ellipsis", "…")', self.script)
        self.assertIn("brief.length - 1", self.script)
        self.assertIn("顯示全文", self.script)
        self.assertIn("收合", self.script)

    def test_the_script_splits_no_sentences_of_its_own(self):
        """The brief arrives with the message. A stop table here would be a
        second answer to the question :func:`live.first_sentence` answers, and
        the two would drift."""
        self.assertNotIn("。！？", self.script)
        self.assertNotIn(".!?", self.script)

    def test_the_script_opens_the_stream_at_the_cursor_the_page_was_drawn_with(self):
        self.assertIn('"?after=" + encodeURIComponent(feed.dataset.cursor)',
                      self.script)

    def test_the_script_listens_for_the_three_frames_the_server_sends(self):
        for name in ("snapshot", "append", "done"):
            self.assertIn('addEventListener("{}"'.format(name), self.script, name)


class WebappLayeringTest(PageFixture, unittest.TestCase):
    """Ticket 08: templates, page assemblers and static files are real inputs."""

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.webapp_root = Path(live.__file__).parent

    def test_the_approved_frontend_layers_exist(self):
        for relative in (
            "templates/document.html",
            "templates/history.html",
            "templates/run.html",
            "templates/live.html",
            "templates/settings.html",
            "static/site.css",
            "static/live.js",
            "pages/__init__.py",
            "pages/components.py",
            "pages/history_page.py",
            "pages/run_page.py",
            "pages/live_page.py",
            "pages/settings_page.py",
        ):
            self.assertTrue((self.webapp_root / relative).is_file(), relative)

    def test_the_settings_route_uses_the_settings_template(self):
        templates = self.data_root / "templates"
        shutil.copytree(self.webapp_root / "templates", templates)
        marker = '<div data-template="settings"></div>'
        settings_template = templates / "settings.html"
        settings_template.write_text(
            marker + settings_template.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        with mock.patch(
            "hoya_market_agents.webapp.pages.components.TEMPLATES_DIR", templates
        ):
            body = self.get("/settings").body

        self.assertIn(marker, body)

    def test_every_page_loads_the_external_site_sheet_and_has_no_inline_style(self):
        self.index_two_runs()
        for path in ("/", "/live", "/history", "/settings", "/nope",
                     "/run/20260801T020000Z-btc-aaaa11"):
            body = self.get(path).body
            self.assertIn(
                '<link rel="stylesheet" href="/static/site.css">', body, path
            )
            self.assertNotIn("<style", body, path)
            policy = self.get(path).headers["Content-Security-Policy"]
            self.assertIn("style-src 'self'", policy, path)
            self.assertNotIn("unsafe-inline", policy, path)

    def test_the_two_static_files_are_served_from_the_approved_directory(self):
        css = self.get("/static/site.css")
        script = self.get("/static/live.js")

        self.assertEqual(200, css.status)
        self.assertEqual("text/css; charset=utf-8", css.headers["Content-Type"])
        self.assertEqual(pages.stylesheet().encode("utf-8"), css.body_bytes)
        self.assertIn(
            (self.webapp_root / "static" / "site.css").read_text(encoding="utf-8"),
            css.body,
        )
        self.assertEqual(200, script.status)
        self.assertEqual("text/javascript; charset=utf-8", script.headers["Content-Type"])
        self.assertEqual(
            (self.webapp_root / "static" / "live.js").read_bytes(),
            script.body_bytes,
        )

    def test_the_legacy_live_script_route_reads_the_static_file(self):
        self.assertEqual(
            self.get("/static/live.js").body_bytes,
            self.get("/live.js").body_bytes,
        )

    def test_the_static_route_is_an_exact_allowlist(self):
        for path in (
            "/static/server.py",
            "/static/../server.py",
            "/static/%2e%2e/server.py",
            "/static/site.css/anything",
            "/static/unknown.css",
        ):
            self.assertEqual(404, self.get(path).status, path)

    def test_the_public_pages_module_is_the_new_package(self):
        package = Path(pages.__file__)

        self.assertEqual("__init__.py", package.name)
        self.assertEqual("pages", package.parent.name)
        self.assertNotEqual(
            Path(server_module.__file__).with_name("pages.py").resolve(),
            package.resolve(),
        )


class LiveStreamTest(PageFixture, unittest.TestCase):
    """The stream: what is sent first, what is sent after, and when it stops."""

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    def frames(self, body):
        """Return ``[(event name, id or None, payload), ...]`` from one stream."""
        parsed = []
        for block in body.split("\n\n"):
            lines = [line for line in block.splitlines() if line.strip()]
            fields = {}
            for line in lines:
                name, _, value = line.partition(": ")
                fields.setdefault(name, value)
            if "event" not in fields:
                continue
            parsed.append(
                (
                    fields["event"],
                    fields.get("id"),
                    json.loads(fields["data"]) if "data" in fields else None,
                )
            )
        return parsed

    def open_stream(self, path="/live/events", headers=(), break_after=None):
        return self.get(path, headers=headers, break_after=break_after)

    def test_the_stream_declares_itself_as_an_event_stream(self):
        response = self.open_stream()

        self.assertEqual(
            "text/event-stream; charset=utf-8", response.headers["Content-Type"]
        )
        self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_the_reconnect_delay_is_sent_before_anything_else(self):
        body = self.open_stream().body

        self.assertLess(body.index("retry: 2000"), body.index("event:"))

    def test_a_first_connection_is_answered_with_the_whole_room(self):
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("news", "bearish", 250000),
            ],
        )

        name, cursor, payload = self.frames(self.open_stream().body)[0]

        self.assertEqual("snapshot", name)
        self.assertEqual(2, len(payload["messages"]))
        self.assertTrue(cursor.startswith(LIVE_RUN_ID + "@"))

    def test_a_client_that_comes_back_is_sent_only_what_it_missed(self):
        first = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )
        append_events(self.run_dir, [seat_message("news", "bearish", 250000)])

        name, _, payload = self.frames(
            self.open_stream(headers=[("Last-Event-ID", live.make_cursor(LIVE_RUN_ID, first))]).body
        )[0]

        self.assertEqual("append", name)
        self.assertEqual(["news"], [m["seat_id"] for m in payload["messages"]])

    def test_what_it_missed_still_arrives_with_the_whole_score(self):
        """A late frame carries the counts, or a reconnecting page shows nothing."""
        first = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )
        append_events(self.run_dir, [seat_message("news", "bearish", 250000)])

        _, _, payload = self.frames(
            self.open_stream(headers=[("Last-Event-ID", live.make_cursor(LIVE_RUN_ID, first))]).body
        )[0]

        self.assertEqual(
            {"bullish": 1, "bearish": 1, "neutral": 0},
            {entry["stance"]: entry["count"] for entry in payload["tally"]},
        )

    def test_a_page_just_rendered_hands_its_cursor_over_as_a_query(self):
        first = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )
        append_events(self.run_dir, [seat_message("news", "bearish", 250000)])

        name, _, payload = self.frames(
            self.open_stream(
                "/live/events?after={}".format(live.make_cursor(LIVE_RUN_ID, first))
            ).body
        )[0]

        self.assertEqual("append", name)
        self.assertEqual(["news"], [m["seat_id"] for m in payload["messages"]])

    def test_the_header_wins_over_the_query_because_it_is_the_newer_of_the_two(self):
        first = append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )
        second = append_events(self.run_dir, [seat_message("news", "bearish", 250000)])

        _, _, payload = self.frames(
            self.open_stream(
                "/live/events?after={}".format(live.make_cursor(LIVE_RUN_ID, first)),
                headers=[("Last-Event-ID", live.make_cursor(LIVE_RUN_ID, second))],
            ).body
        )[0]

        self.assertEqual([], payload["messages"])

    def test_a_cursor_from_another_run_is_answered_with_the_whole_room(self):
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        name, _, payload = self.frames(
            self.open_stream(headers=[("Last-Event-ID", "20260101T000000Z-btc-other1@0")]).body
        )[0]

        self.assertEqual("snapshot", name)
        self.assertEqual(1, len(payload["messages"]))

    def test_a_cursor_past_the_end_is_answered_with_the_whole_room(self):
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        name, _, payload = self.frames(
            self.open_stream(
                headers=[("Last-Event-ID", live.make_cursor(LIVE_RUN_ID, 99999))]
            ).body
        )[0]

        self.assertEqual("snapshot", name)
        self.assertEqual(1, len(payload["messages"]))

    def test_what_is_appended_while_the_stream_runs_is_pushed(self):
        appended = []

        def write_one(_seconds):
            appended.append(1)
            if len(appended) == 1:
                append_events(
                    self.run_dir, [seat_message("news", "bearish", 250000)]
                )

        self.build_handler(
            stream=self.single_pass_stream(max_seconds=2.5, sleeper=write_one)
        )
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        frames = self.frames(self.open_stream().body)

        self.assertEqual(["snapshot", "append"], [name for name, _, _ in frames])
        self.assertEqual(["news"], [m["seat_id"] for m in frames[1][2]["messages"]])

    def test_nothing_new_pushes_nothing(self):
        """FP direction: a stream that pushes every pass would pass the test above."""
        self.build_handler(
            stream=self.single_pass_stream(max_seconds=2.5)
        )
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        frames = self.frames(self.open_stream().body)

        self.assertEqual(["snapshot"], [name for name, _, _ in frames])

    def test_a_finished_run_is_told_so_and_the_stream_ends(self):
        write_run(self.data_root, "20260806T040000Z-btc-fin001", "已完成的題目")

        frames = self.frames(
            self.open_stream("/live/events?run=20260806T040000Z-btc-fin001").body
        )

        self.assertEqual("done", frames[-1][0])
        self.assertEqual("green", frames[-1][2]["outcome"]["confidence_level"])
        self.assertEqual(
            "/run/20260806T040000Z-btc-fin001", frames[-1][2]["outcome"]["run_href"]
        )

    def test_a_run_still_going_is_never_told_it_is_done(self):
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        self.assertEqual(
            [], [name for name, _, _ in self.frames(self.open_stream().body) if name == "done"]
        )

    def test_a_data_root_with_no_run_says_it_is_waiting_and_stops(self):
        for path in sorted(
            (self.data_root / "runs").rglob("*"), key=lambda p: -len(p.parts)
        ):
            path.unlink() if path.is_file() else path.rmdir()

        frames = self.frames(self.open_stream().body)

        self.assertEqual(["waiting"], [name for name, _, _ in frames])

    def test_a_quiet_stream_keeps_the_connection_open_with_a_comment(self):
        self.build_handler(
            stream=self.single_pass_stream(max_seconds=1.5, heartbeat_seconds=0.5)
        )

        self.assertIn(": 保持連線", self.open_stream().body)

    def test_a_stream_that_just_spoke_does_not_also_send_a_heartbeat(self):
        write_run(self.data_root, "20260806T040000Z-btc-fin001", "已完成的題目")
        self.build_handler(
            stream=self.single_pass_stream(max_seconds=1.5, heartbeat_seconds=0.5)
        )

        body = self.open_stream("/live/events?run=20260806T040000Z-btc-fin001").body

        self.assertNotIn(": 保持連線", body)

    def test_the_stream_leaves_no_thread_behind(self):
        before = set(threading.enumerate())

        self.open_stream()

        self.assertEqual(before, set(threading.enumerate()))

    def test_a_tab_closed_and_opened_again_is_caught_up_and_not_told_twice(self):
        """The acceptance path: the page is re-rendered, then resumes from itself."""
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )
        cursor = re.search(r'data-cursor="([^"]+)"', self.get("/live").body).group(1)
        append_events(self.run_dir, [seat_message("news", "bearish", 250000)])

        name, _, payload = self.frames(
            self.open_stream("/live/events?after={}".format(cursor)).body
        )[0]

        self.assertEqual("append", name)
        self.assertEqual(["news"], [m["seat_id"] for m in payload["messages"]])
        self.assertEqual(
            {"bullish": 1, "bearish": 1, "neutral": 0},
            {entry["stance"]: entry["count"] for entry in payload["tally"]},
        )

    def test_the_re_rendered_page_already_holds_what_came_before(self):
        """FP direction: resuming is only correct because the page is not empty."""
        append_events(
            self.run_dir, [seat_message("spot-technical", "bullish", 240000)]
        )

        body = self.get("/live").body

        self.assertIn("spot-technical 的公開理由", body)
        self.assertEqual(1, body.count('class="message '))


class LiveFailureIsolationTest(PageFixture, unittest.TestCase):
    """Architecture §4.0.1: the live view breaking must not reach the run.

    The fixture is a real finished run bundle from the fake drill, so "the run
    is intact" is checked the way the operator checks it — ``verify_run`` — and
    not by comparing a directory to itself.
    """

    def setUp(self):
        super().setUp()
        from hoya_market_agents.competition_drill import run_fake_competition_drill

        self.result = run_fake_competition_drill(
            data_root=self.data_root,
            question="BTC 過去 14 日的市場狀態如何？",
            token="t10-live",
        )
        self.run_id = self.result.run_id
        self.runs_root = self.data_root / "runs"

    def fingerprint(self):
        return {
            str(path.relative_to(self.runs_root)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.runs_root.rglob("*"))
            if path.is_file()
        }

    def verified(self):
        from hoya_market_agents.run_verifier import verify_run

        return verify_run(self.data_root, self.run_id)["status"]

    def test_the_fixture_really_is_a_run_that_verifies(self):
        """Otherwise every assertion below would hold for a broken run too."""
        self.assertEqual("VERIFIED", self.verified())

    def test_a_stream_that_raises_leaves_the_run_verifiable(self):
        before = self.fingerprint()

        with mock.patch.object(live, "open_room", side_effect=RuntimeError("直播壞了")):
            response = self.get("/live/events?run={}".format(self.run_id))

        self.assertEqual(200, response.status)
        self.assertEqual(before, self.fingerprint())
        self.assertEqual("VERIFIED", self.verified())

    def test_a_stream_that_raises_is_recorded_rather_than_dying_silently(self):
        with mock.patch.object(live, "open_room", side_effect=RuntimeError("直播壞了")):
            self.get("/live/events?run={}".format(self.run_id))

        failures = [r for r in self.records() if r["event"] == "stream_failed"]
        self.assertEqual(1, len(failures))
        self.assertEqual("ERROR", failures[0]["level"])
        self.assertIn("RuntimeError", failures[0]["message"])

    def test_a_reader_who_closes_the_tab_leaves_the_run_verifiable(self):
        before = self.fingerprint()

        self.get("/live/events?run={}".format(self.run_id), break_after=1)

        self.assertEqual(before, self.fingerprint())
        self.assertEqual("VERIFIED", self.verified())

    def test_a_reader_who_closes_the_tab_is_not_an_error_to_report(self):
        self.get("/live/events?run={}".format(self.run_id), break_after=1)

        self.assertEqual(
            [], [r for r in self.records() if r["event"] == "stream_failed"]
        )

    def test_a_broken_stream_leaves_no_thread_behind(self):
        before = set(threading.enumerate())

        self.get("/live/events?run={}".format(self.run_id), break_after=1)

        self.assertEqual(before, set(threading.enumerate()))

    def test_the_room_page_failing_leaves_the_run_verifiable(self):
        before = self.fingerprint()

        with mock.patch.object(live, "live_snapshot", side_effect=RuntimeError("壞了")):
            response = self.get("/live")

        self.assertEqual(500, response.status)
        self.assertNotIn("Traceback", response.body)
        self.assertEqual(before, self.fingerprint())
        self.assertEqual("VERIFIED", self.verified())


ASK_TARGET = "BTC"


def ask_bar_submission(question, asset_class=ASSET_CLASS_CRYPTO, target=ASK_TARGET):
    """One ask-bar submission's fields, spelled the way the form spells them.

    Ticket 05 made the market and the target fields of their own, so a ``POST``
    carrying only a question is no longer an old-style submission — it is an
    incomplete one, and what happens to those is pinned in
    ``tests/test_webapp_asset_picker.py``. The names come from the module that
    owns them so a rename cannot leave these tests submitting to nothing.
    """
    return {
        launch_module.QUESTION_FIELD: question,
        launch_module.ASSET_CLASS_FIELD: asset_class,
        launch_module.target_field(asset_class): target,
    }


class LaunchFormTest(PageFixture, unittest.TestCase):
    """The one request that starts something, and everything that stops it."""

    QUESTION = "BTC 未來七天會不會漲"

    def submit(self, question=QUESTION):
        return self.post("/launch", ask_bar_submission(question))

    def test_a_launch_with_a_certificate_starts_one_process(self):
        self.write_certificate()

        response = self.submit()

        self.assertEqual(303, response.status)
        self.assertEqual(1, len(self.spawned))

    def test_the_started_process_carries_this_whole_submission(self):
        """The route passes the menu's answer on; it drops none of the four.

        Ticket 05 made the run's subject something the submission *states* rather
        than something the wording is read for, so what this checks is that all
        four stated values leave this process. How the command spells them is
        ``webapp.launch``'s business and is asserted nowhere; that they arrive at
        the launcher is asserted end to end in
        ``tests/test_webapp_asset_picker.py``. The process boundary is unchanged —
        the child still owns the run directory.
        """
        self.write_certificate()

        self.submit()

        args, _ = self.spawned[0]
        self.assertIn(sys.executable, args)
        for stated in (self.QUESTION, ASSET_CLASS_CRYPTO, ASK_TARGET, str(self.data_root)):
            self.assertTrue(
                any(stated in argument for argument in args), "{}: {}".format(stated, args)
            )

    def test_a_started_launch_sends_the_reader_to_the_live_room(self):
        self.write_certificate()

        self.assertEqual("/live", self.submit().headers["Location"])

    def test_a_started_launch_is_recorded(self):
        self.write_certificate()

        self.submit()

        self.assertIn("launch_started", [r["event"] for r in self.records()])

    def test_a_second_launch_while_the_first_runs_is_refused(self):
        self.write_certificate()
        self.submit()

        response = self.submit("另一個題目")

        self.assertEqual(409, response.status)
        self.assertEqual(1, len(self.spawned))
        self.assertIn("同一時間只允許一個", response.body)

    def test_a_launch_after_the_first_one_finished_is_allowed(self):
        """The other direction: a lock that never releases is also broken."""
        self.write_certificate()
        self.submit()
        self.processes[0].finish(0)

        response = self.submit("另一個題目")

        self.assertEqual(303, response.status)
        self.assertEqual(2, len(self.spawned))

    def test_a_launch_after_the_first_one_crashed_is_allowed_too(self):
        self.write_certificate()
        self.submit()
        self.processes[0].finish(1)

        self.assertEqual(303, self.submit("另一個題目").status)

    def test_the_room_says_a_run_of_its_own_is_in_progress(self):
        self.write_certificate()
        self.submit()

        body = self.get("/live").body

        self.assertIn("還在進行", body)
        self.assertIn("disabled", body)

    def test_the_room_offers_the_form_again_once_that_run_has_ended(self):
        self.write_certificate()
        self.submit()
        self.processes[0].finish(0)

        body = self.get("/live").body

        self.assertIn("結束碼 0", body)
        self.assertNotIn("<button class=\"primary\" type=\"submit\" disabled>", body)

    def test_a_missing_certificate_is_guidance_rather_than_a_traceback(self):
        response = self.submit()

        self.assertEqual(200, response.status)
        self.assertNotIn("Traceback", response.body)
        self.assertIn("latest-ready.json", response.body)
        self.assertEqual([], self.spawned)

    def test_a_missing_certificate_says_the_line_that_produces_one(self):
        body = self.submit().body

        self.assertIn("preflight --provider system --seats 7 --mode real", body)
        self.assertIn(str(self.data_root), body)

    def test_a_certificate_that_is_not_ready_is_refused_with_its_own_reason(self):
        self.write_certificate()
        path = self.data_root / "preflight" / "latest-ready.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["provider_capabilities_ready"] = False
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        response = self.submit()

        self.assertEqual(200, response.status)
        self.assertIn("provider_capabilities_ready", response.body)
        self.assertEqual([], self.spawned)

    def test_a_certificate_whose_manifest_was_edited_is_refused(self):
        manifest_path = self.write_certificate()
        self.data_root.joinpath("preflight").exists()
        manifest_path_file = (
            self.data_root / "preflight" / "20260806T005926Z-aaa111" / "manifest.json"
        )
        manifest_path_file.write_text("{}", encoding="utf-8")

        response = self.submit()

        self.assertEqual(200, response.status)
        self.assertIn("fail closed", response.body)
        self.assertEqual([], self.spawned)

    def test_a_blank_question_is_refused_before_anything_is_started(self):
        self.write_certificate()

        response = self.submit("   ")

        self.assertEqual(200, response.status)
        self.assertIn("請先輸入要分析的題目", response.body)
        self.assertEqual([], self.spawned)

    def test_a_refused_launch_is_recorded(self):
        self.submit()

        refusals = [r for r in self.records() if r["event"] == "launch_refused"]
        self.assertEqual(1, len(refusals))
        self.assertEqual("WARNING", refusals[0]["level"])

    def test_a_post_to_a_path_that_takes_no_form_is_a_404(self):
        self.assertEqual(404, self.post("/", {"question": "x"}).status)

    def test_a_body_larger_than_a_question_is_not_read_into_memory(self):
        self.write_certificate()
        body = urlencode({"question": "很長" * 20000})
        raw = (
            "POST /launch HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: {}\r\n\r\n{}".format(len(body.encode("utf-8")), body)
        )

        response = self.request(raw)

        self.assertEqual(200, response.status)
        self.assertIn("請先輸入要分析的題目", response.body)
        self.assertEqual([], self.spawned)


class LaunchLockBoundaryTest(unittest.TestCase):
    """What the front-end lock does and does not claim to prevent."""

    def test_a_fresh_lock_is_not_busy(self):
        self.assertFalse(launch_module.LaunchLock().busy())

    def test_a_claimed_lock_is_busy_while_its_process_runs(self):
        lock = launch_module.LaunchLock()
        lock.claim(lambda: FakeProcess())

        self.assertTrue(lock.busy())

    def test_a_second_claim_starts_nothing(self):
        lock = launch_module.LaunchLock()
        lock.claim(lambda: FakeProcess())
        started = []

        result = lock.claim(lambda: started.append(1) or FakeProcess())

        self.assertIsNone(result)
        self.assertEqual([], started)

    def test_the_lock_releases_when_the_process_it_watched_ended(self):
        lock = launch_module.LaunchLock()
        process = lock.claim(lambda: FakeProcess())
        process.finish(0)

        self.assertFalse(lock.busy())
        self.assertIsNotNone(lock.claim(lambda: FakeProcess()))

    def test_a_run_this_lock_never_started_is_not_visible_to_it(self):
        """The documented limit, asserted: it locks this process, not the machine."""
        first = launch_module.LaunchLock()
        second = launch_module.LaunchLock()
        first.claim(lambda: FakeProcess())

        self.assertTrue(first.busy())
        self.assertFalse(second.busy())

    def test_the_state_reports_nothing_started_before_anything_was(self):
        state = launch_module.LaunchLock().state()

        self.assertFalse(state["started"])
        self.assertFalse(state["running"])
        self.assertIsNone(state["returncode"])

    def test_the_state_carries_the_exit_code_once_the_launch_has_ended(self):
        lock = launch_module.LaunchLock()
        process = lock.claim(lambda: FakeProcess())
        lock.note_question("BTC 會不會漲")
        process.finish(2)

        state = lock.state()

        self.assertTrue(state["started"])
        self.assertFalse(state["running"])
        self.assertEqual(2, state["returncode"])
        self.assertEqual("BTC 會不會漲", state["question"])


class LaunchReadinessTest(unittest.TestCase):
    """The readiness sentence is the launcher's, asked of the launcher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def submission(self, question="BTC 會漲嗎"):
        """A complete ask-bar submission, so readiness is what is being measured."""
        return launch_module.LaunchRequest(question, ASSET_CLASS_CRYPTO, (ASK_TARGET,))

    def test_a_blank_question_is_the_first_thing_refused(self):
        problem, sentence = launch_module.launch_problem(
            self.data_root, self.submission("  ")
        )

        self.assertEqual(launch_module.PROBLEM_BLANK, problem)
        self.assertIn("題目", sentence)

    def test_a_missing_certificate_is_reported_in_the_launchers_own_words(self):
        from hoya_market_agents.launcher import ready_certificate_problem

        problem, sentence = launch_module.launch_problem(
            self.data_root, self.submission()
        )

        self.assertEqual(launch_module.PROBLEM_NOT_READY, problem)
        self.assertEqual(ready_certificate_problem(self.data_root), sentence)

    def test_a_ready_data_root_refuses_nothing(self):
        """FP direction: a check that always refuses would pass the two above."""
        preflight_id = "20260806T005926Z-bbb222"
        manifest = {
            "schema_version": "1.0.0",
            "status": "READY",
            "provider_capabilities_ready": True,
            "generated_at_utc": "2026-08-06T00:59:26Z",
        }
        path = self.data_root / "preflight" / preflight_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        write_ready_certificate(self.data_root, preflight_id, manifest, path)

        self.assertEqual(
            (None, None),
            launch_module.launch_problem(self.data_root, self.submission()),
        )

    def test_the_repair_line_names_this_data_root(self):
        self.assertIn(
            str(self.data_root), launch_module.preflight_command(self.data_root)
        )


class LivePolicyTest(PageFixture, unittest.TestCase):
    """Two policies. The room's is the smaller change, not a blanket loosening."""

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def test_the_pages_with_no_script_still_forbid_every_script(self):
        for path in ("/history", "/run/20260801T020000Z-btc-aaaa11", "/nope"):
            policy = self.get(path).headers["Content-Security-Policy"]
            self.assertEqual(CONTENT_SECURITY_POLICY, policy, path)
            self.assertIn("script-src 'none'", policy, path)

    def test_the_room_and_its_script_carry_the_rooms_own_policy(self):
        for path in ("/live", "/live.js"):
            self.assertEqual(
                LIVE_CONTENT_SECURITY_POLICY,
                self.get(path).headers["Content-Security-Policy"],
                path,
            )

    def test_the_room_allows_a_same_origin_script_and_not_an_inline_one(self):
        self.assertIn("script-src 'self'", LIVE_CONTENT_SECURITY_POLICY)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", LIVE_CONTENT_SECURITY_POLICY)
        self.assertNotIn("unsafe-eval", LIVE_CONTENT_SECURITY_POLICY)

    def test_the_room_opens_a_connection_back_to_this_origin_only(self):
        self.assertIn("connect-src 'self'", LIVE_CONTENT_SECURITY_POLICY)

    def test_the_room_loosens_exactly_two_directives_and_no_others(self):
        """Anything else that differs between the two is a silent widening."""
        strict = dict(_directives(CONTENT_SECURITY_POLICY))
        room = dict(_directives(LIVE_CONTENT_SECURITY_POLICY))
        differing = {
            name
            for name in set(strict) | set(room)
            if strict.get(name) != room.get(name)
        }

        self.assertEqual({"script-src", "connect-src", "frame-src"}, differing)
        self.assertEqual("'none'", strict["script-src"])
        self.assertEqual("'self'", room["script-src"])
        self.assertNotIn("frame-src", room)

    def test_every_response_still_refuses_to_be_sniffed(self):
        for path in ("/live", "/live.js", "/live/events"):
            self.assertEqual(
                "nosniff", self.get(path).headers["X-Content-Type-Options"], path
            )


def _directives(policy):
    for part in policy.split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            yield name, value


class LiveContrastTest(unittest.TestCase):
    """The stance colours are asserted, not measured once and forgotten."""

    STANCE_TOKENS = ("affirm", "oppose", "abstain")

    def test_every_stance_colour_is_declared_in_the_palette(self):
        for token in self.STANCE_TOKENS:
            self.assertIn(token, pages.PALETTE)

    def test_every_stance_colour_is_required_to_meet_the_text_minimum(self):
        required = {
            (foreground, background)
            for foreground, background, minimum in pages.CONTRAST_REQUIREMENTS
            if minimum >= 4.5
        }
        for token in self.STANCE_TOKENS:
            for background in ("page", "surface"):
                self.assertIn((token, background), required)

    def test_every_class_the_room_hands_out_has_a_colour_of_its_own(self):
        declared = set(live.STANCE_CLASSES) | {live.UNKNOWN_STANCE_CLASS}

        self.assertEqual(declared, set(pages.STANCE_COLOUR_TOKENS))

    def test_each_of_those_classes_names_a_token_the_palette_declares(self):
        for name, token in pages.STANCE_COLOUR_TOKENS.items():
            self.assertIn(token, pages.PALETTE, name)

    def test_the_stylesheet_paints_the_classes_it_is_allowed_to_paint(self):
        """This test used to assert the sheet painted all four, which was never
        what the room rendered: the sheet the room shipped painted
        ``.stance-positive``/``-negative``/``-neutral``, three names ``live.py``
        has never emitted, so the three ballot positions were the body colour.
        Spec R2 freezes that, so the sheet paints the one class that really was
        painted — see :data:`pages.PAINTED_STANCE_CLASSES`."""
        sheet = pages.stylesheet()

        for name in pages.PAINTED_STANCE_CLASSES:
            token = pages.STANCE_COLOUR_TOKENS[name]
            self.assertIn(".{}{{color:var(--{});}}".format(name, token), sheet)

    def test_the_three_frozen_positions_are_painted_by_no_rule_at_all(self):
        """Spec R2: the room's 正／反／無法判斷 text keeps the colour it had, which
        was the body colour. A rule for any of these three would change it.

        The three are named from ``live.STANCE_CLASSES`` — the ballot positions
        themselves — and deliberately **not** derived from
        ``PAINTED_STANCE_CLASSES``: a guard that reads the tuple it is guarding
        goes quiet the moment that tuple grows, which is the one failure mode
        that matters here."""
        sheet = pages.stylesheet()

        self.assertEqual(3, len(live.STANCE_CLASSES))
        for name in live.STANCE_CLASSES:
            self.assertNotIn(".{}{{".format(name), sheet, name)


class LiveReadOnlyTest(PageFixture, unittest.TestCase):
    """The live routes are readers, proved the same way the others were.

    ``POST /launch`` is in here too, and deliberately: the claim is not that a
    launch writes nothing, but that *this process* writes nothing under
    ``runs/``. The child it starts owns every byte of the run directory, and the
    spawn seam here records the command instead of running it — so what is being
    measured is exactly the web app's own footprint.
    """

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)
        append_events(
            self.run_dir,
            [
                seat_message("spot-technical", "bullish", 240000),
                seat_message("news", "bearish", 250000),
            ],
        )
        write_run(self.data_root, "20260801T020000Z-btc-aaaa11", "已完成的題目")
        rebuild_index(self.data_root)
        self.write_certificate()
        self.runs_root = self.data_root / "runs"
        self.addCleanup(self._restore)
        for path in sorted(self.runs_root.rglob("*"), reverse=True):
            path.chmod(stat.S_IRUSR | stat.S_IXUSR if path.is_dir() else stat.S_IRUSR)
        self.runs_root.chmod(stat.S_IRUSR | stat.S_IXUSR)

    def _restore(self):
        self.runs_root.chmod(stat.S_IRWXU)
        for path in self.runs_root.rglob("*"):
            path.chmod(stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR)

    def fingerprint(self):
        return {
            str(path.relative_to(self.runs_root)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.runs_root.rglob("*"))
            if path.is_file()
        }

    def test_every_live_route_still_works_with_the_run_tree_read_only(self):
        for path in ("/live", "/live.js", "/live/events", "/live/events?run=" + LIVE_RUN_ID):
            self.assertEqual(200, self.get(path).status, path)

    def test_a_launch_can_still_be_started_with_the_run_tree_read_only(self):
        self.assertEqual(
            303, self.post("/launch", ask_bar_submission("BTC 會漲嗎")).status
        )

    def test_nothing_under_runs_changes_while_the_live_routes_are_served(self):
        before = self.fingerprint()

        for path in ("/live", "/live.js", "/live/events", "/live/events?run=" + LIVE_RUN_ID):
            self.get(path)
        self.post("/launch", ask_bar_submission("BTC 會漲嗎"))
        self.post("/launch", ask_bar_submission("第二次會被鎖擋下"))

        self.assertEqual(before, self.fingerprint())


class RenderedRoomTest(PageFixture, unittest.TestCase):
    """The substitute for a screenshot: keep the rendered page and assert on it.

    No browser exists in this environment — no chrome, chromium, firefox,
    wkhtmltoimage, selenium or playwright — so there is no screenshot to take.
    What can be kept is what a browser would have been given, so the room is
    rendered to a file and the elements that make it a chat room are asserted
    on that file rather than on a string that vanishes with the test.
    """

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)
        append_events(
            self.run_dir,
            [
                seat_message(seat_id, stance, 240000 + index * 1000, round_number=1)
                for index, (seat_id, stance) in enumerate(
                    [
                        ("spot-technical", "bullish"),
                        ("derivatives", "bullish"),
                        ("onchain", "bullish"),
                        ("official-events", "bullish"),
                        ("news", "bearish"),
                        ("social-macro", "neutral"),
                        ("counter-evidence", "bearish"),
                    ]
                )
            ]
            + [
                seat_message(
                    "news", "bullish", 400000, kind="final_vote", round_number=2,
                    change_reason="被反方證據說服",
                )
            ],
        )
        self.rendered = self.data_root / "live-room.html"
        self.rendered.write_text(self.get("/live").body, encoding="utf-8")

    def test_the_kept_page_is_a_whole_document(self):
        text = self.rendered.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertIn("</html>", text)

    def test_the_kept_page_holds_one_message_per_public_message(self):
        self.assertEqual(
            8, self.rendered.read_text(encoding="utf-8").count('class="message ')
        )

    def test_the_kept_page_holds_the_seven_agents_and_the_tally(self):
        text = self.rendered.read_text(encoding="utf-8")

        self.assertEqual(7, text.count('class="agent '))
        cells = re.findall(r'<div class="stance-\w+"><span class="tally-label"', text)
        self.assertEqual(3, len(cells))

    def test_the_kept_page_names_the_change_between_the_rounds(self):
        text = self.rendered.read_text(encoding="utf-8")

        self.assertIn("是否變更立場：", text)
        self.assertIn("偏空 → 偏多", text)


class RetiredDashboardTest(unittest.TestCase):
    """The old live page is gone, and nothing still reaches for it."""

    def test_the_module_no_longer_exists(self):
        with self.assertRaises(ImportError):
            __import__("hoya_market_agents.live_dashboard")

    def test_no_module_still_imports_it(self):
        root = Path(pages.__file__).resolve().parent.parent
        importing = re.compile(r"^\s*(from\s+\.?\S*live_dashboard|import\s+\S*live_dashboard)")
        offenders = [
            path.name
            for path in root.rglob("*.py")
            if any(
                importing.match(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        ]

        self.assertEqual([], offenders)

    def test_the_cli_no_longer_offers_the_live_command(self):
        from hoya_market_agents.cli import build_parser

        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["live", "--data-root", "/tmp"])

    def test_the_cli_still_offers_the_webapp_command_that_replaced_it(self):
        """FP direction: a parser that refuses everything would pass the test above."""
        from hoya_market_agents.cli import build_parser

        arguments = build_parser().parse_args(["webapp", "--data-root", "/tmp"])

        self.assertEqual("webapp", arguments.command)

    def test_the_handshake_points_at_the_page_that_replaced_it(self):
        from hoya_market_agents.launcher import LIVE_URL

        self.assertTrue(LIVE_URL.endswith("/live"))


# -- Ticket 11: the settings page ---------------------------------------------


def leaf_paths_of(value, path=""):
    """A second, independent walk of the document, written for the test alone.

    :mod:`~hoya_market_agents.webapp.settings` derives its controls from the
    document; this walk is the oracle it is compared against, so a hard-coded
    field list in either one shows up as a difference rather than as agreement.
    """
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            if key.startswith("_"):
                continue
            found += leaf_paths_of(item, "{}.{}".format(path, key) if path else key)
        return found
    if isinstance(value, list) and value and isinstance(value[0], dict):
        found = []
        for index, item in enumerate(value):
            found += leaf_paths_of(item, "{}[{}]".format(path, index))
        return found
    return [path]


class SettingsFixture:
    """A private copy of the shipped rule file, and the shipped rules restored.

    Every test here writes to its own copy — the repository's
    ``config/debate_rules.json`` is read and never written. Publishing is global,
    so the shipped rules are republished on the way in (a known baseline) and on
    the way out; the cleanup is registered before anything can publish, so it
    runs even when a test fails.
    """

    def setUp(self):
        super().setUp()
        self._rules_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._rules_tmp.cleanup)
        self.addCleanup(reload_debate_rules)
        self.rules_dir = Path(self._rules_tmp.name)
        self.rules_path = self.rules_dir / "debate_rules.json"
        self.rules_path.write_text(
            RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.shipped = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        reload_debate_rules()

    def document(self):
        return json.loads(self.rules_path.read_text(encoding="utf-8"))

    def texts(self):
        """The whole form as it comes back when nothing was edited."""
        return {path: text for path, text, _ in settings.field_texts(self.document())}

    def edited(self, **changes):
        """The whole form with some paths replaced by what a user typed.

        ``__`` in a keyword stands for the ``.`` a path separates with, which is
        the only reason this helper exists.
        """
        submitted = self.texts()
        for path, text in changes.items():
            submitted[path.replace("__", ".")] = text
        return submitted

    def bytes_now(self):
        return self.rules_path.read_bytes()


class SettingsFieldDerivationTest(SettingsFixture, unittest.TestCase):
    """The form is derived from the document, never from a list written here."""

    def test_a_field_exists_for_every_leaf_the_document_holds(self):
        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertEqual(leaf_paths_of(self.document()), paths)

    def test_the_shipped_document_really_has_leaves_worth_walking(self):
        """FP direction: an empty walk would make the test above vacuous."""
        self.assertGreater(len(settings.field_texts(self.document())), 20)

    def test_a_field_added_to_the_document_appears_without_editing_this_module(self):
        """The whole point: a new rule field must not be silently dropped."""
        document = self.document()
        document["timeline"]["a_brand_new_wall"] = 123

        paths = [path for path, _, _ in settings.field_texts(document)]

        self.assertIn("timeline.a_brand_new_wall", paths)

    def test_a_field_removed_from_the_document_leaves_no_control_behind(self):
        document = self.document()
        del document["timeline"]["final_settle_offset_ms"]

        paths = [path for path, _, _ in settings.field_texts(document)]

        self.assertNotIn("timeline.final_settle_offset_ms", paths)

    def test_a_comment_key_is_not_a_field(self):
        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertEqual([], [path for path in paths if "_about" in path])

    def test_a_comment_key_survives_a_round_trip_untouched(self):
        rebuilt = settings.candidate_document(self.document(), self.texts())

        self.assertEqual(self.shipped["_about"], rebuilt["_about"])
        self.assertEqual(
            self.shipped["timeline"]["_about"], rebuilt["timeline"]["_about"]
        )

    def test_submitting_the_form_unchanged_rebuilds_the_same_document(self):
        self.assertEqual(
            self.document(), settings.candidate_document(self.document(), self.texts())
        )

    def test_a_list_of_objects_is_walked_one_object_at_a_time(self):
        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertIn("confidence.light_scale[0].min_votes", paths)
        self.assertIn("confidence.light_scale[4].level", paths)

    def test_a_list_of_scalars_is_one_control_and_not_one_per_item(self):
        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertIn(
            "confidence.downgrades.low_trust_source.trusted_source_tiers", paths
        )
        self.assertNotIn(
            "confidence.downgrades.low_trust_source.trusted_source_tiers[0]", paths
        )

    def test_a_path_the_document_does_not_have_is_ignored_rather_than_added(self):
        """FP direction: a submitted name the document never had changes nothing."""
        submitted = self.texts()
        submitted["timeline.made_up"] = "1"

        self.assertEqual(
            self.document(), settings.candidate_document(self.document(), submitted)
        )

    def test_a_field_left_out_of_the_submission_keeps_the_value_it_had(self):
        submitted = self.texts()
        del submitted["timeline.final_settle_offset_ms"]

        rebuilt = settings.candidate_document(self.document(), submitted)

        self.assertEqual(360_000, rebuilt["timeline"]["final_settle_offset_ms"])


class SettingsEditsValuesOnlyTest(SettingsFixture, unittest.TestCase):
    """The scope the module claims: values change, the shape does not.

    Both halves are asserted, because "this page cannot add a field" is a
    limitation a reader has to be able to rely on as much as a feature.
    """

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_root = Path(self._data_tmp.name)

    def save(self, submitted):
        return settings.save_rules(self.rules_path, submitted, self.data_root)

    def test_a_saved_document_has_the_same_keys_it_had(self):
        self.save(self.edited(timeline__final_settle_offset_ms="420000"))

        self.assertEqual(
            leaf_paths_of(self.shipped), leaf_paths_of(self.document())
        )

    def test_a_saved_document_keeps_the_same_number_of_light_rungs(self):
        self.save(self.edited(**{"confidence__light_scale[0]__min_votes": "7"}))

        self.assertEqual(5, len(self.document()["confidence"]["light_scale"]))

    def test_a_downgrade_rule_the_file_leaves_out_gets_no_control(self):
        """The loader treats it as optional, so an absent one is not shown.

        Stated as a limit rather than discovered as a surprise: this page cannot
        put back a rule the file does not have.
        """
        document = self.document()
        del document["confidence"]["downgrades"]["low_trust_source"]
        self.rules_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertEqual([], [p for p in paths if "low_trust_source" in p])

    def test_that_file_is_one_the_loader_still_accepts(self):
        """Which is why the omission is a limitation and not a broken state."""
        document = self.document()
        del document["confidence"]["downgrades"]["low_trust_source"]
        self.rules_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

        rules = load_debate_rules(self.rules_path)

        self.assertEqual(
            ("few_independent_domains",),
            tuple(rule.rule for rule in rules.confidence.downgrades),
        )

    def test_the_rule_that_is_there_still_gets_its_controls(self):
        """FP direction: dropping one must not drop the other."""
        document = self.document()
        del document["confidence"]["downgrades"]["low_trust_source"]
        self.rules_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

        paths = [path for path, _, _ in settings.field_texts(self.document())]

        self.assertIn(
            "confidence.downgrades.few_independent_domains.levels", paths
        )


class SettingsTranslationTest(unittest.TestCase):
    """Text in, JSON out. This step decides nothing about what is legal."""

    def test_an_integer_field_reads_a_number_as_a_number(self):
        self.assertEqual(480_000, settings.value_from_text("480000", 600_000))

    def test_surrounding_spaces_are_not_part_of_a_number(self):
        self.assertEqual(5, settings.value_from_text("  5  ", 6))

    def test_text_that_is_not_a_number_is_handed_on_exactly_as_typed(self):
        """The refusal is the loader's to make, so its argument reaches it."""
        self.assertEqual("abc", settings.value_from_text("abc", 600_000))

    def test_a_decimal_is_handed_on_rather_than_rounded_into_an_integer(self):
        self.assertEqual("6.0", settings.value_from_text("6.0", 6))

    def test_a_blank_integer_field_is_handed_on_as_the_blank_it_was(self):
        self.assertEqual("", settings.value_from_text("", 6))

    def test_a_negative_number_reaches_the_loader_as_a_number(self):
        self.assertEqual(-1, settings.value_from_text("-1", 6))

    def test_a_text_field_stays_text(self):
        self.assertEqual("teal", settings.value_from_text("teal", "blue"))

    def test_a_list_of_numbers_is_split_and_read_as_numbers(self):
        self.assertEqual([1, 2, 3], settings.value_from_text("1, 2, 3", [1, 2]))

    def test_a_list_of_text_is_split_and_stays_text(self):
        self.assertEqual(
            ["news", "social-macro"],
            settings.value_from_text("news, social-macro", ["social-macro"]),
        )

    def test_an_emptied_list_field_is_the_empty_list(self):
        self.assertEqual([], settings.value_from_text("   ", ["social-macro"]))

    def test_an_item_of_a_list_that_is_not_a_number_is_handed_on_as_typed(self):
        self.assertEqual([1, "x"], settings.value_from_text("1, x", [1, 2]))


class SettingsFieldNamingTest(SettingsFixture, unittest.TestCase):
    """Which control a refusal belongs to is read out of the loader's sentence."""

    def paths(self):
        return settings.known_paths(self.document())

    def test_a_sentence_naming_one_field_names_that_field(self):
        named = settings.fields_named_in(
            "timeline.vote_rounds[0].threshold 必須是 1 到 7 之間的整數票數，收到 0。",
            self.paths(),
        )

        self.assertEqual(("timeline.vote_rounds[0].threshold",), named)

    def test_a_sentence_naming_two_fields_names_both_of_them(self):
        """An inverted pair implicates both ends, so both are marked."""
        named = settings.fields_named_in(
            "timeline.vote_rounds[1].open_offset_ms（1）必須大於 "
            "timeline.vote_rounds[0].open_offset_ms（2）；開票 offset 必須嚴格遞增。",
            self.paths(),
        )

        self.assertEqual(
            {
                "timeline.vote_rounds[0].open_offset_ms",
                "timeline.vote_rounds[1].open_offset_ms",
            },
            set(named),
        )

    def test_the_container_is_dropped_when_a_control_inside_it_is_named(self):
        named = settings.fields_named_in(
            "confidence.light_scale[0].min_votes 必須是 0 到 7 之間的整數，收到 9。",
            self.paths(),
        )

        self.assertEqual(("confidence.light_scale[0].min_votes",), named)

    def test_a_sentence_about_a_whole_section_names_that_section(self):
        named = settings.fields_named_in(
            "confidence.light_scale 最後一級的 min_votes 必須是 0；", self.paths()
        )

        self.assertEqual(("confidence.light_scale",), named)

    def test_a_sentence_naming_nothing_this_form_holds_names_nothing(self):
        """FP direction: an unattributable sentence is not pinned on a field."""
        self.assertEqual(
            (), settings.fields_named_in("設定檔的最外層必須是 object。", self.paths())
        )


class SettingsSaveTest(SettingsFixture, unittest.TestCase):
    """Writing: what is refused, what is written, and in whose words."""

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_root = Path(self._data_tmp.name)

    def save(self, submitted, **options):
        return settings.save_rules(
            self.rules_path, submitted, self.data_root, **options
        )

    def loader_sentence(self, document):
        """What ``load_debate_rules`` itself says about this document.

        The probe is written beside the real file and its name is mapped onto
        the real one, because a path in a message is the only part of the
        loader's sentence that depends on where the file happens to be.
        """
        probe = self.rules_dir / "probe.json"
        probe.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        try:
            with self.assertRaises(DebateRulesError) as caught:
                load_debate_rules(probe)
            return str(caught.exception).replace(str(probe), str(self.rules_path))
        finally:
            probe.unlink()

    def test_a_legal_change_is_written_to_the_file(self):
        outcome = self.save(self.edited(timeline__final_settle_offset_ms="420000"))

        self.assertEqual(settings.SAVED, outcome.state)
        self.assertEqual(420_000, self.document()["timeline"]["final_settle_offset_ms"])

    def test_a_legal_change_becomes_the_rules_the_next_run_reads(self):
        self.save(
            self.edited(
                **{
                    "timeline__vote_rounds[2]__threshold": "4",
                    "timeline__vote_rounds[3]__threshold": "3",
                }
            )
        )

        self.assertEqual(4, debate_rules().vote_rounds[2].threshold)

    def test_a_saved_file_is_still_json_a_person_can_read(self):
        self.save(self.edited(timeline__final_settle_offset_ms="420000"))

        self.assertTrue(self.rules_path.read_text(encoding="utf-8").endswith("}\n"))

    def test_a_timeline_in_the_wrong_order_is_refused(self):
        outcome = self.save(
            self.edited(**{"timeline__vote_rounds[1]__open_offset_ms": "1000"})
        )

        self.assertEqual(settings.REFUSED, outcome.state)

    def test_a_refused_timeline_names_the_field_it_is_about(self):
        outcome = self.save(
            self.edited(**{"timeline__vote_rounds[1]__open_offset_ms": "1000"})
        )

        self.assertIn("timeline.vote_rounds[1].open_offset_ms", outcome.fields)

    def test_a_refused_change_leaves_the_file_byte_for_byte_as_it_was(self):
        before = self.bytes_now()

        self.save(
            self.edited(**{"timeline__vote_rounds[1]__open_offset_ms": "1000"})
        )

        self.assertEqual(before, self.bytes_now())

    def test_a_vote_count_of_zero_is_refused_and_named(self):
        outcome = self.save(
            self.edited(**{"timeline__vote_rounds[0]__threshold": "0"})
        )

        self.assertEqual(settings.REFUSED, outcome.state)
        self.assertIn("timeline.vote_rounds[0].threshold", outcome.fields)

    def test_a_refused_change_leaves_no_temporary_file_in_the_directory(self):
        self.save(self.edited(**{"timeline__vote_rounds[0]__threshold": "0"}))

        self.assertEqual(
            ["debate_rules.json"], sorted(p.name for p in self.rules_dir.iterdir())
        )

    def test_a_refused_change_does_not_become_the_published_rules(self):
        before = debate_rules()

        self.save(self.edited(**{"timeline__vote_rounds[0]__threshold": "0"}))

        self.assertIs(before, debate_rules())

    # Every refusal in ``debate_rules.load_debate_rules`` that editing a *value*
    # on this page can reach. It is derived rather than collected: each ``raise
    # DebateRulesError`` in that loader was walked, and where its condition is a
    # disjunction — "not an integer **or** out of range" — each disjunct gets
    # its own case, because they are different mistakes with different numbers
    # in the sentence.
    #
    # The refusals a value edit **cannot** reach are left out on purpose, and
    # they are exactly the ones about a document's *shape*: a file that is not
    # JSON or not an object, a missing section or field, an unknown key, a
    # ``light_scale`` that is not an array. This page adds no keys, removes
    # none, and never changes a container into something else
    # (``settings._walk`` rebuilds the document it was given), so no submission
    # can produce one of those. Listing them would be listing cases that pass
    # for a reason unrelated to the submission.
    #
    # Each entry is ``(the loader rule it reaches, control path, typed text)``.
    ILLEGAL = (
        ("schema_version is not an integer", "schema_version", "abc"),
        ("schema_version is an unsupported integer", "schema_version", "1"),
        (
            "round offset is not an integer",
            "timeline.vote_rounds[0].open_offset_ms",
            "abc",
        ),
        (
            "round offset is negative",
            "timeline.vote_rounds[0].open_offset_ms",
            "-1",
        ),
        (
            "round offsets are not strictly increasing",
            "timeline.vote_rounds[1].open_offset_ms",
            "1000",
        ),
        (
            "round threshold is not an integer",
            "timeline.vote_rounds[0].threshold",
            "abc",
        ),
        (
            "round threshold is below one",
            "timeline.vote_rounds[0].threshold",
            "0",
        ),
        (
            "round threshold is above the seat count",
            "timeline.vote_rounds[0].threshold",
            "8",
        ),
        (
            "round thresholds are not strictly decreasing",
            "timeline.vote_rounds[1].threshold",
            "7",
        ),
        (
            "final settle offset is not an integer",
            "timeline.final_settle_offset_ms",
            "abc",
        ),
        (
            "final settle is not after the last round",
            "timeline.final_settle_offset_ms",
            "1000",
        ),
        (
            "light rung min_votes is not an integer",
            "confidence.light_scale[0].min_votes",
            "abc",
        ),
        (
            "light rung min_votes is negative",
            "confidence.light_scale[0].min_votes",
            "-1",
        ),
        (
            "light rung min_votes is above the seat count",
            "confidence.light_scale[0].min_votes",
            "8",
        ),
        (
            "light ladder is not strictly decreasing",
            "confidence.light_scale[1].min_votes",
            "7",
        ),
        ("light level is blank", "confidence.light_scale[2].level", ""),
        ("light level is a repeat", "confidence.light_scale[1].level", "blue"),
        ("last light rung is not zero", "confidence.light_scale[4].min_votes", "3"),
        (
            "downgrade levels is not an integer",
            "confidence.downgrades.few_independent_domains.levels",
            "abc",
        ),
        (
            "downgrade levels is below one",
            "confidence.downgrades.few_independent_domains.levels",
            "0",
        ),
        (
            "downgrade levels is above the seat count",
            "confidence.downgrades.few_independent_domains.levels",
            "8",
        ),
        (
            "min_independent_domains is not an integer",
            "confidence.downgrades.few_independent_domains.min_independent_domains",
            "abc",
        ),
        (
            "min_independent_domains is below one",
            "confidence.downgrades.few_independent_domains.min_independent_domains",
            "0",
        ),
        (
            "trusted_source_tiers is empty",
            "confidence.downgrades.low_trust_source.trusted_source_tiers",
            "",
        ),
        (
            "trusted_source_tiers holds something that is not an integer",
            "confidence.downgrades.low_trust_source.trusted_source_tiers",
            "abc",
        ),
        (
            "trusted_source_tiers holds a tier below one",
            "confidence.downgrades.low_trust_source.trusted_source_tiers",
            "0",
        ),
        (
            "exempt_seat_ids names something that is not a seat",
            "confidence.downgrades.low_trust_source.exempt_seat_ids",
            "nobody",
        ),
    )

    def test_every_reachable_refusal_is_the_loaders_own_sentence_word_for_word(self):
        """No second vocabulary anywhere: each refusal is quoted, not paraphrased.

        Most of these are refused for reasons nothing in the web app models — a
        last rung that is not zero, a repeated light name, a seat id that is not
        a seat, a schema version this build does not support, a vote ladder that
        stopped decreasing. They are refused all the same, and in the loader's
        words, because there is no second validator to have an opinion.
        """
        for rule, path, typed in self.ILLEGAL:
            with self.subTest(rule=rule, path=path):
                submitted = dict(self.texts(), **{path: typed})
                expected = self.loader_sentence(
                    settings.candidate_document(self.document(), submitted)
                )

                outcome = self.save(submitted)

                self.assertEqual(settings.REFUSED, outcome.state)
                self.assertEqual(expected, outcome.message)

    # How many distinct refusal *sentences* the set above must produce. It is
    # the number of ``raise DebateRulesError`` sites in ``debate_rules`` that a
    # value edit can reach: schema version, non-negative instant, increasing
    # timeline, positive window, vote count, decreasing ladder, light min_votes
    # range, light ladder, blank level, repeated level, last rung, downgrade
    # levels, min_independent_domains, trusted_source_tiers, exempt_seat_ids.
    # Cases sharing a site (a value that is not an integer and one that is out
    # of range) share a sentence but for different reasons, which is why the
    # count of cases is larger than this and this is the floor.
    REACHABLE_REFUSAL_SITES = 15

    def test_the_cases_reach_every_refusal_site_rather_than_one_of_them_often(self):
        """Without this, "twenty-six inputs" could be one refusal typed out often.

        Numbers are masked before comparing, because two disjuncts of one site
        differ only by the value quoted back into the sentence.
        """
        sentences = {}
        for rule, path, typed in self.ILLEGAL:
            outcome = self.save(dict(self.texts(), **{path: typed}))
            sentences.setdefault(re.sub(r"-?\d+", "#", outcome.message), []).append(rule)

        self.assertGreaterEqual(
            len(sentences), self.REACHABLE_REFUSAL_SITES, sorted(sentences)
        )

    def test_every_one_of_those_refusals_points_at_a_control_on_the_page(self):
        """A sentence nobody can act on is the failure this test looks for."""
        for rule, path, typed in self.ILLEGAL:
            with self.subTest(rule=rule, path=path):
                outcome = self.save(dict(self.texts(), **{path: typed}))

                self.assertTrue(outcome.fields, outcome.message)

    def test_none_of_those_refusals_touches_the_file(self):
        before = self.bytes_now()
        for rule, path, typed in self.ILLEGAL:
            with self.subTest(rule=rule, path=path):
                self.save(dict(self.texts(), **{path: typed}))

                self.assertEqual(before, self.bytes_now())

    def test_a_legal_change_carries_no_refusal_and_no_named_field(self):
        """FP direction: a saver that refused everything would pass those tests."""
        outcome = self.save(self.edited(timeline__final_settle_offset_ms="420000"))

        self.assertIsNone(outcome.message)
        self.assertEqual((), outcome.fields)

    def test_a_rule_file_that_is_not_there_is_reported_rather_than_traced_back(self):
        submitted = self.texts()
        self.rules_path.unlink()

        outcome = self.save(submitted)

        self.assertEqual(settings.UNREADABLE, outcome.state)
        self.assertIn(str(self.rules_path), outcome.message)

    def test_a_rule_file_that_is_not_json_is_reported_the_same_way(self):
        submitted = self.texts()
        self.rules_path.write_text("{ 這不是 JSON", encoding="utf-8")

        outcome = self.save(submitted)

        self.assertEqual(settings.UNREADABLE, outcome.state)


class SettingsAtomicWriteTest(SettingsFixture, unittest.TestCase):
    """What ``os.replace`` buys here, and what it does not.

    The claim under test is the narrow one the module makes: a reader never
    opens half a file, because the bytes are complete and validated before the
    name moves. The other direction is here too — two writers are *not*
    serialised — because a suite that only shows the guarantee reads as though
    the gap were covered.
    """

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_root = Path(self._data_tmp.name)

    def save(self, submitted, **options):
        return settings.save_rules(
            self.rules_path, submitted, self.data_root, **options
        )

    def test_the_new_bytes_arrive_under_the_real_name_in_one_move(self):
        moves = []

        def replace(source, target):
            moves.append((Path(source).name, Path(target).name))
            os.replace(source, target)

        self.save(
            self.edited(timeline__final_settle_offset_ms="420000"), replace=replace
        )

        self.assertEqual(1, len(moves))
        self.assertEqual("debate_rules.json", moves[0][1])
        self.assertNotEqual("debate_rules.json", moves[0][0])

    def test_the_file_readers_open_is_whole_and_legal_right_up_to_the_move(self):
        seen = []

        def replace(source, target):
            seen.append(load_debate_rules(target).force_stop_ms)
            os.replace(source, target)

        self.save(
            self.edited(timeline__final_settle_offset_ms="420000"), replace=replace
        )

        self.assertEqual([600_000], seen)
        self.assertEqual(660_000, load_debate_rules(self.rules_path).force_stop_ms)

    def test_a_move_that_fails_leaves_the_original_file_untouched(self):
        before = self.bytes_now()

        def replace(_source, _target):
            raise OSError("磁碟滿了")

        with self.assertRaises(OSError):
            self.save(
                self.edited(timeline__final_settle_offset_ms="420000"), replace=replace
            )

        self.assertEqual(before, self.bytes_now())

    def test_a_move_that_fails_leaves_no_candidate_file_behind(self):
        def replace(_source, _target):
            raise OSError("磁碟滿了")

        with self.assertRaises(OSError):
            self.save(
                self.edited(timeline__final_settle_offset_ms="420000"), replace=replace
            )

        self.assertEqual(
            ["debate_rules.json"], sorted(p.name for p in self.rules_dir.iterdir())
        )

    def test_two_saves_are_not_serialised_and_the_later_one_simply_wins(self):
        """The boundary, written as a test: nothing here is mutual exclusion.

        Both callers are told they saved. Only the second one's number is in the
        file, and the first one is never told its edit is gone.
        """
        first = self.save(self.edited(timeline__final_settle_offset_ms="420000"))
        second = self.save(self.edited(timeline__final_settle_offset_ms="480000"))

        self.assertEqual(settings.SAVED, first.state)
        self.assertEqual(settings.SAVED, second.state)
        self.assertEqual(480_000, self.document()["timeline"]["final_settle_offset_ms"])

    def test_a_publish_that_fails_says_the_file_moved_and_the_rules_did_not(self):
        def publish(_path):
            raise DebateRulesError("讀不到了")

        outcome = self.save(
            self.edited(timeline__final_settle_offset_ms="420000"), publish=publish
        )

        self.assertEqual(settings.NOT_PUBLISHED, outcome.state)
        self.assertEqual(420_000, self.document()["timeline"]["final_settle_offset_ms"])
        self.assertEqual(600_000, debate_rules().force_stop_ms)


class SettingsLockTest(SettingsFixture, unittest.TestCase):
    """Editing is locked while this Data Root has a run going."""

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_root = Path(self._data_tmp.name)

    def finish(self, run_dir):
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")

    def save(self):
        return settings.save_rules(
            self.rules_path,
            self.edited(timeline__final_settle_offset_ms="420000"),
            self.data_root,
        )

    def test_a_data_root_with_no_run_at_all_is_not_locked(self):
        self.assertIsNone(settings.locked_by(self.data_root))

    def test_a_run_still_going_locks_the_page_and_says_which_run(self):
        write_live_run(self.data_root)

        self.assertEqual(LIVE_RUN_ID, settings.locked_by(self.data_root))

    def test_a_run_that_finished_unlocks_the_page_again(self):
        self.finish(write_live_run(self.data_root))

        self.assertIsNone(settings.locked_by(self.data_root))

    def test_a_save_while_a_run_is_going_is_refused_and_says_which_run(self):
        write_live_run(self.data_root)

        outcome = self.save()

        self.assertEqual(settings.LOCKED, outcome.state)
        self.assertEqual(LIVE_RUN_ID, outcome.run_id)

    def test_a_save_while_a_run_is_going_does_not_touch_the_file(self):
        write_live_run(self.data_root)
        before = self.bytes_now()

        self.save()

        self.assertEqual(before, self.bytes_now())

    def test_a_save_after_that_run_ended_is_accepted(self):
        """FP direction: the lock must let go, not merely take hold."""
        self.finish(write_live_run(self.data_root))

        self.assertEqual(settings.SAVED, self.save().state)

    def test_the_lock_reads_the_state_the_live_room_already_publishes(self):
        """Not a second opinion about what "in progress" means.

        ``LaunchLock`` answers a different question — launches *this process*
        started — and is deliberately not consulted here: a run begun from the
        CLI is affected by a rule change just the same, and that lock cannot see
        it.
        """
        write_live_run(self.data_root)

        self.assertEqual(
            live.STATUS_RUNNING, live.live_snapshot(self.data_root)["state"]
        )
        self.assertEqual(
            live.in_progress_run_id(self.data_root), settings.locked_by(self.data_root)
        )

    def test_that_agreement_holds_for_a_finished_run_too(self):
        self.finish(write_live_run(self.data_root))

        self.assertEqual(
            live.STATUS_FINISHED, live.live_snapshot(self.data_root)["state"]
        )
        self.assertEqual(
            live.in_progress_run_id(self.data_root), settings.locked_by(self.data_root)
        )

    def test_a_launch_this_process_started_does_not_by_itself_lock_the_page(self):
        """The two locks are different questions, and this is the difference."""
        lock = launch_module.LaunchLock()
        lock.claim(lambda: FakeProcess())

        self.assertTrue(lock.busy())
        self.assertIsNone(settings.locked_by(self.data_root))


class RulesTakeEffectOnTheNextRunTest(SettingsFixture, unittest.TestCase):
    """Two claims, kept apart on purpose: the run under way, and the next one."""

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_root = Path(self._data_tmp.name)

    def lower_the_ladder(self):
        return settings.save_rules(
            self.rules_path,
            self.edited(
                **{
                    "timeline__vote_rounds[2]__threshold": "4",
                    "timeline__vote_rounds[3]__threshold": "3",
                }
            ),
            self.data_root,
        )

    def test_the_rules_a_run_already_holds_still_answer_the_old_way(self):
        """What ``run_controller`` captures at the start of a run, asked again.

        The state machine's own entry point is used rather than the frozen
        object's method, because that is the seam a run actually goes through.
        """
        held = debate_rules()
        self.assertEqual(5, required_votes_at(480_000, rules=held))

        self.lower_the_ladder()

        self.assertEqual(5, required_votes_at(480_000, rules=held))
        self.assertEqual(5, held.vote_rounds[2].threshold)

    def test_the_next_run_reads_the_new_numbers(self):
        self.lower_the_ladder()

        self.assertEqual(4, required_votes_at(480_000))
        self.assertEqual(4, debate_rules().vote_rounds[2].threshold)

    def test_the_two_answers_come_from_two_different_frozen_objects(self):
        held = debate_rules()

        self.lower_the_ladder()

        self.assertIsNot(held, debate_rules())

    def test_a_refused_save_changes_neither_of_the_two(self):
        """FP direction: neither claim may be produced by a save that failed."""
        held = debate_rules()

        settings.save_rules(
            self.rules_path,
            self.edited(**{"timeline__vote_rounds[0]__threshold": "0"}),
            self.data_root,
        )

        self.assertIs(held, debate_rules())
        self.assertEqual(5, required_votes_at(480_000))

class SettingsPageFixture(PageFixture, SettingsFixture):
    """A handler pointed at this test's own copy of the rule file."""

    def build_handler(self, stream=None, spawn=None):
        self.stream = stream or self.single_pass_stream()
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=spawn or self.spawn,
            rules_path=self.rules_path,
        )
        return self.handler

    def page(self):
        return self.get("/settings").body

    def submit(self, **changes):
        return self.post("/settings", self.edited(**changes))

    def controls(self, body):
        return re.findall(r'<input[^>]*\bid="([^"]+)"', body)

    def value_of(self, body, path):
        found = re.search(
            r'<input[^>]*\bid="{}"[^>]*\bvalue="([^"]*)"'.format(re.escape(path)), body
        )
        return found.group(1) if found else None

    def input_of(self, body, path):
        found = re.search(r'<input[^>]*\bid="{}"[^>]*>'.format(re.escape(path)), body)
        return found.group(0) if found else ""


class SettingsPageTest(SettingsPageFixture, unittest.TestCase):
    """What the page shows before anybody edits anything."""

    def test_the_page_opens(self):
        self.assertEqual(200, self.get("/settings").status)

    def test_the_timeline_has_one_row_per_schema_v2_vote_round(self):
        rows = settings.settings_data(
            self.data_root, rules_path=self.rules_path
        )["timeline"]

        self.assertEqual(
            [
                {
                    "round": index,
                    "label": "第 {} 輪".format(index),
                    "open_offset_ms": vote_round["open_offset_ms"],
                    "threshold": vote_round["threshold"],
                    "clock": settings._clock(vote_round["open_offset_ms"]),
                }
                for index, vote_round in enumerate(
                    self.shipped["timeline"]["vote_rounds"], start=1
                )
            ],
            rows,
        )

    def test_every_field_in_the_document_has_a_control_on_the_page(self):
        body = self.page()

        for path, _, _ in settings.field_texts(self.document()):
            self.assertIn('id="{}"'.format(path), body, path)

    def test_the_page_holds_no_control_the_document_does_not_have(self):
        """FP direction: a page of extra boxes would pass the test above."""
        declared = {path for path, _, _ in settings.field_texts(self.document())}

        self.assertEqual(declared, set(self.controls(self.page())))

    def test_each_control_is_filled_with_what_the_file_says(self):
        body = self.page()

        self.assertEqual(
            "60000", self.value_of(body, "timeline.vote_rounds[0].open_offset_ms")
        )
        self.assertEqual("7", self.value_of(body, "timeline.vote_rounds[0].threshold"))
        self.assertEqual("blue", self.value_of(body, "confidence.light_scale[0].level"))
        self.assertEqual(
            "1, 2",
            self.value_of(
                body, "confidence.downgrades.low_trust_source.trusted_source_tiers"
            ),
        )

    def test_every_control_is_named_by_a_label_that_points_at_it(self):
        body = self.page()
        controls = self.controls(body)
        labelled = re.findall(r'<label[^>]*\bfor="([^"]+)"', body)

        self.assertTrue(controls)
        self.assertEqual(sorted(controls), sorted(set(labelled) & set(controls)))

    def test_the_page_says_which_file_it_is_editing(self):
        self.assertIn(str(self.rules_path), self.page())

    def test_the_timeline_is_drawn_with_every_number_written_out_as_well(self):
        body = self.page()

        for index, vote_round in enumerate(
            self.shipped["timeline"]["vote_rounds"], start=1
        ):
            self.assertIn("第 {} 輪".format(index), body)
            self.assertIn("{} ms".format(vote_round["open_offset_ms"]), body)
            self.assertIn("門檻 {} 票".format(vote_round["threshold"]), body)

    def test_the_timeline_has_exactly_one_list_row_per_vote_round(self):
        timeline = re.search(
            r'<ul class="timeline">(.*?)</ul>', self.page(), re.DOTALL
        ).group(1)

        self.assertEqual(
            len(self.shipped["timeline"]["vote_rounds"]), timeline.count("<li>")
        )

    def test_the_comments_in_the_file_are_shown_rather_than_dropped(self):
        body = self.page()

        self.assertIn(escape(self.shipped["timeline"]["_about"][:20]), body)

    def test_the_history_page_offers_a_way_to_reach_this_one(self):
        self.assertIn('href="/settings"', self.get("/").body)

    def test_the_page_declares_the_same_semantics_the_other_pages_do(self):
        body = self.page()

        self.assertIn('<html lang="zh-Hant">', body)
        self.assertEqual(1, body.count("<h1"))
        self.assertLess(body.index('href="#main"'), body.index("<header"))
        self.assertEqual(
            [], [v for v in re.findall(r'tabindex="(-?\d+)"', body) if int(v) > 0]
        )

    def test_the_form_posts_back_to_this_page(self):
        self.assertIn('<form class="card" method="post" action="/settings"', self.page())

    def test_a_rule_file_that_will_not_read_says_so_instead_of_tracebacking(self):
        self.rules_path.write_text("{ 這不是 JSON", encoding="utf-8")

        response = self.get("/settings")

        self.assertEqual(200, response.status)
        self.assertIn("不是合法 JSON", response.body)

    def test_a_rule_file_the_loader_refuses_is_still_shown_for_editing(self):
        """An operator cannot fix a file the page will not display."""
        document = self.document()
        document["timeline"]["vote_rounds"][0]["threshold"] = 0
        self.rules_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

        body = self.get("/settings").body

        path = "timeline.vote_rounds[0].threshold"
        self.assertIn(path, body)
        self.assertEqual("0", self.value_of(body, path))


class SettingsSubmissionTest(SettingsPageFixture, unittest.TestCase):
    """Submitting the form: accepted, refused, and what the reader is shown."""

    def test_a_legal_change_is_accepted_and_the_file_says_so(self):
        response = self.submit(timeline__final_settle_offset_ms="420000")

        self.assertEqual(200, response.status)
        self.assertEqual(420_000, self.document()["timeline"]["final_settle_offset_ms"])

    def test_an_accepted_change_says_it_takes_effect_on_the_next_run(self):
        body = self.submit(timeline__final_settle_offset_ms="420000").body

        self.assertIn("下一個開始的 run", body)
        self.assertIn("不受影響", body)

    def test_an_accepted_change_comes_back_on_the_page_it_was_typed_on(self):
        body = self.submit(timeline__final_settle_offset_ms="420000").body

        self.assertEqual(
            "420000", self.value_of(body, "timeline.final_settle_offset_ms")
        )

    def test_an_accepted_change_is_recorded(self):
        self.submit(timeline__final_settle_offset_ms="420000")

        events = [record["event"] for record in self.records()]

        self.assertIn("settings_saved", events)

    def test_a_refused_change_shows_the_loaders_own_sentence(self):
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body

        self.assertIn(
            escape("timeline.vote_rounds[0].threshold 必須是 1 到 7"), body
        )

    def test_a_refused_change_marks_the_control_it_is_about(self):
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body
        path = "timeline.vote_rounds[0].threshold"
        control = self.input_of(body, path)

        self.assertIn('aria-invalid="true"', control)
        self.assertIn("error-{}".format(path), control)
        self.assertIn('id="error-{}"'.format(path), body)

    def test_the_message_is_reachable_from_the_control_that_names_it(self):
        """``aria-describedby`` is how a screen reader gets from one to the other."""
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body
        path = "timeline.vote_rounds[0].threshold"
        control = self.input_of(body, path)
        described = re.search(r'aria-describedby="([^"]+)"', control).group(1).split()

        self.assertIn("error-{}".format(path), described)
        for identifier in described:
            self.assertIn('id="{}"'.format(identifier), body, identifier)

    def test_a_control_nobody_complained_about_is_not_marked(self):
        """FP direction: marking every box would pass the two tests above."""
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body

        self.assertNotIn(
            'aria-invalid', self.input_of(body, "timeline.final_settle_offset_ms")
        )

    def test_a_refused_change_is_not_only_a_colour(self):
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body

        self.assertIn("這次沒有存檔", body)
        self.assertIn("這一欄被拒絕", body)

    def test_a_refused_change_keeps_what_was_typed_so_it_can_be_corrected(self):
        body = self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body

        self.assertEqual("0", self.value_of(body, "timeline.vote_rounds[0].threshold"))

    def test_a_refused_change_leaves_the_file_alone(self):
        before = self.bytes_now()

        self.submit(**{"timeline__vote_rounds[0]__threshold": "0"})

        self.assertEqual(before, self.bytes_now())

    def test_a_refused_change_is_recorded(self):
        self.submit(**{"timeline__vote_rounds[0]__threshold": "0"})

        events = [record["event"] for record in self.records()]

        self.assertIn("settings_refused", events)

    def test_an_out_of_order_timeline_marks_both_ends_of_the_pair(self):
        body = self.submit(
            **{"timeline__vote_rounds[1]__open_offset_ms": "1000"}
        ).body

        self.assertIn(
            'aria-invalid="true"',
            self.input_of(body, "timeline.vote_rounds[1].open_offset_ms"),
        )
        self.assertIn(
            'aria-invalid="true"',
            self.input_of(body, "timeline.vote_rounds[0].open_offset_ms"),
        )

    def test_a_non_decreasing_threshold_is_refused_in_the_loaders_own_words(self):
        body = self.submit(**{"timeline__vote_rounds[1]__threshold": "7"}).body

        self.assertIn("票數門檻必須嚴格遞減", body)
        for path in (
            "timeline.vote_rounds[0].threshold",
            "timeline.vote_rounds[1].threshold",
        ):
            self.assertIn('aria-invalid="true"', self.input_of(body, path), path)

    def test_only_the_first_problem_the_loader_found_is_reported(self):
        """The loader stops at the first refusal, so one is all there is to show.

        Two illegal values go in; the sentence is about the timeline, which the
        loader checks first, and the vote count is not mentioned at all. That is
        the loader's shape and this page does not paper over it by running a
        second pass of its own — a second pass would be a second validator.
        """
        body = self.submit(
            **{
                "timeline__vote_rounds[0]__open_offset_ms": "-1",
                "timeline__vote_rounds[1]__threshold": "7",
            }
        ).body

        self.assertIn("必須是非負整數毫秒", body)
        self.assertNotIn("票數門檻必須嚴格遞減", body)
        self.assertNotIn(
            'aria-invalid="true"',
            self.input_of(body, "timeline.vote_rounds[1].threshold"),
        )

    def test_the_next_problem_is_reported_once_the_first_one_is_fixed(self):
        """FP direction: the second problem is not lost, only queued."""
        body = self.submit(
            **{
                "timeline__vote_rounds[0]__open_offset_ms": "-1",
                "timeline__vote_rounds[1]__threshold": "7",
            }
        ).body
        self.assertIn("必須是非負整數毫秒", body)

        body = self.submit(**{"timeline__vote_rounds[1]__threshold": "7"}).body

        self.assertIn("票數門檻必須嚴格遞減", body)
        self.assertIn(
            'aria-invalid="true"',
            self.input_of(body, "timeline.vote_rounds[1].threshold"),
        )

    def test_a_body_that_carries_no_field_is_not_read_as_an_empty_form(self):
        """An oversized or empty body must not be saved as "nothing changed"."""
        response = self.post("/settings", {"question": "不是設定表單"})

        self.assertIn("沒有收到任何設定欄位", response.body)

    def test_the_whole_form_fits_inside_the_body_this_server_will_read(self):
        """A form larger than the cap would arrive as no form at all."""
        body = urlencode(self.texts()).encode("utf-8")

        self.assertLess(len(body), MAX_FORM_BYTES)


class SettingsLockedPageTest(SettingsPageFixture, unittest.TestCase):
    """While a run is under way the page says so and refuses to write."""

    def setUp(self):
        super().setUp()
        self.run_dir = write_live_run(self.data_root)

    def finish(self):
        (self.run_dir / "manifest.json").write_text("{}", encoding="utf-8")

    def test_the_page_says_it_is_locked_and_which_run_locks_it(self):
        body = self.page()

        self.assertIn("設定頁目前鎖定", body)
        self.assertIn(LIVE_RUN_ID, body)

    def test_every_control_is_disabled_while_it_is_locked(self):
        body = self.page()

        controls = re.findall(r"<input[^>]*>", body)
        self.assertTrue(controls)
        for control in controls:
            self.assertIn("disabled", control)

    def test_the_save_button_is_disabled_too(self):
        self.assertIn('type="submit" disabled', self.page())

    def test_a_submission_while_it_is_locked_is_refused_as_a_conflict(self):
        response = self.submit(timeline__final_settle_offset_ms="420000")

        self.assertEqual(409, response.status)
        self.assertIn("設定頁目前鎖定", response.body)

    def test_a_submission_while_it_is_locked_does_not_touch_the_file(self):
        before = self.bytes_now()

        self.submit(timeline__final_settle_offset_ms="420000")

        self.assertEqual(before, self.bytes_now())

    def test_a_locked_submission_is_recorded(self):
        self.submit(timeline__final_settle_offset_ms="420000")

        events = [record["event"] for record in self.records()]

        self.assertIn("settings_locked", events)

    def test_the_page_unlocks_once_that_run_has_ended(self):
        """FP direction: a page that were always locked would pass the rest."""
        self.finish()

        body = self.page()

        self.assertNotIn("設定頁目前鎖定", body)
        for control in re.findall(r"<input[^>]*>", body):
            self.assertNotIn("disabled", control)

    def test_a_submission_once_that_run_has_ended_is_accepted(self):
        self.finish()

        response = self.submit(timeline__final_settle_offset_ms="420000")

        self.assertEqual(200, response.status)
        self.assertEqual(420_000, self.document()["timeline"]["final_settle_offset_ms"])


class SettingsPolicyTest(SettingsPageFixture, unittest.TestCase):
    """The settings page is a server-rendered form, so it keeps the strict policy."""

    def test_the_page_is_sent_with_the_policy_that_forbids_every_script(self):
        for response in (
            self.get("/settings"),
            self.submit(timeline__final_settle_offset_ms="420000"),
        ):
            self.assertEqual(
                CONTENT_SECURITY_POLICY,
                response.headers["Content-Security-Policy"],
            )

    def test_the_page_carries_no_script_at_all(self):
        self.assertNotIn("<script", self.page())
        self.assertNotIn(
            "<script",
            self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body,
        )

    def test_the_strict_policy_still_allows_this_form_to_post_to_this_origin(self):
        self.assertIn("form-action 'self'", CONTENT_SECURITY_POLICY)

    def test_the_page_still_refuses_to_be_sniffed(self):
        self.assertEqual(
            "nosniff", self.get("/settings").headers["X-Content-Type-Options"]
        )


class OnlyTheRoomIsGivenAScriptTest(unittest.TestCase):
    """Which pages carry a script is derived from the renderers, not listed.

    ``_document`` is the one function that puts a ``<script>`` on a page, and it
    only does so for a caller that hands it one. So the callers that pass
    ``scripts`` are exactly the pages with a script, and a new page that grew one
    fails here rather than quietly shipping under ``script-src 'none'``.
    """

    def renderers_passing_scripts(self):
        found = set()
        entry = Path(pages.__file__)
        sources = (
            [entry]
            if entry.name != "__init__.py"
            else sorted(entry.parent.glob("*_page.py"))
        )
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef):
                    continue
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, "id", None)
                    if name != "_document":
                        continue
                    if any(word.arg == "scripts" for word in node.keywords):
                        found.add(function.name)
        return found

    def test_the_room_is_the_only_page_handed_a_script(self):
        self.assertEqual({"render_live_page"}, self.renderers_passing_scripts())

    def test_the_scan_would_notice_a_second_page_that_grew_one(self):
        """Discrimination: the scan has to be able to fail."""
        source = (
            "def render_a(data):\n    return _document('a', '', [])\n"
            "def render_b(data):\n    return _document('b', '', [], scripts=('/x.js',))\n"
        )
        directory = Path(tempfile.mkdtemp(prefix="t11-scripts-"))
        self.addCleanup(shutil.rmtree, directory, True)
        probe = directory / "probe.py"
        probe.write_text(source, encoding="utf-8")

        with mock.patch.object(pages, "__file__", str(probe)):
            self.assertEqual({"render_b"}, self.renderers_passing_scripts())


class PageFooterHonestyTest(SettingsPageFixture, unittest.TestCase):
    """A page that writes a file may not carry the read-only footer."""

    def test_the_settings_page_says_it_writes_the_rule_file(self):
        body = self.page()

        self.assertIn(pages.SETTINGS_FOOTER, body)
        self.assertNotIn(pages.READ_ONLY_FOOTER, body)

    def test_every_page_that_only_reads_still_says_so(self):
        """The run detail page is no longer one of them.

        Ticket 06 put the "匯出 PDF" button on it, so it is a page that can add
        two files to a run directory and its footer says so instead
        (``pages.RUN_DETAIL_FOOTER``, asserted in
        ``tests/test_webapp_pdf_export.py`` beside the button that made it true).
        The three below carry no control that writes anything.
        """
        self.index_two_runs()

        for path in ("/", "/live", "/nope"):
            self.assertIn(pages.READ_ONLY_FOOTER, self.get(path).body, path)


class SettingsColourTest(unittest.TestCase):
    """The two states this page adds are colours that are asserted, not guessed."""

    TOKENS = ("danger", "success")

    def test_both_new_colours_are_declared_in_the_palette(self):
        for token in self.TOKENS:
            self.assertIn(token, pages.PALETTE)

    def test_both_new_colours_answer_to_the_text_minimum_on_both_backgrounds(self):
        required = {
            (foreground, background)
            for foreground, background, minimum in pages.CONTRAST_REQUIREMENTS
            if minimum >= 4.5
        }
        for token in self.TOKENS:
            for background in ("page", "surface"):
                self.assertIn((token, background), required)

    def test_every_outcome_a_save_can_have_is_something_the_page_can_say(self):
        """Derived, not listed: the states come from the module that has them."""
        self.assertEqual(
            set(settings.STATES),
            set(pages.SETTINGS_NOTICES) | {settings.LOCKED},
        )

    def test_the_stylesheet_paints_the_refusal_and_the_confirmation(self):
        sheet = pages.stylesheet()

        self.assertIn(".field-error{", sheet)
        self.assertIn("var(--danger)", sheet)
        self.assertIn("var(--success)", sheet)


class RenderedSettingsPageTest(SettingsPageFixture, unittest.TestCase):
    """The substitute for the screenshot the ticket asks for.

    No browser exists in this environment — chrome, chromium, firefox,
    wkhtmltoimage, selenium and playwright were each looked for and none is
    installed — so there is no screenshot of a refused edit to take. What is kept
    instead is exactly what a browser would have been handed: the rendered page
    of a refusal, written to a file, with the elements that make it a refusal
    asserted on that file.
    """

    def setUp(self):
        super().setUp()
        self.rendered = self.data_root / "settings-refused.html"
        self.rendered.write_text(
            self.submit(**{"timeline__vote_rounds[0]__threshold": "0"}).body,
            encoding="utf-8",
        )
        self.text = self.rendered.read_text(encoding="utf-8")

    def test_the_kept_page_is_a_whole_document(self):
        self.assertTrue(self.text.startswith("<!doctype html>"))
        self.assertIn("</html>", self.text)

    def test_the_kept_page_announces_the_refusal(self):
        self.assertIn('role="alert"', self.text)
        self.assertIn("這次沒有存檔", self.text)

    def test_the_kept_page_names_the_field_and_quotes_the_reason(self):
        self.assertIn('id="error-timeline.vote_rounds[0].threshold"', self.text)
        self.assertIn(escape("必須是 1 到 7 之間的整數票數"), self.text)

    def test_the_kept_page_still_holds_every_control(self):
        self.assertEqual(
            len(settings.field_texts(self.document())),
            len(self.controls(self.text)),
        )

    def test_the_file_on_disk_was_not_changed_by_the_page_that_was_kept(self):
        self.assertEqual(7, self.document()["timeline"]["vote_rounds"][0]["threshold"])


class SettingsValueSurvivesAReloadTest(SettingsPageFixture, unittest.TestCase):
    """Save a rule, then open the page fresh: the new value is what is shown.

    The front-end acceptance step in the design-system ticket, as a test: no rule
    value is frozen at import, so a page rendered after a save shows the file's
    own number rather than the one this process started with. The repaint must not
    quietly turn a reload-aware read into a module-level constant, which is the
    defect Ticket 13 spent thirteen rounds keeping out.
    """

    #: The control's own id, which is the rule's path in the document.
    PATH = "timeline.vote_rounds[0].open_offset_ms"

    def test_a_saved_value_is_the_one_a_fresh_render_shows(self):
        before = self.value_of(self.page(), self.PATH)
        self.assertIsNotNone(before)
        self.assertNotEqual("70000", before)

        self.submit(**{"timeline__vote_rounds[0]__open_offset_ms": "70000"})

        self.assertEqual("70000", self.value_of(self.page(), self.PATH))

    def test_a_second_handler_built_after_the_save_shows_it_too(self):
        """FP direction: the render above could have been answered from state the
        POST left behind in this handler. A new handler has none."""
        self.submit(**{"timeline__vote_rounds[0]__open_offset_ms": "70000"})
        self.build_handler()

        self.assertEqual("70000", self.value_of(self.page(), self.PATH))


class SettingsChangeReachesLiveTimelineTest(SettingsPageFixture, unittest.TestCase):
    """Acceptance: a saved nested round reaches the next live render."""

    PATH = "timeline__vote_rounds[0]__open_offset_ms"

    def test_a_saved_round_offset_reaches_the_next_live_page(self):
        response = self.submit(**{self.PATH: "70000"})

        self.assertEqual(200, response.status)
        self.assertRegex(self.get("/live").body, r"T\+05:10.*第 1 輪開票")

    def test_the_edit_really_went_through_the_page_and_reload(self):
        self.submit(**{self.PATH: "70000"})

        self.assertEqual(
            70_000,
            self.document()["timeline"]["vote_rounds"][0]["open_offset_ms"],
        )
        self.assertEqual(70_000, debate_rules().vote_rounds[0].open_offset_ms)


# -- 設定頁白話中文（Spec R-001） ---------------------------------------------


#: Spec〈R-001 設定頁白話中文〉的逐鍵文案，逐字抄一份進來當對照組。
#:
#: 刻意不是 ``import settings.FIELD_LABELS``：兩邊讀同一個 dict 的話，有人把文案
#: 改掉、打錯字或漏一個鍵，斷言仍然會通過。抄一份的代價是改文案要改兩個地方，而那
#: 正是這一份的用途——文案是需求本身，不該被實作單方面改掉。
PLAIN_FIELD_WORDS = {
    "schema_version": ("規則檔版本", "規則檔的格式版本，目前僅支援 2，平常不需改動"),
    "timeline.vote_rounds[].open_offset_ms": (
        "開票時刻（封存後毫秒）",
        "封存後過這麼多毫秒開這一輪票；單幣題封存在第 4 分鐘",
    ),
    "timeline.vote_rounds[].threshold": (
        "所需同立場票數",
        "這一輪要幾席同立場才能結案寫報告",
    ),
    "timeline.final_settle_offset_ms": (
        "硬停結算時刻（封存後毫秒）",
        "時間到直接停止辯論做最終結算；沒有立場達到末輪票數就亮紅燈",
    ),
    "confidence.light_scale[].min_votes": (
        "最低票數",
        "拿到至少這麼多有效票，燈號落在這一級",
    ),
    "confidence.light_scale[].level": (
        "燈色",
        "這一級對應的燈色（blue／green／yellow／orange／red）",
    ),
    "confidence.downgrades.few_independent_domains.levels": (
        "降幾級",
        "獨立來源網站太少時，燈號往下降的級數",
    ),
    "confidence.downgrades.few_independent_domains.min_independent_domains": (
        "最低獨立網域數",
        "採納立場引用的來源至少要來自幾個不同網站",
    ),
    "confidence.downgrades.low_trust_source.levels": (
        "降幾級",
        "引用低可信來源時，燈號往下降的級數",
    ),
    "confidence.downgrades.low_trust_source.trusted_source_tiers": (
        "可信來源等級",
        "視為可信的來源等級清單（逗號分隔）",
    ),
    "confidence.downgrades.low_trust_source.exempt_seat_ids": (
        "豁免席位",
        "不受此降級約束的席位（輿情席職責即蒐集輿情）",
    ),
}

#: Spec 的分組標題表，同樣逐字抄一份。``confidence`` 在現行規則檔裡沒有直屬控制
#: 項，所以畫不出它的 fieldset；它仍然在表裡，由下面單獨一條斷言守住。
PLAIN_SECTION_WORDS = {
    "": "基本",
    "timeline": "時間軸",
    "timeline.vote_rounds[]": "投票輪清單",
    "confidence": "燈號規則",
    "confidence.light_scale[]": "燈號階梯",
    "confidence.downgrades.few_independent_domains": "降級：獨立來源不足",
    "confidence.downgrades.low_trust_source": "降級：低可信來源",
}

PLAIN_SECTION_DESCRIPTIONS = {
    "timeline": "辯論各輪開票時刻，全部從證據封存那一刻起算",
    "timeline.vote_rounds[]": "一列一輪：何時開票、需要幾席同立場才結案",
}


def generic_path_of(path):
    """``light_scale[3].level`` → ``light_scale[].level``：測試自己的一份寫法。

    文案表是一份而列表有五級，所以查表前要先把索引抹掉。這裡重寫一次而不是呼叫
    ``settings`` 的版本，理由和上面抄文案一樣：兩邊同時錯才會過。
    """
    return re.sub(r"\[\d+\]", "[]", path)


class SettingsPlainWordsTest(SettingsPageFixture, unittest.TestCase):
    """R-001：每個規則項是中文標籤加一句白話說明，分組標題也是中文。"""

    def labels(self, body):
        """``控制項 id`` → 那個控制項的 ``<label>`` 裡的文字。"""
        return dict(re.findall(r'<label for="([^"]+)">(.*?)</label>', body))

    def legends(self, body):
        return re.findall(r"<legend>(.*?)</legend>", body, re.DOTALL)

    def test_the_table_says_exactly_what_the_spec_says(self):
        self.assertEqual(PLAIN_FIELD_WORDS, dict(settings.FIELD_LABELS))

    def test_the_group_titles_say_exactly_what_the_spec_says(self):
        self.assertEqual(PLAIN_SECTION_WORDS, dict(settings.SECTION_LABELS))

    def test_the_group_descriptions_say_exactly_what_the_spec_says(self):
        self.assertEqual(
            PLAIN_SECTION_DESCRIPTIONS, dict(settings.SECTION_DESCRIPTIONS)
        )

    def test_the_approved_group_descriptions_are_rendered(self):
        body = self.page()

        self.assertEqual(
            1, body.count(PLAIN_SECTION_DESCRIPTIONS["timeline"])
        )
        self.assertEqual(
            len(self.document()["timeline"]["vote_rounds"]),
            body.count(PLAIN_SECTION_DESCRIPTIONS["timeline.vote_rounds[]"]),
        )

    def test_the_table_covers_every_key_the_shipped_file_has_and_no_other(self):
        """漏一個鍵會讓它退回原鍵名；多一個是沒人會看到的死文案。"""
        shipped = {
            generic_path_of(path) for path, _, _ in settings.field_texts(self.document())
        }

        self.assertEqual(shipped, set(settings.FIELD_LABELS))

    def test_every_control_is_named_by_its_chinese_label(self):
        body = self.page()
        labels = self.labels(body)

        for path, _, _ in settings.field_texts(self.document()):
            expected, _ = PLAIN_FIELD_WORDS[generic_path_of(path)]
            self.assertEqual(expected, labels[path], path)

    def test_every_control_carries_its_one_sentence_of_plain_chinese(self):
        body = self.page()

        for path, _, _ in settings.field_texts(self.document()):
            _, sentence = PLAIN_FIELD_WORDS[generic_path_of(path)]
            self.assertRegex(
                body,
                r'<p[^>]*\bid="note-{}"[^>]*>{}</p>'.format(
                    re.escape(path), re.escape(sentence)
                ),
                path,
            )

    def test_the_sentence_is_reachable_from_the_control_it_explains(self):
        """一句話擺在畫面上不算數：讀螢幕的人要從欄位本身走得到它。"""
        path = "timeline.vote_rounds[0].threshold"
        control = self.input_of(self.page(), path)
        described = re.search(r'aria-describedby="([^"]+)"', control).group(1).split()

        self.assertIn("note-{}".format(path), described)

    def test_nothing_in_the_shipped_file_is_left_untranslated(self):
        """FP 方向：整頁退回原鍵名也會通過「有標籤」那一條。"""
        self.assertNotIn(settings.UNTRANSLATED_NOTE, self.page())

    def test_every_group_title_is_the_chinese_one_the_spec_wrote(self):
        vote_rounds = len(self.document()["timeline"]["vote_rounds"])
        rungs = len(self.document()["confidence"]["light_scale"])
        expected = (
            [PLAIN_SECTION_WORDS[""]]
            + [PLAIN_SECTION_WORDS["timeline.vote_rounds[]"]] * vote_rounds
            + [PLAIN_SECTION_WORDS["timeline"]]
            + [PLAIN_SECTION_WORDS["confidence.light_scale[]"]] * rungs
            + [
                PLAIN_SECTION_WORDS["confidence.downgrades.few_independent_domains"],
                PLAIN_SECTION_WORDS["confidence.downgrades.low_trust_source"],
            ]
        )

        self.assertEqual(expected, self.legends(self.page()))

    def test_the_title_for_confidence_itself_is_declared_even_with_no_control(self):
        """現行規則檔的 ``confidence`` 只裝著別的分組，所以它畫不出 fieldset。

        Spec 仍然指定了它的標題：日後直接掛一個欄位在 ``confidence`` 底下，那一區
        就已經有中文標題，不必回頭再補一次表。
        """
        self.assertIn("confidence", settings.SECTION_LABELS)
        self.assertNotIn("燈號規則", self.legends(self.page()))

    def test_the_shape_hint_under_each_control_is_still_there(self):
        """白話說明是多的一行，不是拿掉型別提示換來的。"""
        body = self.page()

        self.assertIn(settings.KIND_HINTS[settings.KIND_INTEGER], body)
        self.assertIn(settings.KIND_HINTS[settings.KIND_LIST], body)
        self.assertRegex(
            body, r'<p[^>]*\bid="hint-timeline\.vote_rounds\[0\]\.threshold"'
        )

    def test_the_about_notes_in_the_file_are_shown_the_way_they_were(self):
        """``_about`` 照舊：仍然綁在它被寫在上面的那一區，文字一字不動。"""
        body = self.page()

        self.assertIn('id="about-timeline"', body)
        self.assertIn(escape(self.shipped["timeline"]["_about"]), body)
        self.assertIn(escape(self.shipped["_about"]), body)


class SettingsUntranslatedKeyTest(SettingsPageFixture, unittest.TestCase):
    """規則檔多了一個沒人翻譯的鍵：頁面照畫，欄位照樣可以編輯。

    R-001 的 fail-open 條款。翻譯表只是文案，永遠可能落後於規則檔；它落後的時候
    使用者付出的代價必須僅止於「這一欄還是英文鍵名」，而不是整頁打不開或這一欄不
    給改。
    """

    def add(self, **changes):
        document = self.document()
        for path, value in changes.items():
            self.put(document, path.replace("__", "."), value)
        self.rules_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

    def put(self, document, path, value):
        *containers, key = path.split(".")
        for name in containers:
            document = document[name]
        document[key] = value

    def label_of(self, body, path):
        found = re.search(
            r'<label for="{}">(.*?)</label>'.format(re.escape(path)), body
        )
        return found.group(1) if found else None

    def test_the_page_still_opens_when_a_key_has_no_translation(self):
        self.add(timeline__warm_up=30_000)

        self.assertEqual(200, self.get("/settings").status)

    def test_the_new_key_is_shown_under_the_name_the_file_spells(self):
        self.add(timeline__warm_up=30_000)

        label = self.label_of(self.get("/settings").body, "timeline.warm_up")

        self.assertIn("warm_up", label)
        self.assertIn(settings.UNTRANSLATED_NOTE, label)

    def test_only_the_new_key_is_marked(self):
        """FP 方向：把整頁都標成尚未翻譯也會通過上面那一條。"""
        self.add(timeline__warm_up=30_000)

        self.assertEqual(
            1, self.get("/settings").body.count(settings.UNTRANSLATED_NOTE)
        )

    def test_the_new_key_can_still_be_typed_into(self):
        self.add(timeline__warm_up=30_000)

        control = self.input_of(self.get("/settings").body, "timeline.warm_up")

        self.assertIn('value="30000"', control)
        self.assertNotIn("disabled", control)

    def test_what_is_typed_into_it_reaches_the_document_that_would_be_saved(self):
        self.add(timeline__warm_up=30_000)

        rebuilt = settings.candidate_document(
            self.document(), self.edited(timeline__warm_up="45000")
        )

        self.assertEqual(45_000, rebuilt["timeline"]["warm_up"])

    def test_the_only_thing_that_refuses_the_new_key_is_the_loader(self):
        """這一頁沒有替載入器擋任何東西——擋下來的是載入器自己的句子。"""
        self.add(timeline__warm_up=30_000)

        outcome = settings.save_rules(
            self.rules_path, self.edited(timeline__warm_up="45000"), self.data_root
        )

        self.assertEqual(settings.REFUSED, outcome.state)
        self.assertIn("未知欄位", outcome.message)
        self.assertIn("warm_up", outcome.message)

    def test_a_whole_group_nobody_translated_keeps_its_own_path(self):
        self.add(confidence__downgrades__stale_evidence={"levels": 1})

        legends = re.findall(r"<legend>(.*?)</legend>", self.get("/settings").body)
        mine = [text for text in legends if "stale_evidence" in text]

        self.assertEqual(1, len(mine), legends)
        self.assertIn(settings.UNTRANSLATED_NOTE, mine[0])

    def test_the_translated_groups_beside_it_are_unaffected(self):
        """FP 方向：一個沒翻譯的分組不會把旁邊翻好的分組一起打回原形。"""
        self.add(confidence__downgrades__stale_evidence={"levels": 1})

        legends = re.findall(r"<legend>(.*?)</legend>", self.get("/settings").body)

        self.assertIn("降級：低可信來源", legends)
        self.assertIn("時間軸", legends)


# -- Ticket 12: after the fact ----------------------------------------------


def quote_for(price, day="2026-08-08", priced_on=None, source="stooq-daily"):
    """One canned quote, so no test needs a network or a real response shape."""
    return Quote(
        asset_class=ASSET_CLASS_CRYPTO,
        symbol="BTC",
        day=day,
        priced_on=priced_on or day,
        close=price,
        source=source,
        url="https://example.invalid/{}".format(day),
        summary="Date,Close / {},{}".format(day, price),
    )


# The two days the crypto fixture below is actually priced on. They are *not*
# the calendar dates of its start (2026-08-01T02:00Z) and deadline
# (2026-08-08T02:00Z): a daily close is asked for only once it has printed, and
# at 02:00Z on a day, that day's close has not. So the baseline is the day
# before the start and the settle the day before the deadline. Naming them
# stops a reader reading the fixture as "the start and the deadline" — the
# reading the code used to have, and the defect this pair exists to hold shut.
BASELINE_DAY = "2026-07-31"
SETTLE_DAY = "2026-08-07"


class FakeQuotes:
    """A price per (symbol, day), and a record of what was asked for.

    Anything not put in here raises, which is what a real service that has no
    price for a symbol does — and is how the tests below reach the "no price,
    so no verdict" path without pretending to be offline.
    """

    def __init__(self, prices=None, failure=None):
        self.prices = dict(prices or {})
        self.failure = failure
        self.asked = []

    def __call__(self, asset_class, symbol, day, **_options):
        self.asked.append((asset_class, symbol, day.isoformat()))
        if self.failure is not None:
            raise self.failure
        key = (symbol, day.isoformat())
        if key not in self.prices:
            raise QuoteUnavailableError(
                "測試替身沒有 {} 在 {} 的報價。".format(symbol, day.isoformat())
            )
        return quote_for(self.prices[key], day=day.isoformat())


class OutcomeFixture(PageFixture):
    """A Data Root whose runs can be made to expire on a clock the test owns."""

    START = "2026-08-01T02:00:00Z"
    DUE = datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)
    AFTER_DUE = datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc)
    BEFORE_DUE = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
    PERIOD_DAYS = 7

    def expired_run(self, run_id, question, **options):
        """Write a finished run whose period has a date and has run out.

        Every run a manual verdict is entered for goes through here, because a
        hand-entered outcome is refused for a run that is not finished or not
        due — and a fixture that quietly did not say when it expired would test
        that refusal instead of whatever the test was about.
        """
        options.setdefault("created_at_utc", self.START)
        options.setdefault("period_days", self.PERIOD_DAYS)
        run_dir = write_run(self.data_root, run_id, question, **options)
        rebuild_index(self.data_root)
        return run_dir

    def write_market_run(
        self,
        run_id="20260801T020000Z-btc-aaaa11",
        *,
        adopted="bullish",
        assets=("BTC",),
        asset_class=ASSET_CLASS_CRYPTO,
        level="green",
        period_days=7,
        created_at_utc=None,
        question="BTC 未來七天會不會漲",
    ):
        """A finished single-asset market run with a period that can run out."""
        run_dir = write_run(
            self.data_root,
            run_id,
            question,
            assets=assets,
            asset_class=asset_class,
            level=level,
            adopted=adopted,
            tally={stance: 0 for stance in MARKET_STANCES} | {adopted: 6},
            created_at_utc=self.START if created_at_utc is None else created_at_utc,
            period_days=period_days,
        )
        rebuild_index(self.data_root)
        return run_dir

    def sweep(self, now=None, quotes=None, limit=None):
        options = {} if limit is None else {"limit": limit}
        return outcome_module.sweep_due_runs(
            self.data_root,
            now=now or self.AFTER_DUE,
            quote=quotes if quotes is not None else FakeQuotes(),
            log=self.log,
            **options
        )

    def record(self, run_dir):
        path = Path(run_dir) / OUTCOME_RECORD_NAME
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def indexed_outcome(self, run_id):
        rows = [row for row in query_runs(self.data_root) if row["run_id"] == run_id]
        self.assertEqual(1, len(rows), rows)
        return rows[0]["outcome"]


class ExpirySweepTest(OutcomeFixture, unittest.TestCase):
    """A prediction whose period ran out is checked against the market."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def test_a_run_that_has_not_expired_yet_is_left_alone(self):
        run_dir = self.write_market_run()

        summary = self.sweep(now=self.BEFORE_DUE, quotes=FakeQuotes())

        self.assertIsNone(self.record(run_dir))
        self.assertEqual(0, summary["recorded"])
        self.assertEqual(1, summary["not_due"])

    def test_a_run_expiring_exactly_now_is_due(self):
        """The boundary is inclusive; a period that has run out has run out."""
        run_dir = self.write_market_run()

        self.sweep(
            now=self.DUE,
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0}),
        )

        self.assertIsNotNone(self.record(run_dir))

    def test_a_bullish_run_that_rose_is_a_hit(self):
        run_dir = self.write_market_run(adopted="bullish")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )
        record = self.record(run_dir)

        self.assertEqual(OUTCOME_HIT, record["verdict"])
        self.assertEqual("up", record["actual_direction"])
        self.assertEqual(100.0, record["baseline"]["price"])
        self.assertEqual(120.0, record["settle"]["price"])

    def test_a_bullish_run_that_fell_is_a_miss(self):
        run_dir = self.write_market_run(adopted="bullish")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 80.0})
        )

        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])
        self.assertEqual("down", self.record(run_dir)["actual_direction"])

    def test_a_bearish_run_that_fell_is_a_hit(self):
        run_dir = self.write_market_run(adopted="bearish")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 80.0})
        )

        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])

    def test_a_bearish_run_that_rose_is_a_miss(self):
        run_dir = self.write_market_run(adopted="bearish")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )

        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])

    def test_the_record_cites_the_two_prices_days_source_and_raw_answer(self):
        run_dir = self.write_market_run()

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )
        record = self.record(run_dir)

        for side in ("baseline", "settle"):
            self.assertIn("price", record[side])
            self.assertIn("day", record[side])
            self.assertIn("priced_on", record[side])
            self.assertIn("source", record[side])
            self.assertIn("summary", record[side])
        self.assertEqual("stooq-daily", record["source"])
        self.assertEqual("auto", record["recorded_by"])
        self.assertTrue(record["recorded_at_utc"])
        self.assertEqual("bullish", record["predicted_stance"])
        self.assertEqual(self.RUN_ID, record["run_id"])

    def test_each_end_is_priced_on_the_last_close_that_had_already_printed(self):
        """Neither price may carry information from after the instant it dates.

        The run starts 2026-08-01T02:00Z and is due 2026-08-08T02:00Z. Asking
        for those two *calendar dates* would take both closes from bars that
        print at the end of those days — hours after the prediction was made,
        and hours after the deadline it is scored at. The days asked for are
        therefore the ones before: the last closes that existed at each instant.
        """
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})

        self.sweep(quotes=quotes)

        self.assertEqual(
            [
                (ASSET_CLASS_CRYPTO, "BTC", BASELINE_DAY),
                (ASSET_CLASS_CRYPTO, "BTC", SETTLE_DAY),
            ],
            quotes.asked,
        )

    def test_neither_end_is_the_calendar_date_of_its_own_instant(self):
        """FN direction: the two contracts have to be tellable apart.

        Spelled as the dates the old contract used, so a change back to
        ``started.date()``/``due.date()`` fails here by name rather than by an
        expectation somebody could rewrite without seeing what it meant.
        """
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})

        self.sweep(quotes=quotes)

        asked_days = [day for _class, _symbol, day in quotes.asked]
        self.assertNotIn("2026-08-01", asked_days)
        self.assertNotIn("2026-08-08", asked_days)

    def test_the_index_is_updated_so_the_statistics_page_can_see_it(self):
        self.write_market_run()

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )

        self.assertEqual(OUTCOME_HIT, self.indexed_outcome(self.RUN_ID))

    def test_an_unchanged_price_is_a_miss_for_a_directional_call(self):
        """Neither 偏多 nor 偏空 came true; saying so is not the same as no answer."""
        run_dir = self.write_market_run(adopted="bullish")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 100.0})
        )

        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])
        self.assertEqual("flat", self.record(run_dir)["actual_direction"])

    def test_a_run_already_checked_is_not_checked_again(self):
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        self.sweep(quotes=quotes)
        asked_once = list(quotes.asked)

        summary = self.sweep(quotes=quotes)

        self.assertEqual(asked_once, quotes.asked)
        self.assertEqual(0, summary["recorded"])

    def test_one_pass_checks_no_more_runs_than_it_is_allowed_to(self):
        """A page that fetches prices must not fetch an unbounded number."""
        for index in range(4):
            write_run(
                self.data_root,
                "2026080{}T020000Z-btc-aaaa1{}".format(index + 1, index),
                "BTC 未來七天會不會漲",
                assets=("BTC",),
                asset_class=ASSET_CLASS_CRYPTO,
                adopted="bullish",
                tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
                created_at_utc="2026-08-0{}T02:00:00Z".format(index + 1),
                period_days=1,
            )
        rebuild_index(self.data_root)
        quotes = FakeQuotes(failure=QuoteUnavailableError("沒有報價"))

        summary = self.sweep(now=self.AFTER_DUE, quotes=quotes, limit=2)

        self.assertEqual(2, summary["checked"])

    def test_the_default_cap_is_a_declared_number(self):
        self.assertGreater(outcome_module.MAX_SWEEP_RUNS, 0)


class SweepReachesEveryPendingRunTest(OutcomeFixture, unittest.TestCase):
    """A cap defers work; it must not park a run out of reach for good.

    The index answers newest first, so before the rotation the same newest few
    runs filled the cap on every pass. Two of them failing to quote for ever is
    not exotic — a delisted symbol does it — and the run behind them was then
    never checked at all, however many times the page was opened.
    """

    OLDEST = "20260801T020000Z-btc-old001"
    FAILING = ("20260802T020000Z-bad-bad002", "20260803T020000Z-bad-bad003")

    class OnlyOneSymbolHasPrices:
        """Prices for ``BTC`` only; every other symbol raises, always."""

        def __init__(self):
            self.asked = []

        def __call__(self, asset_class, symbol, day, **_options):
            self.asked.append(symbol)
            if symbol != "BTC":
                raise QuoteUnavailableError("{} 沒有報價".format(symbol))
            return quote_for(100.0 if day.isoformat() == BASELINE_DAY else 120.0)

    def three_due_runs(self):
        """The oldest quotes fine; the two newer ones never will."""
        self.expired_run(
            self.OLDEST, "BTC 未來七天會不會漲", assets=("BTC",),
            asset_class=ASSET_CLASS_CRYPTO, adopted="bullish",
            tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
        )
        for index, run_id in enumerate(self.FAILING):
            self.expired_run(
                run_id, "BAD{} 未來七天會不會漲".format(index + 2),
                assets=("BAD{}".format(index + 2),),
                asset_class=ASSET_CLASS_CRYPTO, adopted="bullish",
                tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
            )

    def test_the_oldest_run_is_reached_even_though_the_cap_is_always_full(self):
        self.three_due_runs()
        quotes = self.OnlyOneSymbolHasPrices()

        for _pass in range(2):
            self.sweep(quotes=quotes, limit=2)

        self.assertEqual(OUTCOME_HIT, outcome_verdict(resolve_run_dir(self.data_root, self.OLDEST)))

    def test_the_run_that_was_reached_is_the_one_the_first_pass_skipped(self):
        """The cap really was full both times; nothing got in for free."""
        self.three_due_runs()
        quotes = self.OnlyOneSymbolHasPrices()

        first = self.sweep(quotes=quotes, limit=2)
        asked_first = list(quotes.asked)
        self.sweep(quotes=quotes, limit=2)

        self.assertEqual(2, first["checked"])
        self.assertEqual(2, first["quote_failed"])
        self.assertNotIn("BTC", asked_first)
        self.assertIn("BTC", quotes.asked)

    def test_one_pass_still_begins_at_the_newest_pending_run(self):
        """FP direction: rotating must not turn into 'oldest first' by accident."""
        self.three_due_runs()
        quotes = self.OnlyOneSymbolHasPrices()

        self.sweep(quotes=quotes, limit=1)

        self.assertEqual(["BAD3"], quotes.asked)

    def test_a_pass_that_reached_the_end_wraps_round_to_the_front(self):
        self.three_due_runs()
        quotes = self.OnlyOneSymbolHasPrices()

        for _pass in range(3):
            self.sweep(quotes=quotes, limit=1)

        self.assertEqual(["BAD3", "BAD2", "BTC", "BTC"], quotes.asked)

    def test_a_cursor_that_will_not_read_is_reported_and_the_sweep_still_runs(self):
        """Swallowed here would mean silently losing the fairness it buys."""
        self.three_due_runs()
        (self.data_root / outcome_module.SWEEP_CURSOR_NAME).write_text(
            "{ 壞掉了", encoding="utf-8"
        )

        summary = self.sweep(quotes=self.OnlyOneSymbolHasPrices(), limit=2)

        self.assertEqual(2, summary["checked"])
        self.assertIn(
            "outcome_sweep_cursor_unreadable", [r["event"] for r in self.records()]
        )

    def test_a_cursor_that_is_valid_json_of_the_wrong_shape_is_reported(self):
        """Parsing is not the question; carrying a position is.

        ``{}`` and ``[]`` parse perfectly and hold no position, so they reset the
        rotation exactly as a corrupt file does. Only one of the two used to say
        anything, which meant the quieter half of the same failure was the half
        nobody would ever find.
        """
        self.three_due_runs()
        for payload in ("{}", "[]", '{"after_run_id": null}', '{"after_run_id": ""}', '"字串"', "7"):
            with self.subTest(payload):
                (self.data_root / outcome_module.SWEEP_CURSOR_NAME).write_text(
                    payload, encoding="utf-8"
                )
                before = len(self.records())

                self.sweep(quotes=self.OnlyOneSymbolHasPrices(), limit=1)

                self.assertIn(
                    "outcome_sweep_cursor_invalid",
                    [r["event"] for r in self.records()[before:]],
                )

    def test_a_cursor_path_that_is_not_a_regular_file_is_reported(self):
        """``is_file()`` reads a directory as "absent", which is the quietest lie."""
        self.three_due_runs()
        (self.data_root / outcome_module.SWEEP_CURSOR_NAME).mkdir()

        self.sweep(quotes=self.OnlyOneSymbolHasPrices(), limit=1)

        self.assertIn(
            "outcome_sweep_cursor_invalid", [r["event"] for r in self.records()]
        )

    def test_the_cursor_is_not_written_under_a_run_directory(self):
        """It is this module's bookkeeping, not a run artifact."""
        self.three_due_runs()

        self.sweep(quotes=self.OnlyOneSymbolHasPrices(), limit=2)

        self.assertTrue((self.data_root / outcome_module.SWEEP_CURSOR_NAME).is_file())
        self.assertEqual(
            [], list((self.data_root / "runs").rglob(outcome_module.SWEEP_CURSOR_NAME))
        )


class EveryPendingRunIsEventuallyCheckedTest(OutcomeFixture, unittest.TestCase):
    """The invariant, held against both ways the cursor stops working.

    A persistent cursor is one mechanism for "every pending run is eventually
    checked", not the property itself, and it has exactly two failure modes. Each
    used to end in the same silent place — the next pass starts from the head of
    the queue for ever, so the oldest pending run keeps a ``verdict`` of ``None``
    however many times the page is opened. Fixing one of them leaves the other
    open, because the thing they break is not the cursor, it is the invariant.
    """

    OLDEST = "20260801T020000Z-btc-old001"
    FAILING = (
        "20260802T020000Z-bad-bad002",
        "20260803T020000Z-bad-bad003",
        "20260804T020000Z-bad-bad004",
    )
    SYMBOLS = {
        OLDEST: "BTC",
        FAILING[0]: "BAD2",
        FAILING[1]: "BAD3",
        FAILING[2]: "BAD4",
    }

    def due_runs(self, run_ids):
        """Write one expired run per id; only ``BTC`` will ever quote."""
        for run_id in run_ids:
            symbol = self.SYMBOLS[run_id]
            self.expired_run(
                run_id, "{} 未來七天會不會漲".format(symbol), assets=(symbol,),
                asset_class=ASSET_CLASS_CRYPTO, adopted="bullish",
                tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
            )

    def only_the_oldest_quotes(self):
        return SweepReachesEveryPendingRunTest.OnlyOneSymbolHasPrices()

    def oldest_verdict(self):
        return outcome_verdict(resolve_run_dir(self.data_root, self.OLDEST))

    def events(self):
        return [record["event"] for record in self.records()]

    # -- Reviewer A's direction: the cursor's run leaves the pending set -------

    def test_a_cursor_whose_run_was_recorded_resumes_at_its_place_not_the_head(self):
        """A position outlives the entry that defined it.

        Looking ``after`` up in the list answers "not found" the moment somebody
        records that run by hand, and "not found" was read as "no cursor" — so the
        rotation went back to the head and the run at the back was passed over
        again. Nobody has to provoke this: recording an outcome by hand is a
        button on the page.
        """
        self.due_runs((self.OLDEST,) + self.FAILING)
        quotes = self.only_the_oldest_quotes()
        self.sweep(quotes=quotes, limit=2)
        cursor = json.loads(
            (self.data_root / outcome_module.SWEEP_CURSOR_NAME).read_text("utf-8")
        )[outcome_module.SWEEP_CURSOR_FIELD]
        self.assertEqual(self.FAILING[1], cursor)
        outcome_module.record_manual_outcome(
            self.data_root, cursor, OUTCOME_HIT, now=self.AFTER_DUE
        )

        self.sweep(quotes=quotes, limit=2)

        self.assertEqual(OUTCOME_HIT, self.oldest_verdict())

    def test_the_run_the_cursor_named_is_really_gone_from_the_queue(self):
        """FP direction: the test above must not pass because nothing moved."""
        self.due_runs((self.OLDEST,) + self.FAILING)
        quotes = self.only_the_oldest_quotes()
        self.sweep(quotes=quotes, limit=2)
        outcome_module.record_manual_outcome(
            self.data_root, self.FAILING[1], OUTCOME_HIT, now=self.AFTER_DUE
        )

        pending = [
            row["run_id"] for row in query_runs(self.data_root) if row["outcome"] is None
        ]

        self.assertNotIn(self.FAILING[1], pending)
        self.assertEqual(3, len(pending))

    # -- Reviewer B's direction: the cursor cannot be persisted at all ---------

    def test_a_cursor_that_cannot_be_written_does_not_strand_the_back_of_the_queue(self):
        """No memory between passes means this pass has to be the one that covers.

        The cap is only a deferral because the cursor makes the next pass resume
        behind it. With no cursor there is no next pass to resume, so a cap is not
        a deferral any more — it is a run nobody ever checks. One pass, and the
        oldest run has a verdict.
        """
        self.due_runs((self.OLDEST,) + self.FAILING)
        (self.data_root / outcome_module.SWEEP_CURSOR_NAME).mkdir()

        summary = self.sweep(quotes=self.only_the_oldest_quotes(), limit=2)

        self.assertEqual(OUTCOME_HIT, self.oldest_verdict())
        self.assertEqual(4, summary["checked"])
        self.assertIn("outcome_sweep_cursor_failed", self.events())
        self.assertIn("outcome_sweep_uncapped", self.events())

    def test_the_degraded_pass_says_what_it_cost(self):
        """A mode with a price on it has to name the price, or nobody can weigh it."""
        self.due_runs((self.OLDEST,) + self.FAILING)
        (self.data_root / outcome_module.SWEEP_CURSOR_NAME).mkdir()

        self.sweep(quotes=self.only_the_oldest_quotes(), limit=2)

        [uncapped] = [r for r in self.records() if r["event"] == "outcome_sweep_uncapped"]
        self.assertEqual("WARNING", uncapped["level"])
        self.assertIn("不設上限", uncapped["message"])
        self.assertIn("慢", uncapped["message"])

    def test_a_cursor_that_writes_fine_keeps_its_cap(self):
        """FP direction: lifting the cap is the exception, never the ordinary pass.

        A sweep that quietly stopped capping itself would answer the two tests
        above and turn every statistics page view into an unbounded run of network
        calls.
        """
        self.due_runs((self.OLDEST,) + self.FAILING)

        summary = self.sweep(quotes=self.only_the_oldest_quotes(), limit=2)

        self.assertEqual(2, summary["checked"])
        self.assertIsNone(self.oldest_verdict())
        self.assertNotIn("outcome_sweep_uncapped", self.events())

    def test_a_pass_with_nothing_pending_writes_no_cursor(self):
        """An empty pass has no place in the rotation to remember, and no unfairness.

        This is the boundary between "the cursor could not be written" and "there
        was nothing to write": only the first one may lift the cap, and telling
        them apart is why :func:`_write_sweep_cursor` is asked at all rather than
        just called.
        """
        self.due_runs((self.OLDEST,))
        outcome_module.record_manual_outcome(
            self.data_root, self.OLDEST, OUTCOME_HIT, now=self.AFTER_DUE
        )

        summary = self.sweep(quotes=self.only_the_oldest_quotes())

        self.assertEqual(0, summary["checked"])
        self.assertFalse((self.data_root / outcome_module.SWEEP_CURSOR_NAME).exists())
        self.assertNotIn("outcome_sweep_uncapped", self.events())

    def test_a_pass_that_only_found_runs_not_yet_due_still_keeps_its_place(self):
        """Examining a run is what advances the rotation, not judging one.

        A pass that walked past a hundred runs whose period has not run out has
        walked past them; starting the next pass at the same hundred is the same
        starvation with a different cause.
        """
        self.due_runs((self.OLDEST,) + self.FAILING)

        self.sweep(now=self.BEFORE_DUE, quotes=self.only_the_oldest_quotes(), limit=2)

        self.assertTrue((self.data_root / outcome_module.SWEEP_CURSOR_NAME).is_file())
        self.assertNotIn("outcome_sweep_uncapped", self.events())

    def test_the_rotation_order_does_not_depend_on_the_index_row_order(self):
        """The rotation is only fair over a sequence that is the same every pass.

        ``_rotated`` resumes at a *position* in the run-id order, so that order is
        taken from the ids rather than accepted from the index — a row whose
        indexed date disagreed with its id would otherwise move the position out
        from under the cursor between two passes.
        """
        pending = [self.FAILING[0], self.OLDEST, self.FAILING[2], self.FAILING[1]]

        self.assertEqual(
            [self.FAILING[2], self.FAILING[1], self.FAILING[0], self.OLDEST],
            outcome_module._rotated(sorted(pending, reverse=True), None),
        )
        self.assertEqual(
            [self.FAILING[0], self.OLDEST, self.FAILING[2], self.FAILING[1]],
            outcome_module._rotated(sorted(pending, reverse=True), self.FAILING[1]),
        )


class NoPriceIsNotAVerdictTest(OutcomeFixture, unittest.TestCase):
    """Ticket 12 §四之4: 「我不知道」不准變成一個編出來的答案."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def test_a_quote_failure_leaves_the_run_unverified(self):
        run_dir = self.write_market_run()

        summary = self.sweep(quotes=FakeQuotes(failure=QuoteUnavailableError("服務中斷")))

        self.assertIsNone(self.record(run_dir))
        self.assertIsNone(self.indexed_outcome(self.RUN_ID))
        self.assertEqual(1, summary["quote_failed"])
        self.assertEqual(0, summary["recorded"])

    def test_a_quote_failure_is_written_to_the_webapp_log(self):
        self.write_market_run()

        self.sweep(quotes=FakeQuotes(failure=QuoteUnavailableError("服務中斷")))

        failures = [r for r in self.records() if r["event"] == "outcome_quote_failed"]
        self.assertEqual(1, len(failures), self.records())
        self.assertIn("服務中斷", failures[0]["message"])
        self.assertIn(self.RUN_ID, failures[0]["message"])

    def test_a_settle_price_that_fails_does_not_leave_a_half_record(self):
        run_dir = self.write_market_run()

        self.sweep(quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0}))

        self.assertIsNone(self.record(run_dir))

    def test_a_failed_run_is_retried_on_the_next_pass(self):
        run_dir = self.write_market_run()
        self.sweep(quotes=FakeQuotes(failure=QuoteUnavailableError("服務中斷")))

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )

        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])

    def test_a_run_that_never_said_when_it_expires_is_not_guessed_at(self):
        run_dir = write_run(
            self.data_root,
            self.RUN_ID,
            "BTC 未來七天會不會漲",
            assets=("BTC",),
            asset_class=ASSET_CLASS_CRYPTO,
            adopted="bullish",
        )
        rebuild_index(self.data_root)

        summary = self.sweep(quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0}))

        self.assertIsNone(self.record(run_dir))
        self.assertEqual(1, summary["no_deadline"])

    def test_a_period_that_is_not_a_positive_number_is_not_guessed_at(self):
        for period in (0, -3, "seven", None):
            with self.subTest(period=period):
                fixture = tempfile.TemporaryDirectory()
                self.addCleanup(fixture.cleanup)
                root = Path(fixture.name)
                write_run(
                    root,
                    self.RUN_ID,
                    "BTC 未來七天會不會漲",
                    assets=("BTC",),
                    asset_class=ASSET_CLASS_CRYPTO,
                    adopted="bullish",
                    created_at_utc=self.START,
                    period_days=period,
                )
                rebuild_index(root)

                summary = outcome_module.sweep_due_runs(
                    root, now=self.AFTER_DUE, quote=FakeQuotes(), log=self.log
                )

                self.assertEqual(1, summary["no_deadline"])
                self.assertEqual(0, summary["recorded"])

    def test_a_start_time_that_will_not_parse_is_not_guessed_at(self):
        self.write_market_run(created_at_utc="昨天下午")

        summary = self.sweep(quotes=FakeQuotes())

        self.assertEqual(1, summary["no_deadline"])


class AQuoteIsCheckedBeforeItBecomesAPermanentRecordTest(
    OutcomeFixture, unittest.TestCase
):
    """注入的 quote 是**呼叫端的宣稱**，不是這段程式的性質。

    ``OutcomeCheck(quote=...)`` 與 ``sweep_due_runs(..., quote=...)`` 是明文支援的
    注入接縫，於是「這個 seam 可以信任」聽起來像一個性質。它不是——它是一句關於
    呼叫端的話，而 ``outcome.json`` write-once 且沒有任何後續紀錄能更正它。

    ``Quote.close`` 是 ``True`` 時 ``2.0 > True`` 一路成立，方向、verdict 與
    ``baseline.price`` 全部寫進磁碟，磁碟上留下的是一個 JSON 布林。抓回來的收盤價
    與手打的價格早就各自過了
    :func:`~hoya_market_agents.quote_api_client.is_usable_price`；第三條入口沒有
    理由自成一套。在永久紀錄的寫入邊界上多問一次要兩行，搞錯是永久的。
    """

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    # 不是價格的東西，寫成例子而不是清單：規則是 ``is_usable_price``，這裡只是
    # 幾個真的會從一個壞掉或說謊的 quote client 回來的形狀。``10**1000`` 在裡面
    # 是因為它以前不是「被拒絕」而是「讓整輪死掉」：它是 ``int``，過得了型別檢查，
    # 而 ``math.isfinite`` 轉 ``float`` 時丟 ``OverflowError``。
    NOT_PRICES = (
        True, False, 0, 0.0, -1.5, float("inf"), float("nan"), "7.0", None,
        10 ** 1000, -(10 ** 1000),
    )

    class ClosesTheTestChose:
        """回兩個 ``Quote``，``close`` 完全由測試決定，包括不是價格的那些。"""

        def __init__(self, baseline, settle):
            self.closes = (baseline, settle)
            self.asked = []

        def __call__(self, asset_class, symbol, day, **_options):
            self.asked.append((symbol, day.isoformat()))
            return quote_for(self.closes[len(self.asked) - 1], day=day.isoformat())

    def test_a_close_that_is_not_a_price_writes_nothing_and_leaves_it_pending(self):
        """被回報的第三條入口。兩端各驗一次，因為兩端都會被拿去比大小。

        什麼都沒寫，所以整組共用同一個 run：每一輪之後它都還是 pending，這件事本
        身就是斷言的一部分。
        """
        run_dir = self.write_market_run()
        for position, side in ((0, "baseline"), (1, "settle")):
            for value in self.NOT_PRICES:
                with self.subTest(side=side, close=repr(value)):
                    closes = [100.0, 120.0]
                    closes[position] = value
                    quotes = self.ClosesTheTestChose(*closes)

                    summary = self.sweep(quotes=quotes)

                    self.assertIsNone(self.record(run_dir))
                    self.assertIsNone(self.indexed_outcome(self.RUN_ID))
                    self.assertEqual(1, summary["quote_failed"])
                    self.assertEqual(0, summary["recorded"])

    def test_the_refusal_says_which_end_and_what_the_value_was(self):
        """一句「取價失敗」對這件事不夠：讀 log 的人要能認出是誰給了什麼。"""
        self.write_market_run()

        self.sweep(quotes=self.ClosesTheTestChose(True, 120.0))

        [refused] = [
            r for r in self.records() if r["event"] == "outcome_quote_not_a_price"
        ]
        self.assertEqual("ERROR", refused["level"])
        self.assertIn(self.RUN_ID, refused["message"])
        self.assertIn("baseline", refused["message"])
        self.assertIn("True", refused["message"])

    def test_both_ends_being_ordinary_prices_still_writes_the_record(self):
        """FP 方向：新的關卡不得把真的價格擋掉。

        ``0.00000001`` 與 ``1.0`` 都在裡面：極小值是合法價格，而 ``1.0`` 正是
        ``float(True)`` 的值——擋的必須是型別，不是那個數字。
        """
        for baseline, settle, verdict in (
            (100.0, 120.0, OUTCOME_HIT),
            (1.0, 0.00000001, OUTCOME_MISS),
            (0.00000001, 1.0, OUTCOME_HIT),
        ):
            with self.subTest(baseline=baseline, settle=settle):
                self.setUp()
                run_dir = self.write_market_run()

                summary = self.sweep(quotes=self.ClosesTheTestChose(baseline, settle))

                self.assertEqual(verdict, self.record(run_dir)["verdict"])
                self.assertEqual(baseline, self.record(run_dir)["baseline"]["price"])
                self.assertEqual(1, summary["recorded"])


class AGuardThatCanDetonateIsAStarvationTest(OutcomeFixture, unittest.TestCase):
    """上一輪為第三條入口加的那道關卡，自己變成了新的例外來源。

    ``_priced_payload`` 的 ``is_usable_price`` 迴圈**不在** ``quote()`` 的 try
    裡面——它就在那道 ``except Exception`` 的下面。所以關卡自己丟出來的例外沒有
    任何人接：它穿過 ``sweep_due_runs``，在 ``_write_sweep_cursor`` 執行**之前**
    結束整輪，游標沒動，下一輪從同一筆開始，再爆一次。

    這正是 :class:`OneRunsQuoteFailureIsOneRunsTest` 修掉的那個 starvation，
    由**修它的那道守衛**重新帶回來。觸發它不需要惡意的呼叫端，只需要一個
    ``int``：``10**1000`` 過得了 ``isinstance(value, (int, float))``，然後
    ``math.isfinite`` 把它轉 ``float`` 時丟 ``OverflowError``。

    所以這裡問的不是「``10**1000`` 有沒有被擋掉」——那是隔壁那個類別的事——而是
    「rotation 有沒有繼續走」。兩件事要分開驗，因為只修其中一件的實作會讓另一件
    看起來還是好的。
    """

    OLDEST = "20260801T020000Z-btc-old001"
    OTHERS = (
        "20260802T020000Z-bad-bad002",
        "20260803T020000Z-bad-bad003",
        "20260804T020000Z-bad-bad004",
    )
    SYMBOLS = {
        OLDEST: "BTC",
        OTHERS[0]: "BAD2",
        OTHERS[1]: "BAD3",
        OTHERS[2]: "BAD4",
    }

    class OnlyBtcQuotesTheRestOverflowTheGuard:
        """BTC 有價；其餘每一次都回一個大到 ``float`` 裝不下的 ``close``。

        兩端都取價成功——失敗的是關卡本身，不是取價。這和隔壁那個「在 ``read``
        半途斷線」的 client 是**不同的**故障點，所以兩個都要有。
        """

        def __init__(self):
            self.asked = []

        def __call__(self, asset_class, symbol, day, **_options):
            self.asked.append(symbol)
            if symbol != "BTC":
                return quote_for(10 ** 1000, day=day.isoformat())
            return quote_for(
                100.0 if day.isoformat() == BASELINE_DAY else 120.0,
                day=day.isoformat(),
            )

    def setUp(self):
        super().setUp()
        for run_id in (self.OLDEST,) + self.OTHERS:
            symbol = self.SYMBOLS[run_id]
            self.expired_run(
                run_id, "{} 未來七天會不會漲".format(symbol), assets=(symbol,),
                asset_class=ASSET_CLASS_CRYPTO, adopted="bullish",
                tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
            )

    def check(self, quotes, limit=1):
        """走 :class:`OutcomeCheck` 那條真正的路，理由與隔壁類別相同。"""
        return outcome_module.OutcomeCheck(
            now=lambda: self.AFTER_DUE, quote=quotes, limit=limit, log=self.log
        ).run(self.data_root)

    def events(self):
        return [record["event"] for record in self.records()]

    def test_four_passes_move_through_the_rotation_instead_of_repeating_one_run(self):
        """被回報的重現：四輪全部停在 ``BAD4``，最舊的那筆永遠等不到。

        壞掉的三筆各問兩次，因為兩端都取價成功了才輪到關卡去炸。
        """
        quotes = self.OnlyBtcQuotesTheRestOverflowTheGuard()

        for _pass in range(4):
            self.check(quotes)

        self.assertEqual(
            ["BAD4", "BAD4", "BAD3", "BAD3", "BAD2", "BAD2", "BTC", "BTC"],
            quotes.asked,
        )
        self.assertEqual(
            OUTCOME_HIT, outcome_verdict(resolve_run_dir(self.data_root, self.OLDEST))
        )

    def test_the_pass_survives_and_its_cursor_moves(self):
        """被回報的三件事一起驗：``summary`` 不是 ``None``、游標存在、游標有動。"""
        quotes = self.OnlyBtcQuotesTheRestOverflowTheGuard()

        summary = self.check(quotes)

        self.assertIsNotNone(summary)
        self.assertEqual(1, summary["checked"])
        self.assertEqual(1, summary["quote_failed"])
        self.assertNotIn("outcome_sweep_failed", self.events())
        cursor_path = self.data_root / outcome_module.SWEEP_CURSOR_NAME
        self.assertTrue(cursor_path.is_file())
        self.assertEqual(
            self.OTHERS[2],
            json.loads(cursor_path.read_text("utf-8"))[
                outcome_module.SWEEP_CURSOR_FIELD
            ],
        )

    def test_the_refusal_is_reported_as_a_value_not_as_a_crash(self):
        """隔離不是吞掉，而且要記成對的那一種事件。

        ``outcome_quote_unexpected``（關卡爆了）與 ``outcome_quote_not_a_price``
        （關卡說不是價格）是兩件事。一個把例外往上丟、被外層 ``except Exception``
        撿走的實作也會讓上面兩條變綠，但 log 會說錯故事。
        """
        self.check(self.OnlyBtcQuotesTheRestOverflowTheGuard())

        [refused] = [
            r for r in self.records() if r["event"] == "outcome_quote_not_a_price"
        ]
        self.assertEqual("ERROR", refused["level"])
        self.assertIn(self.OTHERS[2], refused["message"])
        self.assertIn("baseline", refused["message"])
        self.assertNotIn("outcome_quote_unexpected", self.events())


class OneRunsQuoteFailureIsOneRunsTest(OutcomeFixture, unittest.TestCase):
    """單筆 run 的未知報價失敗不得中止整輪，也不得擋住 cursor 前進。

    這不是合成的例外。``HTTPResponse.read()`` 在連線傳到一半被切掉時丟
    :class:`http.client.IncompleteRead`，它繼承 :class:`Exception` 而不是
    ``OSError``，於是它穿過 ``daily_close``、穿過 ``_priced_payload``、穿過
    ``sweep_due_runs``，在 ``_write_sweep_cursor`` **執行之前**結束整輪。游標沒
    動，下一輪從同一筆開始，再爆一次——第六種 starvation，而且是被外面的網路
    觸發的，沒有人需要去製造它。

    ``OutcomeCheck.run`` 那道 ``except Exception`` 不是解法：它把整輪的死亡記成
    一行 ``outcome_sweep_failed`` 然後回 ``None``，看起來很像有在處理，實際上正
    是讓它安靜地重複下去的那個地方。
    """

    OLDEST = "20260801T020000Z-btc-old001"
    OTHERS = (
        "20260802T020000Z-bad-bad002",
        "20260803T020000Z-bad-bad003",
        "20260804T020000Z-bad-bad004",
    )
    SYMBOLS = {
        OLDEST: "BTC",
        OTHERS[0]: "BAD2",
        OTHERS[1]: "BAD3",
        OTHERS[2]: "BAD4",
    }

    class OnlyBtcQuotesTheRestCutTheConnection:
        """BTC 有價；其餘每一次都在 ``read`` 半途斷線。"""

        def __init__(self):
            self.asked = []

        def __call__(self, asset_class, symbol, day, **_options):
            self.asked.append(symbol)
            if symbol != "BTC":
                raise http.client.IncompleteRead(b"Date,Close")
            return quote_for(
                100.0 if day.isoformat() == BASELINE_DAY else 120.0,
                day=day.isoformat(),
            )

    def setUp(self):
        super().setUp()
        for run_id in (self.OLDEST,) + self.OTHERS:
            symbol = self.SYMBOLS[run_id]
            self.expired_run(
                run_id, "{} 未來七天會不會漲".format(symbol), assets=(symbol,),
                asset_class=ASSET_CLASS_CRYPTO, adopted="bullish",
                tally={stance: 0 for stance in MARKET_STANCES} | {"bullish": 6},
            )

    def check(self, quotes, limit=1):
        """跑一輪，走 :class:`OutcomeCheck` 那條真正的路。

        直接呼叫 ``sweep_due_runs`` 會讓例外原樣冒出來，而被回報的行為是「頁面還
        在、log 有一行、下一輪從頭開始」——那是 ``OutcomeCheck.run`` 攔下來之後的
        樣子，所以要從那裡問。
        """
        return outcome_module.OutcomeCheck(
            now=lambda: self.AFTER_DUE, quote=quotes, limit=limit, log=self.log
        ).run(self.data_root)

    def events(self):
        return [record["event"] for record in self.records()]

    def test_four_passes_move_through_the_rotation_instead_of_repeating_one_run(self):
        """被回報的重現：四輪 ``asked`` 全是 ``BAD4``，最舊的那筆永遠等不到。"""
        quotes = self.OnlyBtcQuotesTheRestCutTheConnection()

        for _pass in range(4):
            self.check(quotes)

        # BTC 出現兩次是因為它是唯一一筆走到「兩端都取價」的 run；斷線的三筆在
        # baseline 那一次就結束了。
        self.assertEqual(["BAD4", "BAD3", "BAD2", "BTC", "BTC"], quotes.asked)
        self.assertEqual(
            OUTCOME_HIT, outcome_verdict(resolve_run_dir(self.data_root, self.OLDEST))
        )

    def test_the_pass_survives_and_its_cursor_moves(self):
        """三件事一起壞掉，所以一起驗：整輪沒死、游標有寫、下一輪不從頭。"""
        quotes = self.OnlyBtcQuotesTheRestCutTheConnection()

        summary = self.check(quotes)

        self.assertIsNotNone(summary)
        self.assertEqual(1, summary["checked"])
        self.assertEqual(1, summary["quote_failed"])
        self.assertNotIn("outcome_sweep_failed", self.events())
        self.assertEqual(
            self.OTHERS[2],
            json.loads(
                (self.data_root / outcome_module.SWEEP_CURSOR_NAME).read_text("utf-8")
            )[outcome_module.SWEEP_CURSOR_FIELD],
        )

    def test_the_failure_is_reported_as_what_it_was(self):
        """隔離不是吞掉：型別、訊息與是哪一筆 run 都要留在 log 裡。"""
        self.check(self.OnlyBtcQuotesTheRestCutTheConnection())

        [reported] = [
            r for r in self.records() if r["event"] == "outcome_quote_unexpected"
        ]
        self.assertEqual("ERROR", reported["level"])
        self.assertIn("IncompleteRead", reported["message"])
        self.assertIn(self.OTHERS[2], reported["message"])

    def test_one_pass_still_stops_at_its_cap(self):
        """FP 方向：繼續輪替不是「把上限拿掉」。

        少了這一條，一個把例外吃掉又順便不再理會 ``limit`` 的實作也會讓上面三條
        變綠，而那正是每次開統計頁都跑滿全部 pending 的那個實作。
        """
        quotes = self.OnlyBtcQuotesTheRestCutTheConnection()

        summary = self.check(quotes, limit=2)

        self.assertEqual(2, summary["checked"])
        self.assertEqual(["BAD4", "BAD3"], quotes.asked)
        self.assertNotIn("outcome_sweep_uncapped", self.events())


class NotEverythingCanBeCheckedTest(OutcomeFixture, unittest.TestCase):
    """Ticket 12 §⑤: 「不可自動驗證」是第四種狀態，不是「沒中」."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def sweep_and_read(self, **run_options):
        run_dir = self.write_market_run(**run_options)
        summary = self.sweep(quotes=FakeQuotes())
        return self.record(run_dir), summary

    def test_an_open_proposition_is_marked_unverifiable_rather_than_missed(self):
        record, summary = self.sweep_and_read(
            asset_class=ASSET_CLASS_OPEN, adopted="affirmative", assets=()
        )

        self.assertEqual(OUTCOME_UNVERIFIABLE, record["verdict"])
        self.assertEqual(1, summary["unverifiable"])

    def test_an_unverifiable_run_never_asks_for_a_price(self):
        self.write_market_run(asset_class=ASSET_CLASS_OPEN, adopted="affirmative", assets=())
        quotes = FakeQuotes()

        self.sweep(quotes=quotes)

        self.assertEqual([], quotes.asked)

    def test_a_comparison_between_two_assets_is_unverifiable_here(self):
        record, _ = self.sweep_and_read(
            assets=("BTC", "ETH"), adopted="asset_a_stronger"
        )

        self.assertEqual(OUTCOME_UNVERIFIABLE, record["verdict"])
        self.assertEqual(outcome_module.REASON_NOT_ONE_ASSET, record["reason"])

    def test_a_stance_with_no_price_direction_is_unverifiable(self):
        record, _ = self.sweep_and_read(adopted="neutral")

        self.assertEqual(OUTCOME_UNVERIFIABLE, record["verdict"])
        self.assertEqual(outcome_module.REASON_STANCE_NOT_DIRECTIONAL, record["reason"])

    def test_an_open_class_names_the_missing_source_as_its_reason(self):
        record, _ = self.sweep_and_read(
            asset_class=ASSET_CLASS_OPEN, adopted="affirmative", assets=()
        )

        self.assertEqual(outcome_module.REASON_NO_QUOTE_SOURCE, record["reason"])

    def test_every_reason_is_a_sentence_a_reader_can_be_shown(self):
        for reason in outcome_module.UNVERIFIABLE_REASONS:
            self.assertTrue(outcome_module.UNVERIFIABLE_REASONS[reason].strip(), reason)

    def test_a_quotable_class_with_one_directional_asset_is_not_unverifiable(self):
        """FP direction: the unverifiable branch has to be able to not fire."""
        record, summary = self.sweep_and_read(
            asset_class=ASSET_CLASS_TW_STOCK, assets=("2330",), adopted="bullish"
        )

        self.assertIsNone(record)
        self.assertEqual(0, summary["unverifiable"])
        self.assertEqual(1, summary["quote_failed"])

    def test_the_direction_table_covers_only_the_two_directional_stances(self):
        """Derived from the ballot, not from a list kept here."""
        self.assertEqual(
            {"bullish", "bearish"}, set(outcome_module.STANCE_DIRECTIONS)
        )
        self.assertTrue(set(outcome_module.STANCE_DIRECTIONS) < set(MARKET_STANCES))


class WriteOnceTest(OutcomeFixture, unittest.TestCase):
    """Ticket 12 §④: three states, three answers, and no overwrite ever."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def manual(self, verdict=OUTCOME_HIT, **options):
        return outcome_module.record_manual_outcome(
            self.data_root, self.RUN_ID, verdict, now=self.AFTER_DUE, **options
        )

    def test_a_run_with_no_record_accepts_one(self):
        run_dir = self.write_market_run()

        written = self.manual(note="我自己去看的")

        self.assertEqual(outcome_module.WRITTEN, written.state)
        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])
        self.assertEqual("manual", self.record(run_dir)["recorded_by"])
        self.assertEqual("我自己去看的", self.record(run_dir)["note"])

    def test_a_manual_record_reaches_the_index_too(self):
        self.write_market_run()

        self.manual(verdict=OUTCOME_MISS)

        self.assertEqual(OUTCOME_MISS, self.indexed_outcome(self.RUN_ID))

    def test_a_second_write_is_refused_and_says_what_is_already_there(self):
        self.write_market_run()
        self.manual(verdict=OUTCOME_HIT)

        again = self.manual(verdict=OUTCOME_MISS)

        self.assertEqual(outcome_module.ALREADY_RECORDED, again.state)
        self.assertEqual(OUTCOME_HIT, again.verdict)
        self.assertIn(OUTCOME_HIT, again.message)

    def test_a_refused_write_leaves_the_first_record_byte_for_byte(self):
        run_dir = self.write_market_run()
        self.manual(verdict=OUTCOME_HIT, note="第一次")
        before = (run_dir / OUTCOME_RECORD_NAME).read_bytes()

        self.manual(verdict=OUTCOME_MISS, note="第二次")

        self.assertEqual(before, (run_dir / OUTCOME_RECORD_NAME).read_bytes())

    def test_the_sweep_will_not_overwrite_a_manual_record_either(self):
        run_dir = self.write_market_run()
        self.manual(verdict=OUTCOME_MISS, note="人工判定")

        self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )

        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])
        self.assertEqual("人工判定", self.record(run_dir)["note"])

    def test_a_record_that_will_not_read_is_neither_accepted_nor_called_checked(self):
        """The third state. Reporting it as 尚未對答案 would let this be overwritten."""
        run_dir = self.write_market_run()
        (run_dir / OUTCOME_RECORD_NAME).write_text("{ 壞掉了", encoding="utf-8")
        before = (run_dir / OUTCOME_RECORD_NAME).read_bytes()

        written = self.manual(verdict=OUTCOME_HIT)

        self.assertEqual(outcome_module.RECORD_UNREADABLE, written.state)
        self.assertEqual(before, (run_dir / OUTCOME_RECORD_NAME).read_bytes())
        self.assertNotIn("尚未", written.message)

    def test_the_three_states_give_three_different_sentences(self):
        run_dir = self.write_market_run()
        absent = self.manual(verdict=OUTCOME_HIT).message
        present = self.manual(verdict=OUTCOME_HIT).message
        (run_dir / OUTCOME_RECORD_NAME).write_text("{ 壞掉了", encoding="utf-8")
        unreadable = self.manual(verdict=OUTCOME_HIT).message

        self.assertEqual(3, len({absent, present, unreadable}))

    def test_the_sweep_leaves_an_unreadable_record_alone(self):
        run_dir = self.write_market_run()
        (run_dir / OUTCOME_RECORD_NAME).write_text("{ 壞掉了", encoding="utf-8")
        before = (run_dir / OUTCOME_RECORD_NAME).read_bytes()

        summary = self.sweep(
            quotes=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        )

        self.assertEqual(before, (run_dir / OUTCOME_RECORD_NAME).read_bytes())
        self.assertEqual(0, summary["recorded"])

    def test_a_run_id_that_names_nothing_is_refused_without_writing(self):
        written = outcome_module.record_manual_outcome(
            self.data_root, "20261231T235959Z-btc-zzzz99", OUTCOME_HIT, now=self.AFTER_DUE
        )

        self.assertEqual(outcome_module.NO_SUCH_RUN, written.state)

    def test_a_verdict_outside_the_closed_set_is_refused(self):
        run_dir = self.write_market_run()

        written = self.manual(verdict="probably")

        self.assertEqual(outcome_module.REFUSED, written.state)
        self.assertIsNone(self.record(run_dir))

    def test_every_declared_verdict_can_be_entered_by_hand(self):
        for verdict in OUTCOME_VERDICTS:
            with self.subTest(verdict):
                fixture = tempfile.TemporaryDirectory()
                self.addCleanup(fixture.cleanup)
                root = Path(fixture.name)
                run_dir = write_run(
                    root,
                    self.RUN_ID,
                    "BTC 未來七天會不會漲",
                    assets=("BTC",),
                    created_at_utc=self.START,
                    period_days=self.PERIOD_DAYS,
                )
                rebuild_index(root)

                written = outcome_module.record_manual_outcome(
                    root, self.RUN_ID, verdict, now=self.AFTER_DUE
                )

                self.assertEqual(outcome_module.WRITTEN, written.state)
                self.assertEqual(
                    verdict, json.loads((run_dir / OUTCOME_RECORD_NAME).read_text("utf-8"))["verdict"]
                )

    def test_a_price_typed_by_hand_is_kept_and_a_bad_one_is_refused(self):
        run_dir = self.write_market_run()

        refused = self.manual(actual_price="很高")
        self.assertEqual(outcome_module.REFUSED, refused.state)
        self.assertIsNone(self.record(run_dir))

        accepted = self.manual(actual_price="123.5")
        self.assertEqual(outcome_module.WRITTEN, accepted.state)
        self.assertEqual(123.5, self.record(run_dir)["actual_price"])

    def test_a_record_written_by_hand_still_rebuilds_the_index(self):
        """The whole point of §②, exercised through the front door."""
        self.write_market_run()
        self.manual(verdict=OUTCOME_MISS)

        rebuild_index(self.data_root)

        self.assertEqual(OUTCOME_MISS, self.indexed_outcome(self.RUN_ID))

    def test_a_price_that_is_infinite_is_refused_like_any_other_non_price(self):
        """``float`` accepts ``inf``, and ``inf > 0`` is true; neither makes it a price."""
        run_dir = self.write_market_run()

        for typed in ("inf", "Infinity", "-inf", "nan", "1e400"):
            with self.subTest(typed):
                refused = self.manual(actual_price=typed)

                self.assertEqual(outcome_module.REFUSED, refused.state)
                self.assertIsNone(self.record(run_dir))

    def test_an_ordinary_price_still_goes_in(self):
        """FP direction: the finiteness check must not refuse real numbers."""
        run_dir = self.write_market_run()

        accepted = self.manual(actual_price="0.00000001")

        self.assertEqual(outcome_module.WRITTEN, accepted.state)
        self.assertEqual(1e-8, self.record(run_dir)["actual_price"])

    def test_true_does_not_become_the_price_one_on_this_path_either(self):
        """The second entrance to the same defect, and the reason it existed.

        ``is_usable_price(True)`` was already ``False``. It was asked *after*
        ``float(True)`` had turned ``True`` into ``1.0``, so it never saw a
        boolean and answered about a number that no longer remembered being one.
        The fetched close had the identical hole.

        What closed both was **not** moving that check earlier — it is the check
        that wants a number. It was putting a text grammar in front of it, so
        this form value and the CSV field both go
        ``is_decimal_numeral`` → ``float`` → ``is_usable_price`` with only the
        grammar before the conversion. Both refuse by type rather than by a list
        of types, which is the property this test is about.
        """
        run_dir = self.write_market_run()

        for typed in (True, False, 1, 1.0, Decimal("1.0")):
            with self.subTest(type(typed).__name__):
                refused = self.manual(actual_price=typed)

                self.assertEqual(outcome_module.REFUSED, refused.state)
                self.assertIsNone(self.record(run_dir))

    def test_form_text_that_float_would_have_misread_is_refused(self):
        """``"1_0"`` is ten to :func:`float`; nothing types a price that way."""
        run_dir = self.write_market_run()

        for typed in ("1_0", "١٢٣", "1,234.5"):
            with self.subTest(typed):
                refused = self.manual(actual_price=typed)

                self.assertEqual(outcome_module.REFUSED, refused.state)
                self.assertIsNone(self.record(run_dir))


class NothingIsJudgedBeforeItHappensTest(OutcomeFixture, unittest.TestCase):
    """``outcome.json`` is written once, so it may not be written early.

    A verdict entered while the prediction still has days to run cannot be
    taken back: the record that would correct it is the record already there.
    Both conditions — the run has finished, and its period has run out — are
    checked before anything is written, and the refusal says which one failed.
    """

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def manual(self, now, **options):
        return outcome_module.record_manual_outcome(
            self.data_root, self.RUN_ID, OUTCOME_HIT, now=now, **options
        )

    def test_a_run_whose_period_has_not_run_out_is_refused(self):
        run_dir = self.write_market_run()

        refused = self.manual(now=self.BEFORE_DUE)

        self.assertEqual(outcome_module.REFUSED, refused.state)
        self.assertIsNone(self.record(run_dir))
        self.assertFalse((run_dir / OUTCOME_RECORD_NAME).exists())

    def test_the_refusal_of_a_live_prediction_names_the_deadline_and_the_clock(self):
        self.write_market_run()

        refused = self.manual(now=self.BEFORE_DUE)

        self.assertIn("2026-08-08T02:00:00Z", refused.message)
        self.assertIn("2026-08-03T02:00:00Z", refused.message)

    def test_a_run_that_has_not_finished_is_refused(self):
        """No ``manifest.json``: the debate is still going, there is no call yet."""
        run_dir = self.write_market_run()
        (run_dir / "manifest.json").unlink()

        refused = self.manual(now=self.AFTER_DUE)

        self.assertEqual(outcome_module.REFUSED, refused.state)
        self.assertIn("manifest.json", refused.message)
        self.assertIsNone(self.record(run_dir))

    def test_the_two_refusals_are_not_the_same_sentence(self):
        """Which condition failed decides what a reader does next."""
        run_dir = self.write_market_run()
        not_due = self.manual(now=self.BEFORE_DUE).message
        (run_dir / "manifest.json").unlink()
        unfinished = self.manual(now=self.AFTER_DUE).message

        self.assertNotEqual(not_due, unfinished)

    def test_a_run_that_cannot_be_dated_is_refused_rather_than_assumed_over(self):
        """The sweep leaves these alone; the form must not walk around it."""
        write_run(
            self.data_root, self.RUN_ID, "BTC 未來七天會不會漲", assets=("BTC",)
        )
        rebuild_index(self.data_root)

        refused = self.manual(now=self.AFTER_DUE)

        self.assertEqual(outcome_module.REFUSED, refused.state)
        self.assertIn("period_days", refused.message)

    def test_a_finished_run_whose_period_ran_out_is_accepted(self):
        """FP direction: the gate has to be able to open."""
        run_dir = self.write_market_run()

        written = self.manual(now=self.AFTER_DUE)

        self.assertEqual(outcome_module.WRITTEN, written.state)
        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])

    def test_a_run_expiring_exactly_now_may_be_judged(self):
        """The same inclusive boundary the sweep uses, not a second reading."""
        run_dir = self.write_market_run()

        written = self.manual(now=self.DUE)

        self.assertEqual(outcome_module.WRITTEN, written.state)
        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])

    def test_the_form_does_not_offer_a_run_whose_period_is_still_running(self):
        self.write_market_run()

        data = views.history_data(
            self.data_root,
            {},
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: self.BEFORE_DUE, quote=FakeQuotes(), log=self.log
            ),
        )

        self.assertEqual(views.STATE_OK, data["state"])
        self.assertEqual([], data["pending_runs"])

    def test_the_form_offers_a_run_that_is_actually_waiting(self):
        """FP direction: the filter must not empty the list it exists to fill."""
        self.write_market_run()

        data = views.history_data(
            self.data_root,
            {},
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: self.AFTER_DUE,
                quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
                log=self.log,
            ),
        )

        self.assertEqual([self.RUN_ID], [row["run_id"] for row in data["pending_runs"]])

    def test_the_page_says_which_runs_the_form_takes(self):
        """The rule is on screen, not only in the refusal a user runs into.

        The clock and the quote source are the test's, because the page that shows
        this form is now also the page that sweeps: left to the defaults this
        request would read the wall clock and ask a real service for a price.
        """
        self.write_market_run()
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn,
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: self.AFTER_DUE,
                quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
                log=self.log,
            ),
        )

        body = self.get("/history").body

        self.assertIn("已到期", body)
        self.assertIn("到期", body)

    def test_a_truncated_list_says_it_was_truncated(self):
        """Showing 20 of 30 without saying so is a page a reader would misread."""
        for index in range(pages.MANUAL_LIST_LIMIT + 3):
            self.write_market_run(
                run_id="20260801T{:02d}{:02d}00Z-btc-aaa{:03d}".format(
                    index // 60, index % 60, index
                ),
                question="第 {} 題 BTC 會不會漲".format(index),
            )
        check = outcome_module.OutcomeCheck(
            now=lambda: self.AFTER_DUE,
            quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
            log=self.log,
        )

        body = pages.render_history_page(
            views.history_data(self.data_root, {}, outcome_check=check)
        )

        self.assertIn(
            "共 {} 個，以下列出 {} 個".format(
                pages.MANUAL_LIST_LIMIT + 3, pages.MANUAL_LIST_LIMIT
            ),
            body,
        )

    def test_a_list_that_fits_does_not_claim_to_be_truncated(self):
        """FP direction: the caption must be able to say 'all of them'."""
        self.write_market_run()
        check = outcome_module.OutcomeCheck(
            now=lambda: self.AFTER_DUE,
            quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
            log=self.log,
        )

        body = pages.render_history_page(
            views.history_data(self.data_root, {}, outcome_check=check)
        )

        self.assertIn("共 1 個。", body)
        self.assertNotIn("以下列出", body)

    def test_the_form_and_the_write_agree_about_every_listed_run(self):
        """Whatever the page offers, a submission of it must not be refused."""
        self.write_market_run()
        self.write_market_run(
            run_id="20260801T030000Z-btc-bbbb22", question="另一題 BTC 會不會漲"
        )
        check = outcome_module.OutcomeCheck(
            now=lambda: self.AFTER_DUE,
            quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
            log=self.log,
        )

        listed = views.history_data(self.data_root, {}, outcome_check=check)[
            "pending_runs"
        ]

        self.assertEqual(2, len(listed))
        for row in listed:
            with self.subTest(row["run_id"]):
                written = outcome_module.record_manual_outcome(
                    self.data_root, row["run_id"], OUTCOME_HIT, now=self.AFTER_DUE
                )
                self.assertEqual(outcome_module.WRITTEN, written.state, written.message)


class StatsPageTest(OutcomeFixture, unittest.TestCase):
    """The hit rates: four states, and a rate that says its own maths.

    They are shown on ``/history`` since Ticket 04 merged the two pages; what is
    asserted here is unchanged, because merging the pages was not allowed to
    change any of it.
    """

    def three_runs(self):
        for index, (verdict, level) in enumerate(
            ((OUTCOME_HIT, "green"), (OUTCOME_MISS, "green"), (OUTCOME_UNVERIFIABLE, "blue"))
        ):
            run_id = "2026080{}T020000Z-btc-aaaa1{}".format(index + 1, index)
            self.expired_run(
                run_id,
                "BTC 未來七天會不會漲 {}".format(index),
                assets=("BTC",),
                level=level,
            )
            written = outcome_module.record_manual_outcome(
                self.data_root, run_id, verdict, now=self.AFTER_DUE
            )
            self.assertEqual(outcome_module.WRITTEN, written.state, written.message)
        rebuild_index(self.data_root)

    def test_the_page_is_served_and_titled(self):
        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn("命中率", response.body)

    def test_the_four_states_are_each_named_on_the_page(self):
        self.three_runs()

        body = self.get("/history").body

        for word in ("命中", "未命中", "待驗證", "不可自動驗證"):
            self.assertIn(word, body)

    def test_the_hit_rate_denominator_is_written_on_the_page(self):
        """A percentage whose denominator is not stated is a percentage of what?"""
        self.three_runs()

        body = self.get("/history").body

        self.assertIn("命中 ÷（命中 + 未命中）", body)

    def test_an_unverifiable_run_does_not_move_the_hit_rate(self):
        self.three_runs()
        before = self.get("/history").body

        self.expired_run("20260804T020000Z-btc-aaaa14", "另一題", assets=("BTC",))
        outcome_module.record_manual_outcome(
            self.data_root, "20260804T020000Z-btc-aaaa14", OUTCOME_UNVERIFIABLE,
            now=self.AFTER_DUE,
        )
        rebuild_index(self.data_root)

        self.assertEqual(0.5, outcome_summary(self.data_root)["totals"]["hit_rate"])
        self.assertIn("50.0%", before)
        self.assertIn("50.0%", self.get("/history").body)

    def test_a_hit_rate_nobody_can_compute_yet_is_not_shown_as_zero(self):
        """An index with no scored run is not an index whose runs were all wrong."""
        rebuild_index(self.data_root)

        body = self.get("/history").body

        self.assertNotIn("0.0%", body)
        self.assertIn(pages.NO_HIT_RATE, body)

    def test_each_light_gets_its_own_row(self):
        self.three_runs()

        body = self.get("/history").body

        self.assertIn("綠燈", body)
        self.assertIn("藍燈", body)

    def test_hits_and_misses_are_not_told_apart_by_colour_alone(self):
        """WCAG: colour is never the only carrier of meaning."""
        self.three_runs()

        body = self.get("/history").body

        self.assertIn("命中", body)
        self.assertIn("未命中", body)

    def test_the_page_carries_no_script_and_the_strict_policy(self):
        response = self.get("/history")

        self.assertNotIn("<script", response.body)
        self.assertEqual(
            CONTENT_SECURITY_POLICY, response.headers["Content-Security-Policy"]
        )

    def test_the_page_says_it_writes_rather_than_claiming_to_only_read(self):
        """Ticket 11's trap, in Ticket 12's shape: this page writes outcome.json."""
        body = self.get("/history").body

        self.assertNotIn(pages.READ_ONLY_FOOTER, body)
        self.assertIn(pages.HISTORY_FOOTER, body)

    def test_the_missing_index_is_a_state_of_the_page_not_a_crash(self):
        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn("index-backfill", response.body)

    def test_the_hit_rate_is_reachable_from_the_history_page(self):
        """It used to be a tab away; since Ticket 04 it is the same page, which is
        the same guarantee stated at the new address."""
        self.index_two_runs()

        self.assertIn("整體命中率", self.get("/history").body)

    def test_the_manual_form_is_on_the_page_with_a_label_for_every_control(self):
        self.three_runs()

        body = self.get("/history").body

        self.assertIn('action="/history"', body)
        self.assertIn('name="run_id"', body)
        self.assertIn('name="verdict"', body)
        for control in ("run_id", "verdict", "note", "actual_price"):
            self.assertIn('for="{}"'.format(control), body)


class AnIndexThatCannotBeReadIsNotAnEmptyOneTest(OutcomeFixture, unittest.TestCase):
    """The page reads the index more than once, and any read can fail.

    Catching only the first left the pending list answering an unreadable index
    with ``[]``. The page then said ``ok`` above an empty list, which reads as
    "nothing is waiting" when what happened is "this page could not find out". Not
    seeing is not the same as nothing being there.

    Since Ticket 04 the merged page makes three reads rather than two — the
    filtered rows as well — so the discrimination below is on the *second* call
    rather than on every call: the rows come back, and the read after them is the
    one that breaks.
    """

    def only_the_pending_query_fails(self):
        """Break the read after the rows, and nothing before it.

        The totals come from ``outcome_summary`` and the sweep has its own import,
        so the name ``views`` calls is the rows read and then the pending read.
        Letting the first through and refusing the second reaches exactly the one
        whose failure used to be reported as an empty list.
        """
        real = views.query_runs
        calls = []

        def query_runs(*args, **options):
            calls.append(1)
            if len(calls) > 1:
                raise views.RunIndexError("索引壞了；請執行 index-backfill 重建。")
            return real(*args, **options)

        return query_runs

    def stats(self, now=None):
        return views.history_data(
            self.data_root,
            {},
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: now or self.AFTER_DUE,
                quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
                log=self.log,
            ),
        )

    def test_a_second_query_that_fails_is_not_reported_as_nothing_pending(self):
        self.write_market_run()

        with mock.patch.object(views, "query_runs", self.only_the_pending_query_fails()):
            data = self.stats()

        self.assertNotEqual(views.STATE_OK, data["state"])
        self.assertIn("index-backfill", data["reason"])

    def test_the_page_still_answers_rather_than_tracebacking(self):
        self.write_market_run()

        with mock.patch.object(views, "query_runs", self.only_the_pending_query_fails()):
            response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn("index-backfill", response.body)

    def test_the_failure_is_written_to_the_webapp_log(self):
        self.write_market_run()

        with mock.patch.object(views, "query_runs", self.only_the_pending_query_fails()):
            self.get("/history")

        self.assertIn("index_unavailable", [r["event"] for r in self.records()])

    def test_a_readable_index_still_reports_ok_and_lists_what_is_waiting(self):
        """FP direction: a page that always said 'unavailable' would pass those."""
        self.write_market_run()

        data = self.stats()

        self.assertEqual(views.STATE_OK, data["state"])
        self.assertIsNone(data["reason"])
        self.assertEqual(
            ["20260801T020000Z-btc-aaaa11"],
            [row["run_id"] for row in data["pending_runs"]],
        )


class StatsSweepThroughThePageTest(OutcomeFixture, unittest.TestCase):
    """The ticket's first acceptance condition, driven through a real request."""

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def build_with_quotes(self, quotes, now, **check_options):
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=self.spawn,
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: now, quote=quotes, log=self.log, **check_options
            ),
        )

    def test_a_run_that_expired_is_checked_recorded_indexed_and_counted(self):
        run_dir = self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        self.build_with_quotes(quotes, self.AFTER_DUE)

        before = self.get("/history").body

        self.assertEqual(OUTCOME_HIT, self.record(run_dir)["verdict"])
        self.assertEqual(OUTCOME_HIT, self.indexed_outcome(self.RUN_ID))
        self.assertIn("100.0%", before)

    def test_nothing_is_written_when_no_run_has_expired(self):
        run_dir = self.write_market_run()
        self.build_with_quotes(FakeQuotes(), self.BEFORE_DUE)

        self.get("/history")

        self.assertIsNone(self.record(run_dir))

    def test_the_sweep_is_recorded_in_the_log_when_it_did_something(self):
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        self.build_with_quotes(quotes, self.AFTER_DUE)

        self.get("/history")

        events = [r["event"] for r in self.records()]
        self.assertIn("outcome_sweep", events)

    def test_a_quiet_sweep_does_not_fill_the_log(self):
        self.write_market_run()
        self.build_with_quotes(FakeQuotes(), self.BEFORE_DUE)

        self.get("/history")

        self.assertNotIn("outcome_sweep", [r["event"] for r in self.records()])

    def test_a_manual_entry_posted_to_the_page_takes_effect(self):
        run_dir = self.write_market_run()
        self.build_with_quotes(FakeQuotes(failure=QuoteUnavailableError("停擺")), self.AFTER_DUE)

        response = self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_MISS})

        self.assertEqual(200, response.status)
        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])
        self.assertEqual(OUTCOME_MISS, self.indexed_outcome(self.RUN_ID))

    def test_a_second_manual_entry_is_refused_on_the_page_with_409(self):
        self.write_market_run()
        self.build_with_quotes(FakeQuotes(), self.AFTER_DUE)
        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_HIT})

        response = self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_MISS})

        self.assertEqual(409, response.status)
        self.assertIn("已經", response.body)

    def test_a_refused_manual_entry_is_logged(self):
        self.write_market_run()
        self.build_with_quotes(FakeQuotes(), self.AFTER_DUE)
        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_HIT})

        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_MISS})

        events = [r["event"] for r in self.records()]
        self.assertIn("outcome_manual_refused", events)

    def test_a_manual_entry_that_went_through_is_logged(self):
        self.write_market_run()
        self.build_with_quotes(FakeQuotes(), self.AFTER_DUE)

        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_HIT})

        events = [r["event"] for r in self.records()]
        self.assertIn("outcome_manual_recorded", events)

    def test_the_api_failure_and_the_manual_fallback_are_one_sequence(self):
        """The ticket's second acceptance condition, end to end."""
        run_dir = self.write_market_run()
        self.build_with_quotes(FakeQuotes(failure=QuoteUnavailableError("報價服務停擺")), self.AFTER_DUE)

        self.get("/history")
        logged = [r for r in self.records() if r["event"] == "outcome_quote_failed"]
        self.assertEqual(1, len(logged))
        self.assertIn("報價服務停擺", logged[0]["message"])
        self.assertIsNone(self.record(run_dir))

        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_MISS, "note": "人工"})

        self.assertEqual(OUTCOME_MISS, self.record(run_dir)["verdict"])
        self.assertEqual("人工", self.record(run_dir)["note"])

    def test_a_broken_quote_source_never_costs_the_page(self):
        """A broken quote source must not take the statistics page down with it.

        它現在也不會帶走**整輪掃描**。一筆 run 的報價爆掉是那一筆的事，所以事件
        從 ``outcome_sweep_failed``（整輪沒了）變成 ``outcome_quote_unexpected``
        （這一筆沒了，其餘照掃）；頁面照樣 200 這件事一個字都沒變。
        """
        self.write_market_run()

        def explode(*_args, **_options):
            raise RuntimeError("報價模組壞了")

        self.build_with_quotes(explode, self.AFTER_DUE)

        response = self.get("/history")

        events = [r["event"] for r in self.records()]
        self.assertEqual(200, response.status)
        self.assertIn("outcome_quote_unexpected", events)
        self.assertNotIn("outcome_sweep_failed", events)

    def test_a_sweep_that_really_breaks_is_still_caught_at_the_page_boundary(self):
        """FP 方向：逐 run 隔離不得把整輪失敗的那道邊界一起關掉。

        逐 run 隔離只搬走「一筆 run 的報價」那一種原因。掃描本身壞掉——這裡用
        ``limit`` 不是數字，也就是 fairness 前提裡「非正／非法 limit」那一項——
        仍然必須被 :meth:`OutcomeCheck.run` 接住、記成 ``outcome_sweep_failed``、
        而且不把統計頁一起帶走。少了這一條，``except Exception`` 搬到哪裡去了在
        測試上看不出來。
        """
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        self.build_with_quotes(quotes, self.AFTER_DUE, limit="20")

        response = self.get("/history")

        self.assertEqual(200, response.status)
        self.assertIn("outcome_sweep_failed", [r["event"] for r in self.records()])

    def test_the_sweep_leaves_no_thread_behind(self):
        self.write_market_run()
        quotes = FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0})
        self.build_with_quotes(quotes, self.AFTER_DUE)
        before = set(threading.enumerate())

        self.get("/history")
        self.post("/history", {"run_id": self.RUN_ID, "verdict": OUTCOME_HIT})

        self.assertEqual(before, set(threading.enumerate()))


class StatsReadOnlyBoundaryTest(OutcomeFixture, unittest.TestCase):
    """What Ticket 12 changed about "the web app only reads", stated exactly.

    Ticket 09 could say the web app never writes under ``runs/``. Ticket 10
    added a launch, which writes through a *separate process*. Ticket 12 is the
    first route in this process to write a run artifact, and it writes exactly
    one file: ``outcome.json``, once, for a run whose period has run out.

    So the read-only assertion is narrowed rather than dropped: every route
    except the two statistics ones still leaves the run tree byte for byte, and
    the two that do write are pinned here to the single file they may create.
    """

    RUN_ID = "20260801T020000Z-btc-aaaa11"

    def fingerprint(self):
        """Every run artifact, keyed by path. The derived index is not one.

        ``index.db`` and its lock sit beside the run directories but are not run
        artifacts: they are rebuildable from the very files below, and the sweep
        is *supposed* to update them. Keeping them out is what lets this
        assertion stay "not one recorded byte moved" instead of softening into
        "not much moved".
        """
        runs_root = self.data_root / "runs"
        return {
            str(path.relative_to(runs_root)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(runs_root.rglob("*"))
            if path.is_file() and len(path.relative_to(runs_root).parts) > 1
        }

    def test_the_statistics_page_writes_nothing_but_the_outcome_record(self):
        self.write_market_run()
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn,
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: self.AFTER_DUE,
                quote=FakeQuotes({("BTC", BASELINE_DAY): 100.0, ("BTC", SETTLE_DAY): 120.0}),
                log=self.log,
            ),
        )
        before = self.fingerprint()

        self.get("/history")
        after = self.fingerprint()

        added = set(after) - set(before)
        self.assertEqual(1, len(added), added)
        self.assertTrue(added.pop().endswith(OUTCOME_RECORD_NAME))
        self.assertEqual(before, {name: after[name] for name in before})

    def test_every_other_route_still_changes_nothing_under_runs(self):
        self.index_two_runs()
        before = self.fingerprint()

        for path in (
            "/",
            "/?keyword=BTC",
            "/run/{}".format(self.RUN_ID),
            "/run/{}/report.html".format(self.RUN_ID),
            "/live",
            "/settings",
            "/nope",
        ):
            self.get(path)

        self.assertEqual(before, self.fingerprint())


class QuoteApiStaysOutOfTheResearchPipelineTest(unittest.TestCase):
    """Ticket 12 §③: enforced by a scan, not by a rule somebody remembers.

    The same shape ``tests/test_seats.py`` uses for the seven seats' identity:
    read every source file in the package and assert that the set naming the
    quote client equals a short allowlist. A ``grep`` in a ticket stops the
    person who read the ticket; this stops the next one.

    Two false-positive checks sit underneath, because a scanner that matched
    nothing — a wrong root, a glob that found no files, a needle that is never
    spelled — would pass the two assertions above while proving nothing.
    """

    NEEDLE = "quote_api_client"

    # Who may reach a live price. The client itself, and the one web app module
    # that verifies a finished prediction after the fact. Nothing that
    # researches, debates or reports may be added here without the boundary
    # this scan exists for being the thing that is discussed.
    ALLOWED = {"quote_api_client.py", "webapp/outcome.py"}

    def package_files(self):
        root = Path(live.__file__).resolve().parent.parent
        return [
            path
            for path in sorted(root.rglob("*.py"))
            if "__pycache__" not in path.parts
        ]

    def modules_naming(self, needle):
        root = Path(live.__file__).resolve().parent.parent
        return {
            path.relative_to(root).as_posix()
            for path in self.package_files()
            if needle in path.read_text(encoding="utf-8")
        }

    def test_only_the_allowed_modules_name_the_quote_client(self):
        self.assertEqual(self.ALLOWED, self.modules_naming(self.NEEDLE))

    def test_no_research_debate_or_report_module_names_it(self):
        for module in (
            "question.py",
            "question_package.py",
            "prompt_builder.py",
            "debate_driver.py",
            "debate_rules.py",
            "debate_state_machine.py",
            "report_contract.py",
            "report_renderer.py",
            "report_workflow.py",
            "run_controller.py",
            "launcher.py",
            "codex_bridge.py",
            "real_provider.py",
            "research_scheduler.py",
        ):
            self.assertNotIn(module, self.modules_naming(self.NEEDLE))

    def test_the_scan_finds_the_authority_itself(self):
        """FP direction 1: a needle nothing spells would pass the two above."""
        self.assertIn("quote_api_client.py", self.modules_naming("QuoteUnavailableError"))
        self.assertIn("quote_api_client.py", self.modules_naming("QUOTE_SOURCES"))

    def test_the_scan_really_reads_every_module_in_the_package(self):
        """FP direction 2: a scan over an empty file list also never fails."""
        scanned = self.modules_naming("import")

        self.assertIn("run_index.py", scanned)
        self.assertIn("debate_driver.py", scanned)
        self.assertIn("webapp/server.py", scanned)
        self.assertIn("webapp/outcome.py", scanned)
        self.assertGreater(len(self.package_files()), 20)

    def test_the_pipeline_never_reaches_the_sweep_either(self):
        """The sweep is the only caller of the client, and it lives in the web app."""
        self.assertEqual(
            {"webapp/outcome.py", "webapp/server.py", "webapp/views.py"},
            self.modules_naming("sweep_due_runs") | self.modules_naming("OutcomeCheck"),
        )


class StatsColourTest(unittest.TestCase):
    """命中／未命中 are colours that are measured, and never the only signal."""

    TOKENS = ("success", "danger")

    def test_the_two_verdict_colours_answer_to_the_text_minimum(self):
        required = {
            (foreground, background)
            for foreground, background, minimum in pages.CONTRAST_REQUIREMENTS
            if minimum >= 4.5
        }
        for token in self.TOKENS:
            for background in ("page", "surface"):
                self.assertIn((token, background), required)

    def test_every_state_a_run_can_be_in_has_words_of_its_own(self):
        """Derived from the index's vocabulary, not from a list kept in the page."""
        self.assertEqual(
            set(pages.OUTCOME_WORDS),
            set(OUTCOME_VERDICTS) | {OUTCOME_UNREADABLE, OUTCOME_PENDING},
        )

    def test_every_state_carries_a_mark_as_well_as_a_colour(self):
        for state, (word, mark, _token) in pages.OUTCOME_WORDS.items():
            self.assertTrue(word.strip(), state)
            self.assertTrue(mark.strip(), state)

    def test_the_two_scored_states_are_the_ones_that_are_coloured(self):
        self.assertEqual("success", pages.OUTCOME_WORDS[OUTCOME_HIT][2])
        self.assertEqual("danger", pages.OUTCOME_WORDS[OUTCOME_MISS][2])

    def test_the_states_that_are_not_scored_are_not_painted_as_failures(self):
        for state in (OUTCOME_UNVERIFIABLE, OUTCOME_PENDING):
            self.assertNotEqual("danger", pages.OUTCOME_WORDS[state][2])


class RenderedHitRatesTest(OutcomeFixture, unittest.TestCase):
    """The substitute for the screenshot the ticket asks for.

    No browser exists in this environment — chrome, chromium, firefox,
    wkhtmltoimage, selenium and playwright were each looked for by the three
    preceding tickets and none is installed. So the page is rendered, written
    to a file the reviewer can open, and its load-bearing parts are asserted
    here instead of eyeballed.

    The page is ``/history`` since Ticket 04 merged the two; what this test is
    about — the arithmetic of five runs reaching the card as one number — did not
    move with it.
    """

    def test_the_kept_page_is_a_whole_document(self):
        for index, (verdict, level) in enumerate(
            (
                (OUTCOME_HIT, "green"),
                (OUTCOME_HIT, "green"),
                (OUTCOME_MISS, "green"),
                (OUTCOME_UNVERIFIABLE, "blue"),
                (None, "red"),
            )
        ):
            run_id = "2026080{}T020000Z-btc-aaaa1{}".format(index + 1, index)
            self.expired_run(
                run_id,
                "BTC 未來七天會不會漲 {}".format(index),
                assets=("BTC",),
                level=level,
            )
            if verdict is not None:
                outcome_module.record_manual_outcome(
                    self.data_root, run_id, verdict, now=self.AFTER_DUE
                )
        rebuild_index(self.data_root)
        # The fifth run is pending and past its deadline, so the sweep would price
        # it — against the wall clock and a real service — and change the very
        # number this test is about. Both are the test's.
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn,
            outcome_check=outcome_module.OutcomeCheck(
                now=lambda: self.AFTER_DUE,
                quote=FakeQuotes(failure=QuoteUnavailableError("停擺")),
                log=self.log,
            ),
        )

        body = self.get("/history").body
        kept = Path(os.environ.get("T12_PAGE_DIR", tempfile.gettempdir())) / "t12-stats.html"
        kept.write_text(body, encoding="utf-8")

        self.assertTrue(body.startswith("<!doctype html>"))
        self.assertIn(
            "<title>{}・{}</title>".format(pages.PAGE_TITLE_HISTORY, pages.SITE_TITLE),
            body,
        )
        self.assertIn("66.7%", body)
        self.assertIn(pages.HISTORY_FOOTER, body)


if __name__ == "__main__":
    unittest.main()
