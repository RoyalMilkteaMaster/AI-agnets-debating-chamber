"""Ticket 03: attempt recovery, terminal outcomes and visible lineage.

Nothing here reaches a real provider. Every executable lookup goes through an
injected ``which``, every provider adapter is a scripted seam, and the one place
a real CLI would be resolved is asserted to be missing from the sources.
"""

import io
import json
import queue
import subprocess
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

from hoya_market_agents import launcher, provider_cli
from hoya_market_agents.antigravity_adapter import AntigravityAdapter
from hoya_market_agents.claude_adapter import (
    ClaudeAdapter,
    ProcessOutput,
    ProcessRegistry,
)
from hoya_market_agents.codex_exec_adapter import (
    CodexExecEmptyOutputError,
    CodexExecOutputError,
    CodexExecResult,
    CodexExecTreeTerminationError,
)
from hoya_market_agents.provider_cli import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCliMissing,
    require_provider_cli,
    resolve_provider_cli,
)
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.real_provider import (
    BACKUP_CANDIDATES,
    LOCAL_WORKER_COUNT,
    PROVIDER_MODELS,
    REPLACEMENT_MODELS,
    RESEARCH_ATTEMPT_CAPACITY,
    RESEARCH_ENVELOPE_SCHEMA,
    RESEARCH_LINEAGE_MESSAGE,
    SEAT_PROVIDERS,
    PRIMARY_MODELS,
    RealEvidenceGateway,
    RealSeatRunner,
    research_envelope_schema,
)
from hoya_market_agents.recovery_state_machine import ResearchAttempt
from hoya_market_agents.research_scheduler import (
    ACCEPT_RESULTS_UNTIL_MS,
    FAILURE_CODES,
    PROCESS_TREE_TERMINATION_FAILED,
    PROVIDER_CLI_MISSING,
    PROVIDER_EMPTY_OUTPUT,
    PROVIDER_MALFORMED_OUTPUT,
    PROVIDER_START_FAILED,
    PROVIDER_TIMEOUT,
    REPLACEMENT_MS,
    RESEARCH_FIRST_VALID_ALREADY_ADOPTED,
    RESEARCH_PROOF_MISSING,
    START_RETRY_MS,
    TERMINAL_OUTCOMES,
    ResearchScheduler,
    failure_code_for,
)
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS
from hoya_market_agents.webapp import live
from hoya_market_agents.webapp.pages import live_page
from tests.fakes import FixedClock
from tests.test_claude_adapter import FakeKillpg, FakeProcessGroup
from tests.test_real_provider import agy_stream, claude_stdout, envelope
from tests.test_research_scheduler import (
    RUN_ID,
    FakeProcessRunner,
    JsonEvidenceGateway,
    NoRepairer,
    TrailingCommaRepairer,
    evidence_raw,
)

QUESTION = "BTC 過去 14 日的市場狀態如何？"
ALL_PROVIDERS = (PROVIDER_CODEX, PROVIDER_CLAUDE, PROVIDER_ANTIGRAVITY)
VISIBLE_PATH = "/fake/wsl/bin/{}".format
UNRECORDED = "未記錄"


def visible_which(name):
    """A WSL shell that can see all three provider commands, and nothing real."""
    return VISIBLE_PATH(name)


def blind_which(name):
    """A WSL shell whose ``PATH`` holds no provider at all."""
    return None


def take_outcomes(results, expected, lineage=None):
    """Take ``expected`` result/failure messages, parking lineage messages.

    A research worker publishes ``research_lineage`` before the result it belongs
    to, so a caller asking for outcomes must not mistake one for the other.
    """
    taken = {}
    while len(taken) < expected:
        message = results.get(timeout=20)
        if message[0] == RESEARCH_LINEAGE_MESSAGE:
            if lineage is not None:
                lineage.append(message)
            continue
        taken[message[1]] = message
    return taken


def attempt_for(seat_id, provider=None, kind="primary", suffix="a1"):
    provider = provider or SEAT_PROVIDERS[seat_id]
    return ResearchAttempt(
        attempt_id="{}-{}".format(seat_id, suffix),
        seat_id=seat_id,
        model=PROVIDER_MODELS[provider],
        kind=kind,
        original_attempt_id="{}-a1".format(seat_id),
        provider=provider,
    )


class ProviderCliResolutionTest(unittest.TestCase):
    """R-007／15.7: a provider is whatever this WSL ``PATH`` resolves."""

    def test_the_three_commands_are_resolved_from_the_current_wsl_path(self):
        asked = []

        def which(name):
            asked.append(name)
            return VISIBLE_PATH(name)

        resolved = [resolve_provider_cli(provider, which) for provider in ALL_PROVIDERS]

        self.assertEqual(["codex", "claude", "agy"], asked)
        self.assertEqual(
            ["/fake/wsl/bin/codex", "/fake/wsl/bin/claude", "/fake/wsl/bin/agy"],
            resolved,
        )

    def test_a_command_this_shell_cannot_find_is_a_stable_missing_failure(self):
        for provider in ALL_PROVIDERS:
            with self.subTest(provider=provider):
                with self.assertRaises(ProviderCliMissing) as caught:
                    require_provider_cli(provider, blind_which)

                self.assertEqual(PROVIDER_CLI_MISSING, caught.exception.failure_code)
                self.assertEqual(provider, caught.exception.provider)

    def test_no_module_hardcodes_a_personal_home_directory(self):
        package = Path(provider_cli.__file__).parent
        offenders = sorted(
            path.name
            for path in package.rglob("*.py")
            if "/home/leslie" in path.read_text(encoding="utf-8")
        )

        self.assertEqual([], offenders)


class BackupRosterPolicyTest(unittest.TestCase):
    """R-008: seven fixed seats, one different-provider backup each."""

    def test_the_primary_roster_stays_three_codex_three_claude_one_antigravity(self):
        self.assertEqual(
            {PROVIDER_CODEX: 3, PROVIDER_CLAUDE: 3, PROVIDER_ANTIGRAVITY: 1},
            dict(Counter(SEAT_PROVIDERS[seat_id] for seat_id in SEAT_IDS)),
        )
        self.assertEqual(sorted(SEAT_IDS), sorted(SEAT_PROVIDERS))

    def test_every_seat_gets_one_backup_on_another_provider_with_its_fixed_model(self):
        for seat_id in SEAT_IDS:
            with self.subTest(seat_id=seat_id):
                backup = BACKUP_CANDIDATES[seat_id]

                self.assertIsNotNone(backup)
                self.assertNotEqual(SEAT_PROVIDERS[seat_id], backup.provider)
                self.assertEqual(PROVIDER_MODELS[backup.provider], backup.model)

    def test_the_candidate_order_is_deterministic_and_first_different_provider(self):
        self.assertEqual(PROVIDER_CLAUDE, BACKUP_CANDIDATES["news"].provider)
        self.assertEqual(PROVIDER_CODEX, BACKUP_CANDIDATES["onchain"].provider)
        self.assertEqual(PROVIDER_CODEX, BACKUP_CANDIDATES["counter-evidence"].provider)

    def test_the_worker_pool_holds_seven_primaries_and_seven_backups_at_once(self):
        self.assertEqual(2 * len(SEAT_IDS), RESEARCH_ATTEMPT_CAPACITY)
        self.assertEqual(14, RESEARCH_ATTEMPT_CAPACITY)
        self.assertGreaterEqual(LOCAL_WORKER_COUNT, RESEARCH_ATTEMPT_CAPACITY)


class SchedulerLineageTest(unittest.TestCase):
    """``attempt_outcomes`` is the only terminal-outcome authority."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.runner = FakeProcessRunner()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )

    def advance_to(self, elapsed_ms):
        self.clock.advance_ms(elapsed_ms - self.scheduler.elapsed_ms)
        self.scheduler.tick()

    def seat(self, seat_id="news"):
        return self.scheduler.recovery.seats[seat_id]

    def outcome(self, attempt_id):
        return self.scheduler.attempt_outcomes[attempt_id]

    def event_names(self):
        return [event["event"] for event in self.scheduler.events]

    def test_only_the_five_approved_terminal_outcomes_exist(self):
        self.assertEqual(
            ("adopted", "superseded", "failed", "cancelled", "late_discarded"),
            TERMINAL_OUTCOMES,
        )

    def test_every_failure_code_the_spec_names_is_in_the_stable_vocabulary(self):
        """R-008 的穩定 failure code 清單是機器值，不得只存在於訊息文字裡。"""
        required = {
            "provider_cli_missing",
            "provider_start_failed",
            "provider_timeout",
            "provider_empty_output",
            "provider_malformed_output",
            "research_proof_missing",
            "research_result_window_closed",
            "process_tree_termination_failed",
        }

        self.assertLessEqual(required, set(FAILURE_CODES))
        for code in required:
            with self.subTest(code=code):
                self.assertEqual(code, failure_code_for(code))

    def test_legacy_runner_failure_kinds_map_onto_the_stable_codes(self):
        self.assertEqual(PROVIDER_START_FAILED, failure_code_for("startup_error"))
        self.assertEqual(PROVIDER_TIMEOUT, failure_code_for("timeout"))
        self.assertEqual(PROVIDER_TIMEOUT, failure_code_for(PROVIDER_TIMEOUT))
        self.assertEqual(PROVIDER_CLI_MISSING, failure_code_for(PROVIDER_CLI_MISSING))
        with self.assertRaises(ValueError):
            failure_code_for("carrier_pigeon_lost")

    def test_a_failed_primary_starts_one_backup_on_the_other_provider(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]

        backup = self.scheduler.report_failure(
            primary.attempt_id, PROVIDER_CLI_MISSING, "PATH 上沒有 codex"
        )

        self.assertEqual("backup", backup.kind)
        self.assertEqual("news", backup.seat_id)
        self.assertEqual(PROVIDER_CLAUDE, backup.provider)
        self.assertEqual(PROVIDER_MODELS[PROVIDER_CLAUDE], backup.model)
        self.assertEqual(PROVIDER_CODEX, primary.provider)
        self.assertEqual(
            "failed", self.outcome(primary.attempt_id)["terminal_outcome"]
        )
        self.assertEqual(
            PROVIDER_CLI_MISSING, self.outcome(primary.attempt_id)["failure_code"]
        )

    def test_a_seat_never_gets_a_second_backup_and_is_then_exhausted(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        self.assertIsNone(
            self.scheduler.report_failure(backup.attempt_id, PROVIDER_TIMEOUT, "也無回覆")
        )
        self.assertEqual(2, len(self.seat().attempts))
        self.assertIn("recovery_exhausted", self.event_names())

    def test_one_exhausted_seat_never_stops_the_other_six(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")
        self.scheduler.report_failure(backup.attempt_id, PROVIDER_TIMEOUT, "也無回覆")

        other = self.seat("onchain").attempts[0]

        self.assertEqual(
            "adopted", self.scheduler.submit_result(other.attempt_id, evidence_raw(other))
        )

    def test_fourteen_attempts_enter_the_window_before_the_receiving_wall(self):
        self.scheduler.start()
        for seat_id in SEAT_IDS:
            primary = self.seat(seat_id).attempts[0]
            self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        started = [
            event for event in self.scheduler.events if event["event"] == "attempt_started"
        ]

        self.assertEqual(14, len(self.scheduler.attempts))
        self.assertEqual(14, len(started))
        self.assertTrue(
            all(event["elapsed_ms"] < ACCEPT_RESULTS_UNTIL_MS for event in started)
        )
        self.assertEqual(
            7, len([item for item in self.scheduler.attempts.values() if item.kind == "backup"])
        )

    def test_a_terminal_outcome_and_failure_are_written_exactly_once(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "第一次逾時")
        before = dict(self.outcome(primary.attempt_id))

        self.assertIsNone(
            self.scheduler.report_failure(
                primary.attempt_id, PROVIDER_CLI_MISSING, "後到的雜訊"
            )
        )
        self.assertEqual(before, self.outcome(primary.attempt_id))
        self.assertEqual("failed", before["terminal_outcome"])
        self.assertEqual(PROVIDER_TIMEOUT, before["failure_code"])
        self.assertEqual("第一次逾時", before["failure_message"])

    def test_a_valid_result_after_a_timeout_is_diagnostic_only(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        verdict = self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        self.assertEqual("diagnostic", verdict)
        self.assertEqual("failed", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(PROVIDER_TIMEOUT, self.outcome(primary.attempt_id)["failure_code"])
        self.assertIsNone(self.seat().adopted_attempt_id)
        self.assertNotIn("news", self.scheduler.adopted_records)
        self.assertFalse((self.run.path / "agents" / "news" / "adopted.json").exists())
        self.assertTrue(
            (self.run.path / "diagnostics" / "attempts" / "news-a1.json").is_file()
        )

    def test_the_backup_is_adopted_without_moving_the_seat_identity(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        verdict = self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        self.assertEqual("adopted", verdict)
        self.assertEqual(backup.attempt_id, self.seat().adopted_attempt_id)
        record = self.outcome(backup.attempt_id)
        self.assertEqual("adopted", record["terminal_outcome"])
        self.assertEqual("news", record["seat_id"])
        self.assertEqual(PROVIDER_CLAUDE, record["provider"])
        self.assertEqual(PROVIDER_MODELS[PROVIDER_CLAUDE], record["requested_model"])
        self.assertEqual("backup", record["attempt_kind"])
        self.assertIsNone(record["failure_code"])

    def test_a_second_valid_result_after_adoption_is_never_adopted(self):
        """採用發生的當下，同席其他 attempt 就已經是 ``cancelled``（Reviewer A1）。

        所以後到的有效結果不是「驗證通過但輸掉採用」，而是「送到一個已經結案的
        attempt」——只留診斷。``superseded`` 仍然有自己的來源，見
        ``BackupFirstAdoptionTest`` 裡那條 run 目錄已有 adopted 紀錄的路徑。
        """
        self.runner.start_behaviors["news-a1"] = False
        self.scheduler.start()
        self.advance_to(START_RETRY_MS)
        primary, backup = self.seat().attempts

        self.assertEqual(
            "adopted", self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))
        )
        verdict = self.scheduler.submit_result(
            primary.attempt_id, evidence_raw(primary, "late")
        )

        self.assertEqual("diagnostic", verdict)
        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(backup.attempt_id, self.seat().adopted_attempt_id)

    def test_the_receiving_wall_cancels_what_is_still_running(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]

        self.advance_to(ACCEPT_RESULTS_UNTIL_MS)

        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(
            "late", self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))
        )
        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertFalse(self.scheduler.adopted_records)

    def test_a_result_arriving_after_the_window_closed_writes_late_discarded(self):
        """收件窗是 scheduler 的公開狀態。這裡只關窗、不觸發收件牆的 cancel，
        才能證明晚到結果自己寫下 ``late_discarded``，而不是靠別人代寫。"""
        self.scheduler.start()
        primary = self.seat().attempts[0]
        self.scheduler.accepting_results = False

        verdict = self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        self.assertEqual("late", verdict)
        self.assertEqual(
            "late_discarded", self.outcome(primary.attempt_id)["terminal_outcome"]
        )
        self.assertTrue(any((self.run.path / "late").iterdir()))
        self.assertFalse(self.scheduler.adopted_records)

    def test_an_adopted_attempt_keeps_its_outcome_against_a_late_failure(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        self.assertIsNone(
            self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "後到的逾時")
        )
        self.assertEqual("adopted", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertIsNone(self.outcome(primary.attempt_id)["failure_code"])
        self.assertEqual(primary.attempt_id, self.seat().adopted_attempt_id)

    def test_a_startup_error_is_failed_with_the_start_failure_code(self):
        self.runner.start_behaviors["news-a1"] = RuntimeError("provider unavailable")

        self.scheduler.start()

        record = self.outcome("news-a1")
        self.assertEqual("failed", record["terminal_outcome"])
        self.assertEqual(PROVIDER_START_FAILED, record["failure_code"])
        self.assertEqual("backup", self.seat().attempts[-1].kind)

    def test_unrepairable_output_is_failed_with_the_malformed_output_code(self):
        self.scheduler.format_repairer = NoRepairer()
        self.scheduler.start()
        primary = self.seat().attempts[0]

        self.assertEqual(
            "unrepairable", self.scheduler.submit_result(primary.attempt_id, "not-json")
        )
        record = self.outcome(primary.attempt_id)
        self.assertEqual("failed", record["terminal_outcome"])
        self.assertEqual(PROVIDER_MALFORMED_OUTPUT, record["failure_code"])

    def test_the_actual_provider_and_model_are_recorded_without_touching_the_outcome(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]

        self.scheduler.record_lineage(
            primary.attempt_id, provider=PROVIDER_CODEX, actual_model="gpt-5.6-sol"
        )
        self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        record = self.outcome(primary.attempt_id)
        self.assertEqual(PROVIDER_CODEX, record["actual_provider"])
        self.assertEqual("gpt-5.6-sol", record["actual_model"])
        self.assertEqual("adopted", record["terminal_outcome"])

    def test_every_research_event_carries_the_attempt_lineage(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        launches = [
            event
            for event in self.scheduler.events
            if event["event"] == "attempt_launch_requested"
        ]

        for event in launches:
            self.assertLessEqual(
                {"seat_id", "attempt_id", "provider", "requested_model", "attempt_kind", "phase"},
                set(event),
            )
        backup = [event for event in launches if event["attempt_kind"] == "backup"][0]
        self.assertEqual(PROVIDER_CLAUDE, backup["provider"])
        self.assertEqual(PROVIDER_MODELS[PROVIDER_CLAUDE], backup["requested_model"])

    def test_the_attempt_summary_projects_one_row_per_seat(self):
        self.scheduler.start()
        primary = self.seat().attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        summary = self.scheduler.attempt_summary()

        self.assertEqual(list(SEAT_IDS), [seat["seat_id"] for seat in summary])
        news = [seat for seat in summary if seat["seat_id"] == "news"][0]
        self.assertTrue(news["adopted"])
        self.assertEqual(backup.attempt_id, news["adopted_attempt_id"])
        self.assertFalse(news["exhausted"])
        self.assertEqual(PROVIDER_CODEX, news["provider"])
        self.assertEqual(
            [PROVIDER_CODEX, PROVIDER_CLAUDE],
            [item["provider"] for item in news["attempts"]],
        )
        self.assertEqual(
            ["failed", "adopted"],
            [item["terminal_outcome"] for item in news["attempts"]],
        )
        self.assertEqual(
            [PROVIDER_TIMEOUT, None], [item["failure_code"] for item in news["attempts"]]
        )


class BackupFirstAdoptionTest(unittest.TestCase):
    """Reviewer B1：某席 backup 先回來時，同席還在跑的 primary 必須當場封死。

    這裡刻意不推進到收件牆。收件牆的 sweep 會取消所有還在跑的 attempt，所以
    只要靠它，測試就分不出「採用當下就封死」與「拖到牆才封死」——而那段空窗
    正是 late primary 有機會被採用的地方。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.runner = FakeProcessRunner()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )

    def advance_to(self, elapsed_ms):
        self.clock.advance_ms(elapsed_ms - self.scheduler.elapsed_ms)
        self.scheduler.tick()

    def backup_first(self, seat_id="news"):
        """Both attempts of one seat running, then the backup answers first.

        T+2:35 是既有的 replacement 里程碑：primary 還沒交卷的席位會在那裡拿到
        backup，所以兩個 attempt 同時在跑，不必假造任何狀態。
        """
        self.scheduler.start()
        self.advance_to(REPLACEMENT_MS)
        state = self.scheduler.recovery.seats[seat_id]
        primary, backup = state.attempts
        self.assertEqual("backup", backup.kind)
        self.assertEqual(
            {primary.attempt_id, backup.attempt_id}, state.started_attempt_ids
        )
        return primary, backup

    def outcome(self, attempt_id):
        return self.scheduler.attempt_outcomes[attempt_id]

    def test_adopting_the_backup_seals_the_still_running_primary_at_once(self):
        primary, backup = self.backup_first()

        verdict = self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        self.assertEqual("adopted", verdict)
        self.assertEqual("adopted", self.outcome(backup.attempt_id)["terminal_outcome"])
        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(
            RESEARCH_FIRST_VALID_ALREADY_ADOPTED,
            self.outcome(primary.attempt_id)["failure_code"],
        )

    def test_the_loser_is_cancelled_and_terminated_immediately_and_alone(self):
        primary, backup = self.backup_first()

        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        # 「當下」＝ submit_result 回來時就已經呼叫過，不是等 tick 或收件牆。
        self.assertEqual([primary.attempt_id], self.runner.cancelled)
        self.assertEqual([primary.attempt_id], self.runner.terminated)

    def test_the_sealed_primary_keeps_its_terminal_outcome_write_once(self):
        primary, backup = self.backup_first()
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))
        before = dict(self.outcome(primary.attempt_id))

        self.assertIsNone(
            self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "後到")
        )

        self.assertEqual(before, self.outcome(primary.attempt_id))

    def test_a_late_primary_result_is_diagnostic_and_never_adopted(self):
        primary, backup = self.backup_first()
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        verdict = self.scheduler.submit_result(
            primary.attempt_id, evidence_raw(primary, "late")
        )

        self.assertEqual("diagnostic", verdict)
        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(
            backup.attempt_id, self.scheduler.recovery.seats["news"].adopted_attempt_id
        )
        adopted = json.loads(
            (self.run.path / "agents" / "news" / "adopted.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(backup.attempt_id, adopted["attempt_id"])

    def test_sealing_one_seat_never_touches_another_seat(self):
        _, backup = self.backup_first("news")
        other = self.scheduler.recovery.seats["onchain"]

        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        for attempt in other.attempts:
            with self.subTest(attempt_id=attempt.attempt_id):
                self.assertNotIn(attempt.attempt_id, self.runner.cancelled)
                self.assertNotIn(attempt.attempt_id, self.runner.terminated)
                self.assertIsNone(
                    self.outcome(attempt.attempt_id)["terminal_outcome"]
                )
        still_open = other.attempts[0]
        self.assertEqual(
            "adopted",
            self.scheduler.submit_result(
                still_open.attempt_id, evidence_raw(still_open)
            ),
        )

    def pending_primary(self, seat_id="news"):
        """A seat whose primary is *pending*, not finished, when its backup starts.

        ``start`` returning ``False`` means the dispatch was requested and has
        not begun — ``attempt_start_pending``. The provider may still register a
        process afterwards, so this attempt is every bit as live as a started
        one, and unsealing it is what lets a late generation run to the wall.
        """
        self.runner.start_behaviors["{}-a1".format(seat_id)] = False
        self.scheduler.start()
        self.advance_to(START_RETRY_MS)
        primary, backup = self.scheduler.recovery.seats[seat_id].attempts
        self.assertNotIn(
            primary.attempt_id, self.scheduler.recovery.seats[seat_id].started_attempt_ids
        )
        return primary, backup

    def test_a_pending_sibling_is_sealed_and_stopped_like_a_started_one(self):
        """Reviewer A1：pending 不是「永遠不會跑」，所以不能是「不必封」。"""
        primary, backup = self.pending_primary()

        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(
            RESEARCH_FIRST_VALID_ALREADY_ADOPTED,
            self.outcome(primary.attempt_id)["failure_code"],
        )
        self.assertEqual([primary.attempt_id], self.runner.cancelled)
        self.assertEqual([primary.attempt_id], self.runner.terminated)

    def test_a_sealed_pending_sibling_can_no_longer_be_adopted(self):
        primary, backup = self.pending_primary()
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        verdict = self.scheduler.submit_result(
            primary.attempt_id, evidence_raw(primary, "late")
        )

        self.assertEqual("diagnostic", verdict)
        self.assertEqual("cancelled", self.outcome(primary.attempt_id)["terminal_outcome"])
        self.assertEqual(
            backup.attempt_id, self.scheduler.recovery.seats["news"].adopted_attempt_id
        )

    def test_a_valid_result_loses_to_an_already_adopted_seat_as_superseded(self):
        """``superseded`` 仍然是活的終局，只是它的來源只剩一個。

        封存同席其他 attempt 之後，同一個 scheduler 內不會再有第二個 attempt
        走到「驗證通過但採用不了」；會走到那裡的，是 run 目錄裡早就有 adopted
        紀錄（例如上一個 scheduler 實例已經採用過）的情形。這正是
        ``record_attempt`` 回報 ``False`` 而席位狀態還不知道的那一格。
        """
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        self.run.write_json(
            "agents/news/adopted.json",
            {
                "run_id": RUN_ID,
                "seat_id": "news",
                "attempt_id": "news-a0",
                "validated_path": "agents/news/attempts/news-a0/validated.json",
            },
            source="first valid attempt selection",
        )

        verdict = self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))

        self.assertEqual("diagnostic", verdict)
        self.assertEqual(
            "superseded", self.outcome(primary.attempt_id)["terminal_outcome"]
        )


class LateRegistrationAfterSealTest(unittest.TestCase):
    """Reviewer A1：封存之後才註冊的 generation 也必須當場被回收。

    這裡不另建登記處。停止走的是既有 runner 的 cancel／terminate，底下是既有的
    ``ProcessRegistry``——它會把 key 毒起來，所以「取消在前、進程在後」這個順序
    正是它本來就要處理的情況。測試只是證明 scheduler 真的把那條路走了。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        self.loser_group = FakeProcessGroup(9101)
        self.other_group = FakeProcessGroup(9102)
        self.runner = RegistryBackedRunner(
            ProcessRegistry(killpg=FakeKillpg(self.loser_group, self.other_group)),
            pending={"news-a1"},
        )
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )

    def adopt_the_backup(self):
        self.scheduler.start()
        self.clock.advance_ms(START_RETRY_MS)
        self.scheduler.tick()
        primary, backup = self.scheduler.recovery.seats["news"].attempts
        self.assertEqual(
            "adopted", self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))
        )
        return primary, backup

    def test_a_process_registering_after_the_seal_is_reclaimed_at_once(self):
        primary, _ = self.adopt_the_backup()

        # 這一步就是 A1 描述的競態：provider 在取消之後才把進程交給登記處。
        self.runner.registry.track(primary.attempt_id, self.loser_group, grace_seconds=0)

        self.assertFalse(self.loser_group.alive)

    def test_another_seat_registering_at_the_same_time_is_untouched(self):
        self.adopt_the_backup()
        other = self.scheduler.recovery.seats["onchain"].attempts[0]

        self.runner.registry.track(other.attempt_id, self.other_group, grace_seconds=0)

        self.assertTrue(self.other_group.alive)
        self.assertEqual(["news-a1"], self.runner.cancelled)
        self.assertEqual(["news-a1"], self.runner.terminated)


class PerAttemptSchemaTest(unittest.TestCase):
    """R-009: one deep-copied, lineage-pinned schema per research invocation."""

    def test_the_generic_template_is_never_mutated_in_place(self):
        before = json.dumps(RESEARCH_ENVELOPE_SCHEMA, sort_keys=True, ensure_ascii=False)

        schema = research_envelope_schema(RUN_ID, attempt_for("news"))
        schema["properties"]["seat_id"]["enum"].append("someone-else")
        schema["properties"]["evidence_cards"]["items"]["properties"]["run_id"][
            "enum"
        ].append("another-run")

        self.assertEqual(
            before, json.dumps(RESEARCH_ENVELOPE_SCHEMA, sort_keys=True, ensure_ascii=False)
        )
        self.assertNotIn("enum", RESEARCH_ENVELOPE_SCHEMA["properties"]["seat_id"])

    def test_every_lineage_field_is_pinned_by_a_single_value_enum(self):
        attempt = attempt_for("news")

        schema = research_envelope_schema(RUN_ID, attempt)

        card = schema["properties"]["evidence_cards"]["items"]["properties"]
        self.assertEqual([attempt.seat_id], schema["properties"]["seat_id"]["enum"])
        self.assertEqual([RUN_ID], card["run_id"]["enum"])
        self.assertEqual([attempt.seat_id], card["seat_id"]["enum"])
        self.assertEqual([attempt.attempt_id], card["attempt_id"]["enum"])

    def test_fourteen_parallel_schemas_share_no_nested_state(self):
        attempts = [
            attempt_for(seat_id, suffix=suffix)
            for suffix in ("a1", "a2")
            for seat_id in SEAT_IDS
        ]
        barrier = threading.Barrier(len(attempts))
        built = {}

        def build(attempt):
            barrier.wait(timeout=10)
            built[attempt.attempt_id] = research_envelope_schema(RUN_ID, attempt)

        threads = [threading.Thread(target=build, args=(item,)) for item in attempts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(14, len(built))
        card_ids = {
            id(schema["properties"]["evidence_cards"]["items"])
            for schema in built.values()
        }
        self.assertEqual(14, len(card_ids))
        for attempt_id, schema in built.items():
            card = schema["properties"]["evidence_cards"]["items"]["properties"]
            self.assertEqual([attempt_id], card["attempt_id"]["enum"])


class MissingProviderCliDispatchTest(unittest.TestCase):
    """A provider this WSL shell cannot find fails at once, not at the timeout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.agy_cli = root / "agy"
        self.agy_cli.write_text("fixture", encoding="utf-8")
        self.agy_cli.chmod(0o700)
        self.run = RunStore(self.data_root).create_run(RUN_ID, SEAT_IDS)
        self.results = queue.Queue()
        self.package = build_question_package(QUESTION)
        self.inbox = self.data_root / "inbox" / RUN_ID / "requests"
        self.claude_runner = RecordingClaudeRunner(self.run.run_id)
        self.agy_runner = RecordingAgyRunner(self.run.run_id)
        self.codex_adapter = RecordingCodexAdapter(self.run.run_id)

    def build_runner(self, which):
        runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.package,
            self.inbox,
            claude_adapter=ClaudeAdapter(
                runner=self.claude_runner,
                code_root=self.code_root,
                data_root=self.data_root,
            ),
            antigravity_adapter=AntigravityAdapter(
                cli_path=self.agy_cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=self.agy_runner,
            ),
            codex_adapter=self.codex_adapter,
            codex_mode="cli",
            which=which,
        )
        self.addCleanup(runner.shutdown)
        return runner

    def test_a_missing_cli_fails_the_attempt_at_once_and_never_dispatches(self):
        """回報「沒有啟動」，因為它真的沒有啟動（Spec R-008 的 requested／started）。

        這一席的命令不在 PATH 上，沒有任何進程被生出來。回 ``True`` 會讓
        scheduler 記下一次不存在的啟動，之後的 summary 與 Live 都會宣稱這席開始
        研究過——那是憑空捏造的歷史。
        """
        runner = self.build_runner(blind_which)

        started = runner.start(attempt_for("news"), None)
        message = self.results.get(timeout=10)

        self.assertIs(False, started)
        self.assertEqual(("failure", "news-a1", PROVIDER_CLI_MISSING), message[:3])
        self.assertIn("codex", message[3])
        self.assertEqual([], self.codex_adapter.calls)

    def test_one_missing_provider_never_stops_the_other_seats(self):
        def half_blind(name):
            return None if name == "codex" else VISIBLE_PATH(name)

        runner = self.build_runner(half_blind)

        runner.start(attempt_for("news"), None)
        runner.start(attempt_for("onchain"), None)
        messages = take_outcomes(self.results, 2)

        self.assertEqual(PROVIDER_CLI_MISSING, messages["news-a1"][2])
        self.assertEqual("result", messages["onchain-a1"][0])

    def test_a_research_worker_publishes_the_provider_it_answered_on(self):
        runner = self.build_runner(visible_which)
        lineage = []

        runner.start(attempt_for("onchain"), None)
        take_outcomes(self.results, 1, lineage)

        self.assertEqual(1, len(lineage))
        message, attempt_id, payload = lineage[0]
        self.assertEqual(RESEARCH_LINEAGE_MESSAGE, message)
        self.assertEqual("onchain-a1", attempt_id)
        self.assertEqual(PROVIDER_CLAUDE, payload["provider"])
        self.assertEqual("claude-opus-5", payload["actual_model"])

    def test_the_three_research_callsites_each_get_a_per_attempt_schema(self):
        runner = self.build_runner(visible_which)
        attempts = {
            "news": attempt_for("news"),
            "onchain": attempt_for("onchain"),
            "counter-evidence": attempt_for("counter-evidence"),
        }

        for attempt in attempts.values():
            runner.start(attempt, None)
        seen = take_outcomes(self.results, 3)

        self.assertEqual({"result"}, {message[0] for message in seen.values()})
        codex_schema = self.codex_adapter.calls[0]["schema"]
        claude_schema = self.claude_runner.schemas[0]
        agy_schema = self.agy_runner.schemas[0]
        for seat_id, schema in (
            ("news", codex_schema),
            ("onchain", claude_schema),
            ("counter-evidence", agy_schema),
        ):
            with self.subTest(seat_id=seat_id):
                card = schema["properties"]["evidence_cards"]["items"]["properties"]
                self.assertEqual([seat_id], schema["properties"]["seat_id"]["enum"])
                self.assertEqual([self.run.run_id], card["run_id"]["enum"])
                self.assertEqual([seat_id], card["seat_id"]["enum"])
                self.assertEqual(
                    [attempts[seat_id].attempt_id], card["attempt_id"]["enum"]
                )

    def test_a_backup_runs_on_the_other_provider_without_moving_the_seat(self):
        runner = self.build_runner(visible_which)
        backup = attempt_for("news", provider=PROVIDER_CLAUDE, kind="backup", suffix="a2")

        runner.start(backup, None)
        message = take_outcomes(self.results, 1)["news-a2"]

        self.assertEqual(("result", "news-a2"), message[:2])
        self.assertEqual([], self.codex_adapter.calls)
        self.assertEqual(["news"], [call["seat_id"] for call in self.claude_runner.calls])


class ParallelCapacityTest(unittest.TestCase):
    """Fourteen attempts really do run at once; nothing waits for a free worker."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.agy_cli = root / "agy"
        self.agy_cli.write_text("fixture", encoding="utf-8")
        self.agy_cli.chmod(0o700)
        self.run = RunStore(self.data_root).create_run(RUN_ID, SEAT_IDS)
        self.results = queue.Queue()
        self.package = build_question_package(QUESTION)
        self.inbox = self.data_root / "inbox" / RUN_ID / "requests"

    def test_seven_primaries_and_seven_backups_are_in_flight_together(self):
        barrier = threading.Barrier(RESEARCH_ATTEMPT_CAPACITY, timeout=20)
        claude_runner = RecordingClaudeRunner(self.run.run_id, barrier=barrier)
        agy_runner = RecordingAgyRunner(self.run.run_id, barrier=barrier)
        codex_adapter = RecordingCodexAdapter(self.run.run_id, barrier=barrier)
        runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.package,
            self.inbox,
            claude_adapter=ClaudeAdapter(
                runner=claude_runner,
                code_root=self.code_root,
                data_root=self.data_root,
            ),
            antigravity_adapter=AntigravityAdapter(
                cli_path=self.agy_cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=agy_runner,
            ),
            codex_adapter=codex_adapter,
            codex_mode="cli",
            which=visible_which,
        )
        self.addCleanup(runner.shutdown)
        attempts = [attempt_for(seat_id) for seat_id in SEAT_IDS] + [
            attempt_for(
                seat_id,
                provider=BACKUP_CANDIDATES[seat_id].provider,
                kind="backup",
                suffix="a2",
            )
            for seat_id in SEAT_IDS
        ]

        for attempt in attempts:
            runner.start(attempt, None)
        seen = take_outcomes(self.results, RESEARCH_ATTEMPT_CAPACITY)

        self.assertEqual(
            sorted(attempt.attempt_id for attempt in attempts), sorted(seen)
        )
        self.assertEqual({"result"}, {message[0] for message in seen.values()})


class RecordingClaudeRunner:
    """Claude CLI seam that answers every seat, including a backup seat."""

    def __init__(self, run_id, barrier=None, web_search_requests=2, web_fetch_requests=1):
        self.run_id = run_id
        self.barrier = barrier
        # 線上檢索紀錄是可調的：把兩個都設成 0，就是一份沒有研究證明的回覆。
        self.web_search_requests = web_search_requests
        self.web_fetch_requests = web_fetch_requests
        self.calls = []
        self.schemas = []

    def run(self, args, *, input_text, cwd, timeout_seconds):
        args = tuple(args)
        work_dir = Path(cwd)
        attempt_id = work_dir.name
        seat_id = work_dir.parent.parent.name
        schema = json.loads(args[args.index("--json-schema") + 1])
        self.calls.append({"seat_id": seat_id, "attempt_id": attempt_id, "args": args})
        self.schemas.append(schema)
        if self.barrier is not None:
            self.barrier.wait()
        return ProcessOutput(
            returncode=0,
            stdout=claude_stdout(
                envelope(self.run_id, seat_id, attempt_id),
                web_search_requests=self.web_search_requests,
                web_fetch_requests=self.web_fetch_requests,
            ),
            stderr="",
            elapsed_ms=11,
        )


class RecordingAgyRunner:
    """Antigravity CLI seam returning one valid stream-json envelope."""

    def __init__(self, run_id, barrier=None):
        self.run_id = run_id
        self.barrier = barrier
        self.calls = []
        self.schemas = []

    def __call__(self, argv, cwd, timeout):
        attempt_dir = Path(cwd)
        attempt_id = attempt_dir.name
        seat_id = attempt_dir.parent.parent.name
        self.calls.append({"seat_id": seat_id, "attempt_id": attempt_id})
        self.schemas.append(
            json.loads((attempt_dir / "input-schema.json").read_text(encoding="utf-8"))
        )
        if self.barrier is not None:
            self.barrier.wait()
        return subprocess.CompletedProcess(
            list(argv),
            0,
            agy_stream(envelope(self.run_id, seat_id, attempt_id)),
            "",
        )


class RecordingCodexAdapter:
    """``codex exec`` seam recording the exact schema each attempt received."""

    def __init__(self, run_id, barrier=None):
        self.run_id = run_id
        self.barrier = barrier
        self.calls = []

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        work_dir = Path(work_dir)
        attempt_id = work_dir.name
        seat_id = work_dir.parent.parent.name
        self.calls.append(
            {"seat_id": seat_id, "attempt_id": attempt_id, "schema": schema}
        )
        if self.barrier is not None:
            self.barrier.wait()
        return CodexExecResult(
            structured_output=envelope(self.run_id, seat_id, attempt_id),
            elapsed_ms=4_200,
            schema_path=work_dir / "codex-output-schema.json",
            last_message_path=work_dir / "codex-last-message.txt",
            search_invocations=1,
        )


class RegistryBackedRunner(FakeProcessRunner):
    """Fake dispatch, real :class:`ProcessRegistry` behind cancel and terminate.

    Only the dispatch seam is faked — the stop path is the production one, so a
    process handed over after the cancel meets the same poisoned key it would
    meet in a real run. ``pending`` names the attempts whose ``start`` reports
    ``False``: requested, not begun, and still able to produce a process.
    """

    def __init__(self, registry, pending=()):
        super().__init__()
        self.registry = registry
        for attempt_id in pending:
            self.start_behaviors[attempt_id] = False

    def cancel(self, attempt_id):
        super().cancel(attempt_id)
        self._stop(attempt_id)

    def terminate(self, attempt_id):
        super().terminate(attempt_id)
        self._stop(attempt_id)

    def _stop(self, attempt_id):
        """Mirror ``RealSeatRunner._best_effort_stop``: never raise, always poison."""
        try:
            self.registry.terminate(attempt_id, grace_seconds=0)
        except Exception:  # cancellation must not break the sweep
            return None
        return None


class ScriptedCodexAdapter:
    """``codex exec`` seam that either raises one exact error or answers."""

    model = "gpt-5.6-sol"

    def __init__(self, run_id, error=None, search_invocations=1):
        self.run_id = run_id
        self.error = error
        self.search_invocations = search_invocations

    def invoke(self, prompt, schema, work_dir, allow_search=True):
        if self.error is not None:
            raise self.error
        work_dir = Path(work_dir)
        attempt_id = work_dir.name
        seat_id = work_dir.parent.parent.name
        return CodexExecResult(
            structured_output=envelope(self.run_id, seat_id, attempt_id),
            elapsed_ms=7,
            schema_path=work_dir / "codex-output-schema.json",
            last_message_path=work_dir / "codex-last-message.txt",
            search_invocations=self.search_invocations,
        )


class FailureCodeRelayTest(unittest.TestCase):
    """Reviewer B2：穩定 failure code 必須整條路活著。

    路徑是真的走一遍——adapter 邊界 → runner 的 queue → launcher 的 relay →
    scheduler 的 ``attempt_outcomes``——因為這個缺陷不在任何單一模組裡，而在
    每一次轉手時把機器值折成人話的那幾層。scheduler 只讀 code，不讀訊息。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.agy_cli = root / "agy"
        self.agy_cli.write_text("fixture", encoding="utf-8")
        self.agy_cli.chmod(0o700)
        self.run = RunStore(self.data_root).create_run(RUN_ID, SEAT_IDS)
        self.results = queue.Queue()
        self.package = build_question_package(QUESTION)
        self.inbox = self.data_root / "inbox" / RUN_ID / "requests"
        self.err = io.StringIO()
        self.clock = FixedClock()
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=FakeProcessRunner(),
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )
        self.scheduler.start()

    def relay_one(self, seat_id, codex_adapter=None, claude_runner=None):
        """Dispatch one real attempt through the runner, then relay every message."""
        runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.package,
            self.inbox,
            claude_adapter=ClaudeAdapter(
                runner=claude_runner or RecordingClaudeRunner(self.run.run_id),
                code_root=self.code_root,
                data_root=self.data_root,
            ),
            antigravity_adapter=AntigravityAdapter(
                cli_path=self.agy_cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=RecordingAgyRunner(self.run.run_id),
            ),
            codex_adapter=codex_adapter or ScriptedCodexAdapter(self.run.run_id),
            codex_mode="cli",
            which=visible_which,
        )
        self.addCleanup(runner.shutdown)
        attempt = self.scheduler.recovery.seats[seat_id].attempts[0]
        runner.start(attempt, None)
        runner.shutdown(wait=True)
        while not self.results.empty():
            launcher._relay(self.scheduler, self.results.get_nowait(), self.err)
        return self.scheduler.attempt_outcomes[attempt.attempt_id]

    def test_codex_without_search_proof_keeps_research_proof_missing(self):
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(self.run.run_id, search_invocations=0),
        )

        self.assertEqual(RESEARCH_PROOF_MISSING, record["failure_code"])
        self.assertEqual("failed", record["terminal_outcome"])

    def test_an_unreclaimed_process_tree_keeps_its_own_terminal_code(self):
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(
                self.run.run_id,
                error=CodexExecTreeTerminationError(PROCESS_TREE_TERMINATION_FAILED),
            ),
        )

        self.assertEqual(PROCESS_TREE_TERMINATION_FAILED, record["failure_code"])

    def test_an_empty_last_message_keeps_provider_empty_output(self):
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(
                self.run.run_id,
                error=CodexExecEmptyOutputError("codex exec 的 last message 為空"),
            ),
        )

        self.assertEqual(PROVIDER_EMPTY_OUTPUT, record["failure_code"])

    def test_an_unparsable_last_message_keeps_provider_malformed_output(self):
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(
                self.run.run_id,
                error=CodexExecOutputError("codex exec 的 last message 不是合法 JSON"),
            ),
        )

        self.assertEqual(PROVIDER_MALFORMED_OUTPUT, record["failure_code"])

    def test_the_relayed_failure_keeps_the_whole_attempt_lineage(self):
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(self.run.run_id, search_invocations=0),
        )

        self.assertEqual("news", record["seat_id"])
        self.assertEqual("news-a1", record["attempt_id"])
        self.assertEqual("research", record["phase"])
        self.assertEqual("primary", record["attempt_kind"])
        self.assertEqual(PROVIDER_CODEX, record["provider"])
        self.assertEqual(PROVIDER_MODELS[PROVIDER_CODEX], record["requested_model"])
        self.assertEqual(PROVIDER_CODEX, record["actual_provider"])

    def test_a_claude_seat_without_search_proof_keeps_research_proof_missing(self):
        record = self.relay_one(
            "onchain",
            claude_runner=RecordingClaudeRunner(
                self.run.run_id, web_search_requests=0, web_fetch_requests=0
            ),
        )

        self.assertEqual(RESEARCH_PROOF_MISSING, record["failure_code"])
        self.assertEqual(PROVIDER_CLAUDE, record["provider"])

    def test_the_scheduler_never_reads_the_human_message_for_a_code(self):
        """訊息換句話說不得改變機器值：code 是自己傳過來的，不是猜出來的。"""
        record = self.relay_one(
            "news",
            ScriptedCodexAdapter(
                self.run.run_id,
                error=CodexExecEmptyOutputError("provider_malformed_output 這個詞只是文字"),
            ),
        )

        self.assertEqual(PROVIDER_EMPTY_OUTPUT, record["failure_code"])
        self.assertIn("只是文字", record["failure_message"])


class MissingCliStartTruthfulnessTest(unittest.TestCase):
    """Reviewer A：沒有啟動的 attempt 不得在紀錄裡留下啟動過的痕跡。

    這裡讓 scheduler 真的用 :class:`RealSeatRunner` 派工，再把 queue 上的訊息經
    launcher 的 relay 送回 scheduler，因為「requested 但沒 started」這件事只有在
    這三方對同一次派工的說法擺在一起時才看得出來。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.code_root = root / "code"
        self.data_root = root / "data"
        self.code_root.mkdir()
        self.data_root.mkdir()
        self.agy_cli = root / "agy"
        self.agy_cli.write_text("fixture", encoding="utf-8")
        self.agy_cli.chmod(0o700)
        self.run = RunStore(self.data_root).create_run(RUN_ID, SEAT_IDS)
        self.results = queue.Queue()
        self.package = build_question_package(QUESTION)
        self.inbox = self.data_root / "inbox" / RUN_ID / "requests"
        self.err = io.StringIO()
        self.clock = FixedClock()
        self.codex_adapter = RecordingCodexAdapter(self.run.run_id)
        # codex 不在這個 shell 的 PATH 上；claude 與 agy 都在。
        self.runner = RealSeatRunner(
            self.run,
            self.data_root,
            self.code_root,
            self.results,
            self.package,
            self.inbox,
            claude_adapter=ClaudeAdapter(
                runner=RecordingClaudeRunner(self.run.run_id),
                code_root=self.code_root,
                data_root=self.data_root,
            ),
            antigravity_adapter=AntigravityAdapter(
                cli_path=self.agy_cli,
                code_root=self.code_root,
                data_root=self.data_root,
                runner=RecordingAgyRunner(self.run.run_id),
            ),
            codex_adapter=self.codex_adapter,
            codex_mode="cli",
            which=lambda name: None if name == "codex" else VISIBLE_PATH(name),
        )
        self.addCleanup(self.runner.shutdown)
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            # 正式 gateway：這條路要驗的就是 runner 真的交出來的 envelope。
            gateway=RealEvidenceGateway(self.run.run_id, self.package.assets),
            process_runner=self.runner,
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )

    def relay_until(self, ready):
        """Relay queued messages the way the launcher's poll loop does, until ``ready``.

        Blocking gets rather than a drain-and-stop: recovery is *dispatched from
        inside* a relay, so a loop that stopped at an empty queue would shut the
        pool before the backup had a chance to answer. No sleeps — the condition
        itself is what is waited on.
        """
        while not ready():
            launcher._relay(self.scheduler, self.results.get(timeout=20), self.err)

    def settled(self, attempt_id):
        record = self.scheduler.attempt_outcomes.get(attempt_id)
        return bool(record and record["terminal_outcome"])

    def start_and_relay(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        self.relay_until(lambda: self.settled(primary.attempt_id))
        return primary

    def events_for(self, attempt_id, name):
        return [
            event
            for event in self.scheduler.events
            if event["event"] == name and event["attempt_id"] == attempt_id
        ]

    def summary_for(self, seat_id, attempt_id):
        seat = [row for row in self.scheduler.attempt_summary() if row["seat_id"] == seat_id][0]
        return [item for item in seat["attempts"] if item["attempt_id"] == attempt_id][0]

    def test_the_dispatch_is_recorded_as_requested(self):
        primary = self.start_and_relay()

        self.assertEqual(1, len(self.events_for(primary.attempt_id, "attempt_launch_requested")))
        self.assertTrue(self.summary_for("news", primary.attempt_id)["requested_at_utc"])

    def test_a_missing_cli_never_reports_a_start_that_did_not_happen(self):
        primary = self.start_and_relay()

        self.assertEqual([], self.events_for(primary.attempt_id, "attempt_started"))
        self.assertFalse(self.summary_for("news", primary.attempt_id)["started"])
        self.assertNotIn(
            primary.attempt_id,
            self.scheduler.recovery.seats["news"].started_attempt_ids,
        )
        self.assertEqual([], self.codex_adapter.calls)

    def test_the_exact_missing_cli_code_survives_the_whole_relay(self):
        primary = self.start_and_relay()

        record = self.scheduler.attempt_outcomes[primary.attempt_id]
        self.assertEqual("failed", record["terminal_outcome"])
        self.assertEqual(PROVIDER_CLI_MISSING, record["failure_code"])
        self.assertIn("codex", record["failure_message"])

    def test_the_missing_attempt_is_terminal_and_never_launched_again(self):
        primary = self.start_and_relay()

        self.assertNotIn(primary.attempt_id, (None,))
        self.assertIsNotNone(
            self.scheduler.attempt_outcomes[primary.attempt_id]["terminal_outcome"]
        )
        # 到了重試里程碑也不得再送一次同一個 attempt。
        self.clock.advance_ms(START_RETRY_MS)
        self.scheduler.tick()
        self.assertEqual(
            1, len(self.events_for(primary.attempt_id, "attempt_launch_requested"))
        )

    def test_the_other_provider_backup_starts_and_can_be_adopted(self):
        self.start_and_relay()
        seat = self.scheduler.recovery.seats["news"]
        primary, backup = seat.attempts

        self.assertEqual("backup", backup.kind)
        self.assertEqual(PROVIDER_CLAUDE, backup.provider)
        self.assertEqual(1, len(self.events_for(backup.attempt_id, "attempt_started")))
        self.assertTrue(self.summary_for("news", backup.attempt_id)["started"])
        # backup 的結果經同一條 relay 回到 scheduler
        self.relay_until(lambda: seat.adopted_attempt_id is not None)
        self.assertEqual(backup.attempt_id, seat.adopted_attempt_id)
        self.assertTrue(self.summary_for("news", backup.attempt_id)["adopted"])

    def test_the_seat_summary_separates_requested_from_started(self):
        self.start_and_relay()
        seat = [row for row in self.scheduler.attempt_summary() if row["seat_id"] == "news"][0]

        self.assertEqual([True, True], [bool(item["requested_at_utc"]) for item in seat["attempts"]])
        self.assertEqual([False, True], [item["started"] for item in seat["attempts"]])
        self.assertEqual(
            [PROVIDER_CLI_MISSING, None],
            [item["failure_code"] for item in seat["attempts"]],
        )


class SeatCardLineageTest(unittest.TestCase):
    """One card per seat, the adopted source wins, an old run reads 未記錄."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock()
        self.run = RunStore(Path(self._tmp.name)).create_run(RUN_ID, SEAT_IDS)
        self.scheduler = ResearchScheduler(
            run=self.run,
            clock=self.clock,
            gateway=JsonEvidenceGateway(),
            process_runner=FakeProcessRunner(),
            format_repairer=TrailingCommaRepairer(),
            primary_models=PRIMARY_MODELS,
            replacement_models=REPLACEMENT_MODELS,
            seat_providers=SEAT_PROVIDERS,
            backup_candidates=BACKUP_CANDIDATES,
        )

    def room(self, records):
        room = live.ChatRoom(("bullish", "bearish", "neutral"), {})
        room.ingest(records)
        return {seat["seat_id"]: seat for seat in room.seat_views()}

    def test_a_run_with_no_attempt_lineage_reads_unrecorded(self):
        seats = self.room([])

        self.assertEqual(len(SEAT_IDS), len(seats))
        for seat_id in SEAT_IDS:
            with self.subTest(seat_id=seat_id):
                attempt = seats[seat_id]["attempt"]
                self.assertEqual(0, attempt["attempt_count"])
                self.assertIsNone(attempt["provider"])
                self.assertIsNone(attempt["terminal_outcome"])
                self.assertEqual(UNRECORDED, attempt["label"])

    def test_the_adopted_backup_is_the_source_shown_for_that_one_seat(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))

        seats = self.room(self.scheduler.events)

        attempt = seats["news"]["attempt"]
        self.assertEqual(2, attempt["attempt_count"])
        self.assertEqual(backup.attempt_id, attempt["attempt_id"])
        self.assertEqual(PROVIDER_CLAUDE, attempt["provider"])
        self.assertEqual("backup", attempt["attempt_kind"])
        self.assertEqual("adopted", attempt["terminal_outcome"])
        self.assertTrue(attempt["adopted"])
        self.assertIn("已採用", attempt["label"])

    def test_an_adopted_seat_is_not_relabelled_by_a_later_failure(self):
        self.runner_failure_after_adoption()

        seats = self.room(self.scheduler.events)

        attempt = seats["news"]["attempt"]
        self.assertTrue(attempt["adopted"])
        self.assertEqual("adopted", attempt["terminal_outcome"])
        self.assertIsNone(attempt["failure_code"])

    def runner_failure_after_adoption(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        self.scheduler.submit_result(primary.attempt_id, evidence_raw(primary))
        self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "後到的逾時")

    def test_an_exhausted_seat_shows_its_last_failure_compactly(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        backup = self.scheduler.report_failure(
            primary.attempt_id, PROVIDER_CLI_MISSING, "PATH 上沒有 codex"
        )
        self.scheduler.report_failure(backup.attempt_id, PROVIDER_TIMEOUT, "無回覆")

        seats = self.room(self.scheduler.events)

        attempt = seats["news"]["attempt"]
        self.assertFalse(attempt["adopted"])
        self.assertTrue(attempt["exhausted"])
        self.assertEqual(PROVIDER_TIMEOUT, attempt["failure_code"])
        self.assertEqual(1, len(attempt["label"].splitlines()))

    def test_the_live_page_shows_one_card_per_seat_with_its_lineage(self):
        self.scheduler.start()
        primary = self.scheduler.recovery.seats["news"].attempts[0]
        backup = self.scheduler.report_failure(primary.attempt_id, PROVIDER_TIMEOUT, "無回覆")
        self.scheduler.submit_result(backup.attempt_id, evidence_raw(backup))
        room = live.ChatRoom(("bullish", "bearish", "neutral"), {})
        room.ingest(self.scheduler.events)

        html = live_page.render_live_page(self._page_data(room.seat_views()))

        self.assertEqual(len(SEAT_IDS), html.count('class="agent '))
        self.assertIn("agent-attempt", html)
        self.assertIn(UNRECORDED, html)

    def _page_data(self, seats):
        return {
            "state": "running",
            "run_id": RUN_ID,
            "run_href": "/run/{}".format(RUN_ID),
            "question": QUESTION,
            "assets": ["BTC"],
            "asset_class": "crypto",
            "messages": [],
            "seats": seats,
            "tally": [],
            "changes": [],
            "round": None,
            "elapsed_ms": 0,
            "cursor": None,
            "outcome": None,
            "report_available": False,
            "debate_report_available": False,
            "run_options": [],
            "rules": live.rule_timeline(),
            "phase_label": "研究中",
            "threshold_label": "—",
            "next_rule": None,
            "focus": live.focus_state([], None, False, None),
            "evidence": [],
            "total_remaining_ms": 0,
            "report_remaining_ms": 0,
        }


if __name__ == "__main__":
    unittest.main()
