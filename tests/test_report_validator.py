"""Ticket #10: confidence caps, prohibited advice and the timed report workflow."""

import unittest

from hoya_market_agents.report_contract import (
    CONFIDENCE_ICONS,
    CONFIDENCE_LEVELS,
    ReportContractError,
    confidence_cap,
    validate_market_report,
)
from hoya_market_agents.report_fixtures import (
    build_fixture,
    build_forged_fixture,
    load_fixture,
)
from hoya_market_agents.report_workflow import (
    CORE_DRAFT_LIMIT_MS,
    CORRECTION_WINDOW_MS,
    HARD_DEADLINE_MS,
    RENDER_WINDOW_MS,
    run_report_workflow,
)
from hoya_market_agents.debate_rules import debate_rules
from hoya_market_agents.seats import SEAT_IDS
from tests.fakes import FixedClock


def _scripted_core(reports, cost_ms, clock):
    """A fake Core Agent that burns injected clock time per attempt."""
    drafts = list(reports)
    costs = list(cost_ms)

    def author(attempt, errors):
        clock.advance_ms(costs[attempt - 1])
        return drafts[attempt - 1]

    return author


def ballot(adopted_votes, valid_votes=len(SEAT_IDS), opposing="bearish"):
    """Seven seats' final stances: ``adopted_votes`` bullish, then ``opposing``.

    ``valid_votes`` 之後的席位沒投到票（``None``）。
    """
    return tuple(
        ("bullish" if index < adopted_votes else opposing)
        if index < valid_votes
        else None
        for index in range(len(SEAT_IDS))
    )


def consensus_with(adopted_votes, valid_votes=len(SEAT_IDS)):
    """A consensus fixture where exactly ``adopted_votes`` valid votes adopt.

    整份 fixture 由同一份票面推導（``build_fixture``），所以它同時通過完整
    ``validate_market_report``——半套修改做出來的東西走不到它宣稱的路徑，那正
    是本票第 1 輪被抓到的假綠。每張有效票只引用自己那一張證據卡（``ev-01``
    對第一席，依此類推），降級測試因此可以逐張擺弄。
    """
    return build_fixture(
        ballot(adopted_votes, valid_votes),
        consensus_status="consensus",
        adopted_stance="bullish",
        confidence=("red", "🔴", "由測試自行指定燈號時再覆寫。"),
    )


def no_consensus_with(*, bullish, bearish, neutral=0, valid_votes=len(SEAT_IDS)):
    """A directionless fixture: nobody's stance was adopted."""
    stances = (("bullish",) * bullish + ("bearish",) * bearish + ("neutral",) * neutral)
    stances += (None,) * (len(SEAT_IDS) - len(stances))
    return build_fixture(
        stances[: len(SEAT_IDS)],
        consensus_status="no_consensus",
        adopted_stance=None,
        confidence=("red", "🔴", "未達共識，沒有可報告的共識強度。"),
    )


def cap_of(fixture):
    return confidence_cap(fixture["report"], fixture["sources"])


class FixtureBuilderGuaranteeTests(unittest.TestCase):
    """``build_fixture`` 的宣稱必須逐字為真，不是「作者相信它一致」。

    第 2 輪的 S1 是「測試看起來測了 A，實際碰不到 A」；這個建構器就是為了讓那
    件事不能再發生。那它自己的保證就不能只靠 docstring——這裡兩個方向都測，而
    且是**掃過參數空間**，不是列舉今天剛好合法的那幾個呼叫。
    """

    ALL_STANCES = ("bullish", "bearish", "neutral")

    def test_every_ballot_and_every_light_at_or_below_the_cap_builds(self):
        # 誤擋方向：合法組合一個都不能被建構器擋下來。
        built = 0
        for adopted_votes in range(len(SEAT_IDS) + 1):
            for valid_votes in range(adopted_votes, len(SEAT_IDS) + 1):
                fixture = consensus_with(adopted_votes, valid_votes=valid_votes)
                cap = cap_of(fixture)
                for level in CONFIDENCE_LEVELS[: CONFIDENCE_LEVELS.index(cap) + 1]:
                    with self.subTest(
                        adopted=adopted_votes, valid=valid_votes, level=level
                    ):
                        self.assertIsNotNone(
                            build_fixture(
                                ballot(adopted_votes, valid_votes),
                                consensus_status="consensus",
                                adopted_stance="bullish",
                                confidence=(level, CONFIDENCE_ICONS[level], "說明。"),
                            )
                        )
                        built += 1
        self.assertGreater(built, 50, "掃描範圍太小就證明不了什麼")

    def test_no_light_above_the_cap_can_ever_be_built(self):
        # 漏擋方向：對每一種票數，掃過所有比上限更好的燈號。
        for adopted_votes in range(len(SEAT_IDS) + 1):
            cap = cap_of(consensus_with(adopted_votes))
            for level in CONFIDENCE_LEVELS[CONFIDENCE_LEVELS.index(cap) + 1 :]:
                with self.subTest(adopted=adopted_votes, level=level):
                    with self.assertRaises(ReportContractError):
                        build_fixture(
                            ballot(adopted_votes),
                            consensus_status="consensus",
                            adopted_stance="bullish",
                            confidence=(level, CONFIDENCE_ICONS[level], "說明。"),
                        )

    def test_a_directionless_status_may_not_be_built_with_an_adopted_stance(self):
        for status in (
            "no_consensus",
            "insufficient_data",
            "failed_insufficient_valid_votes",
            "validation_failed",
        ):
            for stance in self.ALL_STANCES:
                with self.subTest(status=status, stance=stance):
                    with self.assertRaises(ReportContractError):
                        build_fixture(
                            ballot(7),
                            consensus_status=status,
                            adopted_stance=stance,
                            confidence=("red", "🔴", "說明。"),
                        )

    def test_a_consensus_status_may_not_be_built_without_an_adopted_stance(self):
        with self.assertRaises(ReportContractError):
            build_fixture(
                ballot(7),
                consensus_status="consensus",
                adopted_stance=None,
                confidence=("red", "🔴", "說明。"),
            )

    def test_no_light_outside_the_approved_set_can_ever_be_built(self):
        for level in ("yellow_green", "grene", "", None, 1):
            with self.subTest(level=repr(level)):
                with self.assertRaises(ReportContractError):
                    build_fixture(
                        ballot(7),
                        consensus_status="consensus",
                        adopted_stance="bullish",
                        confidence=(level, "🟢", "說明。"),
                    )

    def test_no_light_can_ever_be_built_with_another_lights_icon(self):
        for level in CONFIDENCE_LEVELS:
            for icon in CONFIDENCE_ICONS.values():
                if icon == CONFIDENCE_ICONS[level]:
                    continue
                with self.subTest(level=level, icon=icon):
                    with self.assertRaises(ReportContractError):
                        build_fixture(
                            ballot(7),
                            consensus_status="consensus",
                            adopted_stance="bullish",
                            confidence=(level, icon, "說明。"),
                        )

    def test_an_empty_confidence_text_cannot_be_built(self):
        for text in ("", "   "):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ReportContractError):
                    build_fixture(
                        ballot(7),
                        consensus_status="consensus",
                        adopted_stance="bullish",
                        confidence=("blue", "🔵", text),
                    )

    def test_the_forged_builder_deliberately_skips_the_contract(self):
        """兩個入口的差別必須看得見：一個保證合法，一個明講自己不保證。"""
        arguments = dict(
            stances=ballot(7),
            consensus_status="no_consensus",
            adopted_stance="bullish",
            confidence=("red", "🔴", "說明。"),
        )

        with self.assertRaises(ReportContractError):
            build_fixture(**arguments)

        forged = build_forged_fixture(**arguments)
        self.assertEqual("no_consensus", forged["report"]["consensus_status"])
        self.assertEqual("bullish", forged["report"]["adopted_stance"])
        with self.assertRaises(ReportContractError):
            validate_market_report(forged["report"], forged["sources"])

    def test_the_known_limit_is_semantic_plausibility_not_contract_validity(self):
        """誠實標示的邊界：契約不判斷議場該不該採納那個立場。

        六票偏多卻宣告採納偏空仍然建得出來——這一層由 run_verifier 的停止語意
        攔下（縱深防禦）。把它寫成測試，是為了讓這個已知邊界有紀錄、不會被下
        一個讀 docstring 的人誤讀成更強的保證。
        """
        fixture = build_fixture(
            ballot(6),
            consensus_status="consensus",
            adopted_stance="bearish",
            confidence=("red", "🔴", "說明。"),
        )

        self.assertEqual("bearish", fixture["report"]["adopted_stance"])
        self.assertEqual(6, fixture["report"]["tally"]["bullish"])


class VoteCountLightMatrixTests(unittest.TestCase):
    """ADR 0003：燈號＝最終採納立場的有效票數，7藍／6綠／5黃／4橘／<4紅。"""

    def test_the_light_is_the_adopted_vote_count(self):
        matrix = ((7, "blue"), (6, "green"), (5, "yellow"), (4, "orange"), (3, "red"))
        for adopted_votes, level in matrix:
            with self.subTest(adopted_votes=adopted_votes):
                self.assertEqual(level, cap_of(consensus_with(adopted_votes)))

    def test_votes_lost_to_missing_seats_map_the_same_way(self):
        # 反方票與缺席票對燈號的差別只在有效票總數；採納票數才是燈號。
        matrix = ((6, "green"), (5, "yellow"), (4, "orange"))
        for adopted_votes, level in matrix:
            with self.subTest(adopted_votes=adopted_votes):
                fixture = consensus_with(adopted_votes, valid_votes=adopted_votes)
                self.assertEqual(level, cap_of(fixture))

    def test_every_fixture_this_module_builds_passes_the_whole_contract(self):
        """假綠防線：只改一半 bundle 的 fixture 走不到它宣稱要測的路徑。

        第 1 輪的 `3 票未達共識` 測試就是這樣壞掉的——report 說未達共識、
        official votes 仍說 consensus，完整契約會列出 16 個問題。這個測試讓
        整個模組用的 fixture 建構器必須產出真的能發布的報告。
        """
        cases = [consensus_with(votes) for votes in range(len(SEAT_IDS) + 1)]
        cases += [consensus_with(votes, valid_votes=votes) for votes in (4, 5, 6)]
        cases += [
            no_consensus_with(bullish=3, bearish=3, neutral=1),
            no_consensus_with(bullish=3, bearish=0, valid_votes=3),
        ]
        for index, fixture in enumerate(cases):
            with self.subTest(case=index):
                fixture["report"]["confidence"] = {
                    "level": cap_of(fixture),
                    "icon": CONFIDENCE_ICONS[cap_of(fixture)],
                    "text": "以客觀上限發布。",
                }
                self.assertIsNotNone(
                    validate_market_report(fixture["report"], fixture["sources"])
                )

    def test_a_directionless_report_is_red_whatever_the_losing_blocs_hold(self):
        """驗收條件 1 的「3 票→紅＋未達共識」，兩種形狀各一。

        ADR 0003 決策 1：燈號＝**最終採納立場**的有效票數。未達共識時沒有採納
        立場，可數的採納票數就是 0，於是落在階梯最底一級。用最大落敗集團的票
        數頂替，等於替一個議場明確沒有採納的立場報告共識強度。
        """
        seven_valid = no_consensus_with(bullish=3, bearish=3, neutral=1)
        only_three_valid = no_consensus_with(bullish=3, bearish=0, valid_votes=3)

        self.assertEqual(7, seven_valid["sources"]["votes"]["valid_vote_count"])
        self.assertEqual({"bullish": 3, "bearish": 3, "neutral": 1}, seven_valid["report"]["tally"])
        self.assertEqual("red", cap_of(seven_valid))

        self.assertEqual(3, only_three_valid["sources"]["votes"]["valid_vote_count"])
        self.assertEqual("red", cap_of(only_three_valid))

    def test_a_directionless_report_may_not_publish_any_better_light(self):
        for level in ("orange", "yellow", "green", "blue"):
            with self.subTest(level=level):
                fixture = no_consensus_with(bullish=3, bearish=3, neutral=1)
                fixture["report"]["confidence"] = {
                    "level": level,
                    "icon": CONFIDENCE_ICONS[level],
                    "text": "宣告高於上限的燈號。",
                }
                with self.assertRaises(ReportContractError) as ctx:
                    validate_market_report(fixture["report"], fixture["sources"])
                self.assertIn(
                    "信心 {} 高於資料上限 red".format(level), ctx.exception.problems
                )

    def test_a_forged_ballot_with_no_stances_at_all_earns_no_light(self):
        """七張「有效票」卻沒有任何立場，不得算成七票採納。

        這裡的票面**故意**是不一致的——它就是被測的東西：``votes.json`` 被竄改
        成 state=valid、final_stance=null 時，「立場等於 adopted」對每一列都成
        立（都是 ``None``），少了 ``adopted is None`` 那半段守衛就會算出藍燈。
        這不是拿半套 fixture 冒充合法報告，是對偽造形狀的防線。
        """
        # 逐列偽造（state=valid 卻沒有立場）沒有任何建構器表達得出來，只能就地
        # 改；改的是「有效票列」本身，不是報告與票數各說各話。
        fixture = build_forged_fixture(
            ballot(7),
            consensus_status="consensus",
            adopted_stance=None,
            confidence=("red", "🔴", "說明。"),
        )
        for row in fixture["sources"]["votes"]["votes"]:
            row["final_stance"] = None

        self.assertEqual("red", cap_of(fixture))

    def test_a_forged_record_that_adopts_without_consensus_earns_no_light(self):
        """狀態說未達共識、卻同時填了採納立場——同樣是偽造形狀的防線。

        ``confidence_ceiling`` 直接拿 ``votes.json`` 的欄位組骨架送進來，沒有先
        過完整契約，所以這個組合真的到得了 ``confidence_cap``。
        """
        fixture = build_forged_fixture(
            ballot(7),
            consensus_status="no_consensus",
            adopted_stance="bullish",
            confidence=("red", "🔴", "說明。"),
        )

        self.assertEqual("red", cap_of(fixture))

    def test_the_shipped_no_consensus_fixture_is_red(self):
        fixture = load_fixture("no-consensus-3-3-1")

        self.assertEqual("red", fixture["report"]["confidence"]["level"])
        self.assertEqual("red", cap_of(fixture))
        self.assertIsNotNone(
            validate_market_report(fixture["report"], fixture["sources"])
        )

    def test_a_consensus_report_still_earns_its_vote_count_light(self):
        # 誤擋方向：把未達共識壓成紅燈，不能連帶把有共識的報告也壓下去。
        for votes, level in ((7, "blue"), (6, "green"), (5, "yellow"), (4, "orange")):
            with self.subTest(votes=votes):
                self.assertEqual(level, cap_of(consensus_with(votes)))

    def test_process_failure_is_red_at_any_vote_count(self):
        fixture = consensus_with(7)
        fixture["report"]["process_failure"] = True

        self.assertEqual("red", cap_of(fixture))

    def test_every_failed_status_is_red(self):
        for status in (
            "failed_insufficient_valid_votes",
            "insufficient_data",
            "validation_failed",
        ):
            with self.subTest(status=status):
                fixture = consensus_with(7)
                fixture["report"]["consensus_status"] = status
                self.assertEqual("red", cap_of(fixture))

    def test_the_shipped_insufficient_votes_fixture_is_red(self):
        self.assertEqual("red", cap_of(load_fixture("insufficient-votes-3")))

    def test_the_shipped_six_to_one_fixture_is_green(self):
        self.assertEqual("green", cap_of(load_fixture("consensus-6-1")))


class SourceDowngradeTests(unittest.TestCase):
    """ADR 0003 只保留兩條降級，各自都要同時測「會擋」與「不會誤擋」。"""

    def test_a_single_independent_domain_costs_one_level(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["source_origin"] = "one-and-only.example"

        self.assertEqual("green", cap_of(fixture))

    def test_two_independent_domains_are_already_enough(self):
        fixture = consensus_with(7)
        for index, card in enumerate(fixture["sources"]["evidence"]):
            card["source_origin"] = "a.example" if index < 4 else "b.example"

        self.assertEqual("blue", cap_of(fixture))

    def test_only_the_adopted_stance_citations_count_for_independence(self):
        # 反方那一席自己一個網域，不能把採納立場的多網域拉下來。
        fixture = consensus_with(6)
        fixture["sources"]["evidence"][6]["source_origin"] = "a.example"
        fixture["sources"]["evidence"][0]["source_origin"] = "a.example"

        self.assertEqual("green", cap_of(fixture))

    def test_a_low_trust_source_costs_one_level(self):
        fixture = consensus_with(7)
        fixture["sources"]["evidence"][0]["source_tier"] = 3

        self.assertEqual("green", cap_of(fixture))

    def test_the_trusted_tiers_are_accepted_without_a_downgrade(self):
        for tier in (1, 2):
            with self.subTest(tier=tier):
                fixture = consensus_with(7)
                for card in fixture["sources"]["evidence"]:
                    card["source_tier"] = tier
                self.assertEqual("blue", cap_of(fixture))

    def test_a_tier_that_is_not_an_integer_is_not_trusted(self):
        # True == 1 與 1.0 == 1 在 Python 都成立；等級必須是真正的整數。
        for tier in (True, 1.0, "1", None):
            with self.subTest(tier=repr(tier)):
                fixture = consensus_with(7)
                fixture["sources"]["evidence"][0]["source_tier"] = tier
                self.assertEqual("green", cap_of(fixture))

    def test_a_low_trust_citation_outside_the_adopted_stance_is_ignored(self):
        fixture = consensus_with(6)
        fixture["sources"]["evidence"][6]["source_tier"] = 3

        self.assertEqual("green", cap_of(fixture))

    def test_a_low_trust_citation_inside_the_adopted_stance_does_count(self):
        fixture = consensus_with(6)
        fixture["sources"]["evidence"][0]["source_tier"] = 3

        self.assertEqual("yellow", cap_of(fixture))

    def test_social_macro_evidence_is_exempt_from_the_source_tier_rule(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["seat_id"] = "social-macro"
            card["source_tier"] = 3

        self.assertEqual("blue", cap_of(fixture))

    def test_the_exemption_covers_only_the_social_macro_seat(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["seat_id"] = "social-macro"
            card["source_tier"] = 3
        fixture["sources"]["evidence"][0]["seat_id"] = "news"

        self.assertEqual("green", cap_of(fixture))

    def test_the_exemption_does_not_cover_the_independent_domain_rule(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["seat_id"] = "social-macro"
            card["source_origin"] = "one-and-only.example"

        self.assertEqual("green", cap_of(fixture))

    def test_both_downgrades_stack(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["source_origin"] = "one-and-only.example"
        fixture["sources"]["evidence"][0]["source_tier"] = 3

        self.assertEqual("yellow", cap_of(fixture))

    def test_a_downgrade_never_falls_below_red(self):
        fixture = consensus_with(4)
        for card in fixture["sources"]["evidence"]:
            card["source_origin"] = "one-and-only.example"
            card["source_tier"] = 3

        self.assertEqual("red", cap_of(fixture))


class RemovedQualityDowngradeTests(unittest.TestCase):
    """ADR 0003 移除的規則不得再降級——這是 elif 級聯 bug 的回歸鎖。"""

    def test_seven_votes_with_a_single_evidence_category_still_get_blue(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["category"] = "spot-technical"

        self.assertEqual("blue", cap_of(fixture))

    def test_six_votes_with_a_single_evidence_category_still_get_green(self):
        fixture = consensus_with(6)
        for card in fixture["sources"]["evidence"]:
            card["category"] = "spot-technical"

        self.assertEqual("green", cap_of(fixture))

    def test_evidence_older_than_thirty_days_no_longer_downgrades(self):
        fixture = consensus_with(7)
        for card in fixture["sources"]["evidence"]:
            card["published_at_utc"] = "2020-01-01T00:00:00Z"

        self.assertEqual("blue", cap_of(fixture))

    def test_fatal_counterevidence_no_longer_downgrades(self):
        fixture = consensus_with(7)
        fixture["sources"]["evidence"][0]["fatal_counterevidence"] = True

        self.assertEqual("blue", cap_of(fixture))

    def test_a_material_contradiction_flag_no_longer_downgrades(self):
        fixture = consensus_with(7)
        fixture["sources"]["material_contradiction"] = True

        self.assertEqual("blue", cap_of(fixture))


class ConfidenceCapTests(unittest.TestCase):
    def test_confidence_above_the_cap_is_rejected(self):
        fixture = load_fixture("insufficient-votes-3")
        fixture["report"]["confidence"] = {
            "level": "green",
            "icon": "🟢",
            "text": "資料充分",
        }
        with self.assertRaises(ReportContractError) as ctx:
            validate_market_report(fixture["report"], fixture["sources"])
        self.assertTrue(
            any("信心" in problem for problem in ctx.exception.problems),
            ctx.exception.problems,
        )

    def test_confidence_below_the_cap_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["confidence"] = {
            "level": "yellow",
            "icon": "🟡",
            "text": "Core 自行下調的信心說明。",
        }
        self.assertIsNotNone(validate_market_report(fixture["report"], fixture["sources"]))


class ReportContentIsNotFilteredTests(unittest.TestCase):
    """2026-08-02 使用者決策：報告內容不做審查，品管交給七席同儕辯論。

    驗證器只攔可客觀比對的事實（evidence ID、票數、方向、信心上限）；
    任何市場語言——描述或建議——都不再是驗證失敗理由。
    """

    CASES = (
        ("price_target", "目標價 120000 美元"),
        ("guaranteed", "此區間保證上漲，不會回檔"),
        ("leverage", "建議使用 5 倍槓桿放大部位"),
        ("position_size", "請將倉位配置至資產的 30%"),
        ("direct_order", "現在請買進並於下週賣出"),
        ("english_long_order", "Open a BTC long position now"),
        ("descriptive_pressure", "賣壓沉重、做空情緒升溫、全網槓桿率下降"),
    )

    def test_market_language_never_fails_validation(self):
        for label, phrase in self.CASES:
            with self.subTest(case=label):
                fixture = load_fixture("consensus-6-1")
                fixture["report"]["judgement"] = phrase
                self.assertIsNotNone(
                    validate_market_report(fixture["report"], fixture["sources"])
                )

    def test_nested_fields_are_not_content_filtered(self):
        fixture = load_fixture("consensus-6-1")
        fixture["report"]["limitations"].append("保證上漲")
        self.assertIsNotNone(
            validate_market_report(fixture["report"], fixture["sources"])
        )

    def test_shipped_fixtures_still_validate(self):
        for case in ("consensus-6-1", "no-consensus-3-3-1", "insufficient-data"):
            with self.subTest(case=case):
                fixture = load_fixture(case)
                validate_market_report(fixture["report"], fixture["sources"])


class WorkflowTimingTests(unittest.TestCase):
    def test_windows_are_ninety_sixty_thirty_and_fifteen_minutes(self):
        self.assertEqual(CORE_DRAFT_LIMIT_MS, 90_000)
        self.assertEqual(CORRECTION_WINDOW_MS, 60_000)
        self.assertEqual(RENDER_WINDOW_MS, 30_000)
        self.assertEqual(HARD_DEADLINE_MS, 15 * 60_000)

    def test_first_pass_report_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [60_000], clock),
        )
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.corrections_used, 0)
        self.assertEqual(outcome.report["judgement"], fixture["report"]["judgement"])

    def test_nonzero_monotonic_origin_uses_run_elapsed_time(self):
        fixture = load_fixture("consensus-6-1")
        run_start = 9_000_000
        clock = FixedClock(monotonic_start_ms=run_start + 600_000)
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [10_000], clock),
            run_start_monotonic_ms=run_start,
        )
        self.assertEqual(outcome.status, "accepted")
        self.assertFalse(outcome.late)

    def test_default_system_monotonic_origin_is_not_immediately_late(self):
        from hoya_market_agents.clock import SystemClock

        fixture = load_fixture("consensus-6-1")
        outcome = run_report_workflow(
            SystemClock(), fixture["sources"], lambda attempt, errors: fixture["report"]
        )
        self.assertEqual(outcome.status, "accepted")

    def test_late_core_draft_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [90_001], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertEqual(outcome.report["confidence"]["level"], "red")
        self.assertTrue(any("90" in error for error in outcome.errors))

    def test_one_correction_is_accepted(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, fixture["report"]], [10_000, 10_000], clock),
        )
        self.assertEqual(outcome.status, "corrected")
        self.assertEqual(outcome.corrections_used, 1)

    def test_render_may_use_the_full_thirty_second_window(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()

        def renderer(report):
            clock.advance_ms(30_000)
            return report["run_id"]

        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [10_000], clock),
            renderer=renderer,
        )
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.phase_elapsed_ms["render"], 30_000)

    def test_render_over_thirty_seconds_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()

        def renderer(report):
            clock.advance_ms(30_001)
            return report["run_id"]

        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [10_000], clock),
            renderer=renderer,
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(any("30" in error for error in outcome.errors))

    def test_core_or_renderer_exception_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        for failed_step in ("core", "renderer"):
            with self.subTest(step=failed_step):
                clock = FixedClock()

                def core(attempt, errors):
                    if failed_step == "core":
                        raise RuntimeError("provider failed")
                    return fixture["report"]

                def renderer(report):
                    if failed_step == "renderer":
                        raise RuntimeError("renderer failed")
                    return report

                outcome = run_report_workflow(clock, fixture["sources"], core, renderer)
                self.assertEqual(outcome.status, "red_audit")
                self.assertEqual(outcome.report["confidence"]["level"], "red")

    def test_second_failure_produces_a_red_audit_without_market_conclusion(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, broken], [10_000, 10_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertEqual(outcome.corrections_used, 1)
        self.assertIsNone(outcome.report["adopted_stance"])
        self.assertFalse(outcome.report["direction_bearing"])
        self.assertTrue(outcome.report["validation_errors"])
        self.assertEqual(outcome.report["confidence"]["level"], "red")

    def test_at_most_one_correction_is_requested(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        attempts = []
        clock = FixedClock()

        def author(attempt, errors):
            attempts.append(attempt)
            clock.advance_ms(1_000)
            return broken

        run_report_workflow(clock, fixture["sources"], author)
        self.assertEqual(attempts, [1, 2])

    def test_late_correction_becomes_a_red_audit(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, fixture["report"]], [80_000, 61_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(any("60" in error for error in outcome.errors))

    def test_work_at_or_after_fifteen_minutes_is_a_late_failure(self):
        fixture = load_fixture("consensus-6-1")
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([fixture["report"]], [15 * 60_000], clock),
        )
        self.assertEqual(outcome.status, "red_audit")
        self.assertTrue(outcome.late)
        self.assertTrue(any("T+15" in error for error in outcome.errors))

    def test_red_audit_report_is_itself_contract_valid(self):
        fixture = load_fixture("consensus-6-1")
        broken = load_fixture("consensus-6-1")["report"]
        broken["tally"] = {"bullish": 7, "bearish": 0, "neutral": 0}
        clock = FixedClock()
        outcome = run_report_workflow(
            clock,
            fixture["sources"],
            _scripted_core([broken, broken], [1_000, 1_000], clock),
        )
        self.assertIsNotNone(validate_market_report(outcome.report, fixture["sources"]))


class RedAuditSelfCheckTest(unittest.TestCase):
    """Ticket 11 E2：紅字稽核的自驗證必須留下可觀察的結果。

    原本那段 ``try: validate_market_report(...) except: pass`` 把成功結果丟掉、把
    ``ReportContractError`` 吞掉，所以它跑不跑、跑出什麼，外面完全看不出來——
    **整段刪掉也沒有任何測試會紅**。稽核報告連自己的契約都過不了，是操作者該知
    道的事，所以結果現在留在 ``outcome.audit_contract_problems`` 上。
    """

    def clock(self):
        return FixedClock()

    def exploding_core(self, clock):
        def author(attempt, errors):
            clock.advance_ms(1_000)
            raise RuntimeError("core down")

        return author

    def test_a_sound_red_audit_reports_no_contract_problems(self):
        fixture = load_fixture("consensus-6-1")
        clock = self.clock()

        outcome = run_report_workflow(
            clock, fixture["sources"], self.exploding_core(clock)
        )

        self.assertEqual("red_audit", outcome.status)
        self.assertEqual((), outcome.audit_contract_problems)

    def test_an_unsound_red_audit_names_its_own_contract_problems(self):
        """殺得掉「整段刪除」的那一條。

        自驗證被拿掉時這個欄位會停在預設的空 tuple，斷言就紅。只有真的跑過驗證
        並且把結果留下來，才拿得到這些原因。
        """
        fixture = load_fixture("consensus-6-1")
        sources = dict(fixture["sources"])
        broken_votes = dict(sources["votes"])
        broken_votes.pop("run_id")
        sources["votes"] = broken_votes
        clock = self.clock()

        outcome = run_report_workflow(clock, sources, self.exploding_core(clock))

        self.assertEqual("red_audit", outcome.status)
        self.assertTrue(
            outcome.audit_contract_problems,
            "自驗證的結果被丟掉了：稽核報告過不了契約卻沒有任何痕跡",
        )
        self.assertIn(
            "official votes.run_id 不得為空", outcome.audit_contract_problems
        )

    def test_the_self_check_uses_the_supplied_snapshot(self):
        """自驗證要用呼叫端那一份規則，不是自己再讀一次。"""
        from tests.test_debate_rules import count_authority_reads

        fixture = load_fixture("consensus-6-1")
        snapshot = debate_rules()
        clock = self.clock()

        reads = count_authority_reads(
            lambda: run_report_workflow(
                clock, fixture["sources"], self.exploding_core(clock), rules=snapshot
            )
        )

        self.assertEqual(0, reads)


class RedPathSnapshotTest(unittest.TestCase):
    """Ticket 11 E2／F2：十條紅字路徑各自要有快照防線，而且要真的走到自己那條。

    ``_red_outcome`` 有十個呼叫點，第 4 輪只有「Core 例外」那一條被計數測試蓋
    到。更糟的是 ``draft-past-t15`` 一次推進超過硬期限，先撞上 90 秒那條分支——名字
    叫 T+15、實際走的是別條，於是真正的 T+15 呼叫點漏傳 ``rules`` 也沒人發現。

    所以每一條除了斷言 ``red_audit``，還要斷言**那條分支特有的訊息**。走錯分支
    的測試會在這裡紅。
    """

    # 每一條紅字分支各自的招牌訊息。斷言「我的招牌在、別人的招牌都不在」，
    # 才分得出走到哪一條——只用 assertIn 的話，_red_outcome 追加的 T+15 訊息會
    # 讓 90 秒那條也「通過」T+15 的斷言，那正是 F2 抓到的假綠。
    BRANCH_MARKERS = (
        "Core 初稿失敗：",
        "Core 初稿超過 90 秒",
        "Core correction 失敗：",
        "Core correction 超過 60 秒",
        "renderer 失敗：",
        "renderer 超過 30 秒",
        "T+15 或之後不得宣稱成功",
    )

    def assertReachedBranch(self, outcome, signature, branch=None):
        errors = list(outcome.errors)
        self.assertTrue(
            any(error.startswith(signature) for error in errors),
            "沒有走到 {!r}；實際錯誤 {}".format(signature, errors),
        )
        if branch is not None:
            # 招牌互斥擋不住三個 T+15 呼叫點互相頂替——它們的訊息一字
            # 不差。identity 是唯一分得出「到達的是哪一個呼叫點」的東西。
            self.assertEqual(branch, outcome.red_branch)
        for marker in self.BRANCH_MARKERS:
            if signature.startswith(marker) or marker.startswith(signature):
                continue
            self.assertFalse(
                any(error.startswith(marker) for error in errors),
                "走到了別條分支 {!r}；實際錯誤 {}".format(marker, errors),
            )

    def invalid_report(self):
        """A draft that fails the contract, so the correction paths are reachable."""
        report = dict(load_fixture("consensus-6-1")["report"])
        report["judgement"] = ""
        return report

    def paths(self):
        """``{名稱: (起始時刻, author 工廠, renderer 工廠, 該分支特有的訊息)}``."""
        good = load_fixture("consensus-6-1")["report"]
        bad = self.invalid_report()

        def author(costs, drafts):
            def make(clock):
                def call(attempt, errors):
                    clock.advance_ms(costs[attempt - 1])
                    draft = drafts[attempt - 1]
                    if draft is None:
                        raise RuntimeError("core down")
                    return draft

                return call

            return make

        def renderer(cost, explode=False):
            def make(clock):
                def call(_report):
                    clock.advance_ms(cost)
                    if explode:
                        raise RuntimeError("renderer down")
                    return {"markdown": "", "html": "", "debate_html": ""}

                return call

            return make

        return {
            "draft-exception": (0, author([1_000], [None]), None, "Core 初稿失敗：RuntimeError"),
            "draft-over-limit": (
                0,
                author([CORE_DRAFT_LIMIT_MS + 1_000], [good]),
                None,
                "Core 初稿超過 90 秒",
            ),
            "t15-after-draft": (
                HARD_DEADLINE_MS - 1_000,
                author([2_000], [good]),
                None,
                "T+15 或之後不得宣稱成功",
            ),
            "correction-exception": (
                0,
                author([1_000, 1_000], [bad, None]),
                None,
                "Core correction 失敗：RuntimeError",
            ),
            "correction-over-limit": (
                0,
                author([1_000, CORRECTION_WINDOW_MS + 1_000], [bad, good]),
                None,
                "Core correction 超過 60 秒",
            ),
            "t15-after-correction": (
                HARD_DEADLINE_MS - 2_000,
                author([1_000, 1_500], [bad, good]),
                None,
                "T+15 或之後不得宣稱成功",
            ),
            "second-validation-failure": (
                0,
                author([1_000, 1_000], [bad, bad]),
                None,
                "judgement 不得為空",
            ),
            "renderer-exception": (
                0,
                author([1_000], [good]),
                renderer(0, explode=True),
                "renderer 失敗：RuntimeError",
            ),
            "renderer-over-limit": (
                0,
                author([1_000], [good]),
                renderer(RENDER_WINDOW_MS + 1_000),
                "renderer 超過 30 秒",
            ),
            "t15-after-render": (
                HARD_DEADLINE_MS - 1_500,
                author([500], [good]),
                renderer(1_000),
                "T+15 或之後不得宣稱成功",
            ),
        }

    def drive(self, start_ms, make_author, make_renderer, **options):
        clock = FixedClock()
        clock.advance_ms(start_ms)
        return run_report_workflow(
            clock,
            self.sources,
            make_author(clock),
            None if make_renderer is None else make_renderer(clock),
            run_start_monotonic_ms=0,
            **options
        )

    def setUp(self):
        self.sources = load_fixture("consensus-6-1")["sources"]

    def test_every_red_path_reaches_its_own_branch(self):
        """F2：名字叫哪條分支，就要真的走到那條分支。"""
        for name, (start, make_author, make_renderer, signature) in self.paths().items():
            with self.subTest(path=name):
                outcome = self.drive(start, make_author, make_renderer)

                self.assertEqual("red_audit", outcome.status)
                self.assertReachedBranch(outcome, signature, branch=name)

    def test_every_red_path_runs_on_the_supplied_snapshot(self):
        from tests.test_debate_rules import count_authority_reads

        snapshot = debate_rules()
        for name, (start, make_author, make_renderer, signature) in self.paths().items():
            with self.subTest(path=name):
                outcome = {}

                def run():
                    outcome["value"] = self.drive(
                        start, make_author, make_renderer, rules=snapshot
                    )

                reads = count_authority_reads(run)

                self.assertEqual("red_audit", outcome["value"].status)
                self.assertReachedBranch(outcome["value"], signature, branch=name)
                self.assertEqual(0, reads, "這條紅字路徑沒有把快照傳下去")

    def test_every_red_path_still_validates_when_a_snapshot_is_supplied(self):
        """FP 方向：傳快照不得把驗證關掉。

        ``reads == 0`` 只證明沒有再去讀全域，不證明驗證跑過。改用不健全的
        sources：每一條都必須指出稽核報告自己的契約問題。
        """
        broken = dict(self.sources)
        votes = dict(broken["votes"])
        votes.pop("run_id")
        broken["votes"] = votes
        snapshot = debate_rules()

        for name, (start, make_author, make_renderer, _signature) in self.paths().items():
            with self.subTest(path=name):
                clock = FixedClock()
                clock.advance_ms(start)
                outcome = run_report_workflow(
                    clock,
                    broken,
                    make_author(clock),
                    None if make_renderer is None else make_renderer(clock),
                    run_start_monotonic_ms=0,
                    rules=snapshot,
                )

                self.assertEqual("red_audit", outcome.status)
                self.assertIn(
                    "official votes.run_id 不得為空", outcome.audit_contract_problems
                )

    def test_the_paths_without_a_snapshot_still_read_the_authority(self):
        """FP 方向：不傳快照時走的是現讀，不是完全不驗。"""
        from tests.test_debate_rules import count_authority_reads

        for name, (start, make_author, make_renderer, _signature) in self.paths().items():
            with self.subTest(path=name):
                reads = count_authority_reads(
                    lambda: self.drive(start, make_author, make_renderer)
                )

                self.assertGreaterEqual(reads, 1)


if __name__ == "__main__":
    unittest.main()
