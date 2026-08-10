"""The table every painted surface in this project is being moved onto.

The web app's stylesheet (:mod:`~hoya_market_agents.webapp.pages`) takes every
colour from here and writes none of its own: a hex in that module is a test
failure, and ``tests/test_design_tokens.py`` asserts the finished stylesheet
contains no colour this table does not hold.

**Not yet the whole site.** The two offline renderers
(:mod:`~hoya_market_agents.report_renderer` and
:mod:`~hoya_market_agents.report_audit_renderer`) still carry palettes of their
own; moving them onto this table is Ticket 06's work, and until that lands a
colour changed here does not reach a report rendered by them. This module is
the authority by design and, for the web app, already by reach — the claim that
there is exactly one copy of every colour in the project becomes true when
Ticket 06 closes, and not before.

**One palette, not two.** Dark mode is retired (Spec R-004): the site is white,
whatever the operating system prefers, so there is no ``@media
(prefers-color-scheme: dark)`` block and no second set of values to keep in step
with the first. That halves the surfaces the contrast test has to measure and
removes the class of bug where a colour was calibrated in one palette only.

**The design read.** Google's own neutrals — a grey-50 canvas, white cards,
hairline rules, generous space — with four brand hues used as decoration and
nothing else.

**Decoration and meaning are different vocabularies.** :data:`DECOR_INK` holds
紅藍綠黃, which say nothing; :data:`STANCE_TOKENS` holds 正／反／棄, which say
everything. They are named apart (``google_*`` versus the ballot's own words),
their values are held apart, and a test asserts both — because a decorative red
that is the ballot's red is a page where a chip looks like a vote. The five
confidence lights are deliberately absent: their colour is the authority's own
icon beside the word (``report_contract.CONFIDENCE_ICONS``), so a hex for "green
light" here would be a second copy of a colour that already has an owner.

**Glass has two values, and both are real.** A translucent surface is painted
with :data:`PALETTE`'s ``rgba(...)`` and *read* as the composite of that over
whatever sits behind it. Only the composite can be put through a contrast ratio,
because it is the colour the eye actually receives — so :data:`MEASURED_COLOURS`
flattens every glass token over its backdrop and the contrast test measures
that. The blur itself is native ``backdrop-filter`` (:data:`SCALE`'s
``glass_blur``): no image, no asset, nothing fetched.

**Every colour answers to a minimum.** Each token is placed in exactly one role
— background, text, line, decoration, or the ink that goes on the accent — and
:data:`CONTRAST_REQUIREMENTS` is *generated* from those roles rather than listed,
so a token added to the palette cannot reach a page without a measured minimum
of its own.
"""

import re

# -- compositing ------------------------------------------------------------


def composite(translucent, backdrop):
    """Return the opaque ``#rrggbb`` a reader sees through ``translucent``.

    The CSS source-over model: each channel is the front colour weighted by its
    alpha plus the backdrop weighted by what is left. This exists so a glass
    surface can be *measured*; a contrast ratio taken against ``rgba(...)``
    would be a ratio of a colour nobody looks at.
    """
    red, green, blue, alpha = [
        float(part)
        for part in re.match(r"rgba?\(([^)]*)\)", translucent).group(1).split(",")
    ]
    behind = _channels(backdrop)
    return "#" + "".join(
        "{:02x}".format(round(front * alpha + under * (1 - alpha)))
        for front, under in zip((red, green, blue), behind)
    )


def _channels(colour):
    colour = colour.lstrip("#")
    return [int(colour[index:index + 2], 16) for index in (0, 2, 4)]


# -- the palette ------------------------------------------------------------

# The colour of every surface, word and line on the site. Values that begin
# ``rgba`` are glass and are listed in :data:`GLASS` beside what they sit on.
PALETTE = {
    # Canvas and cards. The canvas is the grey the cards are told apart from,
    # and it doubles as the well colour inside a card (a ``<pre>``, an evidence
    # tile, a chat bubble), which is why it is not pure white.
    "page": "#f8f9fa",
    "surface": "#ffffff",
    # The frosted panel: white at 72% over the canvas. Painted with the value
    # below and measured as its composite — see :data:`MEASURED_COLOURS`.
    "glass_surface": "rgba(255,255,255,0.72)",
    "text": "#202124",
    "muted": "#5f6368",
    # Anchor text and the brand fill are one blue today but two decisions: the
    # colour of a link answers to a reader looking for what is clickable, the
    # accent to a page looking for its own voice.
    "link": "#1967d2",
    "accent": "#1967d2",
    "accent_text": "#ffffff",
    # Hairlines. Google's own rules are lighter than this; these are as light as
    # a line can be and still clear the 3:1 that :data:`LINE_MINIMUM` holds
    # every non-text boundary to.
    "border": "#80868b",
    # One colour per ballot position, calibrated to the new canvas but keeping
    # the meaning they always had (Spec R-004: 語意色保留語意、只校準色階). They
    # are words on the page, never the only signal: a bubble also names its
    # stance, so a reader who cannot tell the three apart still reads the same
    # thing.
    "affirm": "#137333",
    "oppose": "#c5221f",
    "abstain": "#8a5000",
    # The settings page's two outcomes. Separate tokens from the stance colours
    # even where the hue matches today, because they answer a different question
    # — "this edit was refused" is not "this seat is bearish" — and neither
    # should move when the other is repainted. On the page they are never the
    # only signal: a refusal is a sentence beside the control, and the control
    # carries ``aria-invalid``.
    "danger": "#c5221f",
    "success": "#137333",
    # The two provider families that do not read the accent. The chat stripe is
    # how a reader tells three Claude seats from three Codex ones without
    # reading seven names, so it is a semantic hue rather than decoration — see
    # ``pages.PROVIDER_STRIPE_TOKENS``.
    "provider_claude": "#b06000",
    "provider_gemini": "#6d28d9",
    # Decoration, and nothing but decoration. Google's four brand hues, used for
    # chips, bars and dots that carry no meaning a reader has to decode. Named
    # apart from every token above so that "the green one" is never ambiguous.
    # Google's own blue 600 is #1a73e8, which clears its ink by 0.01 — a margin
    # that any future nudge erases. This is that hue one step down: still the
    # brand blue, with room to be repainted.
    "google_blue": "#186ede",
    "google_red": "#d93025",
    "google_yellow": "#fbbc04",
    "google_green": "#188038",
}

# ``glass token`` → the token whose colour sits behind it. A glass surface over
# another glass surface is not a thing this site does, and a test pins that, so
# one round of compositing is always enough.
GLASS = {
    "glass_surface": "page",
}

# What the eye receives: the palette with every glass token flattened over its
# backdrop. This is the table contrast is computed from, and the reason a
# frosted panel can be held to the same AA minimum as a solid one.
#
# Derived, never written out: a composite typed by hand is the second copy of a
# colour this module exists to prevent.
def _flattened(palette, glass):
    measured = dict(palette)
    for token, backdrop in glass.items():
        measured[token] = composite(palette[token], palette[backdrop])
    return measured


MEASURED_COLOURS = _flattened(PALETTE, GLASS)

# -- roles ------------------------------------------------------------------

# What each colour token is *for*, which is what decides the ratio it answers
# to. Every token is in exactly one of these, and a test asserts that — so a
# further colour cannot reach a page without a measured minimum, which is the
# only way this table stays honest.
#
# Glass is a background like any other, because text is read on it like any
# other; it is only the *measurement* that differs.
BACKGROUND_TOKENS = ("page", "surface", "glass_surface")
TEXT_TOKENS = (
    "text",
    "muted",
    "link",
    "accent",
    "affirm",
    "oppose",
    "abstain",
    "danger",
    "success",
)
LINE_TOKENS = ("border", "provider_claude", "provider_gemini")

# The ink that goes on the accent fill, which is the one pairing that is neither
# text-on-background nor a line: a button's label on a button's colour.
INK_ON_ACCENT = "accent_text"

# ``decorative fill`` → the ink that must stay readable on it. This is data
# rather than one rule because a yellow and a blue do not take the same ink, and
# because the pairing is what the pages downstream need to know: a chip is a
# fill plus the word on it.
#
# It is also how a purely decorative hue is held to AA honestly. Asking a brand
# yellow to clear 3:1 against white is asking it to stop being that yellow;
# asking the word printed on it to be readable is what a reader actually needs.
DECOR_INK = {
    "google_blue": "accent_text",
    "google_red": "accent_text",
    "google_green": "accent_text",
    "google_yellow": "text",
}
DECOR_TOKENS = tuple(sorted(DECOR_INK))

# The three that carry a ballot's meaning, and the two that carry a save's. Both
# are named here so the test that keeps decoration away from meaning has
# something to compare against, and so a reader of this module can see at a
# glance which hues are load-bearing.
STANCE_TOKENS = ("affirm", "oppose", "abstain")
OUTCOME_TOKENS = ("danger", "success")

# -- the minimums -----------------------------------------------------------

# 4.5:1 is AA for body text. 3:1 is AA for the things that are not text but
# still have to be seen — the hairlines between one control and the page, and
# the stripe that names a chat bubble's provider family.
TEXT_MINIMUM = 4.5
LINE_MINIMUM = 3.0

# ``(foreground, background, minimum ratio)``, generated from the roles above
# rather than listed, because a list is a second place to forget a token.
# ``accent`` is in :data:`TEXT_TOKENS` and therefore answers to the text
# minimum: it is the colour of a tab, a summary and a seat's status, not only of
# a button's fill.
CONTRAST_REQUIREMENTS = tuple(
    [
        (token, background, TEXT_MINIMUM)
        for token in TEXT_TOKENS
        for background in BACKGROUND_TOKENS
    ]
    + [
        (token, background, LINE_MINIMUM)
        for token in LINE_TOKENS
        for background in BACKGROUND_TOKENS
    ]
    + [(INK_ON_ACCENT, "accent", TEXT_MINIMUM)]
    + [(ink, token, TEXT_MINIMUM) for token, ink in sorted(DECOR_INK.items())]
)

# -- the scale --------------------------------------------------------------

# The colourless half of the table: type, spacing, radii, the two font stacks
# and the one blur.
#
# **The stacks are the operating system's own faces.** No ``@import``, no
# ``url()``, nothing from a CDN: every name is a font already installed on the
# machine the page opens on, with Microsoft JhengHei first because that is the
# face this project is read in (Spec R-004) and a face listed after ``system-ui``
# is a face the machine never reaches for.
#
# The spacing scale is the rhythm — padding, gaps, margins — at dashboard
# density (4px to 32px). A component's own dimension that is not rhythm (the
# feed's maximum height, a grid column's minimum width) stays with the rule that
# needs it, because a scale that also holds one panel's height is not a scale.
SCALE = {
    "font_sans": (
        '"Microsoft JhengHei",system-ui,-apple-system,"Segoe UI",'
        '"PingFang TC","Noto Sans TC","Helvetica Neue",sans-serif'
    ),
    "font_mono": (
        'ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,'
        '"Liberation Mono",monospace'
    ),
    # 12px is the floor: below that a label stops being readable, and every one
    # of these is a real label rather than a decoration.
    "size_2xs": ".75rem",
    "size_xs": ".8rem",
    "size_sm": ".88rem",
    "size_md": "1rem",
    "size_lg": "1.15rem",
    "size_xl": "1.5rem",
    "size_2xl": "2.25rem",
    "line_tight": "1.25",
    "line_base": "1.55",
    "space_1": ".25rem",
    "space_2": ".375rem",
    "space_3": ".5rem",
    "space_4": ".75rem",
    "space_5": "1rem",
    "space_6": "1.5rem",
    "space_7": "2rem",
    "radius_sm": ".25rem",
    "radius_md": ".5rem",
    "radius_lg": ".75rem",
    "radius_pill": "999px",
    # How far the frosted panel blurs what is behind it. A length, because
    # ``backdrop-filter:blur()`` takes one and because a token that is not a
    # length would not survive being read by a rule.
    "glass_blur": "12px",
    # The one width every page's header, body and footer line up on.
    "shell": "86rem",
    # A bubble's entrance, and the one opacity a disabled control dims by.
    "motion_fast": ".18s",
    "dim": ".55",
}
