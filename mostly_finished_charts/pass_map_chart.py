"""Pass Map - every pass as a line, from where it started to where it ended.

The mark never changes: one line, one real pass, never an average. An averaged
arrow invents a finding the data does not contain. What changes across the range
is opacity and weight, because the same chart has to hold 27 passes (a player in
one match) and 17,500 (a team over a season) - a 500x span that is not a corner
case but the normal operating range.

Both ends are legitimate outputs. A three-line pass map is real, not degenerate:
many of the most useful filters select tiny subsets, and the caption carries the
weight the lines cannot. The dense end is equally real - 20,000 faint lines stop
reading as individual passes and start reading as a density field, which is
genuinely informative about build-up corridors and territorial bias. The chart
never refuses to draw either one.

THE PITCH HAS NO GRASS, and that is deliberate. The shot chart's #1E5631 green
works there because shot markers are discrete and sparse. Here thousands of
translucent lines composite with the ground, and two saturated hues mixing
produce mud - measured: 164 of 194 registry team colours fall below 3.5:1
against the green, against 100 of 194 on this dark panel, and the contrast guard
has to lighten so far to rescue them that the brand colour is destroyed
(Liverpool #C8102E -> #F58396 on grass, -> #EC1437 here). One chroma on the
chart, and it belongs to the team.

Coordinates are attack-normalised by the feed with no half-time flip, so every
team attacks towards x=100 and multi-game aggregation needs no correction. Do
NOT port the shot chart's mean-x mirror heuristic - see build_pass_map.
"""
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, FancyBboxPatch
from mplsoccer import Pitch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import (BG_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
                           add_cbs_footer, fit_fontsize)
from shared.colors import ensure_line_contrast

# The pitch panel sits LIGHTER than the page, not darker. Both directions give
# the same 1.15:1 separation from BG_COLOR, but a surface lighter than its
# ground reads as RAISED and one darker reads as a hole - and the hole is what
# made the chart read "dark on dark" (user, 2026-09-10).
#
# It does not go lighter than this. The contrast guard lifts each club's colour
# to 3.5:1 against whatever the panel is, so a lighter panel forces a lighter
# team colour: at #293648 Liverpool's #C8102E is pushed to #F25E77, which is
# pink. At #222E40 it lands on #EF3957 and stays red. Brand survival is the
# ceiling here, the same constraint that ruled out the green pitch.
PANEL_COLOR = '#222E40'
PITCH_LINE = '#66809E'

# Player identity slots, for 2-3 selected players. Validated against the panel
# on the ALL-PAIRS test - the right test here, because any two players' lines
# can cross anywhere on the pitch, so every pair must separate, not just
# neighbours in a legend. All five checks pass: worst CVD dE 11.8 deutan / 8.7
# tritan, normal-vision 19.4, all three inside the dark lightness band.
#
# THREE IS A MEASUREMENT, NOT A PREFERENCE. The best fourth slot found
# (#3FA45C) lands at dE 6.2 deutan / 3.2 tritan against the pink - inside the
# band that is legal only with a secondary encoding, and this chart has none
# left to spend: line style already carries completion, and a 1px line cannot
# take texture. The picker refuses a fourth and says why rather than
# truncating silently.
PLAYER_SLOTS = ('#2F97CC', '#BC7F12', '#CE4C82')
MAX_PLAYERS = len(PLAYER_SLOTS)


# ── The density function ─────────────────────────────────────────────────────
# Anchored on measured scope sizes rather than picked by eye:
#   player - one match     median   27   (max   180)
#   team   - one match     median  461   (max 1,061)
#   player - one season    median  333   (max 3,583)
#   team   - one season           ~17,500
# Interpolated in log10(n) and clipped outside, so two similar selections differ
# smoothly instead of stepping. The top anchor is set where the density field
# stops SATURATING: above roughly 1/alpha overlapping lines a pixel is already
# fully the team colour and further passes add nothing, so an alpha that looks
# reasonable at 2,000 throws away all structure at 20,000.
_N_ANCHORS = np.log10([20, 500, 2000, 20000])
_ALPHA_ANCHORS = [0.90, 0.36, 0.16, 0.04]
_WIDTH_ANCHORS = [1.90, 1.15, 0.85, 0.50]

# When colour carries PLAYER IDENTITY the curve changes, because translucent
# lines COMPOSITE. Measured on the real slots: at alpha 0.16 every pair blends
# to a hue matching neither source - orange over pink lands at h=359 against
# sources at 38 and 335 - and it only resolves near alpha 0.85, where the top
# line wins outright. So no choice of three colours fixes this; a cold critique
# proposed re-picking the third slot and it would not have worked.
# Identity beats density here: alpha up, lines thinner so they overlap less.
_ALPHA_ANCHORS_ID = [0.98, 0.93, 0.86, 0.78]
_WIDTH_ANCHORS_ID = [1.60, 0.80, 0.45, 0.30]

# Above this, arrowheads stop being drawn. Direction only needs to survive while
# the individual pass is the unit of reading - nobody traces one pass among
# 17,500, they read texture - so an encoding that degrades at high n degrades
# exactly where it has already stopped mattering. The threshold sits above the
# 90th-percentile team-match (630) so a whole match keeps its heads.
ARROW_MAX_N = 800

# Incomplete passes. The gap has to be at least as long as the mark or the dash
# closes up at chart scale and an incomplete pass reads as a completed one -
# which is the single most misleading thing this chart could do.
_DASH = (0, (3.4, 1.9))

# Arrowhead length in Opta length units, capped at 45% of a short pass so a
# five-yard ball is not all head.
HEAD_LEN = 2.4

# ONE swatch vocabulary. The bottom-left key was 36x2.5px and the leading-
# passer chip 25x4.2px - same page, same job ("this colour/style means X"),
# 1.7x the weight and 0.7x the length, in opposite corners.
SWATCH_W, SWATCH_LW = 0.020, 3.0


def density_params(n, identity=False):
    """(alpha, linewidth) for n drawn passes. `identity` = colour means WHO."""
    if n <= 0:
        return (0.95, 1.6) if identity else (0.9, 1.9)
    ln = np.log10(max(n, 1))
    a = _ALPHA_ANCHORS_ID if identity else _ALPHA_ANCHORS
    w = _WIDTH_ANCHORS_ID if identity else _WIDTH_ANCHORS
    return float(np.interp(ln, _N_ANCHORS, a)), float(np.interp(ln, _N_ANCHORS, w))


def resolve_colors(team_color, players=None):
    """Map each drawn pass to a colour, and return the legend entries with it.

    Colour means PLAYER IDENTITY in every case, never sometimes-completion. A
    channel that changed meaning based on a selection made elsewhere in the
    sidebar would mislead without ever looking wrong. One player takes the team
    colour; two or three take the validated slots.

    Every colour is lifted to 3.5:1 against the pitch panel first. The lines
    ARE the team colour here, so a dark navy club that merely looks moody on a
    shot chart is invisible on this one.
    """
    players = list(players or [])
    if len(players) >= 2:
        table = {p: ensure_line_contrast(PLAYER_SLOTS[i], PANEL_COLOR)
                 for i, p in enumerate(players[:MAX_PLAYERS])}
        legend = list(table.items())

        def color_for(df):
            return [table.get(p, TEXT_MUTED) for p in df['passer']]
    else:
        c = ensure_line_contrast(team_color or '#888888', PANEL_COLOR)
        legend = []

        def color_for(df):
            return [c] * len(df)
    return color_for, legend


def draw_passes(ax, df, color_for, alpha=None, width=None, arrows=None,
                identity=False):
    """Draw one line per pass.

    Completion is LINE STYLE - solid against dashed - because colour is spent
    on identity. Incomplete sits underneath: at low n the completed passes are
    what the reader traces, and at high n the order stops being visible at all.
    """
    if df.empty:
        return
    n = len(df)
    a, w = density_params(n, identity)
    alpha = a if alpha is None else alpha
    width = w if width is None else width
    arrows = (n <= ARROW_MAX_N) if arrows is None else arrows

    if identity and len(df) > 1:
        df = df.sample(frac=1.0, random_state=17)
    colors = np.asarray(color_for(df))
    done = df['completed'].to_numpy(dtype=bool)

    for mask, style, z in ((~done, _DASH, 4), (done, 'solid', 5)):
        if not mask.any():
            continue
        sub = df[mask]
        x0 = sub['EventX'].to_numpy(float)
        y0 = sub['EventY'].to_numpy(float)
        x1 = sub['PassEndX'].to_numpy(float)
        y1 = sub['PassEndY'].to_numpy(float)
        c = colors[mask]

        if arrows:
            # SHAFT and HEAD are drawn separately, and that is not fussiness.
            # `quiver` is a FILLED PolyCollection with no edge colour, so a
            # linestyle passed to it styles an edge that is never drawn -
            # get_linestyle() comes back as [(0.0, [0.0, 0.0])], the dash
            # silently discarded. That inverted the whole encoding: completion
            # rendered only ABOVE the arrow threshold, where no one can trace a
            # single pass, and not at all below it, where they can. 99.1% of
            # team-matches sit below it, so every pass read as completed.
            #
            # So the shaft is a LineCollection, which honours linestyle, and
            # quiver draws only the last few units as the head.
            L = np.hypot(x1 - x0, y1 - y0)
            safe = np.where(L > 1e-9, L, 1.0)
            ux, uy = (x1 - x0) / safe, (y1 - y0) / safe
            head = np.minimum(HEAD_LEN, L * 0.45)
            hx, hy = x1 - ux * head, y1 - uy * head
            segs = np.stack([np.column_stack([x0, y0]),
                             np.column_stack([hx, hy])], axis=1)
            ax.add_collection(LineCollection(
                segs, colors=c, linewidths=width, alpha=alpha,
                linestyles=style, zorder=z, capstyle='butt'))
            ax.quiver(hx, hy, ux * head, uy * head,
                      angles='xy', scale_units='xy', scale=1,
                      color=c, alpha=alpha, width=width * 0.0016,
                      headwidth=4.2, headlength=5.0, headaxislength=4.6,
                      zorder=z)
        else:
            segs = np.stack([np.column_stack([x0, y0]),
                             np.column_stack([x1, y1])], axis=1)
            ax.add_collection(LineCollection(
                segs, colors=c, linewidths=width, alpha=alpha,
                linestyles=style, zorder=z, capstyle='round'))


def make_pitch(ax, pad=4.0):
    """A horizontal full pitch in Opta coordinates on the dark panel.

    pitch_type='opta' is the same grid the shot chart draws, so both charts put
    the penalty box in the same place by construction. The panel is padded
    generously: 1.7% of passes end OUTSIDE the grid, at -2 or 102, and they are
    drawn there rather than clamped. Clamping was the first instinct and it is
    worse - 27,000 passes land on exactly x=100 and composite into a solid wall
    along the goal line that reads as a finding. A two-unit overshoot past the
    touchline is what happened.
    """
    ax.add_patch(FancyBboxPatch(
        (-pad, -pad), 100 + 2 * pad, 100 + 2 * pad,
        boxstyle='round,pad=0,rounding_size=2.0',
        facecolor=PANEL_COLOR, edgecolor='none', zorder=0))
    Pitch(pitch_type='opta', pitch_color='none', line_color=PITCH_LINE,
          linewidth=1.15, goal_type='box',
          pad_top=pad, pad_bottom=pad, pad_left=pad, pad_right=pad).draw(ax=ax)
    # ABOVE the passes, always. At 21,950 lines the markings measured 1.69:1
    # against the haze - the pitch vanished under its own chart, and with it
    # the only scale reference the reader has. Furniture is never data's to
    # occlude, and lifting it costs nothing at any other density.
    for artist in ax.lines + ax.patches + ax.collections:
        artist.set_zorder(max(artist.get_zorder(), 6))
    ax.patches[0].set_zorder(0)          # the panel stays behind everything
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')


# ── Summary of what is actually drawn ────────────────────────────────────────

def summarise(shown):
    """Headline numbers for the passes ON THE PITCH, not for the population.

    The panel describes the marks; the caption carries the denominator. Keeping
    them separate is what stops the chart quoting a rate the reader cannot see.
    """
    n = len(shown)
    if not n:
        return []
    comp = int(shown['completed'].sum())
    fwd = int(shown['direction'].eq('Forward').sum())
    box = int(shown['dest_in_box'].sum())
    prog = int(shown['progressive'].sum())
    corners = int(shown['restart'].eq('Corner').sum())

    def pct(k):
        # Round normally. Only 99.x is held back off 100, because 435 of 436 is
        # 99.77% and printing "100%" beside a leader row reading 98% is a
        # contradiction on one panel. Flooring EVERYTHING was the first attempt
        # and it silently moved every percentage on every chart down a point.
        v = 100.0 * k / n
        return f"{min(round(v), 99) if v < 100 else 100}%"

    rows = [
        ('COMPLETION', comp, pct(comp)),
        ('AVG LENGTH', None, f"{shown['length_m'].mean():.0f} m"),
        ('FORWARD', fwd, pct(fwd)),
        # CORNERS specifically, not set pieces in aggregate. Corners are 11.4%
        # of passes into the box but every one of them starts on one of two
        # coordinates, so their ink concentrates into the fans that every cold
        # reviewer misread - one of them five times over, as a rendering fault.
        # ENDING IN, not INTO - 26.3% of these were already in the box when
        # struck. Same correction as the filter phrases; see pass_filters.
        ('ENDING IN BOX', box, f"{box:,}"),
        ('PROGRESSIVE', prog, f"{prog:,}"),
        ('FROM CORNERS', corners, f"{corners:,}"),
    ]
    # A stat the active filter has already forced is not a statistic. Under
    # "into the box", INTO THE BOX reads 100% of the shown set and the row is a
    # tautology dressed as a finding. Dropped on the count rather than on
    # knowledge of the filter, so it also catches the cases where a different
    # cut happens to be total.
    return [(label, value) for label, count, value in rows
            if count is None or 0 < count < n]


def top_matches(shown, limit=5):
    """Which games these passes came from, most first.

    The column's job is "what is on the pitch", and when BOTH ends of the pass
    are pinned - one passer, one receiver - neither a passer ranking nor a
    receiver ranking has anything left to rank. Dropping the block left a
    measured 383x461px hole, 12.3% of the frame, enclosed by the pitch, a
    closing note and the footer; three separate cold readers called it a failed
    render rather than deliberate space.

    Matches are what still varies, and "they did this most against X" is a real
    finding rather than padding. Also the block the 9:16 variant needs under
    its pitch, so it is built once here.
    """
    if shown.empty or 'gameId' not in shown:
        return []
    rows = []
    for gid, sub in shown.groupby('gameId', sort=False):
        r = sub.iloc[0]
        opp = str(r.get('opponent_name') or r.get('opponent') or '').upper()
        side = '(H)' if bool(r.get('is_home')) else '(A)'
        label = f"v {opp} {side}".strip() if opp else str(r.get('Date') or gid)[:10]
        rows.append((label, len(sub)))
    rows.sort(key=lambda t: -t[1])
    return rows[:limit]


def player_detail(shown, name):
    """A second line for a named passer, when the list is short enough to
    afford one. Fills the column with information rather than air."""
    sub = shown[shown['passer'] == name]
    if sub.empty:
        return ''
    return (f"{int(sub['dest_in_box'].sum())} ending in box  ·  "
            f"{int(sub['progressive'].sum())} progressive  ·  "
            f"{sub['length_m'].mean():.0f} m avg")


def leaders(shown, limit=6):
    """Who played them, and how much of the total the list accounts for.

    A team-match has a median of 16 passers and this lists 6, covering about
    65% of the passes - close enough to the total that a reader adds the rows
    and expects them to reconcile. The coverage line is what stops that.
    """
    if shown.empty or 'passer' not in shown:
        return [], ''
    g = (shown.groupby('passer')
         .agg(n=('passer', 'size'), comp=('completed', 'mean'))
         .sort_values('n', ascending=False))
    top = g.head(limit)
    rows = [(name, int(r.n), r.comp * 100) for name, r in top.iterrows()]
    if len(g) <= limit:
        return rows, ''
    return rows, f"top {len(top)} of {len(g)} passers"


# ── Layout ───────────────────────────────────────────────────────────────────
# Aspect expressed as DATA, not as `if aspect ==` scattered through the render.
# Only the 16:9 frame is filled in: the pipeline reviews it before the 9:8 and
# 9:16 variants are built, and every number below moves in that review. Adding a
# variant is adding a key, not editing the renderer.
_LAYOUTS = {
    'default': {
        'figsize': (16, 9),
        'kicker_y': 0.969, 'kicker_size': 11.5,
        'title_y': 0.919, 'title_size': 30, 'title_frac': 0.72,
        # The team-colour rule under the title is the house header furniture -
        # the shot chart and the xG race both carry one, width-matched to the
        # title. The pass map was the only chart without it, and a header of
        # four centred lines with no rule and no anchor is most of what read as
        # unfinished.
        'bar_h': 0.0075, 'bar_gap': 0.0074,
        'scope_y': 0.860, 'scope_size': 13,
        # The DECK: what subset is drawn. It is the only line that differs
        # between two pass maps of the same team, so it is the line the chart
        # cannot be read without - and a cold viewer skipped it outright,
        # because full-bleed bold red across 1,590 of 1,600px reads as an error
        # banner, not as a caption. It is white now, wrapped inside the pitch's
        # own width, and the ratio that used to bloat it lives in the panel.
        # DEMOTED. This was 17pt bold white over two lines and it gave a
        # corner-of-the-pitch filter the same voice as the player it described.
        # One line, muted, at the size of a caption - and wider, because a
        # single quiet line can run further than a stacked bold one.
        'deck_y': 0.806, 'deck_size': 12.5, 'deck_min': 10.0,
        'deck_frac': 0.56, 'deck_lead': 0.022, 'deck_lines': 2,
        # MARGIN is the reason these numbers look arbitrary. The pitch is
        # aspect-locked inside its axes, so the DRAWN panel is 74px narrower
        # than the axes box and its left edge lands 37px right of pitch_ax[0].
        # The old numbers gave a 72px left gutter against a 38px right one, so
        # the content block centred at x=817 while the header centred at 800 -
        # the header was centred on nothing. These put the drawn panel's left
        # edge and the stat column's right edge both on a 60px margin, which
        # makes the header's 0.5 centring correct by construction.
        # margin 0.04 = 64px, and the pitch_ax x is SOLVED for it, not guessed:
        # the axes box is 0.630*1600 = 1008px but the aspect-locked pitch inside
        # it is 909.5px, centred, so the painted left edge sits 49.25px right of
        # pitch_ax[0]. 64 - 49.25 = 14.75px = 0.00922. Content block 64..1536,
        # centre 800 - which is what makes the centred header land on it.
        'margin': 0.04,
        'pitch_ax': [0.00922, 0.098, 0.630, 0.672],
        'panel_x': 0.672, 'panel_w': 0.288,
        'panel_top': 0.752, 'panel_bottom': 0.112,
        'big_size': 42, 'label_size': 13, 'value_size': 15,
        'row_size': 13.5, 'head_size': 13, 'cover_size': 12,
        # ONE baseline under the pitch, not four. Measured on the old layout:
        # the legend label sat 4.8px ON the pitch panel, the corner note 6.3px
        # on it and 17.6px past its right edge, and the four bottom-strip rows
        # sat at four different baselines against four different left edges.
        'strip_y': 0.068, 'strip_y2': 0.042, 'legend_size': 10.5,
        'leaders_max': 6, 'row_step': 0.036, 'stat_step': 0.036,
        'gap_max': 0.038,
    },
}


# matplotlib's Text has no letter-spacing property, so the tracked small caps
# this chart's labels are set in have to be built by inserting thin spaces.
_HAIR = ' '


def track(s, n=1):
    """Letterspacing, by hand. n is how many thin spaces go between letters."""
    return (_HAIR * n).join(str(s))


def _text(fig, x, y, s, size, color=TEXT_PRIMARY, weight='normal',
          ha='left', va='center', spaced=0, **kw):
    if spaced:
        s = track(s, spaced)
    return fig.text(x, y, s, fontsize=size, color=color, fontweight=weight,
                    ha=ha, va=va, **kw)


def _runs(fig, y, runs, size, spaced=0, weight='normal'):
    """Draw (text, colour) pieces as one centred line.

    The passers were named in plain white in the subtitle while their colours
    lived only in the panel on the far right, so a reader had to hunt for the
    key and then carry it back to the pitch. Colour is this chart's identity
    channel; it belongs on the identity.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    widths, arts = [], []
    for text, colour in runs:
        t = fig.text(0.5, y, track(text, spaced) if spaced else text,
                     fontsize=size, color=colour, ha='left', va='center',
                     fontweight=weight)
        widths.append(t.get_window_extent(r).width / fig.bbox.width)
        arts.append(t)
    x = 0.5 - sum(widths) / 2.0
    for t, w in zip(arts, widths):
        t.set_position((x, y))
        x += w
    return arts


def _splits(n, k):
    """Every way to cut n items into k contiguous groups, as (start, end) pairs."""
    if k == 1:
        yield [(0, n)]
        return
    for first in range(1, n - k + 2):
        for rest in _splits(n - first, k - 1):
            yield [(0, first)] + [(a + first, b + first) for a, b in rest]


def _wrap(fig, text, size, max_frac, max_lines=2, min_size=12.0, bold=True):
    """Break `text` onto at most `max_lines` lines inside `max_frac` of the width.

    Returns (lines, size). matplotlib does not wrap and does not complain: it
    draws the string at the size you asked for and lets both ends fall off the
    canvas, which is exactly what happened here - a five-filter caption spanned
    x 5.1..1594.9 on a 1600px frame while every other element on the page
    respected a 59px margin.

    shared.styles.fit_fontsize solves the neighbouring problem by SHRINKING,
    and that is right for a title, which is one unbreakable word. It is wrong
    for a list of clauses: shrinking 122 characters to fit one line buys
    legibility nowhere. Break first, and only shrink if the break was not
    enough.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    limit = max_frac * fig.bbox.width

    def width(s, pt):
        t = fig.text(0.5, 0.5, s, fontsize=pt,
                     fontweight='bold' if bold else 'normal')
        fig.canvas.draw()
        w = t.get_window_extent(r).width
        t.remove()
        return w

    # Break on the clause separator first - a list should never break mid
    # clause - and fall back to spaces only when one clause alone overruns.
    from shared.pass_filters import SEP
    parts = text.split(SEP) if SEP in text else text.split(' ')
    glue = SEP if SEP in text else ' '

    def greedy(pt):
        lines, cur = [], ''
        for part in parts:
            trial = f"{cur}{glue}{part}" if cur else part
            if cur and width(trial, pt) > limit:
                lines.append(cur)
                cur = part
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    def balanced(k, pt):
        """Split into k contiguous groups with the most even widths.

        Greedy packing fills line one and leaves the remainder stranded:
        "under no pressure" alone under a 65-character line reads as an
        accident rather than as a break. Centred text has to be balanced or it
        looks like it broke by mistake.
        """
        best = None
        n = len(parts)
        if k > n:
            return None
        for cuts in _splits(n, k):
            groups = [glue.join(parts[a:b]) for a, b in cuts]
            w = [width(g, pt) for g in groups]
            if max(w) > limit:
                continue
            score = max(w) - min(w)
            if best is None or score < best[0]:
                best = (score, groups)
        return best[1] if best else None

    pt = size
    while True:
        lines = greedy(pt)
        if (len(lines) <= max_lines
                and max(width(ln, pt) for ln in lines) <= limit):
            return balanced(len(lines), pt) or lines, pt
        if pt <= min_size:
            # Out of room on both axes. Drop trailing clauses and SAY HOW MANY
            # went, rather than overflowing the slot or truncating silently -
            # the reader needs to know the cut is narrower than it reads, even
            # when there is no room to name every part of it.
            kept = list(parts)
            while len(kept) > 1:
                dropped = len(parts) - len(kept) + 1
                trial = glue.join(kept[:-1]) + f"{glue}+{dropped} more"
                if width(trial, pt) <= limit:
                    return [trial], pt
                kept.pop()
            return [glue.join(parts)], pt
        pt = max(min_size, pt - 0.5)


def _rule(fig, x0, x1, y, color='#2A3648', lw=1.0):
    fig.add_artist(Line2D([x0, x1], [y, y], color=color, lw=lw,
                          transform=fig.transFigure, zorder=1))


def _surname(name):
    """Drop a leading initial, keep everything else.

    Deliberately conservative. Taking the last token would give "Allister" for
    "A. Mac Allister" and "Dijk" for "V. van Dijk"; a particle list would need
    maintaining across every league in the feed. Stripping "X." handles the
    feed's own format and leaves a full name like "Mohamed Salah" intact,
    which is wordier than ideal and never wrong.
    """
    parts = str(name).split()
    if parts and len(parts[0]) == 2 and parts[0].endswith('.'):
        parts = parts[1:]
    return ' '.join(parts) or str(name)


def _title_runs(players, receivers, swatch_colour, team, labels=None):
    """The title: WHO this chart is about, in their own colours.

    Identity is the one filter worth the title. Everything else - thirds,
    channels, length, pressure - qualifies it and belongs in the quiet line.
    Passers carry their series colour here, which is also the only place the
    key and the marks meet at a size anyone reads: a cold viewer worked the
    colours out from the 3px chips in the leaders table, not from tinted names
    buried in the grey metadata line.
    """
    players = [p for p in (players or []) if p]
    receivers = [r for r in (receivers or []) if r]
    if not players and not receivers:
        return [(team, TEXT_PRIMARY)]
    labels = labels or {}

    def show(n):
        # The FULL name where we have one. "I. Konaté" is an initial and a
        # surname, and this database holds five different Konatés - a headline
        # that names a player has to actually name him (user, 2026-09-11).
        # _surname is the fallback for a player with no minutes row.
        return str(labels.get(n) or _surname(n)).upper()

    def side(names, colour=True):
        out = []
        for i, n in enumerate(names):
            if i:
                out.append(('  ·  ', TEXT_MUTED))
            c = swatch_colour.get(n, TEXT_PRIMARY) if colour else TEXT_PRIMARY
            out.append((show(n), c))
        return out

    # THE ARROW, ALWAYS, whenever a receiver is named. A cold viewer read
    # "RECEIVED BY KONATÉ · KERKEZ" as passes BY them - the exact inverse of
    # the chart - because the prefix is grey, smaller, and sits to the left of
    # two big white names, so the eye takes the names and drops the word that
    # carries the direction. Their own diagnosis: the arrow works because it
    # sits BETWEEN the two things it relates and cannot be skipped over. The
    # same reader got the arrow right instantly and this one backwards, so
    # this is a correctness failure rather than a matter of register.
    #
    # With no passer selected the source is the whole team, which is precisely
    # what the data is - every pass by this side that reached him.
    if receivers:
        source = side(players) if players else [(team, TEXT_PRIMARY)]
        return source + [('  →  ', TEXT_SECONDARY)] + side(receivers, False)
    return side(players)


def _scope_line(shown, info, competition, players=None):
    """HOW MUCH FOOTBALL this is, then the admin that qualifies it.

    Returns (lead, tail). The split is the whole point. As one even grey run
    of dot-separated fragments, a cold viewer called it "one lump of admin"
    and skipped it on all five renders - so they never saw the scoreline,
    never saw "38 MATCHES", and read a 38-match total as a single match. Their
    words: getting a season confused with a match is about as wrong as a
    football graphic can make you.

    The lead answers that question and is set brighter; the competition and
    season follow, muted, because they are the part that can be skipped
    without misreading the chart.
    """
    matches = int(info.get('total_matches') or 0)
    season = info.get('season_span') or ''
    tail = [b for b in (competition.upper() if competition else '', season) if b]
    lead = ''
    if matches == 1 and not shown.empty:
        r = shown.iloc[0]
        opp = r.get('opponent_name') or r.get('opponent') or ''
        side = '(H)' if bool(r.get('is_home')) else '(A)'
        lead = f"v {str(opp).upper()} {side}".strip()
        try:
            lead += f"  {int(r['team_score'])}-{int(r['opp_score'])}"
        except (KeyError, TypeError, ValueError):
            pass
        if info.get('date_range'):
            tail = [info['date_range']] + tail
    elif matches:
        lead = f"{matches} MATCHES"
    return lead, '  ·  '.join(tail)


def create_pass_map(shown, info, team_color, *, n_population=None,
                    caption_text='', filter_text=None, players=None,
                    receivers=None, player_labels=None, competition='',
                    custom_title=None, custom_subtitle=None, aspect='default'):
    """Render the pass map.

    `shown` is the FILTERED frame - the numerator, already annotated by
    shared.pass_filters.annotate_passes. `n_population` and the header text
    come from the same module, so the statement beside the marks is generated
    by the object that did the cutting rather than written twice.

    `filter_text` is pass_filters.filter_phrase() - the qualifier list alone.
    `caption_text` is the older full sentence and is still accepted so the
    Streamlit page can keep using one string for its own warnings; the header
    prefers `filter_text` because the counts belong in the panel.
    """
    L = _LAYOUTS.get(aspect, _LAYOUTS['default'])
    n_shown = len(shown)
    # The HEADLINE is ordered by volume so it matches the table beneath it -
    # the two disagreed, and a reader matching them positionally got the wrong
    # player and had to fall back to matching on colour.
    #
    # COLOURS ARE NOT REORDERED WITH IT. Handing the slots out in volume order
    # was the obvious next step and it is wrong: colour has to follow the
    # PLAYER, never his rank, or adding a filter that changes who passed most
    # repaints all three and every earlier render of the same trio disagrees
    # with this one. Pick order is arbitrary but it is STABLE, which is the
    # property that matters. So the title sorts and the palette does not.
    title_players = list(players or [])
    if title_players and len(title_players) > 1 and not shown.empty:
        counts = shown['passer'].value_counts()
        title_players = sorted(title_players,
                               key=lambda p: -int(counts.get(p, 0)))
    n_pop = n_population if n_population is not None else n_shown

    fig = plt.figure(figsize=L['figsize'])
    fig.patch.set_facecolor(BG_COLOR)

    color_for, legend_entries = resolve_colors(team_color, players)
    swatch_colour = {n: c for n, c in legend_entries}
    accent = ensure_line_contrast(team_color or '#888888', BG_COLOR)

    # -- header
    #
    # FILTERS ARE NOT ALL WORTH THE SAME. The chart is a pass map; the argument
    # around it lives in the editorial or the podcast that carries it. So the
    # header ranks its filters by editorial weight rather than listing them:
    # WHO passed and WHO received is the subject and goes in the title, and
    # where on the grass the ball started or finished is a qualifier and goes
    # in a quiet line under the scope (user, 2026-09-11). Stacking every
    # selected filter into a bold two-line deck gave a corner-of-the-pitch
    # cut the same voice as the player it was about.
    kicker = 'PASSES ALLOWED' if info.get('against') else 'PASS MAP'
    _text(fig, 0.5, L['kicker_y'], kicker, L['kicker_size'], TEXT_MUTED,
          'bold', ha='center', spaced=2)

    team = (info.get('team_name') or '').upper()
    title_runs = _title_runs(title_players, receivers, swatch_colour, team,
                             player_labels)
    if custom_title:
        title_runs = [(custom_title, TEXT_PRIMARY)]
    # fit_fontsize on the JOINED string. "WOLVERHAMPTON WANDERERS" is 2.1x the
    # width of "LIVERPOOL", and three surnames plus an arrow is longer still -
    # matplotlib would draw either straight off the frame without a word of
    # complaint, the same silent failure that put the old caption 5px from the
    # canvas edge.
    flat = ''.join(t for t, _ in title_runs)
    size = fit_fontsize(fig, flat, L['title_size'],
                        max_frac=L['title_frac'], floor=17)
    arts = _runs(fig, L['title_y'], title_runs, size, weight='bold')
    fig.canvas.draw()
    r0 = fig.canvas.get_renderer()
    inv0 = fig.transFigure.inverted()
    x0 = min(a.get_window_extent(r0).transformed(inv0).x0 for a in arts)
    x1 = max(a.get_window_extent(r0).transformed(inv0).x1 for a in arts)
    ybot = min(a.get_window_extent(r0).transformed(inv0).y0 for a in arts)
    # Centred on the frame rather than hung off the text bbox: glyph side
    # bearings made it 4px proud on the left and 1px on the right, centring it
    # at 799.5 against a header that centres at 800.0.
    # NEUTRAL when the headline carries player colours. The rule is the team's
    # accent, and a red bar directly under a pink name read as that player's
    # colour swatch - a fourth key on a chart whose whole identity channel is
    # three colours. The club keeps the brand mark on every other render.
    bar_colour = '#46586E' if swatch_colour else accent
    # Thinner in the multi-name case. Measured: with three coloured names the
    # headline drops to 23px caps at contrast 4.7 while the width-matched rule
    # grows to 1140x7px at contrast 6.0 - 4.3x the area and 1.7x the contrast
    # of the thing it is underlining, and the strongest element in the header
    # on a squint test. The width match is the one device that holds across
    # every render, so the weight comes out instead of the measure.
    bar_h = L['bar_h'] * (0.5 if swatch_colour else 1.0)
    fig.patches.append(Rectangle(
        (0.5 - (x1 - x0) / 2.0, ybot - L['bar_gap']), x1 - x0, bar_h,
        transform=fig.transFigure, facecolor=bar_colour, edgecolor='none',
        zorder=10))

    if custom_subtitle:
        _text(fig, 0.5, L['scope_y'], custom_subtitle, L['scope_size'],
              TEXT_SECONDARY, ha='center', spaced=1)
    else:
        lead, tail = _scope_line(shown, info, competition, players)
        runs = []
        # The club drops to the scope line whenever players own the title -
        # it is still the thing that makes the names mean something, but it is
        # no longer the subject of the chart.
        if title_runs and title_runs[0][0] != team and team:
            runs.append((team, TEXT_SECONDARY))
        if lead:
            runs.append((('  ·  ' if runs else '') + lead, TEXT_PRIMARY))
        if tail:
            runs.append((('  ·  ' if runs else '') + tail, TEXT_MUTED))
        if runs:
            _runs(fig, L['scope_y'], runs, L['scope_size'], spaced=1)

    # The remaining filters, demoted. One line, muted, never stacked: if it
    # does not fit it shrinks to the floor and then elides, because a qualifier
    # that has run to two bold lines has stopped being a qualifier.
    deck = filter_text if filter_text is not None else caption_text
    if deck:
        deck = deck[:1].upper() + deck[1:] if deck[:1].islower() else deck
        # Measured at 834px on a five-clause filter, it was the widest line in
        # the header - 3.2x the rule, 1.5x the context line - which made the
        # lowest-ranked element the dominant horizontal. Narrower measure, and
        # a second line is allowed now that it is quiet enough to take one.
        lines, pt = _wrap(fig, deck, L['deck_size'], L['deck_frac'],
                          L['deck_lines'], L['deck_min'])
        top = L['deck_y'] + L['deck_lead'] * (len(lines) - 1) / 2.0
        for i, line in enumerate(lines):
            _text(fig, 0.5, top - i * L['deck_lead'], line, pt, TEXT_MUTED,
                  ha='center')

    # -- pitch
    ax = fig.add_axes(L['pitch_ax'])
    make_pitch(ax)
    draw_passes(ax, shown, color_for, identity=bool(legend_entries))

    # -- the strip under the pitch: ONE baseline, aligned to the PANEL.
    #
    # The pitch is aspect-locked inside its axes, so the drawn panel is 36px
    # narrower than the axes box it lives in. Aligning furniture to the axes -
    # which is what `pitch_ax[0] + pitch_ax[2]` does - therefore hangs it 17.6px
    # past the visible edge. Measure the panel and align to that.
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    inv = fig.transFigure.inverted()
    # THE AXES BOX, not the panel patch. mplsoccer aspect-locks the pitch and
    # compresses the x padding to fit, so the data range is really -2.59..102.59
    # against the -4..104 the patch was built on - and get_window_extent happily
    # extrapolates the transform, returning x 59.9..993.8 for a rectangle that
    # is PAINTED at 72.1..981.6 because it is clipped to the axes. Verified by
    # scanning pixels: the panel fill starts at x=72 and ends at x=981.
    # Aligning furniture to the patch extent put it 12px off the visible edge.
    pan = ax.get_window_extent().transformed(inv)
    sy, sy2 = L['strip_y'], L['strip_y2']

    def _w(artist):
        return artist.get_window_extent(r).transformed(inv)

    # legend, laid out left to right from the panel's left edge. Two entries
    # minimum: the line style IS the completion encoding, so it is never left
    # implicit.
    lx = pan.x0
    # In identity mode the key shows STYLE, not colour, so it cannot take any
    # player's hue. Two lenses pulled opposite ways here: a viewer called the
    # mid-grey key "a placeholder nobody wired up", so it went white - and a
    # designer then measured white as the brightest mark in the bottom 90px of
    # a page where NOTHING drawn is white, pulling the eye down into a corner.
    # The in-between was the problem: too bright to read as furniture, not a
    # data colour either. It now matches its own label and the arrow beside it,
    # which resolves it as furniture rather than as an unassigned series.
    swatch = accent if not legend_entries else TEXT_MUTED
    # The legend prunes on the same rule as the stat block. Under a "completed"
    # filter the panel drops its COMPLETION row, but the legend went on drawing
    # a dashed INCOMPLETE key against a pitch with nothing dashed on it -
    # furniture kept where the data had been cut.
    n_done = int(shown['completed'].sum()) if len(shown) else 0
    keys = ([('solid', 'COMPLETED')] if n_done else []) +            ([(_DASH, 'INCOMPLETE')] if len(shown) - n_done else [])
    for style, label in keys:
        # Pulled toward the ink's own strength. At 2,776 passes the lines wash
        # out to alpha 0.13 while the key stayed fully saturated, and a cold
        # viewer could not tell whether the faint marks were the completed
        # passes the bright key was describing. It cannot match exactly - a key
        # has to stay legible - so it meets the ink part way.
        fig.add_artist(Line2D([lx, lx + SWATCH_W], [sy, sy], color=swatch,
                              lw=SWATCH_LW, linestyle=style,
                              alpha=max(min(density_params(n_shown, bool(legend_entries))[0] * 2.0, 1.0), 0.6),
                              solid_capstyle='butt', dash_capstyle='butt',
                              transform=fig.transFigure))
        a = _text(fig, lx + SWATCH_W + 0.007, sy, label, L['legend_size'], TEXT_MUTED,
                  spaced=1)
        fig.canvas.draw()
        lx = _w(a).x1 + 0.028
    legend_x1 = lx - 0.028

    # Attacking direction: centred on the pitch WHERE IT FITS, pushed clear of
    # the legend where it does not. Centring it unconditionally is what
    # produced "INCOMPLETEATTACKING DIRECTION" the moment the strip went to one
    # baseline - the first version of this guard protected the corner note and
    # forgot the element between it and the legend.
    # TEXT_SECONDARY, not muted. This is the only element orienting the entire
    # pitch, and a cold viewer said they nearly missed it every time and on two
    # renders could not tell which way the team was attacking.
    lab = _text(fig, 0.5, sy, 'ATTACKING DIRECTION', L['legend_size'],
                TEXT_SECONDARY, spaced=1)
    fig.canvas.draw()
    lw_ = _w(lab).width
    # The gap is deliberately generous. The measured clearance at 0.010 was
    # 21px and rendered clean here at every dpi and display scale, but the user
    # sees the arrowhead touching the label on their machine and I could not
    # reproduce it - so the number is set where a mechanism I cannot see still
    # cannot close it, rather than tuned to the clearance I happen to measure.
    arrow_w, gap = 0.034, 0.020
    x0 = max((pan.x0 + pan.x1) / 2.0 - (arrow_w + gap + lw_) / 2.0,
             legend_x1 + 0.030)
    arrow = ax.annotate('', xy=(x0 + arrow_w, sy), xytext=(x0, sy),
                        xycoords='figure fraction', textcoords='figure fraction',
                        arrowprops=dict(arrowstyle='-|>', color=TEXT_SECONDARY,
                                        lw=1.6, mutation_scale=13))
    # Place the label after the arrow's MEASURED extent, not after the width it
    # was asked for. An arrowhead is drawn in POINTS via mutation_scale while
    # the shaft is placed in figure fractions, so the drawn patch is not
    # guaranteed to end where xy says - and the head is exactly the end that
    # meets the text. Measuring costs one draw and removes the assumption
    # rather than padding around it.
    fig.canvas.draw()
    try:
        arrow_x1 = _w(arrow.arrow_patch).x1
    except (AttributeError, TypeError, ValueError):
        arrow_x1 = x0 + arrow_w
    lab.set_position((max(arrow_x1, x0 + arrow_w) + gap, sy))
    fig.canvas.draw()
    dir_x1 = _w(lab).x1
    # Assert the clearance rather than trust the arithmetic. Cheap, and it
    # turns an invisible layout regression into a loud one.
    _gap_px = (_w(lab).x0 - max(arrow_x1, x0 + arrow_w)) * fig.bbox.width
    if _gap_px < 6:
        import warnings
        warnings.warn(f"pass map: direction arrow within {_gap_px:.1f}px of "
                      f"its label", stacklevel=2)

    # The set-piece note. A cold viewer flagged the corner shapes as a
    # rendering fault five times over and, told the number was in the panel,
    # said plainly that a figure in a list does not defuse a shape on a map.
    #
    # It used to say "all from one spot". That is FALSE: measured over a
    # Liverpool season, 137 corners start at (100, 0) and 95 at (100, 100) -
    # two convergence points, both visible, and the same reviewer caught the
    # contradiction. It also fired at 5 corners, where there is no shape to
    # explain and the sentence read as leftover debug text.
    # No proportional term. It used to require 4% of the drawn set, which
    # silenced the note exactly where the fan is still plainly visible but
    # numerically small - 14 corners among 554 passes still converge on two
    # points and still read as a comb. Every cold reader across three rounds
    # has called that shape a rendering glitch, and one of them hit a render
    # where the note was suppressed and said so. It fires whenever there are
    # enough corners to make the shape.
    corners = int(shown['restart'].eq('Corner').sum()) if len(shown) else 0
    if corners >= 8:
        note = _text(fig, pan.x1, sy,
                     f"{corners:,} CORNERS, FANNING FROM THE CORNER FLAGS",
                     L['cover_size'], TEXT_MUTED, ha='right', spaced=1)
        fig.canvas.draw()
        # If it cannot share the baseline it drops to a second one rather than
        # colliding. The strip is measured, not assumed - that is the check the
        # old layout never made. On that second line it also switches to the
        # LEGEND'S rail: right-aligned under a left-aligned row gave the block
        # two alignments and put its left edge on nothing.
        if _w(note).x0 < dir_x1 + 0.012:
            note.set_position((pan.x0, sy2))
            note.set_ha('left')
    # -- right panel: what is on the pitch
    px, pw = L['panel_x'], L['panel_w']
    y = L['panel_top']
    _text(fig, px, y, 'PASSES SHOWN', L['label_size'], TEXT_MUTED,
          spaced=1)
    y -= 0.070
    big = _text(fig, px, y, f"{n_shown:,}", L['big_size'], TEXT_PRIMARY, 'bold')
    # Sit the qualifier on the NUMERAL'S BASELINE, not on its optical centre.
    # Centring a 15pt string against a 42pt one hung "of 21,950" 15px below the
    # figure it qualifies, so the pair read as a separate row rather than as
    # part of the number.
    fig.canvas.draw()
    base = (big.get_window_extent(fig.canvas.get_renderer())
            .transformed(fig.transFigure.inverted()).y0)
    of_what = (('by this player' if len(players) == 1 else 'by these players')
               if players
               else 'in this match' if int(info.get('total_matches') or 0) == 1
               else 'in these matches')
    # The denominator moved here from the header, which means this is now the
    # only place the chart states it - so it stops being the smallest, greyest
    # text on the page. A cold viewer said they nearly skipped it, and it is
    # the text that tells you what the 42pt number means.
    # "554 of 554" is the same noise pass_filters.caption() refuses to print,
    # and for the same reason - a ratio against itself reads like a filter that
    # failed. When nothing is cut there is no denominator to state, only a
    # population to name, which `of_what` does on the line below.
    right = px + pw
    if n_shown < n_pop:
        # NOT accent. Moving the ratio out of a red banner and into a red
        # percentage moved the problem rather than fixing it: a cold viewer
        # said "red = something's wrong to me... every time I saw it I braced
        # for bad news and it was just a share of the total". The accent on
        # this chart belongs to the pass lines and the title rule.
        pct = _text(fig, right, base, f"{100.0 * n_shown / n_pop:.1f}%",
                    L['value_size'], TEXT_PRIMARY, 'bold', ha='right',
                    va='baseline')
        fig.canvas.draw()
        right -= (pct.get_window_extent(fig.canvas.get_renderer())
                  .transformed(fig.transFigure.inverted()).width + 0.014)
        _text(fig, right, base, f"of {n_pop:,}", L['value_size'],
              TEXT_SECONDARY, ha='right', va='baseline')
    # Rides UP to the number's own baseline when the "of N" line is absent.
    # Unfiltered, it sat alone 19px below the figure with 235px of void to its
    # left and nothing on the line above - an orphan rather than a qualifier.
    tail = _text(fig, px + pw, base - (0.024 if n_shown < n_pop else 0.0),
                 of_what, L['cover_size'], TEXT_MUTED, ha='right',
                 va='baseline')
    # The rule hangs off the QUALIFIER, not off a fixed step from the number.
    # A fixed step left 7px of clearance on one render and 2px on another - the
    # qualifier's nearest neighbour became the rule rather than the figure it
    # describes, so it read as captioning the rule. Measure and clear it.
    fig.canvas.draw()
    y = (tail.get_window_extent(fig.canvas.get_renderer())
         .transformed(fig.transFigure.inverted()).y0) - 0.022
    _rule(fig, px, px + pw, y)

    for label, value in summarise(shown):
        y -= L['stat_step']
        _text(fig, px, y, label, L['label_size'], TEXT_MUTED, spaced=1)
        _text(fig, px + pw, y, value, L['value_size'], TEXT_PRIMARY, 'bold',
              ha='right')

    # SAY WHY COMPLETION IS ABSENT when a receiver is named. The row is dropped
    # because naming a receiver forces it to 100% - only completed passes carry
    # one - but silence looked like missing data: a cold viewer noticed the row
    # was gone and wondered whether the data had failed, then had to reason
    # their own way to the answer. Cheaper to say it.
    if receivers and len(shown):
        y -= L['stat_step']
        # Short enough to FIT the column: the first wording ran 530px in a
        # 461px panel and was clipped mid-word at the frame edge.
        _text(fig, px, y, 'only completed passes name a receiver',
              L['cover_size'], TEXT_MUTED)

    swatch_of = swatch_colour
    rows, coverage = leaders(shown, L['leaders_max'])
    # A one-row ranking of the player already named in the title is not a
    # ranking. Its count IS the figure at the top of the panel, and its detail
    # line restates AVG LENGTH, PROGRESSIVE and ENDING IN BOX from the rows
    # directly above. Same rule that drops a forced stat and a constant CMP
    # column: if the filter has already determined it, it is not a finding.
    matches_block = []
    if len(rows) == 1 and rows[0][1] == n_shown:
        rows, coverage = [], ''
        # Only when there is more than one match to rank - a single-match scope
        # would produce a one-row table restating the figure above it, which is
        # the tautology this block was introduced to avoid repeating.
        if int(info.get('total_matches') or 0) > 1:
            matches_block = top_matches(shown, L['leaders_max'])
    # A completion column under a "completed" filter is not a statistic. It
    # printed 100% six rows deep and a cold viewer read the repetition as a
    # broken column - "placeholder data". summarise() has dropped forced stats
    # from the panel above since the first pass; the leaders table never got
    # the same guard, so one half of the column knew and the other did not.
    # Suppressed when the column carries no information, which is more often
    # than "every pass completed". On a shot-assist filter completion is 99%
    # and all six displayed rows round to 100 - a cold viewer read that as "a
    # broken column or a default value", which is what a constant column IS.
    show_cmp = (bool(len(shown))
                and 0 < int(shown['completed'].sum()) < len(shown)
                and not all(round(c) >= 100 for _, _, c in rows)
                and not all(round(c) <= 0 for _, _, c in rows))
    if rows:
        # BOTTOM-ANCHORED, and now actually so. The block used to flow down
        # from wherever the stat rows ended, so a selection that suppressed
        # four stats floated the whole column up and left a measured 129.6px
        # void at its foot - which a cold viewer read as "the rest failed to
        # load". Measure the block and stand it on the panel floor instead.
        detail_rows = len(rows) if len(rows) <= 3 else 0
        height = (0.046 + 0.042 + len(rows) * L['row_step']
                  + detail_rows * 0.038 + (0.042 if coverage else 0.0))
        # Bottom-anchor, but never open a gap the page has no vocabulary for.
        # The designed stats->leaders gap is 36px and holds on four of six
        # renders; pure bottom-anchoring stretched it to 114px when a filter
        # suppressed four stat rows - 3.2x the largest gap used anywhere else,
        # and a cold designer read it as missing rows rather than as
        # separation. Capped at 2x the house gap; the remainder falls to the
        # foot of the column, where slack reads as margin instead of as a hole.
        y = max(min(y, L['panel_bottom'] + height), y - L['gap_max'])
        y -= 0.046
        _rule(fig, px, px + pw, y)
        y -= 0.042
        _text(fig, px, y, 'LEADING PASSERS', L['head_size'], TEXT_MUTED,
              spaced=1)
        if show_cmp:
            _text(fig, px + pw, y, 'CMP', L['head_size'], TEXT_MUTED,
                  ha='right', spaced=1)
        # ONE leading value, not a distributed one. Distributing swung the
        # step 29->40px across the family for identical text, and short lists
        # fell back to top-anchored and left a 185px void at the foot.
        step = L['row_step']
        for name, count, comp in rows:
            y -= step
            # When players are the subject, colour IS their identity, so it
            # belongs beside the name rather than in a separate key the reader
            # has to cross-reference.
            name_x = px
            if name in swatch_of:
                fig.add_artist(Line2D([px, px + SWATCH_W], [y, y],
                                      color=swatch_of[name], lw=SWATCH_LW,
                                      transform=fig.transFigure,
                                      solid_capstyle='butt'))
                name_x = px + SWATCH_W + 0.008
            _text(fig, name_x, y, str(name), L['row_size'], TEXT_PRIMARY)
            _text(fig, px + pw * (0.74 if show_cmp else 1.0), y, f"{count:,}",
                  L['row_size'], TEXT_SECONDARY, ha='right')
            if show_cmp:
                _text(fig, px + pw, y,
                      f"{min(round(comp), 99) if comp < 100 else 100}%",
                      L['row_size'], TEXT_SECONDARY, ha='right')
            if len(rows) <= 3:
                detail = player_detail(shown, name)
                if detail:
                    y -= 0.026
                    _text(fig, name_x, y, detail, L['cover_size'], TEXT_MUTED)
                    y -= 0.012
        # What the list does NOT account for. Without it the six rows read as
        # a roster rather than a ranking, and on a season map 43% of the drawn
        # passes belong to nobody named.
        if coverage:
            # Tracked caps, like every other label on the page. Lowercase
            # conversational text below a table read as a note the developer
            # left in - flagged in three separate cold rounds.
            _text(fig, px, y - 0.042, coverage.upper(), L['cover_size'],
                  TEXT_MUTED, spaced=1)

    if matches_block:
        height = 0.046 + 0.042 + len(matches_block) * L['row_step']
        y = max(min(y, L['panel_bottom'] + height), y - L['gap_max'])
        y -= 0.046
        _rule(fig, px, px + pw, y)
        y -= 0.042
        _text(fig, px, y, 'BUSIEST MATCHES', L['head_size'], TEXT_MUTED,
              spaced=1)
        for label, count in matches_block:
            y -= L['row_step']
            _text(fig, px, y, label, L['row_size'], TEXT_PRIMARY)
            _text(fig, px + pw, y, f"{count:,}", L['row_size'],
                  TEXT_SECONDARY, ha='right')

    add_cbs_footer(fig, x0=L['margin'], x1=1.0 - L['margin'])
    return fig
