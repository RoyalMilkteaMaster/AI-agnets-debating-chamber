"""Versioned normalization of a live question into one votable package.

The four approved question types keep their exact classification order and
vocabulary. A question drawn on the spot need not match any of them, and its
target need not be a cryptocurrency: anything unmatched becomes
``open_proposition`` — a single affirmative/negative/undecided ballot — whether
or not it names a tradable asset at all. Nothing here refuses a question; the
intake gate's two refusals (empty text, unparseable period) are the only ones
left.

``asset_class`` says which market the question belongs to, ``stance_labels`` is
the Traditional Chinese wording of the ballot, and ``proposition`` is the one
votable sentence written for the question. All three travel with the package so
prompts, audits and the vote table read the same words.
"""

from dataclasses import asdict, dataclass, field, replace

from .question import ASSET_CLASS_OPEN, asset_slug_for, inspect_question

# 這兩個例外由 intake gate 拋出，但長年是從本模組被 import 的；
# 白名單移除後 UnknownAssetError 已無人拋出，名稱仍為既有呼叫端保留。
from .question import UnknownAssetError, UnsupportedQuestionError  # noqa: F401

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
    asset_class: str = ASSET_CLASS_OPEN

    @property
    def asset_slug(self):
        return asset_slug_for(self.assets)

    def with_proposition(self, proposition):
        """Return the same package carrying the written votable proposition."""
        return replace(self, proposition=proposition)

    def to_dict(self):
        value = asdict(self)
        value["assets"] = list(self.assets)
        value["stance_options"] = list(self.stance_options)
        value["stance_labels"] = dict(self.stance_labels)
        return value


def build_question_package(question, assets=None, asset_class=None):
    """Return a normalized package; every readable question gets one.

    ``assets`` and ``asset_class`` are passed straight to
    :func:`inspect_question`: where the caller states them, the question's
    wording has no say in them. A menu-driven caller knows which row was
    clicked and which market it came from, and that answer beats anything a
    parser can infer from prose. Left at ``None``, the reading is unchanged.
    """
    scope = inspect_question(question, assets=assets, asset_class=asset_class)
    return _approved_type(scope) or _open_proposition(scope)


def _approved_type(scope):
    """Classify the four approved types in their frozen order, or return None.

    Every wording question is asked of ``scope.reading``, the same width-folded
    view the intake gate named the targets from. Asking one of them a different
    view is how ``ＢＴＣ ｖｓ ＥＴＨ 過去七天`` came to have two targets and
    still not be a comparison. The package keeps ``scope.question`` verbatim —
    prompts and artifacts must quote the user — but nothing *decides* from it.
    """
    reading = scope.reading
    if len(scope.assets) == 2 and _is_comparison(reading):
        return _package(scope, "two_asset_comparison", COMPARISON_STANCES)
    if _is_event(reading) and (scope.assets or _is_overall_market(reading)):
        return _package(scope, "event_impact", EVENT_STANCES)
    if not scope.assets and _is_overall_market(reading):
        return _package(scope, "overall_market_state", MARKET_STANCES)
    if len(scope.assets) != 1 or not _is_market_state(reading):
        return None
    return _package(scope, "single_asset_market_state", MARKET_STANCES)


def _open_proposition(scope):
    """Turn an unmatched question into a votable proposition.

    A question naming no tradable target is the ordinary open case, not a
    refusal: the ballot is still affirmative/negative/undecided.
    """
    return _package(scope, OPEN_QUESTION_TYPE, OPEN_STANCES)


def _package(scope, question_type, stance_options):
    return QuestionPackage(
        schema_version=QUESTION_PACKAGE_VERSION,
        question_type=question_type,
        question=scope.question,
        assets=scope.assets,
        period_days=scope.period_days,
        period_stated=scope.period_stated,
        stance_options=stance_options,
        stance_labels=_stance_labels(stance_options, scope.assets),
        asset_class=scope.asset_class,
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
