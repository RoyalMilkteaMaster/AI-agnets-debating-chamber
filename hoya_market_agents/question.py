"""Intake gate for run questions.

Every question is accepted. The gate holds no approved-asset whitelist: it
reads the question, names whatever analysable targets it can see, labels the
question with one asset class, and hands the reading on. A question it cannot
place in any market is still a legitimate run — it becomes an open proposition,
never a refusal.

Two refusals survive, and neither is about scope: empty text, and a period the
question states but the gate cannot parse. Both mean the run itself would be
undefined.

Naming a target without a whitelist is a judgement call. There is one
identifier grammar — alphanumerics plus an optional share class and exchange
suffix, of any length — and what changes between readers is not the shape but
how strongly the sentence licenses reading it as a target:

* a 4–6 digit code (``2330``, ``2330.TW``) is a Taiwan listing, unless a unit
  such as 年/日/元 follows it, or it sits inside a date, a price or a count;
* a ``$`` cashtag (``$F``, ``$1INCH``, ``$brk-b``) says "this is a ticker"
  outright, so nothing about the identifier has to be inferred — casing does not
  matter, because the ``$`` is the evidence. The whole ``$`` run is read as one
  token and claimed as one token, whichever way it is read, so no later reader
  can reach a fragment of it: ``$BRK_B`` is not a legal identifier and names
  nothing, rather than leaking the ``BRK`` its prefix happens to spell;
* both sides of a comparison (``比較 $F 與 NVDA``) are named by the comparison
  itself, and likewise get the full grammar;
* with none of that, the reader falls back to the conventional shape: ALL CAPS,
  2–6 letters. That is a deliberate floor — with no marker, shape is the only
  evidence there is;
* the five coins in :data:`LEGACY_CRYPTO_SYMBOLS` are always recognised, in any
  case and with no market word at all.

Nothing else names a target. A Chinese demonstrative used to: ``1INCH 這個幣``
named 1INCH for four rounds. It is gone, because deciding what 「這個…」 points
at needs word segmentation, and every rule tried in its place — phrase
terminators, a list of non-asset compounds, an identifier shape gate, then
refusing to look past the word directly after it — leaked in one direction or
the other. Two reviewers found forty misreadings against the last of them. Write
``$1INCH`` or ``1INCH`` instead.

The two mistakes do not cost the same, which is why every rule here fails
towards naming nothing. An invented asset becomes the run's slug and the
evidence gateway's allow-list, so it makes the gateway *reject* the seats' real
research — silently. Naming nothing is visible either way: the cold-start path
opens a proposition and runs, and the fake-provider path refuses the question
outright with its own message. So when the reading is undecidable this module
declines to answer, and the market words still classify the question.

The class is then read off the question's own market words, minus every mention
of the targets themselves — ``COIN`` is a real US listing and must not be the
reason a question is called a crypto question. A longer term outranks a shorter
one only where the two cover the same text (``台灣股票`` speaks for the ``股票``
inside it); terms sitting apart are never compared by length, because character
count is not semantic specificity. A question the gate cannot place in any
market is ``open`` — including one that clearly names a target. Guessing a
market for a bare ticker would only feed the wrong market semantics downstream;
naming the target and admitting the market is unknown is the honest reading, and
is what architecture §11.4 asks for.

Two spellings of one question must reach one answer, so the readers work from
a *width-folded* view of the text: ``＄ＮＶＤＡ`` and ``$NVDA`` are the same
question typed on different keyboards. Only Unicode's ``<wide>``/``<narrow>``
decomposition tags are folded, because those mean "the same character at a
different width"; a circled ①, a superscript ², a Roman ⅩⅤ and a mathematical
𝐀 are *different characters* that merely fold onto ASCII, and treating them as
the same invents targets nobody wrote. Digit classes are ASCII for the same
reason — ``\\d`` would accept 𝟐𝟑𝟑𝟎 and ٢٣٣٠ as Taiwan listing codes.
:class:`QuestionScope` carries that view as ``reading`` so that everything
downstream decides from it, while ``question`` stays the user's own text for
prompts and artifacts to quote.

A caller that already knows the run's subject should say so instead of letting
the text be re-guessed. :func:`inspect_question`, ``build_question_package``
and ``launcher.run_launch`` all take ``assets`` and ``asset_class``; where they
are given, the reading below has no say in them — nothing is added, nothing is
dropped, nothing is rewritten beyond :func:`normalize_asset`. Shape, container
and size are still checked and still fail closed: the container must be a list
or a tuple, because the targets become the run id in the order they arrive and
a ``set`` would name two different directories for one input; one target may
not be written twice; and the resulting slug must fit the byte budget a
directory name leaves it. There is deliberately no CLI flag: ``cli.py`` is
outside this ticket's scope, so the seam is in-process only, which is what a
menu-driven front end calling ``run_launch`` needs and what a shell caller
cannot yet reach.

What a caller that does *not* state a subject may rely on, stated as a contract
rather than left to be inferred from the tests:

* the *symbol decides the target*. Whatever the question is about, the targets
  are the identifiers it actually writes down — the gate never supplies one it
  was not given;
* the *question's wording decides the market*. ``asset_class`` is read from the
  market words around the targets, never from the targets themselves;
* a *bare symbol gets no market guessed for it*. ``分析 DOGE 過去 14 日市場狀態``
  names DOGE and is classed ``open``, because nothing in it says which market
  DOGE trades in. That is the intended reading, not a gap;
* the five symbols in :data:`LEGACY_CRYPTO_SYMBOLS` are the one exception, and
  they are compatibility debt rather than a rule: they are recognised, and they
  classify as crypto, only so that every question that worked in the five-coin
  era keeps working.

Two constants are compatibility aids, and neither can refuse a question:
:data:`NON_ASSET_TOKENS` keeps well-known acronyms out of the asset list (an
acronym missing from it merely rides along), and :data:`LEGACY_CRYPTO_SYMBOLS`
guarantees that every question that worked in the five-coin era still works (a
symbol missing from it still goes through the ordinary rules above).

Every character-count threshold in this module describes the shape of the thing
it matches — a Taiwan listing code is 4–6 digits, a share class is one letter, a
Chinese numeral is 1–3 characters, an analysis period is 1–3 digits, a date is
4 digits and 1–2 digits. Two do not describe a shape, and both are named here
rather than left to be discovered: ``_BARE_TICKER``'s 2–6, justified above
because with no licence the conventional ticker shape is the only evidence
available; and ``_PERIOD_HINT_PATTERN``'s 0–8 window, which is inherited from
before this ticket and is a guess, as its own comment says. Apart from those
two, no threshold in this module stands in for a meaning.

What this module cannot decide, and does not pretend to:

* a lower-case token beside a market word (``value stock`` / ``nvda stock``) —
  both name nothing, so no wrong asset is ever bound;
* a lower-case token anywhere else (``doge 未來如何``) — ``$doge`` names it;
* anything a Chinese demonstrative points at. That whole path is gone, so
  ``F 這檔美股`` and ``1INCH 這個幣`` name nothing at all; ``$F`` and ``$1INCH``
  do;
* ``$2330``. ``$`` followed by digits alone cannot be told from ``$10000``, so
  it reads as an amount. The bare ``2330`` spelling is what names a Taiwan
  listing, and it is the ordinary one. A known exchange suffix settles it the
  other way — ``$2330.TW`` is a listing, and the price of that is that
  ``$10000-TW`` names 10000;
* whether a counting verb earlier in a sentence governs a later number. This
  one is worth stating plainly, because it costs real questions:

      「台積電回購 50000 股是否有利未來股價？」 => ``('50000',)``, ``tw_stock``

  Buyback, rights-issue and capital-action questions are standard market
  research, and this module reads the share count as a Taiwan listing code. The
  run then binds to a listing that does not exist, and the evidence gateway
  rejects every card about 2330 — the very evidence the question wanted. The
  same happens to 「2330 回購 50000 股…」 (``('2330', '50000')``) and 「分析 NVDA
  回購 50000 股…」 (``('NVDA', '50000')``).

  No syntactic rule separates this from 「公司有高達 50000 股東」, which really
  is a count: both are a counting verb, some material, a number and a measure
  word. Four attempts at a vocabulary for the material in between all leaked,
  and the rule now in place — the material may not contain a measure-phrase
  head — draws the line in a defensible place without claiming to draw it
  correctly. Callers that state ``assets`` are not affected by any of this,
  which is the intended answer rather than a workaround;
* which market a question is about when it names two (``比較 nvda stock 與
  2330 台股`` is filed under one class). ``asset_class`` holds a single value;
* a ticker spelled like a currency code (``TOP``, ``ALL``, ``XAU``, ``XAG``)
  standing right beside a number. ``分析 TOP 過去 14 日市場狀態`` names TOP, but
  ``TOP 50 日均線是否轉強`` names nothing, because ``TOP 50`` is also how a price
  is written. That direction is a safe miss — with no targets the gateway binds
  the run only, and a TOP card is still accepted — so it is left as it is.
"""

import re
import unicodedata
from dataclasses import dataclass

DEFAULT_PERIOD_DAYS = 14

ASSET_CLASS_CRYPTO = "crypto"
ASSET_CLASS_TW_STOCK = "tw_stock"
ASSET_CLASS_US_STOCK = "us_stock"
ASSET_CLASS_OPEN = "open"
ASSET_CLASSES = (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASS_OPEN,
)

# The slug a question with no named target gets, so a run id always has a
# middle segment.
OVERALL_MARKET_SLUG = "overall-market"
# What an evidence card says it is about when the question named no target and
# the card cannot be pinned to one either.
OVERALL_MARKET_ASSET = "OVERALL-MARKET"

# Ticker-shaped acronyms that are never the subject of an analysis. Missing an
# entry is harmless: the acronym simply joins the asset list. The market words
# themselves are added to :data:`NON_ASSET_TOKENS` further down.
_ACRONYMS_THAT_ARE_NOT_TARGETS = frozenset(
    {
        "AI", "AM", "API", "BOE", "BOJ", "CEO", "CFO", "COO", "CPI", "ECB",
        "EPS", "ESG", "ETC", "ETF", "ETFS", "EU", "EUR", "FAQ", "FED", "FOMC",
        "GDP", "GMT", "IMF", "IPO", "ISM", "JPY", "KOL", "NATO", "NFP", "NFT",
        "OECD", "OK", "OPEC", "PBOC", "PBR", "PCE", "PER", "PMI", "PPI", "QE",
        "QT", "RMB", "ROE", "SEC", "TVL", "TWD", "UK", "URL", "US", "USA",
        "USD", "UTC", "VS", "WHO", "WTO",
    }
)

# The five symbols this product shipped with. They are recognised in any case
# and with no market word at all, so no question that worked in the whitelist
# era stops working. This is not a whitelist: a symbol outside it is still
# accepted, it just goes through the ordinary rules.
LEGACY_CRYPTO_SYMBOLS = ("BTC", "ETH", "SOL", "BNB", "XRP")

# ---------------------------------------------------------------------------
# One asset-token grammar.
#
# A written target is a body plus decoration: an optional single-letter share
# class, then any number of exchange suffixes. Both are told apart by what they
# are, not by which separator precedes them, so ``BRK-B.US`` and ``BRK.B-US``
# are the same B share of the same company. Every reader below — the three
# intake patterns and :func:`normalize_asset` — is built from these same
# fragments, so nothing can recognise a suffix that another reader would leave
# behind as a target of its own.
#
# Trailing ``-TW``/``-TWO``/``-US``/``-HK`` are therefore reserved syntax: a
# body that genuinely ends that way cannot be told from a suffix by text alone.
# ---------------------------------------------------------------------------
_EXCHANGE_SUFFIXES = ("TWO", "TW", "US", "HK")  # longest first: TWO before TW
_SUFFIX_SEPARATOR = r"[.\-]"
_EXCHANGE_SUFFIX = r"{}(?:{})".format(_SUFFIX_SEPARATOR, "|".join(_EXCHANGE_SUFFIXES))
_EXCHANGE_SUFFIXES_RE = r"(?:{})*".format(_EXCHANGE_SUFFIX)
# A share class is one letter — that is what a share class is, not a cap chosen
# to keep prose out.
_SHARE_CLASS_RE = r"(?:{}[A-Za-z](?![A-Za-z0-9]))?".format(_SUFFIX_SEPARATOR)
# A numeric listing code has no share class; an alphabetic ticker may have one.
_CODE_SUFFIXES_RE = _EXCHANGE_SUFFIXES_RE
_TICKER_SUFFIXES_RE = _SHARE_CLASS_RE + _EXCHANGE_SUFFIXES_RE

_EXCHANGE_SUFFIX_PATTERN = re.compile(r"{}$".format(_EXCHANGE_SUFFIX))
_SHARE_CLASS_PATTERN = re.compile(r"^(.*[A-Z0-9]){}([A-Z])$".format(_SUFFIX_SEPARATOR))

# A calendar date is not a listing code, whichever way round it is written. The
# digit counts here are the shape of a date: four for a year, one or two for a
# month or a day.
_DATE_PATTERN = re.compile(
    r"(?<![0-9])(?:"
    r"[0-9]{4}\s*[-/]\s*[0-9]{1,2}(?![0-9])(?:\s*[-/]\s*[0-9]{1,2}(?![0-9]))?"
    r"|[0-9]{1,2}(?![0-9])\s*[-/]\s*[0-9]{1,2}(?![0-9])\s*[-/]\s*[0-9]{4}(?![0-9])"
    r")"
)
# Spans that belong to something other than a target, and are therefore closed
# to every reader — not just the numeric one. Money is read whole, because
# ``NT$10000`` and ``CNY 10000`` hide a ticker-shaped ``NT``/``CNY`` next to the
# number; blocking only the digits left the currency behind as an asset.
# A quantity needs evidence on *both* sides — a counting verb in front and a
# unit behind — so 「公司有 50000 股東」 is 50000 shareholders while
# 「2330 股東會是否配息」 is still the listing 2330.
#
# Currency is spelled three ways, and each one is taken from an authority
# rather than from a hand-picked sample:
#
#   * the *sign* is any character Unicode files under the ``Sc`` (Symbol,
#     currency) category, so ``¥`` (U+00A5) and ``￥`` (U+FFE5) and ``₩`` and
#     ``₹`` all count without anyone having to remember them;
#   * the *code* is the complete ISO 4217 alphabetic set, matched as written —
#     the standard spells its codes in upper case, and demanding that is what
#     keeps English words that happen to be codes (``ALL``, ``TOP``, ``CUP``,
#     ``SEK``) from reading as money next to a number. The handful of codes
#     already supported before the standard set arrived stay case-insensitive,
#     so every spelling that worked keeps working;
#   * three codes people really write are not in the standard at all —
#     ``USDT``, ``RMB``, ``NTD`` — and are listed as exactly that.
_ISO_4217_CODES = (
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND "
    "BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU "
    "CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP "
    "GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES "
    "KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD "
    "MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR "
    "PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD "
    "SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS "
    "UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB "
    "XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG"
).split()
# Written every day, absent from the standard.
_NON_STANDARD_CURRENCY_CODES = ("USDT", "RMB", "NTD")
# The set that was already read case-insensitively; kept so no spelling that
# used to be money stops being money.
_CASE_INSENSITIVE_CURRENCY_CODES = (
    "USD TWD NTD CNY JPY EUR GBP HKD USDT RMB AUD CAD CHF SGD KRW NZD SEK NOK "
    "DKK THB MYR PHP IDR VND INR BRL MXN ZAR TRY RUB PLN CZK HUF ILS AED SAR"
).split()
_CURRENCY_CODES = "{}|(?i:{})".format(
    "|".join(sorted(set(_ISO_4217_CODES) | set(_NON_STANDARD_CURRENCY_CODES))),
    "|".join(sorted(set(_CASE_INSENSITIVE_CURRENCY_CODES))),
)
# The two decomposition tags that mean "the same character, set at a different
# width". Every other tag means a different character — see _unicode_tables.
_WIDTH_VARIANT_TAGS = ("<wide>", "<narrow>")


def _unicode_tables():
    """One pass over the whole of Unicode, three answers.

    Asking Unicode beats keeping a list: the five signs that used to be
    hand-written here silently missed ``¥`` (U+00A5) while carrying its
    full-width twin, and punctuation is far too large to enumerate at all. The
    range is the whole code space, not the basic plane — Tamil, Wancho and
    Indic Siyaq all file currency signs above U+FFFF, and a sign the gate
    cannot see turns the amount beside it into a listing code.

    The third answer is the width fold. A Chinese IME writes ``＿－／：＄``
    where an ASCII keyboard writes ``_-/:$``, and a reader that knows only the
    ASCII spelling lets ``$BRK＿B`` through as the fragment ``BRK``.

    Two conditions decide what may be folded, and both are load-bearing:

    * the mapping is one character to one character, so folding never moves an
      offset and a span claimed in the folded reading claims the very same text
      in the question as it was written;
    * Unicode's own decomposition tag is ``<wide>`` or ``<narrow>``. That is
      the tag for a *typographic width variant* — the same character, set at a
      different width — which is exactly the class of spelling a Chinese IME
      produces.

    Every other tag is a different character wearing a familiar shape, and
    folding those invents targets the question never mentioned: ``<circle>``
    turns 「選項 ①②③④ 哪個較好？」 into the Taiwan listing 1234, ``<super>``
    and ``<sub>`` turn a measurement 「²³³⁰」 into 2330, ``<compat>`` turns a
    chapter number 「第ⅩⅤⅠⅠ章」 into the ticker XVII, and ``<font>`` does the
    same for every mathematical alphabet. Length was never the property that
    made folding safe — sameness of character was.
    """
    currency_signs = []
    punctuation = []
    fold = {}
    for code_point in range(0x110000):
        character = chr(code_point)
        category = unicodedata.category(character)
        if category == "Sc":
            currency_signs.append(character)
        elif category.startswith("P"):
            punctuation.append(character)
        decomposition = unicodedata.decomposition(character)
        if decomposition.split(" ")[0] not in _WIDTH_VARIANT_TAGS:
            continue
        folded = unicodedata.normalize("NFKC", character)
        if len(folded) == 1 and folded != character:
            fold[code_point] = folded
    return "".join(currency_signs), "".join(punctuation), fold


_CURRENCY_SIGNS, _PUNCTUATION, _WIDTH_FOLD = _unicode_tables()
# ``$`` is left out of the sign set on purpose: it is the one sign that is also
# the cashtag marker, and :func:`_dollar_tokens` owns every one of them.
_CURRENCY_SIGNS = _CURRENCY_SIGNS.replace("$", "")
_CURRENCY_SIGN_RE = "[" + re.escape(_CURRENCY_SIGNS) + "]"
# ``NT$``/``US$``/``R$`` are sign prefixes that are not codes in their own
# right; the codes are prefixes too, which is what makes ``AUD$10000`` money.
_SIGN_PREFIXES = ("NT", "US", "HK", "AU", "NZ", "CA", "SG", "CN", "RMB", "R")
_CURRENCY_PREFIXES = "(?i:{})|{}".format("|".join(_SIGN_PREFIXES), _CURRENCY_CODES)
_MONEY_PATTERN = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:" + _CURRENCY_PREFIXES + r")\s*" + _CURRENCY_SIGN_RE
    + r"|" + _CURRENCY_SIGN_RE + r")"
    r"\s*[0-9][0-9,.]*\s*(?:" + _CURRENCY_CODES + r")?(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])(?:" + _CURRENCY_CODES + r")\s*[0-9][0-9,.]*"
    r"|[0-9][0-9,.]*\s*(?:" + _CURRENCY_CODES + r")(?![A-Za-z0-9])"
)
# The same closed set, read at two strictnesses, because the two verdicts do
# not cost the same. *Confirming* money demands the standard's own upper-case
# spelling, or English words that happen to be codes (``all``, ``top``, ``cup``)
# would turn 「top 10000」 into a price. *Excluding* a number from the listing
# reader asks only that the word beside it be code-shaped in any case: reading
# 「bdt 10000」 as the Taiwan listing 10000 binds the gateway to an asset that
# does not exist and rejects the evidence the question really wanted, which is
# far worse than declining to name a number nobody asked to be named.
_CURRENCY_LOOKALIKE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?i:" + _CURRENCY_CODES + r")\s*[0-9][0-9,.]*(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])[0-9][0-9,.]*\s*(?i:" + _CURRENCY_CODES + r")(?![A-Za-z0-9])"
)
# A count is a shape, not a vocabulary: a counting verb, then whatever modifies
# the number, then the number, then its unit. The modifier slot is *any*
# uninterrupted run of characters that are none of digits, space or
# punctuation — 高達, 足足, 約莫, 近乎, 不下 and everything else anyone writes
# there — because the set of degree words is open and enumerating it is how the
# previous three attempts failed. Evidence is still needed on both sides: the
# verb in front and the unit behind are what separate 「公司有高達 50000 股東」
# from 「2330 股東會是否配息」, which has no counting verb at all.
_COUNTING_VERBS = (
    "成交量", "持股", "共有", "持有", "買進", "賣出", "成交", "發行", "流通",
    "擁有", "有",
)
_COUNT_UNITS = (
    "股東?", "股票", "股數", "張", "口", "手", "筆", "份", "單位", "人", "位", "名",
)
# What may sit between the verb and its number is anything *except* the head of
# a measure phrase. A run carrying one of those characters is not modifying the
# number behind it — it already has a counted noun of its own, so the verb
# belongs to that one and not to this number. That is the whole difference
# between 「公司有高達 50000 股東」, where nothing stands between 有 and the
# count, and 「我持有台股想知道 2330 股價會不會漲」, where 台股 does. The
# excluded set is the units' own first characters, not a second vocabulary that
# would have to be kept in step with them.
_UNIT_HEADS = "".join(sorted({unit[0] for unit in _COUNT_UNITS}))
_QUANTITY_PATTERN = re.compile(
    r"(?:" + "|".join(_COUNTING_VERBS) + r")\s*"
    r"[^0-9\s" + re.escape(_PUNCTUATION + _UNIT_HEADS) + r"]*?\s*"
    r"[0-9]{1,9}\s*(?:" + "|".join(_COUNT_UNITS) + r")"
)
# 4–6 digits is the format of a Taiwan listing code, not a guess about length.
# It is only a code when nothing else claims the number: a unit behind it — 元,
# 張, 筆, USD — makes it a quantity instead.
_TW_CODE_PATTERN = re.compile(
    # A currency mark in front makes it a price, not a listing.
    r"(?<![A-Za-z0-9.$" + re.escape(_CURRENCY_SIGNS) + r"])([0-9]{4,6}"
    + _CODE_SUFFIXES_RE + r")"
    r"(?!\s*(?i:usd|twd|ntd|usdt))"
    r"(?!\s*(?:年|月|日|天|週|周|小時|分鐘|分|秒|％|%|美元|美金|元|點|萬|億"
    r"|張|口|手|次|筆|份|單位|人|位|名|倍|檔(?!股)))"
    r"(?![A-Za-z0-9.])"
)
# One identifier grammar. A written target is alphanumerics plus the decoration
# above; there is no length limit, because a limit would be a bad proxy for "is
# this a ticker" — the discipline comes from what licenses the reading, not from
# counting characters. Where nothing in the sentence licenses it, the reader
# falls back to the conventional shape instead: all caps, 2–6 letters.
_IDENTIFIER = r"[A-Za-z0-9]+" + _TICKER_SUFFIXES_RE
_BARE_TICKER = r"[A-Z]{2,6}" + _TICKER_SUFFIXES_RE
# ``$AAPL`` says "this is a ticker" outright, so nothing about the identifier's
# own shape has to be inferred: ``$doge`` names DOGE where a bare ``doge`` does
# not.
#
# ``$`` is the one character that is both a cashtag marker and a currency sign,
# so the run it introduces is read as **one lexical token** and settled before
# any other reader sees the text. Whatever the verdict, the whole run is spoken
# for, which is what makes the fragments unreachable: ``$BRK_B`` is not a legal
# identifier, and it names nothing rather than leaking the ``BRK`` its prefix
# happens to spell.
_DOLLAR_TOKEN_BODY = r"[A-Za-z0-9._,:/-]*"
_DOLLAR_TOKEN_PATTERN = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:" + _CURRENCY_PREFIXES + r")\s*\$|\$)"
    r"(\s*)(" + _DOLLAR_TOKEN_BODY + r")"
)
# Only what ends a clause is given back to the sentence: ``$2330, $2454`` is
# two tokens and ``$AAPL.`` ends one. A trailing ``-``/``_``/``/`` is not
# punctuation, so it stays inside the token and makes it unreadable — which is
# the safe verdict, not a reason to trim until something parses.
_DOLLAR_TOKEN_TAIL = ".,:"
_IDENTIFIER_ONLY = re.compile(r"\A" + _IDENTIFIER + r"\Z")
# The same grammar, applied to text that has not been through
# :func:`normalize_asset` yet — so an exchange suffix may still be lower case.
# ``re.ASCII`` is what makes ``re.IGNORECASE`` mean ASCII case and nothing
# else: without it ``[A-Za-z]`` also matches ``ſ`` (U+017F) and ``K`` (U+212A),
# which is precisely the class of character this check exists to refuse.
_RAW_IDENTIFIER_ONLY = re.compile(
    r"\A" + _IDENTIFIER + r"\Z", re.IGNORECASE | re.ASCII
)
_AMOUNT_ONLY = re.compile(r"\A[0-9][0-9,.]*(?:" + _CURRENCY_CODES + r")?\Z")
_TICKER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(" + _BARE_TICKER + r")(?![A-Za-z0-9])"
)
_LEGACY_SYMBOL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])({})(?![A-Za-z0-9])".format("|".join(LEGACY_CRYPTO_SYMBOLS)),
    re.IGNORECASE,
)
_COMPARISON_PAIR_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(" + _IDENTIFIER + r")(?![A-Za-z0-9])\s*"
    r"(?:與|和|跟|vs\.?|[/、,])\s*"
    r"(?<![A-Za-z0-9.])(" + _IDENTIFIER + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# One market-word vocabulary, two readings of it.
#
# :data:`MARKET_WORDS_BY_CLASS` is the single source, so a word cannot mean one
# thing to one consumer and something else to the other:
#
# * classify — does the question mention this market at all? Matched anywhere in
#   the text, but as a whole word: ``defi`` must not fire on ``definite``.
# * exclude — the word itself is not the subject. Applies to a token no reader
#   named outright; a question that writes ``TOKEN`` as a ticker means that
#   ticker.
#
# A third reading — promoting the token in front of a market word — existed
# until round 10 and is gone: it could not tell ``nvda stock`` from
# ``value stock``, and naming the wrong asset is worse than naming none.
#
# Entries are complete words. An ASCII word matches with a boundary on both
# sides and an optional plural ``s`` — nothing else — so ``stock`` is not found
# inside ``livestock`` any more than inside ``stockholder``. Compounds that
# really are market terms (``bitcoin``, ``altcoin``, ``cryptocurrency``,
# ``tokenomics``) are listed in their own right rather than reached by
# substring. Chinese has no boundary to assert, so those entries must be
# complete terms for the same reason: a bare ``幣`` would swallow 台幣、人民幣、
# 貨幣政策, and a bare ``加密`` would swallow 資料加密.
# ---------------------------------------------------------------------------
MARKET_WORDS_BY_CLASS = (
    (
        ASSET_CLASS_TW_STOCK,
        (
            "台股", "臺股", "台灣股市", "臺灣股市", "台灣股票", "臺灣股票",
            "上市櫃", "上櫃", "興櫃", "證交所", "櫃買", "台灣證券",
            "加權指數", "台積電", ".tw", ".two",
        ),
    ),
    (
        ASSET_CLASS_US_STOCK,
        (
            "美股", "美國股市", "那斯達克", "納斯達克", "道瓊", "標普", "財報",
            "個股", "股價", "股票", "nasdaq", "nyse", "s&p", "stock", "shares",
        ),
    ),
    (
        ASSET_CLASS_CRYPTO,
        (
            # 中文沒有 token 邊界，所以列的是完整詞：``幣`` 一個字會把台幣、
            # 人民幣、貨幣政策全部吃進來，``加密`` 會吃掉資料加密。
            "加密貨幣", "虛擬貨幣", "數位貨幣", "數字貨幣", "加密市場",
            "虛擬幣", "加密幣", "數位幣", "幣圈", "幣價", "幣種", "代幣",
            "比特幣", "以太幣", "狗狗幣", "鏈上", "區塊鏈",
            "crypto", "cryptocurrency", "cryptocurrencies", "coin", "bitcoin",
            "litecoin", "dogecoin", "altcoin", "stablecoin", "token",
            "tokenomics", "defi",
        ),
    ),
)
# Words that name an analysis but not a market, and the period words that follow
# a target just as naturally. They are excluded from being targets themselves,
# but they say nothing about which market the question is in.
_NEUTRAL_MARKET_WORDS = (
    "市場狀態", "市場位置", "市場", "價格走勢", "價格", "走勢", "漲跌",
    "升級事件", "升級", "事件", "過去", "未來", "近期", "最近",
)
_ALL_MARKET_WORDS = (
    tuple(word for _, words in MARKET_WORDS_BY_CLASS for word in words)
    + _NEUTRAL_MARKET_WORDS
)


def _whole_word(word):
    """One market word, matchable only as a complete word (plural allowed).

    ASCII words get boundaries on both sides, so ``stock`` cannot be found
    inside ``livestock`` any more than inside ``stockholder``. Compounds that
    really are market terms — ``bitcoin``, ``altcoin`` — are listed in the
    vocabulary instead of being reached by substring. Chinese has no token
    boundary to assert, which is why those entries are complete words already.
    """
    if not (word.isascii() and word.isalpha()):
        return re.escape(word)
    return r"(?<![A-Za-z0-9])" + re.escape(word) + r"s?(?![A-Za-z0-9])"


def _any_of(words):
    """Longest alternative first, so 幣價 never loses to 幣, altcoin never to coin."""
    return r"(?:{})".format(
        "|".join(_whole_word(word) for word in sorted(words, key=len, reverse=True))
    )


_CLASS_MATCHERS = tuple(
    (asset_class, re.compile(_any_of(words)))
    for asset_class, words in MARKET_WORDS_BY_CLASS
)
# A market word never names the subject on its own — but only "on its own": a
# ticker written outright outranks this, so ``$TOKEN`` and a bare ``TOKEN`` both
# still mean that ticker. Both spellings the matcher accepts are excluded, or the
# plural would slip through and become a target of its own.
_MARKET_WORD_TOKENS = frozenset(
    form
    for word in _ALL_MARKET_WORDS
    if word.isascii() and word.isalpha()
    for form in (word.upper(), word.upper() + "S")
)
NON_ASSET_TOKENS = _ACRONYMS_THAT_ARE_NOT_TARGETS | _MARKET_WORD_TOKENS
_DEMONSTRATIVE = r"[這那][個檔支張種隻項]"
_DEMONSTRATIVE_PATTERN = re.compile(_DEMONSTRATIVE)
_COMPARISON_MARKERS = ("比較", "相對", "強弱", "市場位置", "誰較強", "vs")

_SLUG_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")

# 1–3 digits is the magnitude of an analysis period; a four-digit day count is
# a different kind of number, not a longer period.
_PERIOD_PATTERN = re.compile(r"([0-9]{1,3})\s*(?:日|天|days?)", re.IGNORECASE)
_WEEK_PERIOD_PATTERN = re.compile(r"([0-9]{1,3})\s*(?:週|周|weeks?)", re.IGNORECASE)
# 1–3 characters is the written length of a Chinese numeral up to 九十九.
_CHINESE_NUMERAL = r"(?<![一二兩三四五六七八九十百])([一二兩三四五六七八九十]{1,3})(?![一二兩三四五六七八九十百])"
_CHINESE_WEEK_PERIOD_PATTERN = re.compile(_CHINESE_NUMERAL + r"\s*(?:週|周)")
_CHINESE_DAY_PERIOD_PATTERN = re.compile(_CHINESE_NUMERAL + r"\s*(?:日|天)")
# Inherited from before the whitelist came out. The 0–8 window is not a rule
# about anything — it is a guess at how far a period phrase reaches, and where
# it guesses wrong the question silently keeps the default period instead of
# failing closed. Left as found; a real fix needs the period parser reworked,
# which is not this module's contract.
_PERIOD_HINT_PATTERN = re.compile(
    r"(?:過去|最近|近)\s*[^\s，。！？,;；]{0,8}?(?:日|天|週|周|星期|月|年)"
)
_CHINESE_DIGITS = {
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
}


class UnsupportedQuestionError(ValueError):
    """Raised when a question cannot describe a run at all."""


class UnknownAssetError(UnsupportedQuestionError):
    """A stated target that cannot describe a run.

    The name is inherited from the whitelist era, and it survives so that
    callers which still catch it keep compiling and keep behaving. What it
    means has changed: no asset is unapproved any more, and *reading* a
    question never raises this. It is raised only on the stated-subject seam —
    see :func:`_stated_assets` — when a caller hands over a container that has
    no stable order, a target that is not identifier-shaped, one target written
    twice, or more targets than a run directory name can hold.
    """


@dataclass(frozen=True)
class QuestionScope:
    """The gate's reading of a user question.

    ``question`` is the text exactly as it was written, and stays that way:
    artifacts and prompts must quote the user, not a normalisation of them.
    ``reading`` is the same text with width variants folded — the view every
    reader here worked from — and it is carried so that whatever else has to
    ask a question about the wording asks it of the same view. Two spellings
    of one question must not reach two different answers, and before this was
    carried, ``ＢＴＣ ｖｓ ＥＴＨ`` found both targets and still failed to be a
    comparison.
    """

    question: str
    assets: tuple
    period_days: int
    period_stated: bool
    asset_class: str = ASSET_CLASS_OPEN
    reading: str = ""

    def __post_init__(self):
        if not self.reading:
            object.__setattr__(self, "reading", _fold_width_variants(self.question))

    @property
    def asset_slug(self):
        return asset_slug_for(self.assets)


def normalize_asset(value):
    """Return the one canonical spelling of a target.

    Upper case, ``BRK-B`` and ``BRK.B`` are the same share class, and an
    exchange suffix is decoration: ``2330.TW`` and ``2330`` are one listing.
    Intake, the run id slug and the evidence gateway all compare through here,
    so a seat that spells a target differently from the question still matches.

    The two suffixes are stripped in a fixed order — every exchange suffix
    first, then the share class — so that a spelling carrying both, in either
    separator style, still lands on one key. The result carries neither, which
    is what makes this idempotent.
    """
    text = _without_exchange_suffixes(str(value).strip().upper())
    share_class = _SHARE_CLASS_PATTERN.match(text)
    if share_class:
        return "{}.{}".format(*share_class.groups())
    return text


def _without_exchange_suffixes(text):
    """Drop trailing exchange suffixes until the value carries none."""
    shorter = _EXCHANGE_SUFFIX_PATTERN.sub("", text)
    while shorter and shorter != text:
        text, shorter = shorter, _EXCHANGE_SUFFIX_PATTERN.sub("", shorter)
    return text


# The asset slug becomes part of a directory name, and a directory name is the
# thing that actually has a limit: ``NAME_MAX`` is 255 bytes on ext4 and on the
# WSL mounts this runs over. The run id spends a fixed prefix of that on its
# timestamp, its random token and two separators, so what is left is the slug's
# budget. The bound is therefore the filesystem's, not a guess about how long a
# ticker ought to be — there is no such thing as a ticker that is too long,
# only a name that will not fit.
_NAME_MAX_BYTES = 255
_RUN_ID_TIMESTAMP_LENGTH = len("20260314T015926Z")
_RUN_ID_TOKEN_LENGTH = 32  # secrets.token_hex, generously over-counted
MAX_ASSET_SLUG_BYTES = (
    _NAME_MAX_BYTES - _RUN_ID_TIMESTAMP_LENGTH - _RUN_ID_TOKEN_LENGTH - 2
)


def asset_slug_for(assets):
    """Return one path-safe run id segment for ``assets``.

    Targets are no longer limited to five upper-case symbols, so anything that
    is not ``[a-z0-9]`` — a share-class dot, a slash, a space — collapses to a
    single dash before it can reach a directory name.
    """
    parts = [part for part in (_slug_token(asset) for asset in assets) if part]
    return "-".join(parts) or OVERALL_MARKET_SLUG


def _slug_token(asset):
    return _SLUG_SEPARATOR_PATTERN.sub("-", normalize_asset(asset).lower()).strip("-")


def analyze_question(question):
    """Return the :class:`QuestionScope` for a question that names a target.

    This is the strict reading used by callers that cannot work without a named
    asset. The open path calls :func:`inspect_question` instead.
    """
    scope = inspect_question(question)
    if not scope.assets:
        raise UnsupportedQuestionError("題目未指名任何可辨識的分析標的；fail closed。")
    return scope


def inspect_question(question, allow_unknown_assets=False, assets=None, asset_class=None):
    """Read text, targets, asset class and period without requiring a target.

    ``allow_unknown_assets`` is accepted for the callers written against the
    whitelist era. Every token is known now, so the flag changes nothing.

    ``assets`` and ``asset_class`` are the caller's own answer, and where they
    are given the text is not consulted for that answer at all — see
    :func:`_stated_assets` and :func:`_stated_asset_class`. Leaving both at
    ``None`` reads the question exactly as before.
    """
    text = (question or "").strip()
    if not text:
        raise UnsupportedQuestionError("題目為空；無法判定分析標的，fail closed。")

    stated_assets = _stated_assets(assets)
    stated_class = _stated_asset_class(asset_class)
    reading = _fold_width_variants(text)
    spoken_for = _spoken_for_spans(reading)
    tw_codes = _taiwan_codes(reading, spoken_for)
    read_assets, asset_spans = _find_assets(reading, tw_codes, spoken_for)
    period_days, period_stated = _read_period(reading)
    final_assets = read_assets if stated_assets is None else stated_assets
    return QuestionScope(
        question=text,
        assets=final_assets,
        period_days=period_days,
        period_stated=period_stated,
        asset_class=stated_class or _detect_asset_class(
            _market_text(reading, asset_spans),
            final_assets,
            _names_a_taiwan_listing(final_assets),
        ),
    )


def _stated_assets(assets):
    """The caller's own target list, checked for shape and nothing else.

    Whoever passes this already knows what the run is about — a menu knows
    which row was clicked — so the reader has no business adding to it,
    dropping from it or rewriting it. Only :func:`normalize_asset` still
    applies, because that is the one spelling the rest of the system stores.

    Shape is still enforced, and fails closed. The shape is the identifier
    grammar this module already reads — reusing it rather than inventing a
    second one is also what keeps a separator, a space or a ``..`` out of the
    run slug, since none of them is part of an identifier.

    **The check runs on the text as given, before normalisation**, and the
    order matters more than it looks. :func:`normalize_asset` upper-cases, and
    Unicode upper-casing is not a relabelling — it rewrites. ``ß`` becomes
    ``SS``, ``ﬃ`` becomes ``FFI``, ``ſ`` becomes ``S``, ``ı`` becomes ``I``,
    ``K`` (U+212A) becomes ``K``. Validating afterwards would have seen only
    the clean result and bound the run to a target the caller never wrote,
    which is sanitising by accident while claiming to refuse. Checking first
    means normalisation can only change case and drop decoration; it can never
    turn something illegal into something legal.

    The *container* is checked too, and for a reason that is not tidiness. The
    targets become the run id in the order they arrive, so the container has to
    have an order: a ``set`` iterates differently between processes, and the
    same call would then produce ``…-aapl-nvda-…`` and ``…-nvda-aapl-…`` on
    alternate runs — two run directories for one input. A generator would be
    consumed by the first reader to touch it. So only a list or a tuple is
    accepted, and anything that is not iterable at all is reported as a stated
    target that cannot describe a run, never as a raw ``TypeError``.

    A subclass may override ``__iter__``, so the elements are taken through the
    base type's own iterator and frozen before anything reads them. That keeps
    the guarantee a guarantee without excluding subclasses that behave — a
    ``namedtuple`` is an ordered, re-readable tuple and goes through untouched.
    """
    if assets is None:
        return None
    if not isinstance(assets, (list, tuple)):
        raise UnknownAssetError(
            "assets 需為 list 或 tuple（有序且可重讀）；收到 {}。"
            "set／dict／generator／字串的元素順序不穩定或只能讀一次，"
            "會讓同一份輸入產生不同的 run id，fail closed。".format(
                type(assets).__name__
            )
        )
    base_iterator = list.__iter__ if isinstance(assets, list) else tuple.__iter__
    stated = []
    for asset in tuple(base_iterator(assets)):
        if not isinstance(asset, str) or not asset.strip():
            raise UnknownAssetError("指定的分析標的不得為空字串。")
        if not _RAW_IDENTIFIER_ONLY.match(asset.strip()):
            raise UnknownAssetError(
                "指定的分析標的 {!r} 含有不允許的字元，fail closed。".format(asset)
            )
        normalized = normalize_asset(asset)
        if normalized in stated:
            # Two spellings of one target is not a two-asset comparison: the
            # ballot would offer 「NVDA較優」 against 「NVDA較優」.
            raise UnknownAssetError(
                "指定的分析標的 {!r} 正規化後與前面重複（{}），fail closed。".format(
                    asset, normalized
                )
            )
        stated.append(normalized)
    _require_a_usable_run_slug(stated)
    return tuple(stated)


def _require_a_usable_run_slug(assets):
    """Refuse at intake what the filesystem would refuse at ``mkdir``.

    Without this the run id is built first and the rejection arrives from
    ``mkdir`` as a generic failure, after a directory tree has been started.
    Deciding here means one target list too long, or too many of them, is an
    intake refusal like any other — and the limit is measured in the bytes the
    name actually has, not in a guess about targets.
    """
    slug = asset_slug_for(assets)
    used = len(slug.encode("utf-8"))
    if used > MAX_ASSET_SLUG_BYTES:
        raise UnknownAssetError(
            "指定的 {} 個分析標的組成 {} bytes 的 run 目錄名稱，超過上限 {} bytes"
            "（檔名上限 {} 減去 run id 的時間戳與亂數），fail closed。".format(
                len(assets), used, MAX_ASSET_SLUG_BYTES, _NAME_MAX_BYTES
            )
        )


def _stated_asset_class(asset_class):
    """The caller's own market, checked against the classes that exist."""
    if asset_class is None:
        return None
    if asset_class not in ASSET_CLASSES:
        raise UnsupportedQuestionError(
            "指定的資產類別 {!r} 不存在；可用類別為 {}。".format(
                asset_class, "／".join(ASSET_CLASSES)
            )
        )
    return asset_class


def _market_text(text, asset_spans):
    """The question's own words, minus every mention of the targets themselves.

    A ticker that happens to be spelled like a market word — ``COIN`` is a real
    US listing — must not be the reason a question is called a crypto question.
    Only the words *around* the targets may decide the market.
    """
    characters = list(text.lower())
    for start, end in asset_spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _fold_width_variants(text):
    """Read compatibility spellings as the characters they stand for.

    Length-preserving by construction — see :data:`_WIDTH_FOLD` — so
    every span the readers hand back indexes the question exactly as written.
    """
    return text.translate(_WIDTH_FOLD)


_TAIWAN_CODE_SHAPE = re.compile(r"\A[0-9]{4,6}\Z")


def _names_a_taiwan_listing(assets):
    """Whether any accepted target is written as a Taiwan listing code.

    The class follows the target that was actually accepted, not the reader
    that found it. ``$2330.TW`` names 2330 through the cashtag grammar, and the
    exchange suffix it consumed is no longer in the market text — asking the
    accepted target directly is what keeps the class from depending on which
    reader ran.
    """
    return any(_TAIWAN_CODE_SHAPE.match(asset) for asset in assets)


def _taiwan_codes(text, spoken_for):
    """Every 4–6 digit listing code, minus the numbers already spoken for."""
    return [
        (match.start(1), match.end(1), match.group(1))
        for match in _TW_CODE_PATTERN.finditer(text)
        if not any(start <= match.start(1) < end for start, end in spoken_for)
    ]


def _dollar_tokens(text):
    """Read every ``$`` run as one token: ``(start, end, target_or_None)``.

    The whole run is classified before it is claimed, and it is claimed either
    way. Three verdicts, decided by the token alone:

    * a legal identifier carrying at least one letter is a target — ``$F``,
      ``$1INCH``, ``$brk-b``, and ``$2330.TW``, where the exchange suffix is
      itself the evidence that this is a listing rather than an amount;
    * digits alone are an amount — ``$2330`` cannot be told from ``$10000``,
      so both are money and the bare ``2330`` spelling is what names a Taiwan
      listing;
    * anything else is spelled like neither and names nothing. ``$BRK_B``,
      ``$2330:AAPL`` and ``$2330-2454`` are consumed whole and dropped, because
      a token that cannot be read is not an invitation to read half of it.
    """
    return [_read_dollar_token(match) for match in _DOLLAR_TOKEN_PATTERN.finditer(text)]


def _read_dollar_token(match):
    """One ``$`` run's verdict, as ``(start, end, target_or_None)``."""
    start, end = match.span()
    gap, raw_body = match.group(1), match.group(2)
    body = raw_body.rstrip(_DOLLAR_TOKEN_TAIL)
    end -= len(raw_body) - len(body)
    # A cashtag is written against its marker; a space means the ``$`` was
    # never part of this word.
    if not gap and _IDENTIFIER_ONLY.match(body) and any(c.isalpha() for c in body):
        return start, end, body
    if _AMOUNT_ONLY.match(body) or not gap:
        return start, end, None
    # 「NT$ 10000」 is still a price, but ``$ AAPL`` is a loose sign standing
    # next to a ticker the ordinary readers must still get to see, so only the
    # sign is claimed.
    return start, match.end(1), None


def _spoken_for_spans(text):
    """Text that belongs to a date, a price or a count, and so to no target."""
    spans = [
        match.span()
        for pattern in (
            _DATE_PATTERN,
            _MONEY_PATTERN,
            _CURRENCY_LOOKALIKE_PATTERN,
            _QUANTITY_PATTERN,
        )
        for match in pattern.finditer(text)
    ]
    spans.extend(
        (start, end) for start, end, target in _dollar_tokens(text) if target is None
    )
    return spans


def _find_assets(text, tw_codes, spoken_for):
    """Return ``(asset, start, end)`` per target, in order of first appearance.

    Each reader consumes its whole match, and characters already claimed are
    closed to every later reader. That is what keeps ``2330-TW`` one listing
    instead of ``2330`` plus a phantom ``TW``.
    """
    accepted = []
    claimed = list(spoken_for)
    for start, end, token, explicit in _asset_candidates(text, tw_codes):
        if any(start < taken_end and taken_start < end for taken_start, taken_end in claimed):
            continue
        asset = _accepted_asset(token, explicit)
        if asset is None:
            # Rejected candidates must not claim their characters: a later,
            # explicit reader has to be able to read the same word.
            continue
        claimed.append((start, end))
        accepted.append((start, end, asset))
    return _in_first_appearance_order(accepted)


def _accepted_asset(token, explicit):
    """Return the target this token names, or ``None`` if it names none."""
    asset = normalize_asset(token)
    if asset in _ACRONYMS_THAT_ARE_NOT_TARGETS:
        return None
    # A market word only loses to the exclusion when nothing wrote it as an
    # identifier: ``stock 未來…`` is not a target, ``$stock`` and ``STOCK`` are.
    if not explicit and asset in _MARKET_WORD_TOKENS:
        return None
    return asset


def _asset_candidates(text, tw_codes):
    """Every token a reader takes for a target, most specific reader first.

    ``explicit`` marks the readers that fire only where the question names its
    target outright — a digit code, a ``$`` cashtag, an all-caps ticker, or one
    side of a comparison. Those outrank the market-word exclusion, so ``$token``
    and ``TOKEN`` both still mean that ticker. The legacy alias reader does not:
    it recognises five symbols by name, which is not the question saying
    anything.
    """
    for start, end, token in tw_codes:
        yield start, end, token, True
    for start, end, token in _comparison_assets(text):
        yield start, end, token, True
    for start, end, token in _dollar_tokens(text):
        if token is not None:
            yield start, end, token, True
    for pattern, explicit in (
        (_TICKER_PATTERN, True),
        (_LEGACY_SYMBOL_PATTERN, False),
    ):
        for match in pattern.finditer(text):
            yield match.start(1), match.end(1), match.group(1), explicit


def _comparison_assets(text):
    """Both sides of ``A 與 B`` count, but only when the question compares."""
    lowered = text.lower()
    if not any(marker in lowered for marker in _COMPARISON_MARKERS):
        return []
    return [
        (match.start(group), match.end(group), match.group(group))
        for match in _COMPARISON_PAIR_PATTERN.finditer(text)
        for group in (1, 2)
    ]


def _in_first_appearance_order(accepted):
    """Return the de-duplicated assets and *every* accepted occurrence's span.

    The two are not the same list: the run's asset list holds each target once,
    while masking the question for the classifier has to cover every mention of
    it. Letting one list do both jobs made a repeated ticker leak its own name
    into the market words.
    """
    ordered = []
    spans = []
    for start, end, asset in sorted(accepted, key=lambda item: item[0]):
        spans.append((start, end))
        if asset not in ordered:
            ordered.append(asset)
    return tuple(ordered), tuple(spans)


def _detect_asset_class(lowered_text, assets, has_taiwan_code):
    """Read the market off the question's own words, or admit it is unknown.

    A longer term outranks a shorter one only where the two cover the same
    text: ``台灣股票`` contains the generic ``股票`` and speaks for it. Terms
    that sit apart are not compared by length at all — character count is not
    semantic specificity, and a Chinese term is not less specific than an
    English one for being shorter. Between surviving terms the question's own
    order decides, and an exact tie falls back to the table's order.
    """
    if has_taiwan_code:
        return ASSET_CLASS_TW_STOCK
    matches = list(_class_matches(lowered_text))
    standing = [match for match in matches if not _covered_by_a_longer_term(match, matches)]
    if standing:
        start, end, rank, asset_class = min(
            standing, key=lambda item: (item[0], -(item[1] - item[0]), item[2])
        )
        return asset_class
    if any(asset in LEGACY_CRYPTO_SYMBOLS for asset in assets):
        return ASSET_CLASS_CRYPTO
    return ASSET_CLASS_OPEN


def _class_matches(lowered_text):
    """Every market word in the text, skipping the ones that split a word.

    A term rejected here must not hide the term behind it, so the scan resumes
    one character past the bad start rather than past the whole match: the
    ``個股`` inside ``這個股票`` is not a market word, but the ``股票`` it overlaps
    is.
    """
    demonstratives = [match.span() for match in _DEMONSTRATIVE_PATTERN.finditer(lowered_text)]
    for rank, (asset_class, matcher) in enumerate(_CLASS_MATCHERS):
        position = 0
        while position < len(lowered_text):
            match = matcher.search(lowered_text, position)
            if match is None:
                break
            if _splits_a_demonstrative(match.start(), demonstratives):
                position = match.start() + 1
                continue
            yield match.start(), match.end(), rank, asset_class
            position = match.end()


def _splits_a_demonstrative(start, demonstratives):
    """True when a term begins in the middle of 「這個」 and so spans two words.

    ``個股`` is a real market word, but the ``個`` in ``這個股東會`` belongs to
    the demonstrative — reading a market word across that seam is reading two
    words as one.
    """
    return any(
        span_start < start < span_end for span_start, span_end in demonstratives
    )


def _covered_by_a_longer_term(match, matches):
    """True when another term spans this one and says more about the same text."""
    start, end, _, _ = match
    return any(
        other_start <= start and end <= other_end
        and (other_end - other_start) > (end - start)
        for other_start, other_end, _, _ in matches
    )


def _read_period(text):
    match = _PERIOD_PATTERN.search(text)
    if match:
        return _positive_period(int(match.group(1)))

    match = _WEEK_PERIOD_PATTERN.search(text)
    if match:
        return _positive_period(int(match.group(1)) * 7)

    days = _chinese_period_days(text)
    if days is not None:
        return _positive_period(days)

    if _PERIOD_HINT_PATTERN.search(text):
        raise UnsupportedQuestionError(
            "題目包含無法解析的分析期間；請使用明確日數或週數，fail closed。"
        )
    return DEFAULT_PERIOD_DAYS, False


def _chinese_period_days(text):
    """Read 「七天」 and 「兩週」; both are how a live question states a period."""
    for pattern, days_per_unit in (
        (_CHINESE_WEEK_PERIOD_PATTERN, 7),
        (_CHINESE_DAY_PERIOD_PATTERN, 1),
    ):
        match = pattern.search(text)
        if match is None:
            continue
        count = _parse_chinese_count(match.group(1))
        if count is not None:
            return count * days_per_unit
    return None


def _positive_period(days):
    if days <= 0:
        raise UnsupportedQuestionError("分析期間必須為正整數日數；fail closed。")
    return days, True


def _parse_chinese_count(token):
    if "十" not in token:
        return _CHINESE_DIGITS.get(token)
    tens, ones = token.split("十", 1)
    tens_value = 1 if not tens else _CHINESE_DIGITS.get(tens)
    ones_value = 0 if not ones else _CHINESE_DIGITS.get(ones)
    if tens_value is None or ones_value is None:
        return None
    return tens_value * 10 + ones_value
