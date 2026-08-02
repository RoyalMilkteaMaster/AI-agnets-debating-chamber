"""Ticket #11 verifies a completed run from immutable artifacts."""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.run_verifier import (
    RunVerificationError,
    _require_attempt_artifact_path,
    _reject_shipped_fake_markers,
    _verify_provider_matrix,
    _verify_real_run_receipts,
    verify_run,
)
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


class ProviderMatrixVerificationTest(unittest.TestCase):
    def setUp(self):
        config_path = Path(__file__).parents[1] / "config" / "agent_roster.json"
        self.roster = json.loads(config_path.read_text(encoding="utf-8"))
        self.matrix = [{
            "seat_id": "core",
            "provider": "codex",
            "target_model": "gpt-5.6-sol",
            "actual_model": "gpt-5.6-sol",
            "model_confirmation_source": "operator_ui",
        }] + [
            {
                "seat_id": seat["seat_id"],
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": seat["target_model"],
            }
            for seat in self.roster["seats"]
        ]

    def test_operator_ui_core_model_confirmation_is_valid(self):
        self.assertEqual(7, len(_verify_provider_matrix(self.matrix, self.roster)))

    def test_unknown_core_model_confirmation_source_is_rejected(self):
        self.matrix[0]["model_confirmation_source"] = "prompt"

        with self.assertRaises(RunVerificationError):
            _verify_provider_matrix(self.matrix, self.roster)


class RealReceiptVerificationTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.run_dir = Path(self._temporary.name) / "runs" / "authorized-run"
        self.run_dir.mkdir(parents=True)
        self.started = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)
        self.challenge = "competition-challenge-1234567890"
        self.artifact_index = {}
        self.evidence = []
        self.debate = []
        self.votes = {"votes": []}
        providers = {
            "spot-technical": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "derivatives": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "onchain": ("codex", "gpt-5.6-sol", "gpt-5.6-sol"),
            "official-events": ("claude", "opus", "claude-opus-5"),
            "news": ("claude", "opus", "claude-opus-5"),
            "social-macro": ("claude", "opus", "claude-opus-5"),
            "counter-evidence": (
                "antigravity",
                "gemini-3.1-pro-high",
                "gemini-3.1-pro-high",
            ),
        }
        self.manifest = {
            "run_id": "authorized-run",
            "started_at_utc": self._utc(0),
            "seats": [],
            "provider_receipts": [],
        }
        self.timeline = {"seat_completion_ms": {}}
        self.authorization = {
            "system_preflight_id": "provider-preflight-1",
            "run_id": "authorized-run",
            "competition_challenge": self.challenge,
        }
        self.receipts = {}
        for index, (seat_id, models) in enumerate(providers.items(), start=1):
            provider, target, actual = models
            attempt_id = "{}-attempt-1".format(seat_id)
            completion_ms = index * 1_000
            search_ms = completion_ms - 250
            evidence_record = {
                "schema_version": "1.0.0",
                "evidence_id": "{}-evidence-1".format(seat_id),
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "statement": "public evidence {}".format(index),
            }
            self.evidence.append(evidence_record)
            self.debate.append({
                "event": "seat_message",
                "seat_id": seat_id,
                "attempt_id": attempt_id,
            })
            self.votes["votes"].append({
                "seat_id": seat_id,
                "attempt_ids": [attempt_id],
            })
            self.timeline["seat_completion_ms"][seat_id] = completion_ms
            self.manifest["seats"].append({
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "target_model": target,
                "actual_model": actual,
            })
            attempt_root = "agents/{}/attempts/{}/".format(seat_id, attempt_id)
            raw_path = attempt_root + "public-transcript.jsonl"
            output_path = attempt_root + "structured-output.json"
            search_path = attempt_root + "search-receipt.json"
            self._write_artifact(raw_path, '{"public":"transcript"}\n')
            self._write_artifact(
                output_path,
                json.dumps([evidence_record]) + "\n",
            )
            search = {
                "schema_version": "1.0.0",
                "receipt_id": "search-{}".format(seat_id),
                "run_id": "authorized-run",
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "competition_challenge": self.challenge,
                "tool": {
                    "codex": "web_search",
                    "claude": "WebSearch",
                    "antigravity": "search_web",
                }[provider],
                "succeeded": True,
                "completed_at_utc": self._utc(search_ms),
                "elapsed_ms": search_ms,
            }
            self._write_artifact(search_path, json.dumps(search) + "\n")
            receipt = {
                "schema_version": "1.0.0",
                "receipt_id": "provider-{}".format(seat_id),
                "system_preflight_id": "provider-preflight-1",
                "run_id": "authorized-run",
                "competition_challenge": self.challenge,
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": provider,
                "target_model": target,
                "actual_model": actual,
                "dispatch": {
                    "receipt_id": "dispatch-{}".format(seat_id),
                    "at_utc": self._utc(0),
                    "elapsed_ms": 0,
                },
                "completion": {
                    "receipt_id": "completion-{}".format(seat_id),
                    "at_utc": self._utc(completion_ms),
                    "elapsed_ms": completion_ms,
                },
                "search_receipt_path": search_path,
                "search_receipt_sha256": self.artifact_index[search_path]["sha256"],
                "raw_transcript_path": raw_path,
                "raw_transcript_sha256": self.artifact_index[raw_path]["sha256"],
                "output_path": output_path,
                "output_sha256": self.artifact_index[output_path]["sha256"],
            }
            receipt_path = "provider-receipts/{}.json".format(seat_id)
            self._write_artifact(receipt_path, json.dumps(receipt) + "\n")
            self.receipts[seat_id] = (receipt_path, receipt)
            self.manifest["provider_receipts"].append({
                "seat_id": seat_id,
                "path": receipt_path,
                "sha256": self.artifact_index[receipt_path]["sha256"],
            })

    def _utc(self, elapsed_ms):
        value = self.started + timedelta(milliseconds=elapsed_ms)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _write_artifact(self, relative, content):
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        path.write_bytes(data)
        self.artifact_index[relative] = {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _rewrite_receipt(self, seat_id):
        relative, receipt = self.receipts[seat_id]
        self._write_artifact(relative, json.dumps(receipt) + "\n")
        next(item for item in self.manifest["provider_receipts"] if item["seat_id"] == seat_id)[
            "sha256"
        ] = self.artifact_index[relative]["sha256"]

    def verify(self):
        return _verify_real_run_receipts(
            self.run_dir,
            self.manifest,
            self.artifact_index,
            self.timeline,
            self.evidence,
            self.debate,
            self.votes,
            self.authorization,
        )

    def test_exact_seven_run_scoped_receipts_pass(self):
        self.assertEqual(28, len(self.verify()))

    def test_shipped_fake_markers_are_rejected_after_receipt_validation(self):
        with self.assertRaises(RunVerificationError):
            _reject_shipped_fake_markers(
                {"provider_mode": "real-subscription"},
                ["https://fake.invalid/source"],
            )

        _reject_shipped_fake_markers(
            {"provider_mode": "real-subscription"},
            ["https://example.com/live-public-evidence"],
        )

    def test_receipt_payload_cannot_escape_its_attempt_directory(self):
        root = "agents/news/attempts/news-attempt-1/"
        _require_attempt_artifact_path(root + "output.json", root, "output")
        for path in (
            root + "../../../other-seat/output.json",
            root + "..\\other-seat\\output.json",
            "/absolute/output.json",
        ):
            with self.subTest(path=path), self.assertRaises(RunVerificationError):
                _require_attempt_artifact_path(path, root, "output")

    def test_receipt_attempt_must_match_adopted_lineage(self):
        next(
            record for record in self.evidence if record["seat_id"] == "news"
        )["attempt_id"] = "news-adopted-a1"

        with self.assertRaisesRegex(RunVerificationError, "adopted evidence attempt"):
            self.verify()

    def test_structured_output_must_equal_formal_evidence(self):
        receipt = self.receipts["news"][1]
        output_path = receipt["output_path"]
        self._write_artifact(output_path, '[{"self_asserted":true}]\n')
        receipt["output_sha256"] = self.artifact_index[output_path]["sha256"]
        self._rewrite_receipt("news")

        with self.assertRaisesRegex(RunVerificationError, "structured output"):
            self.verify()

    def test_wrong_run_challenge_attempt_model_timing_search_or_hash_fails(self):
        cases = {
            "run": lambda receipt: receipt.update(run_id="wrong-run"),
            "challenge": lambda receipt: receipt.update(
                competition_challenge="wrong-challenge-123456789012"
            ),
            "attempt": lambda receipt: receipt.update(attempt_id="wrong-attempt"),
            "model": lambda receipt: receipt.update(actual_model="wrong-model"),
            "completion": lambda receipt: receipt["completion"].update(elapsed_ms=999),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.setUp()
                path, receipt = self.receipts["news"]
                mutate(receipt)
                self._rewrite_receipt("news")
                with self.assertRaises(RunVerificationError):
                    self.verify()

        self.setUp()
        search_path = self.receipts["news"][1]["search_receipt_path"]
        search = json.loads((self.run_dir / search_path).read_text())
        search["succeeded"] = False
        self._write_artifact(search_path, json.dumps(search) + "\n")
        self.receipts["news"][1]["search_receipt_sha256"] = self.artifact_index[search_path]["sha256"]
        self._rewrite_receipt("news")
        with self.assertRaises(RunVerificationError):
            self.verify()

        self.setUp()
        self.receipts["news"][1]["raw_transcript_sha256"] = "0" * 64
        self._rewrite_receipt("news")
        with self.assertRaises(RunVerificationError):
            self.verify()


if __name__ == "__main__":
    unittest.main()
