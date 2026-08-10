"""Run identity and the append-only, never-overwriting run store."""

import errno
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tests.fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents import run_store
from hoya_market_agents.question import MAX_ASSET_SLUG_BYTES
from hoya_market_agents.run_store import (
    ArtifactAlreadyExistsError,
    ArtifactTamperedError,
    FormatRepairSemanticChangeError,
    RunAlreadyExistsError,
    RunStoreError,
    RunStore,
    SnapshotSealedError,
    deduplicate_evidence,
    new_run_id,
    resolve_run_dir,
)

RUN_ID_SHAPE = re.compile(r"^\d{8}T\d{6}Z-[a-z-]+-[0-9a-f]{6}$")
# The characters Win32 refuses in a path component, plus the control range.
# This is a closed, documented set, so it can be written down.
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"/\\|?*') | frozenset(
    chr(code) for code in range(32)
)
# The DOS device names Win32 still reserves. Also closed, also documented.
WINDOWS_RESERVED_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL")
    + tuple("COM{}".format(digit) for digit in range(1, 10))
    + tuple("LPT{}".format(digit) for digit in range(1, 10))
)


def digest_of(run_id):
    """A run directory's trailing digest, spelled out from ADR 0005.

    Written from the rule rather than imported from ``run_store``, so a change
    of rule shows up here as a failure instead of following along silently.
    ``test_the_digest_is_taken_over_the_whole_run_id`` pins it against a value
    produced outside Python entirely.
    """
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


# How hard the claim is raced. Four callers were measured exposing a
# check-then-create claim in 25–40% of rounds on this machine; the low end is
# the one to plan against, and at that rate thirty rounds leaves roughly a
# 1-in-5,600 chance of missing it. That is an estimate from observed rates on
# one machine, not a bound: the rounds share a process and a scheduler, so
# they are not independent, and another machine will expose it differently.
# The mechanism the race rests on is asserted directly and deterministically
# by ``test_a_run_id_is_taken_by_linking_a_finished_claim_into_place``; this is
# the behavioural half.
RACE_WORKERS = 4
RACE_TRIALS = 30


def hash_index(text):
    """A stable per-case token suffix, so subTests do not reuse one run id."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:6], 16)


class RunIdTest(unittest.TestCase):
    def test_run_id_uses_utc_start_asset_slug_and_short_token(self):
        started = datetime(2026, 8, 1, 7, 30, 0, tzinfo=timezone.utc)

        run_id = new_run_id(started, "btc", token="8f3a2c")

        self.assertEqual("20260801T073000Z-btc-8f3a2c", run_id)
        self.assertRegex(run_id, RUN_ID_SHAPE)

    def test_run_ids_differ_even_within_the_same_second(self):
        clock = FixedClock()
        tokens = ScriptedTokenSource(["aaa111", "bbb222"])

        first = new_run_id(clock.utc_now(), "btc", token=tokens())
        second = new_run_id(clock.utc_now(), "btc", token=tokens())

        self.assertNotEqual(first, second)


class DatedRunLayoutTest(unittest.TestCase):
    """Where a run directory lands, and what its name is allowed to be (ADR 0005)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.store = RunStore(self.data_root)
        self.runs_root = self.data_root / "runs"

    def create(self, run_id="20260801T073000Z-btc-8f3a2c", question=None):
        return self.store.create_run(run_id, ["news"], question=question)

    def run_dirs_under(self, date):
        """Run directories only — a claim file is not a run."""
        return sorted(
            path.name
            for path in (self.runs_root / date).iterdir()
            if path.is_dir()
        )

    # --- date folder ----------------------------------------------------

    def test_a_run_lands_under_its_taipei_date_with_an_hhmm_label(self):
        run = self.create()

        self.assertEqual(
            self.runs_root
            / "2026-08-01"
            / "1530-btc-deb97098567e5181",
            run.path,
        )
        self.assertTrue(run.path.is_dir())

    def test_the_date_folder_turns_over_at_taipei_midnight(self):
        """Taipei is UTC+8, so 16:00Z is already the next day for the user."""
        before = self.create("20260805T155959Z-btc-aaa111")
        after = self.create("20260805T160000Z-btc-bbb222")

        self.assertEqual(
            self.runs_root / "2026-08-05" / "2359-btc-57370ce56124e98b",
            before.path,
        )
        self.assertEqual(
            self.runs_root / "2026-08-06" / "0000-btc-db98f435d50bbbb8",
            after.path,
        )

    def test_utc_midnight_alone_does_not_start_a_new_date_folder(self):
        """The false-positive direction: a UTC day boundary is not a Taipei one."""
        before = self.create("20260805T235959Z-btc-aaa111")
        after = self.create("20260806T000000Z-btc-bbb222")

        self.assertEqual(self.runs_root / "2026-08-06", before.path.parent)
        self.assertEqual(self.runs_root / "2026-08-06", after.path.parent)
        self.assertEqual("0759-btc-c567bcf797169250", before.path.name)
        self.assertEqual("0800-btc-8372f1f16257825c", after.path.name)

    def test_two_runs_on_the_same_taipei_day_share_one_date_folder(self):
        first = self.create("20260801T010000Z-btc-aaa111")
        second = self.create("20260801T140000Z-btc-bbb222")

        self.assertEqual(first.path.parent, second.path.parent)
        self.assertEqual(
            ["0900-btc-4997e3ee4770e0fc", "2200-btc-bd1159c00be29fcd"],
            self.run_dirs_under("2026-08-01"),
        )

    def test_a_run_id_without_a_utc_timestamp_is_refused(self):
        with self.assertRaises(RunStoreError):
            self.create("run-1")

        self.assertFalse(self.runs_root.exists())

    # --- the digest that ties a name to one run id ----------------------

    def test_the_digest_is_taken_over_the_whole_run_id(self):
        """Pinned against ``printf %s <id> | sha256sum | cut -c1-16``."""
        run = self.create("20260801T073000Z-btc-8f3a2c")

        self.assertEqual("deb97098567e5181", run.path.name.rsplit("-", 1)[1])
        self.assertEqual(
            digest_of("20260801T073000Z-btc-8f3a2c"), run.path.name.rsplit("-", 1)[1]
        )

    def test_a_run_id_that_differs_anywhere_resolves_to_nothing(self):
        """The false-positive direction, and the reason the tail is a digest.

        A tail carrying only the run id's random token would answer to every
        one of these, because they all share it.
        """
        self.create("20260801T073000Z-btc-abc123", question="這是我的 run")

        for other in (
            "20260801T073055Z-btc-abc123",  # 差幾秒
            "20260801T073000Z-eth-abc123",  # 差標的
            "20260801T073059Z-eth-anything",  # 兩者都差
        ):
            with self.subTest(other=other):
                self.assertIsNone(resolve_run_dir(self.data_root, other))

    def test_a_wrong_run_id_cannot_reach_another_runs_seat_directory(self):
        """The same guarantee where it actually costs something."""
        mine = self.create("20260801T073000Z-btc-abc123", question="這是我的 run")

        self.assertEqual(
            mine.path, resolve_run_dir(self.data_root, "20260801T073000Z-btc-abc123")
        )
        self.assertIsNone(
            resolve_run_dir(self.data_root, "20260801T073055Z-btc-abc123")
        )

    # --- the token a run id carries -------------------------------------

    def test_a_token_that_is_not_identifier_shaped_is_refused(self):
        for token in ('bad<token', "NUL.", "with space", "a" * 33, "", "tab\ttoken"):
            with self.subTest(token=token):
                with self.assertRaises(RunStoreError):
                    self.create("20260801T073000Z-btc-{}".format(token))

                # 拒絕發生在任何目錄被建立以前，不留半個殘骸。
                self.assertFalse(self.runs_root.exists())
                self.assertIsNone(
                    resolve_run_dir(
                        self.data_root, "20260801T073000Z-btc-{}".format(token)
                    )
                )

    def test_an_identifier_shaped_token_is_accepted(self):
        """The false-positive direction: the tokens this project really makes."""
        for token in ("8f3a2c", "aaa111", "debate", "ticket5", "A" * 32):
            with self.subTest(token=token):
                run_id = "20260801T073000Z-btc-{}".format(token)

                run = self.create(run_id)

                self.assertTrue(run.path.is_dir())
                self.assertEqual(run.path, resolve_run_dir(self.data_root, run_id))

    # --- the question in the folder name --------------------------------

    def test_the_folder_label_carries_the_question_that_was_asked(self):
        run = self.create(question="幫我分析 2330 這張股票未來七天會不會漲")

        self.assertEqual(
            "1530-幫我分析-2330-這張股票未來七天會不會漲-deb97098567e5181",
            run.path.name,
        )
        self.assertTrue(run.path.is_dir())

    def test_without_a_question_the_label_falls_back_to_the_run_id_slug(self):
        run = self.create()

        self.assertEqual("1530-btc-deb97098567e5181", run.path.name)

    def test_no_character_windows_refuses_survives_into_a_folder_name(self):
        question = "".join(sorted(WINDOWS_FORBIDDEN_CHARACTERS)) + "BTC 會漲嗎？"

        run = self.create(question=question)

        self.assertEqual(
            set(), WINDOWS_FORBIDDEN_CHARACTERS & set(run.path.name)
        )
        # 刪光光也會滿足上一條，所以同時要求題目讀得出來的部分還在。
        self.assertEqual("1530-btc-會漲嗎-deb97098567e5181", run.path.name)
        self.assertTrue(run.path.is_dir())

    def test_the_words_of_a_question_are_kept_not_stripped(self):
        """The false-positive direction: filtering must not eat the label."""
        run = self.create(question="2330 未來七天會不會漲")

        self.assertIn("2330", run.path.name)
        self.assertIn("未來七天會不會漲", run.path.name)

    def test_a_folder_name_never_ends_in_a_dot_or_a_space(self):
        questions = ("BTC 會漲嗎...", "BTC 會漲嗎   ", "BTC 會漲嗎. . .")
        for index, question in enumerate(questions):
            with self.subTest(question=question):
                run = self.store.create_run(
                    new_run_id(
                        datetime(2026, 8, 1, 7, 30, tzinfo=timezone.utc),
                        "btc",
                        token="{:06x}".format(index),
                    ),
                    ["news"],
                    question=question,
                )

                self.assertFalse(run.path.name.endswith("."))
                self.assertFalse(run.path.name.endswith(" "))
                self.assertIn("btc-會漲嗎", run.path.name)
                self.assertTrue(run.path.is_dir())

    def test_a_windows_device_name_as_a_question_is_not_a_device_name(self):
        for index, reserved in enumerate(sorted(WINDOWS_RESERVED_NAMES)):
            with self.subTest(reserved=reserved):
                run = self.store.create_run(
                    new_run_id(
                        datetime(2026, 8, 1, 7, 30, tzinfo=timezone.utc),
                        "btc",
                        token="{:06x}".format(index),
                    ),
                    ["news"],
                    question=reserved,
                )

                stem = run.path.name.split(".")[0].upper()
                self.assertNotIn(stem, WINDOWS_RESERVED_NAMES)
                # 名字仍然是這個題目的：不是靠把保留名整個刪掉才過關。
                self.assertEqual(
                    "1530-{}-{}".format(
                        reserved.lower(),
                        digest_of("20260801T073000Z-btc-{:06x}".format(index)),
                    ),
                    run.path.name,
                )
                self.assertTrue(run.path.is_dir())

    def test_a_windows_device_name_as_a_token_is_not_a_device_name(self):
        """A token reaches the name only as a digest, so it cannot spell one."""
        for token in ("CON", "NUL", "COM1", "LPT9"):
            with self.subTest(token=token):
                run = self.create("20260801T073000Z-btc-{}".format(token))

                self.assertNotIn(
                    run.path.name.split(".")[0].upper(), WINDOWS_RESERVED_NAMES
                )
                self.assertEqual(
                    "1530-btc-{}".format(
                        digest_of("20260801T073000Z-btc-{}".format(token))
                    ),
                    run.path.name,
                )
                self.assertTrue(run.path.is_dir())

    def test_a_word_that_merely_contains_a_device_name_is_not_mangled(self):
        """The false-positive direction: ``CONSENSUS`` is not ``CON``."""
        run = self.create(question="CONSENSUS 會不會成立")

        self.assertIn("consensus", run.path.name)

    def test_a_script_that_writes_with_marks_keeps_its_words_whole(self):
        """Devanagari vowels and Arabic diacritics are marks, not punctuation.

        Dropping them does not shorten the label, it shreds it — and a folder
        nobody can browse by question is what this label exists to avoid.
        """
        for question, expected in (
            ("बिटकॉइन बढ़ेगा?", "बिटकॉइन-बढ़ेगा"),
            ("क्या बिटकॉइन बढ़ेगा?", "क्या-बिटकॉइन-बढ़ेगा"),
            ("هل سيرتفع البِتكوين؟", "هل-سيرتفع-البِتكوين"),
        ):
            with self.subTest(question=question):
                run = self.create(
                    "20260801T073000Z-btc-{:06x}".format(hash_index(question)),
                    question=question,
                )

                self.assertIn(expected, run.path.name)
                self.assertTrue(run.path.is_dir())

    def test_a_mark_with_nothing_to_attach_to_is_still_a_separator(self):
        """The false-positive direction: keeping marks is not keeping anything."""
        run = self.create(question="BTC <़> 會漲嗎")

        self.assertEqual("1530-btc-會漲嗎-deb97098567e5181", run.path.name)

    # --- length ---------------------------------------------------------

    def test_a_long_question_is_capped_to_a_name_the_filesystem_accepts(self):
        run = self.create(question="漲" * 400)

        self.assertLessEqual(len(run.path.name.encode("utf-8")), 255)
        # 上限不是靠丟掉整個題目達成的：截斷後仍要用掉幾乎整份預算。
        self.assertGreater(len(run.path.name.encode("utf-8")), 200)
        self.assertTrue(run.path.is_dir())

    def test_capping_never_splits_a_character(self):
        run = self.create(question="漲" * 400)

        label = run.path.name[len("1530-") : -(len(digest_of("any")) + 1)]
        self.assertEqual({"漲"}, set(label))

    def test_the_folder_budget_accepts_every_slug_intake_accepts(self):
        """Ticket 05 sized the asset slug; a folder name must not shrink it."""
        longest = "a" * MAX_ASSET_SLUG_BYTES

        run = self.create(
            new_run_id(
                datetime(2026, 8, 1, 7, 30, tzinfo=timezone.utc),
                longest,
                token="8f3a2c",
            )
        )

        self.assertEqual(
            "1530-{}-{}".format(
                longest,
                digest_of("20260801T073000Z-{}-8f3a2c".format(longest)),
            ),
            run.path.name,
        )
        self.assertTrue(run.path.is_dir())

    # --- finding a run again --------------------------------------------

    def test_a_run_is_found_again_from_its_run_id_alone(self):
        run = self.create(question="幫我分析 2330 這張股票未來七天會不會漲")

        self.assertEqual(
            run.path, resolve_run_dir(self.data_root, "20260801T073000Z-btc-8f3a2c")
        )

    def test_an_unknown_run_id_resolves_to_nothing(self):
        self.create()

        self.assertIsNone(
            resolve_run_dir(self.data_root, "20260801T073000Z-btc-nope00")
        )
        self.assertIsNone(resolve_run_dir(self.data_root, "not-a-run-id"))

    def test_resolving_never_crosses_into_another_day(self):
        """The digest repeats across days; the date folder is what parts them."""
        yesterday = self.create("20260801T073000Z-btc-8f3a2c")
        today = self.create("20260802T073000Z-btc-8f3a2c")

        self.assertNotEqual(yesterday.path, today.path)
        self.assertEqual(
            yesterday.path,
            resolve_run_dir(self.data_root, "20260801T073000Z-btc-8f3a2c"),
        )
        self.assertEqual(
            today.path,
            resolve_run_dir(self.data_root, "20260802T073000Z-btc-8f3a2c"),
        )

    def test_two_run_ids_seconds_apart_are_two_runs_and_both_are_findable(self):
        """The false-positive direction of the digest: seconds are part of the id."""
        first = self.create("20260801T073000Z-btc-aaa111", question="A 會漲嗎")
        second = self.create("20260801T073055Z-btc-aaa111", question="B 會跌嗎")

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(2, len(self.run_dirs_under("2026-08-01")))
        self.assertEqual(
            first.path,
            resolve_run_dir(self.data_root, "20260801T073000Z-btc-aaa111"),
        )
        self.assertEqual(
            second.path,
            resolve_run_dir(self.data_root, "20260801T073055Z-btc-aaa111"),
        )

    def test_a_neighbour_in_the_same_minute_is_not_mistaken_for_this_run(self):
        """The false-positive direction: same date, same ``HHMM``, other run."""
        mine = self.create("20260801T073000Z-btc-8f3a2c", question="A 會漲嗎")
        self.create("20260801T073055Z-btc-777777", question="B 會跌嗎")

        self.assertEqual(
            mine.path, resolve_run_dir(self.data_root, "20260801T073000Z-btc-8f3a2c")
        )

    def test_the_same_run_id_with_a_different_question_still_fails_closed(self):
        first = self.create(question="A 會漲嗎")

        with self.assertRaises(RunAlreadyExistsError):
            self.create(question="B 會跌嗎")

        self.assertEqual([first.path.name], self.run_dirs_under("2026-08-01"))

    # --- occupying a run id ---------------------------------------------

    def race_to_create(self, store, run_id, workers=RACE_WORKERS):
        """Let ``workers`` callers reach one ``create_run`` at the same instant."""
        start = threading.Barrier(workers, timeout=10)

        def attempt(index):
            start.wait()
            try:
                return store.create_run(
                    run_id, ["news"], question="第 {} 種問法".format(index)
                )
            except RunAlreadyExistsError as refusal:
                return refusal

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [
                future.result(timeout=20)
                for future in [pool.submit(attempt, index) for index in range(workers)]
            ]

    def test_racing_creates_of_one_run_id_leave_exactly_one_run(self):
        """The real race, not a sequential imitation of one.

        Callers arrive together and word the question differently, so the
        directory names differ and ``mkdir`` sees no collision at all — both
        would be created, and each would then answer the other's lookup, so
        the id would resolve to neither. Only an exclusive claim on a name the
        id alone decides can pick a winner here.

        Repeated, because one attempt is not a test of a race: a claim that
        looks before it creates still wins most rounds. See ``RACE_TRIALS``
        for what repetition is and is not worth here. The correct store yields
        exactly one winner every round, so repetition costs no flakiness.
        """
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(1e-6)

        for trial in range(RACE_TRIALS):
            with self.subTest(trial=trial), tempfile.TemporaryDirectory() as room:
                data_root = Path(room)
                store = RunStore(data_root)
                run_id = "20260801T073000Z-btc-{:06x}".format(trial)

                outcomes = self.race_to_create(store, run_id)

                created = [
                    item for item in outcomes if not isinstance(item, Exception)
                ]
                self.assertEqual(1, len(created), outcomes)
                self.assertEqual(
                    RACE_WORKERS - 1,
                    sum(
                        isinstance(item, RunAlreadyExistsError) for item in outcomes
                    ),
                    outcomes,
                )
                self.assertEqual(
                    [created[0].path.name],
                    sorted(
                        path.name
                        for path in (data_root / "runs" / "2026-08-01").iterdir()
                        if path.is_dir()
                    ),
                )
                self.assertEqual(
                    created[0].path, resolve_run_dir(data_root, run_id)
                )

    def test_two_threads_on_different_run_ids_both_get_their_run(self):
        """The false-positive direction: the claim must not serialise strangers."""
        start = threading.Barrier(2)

        def attempt(run_id):
            start.wait(timeout=5)
            return self.store.create_run(run_id, ["news"], question="同時開始")

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [
                future.result(timeout=10)
                for future in [
                    pool.submit(attempt, "20260801T073000Z-btc-aaa111"),
                    pool.submit(attempt, "20260801T073000Z-btc-bbb222"),
                ]
            ]

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(2, len(self.run_dirs_under("2026-08-01")))
        self.assertEqual(
            first.path,
            resolve_run_dir(self.data_root, "20260801T073000Z-btc-aaa111"),
        )
        self.assertEqual(
            second.path,
            resolve_run_dir(self.data_root, "20260801T073000Z-btc-bbb222"),
        )

    def assert_nothing_of_the_run_survived(self, run_id, date="2026-08-01"):
        """No claim, no directory, nothing a lookup can reach."""
        self.assertIsNone(resolve_run_dir(self.data_root, run_id))
        date_folder = self.runs_root / date
        if date_folder.is_dir():
            self.assertEqual([], self.run_dirs_under(date))
            self.assertEqual(
                [], [path.name for path in date_folder.iterdir()], "殘留檔案"
            )

    def interrupt_the_first_owner_read(self):
        """Interrupt only the read that classifies, letting later ones work.

        Failing every read would hide the fault instead of exposing it: the
        cleanup this is meant to catch reads the owner too, so a blanket
        failure stops it for the wrong reason.
        """
        real = run_store._run_claim_owner
        reads = []

        def read(claim):
            reads.append(claim)
            if len(reads) == 1:
                raise KeyboardInterrupt
            return real(claim)

        self.owner_reads = reads
        return mock.patch.object(run_store, "_run_claim_owner", read)

    def assert_the_run_id_can_still_be_used(self, run_id):
        """The other half: a safe failure must not burn the id for good."""
        run = self.store.create_run(run_id, ["news"], question="重試成功")

        self.assertTrue(run.path.is_dir())
        self.assertEqual(run.path, resolve_run_dir(self.data_root, run_id))
        return run

    def test_a_claim_that_could_not_be_written_is_not_left_behind(self):
        """``ENOSPC`` before the claim is durable.

        A claim nobody can be identified by blocks the run id for good and
        cannot even say on whose behalf, so it must not outlive the failure.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        full = OSError(errno.ENOSPC, "No space left on device")

        with mock.patch.object(os, "fsync", side_effect=full) as refused:
            with self.assertRaises(OSError) as failure:
                self.create(run_id, question="磁碟滿了")

        # 這一步是在證明注入真的打在 claim 的寫入上，而不是別的地方先炸了。
        self.assertEqual(errno.ENOSPC, failure.exception.errno)
        self.assertEqual(1, refused.call_count)

        self.assert_nothing_of_the_run_survived(run_id)
        self.assert_the_run_id_can_still_be_used(run_id)

    def test_an_interrupt_while_writing_the_claim_does_not_burn_the_run_id(self):
        """``KeyboardInterrupt`` does not inherit from ``Exception``.

        Cleanup written for ``Exception`` would step over exactly the
        interruption a person causes by hand, which is the one most likely to
        land in a wait this long.
        """
        run_id = "20260801T073000Z-btc-aaa111"

        with mock.patch.object(os, "fsync", side_effect=KeyboardInterrupt) as hit:
            with self.assertRaises(KeyboardInterrupt):
                self.create(run_id, question="寫到一半被中斷")

        self.assertEqual(1, hit.call_count)
        self.assert_nothing_of_the_run_survived(run_id)
        self.assert_the_run_id_can_still_be_used(run_id)

    def test_an_interrupt_as_the_claim_lands_does_not_burn_the_run_id(self):
        """Interrupted at the instant the claim appears, after the side effect.

        There is no half-taken claim to find here, and that is the whole
        design: the file is written elsewhere and linked into place complete,
        so an interruption at the link either finds no claim at all or finds
        one that already names its owner. Either way the caller can tell whose
        it is and give the id back.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        real_link = os.link

        def link_then_interrupt(source, target, **kwargs):
            real_link(source, target, **kwargs)
            raise KeyboardInterrupt

        with mock.patch.object(os, "link", link_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.create(run_id, question="剛落地就被中斷")

        self.assert_nothing_of_the_run_survived(run_id)
        self.assert_the_run_id_can_still_be_used(run_id)

    def test_an_interrupt_while_naming_the_holder_deletes_nothing(self):
        """A duplicate create, interrupted while working out who holds the id.

        Reading the claim is wording for the refusal, nothing more. Whether
        this call owns anything was settled when the claim could not be taken,
        and an interruption in the middle of the explanation must not be able
        to reopen that question — the run being refused is a finished one, and
        the answer it would get wrong is "delete it".
        """
        run = self.create(question="A 會漲嗎")
        run.write_text("report.md", "# 既有報告")
        before = {
            path.name: path.stat().st_size
            for path in sorted(run.path.parent.iterdir())
        }

        with self.interrupt_the_first_owner_read():
            with self.assertRaises(KeyboardInterrupt):
                self.create(question="A 會漲嗎")

        # 先問「有沒有東西被刪掉」——那才是這個測試的名字說的事。
        self.assertEqual(
            before,
            {
                path.name: path.stat().st_size
                for path in sorted(run.path.parent.iterdir())
            },
        )
        self.assertEqual(
            "# 既有報告", (run.path / "report.md").read_text(encoding="utf-8")
        )
        self.assertEqual(
            run.path, resolve_run_dir(self.data_root, "20260801T073000Z-btc-8f3a2c")
        )
        # 最後才確認注入真的打到了，而不是這一輪根本沒讀 owner。
        self.assertGreaterEqual(len(self.owner_reads), 1)

    def test_an_interrupt_while_naming_the_holder_keeps_the_holders_claim(self):
        """The same interruption, where the two calls word the question apart.

        The refused call then has no directory of its own to be found missing,
        so a cleanup that ran at all would find nothing to stop it and would
        hand back an id the finished run is still holding — after which that
        run could be built a second time.
        """
        run = self.create(question="A 會漲嗎")
        claim = next(
            path for path in run.path.parent.iterdir() if path.name.startswith(".")
        )
        before = claim.read_bytes()

        with self.interrupt_the_first_owner_read():
            with self.assertRaises(KeyboardInterrupt):
                self.create(question="B 會跌嗎")

        self.assertTrue(claim.is_file(), "既有 run 的 claim 被放掉了")
        self.assertEqual(before, claim.read_bytes())
        self.assertEqual([run.path.name], self.run_dirs_under("2026-08-01"))
        with self.assertRaises(RunAlreadyExistsError):
            self.create(question="C 又一次")

    def held_run(self, run_id, question="A 會漲嗎"):
        """One finished run holding its claim, with something to lose."""
        run = self.store.create_run(run_id, ["news"], question=question)
        run.write_text("report.md", "# A 的報告")
        claim = next(
            path for path in run.path.parent.iterdir() if path.name.startswith(".")
        )
        return run, claim, claim.read_bytes()

    def assert_the_held_run_is_untouched(self, run_id, run, claim, claim_bytes):
        """Nothing of the finished run moved, and its id is still refused."""
        self.assertTrue(claim.is_file(), "既有 run 的 claim 被放掉了")
        self.assertEqual(claim_bytes, claim.read_bytes())
        self.assertEqual(
            "# A 的報告", (run.path / "report.md").read_text(encoding="utf-8")
        )
        self.assertEqual([run.path.name], self.run_dirs_under("2026-08-01"))
        self.assertEqual(run.path, resolve_run_dir(self.data_root, run_id))
        # 第三個 caller 仍然拿不到這個 run id。
        with self.assertRaises(RunAlreadyExistsError):
            self.store.create_run(run_id, ["news"], question="C 第三個人")
        self.assertEqual([run.path.name], self.run_dirs_under("2026-08-01"))

    def test_a_failure_before_the_claim_lands_frees_no_one_elses_run_id(self):
        """The second caller never took the id, so it may not give it back.

        Nothing this call can see afterwards distinguishes the claim it failed
        to take from one it took: same path, same contents, and no directory
        of its own to be found missing. The only thing that would have told
        them apart is a link that never happened.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        run, claim, claim_bytes = self.held_run(run_id)
        full = OSError(errno.ENOSPC, "No space left on device")

        for where, target in (
            ("mkstemp", (run_store.tempfile, "mkstemp")),
            ("fsync", (os, "fsync")),
        ):
            with self.subTest(failing=where):
                with mock.patch.object(*target, side_effect=full):
                    with self.assertRaises(OSError):
                        # 題目不同 ⇒ 這次呼叫的目錄名跟既有 run 不一樣。
                        self.store.create_run(run_id, ["news"], question="B 會跌嗎")

                self.assert_the_held_run_is_untouched(
                    run_id, run, claim, claim_bytes
                )

    def test_a_link_that_failed_for_another_reason_frees_no_ones_run_id(self):
        """The one failure where only the inode can tell the claims apart.

        Everything earlier leaves nothing recorded at all. Here the call has
        got as far as knowing which file it *would* have linked, and the claim
        it is looking at exists, is a file, and contains this very run id —
        because a run of the same id wrote it. Only "that is not the file I
        linked" refuses.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        run, claim, claim_bytes = self.held_run(run_id)

        with mock.patch.object(
            os, "link", side_effect=OSError(errno.EXDEV, "Invalid cross-device link")
        ):
            with self.assertRaises(OSError):
                self.store.create_run(run_id, ["news"], question="B 會跌嗎")

        self.assert_the_held_run_is_untouched(run_id, run, claim, claim_bytes)

    def test_a_scratch_that_will_not_go_away_changes_no_conclusion(self):
        """Tidying up may not speak for the call.

        ``link`` already refused this id; if clearing the scratch file then
        raises, that error must not replace the refusal, or the caller reads a
        generic failure and cleans up as though it had taken something.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        run, claim, claim_bytes = self.held_run(run_id)
        real_unlink = os.unlink
        seen = []

        def flaky_unlink(path, **kwargs):
            seen.append(Path(path).name)
            if len(seen) == 1:
                raise OSError(errno.EIO, "I/O error")
            return real_unlink(path, **kwargs)

        with mock.patch.object(os, "unlink", flaky_unlink):
            with self.assertRaises(RunAlreadyExistsError):
                self.store.create_run(run_id, ["news"], question="B 會跌嗎")

        self.assertTrue(seen and seen[0].startswith(".claim-"), seen)
        self.assert_the_held_run_is_untouched(run_id, run, claim, claim_bytes)

    def test_a_directory_that_could_not_be_removed_keeps_the_run_id_occupied(self):
        """The documented half of the trade: a refusal, never an ambiguous run.

        If the half-built directory has to stay, the id must stay taken with
        it — released, it would let a retry build a second directory that
        answers the same lookup.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        expected = self.runs_root / "2026-08-01" / "1530-收不掉-{}".format(
            digest_of(run_id)
        )
        real_mkdir = Path.mkdir

        def mkdir_then_interrupt(target, *args, **kwargs):
            result = real_mkdir(target, *args, **kwargs)
            if target == expected:
                raise KeyboardInterrupt
            return result

        with mock.patch.object(Path, "mkdir", mkdir_then_interrupt):
            with mock.patch.object(
                run_store.shutil,
                "rmtree",
                side_effect=OSError(errno.EACCES, "Permission denied"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    self.create(run_id, question="收不掉")

        self.assertTrue(expected.is_dir())
        self.assertTrue(
            (
                self.runs_root
                / "2026-08-01"
                / ".1530-{}.run-claim".format(digest_of(run_id))
            ).is_file()
        )
        with self.assertRaises(RunAlreadyExistsError):
            self.create(run_id, question="重試應該被擋下")

    def test_an_interrupt_after_a_failed_cleanup_still_keeps_the_run_id(self):
        """The half-built directory survived, so the id must stay taken.

        Whether it survived is asked of the disk at the moment of release. Had
        it instead been remembered by clearing a permission after the removal
        came back, an interrupt landing between those two would leave the
        permission standing over a directory that is still there — and the id
        would be handed out with a corpse under it.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        expected = self.runs_root / "2026-08-01" / "1530-半成品-{}".format(
            digest_of(run_id)
        )
        claim = self.runs_root / "2026-08-01" / ".1530-{}.run-claim".format(
            digest_of(run_id)
        )
        real_mkdir = Path.mkdir

        def mkdir_then_fail(target, *args, **kwargs):
            result = real_mkdir(target, *args, **kwargs)
            if target == expected:
                raise RuntimeError("建到一半壞了")
            return result

        def refuse_then_interrupt(path):
            raise KeyboardInterrupt

        with mock.patch.object(Path, "mkdir", mkdir_then_fail):
            with mock.patch.object(
                run_store.shutil, "rmtree", side_effect=OSError(errno.EACCES, "denied")
            ):
                # 清理已經確定失敗，然後在「記下這件事」之前被中斷。
                with mock.patch.object(
                    run_store, "_remove_started_directory", refuse_then_interrupt
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        self.create(run_id, question="半成品")

        self.assertTrue(expected.is_dir(), "半成品目錄應該還在")
        self.assertTrue(claim.is_file(), "半成品還在，run id 卻被放掉了")
        with self.assertRaises(RunAlreadyExistsError):
            self.create(run_id, question="重試應該被擋下")

    def test_a_recycled_inode_never_frees_a_later_callers_run_id(self):
        """Inode numbers come back; a nonce does not.

        A call that failed before linking recorded the number of a scratch
        file that has since gone away. The filesystem is free to give that
        number to whatever is allocated next — including the claim a *later*
        caller links for the same run id, whose contents read identically.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        claim = self.runs_root / "2026-08-01" / ".1530-{}.run-claim".format(
            digest_of(run_id)
        )
        claim.parent.mkdir(parents=True, exist_ok=True)

        # 一次失敗的取得：link 之前就炸了，但候選 inode 已經記下來。
        stale = []
        with mock.patch.object(
            os, "link", side_effect=OSError(errno.EXDEV, "cross-device")
        ):
            with self.assertRaises(OSError):
                run_store._take_run_id(claim, run_id, stale)
        self.assertEqual(1, len(stale))

        # 後來另一個 caller 合法取得同一個 run id。
        later = self.create(run_id, question="後來的人")
        self.assertTrue(claim.is_file())
        claim_bytes = claim.read_bytes()

        # 模擬 inode 被回收：舊呼叫記下的號碼，正好就是新 claim 的號碼。
        recycled = [(claim.stat().st_ino, stale[0][1])]

        run_store._release_run_id(claim, run_id, recycled, None)

        self.assertTrue(claim.is_file(), "舊呼叫用回收的 inode 放掉了別人的 claim")
        self.assertEqual(claim_bytes, claim.read_bytes())
        self.assertEqual(later.path, resolve_run_dir(self.data_root, run_id))
        with self.assertRaises(RunAlreadyExistsError):
            self.create(run_id, question="第三個人")

    def test_a_failure_never_releases_a_claim_that_is_no_longer_ours(self):
        """The false-positive direction of releasing: hand back only your own.

        Cleaning up must not free a run id that some other run is now holding,
        or a third caller could build a second directory under it. Here an
        outside hand rewrites the claim while this call is still building.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        claim = self.runs_root / "2026-08-01" / ".1530-{}.run-claim".format(
            digest_of(run_id)
        )
        expected = self.runs_root / "2026-08-01" / "1530-換手-{}".format(
            digest_of(run_id)
        )
        real_mkdir = Path.mkdir

        def mkdir_then_change_hands(target, *args, **kwargs):
            result = real_mkdir(target, *args, **kwargs)
            if target == expected:
                claim.write_text("20260801T073000Z-btc-other1\n", encoding="utf-8")
                raise KeyboardInterrupt
            return result

        with mock.patch.object(Path, "mkdir", mkdir_then_change_hands):
            with self.assertRaises(KeyboardInterrupt):
                self.create(run_id, question="換手")

        self.assertEqual(
            "20260801T073000Z-btc-other1",
            claim.read_text(encoding="utf-8").strip(),
        )
        # 半成品目錄仍然要收掉——它是這一次建的，而且會被 lookup 找到。
        self.assertEqual([], self.run_dirs_under("2026-08-01"))
        self.assertIsNone(resolve_run_dir(self.data_root, run_id))

    def test_an_interrupt_right_after_the_claim_does_not_burn_the_run_id(self):
        """The gap between taking the id and doing anything with it."""
        run_id = "20260801T073000Z-btc-aaa111"
        real_take = run_store._take_run_id

        def take_then_interrupt(claim, identifier, record):
            real_take(claim, identifier, record)
            raise KeyboardInterrupt

        with mock.patch.object(run_store, "_take_run_id", take_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.create(run_id, question="剛拿到就被中斷")

        self.assert_nothing_of_the_run_survived(run_id)
        self.assert_the_run_id_can_still_be_used(run_id)

    def test_an_interrupt_after_mkdir_leaves_no_run_a_lookup_can_reach(self):
        """The directory exists on disk before ``mkdir`` returns to Python.

        Deciding what to clean up from a flag set *after* that return would
        call this directory "not mine" while a lookup was already finding it.
        """
        run_id = "20260801T073000Z-btc-aaa111"
        expected = self.runs_root / "2026-08-01" / "1530-中斷-{}".format(
            digest_of(run_id)
        )
        real_mkdir = Path.mkdir

        def mkdir_then_interrupt(target, *args, **kwargs):
            result = real_mkdir(target, *args, **kwargs)
            if target == expected:
                raise KeyboardInterrupt
            return result

        with mock.patch.object(Path, "mkdir", mkdir_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.create(run_id, question="中斷")

        self.assert_nothing_of_the_run_survived(run_id)
        self.assert_the_run_id_can_still_be_used(run_id)

    def test_a_directory_this_call_did_not_create_is_left_alone(self):
        """The false-positive direction of cleaning up: never delete a stranger.

        Holding the claim does not make the directory this call's to remove.
        Here an outside hand deleted the claim of a finished run, so the next
        create takes the id and finds the directory already there.
        """
        run = self.create(question="A 會漲嗎")
        (run.path / "report.md").write_text("原始報告", encoding="utf-8")
        claim = next(
            path for path in run.path.parent.iterdir() if path.name.startswith(".")
        )
        claim.unlink()

        with self.assertRaises(RunAlreadyExistsError):
            self.create(question="A 會漲嗎")

        self.assertEqual(
            "原始報告", (run.path / "report.md").read_text(encoding="utf-8")
        )
        self.assertEqual([run.path.name], self.run_dirs_under("2026-08-01"))

    def test_a_duplicate_create_never_touches_the_run_that_already_exists(self):
        """The other false positive: a refused duplicate removes nothing."""
        run = self.create(question="A 會漲嗎")
        (run.path / "report.md").write_text("原始報告", encoding="utf-8")
        before = sorted(path.name for path in run.path.parent.iterdir())

        with self.assertRaises(RunAlreadyExistsError):
            self.create(question="B 會跌嗎")

        self.assertEqual(before, sorted(path.name for path in run.path.parent.iterdir()))
        self.assertEqual(
            "原始報告", (run.path / "report.md").read_text(encoding="utf-8")
        )
        self.assertEqual(
            run.path, resolve_run_dir(self.data_root, "20260801T073000Z-btc-8f3a2c")
        )

    def test_a_run_id_is_taken_by_linking_a_finished_claim_into_place(self):
        """The deterministic counterpart to the race test.

        The race measures the behaviour; this states the mechanism it rests
        on, so the guarantee does not have to be read off a statistic. Two
        properties, and the claim needs both: the name appears in one step
        that a second caller cannot also win, and it appears already carrying
        its owner.
        """
        linked = []
        real_link = os.link

        def spy(source, target, **kwargs):
            linked.append((Path(source).name, Path(target).name))
            return real_link(source, target, **kwargs)

        with mock.patch.object(os, "link", spy):
            self.create(question="2330 會漲嗎")

        claims = [pair for pair in linked if pair[1].endswith(".run-claim")]
        self.assertEqual(1, len(claims), linked)
        self.assertTrue(claims[0][0].startswith(".claim-"), claims)

        claim = self.runs_root / "2026-08-01" / ".1530-{}.run-claim".format(
            digest_of("20260801T073000Z-btc-8f3a2c")
        )
        recorded = claim.read_text(encoding="utf-8").splitlines()
        self.assertEqual("20260801T073000Z-btc-8f3a2c", recorded[0])
        # 第二行是這一次取得的 nonce：run id 相同的兩個 caller 靠它分辨。
        self.assertRegex(recorded[1], r"^[0-9a-f]{32}$")

    def test_a_second_take_never_rewrites_the_claim_already_there(self):
        """The behavioural half of the same mechanism, without a race."""
        run = self.create(question="A 會漲嗎")
        claim = next(
            path for path in run.path.parent.iterdir() if path.name.startswith(".")
        )
        before = claim.read_bytes()

        with self.assertRaises(RunAlreadyExistsError):
            self.create(question="B 會跌嗎")

        self.assertEqual(before, claim.read_bytes())

    def test_a_create_that_could_not_finish_does_not_burn_the_run_id(self):
        run_id = "20260801T073000Z-btc-aaa111"

        with self.assertRaises(RunStoreError):
            self.store.create_run(run_id, ["news", "news"], question="重複席位")
        with self.assertRaises(OSError):
            self.store.create_run(run_id, ["a" * 300], question="席位名稱過長")

        run = self.store.create_run(run_id, ["news"], question="這次成功了")

        self.assertTrue(run.path.is_dir())
        self.assertEqual(run.path, resolve_run_dir(self.data_root, run_id))

    def test_the_claim_is_never_mistaken_for_a_run_directory(self):
        run = self.create(question="2330 會漲嗎")

        siblings = sorted(path.name for path in run.path.parent.iterdir())

        self.assertEqual(2, len(siblings), siblings)
        self.assertEqual([run.path.name], self.run_dirs_under("2026-08-01"))
        claim = next(name for name in siblings if name != run.path.name)
        self.assertTrue(claim.startswith("."), claim)
        self.assertFalse((run.path.parent / claim).is_dir())

    def test_latest_points_at_the_dated_run_directory(self):
        run = self.create(question="2330 未來七天會不會漲")

        self.store.point_latest_at(run)

        latest = json.loads(
            (self.runs_root / "latest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("20260801T073000Z-btc-8f3a2c", latest["run_id"])
        # 格式保留，值指向新的分層路徑——不是只指向「run.path 而已」。
        self.assertEqual(
            str(
                self.runs_root
                / "2026-08-01"
                / "1530-2330-未來七天會不會漲-deb97098567e5181"
            ),
            latest["run_dir"],
        )
        self.assertEqual(str(run.path), latest["run_dir"])
        self.assertEqual(str(run.path / "report.md"), latest["report_md"])
        self.assertEqual(str(run.path / "report.html"), latest["report_html"])
        self.assertEqual(str(run.path / "debate.html"), latest["debate_html"])


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.store = RunStore(self.data_root)

    def test_create_run_makes_run_and_seat_directories(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["spot-technical", "news"])

        self.assertTrue(run.path.is_dir())
        self.assertEqual(
            self.data_root / "runs" / "2026-08-01" / "1530-btc-deb97098567e5181",
            run.path,
        )
        self.assertTrue(run.seat_dir("spot-technical").is_dir())
        self.assertTrue(run.seat_dir("news").is_dir())
        self.assertTrue((run.seat_dir("news") / "attempts").is_dir())
        for name in ("snapshots", "reports", "late", "diagnostics"):
            self.assertTrue((run.path / name).is_dir(), name)

    def test_creating_the_same_run_id_twice_fails_closed(self):
        self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

        with self.assertRaises(RunAlreadyExistsError):
            self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

    def test_artifacts_are_write_once(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])
        run.write_json("manifest.json", {"run_id": "20260801T073000Z-btc-8f3a2c"})

        with self.assertRaises(ArtifactAlreadyExistsError):
            run.write_json("manifest.json", {"run_id": "tampered"})

        self.assertEqual(
            "20260801T073000Z-btc-8f3a2c",
            json.loads((run.path / "manifest.json").read_text(encoding="utf-8"))["run_id"],
        )

    def test_jsonl_artifact_writes_one_json_object_per_line(self):
        run = self.store.create_run("20260801T073000Z-btc-8f3a2c", ["news"])

        run.write_jsonl("evidence.jsonl", [{"evidence_id": "a"}, {"evidence_id": "b"}])

        lines = (run.path / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(["a", "b"], [json.loads(line)["evidence_id"] for line in lines])

    def test_second_run_never_touches_the_first_run(self):
        first = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        first.write_text("report.md", "# first run")

        second = self.store.create_run("20260801T073000Z-btc-bbb222", ["news"])
        second.write_text("report.md", "# second run")

        self.assertEqual("# first run", (first.path / "report.md").read_text(encoding="utf-8"))
        self.assertEqual("# second run", (second.path / "report.md").read_text(encoding="utf-8"))
        self.assertNotEqual(first.path, second.path)

    def test_latest_pointer_is_the_only_mutable_file(self):
        first = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        self.store.point_latest_at(first)
        second = self.store.create_run("20260801T073000Z-btc-bbb222", ["news"])
        self.store.point_latest_at(second)

        latest = json.loads((self.data_root / "runs" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual("20260801T073000Z-btc-bbb222", latest["run_id"])
        self.assertEqual(str(second.path), latest["run_dir"])

    def test_content_hash_is_recorded_for_written_artifacts(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        run.write_text("report.md", "# first run")

        digest = run.artifact_hashes["report.md"]
        self.assertEqual(64, len(digest))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_parallel_seat_attempt_writes_are_isolated_and_atomic(self):
        run = self.store.create_run(
            "20260801T073000Z-btc-aaa111", ["spot-technical", "news"]
        )
        barrier = threading.Barrier(2)

        def submit(seat_id):
            barrier.wait()
            return run.record_attempt(
                seat_id,
                seat_id + "-a1",
                raw_text='{"seat_id":"' + seat_id + '"}',
                validated_payload={"seat_id": seat_id, "records": []},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ("spot-technical", "news")))

        self.assertEqual([True, True], results)
        for seat_id in ("spot-technical", "news"):
            attempt = run.seat_dir(seat_id) / "attempts" / (seat_id + "-a1")
            self.assertEqual(
                seat_id,
                json.loads((attempt / "validated.json").read_text(encoding="utf-8"))["seat_id"],
            )

    def test_first_valid_attempt_is_adopted_and_later_success_is_diagnostic(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])

        first = run.record_attempt("news", "news-a1", "{}", {"records": [1]})
        second = run.record_attempt("news", "news-a2", "{}", {"records": [2]})

        self.assertTrue(first)
        self.assertFalse(second)
        adopted = json.loads((run.seat_dir("news") / "adopted.json").read_text(encoding="utf-8"))
        self.assertEqual("news-a1", adopted["attempt_id"])
        diagnostic = json.loads(
            (run.path / "diagnostics" / "attempts" / "news-a2.json").read_text(encoding="utf-8")
        )
        self.assertEqual("not_adopted_first_valid_already_selected", diagnostic["reason"])

    def test_syndicated_sources_are_not_counted_twice(self):
        cards = [
            {
                "evidence_id": "news-01",
                "source_url": "https://wire.invalid/story",
                "source_origin": "press-release:abc",
            },
            {
                "evidence_id": "news-02",
                "source_url": "https://publisher.invalid/repost",
                "source_origin": "press-release:abc",
            },
        ]

        unique, duplicates = deduplicate_evidence(cards)

        self.assertEqual(["news-01"], [card["evidence_id"] for card in unique])
        self.assertEqual(
            [{"evidence_id": "news-02", "duplicate_of": "news-01"}], duplicates
        )

    def test_sealed_snapshot_cannot_be_replaced_and_tampering_is_detected(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        records = [{"evidence_id": "news-01"}]

        seal = run.seal_evidence_snapshot(records, "2026-08-01T07:35:00Z", 300000)
        self.assertEqual(64, len(seal["sha256"]))
        self.assertEqual(seal, run.verify_evidence_snapshot())
        with self.assertRaises(SnapshotSealedError):
            run.seal_evidence_snapshot(records, "2026-08-01T07:35:01Z", 301000)

        (run.path / seal["path"]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ArtifactTamperedError):
            run.verify_evidence_snapshot()

    def test_format_repair_preserves_before_after_and_rejects_semantic_change(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        trailing_comma = '{"stance":"bullish","evidence_ids":["news-01"],}'
        run.record_attempt(
            "news", "news-a1", trailing_comma, {"records": ["pre-repair capture"]}
        )

        path = run.record_format_repair(
            repair_id="repair-01",
            seat_id="news",
            source_attempt_id="news-a1",
            repair_attempt_id="format-repair-a1",
            before_text=trailing_comma,
            after_text='{\n  "stance": "bullish",\n  "evidence_ids": ["news-01"]\n}',
            reason="normalize JSON formatting",
            operator="format-repair-agent",
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("before", record)
        self.assertIn("after", record)
        self.assertEqual("news-a1", record["lineage"]["source_attempt_id"])
        self.assertEqual(
            "agents/news/attempts/news-a1/raw.txt", record["lineage"]["source_path"]
        )
        self.assertRegex(record["lineage"]["source_sha256"], r"^[0-9a-f]{64}$")

        numeric = '{"value":1}'
        run.record_attempt("news", "news-a2", numeric, {"records": []})
        with self.assertRaises(FormatRepairSemanticChangeError):
            run.record_format_repair(
                repair_id="repair-02",
                seat_id="news",
                source_attempt_id="news-a2",
                repair_attempt_id="format-repair-a2",
                before_text=numeric,
                after_text='{"value":true}',
                reason="must preserve JSON types",
                operator="format-repair-agent",
            )

        with self.assertRaises(FormatRepairSemanticChangeError):
            run.record_format_repair(
                repair_id="repair-03",
                seat_id="news",
                source_attempt_id="news-a1",
                repair_attempt_id="format-repair-a3",
                before_text=trailing_comma,
                after_text='{"stance":"bearish"}',
                reason="not a format-only repair",
                operator="format-repair-agent",
            )

        with self.assertRaises(RunStoreError) as caught:
            run.record_format_repair(
                repair_id="repair-04",
                seat_id="news",
                source_attempt_id="news-missing",
                repair_attempt_id="format-repair-a4",
                before_text="{}",
                after_text="{}",
                reason="missing source",
                operator="format-repair-agent",
            )
        self.assertIn("news-missing", str(caught.exception))

    def test_manifest_index_traces_hash_and_source_and_detects_tamper(self):
        run = self.store.create_run("20260801T073000Z-btc-aaa111", ["news"])
        run.write_text("evidence.jsonl", "{}\n", source="validated seat attempts")

        index = run.artifact_index()

        self.assertEqual("evidence.jsonl", index["evidence.jsonl"]["path"])
        self.assertEqual("validated seat attempts", index["evidence.jsonl"]["source"])
        self.assertTrue(run.verify_artifacts(index))
        (run.path / "evidence.jsonl").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ArtifactTamperedError):
            run.verify_artifacts(index)


if __name__ == "__main__":
    unittest.main()
