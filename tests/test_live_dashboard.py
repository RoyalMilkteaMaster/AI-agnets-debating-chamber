"""Ticket #13: read-only live debate state and asynchronous dashboard."""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from hoya_market_agents.debate_state_machine import (
    DEBATE_START_MS,
    FORCE_STOP_MS,
    THRESHOLD_FIVE_FROM_MS,
)
from hoya_market_agents.research_scheduler import PRIMARY_ONLY_END_MS
from hoya_market_agents.cli import build_parser
from hoya_market_agents.live_dashboard import (
    AGENT_PROFILES,
    build_live_state,
    create_live_server,
    list_runs,
    render_live_html,
)


RUN_ID = "20260801T020000Z-btc-live01"


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.run_dir = self.data_root / "runs" / RUN_ID
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "question.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "question": "分析 BTC 過去 14 日市場狀態",
                    "assets": ["BTC"],
                    "created_at_utc": "2026-08-01T02:00:00Z",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_events(self, events, partial=""):
        text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)
        (self.run_dir / "events.jsonl").write_text(text + partial, encoding="utf-8")

    def test_state_reports_countdown_rule_tally_and_seven_seats(self):
        self._write_events(
            [
                self._message("spot-technical", "position", "bullish", DEBATE_START_MS),
                self._message("counter-evidence", "position", "bearish", DEBATE_START_MS + 5_000),
            ]
        )

        state = build_live_state(
            self.data_root,
            RUN_ID,
            now_utc=datetime(2026, 8, 1, 2, 7, tzinfo=timezone.utc),
        )

        self.assertEqual("執行中", state["status"])
        self.assertEqual("第一輪辯論", state["phase"]["label"])
        self.assertEqual(6, state["phase"]["required_votes"])
        self.assertEqual(60_000, state["phase"]["next_rule_in_ms"])
        self.assertEqual({"bullish": 1, "bearish": 1, "neutral": 0}, state["tally"])
        self.assertEqual(7, len(state["seats"]))
        self.assertEqual(2, len(state["debate"]))

    def test_vote_history_preserves_every_stance_change(self):
        self._write_events(
            [
                self._message("spot-technical", "position", "bullish", DEBATE_START_MS),
                self._message(
                    "spot-technical",
                    "final_vote",
                    "bearish",
                    370_000,
                    stance_change_reason="反方證據推翻原先判斷。",
                ),
            ]
        )

        state = build_live_state(
            self.data_root,
            RUN_ID,
            now_utc=datetime(2026, 8, 1, 2, 8, tzinfo=timezone.utc),
        )

        self.assertEqual(2, len(state["vote_history"]))
        change = state["vote_history"][-1]
        self.assertEqual("bullish", change["before"])
        self.assertEqual("bearish", change["after"])
        self.assertEqual("反方證據推翻原先判斷。", change["reason"])
        first_message, changed_message = state["debate"]
        self.assertFalse(first_message["stance_changed"])
        self.assertEqual("否（首次表態）", first_message["stance_change_label"])
        self.assertTrue(changed_message["stance_changed"])
        self.assertEqual("是：偏多 → 偏空", changed_message["stance_change_label"])

    def test_incomplete_last_jsonl_line_is_ignored(self):
        self._write_events(
            [self._message("spot-technical", "position", "bullish", DEBATE_START_MS)],
            partial='{"event":"seat_message"',
        )
        state = build_live_state(self.data_root, RUN_ID)
        self.assertEqual(1, len(state["debate"]))

    def test_public_state_translates_target_and_evidence_metadata(self):
        message = self._message("spot-technical", "challenge", "bullish", DEBATE_START_MS + 10_000)
        message["target_seat_id"] = "counter-evidence"
        self._write_events([message])
        (self.run_dir / "evidence.jsonl").write_text(
            json.dumps(
                {
                    "evidence_id": "spot-technical-01",
                    "source_tier": 1,
                    "published_at_utc": "2026-08-01T02:00:00Z",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        state = build_live_state(self.data_root, RUN_ID, elapsed_override_ms=DEBATE_START_MS + 10_000)

        self.assertEqual("反方證據席", state["debate"][0]["target_seat_label"])
        self.assertEqual("第一級：官方或原始資料", state["evidence"][0]["source_tier_label"])
        self.assertEqual("2026/08/01 10:00（台北時間）", state["evidence"][0]["published_at_label"])

    def test_unknown_run_returns_waiting_without_inventing_state(self):
        state = build_live_state(self.data_root, "future-run")
        self.assertEqual("等待執行", state["status"])
        self.assertIsNone(state["run_id"])
        self.assertEqual([], state["debate"])

    def test_completed_state_uses_report_focus_and_never_says_not_voted(self):
        self._write_events(
            [self._message("spot-technical", "final_vote", "bullish", 360_000)]
        )
        (self.run_dir / "manifest.json").write_text(
            json.dumps({"elapsed_ms": 360_000}) + "\n", encoding="utf-8"
        )
        (self.run_dir / "report.json").write_text(
            json.dumps(
                {
                    "assets": ["BTC"],
                    "consensus_status": "consensus",
                    "adopted_stance": "bullish",
                    "market_status": "短期偏多，但存在過熱風險",
                    "confidence": {"icon": "🟡🟢", "text": "中高信心"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.run_dir / "report.html").write_text("報告", encoding="utf-8")

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual("已完成", state["status"])
        self.assertEqual("已結算", state["phase"]["threshold_label"])
        self.assertEqual("BTC", state["asset_label"])
        self.assertEqual("已達共識：偏多", state["focus"]["headline"])
        self.assertEqual("查看市場報告", state["focus"]["action_label"])
        self.assertEqual("🟡🟢", state["focus"]["confidence_icon"])

    @staticmethod
    def _message(seat_id, kind, stance, elapsed_ms, stance_change_reason=None):
        return {
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "phase": "debate",
            "event": "seat_message",
            "created_at_utc": "2026-08-01T02:05:00Z",
            "elapsed_ms": elapsed_ms,
            "seat_id": seat_id,
            "kind": kind,
            "message_id": "{}-{}-{}".format(seat_id, kind, elapsed_ms),
            "round": 0 if kind == "position" else 2,
            "stance": stance,
            "public_reason": "本席依據公開證據提出可稽核判斷。",
            "evidence_ids": ["{}-01".format(seat_id)],
            "responds_to": [],
            "stance_change_reason": stance_change_reason,
        }


class StanceVocabularyTests(unittest.TestCase):
    """Ticket T12a: the vote table speaks the drawn question type's own words."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.run_dir = self.data_root / "runs" / RUN_ID
        self.run_dir.mkdir(parents=True)

    def _write_question(self, **fields):
        payload = {
            "run_id": RUN_ID,
            "question": "題目",
            "created_at_utc": "2026-08-01T02:00:00Z",
        }
        payload.update(fields)
        (self.run_dir / "question.json").write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def _write_events(self, events):
        (self.run_dir / "events.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
            encoding="utf-8",
        )

    def test_each_question_type_drives_the_tally_keys_and_labels(self):
        cases = {
            "single_asset_market_state": (
                ["BTC"],
                ["bullish", "bearish", "neutral"],
                ["偏多", "偏空", "方向不明"],
            ),
            "two_asset_comparison": (
                ["BTC", "ETH"],
                ["asset_a_stronger", "asset_b_stronger", "no_clear_difference"],
                ["BTC較優", "ETH較優", "無明顯差異"],
            ),
            "event_impact": (
                ["BTC"],
                ["positive", "negative", "unclear_or_conditional"],
                ["利多", "利空", "不明或有條件"],
            ),
            "open_proposition": (
                ["BTC"],
                ["affirmative", "negative_side", "undecided"],
                ["正方", "反方", "無法決定"],
            ),
        }
        for question_type, (assets, options, labels) in cases.items():
            with self.subTest(question_type=question_type):
                self._write_question(question_type=question_type, assets=assets)
                self._write_events(
                    [
                        LiveStateTests._message(
                            "spot-technical", "position", options[0], DEBATE_START_MS
                        ),
                        LiveStateTests._message(
                            "counter-evidence", "position", options[1], DEBATE_START_MS + 5_000
                        ),
                    ]
                )

                state = build_live_state(self.data_root, RUN_ID)

                self.assertEqual(options, state["stance_options"])
                self.assertEqual(labels, [state["stance_labels"][key] for key in options])
                self.assertEqual({options[0]: 1, options[1]: 1, options[2]: 0}, state["tally"])
                self.assertEqual(
                    "{} 1｜{} 1｜{} 0".format(*labels), state["focus"]["tally_text"]
                )
                self.assertEqual(labels[0], state["debate"][0]["stance_label"])
                self.assertEqual(
                    labels[1],
                    next(
                        seat["stance_label"]
                        for seat in state["seats"]
                        if seat["seat_id"] == "counter-evidence"
                    ),
                )

    def test_comparison_run_reports_change_history_and_consensus_in_its_own_words(self):
        self._write_question(
            question_type="two_asset_comparison", assets=["BTC", "ETH"]
        )
        self._write_events(
            [
                LiveStateTests._message(
                    "spot-technical", "position", "asset_a_stronger", DEBATE_START_MS
                ),
                LiveStateTests._message(
                    "spot-technical",
                    "final_vote",
                    "asset_b_stronger",
                    370_000,
                    stance_change_reason="ETH 的鏈上資料更強。",
                ),
            ]
        )
        (self.run_dir / "report.json").write_text(
            json.dumps(
                {
                    "assets": ["BTC", "ETH"],
                    "consensus_status": "consensus",
                    "adopted_stance": "asset_b_stronger",
                    "confidence": {"icon": "🟡", "text": "中等信心"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual("已達共識：ETH較優", state["focus"]["headline"])
        self.assertEqual("是：BTC較優 → ETH較優", state["debate"][-1]["stance_change_label"])
        self.assertEqual("ETH較優", state["vote_history"][-1]["after_label"])

    def test_question_without_a_type_falls_back_to_the_market_ballot(self):
        self._write_question(assets=["BTC"])
        self._write_events(
            [LiveStateTests._message("spot-technical", "position", "bullish", DEBATE_START_MS)]
        )

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual(["bullish", "bearish", "neutral"], state["stance_options"])
        self.assertEqual({"bullish": 1, "bearish": 0, "neutral": 0}, state["tally"])

    def test_recorded_stance_labels_win_over_derived_ones(self):
        self._write_question(
            question_type="two_asset_comparison",
            assets=["BTC", "ETH"],
            stance_labels={
                "asset_a_stronger": "BTC 較強勢",
                "asset_b_stronger": "ETH 較強勢",
                "no_clear_difference": "難分高下",
            },
        )
        self._write_events(
            [
                LiveStateTests._message(
                    "spot-technical", "position", "asset_a_stronger", DEBATE_START_MS
                )
            ]
        )

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual("BTC 較強勢", state["stance_labels"]["asset_a_stronger"])
        self.assertEqual("BTC 較強勢", state["debate"][0]["stance_label"])

    def test_unknown_question_type_never_breaks_the_dashboard(self):
        self._write_question(question_type="mystery", assets=["BTC"])
        self._write_events(
            [LiveStateTests._message("spot-technical", "position", "bullish", DEBATE_START_MS)]
        )

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual(["bullish", "bearish", "neutral"], state["stance_options"])
        self.assertEqual("stance-positive", state["stance_classes"]["bullish"])

    def test_dashboard_shell_renders_the_tally_from_state_not_fixed_words(self):
        html = render_live_html()

        self.assertIn("renderTally", html)
        self.assertIn("state.stance_options", html)
        self.assertIn("state.stance_labels", html)
        self.assertIn("stance-unknown", html)
        self.assertNotIn("byId('bullish')", html)
        self.assertNotIn('<div class="bullish">', html)


class ChatStreamTests(unittest.TestCase):
    """Ticket #T10: the debate panel is a chat room fed one message at a time."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.run_dir = self.data_root / "runs" / RUN_ID
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "question.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "question": "分析 BTC 過去 14 日市場狀態",
                    "assets": ["BTC"],
                    "created_at_utc": "2026-08-01T02:00:00Z",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _append_message(self, seat_id, kind, stance, elapsed_ms):
        line = json.dumps(
            LiveStateTests._message(seat_id, kind, stance, elapsed_ms), ensure_ascii=False
        )
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def _serve(self):
        server = create_live_server(self.data_root, run_id=RUN_ID, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:{}".format(server.server_address[1])

    @staticmethod
    def _next_payload(stream):
        while True:
            line = stream.readline().decode("utf-8")
            if not line:
                raise AssertionError("事件串流提前結束")
            if line.startswith("data: "):
                return json.loads(line[len("data: ") :])

    def test_event_stream_pushes_every_appended_message_without_waiting_for_the_wave(self):
        base = self._serve()

        with urlopen(base + "/api/events", timeout=5) as stream:
            first = self._next_payload(stream)
            self.assertEqual([], first["debate"])
            self._append_message("spot-technical", "position", "bullish", DEBATE_START_MS)
            second = self._next_payload(stream)
            self._append_message("counter-evidence", "position", "bearish", DEBATE_START_MS + 5_000)
            third = self._next_payload(stream)

        self.assertEqual(["spot-technical"], [item["seat_id"] for item in second["debate"]])
        self.assertEqual(
            ["spot-technical", "counter-evidence"],
            [item["seat_id"] for item in third["debate"]],
        )
        self.assertEqual([0, 1], [item["seq"] for item in third["debate"]])
        self.assertEqual({"bullish": 1, "bearish": 1, "neutral": 0}, third["tally"])

    def test_event_stream_declares_a_short_reconnect_delay(self):
        base = self._serve()

        with urlopen(base + "/api/events", timeout=5) as stream:
            self.assertTrue(stream.readline().decode("utf-8").startswith("data: "))
            self.assertEqual("retry: 2000\n", stream.readline().decode("utf-8"))

    def test_debate_entries_expose_sequence_seat_label_and_evidence_for_the_chat_room(self):
        self._append_message("spot-technical", "position", "bullish", DEBATE_START_MS)
        self._append_message("spot-technical", "final_vote", "bearish", 370_000)

        state = build_live_state(self.data_root, RUN_ID)

        self.assertEqual([0, 1], [item["seq"] for item in state["debate"]])
        first = state["debate"][0]
        self.assertEqual("現貨技術席", first["seat_label"])
        self.assertEqual("Agent 1", first["agent_number"])
        self.assertEqual(["spot-technical-01"], first["evidence_ids"])

    def test_replay_cutoff_keeps_the_chat_room_a_prefix_of_the_timeline(self):
        self._append_message("spot-technical", "position", "bullish", DEBATE_START_MS)
        self._append_message("counter-evidence", "position", "bearish", DEBATE_START_MS + 5_000)

        partial = build_live_state(self.data_root, RUN_ID, elapsed_override_ms=DEBATE_START_MS + 2_000)
        whole = build_live_state(self.data_root, RUN_ID, elapsed_override_ms=340_000)

        self.assertEqual([0], [item["seq"] for item in partial["debate"]])
        self.assertEqual([0, 1], [item["seq"] for item in whole["debate"]])
        self.assertEqual(partial["debate"][0]["message_id"], whole["debate"][0]["message_id"])

    def test_dashboard_shell_renders_the_debate_as_an_append_only_chat_room(self):
        html = render_live_html()

        self.assertIn('id="feed"', html)
        self.assertIn('role="log"', html)
        self.assertIn('aria-relevant="additions"', html)
        self.assertIn('id="feed-empty"', html)
        self.assertIn('id="feed-jump"', html)
        self.assertIn("syncChat", html)
        self.assertIn("feedPinnedToLatest", html)
        self.assertIn("引用證據", html)
        self.assertIn("chatBubble", html)
        self.assertIn("item.seq>chat.seq", html)
        self.assertNotIn("setInterval", html)
        self.assertNotIn("innerHTML", html)


class WatchExperienceTests(unittest.TestCase):
    """Ticket #T11: the live page must stay readable while 34 messages land."""

    def test_bubbles_show_one_sentence_until_the_viewer_asks_for_the_rest(self):
        html = render_live_html()

        self.assertIn("firstSentence", html)
        self.assertIn("顯示全文", html)
        self.assertIn("收合", html)
        self.assertIn("reason-toggle", html)
        self.assertIn("aria-expanded", html)
        self.assertNotIn("innerHTML", html)

    def test_cited_evidence_renders_as_clickable_source_chips(self):
        html = render_live_html()

        self.assertIn("evidenceChip", html)
        self.assertIn("evidence-chip", html)
        self.assertIn("引用證據", html)
        self.assertIn("source_origin", html)
        self.assertIn("'noopener noreferrer'", html)

    def test_evidence_lookup_fields_survive_into_the_public_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            run_dir = data_root / "runs" / RUN_ID
            run_dir.mkdir(parents=True)
            (run_dir / "evidence.jsonl").write_text(
                json.dumps(
                    {
                        "evidence_id": "spot-technical-01",
                        "statement": "以完整日收盤價計算，BTC 十四日跌幅約 2.93%。",
                        "source_origin": "CoinGecko",
                        "source_url": "https://example.invalid/btc",
                        "source_tier": 2,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            card = build_live_state(data_root, RUN_ID)["evidence"][0]

        self.assertEqual("CoinGecko", card["source_origin"])
        self.assertEqual("https://example.invalid/btc", card["source_url"])
        self.assertTrue(card["statement"])

    def test_vote_history_is_one_compact_line_per_change_with_a_show_all_toggle(self):
        html = render_live_html()

        self.assertIn("historyRow", html)
        self.assertIn("改票", html)
        self.assertIn("顯示全部", html)
        self.assertIn("history-row", html)
        self.assertIn("HISTORY_PREVIEW", html)

    def test_rules_panel_marks_the_current_milestone_and_dims_the_past(self):
        html = render_live_html()

        self.assertIn("rule current", html)
        self.assertIn("rule past", html)
        self.assertIn("門檻", html)
        self.assertIn("ruleClass", html)

    def test_rule_milestones_are_derived_from_the_production_constants(self):
        state = build_live_state(Path(tempfile.gettempdir()) / "missing-run-root", None)

        milestones = {rule["at_ms"]: rule["required_votes"] for rule in state["rules"]}

        self.assertEqual(6, milestones[DEBATE_START_MS])
        self.assertEqual(5, milestones[THRESHOLD_FIVE_FROM_MS])
        self.assertEqual(4, milestones[FORCE_STOP_MS])
        self.assertIn(PRIMARY_ONLY_END_MS, milestones)


class ComparisonRunTimelineTests(unittest.TestCase):
    """Ticket R7: 封存里程碑依該 run 的題型顯示 T+4:00 或 T+4:30。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)
        self.run_dir = self.data_root / "runs" / RUN_ID
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "question.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "question": "比較 BTC 與 ETH 過去 14 日的相對強弱",
                    "question_type": "two_asset_comparison",
                    "assets": ["BTC", "ETH"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.run_dir / "events.jsonl").write_text(
            json.dumps({"event": "noop", "elapsed_ms": 0}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def test_the_seal_milestone_moves_to_four_thirty(self):
        state = build_live_state(self.data_root, RUN_ID)

        milestones = {rule["at_ms"]: rule["required_votes"] for rule in state["rules"]}
        self.assertEqual(6, milestones[270_000])
        self.assertNotIn(DEBATE_START_MS, milestones)
        self.assertEqual(5, milestones[THRESHOLD_FIVE_FROM_MS])
        self.assertEqual(4, milestones[FORCE_STOP_MS])

    def test_the_room_is_still_researching_at_four_minutes(self):
        state = build_live_state(
            self.data_root, RUN_ID, elapsed_override_ms=DEBATE_START_MS + 1_000
        )

        self.assertEqual("research", state["phase"]["key"])
        self.assertEqual("封存證據並開始辯論", state["phase"]["next_rule_label"])
        self.assertEqual([], state["evidence"])


class RunSelectionTests(unittest.TestCase):
    """Ticket #T9: the dashboard must follow the newest run and replay old ones."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.data_root = Path(self._temporary.name)

    def _make_run(self, run_id, question="分析 BTC 過去 14 日市場狀態", sealed=False, report=False, events=0):
        run_dir = self.data_root / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "question.json").write_text(
            json.dumps(
                {"run_id": run_id, "question": question, "assets": ["BTC"]},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if events:
            (run_dir / "events.jsonl").write_text(
                "".join('{"event":"noop"}\n' for _ in range(events)), encoding="utf-8"
            )
        if sealed:
            (run_dir / "snapshots").mkdir()
            (run_dir / "snapshots" / "evidence.snapshot.json").write_text("{}", encoding="utf-8")
        if report:
            (run_dir / "report.html").write_text("<h1>報告</h1>", encoding="utf-8")
        return run_dir

    def _serve(self, run_id=None):
        server = create_live_server(self.data_root, run_id=run_id, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:{}".format(server.server_address[1])

    @staticmethod
    def _get_json(url):
        with urlopen(url, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_default_selection_is_the_newest_run_directory(self):
        self._make_run("20260801T020000Z-btc-old001")
        self._make_run("20260801T230838Z-btc-new001")
        self._make_run("20260801T100000Z-btc-mid001")

        state = build_live_state(self.data_root, None)

        self.assertEqual("20260801T230838Z-btc-new001", state["run_id"])

    def test_open_page_follows_a_newly_started_run_without_reload(self):
        self._make_run("20260801T020000Z-btc-old001")
        base = self._serve()

        self.assertEqual(
            "20260801T020000Z-btc-old001", self._get_json(base + "/api/state")["run_id"]
        )
        self._make_run("20260802T010000Z-btc-new002")
        self.assertEqual(
            "20260802T010000Z-btc-new002", self._get_json(base + "/api/state")["run_id"]
        )

    def test_query_parameter_overrides_server_pin_and_newest_run(self):
        self._make_run("20260801T020000Z-btc-old001", question="舊題目")
        self._make_run("20260801T230838Z-btc-new001", question="新題目")
        base = self._serve(run_id="20260801T230838Z-btc-new001")

        pinned = self._get_json(base + "/api/state")
        overridden = self._get_json(base + "/api/state?run=20260801T020000Z-btc-old001")

        self.assertEqual("20260801T230838Z-btc-new001", pinned["run_id"])
        self.assertEqual("20260801T020000Z-btc-old001", overridden["run_id"])
        self.assertEqual("舊題目", overridden["question"])

    def test_query_parameter_selects_the_historical_report_artifact(self):
        self._make_run("20260801T020000Z-btc-old001", report=True)
        self._make_run("20260801T230838Z-btc-new001")
        base = self._serve()

        with urlopen(base + "/report.html?run=20260801T020000Z-btc-old001", timeout=2) as response:
            self.assertIn("報告", response.read().decode("utf-8"))
        with self.assertRaises(HTTPError) as failure:
            urlopen(base + "/report.html", timeout=2)
        self.assertEqual(404, failure.exception.code)

    def test_path_escaping_run_parameter_is_rejected(self):
        self._make_run("20260801T230838Z-btc-new001")
        outside = self.data_root / "secret"
        outside.mkdir()
        base = self._serve()

        state = self._get_json(base + "/api/state?run=../secret")

        self.assertEqual("等待執行", state["status"])
        self.assertIsNone(state["run_id"])
        self.assertIsNone(build_live_state(self.data_root, "../secret")["run_id"])

    def test_runs_endpoint_lists_every_run_newest_first_with_cheap_metadata(self):
        self._make_run("20260801T020000Z-btc-old001", question="舊題目", report=True, events=2)
        self._make_run("20260801T230838Z-btc-new001", question="新題目", sealed=True, events=27)
        (self.data_root / "runs" / "broken").mkdir()
        base = self._serve()

        runs = self._get_json(base + "/api/runs")["runs"]

        self.assertEqual(
            ["broken", "20260801T230838Z-btc-new001", "20260801T020000Z-btc-old001"],
            [item["run_id"] for item in runs],
        )
        newest = runs[1]
        self.assertEqual("新題目", newest["question"])
        self.assertTrue(newest["sealed"])
        self.assertFalse(newest["has_report"])
        self.assertEqual(27, newest["event_count"])
        oldest = runs[2]
        self.assertTrue(oldest["has_report"])
        self.assertFalse(oldest["sealed"])
        self.assertEqual(2, oldest["event_count"])
        self.assertIsNone(runs[0]["question"])
        self.assertEqual(0, runs[0]["event_count"])

    def test_latest_pointer_is_never_mistaken_for_an_existing_run(self):
        runs_root = self.data_root / "runs"
        runs_root.mkdir()
        (runs_root / "latest.json").write_text(
            json.dumps({"run_id": "20260801T020000Z-btc-gone01"}) + "\n", encoding="utf-8"
        )

        state = build_live_state(self.data_root, None)

        self.assertIsNone(state["run_id"])
        self.assertEqual("等待執行", state["status"])

    def test_missing_runs_directory_waits_instead_of_failing(self):
        state = build_live_state(self.data_root, None)

        self.assertIsNone(state["run_id"])
        self.assertEqual("等待執行", state["status"])
        self.assertEqual([], list_runs(self.data_root))

    def test_dashboard_shell_offers_run_id_history_and_return_link(self):
        html = render_live_html()
        self.assertIn('id="run-id"', html)
        self.assertIn('id="run-picker"', html)
        self.assertIn("回到目前 run", html)
        self.assertIn("fetch('/api/runs'", html)
        self.assertIn("本 run 未進行辯論", html)
        self.assertIn("本 run 未進行投票", html)
        self.assertNotIn("setInterval", html)


class LiveHtmlTests(unittest.TestCase):
    def test_cli_exposes_loopback_only_live_command(self):
        args = build_parser().parse_args(
            ["live", "--data-root", "data", "--run-id", RUN_ID, "--port", "9876"]
        )
        self.assertEqual("live", args.command)
        self.assertEqual("127.0.0.1", args.host)
        self.assertEqual(9876, args.port)

    def test_dashboard_contains_async_updates_rules_and_agent_identities(self):
        html = render_live_html()
        self.assertIn('lang="zh-Hant"', html)
        self.assertIn("new EventSource('/api/events'", html)
        self.assertIn("requestAnimationFrame(updateClock)", html)
        self.assertNotIn("setInterval", html)
        self.assertNotIn('fetch("/api/state"', html)
        self.assertIn("剩餘時間", html)
        self.assertIn("目前共識門檻", html)
        self.assertEqual("Codex・圖表偵探", AGENT_PROFILES["spot-technical"][0])
        self.assertEqual("Claude・官方哨兵", AGENT_PROFILES["official-events"][0])
        self.assertEqual("Gemini・反證稽核員", AGENT_PROFILES["counter-evidence"][0])
        self.assertIn("票數變化", html)
        self.assertIn("判斷／挑戰理由", html)
        self.assertIn("是否變更立場", html)
        self.assertIn("speaker-avatar", html)
        self.assertIn("市場報告", html)
        self.assertIn("完整辯論", html)
        for target in ("live.html", "report.html", "debate.html"):
            self.assertIn('href="{}"'.format(target), html)
        self.assertIn('href="live.html" aria-current="page"', html)
        self.assertIn("renderChanged", html)
        self.assertIn("state.phase.threshold_label", html)
        self.assertIn("現在正在發生", html)
        self.assertIn("查看下一規則", html)
        self.assertLess(html.index("公開辯論直播"), html.index("規則與時間線"))
        self.assertNotIn("https://cdn", html.lower())

    def test_local_server_serves_dashboard_and_json_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = create_live_server(Path(temporary), run_id=None, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            with urlopen("http://127.0.0.1:{}/".format(port), timeout=2) as response:
                self.assertIn("即時 Agent 辯論室", response.read().decode("utf-8"))
            with urlopen("http://127.0.0.1:{}/api/state".format(port), timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual("等待執行", payload["status"])

    def test_event_stream_pushes_an_initial_state_without_client_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = create_live_server(Path(temporary), run_id=None, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            with urlopen(
                "http://127.0.0.1:{}/api/events?replay=1&speed=20".format(port),
                timeout=2,
            ) as response:
                self.assertEqual("text/event-stream; charset=utf-8", response.headers["Content-Type"])
                line = response.readline().decode("utf-8")

            self.assertTrue(line.startswith("data: "))
            payload = json.loads(line.removeprefix("data: "))
            self.assertEqual("等待執行", payload["status"])

    def test_local_server_exposes_only_the_selected_run_html_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            run_dir = data_root / "runs" / RUN_ID
            run_dir.mkdir(parents=True)
            (run_dir / "report.html").write_text("<h1>市場判斷報告</h1>", encoding="utf-8")
            (run_dir / "debate.html").write_text("<h1>完整辯論紀錄</h1>", encoding="utf-8")
            server = create_live_server(data_root, run_id=RUN_ID, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            with urlopen("http://127.0.0.1:{}/report.html".format(port), timeout=2) as response:
                self.assertIn("市場判斷報告", response.read().decode("utf-8"))
            with urlopen("http://127.0.0.1:{}/debate.html".format(port), timeout=2) as response:
                self.assertIn("完整辯論紀錄", response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
