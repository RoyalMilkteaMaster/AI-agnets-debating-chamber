"""Operational receipts must still match the adopted public record."""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hoya_market_agents.competition_drill import run_fake_competition_drill
from hoya_market_agents.report_audit_renderer import render_debate_html
from hoya_market_agents.report_renderer import render_market_html, render_market_markdown
from hoya_market_agents.run_verifier import RunVerificationError, verify_run
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.system_preflight import REQUIRED_CHECK_IDS, build_preflight_manifest


ACTUAL_MODELS = {
    "spot-technical": "gpt-5.6-sol",
    "derivatives": "gpt-5.6-sol",
    "onchain": "gpt-5.6-sol",
    "official-events": "claude-opus-5",
    "news": "claude-opus-5",
    "social-macro": "claude-opus-5",
    "counter-evidence": "gemini-3.1-pro-high",
}
SEARCH_TOOLS = {
    "codex": "web_search",
    "claude": "WebSearch",
    "antigravity": "search_web",
}


class ReviewerCompleteLocalAttackTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def test_local_receipts_with_unadopted_attempt_are_rejected(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="reviewer99",
        )
        self._promote_with_complete_local_claim(result)

        with self.assertRaisesRegex(
            RunVerificationError,
            "adopted evidence attempt",
        ):
            verify_run(self.data_root, result.run_id)

    def test_operational_run_can_verify_with_attestation_advisory(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="operational",
        )
        self._promote_with_complete_local_claim(result, align_formal_attempts=True)

        summary = verify_run(self.data_root, result.run_id)

        self.assertEqual("VERIFIED", summary["status"])
        self.assertTrue(summary["competition_ready"])
        self.assertEqual(
            ["provider_runtime_attestation"], summary["advisories"]
        )

    def _promote_with_complete_local_claim(
        self, result, align_formal_attempts=False
    ):
        run_dir = result.run_dir
        manifest_path = run_dir / "manifest.json"
        manifest = self._sanitize(self._read_json(manifest_path))
        evidence = self._sanitize(self._read_jsonl(run_dir / "evidence.jsonl"))
        debate = self._sanitize(self._read_jsonl(run_dir / "debate.jsonl"))
        votes = self._sanitize(self._read_json(run_dir / "votes.json"))
        report = self._sanitize(self._read_json(run_dir / "report.json"))

        if align_formal_attempts:
            attempts = {
                seat_id: "{}-receipt-attempt-99".format(seat_id)
                for seat_id in SEAT_IDS
            }
            for record in evidence:
                record["attempt_id"] = attempts[record["seat_id"]]
            for record in debate:
                if record.get("seat_id") in attempts and record.get("attempt_id"):
                    record["attempt_id"] = attempts[record["seat_id"]]
            for vote in votes["votes"]:
                vote["attempt_ids"] = [attempts[vote["seat_id"]]]

        self._write_jsonl(run_dir / "evidence.jsonl", evidence)
        self._write_jsonl(run_dir / "snapshots/evidence.jsonl", evidence)
        self._write_jsonl(run_dir / "debate.jsonl", debate)
        self._write_json(run_dir / "votes.json", votes)
        self._write_json(run_dir / "report.json", report)
        sources = {
            "evidence": evidence,
            "debate": [entry for entry in debate if entry.get("seat_id")],
            "votes": votes,
        }
        (run_dir / "report.md").write_bytes(render_market_markdown(report).encode("utf-8"))
        (run_dir / "report.html").write_bytes(
            render_market_html(report, sources).encode("utf-8")
        )
        (run_dir / "debate.html").write_bytes(
            render_debate_html(report, sources).encode("utf-8")
        )

        for relative in (
            "evidence.jsonl",
            "snapshots/evidence.jsonl",
            "debate.jsonl",
            "votes.json",
            "report.json",
            "report.md",
            "report.html",
            "debate.html",
        ):
            self._index(manifest, run_dir, relative)
        snapshot_sha = manifest["artifacts"]["evidence.jsonl"]["sha256"]
        manifest["competition_timeline"]["evidence_snapshot_sha256"] = snapshot_sha

        challenge = "competition-challenge-reviewer-1234567890"
        preflight_id = "local-self-attested-preflight"
        preflight_dir = self.data_root / "preflight" / preflight_id
        preflight_dir.mkdir(parents=True)
        authorization = {
            "schema_version": "1.0.0",
            "status": "AUTHORIZED",
            "system_preflight_id": preflight_id,
            "authorized_run_id": result.run_id,
            "competition_challenge": challenge,
            "issued_at_utc": "2026-08-01T01:59:00Z",
        }
        authorization_bytes = self._json_bytes(authorization)
        (preflight_dir / "competition-authorization.json").write_bytes(
            authorization_bytes
        )
        checks = [
            {
                "check_id": check_id,
                "ok": check_id
                not in (
                    "provider_runtime_attestation",
                    "search",
                    "seven_seat_timeline",
                    "report_deadline",
                ),
                "target": "required",
                "actual": "local self assertion",
                "evidence": "hash-linked local JSON only",
            }
            for check_id in REQUIRED_CHECK_IDS
        ]
        preflight = build_preflight_manifest(
            checks=checks,
            mode="real",
            generated_at_utc="2026-08-01T01:59:00Z",
            code_root="/code",
            data_root=self.data_root,
        )
        preflight["provider_matrix"] = [
            {
                "seat_id": "core",
                "provider": "codex",
                "target_model": "gpt-5.6-sol",
                "actual_model": "gpt-5.6-sol",
            }
        ] + [
            {
                "seat_id": seat["seat_id"],
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": ACTUAL_MODELS[seat["seat_id"]],
            }
            for seat in manifest["seats"]
        ]
        preflight["provider_runtime_attestation"] = {
            "status": "SELF_ASSERTED",
            "issuer": "local-test",
            "sha256": hashlib.sha256(b"local-test").hexdigest(),
        }
        preflight["competition_authorization"] = {
            "status": "AUTHORIZED",
            "authorized_run_id": result.run_id,
            "competition_challenge_sha256": self._sha(challenge.encode("utf-8")),
            "path": "preflight/{}/competition-authorization.json".format(preflight_id),
            "sha256": self._sha(authorization_bytes),
        }
        preflight_bytes = self._json_bytes(preflight)
        (preflight_dir / "manifest.json").write_bytes(preflight_bytes)

        manifest["provider_mode"] = "real-subscription"
        manifest["competition_ready"] = True
        manifest["provider_preflight_lineage"] = {
            "system_preflight_id": preflight_id,
            "manifest_path": "preflight/{}/manifest.json".format(preflight_id),
            "sha256": self._sha(preflight_bytes),
        }
        manifest["provider_receipts"] = []
        started = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)
        evidence_by_seat = {record["seat_id"]: record for record in evidence}
        for seat in manifest["seats"]:
            seat_id = seat["seat_id"]
            seat["actual_model"] = ACTUAL_MODELS[seat_id]
            attempt_id = "{}-receipt-attempt-99".format(seat_id)
            seat["attempt_id"] = attempt_id
            completion_ms = manifest["competition_timeline"]["seat_completion_ms"][seat_id]
            search_ms = completion_ms - 100
            attempt_root = "agents/{}/attempts/{}/".format(seat_id, attempt_id)
            raw_path = attempt_root + "public-transcript.jsonl"
            output_path = attempt_root + "structured-output.json"
            search_path = attempt_root + "search-receipt.json"
            raw = json.dumps(
                {"seat_id": seat_id, "attempt_id": attempt_id, "public": "local"}
            ) + "\n"
            output_record = dict(evidence_by_seat[seat_id], attempt_id=attempt_id)
            self._write_text(run_dir / raw_path, raw)
            self._write_json(run_dir / output_path, [output_record])
            search = {
                "schema_version": "1.0.0",
                "receipt_id": "search-{}".format(seat_id),
                "run_id": result.run_id,
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": seat["provider"],
                "competition_challenge": challenge,
                "tool": SEARCH_TOOLS[seat["provider"]],
                "succeeded": True,
                "completed_at_utc": self._utc(started, search_ms),
                "elapsed_ms": search_ms,
            }
            self._write_json(run_dir / search_path, search)
            for relative in (raw_path, output_path, search_path):
                self._index(manifest, run_dir, relative)
            receipt = {
                "schema_version": "1.0.0",
                "receipt_id": "provider-{}".format(seat_id),
                "system_preflight_id": preflight_id,
                "run_id": result.run_id,
                "competition_challenge": challenge,
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": seat["actual_model"],
                "dispatch": {
                    "receipt_id": "dispatch-{}".format(seat_id),
                    "at_utc": self._utc(started, 0),
                    "elapsed_ms": 0,
                },
                "completion": {
                    "receipt_id": "completion-{}".format(seat_id),
                    "at_utc": self._utc(started, completion_ms),
                    "elapsed_ms": completion_ms,
                },
                "search_receipt_path": search_path,
                "search_receipt_sha256": manifest["artifacts"][search_path]["sha256"],
                "raw_transcript_path": raw_path,
                "raw_transcript_sha256": manifest["artifacts"][raw_path]["sha256"],
                "output_path": output_path,
                "output_sha256": manifest["artifacts"][output_path]["sha256"],
            }
            receipt_path = "provider-receipts/{}.json".format(seat_id)
            self._write_json(run_dir / receipt_path, receipt)
            self._index(manifest, run_dir, receipt_path)
            manifest["provider_receipts"].append(
                {
                    "seat_id": seat_id,
                    "path": receipt_path,
                    "sha256": manifest["artifacts"][receipt_path]["sha256"],
                }
            )
        manifest_path.write_bytes(self._json_bytes(manifest))

    @staticmethod
    def _sanitize(value):
        if isinstance(value, dict):
            return {key: ReviewerCompleteLocalAttackTest._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [ReviewerCompleteLocalAttackTest._sanitize(item) for item in value]
        if isinstance(value, str):
            return (
                value.replace("fake", "public")
                .replace("fixture", "observed")
                .replace(
                    "不得作為真實市場或訂閱 provider READY 證據",
                    "public evidence lineage recorded",
                )
            )
        return value

    @staticmethod
    def _utc(started, elapsed_ms):
        return (started + timedelta(milliseconds=elapsed_ms)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

    @staticmethod
    def _read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_jsonl(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def _json_bytes(value):
        return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    @classmethod
    def _write_json(cls, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cls._json_bytes(value))

    @classmethod
    def _write_jsonl(cls, path, values):
        cls._write_text(
            path,
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        )

    @staticmethod
    def _write_text(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8", newline="\n")

    @classmethod
    def _index(cls, manifest, run_dir, relative):
        manifest["artifacts"][relative] = {
            "path": relative,
            "sha256": cls._sha((run_dir / relative).read_bytes()),
        }

    @staticmethod
    def _sha(value):
        return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    unittest.main()
