"""Ticket #11 verifies a completed run from immutable artifacts."""

import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.run_verifier import RunVerificationError, verify_run
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


if __name__ == "__main__":
    unittest.main()
