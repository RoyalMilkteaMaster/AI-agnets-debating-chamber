"""Request text in, page data out. No HTML here, and no query logic either.

Two jobs live in this module, and they are the two the routes must not do:

**Translating a URL into arguments.** Everything in a query string is text a
person typed, and ``run_index.query_runs`` takes values a caller vouched for.
The gap between the two is real — ``limit=-1`` is a sentence a user can write
and an argument the query refuses — so it is crossed in exactly one place,
:func:`parse_filters`, which reports what it could not use instead of passing
it on. Nothing here re-implements a filter: which runs match is asked once, of
``query_runs``, with the names it declares.

**Reading one run's own records.** A run directory is the source of truth for
its own report, votes and evidence, so the detail page reads it rather than the
index — a run that was never indexed still opens. Every record is read
separately and each reports its own absence: a page that cannot show the votes
says which file is missing, and does not show an empty table that looks like a
run where nobody voted.

**Reading is all this module does, with one named exception.**
:func:`run_data` and :func:`artifact_bytes` open no file for writing and create
no directory. :func:`history_data` is the exception, and it is a deliberate one:
it runs the expiry sweep before it reads the numbers, so a visit to the history
and hit-rate page is what makes this program look at the clock. The writing is
not done here — it is :func:`~.outcome.OutcomeCheck.run`'s, through the one
module allowed to create a run artifact — but a reader of this file is entitled
to know that one of these three functions has a side effect and which one it is.

**Shaping what a form may offer.** :func:`target_suggestions` folds the same rows
into the targets this Data Root has analysed before, so the ask bar can offer them
instead of asking the user to remember. It is the same rule as above — one path to
a run listing, no query logic of its own — with the folding done here in Python
rather than pushed into a statement.

**One page, one function.** The history query and the hit-rate page used to be
two of each. They are one page now (Spec R1), so the sweep, the totals, the
per-light rows, the filtered list and the runs the manual form may offer are
assembled together and in that order: the sweep first, because a page that read
the rows before it looked at the clock would draw the outcome of a run it was
about to record.
"""

import json
from datetime import datetime
from pathlib import Path

from ..report_contract import CONFIDENCE_LEVELS
from ..report_renderer import CONSENSUS_LABELS, stance_labels_for
from ..run_index import (
    OUTCOME_PENDING,
    OUTCOME_STATES,
    OUTCOME_UNREADABLE,
    RunIndexError,
    index_db_path,
    outcome_summary,
    query_runs,
)
from ..run_store import RunStore, resolve_run_dir
from ..seats import seat_display_names
from .outcome import OutcomeCheck, manual_entry_allowed

# How many rows a history page shows when the user names no cap. Small enough
# that the page stays one screen's worth of reading, large enough that a normal
# week of runs arrives in one go.
DEFAULT_ROW_LIMIT = 50

# The most rows one page will draw, whatever a URL asks for. Every listed row
# costs one look at the filesystem (:func:`_report_available`) and one row of
# markup, so an unbounded cap is a page a query string can make arbitrarily
# expensive. This is a ceiling on what is drawn, not on what is stored: a reader
# who has run out of room narrows the conditions, which is what they are for.
MAX_ROW_LIMIT = 500

# How many of the newest runs the ask bar's suggestions are drawn from. The list
# is a convenience — "I have looked at this one before" — so it is bounded: every
# page load folds these rows, and a Data Root with years of runs in it would
# otherwise make the debate room's own page grow with its history. Older targets
# are still typable; they are simply not offered.
SUGGESTION_RUN_LIMIT = 200

# How many times a page will re-read an index that changed underneath it. The
# read window is three queries wide and a commit is one write long, so a second
# attempt is nearly always clean; by the third, "this Data Root is being
# rewritten right now" is a better explanation than bad luck.
INDEX_READ_ATTEMPTS = 3

# What the page says when every attempt was interleaved with a commit. It is a
# caveat and not an error: the index reads fine, and the numbers are as true as
# any answer about a file somebody else is still writing.
#
# **It claims only what the stamp knows.** A version stamp says "this changed",
# never how many times, so the sentence may not name a size — two commits can land
# inside one read window as easily as one. And nothing here can promise the next
# read finds the index quiet, so the sentence suggests trying again rather than
# undertaking that it will work. The number it does carry is this page's own retry
# count, which is a fact about this request.
MIXED_READ_CAVEAT = (
    "查詢索引在本次讀取期間仍在被寫入（連續重試 {} 次都沒讀到同一個版本），"
    "上方統計與下方列表可能來自不同的索引版本，差多少無法從這裡得知；"
    "請稍後重新整理再看一次。"
)

DATE_FORMAT = "%Y-%m-%d"
DATE_FORMAT_HINT = "YYYY-MM-DD"

# The two artifacts the detail page shows or links to, and therefore the only
# two file names a request can name. It is an allowlist rather than a check on
# the path because "which files may be served" is a decision, and a decision
# with two members should be written down as two members.
#
# They are named one by one as well, because the two report tabs in every
# header are these two files and nothing else: :mod:`~.pages` spells the label
# and reads the name from here, so "which file is 市場報告" is one decision.
REPORT_ARTIFACT = "report.html"
DEBATE_ARTIFACT = "debate.html"
LINKED_ARTIFACTS = (REPORT_ARTIFACT, DEBATE_ARTIFACT)

# The records the detail page reads out of a run directory.
DETAIL_RECORDS = ("question.json", "manifest.json", "votes.json", "report.json")
EVIDENCE_RECORD = "evidence.jsonl"

STATE_OK = "ok"
STATE_INDEX_MISSING = "index_missing"
STATE_INDEX_UNREADABLE = "index_unreadable"

# The query-string names, and the ``query_runs`` keyword each one becomes. The
# left column is the URL's vocabulary and matches the CLI's flags; the right is
# the query's, and mapping them here is why no other module has to know both.
TEXT_FILTERS = (
    ("date_from", "date_from"),
    ("date_to", "date_to"),
    ("asset_class", "asset_class"),
    ("confidence", "confidence_level"),
    ("keyword", "keyword"),
)
DATE_FILTERS = ("date_from", "date_to")
LIMIT_FIELD = "limit"


def parse_filters(query):
    """Return ``(query_runs keyword arguments, problems to show the user)``.

    ``query`` is what :func:`urllib.parse.parse_qs` produced: names mapped to
    lists of strings. Only the first value of each is read — a repeated
    parameter is one field submitted twice, not two filters.

    A field left blank comes back as ``None``, which is how ``query_runs`` is
    told a filter was not asked for; passing the empty string instead would
    silently mean "runs whose asset class is the empty string" and return
    nothing. Surrounding spaces are dropped for the same reason.

    ``limit`` is the one field whose meaning differs on the two sides. The
    query takes ``None`` for "no cap" and refuses a negative number, so a
    negative or unreadable number typed by a user becomes the default cap plus
    a problem to show, never a value handed on. Zero is passed through: it is a
    number the user can mean, and the answer to it is no rows.

    Problems are sentences for the page. Every one of them names the value it
    could not use, so the reason a filter is missing is on screen rather than
    inferred from an unexpectedly long list.
    """
    problems = []
    filters = {}
    for field, argument in TEXT_FILTERS:
        value = first_value(query, field)
        if value is None:
            filters[argument] = None
            continue
        if field in DATE_FILTERS and not _is_date(value):
            problems.append(
                "日期 {!r} 不是 {} 格式，本次查詢忽略這個條件。".format(
                    value, DATE_FORMAT_HINT
                )
            )
            filters[argument] = None
            continue
        filters[argument] = value
    filters[LIMIT_FIELD], limit_problem = _parse_limit(first_value(query, LIMIT_FIELD))
    if limit_problem is not None:
        problems.append(limit_problem)
    return filters, problems


def history_data(data_root, query, outcome_check=None, write=None):
    """Return everything the history and hit-rate page shows.

    The sweep happens here rather than on a timer, and that is the whole of
    "while the server is running": a visit to this page is what makes the program
    look at the clock. No thread is started, nothing is scheduled, and a Data Root
    nobody opens the page for simply keeps its runs pending — which is a state the
    page shows rather than one it hides.

    The order matters. The sweep runs first so the totals and the rows below
    already include whatever it just recorded; reading the index first would draw
    a page that was stale the moment it was made.

    ``write`` is the result of a manual submission being answered, and is passed
    straight through for the page to quote. ``sweep`` is what the sweep did on
    this request, carried for the same reason it was before the merge: what a
    visit to this page changed is part of the answer to it, whether or not the
    markup quotes it today.

    **One state covers all three index reads.** The filtered rows, the totals and
    the runs the form may offer are three separate queries, and the second or
    third can fail on its own — an index removed or corrupted between them.
    Catching only the first would draw a page that says ``ok`` above an empty
    pending list, which reads as "nothing is waiting" when what happened is "this
    page could not find out". The index is also derived data a Data Root may
    simply not have yet, so its absence is a state of this page rather than an
    error of it: ``state`` says which of the three situations the reader is in,
    and ``reason`` carries ``run_index``'s own words about it, so the instruction
    on screen is the one that actually rebuilds the file.

    **And one version of the index covers them too**, which is
    :func:`_one_version_read`'s job: three reads that straddled somebody else's
    commit would be a card counting a verdict its own row does not show.
    ``read_caveat`` is the sentence for the case that cannot be resolved by
    reading again, and ``None`` when the reads were of one version.
    """
    check = outcome_check if outcome_check is not None else OutcomeCheck()
    sweep = check.run(data_root)
    filters, problems = parse_filters(query)
    common = {
        "data_root": str(Path(data_root)),
        "submitted": submitted_values(query),
        "problems": problems,
        "sweep": sweep,
        "write": write,
    }
    try:
        read = _one_version_read(data_root, filters, check.now())
    except RunIndexError as exc:
        return dict(
            common,
            state=_index_state(data_root),
            reason=str(exc),
            read_caveat=None,
            capped_at=None,
            rows=[],
            totals=None,
            levels=[],
            pending_runs=[],
        )
    rows = read["rows"]
    runs_root = RunStore(data_root).runs_root
    return dict(
        common,
        state=STATE_OK,
        reason=None,
        read_caveat=read["caveat"],
        # A full page is indistinguishable from a capped one, so the page says
        # so rather than letting a reader believe they saw everything.
        capped_at=filters[LIMIT_FIELD] if _is_capped(rows, filters) else None,
        rows=[row_view(row, runs_root) for row in rows],
        totals=read["summary"]["totals"],
        levels=_ordered_levels(read["summary"]["by_level"]),
        pending_runs=read["pending"],
    )


def _one_version_read(data_root, filters, now):
    """Read this page's three answers from one version of the index.

    The three — the filtered list, the totals, and the runs a verdict may be
    entered for — are three queries, and between any two of them somebody else can
    commit: the launch child indexes a finishing run, another visit to this page
    records what expired, a submission records one by hand, and the server serves
    those concurrently. Reads either side of a commit disagree by whatever landed
    between them — a sweep records one run per due run it prices, so that is not
    bounded at one — and a card counting hits its own rows still call pending is a
    page that is wrong about itself rather than merely a moment behind.

    **The index file is its own version.** ``run_index`` keeps SQLite on the
    default rollback journal — deliberately, for the DrvFs mount, and it says so —
    so every commit rewrites ``index.db`` in place and a rebuild replaces it
    outright. Its identity, size and modification time therefore change whenever
    its contents do, which is a version this module can read without opening the
    database (no SQL here, and none wanted). The three reads are taken between two
    stamps and kept only if the stamps match. **What the stamp answers is "did this
    change", not "how much"**, which is why the caveat below names no size.

    After :data:`INDEX_READ_ATTEMPTS` interleaved attempts the newest read is
    returned with :data:`MIXED_READ_CAVEAT` as its ``caveat``: a Data Root being
    written continuously is worth showing with a sentence about it, and calling it
    an unreadable index would be a false statement about a file that reads fine.

    Raises :class:`~hoya_market_agents.run_index.RunIndexError` exactly as the
    reads do; retrying does not swallow one.
    """
    attempts_left = INDEX_READ_ATTEMPTS
    while True:
        version = _index_version(data_root)
        read = {
            "rows": query_runs(data_root, **filters),
            "summary": outcome_summary(data_root),
            "pending": _pending_runs(data_root, now),
            "caveat": None,
        }
        attempts_left -= 1
        if _index_version(data_root) == version:
            return read
        if attempts_left <= 0:
            return dict(read, caveat=MIXED_READ_CAVEAT.format(INDEX_READ_ATTEMPTS))


def _index_version(data_root):
    """A stamp of the index file that changes whenever its contents do.

    ``None`` for an index that is not there: the reads themselves report that, and
    two ``None`` stamps around reads that raised are never compared.
    """
    try:
        stamp = index_db_path(data_root).stat()
    except OSError:
        return None
    return (stamp.st_ino, stamp.st_size, stamp.st_mtime_ns)


def _index_state(data_root):
    """Which of the two unavailable states this Data Root's index is in."""
    if index_db_path(data_root).is_file():
        return STATE_INDEX_UNREADABLE
    return STATE_INDEX_MISSING


def _is_capped(rows, filters):
    limit = filters[LIMIT_FIELD]
    return limit is not None and limit > 0 and len(rows) == limit


def submitted_values(query):
    """Return what the user typed, so the form can come back filled in."""
    values = {field: first_value(query, field) or "" for field, _ in TEXT_FILTERS}
    values[LIMIT_FIELD] = first_value(query, LIMIT_FIELD) or ""
    return values


def _ordered_levels(by_level):
    """Return one row per light, contract order first and strangers after.

    ``run_index`` deliberately keeps no list of lights, so the order is applied
    here, where ``report_contract`` is already the authority for it. A level the
    contract never declared is still shown — it is in the data, and dropping it
    would make the rows stop adding up to the totals — it simply sorts last,
    under its own name.
    """
    known = [level for level in CONFIDENCE_LEVELS if level in by_level]
    strangers = sorted(
        level for level in by_level if level is not None and level not in CONFIDENCE_LEVELS
    )
    ordered = known + strangers + ([None] if None in by_level else [])
    return [dict(by_level[level], level=level) for level in ordered]


def _pending_runs(data_root, now):
    """Return the runs a person may judge right now, so the form can name them.

    A form that asks a person to paste a run id is a form that gets typos; a
    list of what is actually waiting is the same information without them.

    "Waiting" is narrower than "has no record yet". A run whose period still has
    days to go has no record either, and offering it would invite a verdict on a
    prediction that has not happened — into a file that is written once and
    cannot be corrected. So the list is filtered by the same question the write
    asks, :func:`~.outcome.manual_entry_allowed`, and the form offers exactly
    what a write would accept.

    Raises :class:`~hoya_market_agents.run_index.RunIndexError` rather than
    answering with an empty list: "I could not read the index" and "nothing is
    waiting" are different sentences and the caller states which one it is.
    """
    return [
        {
            "run_id": row["run_id"],
            "run_date": row["run_date"],
            "question": row["question"] or row["run_id"],
        }
        for row in query_runs(data_root)
        if row["outcome"] is None and manual_entry_allowed(data_root, row["run_id"], now)
    ]


def target_suggestions(data_root, limit=SUGGESTION_RUN_LIMIT):
    """Return ``{asset_class: [target, ...]}`` for the ask bar's suggestion lists.

    Newest run first, and each target once. **Where the answer comes from is the
    point**: it is the rows :func:`~hoya_market_agents.run_index.query_runs`
    already returns, folded here in Python. The distinct-target query somebody
    would otherwise have written is not in ``run_index`` and no statement of any
    kind is in this package — a scan asserts that, which is also why this
    paragraph does not spell one — so the one path to a run listing stays one
    path and a suggestion cannot disagree with the history page about what has
    been run.

    A target is filed under the market its run recorded, so choosing 台股 offers
    台股 targets. A run that recorded no market is not filed at all: guessing one
    would put a target under a market it was never analysed in, and the fold has
    no business inventing what the run did not say.

    An absent or unreadable index is not an error of the ask bar — a first run has
    no index yet — so it simply has nothing to suggest. That is the same choice
    :func:`~hoya_market_agents.webapp.live.run_options` makes about the run
    picker, and for the same reason: the form must still render.
    """
    try:
        rows = query_runs(data_root, limit=limit)
    except RunIndexError:
        return {}
    suggestions = {}
    for row in rows:
        asset_class = row.get("asset_class")
        if not isinstance(asset_class, str) or not asset_class:
            continue
        _offer_targets(suggestions.setdefault(asset_class, []), row.get("assets"))
    return suggestions


def _offer_targets(offered, targets):
    """Append the targets this run names that are not offered already."""
    for target in targets or ():
        if isinstance(target, str) and target and target not in offered:
            offered.append(target)


def latest_report_run(data_root):
    """Return the newest run that really produced a report, or ``None``.

    This is what the two report tabs point at on a page that is not about one
    run — the front door before anything has been asked, the history page, the
    settings page, and the pages that answer a request this server could not
    (Spec R-002). A page that *is* about one run points at that run instead and
    never asks this.

    **The reach is the whole Data Root, deliberately.** This was capped at the
    newest 200 rows once, which was wrong rather than merely thrifty: a report
    with 200 reportless runs on top of it was answered as "no report anywhere",
    and the header disabled two tabs on a site that has a report. "The newest
    run that has one" is a claim about every run or it is not that claim. The
    cost is bounded by what the answer costs, not by a number: the rows arrive
    newest first and the loop stops at the first report, so the usual page pays
    one row and one look at the filesystem, and only a Data Root whose newest
    runs all failed to produce a report is walked far — which is the case that
    must be walked far to answer correctly. The listing itself is the same
    unbounded one :func:`_pending_runs` already makes for every history page.

    The answer is ``{"run_id": …, "artifacts": {file name: on disk}}``, which is
    the shape :func:`run_data` already reports a run's files in, so a header
    reads one shape whichever page it is on. ``None`` is "there is nothing to
    point at", and the header shows the two tabs as the disabled ones they have
    always been for a run with no files yet.

    **Where the answer comes from is the point.** It is the rows
    :func:`~hoya_market_agents.run_index.query_runs` already returns, newest
    first, folded here in Python: no filter of any kind is added to the index and
    no column is added to it, so the run this points at cannot disagree with the
    run the history page lists. ``report_path`` is a naming fact the index
    records whether or not the file was ever written, so "did this run produce a
    report" is answered by looking — the same look :func:`row_view` does for a
    listed row.

    An absent or unreadable index is not an error of a header: a Data Root
    before its first run has no index, and every page must still render. That is
    the same choice :func:`target_suggestions` makes, and for the same reason.
    """
    try:
        rows = query_runs(data_root)
    except RunIndexError:
        return None
    runs_root = RunStore(data_root).runs_root
    for row in rows:
        run_id = row.get("run_id")
        artifacts = _artifacts_beside(runs_root, row.get("report_path"))
        if isinstance(run_id, str) and artifacts[REPORT_ARTIFACT]:
            return {"run_id": run_id, "artifacts": artifacts}
    return None


def _artifacts_beside(runs_root, report_path):
    """Which of the two linked artifacts are on disk in one report's directory.

    Both files of a run sit beside each other, so where the report belongs says
    where the transcript belongs. They are answered separately because they are
    written separately: a run can end with a report and no transcript, and a tab
    that claimed otherwise would lead to a page that is not there.

    A row naming no path has no directory to look in, and nothing is opened.
    """
    if not isinstance(report_path, str) or not report_path.strip():
        return {name: False for name in LINKED_ARTIFACTS}
    run_dir = Path(runs_root) / Path(report_path).parent
    return {name: (run_dir / name).is_file() for name in LINKED_ARTIFACTS}


def run_data(data_root, run_id):
    """Return one run's detail, or ``None`` when no directory holds that id.

    ``None`` covers both a run id that is not one and a well formed id that
    names nothing: from a reader's side they are the same answer, and the page
    for both is the same page.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return None
    records = {}
    notes = {}
    for name in DETAIL_RECORDS:
        records[name], notes[name] = _read_record(run_dir, name)
    evidence, notes[EVIDENCE_RECORD] = _read_evidence(run_dir)
    question = records["question.json"] or {}
    manifest = records["manifest.json"] or {}
    votes = records["votes.json"] or {}
    report = records["report.json"] or {}
    assets = _string_list(manifest.get("assets") or question.get("assets"))
    confidence = report.get("confidence")
    return {
        "run_id": run_id,
        "run_date": run_dir.parent.name,
        "question": manifest.get("question") or question.get("question") or run_id,
        "assets": assets,
        "asset_class": question.get("asset_class"),
        "question_type": manifest.get("question_type") or question.get("question_type"),
        "confidence": confidence if isinstance(confidence, dict) else None,
        "consensus": _consensus_view(votes, assets),
        "seats": _seat_views(votes, assets, question.get("asset_class")),
        "evidence": evidence,
        "artifacts": {
            name: (run_dir / name).is_file() for name in LINKED_ARTIFACTS
        },
        "notes": notes,
    }


def artifact_bytes(data_root, run_id, name):
    """Return one linked artifact's bytes, or ``None``.

    ``None`` is the answer for a run that does not exist, a name outside
    :data:`LINKED_ARTIFACTS` and a file that is not there or cannot be read.
    The name is checked against the allowlist before a path is built from it,
    so no request can name a file this module was not going to serve.
    """
    if name not in LINKED_ARTIFACTS:
        return None
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return None
    try:
        return (run_dir / name).read_bytes()
    except OSError:
        return None


def row_view(row, runs_root):
    """Add to one indexed row the words and states its columns are shown as.

    Public because it is this page's shaping seam: a caller — including a test —
    hands it a row shaped like ``query_runs``' own and reads back exactly what
    the list shows, with no index and no Data Root in the way.

    A stance, a consensus status or a light the authorities do not declare is
    carried through as it was recorded rather than replaced with an invented
    word: the index accepts values no configuration in this build declares, and
    showing what is really there is the documented decision for all three (see
    :func:`~.pages.asset_class_label` and :func:`~.pages._light`, which answer
    the same question the same way).
    """
    tally = row.get("tally") if isinstance(row.get("tally"), dict) else {}
    assets = _string_list(row.get("assets"))
    labels = stance_labels_for(sorted(tally), assets)
    adopted = row.get("adopted_stance")
    return dict(
        row,
        assets=assets,
        tally_view=[
            {"stance": stance, "label": labels.get(stance, stance), "count": count}
            for stance, count in sorted(tally.items())
        ],
        adopted_label=labels.get(adopted, adopted),
        consensus_label=CONSENSUS_LABELS.get(
            row.get("consensus_status"), row.get("consensus_status")
        ),
        outcome_state=_outcome_state(row.get("outcome")),
        report_available=_report_available(runs_root, row.get("report_path")),
    )


def _outcome_state(recorded):
    """Which of the five states one row's ``outcome`` column is in.

    The same reading :func:`~hoya_market_agents.run_index.outcome_summary` counts
    by, so a row's own word and the totals above it cannot disagree: nothing
    recorded is a run still waiting, and a value outside the closed set is a
    record that cannot be read as an answer rather than a verdict.
    """
    if recorded is None:
        return OUTCOME_PENDING
    return recorded if recorded in OUTCOME_STATES else OUTCOME_UNREADABLE


def _report_available(runs_root, report_path):
    """Whether this run's report is really on disk.

    ``report_path`` is a naming fact — where this run's report belongs — that the
    index records whether or not the file was written, so "did this run produce a
    report" is answered by looking, once per listed row. A row naming no path has
    no report to find; nothing is opened either way.
    """
    if not isinstance(report_path, str) or not report_path.strip():
        return False
    return (Path(runs_root) / report_path).is_file()


def _consensus_view(votes, assets):
    status = votes.get("consensus_status")
    stances = _string_list(votes.get("stances")) or sorted(votes.get("tally") or {})
    labels = stance_labels_for(stances, assets)
    tally = votes.get("tally") if isinstance(votes.get("tally"), dict) else {}
    return {
        "status": status,
        "status_label": CONSENSUS_LABELS.get(status, status),
        "adopted": votes.get("adopted_stance"),
        "adopted_label": labels.get(votes.get("adopted_stance")),
        "threshold_required": votes.get("threshold_required"),
        "stop_reason": votes.get("stop_reason"),
        "tally_view": [
            {
                "stance": stance,
                "label": labels.get(stance, stance),
                "count": tally.get(stance, 0),
            }
            for stance in stances
        ],
    }


def _seat_views(votes, assets, asset_class):
    """Return one entry per seat the run itself recorded, in its own order.

    The seats come from ``votes.json`` rather than from a list kept here, so a
    run recorded with a seat this build has never heard of is still shown whole
    — under its own id, which is the only name for it that exists.

    A seat's shown name comes from the roster's reading port for **this run's**
    asset class (ADR 0006), which is the same port the debate room reads. Before
    that it was a module-level view of the open set whatever the question was, so
    a 台股 run's detail page printed 幣圈 seat names while the room printed stock
    ones — two names for one seat of one run.
    """
    stances = _string_list(votes.get("stances"))
    labels = stance_labels_for(stances, assets)
    seat_names = seat_display_names(asset_class)

    def label_of(stance):
        if stance is None:
            return None
        return labels.get(stance, stance)

    seats = []
    for record in votes.get("votes") or []:
        if not isinstance(record, dict):
            continue
        seat_id = record.get("seat_id")
        changes = [
            change
            for change in record.get("vote_changes") or []
            if isinstance(change, dict) and change.get("before") != change.get("after")
        ]
        seats.append(
            {
                "seat_id": seat_id,
                "seat_label": seat_names.get(seat_id, seat_id),
                "state": record.get("state"),
                "invalid_reason": record.get("invalid_reason"),
                "initial_stance": record.get("initial_stance"),
                "initial_stance_label": label_of(record.get("initial_stance")),
                "initial_public_reason": record.get("initial_public_reason"),
                "final_stance": record.get("final_stance"),
                "final_stance_label": label_of(record.get("final_stance")),
                "final_public_reason": record.get("final_public_reason"),
                "stance_changed": bool(record.get("stance_changed")),
                "stance_change_reason": record.get("stance_change_reason"),
                "evidence_ids": _string_list(record.get("final_evidence_ids"))
                or _string_list(record.get("initial_evidence_ids")),
                "changes": [
                    {
                        "before": change.get("before"),
                        "before_label": label_of(change.get("before")),
                        "after": change.get("after"),
                        "after_label": label_of(change.get("after")),
                        "reason": change.get("reason") or change.get("public_reason"),
                        "elapsed_ms": change.get("elapsed_ms"),
                    }
                    for change in changes
                ],
            }
        )
    return seats


def _read_record(run_dir, name):
    """Return ``(payload, note)`` for one JSON record in a run directory.

    The note tells the two silences apart: a file that was never written and a
    file that is there but will not read. Both leave the payload empty, and a
    page that says which one happened is the difference between "this run has
    no votes" and "this run's votes are unreadable".
    """
    path = run_dir / name
    if not path.is_file():
        return None, "尚未產生 {}".format(name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, "{} 無法讀取（{}：{}）".format(name, type(exc).__name__, exc)
    if not isinstance(payload, dict):
        return None, "{} 不是預期的 JSON object".format(name)
    return payload, None


def _read_evidence(run_dir):
    """Return ``(cards, note)`` from the run's sealed evidence snapshot."""
    path = run_dir / EVIDENCE_RECORD
    if not path.is_file():
        return [], "尚未產生 {}".format(EVIDENCE_RECORD)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], "{} 無法讀取（{}：{}）".format(EVIDENCE_RECORD, type(exc).__name__, exc)
    cards = []
    unreadable = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            card = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if isinstance(card, dict):
            cards.append(card)
        else:
            unreadable += 1
    note = None
    if unreadable:
        note = "{} 有 {} 行無法解析，未顯示。".format(EVIDENCE_RECORD, unreadable)
    if not cards and note is None:
        # The file is there and it read; it simply holds no card. Saying it was
        # never produced would be a different, and false, statement.
        note = "{} 沒有任何證據卡".format(EVIDENCE_RECORD)
    return cards, note


def first_value(query, field):
    """Return the first submitted value of ``field``, or ``None`` if blank.

    Public because the routes need the same reading of a submitted field that
    the filters do — a repeated parameter is one field sent twice, a blank one
    was not filled in — and two readings of that would drift.
    """
    values = query.get(field)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _is_date(value):
    try:
        datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        return False
    return True


def _parse_limit(raw):
    """Return ``(limit for query_runs, problem or None)``.

    A number above :data:`MAX_ROW_LIMIT` is brought down to it rather than
    refused: the reader asked for "as many as possible" and that is what they get,
    with the ceiling named on the page so the list is not mistaken for the whole
    answer. Refusing it would send them back to the default 50, which is further
    from what they asked for.
    """
    if raw is None:
        return DEFAULT_ROW_LIMIT, None
    try:
        limit = int(raw)
    except ValueError:
        return DEFAULT_ROW_LIMIT, _limit_problem(raw)
    if limit < 0:
        return DEFAULT_ROW_LIMIT, _limit_problem(raw)
    if limit > MAX_ROW_LIMIT:
        return MAX_ROW_LIMIT, _limit_ceiling_problem(raw)
    return limit, None


def _limit_problem(raw):
    return "筆數上限 {!r} 不是零或正整數，本次改用預設 {} 筆。".format(
        raw, DEFAULT_ROW_LIMIT
    )


def _limit_ceiling_problem(raw):
    return "筆數上限 {!r} 超過本頁一次能列出的上限，本次改用 {} 筆。".format(
        raw, MAX_ROW_LIMIT
    )


def _string_list(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
