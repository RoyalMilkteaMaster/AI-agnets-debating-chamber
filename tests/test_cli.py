"""The single command a user runs, exercised through the real entrypoint."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.cli import DEFAULT_DATA_ROOT, main

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
        run_dirs = list((self.data_root / "runs").glob("*-btc-*"))
        self.assertEqual(1, len(run_dirs))
        run_dir = run_dirs[0]

        self.assertIn(run_dir.name, out)
        self.assertIn(str(self.data_root), out)
        self.assertIn(str(run_dir / "report.md"), out)
        self.assertIn(str(run_dir / "report.html"), out)

    def test_run_produces_all_six_required_artifacts(self):
        code, _, err = self.run_question()
        self.assertEqual(0, code, err)

        run_dir = next((self.data_root / "runs").glob("*-btc-*"))
        for name in REQUIRED_ARTIFACTS:
            self.assertTrue((run_dir / name).is_file(), "缺少 {}".format(name))

    def test_second_run_gets_a_different_run_id_and_keeps_the_first(self):
        self.assertEqual(0, self.run_question()[0])
        first = next((self.data_root / "runs").glob("*-btc-*"))
        first_report = (first / "report.md").read_bytes()

        self.assertEqual(0, self.run_question()[0])
        run_ids = sorted(path.name for path in (self.data_root / "runs").glob("*-btc-*"))

        self.assertEqual(2, len(run_ids))
        self.assertEqual(2, len(set(run_ids)))
        self.assertEqual(first_report, (first / "report.md").read_bytes())

    def test_unsupported_asset_exits_non_zero_and_writes_nothing(self):
        code, out, err = self.run_question("分析 DOGE 過去 14 日市場狀態")

        self.assertEqual(2, code)
        self.assertIn("DOGE", err)
        self.assertEqual("", out)
        self.assertFalse((self.data_root / "runs").exists())

    def test_lowercase_unsupported_asset_exits_non_zero_and_writes_nothing(self):
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
            "分析 DOGE 過去 14 日市場狀態",
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
            run_dir = next((Path(tmp) / "runs").glob("*-btc-*"))
            for name in REQUIRED_ARTIFACTS:
                self.assertTrue((run_dir / name).is_file(), "缺少 {}".format(name))
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("fake", manifest["provider_mode"])
            self.assertEqual(run_dir.name, manifest["run_id"])


if __name__ == "__main__":
    unittest.main()
