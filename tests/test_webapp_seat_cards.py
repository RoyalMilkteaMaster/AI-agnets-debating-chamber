"""The live room's seat cards: this run's seat names, each with its 白話說明.

Spec R-005 asks the debate page for one sentence under every seat card saying
what that seat looks at, so seven names stop being seven guesses. The sentence is
the roster's (``blurb``), read through the same port the names come from, and it
follows the run's asset class exactly as the names do: a 台股 run reads the stock
set, a 幣 run the crypto set, and a run recorded before the field existed reads
the open set.

The assertions here are equalities over ``seat_id → sentence`` rather than "this
text appears somewhere on the page". A page that printed all three sets, or that
printed the crypto sentence under a stock run's card, contains the right words
too — only pairing each sentence with the card it is under can tell those apart.

**A name and its sentence travel together or not at all.** The room is redrawn
from the frames the stream sends, so a sentence the client keeps a copy of is a
sentence that stops belonging to the seat above it the moment the frame names a
different set: the waiting room hands over to a real run (Reviewer A F-A05-1) and
a page pinned to one run can be followed by a stream on the newest one (Reviewer
B F-B05-1). Both are asserted below on the frame itself, which is the one place
both the first render and every redraw read from.
"""

import json
import re
import tempfile
import unittest
from html import escape
from pathlib import Path

from hoya_market_agents.question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
)
from hoya_market_agents.run_store import run_dir_parts
from hoya_market_agents.seats import SEAT_IDS, seat_profiles
from hoya_market_agents.webapp import live, pages, server

SEAT_PANEL = re.compile(
    r'<div class="agents" id="live-seats">(.*?)</div>\s*</section>', re.DOTALL
)
SEAT_CARD = re.compile(
    r'<article class="agent [^"]*" data-seat-id="([^"]+)">(.*?)</article>', re.DOTALL
)
SEAT_BLURB = re.compile(r'<p class="agent-blurb">(.*?)</p>', re.DOTALL)

RUN_ID = "20260810T020000Z-seatcards01"
QUESTION = "這一檔未來七天會不會漲"


class SeatCardBlurbTest(unittest.TestCase):
    """Every card names its seat and says, in one line, what it looks at."""

    def room(self, asset_class):
        """The live room as the server renders it for a run of this class.

        The run is written the way the store itself names one
        (:func:`~hoya_market_agents.run_store.run_dir_parts`), so the page under
        test is the page a real run gets rather than the waiting room — which
        also has seven cards and would answer the open set to every question.
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        data_root = Path(directory.name)
        date_dir, name = run_dir_parts(RUN_ID, QUESTION)
        run_dir = data_root / "runs" / date_dir / name
        run_dir.mkdir(parents=True)
        question = {
            "run_id": RUN_ID,
            "question": QUESTION,
            "assets": ["2330"],
            "question_type": "market_direction",
            "created_at_utc": "2026-08-10T02:00:00Z",
        }
        if asset_class is not None:
            question["asset_class"] = asset_class
        (run_dir / "question.json").write_text(
            json.dumps(question, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        snapshot = live.live_snapshot(data_root, RUN_ID)
        self.assertEqual(RUN_ID, snapshot["run_id"])
        self.assertEqual(asset_class, snapshot["asset_class"])
        return pages.render_live_page(snapshot)

    def cards(self, asset_class):
        """``seat_id`` → the markup of that seat's card, from the seat panel."""
        panel = SEAT_PANEL.search(self.room(asset_class))
        self.assertIsNotNone(panel, "找不到席位卡區")
        cards = dict(SEAT_CARD.findall(panel.group(1)))
        self.assertEqual(set(SEAT_IDS), set(cards))
        return cards

    def blurbs_shown(self, asset_class):
        """``seat_id`` → the one sentence printed under that seat's card."""
        shown = {}
        for seat_id, card in self.cards(asset_class).items():
            found = SEAT_BLURB.search(card)
            shown[seat_id] = found.group(1) if found else None
        return shown

    def expected_blurbs(self, asset_class):
        return {
            seat_id: escape(profile.blurb)
            for seat_id, profile in seat_profiles(asset_class).items()
        }

    def test_a_taiwan_stock_run_explains_its_seats_from_the_stock_set(self):
        self.assertEqual(
            self.expected_blurbs(ASSET_CLASS_TW_STOCK),
            self.blurbs_shown(ASSET_CLASS_TW_STOCK),
        )

    def test_a_us_stock_run_reads_the_same_stock_set(self):
        self.assertEqual(
            self.expected_blurbs(ASSET_CLASS_US_STOCK),
            self.blurbs_shown(ASSET_CLASS_US_STOCK),
        )

    def test_a_crypto_run_explains_its_seats_from_the_crypto_set(self):
        self.assertEqual(
            self.expected_blurbs(ASSET_CLASS_CRYPTO),
            self.blurbs_shown(ASSET_CLASS_CRYPTO),
        )

    def test_a_run_recorded_before_the_field_existed_reads_the_open_set(self):
        self.assertEqual(self.expected_blurbs(None), self.blurbs_shown(None))
        self.assertEqual(
            self.expected_blurbs(ASSET_CLASS_OPEN), self.blurbs_shown(None)
        )

    def test_the_two_markets_really_are_explained_differently(self):
        """鑑別力：兩套的句子若完全一樣，上面四條就沒有在分辨任何東西。"""
        stock = self.expected_blurbs(ASSET_CLASS_TW_STOCK)
        crypto = self.expected_blurbs(ASSET_CLASS_CRYPTO)

        differing = [
            seat_id for seat_id in SEAT_IDS if stock[seat_id] != crypto[seat_id]
        ]
        self.assertTrue(differing, "roster 的兩套白話說明已無差別")

        shown = self.blurbs_shown(ASSET_CLASS_TW_STOCK)
        for seat_id in differing:
            self.assertNotEqual(crypto[seat_id], shown[seat_id], seat_id)

    def test_a_card_carries_this_runs_name_beside_this_runs_sentence(self):
        """名字與說明是同一張卡上的兩件事，不是頁面上兩個各自為政的區塊。"""
        cards = self.cards(ASSET_CLASS_TW_STOCK)
        profiles = seat_profiles(ASSET_CLASS_TW_STOCK)

        for seat_id, card in cards.items():
            self.assertIn(escape(profiles[seat_id].display_name), card, seat_id)
            self.assertIn(escape(profiles[seat_id].blurb), card, seat_id)


class SeatFrameCarriesItsOwnSentenceTest(unittest.TestCase):
    """每一幀自己帶著這一趟 run 的名稱與說明，客戶端不留任何副本。

    重畫發生在瀏覽器裡，所以「重畫後還對不對」不是由腳本的字面內容決定，而是由
    **幀裡有沒有那句話**決定：名稱與說明同在一個 seat 物件裡，就沒有任何配對可以
    走鐘。這一類斷言因此下在幀上，而不是下在渲染後的 HTML 上。
    """

    def data_root(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def write_run(self, data_root, run_id, asset_class):
        date_dir, name = run_dir_parts(run_id, QUESTION)
        run_dir = Path(data_root) / "runs" / date_dir / name
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "question.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "question": QUESTION,
                    "assets": ["2330"],
                    "question_type": "market_direction",
                    "created_at_utc": "2026-08-10T02:00:00Z",
                    "asset_class": asset_class,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return run_dir

    def frame_for(self, data_root, run_id=None):
        """The frame the stream sends, built exactly as the server builds it."""
        resolved_id, run_dir = live.resolve_live_run(data_root, run_id)
        room, offset, missed, _ = live.open_room(run_dir, live.read_question(run_dir))
        return server._room_payload(room, missed, resolved_id, offset)

    def pairs_in(self, seats):
        return {seat["seat_id"]: (seat["seat_label"], seat["seat_blurb"]) for seat in seats}

    def approved_pairs(self, asset_class):
        return {
            seat_id: (profile.display_name, profile.blurb)
            for seat_id, profile in seat_profiles(asset_class).items()
        }

    def test_a_frame_names_and_explains_every_seat_from_one_set(self):
        root = self.data_root()
        self.write_run(root, "20260810T020100Z-stockrun001", ASSET_CLASS_TW_STOCK)

        frame = self.frame_for(root)

        self.assertEqual(
            self.approved_pairs(ASSET_CLASS_TW_STOCK), self.pairs_in(frame["seats"])
        )

    def test_the_waiting_room_hands_over_to_the_new_runs_own_sentences(self):
        """Reviewer A F-A05-1 的回歸測試。

        ``POST /launch`` 先把讀者送回還沒有 run 的首頁（開放套），子程序寫出
        ``question.json`` 之後第一幀才帶著真正的套組到達。那一幀必須同時換掉名稱
        **與**說明；只換名稱的話，畫面會是股票席名配開放套的說明。
        """
        root = self.data_root()
        waiting = live.live_snapshot(root)
        self.assertIsNone(waiting["asset_class"])
        waiting_pairs = self.pairs_in(waiting["seats"])
        self.assertEqual(self.approved_pairs(ASSET_CLASS_OPEN), waiting_pairs)

        self.write_run(root, "20260810T020100Z-stockrun001", ASSET_CLASS_TW_STOCK)
        frame = self.frame_for(root)

        self.assertEqual(
            self.approved_pairs(ASSET_CLASS_TW_STOCK), self.pairs_in(frame["seats"])
        )
        moved = [
            seat_id
            for seat_id in SEAT_IDS
            if waiting_pairs[seat_id] != self.approved_pairs(ASSET_CLASS_TW_STOCK)[seat_id]
        ]
        self.assertEqual(list(SEAT_IDS), moved, "兩套若有一席完全相同，這條就少擋一種壞法")

    def test_a_frame_for_another_run_carries_that_runs_own_sentences(self):
        """Reviewer B F-B05-1 的回歸測試。

        頁面釘在 A run（幣），腳本連的 ``/live/events`` 沒有帶 run，於是跟到最新的
        B run（台股）。幀裡的名稱與說明必須整組是 B 的，客戶端才拼不出「B 的名字＋
        A 的說明」。
        """
        root = self.data_root()
        self.write_run(root, "20260810T020000Z-cryptorun01", ASSET_CLASS_CRYPTO)
        page_a = live.live_snapshot(root, "20260810T020000Z-cryptorun01")
        self.assertEqual(
            self.approved_pairs(ASSET_CLASS_CRYPTO), self.pairs_in(page_a["seats"])
        )
        self.write_run(root, "20260810T020500Z-stockrun002", ASSET_CLASS_TW_STOCK)

        frame = self.frame_for(root)

        self.assertEqual("20260810T020500Z-stockrun002", frame["run_id"])
        self.assertEqual(
            self.approved_pairs(ASSET_CLASS_TW_STOCK), self.pairs_in(frame["seats"])
        )

    def test_the_redraw_builds_the_sentence_from_the_frame_it_was_handed(self):
        """腳本讀的是幀上的欄位，不是自己留的副本。

        Reviewer B 的探針方向：把 ``LIVE_SCRIPT`` 挖空只留字串，這一條必須紅——所以
        先真的把 ``drawSeats()`` 這個函式從腳本裡切出來，切不到就失敗，再看它讀的
        是不是幀上的欄位。
        """
        found = re.search(
            r"function drawSeats\(seats\) \{(.*?)\n  \}\n", pages.LIVE_SCRIPT, re.DOTALL
        )
        self.assertIsNotNone(found, "LIVE_SCRIPT 裡找不到 drawSeats()")
        body = found.group(1)

        self.assertIn("seat_blurb", body)
        self.assertIn("agent-blurb", body)
        self.assertNotIn(
            "seatBlurbs", pages.LIVE_SCRIPT, "客戶端不得再留一份 blurb 副本"
        )


if __name__ == "__main__":
    unittest.main()
