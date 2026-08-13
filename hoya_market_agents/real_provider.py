"""Real, subscription-backed process runner for the seven research seats.

The runner owns process dispatch only. It never interprets a market stance, and
it never touches the scheduler or the run store from a worker thread: the only
cross-thread channel is ``results_queue``.

Seat routing
------------
* Claude 3 seats (``onchain`` / ``official-events`` / ``social-macro``) and the
  Antigravity seat (``counter-evidence``) run locally in one shared thread pool
  and answer through ``results_queue``.
* Codex 3 seats (``spot-technical`` / ``derivatives`` / ``news``) take one of
  two channels, chosen by ``codex_mode``:

  ``"cli"`` (default)
      Dispatched locally through ``codex exec`` in the same thread pool, so one
      ``launch`` fills all seven seats without a human opening Codex threads.
  ``"inbox"``
      Fallback only: the request is left in the inbox and an external Codex
      bridge answers by writing an inbox result, which the launcher relays.

  The write-once inbox request is written in *both* modes: in ``"cli"`` mode it
  is the audit record of what was dispatched, never a second dispatch.

Wiring contract used by the launcher::

    RealSeatRunner(
        run,                        # RunDirectory for this run
        data_root,                  # Data Root path
        code_root,                  # Code Root path
        results_queue,              # queue.Queue for worker messages
        question_scope_or_package,  # QuestionPackage or question scope
        inbox_requests_dir,         # <data_root>/inbox/<run_id>/requests
        claude_adapter=None,        # ClaudeAdapter seam (default: real CLI)
        antigravity_adapter=None,   # AntigravityAdapter seam (default: real CLI)
        codex_adapter=None,         # CodexExecAdapter seam (default: real CLI)
        codex_mode="cli",           # "cli" dispatches locally, "inbox" waits
        executor=None,              # ThreadPoolExecutor seam (default: owned)
    )

``shutdown(wait=True)`` releases the owned worker pool. It is a lifecycle
helper for the launcher and tests, not part of the five-method runner protocol
(``start`` / ``checkpoint`` / ``correct`` / ``cancel`` / ``terminate``).

``build_attempt_prompt`` is the single prompt assembly for all seven seats. The
launcher calls it to write the three Codex inbox prompts, so an inbox seat and a
locally dispatched seat read byte-identical shared rules.

Queue messages follow the frozen runner protocol::

    ("result", attempt_id, raw_text)
    ("failure", attempt_id, failure_kind, message)

Debate turns (Ticket T6) reuse this pool, this registry and this queue with a
second, non-overlapping message vocabulary. ``dispatch_id`` is the debate key
(``"<seat_id>-<turn>"``); it never collides with a research ``attempt_id``
(``"<seat_id>-a<n>"``), so one drain loop can tell the two phases apart::

    ("debate_result",  dispatch_id, raw_text)
    ("debate_failure", dispatch_id, failure_kind, message)
    ("provider_lineage", seat_id, {provider, actual_model, elapsed_ms, ...})

``provider_lineage`` is published by debate workers only, before the result or
failure, and is never suppressed by cancellation: which model actually answered
stays true even for a turn whose deadline passed.

Deadline termination
--------------------
A running ``Future`` cannot be cancelled, so cancelling one would leave the real
provider process burning a subscription after the acceptance wall and holding up the launch.
Every locally dispatched seat therefore runs through ``TerminatingRunner`` with
one shared ``ProcessRegistry``, keyed by the attempt (or debate dispatch) the
worker is running — never by the pooled thread, which other seats reuse.
``cancel``/``terminate`` cancel the future *and* terminate that child (kill
after a short grace), and never raise. The registry keeps the key poisoned
afterwards, so a retry spawned just after the cancel — the Claude same-session
resume is the one that can — is stopped the moment it registers instead of
running its full timeout unattended.

Live research proof
-------------------
A seat only counts when its provider proves it researched live in this run:
Claude must report a ``claude-opus`` model and at least one web_search/web_fetch
server tool call, Antigravity must show a completed ``search_web`` step
(``require_search=True``), and Codex must emit a matching JSONL
``item.started`` plus non-error ``item.completed`` for the same ``web_search``
item id. Anything else is published as ``research_proof_missing`` instead of
being adopted as evidence.

Search after the seal
---------------------
The mirror image holds once the evidence snapshot is sealed: every debate turn and
Core's report call is dispatched with ``allow_search=False``, which switches the
capability off for Claude (no tools at all) and Codex
(``tools.web_search=false``). ``agy`` exposes no flag that removes a tool, so
that seat is held to the same rule one layer later — a completed ``search_web``
step makes the reply unusable. All three are also checked again on the result:
Claude's reported ``web_search_requests``, Codex's search invocations and
``agy``'s completed step each have to be zero. A sealed call that searched anyway
is published as ``provider_error`` with ``post_seal_search_detected``; it is
never adopted.

Model and provider lanes
------------------------
``PRIMARY_MODELS`` and ``SEAT_PROVIDERS`` both come from the frozen roster —
3 Codex, 3 Claude, 1 Antigravity. No provider CLI in this system can honestly
serve a second model, so ``REPLACEMENT_MODELS`` stays empty and a same-provider
retry buys nothing when the provider itself is what failed. Recovery is instead
one ``BACKUP_CANDIDATES`` attempt on **another** provider, running that
provider's own fixed model; there is no second backup, and no fake Sonnet or
other unavailable-model dispatch. A backup is attempt lineage only: the seat
keeps its id, its focus and its roster provider.

Provider discovery
------------------
A provider executable is whatever this WSL ``PATH`` resolves (ADR 0009), via
:mod:`hoya_market_agents.provider_cli`. ``start`` resolves it before dispatching
and publishes ``provider_cli_missing`` at once when it cannot, so a seat spends
its recovery window on a backup instead of on a timeout for a CLI that was never
installed. ``which`` is the injectable seam an offline test uses to decide what
this shell can see.
"""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .antigravity_adapter import MODEL as ANTIGRAVITY_MODEL
from .antigravity_adapter import (
    AntigravityAdapter,
    AntigravityEnvelopeError,
    AntigravityError,
    AntigravityNotReady,
    AntigravityPostSealSearch,
    AntigravityResearchProofMissing,
    AntigravitySchemaError,
    AntigravityTimeout,
    AntigravityTreeTermination,
)
from .claude_adapter import CLAUDE_MODEL_ALIAS
from .claude_adapter import (
    ClaudeAdapter,
    ClaudeAttemptRequest,
    ProcessRegistry,
    TerminatingRunner,
)
from .clock import iso_utc
from .codex_exec_adapter import (
    CODEX_MODEL,
    CodexExecAdapter,
    CodexExecEmptyOutputError,
    CodexExecError,
    CodexExecOutputError,
    CodexExecProcessError,
    CodexExecTimeout,
    CodexExecTreeTerminationError,
)
from .contract_validator import (
    CONTRACT_VERSION,
    DIRECTIONS,
    MAX_EVIDENCE_CARDS_PER_SEAT,
    SOURCE_TIERS,
    validate_evidence_card,
)
from .prompt_builder import build_seat_prompt
from .provider_cli import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCliMissing,
    require_provider_cli,
)
from .question import normalize_asset
from .recovery_state_machine import ProviderCandidate
from .research_scheduler import (
    PROCESS_TREE_TERMINATION_FAILED,
    PROVIDER_CLI_MISSING,
    PROVIDER_EMPTY_OUTPUT,
    PROVIDER_MALFORMED_OUTPUT,
    PROVIDER_OUTPUT_REJECTED,
    PROVIDER_PROCESS_ERROR,
    PROVIDER_START_FAILED,
    PROVIDER_TIMEOUT,
    RESEARCH_PROOF_MISSING,
    research_deadlines,
)
from .run_store import _remove_trailing_commas
from .seats import SEAT_IDS, load_roster
from .system_preflight import load_frozen_roster

# 三個分組是 roster ``provider`` 欄的投影，順序就是 roster 的席位順序；roster 是
# 唯一權威，這裡改了對不上會被 tests/test_real_provider.py 的一致性斷言擋下來。
CODEX_SEAT_IDS = ("spot-technical", "derivatives", "news")
CLAUDE_SEAT_IDS = ("onchain", "official-events", "social-macro")
ANTIGRAVITY_SEAT_IDS = ("counter-evidence",)

# Provider 研究呼叫必須在收件牆之前分出勝負，否則得到的是無法採用的晚到答案。
# 收件牆依題型移動，所以 timeout 一律由 research_deadlines
# 推導，這裡只決定要留多少 relay 餘裕。
CLAUDE_TIMEOUT_MARGIN_MS = 5_000
CLAUDE_TIMEOUT_SECONDS = (
    research_deadlines().accept_until_ms - CLAUDE_TIMEOUT_MARGIN_MS
) // 1_000
# 假研究的兩道門：模型身分與線上檢索紀錄，任一不成立就不是本次比賽的證據。
NO_RESEARCH_PROOF = "no_live_research_proof"
# 封存之後的呼叫已經關掉搜尋能力；還是搜到，就代表這份回覆不是只依快照產生。
POST_SEAL_SEARCH = "post_seal_search_detected"
# 每席最多一個 primary 與一個 backup，兩者都必須能同時待在合法平行窗口內
# （Spec R-008）；pool 小於這個數字就會讓 backup 排到收件牆之後才開始。
RESEARCH_ATTEMPT_CAPACITY = 2 * len(SEAT_IDS)
LOCAL_WORKER_COUNT = RESEARCH_ATTEMPT_CAPACITY
RESUME_ATTEMPT_SUFFIX = "-resume"

CODEX_MODE_CLI = "cli"
CODEX_MODE_INBOX = "inbox"
CODEX_MODES = (CODEX_MODE_CLI, CODEX_MODE_INBOX)

DEBATE_RESULT_MESSAGE = "debate_result"
DEBATE_FAILURE_MESSAGE = "debate_failure"
PROVIDER_LINEAGE_MESSAGE = "provider_lineage"
# 研究階段的同名訊息。dispatch 詞彙分開，drain loop 才不必猜這是哪個階段的
# lineage，而 research lineage 走的是 scheduler.record_lineage，不是 debate 的。
RESEARCH_LINEAGE_MESSAGE = "research_lineage"

REPLACEMENT_MODELS = {
    seat_id: None
    for seat_id in (*CODEX_SEAT_IDS, *CLAUDE_SEAT_IDS, *ANTIGRAVITY_SEAT_IDS)
}

# 每個 provider 家族只有一顆能誠實派發的模型；backup 用的就是它，不另立第二套
# 模型設定，也不會出現「換 provider 卻沿用原模型名」這種對不上的 lineage。
PROVIDER_MODELS = {
    PROVIDER_CODEX: CODEX_MODEL,
    PROVIDER_CLAUDE: CLAUDE_MODEL_ALIAS,
    PROVIDER_ANTIGRAVITY: ANTIGRAVITY_MODEL,
}


def load_seat_providers(roster=None):
    """Return the frozen ``seat_id -> provider family`` mapping."""
    roster = roster or load_frozen_roster()
    return {seat["seat_id"]: seat["provider"] for seat in roster["seats"]}


#: roster 的 ``provider`` 欄投影：3 Codex／3 Claude／1 Antigravity，順序同 roster。
SEAT_PROVIDERS = load_seat_providers()

# backup 候選順序是固定的，且只取第一個與本席 primary 不同的 provider：一席只
# 准一個 backup，所以「順序」實際上只決定那一個是誰，不會退到第二順位。
BACKUP_PROVIDER_ORDER = (PROVIDER_CODEX, PROVIDER_CLAUDE, PROVIDER_ANTIGRAVITY)


def backup_candidate(provider, order=BACKUP_PROVIDER_ORDER):
    """The one approved other-provider fallback for a seat on ``provider``."""
    for candidate in order:
        if candidate != provider:
            return ProviderCandidate(
                provider=candidate, model=PROVIDER_MODELS[candidate]
            )
    return None


BACKUP_CANDIDATES = {
    seat_id: backup_candidate(provider)
    for seat_id, provider in SEAT_PROVIDERS.items()
}

# -- stable failure codes, carried rather than re-derived ---------------------
#
# 每個 adapter 邊界都已經知道出了什麼事——是逾時、是整組回收不掉、是交了白卷、
# 還是交了看不懂的東西。以前這些都在 publish 時折成三個籠統的 failure kind，
# scheduler 再折一次，於是 research_proof_missing 與 process_tree_termination_failed
# 這種「永遠不可採用」的終局，跟一次普通的 provider 雜訊變成同一個 code。
# 下面三張表是唯一的轉換處：型別／狀態進去，穩定 code 出來，沒有任何一步去讀
# 人看的訊息文字。

CODEX_FAILURE_CODES = (
    # 由具體到一般：第一個吻合的型別決定 code。
    (CodexExecTreeTerminationError, PROCESS_TREE_TERMINATION_FAILED),
    (CodexExecTimeout, PROVIDER_TIMEOUT),
    (CodexExecProcessError, PROVIDER_PROCESS_ERROR),
    (CodexExecEmptyOutputError, PROVIDER_EMPTY_OUTPUT),
    (CodexExecOutputError, PROVIDER_MALFORMED_OUTPUT),
)

# Claude 邊界本來就分得很細（``empty_output``／``malformed_output``／
# ``invalid_schema``…），只是 ``scheduler_failure_kind`` 把它們全折成
# provider_error。這裡直接讀 status，那些區分才活得下來。
CLAUDE_STATUS_FAILURE_CODES = {
    "timeout": PROVIDER_TIMEOUT,
    "process_error": PROVIDER_PROCESS_ERROR,
    "empty_output": PROVIDER_EMPTY_OUTPUT,
    "malformed_output": PROVIDER_MALFORMED_OUTPUT,
    "invalid_schema": PROVIDER_MALFORMED_OUTPUT,
    PROCESS_TREE_TERMINATION_FAILED: PROCESS_TREE_TERMINATION_FAILED,
}

ANTIGRAVITY_FAILURE_CODES = (
    (AntigravityTreeTermination, PROCESS_TREE_TERMINATION_FAILED),
    (AntigravityTimeout, PROVIDER_TIMEOUT),
    (AntigravityResearchProofMissing, RESEARCH_PROOF_MISSING),
    (AntigravityNotReady, PROVIDER_START_FAILED),
    (AntigravityEnvelopeError, PROVIDER_MALFORMED_OUTPUT),
    (AntigravitySchemaError, PROVIDER_MALFORMED_OUTPUT),
)


def _code_for_type(error, table):
    for error_type, code in table:
        if isinstance(error, error_type):
            return code
    return PROVIDER_OUTPUT_REJECTED


def codex_failure_code(error):
    """The stable code one ``codex exec`` failure keeps all the way through."""
    return _code_for_type(error, CODEX_FAILURE_CODES)


def antigravity_failure_code(error):
    """The stable code one ``agy`` failure keeps all the way through."""
    return _code_for_type(error, ANTIGRAVITY_FAILURE_CODES)


def claude_failure_code(status):
    """The stable code for one Claude attempt's own machine-readable status."""
    return CLAUDE_STATUS_FAILURE_CODES.get(status, PROVIDER_OUTPUT_REJECTED)

EVIDENCE_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": [CONTRACT_VERSION]},
        "evidence_id": {"type": "string"},
        "run_id": {"type": "string"},
        "seat_id": {"type": "string"},
        "attempt_id": {"type": "string"},
        "phase": {"type": "string", "enum": ["research"]},
        "created_at_utc": {
            "type": "string",
            "description": "UTC ISO-8601，格式 YYYY-MM-DDTHH:MM:SSZ",
        },
        "elapsed_ms": {"type": "integer", "minimum": 0},
        "asset": {
            "type": "string",
            "description": "本題分析標的的代號或名稱，維持題目與來源的原格式",
        },
        "category": {"type": "string"},
        "statement": {"type": "string", "description": "繁體中文的公開陳述"},
        "direction": {"type": "string", "enum": list(DIRECTIONS)},
        "source_url": {"type": "string"},
        "source_origin": {"type": "string", "description": "來源機構或原始發布者"},
        "source_tier": {
            "type": "integer",
            "minimum": min(SOURCE_TIERS),
            "maximum": max(SOURCE_TIERS),
            "description": "1=一手官方，2=可信媒體或數據商，3=其他公開來源",
        },
        "published_at_utc": {
            "type": "string",
            "description": "UTC ISO-8601，格式 YYYY-MM-DDTHH:MM:SSZ",
        },
        "retrieved_at_utc": {
            "type": "string",
            "description": "UTC ISO-8601，格式 YYYY-MM-DDTHH:MM:SSZ",
        },
        "excerpt": {"type": "string", "description": "來源原文或原始數值，不得改寫"},
        "credibility_note": {"type": "string", "description": "繁體中文的可信度與限制說明"},
    },
    "required": [
        "schema_version",
        "evidence_id",
        "run_id",
        "seat_id",
        "attempt_id",
        "phase",
        "created_at_utc",
        "elapsed_ms",
        "asset",
        "category",
        "statement",
        "direction",
        "source_url",
        "source_origin",
        "source_tier",
        "published_at_utc",
        "retrieved_at_utc",
        "excerpt",
        "credibility_note",
    ],
    "additionalProperties": False,
}

RESEARCH_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "seat_id": {
            "type": "string",
            "description": "必填，且必須精確等於本次指定的席位 ID",
        },
        "evidence_cards": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_CARDS_PER_SEAT,
            "items": EVIDENCE_CARD_SCHEMA,
        },
    },
    "required": ["seat_id", "evidence_cards"],
    "additionalProperties": False,
}


def research_envelope_schema(run_id, attempt):
    """Return this one attempt's own envelope schema, pinned to its lineage.

    :data:`RESEARCH_ENVELOPE_SCHEMA` is a template and stays one: every call
    deep-copies it, so fourteen invocations building schemas at the same moment
    share no nested object and cannot overwrite each other's ``enum``. The four
    single-value enums make the run, the seat and the attempt part of the
    output contract, which is the earliest point a provider can be refused for
    answering as somebody else. ``RealEvidenceGateway`` still validates the same
    lineage afterwards: this is an earlier refusal, never a replacement for it.
    """
    schema = deepcopy(RESEARCH_ENVELOPE_SCHEMA)
    schema["properties"]["seat_id"]["enum"] = [attempt.seat_id]
    card = schema["properties"]["evidence_cards"]["items"]["properties"]
    card["run_id"]["enum"] = [run_id]
    card["seat_id"]["enum"] = [attempt.seat_id]
    card["attempt_id"]["enum"] = [attempt.attempt_id]
    return schema


class RealProviderError(RuntimeError):
    """Raised when an attempt cannot be dispatched honestly."""


@dataclass(frozen=True)
class DebateDispatch:
    """One seat's one debate turn: a fresh, schema-constrained invocation.

    ``validator`` is mandatory because the Claude boundary refuses any
    structured output it was not told how to check.
    """

    dispatch_id: str
    seat_id: str
    prompt: str
    schema: dict
    validator: object
    timeout_seconds: float
    phase: str = "debate"
    research_attempt_id: str = None
    provider: str = None
    requested_model: str = None
    research_actual_model: str = None
    adopted_evidence_sha256: str = None
    opening_started_elapsed_ms: int = None
    opening_deadline_elapsed_ms: int = None


def build_attempt_prompt(question_scope_or_package, seat, run_id, attempt, checkpoint=None):
    """Assemble the one prompt text a seat receives for a research attempt.

    Every seat — local Claude/Antigravity and the three inbox-dispatched Codex
    seats — must be assembled here, so the shared rules stay byte-identical no
    matter which channel carries the prompt.
    """
    prompt = build_seat_prompt(question_scope_or_package, seat, "research")
    return prompt.text + _output_instructions(run_id, attempt, checkpoint)


def claude_research_timeout_seconds(question_type=None):
    """Return the research timeout that stops just short of this run's wall."""
    deadlines = research_deadlines(question_type)
    return (deadlines.accept_until_ms - CLAUDE_TIMEOUT_MARGIN_MS) // 1_000


def load_primary_models(roster=None):
    """Return the frozen ``seat_id -> target_model`` mapping."""
    roster = roster or load_frozen_roster()
    return {seat["seat_id"]: seat["target_model"] for seat in roster["seats"]}


PRIMARY_MODELS = load_primary_models()


def validate_research_envelope_shape(value):
    """Shallow envelope check for the Claude structured-output boundary.

    The deep per-card contract is enforced later by ``RealEvidenceGateway``;
    this only refuses shapes that could never become evidence.
    """
    if not isinstance(value, dict):
        raise TypeError("research envelope 必須為 JSON 物件")
    cards = value.get("evidence_cards")
    if not isinstance(cards, list) or not cards:
        raise ValueError("evidence_cards 必須為至少一張證據卡的陣列")
    if any(not isinstance(card, dict) for card in cards):
        raise ValueError("每張證據卡必須為 JSON 物件")
    return value


class RealEvidenceGateway:
    """Validate one seat submission coming back from a real provider.

    The gateway is bound to one run and one question's assets: a card carrying
    another ``run_id`` or an asset this question never asked about is research
    from somewhere else, and must never enter this competition's snapshot.

    An open proposition may name no target at all. There is nothing to bind the
    asset to then, so only the run binding applies — the alternative would be
    refusing every card of a question the intake gate just accepted. Matching
    goes through ``normalize_asset``, because a target is now whatever the
    question called it and a seat may spell it ``brk-b``, ``BRK.B`` or
    ``2330.TW`` for the same listing.
    """

    def __init__(self, run_id, allowed_assets=()):
        if not isinstance(run_id, str) or not run_id.strip():
            raise RealProviderError("evidence gateway 必須綁定本次 run_id")
        self.run_id = run_id
        self.allowed_assets = tuple(allowed_assets or ())
        self._allowed_keys = {normalize_asset(asset) for asset in self.allowed_assets}

    def validate(self, attempt, raw_output):
        cards = _unwrap_envelope(json.loads(raw_output), attempt)
        _require_card_count(cards)
        for card in cards:
            if not isinstance(card, dict):
                raise ValueError("每張證據卡必須為 JSON 物件")
            validate_evidence_card(card)
            _require_lineage(card, attempt)
            self._require_binding(card)
        return cards

    def _require_binding(self, card):
        if card["run_id"] != self.run_id:
            raise ValueError(
                "證據卡 run_id 與本次 run 不一致：預期 {}，實際 {!r}".format(
                    self.run_id, card["run_id"]
                )
            )
        if not self.allowed_assets:
            return
        if normalize_asset(card["asset"]) in self._allowed_keys:
            return
        raise ValueError(
            "證據卡 asset 不在本題資產範圍：允許 {}，實際 {!r}".format(
                "/".join(self.allowed_assets), card["asset"]
            )
        )


class TrailingCommaRepairer:
    """Non-voting repair that drops only commas sitting before ``}`` or ``]``."""

    name = "trailing-comma-repair"

    def repair(self, attempt, raw_output, exact_error):
        if not isinstance(raw_output, str):
            return None
        repaired = _remove_trailing_commas(raw_output)
        if repaired == raw_output:
            return None
        return repaired


class RealSeatRunner:
    """Dispatch one research attempt per seat against the real providers."""

    def __init__(
        self,
        run,
        data_root,
        code_root,
        results_queue,
        question_scope_or_package,
        inbox_requests_dir,
        claude_adapter=None,
        antigravity_adapter=None,
        codex_adapter=None,
        codex_mode=CODEX_MODE_CLI,
        executor=None,
        which=shutil.which,
    ):
        if codex_mode not in CODEX_MODES:
            raise RealProviderError(
                "codex_mode 必須是 {}：{!r}".format(" 或 ".join(CODEX_MODES), codex_mode)
            )
        self.run = run
        self.data_root = Path(data_root)
        self.code_root = Path(code_root)
        self.results_queue = results_queue
        self.question_scope_or_package = question_scope_or_package
        self.inbox_requests_dir = Path(inbox_requests_dir)
        # 這個 WSL shell 看得到哪些 Provider 命令。注入假的 ``which`` 就等於
        # 決定了這一場的 PATH，離線測試因此不必依賴機器上真的裝了什麼。
        self.which = which
        # 三個 provider 共用一個 registry，收件牆才能停掉還在跑的真實進程。
        self.process_registry = ProcessRegistry()
        self._worker_attempt = threading.local()
        process_runner = TerminatingRunner(
            self.process_registry, key_source=self.worker_key
        )
        self.claude_adapter = claude_adapter or ClaudeAdapter(
            runner=process_runner,
            code_root=self.code_root,
            data_root=self.data_root,
        )
        # 第七席做的是跟 Claude 三席一模一樣的研究，卻用 adapter 的 60 秒
        # smoke 預設，等於同一份工作只給四分之一的時間——run
        # 20260803T113838Z 就是這樣連兩次 timeout 後失去該席。研究呼叫給它
        # 與 Claude 席相同的預算；辯論回合的呼叫仍由 turn 截止時的 cancel
        # 終止，不會因為預算變長而拖過牆。
        self.antigravity_adapter = antigravity_adapter or AntigravityAdapter(
            code_root=self.code_root,
            data_root=self.data_root,
            runner=process_runner.run_process,
            timeout_seconds=self._research_timeout_seconds(),
        )
        self.codex_adapter = codex_adapter or CodexExecAdapter(
            runner=process_runner,
            timeout_seconds=self._research_timeout_seconds(),
        )
        self.codex_mode = codex_mode
        self.executor = executor or ThreadPoolExecutor(
            max_workers=LOCAL_WORKER_COUNT, thread_name_prefix="hoya-seat"
        )
        self._owns_executor = executor is None
        self._seats = {seat.seat_id: seat for seat in load_roster()}
        self._futures = {}
        self._cancelled = set()
        self._lock = threading.Lock()

    def worker_key(self):
        """Return the process-registry key for the work this thread is running.

        The key is the attempt (or debate dispatch), never the pool thread:
        ``ProcessRegistry.terminate`` poisons the key for the rest of the run,
        and pooled threads are reused by other seats.
        """
        return getattr(self._worker_attempt, "key", None) or threading.get_ident()

    def start(self, attempt, checkpoint):
        """Dispatch one attempt, returning literal ``True`` once it is away.

        A provider this WSL ``PATH`` cannot resolve is refused here rather than
        at the end of a full research timeout: the seat would otherwise lose its
        whole recovery window waiting for a CLI that was never installed. The
        refusal is published on the same queue as any other failure, so the
        scheduler starts the backup and neither the run nor the web app stops.
        """
        seat = self._seat(attempt.seat_id)
        provider = self._provider_for(attempt)
        self._require_provider_model(attempt, provider)
        if not self._provider_cli_available(attempt, provider):
            # 沒有命令就沒有進程，所以這裡回報「沒有啟動」——回 True 會讓
            # scheduler 記下一次不存在的啟動，summary 與 Live 之後都會宣稱這席
            # 開始研究過。失敗訊息已經送上 queue，recovery 照常由它觸發。
            return False
        if provider == PROVIDER_CODEX:
            self._write_dispatch_request(attempt)
        worker = self._worker_for(provider)
        if worker is None:
            return True  # inbox 模式：外部 Codex bridge 擁有這一席
        prompt = self._prompt(seat, attempt, checkpoint)
        work_dir = self._new_work_dir(attempt.seat_id, attempt.attempt_id)
        self._submit(attempt, worker, prompt, work_dir)
        return True

    def _provider_for(self, attempt):
        """Which provider family runs this attempt: its own, else the seat's.

        A backup carries its own provider; a primary — and any attempt built by
        a caller that predates the column — falls back to the roster, so the
        seat's identity still decides and nothing has to guess.
        """
        return attempt.provider or SEAT_PROVIDERS[attempt.seat_id]

    def _provider_cli_available(self, attempt, provider):
        """Publish ``provider_cli_missing`` and refuse if the CLI is not on PATH."""
        try:
            require_provider_cli(provider, self.which)
        except ProviderCliMissing as exc:
            self._publish(
                ("failure", attempt.attempt_id, PROVIDER_CLI_MISSING, str(exc))
            )
            return False
        return True

    def _worker_for(self, provider):
        """Pick the provider worker, or ``None`` when an external channel owns it."""
        if provider == PROVIDER_CODEX:
            return None if self.codex_mode == CODEX_MODE_INBOX else self._run_codex
        if provider == PROVIDER_ANTIGRAVITY:
            return self._run_antigravity
        return self._run_claude

    def start_debate(self, dispatch):
        """Dispatch one debate turn; ``False`` means this seat has no local channel."""
        provider = dispatch.provider or SEAT_PROVIDERS[dispatch.seat_id]
        if dispatch.requested_model is not None:
            expected_model = PROVIDER_MODELS[provider]
            if dispatch.requested_model != expected_model:
                raise RealProviderError(
                    "Opening lineage model {} 不屬於 provider {}（預期 {}）。".format(
                        dispatch.requested_model, provider, expected_model
                    )
                )
        worker = self._debate_worker_for(dispatch.seat_id, provider=provider)
        if worker is None:
            return False  # inbox 模式沒有辯論回填通道，該席誠實缺席
        work_dir = self._new_work_dir(dispatch.seat_id, dispatch.dispatch_id)
        future = self.executor.submit(self._debate_worker, dispatch, worker, work_dir)
        with self._lock:
            self._futures[dispatch.dispatch_id] = future
        return True

    def core_report_adapter(self, timeout_seconds):
        """Codex adapter for Core's report, terminable through the same registry."""
        return CodexExecAdapter(
            runner=TerminatingRunner(
                self.process_registry, key_source=self.worker_key
            ),
            timeout_seconds=timeout_seconds,
        )

    def checkpoint(self, attempt_id):
        """No provider here exposes a public mid-run checkpoint channel."""
        return None

    def correct(self, attempt, raw_output, exact_error):
        """No original-format correction channel exists; the repairer decides."""
        return None

    def cancel(self, attempt_id):
        return self._best_effort_stop(attempt_id)

    def terminate(self, attempt_id):
        return self._best_effort_stop(attempt_id)

    def shutdown(self, wait=True):
        """Release the owned worker pool; injected executors stay caller-owned."""
        if not self._owns_executor:
            return None
        self.executor.shutdown(wait=wait)
        return None

    def _submit(self, attempt, work, prompt, work_dir):
        future = self.executor.submit(self._worker, attempt, work, prompt, work_dir)
        with self._lock:
            self._futures[attempt.attempt_id] = future

    def _worker(self, attempt, work, prompt, work_dir):
        self._bind_worker_key(attempt.attempt_id)
        try:
            work(attempt, prompt, work_dir)
        except Exception as exc:  # a worker must never lose a seat silently
            self._publish(("failure", attempt.attempt_id, "provider_error", str(exc)))
        finally:
            self._release_worker_key()

    def _research_timeout_seconds(self):
        """本場題型的研究 timeout；舊式 scope 沒有題型時退回預設時刻表。"""
        return claude_research_timeout_seconds(
            getattr(self.question_scope_or_package, "question_type", None)
        )

    def _run_claude(self, attempt, prompt, work_dir):
        # 每個 attempt 都在自己的 work dir（獨立 Claude project scope）用固定席位
        # UUID 建立新 session；跨目錄 --resume 找不到 session，會立即 exit 1。
        request = ClaudeAttemptRequest(
            seat_id=attempt.seat_id,
            attempt_id=attempt.attempt_id,
            prompt=prompt,
            attempt_dir=work_dir,
            resume=False,
            timeout_seconds=self._research_timeout_seconds(),
            json_schema=research_envelope_schema(self.run.run_id, attempt),
            validator=validate_research_envelope_shape,
        )
        result = self.claude_adapter.run(request)
        # cancel 之後不准 resume：新進程沒有人會再 terminate 一次，會在截止後
        # 繼續燒訂閱並拖住 launch 收尾（scheduler 的 _cancel_running 只掃一輪）。
        if result.scheduler_failure_kind == "process_error" and not self._is_cancelled(
            attempt.attempt_id
        ):
            result = self._resume_claude(request, attempt)
        self._publish_research_lineage(attempt, PROVIDER_CLAUDE, result.actual_model)
        if result.scheduler_failure_kind is not None:
            # status 是 Claude 邊界自己的機器值，訊息只是給人看的附註。
            self._publish(
                (
                    "failure",
                    attempt.attempt_id,
                    claude_failure_code(result.status),
                    _failure_message(result),
                )
            )
            return
        unproven = _claude_research_proof_problem(result)
        if unproven is not None:
            code = (
                RESEARCH_PROOF_MISSING
                if unproven == NO_RESEARCH_PROOF
                else PROVIDER_OUTPUT_REJECTED
            )
            self._publish(("failure", attempt.attempt_id, code, unproven))
            return
        self._publish(("result", attempt.attempt_id, _dumps(result.structured_output)))

    def _resume_claude(self, request, attempt):
        """Retry once in the same fixed session, as the preflight fallback does.

        Resume 必須沿用同一個 work dir：Claude session 依專案目錄隔離，
        換目錄 resume 會直接 No conversation found。
        """
        retry_attempt_id = attempt.attempt_id + RESUME_ATTEMPT_SUFFIX
        return self.claude_adapter.run(
            self.claude_adapter.resumed_request(
                request,
                attempt_id=retry_attempt_id,
                prompt=request.prompt,
                attempt_dir=request.attempt_dir,
            )
        )

    def _run_codex(self, attempt, prompt, work_dir):
        # 每個 Codex 失敗類別自己帶著 scheduler 的 failure_kind，
        # 所以這裡只要一個 except，不必依症狀分支。
        try:
            result = self.codex_adapter.invoke(
                prompt, research_envelope_schema(self.run.run_id, attempt), work_dir
            )
        except CodexExecError as exc:
            self._publish(
                ("failure", attempt.attempt_id, codex_failure_code(exc), str(exc))
            )
            return
        self._publish_research_lineage(
            attempt, PROVIDER_CODEX, getattr(self.codex_adapter, "model", None)
        )
        if result.search_invocations < 1:
            self._publish(
                (
                    "failure",
                    attempt.attempt_id,
                    RESEARCH_PROOF_MISSING,
                    NO_RESEARCH_PROOF,
                )
            )
            return
        self._publish(("result", attempt.attempt_id, _dumps(result.structured_output)))

    def _run_antigravity(self, attempt, prompt, work_dir):
        try:
            # require_search：沒有成功的 search_web 就不是真研究，直接 fail closed。
            result = self.antigravity_adapter.invoke(
                prompt,
                research_envelope_schema(self.run.run_id, attempt),
                work_dir,
                require_search=True,
            )
        except AntigravityError as exc:
            # 一個 except：型別本身就是機器值，不必依症狀分支。
            self._publish(
                ("failure", attempt.attempt_id, antigravity_failure_code(exc), str(exc))
            )
            return
        self._publish_research_lineage(
            attempt, PROVIDER_ANTIGRAVITY, result.actual_model
        )
        self._publish(("result", attempt.attempt_id, _dumps(result.structured_output)))

    def _debate_worker_for(self, seat_id, provider=None):
        """Pick the adopted provider lane, falling back to the roster lane."""
        provider = provider or SEAT_PROVIDERS[seat_id]
        if provider == PROVIDER_CODEX:
            return None if self.codex_mode == CODEX_MODE_INBOX else self._debate_codex
        if provider == PROVIDER_ANTIGRAVITY:
            return self._debate_antigravity
        return self._debate_claude

    def _debate_worker(self, dispatch, work, work_dir):
        self._bind_worker_key(dispatch.dispatch_id)
        try:
            work(dispatch, work_dir)
        except Exception as exc:  # a worker must never lose a seat silently
            self._publish_debate_failure(dispatch, "provider_error", str(exc))
        finally:
            self._release_worker_key()

    def _debate_claude(self, dispatch, work_dir):
        # 辯論一律 fresh invocation：cancel 過的 turn 不得 resume，也不需要 resume。
        result = self.claude_adapter.run(
            ClaudeAttemptRequest(
                seat_id=dispatch.seat_id,
                attempt_id=dispatch.dispatch_id,
                prompt=dispatch.prompt,
                attempt_dir=work_dir,
                resume=False,
                timeout_seconds=dispatch.timeout_seconds,
                json_schema=dispatch.schema,
                validator=dispatch.validator,
                allow_search=False,
            )
        )
        self._publish_lineage(dispatch, "claude", result.actual_model, result.elapsed_ms)
        failure_kind = result.scheduler_failure_kind
        if failure_kind is not None:
            self._publish_debate_failure(dispatch, failure_kind, _failure_message(result))
            return
        if _non_negative_int(result.web_search_requests) > 0:
            # 能力層關了還是檢索到，代表這份回覆不是只依快照產生：拒收，不當辯論
            # 內容。codex 與 agy 都有這道結果層防線，claude 不能只靠 --tools 就算數。
            self._publish_debate_failure(dispatch, "provider_error", POST_SEAL_SEARCH)
            return
        self._publish_debate_result(dispatch, result.structured_output)

    def _debate_codex(self, dispatch, work_dir):
        try:
            # 辯論階段不得再上網：搜尋能力直接關閉，只讀已 sealed 的快照。
            result = self.codex_adapter.invoke(
                dispatch.prompt,
                dispatch.schema,
                work_dir,
                allow_search=False,
                timeout_seconds=dispatch.timeout_seconds,
            )
        except CodexExecError as exc:
            self._publish_debate_failure(dispatch, exc.failure_kind, str(exc))
            return
        self._publish_lineage(
            dispatch, "codex", getattr(self.codex_adapter, "model", None), result.elapsed_ms
        )
        if result.search_activity_count > 0 or result.malformed_event_count > 0:
            # 能力關了還搜到，代表這份回覆不是只依快照產生：拒收，不當證據。
            self._publish_debate_failure(dispatch, "provider_error", POST_SEAL_SEARCH)
            return
        self._publish_debate_result(dispatch, result.structured_output)

    def _debate_antigravity(self, dispatch, work_dir):
        try:
            # agy 沒有關閉工具的旗標，能力層關不掉；改在結果層攔：
            # 封存後只要真的執行過 search_web，這份回覆一律不收。
            result = self.antigravity_adapter.invoke(
                dispatch.prompt,
                dispatch.schema,
                work_dir,
                allow_search=False,
                timeout_seconds=dispatch.timeout_seconds,
            )
        except AntigravityTimeout as exc:
            self._publish_debate_failure(dispatch, "timeout", str(exc))
            return
        except AntigravityPostSealSearch:
            self._publish_debate_failure(dispatch, "provider_error", POST_SEAL_SEARCH)
            return
        except AntigravityError as exc:
            self._publish_debate_failure(dispatch, "provider_error", str(exc))
            return
        self._publish_lineage(
            dispatch,
            "antigravity",
            result.actual_model,
            _seconds_to_ms(result.duration_seconds),
        )
        self._publish_debate_result(dispatch, result.structured_output)

    def _publish_debate_result(self, dispatch, structured_output):
        self._publish(
            (DEBATE_RESULT_MESSAGE, dispatch.dispatch_id, _dumps(structured_output))
        )

    def _publish_debate_failure(self, dispatch, failure_kind, message):
        self._publish(
            (DEBATE_FAILURE_MESSAGE, dispatch.dispatch_id, failure_kind, message)
        )

    def _publish_research_lineage(self, attempt, provider, actual_model):
        """Say which provider and model actually answered this research attempt.

        Published before the result or failure it belongs to, and silenced by
        cancellation exactly like them — which is where research parts company
        with a debate turn. A cancelled turn's lineage still describes a seat
        that is going to speak again; a cancelled research attempt has already
        been given its terminal outcome by the scheduler, so anything more from
        that worker would only re-open the queue the cutoff sweep just closed.
        """
        self._publish(
            (
                RESEARCH_LINEAGE_MESSAGE,
                attempt.attempt_id,
                {
                    "seat_id": attempt.seat_id,
                    "attempt_id": attempt.attempt_id,
                    "attempt_kind": attempt.kind,
                    "provider": provider,
                    "actual_model": actual_model,
                },
            )
        )

    def _publish_lineage(self, dispatch, provider, actual_model, elapsed_ms):
        """Model provenance is true even for a cancelled turn, so it bypasses cancel."""
        self.results_queue.put(
            (
                PROVIDER_LINEAGE_MESSAGE,
                dispatch.seat_id,
                {
                    "phase": dispatch.phase,
                    "seat_id": dispatch.seat_id,
                    "dispatch_id": dispatch.dispatch_id,
                    "provider": provider,
                    "requested_provider": dispatch.provider,
                    "requested_model": dispatch.requested_model,
                    "actual_model": actual_model,
                    "elapsed_ms": elapsed_ms,
                    "research_attempt_id": dispatch.research_attempt_id,
                    "adopted_evidence_sha256": dispatch.adopted_evidence_sha256,
                },
            )
        )

    def _write_dispatch_request(self, attempt):
        attempt_id = _safe_segment(attempt.attempt_id)
        self.inbox_requests_dir.mkdir(parents=True, exist_ok=True)
        target = self.inbox_requests_dir / "{}.json".format(attempt_id)
        payload = {
            "schema_version": CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "seat_id": attempt.seat_id,
            "attempt_id": attempt_id,
            "kind": attempt.kind,
            "reason": attempt.reason,
            "provider": self._provider_for(attempt),
            "model": attempt.model,
            "parent_attempt_id": attempt.parent_attempt_id,
            "requested_at_utc": iso_utc(datetime.now(timezone.utc)),
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError as exc:
            raise RealProviderError(
                "dispatch request 不得覆寫：{}".format(target)
            ) from exc
        return target

    def _new_work_dir(self, seat_id, name):
        """Work directories never live under ``attempts/``; that path is sealed."""
        path = self.run.path / "agents" / _safe_segment(seat_id) / "work"
        path = path / _safe_segment(name)
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RealProviderError("attempt 工作目錄不得重用：{}".format(path)) from exc
        return path

    def _prompt(self, seat, attempt, checkpoint):
        return build_attempt_prompt(
            self.question_scope_or_package, seat, self.run.run_id, attempt, checkpoint
        )

    def _publish(self, message):
        attempt_id = message[1]
        if self._is_cancelled(attempt_id):
            return
        self.results_queue.put(message)

    def _is_cancelled(self, attempt_id):
        with self._lock:
            return attempt_id in self._cancelled

    def _bind_worker_key(self, attempt_id):
        """Key this worker thread's provider processes by the attempt it runs."""
        self._worker_attempt.key = attempt_id

    def _release_worker_key(self):
        self._worker_attempt.key = None

    def _best_effort_stop(self, attempt_id):
        """Silence a late worker and stop its process; never raise.

        Future.cancel() 只擋得住還沒開始的工作；已經在跑的 provider 進程要靠
        registry 才停得下來，否則它會在截止後繼續燒訂閱並拖住 launch 收尾。
        terminate 同時把這個 attempt 的 key 毒起來，所以連「取消時剛好沒有進程、
        下一秒才生出來」的 retry 也會一註冊就被停掉。
        """
        try:
            with self._lock:
                self._cancelled.add(attempt_id)
                future = self._futures.get(attempt_id)
            if future is not None:
                future.cancel()
            self.process_registry.terminate(attempt_id)
        except Exception:  # cancellation must not break the acceptance sweep
            return None
        return None

    def _require_provider_model(self, attempt, provider):
        """Refuse any model this provider family cannot honestly serve.

        A backup changes the provider, so the model it is allowed to claim is
        that provider's own fixed one — never the seat's primary model wearing
        another CLI's name.
        """
        expected = PROVIDER_MODELS[provider]
        if attempt.model == expected:
            return
        raise RealProviderError(
            "席位 {} 的 attempt 模型 {} 沒有可派發的真實 provider 通道"
            "（{} 家族固定為 {}）；fail closed，不以其他模型冒名執行。".format(
                attempt.seat_id, attempt.model, provider, expected
            )
        )

    def _seat(self, seat_id):
        try:
            return self._seats[seat_id]
        except KeyError as exc:
            raise RealProviderError("未知席位：{}".format(seat_id)) from exc


def _output_instructions(run_id, attempt, checkpoint):
    lines = [
        "",
        "## 本次 attempt 的唯一交付格式",
        "- run_id：{}".format(run_id),
        "- seat_id：{}".format(attempt.seat_id),
        "- attempt_id：{}".format(attempt.attempt_id),
        "- 唯一交付是單一 JSON envelope 物件："
        '{"seat_id": "<本席 seat_id>", "evidence_cards": [ ... ]}。',
        "- envelope 的 seat_id 為必填，且必須精確等於 {}；"
        "不得改用裸陣列，也不得加入其他欄位。".format(attempt.seat_id),
        "- evidence_cards 最多 {} 張；每張必須包含 schema_version=\"{}\"、run_id、"
        "seat_id、attempt_id、phase=\"research\"、created_at_utc、elapsed_ms "
        "與全部內容欄位。".format(MAX_EVIDENCE_CARDS_PER_SEAT, CONTRACT_VERSION),
        "- 每張卡的 run_id、seat_id、attempt_id 必須與上列值完全一致。",
        "- statement 與 credibility_note 必須使用繁體中文；"
        "excerpt 必須保留來源原文或原始數值。",
        "- 你本身就是 research background agent；不得建立任何額外 agent、"
        "不得寫任何檔案，唯一交付就是這個 JSON envelope。",
        "- 除該 JSON envelope 外，不要輸出任何其他文字。",
    ]
    if checkpoint is not None:
        lines.append("- 續作用的公開 checkpoint：")
        lines.append(json.dumps(checkpoint, ensure_ascii=False))
    lines.append("")
    return "\n".join(lines)


def _unwrap_envelope(payload, attempt):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("研究輸出必須是 envelope 物件或證據卡陣列")
    if payload.get("seat_id") != attempt.seat_id:
        raise ValueError(
            "envelope seat_id 與席位不一致：預期 {}，實際 {!r}".format(
                attempt.seat_id, payload.get("seat_id")
            )
        )
    cards = payload.get("evidence_cards")
    if not isinstance(cards, list):
        raise ValueError("envelope 的 evidence_cards 必須為陣列")
    return cards


def _require_card_count(cards):
    """零證據不是研究結果：空 envelope 會讓一個沉默席位被標成 adopted，
    壓掉本來該啟動的替補流程。"""
    if not cards:
        raise ValueError("研究輸出必須至少包含一張證據卡")
    if len(cards) <= MAX_EVIDENCE_CARDS_PER_SEAT:
        return
    raise ValueError(
        "證據卡數量 {} 超過單席上限 {}".format(len(cards), MAX_EVIDENCE_CARDS_PER_SEAT)
    )


def _require_lineage(card, attempt):
    if card["seat_id"] == attempt.seat_id and card["attempt_id"] == attempt.attempt_id:
        return
    raise ValueError(
        "證據卡 lineage 與 attempt 不一致：{}/{} 應為 {}/{}".format(
            card["seat_id"], card["attempt_id"], attempt.seat_id, attempt.attempt_id
        )
    )


def _safe_segment(value):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise RealProviderError("路徑片段必須為安全的非空字串：{!r}".format(value))
    if Path(value).name != value or "/" in value or "\\" in value:
        raise RealProviderError("路徑片段不得包含路徑：{!r}".format(value))
    return value


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


def _seconds_to_ms(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    return int(seconds * 1000)


def _claude_research_proof_problem(result):
    """真研究的兩個硬證明：模型真的是 opus，且真的動用了線上檢索。

    兩條判準都必須與賽前 READY 閘門（claude_adapter.run_claude_preflight）同一套
    定義，否則會出現「賽前發 READY、正式 run 卻整排誤殺」的分歧：模型判準是
    "opus" in actual_model；檢索次數以 result.web_search_requests（usage 與
    modelUsage 的對帳最大值）為下限，再加計 usage 的 web_fetch。
    """
    actual_model = result.actual_model or ""
    if "opus" not in actual_model:
        return "actual_model_mismatch:{}".format(actual_model or "未回報")
    proven_calls = max(
        _non_negative_int(result.web_search_requests),
        _live_research_calls(result.usage),
    )
    if proven_calls < 1:
        return NO_RESEARCH_PROOF
    return None


def _live_research_calls(usage):
    server = usage.get("server_tool_use") if isinstance(usage, dict) else None
    if not isinstance(server, dict):
        return 0
    return sum(
        _non_negative_int(server.get(field))
        for field in ("web_search_requests", "web_fetch_requests")
    )


def _non_negative_int(value):
    """缺欄位、null 或非整數一律當成 0：不能靠格式漂移換到通過。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _failure_message(result):
    """事件流訊息帶上 stderr 摘要，失敗現場不必重跑就能診斷。"""
    message = result.error or result.status
    stderr = (result.stderr or "").strip()
    if stderr:
        message = "{}：{}".format(message, stderr[:200])
    return message
