"""Offline provider used by ``--provider-mode fake``.

It performs no network access, loads no model and needs no credential. Its
content is fixed, obviously synthetic demonstration text whose sources point at
the reserved ``.invalid`` domain, so a fake run can never be mistaken for a
market judgement. It exists so the whole pipeline — prompts, evidence, debate,
votes, report — can be exercised end to end before any real provider lands.
"""

from datetime import timedelta

from .clock import iso_utc
from .seats import SEAT_IDS

MODE = "fake"
_SOURCE_HOST = "https://fake.invalid"

# stance, public reason, then three evidence cards per seat as
# (category, statement, direction, source_tier, source_path, excerpt).
_SEAT_SCRIPT = {
    "spot-technical": (
        "bullish",
        "示範資料中，{asset} 現貨價格與量能在 {days} 日內同向走強。",
        [
            ("spot-price", "{asset} 現貨收盤價較 {days} 日前上升 4.2%。", "support", 1,
             "/spot/{slug}/close", "close 68,420 / {days}d ago 65,660"),
            ("spot-volume", "{asset} 日均成交量較前一期增加 18%。", "support", 1,
             "/spot/{slug}/volume", "avg daily volume 24.1B vs 20.4B"),
            ("technical", "{asset} 已站上 {days} 日均線但 RSI 接近超買。", "neutral", 2,
             "/ta/{slug}/ma", "MA{days} 66,180 / RSI 68.4"),
        ],
    ),
    "derivatives": (
        "bullish",
        "示範資料中，{asset} 未平倉量上升且資金費率仍屬溫和。",
        [
            ("open-interest", "{asset} 永續未平倉量 {days} 日內增加 12%。", "support", 1,
             "/derivatives/{slug}/oi", "OI 21.3B vs 19.0B"),
            ("funding", "{asset} 資金費率維持在 0.01% 附近，未見過熱。", "neutral", 1,
             "/derivatives/{slug}/funding", "8h funding 0.0104%"),
            ("liquidation", "空方清算金額連續三日高於多方。", "support", 2,
             "/derivatives/{slug}/liq", "short liq 142M / long liq 96M"),
        ],
    ),
    "onchain": (
        "bullish",
        "示範資料中，{asset} 交易所餘額下降且長期持有者續抱。",
        [
            ("exchange-flow", "交易所 {asset} 餘額 {days} 日內淨流出。", "support", 1,
             "/onchain/{slug}/exchange-balance", "net outflow 18,400 {asset}"),
            ("whales", "持有 1,000 枚以上的地址數量小幅增加。", "support", 1,
             "/onchain/{slug}/whales", "addresses >1k: 2,108 -> 2,121"),
            ("supply", "長期持有者供給比例持平，未出現大規模派發。", "neutral", 1,
             "/onchain/{slug}/lth", "LTH supply 14.9M, flat"),
        ],
    ),
    "official-events": (
        "neutral",
        "示範資料中，{asset} 相關官方與監管訊息方向互相抵銷。",
        [
            ("regulation", "監管機關公布諮詢文件，未給出最終規則。", "neutral", 1,
             "/official/consultation", "consultation closes in 30 days"),
            ("listing", "主要交易所公告新增 {asset} 相關產品。", "support", 1,
             "/official/listing/{slug}", "product launch scheduled"),
            ("enforcement", "另一司法管轄區宣布延後審查期限。", "oppose", 1,
             "/official/enforcement", "review deferred by 60 days"),
        ],
    ),
    "news": (
        "bullish",
        "示範資料中，{asset} 期間內的具名新聞報導偏向正面。",
        [
            ("adoption", "具名新聞機構報導機構配置 {asset} 的比例上升。", "support", 2,
             "/news/adoption/{slug}", "allocation up from 1.2% to 1.6%"),
            ("timeline", "期間內未出現重大負面事件時間點。", "neutral", 2,
             "/news/timeline/{slug}", "no major adverse event logged"),
            ("correction", "一則早期報導被原媒體更正，數字下修。", "oppose", 2,
             "/news/correction", "figure revised down by 15%"),
        ],
    ),
    "social-macro": (
        "bullish",
        "示範資料中，{asset} 社群情緒回升且與風險資產同向。",
        [
            ("sentiment", "社群情緒指標由中性轉為偏多。", "support", 3,
             "/social/sentiment/{slug}", "sentiment index 52 -> 61"),
            ("macro", "同期風險資產指數上升，流動性環境轉鬆。", "support", 2,
             "/macro/risk", "risk index +2.8%"),
            ("correlation", "{asset} 與大盤相關性升高，獨立性下降。", "oppose", 2,
             "/macro/correlation/{slug}", "30d correlation 0.71"),
        ],
    ),
    "counter-evidence": (
        "bearish",
        "示範資料中，多方訊號高度依賴單一期間且存在轉載重複。",
        [
            ("data-quality", "三則正面報導可追溯到同一份原始新聞稿。", "oppose", 2,
             "/quality/syndication", "3 reports share one press release"),
            ("staleness", "部分鏈上指標的最後更新時間已超過 24 小時。", "oppose", 1,
             "/quality/freshness/{slug}", "last update 31h ago"),
            ("counter-signal", "{days} 日漲幅集中在單一交易日，廣度不足。", "oppose", 1,
             "/quality/breadth/{slug}", "1 of {days} sessions = 74% of gain"),
        ],
    ),
}


class FakeProvider:
    """Deterministic, offline stand-in for the seven research seats."""

    mode = MODE

    @property
    def seat_ids(self):
        return tuple(SEAT_IDS)

    def stance_for(self, seat_id):
        return _SEAT_SCRIPT[seat_id][0]

    def research(self, call):
        """Return this seat's evidence-card content, without audit stamps."""
        fields = _fields(call.scope)
        cards = []
        for index, card in enumerate(_SEAT_SCRIPT[call.seat_id][2], start=1):
            category, statement, direction, source_tier, source_path, excerpt = card
            cards.append(
                {
                    "asset": fields["asset"],
                    "category": category,
                    "statement": statement.format(**fields),
                    "direction": direction,
                    "source_url": _SOURCE_HOST + source_path.format(**fields),
                    "source_tier": source_tier,
                    "published_at_utc": iso_utc(call.now_utc - timedelta(hours=index)),
                    "excerpt": excerpt.format(**fields),
                    "credibility_note": (
                        "fake provider 產生的示範資料，未經任何真實來源查證，不得作為市場依據。"
                    ),
                }
            )
        return cards

    def debate(self, call):
        """Return this seat's debate turn content, without audit stamps."""
        return {
            **self._position(call),
            "responds_to": _responds_to(call.seat_id),
        }

    def vote(self, call):
        """Return this seat's vote content, without audit stamps."""
        return self._position(call)

    def _position(self, call):
        stance, reason, _ = _SEAT_SCRIPT[call.seat_id]
        return {
            "stance": stance,
            "public_reason": reason.format(**_fields(call.scope)),
            "evidence_ids": _own_evidence_ids(call.seat_id, call.evidence_snapshot),
            "stance_change_reason": None,
        }


def _fields(scope):
    asset = scope.assets[0]
    return {"asset": asset, "slug": asset.lower(), "days": scope.period_days}


def _own_evidence_ids(seat_id, evidence_snapshot):
    return [card["evidence_id"] for card in evidence_snapshot if card["seat_id"] == seat_id]


def _responds_to(seat_id):
    """Every seat answers the counter-evidence seat, which answers the first seat."""
    if seat_id == "counter-evidence":
        return ["spot-technical-r1"]
    return ["counter-evidence-r1"]
