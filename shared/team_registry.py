"""One answer to "what colour is this club", for both products.

TWO LAYERS, and keeping them apart is the whole point of this module.

    Layer 1  IDENTITY   what colour IS this club?   -> get_team_colors()
    Layer 2  RENDERING  can I draw it HERE?         -> choose_for_background()

Layer 1 knows nothing about backgrounds. Internazionale's primary is stored as
pure black because Internazionale's primary is pure black; that it cannot be
drawn on a dark canvas is not a fact about Inter, it is a fact about the canvas.
Layer 2 owns that, per product, at draw time.

Merging the two produced a genuinely bad analysis once: 458 clubs were sorted
by CONTRAST and the buckets were then described as though they measured
CORRECTNESS. They do not. Real Madrid arrives from the feed as `#0066FF` - a
saturated, perfectly drawable blue, and the wrong colour for Real Madrid. A
club can pass every rendering test and still be wrong, so rendering can never
be evidence of correctness.

WHO WINS

    authored clubs   the authored value, outright. The feed gets no vote.
    tail clubs       the feed, as provider of last resort.
    neither          NEUTRAL, which is nobody's kit and is meant to look it.

The feed keeps two jobs and neither is resolution: during authoring a
disagreement means one of us is wrong and is cheap to check, and afterwards a
CHANGE in the feed value is a rebrand signal worth re-checking.

STORAGE

Authored entries live in the MotherDuck `app_config` mirror beside the Data
Manager config, so an edit reaches both products with no commit and no deploy -
the property that let a Team Finder run appear in the live app within minutes.
A local JSON file is the fallback and the offline authoring surface.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace

from shared.colors import lighten_hsl, wcag_contrast

# Nobody's kit. The point of a fallback is to look like a fallback: the two it
# replaces were `#6CABDD`, which is Manchester City's actual colour, and
# `#888888`. A club with no colour on file should not quietly wear Man City's.
NEUTRAL = "#8A94A6"

REGISTRY_KEY = "team_registry"
_LOCAL_PATH = os.path.join(os.path.dirname(__file__), "team_registry.json")

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Provenance values. Recorded per club, and reported by the resolver, because
# "Paderborn silently became grey" is the failure this module exists to end.
AUTHORED = "authored"
FEED = "feed"
INHERITED = "inherited"
NEUTRAL_SRC = "neutral"


def is_hex(value):
    """True for a well-formed #RRGGBB string. Everything else is 'no colour'."""
    return isinstance(value, str) and bool(_HEX.match(value.strip()))


@dataclass(frozen=True)
class TeamColors:
    """Layer 1. What this club's colours ARE, with no reference to any canvas."""

    team_id: str
    name: str
    primary: str
    secondary: str | None
    provenance: str
    source_url: str | None = None

    @property
    def authored(self):
        return self.provenance == AUTHORED


@dataclass(frozen=True)
class ColorDecision:
    """Layer 2. What the renderer actually drew, and why it chose that.

    `reason` is meant to be logged or surfaced. A resolver that silently
    substitutes a colour is indistinguishable from one that is broken.
    """

    color: str
    used: str          # 'primary' | 'secondary' | 'neutral'
    lightened: bool
    reason: str


# --------------------------------------------------------------------------
# Layer 1: identity
# --------------------------------------------------------------------------


def load_registry(con=None, path=_LOCAL_PATH):
    """Authored entries, keyed by team_id. MotherDuck first, local file second.

    **ANY CHART MUST PASS `con`.** Without it this reads the local JSON, which
    ships via git and is therefore whatever was true at the last deploy. Edits
    made in the Data Manager land in the MotherDuck mirror, so a chart that
    calls `load_registry()` bare will silently never see them - and the whole
    point of the mirror is that a colour fix goes live without a deploy.

    `shared.motherduck._load_config` is the pattern to copy: mirror first, file
    as the fallback, and log which one answered.

    Returns {} rather than raising when neither source has anything - an empty
    registry is a valid state (every club falls through to the feed), and it is
    what a fresh install looks like.
    """
    if con is not None:
        try:
            from shared.config_store import CONFIG_TABLE

            row = con.execute(
                f"SELECT value FROM {CONFIG_TABLE} WHERE key = ?", [REGISTRY_KEY]
            ).fetchone()
            if row and row[0]:
                return json.loads(row[0])
        except Exception:
            # Fall through to the file. A registry that hard-fails on a
            # MotherDuck hiccup would take every chart down with it.
            pass

    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def save_registry(registry, con=None, path=_LOCAL_PATH):
    """Write the authored registry to the local file and, if given, MotherDuck.

    The file is written first and unconditionally, so an authoring session is
    never lost to a network failure.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False, sort_keys=True)

    if con is not None:
        from shared.config_store import CONFIG_DDL, CONFIG_TABLE
        from datetime import datetime, timezone

        con.execute(CONFIG_DDL)
        con.execute(
            f"INSERT OR REPLACE INTO {CONFIG_TABLE} VALUES (?, ?, ?)",
            [
                REGISTRY_KEY,
                json.dumps(registry, ensure_ascii=False, separators=(",", ":")),
                datetime.now(timezone.utc),
            ],
        )
    return registry


def get_team_colors(team_id, name=None, feed_color=None, registry=None,
                    parent_colors=None):
    """This club's colours. Contrast plays NO part in this function.

    `parent_colors` covers the Women's-team case: 20 of the 38 women's sides
    with no colour of their own resolve by stripping " Women" and inheriting.
    It must be the resolved TeamColors of the parent, not the parent's raw feed
    value - otherwise Real Madrid Women inherits `#0066FF`, the very value the
    override exists to correct.
    """
    registry = registry if registry is not None else {}
    entry = registry.get(team_id) or {}

    primary = entry.get("primary")
    if is_hex(primary):
        return TeamColors(
            team_id=team_id,
            name=entry.get("name") or name or team_id,
            primary=primary.strip(),
            secondary=(entry.get("secondary") or "").strip() or None,
            provenance=AUTHORED,
            source_url=entry.get("source_url"),
        )

    if is_hex(feed_color):
        return TeamColors(
            team_id=team_id,
            name=name or team_id,
            primary=feed_color.strip(),
            secondary=None,          # the feed carries exactly one colour
            provenance=FEED,
        )

    if parent_colors is not None:
        return replace(
            parent_colors,
            team_id=team_id,
            name=name or team_id,
            provenance=INHERITED,
        )

    return TeamColors(
        team_id=team_id,
        name=name or team_id,
        primary=NEUTRAL,
        secondary=None,
        provenance=NEUTRAL_SRC,
    )


WOMENS_SUFFIX = " Women"


def resolve_all(teams, registry=None):
    """Resolve a whole club list, applying parent inheritance where it applies.

    `teams` is an iterable of (team_id, name, feed_color). Returns
    {team_id: TeamColors}.

    Inheritance exists for Women's sides, which the feed frequently leaves with
    no colour at all - 38 of the 86 colourless clubs. It runs in a SECOND pass,
    over already-resolved parents, so a side inherits the parent's authored
    value rather than the parent's raw feed value. Real Madrid Women is the
    case that makes this non-optional: inheriting the feed would hand her the
    `#0066FF` blue that the override exists to correct.

    Clubs with no men's counterpart in the data - Angel City, Bay FC, Kansas
    City Current and the rest of the NWSL - simply do not inherit; they are
    independent clubs and are authored directly.
    """
    registry = registry if registry is not None else {}
    teams = list(teams)

    resolved = {
        tid: get_team_colors(tid, name, feed, registry=registry)
        for tid, name, feed in teams
    }
    by_name = {name: tid for tid, name, _ in teams}

    for tid, name, feed in teams:
        colors = resolved[tid]
        if colors.provenance != NEUTRAL_SRC or not name.endswith(WOMENS_SUFFIX):
            continue
        parent_id = by_name.get(name[: -len(WOMENS_SUFFIX)].strip())
        parent = resolved.get(parent_id) if parent_id else None
        if parent is None or parent.provenance == NEUTRAL_SRC:
            continue
        resolved[tid] = replace(parent, team_id=tid, name=name,
                                provenance=INHERITED)
    return resolved


# --------------------------------------------------------------------------
# Layer 2: rendering
# --------------------------------------------------------------------------


def _shift_needed(color, bg_color, min_ratio, max_steps=10):
    """How many lightening steps to clear `min_ratio`. None if unreachable.

    0 means the colour already passes and would be drawn untouched.
    """
    if wcag_contrast(color, bg_color) >= min_ratio:
        return 0
    current = color
    for step in range(1, max_steps + 1):
        current = lighten_hsl(current, 0.08)
        if wcag_contrast(current, bg_color) >= min_ratio:
            return step
    return None


# Below this there is no hue for lightening to preserve, so the result is grey
# whatever the starting colour was. `#000000` and `#121212` both sit at 0.00.
ACHROMATIC_SAT = 0.15


def _saturation(color):
    import colorsys

    from shared.colors import hex_to_rgb

    _, _, s = colorsys.rgb_to_hls(*hex_to_rgb(color))
    return s


def _separated(color, against, bg_color, min_distance):
    """True if two drawn colours differ enough to be told apart.

    Perceptual DISTANCE, not luminance. This originally compared only each
    colour's contrast ratio against the background, which is hue-blind: red
    and blue of similar lightness read as a clash, and measured over 479 real
    matchups the swap fired on 58% of them - Monaco drawn white against
    Strasbourg, Wolfsburg white against Stuttgart. RGB distance at the old
    guard's threshold (150) keeps the true clashes (Chelsea-Brighton lift to
    21 apart) and lets red-versus-blue through.
    """
    if not against:
        return True
    from shared.colors import color_distance
    return color_distance(color, against) >= min_distance


def choose_for_background(colors, bg_color, against=None, min_ratio=3.5,
                          min_distance=150, max_shift=3):
    """Pick what to actually draw, on THIS background, against THAT other line.

    primary -> secondary -> lighten, in that order:

    1. If the primary reads here and is distinct from `against`, use it. This
       is the overwhelming majority and nothing is touched.
    2. If the primary would have to shift more than `max_shift` steps to be
       legible - or cannot get there at all - reach for the secondary. Pure
       black is the case that motivates this: it has zero saturation, so
       lightening has no hue to preserve and can only produce grey. Paderborn
       came out `#787878`, visible and unrecognisable.
    3. Only if there is no usable secondary, lighten the primary as before.

    `max_shift` is deliberately a shift budget rather than a contrast floor.
    "Reach for the secondary when the primary would have to move too far" is
    the rule; how far it has to move is the thing to measure.
    """
    p_shift = _shift_needed(colors.primary, bg_color, min_ratio)
    p_ok = p_shift is not None and p_shift <= max_shift
    p_distinct = _separated(colors.primary, against, bg_color, min_distance)
    p_achromatic = _saturation(colors.primary) <= ACHROMATIC_SAT

    if p_shift == 0 and p_distinct:
        return ColorDecision(colors.primary, "primary", False,
                             "primary reads as-is")

    if colors.secondary and is_hex(colors.secondary):
        s_shift = _shift_needed(colors.secondary, bg_color, min_ratio)
        s_ok = s_shift is not None and s_shift <= max_shift
        s_distinct = _separated(colors.secondary, against, bg_color,
                                min_distance)
        # The secondary exists to RESOLVE A CLASH between two teams. It is not
        # a general-purpose fix for a dark primary: a navy lightened five steps
        # is still recognisably navy, so a single-team chart should simply
        # lighten. Wiring the shift budget to background legibility as well
        # made Chelsea, Everton and Internazionale all draw WHITE, because dark
        # blue is everywhere in football and no navy clears the floor in three
        # steps.
        #
        # The one background case that does justify the secondary is an
        # achromatic primary, which lightening cannot preserve at all.
        clash = against is not None and not p_distinct
        primary_in_trouble = clash or p_achromatic or p_shift is None
        # The shift budget asks "can this colour survive being lightened?".
        # For a colour with no saturation the answer is never - lightening
        # holds hue and saturation while raising lightness, and there is no
        # hue to hold, so every step walks it up the grey axis. Black CAN be
        # made legible; it cannot be made to still read as black. #787878 is
        # not a lighter Internazionale, it is grey.
        #
        # So an achromatic primary gives way to any chromatic secondary that
        # can reach the floor, budget or not - a navy lightened four steps is
        # still recognisably navy, which is more than the primary can manage.
        # Without this, Paderborn (black primary, navy secondary needing 4
        # steps against a budget of 3) fell back to lightening the primary and
        # rendered #787878 again.
        if p_achromatic and s_shift is not None and s_distinct:
            s_ok = True
        if primary_in_trouble and s_ok and s_distinct:
            if s_shift == 0:
                return ColorDecision(colors.secondary, "secondary", False,
                                     _why(p_ok, p_distinct, p_shift))
            return ColorDecision(
                _lighten_to(colors.secondary, bg_color, min_ratio),
                "secondary", True,
                _why(p_ok, p_distinct, p_shift) + "; secondary lightened")

    if p_shift is None:
        return ColorDecision("#FFFFFF", "primary", True,
                             "primary cannot reach the contrast floor")
    if p_achromatic:
        # Reachable, but only by becoming grey - and grey is nobody's colour.
        # White is the honest fallback for a black club with no secondary on
        # file: it is legible, and for almost every black-kitted side it is
        # the other half of the strip anyway. Affects 18 tail clubs whose feed
        # colour is #000000 and which have no authored entry.
        return ColorDecision("#FFFFFF", "primary", True,
                             "achromatic primary; grey would preserve nothing")
    if p_shift == 0:
        return ColorDecision(colors.primary, "primary", False,
                             "primary reads; no distinct alternative available")
    return ColorDecision(_lighten_to(colors.primary, bg_color, min_ratio),
                         "primary", True,
                         f"primary lightened {p_shift} step(s); "
                         f"no usable secondary")


def _why(p_ok, p_distinct, p_shift):
    if not p_distinct:
        return "primary too close to the opposing line"
    if p_shift is None:
        return "primary cannot reach the contrast floor"
    return f"primary would need {p_shift} steps"


def _lighten_to(color, bg_color, min_ratio, max_steps=10):
    current = color
    for _ in range(max_steps):
        if wcag_contrast(current, bg_color) >= min_ratio:
            return current
        current = lighten_hsl(current, 0.08)
    return current if wcag_contrast(current, bg_color) >= min_ratio else "#FFFFFF"
