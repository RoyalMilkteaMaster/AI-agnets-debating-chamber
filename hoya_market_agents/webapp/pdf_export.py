"""The run directory's third write path, and the only one that adds a ``.pdf``.

Two paths wrote into a run directory before this module: the ``launch`` child,
which owns the directory it creates, and :mod:`~hoya_market_agents.webapp.outcome`,
which creates one ``outcome.json`` per run after that run's period has run out.
This is the third, and it is deliberately the narrowest of the three:

* **It only ever adds a ``.pdf``.** The names it may write are the right-hand
  column of :data:`EXPORTS` and nothing else. No existing record is opened for
  writing, moved or removed, which is why ``run_verifier`` still verifies a run
  it has been used on — the bundle it checks is byte for byte what it was.
* **It overwrites nothing, not even its own output.** If either target is already
  in the run directory the export is refused (:data:`ALREADY_EXPORTED`) and the
  reader is told which files are there. An exported PDF is a file in a run
  directory like any other, and "no existing file is changed" has no exception
  carved out for the one this module wrote last week. Nothing is lost by
  refusing: the same pages produce the same document, and anyone who wants it
  again can move the old one out of the way first.

  **That promise is a promise about concurrent requests too**, which took two
  mechanisms rather than one. The server is threaded, so "look, then convert, then
  put the file in place" was three moments with nothing holding the run in
  between: two submissions for one run both passed the check, both converted, and
  the second put its file over the first's. So (1) one run is exported by one
  request at a time — :func:`_run_lock` is held across the check, the conversion
  *and* the promotion, and a second request for that run is told it is already
  under way (:data:`IN_PROGRESS`) rather than queued behind a browser-length
  wait; and (2) the promotion itself cannot clobber, because it is
  :func:`os.link`, which fails with :class:`FileExistsError` when the name is
  taken instead of quietly replacing what is there. The lock is what makes the
  behaviour orderly; the link is what makes the guarantee hold even if a second
  writer ever appears outside this process.
* **A ``.pdf`` appears only when it is whole.** Each conversion is aimed at a
  uniquely named neighbour in the same directory and moved onto the final name
  with :func:`os.replace` afterwards, so no reader can observe a half-written or
  0-byte ``report.pdf``. A converter that exits claiming success without writing
  anything is treated as a failure, because a 0-byte file is not a PDF.
* **A failed export leaves the directory as it was.** Both files are converted
  before either is promoted, so a failure during conversion adds nothing. Two
  links are not one transaction on any filesystem, so the second can still fail
  after the first succeeded — and then the first is **removed again**. What makes
  that removal safe is not the lock but proof of ownership: a promoted name is a
  second link to the very file this call wrote, so it carries that file's inode,
  and :func:`_undo` removes a name only while it still does. A name that now holds
  somebody else's file is left alone and said out loud, because "the directory is
  back as it was" is a claim, and a claim about a file this code did not create is
  not one it is in a position to make.

**The converter is the seam, and it is the whole seam.** ``convert(source,
target)`` is handed two paths and must leave a PDF at ``target`` or raise; the
default is :class:`EdgeConverter`, Microsoft Edge in headless mode, with paths
translated by ``wslpath -w`` when this process is running under WSL and a
Windows browser is the one being asked to read them. Every test injects its own
converter, so the suite never starts a browser — and by the same token, what a
test may assert about the default one is its *interface*: that both paths were
translated and both translated values reached the runner. The flags Edge is
handed are explicitly not a test's business (Spec〈測試決策／不應耦合的實作細節〉).
"""

import os
import shutil
import subprocess
import tempfile
import threading
from collections import namedtuple
from pathlib import Path

from ..run_store import resolve_run_dir

# ``(source, target)`` for every file one export produces, in the order they are
# converted. This pairing is the whole schedule of the feature: which page
# becomes which PDF is decided here once, and the endpoint's source check, the
# button's enabled state and the failure messages all read it from here rather
# than keeping a second list that could disagree.
EXPORTS = (("report.html", "report.pdf"), ("debate.html", "debate.pdf"))

EXPORT_SOURCES = tuple(source for source, _ in EXPORTS)
EXPORT_TARGETS = tuple(target for _, target in EXPORTS)

# What one export ended as. ``EXPORTED`` is the only state that wrote anything.
EXPORTED = "exported"
RUN_MISSING = "run_missing"
SOURCE_MISSING = "source_missing"
ALREADY_EXPORTED = "already_exported"
IN_PROGRESS = "in_progress"
CONVERSION_FAILED = "conversion_failed"

# Every state an export can end in. Declared as a tuple rather than left implicit
# so the route's status table and the page's wording table can both be asserted
# against it: a sixth state added here without either would otherwise reach a
# reader as an unhandled ``KeyError``.
STATES = (
    EXPORTED,
    RUN_MISSING,
    SOURCE_MISSING,
    ALREADY_EXPORTED,
    IN_PROGRESS,
    CONVERSION_FAILED,
)

# One lock per run directory, and the lock that guards the table of them. Both are
# module-level because "one export of one run at a time" is a statement about this
# process, not about one request or one handler: the server serves requests on
# threads that share this module.
#
# **Entries are never removed, deliberately.** Dropping one safely needs a count of
# who is still holding it, and a wrong count hands two requests two different locks
# for one run — which is the bug this table exists to prevent. What it costs is one
# lock object per run this process exported, a few dozen bytes, bounded by how many
# runs a person exports before restarting a local server.
_RUN_LOCKS = {}
_RUN_LOCKS_GUARD = threading.Lock()

# How a not-yet-whole PDF is named while it is being written: a dotted, uniquely
# suffixed neighbour of the file it is about to become. The leading dot keeps it
# out of a casual listing and the suffix keeps it from ever being mistaken for
# the artifact — nothing in this project treats ``.part`` as a document.
TEMPORARY_PREFIX = "."
TEMPORARY_SUFFIX = ".part"

# The command names and the conventional install locations the default converter
# looks in, in that order. A Windows Edge is what a WSL process finds under
# ``/mnt/c``; the bare names are for a machine where Edge is on the ``PATH``.
# When none of them is there the converter says so instead of guessing, and a
# caller with Edge somewhere else passes ``executable=``.
EDGE_COMMAND_NAMES = ("msedge.exe", "msedge", "microsoft-edge", "microsoft-edge-stable")
EDGE_INSTALL_PATHS = (
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
)

WSLPATH = "wslpath"

# How the throwaway browser profile of one conversion is named. It is created by
# :mod:`tempfile`, so it lands in the operating system's temporary directory —
# outside the Data Root, outside the run directory, and outside the Code Root.
PROFILE_PREFIX = "hoya-edge-profile-"

# How long one conversion and one path translation may take. A resident server
# answers a request on one thread, so a browser that never exits would otherwise
# hold that thread for as long as the process lives.
EDGE_TIMEOUT_SECONDS = 120
WSLPATH_TIMEOUT_SECONDS = 10

# How much of an external program's complaint reaches a page, and from which end.
# **The last lines, not the first.** Edge's stderr opens with startup noise it
# writes even on a conversion that worked — an enclave image it could not load, a
# crashpad lock — and says what actually went wrong at the end. An unbounded quote
# is also a way to fill a page, so it is bounded and the bound is visible.
MESSAGE_LIMIT = 400


class PdfConversionError(Exception):
    """A converter could not produce a PDF, and carries the reason in Chinese."""


class PdfExport(namedtuple("PdfExport", "state message written")):
    """What one export did, and the sentence a reader is shown for it.

    ``written`` is the target names that are on disk because of this call, in the
    order they landed — empty for every state that wrote nothing.
    """

    @property
    def ok(self):
        return self.state == EXPORTED


def export_run_pdfs(data_root, run_id, convert=None):
    """Turn one run's ready-made HTML into PDFs beside it, or say why not.

    ``convert`` is the seam described in the module docstring; ``None`` means the
    default :class:`EdgeConverter`, which is the only thing in this module that
    starts an external process.

    Six answers, and each of them is a sentence a reader can act on: the run id
    names nothing, this run is being exported right now, it already has its PDFs, it
    has not produced the pages to print yet, the conversion broke, or both PDFs are
    there now. Nothing raises out of here for a failure this module can name — the
    caller's job is to choose a status and a page, not to catch.

    **The run is held for the whole of it.** Everything that follows the lock — the
    look for existing PDFs, both conversions and both promotions — happens with this
    run to itself. Checking outside the lock is what let two submissions both decide
    the directory was empty.

    **The wait is refused rather than taken.** ``acquire(blocking=False)``: an export
    is a browser-length operation, and a second request that queued behind it would
    hold a server thread for as long as a browser takes to print two documents. The
    honest answer is that it is already happening.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    if run_dir is None:
        return PdfExport(
            RUN_MISSING, "找不到這個 run_id 的執行紀錄：{}。".format(run_id), ()
        )
    lock = _run_lock(run_dir)
    if not lock.acquire(blocking=False):
        return PdfExport(
            IN_PROGRESS,
            "這個 run 正在匯出中（另一個請求還沒做完），所以這次沒有重複匯出。"
            "等它結束後重新整理這一頁就會看到結果。",
            (),
        )
    try:
        return _export_held(run_dir, convert)
    finally:
        lock.release()


def _run_lock(run_dir):
    """Return the one lock that stands for this run directory.

    Keyed on the resolved directory rather than on the run id: two Data Roots may
    hold a run of the same id, and they are two runs. ``setdefault`` under
    :data:`_RUN_LOCKS_GUARD` so that two threads asking at the same moment get the
    same lock — the failure mode of getting this wrong is two locks for one run,
    which is no lock at all.
    """
    key = os.path.abspath(str(run_dir))
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(key, threading.Lock())


def _export_held(run_dir, convert):
    """The export proper, with this run's lock already held.

    **The refusal is checked before the sources are**, because it is the answer to
    "why can this not be pressed": a run holding both PDFs is done, whether or not
    the pages that made them are still on disk.
    """
    already = _existing_targets(run_dir)
    if already:
        return PdfExport(
            ALREADY_EXPORTED,
            "這個 run 已經有 {} 了。這裡不覆寫任何既有檔案，所以這次沒有匯出；"
            "要重做請先自己把舊的 PDF 移走或刪掉。".format("、".join(already)),
            (),
        )
    missing = [name for name in EXPORT_SOURCES if not (run_dir / name).is_file()]
    if missing:
        return PdfExport(
            SOURCE_MISSING,
            "這個 run 還沒有 {}，沒有可以轉檔的來源，因此沒有建立任何 PDF。".format(
                "、".join(missing)
            ),
            (),
        )
    return _convert_all(run_dir, EdgeConverter() if convert is None else convert)


def existing_targets(data_root, run_id):
    """Return the export targets already in one run's directory.

    Public because the page needs it and may not look for itself:
    :mod:`~hoya_market_agents.webapp.pages` does no I/O, so the route asks this and
    hands the answer over. That is what keeps the button's disabled state and this
    module's refusal the same decision rather than two that agree by luck.

    ``()`` for a run id that names nothing — the page for that is a 404, and a
    caller holding no run has nothing to be told about.
    """
    run_dir = resolve_run_dir(data_root, run_id)
    return () if run_dir is None else _existing_targets(run_dir)


def _existing_targets(run_dir):
    return tuple(name for name in EXPORT_TARGETS if (run_dir / name).is_file())


def _convert_all(run_dir, convert):
    """Convert every pair into a neighbour file, then move them all onto place.

    Two phases rather than one, because one PDF is not the deliverable: a run
    directory holding ``report.pdf`` and no ``debate.pdf`` is a half-done export,
    and a reader who pressed the button once should not have to work out which
    half they got. So a failure anywhere in the first phase ends the export with
    nothing added at all.

    The cleanup is in ``finally`` because that is the only place that covers all
    three ways out — a failure, a promotion, and an exception this module did not
    foresee. A promoted file has already left its temporary name, so unlinking
    what is left is never unlinking an export that worked.
    """
    staged = []
    try:
        for source, target in EXPORTS:
            problem = _stage_one(run_dir, source, target, convert, staged)
            if problem is not None:
                return PdfExport(CONVERSION_FAILED, problem, ())
        return _promote(run_dir, staged)
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def _stage_one(run_dir, source, target, convert, staged):
    """Convert one page. Return ``None``, or the sentence saying what stopped it.

    **Every exception the converter raises is caught**, not only
    :class:`PdfConversionError`. The converter is injected, so what it can raise
    is not this module's to enumerate — a browser that is not installed, a path
    that will not translate and a caller's own stub all arrive here — and the
    reader is owed the actual type and message either way. This is the same
    boundary the request guard and the outcome sweep draw, for the same reason.

    A converter that returns without writing anything is a failure too. Edge can
    exit 0 having printed nothing, and a 0-byte ``report.pdf`` that opens in no
    reader is worse than an error page: it looks like success.
    """
    try:
        temporary = _temporary_path(run_dir, target)
    except OSError as exc:
        return "無法在 {} 建立 {} 的暫存檔（{}：{}），因此沒有建立任何 PDF。".format(
            run_dir, target, type(exc).__name__, exc
        )
    staged.append((target, temporary))
    try:
        convert(run_dir / source, temporary)
    except Exception as exc:  # noqa: BLE001 - an injected converter may raise anything
        return "把 {} 轉成 {} 時失敗（{}：{}），因此沒有留下任何 PDF。".format(
            source, target, type(exc).__name__, exc
        )
    if not _has_content(temporary):
        return (
            "轉換器結束時沒有寫出 {} 的任何內容，"
            "0 位元的檔案不是 PDF，因此沒有留下任何 PDF。".format(target)
        )
    return None


def _promote(run_dir, staged):
    """Give every staged file its final name, all of them or none.

    :func:`os.link` rather than :func:`os.replace`, for one reason: it **refuses**
    when the name is taken. A rename would silently replace whatever is under the
    name, so the "never overwrite" rule would rest entirely on the check made
    earlier — and a check and a write are two moments. This way the filesystem
    itself enforces it, and the export cannot clobber a file that appeared in
    between however it appeared. The name it creates is a second link to the
    already-complete file this call wrote, so the moment the name exists it is a
    whole PDF; the first link is the staged file, removed by the caller.

    A link that fails after an earlier one succeeded undoes the earlier one — see
    :func:`_undo`, which is where "already promoted by *this* call" is established
    rather than assumed.
    """
    written = []
    for target, temporary in staged:
        try:
            identity = _identity(temporary)
            os.link(str(temporary), str(run_dir / target))
        except FileExistsError:
            return _lost_the_name(run_dir, target, written)
        except OSError as exc:
            return _rolled_back(run_dir, target, exc, written)
        written.append((target, identity))
    names = [target for target, _ in written]
    return PdfExport(
        EXPORTED,
        "已在這個 run 的資料夾產生 {}。".format("、".join(names)),
        tuple(names),
    )


def _lost_the_name(run_dir, target, written):
    """Somebody else took one of the names while this export was running.

    Inside one process this cannot happen — the run is held — so reaching here means
    a writer outside it. The answer is the refusal the check would have given,
    because the outcome is the same one: that file was not written by this export
    and this export did not touch it.
    """
    undone = _undo(run_dir, written)
    return PdfExport(
        ALREADY_EXPORTED,
        "匯出到一半時 {} 已經被別的東西建立了；這裡不覆寫任何既有檔案，"
        "所以沒有動它，這次也沒有匯出。{}".format(target, _leftovers(undone)),
        tuple(undone.stuck),
    )


def _rolled_back(run_dir, target, exc, written):
    """Undo a half-finished promotion and report what is really on disk."""
    undone = _undo(run_dir, written)
    return PdfExport(
        CONVERSION_FAILED,
        "轉好的內容放不到 {}（{}：{}）。{}".format(
            target, type(exc).__name__, exc, _leftovers(undone)
        ),
        tuple(undone.stuck),
    )


# What :func:`_undo` found: ``stuck`` are names this call created and could not
# remove, ``foreign`` are names it created and something else has since replaced —
# so they are not this call's to remove and not its to claim either.
_Undone = namedtuple("_Undone", "stuck foreign")


def _undo(run_dir, written):
    """Remove the names this call created, and **only** those.

    Ownership is proved rather than assumed: a promoted name is a second link to
    the file this call wrote, so it carries that file's device and inode. If the
    name no longer does, the file under it is somebody else's — a later export, a
    person copying something in — and removing it would be this code deleting a
    stranger's file to tidy up its own failure. That is exactly what removing by
    name did, and how a rollback came to delete a *successful* export's output.
    """
    stuck = []
    foreign = []
    for target, identity in written:
        path = run_dir / target
        if _identity(path) != identity:
            if path.exists():
                foreign.append(target)
            continue
        try:
            os.remove(str(path))
        except FileNotFoundError:
            continue
        except OSError:
            stuck.append(target)
    return _Undone(stuck, foreign)


def _leftovers(undone):
    """The sentence about what the cleanup left, and never a claim beyond it."""
    if undone.stuck and undone.foreign:
        return (
            "清理時 {} 移不掉，{} 已經被換成別的檔案所以沒有動；"
            "這個資料夾請自己檢查一下。".format(
                "、".join(undone.stuck), "、".join(undone.foreign)
            )
        )
    if undone.stuck:
        return "清理時 {} 移不掉，這個資料夾請自己檢查一下。".format(
            "、".join(undone.stuck)
        )
    if undone.foreign:
        return "{} 已經被換成別的檔案，所以沒有動它；這個資料夾請自己檢查一下。".format(
            "、".join(undone.foreign)
        )
    return "這次建立的都清掉了，這個 run 的資料夾回到匯出前的狀態，沒有留下任何 PDF。"


def _identity(path):
    """``(device, inode)`` for one path, or ``None`` when there is nothing there."""
    try:
        stamp = os.stat(str(path))
    except OSError:
        return None
    return (stamp.st_dev, stamp.st_ino)


def _temporary_path(run_dir, target):
    """Return a fresh, uniquely named neighbour for ``target``.

    :func:`tempfile.mkstemp` rather than a name built from a pid or a clock: two
    exports of one run started at the same moment must not aim at the same file,
    and the operating system is the only thing that can promise that. The handle
    is closed immediately — what the converter needs is the path.
    """
    handle, name = tempfile.mkstemp(
        dir=str(run_dir),
        prefix="{}{}-".format(TEMPORARY_PREFIX, target),
        suffix=TEMPORARY_SUFFIX,
    )
    os.close(handle)
    return Path(name)


def _has_content(path):
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


class EdgeConverter:
    """Microsoft Edge in headless mode, with every edge a test needs injected.

    ``run`` starts the process, ``translate`` turns a path this process can open
    into one the browser can, ``locate`` answers "where is Edge", and
    ``executable`` skips the looking altogether. A test replaces them and no
    browser starts.

    **The paths are the interesting part.** Under WSL the browser is a Windows
    program and a ``/mnt/d/...`` path means nothing to it, so both the page being
    read and the file being written go through ``wslpath -w`` — the same
    translation the runbook and the preflight check use. Off WSL there is nothing
    to translate and the paths are passed as they are.

    **What this class is not tested on, on purpose.** The flags below are Edge's
    vocabulary, not this project's contract: a test that pinned them would fail
    the day Edge renamed one while the export still worked, and would pass the
    day the translation broke as long as the spelling held. The interface — both
    paths translated, both translated values handed to the runner, a non-zero
    exit reported with its own words — is what the tests hold.

    **Every conversion gets a browser profile of its own, and that is not
    tidiness.** Measured on the machine this was written for, with the user's Edge
    running: told to print a real 168 KB ``report.html`` with no profile of its
    own, the process **printed the PDF and then never exited** — 888,870 bytes on
    disk, the call still blocked at 90 seconds — because the instance it started
    handed the work to the browser already running and stayed attached to it. The
    same report with a private profile: exit code 0 in 6.4 seconds, PDF complete
    when the call returned. So the profile is what makes this a conversion that
    ends, and it also keeps the printing out of the session the user is browsing
    in. It is a throwaway directory outside the Data Root, removed when the
    conversion is over, and it is never inside the run directory — nothing but the
    two ``.pdf`` files may appear there.

    **What was measured working, so the next reader knows what is claimed.** With
    the profile in place, a real ``report.html`` (168 KB) and a real
    ``debate.html`` printed through the endpoint into a throwaway Data Root in
    about 13 seconds for the pair, giving an 886 KB and a 1.2 MB ``%PDF-1.4``
    file, with every other file in the directory unchanged and nothing left in the
    temporary directory. That held both for a Data Root on a Windows disk — the
    shape this project actually runs in — and for one inside WSL's own filesystem,
    where ``wslpath`` yields a ``\\\\wsl.localhost\\...`` path that Edge read and
    wrote through as well.

    What is *not* pinned by a test, and is therefore stated here: the exact flags.
    A conversion that fails for a reason this code did not anticipate reports
    Edge's own exit code and its last words, and the seam above is where a caller
    supplies a converter of their own.
    """

    def __init__(
        self,
        executable=None,
        run=None,
        translate=None,
        locate=None,
        timeout=EDGE_TIMEOUT_SECONDS,
    ):
        self.executable = executable
        self.run = run or subprocess.run
        self.translate = translate or host_path
        self.locate = locate or find_edge
        self.timeout = timeout

    def __call__(self, source, target):
        """Print one HTML file to one PDF, or raise :class:`PdfConversionError`.

        The profile directory lives exactly as long as the conversion does, which
        is what the ``with`` is for: it is a browser's scratch space, not an
        output, and one left behind per export would be a slow leak of tens of
        megabytes into the temporary directory.
        """
        executable = self.executable or self.locate()
        if not executable:
            raise PdfConversionError(
                "找不到 Microsoft Edge，沒辦法轉 PDF（找過 {}）。".format(
                    "、".join(EDGE_COMMAND_NAMES + EDGE_INSTALL_PATHS)
                )
            )
        with tempfile.TemporaryDirectory(prefix=PROFILE_PREFIX) as profile:
            completed = self._start(
                edge_command(
                    executable,
                    self.translate(source),
                    self.translate(target),
                    self.translate(profile),
                )
            )
        if completed.returncode != 0:
            raise PdfConversionError(
                "Edge 無頭模式以結束碼 {} 結束：{}".format(
                    completed.returncode, _quoted(completed.stderr)
                )
            )

    def _start(self, command):
        try:
            return self.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PdfConversionError(
                "執行 Edge 無頭模式失敗（{}：{}）。".format(type(exc).__name__, exc)
            ) from exc


def edge_command(executable, source, target, profile):
    """Return the argument list that prints ``source`` to ``target``.

    All three paths are already in the browser's own notation. They are separate
    members of a list and no shell is involved, so a directory name with a space
    in it — which this project's own Data Root has — needs no quoting.

    ``--headless`` and ``--disable-gpu`` are the pair that makes Chromium print
    without a window; ``--no-first-run`` keeps a fresh install from opening its
    welcome flow instead of the page; and the profile is the one measured on this
    machine to be the difference between a conversion that ends and one that never
    does (see :class:`EdgeConverter`). **This composition is not a test's
    business**, which is also why the reason for each flag is written here rather
    than asserted somewhere.
    """
    return [
        str(executable),
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--user-data-dir={}".format(profile),
        "--print-to-pdf={}".format(target),
        str(source),
    ]


def find_edge(which=None, install_paths=None):
    """Return where Edge is, or ``None`` — never a guess at where it might be."""
    which = which or shutil.which
    for name in EDGE_COMMAND_NAMES:
        found = which(name)
        if found:
            return found
    for path in EDGE_INSTALL_PATHS if install_paths is None else install_paths:
        if Path(path).is_file():
            return path
    return None


def host_path(path, run=None, which=None):
    """Return ``path`` as the browser would have to name it.

    Under WSL that is ``wslpath -w``'s answer, which is also how the operator
    runbook opens a report. Off WSL there is no ``wslpath`` on the ``PATH`` and
    nothing to translate, so the path comes back as itself — the presence of the
    tool is the test for "am I in the situation that needs it", rather than a
    second guess at what kind of machine this is.

    A translation that fails raises :class:`PdfConversionError` rather than
    returning the untranslated path: handing a Windows browser ``/mnt/d/...``
    would fail later and less clearly.
    """
    run = run or subprocess.run
    which = which or shutil.which
    if which(WSLPATH) is None:
        return str(path)
    try:
        completed = run(
            [WSLPATH, "-w", str(path)],
            capture_output=True,
            text=True,
            timeout=WSLPATH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PdfConversionError(
            "wslpath 無法把 {} 轉成 Windows 路徑（{}：{}）。".format(
                path, type(exc).__name__, exc
            )
        ) from exc
    translated = (completed.stdout or "").strip()
    if completed.returncode != 0 or not translated:
        raise PdfConversionError(
            "wslpath 無法把 {} 轉成 Windows 路徑（結束碼 {}）：{}".format(
                path, completed.returncode, _quoted(completed.stderr)
            )
        )
    return translated


def _quoted(text):
    """Quote an external program's last words without letting them fill a page."""
    trimmed = (text or "").strip()
    if not trimmed:
        return "沒有輸出"
    if len(trimmed) <= MESSAGE_LIMIT:
        return trimmed
    return "…" + trimmed[-(MESSAGE_LIMIT - 1):]
