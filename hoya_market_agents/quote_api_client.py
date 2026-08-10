"""The one place a public quote API is called, and the only one that may be.

Ticket 12 verifies finished predictions against what the market actually did.
That needs a price, and a price needs an outside service — the first and only
outbound request this project makes.

**This module is for after the fact and nothing else.** Research, debate and
report modules must not import it: a seat that could reach a live price would
be reading the answer instead of arguing for one, and the archive-then-no-more-
searching boundary the run store enforces would mean nothing. That is not left
to discipline. ``tests/test_webapp.py`` — not this module's own test file, which
would be the obvious place to look and is the wrong one — scans every source file
in the package and asserts that the set of modules naming this one equals a short
allowlist, with two false-positive checks so a scan that found nothing could not
pass. Deleting the import from a pipeline module is a one-line fix; deleting the
scan is a visible change to a test whose docstring says what it is for.

What comes back
---------------

:func:`daily_close` returns one :class:`Quote` or raises
:class:`QuoteUnavailableError`. There is no third answer, and in particular
there is no number that means "we could not tell". **That sentence is about what
the answer can do to this module; its limit is the caller's own arguments and is
stated in full below** — read to "The guarantee covers the answer, not the
question" before relying on it, because a promise whose boundary is sixty lines
away from it is one a reader will quote without the boundary. Every way this can
fail — a class with no source, a symbol that is not usable text, a refused
connection, a body that is not exactly bytes, a body that cannot be decoded, a
body that is not the table this expects, a close that is not text or is text that
is not a numeral, a close that is zero, negative or infinite, a date this cannot
read, a body too large to be a day of prices — raises, and the sentence names
what could not be used. What it can name differs by branch, because the branches
are not alike:

* where there is an offending **value**, it is quoted — the close, the date, the
  body's type when what came back was not bytes. Reading a name off somebody
  else's object is somebody else's code, so those are read by :func:`_type_name`
  and :func:`_text_of`, which always answer: a sentence that could not be built
  would be a way out of the single exit, and the last one found was exactly that;
* where an **error** was caught, its type and message are quoted. **Two
  branches do that, not one** — the connection branch and the encoding branch —
  and saying "the one that wraps somebody else's code" was this list
  contradicting itself two lines later, because the next entry is the story of
  discovering that the encoding branch wraps somebody else's code too;
* the encoding branch quotes the error's type and message through those same two
  readers. It used to quote the :class:`UnicodeError` directly and catch only
  that class, on the reasoning that by then the body is the built-in
  :class:`bytes` and the error is CPython's own rather than a caller's. **That
  reasoning was wrong.** ``raw.decode("utf-8")`` does not decide for itself what
  to raise on a bad byte: it looks the name ``"strict"`` up in :mod:`codecs`'
  error-handler registry and calls whatever is registered under it, and
  :func:`codecs.register_error` is a public API any library in this process may
  call. ``"strict"`` is a key, not a reserved word, so that line runs somebody
  else's code and a registered handler raising :class:`RuntimeError` travelled
  out untouched. The codec name beside it is a **narrower** hole rather than no
  hole: what was measured is that ``bytes.decode("utf-8")`` is short-circuited
  inside CPython before the registry is reached, and ``bytes.decode`` is the
  only one of these this module runs. :func:`codecs.decode` and
  :func:`codecs.lookup` do reach the registry for that same name; :func:`_fetch`
  says so at length, and says why an earlier draft of this line claimed
  otherwise;
* the size branch quotes **no underlying error, because none was raised** — the
  body arrived intact and this module refused it, so what the sentence names is
  the cap that was exceeded.

That list of failures is examples, not the boundary. The boundary is the
guarantee: **the whole of the answer — the opener, the response's context
manager, its ``read``, whatever that hands back, and the decoding of it — is
somebody else's code, and every :class:`Exception` any of it raises comes back
as** :class:`QuoteUnavailableError`. "The decoding of it" is on that list
because the registry above put it there, and it was the last piece to be added:
each of the earlier extensions was made by finding one more place where a value
or a call belonged to somebody else, and the decode looked like ground this
module owned until the registry showed it was not.

That guarantee was previously a list of three exception classes, and
:class:`http.client.HTTPException` (a connection cut mid-body raises
:class:`~http.client.IncompleteRead`) is on none of them, so it travelled out
untouched past callers that had been told there was one exception to handle. A
list of exception types over an open set is the same losing game as a list of
forbidden spellings over an open set of inputs; see :func:`_fetch`, which also
says why that guarantee had to be extended twice more — from what their code
*raises* to what it *returns*, and then from ancestry to exact type, because
``isinstance(raw, bytes)`` is itself a list over an open set of subclasses.

What is not caught is every :class:`BaseException` that is not an
:class:`Exception` — said as that set rather than as ``KeyboardInterrupt`` and
``SystemExit``, since :class:`BaseException` has other direct subclasses today
(:class:`GeneratorExit`, :class:`BaseExceptionGroup`) and may gain more. Naming
two members of a set with no authoritative boundary is the defect this module
keeps describing, and a boundary is no more exempt from it than a body of code.
Those are somebody stopping the program.

**The guarantee covers the answer, not the question, and that limit is stated
rather than left to be discovered.** Everything above is about what comes back —
the opener, the response, the bytes and their decoding. It is not a promise about
the caller's own arguments. ``asset_class`` and ``day`` are objects the caller
chose, and this module asks them things: ``QUOTE_SOURCES.get(asset_class)`` runs
their ``__hash__`` and ``__eq__``, both refusals format them, and ``day`` is
subtracted from, compared against and asked to ``strftime`` twice. A caller that
passes something hostile in any of those gets that object's exception, not a
:class:`QuoteUnavailableError`. The other two arguments are already inside the
guarantee for a different reason: ``opener`` and ``timeout`` are used only within
the guarded block, so whatever they do is caught there.

That boundary is not a shrug, and it is drawn where it is for three reasons. The
party this module defends against is **the outside service**, which chooses the
bytes and never the arguments; a hostile ``day`` is not a threat the network can
mount. The real path cannot produce one either: the only caller is
:func:`~hoya_market_agents.webapp.outcome._priced_payload`, where
``asset_class`` and ``symbol`` are read out of an archived run record by
:func:`json.loads` with no hooks — which builds exact :class:`str` and nothing
else — and ``day`` is :func:`available_close_day`'s return, which is
``local.date()`` and so exactly :class:`datetime.date`. And that caller keeps
its own net: it wraps this call in an ``except Exception`` that counts anything
this module failed to wrap as one run's failure and lets the rotation carry on,
which is the property the single exit exists to protect and it does not depend
on this module alone. Code inside this program that hands over a hostile
``symbol`` has already decided to break it and does not need this function as
the way.

**"The only caller" is not what this module's own test file pins**, and the gap
is named here rather than left in the test. That file pins the narrower thing
its scan can reach, and the reach is given as what the scan does see rather than
as a list of what it misses: parsing ``outcome.py``, the scan asserts that the
set of ``node.name`` values for the :class:`ast.FunctionDef` subtrees that
contain an :class:`ast.Call` whose callee name is ``quote`` equals
``{"_priced_payload"}``. A call site outside that description is outside the
claim; the scan's own docstring bounds it in full, and copying that boundary
here would only give it a second place to drift. Pinning that much is still
worth doing, because the allowlist scan above polices who *imports* this
module and a second call site inside ``outcome.py`` would not change a
character of it.

``symbol`` is nonetheless checked, and **not** because that closes the class of
caller arguments — it plainly does not, with ``asset_class`` and ``day`` beside
it. It is checked because a check was already there and was written against
ancestry: ``isinstance(symbol, str)`` is the same "lists the permitted parents
instead of the permitted types" mistake :func:`_fetch` spends a page arguing
against for ``raw``, in the same function, and it let a :class:`str` subclass
whose ``strip`` raised out through the very line meant to validate. Correcting a
check this module already decided to make is not the same act as adding checks
for arguments it decided not to.

**A price that could not be fetched writes nothing at all.** The run is left
pending and the web app's expiry sweep asks again on the next visit; that sweep
counts it as a quote failure rather than as a verdict. The one "cannot be priced"
that does reach an ``outcome.json`` is decided before this module is ever called
— an asset class :func:`is_quotable` says no to is recorded as ``unverifiable``,
which is a real answer distinct from "wrong". A refused fetch is not that answer,
and never becomes an invented number.

Which source answers for which class
------------------------------------

:data:`QUOTE_SOURCES` maps an asset class to the request made for it. Its keys
are not a list somebody kept up to date: a test pins them to
``question.ASSET_CLASSES`` minus :data:`~hoya_market_agents.question.
ASSET_CLASS_OPEN`, the one class that names a proposition rather than a market
and therefore has no price at all. Adding a fifth asset class upstream without
deciding where its prices come from fails that test.

All three currently resolve to the same daily CSV service and differ only in how
a symbol is spelled for it. That is deliberate: one response shape means one
parser and one set of failure modes, rather than three of each. Should one class
need a different service, its entry changes and nothing else does.

Which day is asked about
------------------------

:data:`MARKET_SESSIONS` says when each class's daily close becomes readable, and
:func:`available_close_day` turns an instant into the last day whose close had
already printed by then. A caller scoring a prediction asks with that day rather
than with the calendar date of the instant, because the close printed on the
calendar date of a 02:00Z instant lands *after* it: scoring against it would
compare a prediction with information from after it was made.

**What is not verified here.** The endpoint and its column names are what this
client is written to expect. They were not exercised against the live service in
the environment this was built in, so the assumption that survives untested is
"the service answers in this shape". Everything downstream of that assumption is
tested, and a service that answers differently produces
:class:`QuoteUnavailableError` naming what it could not use, which is the whole
reason the parser reads columns by name and refuses on anything it does not
recognise.

Nothing in this module opens a socket unless :func:`daily_close` is called
without an ``opener`` — that is, with ``None``, and not merely with something
falsy. The distinction is not pedantry: the test used to be ``opener or
urllib.request.urlopen``, which asked the injected object whether it was *true*,
so an opener that answered "no" was thrown away and this sentence was false while
still reading as true. ``tests/test_quote_api_client.py`` installs a guard for
the length of the file that turns a real ``urlopen`` into a failure. Its
ordinary tests inject an opener; **two of them deliberately do not** and call
with no opener at all, because a file that always injected one could not tell an
installed guard from a missing one — see that module's own docstring.
"""

import math
import re
import urllib.request
from collections import namedtuple
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
)

DAY_FORMAT = "%Y-%m-%d"

# The service's own date parameters, which are compact rather than ISO.
_REQUEST_DAY_FORMAT = "%Y%m%d"

# How long one request may take. Long enough for a slow public endpoint, short
# enough that a page that sweeps a handful of runs stays answerable — the sweep
# is bounded by its own cap on how many runs one pass may check.
DEFAULT_TIMEOUT_SECONDS = 10.0

# A day of daily bars is a few hundred bytes. Anything approaching this is not
# the table this parses, and reading an unbounded body because a server said so
# is how one bad answer becomes a memory problem.
MAX_RESPONSE_BYTES = 256 * 1024

# How far back a request reaches from the day wanted. Markets close: a
# prediction whose period ends on a Saturday, on Lunar New Year or on any other
# holiday has no close printed that day, and the honest answer is the last close
# on or before it rather than no answer at all. Ten days covers the longest
# ordinary closure of the three markets here (Taiwan's Lunar New Year break)
# without reaching so far back that a delisted symbol quietly prices off a stale
# bar — beyond that the run is left unverified and says so.
LOOKBACK_DAYS = 10

# The two column names read out of the response header. They are looked up by
# name rather than by position so a service that adds or reorders a column is
# still read correctly, and one that drops either is refused by name.
DATE_COLUMN = "Date"
CLOSE_COLUMN = "Close"

_BASE_URL = "https://stooq.com/q/d/l/"


class QuoteUnavailableError(Exception):
    """Raised when no price can be had. Never carries a substitute number."""


class QuoteSource(namedtuple("QuoteSource", "asset_class source_id label symbol_suffix")):
    """Where one asset class's prices come from, and how it spells a symbol.

    ``symbol_suffix`` is the whole of the per-class difference: the same daily
    CSV service answers for all three, and a class is told apart by the symbol
    space it asks in.
    """

    def symbol_for(self, symbol):
        """Return the service's spelling of ``symbol`` for this class."""
        return "{}{}".format(str(symbol).strip().lower(), self.symbol_suffix)


QUOTE_SOURCES = {
    ASSET_CLASS_CRYPTO: QuoteSource(
        ASSET_CLASS_CRYPTO, "stooq-daily", "Stooq 日線（加密資產對美元）", "usd"
    ),
    ASSET_CLASS_TW_STOCK: QuoteSource(
        ASSET_CLASS_TW_STOCK, "stooq-daily", "Stooq 日線（台股）", ".tw"
    ),
    ASSET_CLASS_US_STOCK: QuoteSource(
        ASSET_CLASS_US_STOCK, "stooq-daily", "Stooq 日線（美股）", ".us"
    ),
}


class MarketSession(namedtuple("MarketSession", "asset_class zone_name close_after")):
    """When one asset class's daily close for a given day becomes readable.

    ``close_after`` is how far into the local day the close is certainly
    printed, measured from local midnight, and ``zone_name`` is the zone that
    wall clock belongs to. The zone is named rather than carried as a fixed
    offset because one of the three markets below — New York — observes
    daylight saving: a fixed ``-05:00`` would place every summer close an hour
    out, which is exactly the hour a deadline can fall in. The other two do not
    (``UTC`` never shifts and Taiwan has not observed DST since 1979), and the
    named zone is what makes that a fact this module reads rather than one it
    assumes.

    **It is an offset from midnight rather than a wall-clock time on purpose.**
    A crypto day is complete at the *next* ``00:00:00Z``, which no
    :class:`datetime.time` can name: ``time(23, 59, 59)`` is half a second short
    of it, and half a second short is a whole incomplete bar. ``timedelta``
    reaches 24:00 and past it, so "the boundary is the end of the day" needs no
    special case anywhere else.

    The comparison is made on the local wall clock — hours, minutes and seconds
    as read off the face — not on elapsed real time, because a close is
    announced at a wall-clock time. On the two days a year a DST zone's day is
    23 or 25 hours long, the close still happens when the clock says so.
    """

    def available_day(self, moment):
        """Return the last day whose close had already printed at ``moment``.

        This is the whole point of the table: a prediction made at 02:00Z on a
        Thursday cannot be scored against Thursday's close, because at 02:00Z
        Thursday's close does not exist yet. The answer is the local day
        ``moment`` falls in when its close is already past, and the day before
        it otherwise. Which of those days a market actually printed a bar on is
        not decided here — :func:`daily_close` walks back from it to the last
        one that did, so weekends and holidays need no calendar.

        A ``close_after`` of a full day (crypto) therefore always answers with
        the previous day, which is the correct reading of "the bar for today is
        not finished until today is".
        """
        local = moment.astimezone(self.zone())
        elapsed = timedelta(
            hours=local.hour,
            minutes=local.minute,
            seconds=local.second,
            microseconds=local.microsecond,
        )
        if elapsed >= self.close_after:
            return local.date()
        return local.date() - timedelta(days=1)

    def zone(self):
        """Return the :class:`~zoneinfo.ZoneInfo` this session's clock runs on.

        Raises :class:`QuoteUnavailableError` when the zone cannot be built,
        rather than falling back to a fixed offset: a silent fallback would
        answer with a day that is an hour wrong twice a year, and a wrong day
        here is a wrong price and then a wrong verdict.

        **The catch is a guarantee and not a list**, for the same reason
        :func:`_fetch`'s is. It used to name :class:`~zoneinfo.
        ZoneInfoNotFoundError` and :class:`ValueError`, on the reading that "the
        zone database has no entry" is the only way this can go wrong. The zone
        database is a **machine this module reads**, not a value it owns, and a
        machine has more ways to refuse than one. Two were measured, and neither
        is hostile input — both are an ordinary machine in an unusual state:

        * a TZif file on ``TZPATH`` that ``stat``\\ s fine and cannot be opened.
          ``os.path.isfile`` answers "yes" and the path is returned, so it is the
          ``open`` that fails and not the test — :class:`PermissionError`, which
          is on neither name above;
        * an import hook that raises for ``tzdata``.
          :func:`zoneinfo._common.load_tzdata` catches :class:`ImportError`,
          :class:`FileNotFoundError` and :class:`UnicodeEncodeError`, so a
          :class:`RuntimeError` from the hook travels straight out.

        Both walked out of :func:`available_close_day` past a docstring
        promising one exception "however it fails". The damage was bounded — no
        wrong price, no ``outcome.json``, and the only caller's own ``except
        Exception`` catches it — but the sentence was false, and the cost it did
        carry is the one that does not show up in a test: an operator reading a
        broken environment as a bug in this program.

        The message is built by :func:`_type_name` and :func:`_text_of`, and so
        are ``asset_class`` and ``zone_name``, for the reason the other branches
        use them: reading a name or a message off somebody else's object is
        somebody else's code, and a sentence that could not be built would be a
        way out of the single exit. For the entries in :data:`MARKET_SESSIONS`
        those two readers change nothing — they are exact :class:`str` already —
        which is what makes the claim above unconditional rather than a claim
        about this table only.
        """
        try:
            zone = ZoneInfo(self.zone_name)
        except Exception as exc:  # noqa: BLE001 - 一個保證，不是一份清單
            raise QuoteUnavailableError(
                "{} 的市場時區 {} 在這台機器上讀不到（{}：{}），無法決定"
                "當下已收盤的交易日。".format(
                    _text_of(self.asset_class),
                    _text_of(self.zone_name),
                    _type_name(exc),
                    _text_of(exc),
                )
            ) from exc
        return zone


# A crypto daily bar covers a whole UTC day, so it is complete at the *next*
# midnight and not one microsecond earlier. 23:59:59 was half a second short of
# that: at 23:59:59.500000Z the day still had half a second of trading left to
# print, and answering with that day handed the scorer a bar that was not
# finished. A full day as the offset is the same statement without an edge.
CRYPTO_DAY_COMPLETE_AFTER = timedelta(days=1)

# Taiwan's ordinary close is 13:30, but the closing call auction may be extended
# to 13:33 for a security that triggered a volatility interruption, and the
# published rule allows it (TWSE trading mechanism). 13:30 is therefore the
# *earliest* the close can print and 13:33 the latest, so 13:30 would sometimes
# claim a close that had not printed yet. The boundary has to be the late one:
# asking a day too early either fails or reads a partial session, while waiting
# three minutes only ever costs three minutes.
TW_STOCK_CLOSE_AFTER = timedelta(hours=13, minutes=33)

# New York's ordinary close. Half days close at 13:00 ET; see
# :func:`available_close_day` for what that does and does not cost.
US_STOCK_CLOSE_AFTER = timedelta(hours=16)

# Which clock each quotable class's day is measured on. The keys are pinned to
# :data:`QUOTE_SOURCES` by a test rather than kept in step by hand: a class that
# can be priced but has no session here would silently fall back to a UTC
# calendar day, which is the defect this table exists to remove.
MARKET_SESSIONS = {
    ASSET_CLASS_CRYPTO: MarketSession(
        ASSET_CLASS_CRYPTO, "UTC", CRYPTO_DAY_COMPLETE_AFTER
    ),
    ASSET_CLASS_TW_STOCK: MarketSession(
        ASSET_CLASS_TW_STOCK, "Asia/Taipei", TW_STOCK_CLOSE_AFTER
    ),
    ASSET_CLASS_US_STOCK: MarketSession(
        ASSET_CLASS_US_STOCK, "America/New_York", US_STOCK_CLOSE_AFTER
    ),
}


def available_close_day(asset_class, moment):
    """Return the last day this class had printed a close for at ``moment``.

    ``moment`` is an aware :class:`~datetime.datetime`. Raises
    :class:`QuoteUnavailableError` for a class with no session — the same class
    :func:`daily_close` has no source for — and for a zone this machine cannot
    build, whatever it was that stopped it (see :meth:`MarketSession.zone`).
    Those two are the whole of "this cannot be priced" here, and a caller has
    one exception to handle for them **however the environment fails**.

    **That last phrase used to read "however it fails", and the difference is
    the caller's own arguments.** The guarantee covers this module's table and
    the machine's zone database; it is not a promise about the objects handed
    in, exactly as in :func:`daily_close` and for the same reason.
    ``MARKET_SESSIONS.get(asset_class)`` runs the caller's ``__hash__`` and
    ``__eq__``, the refusal above formats ``asset_class`` with ``{!r}``, and
    ``moment`` is asked to ``astimezone`` and then for its hour, minute, second
    and microsecond. Something hostile in either comes back as that object's
    exception. The real path hands over an exact :class:`str` read out of an
    archived record and an aware :class:`~datetime.datetime` from the clock.

    **Every boundary here is the latest the close can print at on an ordinary
    session, never the earliest.** That direction is the whole safety property:
    answering with a day whose close has not printed yet is how a prediction gets
    scored against information from after it was made, and no later record can
    correct it because ``outcome.json`` is written once. Answering with an older
    day is a stale comparison, which is visible in the record and can be re-judged
    by hand. Given a choice between the two, this picks stale.

    "On an ordinary session" is doing real work in that sentence and is not a
    hedge. Taiwan's 13:33 is the latest its close can print on **any** day; New
    York's 16:00 is the latest on an ordinary day and three hours *after* the
    close on a scheduled half day. The safety property survives that — a boundary
    later than the real close answers with an older day, never a newer one — but
    the boundary itself is not universally "the latest", and reading it as though
    it were is how the half-day paragraph below stops being read at all.

    **What this does not model, and what that costs.** A session is one constant
    per market, so two real-world effects are read as the ordinary case:

    * **US half days — the threat model, stated as an operator would need it.**
      美股使用固定 16:00 ET 普通收盤界線，不解析交易所提早收盤行事曆。在官方提早
      收盤日的 13:00–16:00 ET，系統會使用前一交易日收盤價；hit/miss 可能與使用當
      日 13:00 收盤價的人工重判不同。請以 outcome 的 ``day`` 與 ``priced_on`` 辨識
      並人工重判。

      Three or so such days a year (around Independence Day, Thanksgiving and
      Christmas) New York closes at 13:00 ET. The verdict can differ from the
      ideal one — a move that happened on the half day itself lands on the wrong
      side of the comparison — so this is a real inaccuracy and not merely a
      rounding. It is kept rather than fixed because every available fix is worse:
      an exchange calendar is a dependency this project does not have and cannot
      verify offline, a hand-written list of holidays is the
      enumeration-without-a-boundary defect this project keeps being bitten by,
      and refusing the 13:00–16:00 window unconditionally would leave one eighth
      of every US run permanently unverifiable to protect three days a year. The
      record names both ``day`` and ``priced_on``, so which close was used is a
      fact a reader can see rather than infer.
    * **Publication lag.** The boundary says when the exchange prints a close,
      not when this particular data service publishes it. A source that is late
      simply has no row for the day yet, and :func:`daily_close` falls back to
      the last row it does have — stale again, in the same safe direction.

    Both are bounded by :data:`LOOKBACK_DAYS` and both err towards an older
    close. Neither can produce a price from after the instant asked about.
    """
    session = MARKET_SESSIONS.get(asset_class)
    if session is None:
        raise QuoteUnavailableError(
            "資產類別 {!r} 沒有可用的報價來源，無法決定交易日。".format(asset_class)
        )
    return session.available_day(moment)


class Quote(namedtuple("Quote", "asset_class symbol day priced_on close source url summary")):
    """One close price, and everything an outcome record has to be able to cite.

    ``day`` is the day that was asked about; ``priced_on`` is the day the price
    actually printed. They differ whenever the market was shut, and both are
    kept because "we used Friday's close for a Sunday deadline" is a fact a
    reader is entitled to and cannot reconstruct from either one alone.
    """

    # A record is evidence, not a copy of the response. Enough of the answer to
    # recognise it later, bounded so one verbose reply cannot fill an artifact.
    MAX_SUMMARY_CHARS = 400


def quotable_asset_classes():
    """Return the asset classes a price can be fetched for."""
    return frozenset(QUOTE_SOURCES)


def is_quotable(asset_class):
    """Whether this asset class has a quote source at all.

    ``False`` for :data:`~hoya_market_agents.question.ASSET_CLASS_OPEN`, for
    ``None`` and for any value no source was declared for. This is the question
    "could this ever be checked against a price", not "is the service up".
    """
    return asset_class in QUOTE_SOURCES


def daily_close(asset_class, symbol, day, opener=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Return the close on or before ``day``, or raise saying why there is none.

    ``day`` is a :class:`datetime.date`. ``opener`` is the seam **most** tests
    use and defaults to :func:`urllib.request.urlopen`; it is called with a URL
    and a timeout and must return a context manager with ``read``. The default is
    taken when ``opener`` is ``None`` **and only then** — an object that is merely
    falsy is still the seam, because "was a seam given" is a question about this
    call and truthiness is the given object's answer to a different one. Two tests
    in ``tests/test_quote_api_client.py`` deliberately inject nothing and take the
    default path, because a file that always injected an opener could not tell an
    installed network guard from a missing one — see that module's docstring.

    Raises :class:`QuoteUnavailableError` for everything the **answer** can do,
    including the two refusals made before anything is opened — an asset class
    with no source, and a symbol that is not usable text — so a caller has one
    exception to handle rather than a guess about which failures happen where.
    That second refusal asks ``type(symbol) is str`` rather than
    :func:`isinstance`, because everything done with a symbol afterwards is
    answered by a subclass: ``strip`` on the line that validates it, ``str``
    inside :meth:`QuoteSource.symbol_for`, ``strip`` again into the
    :class:`Quote`, and ``__format__`` in every refusal sentence below this
    function that names the symbol — which is all of them. Each was a way out. A
    subclass whose ``strip`` raised escaped through the validating line itself,
    and one whose ``strip`` returned a number put that number in the record's
    symbol field, where it is not a symbol and no longer looks like a failure.

    "Everything" also includes whatever the
    injected ``opener`` and its response **raise**, of any type: :func:`_fetch`
    wraps that whole block by its guarantee rather than by a list of exception
    classes, for the reason stated there. It also includes whatever they
    **return**: a ``read`` handing back anything that is not exactly
    :class:`bytes` is refused by name rather than carried into code written for
    bytes, which is where an :class:`AttributeError` and a :class:`TypeError` used
    to escape this promise. **Exactly** is the operative word — a subclass of
    :class:`bytes` inherits the ancestry and not the behaviour, and one whose
    ``decode`` returned a number walked all the way out of this function as a
    price-shaped :class:`AttributeError`. It finally includes naming them: reading
    a type's name or an error's message is somebody else's code too, so the
    sentence that refuses cannot itself fail (:func:`_type_name`,
    :func:`_text_of`).

    Two things travel out untouched, and the second is a limit on the promise
    rather than a member of it. The first is every :class:`BaseException` that is
    not an :class:`Exception` — that whole set, not a list of its members, for
    the reason :func:`_fetch` gives; those are somebody stopping this program
    rather than a price that could not be had.

    The second is **whatever the caller's own other arguments do**. This promise
    is about the answer, not about the question, and ``asset_class`` and ``day``
    are the caller's objects: ``QUOTE_SOURCES.get(asset_class)`` runs their
    ``__hash__`` and ``__eq__`` on the first line, both pre-open refusals format
    ``asset_class`` into their sentence, and ``day`` is subtracted from in
    :func:`_build_url`, compared in :func:`_read_close` and asked to ``strftime``
    twice. A hostile one of those raises its own exception here, not a
    :class:`QuoteUnavailableError`, and no check is made — see the module
    docstring for why that boundary is drawn there and what the real path
    actually passes. ``symbol`` is checked in spite of being on the same side of
    it, and the same paragraph says why that is a correction rather than an
    exception to the rule. ``opener`` and ``timeout`` are not on this list at
    all: they are used only inside :func:`_fetch`'s guarded block, so they are
    covered by the promise above.
    """
    source = QUOTE_SOURCES.get(asset_class)
    if source is None:
        raise QuoteUnavailableError(
            "資產類別 {!r} 沒有可用的報價來源，無法自動取價。".format(asset_class)
        )
    # ``type(...) is str`` and not ``isinstance``: everything below asks this
    # object for ``strip`` twice and for ``str`` once, and only the built-in
    # answers those in text. Same reason as ``raw`` in :func:`_fetch`.
    if type(symbol) is not str or not symbol.strip():
        raise QuoteUnavailableError(
            "{} 的標的代號不是可用的文字，無法向 {} 查價。".format(
                asset_class, source.source_id
            )
        )
    url = _build_url(source, symbol, day)
    text = _fetch(url, source, symbol, opener, timeout)
    priced_on, close = _read_close(text, day, source, symbol)
    return Quote(
        asset_class=asset_class,
        symbol=symbol.strip(),
        day=day.strftime(DAY_FORMAT),
        priced_on=priced_on.strftime(DAY_FORMAT),
        close=close,
        source=source.source_id,
        url=url,
        summary=_summarise(text),
    )


def _build_url(source, symbol, day):
    return "{}?s={}&d1={}&d2={}&i=d".format(
        _BASE_URL,
        source.symbol_for(symbol),
        (day - timedelta(days=LOOKBACK_DAYS)).strftime(_REQUEST_DAY_FORMAT),
        day.strftime(_REQUEST_DAY_FORMAT),
    )


def _fetch(url, source, symbol, opener, timeout):
    """Return the response body as an exact ``str``, or raise naming what stopped it.

    One byte more than :data:`MAX_RESPONSE_BYTES` is read, which is how the cap
    is enforced without ever holding an unbounded body: an answer that fills the
    extra byte is refused rather than truncated and parsed, because a truncated
    table would parse into a real-looking price from a partial row.

    **Everything the opener and the response do is refused as one thing.** The
    block below is entirely somebody else's code — the opener is a caller's seam
    by default :func:`urllib.request.urlopen`, the ``with`` is the response's
    context manager and ``read`` is the response's. What it can raise is
    therefore an open set, and it was previously written as a list of three:
    ``OSError``, :class:`urllib.error.URLError` (itself an ``OSError``) and
    ``ValueError``. That list is wrong in a way listing cannot fix.
    :class:`http.client.HTTPException` — :class:`~http.client.IncompleteRead`
    from a connection cut mid-body, :class:`~http.client.BadStatusLine`,
    :class:`~http.client.LineTooLong` — inherits from :class:`Exception` and from
    nothing on the list, so a truncated response travelled straight out of
    :func:`daily_close` past a caller that had been told there was one exception
    to handle. Adding ``http.client.HTTPException`` to the list would close that
    one and leave the shape of the mistake untouched, which is the enumeration
    this project keeps being bitten by.

    So the rule is stated as the guarantee instead: **this function returns an
    exact** :class:`str` **or raises** :class:`QuoteUnavailableError`, **and there
    is no third answer.** Any :class:`Exception` from the block is that guarantee
    being kept, and the sentence names the type and the message so a
    ``TypeError`` from a badly written opener is as visible in a log as a refused
    connection.

    **That sentence now holds for the whole function, which it did not before.**
    It was written as though the ``return`` at the end were the safe part, and
    the encoding branch below was left outside it on the reasoning corrected
    there. Both halves of it are worth saying plainly. *Raises*: every path out
    of this function that is not the ``return`` is now a
    :class:`QuoteUnavailableError`, the decode included — save the one the next
    paragraph names, which is the same one everywhere in this module. *Returns an
    exact*
    :class:`str`: that is a fact about CPython rather than a check made here —
    ``bytes.decode`` builds its result in the interpreter's own unicode writer,
    so even when a replaced error handler supplies a :class:`str` **subclass** as
    the replacement text the value handed back is an exact :class:`str`. The
    hostile handler can therefore change what this line *raises* but not what it
    *returns*, which is why closing the raising side closes the whole sentence.

    What is deliberately **not** caught is every :class:`BaseException` that is
    not an :class:`Exception` — the whole of that set, said as a set. Writing it
    as ``KeyboardInterrupt`` and ``SystemExit`` would be the enumeration this very
    paragraph replaced, one level up: :class:`BaseException` has other direct
    subclasses today (:class:`GeneratorExit`, :class:`BaseExceptionGroup`) and may
    gain more, so two names are a list over an open set and the set itself is the
    only honest way to say it. Those are somebody stopping this program, not a
    price that could not be had.

    **The same openness applies to what ``read`` returns, and that is a separate
    hole from what it raises.** Catching :class:`Exception` around the block
    closes the question "what can their code throw"; it says nothing about "what
    can their code hand back", because the value is only *acquired* inside the
    block and is *used* after it. An injected ``read`` returning ``"not-bytes"``
    reached ``raw.decode`` — which :class:`str` does not have — and the ``except``
    below, which then read ``UnicodeError``, was not a net for
    :class:`AttributeError`, so it
    escaped as itself past the same callers. One returning ``None`` did not even
    get that far: ``len(raw)`` raised :class:`TypeError` one line sooner. Two
    escapes, one cause, and neither of them inside the guarded block.

    So ``raw`` is checked before anything is asked of it, and the check is
    ``type(raw) is bytes`` rather than :func:`isinstance`. **That difference is
    the whole of what the check is worth.** "``bytes`` is what has both
    ``__len__`` and ``decode``" is true of the built-in type and of nothing else:
    a subclass may define a ``__len__`` that raises, a ``decode`` that raises, or
    a ``decode`` that hands back a number — and that last one is not an exception
    at all, it is this function returning the wrong shape and :func:`_read_close`
    calling ``splitlines`` on an :class:`int` one frame later. Ancestry is an open
    set that anyone may join, so a check written against ancestry is the same
    enumeration this docstring argues against, one level down: it lists the
    permitted *parents* instead of the permitted types and is escaped the same
    way. Exact identity is the closed set — the one type this function's own
    remaining code is written for.

    **That narrowing costs nothing real.** ``bytearray`` and ``memoryview`` are
    not :class:`bytes` subclasses at all, so :func:`isinstance` already refused
    them and nothing about them changed; and
    :meth:`http.client.HTTPResponse.read`, the only thing the default path can
    return, hands back exactly :class:`bytes` on both its plain and its chunked
    branch. A well-behaved subclass is refused too, which is the intended reading
    rather than a casualty: a guarantee that holds only while the object on the
    other side behaves is not a guarantee.

    **Building the refusal is part of the refusal.** ``type(raw).__name__`` reads
    an attribute of the *metaclass*, so the sentence naming what came back was
    itself somebody else's code and was itself a way out — as was ``str(exc)`` in
    the transport branch above. Both now go through :func:`_type_name` and
    :func:`_text_of`, which are total for the same reason
    :func:`is_usable_price` is.

    **The encoding branch at the end was left outside all of this, and that was
    wrong.** It used to catch only :class:`UnicodeError` and quote the exception
    directly, and the paragraph here defended that as the test of whether the
    rule had been understood: the rule is "read nobody else's object without a
    reader", ``raw`` is the built-in :class:`bytes` by then, so the only thing
    ``raw.decode("utf-8")`` could raise was a :class:`UnicodeDecodeError` that
    CPython's own codec built and there was no caller's code left in that
    sentence.

    **The premise was false, and the rule was the one that caught it.**
    ``bytes.decode`` does not decide what to raise on a bad byte. It looks up the
    error handler by the **name** ``"strict"`` in :mod:`codecs`' process-global
    error-handler registry and calls whatever is bound there, and
    :func:`codecs.register_error` is a documented public API that any library in
    this process may call at any time — ``"strict"`` is a key, not a reserved
    word. Four lines in a dependency's import are enough to make that call raise
    :class:`RuntimeError`, which ``except UnicodeError`` does not catch, and it
    walked out of :func:`daily_close` exactly like the :class:`AttributeError`
    two paragraphs above.

    So the mistake was not in the rule but in reading "somebody else's **object**"
    where the rule says "somebody else's **code**". ``raw`` really is the built-in
    :class:`bytes`; the callable that runs on a bad byte is still a stranger's,
    reached through a table rather than through a parameter. Ownership is a
    question about the code a line will run, and an argument list is only one of
    the ways code arrives.

    **The codec name beside it is a narrower hole — and the first draft of this
    paragraph made it narrower still than the measurement supported.**
    ``"utf-8"`` is also a registry key and :func:`codecs.register` is also
    public. What was actually measured is one line: ``bytes.decode("utf-8")``
    does not consult a search function registered afterwards, because CPython
    short-circuits that name in ``PyUnicode_Decode`` before the registry is
    reached. The control for that is ``bytes.decode("cp1252")``, which the very
    same hijacking search function *does* capture — so the utf-8 result is a fast
    path and not a measurement that was quietly broken.

    **The earlier draft said three things where one had been measured**, and the
    other two are wrong. It said :func:`codecs.decode` and :func:`codecs.lookup`
    do not consult such a function either, and gave as the reason a cache and a
    fast path "below the registry". Those two go *through* the registry: clear
    the interpreter's codec search cache and let the search function registered
    ahead of ours decline, and both hand back the hijacked codec. What had been
    emptied in the original measurement was ``encodings._cache`` — a different
    cache one layer above, belonging to the search function that is registered
    first and answers for ``utf_8`` before anything registered later is asked.
    Zero calls measured that ordering, not the absence of a hole.

    So it is one registry with two halves, and only the handler half is on
    **this module's** path — because the line below is ``bytes.decode`` and not
    :func:`codecs.decode`. **That is a fact about one line on one interpreter,
    not a promise either of them makes**, which is exactly why the branch below
    is now guarded by what it does rather than by an argument about what can
    reach it. An argument of that kind is what put the branch outside the
    guarantee in the first place — and then wrote this paragraph one measurement
    wide and three claims long.

    **And the seam is taken by identity, not by truth.** ``opener or
    urllib.request.urlopen`` asked the injected object whether it was *true*, one
    line above the ``try`` and so outside the guarantee. That leaked twice, and
    only one of them is about hostile objects: a ``__bool__`` that raises escaped,
    and — the part that is nobody's attack — an ordinary, correct opener that
    merely happens to be falsy was silently discarded and a real socket opened in
    its place, which is this module's "nothing here opens a socket unless
    :func:`daily_close` is called without an ``opener``" being false in the one
    direction that matters. ``opener is None`` asks the question that was meant.
    """
    # ``is None`` and not ``or``: the question is whether a seam was given, and
    # truthiness is the given object's answer to a different one.
    opener = urllib.request.urlopen if opener is None else opener
    try:
        with opener(url, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - 一個保證，不是一份清單
        raise QuoteUnavailableError(
            "向 {} 查 {} 的報價失敗（{}：{}）。".format(
                source.source_id, symbol, _type_name(exc), _text_of(exc)
            )
        ) from exc
    # What ``read`` *returned* is as much somebody else's answer as what it could
    # have raised, and the guarantee above covers only the raising. Everything
    # below this line is written for the built-in ``bytes`` and for nothing that
    # merely inherits from it: asking this one question is what lets ``len`` and
    # ``decode`` be asked at all.
    if type(raw) is not bytes:
        raise QuoteUnavailableError(
            "{} 對 {} 的回應不是位元組而是 {}，無法當成報價表讀取。".format(
                source.source_id, symbol, _type_name(raw)
            )
        )
    if len(raw) > MAX_RESPONSE_BYTES:
        raise QuoteUnavailableError(
            "{} 對 {} 的回應超過 {} bytes，不是一天份的報價表，已拒絕。".format(
                source.source_id, symbol, MAX_RESPONSE_BYTES
            )
        )
    # ``"strict"`` is a key in a registry anyone may write to, so this line runs
    # somebody else's code too and is caught by the same guarantee as the rest.
    try:
        return raw.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 一個保證，不是一份清單
        raise QuoteUnavailableError(
            "{} 對 {} 的回應不是 UTF-8 文字（{}：{}）。".format(
                source.source_id, symbol, _type_name(exc), _text_of(exc)
            )
        ) from exc


# What a refusal says where it could not read what it wanted to name. A sentence
# that cannot be built is a way out of the single exit this module promises, so
# every piece of somebody else's object that reaches a message goes through one
# of the two readers below, and neither of them can fail. "Cannot fail" has the
# same boundary it has everywhere else in this module: every ``BaseException``
# that is not an ``Exception`` still travels, because somebody stopping the
# program is not a sentence that could not be built.
_UNREADABLE_TYPE = "名稱讀不到的型別"
_UNREADABLE_TEXT = "訊息讀不到"


def _type_name(value):
    """Return the name of ``value``'s type. **Total: an exact** ``str``, always.

    :func:`type` itself is safe — it reads the object's type slot and runs no
    Python, which is why it is used here and ``value.__class__`` is not: that one
    is an ordinary attribute lookup and may be a property that raises. But
    ``__name__`` *on the result* is an attribute of the **metaclass**, so it is
    somebody else's code again and may raise or hand back something that is not
    text. Both are closed the same way :func:`is_usable_price` closes its own
    open domain: by making the answer closed rather than by guessing at the input.

    The exactness of the returned :class:`str` is load bearing and not
    fastidiousness. A :class:`str` subclass would carry its own ``__format__``
    into the very ``format`` call this value exists to feed, which would put the
    escape back one line later.
    """
    try:
        name = type(value).__name__
    except Exception:  # noqa: BLE001 - 一個保證，不是一份清單
        return _UNREADABLE_TYPE
    return name if type(name) is str else _UNREADABLE_TYPE


def _text_of(value):
    """Return ``value`` as text. **Total: an exact** ``str``, always.

    ``"{}".format(x)`` runs ``x``'s own ``__format__`` and usually then its
    ``__str__``. For an exception raised by an injected ``opener`` both are the
    caller's code, so quoting the message an error carries is the same open
    domain as reading the error at all.
    """
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - 一個保證，不是一份清單
        return _UNREADABLE_TEXT
    return text if type(text) is str else _UNREADABLE_TEXT


def _read_close(text, day, source, symbol):
    """Return ``(day the price printed, close)`` for the latest row up to ``day``.

    Rows dated after ``day`` are not near misses to fall back on — a price from
    after the deadline answers a different question — so they are skipped, and a
    response holding only those is refused naming the day that was wanted.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise QuoteUnavailableError(
            "{} 對 {} 沒有回應任何內容。".format(source.source_id, symbol)
        )
    header = [name.strip() for name in lines[0].split(",")]
    for column in (DATE_COLUMN, CLOSE_COLUMN):
        if column not in header:
            raise QuoteUnavailableError(
                "{} 對 {} 的回應沒有 {} 欄（表頭：{}）。".format(
                    source.source_id, symbol, column, lines[0][:120]
                )
            )
    date_at = header.index(DATE_COLUMN)
    close_at = header.index(CLOSE_COLUMN)
    best = None
    for line in lines[1:]:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) <= max(date_at, close_at):
            continue
        printed = _parse_day(fields[date_at], source, symbol)
        if printed > day:
            continue
        if best is None or printed > best[0]:
            best = (printed, fields[close_at])
    if best is None:
        raise QuoteUnavailableError(
            "{} 對 {} 沒有 {} 或之前的收盤價。".format(
                source.source_id, symbol, day.strftime(DAY_FORMAT)
            )
        )
    return best[0], _parse_close(best[1], source, symbol)


def _parse_day(value, source, symbol):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QuoteUnavailableError(
            "{} 對 {} 回應了無法解讀的日期 {!r}。".format(
                source.source_id, symbol, value
            )
        ) from exc


def _parse_close(value, source, symbol):
    """Return the close as a number, refusing everything that is not one.

    Three steps, in this order and no other:
    :func:`is_decimal_numeral` (**a text grammar, before the conversion**) →
    :func:`float` → :func:`is_usable_price` (**a numeric check, after it**).

    That is the path for **text** — this service's CSV field here, and the
    hand-typed form value in
    :func:`~hoya_market_agents.webapp.outcome._manual_price`. The third way a
    price reaches an ``outcome.json`` is not text and does not come through here
    at all: a ``close`` from an injected quote client is checked by
    :func:`is_usable_price` alone, which is why that one is total rather than
    merely correct about numbers.

    Only the first gate is before the conversion, and that is the one that has to
    be: it is what makes the type the input actually is still knowable.
    ``float(True)`` is ``1.0``, and a guard handed ``1.0`` cannot tell it from a
    price however careful it is — so the question "is this text spelling a
    number" must be asked while there is still a difference between ``True`` and
    ``1.0``. The second gate asks something a number is the right shape for
    ("finite and above zero"), so it belongs after ``float`` and could not be
    asked before it. Writing them as "both before the conversion" describes a
    program that does not exist and hides which of the two is load bearing.

    Zero and negatives are refused rather than stored: no market prints them,
    so they are a placeholder the service used for "no data", and treating one
    as a price would make every direction judged against it wrong *and*
    confident. ``inf``, ``Infinity`` and ``nan`` are refused for the same
    reason — an infinite close compares greater than every real price and would
    settle its run's direction outright.
    """
    if not is_decimal_numeral(value):
        raise QuoteUnavailableError(
            "{} 對 {} 回應的收盤價 {!r} 不是數字。".format(
                source.source_id, symbol, value
            )
        )
    # A vetted numeral always parses; the largest one only overflows to ``inf``,
    # which the price test below refuses.
    close = float(value.strip())
    if not is_usable_price(close):
        raise QuoteUnavailableError(
            "{} 對 {} 回應的收盤價是 {}，不是有效價格。".format(
                source.source_id, symbol, value
            )
        )
    return close


# What a price may be *written* as, stated as what is allowed rather than as
# what is not: an optional sign, decimal digits with an optional fractional
# part, and an optional decimal exponent. ``re.ASCII`` is load bearing — plain
# ``\d`` matches every Unicode decimal digit, and :func:`float` reads those too,
# so ``"١٢٣"`` would otherwise become ``123.0``.
_DECIMAL_NUMERAL = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII
)


def is_decimal_numeral(value):
    """Whether ``value`` is text spelling a plain decimal number.

    **Total: every value gets** ``True`` **or** ``False``, and nothing raises.
    ``isinstance(value, str)`` bounds ancestry, not behaviour: a :class:`str`
    subclass may define a ``strip`` that raises, or one that returns something
    :func:`re.Pattern.fullmatch` then rejects with :class:`TypeError`, and
    ``__class__`` may be a property, so even the type check is somebody else's
    code. That is the same open domain :func:`is_usable_price` argues about, and
    it is closed the same way — by making the *answer* closed rather than by
    guessing at the input — so see that function for the argument in full.

    The text gate, and **the only check any price passes before** :func:`float`.
    That is not an accident of ordering: it is a grammar over text, so it can be
    asked while the input is still text, and it is the last moment at which
    ``True`` is distinguishable from ``1.0``. See :func:`_parse_close` for the
    full order, and for which prices pass through here at all.

    It is the reason the rule is written as a positive list. **The two ways a
    price reaches an** ``outcome.json`` **as text** — a field of this service's
    CSV answer, and a form control typed by hand — are the ways that reach
    :func:`float`, so text is the only type that has any business reaching it,
    and saying so once refuses in one line every type a list of exclusions would
    have had to guess at: :class:`bool` (``float(True)`` is ``1.0``, which is how
    ``True`` became the price ``1.0``), :class:`~decimal.Decimal`,
    :class:`~fractions.Fraction`, a numpy scalar if one is ever introduced, and
    any object at all that defines ``__float__``.

    Within text the same rule applies to spelling. :func:`float` is not a
    numeral grammar: it also accepts ``"inf"``, ``"nan"``, ``"Infinity"``,
    digit-group underscores (``"1_0"`` is ten) and non-ASCII digits. Listing
    those would be the same losing game one level down, so the pattern says what
    a number looks like instead. Surrounding whitespace is allowed and stripped,
    because a CSV field arrives with it.

    **The injected** ``Quote.close`` **never passes through here**, and that is
    why :func:`is_usable_price` keeps its own type check rather than leaning on
    this one: a ``close`` handed to
    :func:`~hoya_market_agents.webapp.outcome._priced_payload` by an injected
    quote client was never text, so there is no text for this grammar to read.
    """
    try:
        return isinstance(value, str) and _DECIMAL_NUMERAL.fullmatch(value.strip()) is not None
    except Exception:  # noqa: BLE001 - 一個保證，不是一份清單
        return False


def is_usable_price(value):
    """Whether a number may be treated as a price at all.

    **Total: every value gets** ``True`` **or** ``False``, and nothing raises.
    One rule — finite and above zero — shared by every way a price reaches an
    ``outcome.json``: parsed out of this service's answer, typed by hand on the
    statistics page, and handed to
    :func:`~hoya_market_agents.webapp.outcome._priced_payload` by an injected
    quote client. Two spellings of it would be two chances to accept ``inf`` in
    one of them.

    **Why total has to be stated, and why a type list cannot deliver it.** This
    is the one gate the injected ``close`` passes, and that value is whatever the
    caller's object was — an open domain. ``isinstance(value, (int, float))``
    reads like it closes that domain and does not: it is a question about
    ancestry, not about behaviour. ``10**1000`` is an ordinary :class:`int` and
    :func:`math.isfinite` raises :class:`OverflowError` converting it; an
    :class:`int` subclass may define ``__gt__``, ``__float__`` or ``__index__``
    that raise anything at all; ``__class__`` may be a property, so even
    :func:`isinstance` is somebody else's code. Enumerating those is the losing
    game this module keeps naming — see :func:`_fetch` and
    :func:`is_decimal_numeral`.

    So totality is not claimed by listing what may arrive. It is obtained by
    closing the **answer**: the whole body is attempted, and any
    :class:`Exception` from any of it means the one thing it could mean here —
    this is not a value that may be treated as a price. That converts an open set
    of exception types into the closed set the signature already promised, which
    is the same move :func:`_fetch` makes for the transport. Every
    :class:`BaseException` that is not an :class:`Exception` still travels —
    the set, not a list of two of its members, for the reason :func:`_fetch`
    gives: those are somebody stopping the program, not a bad price.

    **This gate raising is not a local matter.** Its caller
    :func:`~hoya_market_agents.webapp.outcome._priced_payload` asks it *outside*
    the ``except`` that isolates one run's quote failure, so an exception from
    here ended the whole sweep before its cursor was written and every later pass
    began at the same run — the starvation the cursor exists to prevent, entered
    through the guard added to prevent a different one.

    Its own type check is not redundant with :func:`is_decimal_numeral`: that one
    never sees the injected ``close``, so :class:`bool` must not slip past
    ``> 0`` here — :class:`bool` is a subclass of :class:`int` and ``True`` would
    otherwise read as the price ``1``.

    **Total is not the same as unfoolable, and the difference is stated rather
    than left to be discovered.** A subclass that raises nothing but lies —
    ``__gt__`` returning a truthy non-boolean — is answered ``True``, so a caller
    handing over an object built to be believed can still get a number of its
    choosing past this gate. That is not a hole this predicate can close: it is
    given the object, and every question it could ask is answered by that object.
    What it guarantees is the part that is its to guarantee — an answer, always,
    of the right type — so that no caller's object can stop the sweep. Being lied
    to by one is the caller's claim, which is what
    :func:`~hoya_market_agents.webapp.outcome._priced_payload` means by "a seam is
    a claim about the caller".
    """
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        # ``bool(...)`` is load bearing: ``value > 0`` on a subclass returns
        # whatever that subclass chose, and this function promises a bool.
        return bool(math.isfinite(value) and value > 0)
    except Exception:  # noqa: BLE001 - 一個保證，不是一份清單
        return False


def _summarise(text):
    """Return a bounded excerpt of the raw answer for the outcome record."""
    collapsed = " / ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(collapsed) <= Quote.MAX_SUMMARY_CHARS:
        return collapsed
    return collapsed[: Quote.MAX_SUMMARY_CHARS - 1] + "…"
