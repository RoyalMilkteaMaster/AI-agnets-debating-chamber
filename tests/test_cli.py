"""The single command a user runs, exercised through the real entrypoint."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.cli import DEFAULT_DATA_ROOT, build_parser, main
from hoya_market_agents.run_index import COLUMNS
from hoya_market_agents.run_store import resolve_run_dir
from hoya_market_agents.webapp import DEFAULT_PORT

CODE_ROOT = Path(__file__).resolve().parent.parent
QUESTION = "分析 BTC 過去 14 日市場狀態"
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "evidence.jsonl",
    "debate.jsonl",
    "votes.json",
    "report.md",
    "report.html",
)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def run_question(self, question=QUESTION):
        return self.run_cli(
            "run",
            "--provider-mode",
            "fake",
            "--question",
            question,
            "--data-root",
            str(self.data_root),
        )

    def test_one_command_completes_a_run_and_reports_its_paths(self):
        code, out, err = self.run_question()

        self.assertEqual(0, code, err)
        run_dirs = list((self.data_root / "runs").glob("*/*-btc-*"))
        self.assertEqual(1, len(run_dirs))
        run_dir = run_dirs[0]

        self.assertIn(run_dir.name, out)
        self.assertIn(str(self.data_root), out)
        self.assertIn(str(run_dir / "report.md"), out)
        self.assertIn(str(run_dir / "report.html"), out)

    def test_run_produces_all_six_required_artifacts(self):
        code, _, err = self.run_question()
        self.assertEqual(0, code, err)

        run_dir = next((self.data_root / "runs").glob("*/*-btc-*"))
        for name in REQUIRED_ARTIFACTS:
            self.assertTrue((run_dir / name).is_file(), "缺少 {}".format(name))

    def test_second_run_gets_a_different_run_id_and_keeps_the_first(self):
        self.assertEqual(0, self.run_question()[0])
        first = next((self.data_root / "runs").glob("*/*-btc-*"))
        first_report = (first / "report.md").read_bytes()

        self.assertEqual(0, self.run_question()[0])
        run_dirs = sorted((self.data_root / "runs").glob("*/*-btc-*"))
        # 資料夾名是標籤不是身分（ADR 0005），所以比對兩份 manifest 記下的
        # run_id 本身，而不是兩個資料夾名字不一樣就算數。
        run_ids = [
            json.loads((path / "manifest.json").read_text(encoding="utf-8"))["run_id"]
            for path in run_dirs
        ]

        self.assertEqual(2, len(run_ids))
        self.assertEqual(2, len(set(run_ids)))
        for run_dir, run_id in zip(run_dirs, run_ids):
            self.assertEqual(run_dir, resolve_run_dir(self.data_root, run_id))
        self.assertEqual(first_report, (first / "report.md").read_bytes())

    def test_a_coin_outside_the_old_whitelist_now_completes_a_run(self):
        code, out, err = self.run_question("分析 DOGE 過去 14 日市場狀態")

        self.assertEqual(0, code, err)
        self.assertEqual(1, len(list((self.data_root / "runs").glob("*/*-doge-*"))))
        self.assertNotEqual("", out)

    def test_question_naming_no_asset_exits_non_zero_and_writes_nothing(self):
        code, out, err = self.run_question("分析 過去 14 日市場狀態")

        self.assertEqual(2, code)
        self.assertIn("未指名", err)
        self.assertEqual("", out)
        self.assertFalse((self.data_root / "runs").exists())

    def test_two_assets_exit_non_zero_in_the_single_asset_tracer_scope(self):
        for separator in ("、", ","):
            with self.subTest(separator=separator):
                code, out, err = self.run_question(
                    "比較 BTC{}doge 過去 14 日市場狀態".format(separator)
                )

                self.assertEqual(2, code)
                self.assertIn("DOGE", err)
                self.assertEqual("", out)
                self.assertFalse((self.data_root / "runs").exists())

    def test_only_the_fake_provider_mode_is_accepted_in_this_version(self):
        with contextlib.redirect_stderr(io.StringIO()) as argparse_stderr:
            with self.assertRaises(SystemExit):
                self.run_cli(
                    "run",
                    "--provider-mode",
                    "real",
                    "--question",
                    QUESTION,
                    "--data-root",
                    str(self.data_root),
                )

        self.assertIn("invalid choice: 'real'", argparse_stderr.getvalue())
        self.assertFalse((self.data_root / "runs").exists())

    def test_default_data_root_is_the_sibling_data_directory(self):
        self.assertEqual("hoya-bit-market-agents_data", DEFAULT_DATA_ROOT.name)
        self.assertEqual(CODE_ROOT.parent, DEFAULT_DATA_ROOT.parent)

    def test_core_prepare_launch_requires_only_the_question_and_internal_data_root(self):
        code, out, err = self.run_cli(
            "prepare-launch",
            "--question",
            QUESTION,
            "--data-root",
            str(self.data_root),
        )

        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual("PREPARED", payload["status"])
        self.assertRegex(
            payload["competition_run_id"],
            r"^\d{8}T\d{6}Z-btc-[0-9a-f]{6}$",
        )
        self.assertRegex(payload["preflight_challenge"], r"^[A-Za-z0-9_-]{24,128}$")
        self.assertRegex(payload["competition_challenge"], r"^[A-Za-z0-9_-]{24,128}$")
        self.assertNotEqual(
            payload["preflight_challenge"], payload["competition_challenge"]
        )
        self.assertFalse((self.data_root / "runs").exists())

    def test_prepare_launch_rejects_bad_question_without_writing_a_reservation(self):
        code, out, err = self.run_cli(
            "prepare-launch",
            "--question",
            "分析 BTC 過去幾週市場狀態",
            "--data-root",
            str(self.data_root),
        )

        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("NOT PREPARED", err)
        self.assertFalse((self.data_root / "preflight").exists())

    def test_prepare_launch_rejects_code_root_as_data_root(self):
        code, out, err = self.run_cli(
            "prepare-launch",
            "--question",
            QUESTION,
            "--data-root",
            str(CODE_ROOT),
        )

        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("Data Root", err)


class RunIndexCliTest(unittest.TestCase):
    """The two commands an operator has for the rebuildable run index."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def drill(self, question):
        code, _, err = self.run_cli(
            "drill",
            "--provider-mode",
            "fake",
            "--question",
            question,
            "--data-root",
            str(self.data_root),
        )
        self.assertEqual(0, code, err)

    def backfill(self):
        code, out, err = self.run_cli("index-backfill", "--data-root", str(self.data_root))
        self.assertEqual(0, code, err)
        return json.loads(out)

    def query(self, *argv):
        code, out, err = self.run_cli(
            "index-query", "--data-root", str(self.data_root), *argv
        )
        self.assertEqual(0, code, err)
        return json.loads(out)

    def test_backfill_indexes_the_runs_already_on_disk(self):
        self.drill("2330 未來七天會不會漲")

        summary = self.backfill()

        self.assertEqual(1, summary["indexed"])
        self.assertEqual([], summary["skipped"])
        self.assertEqual([], summary["unexpected_date_folders"])

    def test_backfill_repairs_a_corrupt_index_without_the_operator_deleting_it(self):
        """票面第一句「損毀可全量重建」——照著錯誤訊息做就要成功。"""
        self.drill("2330 未來七天會不會漲")
        self.backfill()
        database = self.data_root / "runs" / "index.db"
        database.write_bytes(b"not a database" * 400)
        failed_code, _, failed_err = self.run_cli(
            "index-query", "--data-root", str(self.data_root)
        )

        summary = self.backfill()
        rows = self.query()

        self.assertEqual(1, failed_code)
        self.assertIn("index-backfill", failed_err)
        self.assertEqual(1, summary["indexed"])
        self.assertEqual(1, len(rows))

    def test_backfill_reports_a_directory_it_cannot_read_instead_of_emptying(self):
        self.drill("2330 未來七天會不會漲")
        self.backfill()
        before = self.query()
        date_dir = next((self.data_root / "runs").glob("2*"))
        os.chmod(date_dir, 0o000)
        self.addCleanup(os.chmod, date_dir, 0o755)

        code, out, err = self.run_cli(
            "index-backfill", "--data-root", str(self.data_root)
        )

        self.assertEqual(1, code)
        self.assertIn("BACKFILL FAILED", err)
        os.chmod(date_dir, 0o755)
        self.assertEqual(before, self.query())

    def test_query_prints_every_column_of_the_indexed_run(self):
        self.drill("2330 未來七天會不會漲")
        self.backfill()

        rows = self.query()

        self.assertEqual(1, len(rows))
        # 「每一欄」就是每一欄：欄位集合必須逐字等於模組宣告的那一組。
        self.assertEqual(set(COLUMNS), set(rows[0]))
        self.assertEqual(
            {name for name in COLUMNS if name != "outcome"},
            {name for name, value in rows[0].items() if value is not None},
        )
        self.assertEqual("2330 未來七天會不會漲", rows[0]["question"])
        self.assertEqual("tw_stock", rows[0]["asset_class"])
        self.assertEqual(["2330"], rows[0]["assets"])
        self.assertIsNone(rows[0]["outcome"])
        self.assertTrue(
            (self.data_root / "runs" / rows[0]["report_path"]).is_file(), rows[0]
        )

    def test_a_keyword_on_the_command_line_is_a_literal_substring(self):
        self.drill("DOGE 會漲 50 元嗎")
        self.backfill()

        self.assertEqual([], self.query("--keyword", "50%"))
        self.assertEqual(1, len(self.query("--keyword", "50 元")))

    def test_the_filters_accept_values_no_argument_list_declares(self):
        """argparse 不得先擋下來——擋掉就等於在 CLI 複製一份合法值清單。"""
        self.drill("2330 未來七天會不會漲")
        self.backfill()
        indexed = self.query()[0]

        self.assertEqual([], self.query("--asset-class", "not-a-declared-class"))
        self.assertEqual([], self.query("--confidence", "chartreuse"))
        # 對照組：真實存在的值照樣查得到，證明上面兩個空結果是「查無此值」
        # 而不是「篩選壞掉」。
        self.assertEqual(1, len(self.query("--asset-class", indexed["asset_class"])))
        self.assertEqual(
            1, len(self.query("--confidence", indexed["confidence_level"]))
        )

    def test_querying_before_any_backfill_exits_non_zero_and_says_why(self):
        code, out, err = self.run_cli("index-query", "--data-root", str(self.data_root))

        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("index-backfill", err)

    def test_a_negative_limit_is_refused_rather_than_listing_everything(self):
        self.drill("2330 未來七天會不會漲")
        self.backfill()

        code, out, err = self.run_cli(
            "index-query", "--data-root", str(self.data_root), "--limit", "-1"
        )

        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("limit", err)
        self.assertEqual(1, len(self.query("--limit", "1")))

    def test_an_index_the_user_cannot_open_exits_non_zero_instead_of_tracebacking(self):
        self.drill("2330 未來七天會不會漲")
        self.backfill()
        database = self.data_root / "runs" / "index.db"
        os.chmod(database, 0o000)
        self.addCleanup(os.chmod, database, 0o600)

        code, out, err = self.run_cli("index-query", "--data-root", str(self.data_root))

        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("QUERY FAILED", err)


class WebappCliTest(unittest.TestCase):
    """The command that puts the resident pages on 127.0.0.1."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def log_records(self):
        path = self.data_root / "logs" / "webapp.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_the_port_defaults_to_the_one_the_ticket_names(self):
        args = build_parser().parse_args(["webapp"])

        self.assertEqual(DEFAULT_PORT, args.port)
        self.assertEqual(str(DEFAULT_DATA_ROOT), args.data_root)

    def test_an_occupied_port_exits_non_zero_and_says_which_port(self):
        taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]

        code, out, err = self.run_cli(
            "webapp", "--data-root", str(self.data_root), "--port", str(port)
        )

        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn(str(port), err)
        self.assertIn("占用", err)

    def test_the_refusal_is_in_the_log_because_the_log_opened_first(self):
        taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]

        self.run_cli("webapp", "--data-root", str(self.data_root), "--port", str(port))

        events = [record["event"] for record in self.log_records()]
        self.assertEqual(["server_start", "server_start_failed"], events)


class RetiredLiveCommandTest(unittest.TestCase):
    """The old ``live`` command is gone; the calls around it still work."""

    def test_the_live_subcommand_is_no_longer_accepted(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["live"])

    def test_launch_still_accepts_the_flag_that_used_to_turn_it_off(self):
        """``--no-live`` is kept so an existing call does not start failing."""
        args = build_parser().parse_args(
            ["launch", "--question", "BTC 會不會漲", "--no-live"]
        )

        self.assertTrue(args.no_live)

    def test_launch_without_that_flag_still_parses_the_same_way(self):
        args = build_parser().parse_args(["launch", "--question", "BTC 會不會漲"])

        self.assertFalse(args.no_live)

    def test_the_flag_says_it_no_longer_changes_anything(self):
        """A flag that quietly does nothing is worse than one that says so."""
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["launch", "--help"])

        self.assertIn("沒有任何差別", printed.getvalue())


class CliSubprocessTest(unittest.TestCase):
    """The literal command from the ticket, run as its own process."""

    def test_module_entrypoint_runs_offline_with_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "hoya_market_agents",
                    "run",
                    "--provider-mode",
                    "fake",
                    "--question",
                    QUESTION,
                    "--data-root",
                    tmp,
                ],
                cwd=str(CODE_ROOT),
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", "HOME": tmp, "LC_ALL": "C.UTF-8"},
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            run_dir = next((Path(tmp) / "runs").glob("*/*-btc-*"))
            for name in REQUIRED_ARTIFACTS:
                self.assertTrue((run_dir / name).is_file(), "缺少 {}".format(name))
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("fake", manifest["provider_mode"])
            # ADR 0005：資料夾名是給人看的日期＋題目標籤，run 的身分在 manifest
            # 裡；兩者相連的證明是 run_id 找得回這個目錄。
            self.assertEqual(run_dir, resolve_run_dir(Path(tmp), manifest["run_id"]))


if __name__ == "__main__":
    unittest.main()
