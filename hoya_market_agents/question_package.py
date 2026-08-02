"""Versioned normalization of a live question into one votable package.

The four approved question types keep their exact classification order and
vocabulary. A question drawn on the spot need not match any of them: anything
that still names an approved asset becomes ``open_proposition`` — a single
affirmative/negative/undecided ballot — instead of a refusal. Only a question
with no approved asset at all fails closed, because the competition scope is
the five approved assets.

``stance_labels`` is the Traditional Chinese wording of the ballot, and
``proposition`` is the one votable sentence written for an open question. Both
travel with the package so prompts, audits and the vote table read the same
words.
"""

import re
from dataclasses import asdict, dataclass, field, replace

from .question import SUPPORTED_ASSETS, UnsupportedQuestionError, inspect_question
from .question import UnknownAssetError

QUESTION_PACKAGE_VERSION = "1.0.0"
MARKET_STANCES = ("bullish", "bearish", "neutral")
COMPARISON_STANCES = ("asset_a_stronger", "asset_b_stronger", "no_clear_difference")
EVENT_STANCES = ("positive", "negative", "unclear_or_conditional")
# ``negative_side`` never collides with the event vocabulary's ``negative``, so a
# stance string alone always names exactly one question type's ballot.
OPEN_STANCES = ("affirmative", "negative_side", "undecided")
OPEN_QUESTION_TYPE = "open_proposition"

MARKET_STANCE_LABELS = {"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"}
EVENT_STANCE_LABELS = {
    "positive": "利多",
    "negative": "利空",
    "unclear_or_conditional": "不明或有條件",
}
OPEN_STANCE_LABELS = {
    "affirmative": "正方",
    "negative_side": "反方",
    "undecided": "無法決定",
}
NO_CLEAR_DIFFERENCE_LABEL = "無明顯差異"

_SUPPORTED_ASSET_PATTERN = re.compile(
    r"(?<![A-Za-z])({})(?![A-Za-z])".format("|".join(SUPPORTED_ASSETS)),
    re.IGNORECASE,
)
_STANCE_LABELS_BY_STANCES = {
    MARKET_STANCES: MARKET_STANCE_LABELS,
    EVENT_STANCES: EVENT_STANCE_LABELS,
    OPEN_STANCES: OPEN_STANCE_LABELS,
}


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
    stance_labels: dict = field(default_factory=dict)
    proposition: str = None

    @property
    def asset_slug(self):
        return "-".join(asset.lower() for asset in self.assets) or "overall-market"

    def with_proposition(self, proposition):
        """Return the same package carrying the written votable proposition."""
        return replace(self, proposition=proposition)

    def to_dict(self):
        value = asdict(self)
        value["assets"] = list(self.assets)
        value["stance_options"] = list(self.stance_options)
        value["stance_labels"] = dict(self.stance_labels)
        return value


def build_question_package(question):
    """Return a normalized package, opening a proposition before refusing."""
    try:
        scope = inspect_question(question)
    except UnknownAssetError as unknown:
        # An unknown token is not a reason to refuse a live question; it only
        # means no approved type can claim it. The token never becomes an asset,
        # and a question made only of unknown tokens still fails closed with the
        # intake gate's own reason.
        relaxed = inspect_question(question, allow_unknown_assets=True)
        return _open_proposition(relaxed, unknown)
    return _approved_type(scope) or _open_proposition(scope)


def _approved_type(scope):
    """Classify the four approved types in their frozen order, or return None."""
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
        return None
    return _package(scope, "single_asset_market_state", MARKET_STANCES)


def _open_proposition(scope, unknown=None):
    """Turn an unmatched question into a votable proposition, or fail closed."""
    if scope.assets:
        return _package(scope, OPEN_QUESTION_TYPE, OPEN_STANCES)
    if unknown is not None:
        raise unknown
    raise UnsupportedQuestionError(
        "題目未指名任何已核准資產（{}）；fail closed。".format(", ".join(SUPPORTED_ASSETS))
    )


def _package(scope, question_type, stance_options, assets=None):
    assets = assets or scope.assets
    return QuestionPackage(
        schema_version=QUESTION_PACKAGE_VERSION,
        question_type=question_type,
        question=scope.question,
        assets=assets,
        period_days=scope.period_days,
        period_stated=scope.period_stated,
        stance_options=stance_options,
        stance_labels=_stance_labels(stance_options, assets),
    )


def _stance_labels(stance_options, assets):
    """Name each stance in the ballot's own Traditional Chinese wording."""
    fixed = _STANCE_LABELS_BY_STANCES.get(stance_options)
    if fixed is not None:
        return dict(fixed)
    first, second = assets
    return {
        "asset_a_stronger": "{}較優".format(first),
        "asset_b_stronger": "{}較優".format(second),
        "no_clear_difference": NO_CLEAR_DIFFERENCE_LABEL,
    }


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
    ordered = []
    for match in _SUPPORTED_ASSET_PATTERN.finditer(question):
        asset = match.group(1).upper()
        if asset not in ordered:
            ordered.append(asset)
    return tuple(ordered)
