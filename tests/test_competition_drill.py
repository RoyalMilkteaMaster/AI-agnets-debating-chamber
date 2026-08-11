"""A deterministic seven-seat drill proves the integrated time gates.

The drill runs on every approved question type, so a zero-subscription run can
exercise research, debate, votes, report and verify-run for any of them.
"""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents import question_package as question_package_module
from hoya_market_agents.cli import main
from hoya_market_agents.competition_drill import (
    FALLBACK_EVIDENCE_ASSET,
    run_fake_competition_drill,
)
from hoya_market_agents.debate_rules import debate_rules
from hoya_market_agents.debate_state_machine import STANCES_BY_QUESTION_TYPE
from hoya_market_agents.question import UnsupportedQuestionError
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.research_scheduler import (
    ACCEPT_RESULTS_UNTIL_MS,
    SEAL_MS,
    research_deadlines,
)
from hoya_market_agents.run_verifier import RunVerificationError, verify_run
from hoya_market_agents.system_preflight import REQUIRED_CHECK_IDS, build_preflight_manifest

OPEN_QUESTION = "若 SEC 通過 BTC 現貨 ETF，市場會如何反應？"
COMPARISON_QUESTION = "比較 XRP 與 BTC 過去 7 日的市場位置與風險"


def open_proposition_package():
    """Return the open-proposition package, or None while that type is absent."""
    if not getattr(question_package_module, "OPEN_STANCES", None):
        return None
    try:
        package = build_question_package(OPEN_QUESTION)
    except UnsupportedQuestionError:
        return None
    if package.question_type not in STANCES_BY_QUESTION_TYPE:
        return None
    return package


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
        self.assertLessEqual(
            max(timeline["seat_completion_ms"].values()), ACCEPT_RESULTS_UNTIL_MS
        )
        self.assertEqual(SEAL_MS, timeline["evidence_snapshot_sealed_at_ms"])
        self.assertRegex(timeline["evidence_snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("consensus_6_votes", timeline["debate_stop_reason"])
        self.assertLessEqual(timeline["report_completed_at_ms"], 780_000)
        self.assertLessEqual(
            timeline["report_completed_at_ms"] - timeline["debate_stop_at_ms"],
            180_000,
        )

    def test_drill_preserves_votes_dissent_and_two_page_audit_report(self):
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
        for name in (
            "manifest.json",
            "evidence.jsonl",
            "debate.jsonl",
            "votes.json",
            "report.md",
            "report.html",
            "debate.html",
        ):
            self.assertTrue((result.run_dir / name).is_file(), name)
        report_html = (result.run_dir / "report.html").read_text(encoding="utf-8")
        debate_html = (result.run_dir / "debate.html").read_text(encoding="utf-8")
        self.assertIn('href="debate.html"', report_html)
        self.assertIn('href="report.html"', debate_html)
        self.assertEqual("VERIFIED", verify_run(self.data_root, result.run_id)["status"])

    def test_drill_uses_free_debate_between_the_first_two_vote_walls(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d22223",
        )
        entries = [
            json.loads(line)
            for line in (result.run_dir / "debate.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        messages = [entry for entry in entries if entry.get("event") == "seat_message"]
        final_votes = [entry for entry in messages if entry["kind"] == "final_vote"]
        rules = debate_rules()
        seal_ms = research_deadlines("single_asset_market_state").seal_ms
        first_wall = seal_ms + rules.vote_rounds[0].open_offset_ms
        second_wall = seal_ms + rules.vote_rounds[1].open_offset_ms

        self.assertEqual({"position", "final_vote"}, {entry["kind"] for entry in messages})
        self.assertEqual(7, len(final_votes))
        self.assertTrue(
            all(
                first_wall <= entry["elapsed_ms"] < second_wall
                and entry["round"] == 1
                and entry["public_reason"].strip()
                for entry in final_votes
            )
        )
        votes = json.loads((result.run_dir / "votes.json").read_text(encoding="utf-8"))
        for row in votes["votes"]:
            self.assertEqual(1, len(row["vote_changes"]))
            self.assertEqual(
                row["vote_changes"][0]["before"], row["vote_changes"][0]["after"]
            )
            self.assertTrue(row["vote_changes"][0]["public_reason"].strip())

    def test_drill_is_explicitly_fake_and_cannot_be_live_readiness_evidence(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d33333",
        )
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("fake-competition-drill", manifest["provider_mode"])
        self.assertFalse(manifest["competition_ready"])
        self.assertEqual("2.0.0", manifest["presentation_version"])
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

    def test_hash_matching_preflight_cannot_promote_shipped_fake_artifacts(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d36666",
        )
        manifest_path = result.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        challenge = "competition-challenge-1234567890"
        preflight_id = "forged-provider-preflight"
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
        authorization_bytes = (
            json.dumps(authorization, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        authorization_path = preflight_dir / "competition-authorization.json"
        authorization_path.write_bytes(authorization_bytes)

        checks = [
            {
                "check_id": check_id,
                "ok": check_id
                not in ("search", "seven_seat_timeline", "report_deadline"),
                "target": "required",
                "actual": "forged reviewer reproduction",
                "evidence": "hash matching but synthetic",
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
        actual_models = {
            "spot-technical": "gpt-5.6-sol",
            "derivatives": "gpt-5.6-sol",
            "onchain": "claude-opus-5",
            "official-events": "claude-opus-5",
            "news": "gpt-5.6-sol",
            "social-macro": "claude-opus-5",
            "counter-evidence": "gemini-3.1-pro-high",
        }
        preflight["provider_matrix"] = [{
            "seat_id": "core",
            "provider": "codex",
            "target_model": "gpt-5.6-sol",
            "actual_model": "gpt-5.6-sol",
        }] + [
            {
                "seat_id": seat["seat_id"],
                "provider": seat["provider"],
                "target_model": seat["target_model"],
                "actual_model": actual_models[seat["seat_id"]],
            }
            for seat in manifest["seats"]
        ]
        preflight["competition_authorization"] = {
            "status": "AUTHORIZED",
            "authorized_run_id": result.run_id,
            "competition_challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
            "path": "preflight/{}/competition-authorization.json".format(preflight_id),
            "sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        }
        preflight_path = preflight_dir / "manifest.json"
        preflight_bytes = (json.dumps(preflight, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        preflight_path.write_bytes(preflight_bytes)

        manifest["provider_mode"] = "real-subscription"
        manifest["competition_ready"] = True
        for seat in manifest["seats"]:
            seat["actual_model"] = actual_models[seat["seat_id"]]
        manifest["provider_preflight_lineage"] = {
            "system_preflight_id": preflight_id,
            "manifest_path": "preflight/{}/manifest.json".format(preflight_id),
            "sha256": hashlib.sha256(preflight_bytes).hexdigest(),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, result.run_id)

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


class ComparisonDrillSealsThirtySecondsLaterTest(unittest.TestCase):
    """Ticket R7: 比較題的 drill 走自己的 T+4:30 時刻表。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def drill(self, token):
        return run_fake_competition_drill(
            data_root=self.data_root, question=COMPARISON_QUESTION, token=token
        )

    def test_the_comparison_drill_seals_at_four_thirty_and_verifies(self):
        result = self.drill("d77771")
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        timeline = manifest["competition_timeline"]
        deadlines = research_deadlines("two_asset_comparison")

        self.assertEqual("two_asset_comparison", manifest["question_type"])
        self.assertEqual(deadlines.seal_ms, timeline["evidence_snapshot_sealed_at_ms"])
        self.assertEqual(
            deadlines.accept_until_ms, timeline["research_accept_until_ms"]
        )
        self.assertGreaterEqual(timeline["debate_stop_at_ms"], deadlines.seal_ms)
        self.assertEqual("VERIFIED", verify_run(self.data_root, result.run_id)["status"])

    def test_verify_run_rejects_a_comparison_run_sealed_at_four_minutes(self):
        result = self.drill("d77772")
        manifest_path = result.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["competition_timeline"]["evidence_snapshot_sealed_at_ms"] = SEAL_MS
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, result.run_id)

    def test_verify_run_rejects_a_single_asset_run_sealed_at_four_thirty(self):
        result = run_fake_competition_drill(
            data_root=self.data_root,
            question="分析 BTC 過去 14 日市場狀態",
            token="d77773",
        )
        manifest_path = result.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["competition_timeline"]["evidence_snapshot_sealed_at_ms"] = 270_000
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, result.run_id)

    def test_a_forged_question_type_cannot_buy_the_later_seal(self):
        """改 manifest 的題型不能換到晚 30 秒：question.json 仍在旁邊對帳。"""
        result = self.drill("d77774")
        manifest_path = result.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["question_type"] = "single_asset_market_state"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaises(RunVerificationError):
            verify_run(self.data_root, result.run_id)


class DrillCoversEveryApprovedQuestionTypeTest(unittest.TestCase):
    """One end-to-end drill per approved question type, all at zero cost."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def drill_cli(self, question):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(
            [
                "drill",
                "--provider-mode", "fake",
                "--question", question,
                "--data-root", str(self.data_root),
            ],
            stdout=stdout,
            stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_drill(self, question, question_type, stances, assets, asset_class=None):
        code, out, err = self.drill_cli(question)

        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual("VERIFIED", payload["verification"]["status"])
        run_dir = Path(payload["run_dir"])

        recorded = json.loads((run_dir / "question.json").read_text(encoding="utf-8"))
        self.assertEqual(question_type, recorded["question_type"])
        self.assertEqual(list(assets), recorded["assets"])
        if asset_class is not None:
            self.assertEqual(asset_class, recorded["asset_class"])

        votes = json.loads((run_dir / "votes.json").read_text(encoding="utf-8"))
        self.assertEqual(set(stances), set(votes["tally"]))
        self.assertEqual(6, votes["tally"][stances[0]])
        self.assertEqual(1, votes["tally"][stances[1]])
        self.assertEqual(0, votes["tally"][stances[2]])
        self.assertEqual(stances[0], votes["adopted_stance"])
        self.assertEqual("consensus", votes["consensus_status"])

        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(votes["tally"], manifest["tally"])
        self.assertEqual(list(assets), manifest["assets"])

        evidence = [
            json.loads(line)
            for line in (run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(7, len(evidence))
        self.assertEqual(
            set(assets) or {FALLBACK_EVIDENCE_ASSET},
            {card["asset"] for card in evidence},
        )
        return run_dir, recorded

    def test_single_asset_market_state_drill_uses_market_stances(self):
        self.assert_drill(
            "分析 ETH 過去 14 日市場狀態",
            "single_asset_market_state",
            ("bullish", "bearish", "neutral"),
            ("ETH",),
            asset_class="crypto",
        )

    def test_two_asset_comparison_drill_uses_comparison_stances_and_both_assets(self):
        self.assert_drill(
            "比較 XRP 與 BTC 過去 7 日的市場位置與風險",
            "two_asset_comparison",
            ("asset_a_stronger", "asset_b_stronger", "no_clear_difference"),
            ("XRP", "BTC"),
            asset_class="crypto",
        )

    def test_event_impact_drill_uses_event_stances(self):
        self.assert_drill(
            "評估網路升級事件對 SOL 的影響",
            "event_impact",
            ("positive", "negative", "unclear_or_conditional"),
            ("SOL",),
            asset_class="crypto",
        )

    def test_open_proposition_drill_uses_open_stances_and_a_degraded_proposition(self):
        package = open_proposition_package()
        if package is None:
            self.skipTest("open_proposition 題型尚未落地")

        stances = tuple(package.stance_options)
        _, recorded = self.assert_drill(
            OPEN_QUESTION,
            package.question_type,
            stances,
            package.assets,
        )

        self.assertEqual(OPEN_QUESTION, recorded["proposition"])

    def test_taiwan_listing_runs_the_whole_drill(self):
        _, recorded = self.assert_drill(
            "幫我分析 2330 未來七天會不會漲",
            "open_proposition",
            ("affirmative", "negative_side", "undecided"),
            ("2330",),
            asset_class="tw_stock",
        )

        self.assertEqual(7, recorded["period_days"])
        self.assertEqual("幫我分析 2330 未來七天會不會漲", recorded["proposition"])

    def test_us_listing_runs_the_whole_drill(self):
        self.assert_drill(
            "NVDA 這檔美股未來七天股價會不會漲",
            "open_proposition",
            ("affirmative", "negative_side", "undecided"),
            ("NVDA",),
            asset_class="us_stock",
        )

    def test_coin_outside_the_old_whitelist_runs_the_whole_drill(self):
        self.assert_drill(
            "分析 DOGE 幣價過去 14 日市場狀態",
            "single_asset_market_state",
            ("bullish", "bearish", "neutral"),
            ("DOGE",),
            asset_class="crypto",
        )

    def test_a_question_naming_no_asset_runs_the_whole_drill(self):
        """開放命題不再 fail closed，整條管線照跑。"""
        self.assert_drill(
            "幫我預測下週樂透號碼",
            "open_proposition",
            ("affirmative", "negative_side", "undecided"),
            (),
            asset_class="open",
        )

    def test_a_blank_question_is_the_only_shape_left_that_fails_closed(self):
        code, _, err = self.drill_cli("   ")

        self.assertEqual(1, code)
        self.assertIn("DRILL FAILED", err)


if __name__ == "__main__":
    unittest.main()
