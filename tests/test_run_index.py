"""Ticket 08: the rebuildable SQLite query index over finished runs.

Every test here drives the module through its public functions against a
temporary Data Root. Nothing asserts SQLite's own storage format — the row a
caller gets back is the contract, not the column types that hold it.

The fixtures deliberately make ``manifest.json``, ``question.json``,
``votes.json`` and ``report.json`` disagree with each other, because a fixture
where every record says the same thing cannot tell one column's source from
another's — and cannot tell a backfill that drifted away from the live upsert
from one that did not.
"""

import ast
import contextlib
import errno
import fcntl
import io
import json
import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_debate_driver import BULLISH_SIX, QUESTION as DRIVER_QUESTION, DebateDriverTestCase

from hoya_market_agents import run_index
from hoya_market_agents.run_index import (
    INDEX_LOCK_NAME,
    RunIndexBusyError,
    OUTCOME_HIT,
    OUTCOME_MISS,
    OUTCOME_PENDING,
    OUTCOME_RECORD_NAME,
    OUTCOME_UNREADABLE,
    OUTCOME_UNVERIFIABLE,
    OUTCOME_VERDICTS,
    RunIndexError,
    index_db_path,
    index_finalized_run,
    index_lock_path,
    outcome_summary,
    outcome_verdict,
    query_runs,
    rebuild_index,
    run_row,
    upsert_run,
)
from hoya_market_agents.run_store import RunStore, resolve_run_dir, run_dir_slug

QUESTION = "台積電 2330 未來七天會不會漲 50% 呢"
RUN_ID = "20260801T020000Z-2330-9d05c8"
DATE_FOLDER = "2026-08-01"
DIR_NAME = "1000-2330-未來七天會不會漲-c30df4c81e360727"
TALLY = {"affirmative": 6, "negative_side": 1, "undecided": 0}
WAVES = {"opening": 30_000, "r1": 50_000}


def write_run_dir(
    data_root,
    *,
    date_folder=DATE_FOLDER,
    dir_name=DIR_NAME,
    run_id=RUN_ID,
    question=QUESTION,
    question_type="open_proposition",
    asset_class="tw_stock",
    assets=("2330",),
    confidence_level="green",
    adopted_stance="affirmative",
    consensus_status="consensus",
    tally=None,
    manifest=True,
    votes=True,
    report=True,
    question_record=True,
    manifest_overrides=None,
    question_overrides=None,
    votes_overrides=None,
):
    """Write one finished run's directory tree and return its path."""
    run_dir = Path(data_root) / "runs" / date_folder / dir_name
    run_dir.mkdir(parents=True)
    tally = TALLY if tally is None else tally
    if manifest:
        _write_json(
            run_dir / "manifest.json",
            dict(
                {
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "question": question,
                    "question_type": question_type,
                    "assets": list(assets),
                    "period_days": 7,
                    "tally": dict(tally),
                },
                **(manifest_overrides or {})
            ),
        )
    if question_record:
        _write_json(
            run_dir / "question.json",
            dict(
                {
                    "run_id": run_id,
                    "question": question,
                    "question_type": question_type,
                    "asset_class": asset_class,
                    "assets": list(assets),
                },
                **(question_overrides or {})
            ),
        )
    if votes:
        _write_json(
            run_dir / "votes.json",
            dict(
                {
                    "run_id": run_id,
                    "tally": dict(tally),
                    "consensus_status": consensus_status,
                    "adopted_stance": adopted_stance,
                    "valid_vote_count": sum(tally.values()),
                },
                **(votes_overrides or {})
            ),
        )
    if report:
        _write_json(
            run_dir / "report.json",
            {
                "run_id": run_id,
                "confidence": {"level": confidence_level, "icon": "🟢", "text": "示範"},
                "tally": dict(tally),
                "consensus_status": consensus_status,
                "adopted_stance": adopted_stance,
            },
        )
        (run_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    return run_dir


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_outcome(run_dir, verdict, **extra):
    """Put one Ticket 12 outcome record beside a run, the way the web app does."""
    payload = dict({"schema_version": 1, "verdict": verdict}, **extra)
    _write_json(Path(run_dir) / OUTCOME_RECORD_NAME, payload)
    return payload


class RunIndexTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        (self.data_root / "runs").mkdir(parents=True)
        self.err = io.StringIO()

    def index(self, run_dir):
        self.assertTrue(upsert_run(self.data_root, run_dir))

    def only_row(self, **filters):
        rows = query_runs(self.data_root, **filters)
        self.assertEqual(1, len(rows), rows)
        return rows[0]


class RowFieldsTest(RunIndexTestCase):
    """Where each column comes from, and what it means when it is empty."""

    def test_a_finalized_run_is_queryable_with_every_column(self):
        run_dir = write_run_dir(self.data_root)

        self.index(run_dir)

        self.assertEqual(
            {
                "run_id": RUN_ID,
                "run_date": DATE_FOLDER,
                "question": QUESTION,
                "slug": "2330-未來七天會不會漲",
                "asset_class": "tw_stock",
                "assets": ["2330"],
                "question_type": "open_proposition",
                "confidence_level": "green",
                "adopted_stance": "affirmative",
                "tally": TALLY,
                "consensus_status": "consensus",
                "report_path": "{}/{}/report.html".format(DATE_FOLDER, DIR_NAME),
                "outcome": None,
            },
            self.only_row(),
        )

    def test_the_date_column_is_the_taipei_folder_not_the_utc_day(self):
        """Ticket 07's real case: UTC 2026-08-06 16:07 is Taipei 2026-08-07 00:07.

        The run id inside the directory still carries the UTC day, so a date
        taken from it would file this run under the day before the folder a
        user has to open to find it.
        """
        store = RunStore(self.data_root)
        run_id = "20260806T160733Z-btc-r6real"
        run = store.create_run(run_id, ("spot-technical",), question="過去 14 日的市場狀態如何")
        run_dir = run.path
        _write_json(
            run_dir / "manifest.json", {"run_id": run_id, "question": "過去 14 日的市場狀態如何"}
        )

        self.index(run_dir)
        row = self.only_row()

        self.assertEqual("2026-08-07", run_dir.parent.name)
        self.assertEqual(run_dir.parent.name, row["run_date"])
        self.assertNotEqual("2026-08-06", row["run_date"])

    def test_the_slug_column_is_the_label_inside_the_directory_name(self):
        run_dir = write_run_dir(self.data_root, dir_name="0007-btc-過去-14-日-fb06e62ec5f8bf30")

        self.index(run_dir)

        self.assertEqual("btc-過去-14-日", self.only_row()["slug"])

    def test_a_directory_named_without_a_label_has_an_empty_slug(self):
        run_dir = write_run_dir(self.data_root, dir_name="0007-fb06e62ec5f8bf30")

        self.index(run_dir)

        self.assertEqual("", self.only_row()["slug"])

    def test_the_report_path_is_relative_to_the_runs_root(self):
        run_dir = write_run_dir(self.data_root)

        self.index(run_dir)
        report_path = self.only_row()["report_path"]

        self.assertFalse(Path(report_path).is_absolute())
        self.assertTrue((self.data_root / "runs" / report_path).is_file())

    def test_the_outcome_column_is_empty_and_nullable_for_the_later_ticket(self):
        """Ticket 12 must be able to tell 未驗證 from 驗證結果為空。

        The insert succeeding is what proves the column is nullable: a
        ``NOT NULL`` on it would have refused this row outright.
        """
        run_dir = write_run_dir(self.data_root)

        self.index(run_dir)

        self.assertIsNone(self.only_row()["outcome"])

    def test_a_missing_record_leaves_its_own_columns_empty_only(self):
        run_dir = write_run_dir(self.data_root, votes=False, report=False)

        self.index(run_dir)
        row = self.only_row()

        self.assertEqual(QUESTION, row["question"])
        self.assertIsNone(row["adopted_stance"])
        self.assertIsNone(row["consensus_status"])
        self.assertIsNone(row["confidence_level"])
        self.assertIsNone(row["tally"])

    def test_a_directory_without_a_manifest_is_not_a_finished_run(self):
        run_dir = write_run_dir(self.data_root, manifest=False)

        self.assertIsNone(run_row(run_dir))
        self.assertFalse(upsert_run(self.data_root, run_dir))

    def test_a_directory_whose_manifest_will_not_parse_is_not_a_finished_run(self):
        run_dir = write_run_dir(self.data_root)
        (run_dir / "manifest.json").write_text("{ broken", encoding="utf-8")

        self.assertIsNone(run_row(run_dir))
        self.assertFalse(upsert_run(self.data_root, run_dir))

    def test_a_manifest_without_a_run_id_is_not_a_finished_run(self):
        run_dir = write_run_dir(self.data_root)
        _write_json(run_dir / "manifest.json", {"question": QUESTION})

        self.assertIsNone(run_row(run_dir))
        self.assertFalse(upsert_run(self.data_root, run_dir))


class ColumnSourceTest(RunIndexTestCase):
    """Which record owns each column, pinned by making the records disagree.

    Without a disagreement these assertions would pass against any of the
    candidate sources, and a backfill that read a different file from the one
    the live upsert reads would look identical.
    """

    def row_from(self, **kwargs):
        run_dir = write_run_dir(self.data_root, **kwargs)
        self.index(run_dir)
        return self.only_row()

    def test_the_question_column_comes_from_the_manifest(self):
        row = self.row_from(question_overrides={"question": "question.json 的舊題目"})

        self.assertEqual(QUESTION, row["question"])

    def test_the_question_type_column_comes_from_the_manifest(self):
        row = self.row_from(question_overrides={"question_type": "event_impact"})

        self.assertEqual("open_proposition", row["question_type"])

    def test_the_assets_column_comes_from_the_manifest(self):
        row = self.row_from(question_overrides={"assets": ["別的標的"]})

        self.assertEqual(["2330"], row["assets"])

    def test_the_asset_class_column_comes_from_the_question_record(self):
        """The manifest never carries it; only ``question.json`` does."""
        row = self.row_from(manifest_overrides={"asset_class": "crypto"})

        self.assertEqual("tw_stock", row["asset_class"])

    def test_the_tally_column_comes_from_the_votes_record(self):
        row = self.row_from(manifest_overrides={"tally": {"affirmative": 99}})

        self.assertEqual(TALLY, row["tally"])

    def test_the_consensus_columns_come_from_the_votes_record(self):
        row = self.row_from(
            votes_overrides={"consensus_status": "forced_stop", "adopted_stance": "negative_side"}
        )

        self.assertEqual("forced_stop", row["consensus_status"])
        self.assertEqual("negative_side", row["adopted_stance"])

    def test_the_confidence_column_comes_from_the_report(self):
        """No other record carries a light, so there is nothing to disagree with."""
        run_dir = write_run_dir(self.data_root, confidence_level="orange")
        self.index(run_dir)

        self.assertEqual("orange", self.only_row()["confidence_level"])


class OneRowBuilderTest(RunIndexTestCase):
    """Both write paths compute their row through the same function."""

    def doctored(self, run_dir):
        row = run_row(run_dir)
        row["question"] = "只有 run_row 會產生這個字串"
        return row

    def test_the_live_upsert_builds_its_row_through_run_row(self):
        run_dir = write_run_dir(self.data_root)

        with mock.patch.object(run_index, "run_row", side_effect=self.doctored):
            upsert_run(self.data_root, run_dir)

        self.assertEqual("只有 run_row 會產生這個字串", self.only_row()["question"])

    def test_the_backfill_builds_its_row_through_run_row(self):
        write_run_dir(self.data_root)

        with mock.patch.object(run_index, "run_row", side_effect=self.doctored):
            rebuild_index(self.data_root)

        self.assertEqual("只有 run_row 會產生這個字串", self.only_row()["question"])


class UpsertTest(RunIndexTestCase):
    def test_indexing_the_same_run_twice_keeps_one_row_and_refreshes_it(self):
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)

        _write_json(
            run_dir / "report.json",
            {"run_id": RUN_ID, "confidence": {"level": "orange"}},
        )
        self.index(run_dir)

        self.assertEqual("orange", self.only_row()["confidence_level"])

    def test_re_indexing_a_verified_run_keeps_its_verdict(self):
        """Ticket 08's promise, now kept the only way it can be kept.

        Ticket 08 wrote this by excluding ``outcome`` from the upsert, because
        at the time the verdict existed nowhere but in this file. Ticket 12
        gave it a home on disk — ``outcome.json`` — so the column is derived
        like every other one, and a re-index reproduces it instead of tiptoeing
        around it. The behaviour a caller sees is unchanged and is asserted
        here: indexing a verified run again does not cost it its verification.
        """
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        write_outcome(run_dir, OUTCOME_HIT)

        self.index(run_dir)
        self.index(run_dir)

        self.assertEqual(OUTCOME_HIT, self.only_row()["outcome"])

    def test_a_verdict_that_exists_only_in_the_index_is_not_preserved(self):
        """FP direction, and the reason Ticket 12 had to move the verdict to disk.

        A value that lives only in this file cannot survive ``index-backfill``,
        which empties the table before it rebuilds. Preserving it on the live
        path while a rebuild drops it would be the worst of both: the operator
        would be told the verdict is safe right up until the rebuild that loses
        it. The index is derived data, so a verdict with no record behind it is
        not a verdict.
        """
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        with sqlite3.connect(index_db_path(self.data_root)) as conn:
            conn.execute("UPDATE runs SET outcome = ?", ("hit",))

        self.index(run_dir)

        self.assertIsNone(self.only_row()["outcome"])


class MountPointTest(DebateDriverTestCase):
    """The real FINALIZED path, driven offline by the debate driver harness."""

    def finalized_row(self):
        rows = query_runs(self.data_root)
        self.assertEqual(1, len(rows), rows)
        return rows[0]

    def read_only_index(self):
        database = index_db_path(self.data_root)
        database.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(database).close()
        os.chmod(database, stat.S_IRUSR)
        self.addCleanup(os.chmod, database, stat.S_IRUSR | stat.S_IWUSR)
        return database

    def test_a_run_that_reaches_finalized_lands_in_the_index(self):
        runner = self.build_runner(BULLISH_SIX, wave_advance_ms=WAVES)

        handshake = self.finish(runner)
        row = self.finalized_row()

        self.assertEqual("FINALIZED", handshake["status"])
        self.assertEqual(self.run_id, row["run_id"])
        self.assertEqual(self.run.path.parent.name, row["run_date"])
        self.assertEqual(handshake["consensus_status"], row["consensus_status"])
        self.assertEqual(handshake["adopted_stance"], row["adopted_stance"])
        self.assertEqual(handshake["tally"], row["tally"])
        self.assertEqual(DRIVER_QUESTION, row["question"])
        self.assertEqual("single_asset_market_state", row["question_type"])
        # 燈號的權威是這一場自己的 report.json，不是測試裡再抄一份級別清單。
        published = json.loads((self.run.path / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(published["confidence"]["level"], row["confidence_level"])
        self.assertEqual(
            str(self.data_root / "runs" / row["report_path"]), handshake["report_html"]
        )

    def test_a_read_only_index_does_not_stop_a_run_from_finalizing(self):
        self.read_only_index()
        runner = self.build_runner(BULLISH_SIX, wave_advance_ms=WAVES)

        handshake = self.finish(runner)

        self.assertEqual("FINALIZED", handshake["status"])
        self.assertIn("索引", self.err.text)
        self.assertTrue((self.run.path / "manifest.json").is_file())
        self.assertTrue((self.data_root / "runs" / "latest.json").is_file())

    def test_a_run_whose_finalize_failed_before_the_manifest_is_not_indexed(self):
        """``manifest.json`` is the last thing ``run_after_seal`` writes, which
        is what lets its presence stand for "this run finished".

        Here ``latest.json`` — the step immediately before it — fails, so no
        FINALIZED handshake comes back. Neither the live hook nor a later
        backfill may treat what is on disk as a finished run.
        """
        runner = self.build_runner(BULLISH_SIX, wave_advance_ms=WAVES)
        broken = mock.patch.object(
            type(self.store), "point_latest_at", side_effect=OSError("latest 寫入失敗")
        )

        with broken:
            with self.assertRaises(OSError):
                self.finish(runner)

        self.assertFalse((self.run.path / "manifest.json").exists())
        self.assertEqual({"indexed": 0, "skipped": [
            "{}/{}".format(self.run.path.parent.name, self.run.path.name)
        ], "unexpected_date_folders": []}, rebuild_index(self.data_root))
        self.assertEqual([], query_runs(self.data_root))

    def test_backfill_recovers_a_run_the_index_could_not_take_live(self):
        database = self.read_only_index()
        runner = self.build_runner(BULLISH_SIX, wave_advance_ms=WAVES)
        self.finish(runner)
        os.chmod(database, stat.S_IRUSR | stat.S_IWUSR)
        # 先證明 live 那一次真的沒寫進去，否則這個測試證明不了「recovers」：
        # 唯讀擋在建表之前，所以連 runs 表都還不存在。
        with self.assertRaises(RunIndexError):
            query_runs(self.data_root)

        rebuild_index(self.data_root)

        self.assertEqual(
            [self.run_id], [row["run_id"] for row in query_runs(self.data_root)]
        )


class FailureIsolationTest(RunIndexTestCase):
    """索引寫入失敗記 log 不阻擋 run 完成 — and what does not count as one."""

    def read_only_database(self):
        database = index_db_path(self.data_root)
        sqlite3.connect(database).close()
        os.chmod(database, stat.S_IRUSR)
        self.addCleanup(os.chmod, database, stat.S_IRUSR | stat.S_IWUSR)
        return database

    def test_a_read_only_database_is_reported_and_never_raised(self):
        run_dir = write_run_dir(self.data_root)
        self.read_only_database()

        self.assertFalse(index_finalized_run(self.data_root, run_dir, err=self.err))
        self.assertIn("索引", self.err.getvalue())
        self.assertIn(DIR_NAME, self.err.getvalue())
        self.assertIn("readonly", self.err.getvalue())

    def test_the_same_write_raises_when_the_caller_asked_for_the_error(self):
        run_dir = write_run_dir(self.data_root)
        self.read_only_database()

        with self.assertRaises(sqlite3.OperationalError) as caught:
            upsert_run(self.data_root, run_dir)

        self.assertIn("readonly", str(caught.exception))

    def test_a_corrupt_database_file_is_reported_and_never_raised(self):
        run_dir = write_run_dir(self.data_root)
        index_db_path(self.data_root).write_bytes(b"not a database" * 400)

        self.assertFalse(index_finalized_run(self.data_root, run_dir, err=self.err))
        self.assertIn("索引", self.err.getvalue())
        self.assertIn("DatabaseError", self.err.getvalue())
        # 現場路徑只回報，從不刪東西；換掉損毀檔是 backfill 的事。
        self.assertTrue(index_db_path(self.data_root).is_file())

    def test_a_runs_directory_that_cannot_be_made_is_reported_and_never_raised(self):
        run_dir = write_run_dir(self.data_root)
        # A Data Root that is a regular file: creating ``runs/`` under it is an
        # OSError, which happens before SQLite is ever reached.
        blocked = self.data_root / "blocked"
        blocked.write_text("這不是目錄", encoding="utf-8")

        self.assertFalse(index_finalized_run(blocked, run_dir, err=self.err))
        self.assertIn("索引", self.err.getvalue())
        self.assertIn("NotADirectoryError", self.err.getvalue())

    def test_a_full_disk_is_reported_and_never_raised(self):
        run_dir = write_run_dir(self.data_root)
        full = sqlite3.OperationalError("database or disk is full")

        with mock.patch.object(run_index, "_connect_for_write", side_effect=full):
            self.assertFalse(index_finalized_run(self.data_root, run_dir, err=self.err))

        self.assertIn("database or disk is full", self.err.getvalue())

    def test_a_keyboard_interrupt_is_not_an_index_failure(self):
        run_dir = write_run_dir(self.data_root)

        with mock.patch.object(run_index, "_connect_for_write", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                index_finalized_run(self.data_root, run_dir, err=self.err)

        self.assertEqual("", self.err.getvalue())

    def test_a_system_exit_is_not_an_index_failure(self):
        run_dir = write_run_dir(self.data_root)

        with mock.patch.object(run_index, "_connect_for_write", side_effect=SystemExit(3)):
            with self.assertRaises(SystemExit):
                index_finalized_run(self.data_root, run_dir, err=self.err)

        self.assertEqual("", self.err.getvalue())

    def test_a_finalized_run_whose_records_cannot_be_read_is_still_reported(self):
        """This hook only runs where a run *has* finished, so finding nothing
        to index is a failure and has to leave the same trace as one."""
        run_dir = write_run_dir(self.data_root, manifest=False)

        self.assertFalse(index_finalized_run(self.data_root, run_dir, err=self.err))
        self.assertIn("索引", self.err.getvalue())
        self.assertIn("manifest.json", self.err.getvalue())
        self.assertIn(DIR_NAME, self.err.getvalue())
        self.assertFalse(index_db_path(self.data_root).exists())

    def test_a_run_that_indexes_cleanly_reports_nothing(self):
        run_dir = write_run_dir(self.data_root)

        self.assertTrue(index_finalized_run(self.data_root, run_dir, err=self.err))
        self.assertEqual("", self.err.getvalue())

    def test_without_a_stream_the_warning_goes_to_stderr(self):
        run_dir = write_run_dir(self.data_root)
        self.read_only_database()
        captured = io.StringIO()

        with contextlib.redirect_stderr(captured):
            self.assertFalse(index_finalized_run(self.data_root, run_dir))

        self.assertIn("索引", captured.getvalue())


class BackfillTest(RunIndexTestCase):
    def second_run(self):
        return write_run_dir(
            self.data_root,
            date_folder="2026-08-02",
            dir_name="0930-btc-a1b2c3d4e5f60718",
            run_id="20260802T013000Z-btc-aa11bb",
            question="BTC 未來 30 天走勢",
            question_type="single_asset_market_state",
            asset_class="crypto",
            assets=("BTC",),
            confidence_level="orange",
            adopted_stance="bearish",
            consensus_status="forced_stop",
            tally={"bullish": 2, "bearish": 4, "neutral": 1},
            # The records disagree on purpose, so a backfill reading a
            # different file from the live upsert cannot come out equal.
            question_overrides={"question": "question.json 的舊題目", "assets": ["別的標的"]},
            manifest_overrides={"tally": {"bullish": 99}},
        )

    def test_deleting_the_index_and_backfilling_reproduces_every_row(self):
        first = write_run_dir(self.data_root)
        second = self.second_run()
        self.index(first)
        self.index(second)
        before = query_runs(self.data_root)

        index_db_path(self.data_root).unlink()
        summary = rebuild_index(self.data_root)

        self.assertEqual(2, summary["indexed"])
        self.assertEqual(before, query_runs(self.data_root))

    def test_backfill_skips_the_claim_files_beside_the_run_directories(self):
        run_dir = write_run_dir(self.data_root)
        claim = run_dir.parent / ".1000-c30df4c81e360727.run-claim"
        claim.write_text("{}\n{}\n".format(RUN_ID, "0" * 32), encoding="utf-8")

        summary = rebuild_index(self.data_root)

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([], summary["skipped"])

    def test_backfill_skips_latest_json_and_the_index_beside_the_date_folders(self):
        write_run_dir(self.data_root)
        (self.data_root / "runs" / "latest.json").write_text("{}", encoding="utf-8")

        summary = rebuild_index(self.data_root)

        # index.db 在掃描開始前就已經建好，所以它確實是掃描看得到的鄰居。
        self.assertTrue(index_db_path(self.data_root).is_file())
        self.assertEqual(1, summary["indexed"])
        self.assertEqual([], summary["skipped"])

    def test_one_half_built_directory_does_not_stop_the_rest(self):
        write_run_dir(self.data_root, manifest=False, dir_name="0800-halfbuilt-1111111111111111")
        write_run_dir(self.data_root)
        write_run_dir(
            self.data_root,
            dir_name="1100-second-2222222222222222",
            run_id="20260801T030000Z-eth-bb22cc",
            question="ETH 呢",
        )

        summary = rebuild_index(self.data_root)

        self.assertEqual(2, summary["indexed"])
        self.assertEqual(
            ["{}/0800-halfbuilt-1111111111111111".format(DATE_FOLDER)], summary["skipped"]
        )
        self.assertEqual(2, len(query_runs(self.data_root)))

    def test_backfill_indexes_the_directory_names_ticket_07_verified(self):
        names = [
            "0007-con-0000000000000001",
            "0007-बिटकॉइन-0000000000000002",
            "0007-البيتكوين-0000000000000003",
            "0007-ｂｔｃ-0000000000000004",
            "0007-比特幣未來七天的市場狀態如何請詳細說明-0000000000000005",
        ]
        for offset, name in enumerate(names):
            write_run_dir(
                self.data_root,
                dir_name=name,
                run_id="20260801T02000{}Z-x-00000{}".format(offset, offset),
                question="題目 {}".format(offset),
            )

        summary = rebuild_index(self.data_root)

        self.assertEqual(len(names), summary["indexed"])
        self.assertEqual(
            sorted(name.split("-", 1)[1].rsplit("-", 1)[0] for name in names),
            sorted(row["slug"] for row in query_runs(self.data_root)),
        )

    def test_backfill_indexes_the_questions_ticket_07_verified_on_windows(self):
        """The eight shapes Ticket 07 created on Windows and read back in WSL.

        The directory names come from ``run_store`` rather than from this
        test, so what is scanned is what a real run of each question would
        leave on disk — including the two whose labels normalise away to
        nothing.
        """
        questions = [
            '<>:"/\\|?*',
            "CON.",
            "比特幣未來七天",
            "बिटकॉइन",
            "البِيتكُوين",
            "ＢＴＣ",
            "🚀📈",
            "比特幣" * 60,
        ]
        store = RunStore(self.data_root)
        expected = {}
        for offset, question in enumerate(questions):
            run_id = "20260801T0200{:02d}Z-x-{:06d}".format(offset, offset)
            run = store.create_run(run_id, ("spot-technical",), question=question)
            _write_json(run.path / "manifest.json", {"run_id": run_id, "question": question})
            # The oracle for the slug column is ``run_store``'s own answer for
            # this question, not a second reading of the directory name.
            expected[run_id] = (run.path.parent.name, run.path.name, run_dir_slug(question))

        summary = rebuild_index(self.data_root)
        rows = {row["run_id"]: row for row in query_runs(self.data_root)}

        self.assertEqual(len(questions), summary["indexed"])
        self.assertEqual([], summary["skipped"])
        self.assertEqual(set(expected), set(rows))
        for run_id, (date_folder, dir_name, slug) in expected.items():
            self.assertEqual(date_folder, rows[run_id]["run_date"])
            self.assertEqual(
                "{}/{}/report.html".format(date_folder, dir_name),
                rows[run_id]["report_path"],
            )
            self.assertEqual(slug, rows[run_id]["slug"])
        # 兩個題目的標籤會被正規化成空字串；這一組就是要證明它們也照樣進索引。
        self.assertEqual(2, sum(1 for _, _, slug in expected.values() if slug == ""))

    def test_backfill_drops_rows_for_runs_no_longer_on_disk(self):
        """Only the deleted one goes: a rebuild that emptied the table would
        pass a test that kept nothing to compare it against."""
        deleted = write_run_dir(self.data_root)
        survivor = self.second_run()
        self.index(deleted)
        self.index(survivor)
        for child in sorted(deleted.iterdir()):
            child.unlink()
        deleted.rmdir()

        rebuild_index(self.data_root)

        self.assertEqual(
            ["20260802T013000Z-btc-aa11bb"],
            [row["run_id"] for row in query_runs(self.data_root)],
        )

    def test_an_interrupted_backfill_leaves_the_previous_index_intact(self):
        first = write_run_dir(self.data_root)
        self.index(first)
        before = query_runs(self.data_root)
        write_run_dir(
            self.data_root,
            dir_name="1100-second-2222222222222222",
            run_id="20260801T030000Z-eth-bb22cc",
            question="ETH 呢",
        )

        with mock.patch.object(run_index, "run_row", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                rebuild_index(self.data_root)

        self.assertEqual(before, query_runs(self.data_root))

    def test_backfill_on_a_data_root_with_no_runs_yields_an_empty_index(self):
        summary = rebuild_index(self.data_root)

        self.assertEqual(0, summary["indexed"])
        self.assertEqual([], query_runs(self.data_root))

    def test_a_date_folder_that_cannot_be_listed_stops_the_rebuild(self):
        """"I could not look" must not be recorded as "there is nothing there".

        The scan decides which rows survive, so an empty answer from an
        unreadable directory is a decision to delete every row it did not
        find.
        """
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        before = query_runs(self.data_root)
        second = write_run_dir(self.data_root, date_folder="2026-08-02")
        os.chmod(second.parent, 0o000)
        self.addCleanup(os.chmod, second.parent, 0o755)

        with self.assertRaises(PermissionError):
            rebuild_index(self.data_root)

        self.assertEqual(before, query_runs(self.data_root))

    def test_a_runs_root_that_cannot_be_listed_stops_the_rebuild(self):
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        before = query_runs(self.data_root)
        runs_root = self.data_root / "runs"
        os.chmod(runs_root, 0o300)
        self.addCleanup(os.chmod, runs_root, 0o755)

        with self.assertRaises(PermissionError):
            rebuild_index(self.data_root)

        os.chmod(runs_root, 0o755)
        self.assertEqual(before, query_runs(self.data_root))

    def test_backfill_names_a_folder_under_runs_that_is_not_a_taipei_date(self):
        write_run_dir(self.data_root)
        write_run_dir(self.data_root, date_folder="不是日期", dir_name="1000-x-3333333333333333",
                      run_id="20260801T040000Z-x-cc33dd", question="被搬過的 run")

        summary = rebuild_index(self.data_root)

        self.assertEqual(2, summary["indexed"])
        self.assertEqual(["不是日期"], summary["unexpected_date_folders"])
        self.assertIn("不是日期", [row["run_date"] for row in query_runs(self.data_root)])

    def test_backfill_names_no_folder_when_every_date_folder_is_a_date(self):
        write_run_dir(self.data_root)
        write_run_dir(self.data_root, date_folder="2026-08-02", dir_name="1000-x-4444444444444444",
                      run_id="20260802T040000Z-x-dd44ee", question="正常的 run")

        summary = rebuild_index(self.data_root)

        self.assertEqual(2, summary["indexed"])
        self.assertEqual([], summary["unexpected_date_folders"])


class CorruptIndexTest(RunIndexTestCase):
    """票面第一句：損毀可全量重建。修復指示必須真的跑得起來。"""

    def corrupt(self):
        database = index_db_path(self.data_root)
        database.write_bytes(b"this is not a database" * 200)
        return database

    def half_corrupt(self):
        """Build a file SQLite still recognises but reports problems inside.

        Whole garbage makes ``quick_check`` raise; this makes it come back
        with error rows instead, which is the other half of "not usable" and
        the half a check that only watches for exceptions would miss.
        """
        database = index_db_path(self.data_root)
        conn = sqlite3.connect(database)
        conn.execute("CREATE TABLE filler (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("CREATE INDEX filler_body ON filler(body)")
        conn.executemany(
            "INSERT INTO filler VALUES (?, ?)",
            [(number, "x" * 200) for number in range(300)],
        )
        conn.commit()
        conn.close()
        raw = bytearray(database.read_bytes())
        raw[8192:12288] = bytes(4096)
        database.write_bytes(bytes(raw))
        # 先確認這份檔案真的落在「回報列」那一半，而不是「拋例外」那一半，
        # 否則這個測試會安靜地測到另一個分支。
        probe = sqlite3.connect("file:{}?mode=ro".format(database), uri=True)
        self.assertNotEqual([("ok",)], probe.execute("PRAGMA quick_check").fetchall())
        probe.close()
        return database

    def two_runs(self):
        first = write_run_dir(self.data_root)
        second = write_run_dir(
            self.data_root,
            date_folder="2026-08-02",
            dir_name="0930-btc-a1b2c3d4e5f60718",
            run_id="20260802T013000Z-btc-aa11bb",
            question="BTC 未來 30 天走勢",
        )
        return first, second

    def test_backfill_repairs_an_index_whose_content_is_not_a_database(self):
        first, second = self.two_runs()
        self.index(first)
        self.index(second)
        expected = query_runs(self.data_root)
        self.corrupt()

        summary = rebuild_index(self.data_root)

        self.assertEqual(2, summary["indexed"])
        self.assertEqual(expected, query_runs(self.data_root))

    def test_backfill_repairs_an_index_whose_quick_check_reports_problems(self):
        """"Unusable" is not only "raises" — a database that opens and answers
        with a list of its own faults is just as unusable, and a rebuild that
        only handled the raising kind would leave this one in place."""
        write_run_dir(self.data_root)
        self.half_corrupt()

        summary = rebuild_index(self.data_root)

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(self.data_root)])

    def test_backfill_repairs_an_index_that_is_only_an_empty_file(self):
        write_run_dir(self.data_root)
        index_db_path(self.data_root).write_bytes(b"")

        summary = rebuild_index(self.data_root)

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(self.data_root)])

    def test_backfill_supersedes_an_index_another_process_holds_locked(self):
        """A lock is on the database, not on its name.

        The holder keeps reading the inode it opened — a consistent snapshot
        of the moment it connected — and the next reader to open the name
        gets the rebuilt one.
        """
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        holder = sqlite3.connect(index_db_path(self.data_root))
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")

        summary = rebuild_index(self.data_root)

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(self.data_root)])
        self.assertEqual(
            [(RUN_ID,)], holder.execute("SELECT run_id FROM runs").fetchall()
        )

    def test_a_rebuild_never_unlinks_or_writes_over_the_live_index(self):
        """C2: an inode comparison cannot show this.

        Ticket 07 §9 ② settled that a freed inode number is reusable and so
        is not an identity. What has to be shown is that the live name is
        never passed to ``unlink`` at all, and that it only ever changes by
        one atomic ``os.replace``.
        """
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        database = index_db_path(self.data_root)
        unlinked = []
        installed = []
        real_unlink = Path.unlink
        real_replace = os.replace

        def watch_unlink(path, *args, **kwargs):
            unlinked.append(Path(path))
            return real_unlink(path, *args, **kwargs)

        def watch_replace(source, target, *args, **kwargs):
            installed.append((Path(source), Path(target)))
            return real_replace(source, target, *args, **kwargs)

        with mock.patch.object(Path, "unlink", watch_unlink):
            with mock.patch.object(os, "replace", watch_replace):
                rebuild_index(self.data_root)

        self.assertNotIn(database, unlinked)
        self.assertEqual([database], [target for _, target in installed])
        # What matters about the source is that it is a different file in the
        # same directory — the first so the live index is never written
        # through, the second so ``os.replace`` stays a rename. Its *name* is
        # not asserted: it is a file whatever it is called, and every reader
        # of ``runs/`` skips files on ``is_dir`` alone.
        self.assertTrue(all(source != database for source, _ in installed), installed)
        self.assertEqual(
            [database.parent], [source.parent for source, _ in installed]
        )
        self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(self.data_root)])

    def test_the_scratch_file_is_gone_and_left_no_journal_behind(self):
        write_run_dir(self.data_root)

        rebuild_index(self.data_root)

        beside = sorted(
            path.name
            for path in (self.data_root / "runs").iterdir()
            if path.is_file()
        )
        # 只該剩下索引與那把永久存在的鎖；沒有暫存檔、沒有 journal。
        self.assertEqual([INDEX_LOCK_NAME, "index.db"], beside)

    def test_a_rebuild_leaves_no_open_descriptor_on_what_it_built(self):
        """The commit already made the file complete; the close is what stops
        a descriptor leaking, and that is invisible in the installed bytes."""
        if not Path("/proc/self/fd").is_dir():
            self.skipTest("需要 /proc/self/fd")
        write_run_dir(self.data_root)

        rebuild_index(self.data_root)

        self.assertEqual(
            [], [name for name in open_descriptor_targets() if "index.db" in name]
        )

    def test_the_scratch_is_already_closed_at_the_moment_it_is_installed(self):
        """E3: the ordering, not the postcondition.

        A rebuild that installed the scratch and closed it afterwards would
        satisfy every "nothing is open when this returns" check and still be
        moving a file it had open. What has to hold is that the descriptor is
        gone *before* the move, so this looks while the move is happening.
        """
        if not Path("/proc/self/fd").is_dir():
            self.skipTest("需要 /proc/self/fd")
        write_run_dir(self.data_root)
        observed = []
        real_replace = os.replace

        def look_during_install(source, target, *args, **kwargs):
            observed.append((str(source), open_descriptor_targets()))
            return real_replace(source, target, *args, **kwargs)

        with mock.patch.object(os, "replace", look_during_install):
            rebuild_index(self.data_root)

        self.assertEqual(1, len(observed), observed)
        source, open_at_install = observed[0]
        self.assertNotIn(source, open_at_install)
        self.assertEqual(
            [], [name for name in open_at_install if "index.db" in name], open_at_install
        )

    def test_a_rebuild_that_fails_leaves_the_old_index_and_no_scratch(self):
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        before = index_db_path(self.data_root).read_bytes()

        with mock.patch.object(run_index, "run_row", side_effect=OSError("掃描壞了")):
            with self.assertRaises(OSError):
                rebuild_index(self.data_root)

        self.assertEqual(before, index_db_path(self.data_root).read_bytes())
        self.assertEqual(
            [INDEX_LOCK_NAME, "index.db"],
            sorted(
                path.name
                for path in (self.data_root / "runs").iterdir()
                if path.is_file()
            ),
        )

    def test_a_scratch_file_left_beside_the_runs_is_never_read_as_a_run(self):
        write_run_dir(self.data_root)
        stray = self.data_root / "runs" / ".index.db.abandoned.tmp"
        stray.write_bytes(b"")

        summary = rebuild_index(self.data_root)

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([], summary["skipped"])
        self.assertEqual([], summary["unexpected_date_folders"])

    def test_a_query_on_a_corrupt_index_names_the_command_that_repairs_it(self):
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        self.corrupt()

        with self.assertRaises(RunIndexError) as caught:
            query_runs(self.data_root)

        self.assertIn("index-backfill", str(caught.exception))
        # 照著訊息做真的會成功——這一條才是驗收「損毀可全量重建」。
        rebuild_index(self.data_root)
        self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(self.data_root)])


class QueryTest(RunIndexTestCase):
    def setUp(self):
        super().setUp()
        self._added = []

    def add(self, **overrides):
        offset = len(self._added)
        overrides.setdefault("run_id", "20260801T02{:04d}Z-x-{:06d}".format(offset, offset))
        overrides.setdefault("dir_name", "1000-slug{}-{:016d}".format(offset, offset))
        run_dir = write_run_dir(self.data_root, **overrides)
        self._added.append(run_dir)
        self.index(run_dir)
        return run_dir

    def questions(self, **filters):
        return [row["question"] for row in query_runs(self.data_root, **filters)]

    def test_a_date_range_includes_both_of_its_own_ends(self):
        self.add(date_folder="2026-08-01", question="第一天")
        self.add(date_folder="2026-08-03", question="第三天")

        self.assertEqual(
            {"第一天", "第三天"},
            set(self.questions(date_from="2026-08-01", date_to="2026-08-03")),
        )

    def test_a_date_range_leaves_out_what_falls_outside_it(self):
        self.add(date_folder="2026-07-31", question="前一天")
        self.add(date_folder="2026-08-02", question="範圍內")
        self.add(date_folder="2026-08-04", question="後一天")

        self.assertEqual(
            ["範圍內"], self.questions(date_from="2026-08-01", date_to="2026-08-03")
        )

    def test_an_asset_class_filter_keeps_only_that_class(self):
        self.add(asset_class="crypto", question="幣")
        self.add(asset_class="tw_stock", question="台股")

        self.assertEqual(["幣"], self.questions(asset_class="crypto"))

    def test_a_confidence_filter_keeps_only_that_light(self):
        self.add(confidence_level="green", question="綠")
        self.add(confidence_level="red", question="紅")

        self.assertEqual(["綠"], self.questions(confidence_level="green"))

    def test_filters_combine_rather_than_replace_one_another(self):
        self.add(date_folder="2026-08-01", asset_class="crypto", confidence_level="green", question="全中")
        self.add(date_folder="2026-08-01", asset_class="crypto", confidence_level="red", question="燈號不合")
        self.add(date_folder="2026-08-09", asset_class="crypto", confidence_level="green", question="日期不合")
        self.add(date_folder="2026-08-01", asset_class="us_stock", confidence_level="green", question="類別不合")

        self.assertEqual(
            ["全中"],
            self.questions(
                date_from="2026-08-01",
                date_to="2026-08-02",
                asset_class="crypto",
                confidence_level="green",
            ),
        )

    def test_a_keyword_finds_a_question_that_really_contains_a_percent_sign(self):
        self.add(question="BTC 會不會漲 50% 呢")

        self.assertEqual(["BTC 會不會漲 50% 呢"], self.questions(keyword="50%"))

    def test_a_percent_in_the_keyword_is_not_a_wildcard(self):
        self.add(question="BTC 會不會漲 50 元呢")

        self.assertEqual([], self.questions(keyword="50%"))

    def test_a_keyword_finds_a_question_that_really_contains_an_underscore(self):
        self.add(question="欄位 a_b 的意思")

        self.assertEqual(["欄位 a_b 的意思"], self.questions(keyword="a_b"))

    def test_an_underscore_in_the_keyword_is_not_a_wildcard(self):
        self.add(question="欄位 axb 的意思")

        self.assertEqual([], self.questions(keyword="a_b"))

    def test_a_keyword_finds_a_question_that_really_contains_a_backslash(self):
        self.add(question="路徑 C:\\Users 在哪")

        self.assertEqual(["路徑 C:\\Users 在哪"], self.questions(keyword="C:\\Users"))

    def test_a_backslash_in_the_keyword_does_not_disarm_the_next_character(self):
        self.add(question="漲 50 元")

        self.assertEqual([], self.questions(keyword="\\%"))

    def test_a_keyword_matches_arbitrary_unicode_in_the_question(self):
        self.add(question="🚀 台積電 2330 未來如何")

        self.assertEqual(["🚀 台積電 2330 未來如何"], self.questions(keyword="台積電"))

    def test_an_empty_keyword_still_filters_rather_than_being_dropped(self):
        """``keyword=""`` is a filter that a run with no question fails."""
        self.add(question="任何題目")
        self.add(question="有題目但 manifest 沒記", manifest_overrides={"question": None})

        self.assertEqual(2, len(query_runs(self.data_root)))
        self.assertEqual(["任何題目"], self.questions(keyword=""))

    def test_results_come_back_newest_first(self):
        self.add(date_folder="2026-08-01", question="舊")
        self.add(date_folder="2026-08-05", question="新")

        self.assertEqual(["新", "舊"], self.questions())

    def test_two_runs_on_one_day_come_back_newest_first(self):
        self.add(date_folder="2026-08-01", run_id="20260801T010000Z-x-aaaaaa", question="早")
        self.add(date_folder="2026-08-01", run_id="20260801T090000Z-x-bbbbbb", question="晚")

        self.assertEqual(["晚", "早"], self.questions())

    def test_a_limit_caps_the_number_of_rows(self):
        self.add(date_folder="2026-08-01", question="舊")
        self.add(date_folder="2026-08-05", question="新")

        self.assertEqual(["新"], self.questions(limit=1))

    def test_a_limit_of_zero_returns_nothing_rather_than_everything(self):
        self.add(question="有一筆")

        self.assertEqual([], self.questions(limit=0))

    def test_a_negative_limit_is_refused_instead_of_lifting_the_cap(self):
        """SQLite reads ``LIMIT -1`` as no limit at all, so a front end passing
        a user's number straight through would silently uncap itself."""
        self.add(question="第一筆")
        self.add(question="第二筆")

        with self.assertRaises(ValueError):
            query_runs(self.data_root, limit=-1)

        # 拒絕的是負數，不是 limit 這個功能。
        self.assertEqual(1, len(query_runs(self.data_root, limit=1)))

    def test_no_limit_at_all_returns_everything(self):
        self.add(question="第一筆")
        self.add(question="第二筆")

        self.assertEqual(2, len(query_runs(self.data_root)))

    def test_the_index_accepts_values_no_configuration_file_declares(self):
        """燈號與資產類別的權威在別的檔案；schema 不得再宣告一次。"""
        self.add(asset_class="not-a-declared-class", confidence_level="chartreuse", question="怪值")

        self.assertEqual(["怪值"], self.questions(asset_class="not-a-declared-class"))
        self.assertEqual(["怪值"], self.questions(confidence_level="chartreuse"))

    def test_querying_without_an_index_says_so_instead_of_returning_nothing(self):
        with self.assertRaises(RunIndexError) as caught:
            query_runs(self.data_root)

        message = str(caught.exception)
        self.assertIn(str(index_db_path(self.data_root)), message)
        self.assertIn("index-backfill", message)

    def test_an_index_that_cannot_be_opened_is_a_run_index_error_too(self):
        """A caller that only knows about RunIndexError must not meet a raw
        SQLite error just because the file's permissions changed."""
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        database = index_db_path(self.data_root)
        os.chmod(database, 0o000)
        self.addCleanup(os.chmod, database, stat.S_IRUSR | stat.S_IWUSR)

        with self.assertRaises(RunIndexError) as caught:
            query_runs(self.data_root)

        self.assertIn("index-backfill", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, sqlite3.OperationalError)

    def test_querying_never_creates_the_database(self):
        with self.assertRaises(RunIndexError):
            query_runs(self.data_root)

        self.assertFalse(index_db_path(self.data_root).exists())

    def test_a_row_whose_stored_structure_will_not_decode_is_a_read_failure(self):
        """索引損壞不是只有整個檔案壞掉一種；壞在一格也要說得出「請重建」。"""
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        with sqlite3.connect(index_db_path(self.data_root)) as conn:
            conn.execute("UPDATE runs SET tally = ?", ("{ 壞掉",))

        with self.assertRaises(RunIndexError) as caught:
            query_runs(self.data_root)

        # 不是「反正拋了 RunIndexError」——必須真的是解不開那一格造成的。
        self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)
        self.assertIn("index-backfill", str(caught.exception))

    def test_a_data_root_whose_path_has_uri_punctuation_is_still_queryable(self):
        """A read opens the index by URI, and a URI ends its filename at ``?``/``#``.

        Unescaped, both characters truncate the path and the query answers
        from a different database than the one that was written.
        """
        for awkward in ("has space", "has#hash", "has?question", "百分之50%"):
            with self.subTest(awkward):
                data_root = Path(self._tmp.name) / awkward
                (data_root / "runs").mkdir(parents=True)
                run_dir = write_run_dir(data_root)
                upsert_run(data_root, run_dir)

                self.assertEqual([RUN_ID], [row["run_id"] for row in query_runs(data_root)])


CODE_ROOT = Path(run_index.__file__).resolve().parent.parent


def _close_quietly(handle):
    """Close a descriptor that a test may already have closed."""
    try:
        os.close(handle)
    except OSError:
        pass


def open_descriptor_targets():
    """Return what this process currently has open, by path."""
    targets = []
    for entry in Path("/proc/self/fd").iterdir():
        try:
            targets.append(os.readlink(entry))
        except OSError:
            continue
    return targets

# One rebuild, slowed down so a second process can observe it while it runs.
# The slowdown is in this script rather than in the module, so what is watched
# is the real ``rebuild_index``.
SLOW_REBUILD = """
import sys, time
from pathlib import Path
sys.path.insert(0, {code_root!r})
from hoya_market_agents import run_index

original = run_index.run_row


def slow(run_dir):
    time.sleep(0.05)
    return original(run_dir)


run_index.run_row = slow
Path(sys.argv[2]).write_text("started", encoding="utf-8")
print(run_index.rebuild_index(Path(sys.argv[1]))["indexed"])
"""

# A second process watching the index's name for the whole of that rebuild.
# It records every distinct state it saw: missing, unreadable, or the set of
# run ids it could read.
WATCH_INDEX = """
import json, sqlite3, sys, time
from pathlib import Path

database = Path(sys.argv[1])
started = Path(sys.argv[2])
stop = Path(sys.argv[3])
seen = []


def sample():
    if not database.exists():
        return "MISSING"
    try:
        conn = sqlite3.connect("file:{}?mode=ro".format(database), uri=True)
        rows = sorted(row[0] for row in conn.execute("SELECT run_id FROM runs"))
        conn.close()
        return "ROWS:" + ",".join(rows)
    except sqlite3.Error as exc:
        return "UNREADABLE:" + type(exc).__name__


while not started.exists():
    time.sleep(0.002)
while not stop.exists():
    state = sample()
    if not seen or seen[-1] != state:
        seen.append(state)
    time.sleep(0.002)
print(json.dumps(seen))
"""


# A rebuild that stops with its new index built but not yet installed — the
# exact instant both reviewers used to show a concurrent row being discarded.
PAUSED_REBUILD = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, {code_root!r})
from hoya_market_agents import run_index

data_root, paused, go = (Path(item) for item in sys.argv[1:4])
real_replace = os.replace


def wait_then_replace(source, target, *args, **kwargs):
    paused.write_text("paused", encoding="utf-8")
    while not go.exists():
        time.sleep(0.002)
    return real_replace(source, target, *args, **kwargs)


os.replace = wait_then_replace
print(run_index.rebuild_index(data_root)["indexed"])
"""

# The same rebuild stopped earlier — partway through the scan, before it has
# decided what the new index will contain. A lock taken only around the
# install would leave this window open.
REBUILD_PAUSED_IN_SCAN = """
import sys, time
from pathlib import Path
sys.path.insert(0, {code_root!r})
from hoya_market_agents import run_index

data_root, paused, go = (Path(item) for item in sys.argv[1:4])
original = run_index.run_row


def pause_once(run_dir):
    if not paused.exists():
        paused.write_text("paused", encoding="utf-8")
        while not go.exists():
            time.sleep(0.002)
    return original(run_dir)


run_index.run_row = pause_once
print(run_index.rebuild_index(data_root)["indexed"])
"""

# One finishing run's index write, in its own process.
LIVE_UPSERT = """
import io, sys
from pathlib import Path
sys.path.insert(0, {code_root!r})
from hoya_market_agents import run_index

data_root, run_dir, started = (Path(item) for item in sys.argv[1:4])
started.write_text("started", encoding="utf-8")
err = io.StringIO()
written = run_index.index_finalized_run(data_root, run_dir, err=err)
print(written)
print(err.getvalue().strip())
"""


class ConcurrentRebuildTest(unittest.TestCase):
    """Two real processes, because this is where round 2's defect lived.

    Round 2 decided whether the index was usable and then acted on that
    decision later, on whatever file happened to be at the name by then. Two
    reviewers each reproduced a healthy index being destroyed by a verdict
    formed before it existed.

    What is asserted here is the harm rather than the mechanism: whatever a
    rebuild does internally, another process watching the index's name must
    never see it missing or unreadable. That holds for any implementation
    that is correct, and fails for every one that removes the live file
    before putting a new one back.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name) / "data"
        (self.data_root / "runs").mkdir(parents=True)
        self.signals = Path(self._tmp.name) / "signals"
        self.signals.mkdir()
        self.run_ids = []
        for offset in range(6):
            run_id = "20260801T02000{}Z-x-{:06d}".format(offset, offset)
            write_run_dir(
                self.data_root,
                dir_name="10{:02d}-slug{}-{:016d}".format(offset, offset, offset),
                run_id=run_id,
                question="題目 {}".format(offset),
            )
            self.run_ids.append(run_id)

    def child(self, source, *arguments):
        return subprocess.Popen(
            [sys.executable, "-c", source, *[str(item) for item in arguments]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": self._tmp.name,
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def watch_one_rebuild(self):
        """Run one slowed rebuild while a second process watches the name."""
        started = self.signals / "started"
        stop = self.signals / "stop"
        watcher = self.child(WATCH_INDEX, index_db_path(self.data_root), started, stop)
        rebuilder = self.child(
            SLOW_REBUILD.format(code_root=str(CODE_ROOT)), self.data_root, started
        )
        rebuilt, rebuild_error = rebuilder.communicate(timeout=120)
        stop.write_text("stop", encoding="utf-8")
        observed, watch_error = watcher.communicate(timeout=120)
        self.assertEqual(0, rebuilder.returncode, rebuild_error)
        self.assertEqual(0, watcher.returncode, watch_error)
        self.assertEqual(str(len(self.run_ids)), rebuilt.strip())
        states = json.loads(observed)
        self.assertGreater(len(states), 0)
        return states

    def test_a_watcher_never_sees_a_healthy_index_leave_the_name(self):
        complete = "ROWS:" + ",".join(sorted(self.run_ids))
        rebuild_index(self.data_root)

        states = self.watch_one_rebuild()

        self.assertEqual({complete}, set(states), states)

    def test_a_watcher_never_sees_the_name_empty_while_a_corrupt_index_is_repaired(self):
        """This is the interleaving the reviewers reported, watched from
        outside.

        A design that decides the file is unusable and then deletes it hands
        the name to a fresh, empty database for the whole of the following
        scan. Run against that design this test reports
        ``['ROWS:', 'ROWS:<all six>']`` — a reader in between gets a
        perfectly readable index that says there is no history at all, which
        is worse than an error. Superseding the file instead never produces
        any state but the corrupt one and then the complete one.
        """
        complete = "ROWS:" + ",".join(sorted(self.run_ids))
        index_db_path(self.data_root).write_bytes(b"not a database" * 400)

        states = self.watch_one_rebuild()

        self.assertNotIn("MISSING", states)
        self.assertEqual(complete, states[-1])
        self.assertTrue(
            all(state.startswith("UNREADABLE") or state == complete for state in states),
            states,
        )

    def test_two_rebuilds_racing_each_other_both_land_a_complete_index(self):
        expected = sorted(self.run_ids)
        first = self.child(
            SLOW_REBUILD.format(code_root=str(CODE_ROOT)),
            self.data_root,
            self.signals / "a",
        )
        second = self.child(
            SLOW_REBUILD.format(code_root=str(CODE_ROOT)),
            self.data_root,
            self.signals / "b",
        )
        first_out, first_err = first.communicate(timeout=120)
        second_out, second_err = second.communicate(timeout=120)

        self.assertEqual(0, first.returncode, first_err)
        self.assertEqual(0, second.returncode, second_err)
        self.assertEqual(str(len(expected)), first_out.strip())
        self.assertEqual(str(len(expected)), second_out.strip())
        self.assertEqual(expected, sorted(row["run_id"] for row in query_runs(self.data_root)))

    def test_a_run_finishing_mid_rebuild_is_not_discarded_by_the_install(self):
        """D1: the interleaving both reviewers reproduced.

        A rebuild is stopped with its new index built but not yet installed.
        A run finishes in that gap and writes its row. If the install could
        go ahead regardless, that row would be superseded by an index built
        before the run existed — and the live path would have reported
        success, so nobody would ever know. Holding the lock across the scan
        *and* the install is what removes the gap: the late row is written
        into the index that ends up on disk.
        """
        indexed = self.run_finishing_while_paused(PAUSED_REBUILD)

        self.assertIn("20260809T153000Z-late-ff99aa", indexed)
        self.assertEqual(len(self.run_ids) + 1, len(indexed))

    def run_finishing_while_paused(self, script):
        """Finalize one run while a rebuild is stopped, and return the index."""
        rebuild_index(self.data_root)
        paused = self.signals / "paused"
        go = self.signals / "go"
        started = self.signals / "upsert-started"

        rebuilder = self.child(
            script.format(code_root=str(CODE_ROOT)), self.data_root, paused, go
        )
        while not paused.exists() and rebuilder.poll() is None:
            time.sleep(0.002)
        self.assertTrue(paused.exists(), "rebuild 沒有停在預期的地方")

        latecomer = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-latecomer-9999999999999999",
            run_id="20260809T153000Z-late-ff99aa",
            question="rebuild 進行中才完成的 run",
        )
        writer = self.child(
            LIVE_UPSERT.format(code_root=str(CODE_ROOT)),
            self.data_root,
            latecomer,
            started,
        )
        while not started.exists() and writer.poll() is None:
            time.sleep(0.002)
        go.write_text("go", encoding="utf-8")

        rebuilt, rebuild_error = rebuilder.communicate(timeout=120)
        written, write_error = writer.communicate(timeout=120)

        self.assertEqual(0, rebuilder.returncode, rebuild_error)
        self.assertEqual(0, writer.returncode, write_error)
        self.assertEqual(str(len(self.run_ids)), rebuilt.strip())
        reported, warning = (written.splitlines() + [""])[:2]
        self.assertEqual("True", reported, written)
        self.assertEqual("", warning, written)
        return [row["run_id"] for row in query_runs(self.data_root)]

    def test_a_run_finishing_during_the_scan_is_not_discarded_either(self):
        """The other half of D1, and the half a lock on the install alone misses.

        Here the rebuild is stopped before it has finished deciding what its
        new index contains. A writer that could slip in now would write into
        an index the rebuild is about to supersede — and the rebuild's own
        contents were settled before this run existed, so the row would be
        gone with nobody told. The lock covers the scan for exactly this.
        """
        indexed = self.run_finishing_while_paused(REBUILD_PAUSED_IN_SCAN)

        self.assertIn("20260809T153000Z-late-ff99aa", indexed)
        self.assertEqual(len(self.run_ids) + 1, len(indexed))

    def held_lock(self):
        """Hold the writer lock from this process until the cleanup runs."""
        handle = os.open(index_lock_path(self.data_root), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(handle, fcntl.LOCK_EX)
        self.addCleanup(_close_quietly, handle)
        return handle

    def test_a_run_deleted_while_its_writer_waits_leaves_no_orphan_row(self):
        """E1: the row must be read from disk on the near side of the lock.

        A row worked out before the wait describes the run as it was while
        this call was still queuing. Delete the run in the meantime and that
        row is a claim about something that is no longer there — written into
        an index whose whole promise is that it says what is on disk, and
        written with a ``True`` and no warning, so nobody would know.

        This is the same mistake as scanning outside the lock, one row wide.
        """
        rebuild_index(self.data_root)
        before = [row["run_id"] for row in query_runs(self.data_root)]
        victim = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-victim-7777777777777777",
            run_id="20260809T153000Z-gone-ee77bb",
            question="寫入者等鎖時被刪掉的 run",
        )
        handle = self.held_lock()
        started = self.signals / "upsert-started"

        writer = self.child(
            LIVE_UPSERT.format(code_root=str(CODE_ROOT)),
            self.data_root,
            victim,
            started,
        )
        while not started.exists() and writer.poll() is None:
            time.sleep(0.002)
        time.sleep(0.05)
        for child in sorted(victim.iterdir()):
            child.unlink()
        victim.rmdir()
        _close_quietly(handle)
        written, write_error = writer.communicate(timeout=120)

        self.assertEqual(0, writer.returncode, write_error)
        reported, warning = (written.splitlines() + [""])[:2]
        self.assertEqual("False", reported, written)
        self.assertIn("索引", warning)
        self.assertEqual(
            sorted(before), sorted(row["run_id"] for row in query_runs(self.data_root))
        )

    def test_a_row_written_just_before_its_run_is_deleted_is_cleared_by_backfill(self):
        """The residual risk this ticket accepts, and the sentence that covers it.

        Deleting a run *after* its row was read is the one ordering no lock
        can undo — the write had already happened. What has to be true is the
        runbook's claim that a rebuild then puts the table back in step with
        the disk. That needs an orphan to actually exist first: asserting it
        on an index that was already clean would only show that nothing
        changed nothing.
        """
        rebuild_index(self.data_root)
        victim_id = "20260809T153000Z-late-aa33ff"
        victim = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-deleted-3333333333333333",
            run_id=victim_id,
            question="讀完之後才被刪掉的 run",
        )
        real_row = run_index.run_row

        def delete_after_reading(run_dir):
            row = real_row(run_dir)
            if Path(run_dir) == victim:
                for child in sorted(victim.iterdir()):
                    child.unlink()
                victim.rmdir()
            return row

        with mock.patch.object(run_index, "run_row", delete_after_reading):
            self.assertTrue(upsert_run(self.data_root, victim))

        self.assertFalse(victim.exists())
        self.assertIn(victim_id, [row["run_id"] for row in query_runs(self.data_root)])

        rebuild_index(self.data_root)

        self.assertNotIn(
            victim_id, [row["run_id"] for row in query_runs(self.data_root)]
        )
        self.assertEqual(
            sorted(self.run_ids),
            sorted(row["run_id"] for row in query_runs(self.data_root)),
        )

    def test_waiting_for_both_locks_never_costs_more_than_one_budget(self):
        """E2: two waits, one budget.

        The lock and SQLite are two separate waits on the same call. Given a
        timeout each, a run finishing while both are contended would be held
        up for twice the number this module advertises.
        """
        run_dir = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-budget-6666666666666666",
            run_id="20260809T153000Z-slow-dd66cc",
            question="兩把鎖都要等的 run",
        )
        rebuild_index(self.data_root)
        budget = 0.4
        handle = self.held_lock()
        blocker = sqlite3.connect(index_db_path(self.data_root))
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN EXCLUSIVE")
        blocker.execute("DELETE FROM runs")
        releaser = threading.Timer(budget * 0.75, _close_quietly, args=(handle,))
        releaser.start()
        self.addCleanup(releaser.cancel)
        err = io.StringIO()

        with mock.patch.object(run_index, "_BUSY_TIMEOUT_SECONDS", budget):
            started = time.monotonic()
            written = index_finalized_run(self.data_root, run_dir, err=err)
            elapsed = time.monotonic() - started

        self.assertFalse(written)
        self.assertIn("索引", err.getvalue())
        self.assertLess(elapsed, budget * 1.5, err.getvalue())

    def test_a_write_with_no_budget_left_says_the_wait_ran_out(self):
        """A spent budget is a wait that ran out, not a database problem.

        Handing SQLite a zero timeout instead would let it answer for a
        question it was never asked — the caller waited too long for a lock,
        which is not something SQLite knows about.
        """
        with self.assertRaises(RunIndexBusyError):
            with run_index._writing(self.data_root, time.monotonic() - 1):
                pass

    def test_a_lock_error_that_is_not_contention_is_not_called_contention(self):
        """E4 FP direction: "I do not know what happened" must not become a
        confident answer.

        Retrying every ``OSError`` turns a bad descriptor into a full-budget
        wait and then a message naming a competing process that never existed
        — sending an operator to look for it.
        """
        handle = os.open(index_lock_path(self.data_root), os.O_CREAT | os.O_RDWR, 0o644)
        os.close(handle)
        started = time.monotonic()

        with self.assertRaises(OSError) as caught:
            run_index._take_lock(
                handle, self.data_root / "runs", time.monotonic() + 30
            )
        elapsed = time.monotonic() - started

        self.assertNotIsInstance(caught.exception, RunIndexError)
        self.assertEqual(errno.EBADF, caught.exception.errno)
        self.assertLess(elapsed, 1.0)

    def test_no_lock_error_other_than_contention_is_ever_waited_out(self):
        """F1 FP direction, widened: the whole non-contention set, injected."""
        for number in (errno.EBADF, errno.EINVAL, errno.ENOLCK, errno.EIO):
            with self.subTest(errno.errorcode[number]):
                def refuse(handle, operation, code=number):
                    raise OSError(code, os.strerror(code))

                started = time.monotonic()
                with mock.patch.object(run_index.fcntl, "flock", refuse):
                    with self.assertRaises(OSError) as caught:
                        run_index._take_lock(
                            0, self.data_root / "runs", time.monotonic() + 30
                        )

                self.assertNotIsInstance(caught.exception, RunIndexError)
                self.assertEqual(number, caught.exception.errno)
                self.assertLess(time.monotonic() - started, 1.0)

    def test_the_contention_set_names_every_errno_both_lock_paths_can_use(self):
        """G1: asserted on the *symbols*, because the values cannot show it.

        On Linux ``EWOULDBLOCK`` and ``EAGAIN`` are the same number, so a set
        built from either one is indistinguishable at runtime — dropping the
        symbol changes nothing this process can observe, and would still
        leave the native ``flock(2)`` path's documented errno unnamed on any
        platform that keeps the two distinct. So this reads the source.

        The three names are the union of what the two paths promise: native
        ``flock(2)`` reports ``EWOULDBLOCK``; CPython's ``fcntl(F_SETLK)``
        emulation reports ``EACCES`` or ``EAGAIN``.
        """
        source = Path(run_index.__file__).read_text(encoding="utf-8")
        assignment = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_CONTENTION_ERRNOS"
                for target in node.targets
            )
        )
        named = {
            node.attr
            for node in ast.walk(assignment)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "errno"
        }

        self.assertEqual({"EACCES", "EAGAIN", "EWOULDBLOCK"}, named)
        # 值的層次仍然要對，只是它證明不了上面那件事。
        self.assertEqual(
            {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK},
            set(run_index._CONTENTION_ERRNOS),
        )

    def test_contention_reported_as_eacces_is_waited_for_not_refused(self):
        """F1 FN direction.

        ``fcntl.flock`` is documented to report "would block" as either
        ``EAGAIN`` or ``EACCES``, and Python raises those as two different
        exception classes. A handler written around one class turns real
        contention on the other platform into an immediate hard error — which
        is the failure the previous round's fix created while closing the
        opposite one.
        """
        run_dir = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-eacces-5555555555555555",
            run_id="20260809T153000Z-eacc-cc55dd",
            question="被 EACCES 擋住的 run",
        )
        rebuild_index(self.data_root)
        attempts = []
        real_flock = fcntl.flock

        def busy_twice(handle, operation):
            attempts.append(operation)
            if len(attempts) <= 2:
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))
            return real_flock(handle, operation)

        with mock.patch.object(run_index.fcntl, "flock", busy_twice):
            self.assertTrue(upsert_run(self.data_root, run_dir))

        self.assertEqual(3, len(attempts))
        self.assertIn(
            "20260809T153000Z-eacc-cc55dd",
            [row["run_id"] for row in query_runs(self.data_root)],
        )

    def test_contention_reported_as_eacces_still_times_out_as_a_wait(self):
        """The same errno, when it never clears, is a wait that ran out —
        not a permission problem to hand back raw."""
        run_dir = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-forever-4444444444444444",
            run_id="20260809T153000Z-fore-bb44ee",
            question="EACCES 一直不放的 run",
        )

        def always_busy(handle, operation):
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        with mock.patch.object(run_index.fcntl, "flock", always_busy):
            with mock.patch.object(run_index, "_BUSY_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(RunIndexBusyError):
                    upsert_run(self.data_root, run_dir)

    def test_a_writer_that_cannot_get_the_lock_says_so_rather_than_vanishing(self):
        """The one failure the lock can still produce has to be audible."""
        run_dir = write_run_dir(
            self.data_root,
            date_folder="2026-08-09",
            dir_name="2359-blocked-8888888888888888",
            run_id="20260809T153000Z-late-ff99aa",
            question="等不到鎖的 run",
        )
        err = io.StringIO()
        handle = os.open(index_lock_path(self.data_root), os.O_CREAT | os.O_RDWR, 0o644)
        self.addCleanup(os.close, handle)
        fcntl.flock(handle, fcntl.LOCK_EX)

        with mock.patch.object(run_index, "_BUSY_TIMEOUT_SECONDS", 0.05):
            written = index_finalized_run(self.data_root, run_dir, err=err)

        self.assertFalse(written)
        self.assertIn("索引", err.getvalue())
        self.assertIn("鎖", err.getvalue())

    def test_the_lock_file_is_never_read_as_a_run_by_anyone(self):
        rebuild_index(self.data_root)

        self.assertTrue(index_lock_path(self.data_root).is_file())
        summary = rebuild_index(self.data_root)

        self.assertEqual(len(self.run_ids), summary["indexed"])
        self.assertEqual([], summary["skipped"])
        self.assertEqual([], summary["unexpected_date_folders"])
        self.assertIsNone(resolve_run_dir(self.data_root, INDEX_LOCK_NAME))

    def test_a_rebuild_racing_a_repair_of_a_corrupt_index_still_ends_usable(self):
        """The interleaving the reviewers reported, in the shape it can still
        take: one process starts while the index is corrupt, another repairs
        it meanwhile, and the first finishes afterwards."""
        index_db_path(self.data_root).write_bytes(b"not a database" * 400)
        started = self.signals / "started"

        slow = self.child(
            SLOW_REBUILD.format(code_root=str(CODE_ROOT)), self.data_root, started
        )
        while not started.exists() and slow.poll() is None:
            time.sleep(0.002)
        repair = self.child(
            SLOW_REBUILD.format(code_root=str(CODE_ROOT)),
            self.data_root,
            self.signals / "repair",
        )
        repair_out, repair_err = repair.communicate(timeout=120)
        slow_out, slow_err = slow.communicate(timeout=120)

        self.assertEqual(0, repair.returncode, repair_err)
        self.assertEqual(0, slow.returncode, slow_err)
        self.assertEqual(
            sorted(self.run_ids),
            sorted(row["run_id"] for row in query_runs(self.data_root)),
        )


class RunbookDeletionGuardTest(unittest.TestCase):
    """The runbook's delete guard, run exactly as an operator would paste it.

    Verifying each ``test`` on its own would pass even if nothing chained
    them together — a bare list of tests followed by a ``printf`` reports
    success whatever the tests said. So the block is lifted out of the
    document verbatim and the whole thing is executed, and what is asserted
    is the block's own exit status and whether it announced a target.
    """

    RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "operator-runbook.md"
    SECTION = "## 8. 精確 Run ID 清理"

    def setUp(self):
        if shutil.which("bash") is None or shutil.which("realpath") is None:
            self.skipTest("需要 bash 與 realpath")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.data_root = self.workspace / "data"
        self.code_root = self.workspace / "code"
        self.code_root.mkdir(parents=True)
        self.good = self.data_root / "runs" / "2026-08-07" / "0007-good-1111111111111111"
        self.good.mkdir(parents=True)

    def block(self):
        """Return the §8 bash block, exactly as the document has it."""
        lines = self.RUNBOOK.read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(self.SECTION))
        opening = next(
            i for i in range(start, len(lines)) if lines[i].strip() == "```bash"
        )
        closing = next(
            i for i in range(opening + 1, len(lines)) if lines[i].strip() == "```"
        )
        return "\n".join(lines[opening + 1 : closing])

    def guard(self, run_dir):
        script = 'DATA_ROOT={}\nRUN_DIR={}\n{}\n'.format(
            shlex.quote(str(self.data_root)), shlex.quote(str(run_dir)), self.block()
        )
        completed = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True
        )
        return completed.returncode, completed.stdout, completed.stderr

    def test_the_block_lifted_from_the_document_is_the_one_being_tested(self):
        block = self.block()

        self.assertIn("confirm_run_dir_for_deletion", block)
        self.assertIn("確認目標", block)

    def test_a_real_run_directory_is_confirmed(self):
        code, out, err = self.guard(self.good)

        self.assertEqual(0, code, err)
        self.assertIn("確認目標", out)
        self.assertIn(str(self.good), out)

    def test_every_forbidden_target_is_refused_by_the_whole_block(self):
        forbidden = {
            "runs_root": self.data_root / "runs",
            "date_folder": self.good.parent,
            "data_root": self.data_root,
            "code_root": self.code_root,
            "workspace_root": self.workspace,
            "empty": "",
            "missing": self.data_root / "runs" / "2026-08-07" / "0007-gone-2222222222222222",
            "file_not_directory": self.RUNBOOK,
        }
        for name, target in forbidden.items():
            with self.subTest(name):
                code, out, err = self.guard(target)

                self.assertNotEqual(0, code, "{} 竟然通過了：{}".format(name, out))
                self.assertNotIn("確認目標", out)
                self.assertIn("拒絕", out + err)

    def test_a_directory_outside_the_data_root_is_refused_even_at_the_right_depth(self):
        """深度對了還不夠：日期夾的父目錄必須真的是這個 Data Root 的 runs/。"""
        elsewhere = self.workspace / "other" / "2026-08-07" / "0007-x-3333333333333333"
        elsewhere.mkdir(parents=True)

        code, out, err = self.guard(elsewhere)

        self.assertNotEqual(0, code)
        self.assertNotIn("確認目標", out)


class OutcomeColumnTest(RunIndexTestCase):
    """Ticket 12's column, read off disk like every other one.

    The three states a record can be in are three different answers, and the
    dangerous one is the third: a file that is there but will not read is not
    "not checked yet". Treating it as that would let the next sweep overwrite
    whatever it held, which is the one thing a write-once artifact exists to
    prevent.
    """

    def test_a_run_with_no_outcome_record_is_pending(self):
        run_dir = write_run_dir(self.data_root)

        self.assertIsNone(outcome_verdict(run_dir))
        self.index(run_dir)
        self.assertIsNone(self.only_row()["outcome"])

    def test_a_recorded_verdict_reaches_the_column(self):
        for verdict in OUTCOME_VERDICTS:
            with self.subTest(verdict):
                store = tempfile.TemporaryDirectory()
                self.addCleanup(store.cleanup)
                data_root = Path(store.name) / "data"
                (data_root / "runs").mkdir(parents=True)
                run_dir = write_run_dir(data_root)
                write_outcome(run_dir, verdict)

                upsert_run(data_root, run_dir)

                self.assertEqual(verdict, query_runs(data_root)[0]["outcome"])

    def test_a_record_that_will_not_decode_is_neither_pending_nor_a_verdict(self):
        run_dir = write_run_dir(self.data_root)
        (run_dir / OUTCOME_RECORD_NAME).write_text("{ not json", encoding="utf-8")

        self.index(run_dir)

        self.assertEqual(OUTCOME_UNREADABLE, outcome_verdict(run_dir))
        self.assertEqual(OUTCOME_UNREADABLE, self.only_row()["outcome"])

    def test_a_record_that_is_not_an_object_is_unreadable(self):
        run_dir = write_run_dir(self.data_root)
        _write_json(run_dir / OUTCOME_RECORD_NAME, ["hit"])

        self.assertEqual(OUTCOME_UNREADABLE, outcome_verdict(run_dir))

    def test_a_record_naming_a_verdict_this_build_does_not_know_is_unreadable(self):
        run_dir = write_run_dir(self.data_root)
        write_outcome(run_dir, "probably")

        self.assertEqual(OUTCOME_UNREADABLE, outcome_verdict(run_dir))

    def test_a_record_with_no_verdict_at_all_is_unreadable(self):
        run_dir = write_run_dir(self.data_root)
        _write_json(run_dir / OUTCOME_RECORD_NAME, {"schema_version": 1})

        self.assertEqual(OUTCOME_UNREADABLE, outcome_verdict(run_dir))

    def test_an_unopenable_record_is_unreadable_rather_than_pending(self):
        run_dir = write_run_dir(self.data_root)
        path = run_dir / OUTCOME_RECORD_NAME
        write_outcome(run_dir, OUTCOME_HIT)
        path.chmod(0)
        self.addCleanup(path.chmod, stat.S_IRUSR | stat.S_IWUSR)

        self.assertEqual(OUTCOME_UNREADABLE, outcome_verdict(run_dir))

    def test_the_unreadable_marker_is_never_one_of_the_writable_verdicts(self):
        """It is derived, never written: no record may claim it."""
        self.assertNotIn(OUTCOME_UNREADABLE, OUTCOME_VERDICTS)
        self.assertNotIn(OUTCOME_PENDING, OUTCOME_VERDICTS)

    def test_run_row_reports_the_verdict_without_touching_the_index(self):
        run_dir = write_run_dir(self.data_root)
        write_outcome(run_dir, OUTCOME_MISS)

        self.assertEqual(OUTCOME_MISS, run_row(run_dir)["outcome"])


class BackfillKeepsTheOutcomeTest(RunIndexTestCase):
    """The one test Ticket 12 must never lose. Read this before deleting it.

    ``index-backfill`` empties the table and rebuilds every row from the run
    directories. If ``run_row`` did not read ``outcome.json``, then *every*
    backfill — the operation the operator is told to run whenever the index
    looks wrong — would silently erase every answer anyone had checked. The
    verdicts would not come back, because a rebuild has nothing to read them
    from except the disk.

    So this is not a test that a feature works. It is a test that the index
    stayed disposable: everything in it can be reconstructed from the run
    directories, which is the property the whole module is built on.
    """

    def test_a_full_rebuild_reproduces_every_recorded_verdict(self):
        runs = {}
        for index, verdict in enumerate(OUTCOME_VERDICTS):
            run_id = "20260801T02000{}Z-2330-9d05c{}".format(index, index)
            run_dir = write_run_dir(
                self.data_root,
                dir_name="100{}-2330-未來七天會不會漲-c30df4c81e36072{}".format(index, index),
                run_id=run_id,
            )
            write_outcome(run_dir, verdict)
            runs[run_id] = verdict
        pending_id = "20260801T029999Z-2330-9d05cff"
        write_run_dir(
            self.data_root,
            dir_name="1099-2330-未來七天會不會漲-c30df4c81e3607ff",
            run_id=pending_id,
        )
        runs[pending_id] = None

        summary = rebuild_index(self.data_root)

        self.assertEqual(len(runs), summary["indexed"])
        self.assertEqual(
            runs, {row["run_id"]: row["outcome"] for row in query_runs(self.data_root)}
        )

    def test_verifying_then_backfilling_leaves_the_verdict_in_place(self):
        """The operator's actual sequence: check an answer, then rebuild."""
        run_dir = write_run_dir(self.data_root)
        self.index(run_dir)
        write_outcome(run_dir, OUTCOME_HIT)
        upsert_run(self.data_root, run_dir)
        self.assertEqual(OUTCOME_HIT, self.only_row()["outcome"])

        rebuild_index(self.data_root)

        self.assertEqual(OUTCOME_HIT, self.only_row()["outcome"])

    def test_deleting_the_index_entirely_still_recovers_the_verdict(self):
        run_dir = write_run_dir(self.data_root)
        write_outcome(run_dir, OUTCOME_UNVERIFIABLE)
        self.index(run_dir)
        index_db_path(self.data_root).unlink()

        rebuild_index(self.data_root)

        self.assertEqual(OUTCOME_UNVERIFIABLE, self.only_row()["outcome"])

    def test_the_rebuild_would_notice_if_run_row_stopped_reading_the_record(self):
        """FP direction: the assertion above has to be able to fail.

        Removing the record and rebuilding must leave the column empty. If it
        did not, the three tests above would pass against an index that was
        remembering rather than rebuilding, and the erasure they guard against
        would go unnoticed.
        """
        run_dir = write_run_dir(self.data_root)
        write_outcome(run_dir, OUTCOME_HIT)
        rebuild_index(self.data_root)
        self.assertEqual(OUTCOME_HIT, self.only_row()["outcome"])

        (run_dir / OUTCOME_RECORD_NAME).unlink()
        rebuild_index(self.data_root)

        self.assertIsNone(self.only_row()["outcome"])


class OutcomeSummaryTest(RunIndexTestCase):
    """The numbers the statistics page shows, counted once, here.

    The hit rate's denominator is the whole point of this class: a prediction
    nobody could check is not a prediction that was wrong.
    """

    def write(self, index, verdict=None, confidence_level="green"):
        run_dir = write_run_dir(
            self.data_root,
            dir_name="10{:02d}-2330-未來七天會不會漲-c30df4c81e3600{:02d}".format(index, index),
            run_id="20260801T0200{:02d}Z-2330-9d05c{:02d}".format(index, index),
            confidence_level=confidence_level,
        )
        if verdict is not None:
            write_outcome(run_dir, verdict)
        return run_dir

    def summary(self):
        rebuild_index(self.data_root)
        return outcome_summary(self.data_root)

    def test_an_empty_index_reports_no_hit_rate_rather_than_zero_percent(self):
        """Nobody has been right 0% of the time before anybody has been checked."""
        totals = self.summary()["totals"]

        self.assertEqual(0, totals["total"])
        self.assertEqual(0, totals["scored"])
        self.assertIsNone(totals["hit_rate"])

    def test_every_state_a_run_can_be_in_is_counted_separately(self):
        self.write(1, OUTCOME_HIT)
        self.write(2, OUTCOME_HIT)
        self.write(3, OUTCOME_MISS)
        self.write(4, OUTCOME_UNVERIFIABLE)
        self.write(5, None)

        totals = self.summary()["totals"]

        self.assertEqual(2, totals[OUTCOME_HIT])
        self.assertEqual(1, totals[OUTCOME_MISS])
        self.assertEqual(1, totals[OUTCOME_UNVERIFIABLE])
        self.assertEqual(1, totals[OUTCOME_PENDING])
        self.assertEqual(0, totals[OUTCOME_UNREADABLE])
        self.assertEqual(5, totals["total"])

    def test_the_hit_rate_is_hits_over_hits_plus_misses(self):
        self.write(1, OUTCOME_HIT)
        self.write(2, OUTCOME_HIT)
        self.write(3, OUTCOME_MISS)

        totals = self.summary()["totals"]

        self.assertEqual(3, totals["scored"])
        self.assertAlmostEqual(2 / 3, totals["hit_rate"])

    def test_a_run_nobody_could_verify_does_not_change_the_hit_rate(self):
        """Ticket 12 §⑤: 不可自動驗證 is not 未命中."""
        self.write(1, OUTCOME_HIT)
        self.write(2, OUTCOME_MISS)
        before = self.summary()["totals"]

        self.write(3, OUTCOME_UNVERIFIABLE)
        after = self.summary()["totals"]

        self.assertEqual(0.5, before["hit_rate"])
        self.assertEqual(before["hit_rate"], after["hit_rate"])
        self.assertEqual(before["scored"], after["scored"])
        self.assertEqual(3, after["total"])

    def test_a_run_still_waiting_does_not_change_the_hit_rate(self):
        self.write(1, OUTCOME_HIT)
        self.write(2, OUTCOME_MISS)
        before = self.summary()["totals"]

        self.write(3, None)
        after = self.summary()["totals"]

        self.assertEqual(before["hit_rate"], after["hit_rate"])
        self.assertEqual(before["scored"], after["scored"])

    def test_a_record_that_will_not_read_does_not_change_the_hit_rate_either(self):
        self.write(1, OUTCOME_HIT)
        self.write(2, OUTCOME_MISS)
        before = self.summary()["totals"]

        broken = self.write(3, None)
        (broken / OUTCOME_RECORD_NAME).write_text("{ not json", encoding="utf-8")
        after = self.summary()["totals"]

        self.assertEqual(1, after[OUTCOME_UNREADABLE])
        self.assertEqual(before["hit_rate"], after["hit_rate"])
        self.assertEqual(before["scored"], after["scored"])

    def test_a_miss_does_move_the_hit_rate(self):
        """FN direction: the denominator is not simply ignoring everything."""
        self.write(1, OUTCOME_HIT)
        before = self.summary()["totals"]

        self.write(2, OUTCOME_MISS)
        after = self.summary()["totals"]

        self.assertEqual(1.0, before["hit_rate"])
        self.assertEqual(0.5, after["hit_rate"])

    def test_the_counts_are_broken_down_by_light(self):
        self.write(1, OUTCOME_HIT, confidence_level="green")
        self.write(2, OUTCOME_MISS, confidence_level="green")
        self.write(3, OUTCOME_HIT, confidence_level="blue")

        by_level = self.summary()["by_level"]

        self.assertEqual(0.5, by_level["green"]["hit_rate"])
        self.assertEqual(1.0, by_level["blue"]["hit_rate"])
        self.assertEqual(2, by_level["green"]["total"])

    def test_a_light_this_build_never_heard_of_is_still_counted(self):
        """The index does not own the list of lights and does not filter on it."""
        self.write(1, OUTCOME_HIT, confidence_level="turquoise")

        by_level = self.summary()["by_level"]

        self.assertEqual(1, by_level["turquoise"][OUTCOME_HIT])

    def test_a_run_whose_report_named_no_light_is_still_counted_somewhere(self):
        self.write(1, OUTCOME_HIT, confidence_level=None)

        summary = self.summary()

        self.assertEqual(1, summary["totals"][OUTCOME_HIT])
        self.assertEqual(1, sum(level[OUTCOME_HIT] for level in summary["by_level"].values()))

    def test_the_levels_add_up_to_the_totals(self):
        self.write(1, OUTCOME_HIT, confidence_level="green")
        self.write(2, OUTCOME_MISS, confidence_level="blue")
        self.write(3, OUTCOME_UNVERIFIABLE, confidence_level="red")
        self.write(4, None, confidence_level="red")

        summary = self.summary()

        for state in (OUTCOME_HIT, OUTCOME_MISS, OUTCOME_UNVERIFIABLE, OUTCOME_PENDING):
            self.assertEqual(
                summary["totals"][state],
                sum(level[state] for level in summary["by_level"].values()),
                state,
            )

    def test_a_missing_index_is_reported_rather_than_counted_as_nothing(self):
        """"I could not look" must not come back as "there is nothing there"."""
        with self.assertRaises(RunIndexError):
            outcome_summary(self.data_root)


if __name__ == "__main__":
    unittest.main()
