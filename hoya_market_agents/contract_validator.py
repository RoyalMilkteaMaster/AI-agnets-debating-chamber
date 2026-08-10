"""Explicit record contracts for evidence cards, debate turns and votes.

The Python standard library has no general JSON Schema validator, so the
approved contracts are enforced here as explicit field, type and enum checks.
Every violation in a record is reported at once, so a provider can repair its
output in a single pass instead of one field at a time.

The validator only checks objectively verifiable structure. It never inspects
or overrides a seat's market direction, and — since the approved-asset whitelist
was removed — it never judges which asset a record is about either. ``asset`` is
checked for shape only; keeping a card on this run's subject is the evidence
gateway's job, because only the gateway knows the question.
"""

import hashlib
import json
import re
import tempfile
from pathlib import Path

from .debate_rules import (
    _DOWNGRADE_FIELDS,
    SUPPORTED_SCHEMA_VERSION,
    load_debate_rules,
)
from .seats import SEAT_IDS

MAX_EVIDENCE_CARDS_PER_SEAT = 8
CONTRACT_VERSION = "1.0.0"

DIRECTIONS = ("support", "oppose", "neutral")
STANCES = ("bullish", "bearish", "neutral")
SOURCE_TIERS = (1, 2, 3)
QUESTION_TYPES = ("single_asset", "comparison", "market", "event")

# 該 run 執行當時的辯論規則，整份存進 manifest。欄位名字在這裡定義一次，寫入端
# 與 run_verifier 共用，免得兩邊各打一個字串。
RUN_RULES_FIELD = "debate_rules"
_RUN_RULES_KEYS = ("sha256", "document")

_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractViolationError(ValueError):
    """Raised when a record does not satisfy its approved contract."""

    def __init__(self, kind, problems):
        self.kind = kind
        self.problems = list(problems)
        super().__init__("{} 不符合 contract：{}".format(kind, "；".join(self.problems)))


def validate_evidence_card(card):
    """Validate one evidence card and return it unchanged."""
    problems = _version_problems(card) + _audit_problems(card)
    problems += _enum_problem(card, "direction", DIRECTIONS)
    problems += _enum_problem(card, "source_tier", SOURCE_TIERS)
    for field in (
        "asset",
        "evidence_id",
        "category",
        "statement",
        "source_url",
        "source_origin",
        "excerpt",
        "credibility_note",
    ):
        problems += _non_empty_string_problem(card, field)
    for field in ("published_at_utc", "retrieved_at_utc"):
        problems += _utc_timestamp_problem(card, field)
    if "phase" in card and card["phase"] != "research":
        problems.append("phase 必須為 research，實際為 {!r}".format(card["phase"]))
    return _result("evidence card", card, problems)


def validate_seat_evidence(seat_id, cards):
    """Validate a seat's whole evidence submission and return it unchanged."""
    problems = []
    if len(cards) > MAX_EVIDENCE_CARDS_PER_SEAT:
        problems.append(
            "席位 {} 提交 {} 張證據卡，超過上限 {}".format(
                seat_id, len(cards), MAX_EVIDENCE_CARDS_PER_SEAT
            )
        )
    seen = set()
    for card in cards:
        validate_evidence_card(card)
        if card.get("seat_id") != seat_id:
            problems.append(
                "證據卡 {} 的 seat_id 為 {!r}，不屬於席位 {}".format(
                    card.get("evidence_id"), card.get("seat_id"), seat_id
                )
            )
        evidence_id = card.get("evidence_id")
        if evidence_id in seen:
            problems.append("evidence_id {} 在同一席位重複".format(evidence_id))
        seen.add(evidence_id)
    return _result("seat evidence", cards, problems)


def validate_agent_position(record, known_evidence_ids=None):
    """Validate an agent position and its evidence references."""
    problems = _version_problems(record)
    problems += _position_problems(record, expected_phase="position")
    problems += _evidence_reference_problems(record, known_evidence_ids)
    return _result("agent position", record, problems)


def validate_debate_turn(turn, known_evidence_ids=None):
    """Validate one shared-transcript debate turn and return it unchanged."""
    problems = _version_problems(turn)
    problems += _position_problems(turn, expected_phase="debate")
    problems += _evidence_reference_problems(turn, known_evidence_ids)
    problems += _non_empty_string_problem(turn, "turn_id")
    if not isinstance(turn.get("responds_to"), list):
        problems.append("responds_to 必須為陣列")
    return _result("debate turn", turn, problems)


def validate_vote(record, known_evidence_ids=None):
    """Validate one seat vote and return it unchanged."""
    problems = _version_problems(record)
    problems += _position_problems(record, expected_phase="vote")
    problems += _evidence_reference_problems(record, known_evidence_ids)
    return _result("vote", record, problems)


def validate_question_package(package):
    """Validate the Core-created question passed to the seven seats."""
    problems = _version_problems(package)
    problems += _non_empty_string_problem(package, "run_id")
    problems += _non_empty_string_problem(package, "question")
    problems += _enum_problem(package, "question_type", QUESTION_TYPES)
    problems += _utc_timestamp_problem(package, "created_at_utc")
    problems += _elapsed_problem(package)
    if package.get("phase") != "question":
        problems.append("phase 必須為 question，實際為 {!r}".format(package.get("phase")))
    problems += _assets_problems(package)
    problems += _positive_integer_problem(package, "period_days")
    return _result("question package", package, problems)


def validate_run_manifest(manifest):
    """Validate one run manifest, its artifact hash index and its rule snapshot.

    ``debate_rules`` 是**選填**的：它是 Ticket 11 B2 才加進契約的欄位，比它早
    產生的 manifest 沒有這一欄，而那些 run 必須繼續讀得下去。缺席不是格式錯誤。

    填了的話，這裡只驗**結構**：鍵名、摘要格式、摘要與內容相符。裡面那份規則本
    身合不合法（階梯遞減、時間軸遞增、燈號階梯）**不在這一關**——那要真的把文件
    載入一次，是 :func:`load_run_rules` 的事。所以一份結構正確、內容卻是非法規
    則的 manifest 會在這裡通過、在 ``run_verifier`` 讀它的時候才被拒。
    """
    problems = _version_problems(manifest)
    for field in ("run_id", "provider_mode", "question"):
        problems += _non_empty_string_problem(manifest, field)
    for field in ("started_at_utc", "completed_at_utc"):
        problems += _utc_timestamp_problem(manifest, field)
    problems += _elapsed_problem(manifest)
    problems += _assets_problems(manifest)
    problems += _positive_integer_problem(manifest, "period_days")

    seats = manifest.get("seats")
    if not isinstance(seats, list):
        problems.append("seats 必須為陣列")
    else:
        for index, seat in enumerate(seats):
            if not isinstance(seat, dict):
                problems.append("seats[{}] 必須為物件".format(index))
                continue
            seat_id = seat.get("seat_id")
            if seat_id not in SEAT_IDS:
                problems.append("seats[{}].seat_id {!r} 不是七席之一".format(index, seat_id))
            attempts = seat.get("attempt_ids")
            if not isinstance(attempts, list) or any(
                not isinstance(item, str) or not item.strip() for item in attempts
            ):
                problems.append("seats[{}].attempt_ids 必須為字串陣列".format(index))

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        problems.append("artifacts 必須為物件")
    else:
        for name, entry in artifacts.items():
            prefix = "artifacts[{!r}]".format(name)
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                problems.append("{} 必須以非空檔名對應物件".format(prefix))
                continue
            if entry.get("path") != name:
                problems.append("{}.path 必須等於 artifact 名稱".format(prefix))
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                problems.append("{}.sha256 必須為 64 位小寫十六進位".format(prefix))
            if not isinstance(entry.get("source"), str) or not entry["source"].strip():
                problems.append("{}.source 必須為非空字串".format(prefix))
    if manifest.get(RUN_RULES_FIELD) is not None:
        problems += _run_rules_problems(manifest[RUN_RULES_FIELD])
    return _result("run manifest", manifest, problems)


# -- the rules one run actually ran under -----------------------------------
#
# 沒有這一欄的話，``run_verifier`` 只能讀「現在的」``config/debate_rules.json``，
# 於是使用者從設定頁改一次門檻，一場從頭到尾都正確的舊 run 就會被以「T+10 有效
# 票不足停止語意不一致」這種與它本身無關的理由判成失敗。
#
# 存的是**整份規則**而不是版本號或摘要：摘要要能驗，就得有辦法從摘要找回當時那
# 份規則，而那份規則已經被使用者覆寫掉了——「找不到就驗不了」只是把誤判換成拒
# 驗，沒有解決問題。整份存下來，run 目錄就仍然是自給自足的完整證據（架構 §4）。


def run_rules_record(rules):
    """Freeze one :class:`~.debate_rules.DebateRules` into a manifest field.

    回傳值只由**規則內容**決定：同一份規則永遠得到同一份 record 與同一個
    ``sha256``，沒有時間戳、沒有隨機值、沒有路徑。Ticket 07 的「同 token 重跑，
    run 內部逐檔一致」靠的就是這一條。

    ``document`` 是一份合法的 ``config/debate_rules.json`` 文件——不是另一種格
    式。所以讀回來時走的是同一個載入器（見 :func:`load_run_rules`），系統裡只有
    一份規則驗證邏輯。
    """
    document = _rules_document(rules)
    return {"sha256": _rules_document_digest(document), "document": document}


def load_run_rules(manifest):
    """Return the rules a run recorded, or ``None`` when it recorded none.

    ``None`` 的意思是「這個 run 沒有記錄規則」，**不是**「用預設規則」。呼叫端
    必須把它當成未知處理：拿現行設定去補，正是這個欄位要修掉的那個 bug。欄位缺
    席與欄位寫成 JSON ``null`` 都算沒有記錄，和 :func:`validate_run_manifest` 同
    一條判斷。

    欄位有值但壞掉時一律丟例外，不退回 ``None``——把「這份紀錄被動過」講成「沒
    有紀錄」會讓竄改看起來像舊格式。壞掉分兩種，兩種都會丟：結構不對是
    :class:`ContractViolationError`，結構對但規則非法是
    :class:`~.debate_rules.DebateRulesError`。
    """
    record = manifest.get(RUN_RULES_FIELD) if isinstance(manifest, dict) else None
    if record is None:
        return None
    _result("run rules snapshot", record, _run_rules_problems(record))
    return _load_rules_document(record["document"])


def _rules_document(rules):
    """Re-serialise validated rules into the config document that produced them.

    正規化是刻意的：註解鍵（``_about``）、鍵序與空白都不影響輸出，所以只改註解
    的設定檔會得到同一個 ``sha256``——摘要認的是規則，不是檔案。
    """
    confidence = rules.confidence
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "timeline_ms": {
            "debate_start": rules.debate_start_ms,
            "round_one_window": rules.round_one_window_ms,
            "reduced_threshold_from": rules.reduced_threshold_from_ms,
            "final_round_start": rules.final_round_start_ms,
            "final_round_end": rules.final_round_end_ms,
            "force_stop": rules.force_stop_ms,
        },
        "vote_thresholds": {
            "unanimous_blind_pass": rules.unanimous_blind_pass_votes,
            "initial": rules.initial_votes,
            "reduced": rules.reduced_votes,
            "forced_stop": rules.forced_stop_votes,
        },
        "confidence": {
            "light_scale": [
                {"min_votes": step.min_votes, "level": step.level}
                for step in confidence.light_scale
            ],
            # 每條降級規則只擁有自己的參數欄位，而那張對照表的權威是
            # ``debate_rules._DOWNGRADE_FIELDS``。在這裡抄一份等於多一個會各自
            # 漂移的來源；直接讀它，新增第三條降級時這裡不必跟著改。抄錯或漂移
            # 也不會靜靜過去——多寫或少寫一個欄位，讀回來時載入器就會拒絕。
            "downgrades": {
                rule.rule: {
                    "levels": rule.levels,
                    **{
                        name: _json_plain(getattr(rule, name))
                        for name in _DOWNGRADE_FIELDS[rule.rule]
                    },
                }
                for rule in confidence.downgrades
            },
        },
    }


def _json_plain(value):
    """Tuples are how the loader freezes arrays; JSON only has lists."""
    return list(value) if isinstance(value, tuple) else value


def _rules_document_digest(document):
    """A stable sha256 over the rule content, independent of key order."""
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_rules_document(document):
    """Validate one snapshot through the loader that owns rule validation.

    快照是一份設定文件，所以驗證它的權威就是 :func:`~.debate_rules.
    load_debate_rules`——本模組不重寫一遍階梯、時間軸與燈號的檢查。載入器只吃路
    徑，因此文件先落到一個暫存檔再交出去；用完即刪。

    這也是「規則結構日後改變時，舊快照怎麼讀」的答案：快照帶著自己的
    ``schema_version``，載入器支援到哪個版本，舊快照就讀到哪個版本；不支援時它
    會逐字指名版本並拒絕，不會拿新結構去猜舊資料。要延長舊快照的壽命，就在那個
    載入器裡加相容分支——只有一個地方要改。
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    )
    try:
        json.dump(document, handle, ensure_ascii=False)
        handle.close()
        return load_debate_rules(handle.name)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def _run_rules_problems(record):
    """Structural problems with the record; rule legality is the loader's job."""
    if not isinstance(record, dict):
        return ["{} 必須為物件".format(RUN_RULES_FIELD)]
    problems = []
    unknown = sorted(name for name in record if name not in _RUN_RULES_KEYS)
    if unknown:
        problems.append(
            "{} 含未知欄位：{}".format(RUN_RULES_FIELD, "、".join(unknown))
        )
    for name in _RUN_RULES_KEYS:
        if name not in record:
            problems.append("{} 缺少必要欄位：{}".format(RUN_RULES_FIELD, name))
    if problems:
        return problems
    digest = record["sha256"]
    document = record["document"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        problems.append("{}.sha256 必須為 64 位小寫十六進位".format(RUN_RULES_FIELD))
    if not isinstance(document, dict):
        problems.append("{}.document 必須為物件".format(RUN_RULES_FIELD))
    if problems:
        return problems
    if _rules_document_digest(document) != digest:
        problems.append(
            "{}.sha256 與 document 內容不符；規則快照已被改動".format(RUN_RULES_FIELD)
        )
    return problems


def validate_report(report):
    """Validate the shared report contract and all evidence references."""
    problems = _version_problems(report)
    for field in ("run_id", "question", "provider_mode"):
        problems += _non_empty_string_problem(report, field)
    for field in ("started_at_utc", "generated_at_utc"):
        problems += _utc_timestamp_problem(report, field)
    problems += _assets_problems(report)
    problems += _positive_integer_problem(report, "period_days")
    if not isinstance(report.get("confidence"), dict):
        problems.append("confidence 必須為物件")
    if not isinstance(report.get("conclusion"), dict):
        problems.append("conclusion 必須為物件")
    if not isinstance(report.get("tally"), dict):
        problems.append("tally 必須為物件")
    else:
        for stance, count in report["tally"].items():
            if stance not in STANCES:
                problems.append("tally 包含非法 stance {!r}".format(stance))
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                problems.append("tally[{!r}] 必須為 >= 0 的整數".format(stance))
    seat_count = report.get("seat_count")
    if not isinstance(seat_count, int) or isinstance(seat_count, bool) or seat_count < 0:
        problems.append("seat_count 必須為 >= 0 的整數")
    for field in ("scope_limits", "raw_records", "debate", "seats", "evidence"):
        if not isinstance(report.get(field), list):
            problems.append("{} 必須為陣列".format(field))

    evidence = report.get("evidence")
    known = set()
    if isinstance(evidence, list):
        for index, card in enumerate(evidence):
            evidence_id = card.get("evidence_id") if isinstance(card, dict) else None
            if not isinstance(evidence_id, str) or not evidence_id:
                problems.append("evidence[{}].evidence_id 必須為非空字串".format(index))
            elif evidence_id in known:
                problems.append("evidence_id {} 重複".format(evidence_id))
            else:
                known.add(evidence_id)
            if isinstance(card, dict):
                problems += [
                    "evidence[{}].{}".format(index, problem)
                    for problem in _non_empty_string_problem(card, "source_origin")
                ]

    for collection in ("seats", "debate"):
        records = report.get(collection)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                problems.append("{}[{}] 必須為物件".format(collection, index))
                continue
            if collection == "seats":
                for field in ("seat_id", "attempt_id", "public_reason"):
                    problems += [
                        "seats[{}].{}".format(index, problem)
                        for problem in _non_empty_string_problem(record, field)
                    ]
                problems += [
                    "seats[{}].{}".format(index, problem)
                    for problem in _enum_problem(record, "seat_id", SEAT_IDS)
                ]
                problems += [
                    "seats[{}].{}".format(index, problem)
                    for problem in _enum_problem(record, "stance", STANCES)
                ]
            evidence_ids = record.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                problems.append("{}[{}].evidence_ids 必須為陣列".format(collection, index))
                continue
            invalid_ids = [
                item for item in evidence_ids if not isinstance(item, str) or not item.strip()
            ]
            if invalid_ids:
                problems.append(
                    "{}[{}].evidence_ids 只能包含非空字串".format(collection, index)
                )
            unknown = [
                item for item in evidence_ids if isinstance(item, str) and item not in known
            ]
            if unknown:
                problems.append(
                    "{}[{}] 引用未知 evidence ID：{}".format(
                        collection, index, ", ".join(str(item) for item in unknown)
                    )
                )
    seats = report.get("seats")
    if isinstance(seats, list) and isinstance(seat_count, int) and not isinstance(
        seat_count, bool
    ):
        if seat_count != len(seats):
            problems.append(
                "seat_count {} 與 seats 數量 {} 不一致".format(seat_count, len(seats))
            )
    return _result("report", report, problems)


def _position_problems(record, expected_phase):
    problems = _audit_problems(record)
    problems += _enum_problem(record, "stance", STANCES)
    problems += _non_empty_string_problem(record, "public_reason")
    if record.get("phase") != expected_phase:
        problems.append(
            "phase 必須為 {}，實際為 {!r}".format(expected_phase, record.get("phase"))
        )
    round_number = record.get("round")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        problems.append("round 必須為 >= 1 的整數，實際為 {!r}".format(round_number))
    evidence_ids = record.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        problems.append("evidence_ids 必須為至少一個 evidence ID 的陣列")
    elif any(not isinstance(item, str) or not item.strip() for item in evidence_ids):
        problems.append("evidence_ids 只能包含非空字串")
    change_reason = record.get("stance_change_reason", None)
    if change_reason is not None and not isinstance(change_reason, str):
        problems.append("stance_change_reason 必須為字串或 null")
    return problems


def _version_problems(record):
    return _enum_problem(record, "schema_version", (CONTRACT_VERSION,))


def _evidence_reference_problems(record, known_evidence_ids):
    if known_evidence_ids is None:
        return []
    evidence_ids = record.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    known = set(known_evidence_ids)
    unknown = [item for item in evidence_ids if isinstance(item, str) and item not in known]
    if not unknown:
        return []
    return ["引用未知 evidence ID：{}".format(", ".join(str(item) for item in unknown))]


def _assets_problems(record):
    """Check the shape of the asset list, never its membership.

    An open proposition legitimately names no tradable target, so an empty list
    is a valid reading of a question — but a blank entry never is.
    """
    assets = record.get("assets")
    if not isinstance(assets, list):
        return ["assets 必須為標的陣列"]
    invalid = [asset for asset in assets if not isinstance(asset, str) or not asset.strip()]
    if invalid:
        return ["assets 只能包含非空字串標的：{}".format(", ".join(repr(item) for item in invalid))]
    return []


def _positive_integer_problem(record, field):
    value = record.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return ["{} 必須為 >= 1 的整數，實際為 {!r}".format(field, value)]
    return []


def _elapsed_problem(record):
    elapsed = record.get("elapsed_ms")
    if "elapsed_ms" not in record:
        return ["缺少必要欄位 elapsed_ms"]
    if not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
        return ["elapsed_ms 必須為 >= 0 的整數，實際為 {!r}".format(elapsed)]
    return []


def _audit_problems(record):
    """Check the audit fields every formal record must carry."""
    problems = []
    for field in ("run_id", "seat_id", "attempt_id", "phase"):
        problems += _non_empty_string_problem(record, field)
    if "seat_id" in record and record.get("seat_id") not in SEAT_IDS:
        problems.append("seat_id {!r} 不是七席之一".format(record.get("seat_id")))
    problems += _utc_timestamp_problem(record, "created_at_utc")
    problems += _elapsed_problem(record)
    return problems


def _non_empty_string_problem(record, field):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        return ["{} 必須為非空字串，實際為 {!r}".format(field, value)]
    return []


def _enum_problem(record, field, allowed):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    value = record[field]
    if not any(type(value) is type(item) and value == item for item in allowed):
        return [
            "{} 必須為 {} 之一，實際為 {!r}".format(
                field, "/".join(str(item) for item in allowed), record[field]
            )
        ]
    return []


def _utc_timestamp_problem(record, field):
    if field not in record:
        return ["缺少必要欄位 {}".format(field)]
    value = record[field]
    if not isinstance(value, str) or not _UTC_TIMESTAMP.match(value):
        return ["{} 必須為 UTC ISO-8601 並以 Z 結尾，實際為 {!r}".format(field, value)]
    return []


def _result(kind, value, problems):
    if problems:
        raise ContractViolationError(kind, problems)
    return value
