"""The seven fixed seats and the explicit record contracts they must satisfy."""

import unittest

from hoya_market_agents.contract_validator import (
    CONTRACT_VERSION,
    ContractViolationError,
    MAX_EVIDENCE_CARDS_PER_SEAT,
    validate_debate_turn,
    validate_evidence_card,
    validate_seat_evidence,
    validate_vote,
)
from hoya_market_agents.seats import SEAT_IDS, load_roster


def evidence_card(**overrides):
    card = {
        "schema_version": CONTRACT_VERSION,
        "evidence_id": "spot-technical-01",
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "research",
        "created_at_utc": "2026-08-01T07:30:05Z",
        "elapsed_ms": 5000,
        "asset": "BTC",
        "category": "spot-price",
        "statement": "現貨收盤價較 14 日前上升 4.2%。",
        "direction": "support",
        "source_url": "https://fake.invalid/spot/btc",
        "source_origin": "https://fake.invalid/spot/btc",
        "source_tier": 1,
        "published_at_utc": "2026-08-01T07:00:00Z",
        "retrieved_at_utc": "2026-08-01T07:30:05Z",
        "excerpt": "close 68,420 / 14d ago 65,660",
        "credibility_note": "fake provider 產生的示範資料，不得作為市場依據。",
    }
    card.update(overrides)
    return card


def debate_turn(**overrides):
    turn = {
        "schema_version": CONTRACT_VERSION,
        "turn_id": "spot-technical-r1",
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "debate",
        "created_at_utc": "2026-08-01T07:35:00Z",
        "elapsed_ms": 300000,
        "round": 1,
        "stance": "bullish",
        "public_reason": "價格結構與量能一致偏多。",
        "evidence_ids": ["spot-technical-01"],
        "responds_to": ["counter-evidence-r1"],
        "stance_change_reason": None,
    }
    turn.update(overrides)
    return turn


def vote(**overrides):
    record = {
        "schema_version": CONTRACT_VERSION,
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "seat_id": "spot-technical",
        "attempt_id": "spot-technical-a1",
        "phase": "vote",
        "created_at_utc": "2026-08-01T07:36:00Z",
        "elapsed_ms": 360000,
        "round": 1,
        "stance": "bullish",
        "public_reason": "價格結構與量能一致偏多。",
        "evidence_ids": ["spot-technical-01"],
        "stance_change_reason": None,
    }
    record.update(overrides)
    return record


class SeatRosterTest(unittest.TestCase):
    def test_seven_fixed_seats_in_approved_order(self):
        self.assertEqual(
            (
                "spot-technical",
                "derivatives",
                "onchain",
                "official-events",
                "news",
                "social-macro",
                "counter-evidence",
            ),
            SEAT_IDS,
        )

    def test_roster_config_describes_every_seat(self):
        roster = load_roster()

        self.assertEqual(list(SEAT_IDS), [seat.seat_id for seat in roster])
        for seat in roster:
            self.assertTrue(seat.focus, "seat {} 缺少研究範圍".format(seat.seat_id))
            self.assertEqual(seat.seat_id, seat.output_dir)


class EvidenceCardContractTest(unittest.TestCase):
    def test_valid_card_passes(self):
        validate_evidence_card(evidence_card())

    def test_missing_audit_field_is_rejected(self):
        for field in ("run_id", "seat_id", "attempt_id", "phase", "created_at_utc", "elapsed_ms"):
            card = evidence_card()
            del card[field]
            with self.assertRaises(ContractViolationError) as caught:
                validate_evidence_card(card)
            self.assertIn(field, str(caught.exception))

    def test_unknown_seat_id_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(seat_id="rogue-seat"))
        self.assertIn("seat_id", str(caught.exception))

    def test_direction_enum_is_enforced(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(direction="probably-up"))
        self.assertIn("direction", str(caught.exception))

    def test_source_tier_outside_one_to_three_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_evidence_card(evidence_card(source_tier=4))

    def test_any_named_asset_is_accepted(self):
        for asset in ("DOGE", "NVDA", "2330"):
            with self.subTest(asset=asset):
                card = evidence_card(asset=asset)
                self.assertIs(card, validate_evidence_card(card))

    def test_a_card_without_a_named_asset_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(asset="  "))
        self.assertIn("asset", str(caught.exception))

    def test_non_utc_timestamp_is_rejected(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(created_at_utc="2026-08-01 07:30:05+08:00"))
        self.assertIn("created_at_utc", str(caught.exception))

    def test_negative_elapsed_ms_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_evidence_card(evidence_card(elapsed_ms=-1))

    def test_all_violations_are_reported_together(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_evidence_card(evidence_card(direction="sideways", source_tier=9))
        message = str(caught.exception)
        self.assertIn("direction", message)
        self.assertIn("source_tier", message)


class SeatEvidenceCapTest(unittest.TestCase):
    def test_cap_is_eight_cards_per_seat(self):
        self.assertEqual(8, MAX_EVIDENCE_CARDS_PER_SEAT)

    def test_seat_may_submit_up_to_the_cap(self):
        cards = [evidence_card(evidence_id="spot-technical-{:02d}".format(i)) for i in range(8)]
        validate_seat_evidence("spot-technical", cards)

    def test_seat_exceeding_the_cap_is_rejected(self):
        cards = [evidence_card(evidence_id="spot-technical-{:02d}".format(i)) for i in range(9)]
        with self.assertRaises(ContractViolationError):
            validate_seat_evidence("spot-technical", cards)

    def test_duplicate_evidence_ids_are_rejected(self):
        cards = [evidence_card(), evidence_card()]
        with self.assertRaises(ContractViolationError) as caught:
            validate_seat_evidence("spot-technical", cards)
        self.assertIn("evidence_id", str(caught.exception))

    def test_card_belonging_to_another_seat_is_rejected(self):
        with self.assertRaises(ContractViolationError):
            validate_seat_evidence("news", [evidence_card()])


class PositionContractTest(unittest.TestCase):
    def test_valid_debate_turn_passes(self):
        validate_debate_turn(debate_turn())

    def test_debate_turn_requires_at_least_one_evidence_id(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_debate_turn(debate_turn(evidence_ids=[]))
        self.assertIn("evidence_ids", str(caught.exception))

    def test_debate_round_must_be_positive(self):
        with self.assertRaises(ContractViolationError):
            validate_debate_turn(debate_turn(round=0))

    def test_valid_vote_passes(self):
        validate_vote(vote())

    def test_vote_stance_enum_is_enforced(self):
        with self.assertRaises(ContractViolationError) as caught:
            validate_vote(vote(stance="maybe"))
        self.assertIn("stance", str(caught.exception))


class RunRulesRecordTest(unittest.TestCase):
    """Ticket 11 B2：manifest 記下該 run 當時的完整規則，事後照它驗。

    這一組只驗序列化契約本身（寫得出、讀得回、認得出被動過的）；「verify-run
    用它」由 ``tests/test_verify_run.py`` 驗。
    """

    def setUp(self):
        from hoya_market_agents.debate_rules import debate_rules

        self.rules = debate_rules()

    def record(self):
        from hoya_market_agents.contract_validator import run_rules_record

        return run_rules_record(self.rules)

    def test_a_record_round_trips_back_to_the_same_rules(self):
        from hoya_market_agents.contract_validator import load_run_rules

        restored = load_run_rules({"debate_rules": self.record()})

        self.assertEqual(self.rules, restored)

    def test_the_digest_is_derived_only_from_the_rule_content(self):
        """同一份規則永遠得到同一個值——Ticket 07 的逐檔一致靠這一條。"""
        from dataclasses import replace

        first = self.record()
        second = self.record()
        other = replace(
            self.rules,
            vote_rounds=tuple(
                replace(vote_round, threshold=vote_round.threshold - 1)
                for vote_round in self.rules.vote_rounds
            ),
        )

        self.assertEqual(first, second)
        self.assertRegex(first["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["sha256"], self.record_of(other)["sha256"])

    def record_of(self, rules):
        from hoya_market_agents.contract_validator import run_rules_record

        return run_rules_record(rules)

    def test_a_manifest_without_the_field_reports_no_rules_rather_than_a_guess(self):
        from hoya_market_agents.contract_validator import load_run_rules

        for label, manifest in (
            ("absent", {"run_id": "x"}),
            ("explicit null", {"run_id": "x", "debate_rules": None}),
        ):
            with self.subTest(case=label):
                self.assertIsNone(load_run_rules(manifest))

    def test_editing_only_the_comments_of_a_rule_file_keeps_the_same_digest(self):
        """摘要認的是規則，不是檔案：設定頁改註解不該讓歷史 run 對不上。"""
        import json
        import tempfile
        from pathlib import Path

        from hoya_market_agents.contract_validator import run_rules_record
        from hoya_market_agents.debate_rules import RULES_PATH, load_debate_rules

        document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        document["_about"] = "改過的說明文字，規則一個字都沒動。"
        document["timeline"]["_about"] = "也改這一段。"
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        )
        try:
            # 鍵序也一併打亂，證明摘要與鍵序無關。
            json.dump(dict(sorted(document.items(), reverse=True)), handle)
            handle.close()
            commented = load_debate_rules(handle.name)
        finally:
            Path(handle.name).unlink(missing_ok=True)

        self.assertEqual(
            run_rules_record(self.rules)["sha256"],
            run_rules_record(commented)["sha256"],
        )

    def test_a_snapshot_from_an_unsupported_schema_version_is_refused_by_name(self):
        """快照帶著自己的 schema_version，載入器不支援時要指名版本，不猜。"""
        from hoya_market_agents.contract_validator import (
            _rules_document_digest,
            load_run_rules,
        )
        from hoya_market_agents.debate_rules import DebateRulesError

        record = self.record()
        record["document"]["schema_version"] = 3
        record["sha256"] = _rules_document_digest(record["document"])

        with self.assertRaises(DebateRulesError) as caught:
            load_run_rules({"debate_rules": record})
        self.assertIn("schema_version", str(caught.exception))

    def test_a_document_edited_without_its_digest_is_refused(self):
        from hoya_market_agents.contract_validator import load_run_rules

        record = self.record()
        record["document"]["timeline"]["vote_rounds"][0]["threshold"] = 6

        with self.assertRaises(ContractViolationError) as caught:
            load_run_rules({"debate_rules": record})
        self.assertIn("sha256", str(caught.exception))

    def test_a_digest_edited_without_its_document_is_refused(self):
        """反向：改摘要不改內容也一樣要擋，否則只擋得住一半的竄改。"""
        from hoya_market_agents.contract_validator import load_run_rules

        record = self.record()
        record["sha256"] = "0" * 64

        with self.assertRaises(ContractViolationError):
            load_run_rules({"debate_rules": record})

    def test_a_snapshot_that_is_not_a_legal_rule_document_is_refused(self):
        """快照走的是設定檔那一個載入器，所以非法階梯在這裡也非法。"""
        from hoya_market_agents.contract_validator import load_run_rules, run_rules_record
        from hoya_market_agents.debate_rules import DebateRulesError

        record = run_rules_record(self.rules)
        record["document"]["timeline"]["vote_rounds"][1]["threshold"] = 7
        record["sha256"] = self.digest(record["document"])

        with self.assertRaises(DebateRulesError):
            load_run_rules({"debate_rules": record})

    def digest(self, document):
        from hoya_market_agents.contract_validator import _rules_document_digest

        return _rules_document_digest(document)

    def test_a_record_missing_either_half_is_refused_by_name(self):
        """摘要與內容缺哪一個都不行，而且要指名缺的是哪一個。"""
        from hoya_market_agents.contract_validator import load_run_rules

        for name in ("sha256", "document"):
            with self.subTest(missing=name):
                record = self.record()
                record.pop(name)
                with self.assertRaises(ContractViolationError) as caught:
                    load_run_rules({"debate_rules": record})
                self.assertIn(name, str(caught.exception))

    def test_a_record_of_the_wrong_shape_is_refused(self):
        from hoya_market_agents.contract_validator import load_run_rules

        for value in ("a" * 64, [], 1):
            with self.subTest(record=value):
                with self.assertRaises(ContractViolationError):
                    load_run_rules({"debate_rules": value})

    def test_an_unknown_key_in_the_record_is_refused(self):
        from hoya_market_agents.contract_validator import load_run_rules

        record = self.record()
        record["note"] = "extra"

        with self.assertRaises(ContractViolationError) as caught:
            load_run_rules({"debate_rules": record})
        self.assertIn("note", str(caught.exception))

    def test_the_manifest_validator_accepts_a_manifest_that_records_its_rules(self):
        from hoya_market_agents.contract_validator import validate_run_manifest

        manifest = {**run_manifest(), "debate_rules": self.record()}

        self.assertIs(manifest, validate_run_manifest(manifest))

    def test_the_manifest_validator_refuses_a_broken_rules_record(self):
        from hoya_market_agents.contract_validator import validate_run_manifest

        record = self.record()
        record["sha256"] = "not-a-digest"

        with self.assertRaises(ContractViolationError) as caught:
            validate_run_manifest({**run_manifest(), "debate_rules": record})
        self.assertIn("debate_rules", str(caught.exception))

    def test_a_manifest_that_records_no_rules_is_still_a_valid_manifest(self):
        """舊 manifest 讀得下去是硬需求：欄位缺席不是格式錯誤。"""
        from hoya_market_agents.contract_validator import validate_run_manifest

        manifest = run_manifest()

        self.assertIs(manifest, validate_run_manifest(manifest))


class ManifestWritePointsTest(unittest.TestCase):
    """每一個寫 manifest 的地方都要記下該 run 的規則，漏掉一個要自己會紅。

    「有三個寫入點」這種靠 grep 數出來的清單沒有權威邊界：第四個寫入點加進來
    的那天，沒有任何東西會提醒作者也要蓋規則快照，而那條路徑產生的 run 從此驗
    不了規則相關的項目——而且是靜靜地驗不了。

    這裡的權威來源是**原始碼本身**：用 AST 掃整個 package，把
    ``write_json("manifest.json", ...)`` 的呼叫點全部找出來。清單不是手寫的，是
    推導出來的；手寫的只有「已知並且已經有行為測試在看著的那一組」，兩者不一致
    就紅。
    """

    #: 已知的寫入點，以及看著它們的行為測試。
    #: 新增第四個寫入點時，這張表和那條測試要一起補——這正是本測試要逼出來的動作。
    KNOWN_WRITE_POINTS = {
        ("hoya_market_agents.run_controller", "_write_manifest"),
        ("hoya_market_agents.debate_driver", "run_after_seal"),
        ("hoya_market_agents.competition_drill", "run_fake_competition_drill"),
    }

    def write_points(self):
        """Every ``write_json("manifest.json", ...)`` call site in the package."""
        import ast
        from pathlib import Path

        import hoya_market_agents

        package_root = Path(hoya_market_agents.__file__).parent
        found = set()
        for path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module = "hoya_market_agents." + ".".join(
                path.relative_to(package_root).with_suffix("").parts
            )
            for enclosing in ast.walk(tree):
                if not isinstance(
                    enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                for node in ast.walk(enclosing):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not isinstance(func, ast.Attribute) or func.attr != "write_json":
                        continue
                    first = node.args[0] if node.args else None
                    if isinstance(first, ast.Constant) and first.value == "manifest.json":
                        found.add((module, enclosing.name))
        return found

    def test_the_scan_finds_the_write_points_it_is_supposed_to_guard(self):
        """先證明掃描器抓得到東西，否則下面兩條都是空轉。"""
        self.assertTrue(self.write_points())

    def test_no_manifest_write_point_escapes_the_known_set(self):
        found = self.write_points()

        self.assertEqual(
            self.KNOWN_WRITE_POINTS,
            found,
            "manifest 寫入點的集合變了。新的寫入點必須用 "
            "contract_validator.run_rules_record(rules) 記下該 run 當時的規則，"
            "並補一條行為測試，然後把它加進 KNOWN_WRITE_POINTS；"
            "消失的寫入點請把它從表裡拿掉。",
        )

    def test_every_module_that_writes_a_manifest_records_the_rules(self):
        """靜態清單之外的一道推導檢查：寫 manifest 的模組必須引用那個記錄函式。

        這一條不看手寫清單——寫入點搬到新模組時它照樣成立。
        """
        from pathlib import Path

        import hoya_market_agents

        package_root = Path(hoya_market_agents.__file__).parent
        modules = {module for module, _ in self.write_points()}
        self.assertTrue(modules)
        for module in sorted(modules):
            relative = module.split(".", 1)[1].replace(".", "/") + ".py"
            source = (package_root / relative).read_text(encoding="utf-8")
            with self.subTest(module=module):
                # assertIn 會把整份原始碼印進失敗訊息，這裡只要「有沒有」。
                self.assertTrue(
                    "run_rules_record" in source,
                    "{} 寫 manifest 卻沒有引用 contract_validator.run_rules_record，"
                    "那條路徑產生的 run 事後驗不了規則相關的項目".format(module),
                )


def run_manifest(**overrides):
    manifest = {
        "schema_version": CONTRACT_VERSION,
        "run_id": "20260801T073000Z-btc-8f3a2c",
        "provider_mode": "fake",
        "question": "分析 BTC 過去 14 日市場狀態",
        "assets": ["BTC"],
        "period_days": 14,
        "started_at_utc": "2026-08-01T07:30:00Z",
        "completed_at_utc": "2026-08-01T07:43:00Z",
        "elapsed_ms": 780_000,
        "seats": [{"seat_id": "spot-technical", "attempt_ids": ["spot-technical-a1"]}],
        "artifacts": {
            "evidence.jsonl": {
                "path": "evidence.jsonl",
                "sha256": "a" * 64,
                "source": "validated seat attempts",
            }
        },
    }
    manifest.update(overrides)
    return manifest


if __name__ == "__main__":
    unittest.main()
