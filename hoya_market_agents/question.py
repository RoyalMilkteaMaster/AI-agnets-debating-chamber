"""Intake gate for run questions.

Only the five approved assets may start a run. Anything the gate cannot
positively recognise as an approved asset is rejected, so an out-of-scope
question can never reach the research seats.
"""

import re
from dataclasses import dataclass

SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
DEFAULT_PERIOD_DAYS = 14

# Approved symbols are case-insensitive at the input boundary and canonical in
# the resulting scope. Unknown upper-case ticker-shaped tokens still fail
# closed. Lower-case words are only treated as unknown assets when paired with
# an approved symbol by a comparison connector, so ordinary prose such as
# ``price action`` is not mistaken for a cryptocurrency.
_ASSET_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,5})(?![A-Za-z])")
_UPPERCASE_TICKER_PATTERN = re.compile(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])")
_COMPARISON_PAIR_PATTERN = re.compile(
    r"(?<![A-Za-z])([A-Za-z]{2,5})(?![A-Za-z])\s*"
    r"(?:與|和|跟|vs\.?|[/、,])\s*"
    r"(?<![A-Za-z])([A-Za-z]{2,5})(?![A-Za-z])",
    re.IGNORECASE,
)
_PERIOD_PATTERN = re.compile(r"(\d{1,3})\s*(?:日|天|days?)", re.IGNORECASE)
_WEEK_PERIOD_PATTERN = re.compile(r"(\d{1,3})\s*(?:週|周|weeks?)", re.IGNORECASE)
_CHINESE_WEEK_PERIOD_PATTERN = re.compile(r"([一二兩三四五六七八九十])\s*(?:週|周)")
_PERIOD_HINT_PATTERN = re.compile(
    r"(?:過去|最近|近)\s*[^\s，。！？,;；]{0,8}?(?:日|天|週|周|星期|月|年)"
)
_CONTEXTUAL_ASSET_PATTERNS = (
    re.compile(r"(?:分析|評估|研究|關注)\s*([A-Za-z]{2,5})(?![A-Za-z])", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z])([A-Za-z]{2,5})(?![A-Za-z])\s*"
        r"(?:代幣|幣種|升級(?:事件)?|市場狀態|價格(?:走勢)?|鏈上)",
        re.IGNORECASE,
    ),
)
_CHINESE_WEEK_COUNTS = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class UnsupportedQuestionError(ValueError):
    """Raised when a question falls outside the approved analysis scope."""


@dataclass(frozen=True)
class QuestionScope:
    """The approved reading of a user question."""

    question: str
    assets: tuple
    period_days: int
    period_stated: bool

    @property
    def asset_slug(self):
        return "-".join(asset.lower() for asset in self.assets)


def analyze_question(question):
    """Return the :class:`QuestionScope` for ``question`` or fail closed."""
    scope = inspect_question(question)
    if not scope.assets:
        raise UnsupportedQuestionError(
            "題目未指名任何已核准資產（{}）；fail closed。".format(
                ", ".join(SUPPORTED_ASSETS)
            )
        )
    return scope


def inspect_question(question):
    """Normalize text, approved assets and period without requiring an asset.

    Overall-market questions intentionally have no named asset. Unsupported
    symbols still fail closed before the question-type classifier runs.
    """
    text = (question or "").strip()
    if not text:
        raise UnsupportedQuestionError("題目為空；無法判定分析資產，fail closed。")

    asset_tokens = [token.upper() for token in _ASSET_TOKEN_PATTERN.findall(text)]
    unsupported = {
        token
        for token in _UPPERCASE_TICKER_PATTERN.findall(text)
        if token not in SUPPORTED_ASSETS
    }
    unsupported.update(_unsupported_comparison_assets(text))
    unsupported.update(_unsupported_contextual_assets(text))
    if unsupported:
        raise UnsupportedQuestionError(
            "題目包含未核准資產或無法辨識的代號 {}；僅支援 {}，fail closed。".format(
                ", ".join(sorted(unsupported)), ", ".join(SUPPORTED_ASSETS)
            )
        )

    found = {token for token in asset_tokens if token in SUPPORTED_ASSETS}
    assets = tuple(asset for asset in SUPPORTED_ASSETS if asset in found)
    period_days, period_stated = _read_period(text)
    return QuestionScope(
        question=text,
        assets=assets,
        period_days=period_days,
        period_stated=period_stated,
    )


def _unsupported_comparison_assets(text):
    unsupported = set()
    for left, right in _COMPARISON_PAIR_PATTERN.findall(text):
        pair = (left.upper(), right.upper())
        if not any(token in SUPPORTED_ASSETS for token in pair):
            continue
        unsupported.update(token for token in pair if token not in SUPPORTED_ASSETS)
    return unsupported


def _unsupported_contextual_assets(text):
    candidates = {
        match.group(1).upper()
        for pattern in _CONTEXTUAL_ASSET_PATTERNS
        for match in pattern.finditer(text)
    }
    return candidates.difference(SUPPORTED_ASSETS)


def _read_period(text):
    match = _PERIOD_PATTERN.search(text)
    if match:
        return _positive_period(int(match.group(1)))

    match = _WEEK_PERIOD_PATTERN.search(text)
    if match:
        return _positive_period(int(match.group(1)) * 7)

    match = _CHINESE_WEEK_PERIOD_PATTERN.search(text)
    if match:
        return _positive_period(_CHINESE_WEEK_COUNTS[match.group(1)] * 7)

    if _PERIOD_HINT_PATTERN.search(text):
        raise UnsupportedQuestionError(
            "題目包含無法解析的分析期間；請使用明確日數或週數，fail closed。"
        )
    return DEFAULT_PERIOD_DAYS, False


def _positive_period(days):
    if days <= 0:
        raise UnsupportedQuestionError("分析期間必須為正整數日數；fail closed。")
    return days, True
