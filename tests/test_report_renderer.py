"""Markdown and HTML must come from one report contract, and HTML must open offline."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FixedClock, ScriptedTokenSource
from hoya_market_agents import design_tokens
from hoya_market_agents.debate_driver import assemble_market_report
from hoya_market_agents.debate_state_machine import stances_for
from hoya_market_agents.fake_provider import FakeProvider
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.report_audit_renderer import render_debate_html
from hoya_market_agents.report_contract import CONFIDENCE_ICONS, CONFIDENCE_LEVELS
from hoya_market_agents.report_fixtures import (
    FIXTURE_CASES,
    build_fixture,
    load_fixture,
)
from hoya_market_agents.report_workflow import run_report_workflow
from hoya_market_agents.report_renderer import (
    REPORT_SCHEMA_VERSION,
    build_report,
    render_html,
    render_market_html,
    render_market_markdown,
    render_markdown,
    resolve_stance_labels,
    stance_labels_for,
)
from hoya_market_agents.run_controller import RunController
from hoya_market_agents.run_store import RunStore
from hoya_market_agents.seats import SEAT_IDS, seat_profiles

QUESTION_BY_TYPE = {
    "single_asset_market_state": "分析 BTC 過去 14 日市場狀態",
    "two_asset_comparison": "比較 BTC 與 ETH 過去 14 日的相對強弱",
    "event_impact": "分析監管事件對 BTC 的影響",
    "open_proposition": "分析 BTC 是否值得長期持有",
}

QUESTION = "分析 BTC 過去 14 日市場狀態"

# Core 撰稿的散文部分，形狀與 ``validate_core_narrative`` 接受的相同；內容是固定
# 測試字串，只用來把契約組起來。
CORE_NARRATIVE = {
    "market_status": "短期偏多，但存在過熱風險",
    "period_label": "過去 14 日",
    "judgement": "目前較適合描述為偏多但不穩定。",
    "confidence_level": "green",
    "confidence_text": "六票採納，引用來源涵蓋多個獨立網域。",
    "limitations": ["本資料為固定測試資料，不代表真實市場。"],
    "invalidation_conditions": ["若價格跌回示範區間，原判斷失效。"],
}

# Anything that would make the browser fetch a resource at open time.
_RESOURCE_LOADERS = re.compile(
    r"<script\b|<link\b|<iframe\b|<img\b|\bsrc\s*=|@import|url\s*\(", re.IGNORECASE
)

# Every colour written out longhand. Spelled here rather than imported so a
# renderer that starts naming colours cannot also decide what counts as one.
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)")
_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_TOKEN_BLOCK = re.compile(r"^:root\{[^}]*\}")
# The structural elements a reader navigates by: the page's 章節, not its
# nesting. Headings and inline wrappers are left out on purpose, because this
# is the guard on "章節結構維持不變" and not a snapshot of the whole document.
_LANDMARKS = re.compile(r"<(main|header|footer|section|nav|article)([^>]*)>")


def stylesheets_of(page):
    """Every ``<style>`` element's text, in document order."""
    return _STYLE.findall(page)


def rules_of(sheet):
    """The sheet with its token block removed: the rules and nothing else."""
    return _TOKEN_BLOCK.sub("", sheet)


def colour_literals(text):
    return set(_COLOUR.findall(text))


def landmarks(page):
    """The page's section skeleton as ``tag#id.class`` in document order."""
    found = []
    for tag, attributes in _LANDMARKS.findall(_STYLE.sub("", page)):
        identifier = re.search(r'id="([^"]*)"', attributes)
        classes = re.search(r'class="([^"]*)"', attributes)
        found.append(
            "{}{}{}".format(
                tag,
                "#" + identifier.group(1) if identifier else "",
                "." + classes.group(1).replace(" ", ".") if classes else "",
            )
        )
    return found


def _execute_tracer_run(data_root):
    """Execute one fake tracer run and return its sealed run directory."""
    controller = RunController(
        store=RunStore(data_root),
        provider=FakeProvider(),
        clock=FixedClock(auto_advance_ms=250),
        token_source=ScriptedTokenSource(["aaa111"]),
    )
    return controller.execute(QUESTION).run_dir


class ReportRendererTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = _execute_tracer_run(Path(self._tmp.name))
        self.report = json.loads((self.run_dir / "report.json").read_text(encoding="utf-8"))
        self.markdown = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.html = (self.run_dir / "report.html").read_text(encoding="utf-8")

    def test_run_writes_both_rendered_reports(self):
        self.assertTrue((self.run_dir / "report.md").is_file())
        self.assertTrue((self.run_dir / "report.html").is_file())

    def test_report_contract_carries_the_audited_facts(self):
        self.assertEqual(REPORT_SCHEMA_VERSION, self.report["schema_version"])
        self.assertEqual(QUESTION, self.report["question"])
        self.assertEqual(["BTC"], self.report["assets"])
        self.assertEqual(14, self.report["period_days"])
        self.assertEqual("fake", self.report["provider_mode"])
        self.assertEqual(7, len(self.report["seats"]))
        self.assertEqual(7, sum(self.report["tally"].values()))

    def test_both_renderings_state_the_same_tally(self):
        for stance, count in self.report["tally"].items():
            expected = "{}：{}".format(stance, count)
            self.assertIn(expected, self.markdown)
            self.assertIn(expected, self.html)

    def test_both_renderings_name_all_seven_seats(self):
        for seat_id in SEAT_IDS:
            self.assertIn(seat_id, self.markdown)
            self.assertIn(seat_id, self.html)

    def test_both_renderings_keep_the_minority_stance_visible(self):
        minority = min(self.report["tally"], key=lambda stance: self.report["tally"][stance])
        minority_seats = [s["seat_id"] for s in self.report["seats"] if s["stance"] == minority]

        self.assertTrue(minority_seats)
        for seat_id in minority_seats:
            self.assertIn(seat_id, self.markdown)
            self.assertIn(seat_id, self.html)

    def test_both_renderings_disclose_the_fake_provider_and_scope_limits(self):
        for limit in self.report["scope_limits"]:
            self.assertIn(limit, self.markdown)
            self.assertIn(limit, self.html)

    def test_renderers_are_pure_functions_of_the_contract(self):
        self.assertEqual(self.markdown, render_markdown(self.report))
        self.assertEqual(self.html, render_html(self.report))

    def test_changing_the_contract_changes_both_renderings(self):
        altered = json.loads(json.dumps(self.report))
        altered["question"] = "分析 ETH 過去 30 日市場狀態"

        self.assertIn("分析 ETH 過去 30 日市場狀態", render_markdown(altered))
        self.assertIn("分析 ETH 過去 30 日市場狀態", render_html(altered))

    def test_html_is_self_contained_and_loads_nothing_at_open_time(self):
        self.assertIsNone(
            _RESOURCE_LOADERS.search(self.html),
            "report.html 不得在開啟時載入任何外部資源",
        )
        self.assertIn("<style>", self.html)

    def test_html_only_external_references_are_clickable_source_links(self):
        hrefs = re.findall(r'href="([^"]+)"', self.html)
        external = [href for href in hrefs if "://" in href]

        self.assertTrue(external, "來源網址必須維持可點擊")
        for href in external:
            self.assertTrue(href.startswith("https://fake.invalid"), href)

    def test_html_links_to_the_raw_audit_files_relatively(self):
        hrefs = re.findall(r'href="([^"]+)"', self.html)

        self.assertIn("evidence.jsonl", hrefs)
        self.assertIn("debate.jsonl", hrefs)

    def test_html_states_confidence_as_text_not_only_colour(self):
        self.assertIn(self.report["confidence"]["label"], self.html)
        self.assertIn(self.report["confidence"]["icon"], self.html)

    def test_html_escapes_contract_content(self):
        altered = json.loads(json.dumps(self.report))
        altered["question"] = '<script>alert("x")</script>'

        html = render_html(altered)

        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_every_cited_evidence_id_exists_in_the_evidence_snapshot(self):
        known = {
            json.loads(line)["evidence_id"]
            for line in (self.run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        }
        for seat in self.report["seats"]:
            for evidence_id in seat["evidence_ids"]:
                self.assertIn(evidence_id, known)


class StanceVocabularyTest(unittest.TestCase):
    """Ticket T12a: report wording follows the drawn question type, not bullish/bearish."""

    def test_every_question_type_keeps_its_own_ballot_wording(self):
        expected = {
            "single_asset_market_state": ["偏多", "偏空", "方向不明"],
            "two_asset_comparison": ["BTC較優", "ETH較優", "無明顯差異"],
            "event_impact": ["利多", "利空", "不明或有條件"],
            "open_proposition": ["正方", "反方", "無法決定"],
        }
        for question_type, question in QUESTION_BY_TYPE.items():
            package = build_question_package(question)
            with self.subTest(question_type=question_type):
                self.assertEqual(question_type, package.question_type)
                labels = stance_labels_for(stances_for(question_type), package.assets)
                self.assertEqual(expected[question_type], list(labels.values()))
                # The renderer must agree with the package the seats were given.
                self.assertEqual(package.stance_labels, labels)

    def test_comparison_report_names_both_assets_in_the_tally_and_seats(self):
        report = _comparison_report()

        markdown = render_market_markdown(report)
        html = render_market_html(report)

        for text in (markdown, html):
            self.assertIn("BTC較優：6", text)
            self.assertIn("ETH較優：1", text)
            self.assertIn("無明顯差異：0", text)
            # 只有 Core 撰寫的散文可以保留原句；票數與各席立場不得再出現市場詞彙。
            self.assertNotIn("偏多：", text)
            self.assertNotIn("最終立場：偏多", text)
            self.assertNotIn("asset_a_stronger", text)
        self.assertIn("最終立場：BTC較優", markdown)
        self.assertIn("最終 BTC較優", html)

    def test_comparison_labels_degrade_without_asset_names(self):
        labels = stance_labels_for(stances_for("two_asset_comparison"), ())

        self.assertEqual(["前者較優", "後者較優", "無明顯差異"], list(labels.values()))

    def test_unknown_stance_renders_verbatim_instead_of_failing(self):
        report = _comparison_report()
        report["tally"] = {"asset_a_stronger": 6, "surprise_stance": 1}

        self.assertIn("surprise_stance：1", render_market_markdown(report))

    def test_resolved_labels_prefer_the_ballot_the_run_recorded(self):
        stances = stances_for("two_asset_comparison")
        provided = {
            "asset_a_stronger": "BTC較優",
            "asset_b_stronger": "ETH較優",
            "no_clear_difference": "無明顯差異",
        }

        self.assertEqual(provided, resolve_stance_labels(stances, (), provided))
        self.assertEqual(
            stance_labels_for(stances, ("BTC", "ETH")),
            resolve_stance_labels(stances, ("BTC", "ETH"), {"asset_a_stronger": "BTC較優"}),
        )
        self.assertEqual(
            stance_labels_for(stances, ("BTC", "ETH")),
            resolve_stance_labels(stances, ("BTC", "ETH"), None),
        )


def _comparison_report():
    """The consensus fixture re-cast onto the two-asset comparison ballot."""
    fixture = load_fixture("consensus-6-1")
    report = json.loads(json.dumps(fixture["report"]))
    mapping = {"bullish": "asset_a_stronger", "bearish": "asset_b_stronger"}
    report["assets"] = ["BTC", "ETH"]
    report["tally"] = {
        "asset_a_stronger": report["tally"]["bullish"],
        "asset_b_stronger": report["tally"]["bearish"],
        "no_clear_difference": report["tally"]["neutral"],
    }
    report["adopted_stance"] = mapping[report["adopted_stance"]]
    for seat in report["seats"]:
        for field in ("initial_stance", "final_stance"):
            seat[field] = mapping.get(seat[field], seat[field])
    return report


class ConfidenceLightRenderingTest(unittest.TestCase):
    """ADR 0003：藍燈是新的一級，report.html 必須看得到圖示、文字與樣式。"""

    @staticmethod
    def _report_with(level):
        fixture = load_fixture("consensus-6-1")
        report = fixture["report"]
        report["confidence"] = {
            "level": level,
            "icon": CONFIDENCE_ICONS[level],
            "text": "七席一致採納，來源涵蓋多個獨立網域。",
        }
        return report

    def test_no_light_is_painted_with_a_hex_of_its_own(self):
        """Spec R-004：燈色 hex 從 renderer 刪除，燈號的顏色回到它本來的擁有者。

        那個擁有者是 ``report_contract.CONFIDENCE_ICONS``——🔴🟠🟡🟢🔵 的顏色就
        在圖示裡，所以 renderer 再寫一條 ``.green{color:#12602e}`` 只是同一個顏色
        的第二份拷貝，而且是 ``design_tokens`` 這張表刻意不收的那五個值。刪掉之
        後每一級仍然說得出自己是什麼：圖示、文字與 ``aria-label`` 都在，顏色從來
        就不是唯一訊號。
        """
        for level in CONFIDENCE_LEVELS:
            report = self._report_with(level)
            html = render_market_html(report)
            with self.subTest(level=level):
                self.assertNotIn(".{}{{color:".format(level), html)
                self.assertIn(CONFIDENCE_ICONS[level], html)
                self.assertIn(report["confidence"]["text"], html)

    def test_the_blue_light_shows_its_icon_and_its_text(self):
        report = self._report_with("blue")

        html = render_market_html(report)

        self.assertIn('class="confidence blue"', html)
        self.assertIn("<strong>🔵</strong>", html)
        self.assertIn(report["confidence"]["text"], html)
        self.assertIn(
            'aria-label="信心 🔵 {}"'.format(report["confidence"]["text"]), html
        )

    def test_the_blue_light_also_reaches_the_markdown_rendering(self):
        report = self._report_with("blue")

        markdown = render_market_markdown(report)

        self.assertIn("- 信心：🔵 {}".format(report["confidence"]["text"]), markdown)

    def test_the_retired_yellow_green_light_is_gone_from_every_rendering(self):
        report = self._report_with("green")

        self.assertNotIn("yellow_green", render_market_html(report))
        self.assertNotIn("🟡🟢", render_market_html(report))
        self.assertNotIn("yellow_green", render_market_markdown(report))


class SharedNavigationTest(unittest.TestCase):
    """市場報告的共用導覽只列 bundle 真的帶著的頁面。

    ``live.html`` 從來不是 run 目錄裡的檔案——舊的直播頁走伺服器 URL，Ticket 10
    退役 live_dashboard 之後連那條脈絡都沒有了——所以那個 tab 是一個永遠 404 的
    連結。封存後的 bundle 也不需要它：辯論逐字稿就在 ``debate.html``。
    """

    def setUp(self):
        self.fixture = load_fixture("consensus-6-1")
        self.html = render_market_html(self.fixture["report"], self.fixture["sources"])

    def test_the_market_page_offers_no_live_room(self):
        self.assertNotIn('href="live.html"', self.html)
        self.assertNotIn("即時辯論", self.html)

    def test_a_relative_link_is_never_swapped_for_a_server_path(self):
        """離線 bundle 的契約是它自己就能看，``/live`` 不算修好。"""
        for target in re.findall(r'href="([^"]*)"', self.html):
            if target.startswith(("http://", "https://")):
                continue
            with self.subTest(target=target):
                self.assertFalse(target.startswith("/"), target)

    def test_the_navigation_still_reaches_both_bundle_pages(self):
        self.assertIn('href="report.html" aria-current="page"', self.html)
        self.assertIn('href="debate.html"', self.html)


class SeatNamesFollowTheRunsMarketTest(unittest.TestCase):
    """One ballot, three markets: the seat names are the ones that market uses.

    ADR 0006: the roster holds three profile sets and the run's asset class picks
    one, so a Taiwan-stock report can never print a crypto seat name. Expected
    names come from the roster port rather than being spelled here, and the
    retired vocabulary is asserted absent so a renderer that quietly kept its own
    table cannot pass.
    """

    RETIRED_AND_CRYPTO_NAMES = (
        "合約槓桿分析師",
        "鏈上資金追蹤師",
        "輿情與幣市社群觀察員",
        "項目體質分析師",
        "鏈上獵人",
        "槓桿雷達",
        "反證稽核員",
        "現貨技術席",
        "衍生品席",
        "鏈上資料席",
        "官方事件席",
        "新聞資訊席",
        "社群與總體經濟席",
        "反方證據席",
    )

    def rendered(self, asset_class):
        fixture = load_fixture("consensus-6-1", asset_class)
        return (
            render_market_html(fixture["report"], fixture["sources"]),
            render_market_markdown(fixture["report"]),
        )

    def test_a_taiwan_stock_report_shows_every_stock_seat_name(self):
        expected = seat_profiles("tw_stock")

        for rendering in self.rendered("tw_stock"):
            for seat_id in SEAT_IDS:
                self.assertIn(expected[seat_id].display_name, rendering, seat_id)

    def test_a_taiwan_stock_report_prints_no_crypto_or_retired_seat_name(self):
        for rendering in self.rendered("tw_stock"):
            for name in self.RETIRED_AND_CRYPTO_NAMES:
                self.assertNotIn(name, rendering, name)

    def test_a_us_stock_report_reads_the_same_set_as_a_taiwan_one(self):
        self.assertEqual(self.rendered("tw_stock"), self.rendered("us_stock"))

    def test_a_crypto_report_shows_the_crypto_seat_names(self):
        expected = seat_profiles("crypto")

        for rendering in self.rendered("crypto"):
            for seat_id in SEAT_IDS:
                self.assertIn(expected[seat_id].display_name, rendering, seat_id)
        self.assertNotIn("法人動向分析師", self.rendered("crypto")[0])

    def test_a_report_with_no_asset_class_reads_the_open_set(self):
        fixture = load_fixture("consensus-6-1")
        del fixture["report"]["asset_class"]
        expected = seat_profiles(None)

        html = render_market_html(fixture["report"], fixture["sources"])

        for seat_id in SEAT_IDS:
            self.assertIn(expected[seat_id].display_name, html, seat_id)

    def test_the_seventh_seat_is_shown_as_the_fundamentals_researcher(self):
        """第七席在每一套都研究基本面，名字則是那一套自己的（Spec R-005）。"""
        for asset_class, name in (
            ("tw_stock", "基本面分析師"),
            ("us_stock", "基本面分析師"),
            ("crypto", "項目體質分析師"),
            ("open", "基本面研究員"),
        ):
            with self.subTest(asset_class=asset_class):
                for rendering in self.rendered(asset_class):
                    self.assertIn(name, rendering)
                    self.assertNotIn("反證稽核員", rendering)

    def test_the_byline_names_the_provider_family_then_the_seat(self):
        html = self.rendered("tw_stock")[0]

        self.assertIn("Claude・法人動向分析師", html)
        self.assertIn("Gemini・基本面分析師", html)

    def test_the_default_fixture_says_which_market_it_is(self):
        """無參數的 fixture 是 BTC run（``fixture-20260801-btc``），預設必須是 crypto。

        兩套的顯示名稱曾經完全相同（改成 open 也照樣全綠），所以這一項直接釘住契約
        欄位本身，不靠渲染結果間接證明——名稱分家之後這條依然是最直接的那一條。
        """
        for case in FIXTURE_CASES:
            with self.subTest(case=case):
                self.assertEqual(
                    "crypto", load_fixture(case)["report"]["asset_class"], case
                )
        built = build_fixture(
            ("bullish",) * 6 + ("bearish",),
            "consensus",
            "bullish",
            ("green", "🟢", "六票採納，引用來源涵蓋多個獨立網域。"),
        )

        self.assertEqual("crypto", built["report"]["asset_class"])

    def test_a_fixture_can_state_any_market_it_is_asked_for(self):
        for asset_class in ("tw_stock", "us_stock", "crypto", "open"):
            with self.subTest(asset_class=asset_class):
                fixture = load_fixture("consensus-6-1", asset_class)
                self.assertEqual(asset_class, fixture["report"]["asset_class"])

    def test_a_real_run_carries_its_own_market_into_the_report_contract(self):
        """一個真的台股 run，離線報告要印股票套席名，不是靠 fixture 餵進來的。

        接縫是報告契約本身：``assemble_market_report`` 是正式路徑上唯一組出這份
        契約的地方，資產類別要來自該 run 的 question package，不得由 renderer 猜。
        測試走公開介面（package → 契約 → 兩種渲染），不看任何內部呼叫。
        """
        package = build_question_package("幫我分析 2330 未來七天會不會漲")
        self.assertEqual("tw_stock", package.asset_class)
        sources = load_fixture("consensus-6-1")["sources"]

        report = assemble_market_report(
            CORE_NARRATIVE,
            sources,
            generated_at_utc="2026-08-01T02:00:00Z",
            assets=package.assets,
            period_days=package.period_days,
            asset_class=package.asset_class,
        )

        self.assertEqual("tw_stock", report["asset_class"])
        expected = seat_profiles("tw_stock")
        for rendering in (
            render_market_html(report, sources),
            render_market_markdown(report),
        ):
            for seat_id in SEAT_IDS:
                self.assertIn(expected[seat_id].display_name, rendering, seat_id)
            for crypto_name in ("鏈上獵人", "槓桿雷達", "反證稽核員"):
                self.assertNotIn(crypto_name, rendering, crypto_name)

    def test_a_refused_core_report_still_says_which_market_it_was(self):
        """D-1：驗證失敗的紅字稽核骨架是報告契約的**第二個**生產端。

        Core 兩稿都沒過客觀驗證時，系統照樣發佈一份 ``validation_failed`` 報告並
        照常渲染（run 仍 FINALIZED、使用者點得到）。那份骨架不帶資產類別的話，台股
        run 的離線報告就落到 open 套、印出幣圈席名——與裁定三修掉的是同一類缺陷，
        只是換一個生產端。
        """
        sources = load_fixture("consensus-6-1", "tw_stock")["sources"]

        def refused_core(attempt, errors):
            draft = load_fixture("consensus-6-1", "tw_stock")["report"]
            draft["confidence"]["level"] = "chartreuse"  # 不在核准燈號內
            return draft

        outcome = run_report_workflow(
            FixedClock(), sources, refused_core, asset_class="tw_stock"
        )

        self.assertEqual("red_audit", outcome.status)
        self.assertEqual("validation_failed", outcome.report["consensus_status"])
        self.assertTrue(outcome.report["process_failure"])
        self.assertEqual("tw_stock", outcome.report["asset_class"])
        expected = seat_profiles("tw_stock")
        for rendering in (
            render_market_html(outcome.report, sources),
            render_debate_html(outcome.report, sources),
        ):
            for seat_id in SEAT_IDS:
                self.assertIn(expected[seat_id].display_name, rendering, seat_id)
            for crypto_name in ("鏈上獵人", "槓桿雷達", "反證稽核員"):
                self.assertNotIn(crypto_name, rendering, crypto_name)

    def test_a_refused_report_for_a_run_with_no_market_reads_the_open_set(self):
        sources = load_fixture("consensus-6-1")["sources"]

        def refused_core(attempt, errors):
            raise RuntimeError("Core 初稿失敗")

        outcome = run_report_workflow(FixedClock(), sources, refused_core)

        self.assertEqual("red_audit", outcome.status)
        self.assertEqual("open", outcome.report["asset_class"])

    def test_a_run_that_states_no_market_still_assembles_a_named_report(self):
        """沒有資產類別的 run 不得組出沒有名字的報告：套組退到 open。"""
        sources = load_fixture("consensus-6-1")["sources"]

        report = assemble_market_report(
            CORE_NARRATIVE,
            sources,
            generated_at_utc="2026-08-01T02:00:00Z",
            assets=("BTC",),
            period_days=14,
        )

        self.assertEqual("open", report["asset_class"])
        html = render_market_html(report, sources)
        for seat_id, profile in seat_profiles("open").items():
            self.assertIn(profile.display_name, html, seat_id)


class OfflineStylesAreTheTokenTableTest(unittest.TestCase):
    """Spec R-004：離線報告的顏色只有一個來源——``design_tokens``。

    這裡刻意**不**斷言色碼字面值，也不斷言 CSS 類名的順序（Spec 測試決策明列
    不得耦合的兩件事）。斷言的是來源與結構：token 區塊宣告的是那張表當下持有
    的值、規則區一個顏色字面值都沒有、每個 ``var()`` 都解析得到、重新上色會傳
    到新產出的頁面，以及章節骨架與改版前一致。

    兩份輸出都在測：Core 撰稿的 ``report.html``（:func:`render_market_html`）與
    模擬追蹤執行寫出的 ``report.html``（:func:`render_html`）。後者讀的是 run 目錄
    裡真的那個檔案，所以測的是出貨的位元組，不是另外呼叫一次渲染函式。
    """

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        run_dir = _execute_tracer_run(Path(tmp.name))
        cls.tracer_report = json.loads(
            (run_dir / "report.json").read_text(encoding="utf-8")
        )
        cls.fixture = load_fixture("consensus-6-1")
        cls.pages = {
            "市場判斷報告": render_market_html(
                cls.fixture["report"], cls.fixture["sources"]
            ),
            "追蹤執行報告": (run_dir / "report.html").read_text(encoding="utf-8"),
        }

    def sheets(self):
        for name, page in self.pages.items():
            sheet = stylesheets_of(page)
            self.assertEqual(1, len(sheet), name)
            yield name, sheet[0]

    def test_each_page_declares_its_tokens_once_and_inline(self):
        """一個 ``:root``。第二個就是第二套 palette 換個名字回來了。"""
        for name, sheet in self.sheets():
            with self.subTest(page=name):
                self.assertEqual(1, sheet.count(":root"))

    def test_the_token_block_declares_the_table_the_module_holds(self):
        """值取自 :mod:`design_tokens` 當下的表，不是測試裡抄的一份。"""
        table = list(design_tokens.PALETTE.items()) + list(design_tokens.SCALE.items())
        for name, sheet in self.sheets():
            block = _TOKEN_BLOCK.search(sheet)
            self.assertIsNotNone(block, name)
            for token, value in table:
                with self.subTest(page=name, token=token):
                    self.assertIn(
                        "--{}:{};".format(token.replace("_", "-"), value), block.group(0)
                    )

    def test_no_rule_names_a_colour_of_its_own(self):
        """驗收條件：兩個 renderer 不再有寫死的 palette 色值與燈色 hex。

        比「找不到某幾個舊色碼」強：token 區塊以外**一個**顏色字面值都不准有，
        所以連一份與 palette 相同的正確拷貝也過不了。
        """
        for name, sheet in self.sheets():
            with self.subTest(page=name):
                self.assertEqual(set(), colour_literals(rules_of(sheet)))

    def test_every_var_reference_resolves_to_a_declared_token(self):
        """改名一個 token 不得留下一條用空值上色的規則。"""
        for name, sheet in self.sheets():
            declared = {
                "--{}".format(token.replace("_", "-"))
                for token in list(design_tokens.PALETTE) + list(design_tokens.SCALE)
            }
            declared |= set(re.findall(r"(--[\w-]+):", sheet))
            with self.subTest(page=name):
                self.assertEqual(
                    set(), set(re.findall(r"var\((--[\w-]+)", sheet)) - declared
                )

    def test_no_offline_page_carries_a_dark_mode_at_all(self):
        """驗收條件：作業系統設深色時仍是同一套白底。"""
        for name, page in self.pages.items():
            with self.subTest(page=name):
                self.assertNotIn("@media (prefers-color-scheme: dark)", page)
                self.assertNotIn("prefers-color-scheme", page)

    def test_a_repainted_token_reaches_a_newly_rendered_page(self):
        """「新 run 起生效」的實際意思：色值在渲染當下才從那張表讀出來。"""
        original = design_tokens.PALETTE["accent"]
        design_tokens.PALETTE["accent"] = "#123456"
        try:
            repainted = (
                render_market_html(self.fixture["report"], self.fixture["sources"]),
                render_html(self.tracer_report),
            )
        finally:
            design_tokens.PALETTE["accent"] = original
        for page in repainted:
            self.assertIn("--accent:#123456;", page)
        self.assertNotIn(
            "--accent:#123456;",
            render_market_html(self.fixture["report"], self.fixture["sources"]),
        )
        self.assertNotIn("--accent:#123456;", render_html(self.tracer_report))

    def test_the_four_decorative_hues_decorate_every_offline_page(self):
        """R-004 的「紅藍綠黃彩色點綴」：四個裝飾 token 每一個都真的被讀到。"""
        for name, sheet in self.sheets():
            rules = rules_of(sheet)
            for token in design_tokens.DECOR_TOKENS:
                with self.subTest(page=name, token=token):
                    self.assertIn("var(--{})".format(token.replace("_", "-")), rules)

    def test_glass_is_the_browsers_own_and_fetches_nothing(self):
        """毛玻璃＝原生 ``backdrop-filter``：沒有圖片、沒有 ``url()``、離線可開。"""
        for name, sheet in self.sheets():
            with self.subTest(page=name):
                self.assertIn("var(--glass-surface)", rules_of(sheet))
                self.assertIn("blur(var(--glass-blur))", rules_of(sheet))
                self.assertNotIn("url(", sheet)
                self.assertNotIn("@import", sheet)

    def test_the_pages_are_set_in_the_stack_the_table_names(self):
        """微軟正黑體優先，而且是那張表的那一份堆疊，不是 renderer 自己的。"""
        for name, sheet in self.sheets():
            with self.subTest(page=name):
                self.assertIn("font-family:var(--font-sans)", rules_of(sheet))
                declared = re.search(r"--font-sans:([^;]*);", sheet).group(1)
                self.assertEqual(design_tokens.SCALE["font_sans"], declared)
                self.assertTrue(declared.startswith('"Microsoft JhengHei"'), declared)

    def test_the_market_pages_section_structure_is_unchanged(self):
        """驗收條件：離線頁 DOM 章節結構與改版前一致（換裝只動樣式）。"""
        expected = [
            "main",
            "header.page-header",
            "nav.page-tabs",
            "section.decision",
            "section.evidence-compare",
            "article.evidence-side.support",
            "article.evidence-side.oppose",
            "section.risk-section",
            "section",
        ]
        expected += ["article.seat-card", "header.seat-head"] * len(SEAT_IDS)
        expected += ["section#evidence-library"]

        self.assertEqual(expected, landmarks(self.pages["市場判斷報告"]))

    def test_the_tracer_pages_section_structure_is_unchanged(self):
        expected = ["section.headline"] + ["article"] * len(SEAT_IDS)

        self.assertEqual(expected, landmarks(self.pages["追蹤執行報告"]))


class ReportContractTest(unittest.TestCase):
    def test_build_report_rejects_a_vote_count_that_is_not_seven(self):
        with self.assertRaises(ValueError):
            build_report(
                run_id="20260314T015926Z-btc-aaa111",
                question=QUESTION,
                assets=["BTC"],
                period_days=14,
                period_stated=True,
                provider_mode="fake",
                started_at_utc="2026-03-14T01:59:26Z",
                generated_at_utc="2026-03-14T01:59:40Z",
                evidence=[],
                debate=[],
                votes=[],
                tally={"bullish": 0, "bearish": 0, "neutral": 0},
                roster=[],
                scope_limits=[],
            )


if __name__ == "__main__":
    unittest.main()
