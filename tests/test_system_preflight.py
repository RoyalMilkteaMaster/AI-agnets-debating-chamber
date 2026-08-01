"""Ticket #11 system readiness is machine-verifiable and fail closed."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from hoya_market_agents.claude_adapter import CLAUDE_SEAT_SESSIONS
from hoya_market_agents.codex_bridge import CODEX_SEAT_IDS
from hoya_market_agents.system_preflight import (
    REQUIRED_CHECK_IDS,
    build_competition_authorization,
    build_preflight_manifest,
    load_frozen_roster,
    write_competition_authorization,
    write_preflight_manifest,
)


def passing_checks():
    return [
        {
            "check_id": check_id,
            "ok": True,
            "target": "required",
            "actual": "observed",
            "evidence": "masked fixture evidence",
        }
        for check_id in REQUIRED_CHECK_IDS
    ]


class FrozenRosterTest(unittest.TestCase):
    def test_roster_freezes_core_and_exactly_seven_distinct_provider_seats(self):
        roster = load_frozen_roster()

        self.assertEqual("gpt-5.6-sol", roster["core"]["target_model"])
        self.assertEqual(7, len(roster["seats"]))
        self.assertEqual(7, len({seat["seat_id"] for seat in roster["seats"]}))
        self.assertEqual(
            {"claude": 3, "codex": 3, "antigravity": 1},
            {
                provider: sum(seat["provider"] == provider for seat in roster["seats"])
                for provider in ("claude", "codex", "antigravity")
            },
        )
        gemini = [seat for seat in roster["seats"] if seat["provider"] == "antigravity"]
        self.assertEqual("gemini-3.1-pro-high", gemini[0]["target_model"])
        self.assertEqual(
            set(CODEX_SEAT_IDS),
            {seat["seat_id"] for seat in roster["seats"] if seat["provider"] == "codex"},
        )
        self.assertEqual(
            set(CLAUDE_SEAT_SESSIONS),
            {seat["seat_id"] for seat in roster["seats"] if seat["provider"] == "claude"},
        )
        codex_seats = [seat for seat in roster["seats"] if seat["provider"] == "codex"]
        self.assertTrue(codex_seats)
        self.assertTrue(
            all(seat["allowed_tools"] == ["web_search"] for seat in codex_seats)
        )
        self.assertTrue(
            all(seat["required_skills"] == ["research"] for seat in roster["seats"])
        )

    def test_roster_rejects_provider_or_tool_policy_drift(self):
        roster = load_frozen_roster()
        for mutation in ("provider", "tools", "skills"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                changed = deepcopy(roster)
                if mutation == "provider":
                    changed["seats"][0]["provider"] = "claude"
                    changed["seats"][3]["provider"] = "codex"
                else:
                    if mutation == "tools":
                        changed["seats"][0]["allowed_tools"] = []
                    else:
                        changed["seats"][0]["required_skills"] = []
                path = Path(temporary) / "roster.json"
                path.write_text(json.dumps(changed), encoding="utf-8")

                with self.assertRaises(ValueError):
                    load_frozen_roster(path)

    def test_wsl_path_translation_is_a_required_system_check(self):
        self.assertIn("path_translation", REQUIRED_CHECK_IDS)


class SystemPreflightManifestTest(unittest.TestCase):
    def build(self, checks=None, mode="real"):
        return build_preflight_manifest(
            checks=checks or passing_checks(),
            mode=mode,
            generated_at_utc="2026-08-01T10:00:00Z",
            code_root="/mnt/d/workstationD/hoya bit/hoya-bit-market-agents",
            data_root="/mnt/d/workstationD/hoya bit/hoya-bit-market-agents_data",
        )

    def test_real_mode_is_ready_only_when_every_required_check_passes(self):
        manifest = self.build()

        self.assertEqual("READY", manifest["status"])
        self.assertTrue(manifest["ready"])
        self.assertTrue(manifest["provider_capabilities_ready"])
        self.assertEqual([], manifest["blockers"])
        self.assertEqual(set(REQUIRED_CHECK_IDS), {item["check_id"] for item in manifest["checks"]})

    def test_fixture_mode_can_pass_regression_but_never_claim_competition_ready(self):
        manifest = self.build(mode="fixture")

        self.assertEqual("NOT_READY", manifest["status"])
        self.assertFalse(manifest["ready"])
        self.assertFalse(manifest["provider_capabilities_ready"])
        self.assertEqual("PASS", manifest["simulation_status"])
        self.assertIn("fixture_mode_is_not_live_evidence", manifest["blockers"])

    def test_broken_login_model_write_or_renderer_each_forces_not_ready(self):
        for broken in ("provider_login", "target_actual_models", "data_root_write", "renderer"):
            with self.subTest(broken=broken):
                checks = passing_checks()
                next(item for item in checks if item["check_id"] == broken).update(
                    ok=False, actual="broken fixture"
                )

                manifest = self.build(checks)

                self.assertEqual("NOT_READY", manifest["status"])
                self.assertFalse(manifest["ready"])
                self.assertIn(broken, manifest["blockers"])

    def test_provider_capabilities_can_be_proven_before_run_scoped_receipts(self):
        checks = passing_checks()
        for check_id in ("search", "seven_seat_timeline", "report_deadline"):
            next(item for item in checks if item["check_id"] == check_id)["ok"] = False

        manifest = self.build(checks)

        self.assertEqual("NOT_READY", manifest["status"])
        self.assertTrue(manifest["provider_capabilities_ready"])
        self.assertEqual(
            ["search", "seven_seat_timeline", "report_deadline"],
            manifest["blockers"],
        )

    def test_unavailable_provider_attestation_is_advisory_not_a_ready_blocker(self):
        checks = passing_checks()
        next(
            item
            for item in checks
            if item["check_id"] == "provider_runtime_attestation"
        ).update(ok=False, actual="provider_runtime_attestation_unavailable")

        manifest = self.build(checks)

        self.assertTrue(manifest["provider_capabilities_ready"])
        self.assertTrue(manifest["ready"])
        self.assertNotIn("provider_runtime_attestation", manifest["blockers"])
        self.assertEqual(
            ["provider_runtime_attestation"], manifest["advisories"]
        )

    def test_missing_required_check_fails_closed(self):
        manifest = self.build(passing_checks()[:-1])

        self.assertEqual("NOT_READY", manifest["status"])
        self.assertIn(REQUIRED_CHECK_IDS[-1], manifest["blockers"])

    def test_manifest_is_written_once_under_data_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory)
            manifest = self.build()

            path = write_preflight_manifest(data_root, "ticket11-fixture", manifest)

            self.assertEqual(data_root / "preflight" / "ticket11-fixture" / "manifest.json", path)
            self.assertTrue(path.is_file())
            with self.assertRaises(FileExistsError):
                write_preflight_manifest(data_root, "ticket11-fixture", manifest)

    def test_empty_or_path_like_preflight_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            for preflight_id in ("", ".", "..", "../outside", "nested/id"):
                with self.subTest(preflight_id=preflight_id), self.assertRaises(ValueError):
                    write_preflight_manifest(
                        Path(temporary_directory), preflight_id, self.build()
                    )

    def test_preflight_target_symlink_cannot_escape_data_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_root = Path(temporary_directory) / "data"
            outside = Path(temporary_directory) / "outside"
            (data_root / "preflight").mkdir(parents=True)
            outside.mkdir()
            (data_root / "preflight" / "escape").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                write_preflight_manifest(data_root, "escape", self.build())

    def test_competition_authorization_is_run_scoped_and_write_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            authorization = build_competition_authorization(
                preflight_id="provider-preflight-1",
                run_id="20260801T020000Z-btc-abc123",
                competition_challenge="competition-challenge-1234567890",
                issued_at_utc="2026-08-01T01:59:00Z",
            )

            lineage = write_competition_authorization(
                Path(temporary_directory), "provider-preflight-1", authorization
            )

            self.assertEqual("AUTHORIZED", lineage["status"])
            self.assertEqual("20260801T020000Z-btc-abc123", lineage["authorized_run_id"])
            self.assertRegex(lineage["sha256"], r"^[0-9a-f]{64}$")
            with self.assertRaises(ValueError):
                write_competition_authorization(
                    Path(temporary_directory), "provider-preflight-1", authorization
                )


if __name__ == "__main__":
    unittest.main()
