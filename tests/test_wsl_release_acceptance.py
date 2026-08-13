import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hoya_market_agents.seats import SEAT_IDS


ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = (
    ROOT
    / "docs"
    / "work"
    / "wsl-only-runtime-onboarding"
    / "acceptance"
    / "tools"
    / "acceptance.py"
)
SPEC = importlib.util.spec_from_file_location("wsl_release_acceptance", TOOL_PATH)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class DataRootSnapshotTest(unittest.TestCase):
    RUN_ID = "20260813T010203Z-btc-market1"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_directory_timestamp_drift_is_not_a_content_change(self):
        (self.root / "runs").mkdir()
        before = acceptance.snapshot_data_root(self.root)
        (self.root / "runs" / "2026-08-13").mkdir()
        after = acceptance.snapshot_data_root(self.root)

        result = acceptance.compare_snapshots(
            before, after, "20260813T010203Z-btc-market1"
        )

        self.assertEqual("PASS", result["status"])

    def test_existing_run_content_change_fails_closed(self):
        old = self.root / "runs" / "2026-08-13" / "old-run" / "report.html"
        old.parent.mkdir(parents=True)
        old.write_text("before", encoding="utf-8")
        before = acceptance.snapshot_data_root(self.root)
        old.write_text("after", encoding="utf-8")
        after = acceptance.snapshot_data_root(self.root)

        result = acceptance.compare_snapshots(
            before, after, "20260813T010203Z-btc-market1"
        )

        self.assertEqual("FAIL", result["status"])
        self.assertIn(
            "runs/2026-08-13/old-run/report.html", result["problems"]
        )

    def test_special_file_added_to_snapshot_diff_fails_closed(self):
        before = {"records": [], "index_rows": []}
        after = {"records": [{"path": "logs/new-link", "type": "symlink"}], "index_rows": []}

        result = acceptance.compare_snapshots(
            before, after, "20260813T010203Z-btc-market1"
        )

        self.assertEqual("FAIL", result["status"])

    def test_exact_inbox_run_root_is_an_allowed_addition(self):
        before = {"records": [], "index_rows": []}
        after = {
            "index_rows": [],
            "records": [
                {"path": "inbox/{}".format(self.RUN_ID), "type": "directory"}
            ]
        }

        result = acceptance.compare_snapshots(before, after, self.RUN_ID)

        self.assertEqual("PASS", result["status"])

    def test_real_latest_and_index_paths_are_allowed_only_after_exact_readback(self):
        runs = self.root / "runs"
        runs.mkdir()
        run_dir = runs / "2026-08-13" / "0902-btc-1234567890abcdef"
        run_dir.mkdir(parents=True)
        (runs / "latest.json").write_text(
            json.dumps({"run_id": self.RUN_ID, "run_dir": str(run_dir)}) + "\n",
            encoding="utf-8",
        )
        before = {
            "records": [],
            "index_rows": [],
        }
        after = {
            "root": str(self.root),
            "records": [
                {"path": "runs/latest.json", "type": "file"},
                {"path": "runs/index.db", "type": "file"},
            ],
            "index_rows": [{"run_id": self.RUN_ID}],
        }
        with (
            mock.patch.object(acceptance, "resolve_run_dir", return_value=run_dir),
            mock.patch.object(
                acceptance, "query_runs", return_value=[{"run_id": self.RUN_ID}]
            ),
        ):
            result = acceptance.compare_snapshots(before, after, self.RUN_ID)

        self.assertEqual("PASS", result["status"])

    def test_latest_pointer_to_another_run_fails_closed(self):
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "latest.json").write_text(
            json.dumps({"run_id": "another-run", "run_dir": "/tmp/another-run"})
            + "\n",
            encoding="utf-8",
        )
        after = {
            "root": str(self.root),
            "records": [{"path": "runs/latest.json", "type": "file"}],
            "index_rows": [],
        }

        result = acceptance.compare_snapshots({"records": [], "index_rows": []}, after, self.RUN_ID)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("runs/latest.json", result["problems"])

    def test_index_change_without_exact_run_readback_fails_closed(self):
        after = {
            "root": str(self.root),
            "records": [{"path": "runs/index.db", "type": "file"}],
            "index_rows": [],
        }
        with mock.patch.object(acceptance, "query_runs", return_value=[]):
            result = acceptance.compare_snapshots(
                {"records": [], "index_rows": []}, after, self.RUN_ID
            )

        self.assertEqual("FAIL", result["status"])
        self.assertIn("runs/index.db", result["problems"])

    def test_explicit_provider_bin_dir_on_windows_mount_is_refused(self):
        with mock.patch.object(Path, "is_file", return_value=True), mock.patch.object(
            acceptance.os, "access", return_value=True
        ):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.resolve_provider("codex", "/mnt/d/provider-bin")

    def test_removed_allowed_pointer_still_fails_closed(self):
        before = {"records": [{"path": "runs/latest.json", "type": "file"}], "index_rows": []}
        after = {"records": [], "index_rows": []}
        result = acceptance.compare_snapshots(before, after, self.RUN_ID)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("runs/latest.json", result["problems"])

    def test_active_launch_log_must_preserve_old_bytes_as_prefix(self):
        log = self.root / "logs" / "launch.log"
        log.parent.mkdir()
        log.write_bytes(b"old\n")
        before = acceptance.snapshot_data_root(self.root)
        log.write_bytes(b"old\nnew\n")
        after = acceptance.snapshot_data_root(self.root)
        self.assertEqual("PASS", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])
        log.write_bytes(b"bad\nnew\n")
        after = acceptance.snapshot_data_root(self.root)
        self.assertEqual("FAIL", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])

    def test_webapp_rollover_may_append_before_rotation_but_preserves_prefix(self):
        active = self.root / "logs" / "webapp.jsonl"
        active.parent.mkdir()
        active.write_bytes(b"before\n")
        before = acceptance.snapshot_data_root(self.root)
        rotated = active.with_name("webapp-2026-08-12.jsonl")
        rotated.write_bytes(b"before\nbetween\n")
        active.write_bytes(b"after\n")
        after = acceptance.snapshot_data_root(self.root)
        self.assertEqual("PASS", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])
        rotated.write_bytes(b"tamper\nbetween\n")
        after = acceptance.snapshot_data_root(self.root)
        self.assertEqual("FAIL", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])

    def test_index_logical_history_may_only_add_exact_run(self):
        old = {"run_id": "old", "question": "kept"}
        exact = {"run_id": self.RUN_ID, "question": "new"}
        before = {"records": [], "index_rows": [old]}
        after = {"records": [], "index_rows": [old, exact]}
        self.assertEqual("PASS", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])
        after["index_rows"][0] = {"run_id": "old", "question": "changed"}
        self.assertEqual("FAIL", acceptance.compare_snapshots(before, after, self.RUN_ID)["status"])

    def test_old_snapshot_without_logical_rows_fails_closed(self):
        result = acceptance.compare_snapshots({"records": []}, {"records": [], "index_rows": []}, self.RUN_ID)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("runs/index.db:logical_rows", result["problems"])


class ProviderCanaryGuardTest(unittest.TestCase):
    def test_canary_requires_explicit_provider_execution_flag(self):
        with mock.patch.object(acceptance, "run_canary") as run_canary:
            code = acceptance.main(
                [
                    "canary",
                    "codex",
                    "--scratch-root",
                    "/tmp/unused",
                    "--output",
                    "/tmp/unused.json",
                ]
            )

        self.assertEqual(2, code)
        run_canary.assert_not_called()

    def test_provider_resolution_refuses_windows_mount(self):
        with mock.patch.object(acceptance.shutil, "which", return_value="/mnt/c/codex"):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.resolve_provider("codex")

    def test_offline_sitecustomize_refuses_a_real_provider_path(self):
        guard = TOOL_PATH.parent.parent / "offline_guard"
        environment = dict(os.environ)
        environment["HOYA_OFFLINE_PROVIDER_GUARD"] = "1"
        environment["PYTHONPATH"] = str(guard)
        probe = subprocess.run(
            [
                os.fsdecode(Path(os.sys.executable)),
                "-c",
                "import subprocess; subprocess.Popen(['/home/leslie/.local/bin/claude'])",
            ],
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertNotEqual(0, probe.returncode)
        self.assertIn("offline acceptance refused real provider CLI", probe.stderr)

    def _guard_probe(self, source):
        guard = TOOL_PATH.parent.parent / "offline_guard"
        environment = dict(os.environ, HOYA_OFFLINE_PROVIDER_GUARD="1", PYTHONPATH=str(guard))
        return subprocess.run(
            [os.fsdecode(Path(os.sys.executable)), "-c", source],
            capture_output=True, text=True, env=environment,
        )

    def test_offline_guard_refuses_indirect_shell_provider_calls(self):
        sources = (
            "import subprocess; subprocess.Popen(['bash','-c','/home/leslie/.local/bin/codex exec'])",
            "import subprocess; subprocess.Popen('/home/leslie/.local/bin/claude -p x', shell=True)",
            "import os; os.system('/home/leslie/.local/bin/agy --print')",
        )
        for source in sources:
            with self.subTest(source=source):
                probe = self._guard_probe(source)
                self.assertNotEqual(0, probe.returncode)
                self.assertIn("offline acceptance refused real provider CLI", probe.stderr)

    def test_offline_guard_does_not_block_unrelated_shell(self):
        probe = self._guard_probe("import subprocess; subprocess.run(['bash','-c','true'],check=True)")
        self.assertEqual(0, probe.returncode, probe.stderr)

    def test_run_sh_audits_after_each_provider_and_has_no_hardcoded_git_dir(self):
        script = (TOOL_PATH.parent.parent / "run.sh").read_text(encoding="utf-8")
        self.assertIn('resolve-git-dir --code-root "$ROOT"', script)
        self.assertIn('$provider-process-audit.json', script)
        self.assertNotIn("AI-agnets-debating-chamber/.git/worktrees", script)

    def test_git_dir_resolver_supports_clone_and_windows_worktree_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".git").mkdir()
            self.assertEqual((root / ".git").resolve(), acceptance.resolve_git_dir(root))
        actual = acceptance.resolve_git_dir(ROOT)
        windows = "D:\\" + str(actual)[len("/mnt/d/"):].replace("/", "\\")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".git").write_text("gitdir: {}\n".format(windows), encoding="utf-8")
            resolved = acceptance.resolve_git_dir(root)
        self.assertEqual(actual, resolved)


class ReconcileRunTest(unittest.TestCase):
    RUN_ID = "20260813T010203Z-btc-market1"
    ARTIFACT_NAMES = (
        "votes.json",
        "diagnostics/launch-summary.json",
        "events.jsonl",
        "evidence.jsonl",
        "debate.jsonl",
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name)
        (self.run_dir / "diagnostics").mkdir()
        attempts = {seat: "{}-a1".format(seat) for seat in SEAT_IDS}
        self._write_json(
            "votes.json",
            {
                "valid_vote_count": 7,
                "votes": [
                    {
                        "seat_id": seat,
                        "state": "valid",
                        "final_stance": "affirmative",
                        "attempt_ids": [attempts[seat]],
                    }
                    for seat in SEAT_IDS
                ],
            },
        )
        self._write_json(
            "diagnostics/launch-summary.json",
            {
                "seats": [
                    {
                        "seat_id": seat,
                        "adopted": True,
                        "adopted_attempt_id": attempts[seat],
                        "attempt_ids": [attempts[seat]],
                        "attempts": [
                            {
                                "attempt_id": attempts[seat],
                                "attempt_kind": "primary",
                                "terminal_outcome": "adopted",
                            }
                        ],
                    }
                    for seat in SEAT_IDS
                ]
            },
        )
        self._write_jsonl(
            "events.jsonl",
            [record for seat in SEAT_IDS for record in (
                {"event": "attempt_outcome_recorded", "seat_id": seat, "attempt_id": attempts[seat], "terminal_outcome": "adopted"},
                {"event": "opening_invocation_lineage_recorded", "seat_id": seat, "research_attempt_id": attempts[seat]},
                {"event": "opening_result_received", "seat_id": seat, "research_attempt_id": attempts[seat]},
            )],
        )
        self._write_jsonl(
            "evidence.jsonl",
            [
                {
                    "seat_id": seat,
                    "attempt_id": attempts[seat],
                    "run_id": self.RUN_ID,
                }
                for seat in SEAT_IDS
            ],
        )
        self._write_jsonl(
            "debate.jsonl",
            [self._position_entry(seat, attempts[seat]) for seat in SEAT_IDS],
        )
        for seat in SEAT_IDS:
            self._write_json(
                "agents/{}/adopted.json".format(seat),
                {
                    "run_id": self.RUN_ID,
                    "seat_id": seat,
                    "attempt_id": attempts[seat],
                    "validated_path": "agents/{}/attempts/{}/validated.json".format(seat, attempts[seat]),
                },
            )
        artifacts = {}
        for name in self.ARTIFACT_NAMES:
            artifacts[name] = {
                "path": name,
                "sha256": acceptance.sha256_bytes((self.run_dir / name).read_bytes()),
                "source": "test",
            }
        self._write_json(
            "manifest.json",
            {
                "run_id": self.RUN_ID,
                "provider_mode": "real-subscription-fast",
                "competition_ready": False,
                "competition_timeline": {},
                "seats": [
                    {"seat_id": seat, "attempt_ids": [attempts[seat]]}
                    for seat in SEAT_IDS
                ],
                "artifacts": artifacts,
            },
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_json(self, name, value):
        target = self.run_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def _write_jsonl(self, name, values):
        target = self.run_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps(value) + "\n" for value in values),
            encoding="utf-8",
        )

    def _read_json(self, name):
        return json.loads((self.run_dir / name).read_text(encoding="utf-8"))

    def _read_jsonl(self, name):
        return [
            json.loads(line)
            for line in (self.run_dir / name).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _reseal_manifest(self):
        manifest = self._read_json("manifest.json")
        for name in self.ARTIFACT_NAMES:
            manifest["artifacts"][name]["sha256"] = acceptance.sha256_bytes(
                (self.run_dir / name).read_bytes()
            )
        self._write_json("manifest.json", manifest)

    def _seal_summary(self, summary):
        """Persist launch-summary, realign its terminal events, reseal manifest."""
        self._write_json("diagnostics/launch-summary.json", summary)
        others = [
            row
            for row in self._read_jsonl("events.jsonl")
            if row.get("event") != "attempt_outcome_recorded"
        ]
        terminal = [
            {
                "event": "attempt_outcome_recorded",
                "seat_id": seat["seat_id"],
                "attempt_id": attempt.get("attempt_id"),
                "terminal_outcome": attempt.get("terminal_outcome"),
            }
            for seat in summary["seats"]
            for attempt in seat["attempts"]
        ]
        self._write_jsonl("events.jsonl", terminal + others)
        self._reseal_manifest()

    def _add_backup_attempt(self, seat_id, terminal_outcome="cancelled"):
        """Give one seat a research backup attempt that never entered the debate."""
        summary = self._read_json("diagnostics/launch-summary.json")
        row = self._summary_seat(summary, seat_id)
        backup_id = "{}-a2".format(seat_id)
        row["attempt_ids"] = [row["adopted_attempt_id"], backup_id]
        row["attempts"].append(
            {
                "attempt_id": backup_id,
                "attempt_kind": "backup",
                "terminal_outcome": terminal_outcome,
            }
        )
        self._seal_summary(summary)
        return backup_id

    def _adopt_backup_attempt(self, seat_id):
        """Adopt the seat's research backup.

        Only the research lineage moves. The debate speech lineage legitimately
        stays rooted at ``seat-a1``, because the two are independent namespaces.
        """
        backup_id = self._add_backup_attempt(seat_id, terminal_outcome="adopted")
        summary = self._read_json("diagnostics/launch-summary.json")
        seat = self._summary_seat(summary, seat_id)
        seat["adopted_attempt_id"] = backup_id
        seat["attempts"][0]["terminal_outcome"] = "superseded"
        self._seal_summary(summary)
        self._rebind_research_lineage(seat_id, backup_id)
        return backup_id

    def _rebind_research_lineage(self, seat_id, attempt_id):
        """Point every research-lineage artifact of one seat at ``attempt_id``."""
        evidence = [
            {**row, "attempt_id": attempt_id} if row["seat_id"] == seat_id else row
            for row in self._read_jsonl("evidence.jsonl")
        ]
        self._write_jsonl("evidence.jsonl", evidence)
        opening = {"opening_invocation_lineage_recorded", "opening_result_received"}
        events = [
            {**row, "research_attempt_id": attempt_id}
            if row["seat_id"] == seat_id and row["event"] in opening
            else row
            for row in self._read_jsonl("events.jsonl")
        ]
        self._write_jsonl("events.jsonl", events)
        debate = self._read_jsonl("debate.jsonl")
        position = next(
            row
            for row in debate
            if row["seat_id"] == seat_id and row["kind"] == "position"
        )
        position["content"]["research_attempt_id"] = attempt_id
        self._write_jsonl("debate.jsonl", debate)
        self._write_json(
            "agents/{}/adopted.json".format(seat_id),
            {
                "run_id": self.RUN_ID,
                "seat_id": seat_id,
                "attempt_id": attempt_id,
                "validated_path": "agents/{}/attempts/{}/validated.json".format(
                    seat_id, attempt_id
                ),
            },
        )
        self._reseal_manifest()

    @staticmethod
    def _summary_seat(summary, seat_id):
        return next(row for row in summary["seats"] if row["seat_id"] == seat_id)

    @staticmethod
    def _position_entry(seat_id, research_attempt_id):
        """A sealed debate entry keeps the research lineage inside ``content``."""
        content = {
            "seat_id": seat_id,
            "attempt_id": "{}-a1".format(seat_id),
            "kind": "position",
            "message_id": "{}-position".format(seat_id),
            "research_attempt_id": research_attempt_id,
        }
        return {
            "event": "seat_message",
            "seat_id": seat_id,
            "attempt_id": content["attempt_id"],
            "kind": "position",
            "message_id": content["message_id"],
            "content": content,
        }

    def _reconcile(self):
        with mock.patch.object(acceptance, "resolve_run_dir", return_value=self.run_dir):
            return acceptance.reconcile_run("/unused", self.RUN_ID)

    def test_reconciliation_accepts_seven_lineage_bound_votes(self):
        result = self._reconcile()

        self.assertEqual("PASS", result["status"])
        self.assertEqual(7, result["valid_vote_count"])

    def test_reconciliation_accepts_adopted_primary_with_cancelled_backup(self):
        self._add_backup_attempt(SEAT_IDS[0])

        result = self._reconcile()

        self.assertEqual("PASS", result["status"], result["problems"])

    def test_reconciliation_rejects_a_second_adopted_attempt_outcome(self):
        self._add_backup_attempt(SEAT_IDS[0], terminal_outcome="adopted")

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_accepts_a_seat_that_adopted_its_research_backup(self):
        backup_id = self._adopt_backup_attempt(SEAT_IDS[0])

        result = self._reconcile()

        self.assertEqual("PASS", result["status"], result["problems"])
        self.assertEqual(backup_id, result["adopted_attempts"][SEAT_IDS[0]])

    def test_reconciliation_rejects_an_adoption_no_research_artifact_backs(self):
        """Moving the adoption pointer alone must not pass: the research lineage
        artifacts bind the adoption, not the debate speech attempt ids."""
        backup_id = self._add_backup_attempt(SEAT_IDS[0], terminal_outcome="adopted")
        summary = self._read_json("diagnostics/launch-summary.json")
        row = self._summary_seat(summary, SEAT_IDS[0])
        row["adopted_attempt_id"] = backup_id
        row["attempts"][0]["terminal_outcome"] = "superseded"
        self._seal_summary(summary)

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("evidence_attempt_cross_bound", result["problems"])

    def test_reconciliation_rejects_summary_attempt_ids_out_of_step(self):
        seat_id = SEAT_IDS[0]
        primary_id = "{}-a1".format(seat_id)
        backup_id = self._add_backup_attempt(seat_id)
        shapes = {
            "missing": [primary_id],
            "extra": [primary_id, backup_id, "{}-a3".format(seat_id)],
            "reordered": [backup_id, primary_id],
            "duplicated": [primary_id, primary_id],
        }
        for label, attempt_ids in shapes.items():
            with self.subTest(shape=label):
                summary = self._read_json("diagnostics/launch-summary.json")
                self._summary_seat(summary, seat_id)["attempt_ids"] = attempt_ids
                self._seal_summary(summary)

                result = self._reconcile()

                self.assertEqual("FAIL", result["status"])
                self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_duplicate_attempts_inside_summary(self):
        seat_id = SEAT_IDS[0]
        self._add_backup_attempt(seat_id)
        summary = self._read_json("diagnostics/launch-summary.json")
        row = self._summary_seat(summary, seat_id)
        row["attempts"][1]["attempt_id"] = row["attempts"][0]["attempt_id"]
        row["attempt_ids"] = [attempt["attempt_id"] for attempt in row["attempts"]]
        self._seal_summary(summary)

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_unknown_backup_terminal_outcome(self):
        self._add_backup_attempt(SEAT_IDS[0], terminal_outcome="done")

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_backup_attempt_without_terminal_outcome(self):
        self._add_backup_attempt(SEAT_IDS[0])
        summary = self._read_json("diagnostics/launch-summary.json")
        row = self._summary_seat(summary, SEAT_IDS[0])
        row["attempts"][1].pop("terminal_outcome")
        self._seal_summary(summary)

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_public_position_bound_to_another_attempt(self):
        entries = self._read_jsonl("debate.jsonl")
        entries[0]["content"]["research_attempt_id"] = "{}-a2".format(SEAT_IDS[0])
        self._write_jsonl("debate.jsonl", entries)
        self._reseal_manifest()

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("opening_public_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_public_position_without_sealed_content(self):
        entries = self._read_jsonl("debate.jsonl")
        entries[0].pop("content")
        self._write_jsonl("debate.jsonl", entries)
        self._reseal_manifest()

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("opening_public_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_tampered_artifact(self):
        (self.run_dir / "votes.json").write_text("{}\n", encoding="utf-8")

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("artifact_hash_mismatch", result["problems"])

    def test_reconciliation_rejects_cross_bound_or_duplicate_lineage(self):
        votes = self._read_json("votes.json")
        votes["votes"][0]["attempt_ids"] = ["derivatives-a1"]
        self._write_json("votes.json", votes)
        self._reseal_manifest()

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_empty_vote_lineage(self):
        votes = self._read_json("votes.json")
        votes["votes"][0]["attempt_ids"] = []
        self._write_json("votes.json", votes)
        manifest = self._read_json("manifest.json")
        manifest["seats"][0]["attempt_ids"] = []
        self._write_json("manifest.json", manifest)
        self._reseal_manifest()

        result = self._reconcile()

        self.assertEqual("FAIL", result["status"])
        self.assertIn("attempt_lineage_cross_bound", result["problems"])

    def test_reconciliation_rejects_wrong_run_contract(self):
        manifest = self._read_json("manifest.json")
        manifest["provider_mode"] = "real-subscription"
        self._write_json("manifest.json", manifest)

        result = self._reconcile()

        self.assertIn("run_contract_not_release_fast", result["problems"])


if __name__ == "__main__":
    unittest.main()
