"""The roster is the only authority for what a seat is called and researches.

Three profile sets per seat (stock／crypto／open), one decision point that turns
a run's asset class into a set, and one reading port every display path uses.
A missing seat, a missing set or a blank field must refuse to load rather than
fall back to a made-up name (ADR 0006). Since Spec R-005 a set also carries the
one-sentence 白話說明 a reader sees under the seat card, on the same terms: it is
required, so a roster without it does not load at all.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from hoya_market_agents import seats as seats_module
from hoya_market_agents.prompt_builder import build_seat_prompt
from hoya_market_agents.question import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.question_package import build_question_package
from hoya_market_agents.seats import (
    PROFILE_SET_BY_ASSET_CLASS,
    PROFILE_SETS,
    ROSTER_PATH,
    RosterError,
    SEAT_IDS,
    load_roster,
    profile_set_for,
    seat_identities,
    seat_profiles,
)

# The approved division of labour, straight out of Spec R-005's table:
# ``(seat_id, display_name, blurb)`` per set, in the roster's fixed seat order.
# Kept here as the independent truth the roster is checked against, so a silent
# edit to the config cannot quietly rename a seat or rewrite what it says it
# does. The stock set is what a 台股 and a 美股 run read; 開放 keeps the names it
# shipped with (Spec R-005 leaves them alone) and gains a blurb like the others.
APPROVED_PROFILES = {
    "stock": (
        (
            "spot-technical",
            "技術面分析師",
            "看線圖：價量、均線、支撐壓力，判斷走勢強弱與關鍵價位",
        ),
        (
            "derivatives",
            "籌碼面分析師",
            "看期貨與選擇權部位、融資融券：判斷大戶與散戶各押哪一邊",
        ),
        (
            "onchain",
            "法人動向分析師",
            "看法人與大股東的錢往哪走：買賣超、持股變化、資金流向",
        ),
        (
            "official-events",
            "官方公告哨兵",
            "盯官方公告與行事曆：重大訊息、財報法說日程、主管機關動作",
        ),
        (
            "news",
            "新聞探員",
            "查證具名媒體報導、整理事件時間線，過濾未經證實的消息",
        ),
        (
            "social-macro",
            "輿情與大盤觀察員",
            "看散戶討論風向與大環境：論壇情緒、宏觀消息、大盤與產業連動",
        ),
        (
            "counter-evidence",
            "基本面分析師",
            "看公司本身體質：營收財報、估值比較、產業供需",
        ),
    ),
    "crypto": (
        (
            "spot-technical",
            "技術面分析師",
            "看線圖：價量、均線、支撐壓力，判斷走勢強弱與關鍵價位",
        ),
        (
            "derivatives",
            "合約槓桿分析師",
            "看合約市場的槓桿狀態：多空部位、資金費率、清算風險",
        ),
        (
            "onchain",
            "鏈上資金追蹤師",
            "看鏈上的錢往哪走：巨鯨動向、交易所進出、籌碼供給",
        ),
        (
            "official-events",
            "官方公告哨兵",
            "盯官方消息：項目方公告、監管動作、重大事件時程",
        ),
        (
            "news",
            "新聞探員",
            "查證具名媒體報導、整理事件時間線，過濾未經證實的消息",
        ),
        (
            "social-macro",
            "輿情與幣市社群觀察員",
            "看社群風向與大環境：討論熱度、宏觀消息、BTC 連動",
        ),
        (
            "counter-evidence",
            "項目體質分析師",
            "看項目本身體質：鎖倉量、協議收入、代幣解鎖時程",
        ),
    ),
    "open": (
        ("spot-technical", "圖表偵探", "看價量與技術結構，判斷走勢強弱"),
        ("derivatives", "槓桿雷達", "看衍生品部位與槓桿狀態"),
        ("onchain", "鏈上獵人", "看鏈上資金與供給動向"),
        ("official-events", "官方哨兵", "盯官方公告、監管與重大事件"),
        ("news", "新聞探員", "查證具名媒體報導、整理事件時間線"),
        ("social-macro", "社群觀察員", "看社群情緒與宏觀環境"),
        ("counter-evidence", "基本面研究員", "查核題目相關的關鍵數據與事實"),
    ),
}
PROFILE_FIELDS = ("display_name", "focus", "blurb")


def approved_names(set_name):
    return tuple(name for _, name, _ in APPROVED_PROFILES[set_name])


STOCK_DISPLAY_NAMES = approved_names("stock")
CRYPTO_DISPLAY_NAMES = approved_names("crypto")
OPEN_DISPLAY_NAMES = approved_names("open")
# 2026-08-09 前的 seat_id 與 output_dir，逐字釘住：歷史 run 目錄、
# ``ANTIGRAVITY_SEAT_IDS`` 與賽前預檢全部綁著它們，永不隨套組或職能改變。
FROZEN_SEAT_DIRS = (
    ("spot-technical", "spot-technical"),
    ("derivatives", "derivatives"),
    ("onchain", "onchain"),
    ("official-events", "official-events"),
    ("news", "news"),
    ("social-macro", "social-macro"),
    ("counter-evidence", "counter-evidence"),
)


def roster_document():
    return json.loads(ROSTER_PATH.read_text(encoding="utf-8"))


def with_reversed_key_order(value):
    """The same roster, every object's keys written in the opposite order.

    A loader that reads fields by name answers exactly the same thing; one that
    leans on the order the file happens to use does not.
    """
    if isinstance(value, dict):
        return {
            key: with_reversed_key_order(value[key]) for key in reversed(list(value))
        }
    if isinstance(value, list):
        return [with_reversed_key_order(item) for item in value]
    return value


class RosterAtPath:
    """Write one mutated roster to a temp file and load it from there."""

    def __init__(self, test):
        self.test = test

    def load(self, document):
        return self.load_text(json.dumps(document, ensure_ascii=False))

    def load_text(self, text):
        """Load whatever bytes a roster file might actually contain."""
        directory = tempfile.TemporaryDirectory()
        self.test.addCleanup(directory.cleanup)
        path = Path(directory.name) / "agent_roster.json"
        path.write_text(text, encoding="utf-8")
        return load_roster(path)

    def missing_path(self):
        directory = tempfile.TemporaryDirectory()
        self.test.addCleanup(directory.cleanup)
        return Path(directory.name) / "not-written.json"


class ProfileSetSelectionTest(unittest.TestCase):
    """One decision point turns a run's asset class into a profile set."""

    def test_both_stock_markets_share_the_stock_set(self):
        self.assertEqual("stock", profile_set_for(ASSET_CLASS_TW_STOCK))
        self.assertEqual("stock", profile_set_for(ASSET_CLASS_US_STOCK))

    def test_crypto_and_open_get_their_own_sets(self):
        self.assertEqual("crypto", profile_set_for(ASSET_CLASS_CRYPTO))
        self.assertEqual("open", profile_set_for(ASSET_CLASS_OPEN))

    def test_a_run_with_no_single_asset_class_reads_the_open_set(self):
        """跨類與未指名一律 open：顯示端永遠拿得到一套，不得沒有名字。"""
        self.assertEqual("open", profile_set_for(None))
        self.assertEqual("open", profile_set_for("tw_stock+crypto"))

    def test_every_asset_class_the_product_knows_is_mapped(self):
        """新增資產類別而沒有補套組對應，這裡就紅——不是靜靜落到 open。"""
        self.assertEqual(set(ASSET_CLASSES), set(PROFILE_SET_BY_ASSET_CLASS))
        self.assertEqual(set(PROFILE_SET_BY_ASSET_CLASS.values()), set(PROFILE_SETS))


class RosterProfileLoadTest(unittest.TestCase):
    """Seven seats, three filled sets each, in the approved order."""

    def setUp(self):
        self.roster = load_roster()

    def test_the_seats_load_in_approved_order(self):
        self.assertEqual(list(SEAT_IDS), [seat.seat_id for seat in self.roster])

    def test_every_seat_carries_three_named_directed_and_explained_profiles(self):
        for seat in self.roster:
            self.assertEqual(set(PROFILE_SETS), set(seat.profiles), seat.seat_id)
            for set_name in PROFILE_SETS:
                profile = seat.profiles[set_name]
                for field in PROFILE_FIELDS:
                    self.assertTrue(
                        getattr(profile, field), (seat.seat_id, set_name, field)
                    )

    def test_every_set_is_named_and_explained_exactly_as_approved(self):
        """三套的名字與白話說明逐字對 Spec R-005 的表，一個字都不許漂。"""
        for set_name in PROFILE_SETS:
            with self.subTest(profile_set=set_name):
                self.assertEqual(
                    list(APPROVED_PROFILES[set_name]),
                    [
                        (
                            seat.seat_id,
                            seat.profiles[set_name].display_name,
                            seat.profiles[set_name].blurb,
                        )
                        for seat in self.roster
                    ],
                )

    def test_the_open_set_keeps_the_names_it_shipped_with(self):
        """開放套不改名（Spec R-005 明列不在範圍內），所以它跟另外兩套不同名。"""
        self.assertEqual(
            list(OPEN_DISPLAY_NAMES),
            [seat.profiles["open"].display_name for seat in self.roster],
        )
        for set_name in ("stock", "crypto"):
            renamed = [
                seat.seat_id
                for seat in self.roster
                if seat.profiles[set_name].display_name
                != seat.profiles["open"].display_name
            ]
            self.assertTrue(renamed, set_name)

    def test_a_blurb_is_a_sentence_of_its_own_and_not_the_research_brief(self):
        """白話說明是給讀者的一句話，不是把 ``focus`` 抄一遍當顯示文字。"""
        for seat in self.roster:
            for set_name in PROFILE_SETS:
                profile = seat.profiles[set_name]
                self.assertNotEqual(
                    profile.focus, profile.blurb, (seat.seat_id, set_name)
                )

    def test_the_generic_focus_stays_a_projection_of_the_open_set(self):
        """席位層 ``focus`` 仍是賽前預檢的必填欄位，但不得成為第二份事實。"""
        for seat in self.roster:
            self.assertEqual(seat.profiles["open"].focus, seat.focus, seat.seat_id)

    def test_the_seventh_seat_researches_fundamentals_in_every_set(self):
        seventh = self.roster[6]

        self.assertEqual("counter-evidence", seventh.seat_id)
        self.assertEqual("基本面分析師", seventh.profiles["stock"].display_name)
        self.assertEqual("項目體質分析師", seventh.profiles["crypto"].display_name)
        self.assertEqual("基本面研究員", seventh.profiles["open"].display_name)
        self.assertIn("月營收", seventh.profiles["stock"].focus)
        self.assertIn("TVL", seventh.profiles["crypto"].focus)
        self.assertEqual("關鍵數據與事實查核", seventh.profiles["open"].focus)
        for set_name in PROFILE_SETS:
            self.assertNotIn("反方證據", seventh.profiles[set_name].focus)

    def test_the_stock_focus_covers_both_stock_markets(self):
        """台股與美股共用一套，所以那一套必須同時說出兩個市場的方向。"""
        for seat in self.roster:
            focus = seat.profiles["stock"].focus
            self.assertIn("台股", focus, seat.seat_id)
            self.assertIn("美股", focus, seat.seat_id)

    def test_seat_ids_and_output_dirs_are_frozen(self):
        self.assertEqual(
            list(FROZEN_SEAT_DIRS),
            [(seat.seat_id, seat.output_dir) for seat in self.roster],
        )

    def test_the_schema_version_records_the_profile_upgrade(self):
        """必填欄位的集合與版號一起釘：一個動了另一個沒動，這裡就紅。

        `blurb` 是新的**必填**欄位，舊 roster 一律載不進來，所以那是不相容變更，
        主版號要走（2.0.0 → 3.0.0）。版號會被寫進賽前預檢 manifest，是稽核用來分辨
        兩份 schema 的唯一依據——所以它不能停在描述舊 schema 的號碼上。
        """
        document = roster_document()

        self.assertEqual("3.0.0", document["roster_version"])
        for seat in document["seats"]:
            for set_name in PROFILE_SETS:
                self.assertEqual(
                    set(PROFILE_FIELDS),
                    set(seat["profiles"][set_name]),
                    (seat["seat_id"], set_name),
                )

    def test_the_key_order_the_file_happens_to_use_changes_nothing(self):
        """設定檔的鍵順序不是契約：整份倒著寫，載入結果必須一模一樣。"""
        document = roster_document()
        reordered = with_reversed_key_order(document)
        self.assertNotEqual(
            json.dumps(document, ensure_ascii=False),
            json.dumps(reordered, ensure_ascii=False),
            "倒序後檔案內容沒變，這條就測不到東西",
        )

        self.assertEqual(self.roster, RosterAtPath(self).load(reordered))

    def test_a_blurb_never_reaches_a_seats_research_prompt(self):
        """白話說明只給讀者看：席位 prompt 讀的是 ``focus``（Spec R-005）。"""
        package = build_question_package("幫我分析 2330 未來七天會不會漲")

        for seat in self.roster:
            section = build_seat_prompt(package, seat, "research").seat_section
            self.assertIn(seat.profiles["stock"].focus, section, seat.seat_id)
            for set_name in PROFILE_SETS:
                self.assertNotIn(
                    seat.profiles[set_name].blurb, section, (seat.seat_id, set_name)
                )


class RosterFailsClosedTest(unittest.TestCase):
    """Nothing missing is ever filled in with a default."""

    def setUp(self):
        self.roster = RosterAtPath(self)

    def test_a_missing_seat_is_refused(self):
        document = roster_document()
        document["seats"] = [
            seat for seat in document["seats"] if seat["seat_id"] != "counter-evidence"
        ]

        with self.assertRaises(RosterError) as caught:
            self.roster.load(document)
        self.assertIn("counter-evidence", str(caught.exception))

    def test_a_seat_with_no_profiles_at_all_is_refused(self):
        document = roster_document()
        del document["seats"][2]["profiles"]

        with self.assertRaises(RosterError) as caught:
            self.roster.load(document)
        self.assertIn("onchain", str(caught.exception))
        self.assertIn("profiles", str(caught.exception))

    def test_a_missing_profile_set_names_the_seat_and_the_set(self):
        for index, seat_id in enumerate(SEAT_IDS):
            for set_name in PROFILE_SETS:
                with self.subTest(seat_id=seat_id, profile_set=set_name):
                    document = roster_document()
                    del document["seats"][index]["profiles"][set_name]

                    with self.assertRaises(RosterError) as caught:
                        self.roster.load(document)
                    message = str(caught.exception)
                    self.assertIn(seat_id, message)
                    self.assertIn(set_name, message)

    def test_a_missing_field_in_a_set_names_the_seat_the_set_and_the_field(self):
        """欄位整個不見（而不是留空）也一樣拒載——每席、每套、每個欄位都試過。"""
        for index, seat_id in enumerate(SEAT_IDS):
            for set_name in PROFILE_SETS:
                for field in PROFILE_FIELDS:
                    with self.subTest(
                        seat_id=seat_id, profile_set=set_name, field=field
                    ):
                        document = roster_document()
                        del document["seats"][index]["profiles"][set_name][field]

                        with self.assertRaises(RosterError) as caught:
                            self.roster.load(document)
                        message = str(caught.exception)
                        self.assertIn(seat_id, message)
                        self.assertIn(set_name, message)
                        self.assertIn(field, message)

    def test_a_blank_field_in_a_set_names_the_seat_the_set_and_the_field(self):
        for field in PROFILE_FIELDS:
            for blank in ("", "   "):
                with self.subTest(field=field, blank=repr(blank)):
                    document = roster_document()
                    document["seats"][4]["profiles"]["crypto"][field] = blank

                    with self.assertRaises(RosterError) as caught:
                        self.roster.load(document)
                    message = str(caught.exception)
                    self.assertIn("news", message)
                    self.assertIn("crypto", message)
                    self.assertIn(field, message)

    def test_a_profile_set_that_is_not_an_object_is_refused(self):
        document = roster_document()
        document["seats"][0]["profiles"]["stock"] = "圖表偵探"

        with self.assertRaises(RosterError) as caught:
            self.roster.load(document)
        self.assertIn("spot-technical", str(caught.exception))
        self.assertIn("stock", str(caught.exception))

    def test_a_seat_level_field_must_be_a_non_blank_string(self):
        """``focus`` 與 ``output_dir`` 進 prompt 與檔案路徑，型別對了才有意義。

        非空字串以外的 truthy 值（list、dict、數字）過去會被當成填好了，於是
        ``agents/['spot-technical']/`` 這種目錄敘述會直接寫進席位 prompt。
        """
        for field in ("focus", "output_dir"):
            for value in (["spot-technical"], {"dir": "x"}, 7, True, None, "", "   "):
                with self.subTest(field=field, value=repr(value)):
                    document = roster_document()
                    document["seats"][0][field] = value

                    with self.assertRaises(RosterError) as caught:
                        self.roster.load(document)
                    message = str(caught.exception)
                    self.assertIn("spot-technical", message)
                    self.assertIn(field, message)

    def test_a_seat_id_that_is_not_one_of_the_seven_is_refused(self):
        for seat_id in ("chief-analyst", "", None, ["news"]):
            with self.subTest(seat_id=repr(seat_id)):
                document = roster_document()
                document["seats"][4]["seat_id"] = seat_id

                with self.assertRaises(RosterError):
                    self.roster.load(document)

    def test_a_seat_listed_twice_is_refused(self):
        """兩份同 ID 的席位裡，後面那份會靜靜蓋掉前面那份的方向與目錄。"""
        document = roster_document()
        duplicate = copy.deepcopy(document["seats"][4])
        duplicate["focus"] = "偷偷覆蓋掉的研究範圍"
        document["seats"].append(duplicate)

        with self.assertRaises(RosterError) as caught:
            self.roster.load(document)
        self.assertIn("news", str(caught.exception))

    def test_a_seat_entry_that_is_not_an_object_is_refused(self):
        document = roster_document()
        document["seats"][3] = "official-events"

        with self.assertRaises(RosterError):
            self.roster.load(document)

    def test_a_roster_whose_outermost_value_is_not_an_object_is_refused(self):
        for text in ("[]", '"roster"', "null", "7"):
            with self.subTest(text=text):
                with self.assertRaises(RosterError):
                    self.roster.load_text(text)

    def test_a_seats_field_that_is_not_an_array_is_refused(self):
        for seats in ({}, "spot-technical", None, 7):
            with self.subTest(seats=repr(seats)):
                document = roster_document()
                document["seats"] = seats

                with self.assertRaises(RosterError) as caught:
                    self.roster.load(document)
                self.assertIn("seats", str(caught.exception))

    def test_a_file_that_is_not_json_is_refused_readably(self):
        with self.assertRaises(RosterError) as caught:
            self.roster.load_text("{ not json")
        self.assertIn("JSON", str(caught.exception))

    def test_a_roster_that_is_not_there_is_refused(self):
        path = self.roster.missing_path()

        with self.assertRaises(RosterError) as caught:
            load_roster(path)
        self.assertIn(str(path), str(caught.exception))


class SeatProfileReadingPortTest(unittest.TestCase):
    """Every display path asks the same question: name and focus for this class."""

    def test_the_port_answers_per_seat_for_a_stock_run(self):
        profiles = seat_profiles(ASSET_CLASS_TW_STOCK)

        self.assertEqual(list(SEAT_IDS), list(profiles))
        self.assertEqual(
            list(STOCK_DISPLAY_NAMES),
            [profiles[seat_id].display_name for seat_id in SEAT_IDS],
        )

    def test_the_port_hands_back_this_sets_blurb_beside_its_name(self):
        """顯示端要的一句白話從同一個讀取口出來，沒有第二條路徑（Spec R-005）。"""
        for asset_class, set_name in (
            (ASSET_CLASS_TW_STOCK, "stock"),
            (ASSET_CLASS_US_STOCK, "stock"),
            (ASSET_CLASS_CRYPTO, "crypto"),
            (ASSET_CLASS_OPEN, "open"),
            (None, "open"),
        ):
            with self.subTest(asset_class=asset_class):
                profiles = seat_profiles(asset_class)

                self.assertEqual(
                    list(APPROVED_PROFILES[set_name]),
                    [
                        (seat_id, profiles[seat_id].display_name, profiles[seat_id].blurb)
                        for seat_id in SEAT_IDS
                    ],
                )

    def test_both_stock_markets_read_the_same_names(self):
        self.assertEqual(
            seat_profiles(ASSET_CLASS_TW_STOCK), seat_profiles(ASSET_CLASS_US_STOCK)
        )

    def test_a_crypto_run_reads_the_crypto_names(self):
        profiles = seat_profiles(ASSET_CLASS_CRYPTO)

        self.assertEqual(
            list(CRYPTO_DISPLAY_NAMES),
            [profiles[seat_id].display_name for seat_id in SEAT_IDS],
        )

    def test_the_same_seat_is_named_and_directed_differently_per_class(self):
        stock = seat_profiles(ASSET_CLASS_TW_STOCK)["onchain"]
        crypto = seat_profiles(ASSET_CLASS_CRYPTO)["onchain"]

        self.assertEqual("法人動向分析師", stock.display_name)
        self.assertEqual("鏈上資金追蹤師", crypto.display_name)
        self.assertNotEqual(stock.focus, crypto.focus)
        self.assertNotEqual(stock.blurb, crypto.blurb)

    def test_a_run_with_no_asset_class_still_gets_the_open_set(self):
        self.assertEqual(seat_profiles(ASSET_CLASS_OPEN), seat_profiles(None))

    def test_the_port_hands_back_a_view_the_caller_cannot_edit(self):
        profiles = seat_profiles(ASSET_CLASS_CRYPTO)

        with self.assertRaises(TypeError):
            profiles["news"] = None

    def test_an_identity_shows_the_provider_family_and_the_set_name(self):
        stock = seat_identities(ASSET_CLASS_TW_STOCK)
        crypto = seat_identities(ASSET_CLASS_CRYPTO)

        self.assertEqual("Codex・技術面分析師", stock["spot-technical"].display_name)
        self.assertEqual("Claude・法人動向分析師", stock["onchain"].display_name)
        self.assertEqual("Claude・鏈上資金追蹤師", crypto["onchain"].display_name)
        self.assertEqual(
            "Gemini・項目體質分析師", crypto["counter-evidence"].display_name
        )

    def test_an_edited_roster_is_the_answer_the_next_caller_gets(self):
        """權威沒有在模組層凍結：改了 roster，下一次查詢就是新的名字。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "agent_roster.json"
        document = roster_document()
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        original = seats_module.ROSTER_PATH
        seats_module.ROSTER_PATH = path
        self.addCleanup(setattr, seats_module, "ROSTER_PATH", original)

        self.assertEqual("圖表偵探", seat_profiles()["spot-technical"].display_name)

        document["seats"][0]["profiles"]["open"]["display_name"] = "改過的名字"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        self.assertEqual("改過的名字", seat_profiles()["spot-technical"].display_name)

    def test_an_identity_keeps_the_fields_that_do_not_follow_the_asset_class(self):
        stock = seat_identities(ASSET_CLASS_TW_STOCK)
        crypto = seat_identities(ASSET_CLASS_CRYPTO)

        for seat_id in SEAT_IDS:
            for field in ("agent_number", "avatar", "provider"):
                self.assertEqual(
                    getattr(stock[seat_id], field),
                    getattr(crypto[seat_id], field),
                    (seat_id, field),
                )
        self.assertEqual("Agent 1", stock["spot-technical"].agent_number)
        self.assertEqual("📈", stock["spot-technical"].avatar)


if __name__ == "__main__":
    unittest.main()
