"""The debate audit page must transcribe the run, never author or invent it.

The renderer under test is read-only for these tests: every expectation here is
derived from the recorded snapshot, the Traditional-Chinese label tables and the
shared navigation contract it holds with ``report_renderer``. That navigation is
**two pages** — ``report.html`` and ``debate.html`` — and its authority is the
bundle's own required-artifact list rather than a count written down anywhere. It
was three until Ticket 10 retired the live room, whose ``live.html`` was never a
file in a run directory at all.
"""

import html
import re
import unittest

from hoya_market_agents import design_tokens
from hoya_market_agents.report_audit_renderer import (
    CONSENSUS_LABELS,
    MISSING,
    SOURCE_TIER_LABELS,
    STANCE_LABELS,
    DebateAuditError,
    evidence_anchor,
    render_debate_html,
)
from hoya_market_agents.report_fixtures import load_fixture
from hoya_market_agents.report_renderer import render_market_html
from hoya_market_agents.seats import (
    SEAT_AVATARS,
    SEAT_IDS,
    seat_identities,
    seat_profiles,
)


def chat_names(asset_class=None):
    """How this page must name each seat: the roster byline plus its Agent number.

    Derived from the seat authority rather than written down here, so the page and
    the authority cannot drift apart while both stay green.
    """
    return {
        seat_id: "{}（{}）".format(identity.display_name, identity.agent_number)
        for seat_id, identity in seat_identities(asset_class).items()
    }

# Anything that would make a browser fetch a resource when the file is opened.
_RESOURCE_LOADERS = re.compile(
    r"<script\b|<link\b|<iframe\b|<img\b|<object\b|<embed\b|<audio\b|<video\b"
    r"|\bsrc\s*=|\bsrcset\s*=|@import|url\s*\(|<base\b",
    re.IGNORECASE,
)

_HREF = re.compile(r'href="([^"]*)"')

# Every colour written out longhand, the page's one stylesheet, and the token
# block that is the only place a colour may be written at all.
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)")
_STYLE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_TOKEN_BLOCK = re.compile(r"^:root\{[^}]*\}")
# The structural elements a reader navigates by: the page's 章節, not its
# nesting.
_LANDMARKS = re.compile(r"<(main|header|footer|section|nav|article)([^>]*)>")

_RUN_ID = "audit-20260802-btc"


def stylesheets_of(page):
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


def _evidence(evidence_id, seat_id, **overrides):
    card = {
        "run_id": _RUN_ID,
        "evidence_id": evidence_id,
        "seat_id": seat_id,
        "category": "spot-technical",
        "statement": "示範證據陳述 {}。".format(evidence_id),
        "direction": "support",
        "source_url": "https://{}.example/{}".format(seat_id, evidence_id),
        "source_origin": "{}.example".format(seat_id),
        "source_tier": 1,
        "published_at_utc": "2026-07-30T00:00:00Z",
        "retrieved_at_utc": "2026-08-01T01:00:00Z",
        "excerpt": "原文摘錄 {}。".format(evidence_id),
        "credibility_note": "可信度說明 {}。".format(evidence_id),
    }
    card.update(overrides)
    return card


def _message(message_id, seat_id, kind, **overrides):
    message = {
        "run_id": _RUN_ID,
        "event": "seat_message",
        "message_id": message_id,
        "seat_id": seat_id,
        "attempt_id": "{}-a1".format(seat_id),
        "kind": kind,
        "round": 1,
        "elapsed_ms": 61000,
        "created_at_utc": "2026-08-01T02:01:00Z",
        "stance": "bullish",
        "public_reason": "公開理由 {}。".format(message_id),
        "evidence_ids": [],
        "content_sha256": "0" * 64,
    }
    message.update(overrides)
    return message


def _report(**overrides):
    report = {
        "run_id": _RUN_ID,
        "period": {"label": "過去 14 日"},
        "consensus_status": "consensus",
        "adopted_stance": "bullish",
        "tally": {"bullish": 5, "bearish": 1, "neutral": 1},
    }
    report.update(overrides)
    return report


def _snapshot():
    """A snapshot exercising all seven seats and all four message kinds."""
    evidence = [_evidence("ev-{:02d}".format(i + 1), seat) for i, seat in enumerate(SEAT_IDS)]
    evidence[6]["source_tier"] = 3
    evidence[6]["direction"] = "oppose"
    evidence[3]["source_tier"] = 2
    messages = [
        _message(
            "m-01",
            "spot-technical",
            "position",
            round=1,
            elapsed_ms=12000,
            evidence_ids=["ev-01"],
        ),
        _message(
            "m-02",
            "counter-evidence",
            "challenge",
            round=2,
            elapsed_ms=754000,
            stance="bearish",
            target_seat_id="spot-technical",
            content={"target_claim": "現貨技術席宣稱突破已確認。"},
            evidence_ids=["ev-07"],
        ),
        _message(
            "m-03",
            "spot-technical",
            "response",
            round=2,
            elapsed_ms=900500,
            responds_to=["m-02"],
            target_seat_id="counter-evidence",
            evidence_ids=["ev-01", "ev-02"],
        ),
        _message("m-04", "derivatives", "position", round=1, stance="neutral"),
        _message("m-05", "onchain", "position", round=1),
        _message("m-06", "official-events", "position", round=1),
        _message("m-07", "news", "position", round=1),
        _message("m-08", "social-macro", "position", round=1, stance="bearish"),
        _message("m-09", "counter-evidence", "final_vote", round=3, stance="bearish"),
    ]
    return _report(), {"evidence": evidence, "debate": list(messages)}, messages


class DebateLabelTest(unittest.TestCase):
    """Every enum an auditor sees must be a Traditional-Chinese phrase."""

    def setUp(self):
        self.report, self.sources, self.messages = _snapshot()
        self.html = render_debate_html(self.report, self.sources)

    def test_page_chrome_is_traditional_chinese(self):
        for label in (
            "完整辯論室",
            "AI agnets debating chamber 可稽核市場研究",
            "本場辯論摘要",
            "七席公開辯論聊天室",
            "完整證據卡",
            "本頁限制",
            "立場",
            "判斷理由",
            "挑戰理由",
            "是否變更立場",
            "引用證據",
            "證據陳述：",
            "原文摘錄：",
            "可信度說明：",
            "來源網站",
            "來源等級",
            "發布時間",
            "取得時間",
        ):
            self.assertIn(label, self.html, label)

    def test_all_seven_seats_show_consistent_chat_names_and_avatars(self):
        names = chat_names()
        self.assertEqual(set(SEAT_IDS), set(names))
        self.assertEqual(set(SEAT_IDS), set(SEAT_AVATARS))
        self.assertEqual(7, len(set(SEAT_AVATARS.values())))
        transcript = self._transcript()
        for seat_id in SEAT_IDS:
            self.assertIn(names[seat_id], transcript, seat_id)
            self.assertIn(SEAT_AVATARS[seat_id], transcript, seat_id)
            self.assertNotIn(seat_id, transcript, seat_id)

    def test_the_names_follow_the_runs_market_and_never_the_retired_ones(self):
        """This page shows a seat; the roster decides who that seat is (ADR 0006)."""
        report, sources, _ = _snapshot()
        report["asset_class"] = "tw_stock"
        stock_transcript = render_debate_html(report, sources)

        for seat_id, profile in seat_profiles("tw_stock").items():
            self.assertIn(profile.display_name, stock_transcript, seat_id)
        for retired in ("鏈上獵人", "槓桿雷達", "反證稽核員"):
            self.assertNotIn(retired, stock_transcript, retired)

    def test_stances_are_rendered_in_chinese_and_raw_codes_are_absent(self):
        self.assertEqual(
            {"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"}, STANCE_LABELS
        )
        for code, label in STANCE_LABELS.items():
            self.assertIn(label, self.html, code)
        body = self._transcript()
        for code in STANCE_LABELS:
            self.assertNotIn(">{}<".format(code), body)

    def test_message_reasons_use_plain_traditional_chinese_headings(self):
        transcript = self._transcript()
        self.assertIn("判斷理由", transcript)
        self.assertIn("挑戰理由", transcript)
        for code in ("position", "challenge", "response", "final_vote"):
            self.assertNotIn(">{}<".format(code), transcript)

    def test_source_tiers_are_rendered_in_chinese(self):
        self.assertEqual({1, 2, 3}, set(SOURCE_TIER_LABELS))
        for tier, label in SOURCE_TIER_LABELS.items():
            self.assertIn(label, self.html, tier)
            self.assertIn("第", label)

    def test_consensus_status_uses_the_chinese_table(self):
        self.assertIn(CONSENSUS_LABELS["consensus"], self.html)
        for status, label in CONSENSUS_LABELS.items():
            rendered = render_debate_html(
                _report(consensus_status=status), self.sources
            )
            self.assertIn(label, rendered, status)
            self.assertNotIn("<dd>{}</dd>".format(status), rendered)

    def test_summary_counts_come_from_the_snapshot_not_from_a_guess(self):
        self.assertIn("{} 則".format(len(self.messages)), self.html)
        self.assertIn("{} 張".format(len(self.sources["evidence"])), self.html)
        self.assertNotIn(_RUN_ID, self.html)
        self.assertIn("過去 14 日", self.html)

    def _transcript(self):
        start = self.html.index("七席公開辯論聊天室")
        return self.html[start : self.html.index("完整證據卡")]


class DebateTranscriptTest(unittest.TestCase):
    """Every recorded public message, complete and in the recorded order."""

    def setUp(self):
        self.report, self.sources, self.messages = _snapshot()
        self.html = render_debate_html(self.report, self.sources)

    def test_every_seat_message_appears_exactly_once(self):
        for message in self.messages:
            self.assertEqual(
                1,
                self.html.count(html.escape(message["public_reason"])),
                message["message_id"],
            )

    def test_messages_keep_the_recorded_input_order(self):
        positions = [
            self.html.index(html.escape(message["public_reason"]))
            for message in self.messages
        ]
        self.assertEqual(sorted(positions), positions)
        # A shuffled snapshot must render in its own recorded order, not sorted.
        reordered = list(reversed(self.messages))
        shuffled = render_debate_html(
            self.report, {"evidence": self.sources["evidence"], "debate": reordered}
        )
        shuffled_positions = [
            shuffled.index(html.escape(message["public_reason"])) for message in reordered
        ]
        self.assertEqual(sorted(shuffled_positions), shuffled_positions)

    def test_non_seat_message_events_are_excluded_but_messages_stay_complete(self):
        debate = [
            {"event": "round_start", "round": 1, "note": "非發言事件不得出現"},
            self.messages[0],
            {"event": "vote_tally", "note": "另一個非發言事件"},
            self.messages[1],
        ]
        rendered = render_debate_html(
            self.report, {"evidence": self.sources["evidence"], "debate": debate}
        )
        self.assertNotIn("非發言事件不得出現", rendered)
        self.assertNotIn("另一個非發言事件", rendered)
        self.assertIn("2 則", rendered)
        self.assertLess(
            rendered.index(html.escape(self.messages[0]["public_reason"])),
            rendered.index(html.escape(self.messages[1]["public_reason"])),
        )

    def test_round_and_elapsed_offsets_are_shown_per_message(self):
        self.assertIn("第 1 輪", self.html)
        self.assertIn("第 2 輪", self.html)
        self.assertIn("第 3 輪", self.html)
        self.assertIn("T+00:12", self.html)   # 12000 ms
        self.assertIn("T+12:34", self.html)   # 754000 ms
        self.assertIn("T+15:00", self.html)   # 900500 ms truncates, never rounds up

    def test_each_message_shows_who_when_stance_and_verbatim_reason(self):
        item = self._item(html.escape("公開理由 m-02。"))
        self.assertIn(chat_names()["counter-evidence"], item)
        self.assertIn(SEAT_AVATARS["counter-evidence"], item)
        self.assertIn(STANCE_LABELS["bearish"], item)
        self.assertIn("公開理由 m-02。", item)
        self.assertIn("T+12:34", item)

    def test_challenge_shows_its_reason_without_internal_reply_metadata(self):
        item = self._item(html.escape("公開理由 m-02。"))
        self.assertIn("挑戰理由", item)
        self.assertNotIn("現貨技術席宣稱突破已確認。", item)
        self.assertNotIn("m-02", self._item(html.escape("公開理由 m-03。")))

    def test_internal_message_metadata_is_not_rendered(self):
        message = _message(
            "MESSAGE-METADATA-SENTINEL",
            "news",
            "position",
            public_reason="這是可公開的完整理由。",
            attempt_id="ATTEMPT-METADATA-SENTINEL",
            content_sha256="HASH-METADATA-SENTINEL",
        )
        rendered = render_debate_html(
            self.report, {"evidence": [], "debate": [message]}
        )
        for hidden in (
            "發言識別碼",
            "嘗試識別碼",
            "內容雜湊",
            "MESSAGE-METADATA-SENTINEL",
            "ATTEMPT-METADATA-SENTINEL",
            "HASH-METADATA-SENTINEL",
        ):
            self.assertNotIn(hidden, rendered)

    def test_real_stance_change_shows_old_new_and_public_reason(self):
        messages = [
            _message("before", "news", "position", stance="bearish", public_reason="起初偏空。"),
            _message(
                "after",
                "news",
                "final_vote",
                stance="bullish",
                public_reason="重新檢視後偏多。",
                stance_change_reason="現貨量能證據推翻原判斷。",
            ),
        ]
        rendered = render_debate_html(
            self.report, {"evidence": [], "debate": messages}
        )
        changed = self._item_from(rendered, "重新檢視後偏多。")
        self.assertIn("是：偏空 → 偏多", changed)
        self.assertIn("現貨量能證據推翻原判斷。", changed)

    def test_first_recorded_stance_never_invents_a_change(self):
        message = _message(
            "only",
            "news",
            "final_vote",
            public_reason="第一則可見表態。",
            stance_change_reason="只有公開補充說明。",
        )
        rendered = render_debate_html(
            self.report, {"evidence": [], "debate": [message]}
        )
        item = self._item_from(rendered, "第一則可見表態。")
        self.assertIn("否（首次公開表態）", item)
        self.assertIn("只有公開補充說明。", item)
        self.assertNotIn("是：", item)

    def test_messages_link_every_cited_evidence_id(self):
        item = self._item(html.escape("公開理由 m-03。"))
        self.assertIn("ev-01", item)
        self.assertIn("ev-02", item)
        self.assertIn('href="#{}"'.format(evidence_anchor("ev-01")), item)
        self.assertIn('href="#{}"'.format(evidence_anchor("ev-02")), item)

    def test_message_without_citations_says_not_provided(self):
        item = self._item(html.escape("公開理由 m-04。"))
        self.assertIn("引用證據", item)
        self.assertIn(MISSING, item)

    def test_empty_debate_states_the_absence_without_inventing_messages(self):
        rendered = render_debate_html(
            self.report, {"evidence": self.sources["evidence"], "debate": []}
        )
        self.assertIn("本次執行沒有記錄任何公開發言", rendered)
        self.assertIn("0 則", rendered)
        self.assertNotIn("<ol", rendered)

    def _item(self, needle):
        return self._item_from(self.html, needle)

    @staticmethod
    def _item_from(rendered, needle):
        needle = html.escape(needle)
        end = rendered.index(needle)
        start = rendered.rindex('<li class="turn ', 0, end)
        return rendered[start : rendered.index("</li>", end)]


class EvidenceCardTest(unittest.TestCase):
    """Cards must carry the whole provenance chain, and anchors must be safe."""

    def setUp(self):
        self.report, self.sources, self.messages = _snapshot()
        self.html = render_debate_html(self.report, self.sources)

    def test_every_card_shows_the_full_provenance_chain(self):
        for card in self.sources["evidence"]:
            block = self._card(card["evidence_id"])
            self.assertIn(html.escape(card["statement"]), block)
            self.assertIn(html.escape(card["source_origin"]), block)
            self.assertIn(SOURCE_TIER_LABELS[card["source_tier"]], block)
            self.assertIn(card["published_at_utc"], block)
            self.assertIn(card["retrieved_at_utc"], block)
            self.assertIn(html.escape(card["excerpt"]), block)
            self.assertIn(html.escape(card["credibility_note"]), block)
            self.assertIn(html.escape(card["source_url"]), block)
            self.assertIn('href="{}"'.format(html.escape(card["source_url"])), block)

    def test_evidence_cards_are_native_and_collapsed_by_default(self):
        self.assertEqual(
            len(self.sources["evidence"]),
            self.html.count('<details class="evidence-card"'),
        )
        self.assertNotRegex(self.html, r'<details class="evidence-card"[^>]*\sopen(?:\s|=|>)')
        self.assertEqual(len(self.sources["evidence"]), self.html.count("<summary>"))

    def test_every_card_has_an_anchor_that_messages_can_jump_to(self):
        for card in self.sources["evidence"]:
            anchor = evidence_anchor(card["evidence_id"])
            self.assertIn('id="{}"'.format(anchor), self.html)
        for target in _HREF.findall(self.html):
            if target.startswith("#"):
                self.assertIn('id="{}"'.format(target[1:]), self.html, target)

    def test_anchor_ids_stay_url_safe_and_collision_free(self):
        weird = ['ev "><script>', "ev-#1", "ev_1", "證據-1", "ev%20a"]
        anchors = [evidence_anchor(value) for value in weird]
        self.assertEqual(len(anchors), len(set(anchors)))
        for anchor in anchors:
            self.assertRegex(anchor, r"^evidence-[A-Za-z0-9_-]+$")
        # The escape character is itself escaped, so distinct ids cannot collide.
        self.assertNotEqual(evidence_anchor("a_5f_b"), evidence_anchor("a_b"))

    def test_hostile_evidence_id_still_produces_a_matching_safe_anchor(self):
        evidence_id = 'ev "><script>alert(1)</script>'
        sources = {
            "evidence": [_evidence(evidence_id, "onchain")],
            "debate": [_message("m-x", "onchain", "position", evidence_ids=[evidence_id])],
        }
        rendered = render_debate_html(self.report, sources)
        anchor = evidence_anchor(evidence_id)
        self.assertIn('id="{}"'.format(anchor), rendered)
        self.assertIn('href="#{}"'.format(anchor), rendered)
        self.assertNotIn("<script>", rendered)

    def test_unknown_cited_evidence_id_is_flagged_not_linked(self):
        sources = {
            "evidence": self.sources["evidence"],
            "debate": [_message("m-x", "news", "position", evidence_ids=["ev-99"])],
        }
        rendered = render_debate_html(self.report, sources)
        self.assertIn("ev-99", rendered)
        self.assertIn("未收錄於證據快照", rendered)
        self.assertNotIn('href="#{}"'.format(evidence_anchor("ev-99")), rendered)

    def test_empty_evidence_snapshot_states_the_absence(self):
        rendered = render_debate_html(
            self.report, {"evidence": [], "debate": self.sources["debate"]}
        )
        self.assertIn("本次執行沒有記錄任何證據卡", rendered)
        self.assertIn("0 張", rendered)

    def _card(self, evidence_id):
        start = self.html.index('id="{}"'.format(evidence_anchor(evidence_id)))
        return self.html[start : self.html.index("</details>", start)]


class SharedNavigationTest(unittest.TestCase):
    """Every static page links to the pages the bundle actually carries.

    導覽曾經列三頁，第一頁 ``live.html`` 卻**從來不是 run 目錄裡的檔案**：舊的
    直播頁走伺服器 URL，Ticket 10 退役 live_dashboard 之後連那條脈絡都沒有了。
    封存後的 bundle 也不缺那一頁——「即時辯論」的內容就是辯論逐字稿，本頁已經
    逐字轉錄了。
    """

    def setUp(self):
        self.fixture = load_fixture("consensus-6-1")
        self.debate_html = render_debate_html(
            self.fixture["report"], self.fixture["sources"]
        )
        self.market_html = render_market_html(
            self.fixture["report"], self.fixture["sources"]
        )

    def test_both_pages_expose_every_page_the_bundle_carries(self):
        for rendered in (self.market_html, self.debate_html):
            for target in ("report.html", "debate.html"):
                self.assertIn('href="{}"'.format(target), rendered)

    def test_neither_page_advertises_a_room_the_bundle_cannot_open(self):
        for rendered in (self.market_html, self.debate_html):
            self.assertNotIn('href="live.html"', rendered)
            self.assertNotIn("即時辯論", rendered)

    def test_each_static_page_marks_its_current_tab(self):
        self.assertIn('href="report.html" aria-current="page"', self.market_html)
        self.assertIn('href="debate.html" aria-current="page"', self.debate_html)

    def test_navigation_is_bidirectional_and_relative(self):
        for target in _HREF.findall(self.debate_html):
            self.assertFalse(target.startswith("/"), target)

    def test_fixture_run_renders_without_inventing_a_judgement(self):
        # The debate page transcribes; it never restates a market verdict.
        for message in self.fixture["sources"]["debate"]:
            self.assertIn(html.escape(message["public_reason"]), self.debate_html)
        self.assertNotIn(self.fixture["report"]["judgement"], self.debate_html)
        self.assertNotIn(self.fixture["report"]["market_status"], self.debate_html)


class EscapingAndUrlSafetyTest(unittest.TestCase):
    """Untrusted run text is data, never markup, and never an active link."""

    PAYLOAD = '<script>alert("xss")</script>'

    def setUp(self):
        self.report = _report()

    def _render_with_payload(self):
        card = _evidence(
            "ev-01",
            "news",
            statement=self.PAYLOAD,
            excerpt=self.PAYLOAD,
            credibility_note=self.PAYLOAD,
            source_origin=self.PAYLOAD,
            category=self.PAYLOAD,
        )
        message = _message(
            "m-01",
            "news",
            "challenge",
            public_reason=self.PAYLOAD,
            stance_change_reason=self.PAYLOAD,
            target_seat_id=self.PAYLOAD,
            content={"target_claim": self.PAYLOAD},
            responds_to=[self.PAYLOAD],
            evidence_ids=["ev-01"],
        )
        return render_debate_html(
            self.report, {"evidence": [card], "debate": [message]}
        )

    def test_hostile_text_is_escaped_everywhere_it_is_shown(self):
        rendered = self._render_with_payload()
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("</script>", rendered)
        self.assertNotIn('alert("xss")', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&quot;xss&quot;", rendered)

    def test_hostile_text_cannot_break_out_of_an_attribute(self):
        url = 'https://a.example/" onload="x'
        card = _evidence("ev-01", "news", source_url=url)
        rendered = render_debate_html(
            self.report, {"evidence": [card], "debate": []}
        )
        # The quote is neutralised, so the whole payload stays one attribute
        # value and the injected handler never becomes an attribute of its own.
        self.assertNotIn(url, rendered)
        self.assertIn("&quot;", rendered)
        self.assertEqual(
            ["report.html", "debate.html", "report.html", html.escape(url)],
            _HREF.findall(rendered),
        )
        self.assertEqual(url, html.unescape(_HREF.findall(rendered)[-1]))

    def test_unsafe_source_urls_never_become_hrefs(self):
        unsafe = (
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "/relative/path",
            "//evil.example/x",
            "  ",
        )
        for url in unsafe:
            card = _evidence("ev-01", "news", source_url=url)
            rendered = render_debate_html(
                self.report, {"evidence": [card], "debate": []}
            )
            for target in _HREF.findall(rendered):
                self.assertNotIn(html.unescape(target), (url, url.strip()) if url.strip() else ("",))
                self.assertNotIn(":", target.split("#")[0].replace(".html", ""), target)
            self.assertNotIn('href="{}"'.format(html.escape(url)), rendered)
            if url.strip():
                self.assertIn("未建立連結", rendered)
                self.assertIn(html.escape(url), rendered)

    def test_http_and_https_source_urls_stay_clickable(self):
        for url in ("https://a.example/x?q=1&r=2", "http://b.example/y"):
            card = _evidence("ev-01", "news", source_url=url)
            rendered = render_debate_html(
                self.report, {"evidence": [card], "debate": []}
            )
            self.assertIn('href="{}"'.format(html.escape(url)), rendered)
            self.assertNotIn("未建立連結", rendered)

    def test_missing_source_url_is_reported_not_linked(self):
        for value in (None, "", 12345, ["https://a.example"]):
            card = _evidence("ev-01", "news")
            card["source_url"] = value
            rendered = render_debate_html(
                self.report, {"evidence": [card], "debate": []}
            )
            self.assertIn("來源網址", rendered)
            self.assertNotIn('href="http', rendered)

    def test_every_href_in_the_page_is_a_relative_page_or_a_safe_absolute_link(self):
        _, sources, _ = _snapshot()
        sources["evidence"][0]["source_url"] = "javascript:alert(1)"
        rendered = render_debate_html(self.report, sources)
        for target in _HREF.findall(rendered):
            self.assertTrue(
                target in ("report.html", "debate.html")
                or target.startswith("#")
                or target.startswith("https://")
                or target.startswith("http://"),
                target,
            )


class SelfContainedDocumentTest(unittest.TestCase):
    """The page must open from disk, offline, with no runtime dependency."""

    def setUp(self):
        self.report, self.sources, _ = _snapshot()
        self.html = render_debate_html(self.report, self.sources)

    def test_document_declares_traditional_chinese(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="zh-Hant">', self.html)
        self.assertIn('<meta charset="utf-8">', self.html)
        self.assertIn("</html>", self.html)

    def test_document_loads_no_external_resource(self):
        match = _RESOURCE_LOADERS.search(self.html)
        self.assertIsNone(match, match.group(0) if match else "")

    def test_styles_are_inline_and_carry_no_url_references(self):
        style = self.html[self.html.index("<style>") : self.html.index("</style>")]
        self.assertNotIn("url(", style)
        self.assertNotIn("@import", style)
        self.assertIn("<style>", self.html)

    def test_no_inline_event_handlers_or_javascript_urls(self):
        lowered = self.html.lower()
        for hostile in ("javascript:", "onclick=", "onload=", "onerror=", "<script"):
            self.assertNotIn(hostile, lowered, hostile)

    def test_document_is_a_single_string_with_no_placeholders_left(self):
        self.assertNotIn("{}", self.html)
        self.assertNotIn("None", self.html)


class MissingDataAndFailClosedTest(unittest.TestCase):
    """Absent data is stated as 未提供; unusable structure raises instead."""

    def setUp(self):
        self.report = _report()

    def test_message_with_no_recorded_fields_shows_not_provided(self):
        message = {"event": "seat_message", "kind": "position"}
        rendered = render_debate_html(self.report, {"evidence": [], "debate": [message]})
        item = rendered[rendered.index('<li class="turn ') : rendered.index("</li>")]
        for term in ("立場", "判斷理由", "是否變更立場"):
            self.assertIn(term, item)
        self.assertGreaterEqual(item.count(MISSING), 3)
        self.assertNotIn("T+", item)
        for hidden in ("發言識別碼", "嘗試識別碼", "內容雜湊"):
            self.assertNotIn(hidden, item)

    def test_evidence_card_with_no_recorded_fields_shows_not_provided(self):
        card = {"evidence_id": "ev-01"}
        rendered = render_debate_html(self.report, {"evidence": [card], "debate": []})
        block = rendered[
            rendered.index('<details class="evidence-card"') : rendered.index("</details>")
        ]
        self.assertGreaterEqual(block.count(MISSING), 8)
        self.assertIn("ev-01", block)
        self.assertNotIn('href="http', block)

    def test_report_summary_fields_degrade_to_not_provided(self):
        rendered = render_debate_html({}, {"evidence": [], "debate": []})
        self.assertIn(MISSING, rendered)
        self.assertIn("0 則", rendered)
        self.assertIn("0 張", rendered)

    def test_unknown_enum_codes_are_flagged_not_guessed(self):
        message = _message("m-01", "unknown-seat", "gossip", stance="sideways")
        card = _evidence("ev-01", "news", source_tier=9, direction="maybe")
        rendered = render_debate_html(
            self.report, {"evidence": [card], "debate": [message]}
        )
        self.assertIn("未提供（無法對應的研究席）", rendered)
        self.assertNotIn("unknown-seat", rendered)
        for noun, value in (
            ("發言類型", "gossip"),
            ("立場", "sideways"),
            ("來源等級", "9"),
            ("證據方向", "maybe"),
        ):
            if noun == "發言類型":
                self.assertNotIn(value, rendered)
            else:
                self.assertIn("{}（未知{}代碼：{}）".format(MISSING, noun, value), rendered)

    def test_out_of_range_round_and_elapsed_are_not_provided(self):
        for field, value in (
            ("round", -1),
            ("round", "2"),
            ("round", True),
            ("round", 1.5),
            ("elapsed_ms", -5),
            ("elapsed_ms", "600"),
            ("elapsed_ms", None),
        ):
            message = _message("m-01", "news", "position")
            message[field] = value
            rendered = render_debate_html(
                self.report, {"evidence": [], "debate": [message]}
            )
            self.assertIn(MISSING, rendered, (field, value))
            if field == "elapsed_ms":
                self.assertNotIn("T+", rendered)
            else:
                self.assertNotIn("第 {} 輪".format(value), rendered)

    def test_missing_challenge_target_does_not_create_a_target(self):
        message = _message("m-01", "news", "challenge")
        rendered = render_debate_html(self.report, {"evidence": [], "debate": [message]})
        self.assertIn("挑戰理由", rendered)
        self.assertNotIn("挑戰對象", rendered)
        self.assertNotIn("回應對象", rendered)

    def test_structurally_unusable_snapshots_fail_closed(self):
        good = {"evidence": [], "debate": []}
        cases = (
            (None, good),
            ("報告", good),
            ([], good),
            (self.report, None),
            (self.report, []),
            (self.report, {"evidence": []}),
            (self.report, {"debate": []}),
            (self.report, {"evidence": [], "debate": "not-a-list"}),
            (self.report, {"evidence": "not-a-list", "debate": []}),
            (self.report, {"evidence": [], "debate": ["not-an-object"]}),
            (self.report, {"evidence": ["not-an-object"], "debate": []}),
            (self.report, {"evidence": [{}], "debate": []}),
            (self.report, {"evidence": [{"evidence_id": "  "}], "debate": []}),
            (self.report, {"evidence": [{"evidence_id": 7}], "debate": []}),
        )
        for report, sources in cases:
            with self.assertRaises(DebateAuditError):
                render_debate_html(report, sources)

    def test_duplicate_evidence_ids_fail_closed(self):
        sources = {
            "evidence": [_evidence("ev-01", "news"), _evidence("ev-01", "onchain")],
            "debate": [],
        }
        with self.assertRaises(DebateAuditError):
            render_debate_html(self.report, sources)

    def test_evidence_anchor_rejects_unusable_ids(self):
        for value in (None, "", 7, [], {}):
            with self.assertRaises(DebateAuditError):
                evidence_anchor(value)

    def test_fail_closed_errors_are_value_errors_with_chinese_text(self):
        with self.assertRaises(ValueError) as caught:
            render_debate_html(self.report, {"evidence": [], "debate": {}})
        self.assertIn("sources.debate", str(caught.exception))


class DebatePageStylesAreTheTokenTableTest(unittest.TestCase):
    """Spec R-004：這一頁的顏色也只有一個來源——``design_tokens``。

    與市場報告同一套判準，不斷言色碼字面值、不斷言類名順序：token 區塊宣告的
    是那張表當下持有的值、規則區一個顏色字面值都沒有、每個 ``var()`` 都解析得
    到、重新上色會傳到新產出的頁面，章節骨架不動。
    """

    def setUp(self):
        self.report, self.sources, self.messages = _snapshot()
        self.html = render_debate_html(self.report, self.sources)
        sheets = stylesheets_of(self.html)
        self.assertEqual(1, len(sheets))
        self.sheet = sheets[0]
        self.rules = rules_of(self.sheet)

    def test_the_page_declares_its_tokens_once_and_inline(self):
        self.assertEqual(1, self.sheet.count(":root"))

    def test_the_token_block_declares_the_table_the_module_holds(self):
        block = _TOKEN_BLOCK.search(self.sheet)
        self.assertIsNotNone(block)
        for token, value in list(design_tokens.PALETTE.items()) + list(
            design_tokens.SCALE.items()
        ):
            with self.subTest(token=token):
                self.assertIn(
                    "--{}:{};".format(token.replace("_", "-"), value), block.group(0)
                )

    def test_no_rule_names_a_colour_of_its_own(self):
        """驗收條件：renderer 原始碼不再有寫死的 palette 色值。"""
        self.assertEqual(set(), colour_literals(self.rules))

    def test_every_var_reference_resolves_to_a_declared_token(self):
        declared = {
            "--{}".format(token.replace("_", "-"))
            for token in list(design_tokens.PALETTE) + list(design_tokens.SCALE)
        }
        # 表以外，這份樣式表自己宣告的元件變數（例如每則發言的色條）也算數。
        declared |= set(re.findall(r"(--[\w-]+):", self.sheet))

        self.assertEqual(
            set(), set(re.findall(r"var\((--[\w-]+)", self.sheet)) - declared
        )

    def test_the_page_carries_no_dark_mode_at_all(self):
        self.assertNotIn("@media (prefers-color-scheme: dark)", self.html)
        self.assertNotIn("prefers-color-scheme", self.html)

    def test_a_repainted_token_reaches_a_newly_rendered_page(self):
        original = design_tokens.PALETTE["accent"]
        design_tokens.PALETTE["accent"] = "#123456"
        try:
            repainted = render_debate_html(self.report, self.sources)
        finally:
            design_tokens.PALETTE["accent"] = original

        self.assertIn("--accent:#123456;", repainted)
        self.assertNotIn("--accent:#123456;", render_debate_html(self.report, self.sources))

    def test_every_seat_gets_one_of_the_four_decorative_hues(self):
        """七席、四個裝飾色：色條照表輪替，而不是七個各自寫死的色碼。

        兩席共用一個色調不會弄丟任何資訊——說話的人本來就是以名字署名的，色條
        從頭到尾都不是唯一訊號。
        """
        painted = re.findall(r"\.tone-(\d+)\{--tone:var\((--[\w-]+)\);\}", self.rules)
        hues = {
            "--{}".format(token.replace("_", "-"))
            for token in design_tokens.DECOR_TOKENS
        }

        self.assertEqual(
            [str(number) for number in range(1, len(SEAT_IDS) + 1)],
            [number for number, _token in painted],
        )
        self.assertEqual(hues, {token for _number, token in painted})

    def test_a_decorative_hue_never_becomes_a_word(self):
        """裝飾色是色條與填色，永遠不是字。

        ``DECOR_INK`` 只保證「印在該色塊上的字」可讀；把品牌黃直接當成白底上的
        文字顏色是另一回事，那是 1.7:1，AA 不會過。所以整份樣式表沒有一條
        ``color:var(--google-*)``。
        """
        self.assertIsNone(re.search(r"(?<![\w-])color:var\(--google-", self.sheet))

    def test_glass_is_the_browsers_own_and_fetches_nothing(self):
        self.assertIn("var(--glass-surface)", self.rules)
        self.assertIn("blur(var(--glass-blur))", self.rules)
        self.assertNotIn("url(", self.sheet)
        self.assertNotIn("@import", self.sheet)

    def test_the_page_is_set_in_the_stack_the_table_names(self):
        self.assertIn("font-family:var(--font-sans)", self.rules)
        declared = re.search(r"--font-sans:([^;]*);", self.sheet).group(1)

        self.assertEqual(design_tokens.SCALE["font_sans"], declared)
        self.assertTrue(declared.startswith('"Microsoft JhengHei"'), declared)

    def test_the_section_structure_is_unchanged(self):
        """驗收條件：離線頁 DOM 章節結構與改版前一致（換裝只動樣式）。"""
        expected = [
            "main",
            "header.page-header",
            "nav.page-tabs",
            "section.run-summary",
            "section.transcript",
        ]
        expected += ["article.bubble"] * len(self.messages)
        expected += ["section#evidence-library", "footer.page-footer"]

        self.assertEqual(expected, landmarks(self.html))


if __name__ == "__main__":
    unittest.main()
