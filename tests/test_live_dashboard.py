"""Ticket #13: read-only live debate state and asynchronous dashboard."""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from hoya_market_agents.cli import build_parser
from hoya_market_agents.live_dashboard import (
    AGENT_PROFILES,
    build_live_state,
    create_live_server,
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
                self._message("spot-technical", "position", "bullish", 300_000),
                self._message("counter-evidence", "position", "bearish", 305_000),
            ]
        )

        state = build_live_state(
            self.data_root,
            RUN_ID,
            now_utc=datetime(2026, 8, 1, 2, 6, tzinfo=timezone.utc),
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
                self._message("spot-technical", "position", "bullish", 300_000),
                self._message(
                    "spot-technical",
                    "final_vote",
                    "bearish",
                    430_000,
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
            [self._message("spot-technical", "position", "bullish", 300_000)],
            partial='{"event":"seat_message"',
        )
        state = build_live_state(self.data_root, RUN_ID)
        self.assertEqual(1, len(state["debate"]))

    def test_public_state_translates_target_and_evidence_metadata(self):
        message = self._message("spot-technical", "challenge", "bullish", 310_000)
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

        state = build_live_state(self.data_root, RUN_ID, elapsed_override_ms=310_000)

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
