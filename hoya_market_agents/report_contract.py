"""Validation for a Core-authored market report.

The functions in this module verify structure, lineage, policy and confidence
ceilings.  They deliberately do not create or choose market judgements.
"""

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .contract_validator import CONTRACT_VERSION
from .seats import SEAT_IDS

CONFIDENCE_LEVELS = ("red", "orange", "yellow", "yellow_green", "green")
CONFIDENCE_ICONS = {
    "red": "🔴",
    "orange": "🟠",
    "yellow": "🟡",
    "yellow_green": "🟡🟢",
    "green": "🟢",
}

_DIRECTIONLESS_STATUSES = {
    "no_consensus",
    "failed_insufficient_valid_votes",
    "insufficient_data",
    "validation_failed",
}
_PROHIBITED = (
    re.compile(r"目標價|價格目標|price\s*target", re.IGNORECASE),
    re.compile(r"保證(?:上漲|下跌|漲|跌)|一定(?:上漲|下跌|漲|跌)|不會回檔|guaranteed", re.IGNORECASE),
    re.compile(r"槓桿|leverage", re.IGNORECASE),
    re.compile(r"倉位|部位.{0,16}\d+\s*%|position\s*siz", re.IGNORECASE),
    re.compile(r"(?:請|建議|現在)?\s*(?:買進|買入|賣出|做多|做空)|\b(?:buy|sell)\s+(?:now|btc|eth|sol|bnb|xrp)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:open|enter)\s+(?:a\s+|the\s+)?(?:btc\s+|eth\s+|sol\s+|bnb\s+|xrp\s+)?"
        r"(?:long|short)(?:\s+(?:position|order))?(?:\s+now)?\b|"
        r"\btake\s+(?:a\s+|the\s+)(?:btc\s+|eth\s+|sol\s+|bnb\s+|xrp\s+)?"
        r"(?:long|short)\s+(?:position|order)(?:\s+now)?\b|"
        r"\bgo\s+(?:long|short)(?:\s+(?:btc|eth|sol|bnb|xrp))?(?:\s+now)?\b",
        re.IGNORECASE,
    ),
)
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ReportContractError(ValueError):
    """One or more objective report checks failed."""

    def __init__(self, problems):
        self.problems = list(dict.fromkeys(problems))
        super().__init__("report contract failed: {}".format("; ".join(self.problems)))


def validate_market_report(report, sources):
    """Return the Core report unchanged when every objective check passes."""
    problems = []
    if not isinstance(report, dict):
        raise ReportContractError(["report 必須為物件"])
    if not isinstance(sources, dict):
        raise ReportContractError(["sources 必須為物件"])

    for field in (
        "run_id",
        "generated_at_utc",
        "market_status",
        "judgement",
        "consensus_status",
    ):
        problems += _string_problems(report, field)
    if report.get("schema_version") != CONTRACT_VERSION:
        problems.append("schema_version 必須為 {}".format(CONTRACT_VERSION))
    if not _is_utc(report.get("generated_at_utc")):
        problems.append("generated_at_utc 必須為 UTC ISO-8601")

    period = report.get("period")
    if not isinstance(period, dict):
        problems.append("period 必須為物件")
    else:
        for field in ("label", "start_utc", "end_utc"):
            problems += _string_problems(period, field, "period.")
        for field in ("start_utc", "end_utc"):
            if not _is_utc(period.get(field)):
                problems.append("period.{} 必須為 UTC ISO-8601".format(field))

    for field in ("limitations", "invalidation_conditions", "validation_errors"):
        problems += _string_list_problems(report, field)
    if not report.get("limitations"):
        problems.append("limitations 不得為空")
    if not report.get("invalidation_conditions"):
        problems.append("invalidation_conditions 不得為空")
    if type(report.get("direction_bearing")) is not bool:
        problems.append("direction_bearing 必須為布林值")
    if type(report.get("process_failure")) is not bool:
        problems.append("process_failure 必須為布林值")

    votes = _votes_source(sources, problems)
    official_run_id = votes.get("record", {}).get("run_id")
    evidence_by_id = _evidence_index(sources, problems, official_run_id)
    if report.get("run_id") != official_run_id:
        problems.append("report.run_id 與 official votes.run_id 不一致")
    _validate_report_evidence(report, evidence_by_id, problems)
    _validate_vote_cross_references(report, votes, evidence_by_id, problems)
    _validate_debate_cross_references(sources, votes, evidence_by_id, problems)
    _validate_direction(report, problems)
    _validate_confidence(report, sources, problems)
    _validate_prohibited_content(report, problems)

    if problems:
        raise ReportContractError(problems)
    return report


def confidence_cap(report, sources):
    """Compute an upper bound from votes and evidence; never choose a level."""
    votes = sources.get("votes", {}) if isinstance(sources, dict) else {}
    rows = votes.get("votes", []) if isinstance(votes, dict) else []
    valid_rows = [row for row in rows if isinstance(row, dict) and row.get("state") == "valid"]
    valid_count = len(valid_rows)
    status = report.get("consensus_status") if isinstance(report, dict) else None
    adopted = report.get("adopted_stance") if isinstance(report, dict) else None
    adopted_rows = [row for row in valid_rows if row.get("final_stance") == adopted]
    effective_leader = len(adopted_rows)

    if report.get("process_failure") or not isinstance(valid_count, int) or valid_count < 4:
        return "red"
    if status in ("failed_insufficient_valid_votes", "insufficient_data", "validation_failed"):
        return "red"
    if status != "consensus" or report.get("adopted_stance") is None:
        return "orange"
    if effective_leader < 4:
        return "red"

    if effective_leader == 4:
        cap = "orange"
    elif effective_leader == 5:
        cap = "yellow"
    elif effective_leader == 6:
        cap = "yellow_green"
    else:
        cap = "green"

    all_cards = sources.get("evidence", []) if isinstance(sources, dict) else []
    cited_ids = {
        evidence_id
        for row in adopted_rows
        for evidence_id in row.get("final_evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    cards = [
        card
        for card in all_cards
        if isinstance(card, dict) and card.get("evidence_id") in cited_ids
    ]
    origins = {card.get("source_origin") for card in cards if isinstance(card, dict)}
    origins.discard(None)
    categories = {card.get("category") for card in cards if isinstance(card, dict)}
    categories.discard(None)
    reliable_categories = {
        card.get("category")
        for card in cards
        if isinstance(card, dict) and card.get("source_tier") in (1, 2)
    }
    reliable_categories.discard(None)

    if len(origins) < 2:
        cap = _lower(cap, "orange")
    if effective_leader >= 7 and len(categories) < 4:
        cap = _lower(cap, "yellow_green")
    elif effective_leader >= 6 and len(reliable_categories) < 3:
        cap = _lower(cap, "yellow")
    elif effective_leader >= 5 and len(categories) < 2:
        cap = _lower(cap, "orange")

    generated = _parse_utc(report.get("generated_at_utc"))
    stale = any(_is_stale(card, generated) for card in cards if isinstance(card, dict))
    low_quality = any(card.get("source_tier") not in (1, 2) for card in cards if isinstance(card, dict))
    fatal = any(bool(card.get("fatal_counterevidence")) for card in cards if isinstance(card, dict))
    contradictory = bool(sources.get("material_contradiction"))
    if stale or low_quality or fatal or contradictory:
        cap = CONFIDENCE_LEVELS[max(0, CONFIDENCE_LEVELS.index(cap) - 1)]
    return cap


def canonical_sha256(value):
    """Stable hash helper used for report lineage."""
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_index(sources, problems, official_run_id):
    cards = sources.get("evidence")
    if not isinstance(cards, list):
        problems.append("sources.evidence 必須為陣列")
        return {}
    result = {}
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            problems.append("sources.evidence[{}] 必須為物件".format(index))
            continue
        evidence_id = card.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            problems.append("sources.evidence[{}] 缺少 evidence_id".format(index))
            continue
        if evidence_id in result:
            problems.append("sources evidence ID 重複：{}".format(evidence_id))
        result[evidence_id] = card
        if card.get("run_id") != official_run_id:
            problems.append("evidence {} 的 run_id 與 official run 不一致".format(evidence_id))
        for field in ("source_url", "source_origin", "category", "published_at_utc"):
            if not isinstance(card.get(field), str) or not card[field].strip():
                problems.append("evidence {} 缺少 {}".format(evidence_id, field))
        if not is_safe_source_url(card.get("source_url")):
            problems.append("evidence {} source_url 必須為 http/https".format(evidence_id))
    return result


def _votes_source(sources, problems):
    votes = sources.get("votes")
    if not isinstance(votes, dict):
        problems.append("sources.votes 必須為物件")
        return {}
    if not isinstance(votes.get("run_id"), str) or not votes["run_id"].strip():
        problems.append("official votes.run_id 不得為空")
    rows = votes.get("votes")
    if not isinstance(rows, list) or len(rows) != len(SEAT_IDS):
        problems.append("official votes 必須完整保留七席")
        rows = rows if isinstance(rows, list) else []
    by_seat = {}
    recomputed = {key: 0 for key in votes.get("tally", {})} if isinstance(votes.get("tally"), dict) else {}
    valid_count = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("seat_id") not in SEAT_IDS:
            problems.append("official vote 含未知席位")
            continue
        seat_id = row["seat_id"]
        if seat_id in by_seat:
            problems.append("official vote 席位重複：{}".format(seat_id))
        by_seat[seat_id] = row
        if row.get("state") == "valid":
            valid_count += 1
            stance = row.get("final_stance")
            if stance not in recomputed:
                problems.append("official vote 立場不在 tally：{}".format(stance))
            else:
                recomputed[stance] += 1
    if set(by_seat) != set(SEAT_IDS):
        problems.append("official votes 缺少固定席位")
    if votes.get("valid_vote_count") != valid_count:
        problems.append("valid_vote_count 與 official votes 不一致")
    if votes.get("tally") != recomputed:
        problems.append("official tally 與逐席票數不一致")
    return {"record": votes, "by_seat": by_seat}


def _validate_report_evidence(report, evidence_by_id, problems):
    cards = report.get("evidence")
    if not isinstance(cards, list):
        problems.append("report.evidence 必須為陣列")
        return
    seen = set()
    for card in cards:
        if not isinstance(card, dict):
            problems.append("report evidence 必須為物件")
            continue
        evidence_id = card.get("evidence_id")
        source = evidence_by_id.get(evidence_id)
        if source is None:
            problems.append("report 引用未知 evidence ID：{}".format(evidence_id))
            continue
        if evidence_id in seen:
            problems.append("report evidence ID 重複：{}".format(evidence_id))
        seen.add(evidence_id)
        if card.get("url") != source.get("source_url"):
            problems.append("evidence {} URL 與正式快照不一致".format(evidence_id))
        if card.get("statement") != source.get("statement"):
            problems.append("evidence {} statement 與正式快照不一致".format(evidence_id))
        if card.get("direction") != source.get("direction"):
            problems.append("evidence {} direction 與正式快照不一致".format(evidence_id))
    if seen != set(evidence_by_id):
        problems.append("report.evidence 必須完整對應正式 evidence snapshot")


def _validate_vote_cross_references(report, votes, evidence_by_id, problems):
    vote_record = votes.get("record", {})
    by_seat = votes.get("by_seat", {})
    if report.get("tally") != vote_record.get("tally"):
        problems.append("report tally 與 official votes 不一致")
    audit_failure = report.get("process_failure") and report.get("consensus_status") == "validation_failed"
    if not audit_failure and report.get("consensus_status") != vote_record.get("consensus_status"):
        problems.append("report consensus_status 與 official votes 不一致")
    if not audit_failure and report.get("adopted_stance") != vote_record.get("adopted_stance"):
        problems.append("report adopted_stance 與 official votes 不一致")

    rows = report.get("seats")
    if not isinstance(rows, list) or len(rows) != len(SEAT_IDS):
        problems.append("report 必須完整保留七席")
        return
    if [row.get("seat_id") for row in rows if isinstance(row, dict)] != list(SEAT_IDS):
        problems.append("report 七席順序或 identity 不正確")
    for row in rows:
        if not isinstance(row, dict):
            problems.append("report seat row 必須為物件")
            continue
        seat_id = row.get("seat_id")
        vote = by_seat.get(seat_id)
        if vote is None:
            problems.append("report seat 無 official vote：{}".format(seat_id))
            continue
        mappings = (
            ("initial_stance", "initial_stance"),
            ("final_stance", "final_stance"),
            ("stance_changed", "stance_changed"),
            ("initial_public_reason", "initial_public_reason"),
            ("public_reason", "final_public_reason"),
            ("stance_change_reason", "stance_change_reason"),
        )
        for report_field, vote_field in mappings:
            expected = vote.get(vote_field)
            if report_field == "initial_public_reason" and expected is None:
                expected = "未取得初始票。"
            if row.get(report_field) != expected:
                problems.append("seat {} 的 {} 與 official vote 不一致".format(seat_id, report_field))
        attempts = vote.get("attempt_ids", [])
        if row.get("replacement_attempt_ids") != attempts[1:]:
            problems.append("seat {} replacement lineage 不一致".format(seat_id))
        support = row.get("support_evidence_ids")
        counter = row.get("counter_evidence_ids")
        if not isinstance(support, list) or not isinstance(counter, list):
            problems.append("seat {} 支持/反方 evidence IDs 必須為陣列".format(seat_id))
            continue
        cited = support + counter
        unknown = [evidence_id for evidence_id in cited if evidence_id not in evidence_by_id]
        if unknown:
            problems.append("seat {} 引用未知 evidence ID：{}".format(seat_id, ", ".join(unknown)))
        official = set(vote.get("final_evidence_ids", []))
        if set(cited) != official:
            problems.append("seat {} evidence IDs 與 official vote 不一致".format(seat_id))
        if row.get("stance_changed"):
            if not isinstance(row.get("stance_change_reason"), str) or not row["stance_change_reason"].strip():
                problems.append("seat {} 改票但缺少原因".format(seat_id))
        elif row.get("no_change_reason") != vote.get("final_public_reason"):
            problems.append("seat {} 未改票原因與 official public reason 不一致".format(seat_id))


def _validate_debate_cross_references(sources, votes, evidence_by_id, problems):
    debate = sources.get("debate")
    if not isinstance(debate, list):
        problems.append("sources.debate 必須為陣列")
        return
    by_seat = votes.get("by_seat", {})
    official_run_id = votes.get("record", {}).get("run_id")
    for entry in debate:
        if not isinstance(entry, dict):
            problems.append("debate entry 必須為物件")
            continue
        if entry.get("run_id") != official_run_id:
            problems.append("debate entry 的 run_id 與 official run 不一致")
        seat_id = entry.get("seat_id")
        vote = by_seat.get(seat_id)
        if vote is None:
            problems.append("debate 引用未知 seat：{}".format(seat_id))
            continue
        if entry.get("attempt_id") not in vote.get("attempt_ids", []):
            problems.append("debate attempt 無法回查：{}".format(entry.get("attempt_id")))
        unknown = [item for item in entry.get("evidence_ids", []) if item not in evidence_by_id]
        if unknown:
            problems.append("debate 引用未知 evidence ID：{}".format(", ".join(unknown)))


def _validate_direction(report, problems):
    status = report.get("consensus_status")
    adopted = report.get("adopted_stance")
    direction_bearing = report.get("direction_bearing")
    if status in _DIRECTIONLESS_STATUSES:
        if adopted is not None or direction_bearing is not False:
            problems.append("未達共識/資料不足/流程失敗不得形成方向判斷")
    elif status == "consensus":
        if adopted is None or direction_bearing is not True:
            problems.append("consensus report 必須如實標示 adopted_stance")


def _validate_confidence(report, sources, problems):
    confidence = report.get("confidence")
    if not isinstance(confidence, dict):
        problems.append("confidence 必須為物件")
        return
    level = confidence.get("level")
    if level not in CONFIDENCE_LEVELS:
        problems.append("confidence.level 不在核准燈號")
        return
    if confidence.get("icon") != CONFIDENCE_ICONS[level]:
        problems.append("confidence icon 與 level 不一致")
    if not isinstance(confidence.get("text"), str) or not confidence["text"].strip():
        problems.append("confidence.text 不得為空")
    cap = confidence_cap(report, sources)
    if CONFIDENCE_LEVELS.index(level) > CONFIDENCE_LEVELS.index(cap):
        problems.append("信心 {} 高於資料上限 {}".format(level, cap))


def _validate_prohibited_content(report, problems):
    text = "\n".join(_all_strings(report))
    if any(pattern.search(text) for pattern in _PROHIBITED):
        problems.append("禁止價格目標、保證、槓桿、倉位或直接交易指令")


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def _string_problems(record, field, prefix=""):
    value = record.get(field) if isinstance(record, dict) else None
    return [] if isinstance(value, str) and value.strip() else ["{}{} 不得為空".format(prefix, field)]


def _string_list_problems(record, field):
    value = record.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return ["{} 必須為字串陣列".format(field)]
    return []


def _is_utc(value):
    return isinstance(value, str) and _UTC.fullmatch(value) is not None


def _parse_utc(value):
    if not _is_utc(value):
        return None
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)


def _is_stale(card, generated):
    published = _parse_utc(card.get("published_at_utc"))
    return generated is not None and (published is None or (generated - published).days > 30)


def _lower(left, right):
    return CONFIDENCE_LEVELS[min(CONFIDENCE_LEVELS.index(left), CONFIDENCE_LEVELS.index(right))]


def is_safe_source_url(value):
    """Only absolute HTTP(S) evidence links may become active links."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)
