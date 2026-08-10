"""Deterministic report fixtures for Ticket #10 tests and demos.

The text is synthetic and exists only to exercise contracts and renderers.  It
must never be presented as current market research.
"""

from copy import deepcopy

from .question import ASSET_CLASS_CRYPTO
from .report_contract import validate_market_report
from .seats import SEAT_IDS

FIXTURE_CASES = (
    "consensus-6-1",
    "no-consensus-3-3-1",
    "insufficient-votes-3",
    "insufficient-data",
    "cross-reference-failure",
)

_RUN_ID = "fixture-20260801-btc"
_GENERATED_AT = "2026-08-01T02:00:00Z"
_CATEGORIES = (
    "spot-technical",
    "derivatives",
    "onchain",
    "official-events",
    "news",
    "social-macro",
    "data-quality",
)


def load_fixture(case, asset_class=ASSET_CLASS_CRYPTO):
    """Return an independent copy of one named fixture.

    ``asset_class`` is the market the fixture run belongs to. It reaches the
    report contract unchanged, which is what lets a renderer test prove the same
    ballot is shown under the seat names that market actually uses (ADR 0006).
    The default keeps the historical fixture a crypto run.
    """
    if case not in FIXTURE_CASES:
        raise KeyError("unknown report fixture: {}".format(case))
    fixture = _fixture(case, asset_class)
    return deepcopy(fixture)


def build_fixture(*args, **kwargs):
    """Build one fixture from a ballot and **prove** it passes the report contract.

    七席的立場（``None`` 代表沒投到票）決定 tally、有效票數、逐席報告列與證據
    引用，全部由同一份資料推導；接著把成品送進 :func:`validate_market_report`，
    通過才回傳。所以這個保證不是「作者相信它一致」，而是每次呼叫都實際驗過的：
    **回傳值必然通過完整報告契約，矛盾的參數在呼叫點就拋 ReportContractError。**

    有這個入口，測試就不必自己去改 ``sources.votes`` 的一半再假裝那是合法報告
    ——半套修改做出來的 fixture 走不到它宣稱要測的那條路徑。

    保證的**邊界**（誠實標示，不要讀成更多）：它保證的是「通過報告契約」，不是
    「票面在語意上合理」。契約比對的是報告與正式票數是否一致，不判斷議場該不
    該採納那個立場，所以例如「六票偏多、卻宣告採納偏空」仍然建得出來——那一層
    由 ``run_verifier`` 的停止語意攔下，屬縱深防禦。要刻意造壞掉的形狀請改用
    :func:`build_forged_fixture`，讓「我要合法的」與「我要壞掉的」在呼叫點就能
    一眼分辨。
    """
    fixture = build_forged_fixture(*args, **kwargs)
    validate_market_report(fixture["report"], fixture["sources"])
    return fixture


def build_forged_fixture(
    stances,
    consensus_status,
    adopted_stance,
    confidence,
    market_status="固定測試市場狀態，僅用於驗證契約。",
    judgement="固定測試判斷，僅用於驗證契約。",
    process_failure=False,
    asset_class=ASSET_CLASS_CRYPTO,
):
    """Same ballot → fixture, **without** the contract check.

    只給「偽造形狀本身就是被測對象」的測試用——例如票面說未達共識卻同時填了採
    納立場。名字要吵，這樣半套 bundle 不會再偽裝成合法報告混進來。
    """
    return _build_case(
        stances=tuple(stances),
        consensus_status=consensus_status,
        adopted_stance=adopted_stance,
        market_status=market_status,
        judgement=judgement,
        confidence=confidence,
        process_failure=process_failure,
        asset_class=asset_class,
    )


def _fixture(case, asset_class=ASSET_CLASS_CRYPTO):
    if case == "consensus-6-1":
        return _build_case(
            stances=("bullish",) * 6 + ("bearish",),
            consensus_status="consensus",
            adopted_stance="bullish",
            market_status="短期偏多，但存在過熱風險",
            judgement="目前較適合描述為偏多但不穩定。",
            confidence=("green", "🟢", "六票採納，引用來源涵蓋多個獨立網域。"),
            asset_class=asset_class,
        )
    if case == "no-consensus-3-3-1":
        return _build_case(
            stances=("bullish",) * 3 + ("bearish",) * 3 + ("neutral",),
            consensus_status="no_consensus",
            adopted_stance=None,
            market_status="高波動且方向不明",
            judgement="票數未達絕對門檻，因此目前無方向判斷。",
            confidence=("red", "🔴", "多方與空方票數相同，沒有採納立場可報告共識強度。"),
            asset_class=asset_class,
        )
    if case == "insufficient-votes-3":
        return _build_case(
            stances=("bullish", "bearish", "neutral", None, None, None, None),
            consensus_status="failed_insufficient_valid_votes",
            adopted_stance=None,
            market_status="流程失敗",
            judgement="少於四張有效票，未形成市場結論。",
            confidence=("red", "🔴", "只有三張有效票。"),
            process_failure=True,
            asset_class=asset_class,
        )
    if case == "insufficient-data":
        return _build_case(
            stances=("neutral",) * 7,
            consensus_status="insufficient_data",
            adopted_stance=None,
            market_status="資料不足",
            judgement="可追溯資料不足，目前無方向判斷。",
            confidence=("red", "🔴", "資料不足，不能形成方向性結論。"),
            asset_class=asset_class,
        )
    fixture = _build_case(
        stances=("bullish",) * 6 + ("bearish",),
        consensus_status="consensus",
        adopted_stance="bullish",
        market_status="短期偏多，但存在過熱風險",
        judgement="目前較適合描述為偏多但不穩定。",
        confidence=("green", "🟢", "六票採納，引用來源涵蓋多個獨立網域。"),
        asset_class=asset_class,
    )
    fixture["report"]["seats"][0]["support_evidence_ids"] = ["unknown-evidence"]
    return fixture


def _build_case(
    stances,
    consensus_status,
    adopted_stance,
    market_status,
    judgement,
    confidence,
    process_failure=False,
    asset_class=ASSET_CLASS_CRYPTO,
):
    evidence = [_evidence(index, seat_id) for index, seat_id in enumerate(SEAT_IDS)]
    vote_rows = [_vote(index, seat_id, stances[index]) for index, seat_id in enumerate(SEAT_IDS)]
    valid_rows = [row for row in vote_rows if row["state"] == "valid"]
    tally = {"bullish": 0, "bearish": 0, "neutral": 0}
    for row in valid_rows:
        tally[row["final_stance"]] += 1
    votes = {
        "run_id": _RUN_ID,
        "seat_count": 7,
        "votes": vote_rows,
        "valid_vote_count": len(valid_rows),
        "tally": tally,
        "consensus_status": consensus_status,
        "adopted_stance": adopted_stance,
    }
    debate = [
        {
            "run_id": _RUN_ID,
            "message_id": "{}-final".format(row["seat_id"]),
            "seat_id": row["seat_id"],
            "attempt_id": row["attempt_ids"][-1],
            "kind": "final_vote",
            "stance": row["final_stance"],
            "public_reason": row["final_public_reason"],
            "evidence_ids": row["final_evidence_ids"],
        }
        for row in valid_rows
    ]
    report = {
        "schema_version": "1.0.0",
        "run_id": _RUN_ID,
        "generated_at_utc": _GENERATED_AT,
        "asset_class": asset_class,
        "market_status": market_status,
        "period": {
            "label": "過去 14 日",
            "start_utc": "2026-07-18T00:00:00Z",
            "end_utc": "2026-08-01T00:00:00Z",
        },
        "confidence": {
            "level": confidence[0],
            "icon": confidence[1],
            "text": confidence[2],
        },
        "tally": tally,
        "consensus_status": consensus_status,
        "adopted_stance": adopted_stance,
        "direction_bearing": adopted_stance is not None,
        "judgement": judgement,
        "limitations": [
            "本資料為固定測試資料，不代表真實市場。",
            "票數代表七席代理人的共識，不代表客觀真理。",
        ],
        "invalidation_conditions": [
            "若價格跌回示範區間，原判斷失效。",
            "若成交量與鏈上活躍度同步回落，原判斷失效。",
        ],
        "process_failure": process_failure,
        "validation_errors": [],
        "seats": [_report_seat(row, evidence[index]) for index, row in enumerate(vote_rows)],
        "evidence": [
            {
                "evidence_id": card["evidence_id"],
                "url": card["source_url"],
                "statement": card["statement"],
                "direction": card["direction"],
            }
            for card in evidence
        ],
    }
    return {"report": report, "sources": {"evidence": evidence, "debate": debate, "votes": votes}}


def _evidence(index, seat_id):
    return {
        "run_id": _RUN_ID,
        "evidence_id": "ev-{:02d}".format(index + 1),
        "seat_id": seat_id,
        "category": _CATEGORIES[index],
        "statement": "本席提交的固定可追溯示範證據。",
        "direction": "oppose" if index == 6 else "support",
        "source_url": "https://{}.example/evidence/{}".format(seat_id, index + 1),
        "source_origin": "{}.example".format(seat_id),
        "source_tier": 1 if index < 4 else 2,
        "published_at_utc": "2026-07-{:02d}T00:00:00Z".format(25 + index),
        "retrieved_at_utc": "2026-08-01T01:{:02d}:00Z".format(index),
        "excerpt": "固定測試數值 {}，僅用於驗證報告呈現。".format(index + 1),
        "credibility_note": "固定測試資料，不是真實市場證據。",
        "fatal_counterevidence": False,
    }


def _vote(index, seat_id, stance):
    attempt_ids = ["{}-a1".format(seat_id)]
    if index == 1:
        attempt_ids.append("{}-a2".format(seat_id))
    valid = stance is not None
    initial = "bearish" if index == 1 and valid else stance
    changed = valid and initial != stance
    evidence_ids = ["ev-{:02d}".format(index + 1)] if valid else []
    return {
        "seat_id": seat_id,
        "state": "valid" if valid else "missing",
        "attempt_ids": attempt_ids,
        "invalid_reason": None if valid else "provider_timeout",
        "initial_stance": initial,
        "initial_public_reason": "本席依據已引用的資料形成初始判斷。" if valid else None,
        "initial_evidence_ids": evidence_ids,
        "final_stance": stance,
        "final_public_reason": "本席檢視正反證據後維持最終判斷。" if valid else "未取得有效票。",
        "final_evidence_ids": evidence_ids,
        "stance_changed": changed,
        "stance_change_reason": "替補讀取公開歷史後改票。" if changed else None,
    }


def _report_seat(vote, card):
    evidence_ids = vote["final_evidence_ids"]
    return {
        "seat_id": vote["seat_id"],
        "initial_stance": vote["initial_stance"],
        "final_stance": vote["final_stance"],
        "stance_changed": vote["stance_changed"],
        "initial_public_reason": vote["initial_public_reason"] or "未取得初始票。",
        "public_reason": vote["final_public_reason"],
        "stance_change_reason": vote["stance_change_reason"],
        "no_change_reason": None if vote["stance_changed"] else vote["final_public_reason"],
        "replacement_attempt_ids": vote["attempt_ids"][1:],
        "support_evidence_ids": evidence_ids if card["direction"] == "support" else [],
        "counter_evidence_ids": evidence_ids if card["direction"] == "oppose" else [],
    }
