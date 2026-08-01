"""Run identity and the append-only, never-overwriting run store."""

import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents.run_store import (
    ArtifactAlreadyExistsError,
    RunAlreadyExistsError,
    RunStore,
    new_run_id,
)

RUN_ID_SHAPE = re.compile(r"^\d{8}T\d{6}Z-[a-z-]+-[0-9a-f]{6}$")


class RunIdTest(unittest.TestCase):
    def test_run_id_uses_utc_start_asset_slug_and_short_token(self):
        started = datetime(2026, 8, 1, 7, 30, 0, tzinfo=timezone.utc)

        run_id = new_run_id(started, "btc", token="8f3a2c")

        self.assertEqual("20260801T073000Z-btc-8f3a2c", run_id)
        self.assertRegex(run_id, RUN_ID_SHAPE)

    def test_run_ids_differ_even_within_the_same_second(self):
        clock = FixedClock()
        tokens = ScriptedTokenSource(["aaa111", "bbb222"])

        first = new_run_id(clock.utc_now(), "btc", token=tokens())
        second = new_run_id(clock.utc_now(), "btc", token=tokens())

        self.assertNotEqual(first, second)


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.store = RunStore(self.data_root)

    def test_create_run_makes_run_and_seat_directories(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["spot-technical", "news"])

        self.assertTrue(run.path.is_dir())
        self.assertEqual(self.data_root / "runs" / "20260801T073000Z-btc-8f3a2c", run.path)
        self.assertTrue(run.seat_dir("spot-technical").is_dir())
        self.assertTrue(run.seat_dir("news").is_dir())

    def test_creating_the_same_run_id_twice_fails_closed(self):
        self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

        with self.assertRaises(RunAlreadyExistsError):
            self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

    def test_artifacts_are_write_once(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])
        run.write_json("manifest.json", {"run_id": "20260801T073000Z-btc-8f3a2c"})

        with self.assertRaises(ArtifactAlreadyExistsError):
            run.write_json("manifest.json", {"run_id": "tampered"})

        self.assertEqual(
            "20260801T073000Z-btc-8f3a2c",
            json.loads((run.path / "manifest.json").read_text(encoding="utf-8"))["run_id"],
        )

    def test_jsonl_artifact_writes_one_json_object_per_line(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

        run.write_jsonl("evidence.jsonl", [{"evidence_id": "a"}, {"evidence_id": "b"}])

        lines = (run.path / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(["a", "b"], [json.loads(line)["evidence_id"] for line in lines])

    def test_second_run_never_touches_the_first_run(self):
        first = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        first.write_text("report.md", "# first run")

        second = self.store.create_run("20260801T073000Z-btc-bbb222", ["news"])
        second.write_text("report.md", "# second run")

        self.assertEqual("# first run", (first.path / "report.md").read_text(encoding="utf-8"))
        self.assertEqual("# second run", (second.path / "report.md").read_text(encoding="utf-8"))
        self.assertNotEqual(first.path, second.path)

    def test_latest_pointer_is_the_only_mutable_file(self):
        first = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        self.store.point_latest_at(first)
        second = self.store.create_run("20260801T073000Z-btc-bbb222", ["news"])
        self.store.point_latest_at(second)

        latest = json.loads((self.data_root / "runs" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual("20260801T073000Z-btc-bbb222", latest["run_id"])
        self.assertEqual(str(second.path), latest["run_dir"])

    def test_content_hash_is_recorded_for_written_artifacts(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        run.write_text("report.md", "# first run")

        digest = run.artifact_hashes["report.md"]
        self.assertEqual(64, len(digest))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
