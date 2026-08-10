"""The seven seats: the roster they are loaded from, and who they are shown as."""

import unittest
from pathlib import Path

from hoya_market_agents import seats as seats_module
from hoya_market_agents.question import ASSET_CLASSES
from hoya_market_agents.seats import (
    SEAT_AVATARS,
    SEAT_IDENTITIES,
    SEAT_IDS,
    seat_identities,
    seat_identity,
    seat_profiles,
)

PACKAGE_ROOT = Path(seats_module.__file__).resolve().parent
CODE_ROOT = PACKAGE_ROOT.parent
ROSTER_NAME = "agent_roster.json"

# 影子詞彙表可能落腳的兩種出貨檔案：套件裡的 Python，以及 roster 以外的設定檔。
# 刻意不掃 ``tests/``、``docs/`` 與 ``*.md``——規格、ADR 與測試的預期值本來就會逐字
# 寫出席名，那是獨立真值來源，不是第二份權威。
SCANNED_FILES = sorted(PACKAGE_ROOT.rglob("*.py")) + [
    path
    for path in sorted((CODE_ROOT / "config").glob("*.json"))
    if path.name != ROSTER_NAME
]


def modules_containing(needles):
    """Return the shipped files whose text spells any of ``needles``.

    Reading the source rather than importing is the point: a second copy of the
    seven seats' identity is a *literal* written somewhere else, and a module
    that derives its names from the authority does not contain them.
    """
    found = set()
    for path in SCANNED_FILES:
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            found.add(path.relative_to(CODE_ROOT).as_posix())
    return found


class SeatIdentityTest(unittest.TestCase):
    """One identity per fixed seat, and every field is filled in."""

    def test_the_identities_cover_the_fixed_seats_and_nothing_else(self):
        self.assertEqual(set(SEAT_IDS), set(SEAT_IDENTITIES))

    def test_each_identity_carries_its_own_seat_id(self):
        for seat_id, identity in SEAT_IDENTITIES.items():
            self.assertEqual(seat_id, identity.seat_id)

    def test_no_field_of_an_identity_is_left_blank(self):
        for seat_id, identity in SEAT_IDENTITIES.items():
            for field in ("display_name", "agent_number", "avatar", "provider"):
                self.assertTrue(getattr(identity, field), "{}.{}".format(seat_id, field))

    def test_every_seat_is_told_apart_by_its_name_number_and_avatar(self):
        for field in ("display_name", "agent_number", "avatar"):
            values = [getattr(i, field) for i in SEAT_IDENTITIES.values()]
            self.assertEqual(len(SEAT_IDS), len(set(values)), field)

    def test_an_identity_is_read_by_seat_id(self):
        self.assertEqual(
            SEAT_IDENTITIES["spot-technical"], seat_identity("spot-technical")
        )

    def test_a_seat_id_that_is_not_one_of_the_seven_has_no_identity(self):
        self.assertIsNone(seat_identity("chief-analyst"))

    def test_an_identity_cannot_be_edited_in_place(self):
        with self.assertRaises(Exception):
            seat_identity("news").display_name = "別的名字"

    def test_the_shown_name_is_the_provider_family_plus_the_rosters_own_name(self):
        """顯示名稱是投影，不是本模組的字面值：roster 改名這裡就跟著改。"""
        names = seat_profiles()

        for seat_id, identity in SEAT_IDENTITIES.items():
            self.assertEqual(
                "{}・{}".format(
                    identity.provider.capitalize(), names[seat_id].display_name
                ),
                identity.display_name,
                seat_id,
            )


class SingleIdentityAuthorityTest(unittest.TestCase):
    """Seven seats, one authority: ``config/agent_roster.json`` (ADR 0006).

    Since the profile sets landed, no module may spell a seat's shown name at
    all — a name depends on the run's asset class, so a literal written in Python
    is by definition the wrong one for two of the three sets. The avatars are a
    different kind of fact: they do not follow the asset class, so ``seats`` still
    holds them and is the only file allowed to.
    """

    def names(self):
        """Every name a copy could hold: all three sets, plus their bylines.

        Reading only the default (open) set left the stock-only names 籌碼面分析師
        and 法人動向分析師 outside the scan, so a hard-coded stock table would not
        have turned this red. A shadow table is per-set by nature — that is exactly
        the bug this scan exists to catch.
        """
        names = set()
        for asset_class in ASSET_CLASSES:
            names |= {
                profile.display_name
                for profile in seat_profiles(asset_class).values()
            }
            names |= {
                identity.display_name
                for identity in seat_identities(asset_class).values()
            }
        return sorted(names)

    def avatars(self):
        return [identity.avatar for identity in SEAT_IDENTITIES.values()]

    def test_the_scan_looks_for_every_sets_names_not_just_the_default_one(self):
        """FP direction: 只收 open 套的話，股票套獨有的名字永遠掃不到。"""
        collected = set(self.names())

        for asset_class in ASSET_CLASSES:
            for seat_id, profile in seat_profiles(asset_class).items():
                self.assertIn(profile.display_name, collected, (asset_class, seat_id))
        for stock_only in ("籌碼面分析師", "法人動向分析師"):
            self.assertIn(stock_only, collected, stock_only)

    def test_no_module_spells_a_seat_name_of_its_own(self):
        self.assertEqual(set(), modules_containing(self.names()))

    def test_only_the_seat_module_spells_a_seat_avatar(self):
        self.assertEqual(
            {"hoya_market_agents/seats.py"}, modules_containing(self.avatars())
        )

    def test_the_retired_vocabulary_is_gone_from_the_package(self):
        """舊寫死表的字面值一個都不許留在程式裡。"""
        self.assertEqual(
            set(),
            modules_containing(
                [
                    "反證稽核員",
                    "現貨技術席",
                    "衍生品席",
                    "鏈上資料席",
                    "官方事件席",
                    "新聞資訊席",
                    "社群與總體經濟席",
                    "反方證據席",
                ]
            ),
        )

    def test_the_scan_finds_a_literal_that_really_is_in_the_package(self):
        """FP direction: a scan that matches nothing would pass the three above."""
        self.assertEqual({"hoya_market_agents/seats.py"}, modules_containing(["📈"]))

    def test_the_scan_reads_every_module_in_the_package(self):
        """A scan over an empty file list is also a scan that never fails."""
        scanned = modules_containing(["import"])

        self.assertIn("hoya_market_agents/seats.py", scanned)
        self.assertIn("hoya_market_agents/report_renderer.py", scanned)
        self.assertIn("hoya_market_agents/webapp/server.py", scanned)

    def test_the_scan_reads_the_shipped_config_beside_the_roster(self):
        """設定檔也可能藏第二份詞彙表，所以掃描不能只看 Python。"""
        self.assertIn(
            "config/market_scopes.json", modules_containing(["加密資產"])
        )
        self.assertNotIn(
            "config/{}".format(ROSTER_NAME),
            {path.relative_to(CODE_ROOT).as_posix() for path in SCANNED_FILES},
        )

    def test_the_avatars_are_the_identity_authoritys_own(self):
        self.assertEqual(set(SEAT_IDS), set(SEAT_AVATARS))
        for seat_id, identity in SEAT_IDENTITIES.items():
            self.assertEqual(identity.avatar, SEAT_AVATARS[seat_id], seat_id)


if __name__ == "__main__":
    unittest.main()
