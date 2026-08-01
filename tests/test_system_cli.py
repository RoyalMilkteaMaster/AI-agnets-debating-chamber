"""Public CLI seams for Ticket #11 preflight and run verification."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hoya_market_agents.cli import (
    _apply_drill_observation,
    _claude_matrix,
    _fixture_system_checks,
    main,
)
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from tests.fakes import FixedClock, ScriptedTokenSource


class SystemCliTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(list(args), stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_fixture_preflight_is_reproducible_but_never_ready(self):
        code, out, err = self.run_cli(
            "preflight",
            "--provider", "system",
            "--seats", "7",
            "--mode", "fixture",
            "--preflight-id", "fixture-pass",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(1, code)
        self.assertEqual("", err)
        payload = json.loads(out)
        self.assertEqual("NOT_READY", payload["status"])
        self.assertEqual("PASS", payload["simulation_status"])
        self.assertTrue(Path(payload["manifest_path"]).is_file())

    def test_each_deliberate_failure_is_a_machine_blocker(self):
        for failure in ("login", "model", "write", "renderer"):
            with self.subTest(failure=failure):
                code, out, _ = self.run_cli(
                    "preflight",
                    "--provider", "system",
                    "--seats", "7",
                    "--mode", "fixture",
                    "--fixture-failure", failure,
                    "--preflight-id", "fixture-" + failure,
                    "--data-root", str(self.data_root),
                )
                payload = json.loads(out)
                self.assertEqual(1, code)
                self.assertEqual("FAIL", payload["simulation_status"])
                self.assertGreater(len(payload["blockers"]), 1)

    def test_real_system_preflight_without_live_codex_handoff_fails_before_ready(self):
        code, out, err = self.run_cli(
            "preflight",
            "--provider", "system",
            "--seats", "7",
            "--mode", "real",
            "--preflight-id", "real-missing-codex",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(1, code)
        self.assertEqual("", err)
        payload = json.loads(out)
        self.assertEqual("NOT_READY", payload["status"])
        self.assertIn("codex_runtime_receipts", payload["blockers"])
        self.assertIn("provider_runtime_attestation", payload["blockers"])
        self.assertEqual(
            "provider_runtime_attestation_unavailable",
            next(
                item["actual"]
                for item in payload["checks"]
                if item["check_id"] == "provider_runtime_attestation"
            ),
        )
        self.assertEqual("not_observed", next(
            item["actual"] for item in payload["checks"]
            if item["check_id"] == "codex_runtime_receipts"
        ))

    def test_not_ready_provider_preflight_does_not_issue_competition_authorization(self):
        code, out, err = self.run_cli(
            "preflight",
            "--provider", "system",
            "--seats", "7",
            "--mode", "real",
            "--preflight-id", "not-authorized",
            "--competition-run-id", "20260801T020000Z-btc-abc123",
            "--competition-challenge", "competition-challenge-1234567890",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(1, code)
        self.assertEqual("", err)
        payload = json.loads(out)
        self.assertFalse(payload["provider_capabilities_ready"])
        self.assertEqual("NOT_AUTHORIZED", payload["competition_authorization"]["status"])
        self.assertFalse(
            (self.data_root / "preflight" / "not-authorized" / "competition-authorization.json").exists()
        )

    def test_unsafe_preflight_id_is_rejected_before_any_probe_write(self):
        code, out, err = self.run_cli(
            "preflight",
            "--provider", "system",
            "--seats", "7",
            "--mode", "real",
            "--preflight-id", "../escape",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("NOT READY", err)
        self.assertFalse((self.data_root / "escape").exists())

    def test_fake_drill_cannot_satisfy_real_seven_seat_or_report_checks(self):
        from hoya_market_agents.competition_drill import run_fake_competition_drill

        drill = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="f11111",
        )
        code, out, _ = self.run_cli(
            "preflight",
            "--provider", "system",
            "--seats", "7",
            "--mode", "real",
            "--drill-run-id", drill.run_id,
            "--preflight-id", "real-with-fake-drill",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(1, code)
        payload = json.loads(out)
        self.assertIn("seven_seat_timeline", payload["blockers"])
        self.assertIn("report_deadline", payload["blockers"])
        self.assertIn("fake-competition-drill", next(
            item["evidence"] for item in payload["checks"]
            if item["check_id"] == "seven_seat_timeline"
        ))

    def test_verify_run_command_prints_json_and_tamper_returns_nonzero(self):
        result = RunController(
            store=RunStore(self.data_root),
            provider=FakeProvider(),
            clock=FixedClock(auto_advance_ms=250),
            token_source=ScriptedTokenSource(["abc123"]),
        ).execute("分析 BTC 過去 14 日市場狀態")

        code, out, err = self.run_cli(
            "verify-run", "--run-id", result.run_id, "--data-root", str(self.data_root)
        )
        self.assertEqual(0, code, err)
        self.assertEqual("VERIFIED", json.loads(out)["status"])

        (result.run_dir / "votes.json").write_text("{}\n", encoding="utf-8")
        code, out, err = self.run_cli(
            "verify-run", "--run-id", result.run_id, "--data-root", str(self.data_root)
        )
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertIn("NOT VERIFIED", err)

    def test_fake_drill_command_runs_full_timeline_and_self_verifies(self):
        code, out, err = self.run_cli(
            "drill",
            "--provider-mode", "fake",
            "--question", "分析 BTC 過去 14 日市場狀態",
            "--data-root", str(self.data_root),
        )

        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual("VERIFIED", payload["verification"]["status"])
        self.assertEqual("fake-competition-drill", payload["verification"]["provider_mode"])
        self.assertTrue((Path(payload["run_dir"]) / "manifest.json").is_file())

    def test_provider_matrix_masks_claude_session_identity(self):
        rows = _claude_matrix({
            "seats": [{
                "seat_id": "official-events",
                "actual_model": "claude-opus-5",
                "session_id": "0eed52ad-0c61-462d-a61c-f4b45c9e545f",
                "web_search_requests": 1,
                "elapsed_ms": 42,
            }]
        })

        self.assertNotIn("0eed52ad-0c61-462d-a61c-f4b45c9e545f", str(rows))
        self.assertIn("…", rows[0]["identity"])

    def test_real_drill_observation_rejects_missing_timeline(self):
        checks = _fixture_system_checks()
        with patch(
            "hoya_market_agents.cli.verify_run",
            return_value={
                "status": "VERIFIED",
                "provider_mode": "real-subscription",
                "competition_ready": True,
                "timeline": None,
            },
        ):
            _apply_drill_observation(checks, self.data_root, "forged-real")

        for check_id in ("seven_seat_timeline", "report_deadline"):
            check = next(item for item in checks if item["check_id"] == check_id)
            self.assertFalse(check["ok"])


if __name__ == "__main__":
    unittest.main()
