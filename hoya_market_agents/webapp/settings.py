"""The rule file, on a page: read it, edit it, and hand it back to the loader.

This is the one thing this web app writes that is neither its own log nor a
child process's: ``config/debate_rules.json``. These boundaries shape it.

**There is no second validator.** Which documents are legal is
:mod:`hoya_market_agents.debate_rules`'s question and it is asked exactly one
way — :func:`~hoya_market_agents.debate_rules.load_debate_rules`, handed the
candidate file. Nothing here checks a range, an ordering or a type in order to
refuse: what this module does with the text a user typed is *translate* it into
the JSON value it names (:func:`value_from_text`), and when it cannot, it hands
the raw text on so the loader refuses it and says why. Every refusal of a
*value* is therefore the loader's own sentence, copied, shown beside whichever
control that sentence names (:func:`fields_named_in`). The sentences this module
writes itself are about the request and the file — locked, nothing submitted,
unreadable, written-but-not-published — and none of them is a verdict on a
number someone typed.

**There is no list of fields.** The controls are walked out of the document that
is on disk (:func:`field_texts`), so a rule field added upstream reaches this
page by itself and one removed leaves no control behind. Two limits come with
that and neither is hidden: this page **edits values, and only values**. It does
not add or remove keys, rungs of ``confidence.light_scale`` or whole downgrade
rules — and where the loader treats something as optional (a downgrade rule may
simply be absent; ``light_scale`` may be empty), a document that omits it has no
control for it here, and this page cannot put it back. That is an editor for a
file under version control, which is the scope the ticket set.

**The words are a table; the fields still are not.** :data:`FIELD_LABELS` and
:data:`SECTION_LABELS` say what a control and a group are *called* in 中文, and
say nothing else. A key they do not cover still gets its control, still holds
its value, and is still submitted and saved exactly like the rest — it is shown
under the name the file spells it, marked :data:`UNTRANSLATED_NOTE`. So this
table can only ever be out of date; it is never a gate, and the one thing that
decides which controls exist is still the document.

**Writing is atomic in one specific sense, and no other.** The candidate is
complete and has already been through the loader before the name moves, so no
reader opens a half-written file and no save puts an illegal one there. It says
nothing about a file that was already illegal before the save — that one is
shown for editing rather than hidden — and nothing about two people saving at
once; see :func:`save_rules`.

What this page is locked by is the Data Root's own state — a run that is under
way — read from :func:`~hoya_market_agents.webapp.live.in_progress_run_id`, the
same answer the live room shows. :class:`~.launch.LaunchLock` is a different
question (launches *this process* started) and is deliberately not consulted: a
run begun from the CLI is affected by a rule change just the same, and that lock
cannot see it.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..debate_rules import (
    RULES_PATH,
    DebateRulesError,
    load_debate_rules,
    reload_debate_rules,
)
from .live import in_progress_run_id

# A key JSON has no comments for. ``debate_rules`` accepts these at every level
# and reads none of them, so they are carried through untouched and shown as
# prose rather than offered as controls.
COMMENT_PREFIX = "_"

# What separates the items of a list inside one text box. One character, and the
# one a reader of ``[1, 2]`` would type.
LIST_SEPARATOR = ","

SAVED = "saved"
REFUSED = "refused"
LOCKED = "locked"
UNREADABLE = "unreadable"
NOT_PUBLISHED = "not_published"
NOTHING_SUBMITTED = "nothing_submitted"

# Every way :func:`save_rules` can end. It is a tuple rather than a habit
# because the page has to have words for each one, and a test compares the two
# sets — an outcome added here with no sentence on the page fails there.
STATES = (SAVED, REFUSED, LOCKED, UNREADABLE, NOT_PUBLISHED, NOTHING_SUBMITTED)

# What each shape of value is described as under its control. These describe the
# value that is in the file right now — they are not a statement about what the
# loader will accept, which is the loader's to say and only after a save.
KIND_INTEGER = "integer"
KIND_TEXT = "text"
KIND_LIST = "list"

KIND_HINTS = {
    KIND_INTEGER: "目前是整數；送出的文字原樣交給載入器判斷。",
    KIND_TEXT: "目前是文字。",
    KIND_LIST: "目前是陣列，用半形逗號分隔；清空代表空陣列。",
}

# What a control is called when nothing here has words for it. The key name the
# file spells is shown as it is, and this is written beside it — an answer that
# is honest about being incomplete, rather than a page that refuses to draw a
# key it does not recognise.
UNTRANSLATED_NOTE = "尚未翻譯"

# What every rule field is called, and the one sentence that says what it does.
# Spec〈R-001 設定頁白話中文〉is the authority for both; the words are copied from
# it and are not paraphrased here.
#
# The key is a control's path with every list index written ``[]``, because one
# rung of ``confidence.light_scale`` is described the same way as the next: they
# are the same field, five times over. :func:`_generic` is what turns a real
# path into this spelling, and it is the only difference between the two.
FIELD_LABELS = {
    "schema_version": ("規則檔版本", "規則檔的格式版本，目前僅支援 2，平常不需改動"),
    "timeline.vote_rounds[].open_offset_ms": (
        "開票時刻（封存後毫秒）",
        "封存後過這麼多毫秒開這一輪票；單幣題封存在第 4 分鐘",
    ),
    "timeline.vote_rounds[].threshold": (
        "所需同立場票數",
        "這一輪要幾席同立場才能結案寫報告",
    ),
    "timeline.final_settle_offset_ms": (
        "硬停結算時刻（封存後毫秒）",
        "時間到直接停止辯論做最終結算；沒有立場達到末輪票數就亮紅燈",
    ),
    "confidence.light_scale[].min_votes": (
        "最低票數",
        "拿到至少這麼多有效票，燈號落在這一級",
    ),
    "confidence.light_scale[].level": (
        "燈色",
        "這一級對應的燈色（blue／green／yellow／orange／red）",
    ),
    "confidence.downgrades.few_independent_domains.levels": (
        "降幾級",
        "獨立來源網站太少時，燈號往下降的級數",
    ),
    "confidence.downgrades.few_independent_domains.min_independent_domains": (
        "最低獨立網域數",
        "採納立場引用的來源至少要來自幾個不同網站",
    ),
    "confidence.downgrades.low_trust_source.levels": (
        "降幾級",
        "引用低可信來源時，燈號往下降的級數",
    ),
    "confidence.downgrades.low_trust_source.trusted_source_tiers": (
        "可信來源等級",
        "視為可信的來源等級清單（逗號分隔）",
    ),
    "confidence.downgrades.low_trust_source.exempt_seat_ids": (
        "豁免席位",
        "不受此降級約束的席位（輿情席職責即蒐集輿情）",
    ),
}

# What each group of controls is called. Spelt the same way as the keys above,
# so ``confidence.light_scale[]`` is the title of *each rung* of that ladder —
# Spec writes that group ``confidence.light_scale``, and the five fieldsets it
# draws are five items of that one list, all titled the same.
#
# ``confidence`` holds nothing but other groups in the rule file as it stands,
# so no fieldset is drawn for it today. Its title is here anyway: a field added
# directly under it arrives with a Chinese title rather than with its path.
SECTION_LABELS = {
    "": "基本",
    "timeline": "時間軸",
    "timeline.vote_rounds[]": "投票輪清單",
    "confidence": "燈號規則",
    "confidence.light_scale[]": "燈號階梯",
    "confidence.downgrades.few_independent_domains": "降級：獨立來源不足",
    "confidence.downgrades.low_trust_source": "降級：低可信來源",
}

SECTION_DESCRIPTIONS = {
    "timeline": "辯論各輪開票時刻，全部從證據封存那一刻起算",
    "timeline.vote_rounds[]": "一列一輪：何時開票、需要幾席同立場才結案",
}

LOCKED_MESSAGE = (
    "這個 Data Root 有一個 run 還在進行中，設定頁在它結束前不接受存檔。"
    "進行中的 run 用的是它開始時那一份規則，改設定不會影響它。"
)

SAVED_MESSAGE = (
    "已寫入設定檔，並且已經生效：下一個開始的 run 會讀到新的規則。"
    "已經在進行中的 run 仍然用它開始時的那一份，不受影響。"
)

NOT_PUBLISHED_MESSAGE = (
    "設定檔已經換成新的內容，但重新載入沒有成功，所以系統目前仍在使用舊規則。"
)

NOTHING_SUBMITTED_MESSAGE = (
    "沒有收到任何設定欄位，所以什麼都沒有寫入。表單若超過伺服器願意讀的長度，"
    "送出的內容會整份被丟掉——那和「沒有改動」不是同一件事，因此這裡不當成存檔成功。"
)


@dataclass(frozen=True)
class SaveOutcome:
    """What happened to one submitted form, in the words of whoever refused it.

    ``message`` is ``None`` only when ``state`` is :data:`SAVED`. ``fields`` are
    the control paths ``message`` names, which is empty whenever the sentence
    names none of them — an unattributable sentence is shown whole rather than
    pinned on a field that may have nothing to do with it.
    """

    state: str
    message: str = None
    fields: tuple = ()
    run_id: str = None


# -- walking the document ----------------------------------------------------


def _walk(value, path, visit):
    """Rebuild ``value``, replacing every editable leaf with ``visit(path, leaf)``.

    This is the only place that decides what a leaf is and how a path is spelt,
    so the controls a page shows and the document a save rebuilds cannot come to
    two different answers. The three functions that read a path back apart —
    :func:`_segments`, :func:`_parent_of`, :func:`_label_of` — split on the two
    separators this one writes and nothing else.

    A list is walked one object at a time when its **first** item is an object,
    and is a single leaf otherwise. A list holding both would therefore be read
    by its first item; no such list exists in a rule file the loader accepts,
    and this module does not detect one.
    """
    if isinstance(value, dict):
        return {
            key: (
                item
                if key.startswith(COMMENT_PREFIX)
                else _walk(item, _join(path, key), visit)
            )
            for key, item in value.items()
        }
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [
            _walk(item, "{}[{}]".format(path, index), visit)
            for index, item in enumerate(value)
        ]
    return visit(path, value)


def _join(path, key):
    return "{}.{}".format(path, key) if path else key


def field_texts(document):
    """Return ``((path, current value as text, kind), ...)`` in document order.

    The path is the control's ``name`` and its ``id``: one string that is also
    how the loader spells the field in its own messages, which is what lets a
    refusal be shown beside the control it is about without a table mapping the
    two.
    """
    found = []

    def record(path, value):
        found.append((path, text_from_value(value), kind_of(value)))
        return value

    _walk(document, "", record)
    return tuple(found)


def kind_of(value):
    """Name the shape of one current value, for the hint under its control."""
    if isinstance(value, list):
        return KIND_LIST
    if isinstance(value, int) and not isinstance(value, bool):
        return KIND_INTEGER
    return KIND_TEXT


def text_from_value(value):
    """Render one current value as the text its control is filled with."""
    if isinstance(value, list):
        return "{} ".format(LIST_SEPARATOR).join(str(item) for item in value)
    return str(value)


def value_from_text(text, current):
    """Return the JSON value ``text`` names, shaped like ``current``.

    ``text`` is what a form field carried, so it is a string; nothing here
    checks that, and a caller handing it something else gets that type's error.

    **This function never refuses anything.** When the text does not name a
    value of the current shape — ``abc`` where an integer is, ``6.0`` where an
    integer is, a blank box — the text itself is returned, and the loader is the
    one that says so and names the field. That is the difference between a
    translation and a second validator: a translation that cannot do its job
    hands the argument on rather than inventing a verdict.

    Two translation choices are worth stating because they lose information.
    Blank text is the empty list, so a list holding one empty string cannot be
    typed here. An item that is blank after trimming is dropped, so ``1,,2`` is
    two items. Neither is a judgement about legality; both are limits of this
    text box.
    """
    if isinstance(current, list):
        return _list_from_text(text, current)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(text)
        except ValueError:
            return text
    return text


def _list_from_text(text, current):
    items = [part.strip() for part in text.split(LIST_SEPARATOR)]
    shape = current[0] if current else ""
    return [value_from_text(item, shape) for item in items if item]


def candidate_document(document, submitted):
    """Return ``document`` with every submitted control's text read back in.

    A path that was not submitted keeps the value it had, so a partial form
    changes only what it carried. A submitted name the document has no leaf for
    is ignored — the shape of the result is the shape of the document, never the
    shape of the request.
    """

    def replace(path, value):
        if path not in submitted:
            return value
        return value_from_text(submitted[path], value)

    return _walk(document, "", replace)


def known_paths(document):
    """Return every path this form knows: each control, and each box around one.

    The containers are derived from the control paths rather than walked again,
    because a container is exactly a prefix of something inside it. They are
    here because the loader talks about sections too — "the last rung of
    ``confidence.light_scale``" is about a group of controls, not one of them.
    """
    leaves = {path for path, _, _ in field_texts(document)}
    containers = set()
    for path in leaves:
        for index, character in enumerate(path):
            if index and character in ".[":
                containers.add(path[:index])
    return frozenset(leaves | containers)


def fields_named_in(message, paths):
    """Return the paths ``message`` names — the most specific ones, in path order.

    A refusal is attributed by reading the loader's own sentence: whichever of
    this form's paths appear in it are the ones it is about. Two are named when
    the sentence names two — an out-of-order pair implicates both ends — and
    none is named when the sentence names none, which is the honest answer for
    a failure that is about the file rather than a value in it.

    A path that is a prefix of another named path is dropped, so a sentence
    about one control is not also pinned on the section around it. "Prefix" is
    the whole rule: two sibling fields whose names shared a prefix would collide,
    and the rule file has none.
    """
    named = [path for path in sorted(paths) if path and path in message]
    return tuple(
        path
        for path in named
        if not any(other != path and other.startswith(path) for other in named)
    )


# -- reading the file --------------------------------------------------------


def read_document(path):
    """Return ``(document, problem)`` for the rule file as it is on disk.

    ``document`` is the parsed JSON — needed to draw the form even when the file
    is illegal, because a page that will not show an operator the values cannot
    be used to fix them. ``problem`` is the loader's sentence about that file,
    present both when the file will not parse and when it parses but is refused;
    ``None`` means the loader accepts it as it stands.

    The two fallback sentences are a default so ``problem`` is never ``None``
    beside a ``document`` of ``None``. Reaching one would mean the loader read a
    file this function could not, and both read it the same way, so nothing in
    the current code makes that happen — they are not a second diagnosis.
    """
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, _refusal(path) or "無法讀取辯論規則設定檔 {}。".format(path)
    if not isinstance(payload, dict):
        return None, _refusal(path) or "辯論規則設定檔 {} 的最外層必須是 object。".format(path)
    return payload, _refusal(path)


def _refusal(path):
    """The loader's own sentence about one file, or ``None`` if it accepts it."""
    try:
        load_debate_rules(path)
    except DebateRulesError as exc:
        return str(exc)
    return None


def locked_by(data_root):
    """Return the id of the run that locks this page, or ``None``.

    One question, asked once: :func:`~.live.in_progress_run_id`. Its blind spot
    is this page's blind spot too — it follows the newest run directory, so an
    older run that never finished while a newer one did is not seen.
    """
    return in_progress_run_id(data_root)


# -- the one write -----------------------------------------------------------


def save_rules(rules_path, submitted, data_root, replace=os.replace,
               publish=reload_debate_rules):
    """Validate one submitted form, put it in the file, and publish it.

    The order is the whole design. The candidate is written under a temporary
    name **in the same directory**, handed to
    :func:`~hoya_market_agents.debate_rules.load_debate_rules`, and only then
    moved onto the real name. So a refused edit never reaches the file, a reader
    never opens half of one, and the only document this function ever puts there
    is one the loader accepted. What was in the file before this function ran is
    not this function's claim to make — see the paragraph on an already-illegal
    file below.

    **That is the only thing the move guarantees.** ``os.replace`` says nothing
    about two saves at once: they are not serialised, the later completion wins,
    and the earlier saver is told it saved and never told its edit is gone.
    Anyone who needs one-writer-at-a-time has to look outside this module, and
    there is nothing here that claims to be it. What is here instead is the
    lock above — while a run is under way nothing is written at all — and that
    lock is about runs, not about a second person at a second browser.

    A rule file that is already illegal is still edited: it is read for its
    values, the form is drawn from them, and the save is judged on the candidate
    alone. Refusing to save until the file is legal would leave the only page
    that can fix it unusable.

    ``replace`` and ``publish`` are seams. A failure in ``replace`` leaves the
    original file, removes the candidate and re-raises. A
    :class:`~hoya_market_agents.debate_rules.DebateRulesError` from ``publish``
    is reported as :data:`NOT_PUBLISHED`, because at that point the file really
    has changed and the running system really has not, and saying either half
    alone would be false; any other exception from ``publish`` is not caught
    here and reaches the route's own boundary, which logs it.
    """
    rules_path = Path(rules_path)
    run_id = locked_by(data_root)
    if run_id is not None:
        return SaveOutcome(LOCKED, LOCKED_MESSAGE, run_id=run_id)

    document, problem = read_document(rules_path)
    if document is None:
        return SaveOutcome(UNREADABLE, problem)

    known = {path for path, _, _ in field_texts(document)}
    if not known & set(submitted):
        # A request carrying none of this form's names is not this form. Reading
        # it as "nothing was edited" would rebuild the document unchanged and
        # report a successful save, which is the one answer that would be a lie.
        return SaveOutcome(NOTHING_SUBMITTED, NOTHING_SUBMITTED_MESSAGE)

    candidate = candidate_document(document, submitted)
    written = _write_candidate(rules_path, candidate)
    try:
        load_debate_rules(written)
    except DebateRulesError as exc:
        written.unlink()
        message = str(exc).replace(str(written), str(rules_path))
        return SaveOutcome(REFUSED, message, fields_named_in(message, known_paths(document)))
    except BaseException:
        written.unlink()
        raise

    try:
        replace(str(written), str(rules_path))
    except BaseException:
        written.unlink()
        raise

    try:
        publish(rules_path)
    except DebateRulesError as exc:
        return SaveOutcome(NOT_PUBLISHED, "{}（{}）".format(NOT_PUBLISHED_MESSAGE, exc))
    return SaveOutcome(SAVED)


def _write_candidate(rules_path, document):
    """Write the candidate beside the real file and return where it landed.

    Beside it, because a move is only atomic within one filesystem, and the
    directory the file is already in is the one directory known to be on the
    same one.
    """
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(rules_path.parent),
        prefix=".{}-".format(rules_path.name),
        suffix=".candidate",
        delete=False,
    )
    written = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        written.unlink()
        raise
    return written


# -- what the page is handed -------------------------------------------------


def settings_data(data_root, rules_path=None, submitted=None, outcome=None):
    """Return everything the settings page shows, including why it cannot edit.

    ``submitted`` is what came back from a refused form, so the boxes hold what
    was typed rather than what is on disk; without it the boxes hold the file.
    ``outcome`` is the :class:`SaveOutcome` of the save that has just happened,
    or ``None`` on a plain visit.
    """
    rules_path = Path(RULES_PATH if rules_path is None else rules_path)
    document, problem = read_document(rules_path)
    run_id = locked_by(data_root)
    fields = () if document is None else field_texts(document)
    errors = _errors_of(outcome)
    return {
        "data_root": str(Path(data_root)),
        "rules_path": str(rules_path),
        "document": document,
        "problem": problem,
        "locked": run_id is not None,
        "locked_run_id": run_id,
        "locked_message": LOCKED_MESSAGE if run_id is not None else None,
        "outcome": outcome,
        "sections": _sections(fields, document, submitted or {}, errors),
        "timeline": _timeline(document),
        "comments": _comments(document),
        "field_errors": errors,
    }


def _errors_of(outcome):
    if outcome is None or outcome.state != REFUSED:
        return {}
    return {path: outcome.message for path in outcome.fields}


def _sections(fields, document, submitted, errors):
    """Group the controls under the container each one sits directly in.

    The grouping is the paths' own, so a section appears because the document
    has one, not because a section list here says so. A container holding no
    control of its own — one that only holds other containers — is not a section;
    its ``_about`` is still shown, in :func:`_comments`.
    """
    order = []
    grouped = {}
    for path, text, kind in fields:
        parent = _parent_of(path)
        if parent not in grouped:
            order.append(parent)
            grouped[parent] = []
        label, description = _field_label(path)
        grouped[parent].append(
            {
                "path": path,
                "label": label,
                "description": description,
                "untranslated": description is None,
                "kind": kind,
                "hint": KIND_HINTS[kind],
                "value": submitted.get(path, text),
                "stored": text,
                "error": errors.get(path),
            }
        )
    return [_section_of(parent, document, grouped, errors) for parent in order]


def _section_of(parent, document, grouped, errors):
    """One group of controls, under the title :data:`SECTION_LABELS` gives it.

    A container with no entry keeps the path it has always shown, marked as
    untranslated — the same fallback the controls inside it get, for the same
    reason: a whole downgrade rule added upstream must reach this page.
    """
    title = _section_label(parent)
    return {
        "path": parent,
        "label": title if title is not None else (parent or "（最外層）"),
        "untranslated": title is None,
        "description": SECTION_DESCRIPTIONS.get(_generic(parent)),
        "about": _about_at(document, parent),
        "error": errors.get(parent),
        "fields": grouped[parent],
    }


def _parent_of(path):
    """The container path one control sits directly in, or ``""`` at the top."""
    cut = max(path.rfind("."), path.rfind("["))
    return path[:cut] if cut > 0 else ""


def _label_of(path):
    """The control's own name: the last segment of its path."""
    cut = max(path.rfind("."), path.rfind("["))
    return path[cut + 1:] if cut >= 0 else path


_LIST_INDEX = re.compile(r"\[\d+\]")


def _generic(path):
    """One path with every list index written ``[]``.

    How the two tables above are keyed, and the only reason a ladder of five
    rungs needs one entry rather than five. :func:`_walk` is what spells an
    index in the first place, and it writes nothing but digits between the
    brackets, so this is exact rather than a guess at the shape.
    """
    return _LIST_INDEX.sub("[]", path)


def _field_label(path):
    """Return ``(標籤, 白話說明)`` for one control.

    A path :data:`FIELD_LABELS` does not cover returns the key name the file
    spells and ``None`` — no sentence, because there is no honest one to write.
    That pair is what the page draws as "untranslated", and it is the whole of
    the fallback: nothing else about the control changes.
    """
    return FIELD_LABELS.get(_generic(path), (_label_of(path), None))


def _section_label(path):
    """Return the 中文 title of one group, or ``None`` when there is no entry."""
    return SECTION_LABELS.get(_generic(path))


def _about_at(document, path):
    """The ``_about`` comment written on one container, or ``""``."""
    value = _at(document, path)
    if not isinstance(value, dict):
        return ""
    about = value.get("{}about".format(COMMENT_PREFIX))
    return about if isinstance(about, str) else ""


def _at(document, path):
    """Resolve one container path against the document, or ``None``.

    The inverse of how :func:`_walk` spells a path, and the only other place
    that reads one; it understands the two separators that function writes.
    """
    value = document
    for segment in _segments(path):
        if isinstance(segment, int):
            if not isinstance(value, list) or segment >= len(value):
                return None
            value = value[segment]
            continue
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def _segments(path):
    if not path:
        return []
    found = []
    for part in path.split("."):
        name, _, rest = part.partition("[")
        if name:
            found.append(name)
        while rest:
            index, _, rest = rest.partition("]")
            if index:
                found.append(int(index))
            _, _, rest = rest.partition("[")
    return found


def _timeline(document):
    """Return one readable row per schema-v2 vote round.

    The document already owns the order and number of rounds. This projection
    adds only presentation words and a clock conversion; it does not validate
    the values or keep a second rule ladder.
    """
    rounds = _at(document, "timeline.vote_rounds") if isinstance(document, dict) else None
    if not isinstance(rounds, list):
        return []
    rows = []
    for index, vote_round in enumerate(rounds, start=1):
        if not isinstance(vote_round, dict):
            continue
        offset = vote_round.get("open_offset_ms")
        rows.append(
            {
                "round": index,
                "label": "第 {} 輪".format(index),
                "open_offset_ms": offset,
                "threshold": vote_round.get("threshold"),
                "clock": _clock(offset) if type(offset) is int else "—",
            }
        )
    return rows


def _clock(milliseconds):
    seconds = max(0, int(milliseconds) // 1000)
    return "{:02d}:{:02d}".format(seconds // 60, seconds % 60)


def _comments(document):
    """Return ``(path, comment)`` for every ``_about`` the document carries.

    Comments are the only thing in the file this page cannot edit, and dropping
    them from view would leave an operator editing numbers whose written
    explanation is one file away.
    """
    found = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "{}about".format(COMMENT_PREFIX) and isinstance(item, str):
                    found.append((path or "（最外層）", item))
                elif not key.startswith(COMMENT_PREFIX):
                    walk(item, _join(path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "{}[{}]".format(path, index))

    if isinstance(document, dict):
        walk(document, "")
    return found
