"""A deterministic seven-seat BTC drill proves the integrated time gates."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents.competition_drill import run_fake_competition_drill
from hoya_market_agents.run_verifier import RunVerificationError, verify_run


class CompetitionDrillTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def test_seven_seats_finish_before_cutoff_seal_at_t5_and_report_before_t13(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d11111",
        )
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        timeline = manifest["competition_timeline"]

        self.assertEqual(0, timeline["all_seats_dispatched_at_ms"])
        self.assertEqual(7, len(timeline["seat_completion_ms"]))
        self.assertLessEqual(max(timeline["seat_completion_ms"].values()), 285_000)
        self.assertEqual(300_000, timeline["evidence_snapshot_sealed_at_ms"])
        self.assertRegex(timeline["evidence_snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("consensus_6_votes", timeline["debate_stop_reason"])
        self.assertLessEqual(timeline["report_completed_at_ms"], 780_000)
        self.assertLessEqual(
            timeline["report_completed_at_ms"] - timeline["debate_stop_at_ms"],
            180_000,
        )

    def test_drill_preserves_all_votes_dissent_and_six_required_artifacts(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d22222",
        )
        votes = json.loads((result.run_dir / "votes.json").read_text(encoding="utf-8"))

        self.assertEqual(7, votes["valid_vote_count"])
        self.assertEqual(6, votes["tally"]["bullish"])
        self.assertEqual(1, votes["tally"]["bearish"])
        self.assertEqual("counter-evidence", votes["dissent"][0]["seat_id"])
        for name in ("manifest.json", "evidence.jsonl", "debate.jsonl", "votes.json", "report.md", "report.html"):
            self.assertTrue((result.run_dir / name).is_file(), name)
        self.assertEqual("VERIFIED", verify_run(self.data_root, result.run_id)["status"])

    def test_drill_is_explicitly_fake_and_cannot_be_live_readiness_evidence(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d33333",
        )
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("fake-competition-drill", manifest["provider_mode"])
        self.assertFalse(manifest["competition_ready"])
        self.assertIn("不得作為真實市場或訂閱 provider READY 證據", manifest["limitations"])

    def test_fake_manifest_cannot_be_promoted_to_real_by_flag_edits(self):
        for remove_timeline in (True, False):
            with self.subTest(remove_timeline=remove_timeline):
                root = self.data_root / str(remove_timeline)
                result = run_fake_competition_drill(
                    data_root=root,
                    question="分析 BTC 過去 14 日市場狀態",
                    token="d3333{}".format(int(remove_timeline)),
                )
                manifest_path = result.run_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["provider_mode"] = "real-subscription"
                manifest["competition_ready"] = True
                if remove_timeline:
                    manifest.pop("competition_timeline")
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaises(RunVerificationError):
                    verify_run(root, result.run_id)

    def test_verify_run_rejects_late_seat_late_report_and_snapshot_tamper(self):
        for failure in ("seat", "report", "snapshot"):
            with self.subTest(failure=failure):
                root = self.data_root / failure
                result = run_fake_competition_drill(
                    data_root=root,
                    question="分析 BTC 過去 14 日市場狀態",
                    token={"seat": "d44441", "report": "d44442", "snapshot": "d44443"}[failure],
                )
                manifest_path = result.run_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if failure == "seat":
                    manifest["competition_timeline"]["seat_completion_ms"]["news"] = 285_001
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                elif failure == "report":
                    manifest["competition_timeline"]["report_completed_at_ms"] = 780_001
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                else:
                    snapshot = result.run_dir / "snapshots" / "evidence.jsonl"
                    snapshot.write_text("tampered\n", encoding="utf-8")

                with self.assertRaises(RunVerificationError):
                    verify_run(root, result.run_id)

    def test_verify_run_rejects_vote_duplication_and_report_lineage_tamper(self):
        for failure in ("duplicate-vote", "report-tally"):
            with self.subTest(failure=failure):
                root = self.data_root / failure
                result = run_fake_competition_drill(
                    data_root=root,
                    question="分析 BTC 過去 14 日市場狀態",
                    token={"duplicate-vote": "d55551", "report-tally": "d55552"}[failure],
                )
                manifest_path = result.run_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if failure == "duplicate-vote":
                    artifact_name = "votes.json"
                    artifact_path = result.run_dir / artifact_name
                    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                    payload["votes"].append(dict(payload["votes"][0]))
                else:
                    artifact_name = "report.json"
                    artifact_path = result.run_dir / artifact_name
                    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                    payload["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
                content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                artifact_path.write_bytes(content)
                manifest["artifacts"][artifact_name]["sha256"] = hashlib.sha256(content).hexdigest()
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaises(RunVerificationError):
                    verify_run(root, result.run_id)


if __name__ == "__main__":
    unittest.main()
