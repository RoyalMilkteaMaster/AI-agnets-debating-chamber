"""Versioned, fail-closed normalization of approved market questions."""

import re
from dataclasses import asdict, dataclass

from .question import SUPPORTED_ASSETS, UnsupportedQuestionError, inspect_question

QUESTION_PACKAGE_VERSION = "1.0.0"
MARKET_STANCES = ("bullish", "bearish", "neutral")
COMPARISON_STANCES = ("asset_a_stronger", "asset_b_stronger", "no_clear_difference")
EVENT_STANCES = ("positive", "negative", "unclear_or_conditional")
_SUPPORTED_ASSET_PATTERN = re.compile(
    r"(?<![A-Za-z])({})(?![A-Za-z])".format("|".join(SUPPORTED_ASSETS)),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuestionPackage:
    """Provider-neutral question data used by every research seat."""

    schema_version: str
    question_type: str
    question: str
    assets: tuple
    period_days: int
    period_stated: bool
    stance_options: tuple

    @property
    def asset_slug(self):
        return "-".join(asset.lower() for asset in self.assets) or "overall-market"

    def to_dict(self):
        value = asdict(self)
        value["assets"] = list(self.assets)
        value["stance_options"] = list(self.stance_options)
        return value


def build_question_package(question):
    """Return a normalized package or reject an unapproved question."""
    scope = inspect_question(question)
    if len(scope.assets) == 2 and _is_comparison(scope.question):
        return _package(
            scope,
            "two_asset_comparison",
            COMPARISON_STANCES,
            _asset_order(scope.question),
        )
    if _is_event(scope.question) and (scope.assets or _is_overall_market(scope.question)):
        return _package(scope, "event_impact", EVENT_STANCES)
    if not scope.assets and _is_overall_market(scope.question):
        return _package(scope, "overall_market_state", MARKET_STANCES)
    if len(scope.assets) != 1 or not _is_market_state(scope.question):
        raise UnsupportedQuestionError("題目不是已核准題型；fail closed。")
    return _package(scope, "single_asset_market_state", MARKET_STANCES)


def _package(scope, question_type, stance_options, assets=None):
    return QuestionPackage(
        schema_version=QUESTION_PACKAGE_VERSION,
        question_type=question_type,
        question=scope.question,
        assets=assets or scope.assets,
        period_days=scope.period_days,
        period_stated=scope.period_stated,
        stance_options=stance_options,
    )


def _is_market_state(question):
    return any(
        term in question.lower()
        for term in ("市場狀態", "走勢", "盤整", "market state")
    )


def _is_comparison(question):
    return any(
        term in question.lower()
        for term in ("比較", "相對", "強弱", "市場位置", " vs ")
    )


def _is_overall_market(question):
    return any(
        term in question.lower()
        for term in ("加密市場", "整體市場", "市場整體", "crypto market")
    )


def _is_event(question):
    return any(
        term in question.lower()
        for term in ("事件", "影響", "公告", "監管", "升級", "impact")
    )


def _asset_order(question):
    return tuple(
        match.group(1).upper() for match in _SUPPORTED_ASSET_PATTERN.finditer(question)
    )
