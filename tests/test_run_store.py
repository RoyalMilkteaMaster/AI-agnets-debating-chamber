"""Run identity and the append-only, never-overwriting run store."""

import json
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from tests.fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents.run_store import (
    ArtifactAlreadyExistsError,
    ArtifactTamperedError,
    FormatRepairSemanticChangeError,
    RunAlreadyExistsError,
    RunStoreError,
    RunStore,
    SnapshotSealedError,
    deduplicate_evidence,
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
        self.assertTrue((run.seat_dir("news") / "attempts").is_dir())
        for name in ("snapshots", "reports", "late", "diagnostics"):
            self.assertTrue((run.path / name).is_dir(), name)

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

    def test_parallel_seat_attempt_writes_are_isolated_and_atomic(self):
        run = self.store.create_run(
            "20260801T073000Z-btc-aaa111", ["spot-technical", "news"]
        )
        barrier = threading.Barrier(2)

        def submit(seat_id):
            barrier.wait()
            return run.record_attempt(
                seat_id,
                seat_id + "-a1",
                raw_text='{"seat_id":"' + seat_id + '"}',
                validated_payload={"seat_id": seat_id, "records": []},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ("spot-technical", "news")))

        self.assertEqual([True, True], results)
        for seat_id in ("spot-technical", "news"):
            attempt = run.seat_dir(seat_id) / "attempts" / (seat_id + "-a1")
            self.assertEqual(
                seat_id,
                json.loads((attempt / "validated.json").read_text(encoding="utf-8"))["seat_id"],
            )

    def test_first_valid_attempt_is_adopted_and_later_success_is_diagnostic(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])

        first = run.record_attempt("news", "news-a1", "{}", {"records": [1]})
        second = run.record_attempt("news", "news-a2", "{}", {"records": [2]})

        self.assertTrue(first)
        self.assertFalse(second)
        adopted = json.loads((run.seat_dir("news") / "adopted.json").read_text(encoding="utf-8"))
        self.assertEqual("news-a1", adopted["attempt_id"])
        diagnostic = json.loads(
            (run.path / "diagnostics" / "attempts" / "news-a2.json").read_text(encoding="utf-8")
        )
        self.assertEqual("not_adopted_first_valid_already_selected", diagnostic["reason"])

    def test_syndicated_sources_are_not_counted_twice(self):
        cards = [
            {
                "evidence_id": "news-01",
                "source_url": "https://wire.invalid/story",
                "source_origin": "press-release:abc",
            },
            {
                "evidence_id": "news-02",
                "source_url": "https://publisher.invalid/repost",
                "source_origin": "press-release:abc",
            },
        ]

        unique, duplicates = deduplicate_evidence(cards)

        self.assertEqual(["news-01"], [card["evidence_id"] for card in unique])
        self.assertEqual(
            [{"evidence_id": "news-02", "duplicate_of": "news-01"}], duplicates
        )

    def test_sealed_snapshot_cannot_be_replaced_and_tampering_is_detected(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        records = [{"evidence_id": "news-01"}]

        seal = run.seal_evidence_snapshot(records, "2026-08-01T07:35:00Z", 300000)
        self.assertEqual(64, len(seal["sha256"]))
        self.assertEqual(seal, run.verify_evidence_snapshot())
        with self.assertRaises(SnapshotSealedError):
            run.seal_evidence_snapshot(records, "2026-08-01T07:35:01Z", 301000)

        (run.path / seal["path"]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ArtifactTamperedError):
            run.verify_evidence_snapshot()

    def test_format_repair_preserves_before_after_and_rejects_semantic_change(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        trailing_comma = '{"stance":"bullish","evidence_ids":["news-01"],}'
        run.record_attempt(
            "news", "news-a1", trailing_comma, {"records": ["pre-repair capture"]}
        )

        path = run.record_format_repair(
            repair_id="repair-01",
            seat_id="news",
            source_attempt_id="news-a1",
            repair_attempt_id="format-repair-a1",
            before_text=trailing_comma,
            after_text='{\n  "stance": "bullish",\n  "evidence_ids": ["news-01"]\n}',
            reason="normalize JSON formatting",
            operator="format-repair-agent",
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("before", record)
        self.assertIn("after", record)
        self.assertEqual("news-a1", record["lineage"]["source_attempt_id"])
        self.assertEqual(
            "agents/news/attempts/news-a1/raw.txt", record["lineage"]["source_path"]
        )
        self.assertRegex(record["lineage"]["source_sha256"], r"^[0-9a-f]{64}$")

        numeric = '{"value":1}'
        run.record_attempt("news", "news-a2", numeric, {"records": []})
        with self.assertRaises(FormatRepairSemanticChangeError):
            run.record_format_repair(
                repair_id="repair-02",
                seat_id="news",
                source_attempt_id="news-a2",
                repair_attempt_id="format-repair-a2",
                before_text=numeric,
                after_text='{"value":true}',
                reason="must preserve JSON types",
                operator="format-repair-agent",
            )

        with self.assertRaises(FormatRepairSemanticChangeError):
            run.record_format_repair(
                repair_id="repair-03",
                seat_id="news",
                source_attempt_id="news-a1",
                repair_attempt_id="format-repair-a3",
                before_text=trailing_comma,
                after_text='{"stance":"bearish"}',
                reason="not a format-only repair",
                operator="format-repair-agent",
            )

        with self.assertRaises(RunStoreError) as caught:
            run.record_format_repair(
                repair_id="repair-04",
                seat_id="news",
                source_attempt_id="news-missing",
                repair_attempt_id="format-repair-a4",
                before_text="{}",
                after_text="{}",
                reason="missing source",
                operator="format-repair-agent",
            )
        self.assertIn("news-missing", str(caught.exception))

    def test_manifest_index_traces_hash_and_source_and_detects_tamper(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        run.write_text("evidence.jsonl", "{}\n", source="validated seat attempts")

        index = run.artifact_index()

        self.assertEqual("evidence.jsonl", index["evidence.jsonl"]["path"])
        self.assertEqual("validated seat attempts", index["evidence.jsonl"]["source"])
        self.assertTrue(run.verify_artifacts(index))
        (run.path / "evidence.jsonl").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ArtifactTamperedError):
            run.verify_artifacts(index)


if __name__ == "__main__":
    unittest.main()
