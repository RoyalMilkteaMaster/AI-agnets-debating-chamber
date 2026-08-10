"""Validation for a Core-authored market report.

The functions in this module verify structure, lineage, policy and confidence
ceilings.  They deliberately do not create or choose market judgements.
"""

import json
import re
from urllib.parse import urlsplit

from .contract_validator import CONTRACT_VERSION
from .debate_rules import DebateRulesError, debate_rules
from .seats import SEAT_IDS

# ADR 0003：燈號＝共識強度，由壞到好。這個順序就是「降一級」的方向，也是
# ``_validate_confidence`` 判斷「不得高於上限」用的排序。這裡擁有的是**詞彙**
# ——有哪些燈、誰比誰好、配哪個圖示；「幾票對哪一級」則由
# ``config/debate_rules.json`` 擁有，見 :func:`confidence_scale`。
CONFIDENCE_LEVELS = ("red", "orange", "yellow", "green", "blue")
CONFIDENCE_ICONS = {
    "red": "🔴",
    "orange": "🟠",
    "yellow": "🟡",
    "green": "🟢",
    "blue": "🔵",
}
_WORST_LEVEL = CONFIDENCE_LEVELS[0]


class _RulesNotRecorded:
    """The third value of ``rules``: this run did not record the rules it used.

    ``rules`` 本來只有兩個值：一份 :class:`~.debate_rules.ConfidenceRules`，或
    ``None``＝「省略，現讀」。少了「不知道」這一個值，於是
    ``run_verifier`` 驗一份沒有記錄規則的舊 run 時只剩兩個爛選項：拿現行設定去
    算（把「我不知道」變成一個有自信的失敗宣稱），或整段報告契約都不驗（連證據
    回查、票數交叉比對這些與規則無關的檢查一起丟掉）。

    這個哨兵讓第三個答案講得出來，而且範圍很窄：**只有「燈號不得高於上限」那一
    項**被略過，因為只有它是規則的函數。級別、圖示、說明文字與報告契約的其餘部
    分照常驗。

    是獨立型別而不是 ``None`` 或空的 ``ConfidenceRules``：``None`` 已經是「現
    讀」，空的階梯會被 :func:`_checked_scale` 判成缺燈號而拒絕——兩者都無法表達
    「不知道」，而且都會被誤讀成別的意思。
    """

    __slots__ = ()

    def __repr__(self):
        return "RULES_NOT_RECORDED"


#: 見 :class:`_RulesNotRecorded`。以身分（``is``）比對，不比值。
RULES_NOT_RECORDED = _RulesNotRecorded()

_DIRECTIONLESS_STATUSES = {
    "no_consensus",
    "failed_insufficient_valid_votes",
    "insufficient_data",
    "validation_failed",
}
# 2026-08-02 使用者決策：報告不做內容審查（原禁詞正則會把「賣壓」「槓桿率」等
# 描述性語言誤殺成投資建議）。品管由七席同儕辯論與客觀交叉驗證承擔；本模組
# 只驗可客觀比對的事實（evidence ID、票數、方向、信心上限、時間格式）。
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ReportContractError(ValueError):
    """One or more objective report checks failed."""

    def __init__(self, problems):
        self.problems = list(dict.fromkeys(problems))
        super().__init__("report contract failed: {}".format("; ".join(self.problems)))


def validate_market_report(report, sources, rules=None):
    """Return the Core report unchanged when every objective check passes.

    ``rules`` 是可選的 :class:`~.debate_rules.ConfidenceRules` 快照。本模組裡
    ``rules`` 一律指這個型別（見 :func:`confidence_scale`）——呼叫端若已經持有一
    份規則快照，傳 ``snapshot.confidence`` 進來，整段驗證就與呼叫端的其他檢查用
    同一份設定。省略時現讀，行為與過去完全相同。

    第三個值是 :data:`RULES_NOT_RECORDED`＝「這個 run 沒有記錄它跑的規則」。它
    **只**略過「燈號不得高於上限」那一項，其餘檢查一項不減——包含燈號自己的級
    別、圖示與說明文字。
    """
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
    _validate_confidence(report, sources, problems, rules)

    if problems:
        raise ReportContractError(problems)
    return report


def confidence_scale(rules=None):
    """Return the vote→light ladder, refusing one that speaks another vocabulary.

    設定檔擁有「幾票對哪一級」，本模組擁有「有哪些級、誰比誰好」。載入器
    （Ticket 02）刻意只把 ``level`` 當成不重複的非空字串，好讓自己維持只依賴
    stdlib 與 ``seats`` 的葉節點；認得燈號的是這裡，所以缺口也在這裡關掉：
    階梯的級別序列必須逐字等於 ``CONFIDENCE_LEVELS`` 的反序（由好到壞）。

    要求「同一組級別」是因為每一級都必須有票數能走到，否則 ``CONFIDENCE_ICONS``
    與 Core 輸出 schema 會宣告一個系統永遠產不出來的燈；要求「同一個順序」是
    因為 ``confidence_cap`` 沿階梯降級、``_validate_confidence`` 沿
    ``CONFIDENCE_LEVELS`` 比較上限——兩條排序不一致時，「不得高於上限」就沒有
    唯一解釋。

    傳入 ``rules`` 可直接檢查一份 :class:`~.debate_rules.ConfidenceRules`（測試
    接縫）；省略時檢查**現行**規則。

    這裡刻意不存快取。階梯是規則算出來的衍生值：另存一份的話，
    :func:`~.debate_rules.reload_debate_rules` 換掉規則之後就會出現「規則是新
    的、階梯是舊的」混合狀態，而且沒有任何地方會報錯，報告會沿著一條沒有人選
    過的階梯評燈。每次都從現行規則算，那個狀態就無法表達——不必靠呼叫者記得
    再清第二份快取，那正是下一個人一定會忘記的東西。

    成本：每次 :func:`confidence_cap` 跑一次；一個正常成功的 run 通常是兩次
    （Core 撰稿時的上限、事後契約驗證各一），走修正流程會更多。單次約 0.5 µs，
    總成本仍可忽略。
    """
    return _checked_scale(debate_rules().confidence if rules is None else rules)


def _checked_scale(confidence):
    # 兩個公開入口（confidence_scale、confidence_cap）都會流經這裡，所以哨兵只
    # 要在這一個地方擋。「這一份規則的階梯長怎樣」在規則未知時沒有答案，回一個
    # 猜的比丟例外糟得多——唯一該略過上限的地方是 _validate_confidence，而它是
    # 靠自己的分支略過，不是靠這裡回傳一個假階梯。
    if confidence is RULES_NOT_RECORDED:
        raise DebateRulesError(
            "規則未知時算不出燈號階梯與上限：這個 run 沒有記錄它跑的規則。"
        )
    expected = tuple(reversed(CONFIDENCE_LEVELS))
    levels = tuple(step.level for step in confidence.light_scale)
    unknown = [level for level in levels if level not in CONFIDENCE_LEVELS]
    if unknown:
        raise DebateRulesError(
            "confidence.light_scale 含未核准燈號：{}；核准燈號為 {}。".format(
                "、".join(unknown), "、".join(expected)
            )
        )
    missing = [level for level in expected if level not in levels]
    if missing:
        raise DebateRulesError(
            "confidence.light_scale 缺少燈號：{}；每一級都必須有票數對應得到"
            "它。".format("、".join(missing))
        )
    if levels != expected:
        raise DebateRulesError(
            "confidence.light_scale 的燈號順序必須由好到壞：{}；收到 {}。".format(
                "、".join(expected), "、".join(levels)
            )
        )
    return confidence.light_scale


def confidence_cap(report, sources, rules=None):
    """Compute an upper bound from votes and evidence; never choose a level.

    ADR 0003：上限＝最終採納立場的有效票數直接映射，再套用設定檔裡的來源降級。
    證據的廣度與新鮮度不再影響燈號——那由七席同儕辯論把關。

    整段計算只讀一次規則權威：階梯與降級規則都取自同一份 ``confidence`` 快照。
    分兩次讀的話，中間若有人 :func:`~.debate_rules.reload_debate_rules`，就會拿
    新規則的降級去修舊階梯算出來的上限——一個兩份設定都沒有描述過的結果。

    ``rules`` 讓呼叫端把自己那一份 :class:`~.debate_rules.ConfidenceRules` 傳進
    來，好讓「一次操作一份快照」延伸到跨模組的操作（``run_verifier.verify_run``
    就是這樣把同一份快照同時用在時間線、停止語意與這裡）。省略時現讀。
    """
    confidence = debate_rules().confidence if rules is None else rules
    scale = confidence_scale(confidence)
    votes = sources.get("votes", {}) if isinstance(sources, dict) else {}
    rows = votes.get("votes", []) if isinstance(votes, dict) else []
    valid_rows = [row for row in rows if isinstance(row, dict) and row.get("state") == "valid"]
    status = report.get("consensus_status") if isinstance(report, dict) else None
    adopted = report.get("adopted_stance") if isinstance(report, dict) else None
    adopted_rows = [row for row in valid_rows if row.get("final_stance") == adopted]

    # 紅燈語意不變：流程失敗，或票數不足。後者不需要自己的分支——採納票是有效
    # 票的子集，而 `_light_for` 對票數單調不減，所以
    #     light(有效票) == 最底一級  ⟹  light(採納票) == 最底一級
    # 下面那一次查表已經涵蓋。成立條件只要階梯通得過載入器（min_votes 嚴格遞
    # 減、末級為 0），與門檻設成多少無關；反向不成立，也不需要成立。
    if report.get("process_failure"):
        return _WORST_LEVEL
    if status in ("failed_insufficient_valid_votes", "insufficient_data", "validation_failed"):
        return _WORST_LEVEL
    # ADR 0003 決策 1：燈號＝**最終採納立場**的有效票數。沒有採納立場就沒有票
    # 數可數，可數的採納票數是 0，於是落在階梯最底一級。改用最大落敗集團的票
    # 數頂替，等於替一個議場明確沒有採納的立場報告共識強度——ADR 的主詞寫得很
    # 死，這是選這一版的理由本身。
    #
    # 注意**不要**把它讀成「兩種算法等價」：只有在出貨的 forced_stop=4 階梯下，
    # 合法的未達共識停止（run_verifier 要求領先票 < forced_stop）領先票必 ≤3，
    # 兩者才同樣落在紅。把門檻設成 forced_stop=5 就有實跑得出來的反例——4/3/0
    # 未達共識，本版仍是 red，最大集團版會是 orange。
    if status != "consensus" or adopted is None:
        adopted_rows = []

    cap = _light_for(len(adopted_rows), scale)
    cards = _cited_cards(sources, adopted_rows)
    for rule in confidence.downgrades:
        if _DOWNGRADE_CONDITIONS[rule.rule](rule, _cards_judged_by(rule, cards)):
            cap = _worse(cap, rule.levels)
    return cap


def _light_for(votes, scale):
    """Read one vote count off the ladder, which runs best rung first."""
    for step in scale:
        if votes >= step.min_votes:
            return step.level
    return _WORST_LEVEL


def _worse(level, levels):
    """Move ``levels`` rungs toward the worse end, clamped at the worst light."""
    return CONFIDENCE_LEVELS[max(0, CONFIDENCE_LEVELS.index(level) - levels)]


def _cited_cards(sources, adopted_rows):
    """The evidence cards the adopted stance actually cited."""
    all_cards = sources.get("evidence", []) if isinstance(sources, dict) else []
    cited_ids = {
        evidence_id
        for row in adopted_rows
        for evidence_id in row.get("final_evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    return [
        card
        for card in all_cards
        if isinstance(card, dict) and card.get("evidence_id") in cited_ids
    ]


def _cards_judged_by(rule, cards):
    """Drop the cards contributed by seats this rule exempts."""
    return [card for card in cards if card.get("seat_id") not in rule.exempt_seat_ids]


def _too_few_independent_domains(rule, cards):
    origins = {card.get("source_origin") for card in cards}
    origins.discard(None)
    return len(origins) < rule.min_independent_domains


def _cites_an_untrusted_source(rule, cards):
    # ``type(...) is int`` 而非 ``in``：True == 1 且 1.0 == 1，只比值的話一張
    # 沒有等級的卡片可以靠 ``true`` 冒充 tier 1。
    return any(
        type(card.get("source_tier")) is not int
        or card["source_tier"] not in rule.trusted_source_tiers
        for card in cards
    )


# 每條降級的條件是程式，不是資料——新增第四條規則必然要寫評估它的函式，所以
# 這張表跟著 debate_rules._DOWNGRADE_PARAMETERS 一起走。
_DOWNGRADE_CONDITIONS = {
    "few_independent_domains": _too_few_independent_domains,
    "low_trust_source": _cites_an_untrusted_source,
}


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


def _validate_confidence(report, sources, problems, rules=None):
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
    # 上限是規則的函數，上面那三項不是。規則未知時只放掉這一項——拿現行設定去
    # 算會把「我不知道」變成一個有自信的失敗宣稱，而那正是「改了設定就判舊 run
    # 失敗」的那個 bug。
    if rules is RULES_NOT_RECORDED:
        return
    cap = confidence_cap(report, sources, rules)
    if CONFIDENCE_LEVELS.index(level) > CONFIDENCE_LEVELS.index(cap):
        problems.append("信心 {} 高於資料上限 {}".format(level, cap))


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


def is_safe_source_url(value):
    """Only absolute HTTP(S) evidence links may become active links."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)
