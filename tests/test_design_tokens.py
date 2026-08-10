"""The design token table, and the stylesheet that is nothing but that table.

Everything the site is painted with is data in
:mod:`~hoya_market_agents.design_tokens`: one palette in :data:`PALETTE` and one
scale in :data:`SCALE`. There is no second palette — the dark one is gone (Spec
R-004), and the checks here are what stops a second one growing back.

They are deliberately about the *table* rather than about any rule's text:

* WCAG AA is **computed** from the palette, with the measured ratio in the
  failure message, so a repaint that drops below the minimum names the pair and
  the number instead of a diff of hex codes.
* Every colour token is placed in a role — background, text, line or decoration
  — and the requirement table is generated from those roles. A further token
  therefore cannot reach a page without a measured minimum of its own.
* A translucent surface is measured as the colour a reader actually sees: the
  composite of the glass over the thing behind it. A ratio computed against
  ``rgba(...)`` would be a ratio of a colour nobody looks at.
* The four decorative hues are a different vocabulary from the three that carry
  a ballot's meaning, and both the names and the values are held apart, because
  "紅藍綠黃點綴" and "正／反／棄" answering to the same red is exactly the
  confusion the requirement forbids.
* Every ``var(--x)`` the rules reference resolves to a declared token, so a
  token renamed in the table cannot leave a rule painting with nothing.
* No rule names a colour of its own. The set of colour literals in the whole
  sheet is exactly the set of values the palette holds, which is what "the token
  table is the only colour source" means when it is checked rather than claimed.

The WCAG maths and the alpha compositing here are written out from the
specifications rather than imported from the module under test, because a ratio
computed by the same code that chose the colours is not a measurement.
"""

import re
import unittest

from hoya_market_agents import design_tokens, seats
from hoya_market_agents.webapp import live, pages


def _channel(value):
    value = value / 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour):
    """Relative luminance of ``#rrggbb``, per WCAG 2.1."""
    colour = colour.lstrip("#")
    red, green, blue = (int(colour[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(first, second):
    darker, lighter = sorted((luminance(first), luminance(second)))
    return (lighter + 0.05) / (darker + 0.05)


def meets(ratio, minimum):
    """Whether ``ratio`` clears an AA minimum.

    AA is a floor, and ``round(ratio, 2) >= minimum`` is not that floor: it
    accepts everything from ``minimum - 0.005`` upwards. ``#006ffb`` on white
    measures 4.4999 and rounds to 4.50, so a rounding guard would call it AA
    text contrast when it is not. Two decimal places are for reading, never for
    deciding.
    """
    return ratio >= minimum


def channels(colour):
    colour = colour.lstrip("#")
    return [int(colour[index:index + 2], 16) for index in (0, 2, 4)]


def flatten(translucent, backdrop):
    """``rgba(r,g,b,a)`` over an opaque ``#rrggbb``, per the CSS compositing model.

    Written out here rather than imported: the point of this file is to check the
    module's own compositing against the formula, not against itself.
    """
    red, green, blue, alpha = [
        float(part) for part in re.match(
            r"rgba?\(([^)]*)\)", translucent
        ).group(1).replace("/", ",").split(",")
    ]
    return "#" + "".join(
        "{:02x}".format(round(front * alpha + behind * (1 - alpha)))
        for front, behind in zip((red, green, blue), channels(backdrop))
    )


def colour_literals(text):
    """Every colour written out longhand in ``text``."""
    return set(re.findall(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", text))


def _rules_region(sheet):
    """The sheet with its token block removed: the rules and nothing else.

    The token block is the only place a colour may be written out, so every
    check on "does a rule name a value" runs on what is left after it goes.
    """
    return re.sub(r"^:root\{[^}]*\}", "", sheet)


class OnePaletteTest(unittest.TestCase):
    """One palette, and no way back to two."""

    def test_the_palette_is_one_flat_table_of_colours(self):
        self.assertTrue(design_tokens.PALETTE)
        for name, value in design_tokens.PALETTE.items():
            self.assertIsInstance(value, str, name)
            self.assertTrue(colour_literals(value), "{}={}".format(name, value))

    def test_no_second_palette_survives_anywhere(self):
        """Spec R-004 retires dark mode. A ``THEMES``-shaped thing on either
        module is the two-palette world growing back."""
        for module in (design_tokens, pages):
            self.assertFalse(hasattr(module, "THEMES"), module.__name__)

    def test_the_page_and_its_surfaces_are_light(self):
        """"深色模式退場、只留白底一套" is a claim about the colours, not only
        about the absence of a media query: every background the site paints on
        is nearer white than black."""
        for token in design_tokens.BACKGROUND_TOKENS:
            self.assertGreater(
                luminance(design_tokens.MEASURED_COLOURS[token]), 0.5, token
            )


class TokenRolesTest(unittest.TestCase):
    """The shape of the table itself."""

    def test_every_colour_token_is_placed_in_exactly_one_role(self):
        """A token with no role has no measured minimum, which is how an
        unreadable colour reaches a page; a token in two roles answers to two
        minimums and quietly keeps the looser one."""
        roles = (
            set(design_tokens.BACKGROUND_TOKENS),
            set(design_tokens.TEXT_TOKENS),
            set(design_tokens.LINE_TOKENS),
            set(design_tokens.DECOR_TOKENS),
            {design_tokens.INK_ON_ACCENT},
        )
        placed = set()
        for role in roles:
            self.assertEqual(set(), placed & role, role)
            placed |= role

        self.assertEqual(set(design_tokens.PALETTE), placed)

    def test_every_requirement_names_tokens_the_palette_declares(self):
        for foreground, background, _minimum in design_tokens.CONTRAST_REQUIREMENTS:
            self.assertIn(foreground, design_tokens.PALETTE)
            self.assertIn(background, design_tokens.PALETTE)

    def test_every_text_token_is_measured_against_every_background(self):
        required = {
            (foreground, background)
            for foreground, background, minimum in design_tokens.CONTRAST_REQUIREMENTS
            if minimum >= design_tokens.TEXT_MINIMUM
        }
        for token in design_tokens.TEXT_TOKENS:
            for background in design_tokens.BACKGROUND_TOKENS:
                self.assertIn((token, background), required)

    def test_every_line_token_is_measured_against_every_background(self):
        required = {
            (foreground, background)
            for foreground, background, minimum in design_tokens.CONTRAST_REQUIREMENTS
            if minimum >= design_tokens.LINE_MINIMUM
        }
        for token in design_tokens.LINE_TOKENS:
            for background in design_tokens.BACKGROUND_TOKENS:
                self.assertIn((token, background), required)

    def test_the_two_minimums_are_the_ones_aa_names(self):
        self.assertEqual(4.5, design_tokens.TEXT_MINIMUM)
        self.assertEqual(3.0, design_tokens.LINE_MINIMUM)


class GlassTest(unittest.TestCase):
    """A translucent surface is measured as the colour a reader sees.

    Spec R-004 asks for glass, and glass has two values: the one the sheet
    paints with and the one a contrast ratio can be computed from. Holding both
    is the whole reason the requirement "毛玻璃面用合成色" can be checked.
    """

    def test_the_site_declares_at_least_one_glass_surface(self):
        self.assertTrue(design_tokens.GLASS)

    def test_every_glass_token_is_translucent_and_names_what_is_behind_it(self):
        for token, backdrop in design_tokens.GLASS.items():
            self.assertIn(token, design_tokens.PALETTE, token)
            self.assertTrue(
                design_tokens.PALETTE[token].startswith("rgba("), token
            )
            self.assertIn(backdrop, design_tokens.PALETTE, backdrop)
            self.assertNotIn(backdrop, design_tokens.GLASS, backdrop)

    def test_every_glass_token_is_measured_as_its_composite(self):
        for token, backdrop in design_tokens.GLASS.items():
            self.assertEqual(
                flatten(
                    design_tokens.PALETTE[token],
                    design_tokens.MEASURED_COLOURS[backdrop],
                ),
                design_tokens.MEASURED_COLOURS[token],
                token,
            )

    def test_every_glass_surface_is_a_background_the_text_answers_to(self):
        """Glass that no text is measured against is glass nothing can be read
        on."""
        for token in design_tokens.GLASS:
            self.assertIn(token, design_tokens.BACKGROUND_TOKENS, token)

    def test_an_opaque_token_is_measured_as_the_value_the_sheet_paints(self):
        """The flattening applies to glass and to nothing else: an opaque token
        whose measured colour differs from its declared one is a second copy of
        that colour."""
        for token, value in design_tokens.PALETTE.items():
            if token not in design_tokens.GLASS:
                self.assertEqual(value, design_tokens.MEASURED_COLOURS[token], token)

    def test_the_blur_is_a_native_css_length(self):
        """Glass is ``backdrop-filter`` and nothing else — no image, no asset."""
        self.assertRegex(design_tokens.SCALE["glass_blur"], r"^[\d.]+(px|rem)$")


class DecorationIsNotMeaningTest(unittest.TestCase):
    """紅藍綠黃 decorate; 正／反／棄 mean. They are held apart on purpose."""

    def test_the_four_decorative_hues_are_named_apart_from_the_ballot_hues(self):
        self.assertEqual(4, len(design_tokens.DECOR_TOKENS))
        self.assertEqual(
            set(),
            set(design_tokens.DECOR_TOKENS) & set(design_tokens.STANCE_TOKENS),
        )

    def test_every_decorative_token_is_named_as_decoration(self):
        """One prefix, so a reader of the table can tell at a glance which
        vocabulary a token belongs to."""
        for token in design_tokens.DECOR_TOKENS:
            self.assertTrue(token.startswith("google_"), token)
        for token in design_tokens.STANCE_TOKENS:
            self.assertFalse(token.startswith("google_"), token)

    def test_no_decorative_hue_is_the_same_colour_as_a_hue_that_means_something(self):
        """"必須在畫面上可區分" is about the colours, not only the names: a
        decoration that is byte-for-byte the ballot's red is the mixing the
        requirement forbids, whatever it is called."""
        meaningful = {
            design_tokens.PALETTE[token]
            for token in design_tokens.STANCE_TOKENS
            + design_tokens.OUTCOME_TOKENS
            + design_tokens.LINE_TOKENS
        }
        for token in design_tokens.DECOR_TOKENS:
            self.assertNotIn(design_tokens.PALETTE[token], meaningful, token)

    def test_every_decorative_hue_carries_an_ink_that_is_readable_on_it(self):
        """A decoration is a fill — a chip, a bar, a dot — so what AA asks of it
        is that the text placed on it can be read. That pairing is data, because
        a yellow and a blue do not take the same ink."""
        required = {
            (foreground, background)
            for foreground, background, minimum in design_tokens.CONTRAST_REQUIREMENTS
            if minimum >= design_tokens.TEXT_MINIMUM
        }
        for token in design_tokens.DECOR_TOKENS:
            ink = design_tokens.DECOR_INK[token]
            self.assertIn(ink, design_tokens.PALETTE, token)
            self.assertIn((ink, token), required, token)

    def test_the_lights_keep_their_meaning_and_hold_no_hue_here(self):
        """The five lights are the authority's own icon beside the word
        (``report_contract.CONFIDENCE_ICONS``), so a hex for "green light" in
        this table would be a second copy of a colour that already has an
        owner — and a fifth thing for 紅藍綠黃 to be confused with."""
        for name in design_tokens.PALETTE:
            self.assertFalse(name.startswith("light_"), name)

    def test_the_three_ballot_hues_survive_the_repaint(self):
        """"語意色保留語意、只校準色階": the roles are still here and still
        readable, whatever their new shade is."""
        self.assertEqual(("affirm", "oppose", "abstain"), design_tokens.STANCE_TOKENS)
        for token in design_tokens.STANCE_TOKENS:
            self.assertIn(token, design_tokens.TEXT_TOKENS, token)


class ContrastFromTokensTest(unittest.TestCase):
    """WCAG AA, computed from the palette.

    The measured table is printed here because a ratio that is only asserted is
    a ratio nobody can quote in a review.
    """

    def measured(self):
        for foreground, background, minimum in design_tokens.CONTRAST_REQUIREMENTS:
            yield (
                foreground,
                background,
                contrast_ratio(
                    design_tokens.MEASURED_COLOURS[foreground],
                    design_tokens.MEASURED_COLOURS[background],
                ),
                minimum,
            )

    def test_every_declared_pair_meets_its_minimum(self):
        print(
            "\n".join(
                "{:>16} on {:<14} {:6.2f}:1  needs {}:1".format(
                    foreground, background, ratio, minimum
                )
                for foreground, background, ratio, minimum in self.measured()
            )
        )
        for foreground, background, ratio, minimum in self.measured():
            self.assertTrue(
                meets(ratio, minimum),
                "{} on {} = {:.4f}:1 (needs {}:1)".format(
                    foreground, background, ratio, minimum
                ),
            )

    def test_a_pair_that_only_rounds_up_to_the_minimum_is_not_accepted(self):
        """The guard above is a floor, not a rounded floor.

        ``#006ffb`` on white is the counter-example that makes the difference
        visible: it measures 4.4999:1, which two decimal places turn into
        "4.50" and a rounding guard would then wave through as AA text
        contrast. Pinned here so the cheaper comparison cannot come back.
        """
        aa_text = 4.5  # WCAG 2.1 AA for body text, which is the spec's number.
        near_miss = contrast_ratio("#ffffff", "#006ffb")

        self.assertLess(near_miss, aa_text)
        self.assertEqual(aa_text, round(near_miss, 2))
        self.assertFalse(meets(near_miss, aa_text))


class TypeScaleTest(unittest.TestCase):
    """The half of the table that is not colour."""

    def test_the_scale_carries_type_spacing_and_radius_families(self):
        for prefix in ("font_", "size_", "space_", "radius_", "line_"):
            self.assertTrue(
                [name for name in design_tokens.SCALE if name.startswith(prefix)],
                prefix,
            )

    def test_the_scale_holds_no_colour(self):
        """Colour lives in the palette; a hue in the scale would be a value with
        no role and no measured minimum."""
        self.assertEqual(
            set(), colour_literals("".join(map(str, design_tokens.SCALE.values())))
        )

    def test_the_body_stack_asks_for_microsoft_jhenghei_first(self):
        """A hard requirement of R-004, and the reason it is checked by position
        rather than by presence: a face listed after the generic UI font is a
        face the machine never reaches for."""
        first = design_tokens.SCALE["font_sans"].split(",")[0]

        self.assertEqual('"Microsoft JhengHei"', first)

    def test_every_face_in_every_stack_is_one_the_machine_already_has(self):
        """Pure system stacks: no ``@font-face``, no ``url()``, nothing to
        download. The generic family at the end is what makes the stack total."""
        for name in ("font_sans", "font_mono"):
            stack = design_tokens.SCALE[name]
            self.assertEqual(set(), colour_literals(stack), name)
            for forbidden in ("url(", "@import", "http", "//"):
                self.assertNotIn(forbidden, stack, "{} {}".format(name, forbidden))
            self.assertIn(stack.rsplit(",", 1)[-1], ("sans-serif", "monospace"), name)


class SingleStylesheetTest(unittest.TestCase):
    """One sheet, built from the table and from nothing else."""

    def setUp(self):
        self.sheet = pages.stylesheet()

    def test_the_room_no_longer_ships_a_stylesheet_of_its_own(self):
        self.assertFalse(hasattr(pages, "LIVE_ROOM_CSS"))

    def test_the_sheet_declares_the_palette_and_the_scale(self):
        for name, value in design_tokens.PALETTE.items():
            self.assertIn("--{}:{};".format(name.replace("_", "-"), value), self.sheet)
        for name, value in design_tokens.SCALE.items():
            self.assertIn("--{}:{};".format(name.replace("_", "-"), value), self.sheet)

    def test_the_sheet_carries_no_dark_mode_block_at_all(self):
        """Acceptance for R-004: the finished sheet does not contain the string,
        which is the only form of "深色模式退場" a reader of the output can
        check. The bare feature name is asserted too, so a repaint cannot bring
        back a light-scheme query and call it a different rule."""
        self.assertNotIn("@media (prefers-color-scheme: dark)", self.sheet)
        self.assertNotIn("prefers-color-scheme", self.sheet)

    def test_the_sheet_declares_its_tokens_exactly_once(self):
        """One ``:root``. A second one is the second palette arriving under
        another name."""
        self.assertEqual(1, self.sheet.count(":root"))

    def test_a_repainted_colour_token_reaches_the_sheet(self):
        original = design_tokens.PALETTE["accent"]
        design_tokens.PALETTE["accent"] = "#123456"
        try:
            self.assertIn("--accent:#123456;", pages.stylesheet())
        finally:
            design_tokens.PALETTE["accent"] = original

    def test_a_resized_type_token_reaches_the_sheet(self):
        self._retuned("size_md", "3.5rem", "--size-md:3.5rem;")

    def test_a_retuned_spacing_token_reaches_the_sheet(self):
        self._retuned("space_5", "9.5rem", "--space-5:9.5rem;")

    def _retuned(self, name, value, expected):
        original = design_tokens.SCALE[name]
        design_tokens.SCALE[name] = value
        try:
            self.assertIn(expected, pages.stylesheet())
        finally:
            design_tokens.SCALE[name] = original

    def test_the_only_colours_in_the_sheet_are_the_palettes_own(self):
        declared = set(design_tokens.PALETTE.values())

        self.assertEqual(declared, colour_literals(self.sheet))

    def test_the_composited_colours_are_measurements_not_paint(self):
        """The flattened glass is how a ratio is computed, not a colour the site
        paints with: shipping it as a custom property would put a second value
        for one surface in the sheet."""
        for token in design_tokens.GLASS:
            self.assertNotIn(design_tokens.MEASURED_COLOURS[token], self.sheet, token)

    def test_no_rule_names_a_colour_at_all(self):
        """Stronger than the set above, which a literal that happens to equal a
        palette value slips through: outside the token block there is no colour
        written out anywhere, so a rule cannot hold even a *correct* copy of a
        hex that the palette already owns."""
        rules = _rules_region(self.sheet)

        self.assertNotIn(":root", rules)
        self.assertEqual(set(), colour_literals(rules))

    def test_every_var_reference_resolves_to_a_declared_token(self):
        declared = {
            "--{}".format(name.replace("_", "-"))
            for name in list(design_tokens.PALETTE) + list(design_tokens.SCALE)
        }
        used = set(re.findall(r"var\((--[\w-]+)\)", self.sheet))

        self.assertEqual(set(), used - declared)

    def test_the_sheet_requests_nothing_from_the_network(self):
        for forbidden in ("url(", "@import", "http:", "https:", "//"):
            self.assertNotIn(forbidden, self.sheet, forbidden)

    def test_every_stance_class_the_room_hands_out_owns_a_declared_token(self):
        handed_out = set(live.STANCE_CLASSES) | {live.UNKNOWN_STANCE_CLASS}

        self.assertEqual(handed_out, set(pages.STANCE_COLOUR_TOKENS))
        for name, token in pages.STANCE_COLOUR_TOKENS.items():
            self.assertIn(token, design_tokens.PALETTE, name)

    def test_the_ballot_hues_the_table_declares_are_the_ones_the_room_hands_out(self):
        """Two modules name these three: the table that holds their colour and
        the room that hands out their classes. This is the join, so a fourth
        ballot position cannot be coloured in one place and not the other."""
        self.assertTrue(
            set(design_tokens.STANCE_TOKENS)
            <= set(pages.STANCE_COLOUR_TOKENS.values())
        )

    def test_only_the_stance_class_r2_leaves_free_is_painted(self):
        """Spec R2 freezes the room's 正／反／無法判斷 text. Owning a token and
        being painted are two different things, and this is the line between
        them: painting one of the three frozen classes changes a colour the
        ticket has no authority over.

        The frozen three are named from ``live.STANCE_CLASSES`` rather than
        derived from ``PAINTED_STANCE_CLASSES``, so growing that tuple turns this
        red instead of turning it vacuous.
        """
        self.assertEqual(("stance-unknown",), tuple(pages.PAINTED_STANCE_CLASSES))
        self.assertTrue(
            set(pages.PAINTED_STANCE_CLASSES) <= set(pages.STANCE_COLOUR_TOKENS)
        )
        self.assertEqual(
            {live.UNKNOWN_STANCE_CLASS}, set(pages.PAINTED_STANCE_CLASSES)
        )

        token = pages.STANCE_COLOUR_TOKENS[live.UNKNOWN_STANCE_CLASS]
        self.assertIn(
            ".{}{{color:var(--{});}}".format(live.UNKNOWN_STANCE_CLASS, token),
            self.sheet,
        )
        for name in live.STANCE_CLASSES:
            self.assertNotIn(".{}{{".format(name), self.sheet, name)

    def test_the_colours_no_rule_reads_are_exactly_the_ones_on_the_record(self):
        """A token that quietly stops being read is dead data; one that quietly
        starts being read is an unauthorised repaint. So the unread set is named
        rather than allowed to drift, and it now has exactly one member:

        * ``affirm`` is declared and measured but applied by nothing, because
          the only elements that could wear it are inside the R2 freeze. It stays
          measured so that whichever ticket is authorised to lift that freeze
          paints with a colour already known to clear AA.

        The glass surface and the four decorative hues were on this list while
        Ticket 01 held the table and Ticket 03 had not yet spent it — the split
        R-004 draws between the authority and the pages. Ticket 03 closed that
        gap: the frost is on the site's own panels and the four hues are the band
        across every header, so a token declared here and read by nothing is once
        again a mistake rather than a schedule.
        """
        declared = {
            "--{}".format(name.replace("_", "-")) for name in design_tokens.PALETTE
        }
        read = set(re.findall(r"var\((--[\w-]+)\)", self.sheet))

        self.assertEqual({"--affirm"}, declared - read)

    def test_every_provider_family_the_roster_declares_has_a_stripe_of_its_own(self):
        """The stripe is how a reader tells the families apart; an eighth seat on
        a fourth provider must fail here rather than reach a page unmarked."""
        families = {
            identity.provider for identity in seats.seat_identities().values()
        }

        self.assertEqual(families, set(pages.PROVIDER_STRIPE_TOKENS))
        for provider, token in pages.PROVIDER_STRIPE_TOKENS.items():
            self.assertIn(token, design_tokens.PALETTE, provider)
            self.assertIn(
                ".message.{}{{border-left-color:var(--{});}}".format(
                    provider, token.replace("_", "-")
                ),
                self.sheet,
            )


class LightSemanticsTest(unittest.TestCase):
    """The five lights keep their meaning, and keep it in one place.

    Their colour is carried by the authority's own icon beside the word — the
    markup has no per-level class and gains none here, because that markup is
    frozen (Spec R2). So there is no second copy of a light's colour to drift.
    """

    def test_every_light_the_contract_declares_has_an_icon_and_a_word(self):
        from hoya_market_agents.report_contract import (
            CONFIDENCE_ICONS,
            CONFIDENCE_LEVELS,
        )

        self.assertEqual(set(CONFIDENCE_LEVELS), set(pages.CONFIDENCE_WORDS))
        for level in CONFIDENCE_LEVELS:
            self.assertTrue(CONFIDENCE_ICONS[level].strip(), level)
            self.assertTrue(pages.CONFIDENCE_WORDS[level].strip(), level)

    def test_a_light_is_rendered_with_no_colour_class_of_its_own(self):
        from hoya_market_agents.report_contract import CONFIDENCE_LEVELS

        for level in CONFIDENCE_LEVELS:
            self.assertEqual(
                '<span class="light">', pages._light(level)[: len('<span class="light">')]
            )


if __name__ == "__main__":
    unittest.main()
