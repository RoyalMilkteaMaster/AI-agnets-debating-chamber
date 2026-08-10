"""The resident local web app: ask a question, watch it happen, read it later.

This package is the front end that stays open on ``127.0.0.1``. It serves the
history list, one run's detail, the two artifacts that detail page links to,
the live chat room a run is watched in, and the page the debate rules are
edited on — and it writes its own operational log.

Four boundaries shape everything below and are worth stating once:

* **The routes hold no query logic.** Which runs match a set of filters is
  :mod:`hoya_market_agents.run_index`'s question, and it is asked exactly one
  way: :func:`~hoya_market_agents.run_index.query_runs`. What lives here is the
  translation between a URL's text and that function's arguments, which is a
  different job with its own failure modes — a user can type ``limit=-1``, and
  a caller of ``query_runs`` may not pass it.
* **Run artifacts are read, with two named exceptions.** No function in this
  package opens a run's own *record* under ``runs/`` for writing or replaces one.
  The first exception is :mod:`~hoya_market_agents.webapp.outcome`, which
  *creates* one file that no run ever writes — ``outcome.json`` — once per run
  and only after that run's analysis period has run out. It is write-once at the
  filesystem level, so even that exception cannot become an overwrite. The second
  is :mod:`~hoya_market_agents.webapp.pdf_export`, which adds ``report.pdf`` and
  ``debate.pdf`` beside the pages they were printed from and can write no other
  name. It overwrites nothing, including its own output: a run that already has
  either file is refused, with the files named, rather than exported over. **That
  holds for simultaneous requests as well**, which this server has — one run is
  exported by one request at a time, and the write that gives a PDF its name is one
  the filesystem refuses when the name is taken, so neither a second submission nor
  a second writer can overwrite the first. An export that fails adds nothing: a
  promotion that breaks halfway is undone, and undone means "the names this request
  created", proved by inode rather than assumed from the spelling. Both exceptions
  therefore only ever *add* to a run directory, which is why a run either of them
  has been used on verifies exactly as it did before.
* **Five files are written, and they are named.** ``_data/logs/`` is this
  package's own; ``config/debate_rules.json`` is the Code Root's rule file, put
  there by :mod:`~hoya_market_agents.webapp.settings` and by nothing else here;
  a run's ``outcome.json`` is written by
  :mod:`~hoya_market_agents.webapp.outcome` and by nothing else here; a run's
  ``report.pdf`` and ``debate.pdf`` are written by
  :mod:`~hoya_market_agents.webapp.pdf_export` and by nothing else here.
* **The public quote service is reached from one module and one page.** Only
  :mod:`~hoya_market_agents.webapp.outcome` calls the quote client, only the
  statistics page calls that, and nothing in the research pipeline can reach
  either. That is asserted by a scan over every source file in the package
  rather than left as a convention — which is also why this paragraph does not
  spell the client's module name: the scan reads text, and a mention here would
  be a hit that means nothing.
* **Which rule documents are legal is not decided here.** That is
  :mod:`hoya_market_agents.debate_rules`'s question, asked one way —
  :func:`~hoya_market_agents.debate_rules.load_debate_rules`, handed the
  candidate file — and every refusal shown to a reader is its sentence, quoted.
* **Starting a run is not the same as owning it.** ``POST /launch`` spawns a
  separate ``launch`` process and lets go. Everything after that — including
  the live room — is a reader. A browser that closes, a stream that fails and
  a server that is killed all leave the run alone (architecture §4.0.1).

The parts:

``log``
    ``_data/logs/webapp.jsonl`` — one JSON object per line, rotated by day and
    kept for thirty.
``views``
    Request text in, page data out: filter translation and one run's records.
``live``
    One run's public chat, read forward out of its append-only event log.
``launch``
    Starting a run in its own process, and the front-end lock over that. It owns
    both ends of one submission as well: the ask bar's field names, and the entry
    point the child process is started on.
``outcome``
    After the fact: which predictions have expired, what the market did, and
    the write-once record of the answer — with the hand-entered fallback for
    everything no price can settle.
``pdf_export``
    The run directory's third write path: one run's ready-made pages turned into
    ``report.pdf`` and ``debate.pdf`` by an injectable converter — headless
    Microsoft Edge by default — adding nothing at all when it fails.
``settings``
    ``config/debate_rules.json`` as a form: the controls are walked out of the
    document, the loader is the only thing that refuses, and the file is
    replaced whole or not at all.
``pages``
    Page data in, HTML out. No I/O.
``server``
    Routing, headers and the server's own lifecycle.
"""

from .live import live_snapshot, read_events, resume_offset
from .log import WebappLogError, open_webapp_log
from .server import (
    CONTENT_SECURITY_POLICY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LIVE_CONTENT_SECURITY_POLICY,
    StreamSettings,
    WebappError,
    create_webapp_server,
    serve_webapp,
    webapp_handler_class,
)

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LIVE_CONTENT_SECURITY_POLICY",
    "StreamSettings",
    "WebappError",
    "WebappLogError",
    "create_webapp_server",
    "live_snapshot",
    "open_webapp_log",
    "read_events",
    "resume_offset",
    "serve_webapp",
    "webapp_handler_class",
]
