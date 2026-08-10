"""The file inbox that carries Codex seat prompts and raw results."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.codex_inbox import (
    EXIT_OK,
    EXIT_REJECTED,
    InboxError,
    ensure_inbox,
    inbox_root,
    poll_results,
    run_submit_seat,
    write_seat_prompt,
    write_seat_result,
)

RUN_ID = "20260101T000000Z-btc-abc123"
RAW_ENVELOPE = '{"seat_id": "derivatives", "evidence_cards": []}'


class InboxLayoutTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def test_inbox_root_sits_outside_the_run_directory(self):
        root = inbox_root(self.data_root, RUN_ID)

        self.assertEqual(self.data_root / "inbox" / RUN_ID, root)
        self.assertFalse(root.is_relative_to(self.data_root / "runs"))

    def test_ensure_inbox_creates_the_three_channels(self):
        root = ensure_inbox(self.data_root, RUN_ID)

        for name in ("prompts", "requests", "results"):
            self.assertTrue((root / name).is_dir(), "缺少 {}".format(name))

    def test_ensure_inbox_is_idempotent(self):
        first = ensure_inbox(self.data_root, RUN_ID)
        second = ensure_inbox(self.data_root, RUN_ID)

        self.assertEqual(first, second)

    def test_run_id_containing_a_path_is_rejected(self):
        with self.assertRaises(InboxError):
            inbox_root(self.data_root, "../escape")


class SeatPromptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def test_prompt_is_written_once_and_readable(self):
        path = write_seat_prompt(
            self.data_root, RUN_ID, "news", "查證 BTC 新聞與事件時間線"
        )

        self.assertEqual(
            "查證 BTC 新聞與事件時間線", path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            inbox_root(self.data_root, RUN_ID) / "prompts" / "news.txt", path
        )

    def test_second_prompt_for_the_same_seat_is_rejected(self):
        write_seat_prompt(self.data_root, RUN_ID, "news", "第一版")

        with self.assertRaises(InboxError):
            write_seat_prompt(self.data_root, RUN_ID, "news", "第二版")

        self.assertEqual(
            "第一版",
            (inbox_root(self.data_root, RUN_ID) / "prompts" / "news.txt").read_text(
                encoding="utf-8"
            ),
        )


class SeatResultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def test_result_records_the_frozen_contract_fields(self):
        path = write_seat_result(
            self.data_root,
            RUN_ID,
            "derivatives",
            "derivatives-codex-1",
            RAW_ENVELOPE,
            submitted_at_utc="2026-01-01T00:05:00Z",
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "schema_version": "1.0.0",
                "run_id": RUN_ID,
                "seat_id": "derivatives",
                "attempt_id": "derivatives-codex-1",
                "submitted_at_utc": "2026-01-01T00:05:00Z",
                "raw_output": RAW_ENVELOPE,
            },
            payload,
        )
        self.assertEqual(
            inbox_root(self.data_root, RUN_ID)
            / "results"
            / "derivatives.derivatives-codex-1.json",
            path,
        )

    def test_default_submitted_at_is_stamped_in_the_utc_record_format(self):
        path = write_seat_result(
            self.data_root, RUN_ID, "news", "news-codex-1", RAW_ENVELOPE
        )

        stamp = json.loads(path.read_text(encoding="utf-8"))["submitted_at_utc"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_duplicate_submission_is_rejected_and_keeps_the_first(self):
        write_seat_result(
            self.data_root, RUN_ID, "news", "news-codex-1", "第一次"
        )

        with self.assertRaises(InboxError):
            write_seat_result(
                self.data_root, RUN_ID, "news", "news-codex-1", "第二次"
            )

        results = poll_results(self.data_root, RUN_ID, set())
        self.assertEqual([("news", "news-codex-1", "第一次")], results)

    def test_non_codex_seat_is_rejected(self):
        for seat_id in (
            "onchain",
            "official-events",
            "social-macro",
            "counter-evidence",
        ):
            with self.subTest(seat_id=seat_id):
                with self.assertRaises(InboxError):
                    write_seat_result(
                        self.data_root, RUN_ID, seat_id, "attempt-1", RAW_ENVELOPE
                    )

    def test_attempt_id_containing_a_path_is_rejected(self):
        with self.assertRaises(InboxError):
            write_seat_result(
                self.data_root, RUN_ID, "news", "../escape", RAW_ENVELOPE
            )

    def test_raw_output_must_be_text(self):
        with self.assertRaises(InboxError):
            write_seat_result(self.data_root, RUN_ID, "news", "news-codex-1", {})


class PollResultsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def test_missing_inbox_polls_empty(self):
        self.assertEqual([], poll_results(self.data_root, RUN_ID, set()))

    def test_polling_is_incremental(self):
        seen = set()
        write_seat_result(
            self.data_root, RUN_ID, "news", "news-codex-1", "第一份"
        )

        first = poll_results(self.data_root, RUN_ID, seen)
        self.assertEqual([("news", "news-codex-1", "第一份")], first)
        self.assertEqual([], poll_results(self.data_root, RUN_ID, seen))

        write_seat_result(
            self.data_root, RUN_ID, "derivatives", "derivatives-codex-1", "第二份"
        )
        second = poll_results(self.data_root, RUN_ID, seen)
        self.assertEqual([("derivatives", "derivatives-codex-1", "第二份")], second)

    def test_broken_files_are_skipped_without_losing_valid_results(self):
        results_dir = ensure_inbox(self.data_root, RUN_ID) / "results"
        (results_dir / "news.broken.json").write_text("{ not json", encoding="utf-8")
        (results_dir / "derivatives.partial.json").write_text(
            json.dumps({"schema_version": "1.0.0"}), encoding="utf-8"
        )
        write_seat_result(
            self.data_root, RUN_ID, "spot-technical", "spot-technical-codex-1", "有效"
        )

        seen = set()
        results = poll_results(self.data_root, RUN_ID, seen)

        self.assertEqual(
            [("spot-technical", "spot-technical-codex-1", "有效")], results
        )
        self.assertEqual({("spot-technical", "spot-technical-codex-1")}, seen)


class SubmitSeatCommandTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def submit(self, raw, seat_id="news", attempt_id="news-codex-1", argv=None):
        out, err = io.StringIO(), io.StringIO()
        code = run_submit_seat(
            argv
            if argv is not None
            else [
                "--run-id",
                RUN_ID,
                "--seat-id",
                seat_id,
                "--attempt-id",
                attempt_id,
                "--data-root",
                str(self.data_root),
            ],
            io.StringIO(raw),
            out,
            err,
        )
        return code, out.getvalue(), err.getvalue()

    def test_stdin_raw_output_is_stored_verbatim(self):
        code, out, err = self.submit(RAW_ENVELOPE + "\n")

        self.assertEqual(EXIT_OK, code, err)
        results = poll_results(self.data_root, RUN_ID, set())
        self.assertEqual([("news", "news-codex-1", RAW_ENVELOPE + "\n")], results)
        self.assertIn("news.news-codex-1.json", out)

    def test_blank_stdin_is_rejected(self):
        code, _, err = self.submit("   \n")

        self.assertEqual(EXIT_REJECTED, code)
        self.assertIn("stdin", err)
        self.assertEqual([], poll_results(self.data_root, RUN_ID, set()))

    def test_non_codex_seat_is_rejected(self):
        code, _, err = self.submit(RAW_ENVELOPE, seat_id="onchain")

        self.assertEqual(EXIT_REJECTED, code)
        self.assertIn("onchain", err)

    def test_duplicate_submission_is_rejected(self):
        self.assertEqual(EXIT_OK, self.submit(RAW_ENVELOPE)[0])

        code, _, err = self.submit(RAW_ENVELOPE)

        self.assertEqual(EXIT_REJECTED, code)
        self.assertTrue(err.strip())

    def test_missing_required_argument_is_rejected(self):
        code, _, err = self.submit(RAW_ENVELOPE, argv=["--run-id", RUN_ID])

        self.assertEqual(EXIT_REJECTED, code)
        self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
