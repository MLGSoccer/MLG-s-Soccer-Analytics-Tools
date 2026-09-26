"""Touch Map - where a player (or a team) touched the ball.

A flat topographic field: nine tint bands, each holding a tenth of the
touches, on the CBS pitch. The design was settled over three sketch rounds on
real data (memory: project_touch_map.md); the short version of what was
rejected and why:

- zone shares ("18% this match vs 11% typical") - the zone boundaries did the
  analytical work, and a pitch covered in percentages is a table in costume
- an average position and a 50% ellipse - one real match (a player who switched
  flanks at half time) was called "a normal game, 7 m deeper"; density does not
  have to be contiguous, which is exactly why a circle is a bad measure of it
- hexbins, relief shading, terraces, 3-D - hexbins weakened the second zone;
  relief shading turned noise into texture; terraces broke every pitch line at
  every step; 3-D is parked (a per-pixel renderer exists in the scratchpad)

WHAT THE BANDS MEAN. The smoothed field's cells are sorted by density and cut
at 10, 20 ... 90% of the cumulative mass. So the brightest band is the
smallest area holding a tenth of the touches, and every band between two cuts
holds exactly another tenth - which is what the key says, in one line. The
ground outside the ninth cut holds the last tenth, spread thin, and is left
unpainted.

Coordinates are attack-normalised by the feed (every team attacks towards
x=100, no half-time flip), so multi-game aggregation needs no correction.
Smoothing happens in METRES - the Opta grid is 100x100 over a 105x68 pitch,
so a kernel in Opta units would be 1.5x wider across the pitch than along it.
"""
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.patches import FancyArrow, Rectangle
from scipy.ndimage import gaussian_filter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import (BG_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
                           add_cbs_footer)
from shared.colors import ensure_line_contrast
from mostly_finished_charts.pass_map_chart import (
    make_pitch, _header, _text, _pack_runs, _scope_line, _wrap, PANEL_COLOR,
)

PITCH_L, PITCH_W = 105.0, 68.0
CELL = 0.4                                   # metres per density cell
SHARES = (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)
N_BANDS = len(SHARES)
SEP = '  ' + chr(0xB7) + '  '

# The kernel widens as the sample shrinks (Scott's rule goes as n^-1/6),
# anchored at a typical 1,400-touch season. One fixed width drew a 72-touch
# sample as measles and a 3,000-touch one as a smooth field (the DP card found
# this first). Clamped at both ends: a cameo of three touches must not spread
# over half the pitch, and a team season must not sharpen into speckle.
SIGMA_M = 3.4
SIGMA_N = 1400
SIGMA_RANGE_M = (2.5, 6.3)


# -- The field ---------------------------------------------------------------

def _xy(touches):
    x = touches['EventX'].to_numpy(float)
    y = touches['EventY'].to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def density(touches):
    """(g, xs, ys): the smoothed field on a metre grid, normalised to sum 1,
    with its cell centres in OPTA units for drawing."""
    x, y = _xy(touches)
    nx, ny = int(round(PITCH_L / CELL)), int(round(PITCH_W / CELL))
    h, _, _ = np.histogram2d(np.clip(y, 0, 100) * PITCH_W / 100,
                             np.clip(x, 0, 100) * PITCH_L / 100,
                             bins=(ny, nx), range=[[0, PITCH_W], [0, PITCH_L]])
    n = max(1, len(x))
    sigma = float(np.clip(SIGMA_M * (SIGMA_N / n) ** (1 / 6), *SIGMA_RANGE_M))
    # Reflect at the touchlines: the ball cannot leave the pitch and come back,
    # so a zero pad would drag every wide player's field inward and draw the
    # touchline as a cliff.
    g = gaussian_filter(h, sigma / CELL, mode='reflect')
    total = g.sum()
    g = g / total if total > 0 else g
    xs = (np.arange(nx) + 0.5) * CELL * 100 / PITCH_L
    ys = (np.arange(ny) + 0.5) * CELL * 100 / PITCH_W
    return g, xs, ys


def band_levels(g):
    """The nine density cuts, ascending: the area above cut k holds
    SHARES[k] of the mass. Forced strictly increasing - a tiny sample can put
    two cuts on the same value, and contourf will not take a flat level."""
    v = np.sort(g.ravel())[::-1]
    c = np.cumsum(v)
    lv = [float(v[min(len(v) - 1, np.searchsorted(c, s))]) for s in SHARES]
    eps = max(float(v[0]) * 1e-9, 1e-15)
    for i in range(1, len(lv)):
        if lv[i] <= lv[i - 1]:
            lv[i] = lv[i - 1] + eps
    return lv


def lightness(rgb):
    """CIE L* (0-100) of an sRGB triple - perceptual lightness, which is what
    decides whether two adjacent bands can be told apart."""
    def lin(u):
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(u)) for u in rgb)
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 116 * y ** (1 / 3) - 16 if y > 0.008856 else 903.3 * y


def band_colours(color):
    """Nine tints of the club colour, dark to light, on the navy panel.

    The club colour is lifted to 3.5:1 against the panel first - the same
    guard the pass map uses, because a navy club's field would otherwise be
    navy on navy. A NEAR-WHITE club colour goes at the top of the ramp rather
    than in the middle: blending it toward white as well would spend the top
    three bands on three shades of white. Only near-white: the first cut used
    a looser test and capped Man City's sky blue and Wolves' gold at their own
    colour, which squeezed nine bands into 34 and 43 units of lightness - 3.8
    per step, below what the eye separates reliably.
    """
    c = np.array(to_rgb(ensure_line_contrast(color or '#888888', PANEL_COLOR)))
    p = np.array(to_rgb(PANEL_COLOR))
    w = np.ones(3)
    low = 0.72 * p + 0.28 * c
    if lightness(c) > 85:
        anchors = [(0.0, low), (0.5, 0.35 * p + 0.65 * c), (1.0, c)]
    else:
        # The club colour sits on the ramp at its OWN lightness, so the nine
        # steps are even. Pinned at the midpoint, Wolves' gold (already light)
        # left four steps of 3.4 units above it and four of 10 below.
        high = 0.25 * c + 0.75 * w
        lo, hi = lightness(low), lightness(high)
        t = float(np.clip((lightness(c) - lo) / max(hi - lo, 1e-6), 0.3, 0.7))
        anchors = [(0.0, low), (t, c), (1.0, high)]
    cm = LinearSegmentedColormap.from_list('touch', anchors)
    return [tuple(cm(t)[:3]) for t in np.linspace(0, 1, N_BANDS)]


def mark_colour(color):
    """The dots are the club colour, lifted for the panel."""
    return ensure_line_contrast(color or '#888888', PANEL_COLOR)


def draw_field(ax, touches, color, vertical=False):
    """The nine bands. contourf on the pitch axes - never imshow, whose
    aspect='auto' stretched the pitch in a sketch."""
    if touches is None or len(touches) == 0:
        return None
    g, xs, ys = density(touches)
    lv = band_levels(g)
    cols = band_colours(color)
    bounds = lv + [max(float(g.max()), lv[-1]) * 1.001 + 1e-15]
    if vertical:
        # Display axes, not Opta axes: rotated, the pitch's LENGTH runs up the
        # screen, so display-x reads from EventY and display-y from EventX
        # (make_pitch's VerticalPitch inverts x, which puts a player's right on
        # the right).
        cs = ax.contourf(ys, xs, g.T, levels=bounds, colors=cols, zorder=3,
                         antialiased=True)
    else:
        cs = ax.contourf(xs, ys, g, levels=bounds, colors=cols, zorder=3,
                         antialiased=True)
    return cs


def band_shares(touches, g=None, xs=None, ys=None):
    """The share of the RAW touches that falls in each band, brightest first.

    The key says each band holds a tenth; that is exact for the smoothed mass
    by construction, and this measures how far smoothing moves the raw count.
    Used by the tests, not by the chart."""
    if g is None:
        g, xs, ys = density(touches)
    lv = band_levels(g)
    x, y = _xy(touches)
    ix = np.clip(np.searchsorted(xs, np.clip(x, 0, 100)), 0, len(xs) - 1)
    iy = np.clip(np.searchsorted(ys, np.clip(y, 0, 100)), 0, len(ys) - 1)
    k = np.digitize(g[iy, ix], lv)                    # 0 = ground, 1..9
    n = max(1, len(x))
    return [float((k == b).sum()) / n for b in range(N_BANDS, 0, -1)]


def _marks_style(n, aspect):
    """(size, alpha) for n dots. Sixty dots can each be a place; three
    thousand are a field, and a dot that size would be dust."""
    ln = np.log10(max(n, 1))
    size = float(np.interp(ln, [1, 2, 3, 4], [70, 46, 16, 6]))
    alpha = float(np.interp(ln, [1, 2, 3, 4], [0.92, 0.82, 0.45, 0.2]))
    if aspect != 'default':
        size *= 0.8
    return size, alpha


def draw_marks(ax, touches, color, vertical=False, aspect='default'):
    if touches is None or len(touches) == 0:
        return None
    x, y = _xy(touches)
    size, alpha = _marks_style(len(x), aspect)
    dx, dy = (y, x) if vertical else (x, y)
    return ax.scatter(dx, dy, s=size, c=mark_colour(color), alpha=alpha,
                      linewidths=0, zorder=4)


# -- Layout ------------------------------------------------------------------

# The header keys are the pass map's, deliberately: the two charts sit side by
# side on the app and a reader should not see two house styles. The BODY keys
# are this chart's own - no stat panel, one or two pitches and a key strip.
_LAYOUTS = {
    'default': {
        'figsize': (16, 9),
        'kicker_y': 0.969, 'kicker_size': 11.5,
        'title_y': 0.919, 'title_size': 30, 'title_frac': 0.72,
        'title_floor': 17,
        'bar_h': 0.0075, 'bar_gap': 0.0050,
        'scope_y': 0.860, 'scope_size': 13,
        'scope_frac': 0.94, 'scope_floor': 11,
        'deck_y': 0.826, 'deck_size': 12.5, 'deck_min': 10.0,
        'deck_frac': 0.56, 'deck_lead': 0.022, 'deck_lines': 2,
        'margin': 0.04,
        'body_gap': 0.020, 'body_floor': 0.045,
        'panel_label_size': 13, 'panel_detail_size': 11.5,
        'panel_label_gap': 0.012, 'panel_gap': 0.03,
        'strip_gap': 0.032, 'strip_rows': 0.0,
        'key_size': 11, 'key_label_size': 11,
        'swatch_w': 0.0150, 'swatch_h': 0.018, 'arrow_len': 0.038,
        'footer_y': 0.01,
        'compare': 'side',
    },
    '9x16': {
        'figsize': (9, 16),
        'flow': True,
        'kicker_y': 0.9750, 'kicker_size': 16,
        'title_y': 0.9440, 'title_size': 38, 'title_frac': 0.84,
        'title_floor': 19, 'title_lines': 3, 'title_prefer': 30,
        'title_lead_em': 1.30,
        'bar_h': 0.0042, 'bar_gap': 0.0043,
        'scope_y': 0.0185, 'scope_size': 16, 'scope_lead': 0.0185,
        'scope_frac': 0.88, 'scope_floor': 16, 'scope_track': 0,
        'deck_y': 0.0150, 'deck_size': 16.5, 'deck_min': 16,
        'deck_frac': 0.86, 'deck_lead': 0.0170, 'deck_lines': 2,
        'margin': 0.06,
        'body_gap': 0.018, 'body_floor': 0.040,
        'panel_label_size': 17, 'panel_detail_size': 16,
        'panel_label_gap': 0.008, 'panel_gap': 0.030,
        'strip_gap': 0.020, 'strip_rows': 0.031, 'key_row': 0.017,
        'key_size': 16, 'key_label_size': 16,
        'swatch_w': 0.048, 'swatch_h': 0.011, 'arrow_len': 0.030,
        'arrow_rise': 0.020,
        'footer_y': 0.0175,
        'vertical_single': True,
        # STACKED FLAT pitches, not upright ones side by side. Upright pairs were
        # tried (the cold viewer had to rotate the flat compare in their head
        # after the upright single map) and are width-bound: 380px each in a
        # 900px frame, which left ~40% of the portrait frame's height empty.
        # The arrow states the direction; an empty frame costs every reader.
        'compare': 'stack',
    },
    '9x8': {
        'figsize': (9, 8),
        'flow': True,
        'kicker_y': 0.9625, 'kicker_size': 16,
        'title_y': 0.9100, 'title_size': 30, 'title_frac': 0.86,
        'title_floor': 19, 'title_lines': 3, 'title_prefer': 22,
        'title_multi_max': 23, 'title_lead_em': 1.30,
        'bar_h': 0.0075, 'bar_gap': 0.0080,
        'scope_y': 0.0300, 'scope_size': 16, 'scope_lead': 0.0330,
        'scope_frac': 0.90, 'scope_floor': 16, 'scope_track': 0,
        'deck_y': 0.0250, 'deck_size': 16, 'deck_min': 16,
        'deck_frac': 0.88, 'deck_lead': 0.0300, 'deck_lines': 2,
        'margin': 0.045,
        'body_gap': 0.020, 'body_floor': 0.055,
        'panel_label_size': 16, 'panel_detail_size': 16,
        'panel_label_gap': 0.010, 'panel_gap': 0.03,
        'strip_gap': 0.030, 'strip_rows': 0.050, 'key_row': 0.030,
        'key_size': 16, 'key_label_size': 16,
        'swatch_w': 0.036, 'swatch_h': 0.020, 'arrow_len': 0.045,
        'arrow_rise': 0.030,
        'footer_y': 0.026,
        'compare': 'side',
    },
}


def _drawn_box(fig, ax):
    """The pitch panel as DRAWN, in figure fraction - the axes box is wider
    or taller than the aspect-locked pitch inside it."""
    ax.apply_aspect()
    return ax.get_position()


def _pitch_axes(fig, rect, vertical):
    ax = fig.add_axes(rect)
    make_pitch(ax, vertical=vertical)
    return ax


GROUND_EDGE = '#46586E'


def _width(fig, s, size, weight='normal', spaced=0):
    """Rendered width of a string in figure fraction (drawn, measured, removed)."""
    t = _text(fig, 0, 0, s, size, TEXT_SECONDARY, weight, ha='left', spaced=spaced)
    fig.canvas.draw()
    w = (t.get_window_extent(fig.canvas.get_renderer())
         .transformed(fig.transFigure.inverted()).width)
    t.remove()
    return w


def _swatches(fig, x0, y, cols, sw, sh):
    """The ground swatch first, then the nine shades. Ten swatches for "each a
    tenth": with nine, a cold viewer counted them, found no tenth, and read
    the unpainted pitch as NO touches - where the last tenth actually is.

    Laid out in WHOLE PIXELS with one hairline on every swatch. Fractional
    widths rounded into 1/3/2px gaps, and an outline on the first swatch alone
    made it read as an empty checkbox rather than step one (the designer)."""
    cols = [PANEL_COLOR] + list(cols)
    W = fig.get_figwidth() * fig.dpi
    w_px = max(8, int(round(sw * W)) - 2)
    x_px = int(round(x0 * W))
    for i, c in enumerate(cols):
        fig.patches.append(Rectangle(((x_px + i * (w_px + 2)) / W, y - sh / 2), w_px / W, sh,
                                     transform=fig.transFigure, facecolor=c,
                                     edgecolor=GROUND_EDGE, linewidth=0.6, figure=fig))


def _key(fig, L, y, cols, x_centre=0.5):
    """FEWER [ten swatches] MORE, one row at every aspect. Returns (x0, x1).

    The key says which end is more and nothing else. It used to read "EACH
    SHADE - a tenth of his touches": the first two words named what the
    swatches visibly are and the rest described how the bands were BUILT
    (user: "what does 'each shade' add to this chart?"). The unpainted ground
    swatch sits at the FEWER end, so a blank pitch reads as least, not none."""
    n = len(cols) + 1                       # + the ground
    sw, sh = L['swatch_w'], L['swatch_h']
    spaced = 0 if L.get('flow') else 1
    lo = _text(fig, 0, y, 'FEWER', L['key_size'], TEXT_SECONDARY, 'bold',
               ha='left', va='center', spaced=spaced)
    hi = _text(fig, 0, y, 'MORE', L['key_size'], TEXT_SECONDARY, 'bold',
               ha='left', va='center', spaced=spaced)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    inv = fig.transFigure.inverted()
    wl = lo.get_window_extent(r).transformed(inv).width
    wh = hi.get_window_extent(r).transformed(inv).width
    gap = 0.012
    total = wl + gap + n * sw + gap + wh
    x0 = x_centre - total / 2
    lo.set_x(x0)
    sx = x0 + wl + gap
    _swatches(fig, sx, y, cols, sw, sh)
    hi.set_x(sx + n * sw + gap)
    return x0, x0 + total


def _arrow(fig, L, y, vertical, x=None, x_centre=None):
    """Attacking direction - the same words the pass map uses. Placed at `x`,
    or centred (arrow + words as one unit) on `x_centre`."""
    ln = L['arrow_len']
    fw, fh = L['figsize']
    lab = _text(fig, 0, y, 'ATTACKING DIRECTION', L['key_size'],
                TEXT_SECONDARY, 'bold', ha='left', va='center',
                spaced=0 if L.get('flow') else 1)
    fig.canvas.draw()
    wl = (lab.get_window_extent(fig.canvas.get_renderer())
          .transformed(fig.transFigure.inverted()).width)
    glyph = 0.012 if vertical else ln
    gap = 0.012
    if x is None:
        x = x_centre - (glyph + gap + wl) / 2
    # Widths in PIXELS, converted per axis: 3.5px shaft, 16px head. The first
    # cut was a 2px shaft that measured 1px on a phone (the designer).
    px_x, px_y = 1.0 / (fw * 100), 1.0 / (fh * 100)
    if vertical:
        dy = L['arrow_rise']
        fig.patches.append(FancyArrow(x + glyph / 2, y - dy / 2, 0.0, dy,
                                      width=3.5 * px_x,
                                      head_width=16 * px_x, head_length=dy * 0.4,
                                      length_includes_head=True,
                                      transform=fig.transFigure,
                                      color=TEXT_SECONDARY, figure=fig))
    else:
        fig.patches.append(FancyArrow(x, y, ln, 0.0, width=3.5 * px_y,
                                      head_width=16 * px_y,
                                      head_length=ln * 0.3,
                                      length_includes_head=True,
                                      transform=fig.transFigure,
                                      color=TEXT_SECONDARY, figure=fig))
    lab.set_x(x + glyph + gap)


# -- The frame ---------------------------------------------------------------

def _pack_tail(tail):
    """Competition and season as ONE unit ("PREMIER LEAGUE 2025/26"), and no
    season beside a date that already says it. As separate items the packer
    left "2025/26" alone on a line of its own at 9:16, and the 16:9 single-match
    line ran 1,371px - wider than the pitch it sits over."""
    kinds = dict(tail)
    if 'date' in kinds:
        tail = [(k, t) for k, t in tail if k != 'season']
        kinds = dict(tail)
    if 'competition' in kinds and 'season' in kinds:
        joined = f"{kinds['competition']} {kinds['season']}"
        tail = [(k, t) for k, t in tail if k not in ('competition', 'season')]
        tail.append(('competition', joined))
    return tail


def _scope_lines(fig, L, touches, info, competition, title_is_player,
                 compare=False):
    """The header's scope line.

    Single view: how much football this is (the fixture, or N MATCHES), the
    touch count, then competition and season. COMPARE: only what the two
    panels SHARE - the club, the competition, the season. The fixture and the
    counts belong to each panel's own label; in the header they described the
    left panel alone and the right one's label repeated them.
    """
    lead, tail = _scope_line(touches, info, competition)
    if compare:
        # the fixture's date belongs to the left panel's label - drop it BEFORE
        # packing, or the packer drops the season for a date that then goes too
        tail = [(k, t) for k, t in tail if k != 'date']
    tail = _pack_tail(tail)
    head = []
    team = (info.get('team_name') or '').upper()
    if title_is_player and team:
        head.append((team, TEXT_SECONDARY))
    if compare:
        if not tail and info.get('season_span'):
            tail = [('season', info['season_span'])]
    else:
        count = _count(touches)
        lead = f"{lead}{SEP}{count}" if lead else count
        head.append((((SEP if head and not L.get('flow') else '') + lead),
                     TEXT_PRIMARY))
    if L.get('flow'):
        has_date = any(k == 'date' for k, _ in tail)
        items = [(t, c) for t, c in head if t] + [
            (t, TEXT_MUTED) for k, t in tail if not (has_date and k == 'season')]
        return _pack_runs(fig, items, L['scope_size'], L['scope_frac'],
                          L.get('scope_track', 1))
    if tail:
        head.append(((SEP if head else '') + SEP.join(t for _, t in tail),
                     TEXT_MUTED))
    return [head]


_EXTRA_NOUNS = {
    'carry_start': ('CARRY START', 'CARRY STARTS'),
    'recovery': ('BALL RECOVERY', 'BALL RECOVERIES'),
    'aerial_won': ('AERIAL WON', 'AERIALS WON'),
    'pickup': ('KEEPER PICK-UP', 'KEEPER PICK-UPS'),
    'sweeper': ('KEEPER SWEEP', 'KEEPER SWEEPS'),
}


def _extras_in(frame):
    if frame is None or 'touch_type' not in frame:
        return {}
    vc = frame['touch_type'].value_counts()
    return {k: int(vc[k]) for k in _EXTRA_NOUNS if k in vc}


def _count(frame):
    """What is drawn, counted by its own name. A carry start is not a touch -
    it is where one began - so "4,651 TOUCHES" for 3,001 touches and 1,650
    carry starts was false; it reads "3,001 TOUCHES  .  1,650 CARRY STARTS"."""
    extras = _extras_in(frame)
    n = len(frame) - sum(extras.values())
    parts = [f"{n:,} TOUCH{'ES' if n != 1 else ''}"]
    for k, v in extras.items():
        one, many = _EXTRA_NOUNS[k]
        parts.append(f"{v:,} {one if v == 1 else many}")
    return SEP.join(parts)


def panel_labels_for(touches, baseline, info, competition, pronoun='his',
                     baseline_name=None):
    """((name, detail), (name, detail)) for the two compare panels, derived
    from the frames themselves so a label cannot disagree with its pitch.

    The scope panel is named the way the scope line names a fixture ("v
    MANCHESTER CITY (H)  1-2") or by its match count; the baseline is "HIS
    OTHER 35 MATCHES" unless the page names a date window. The detail line is
    the date (single match) and the touch count."""
    lead, tail = _scope_line(touches, info, competition)
    date = next((t for k, t in tail if k == 'date'), '')
    left = (lead or f"{int(info.get('total_matches') or 0)} MATCHES",
            SEP.join(x for x in (date, _count(touches), _rate(touches)) if x))
    n_other = int(baseline['gameId'].nunique()) if 'gameId' in baseline else 0
    plural = 'ES' if n_other != 1 else ''
    name = baseline_name or f"{pronoun.upper()} OTHER {n_other} MATCH{plural}"
    return left, (name, SEP.join(x for x in (_count(baseline), _rate(baseline)) if x))


def _rate(frame):
    """Touches a match, for a panel of more than one match. Each panel is
    shaded against its OWN total, so side by side an 18-touch substitute
    appearance lights up as fully as a season - the cold analyst's worst
    compare finding. The rate puts the volume back where it can be read."""
    if frame is None or 'gameId' not in frame:
        return ''
    games = int(frame['gameId'].nunique())
    if games < 2:
        return ''
    n = len(frame) - sum(_extras_in(frame).values())
    return f"{n / games:,.0f} A MATCH"


def _place_block(fig, axes, top, bottom, label_h, strip_h=0.0):
    """Centre ONE block - panel labels, pitches and the key strip under them -
    between the header and the footer. The pitches are aspect-locked, so the
    space they leave is only known once drawn. Hanging them off the header put
    all of it underneath; centring the pitches alone split it into two dead
    bands either side (26% of the 9:8 compare, the designer measured) with
    the key stranded at the bottom. The strip travels with the pitches now."""
    boxes = [_drawn_box(fig, ax) for ax in axes]
    block_top = max(b.y1 for b in boxes) + label_h
    # HUNG from the header, not centred. Centring split the spare height into
    # two dead bands (89px above the panel headings and 87 below the key on
    # the 16:9 compare, 111 and 103 on the 9:8) that cut the headings off from
    # the title they belong to. The spare now sits in ONE margin, above the
    # footer - which is what the first critique asked for.
    delta = top - block_top
    for ax in axes:
        pos = ax.get_position(original=True)
        ax.set_position([pos.x0, pos.y0 + delta, pos.width, pos.height])
    return [_drawn_box(fig, ax) for ax in axes]


def create_touch_map(touches, info, team_color, *, view='field', baseline=None,
                     panel_labels=None, baseline_name=None, subject_name=None,
                     pronoun='his', competition='', filter_text='',
                     custom_title=None, custom_subtitle=None, aspect='default'):
    """Render the touch map.

    touches: one row per touch in scope, EventX/EventY in Opta units, plus the
    fixture columns _scope_line reads (opponent_name, is_home, scores).
    info: team_name, total_matches, season_span, date_range.
    view: 'field' (the nine bands) or 'marks' (one dot per touch).
    baseline: the subject's other matches, drawn beside the scope in the same
    treatment. baseline_name names a date window ("OTHER MATCHES, AUG 2025 -
    MAY 2026"); otherwise the panel is "HIS OTHER N MATCHES". panel_labels
    overrides both labels outright (tests, custom exports).
    subject_name: a player's full name (the title), or None for the team.
    filter_text: the touch-type registry's exclusion phrase, set as the deck.
    """
    L = _LAYOUTS.get(aspect, _LAYOUTS['default'])
    fig = plt.figure(figsize=L['figsize'])
    fig.patch.set_facecolor(BG_COLOR)
    accent = ensure_line_contrast(team_color or '#888888', BG_COLOR)
    team = (info.get('team_name') or '').upper()
    title = custom_title or (subject_name.upper() if subject_name else team)
    compare = baseline is not None
    scope = None if custom_subtitle else _scope_lines(
        fig, L, touches, info, competition, bool(subject_name), compare=compare)
    header_bottom, _ = _header(
        fig, L, kicker='TOUCH MAP', title_runs=[(title, TEXT_PRIMARY)],
        accent=accent, swatch_colour=None, scope_lines=scope,
        scope_text=custom_subtitle, deck_text=filter_text or '')

    top = header_bottom - L['body_gap']
    bottom = L['body_floor']
    m = L['margin']
    frames = [touches] + ([baseline] if compare else [])
    fh = L['figsize'][1]
    line1 = L['panel_label_size'] * 1.35 / 72.0 / fh
    line2 = L['panel_detail_size'] * 1.35 / 72.0 / fh
    stacked = L['compare'] == 'stack'
    panel_w = (1 - 2 * m) if stacked else (1 - 2 * m - L['panel_gap']) / 2
    labels, label_h = [], 0.0
    if compare:
        raw = panel_labels or panel_labels_for(
            touches, baseline, info, competition, pronoun, baseline_name)
        # Wrapped to the PANEL's width before anything is laid out: a date
        # window's name is three times a fixture's, and two upright pitches at
        # 9:16 give each label under half the frame.
        for name, detail in raw:
            nl, ns = _wrap(fig, name, L['panel_label_size'], panel_w * 0.98, 2,
                           L['panel_detail_size'])
            dl, ds = _wrap(fig, detail, L['panel_detail_size'], panel_w * 0.98, 2,
                           L['panel_detail_size'], bold=False)
            labels.append((nl, ns, dl, ds))
        rows = max(len(nl) for nl, _, _, _ in labels) * line1 + \
            max(len(dl) for _, _, dl, _ in labels) * line2
        label_h = rows + L['panel_label_gap']

    # The strip under the pitch: arrow, then (field) the swatches and words.
    key_rows = 0 if view == 'marks' else (1 if L.get('flow') else 0)
    row = L.get('strip_rows', 0.0)
    strip_h = L['strip_gap'] + (key_rows * row if L.get('flow') else 0.0) + 0.012

    vertical = bool(L.get('vertical_compare' if compare else 'vertical_single'))
    avail = top - bottom - strip_h
    if not compare:
        rects = [[m, bottom + strip_h, 1 - 2 * m, avail]]
    elif stacked:
        h = (avail - L['panel_gap'] - 2 * label_h) / 2
        y0 = bottom + strip_h
        rects = [[m, y0 + h + L['panel_gap'] + label_h, 1 - 2 * m, h],
                 [m, y0, 1 - 2 * m, h]]
    else:
        h = avail - label_h
        rects = [[m, bottom + strip_h, panel_w, h],
                 [m + panel_w + L['panel_gap'], bottom + strip_h, panel_w, h]]

    axes = []
    for frame, rect in zip(frames, rects):
        ax = _pitch_axes(fig, rect, vertical)
        if view == 'marks':
            draw_marks(ax, frame, team_color, vertical=vertical, aspect=aspect)
        else:
            draw_field(ax, frame, team_color, vertical=vertical)
        axes.append(ax)
    boxes = _place_block(fig, axes, top, bottom, label_h, strip_h)
    n_detail = max((len(dl) for _, _, dl, _ in labels), default=0)
    for k, (box, (nl, ns, dl, ds)) in enumerate(zip(boxes, labels)):
        cx = box.x0 + box.width / 2
        # The scope panel LEADS; the baseline is context and steps back. Equal
        # weight left the eye on the richer baseline map (the designer).
        name_colour = TEXT_PRIMARY if k == 0 else TEXT_SECONDARY
        y = box.y1 + L['panel_label_gap'] + line2 * (n_detail - 0.5)
        # Untracked at every aspect: panel labels compete for a panel's width,
        # and tracked caps turned the house "v" into what read as a typo.
        for i, t in enumerate(dl):                       # detail, bottom-up
            _text(fig, cx, y - i * line2, t, ds, TEXT_SECONDARY, ha='center',
                  va='center')
        y = box.y1 + L['panel_label_gap'] + line2 * n_detail + line1 * (len(nl) - 0.5)
        for i, t in enumerate(nl):
            _text(fig, cx, y - i * line1, t, ns, name_colour, 'bold',
                  ha='center', va='center')

    # The strip, hung from the pitch it describes. One row at 16:9. In portrait
    # and at tile size the arrow comes first - directly under the pitch, the
    # thing it describes - then the swatches, then the words.
    # One ORDER at every aspect - direction, then the scale - which the first
    # cut did not have (16:9 ran key-then-arrow, portrait arrow-then-key).
    y = min(b.y0 for b in boxes) - L['strip_gap']
    if L.get('flow'):
        _arrow(fig, L, y, vertical, x_centre=0.5)
        if view != 'marks':
            _key(fig, L, y - row, band_colours(team_color))
    elif view == 'marks':
        _arrow(fig, L, y, vertical, x_centre=0.5)
    else:
        # One row, measured then centred: a fixed arrow position ran into the
        # key the moment the key's centre moved (a lint OVERLAP on all nine
        # 16:9 frames - the fix that set the order made it).
        n = N_BANDS + 1
        aw = L['arrow_len'] + 0.012 + _width(fig, 'ATTACKING DIRECTION',
                                             L['key_size'], 'bold', spaced=1)
        kw = (_width(fig, 'FEWER', L['key_size'], 'bold', spaced=1) + 0.012
              + n * L['swatch_w'] + 0.012
              + _width(fig, 'MORE', L['key_size'], 'bold', spaced=1))
        gap = 0.05
        x0 = 0.5 - (aw + gap + kw) / 2
        _arrow(fig, L, y, vertical, x=x0)
        _key(fig, L, y, band_colours(team_color), x_centre=x0 + aw + gap + kw / 2)

    add_cbs_footer(fig, x0=m, x1=1.0 - m, y=L.get('footer_y', 0.01))
    return fig
