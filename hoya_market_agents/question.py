"""Intake gate for run questions.

Only the five approved assets may start a run. Anything the gate cannot
positively recognise as an approved asset is rejected, so an out-of-scope
question can never reach the research seats.
"""

import re
from dataclasses import dataclass

SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
DEFAULT_PERIOD_DAYS = 14

# Ticker-shaped tokens are upper-case runs of 2 to 5 latin letters. Every such
# token must be an approved asset; the gate has no allow-list for other
# acronyms, so an unrecognised token rejects the question rather than being
# silently ignored.
_TICKER_PATTERN = re.compile(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])")
_PERIOD_PATTERN = re.compile(r"(\d{1,3})\s*(?:日|天|days?)", re.IGNORECASE)


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
    text = (question or "").strip()
    if not text:
        raise UnsupportedQuestionError("題目為空；無法判定分析資產，fail closed。")

    tickers = _TICKER_PATTERN.findall(text)
    unsupported = sorted({t for t in tickers if t not in SUPPORTED_ASSETS})
    if unsupported:
        raise UnsupportedQuestionError(
            "題目包含未核准資產或無法辨識的代號 {}；僅支援 {}，fail closed。".format(
                ", ".join(unsupported), ", ".join(SUPPORTED_ASSETS)
            )
        )

    found = {t for t in tickers if t in SUPPORTED_ASSETS}
    if not found:
        raise UnsupportedQuestionError(
            "題目未指名任何已核准資產（{}）；fail closed。".format(
                ", ".join(SUPPORTED_ASSETS)
            )
        )

    assets = tuple(asset for asset in SUPPORTED_ASSETS if asset in found)
    period_days, period_stated = _read_period(text)
    return QuestionScope(
        question=text,
        assets=assets,
        period_days=period_days,
        period_stated=period_stated,
    )


def _read_period(text):
    match = _PERIOD_PATTERN.search(text)
    if not match:
        return DEFAULT_PERIOD_DAYS, False
    days = int(match.group(1))
    if days <= 0:
        raise UnsupportedQuestionError("分析期間必須為正整數日數；fail closed。")
    return days, True
