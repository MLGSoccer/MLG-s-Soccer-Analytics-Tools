"""Active minutes: xG - the path. CBS chart.

Signed off by the owner 2026-10-14 (form R12-A1, then the accepted raw mock
mockups/ideas/possession_threat/mock_path/, v5). This module REPRODUCES that
mock; a difference from it is a question for the owner, not a design call.

Construction: a twin staircase on one proportional scale, "vs league average".
Red = the team, blue = the team's opponents. Both leave the league-average mark
together. Each riser is that stat's OWN ratio to its league average, drawn on a
log scale and printed nowhere (owner: a riser carries no number, and no xG may
be attributed to the minutes column). The levels after each step are real
per-game figures against their league averages: active minutes, then shots,
then xG. It ends on xG difference, the thing the chart is about.

League averages are league-wide: mean active minutes of the teams; all
minutes / all shots; all xG / all shots. Their product is the league's mean xG
per game (= mean xG against), so one start serves both lines. Ranks: one
ordering per stat (1 = most minutes / shortest wait per shot / highest xG per
shot); both sides are ranked on it, the team among teams, its opponents among
the teams' opponents.
"""
import math

import matplotlib.pyplot as plt

from shared.styles import BG_COLOR, GRID_COLOR, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer, fit_fontsize

RED = '#F0524F'      # the mock's subject colour (Arsenal); now the club's own colour, resolved per club
BLUE = '#6FB3F2'     # the mock's opponents colour; kept unless it clashes with the club's
AMBER = '#D9A441'    # opponents when the club's colour is itself close to BLUE (owner-accepted mock, critique round 8;
                     # #F2B84B at 8.8:1 out-shouted the club line - round 20 takes the default blue's luminance, 7.0:1)
CLASH = 150          # RGB distance below which two lines read as one (shared.colors.check_color_similarity)
FG, FG2 = TEXT_PRIMARY, TEXT_SECONDARY
NL = chr(10)

KICKER = 'ACTIVE MINUTES: xG'
COL_HEADS = ('Active minutes in possession', 'Active minutes per shot', 'xG per shot', 'xG difference')

FIGSIZE = {'16x9': (16, 9), '9x16': (10.8, 19.2), '9x8': (10.8, 9.6)}

# Layouts, copied from the accepted mock's three scripts.
LAYOUT = {
    '16x9': dict(
        axes=[0.02, 0.035, 0.955, 0.665],
        X=[2.1, 3.75, 5.4], XE=6.9, XT=0.72, tick_gap=0.1, xlim=(-0.05, 7.8), axis_title_x=-0.02,
        # one_line=True was the mock's 16:9 (labels LEFT of the step, over the previous column's segment); right of
        # the step as in 9:16 / 9:8 after critique rounds 1-6 (owner, round 9: "its really important that the number
        # is associated with the step but if after works and is necessary that's fine")
        pad=0.08, lw=5, start_off=0.011, one_line=False, label_side='right', base_up=0.02, base_dn=0.055,
        stack=0.049, big_gap=0.075,
        fs=dict(tick=14, axis_title=15, avg=15, val=20, rank=15, big=40, head=18),
        heads=['Active minutes' + NL + 'in possession', 'Active minutes' + NL + 'per shot',
               'xG per' + NL + 'shot', 'xG' + NL + 'difference'],
        header=dict(x=0.025, yk=0.955, yh=0.905, yr=0.862, ys=0.828, fk=14, fh=34, fsub=14, rule_w=0.05, rule_lw=4),
        key=dict(y=0.905, x0=0.62, x1=0.77, len=0.025, gap=0.006, fs=16, lw=5),
        footer_y=0.012),
    '9x16': dict(
        axes=[0.02, 0.16, 0.95, 0.60],
        X=[2.05, 3.85, 5.65], XE=7.35, XT=0.95, tick_gap=0.1, xlim=(-0.05, 8.95), axis_title_x=-0.02,
        pad=0.09, lw=6, start_off=0.008, one_line=False, label_side='right', base_up=0.02, base_dn=0.05,
        stack=0.030, big_gap=0.042,     # stack (value to rank) 0.045 in the mock: 24 px apart, a stacked rank sat
                                        # halfway between two values (critique round 6); now ~8 px
        fs=dict(tick=17, axis_title=18, avg=17, val=23, rank=18, big=44, head=19),
        heads=['Active' + NL + 'minutes' + NL + 'in possession', 'Active' + NL + 'minutes' + NL + 'per shot',
               'xG per' + NL + 'shot', 'xG' + NL + 'difference'],
        header=dict(x=0.05, yk=0.955, yh=0.925, yr=0.900, ys=0.882, fk=20, fh=52, fsub=19, rule_w=0.09, rule_lw=6),
        key=dict(y=0.855, x0=0.05, x1=0.44, len=0.05, gap=0.015, fs=22, lw=6),
        footer_y=0.012),
    '9x8': dict(
        axes=[0.02, 0.025, 0.95, 0.64],
        X=[2.05, 3.85, 5.65], XE=7.35, XT=0.95, tick_gap=0.1, xlim=(-0.05, 8.95), axis_title_x=-0.02,
        pad=0.09, lw=5, start_off=0.010, one_line=False, label_side='right', base_up=0.02, base_dn=0.05,
        stack=0.047, big_gap=0.07,      # 0.062 in the mock; tightened with the 9:16 (critique round 6)
        fs=dict(tick=14, axis_title=15, avg=14, val=19, rank=15, big=36, head=16),
        # three-line breaks as on 9:16: 17 px apart, "Active minutes Active minutes" ran together (owner, 2026-10-17:
        # "run together is ALWAYS unacceptable")
        heads=['Active' + NL + 'minutes' + NL + 'in possession', 'Active' + NL + 'minutes' + NL + 'per shot',
               'xG per' + NL + 'shot', 'xG' + NL + 'difference'],
        header=dict(x=0.04, yk=0.955, yh=0.905, yr=0.862, ys=0.828, fk=15, fh=38, fsub=15, rule_w=0.08, rule_lw=5),
        key=dict(y=0.905, x0=0.47, x1=0.66, len=0.05, gap=0.01, fs=17, lw=5),
        footer_y=0.006),
}


MINUS = chr(0x2212)      # the true minus sign (label editor #9); written as chr() - the source stays ASCII


def signed(s):
    """a formatted number with its leading hyphen as a true minus sign"""
    return MINUS + s[1:] if s.startswith('-') else s


def mss(minutes):
    """time as '2m 06s' (label editor #2: '2:06' read as a clock time)"""
    s = int(round(minutes * 60))
    return '%dm %02ds' % (s // 60, s % 60)


_S = 1.0          # data-per-pixel of the current frame / of the mock's fixed frame (set in _draw)


def _u(v):
    """a data-unit offset tuned on the mock's frame, at the same PIXEL size on this frame"""
    return v * _S


FMT = (lambda v: '%.1f min' % v, mss, lambda v: '%.3f' % v)
FOOTNOTE = 'Active minutes: ball in play only'


def league_figures(ref):
    """the league's figure in each column (owner, 2026-10-16: "avg." approved, then dropped on seeing it rendered)"""
    return [f(v) for f, v in zip(FMT, (ref['mip'], ref['mps'], ref['xps']))]


def _footnote_y(fig, c):
    """The footnote's baseline (figure fraction): a line's lead above the footer's cap height, left on the content
    edge - set as the team profile sets its footnote, at this page's smallest chart type (the tick labels)"""
    px = fig.dpi / 72 / fig.bbox.height
    return c['footer_y'] + 11 * 0.72 * px + c['fs']['tick'] * 1.30 * px


def _footnote(fig, ax, c, X):
    """'Active minutes: ball in play only' (owner, label edit 2026-10-16: "good if space allows"). Where it reaches
    into the plot's bottom margin (16:9, 9:8) it stays in the left gutter, under the % labels and clear of the first
    column's labels (West Ham / Liverpool 9:8: a step label under the lowest level met it), broken after the colon
    when one line will not fit there. Returns its lines, top first."""
    r = fig.canvas.get_renderer()
    fy = _footnote_y(fig, c)
    gutter = ax.transData.transform((X[0] - c['pad'], 0))[0]
    in_plot = fy * fig.bbox.height + 2 * c['fs']['tick'] * fig.dpi / 72 > ax.get_position().y0 * fig.bbox.height
    out = []
    for lines in ([FOOTNOTE], FOOTNOTE.replace(': ', ':' + NL).split(NL)):
        for t in out:
            t.remove()
        lead = c['fs']['tick'] * 1.30 * fig.dpi / 72 / fig.bbox.height
        out = [fig.text(c['header']['x'], fy + (len(lines) - 1 - i) * lead, ln, color=TEXT_MUTED,
                        fontsize=c['fs']['tick'], ha='left', va='baseline') for i, ln in enumerate(lines)]
        if not in_plot or max(t.get_window_extent(renderer=r).x1 for t in out) < gutter:
            break
    return out


def league_xg(start):
    return '%.2f' % start


def opponents_label(name):
    return name + ("' opponents" if name.endswith('s') else "'s opponents")


def compute(table, team_id, colours=(RED, BLUE)):
    """League references, both sides' levels and ranks, from the per-team table (shared.active_minutes)."""
    rows = table.to_dict('records')
    n = len(rows)
    tot_min = sum(r['mip'] for r in rows)
    tot_shots = sum(r['shots'] for r in rows)
    tot_xg = sum(r['xg'] for r in rows)
    ref = dict(mip=tot_min / n, mps=tot_min / tot_shots, xps=tot_xg / tot_shots)
    start = ref['mip'] / ref['mps'] * ref['xps']
    me = next(r for r in rows if r['teamId'] == team_id)
    better_high = (True, False, True)

    def rank(value, pool, k):
        if better_high[k]:
            return 1 + sum(1 for v in pool if v > value + 1e-12)
        return 1 + sum(1 for v in pool if v < value - 1e-12)

    sides = {'att': dict(col=colours[0], keys=('mip', 'mps', 'xps'), xg=me['xg']),
             'def': dict(col=colours[1], keys=('opp_mip', 'opp_mps', 'opp_xps'), xg=me['opp_xg'])}
    for s in sides.values():
        v = [me[k] for k in s['keys']]
        s['vals'] = v
        m = [v[0] / ref['mip'], ref['mps'] / v[1], v[2] / ref['xps']]
        L = [0.0]
        for x in m:
            L.append(L[-1] + math.log(x))
        s['L'] = L
        s['rank'] = [rank(v[i], [r[s['keys'][i]] for r in rows], i) for i in range(3)]
    xgd = me['xg'] - me['opp_xg']
    xgd_rank = 1 + sum(1 for r in rows if r['xg'] - r['opp_xg'] > xgd + 1e-12)
    # the PRINTED difference is the difference of the printed ends, so the frame's own subtraction holds
    # (Burnley 2.00 - 0.86 printed -1.13 from the unrounded values; the rank still uses the exact values)
    xgd_shown = round(me['xg'], 2) - round(me['opp_xg'], 2)
    return dict(ref=ref, start=start, sides=sides, xgd=xgd_shown, xgd_rank=xgd_rank, n=n, team=me['team'])


MIN_SPAN = math.log(1.20) - math.log(0.85)   # the narrowest data window: ~35% of league average


def _ylim(D, h_px=None, c=None, dpi=100):
    """The FITTED frame (owner, 2026-10-15). The data window is the club's own: every level of both lines and the
    league line (0), never narrower than MIN_SPAN (a near-average club's 5% gap must not be blown up into a cliff;
    the window then centres on the data). Above and below it, a FIXED PIXEL margin for what the labels need there -
    a step label's value and rank, the end value's hang - so the margin is the same on a squeezed club and a wide
    one, and the data takes whatever height is left. Without h_px: the mock's old frame (for comparison)."""
    levels = [y for s in D['sides'].values() for y in s['L']] + [0.0]
    if h_px is None:
        lo = min(math.log(0.47), min(levels) - 0.10)
        hi = max(math.log(1.40), max(levels) + 0.13)
        return lo, hi
    d_lo, d_hi = min(levels), max(levels)
    if d_hi - d_lo < MIN_SPAN:
        mid = (d_lo + d_hi) / 2
        d_lo, d_hi = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
    val_px = c['fs']['val'] * dpi / 72
    pad_px = 3.4 * val_px            # a step label (value over rank) and the end value's hang, with air
    top_px = pad_px + 1.4 * c['fs']['axis_title'] * dpi / 72 + 12    # ... and, above, the 'vs league average' title
    per_px = (d_hi - d_lo) / max(h_px - pad_px - top_px, 1)
    return d_lo - pad_px * per_px, d_hi + top_px * per_px


def _pct_ticks(ylo, yhi, h_px, keep_out):
    """% gridlines at a sensible step: the smallest of 5 / 10 / 20 / 25 / 50 whose ticks sit at least 55 px apart
    on the log scale; none in `keep_out` (pixel bands: the axis title, the 'league average' label)."""
    per = h_px / (yhi - ylo)
    p_lo = (math.exp(ylo) - 1) * 100
    p_hi = (math.exp(yhi) - 1) * 100
    for step in (5, 10, 20, 25, 50):
        ps = [p for p in range(int(math.floor(p_lo / step)) * step, int(math.ceil(p_hi / step)) * step + 1, step)
              if p != 0 and p > -100 and ylo < math.log(1 + p / 100) < yhi]
        ys = sorted(math.log(1 + p / 100) for p in ps + [0])
        if all((b - a) * per >= 55 for a, b in zip(ys, ys[1:])):
            break
    out = []
    for p in ps:
        y_px = (math.log(1 + p / 100) - ylo) * per
        if any(lo_ <= y_px <= hi_ for lo_, hi_ in keep_out):
            continue
        out.append(p)
    return out


def _draw(fig, ax, D, c):
    ref, sides = D['ref'], D['sides']
    a, d = sides['att'], sides['def']
    X, XE, XT = c['X'], c['XE'], c['XT']
    ax.set_facecolor(BG_COLOR)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_xlim(*c['xlim'])
    h_px = ax.get_position().height * fig.bbox.height
    ylo, yhi = _ylim(D, h_px, c, fig.dpi)
    ax.set_ylim(ylo, yhi)
    fs = c['fs']
    per_px = (yhi - ylo) / h_px
    global _S
    _S = per_px / ((math.log(1.40) - math.log(0.47)) / h_px)
    c = dict(c, stack=c['stack'] * _S, start_off=c['start_off'] * _S, base_up=c['base_up'] * _S,
             base_dn=c['base_dn'] * _S, big_gap=c['big_gap'] * _S)
    title_px = 1.4 * fs['axis_title'] * fig.dpi / 72          # the axis title's centre, under the plot's top edge
    zero_px = (0 - ylo) / per_px
    lab_px = fs['tick'] * fig.dpi / 72                         # one tick-label line
    title_h = fs['axis_title'] * fig.dpi / 72
    fn_lines = _footnote(fig, ax, c, X)
    fn_px = (fn_lines[0].get_window_extent().y1 - ax.get_position().y0 * fig.bbox.height) + 8
    keep_out = [(h_px - title_px - title_h / 2 - 30 - lab_px / 2, h_px + 1),   # the title, 30 px clear of the top % label
                (zero_px - lab_px - 8, zero_px + lab_px + 8),  # the two-line 'league average' label on the dash
                (-1, fn_px + lab_px / 2)]                      # the footnote, where it reaches into the plot
    for p in _pct_ticks(ylo, yhi, h_px, keep_out):
        y = math.log(1 + p / 100)
        ax.plot([XT + c['tick_gap'], XE], [y, y], color=GRID_COLOR, lw=0.8, alpha=0.32, zorder=0)
        ax.text(XT, y, signed('%+d%%' % p), color=FG2, fontsize=fs['tick'], ha='right', va='center')
    ax.plot([XT + c['tick_gap'], XE], [0, 0], color=FG2, lw=1.8, ls=(0, (5, 4)), alpha=0.9, zorder=3)
    ax.text(XT, 0, 'league' + NL + 'average', color=FG, fontsize=fs['tick'], weight='bold', ha='right',
            va='center', linespacing=1.0)
    ax.text(c['axis_title_x'], yhi - title_px * per_px, 'vs league average', color=FG, fontsize=fs['axis_title'],
            weight='bold', ha='left', va='center')
    for x in list(X) + [XE]:
        ax.plot([x, x], [ylo, yhi], color=GRID_COLOR, lw=0.7, alpha=0.18, zorder=0)

    gx = [X[0], X[1], X[1], X[2], X[2], XE]
    gt = [a['L'][1], a['L'][1], a['L'][2], a['L'][2], a['L'][3], a['L'][3]]
    gb = [d['L'][1], d['L'][1], d['L'][2], d['L'][2], d['L'][3], d['L'][3]]
    ax.fill_between(gx, gb, gt, color='#FFFFFF', alpha=0.07, lw=0, zorder=1)

    XS = XT + c['tick_gap'] + 0.05
    off = c['start_off']
    ax.plot([XS, X[0]], [off, off], color=a['col'], lw=c['lw'] * 0.8, zorder=4, solid_capstyle='butt')
    ax.plot([XS, X[0]], [-off, -off], color=d['col'], lw=c['lw'] * 0.8, zorder=4, solid_capstyle='butt')

    r = fig.canvas.get_renderer()
    inv = ax.transData.inverted()

    for key, s in sides.items():
        L = s['L']
        xs = [X[0], X[0], X[1], X[1], X[2], X[2], XE]
        ys = [L[0], L[1], L[1], L[2], L[2], L[3], L[3]]
        # the club's line on top where the two coincide - it is the subject (critique round 8)
        ax.plot(xs, ys, color=s['col'], lw=c['lw'], solid_joinstyle='miter', solid_capstyle='butt',
                zorder=5.2 if key == 'att' else 5)

    # Placement (critique round 3, designer D2-1): each league figure stays ON the dashed line, just under or just
    # over it - it names the line. A step label never crosses the dashed line: one that would flips to the other
    # side of its own line (Palace's opponents' 2:29). Round 2 let the figures search for space instead, and they
    # drifted up to 130 px off the line onto the teams' levels (Spurs, Newcastle, Palace).
    dash_px = ax.transData.transform((0, 0))[1]
    gap_px = 8
    row_px = 0.075 * fig.bbox.width     # ~120 px on the 16:9: a same-row neighbour that close reads as one string
    lw_px = c['lw'] * fig.dpi / 72

    def shift_px(texts, dy):
        for t in texts:
            x, y = t.get_position()
            t.set_position((x, inv.transform((0, ax.transData.transform((0, y))[1] + dy))[1]))

    def extent(texts):
        bbs = [t.get_window_extent(renderer=r) for t in texts]
        return min(b.y0 for b in bbs), max(b.y1 for b in bbs)

    def crosses(texts, level):
        """On or across the dashed line - or wholly on its far side from the level it names (16:9 labels sit over
        the segment BEFORE the step, so they can clear the dash and still be on the wrong side of it)."""
        y0, y1 = extent(texts)
        if y0 - gap_px / 2 < dash_px < y1 + gap_px / 2:
            return True
        ly = ax.transData.transform((0, level))[1]
        # however close the line is: Spurs' 27.2 min (-0.4%, 2 px under the dash) printed above it read as above 27.3
        return abs(ly - dash_px) > 0.5 and (ly - dash_px) * ((y0 + y1) / 2 - dash_px) < 0

    # every drawn segment of each line, in pixels, for "which line is this label nearest?" (round 3: a flipped label
    # landed 6 px under the OTHER line - Spurs' opponents' 0.113, 16:9)
    segs_px = {}
    for key, s in sides.items():
        L = s['L']
        y_stub = off if key == 'att' else -off
        pts = [((XS, y_stub), (X[0], y_stub), True)]
        nxt = list(X[1:]) + [XE]
        prev = y_stub
        for i in range(3):
            pts.append(((X[i], prev), (X[i], L[i + 1]), False))
            pts.append(((X[i], L[i + 1]), (nxt[i], L[i + 1]), True))
            prev = L[i + 1]
        segs_px[key] = [(ax.transData.transform(p0), ax.transData.transform(p1), flat) for p0, p1, flat in pts]

    def nearest_px(texts, key, flats_only=False, risers=False):
        bbs = [t.get_window_extent(renderer=r) for t in texts]
        bx0, bx1 = min(b.x0 for b in bbs), max(b.x1 for b in bbs)
        by0, by1 = min(b.y0 for b in bbs), max(b.y1 for b in bbs)
        out = []
        for (x0, y0), (x1, y1), flat in segs_px[key]:
            if flats_only:
                if flat and (min(x0, x1) >= bx1 or max(x0, x1) <= bx0):
                    continue        # only the levels the label sits over or under
                if not flat and not risers:
                    continue        # risers count only for a 16:9 label, which ends its step
            dx = max(min(x0, x1) - bx1, bx0 - max(x0, x1), 0)
            dy = max(min(y0, y1) - by1, by0 - max(y0, y1), 0)
            out.append(math.hypot(dx, dy))
        return min(out) if out else float('inf')

    def owned(texts, key, risers=False):
        """Nearer its own line's level than the other line's - of the levels running over or under it - and not
        touching the other line anywhere. Risers and the ends of segments are left out of the comparison: a label
        right of a step always sits a pad from both risers and from the end of the segment before it."""
        other = 'def' if key == 'att' else 'att'
        mine, theirs = nearest_px(texts, key, True, risers), nearest_px(texts, other, True, risers)
        y0, y1 = extent(texts)
        dash_d = max(y0 - dash_px, dash_px - y1, 0)
        # nor nearer the DASHED line: Palace's 2:29, 3 px under it and 11 px over its own line, read as the
        # league figure's (round 4)
        return mine <= theirs + 2 and mine <= dash_d + 2 and nearest_px(texts, other) > (gap_px + lw_px) / 2

    col_right = [ax.transData.transform((x, 0))[0] - gap_px for x in list(X[1:]) + [XE]]

    def fits_column(texts, i):
        """Inside its column - or, for the first two columns, past the next step where neither line's riser is at
        that height: a one-row "26.2 min (12/20)" overran a 9:8 / 9:16 column by 25-70 px, and the stack fell back
        to value-over-rank blocks 200 px tall (round 5, Fulham 9:16)."""
        bx0, bx1, by0, by1 = box(texts)
        if bx1 <= col_right[i]:
            return True
        if i == 2:
            return False
        for s in sides.values():
            lo, hi = sorted(ax.transData.transform((0, y))[1] for y in (s['L'][i + 1], s['L'][i + 2]))
            # half the usual gap: a label set the normal 7 px under a line passes under that line's corner
            if by0 < hi + gap_px / 2 + lw_px / 2 and by1 > lo - gap_px / 2 - lw_px / 2:
                return False
        return bx1 <= col_right[i + 1]

    def step_label(key, i, up, compact=False):
        s = sides[key]
        other = d if key == 'att' else a
        L, y1 = s['L'], s['L'][i + 1]
        val = FMT[i](s['vals'][i])
        rk = '(%d/%d)' % (s['rank'][i], D['n'])
        if compact:
            # value and rank on one row, right of the riser, beside the level it names: fits between two close
            # lines, and is the form both labels take when they share a side (a self-contained row each)
            y_, va = (y1 + _u(0.012), 'bottom') if up else (y1 - _u(0.012), 'top')
            t_val = ax.text(X[i] + c['pad'], y_, val, color=s['col'], fontsize=fs['val'], weight='bold',
                            ha='left', va=va, zorder=7)
            bb = t_val.get_window_extent(renderer=r)
            t_rank = ax.text(inv.transform((bb.x1, bb.y0))[0] + c['pad'] * 0.6, y_, rk, color=FG2,
                             fontsize=fs['rank'], ha='left', va=va, zorder=7)
            return [t_val, t_rank]
        if c['one_line']:
            # LEFT of the riser: shares x with the segment BEFORE the step - of BOTH lines - and, on the first
            # step, with the start stubs. Clear all of them (Man City's step back; Spurs on the league line).
            under = [L[i], y1, other['L'][i]] + ([off, -off] if i == 0 else [])
            ya = max(under) if up else min(under)
            dy = c['base_up'] if up else -c['base_dn']
            t_rank = ax.text(X[i] - c['pad'], ya + dy, rk, color=FG2, fontsize=fs['rank'], ha='right',
                             va='baseline', zorder=7)
            bb = t_rank.get_window_extent(renderer=r)
            x_left = inv.transform((bb.x0, bb.y0))[0]
            t_val = ax.text(x_left - c['pad'] * 0.6, ya + dy, val, color=s['col'], fontsize=fs['val'],
                            weight='bold', ha='right', va='baseline', zorder=7)
            return [t_val, t_rank]
        lx = X[i] + c['pad']
        if up:
            return [ax.text(lx, y1 + _u(0.012) + c['stack'], val, color=s['col'], fontsize=fs['val'], weight='bold',
                            ha='left', va='bottom', zorder=7),
                    ax.text(lx, y1 + _u(0.012), rk, color=FG2, fontsize=fs['rank'], ha='left', va='bottom', zorder=7)]
        # below the line: the rank hangs the SAME gap under the value as it sits under it above the line (from
        # 'stack' less the rank's height) - measured, so a tall value never runs into its rank (round 7, Burnley)
        t_rank = ax.text(lx, y1 + _u(0.012), rk, color=FG2, fontsize=fs['rank'], ha='left', va='bottom', zorder=7)
        rb = t_rank.get_window_extent(renderer=r)
        inside = c['stack'] * (ax.transData.transform((0, 1))[1] - ax.transData.transform((0, 0))[1]) - rb.height
        t_val = ax.text(lx, y1 - _u(0.012), val, color=s['col'], fontsize=fs['val'], weight='bold', ha='left',
                        va='top', zorder=7)
        vb = t_val.get_window_extent(renderer=r)
        t_rank.set_va('top')
        t_rank.set_position((lx, inv.transform((0, vb.y0 - inside))[1]))
        return [t_val, t_rank]

    labels = {}

    def box(texts):
        bbs = [t.get_window_extent(renderer=r) for t in texts]
        return min(b.x0 for b in bbs), max(b.x1 for b in bbs), min(b.y0 for b in bbs), max(b.y1 for b in bbs)

    def drop(texts):
        for t in texts:
            t.remove()

    off_px = ax.transData.transform((0, _u(0.012)))[1] - dash_px
    row_gap = 10          # px between two one-row labels sharing a side

    def lay_labels(flips):
        """Each step label beside its OWN line: the outer side of the pair first (the mock: club above, opponents
        below; `flips` reverses that where a league figure needs the spot), then the other side - the first that
        neither crosses the dashed line nor sits nearer the other line. A label with neither goes on one row right of
        its riser, if that fits and is clear of every other label (16:9: the next column's labels sit left of ITS
        riser). Where the two still cannot be placed apart, they share a side in LINE ORDER, the higher line's on
        top (round 3: a stacked rank read as the other side's value; Fulham's stack came out upside down)."""
        for ts in labels.values():
            drop(ts)
        labels.clear()

        def apart(p, q):
            return p[0] >= q[1] + gap_px or q[0] >= p[1] + gap_px or p[2] >= q[3] + gap_px or q[2] >= p[3] + gap_px

        def free(ts, own):
            bt = box(ts)
            return all(apart(bt, box(o)) for k, o in labels.items() if k != own)

        levels = {}
        for i in range(3):
            for key, s in sides.items():
                other = d if key == 'att' else a
                y1, yo = s['L'][i + 1], other['L'][i + 1]
                outer = y1 >= yo if key == 'att' else y1 > yo
                first = outer != ((key, i) in flips)
                levels[(key, i)] = y1
                for up in (first, not first):
                    ts = step_label(key, i, up)
                    # 16:9 labels sit LEFT of the riser, at the end of the step: the step is theirs to be near
                    if not crosses(ts, y1) and owned(ts, key, risers=c['one_line']):
                        labels[(key, i)] = ts
                        break
                    drop(ts)
        for i in range(3):
            for key in sides:
                if (key, i) in labels:
                    continue
                y1 = levels[(key, i)]
                for up in (y1 >= 0, y1 < 0):
                    ts = step_label(key, i, up, compact=True)
                    if not crosses(ts, y1) and owned(ts, key) and fits_column(ts, i) and free(ts, (key, i)):
                        labels[(key, i)] = ts
                        break
                    drop(ts)
            pair = [labels.get(('att', i)), labels.get(('def', i))]
            if all(pair) and apart(box(pair[0]), box(pair[1])):
                continue
            if all(pair):
                # the two collide: one of them on one row beside its own line may part them (Brentford's
                # opponents just over the dash, 16:9) - before falling back to a stack
                done = False
                for key in ('def', 'att'):
                    y1 = levels[(key, i)]
                    for up in (y1 >= 0, y1 < 0):
                        ts = step_label(key, i, up, compact=True)
                        keep = labels[(key, i)]
                        labels[(key, i)] = ts
                        ok = not crosses(ts, y1) and owned(ts, key) and fits_column(ts, i) and free(ts, (key, i))
                        if ok:
                            drop(keep)
                            done = True
                            break
                        labels[(key, i)] = keep
                        drop(ts)
                    if done:
                        break
                if done:
                    continue
            ya_, yd_ = a['L'][i + 1], d['L'][i + 1]
            px_a, px_d = (ax.transData.transform((0, y))[1] - dash_px for y in (ya_, yd_))
            if px_a * px_d < 0 and min(abs(px_a), abs(px_d)) > 0.5:
                # the lines straddle the dashed line: any shared side puts one label across it (Brentford 16:9).
                # Each keeps its own side, in the clearest spot there, even if it sits nearer the other line.
                for key in ('att', 'def'):
                    if (key, i) in labels and all(apart(box(labels[(key, i)]), box(o)) for k, o in labels.items()
                                                  if k != (key, i)):
                        continue
                    if (key, i) in labels:
                        drop(labels.pop((key, i)))
                    y1 = levels[(key, i)]
                    for up, compact in ((y1 >= 0, True), (y1 >= 0, False)):
                        ts = step_label(key, i, up, compact)
                        if not crosses(ts, y1) and free(ts, (key, i)):
                            labels[(key, i)] = ts
                            break
                        drop(ts)
                    if (key, i) not in labels:          # nothing clear: the plain spot on its own side
                        labels[(key, i)] = step_label(key, i, y1 >= 0)
                continue
            for k in (('att', i), ('def', i)):
                if k in labels:
                    drop(labels.pop(k))
            ya_, yd_ = a['L'][i + 1], d['L'][i + 1]
            hi_k, lo_k = ('att', 'def') if ya_ >= yd_ else ('def', 'att')
            hi_px = ax.transData.transform((0, max(ya_, yd_)))[1]
            lo_px = ax.transData.transform((0, min(ya_, yd_)))[1]
            up = (ya_ + yd_) / 2 >= 0                     # the side away from the dashed line

            def build(compact):
                t_hi = step_label(hi_k, i, up, compact=compact)
                t_lo = step_label(lo_k, i, up, compact=compact)
                sep = row_gap
                if not compact and not c['one_line']:
                    # value-over-rank blocks: the gap BETWEEN the two clearly larger than the gap inside each
                    # (round 3: Fulham's (10/20) read as 28.1 min's rank)
                    vb, rb = t_hi[0].get_window_extent(renderer=r), t_hi[1].get_window_extent(renderer=r)
                    # twice the (tight, round 7) inside gap: groups read at ~2.5:1 without the ~65 px of empty space
                    # the loose mock gap gave (round 5, Fulham 9:16)
                    sep = max(row_gap, 2 * max(vb.y0 - rb.y1, rb.y0 - vb.y1, 0) + 4)
                anchor = compact or not c['one_line']     # 16:9 left-of-riser labels already clear the lines
                if up:      # above both lines: the lower line's label first, the higher line's on top of it
                    if anchor:
                        shift_px(t_lo, hi_px + off_px + lw_px / 2 - box(t_lo)[2])
                    shift_px(t_hi, box(t_lo)[3] + sep - box(t_hi)[2])
                else:       # below both lines: the higher line's label first, the lower line's under it
                    if anchor:
                        shift_px(t_hi, lo_px - off_px - lw_px / 2 - box(t_hi)[3])
                    shift_px(t_lo, box(t_hi)[2] - sep - box(t_lo)[3])
                return t_hi, t_lo

            t_hi, t_lo = build(True)
            if not (fits_column(t_hi, i) and fits_column(t_lo, i) and free(t_hi, (hi_k, i)) and free(t_lo, (lo_k, i))):
                drop(t_hi); drop(t_lo)
                t_hi, t_lo = build(False)
            labels[(hi_k, i)], labels[(lo_k, i)] = t_hi, t_lo

    avgs = league_figures(ref)
    figs = []

    def place_figures():
        """Each league figure just under the dashed line, else just over it. Returns the step labels that block
        both spots - they flip on the next pass."""
        for t in figs:
            t.remove()
        figs.clear()
        owner = {id(t): k for k, ts in labels.items() for t in ts}
        need = set()
        for i, (x, txt) in enumerate(zip(X, avgs)):
            lv_px = [ax.transData.transform((x, y))[1] for y in (a['L'][i + 1], d['L'][i + 1])]
            tries = []
            for y, va in ((-_u(0.012), 'top'), (_u(0.012), 'bottom')):
                ta = ax.text(x + c['pad'], y, txt, color=FG2, fontsize=fs['avg'], ha='left', va=va, zorder=7)
                bb = ta.get_window_extent(renderer=r)
                block = set()
                hit = any(bb.y0 - gap_px - lw_px / 2 <= ly <= bb.y1 + gap_px + lw_px / 2 for ly in lv_px)
                for o in ax.texts:
                    if o is ta or not o.get_text():
                        continue
                    ob = o.get_window_extent(renderer=r)
                    # the row's reach only on the figure's own row; a text a row above or below needs only the gap
                    # (label edit: the wider "avg. 27.3 min" reached the next column's rank a row up and gave up its
                    # spot, pushing "26.2 min" into the other line's stack - Bournemouth 9:16)
                    reach = row_px if ob.y0 < bb.y1 and ob.y1 > bb.y0 else gap_px
                    if ob.x0 < bb.x1 + reach and ob.x1 > bb.x0 - reach and ob.y0 < bb.y1 + gap_px \
                            and ob.y1 > bb.y0 - gap_px:
                        hit = True
                        if id(o) in owner:
                            block.add(owner[id(o)])
                tries.append((ta, hit, block))
                if not hit:
                    break
            # both spots taken: the one that displaces the fewest step labels, under first on a tie (label edit:
            # Palace 9:8's "avg. 2m 11s" kept the under spot from "2m 29s" when over the dash only another league
            # figure was in reach, and "2m 29s" was pushed into the other line's stack)
            keep = next((t for t in tries if not t[1]), None) or min(tries, key=lambda t: len(t[2]))
            for ta, _, _ in tries:
                if ta is not keep[0]:
                    ta.remove()
            figs.append(keep[0])
            if keep[1]:
                need |= min((t[2] for t in tries), key=len)
        return need

    flips = set()
    for _ in range(4):
        lay_labels(flips)
        new = place_figures() - flips
        if not new:
            break
        flips |= new
    _crowded_figures(ax, r, figs, labels, [(a['L'][i + 1], d['L'][i + 1]) for i in range(3)], X, c['pad'],
                     dash_px, gap_px, lw_px, row_px)
    flats = [(min(p0[0], p1[0]), max(p0[0], p1[0]), p0[1]) for k_ in segs_px for p0, p1, fl in segs_px[k_] if fl]
    flats.append((ax.transData.transform((XT + c['tick_gap'], 0))[0], ax.transData.transform((XE, 0))[0], dash_px))
    clr = lw_px / 2 + 4

    def line_hit(ts):
        bx0, bx1, by0, by1 = box(ts)
        return any(x0 < bx1 and x1 > bx0 and by0 - clr < y < by1 + clr for x0, x1, y in flats)

    def text_hit(ts):
        bx0, bx1, by0, by1 = box(ts)
        for o in ax.texts:
            if o in ts or not o.get_text():
                continue
            ob = o.get_window_extent(renderer=r)
            if ob.x0 < bx1 + 4 and ob.x1 > bx0 - 4 and ob.y0 < by1 + 4 and ob.y1 > by0 - 4:
                return True
        return False

    for k_lab, ts in list(labels.items()):
        lvl = sides[k_lab[0]]['L'][k_lab[1] + 1]      # never across the dash from its own line (round 18, Palace)
        if not line_hit(ts):
            continue
        cy = sum(box(ts)[2:]) / 2
        away = -1 if cy < dash_px else 1
        moved = False
        for k_ in range(1, 120):
            for sg in (away, -away):
                shift_px(ts, sg * 2 * k_)
                if not line_hit(ts) and not text_hit(ts) and not crosses(ts, lvl):
                    moved = True
                    break
                shift_px(ts, -sg * 2 * k_)
            if moved:
                break
    # ... and a column's two labels still read in LINE ORDER (owner-accepted, round 8): if the guard moved one past
    # the other, they trade places, the higher line's label on top, the gap between them kept
    for i in range(3):
        ta, td = labels.get(('att', i)), labels.get(('def', i))
        if not ta or not td:
            continue
        ba, bd = box(ta), box(td)
        if (ba[0] < bd[1] and bd[0] < ba[1]) is False:
            continue
        a_high = a['L'][i + 1] >= d['L'][i + 1]
        if ((ba[2] + ba[3]) / 2 > (bd[2] + bd[3]) / 2) == a_high:
            continue
        up_t, lo_t = (ta, td) if (ba[2] + ba[3]) / 2 > (bd[2] + bd[3]) / 2 else (td, ta)
        bu, bl = box(up_t), box(lo_t)
        gap_ = bu[2] - bl[3]
        shift_px(lo_t, bu[3] - bl[3])                      # the should-be-upper label to the top
        shift_px(up_t, box(lo_t)[2] - gap_ - bu[3])       # the other under it, same gap

    # The league's xG per game sits on the dashed line right of the bracket. Where an end value would have to leave
    # its line to clear it (a short bracket: Palace, Newcastle), it goes LEFT of the bracket, on the dash.
    t_lg = ax.text(XE + c['pad'] * 1.5, 0, league_xg(D['start']), color=FG2, fontsize=fs['avg'], ha='left',
                   va='center')
    probe = ax.text(0, 0, 'xG 0.00', fontsize=fs['val'], weight='bold')
    h_end = probe.get_window_extent(renderer=r).height
    probe.remove()
    h_lg = t_lg.get_window_extent(renderer=r).height
    end_px = [ax.transData.transform((0, s['L'][3]))[1] for s in (a, d)]
    lg_moved = False
    if any(abs(y - dash_px) < (h_lg + h_end) / 2 + 2 for y in end_px):
        for y, va in ((_u(0.012), 'bottom'), (-_u(0.012), 'top')):
            t_lg.set_position((XE - c['pad'] * 0.6, y))
            t_lg.set_ha('right'); t_lg.set_va(va)
            bb = t_lg.get_window_extent(renderer=r)
            if any(bb.y0 - gap_px - lw_px / 2 <= ly <= bb.y1 + gap_px + lw_px / 2 for ly in end_px):
                continue
            if any(o is not t_lg and o.get_text() and o.get_window_extent(renderer=r).overlaps(bb)
                   for o in ax.texts):
                continue
            lg_moved = True
            break
        if not lg_moved:
            t_lg.set_position((XE + c['pad'] * 1.5, 0)); t_lg.set_ha('left'); t_lg.set_va('center')

    XB = XE + 0.02
    _end_dimension(fig, ax, r, D, c, a, d, X, XE, XB, t_lg, lg_moved)

    for x, h in zip(list(X) + [XE], c['heads']):
        ax.text(x, yhi + 0.025, h, color=FG, fontsize=fs['head'], weight='bold', ha='center', va='bottom',
                clip_on=False, linespacing=1.05)


def _crowded_figures(ax, r, figs, labels, levels, X, pad, dash_px, gap_px, lw_px, row_px):
    """A league figure with no room just under or over the dashed line - both lines hug it (Newcastle's 2:11
    between +3% and -1%) - goes as near the dash as the lines allow, and any step label in its way moves outward
    past it, away from the dash. The figure stays the thing nearest the line it names."""
    inv = ax.transData.inverted()

    def blocks():
        return [ts for ts in labels.values()]

    def ext(ts):
        bbs = [t.get_window_extent(renderer=r) for t in ts]
        return min(b.x0 for b in bbs), max(b.x1 for b in bbs), min(b.y0 for b in bbs), max(b.y1 for b in bbs)

    def shift(ts, dy):
        for t in ts:
            x, y = t.get_position()
            t.set_position((x, inv.transform((0, ax.transData.transform((0, y))[1] + dy))[1]))

    def meets(e, f, reach=0):
        if not (e[2] < f[3] and e[3] > f[2]):
            reach = 0       # the row's reach only on the same row, as in place_figures (Bournemouth 9:16, label edit)
        return e[0] < f[1] + reach and e[1] > f[0] - reach and e[2] < f[3] + gap_px and e[3] > f[2] - gap_px

    moved = False
    for i, ta in enumerate(figs):
        bb = ta.get_window_extent(renderer=r)
        lv = [ax.transData.transform((0, y))[1] for y in levels[i]]
        on_line = any(bb.y0 - gap_px - lw_px / 2 <= y <= bb.y1 + gap_px + lw_px / 2 for y in lv)
        f = (bb.x0, bb.x1, bb.y0, bb.y1)
        if not on_line and not any(meets(ext(ts), f, row_px) for ts in blocks()):
            continue
        h = bb.y1 - bb.y0
        best = None
        tight = gap_px / 2      # a pinched dash: half the usual clearance keeps the figure ON it (Fulham 9:8 2:11)
        for k in range(0, 80):          # nearest spot, either side, clear of both lines
            for sgn in (-1, 1):
                y0 = dash_px - 2 - h - k * 2 if sgn < 0 else dash_px + 2 + k * 2
                if not any(y0 - tight - lw_px / 2 <= y <= y0 + h + tight + lw_px / 2 for y in lv):
                    best = y0
                    break
            if best is not None:
                break
        if best is None:
            continue
        shift([ta], best - bb.y0)
        moved = True
    if not moved:
        return
    # labels in a figure's way move outward (away from the dash), then same-side labels re-clear each other
    fixed = [ext([t]) for t in figs]
    n_fixed = len(fixed)
    for _ in range(6):
        changed = False
        bl = blocks()
        for ts in bl:
            e = ext(ts)
            out = 1 if (e[2] + e[3]) / 2 > dash_px else -1
            near = [ext(o) for o in bl if o is not ts and (ext(o)[2] + ext(o)[3]) / 2 * out < (e[2] + e[3]) / 2 * out]
            for j, g in enumerate(fixed + near):
                # a figure also needs same-row room: 35 px apart read as one string (Spurs '27.2 min (10/20) 27.3 min')
                if meets(e, g, row_px if j < n_fixed else 0):
                    # past a figure by a DOUBLE gap: at the stack's own rhythm it read as the stack's top row
                    # (Man Utd 9:8 27.3 min over 26.7 min)
                    gp = 2 * gap_px if j < n_fixed else gap_px
                    shift(ts, (g[3] + gp - e[2]) if out > 0 else (g[2] - gp - e[3]))
                    e = ext(ts)
                    changed = True
        if not changed:
            break


def _end_values(ax, c, a, d, XE, opp_prefix=''):
    fs = c['fs']
    t_a = ax.text(XE + c['pad'] * 1.5, a['L'][3], 'xG %.2f' % a['xg'], color=a['col'], fontsize=fs['val'],
                  weight='bold', ha='left', va='center', zorder=7)
    t_d = ax.text(XE + c['pad'] * 1.5, d['L'][3], opp_prefix + 'xG %.2f' % d['xg'], color=d['col'],
                  fontsize=fs['val'], weight='bold', ha='left', va='center', zorder=7)
    return t_a, t_d


def _bb(ts, r):
    bbs = [t.get_window_extent(renderer=r) for t in ts]
    return min(b.x0 for b in bbs), max(b.x1 for b in bbs), min(b.y0 for b in bbs), max(b.y1 for b in bbs)


def _hits(ax, r, ts, gap, skip=()):
    """Any other text within `gap` px of the block."""
    x0, x1, y0, y1 = _bb(ts, r)
    for o in ax.texts:
        if o in ts or o in skip or not o.get_text():
            continue
        ob = o.get_window_extent(renderer=r)
        if ob.x0 < x1 + gap and ob.x1 > x0 - gap and ob.y0 < y1 + gap and ob.y1 > y0 - gap:
            return True
    return False


def _place_league(ax, r, c, X, XE, t_lg, ends, block, lw_px):
    """The league's xG per game (1.42) has ONE home: on the dashed line, just inside the end of the last tread
    (right-aligned a pad left of the line ends), over the dash or under it - the side clear of both lines' last
    segments, of the value and of every label. Never in the end-label column (critique round 12: there it read as a
    third end value), never across a line (Everton), never beside the value on the dash (Liverpool / West Ham).
    The VALUE is placed first and keeps its spot (critique round 13: when the two compete, the value is the one that
    must stay attached to its line); the 1.42 slides left along the dash, first at the usual clearances, then at
    tighter ones, until clear."""
    inv = ax.transData.inverted()
    end_px = [ax.transData.transform((0, y))[1] for y in ends]
    xe_px = ax.transData.transform((XE, 0))[0]
    x_min_px = ax.transData.transform((X[2], 0))[0] + 12
    blk = _bb(block, r) if block else None

    def ok(clear, vgap):
        colm = 30 if vgap >= 24 else (12 if vgap >= 12 else -1e9)
        bb = t_lg.get_window_extent(renderer=r)
        if bb.x0 < x_min_px:
            return False
        if any(bb.y0 - clear < y < bb.y1 + clear for y in end_px):
            return False
        if blk and bb.x0 < blk[1] + vgap and bb.x1 > blk[0] - vgap and bb.y0 < blk[3] + vgap \
                and bb.y1 > blk[2] - vgap:
            return False
        if block:
            bx0_, bx1_, by0_, by1_ = _bb(block, r)
            if bb.x0 < bx1_ + colm and bb.x1 > bx0_ - colm and bb.y0 < by1_ + 45 and bb.y1 > by0_ - 45:
                return False
            vb = block[0].get_window_extent(renderer=r)
            level = abs((bb.y0 + bb.y1) / 2 - (vb.y0 + vb.y1) / 2) < 0.6 * (bb.height + vb.height) / 2 + 12
            if level and max(bb.x0 - vb.x1, vb.x0 - bb.x1) < max(0.075 * ax.figure.bbox.width, 120):
                return False
        # on a row it shares, room[0] px from its neighbour: at 14-17 px "0.113  xG 1.42" read as "0.113 xG" and a
        # stray 1.42 (Palace / Chelsea 9:16, label edit re-critique; the 9:8 pair at 42 px read as two). Where no spot
        # gives 40 px (9:16's 193 px column 3 with both lines hugging the dash: Fulham, Leeds, Villa, Sunderland) the
        # search runs as before - a stricter fallback moved their big values instead (tried)
        for o in ax.texts:
            if o is t_lg or not o.get_text():
                continue
            ob = o.get_window_extent(renderer=r)
            if ob.y0 < bb.y1 and ob.y1 > bb.y0 and max(ob.x0 - bb.x1, bb.x0 - ob.x1) < room[0]:
                return False
        return not _hits(ax, r, [t_lg], 6)

    room = [40]
    for room[0] in (40, -1e9):
        tight = 3 + lw_px / 2
        if block:
            vb_ = block[0].get_window_extent(renderer=r)
            dash_px_ = ax.transData.transform((0, 0))[1]
            # the same offset from the dash as the other league figures (critique round 19: at 3 px - a workaround for
            # Bournemouth's tight gap on the old fixed frame - the dashes struck through the top of "1.42", Newcastle
            # 9:16, Man City 9:8, and its row jogged against "0.113")
            if (vb_.y0 + vb_.y1) / 2 < dash_px_:
                pref = (_u(0.012), 'bottom')
            else:
                pref = (-_u(0.012), 'top')
            for tier, (clear, vgap) in enumerate(((8 + lw_px / 2, 24),)):
                for shift in range(0, 600, 6):
                    x = inv.transform((xe_px - 10 - shift, 0))[0]
                    t_lg.set_position((x, pref[0])); t_lg.set_ha('right'); t_lg.set_va(pref[1])
                    if ok(clear, vgap):
                        return tier
        for tier, (clear, vgap) in enumerate(((8 + lw_px / 2, 24), (tight, 24), (tight, 12), (tight, 6))):
            for shift in range(0, 600, 6):
                x = inv.transform((xe_px - 10 - shift, 0))[0]
                for y, va in ((_u(0.012), 'bottom'), (-_u(0.012), 'top')):
                    t_lg.set_position((x, y)); t_lg.set_ha('right'); t_lg.set_va(va)
                    if ok(clear, vgap):
                        return tier
    t_lg.set_position((inv.transform((xe_px - 10, 0))[0], -_u(0.012))); t_lg.set_ha('right'); t_lg.set_va('top')
    return None


def _end_dimension(fig, ax, r, D, c, a, d, X, XE, XB, t_lg, lg_moved):
    """The END of the path (R13-A1, signed off by the owner 2026-10-15): the bracket is a DIMENSION LINE
    (technical-drawing convention). A terminator tick at each end marks the measured span; the value sits ON the
    line. Wherever ANY part of the span holds the value clear of the dashed line, the value breaks the line there,
    as near the span's middle as the dash allows. Otherwise it hangs on the line's EXTENSION a fixed, short distance
    past one terminator (just clear of that end's label), on the side away from the dashed line.

    Critique round 12: the extension is a grey stroke (never part of the span); the league's 1.42 has one home
    (_place_league); a 10 px gap between the lines' ends and the terminators, and a grey dimension line when the
    club's own line is white-ish (Spurs, Fulham); portrait frames set the value at 0.75x.
    Critique round 13: the value is placed BEFORE the 1.42 and keeps its spot (West Ham 9:16 lost its in-span spot to
    the 1.42); inside, the dash clearance is the same 16 px as outside, so the value stays near the span's middle
    (Man City 9:8 was pushed up under its top end label); the extension is a fixed hang, not wherever the value
    landed (Bournemouth 9:8 78 px, Leeds 9:16 101 px); the extension stroke is 1.6 so it shows at phone size."""
    from shared.colors import color_distance
    fs = c['fs']
    inv = ax.transData.inverted()
    lw_px = c['lw'] * fig.dpi / 72
    portrait = fig.bbox.height > fig.bbox.width
    px_per_x = ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0]
    tick = 0.045
    XB = XE + 10 / px_per_x + tick
    dim_col = FG2 if color_distance(a['col'], FG) < CLASH else FG
    y_hi, y_lo = max(a['L'][3], d['L'][3]), min(a['L'][3], d['L'][3])
    xb_px = ax.transData.transform((XB, 0))[0]
    hi_px, lo_px = (ax.transData.transform((0, y))[1] for y in (y_hi, y_lo))
    dash_px = ax.transData.transform((0, 0))[1]
    axb = ax.get_window_extent(renderer=r)
    for y in (y_hi, y_lo):
        ax.plot([XB - tick, XB + tick], [y, y], color=dim_col, lw=2.4, zorder=6, solid_capstyle='butt')
    # the end values right of the terminators; the league figure leaves this column (placed last, below)
    t_lg.set_position((XE, 0)); t_lg.set_ha('right')
    t_a, t_d = _end_values(ax, c, a, d, XB + tick - c['pad'] * 0.5)
    _spread_end_labels(ax, r, t_a, (t_d,))
    size = round(fs['big'] * (0.75 if portrait else 1.0))
    # THE UNIT SET SMALLER THAN THE NUMBER (label edit 2026-10-16): "+0.15 xG" in one size was ~90 px wider than the
    # old "+0.15" and, hung above the upper end, met column 3's step label ("0.150 (1/20)", Brentford 9:8; Leeds 9:8),
    # so it hung below, across the dash, onto the league's xG. The unit "xG" is set at the end values' size and
    # weight (the "xG 1.60" beside it), on the number's baseline, a word space after it; it follows the number
    # wherever the number goes, and the pair - not the number - is centred or started where the number is set.
    from matplotlib.font_manager import FontProperties
    from matplotlib.transforms import ScaledTranslation
    t_big = ax.text(XB, 0, signed('%+.2f' % D['xgd']), color=FG, fontsize=size, weight='bold', ha='center',
                    va='center', zorder=8)
    desc = [r.get_text_width_height_descent('lp', FontProperties(size=s_, weight='bold'), ismath=False)[2]
            for s_ in (size, fs['val'])]
    probe = ax.text(0, 0, '0 0', fontsize=fs['val'], weight='bold')
    sp_px = probe.get_window_extent(renderer=r).width
    probe.set_text('00')
    sp_px -= probe.get_window_extent(renderer=r).width             # one word space at the unit's size
    probe.remove()
    t_unit = ax.annotate('xG', xy=(1, 0), xycoords=t_big, textcoords='offset points',
                         xytext=(sp_px * 72 / fig.dpi, (desc[0] - desc[1]) * 72 / fig.dpi), color=FG,
                         fontsize=fs['val'], weight='bold', ha='left', va='bottom', zorder=8, annotation_clip=False)
    w_unit = sp_px + t_unit.get_window_extent(renderer=r).width

    def centre_pair():
        # a centred number shifts left by half the unit, so the PAIR sits centred on the line
        dx = -w_unit / 2 if t_big.get_ha() == 'center' else 0
        t_big.set_transform(ax.transData + ScaledTranslation(dx / fig.dpi, 0, fig.dpi_scale_trans))

    centre_pair()
    t_rk = ax.text(XB, 0, '(%d/%d)' % (D['xgd_rank'], D['n']), color=FG2, fontsize=fs['rank'], ha='center',
                   va='top', zorder=8)
    block = (t_big, t_rk, t_unit)
    skip = (t_lg,)            # the value has priority over the 1.42

    def stack_at(cy_px):
        """number centred at cy_px, rank tight under it (x as the block is set: on the line, or starting at it)"""
        x_px = ax.transData.transform((t_big.get_position()[0], 0))[0]
        t_big.set_position(tuple(inv.transform((x_px, cy_px))))
        bb_ = t_big.get_window_extent(renderer=r)
        t_rk.set_position((t_rk.get_position()[0], inv.transform((x_px, bb_.y0 - 3))[1]))   # x set by place_x

    stack_at(0)
    hb = t_big.get_window_extent(renderer=r).height
    hr = t_rk.get_window_extent(renderer=r).height
    pad = 10
    dash_gap = 16

    def league_fits():
        # the value keeps its spot, but not one that leaves the 1.42 no home (Brentford 9:16, round 13)
        return _place_league(ax, r, c, X, XE, t_lg, (a['L'][3], d['L'][3]), block, lw_px)

    def clear_of_dash():
        x0_, x1_, y0_, y1_ = _bb(block, r)
        return not (y0_ - dash_gap < dash_px < y1_ + dash_gap)

    # inside: centred in the part of the span the dashed line leaves free (the larger part first), else the valid
    # spot nearest that part's centre (round 13: West Ham 9:16's lower part holds it with room either side; Man City
    # 9:8 sat 6 px under its top end, now centred in the upper part)
    inside = False
    lo_in, hi_in = lo_px + pad + lw_px / 2, hi_px - pad - lw_px / 2
    if lo_in < dash_px < hi_in:
        parts = [(dash_px + dash_gap, hi_in), (lo_in, dash_px - dash_gap)]
    else:
        parts = [(lo_in, hi_in)]
    parts = sorted(parts, key=lambda q: q[0] - q[1])          # larger first
    h_block = hb + 3 + hr
    for p0, p1 in parts:
        if p1 - p0 < h_block:
            continue
        centre = (p0 + p1) / 2 + (3 + hr) / 2                 # number centre that centres the block in the part
        for k in range(0, int((p1 - p0) / 2) + 2):
            for sgn in ((1,) if k == 0 else (-1, 1)):
                stack_at(centre + sgn * 2 * k)
                x0_, x1_, y0_, y1_ = _bb(block, r)
                if y0_ < p0 or y1_ > p1:
                    continue
                lf = league_fits()
                if not _hits(ax, r, block, 22, skip=skip) and lf is not None and lf <= 2:
                    inside = True
                    break
            if inside:
                break
        if inside:
            break
    if inside:
        x0_, x1_, y0_, y1_ = _bb(block, r)
        ax.plot([XB, XB], [y_lo, inv.transform((0, y0_ - 6))[1]], color=dim_col, lw=2.4, zorder=6)
        ax.plot([XB, XB], [inv.transform((0, y1_ + 6))[1], y_hi], color=dim_col, lw=2.4, zorder=6)
    else:
        ax.plot([XB, XB], [y_lo, y_hi], color=dim_col, lw=2.4, zorder=6)
        top_lab = t_a if a['L'][3] >= d['L'][3] else t_d
        bot_lab = t_d if top_lab is t_a else t_a
        # THE FIXED HANG (critique round 14): the value's near edge a fixed 20 px past that end's label - twice the
        # value-to-rank gap, so "xG 1.24 / -0.25 / (14/20)" no longer reads as one evenly spaced stack (Everton,
        # Spurs, Chelsea). Side: away from the dashed line, as in every aspect. Where the step-3 labels crowd the
        # centred value (portrait: Palace, Newcastle, Leeds 9:16) it starts at the line instead of centring on it -
        # the extension still runs into it - before it ever switches side or lengthens. It never lengthens to make
        # room for the 1.42, which yields instead. Nor may it sit level with another label within a row's reach
        # (Fulham 9:16 read "0.100 (15/20) | -0.14 (13/20)").
        away = 1 if (lo_px > dash_px - 2 or (hi_px - dash_px) > (dash_px - lo_px)) else -1
        gap_lab = 20
        row_px = max(0.075 * fig.bbox.width, 120)
        others = [o for o in ax.texts if o.get_text() and o not in block and o not in (t_a, t_d, t_lg)]

        right_px = fig.bbox.width * (1 - c['header']['x'])                    # the legend's / credit's right edge
        head_px = ax.transData.transform((0, ax.get_ylim()[1] + 0.025))[1]   # bottom of the column headers

        def place_x(mode):
            # the unit follows the number; the rank is centred under number + unit in both modes (label edit
            # re-critique: left-aligned under a started value, West Ham 9:8's "(17/20)" read as column 3's "0.107" rank)
            if mode == 'centre':
                for t in (t_big, t_rk):
                    t.set_ha('center'); t.set_position((XB, t.get_position()[1]))
            else:
                w_num = t_big.get_window_extent(renderer=r).width + w_unit
                x0_px = min(xb_px - 8, right_px - w_num)
                t_big.set_ha('left'); t_big.set_position((inv.transform((x0_px, 0))[0], t_big.get_position()[1]))
                t_rk.set_ha('center')
                t_rk.set_position((inv.transform((x0_px + w_num / 2, 0))[0], t_rk.get_position()[1]))
            centre_pair()

        def hang(side, extra, mode):
            place_x(mode)
            if side > 0:
                base = max(hi_px + 12, top_lab.get_window_extent(renderer=r).y1 + gap_lab) + extra
                stack_at(base + hb / 2 + 3 + hr)
            else:
                top = min(lo_px - 12, bot_lab.get_window_extent(renderer=r).y0 - gap_lab) - extra
                stack_at(top - hb / 2)
            place_x(mode)
            x0_, x1_, y0_, y1_ = _bb(block, r)
            if y0_ < axb.y0 + 4 or y1_ > head_px - 2 or x1_ > right_px + 1:
                return False
            if not clear_of_dash():
                return False
            if _hits(ax, r, block, 22, skip=skip + (t_a, t_d)) or _hits(ax, r, block, gap_lab - 2,
                                                                      skip=tuple(others) + skip):
                return False
            vb = t_big.get_window_extent(renderer=r)
            for o in others:                         # not on a label's row (centres level) within a row's reach
                ob = o.get_window_extent(renderer=r)
                level = abs((ob.y0 + ob.y1) / 2 - (vb.y0 + vb.y1) / 2) < 0.6 * (ob.height + vb.height) / 2
                if level and max(ob.x0 - vb.x1, vb.x0 - ob.x1) < row_px:
                    return False
            return True

        top_lab = t_a if a['L'][3] >= d['L'][3] else t_d
        bot_lab = t_d if top_lab is t_a else t_a
        # away from the dash up to 30 px longer before the near side - a value hung across the dashed line, below
        # two lines that are both above average, is the worse reading (Bournemouth 16:9)
        def crosses_dash():
            x0_, x1_, y0_, y1_ = _bb(block, r)
            span_above = lo_px > dash_px            # both ends above the dash
            span_below = hi_px < dash_px
            return (span_above and y1_ < dash_px) or (span_below and y0_ > dash_px)

        def first(cands, allow_cross=True):
            for side, extra in cands:
                for mode in ('start', 'centre'):
                    if hang(side, extra, mode) and (allow_cross or not crosses_dash()):
                        return (side, extra, mode)
            return None

        found = (first([(away, 0)]) or first([(-away, 0)], allow_cross=False)
                 or first([(away, e) for e in range(3, 31, 3)])
                 or first([(sd, e) for e in range(0, 300, 3) for sd in (-away, away)]))
        if found:
            hang(*found)
            # only if the 1.42 would otherwise sit within 6 px of the value (Leeds 9:8: both lines and the league
            # figures pin it to the dash beside the value) does the hang lengthen, by the least that frees it
            tier = league_fits()
            if tier is None or tier >= 3:
                side, extra0, mode = found
                for extra in range(extra0 + 3, extra0 + 48, 3):
                    if hang(side, extra, mode):
                        t2 = league_fits()
                        if t2 is not None and t2 <= 2:
                            break
                else:
                    hang(*found)
        else:
            hang(away, 0, 'centre')
        x0_, x1_, y0_, y1_ = _bb(block, r)
        if y0_ > hi_px:     # above: the extension from the upper terminator up to the value
            ax.plot([XB, XB], [inv.transform((0, hi_px + 4))[1], inv.transform((0, y0_ - 5))[1]], color=FG2,
                    lw=1.6, ls=(0, (1, 2.2)), zorder=6, solid_capstyle='round', dash_capstyle='round')
        else:               # below
            ax.plot([XB, XB], [inv.transform((0, y1_ + 5))[1], inv.transform((0, lo_px - 4))[1]], color=FG2,
                    lw=1.6, ls=(0, (1, 2.2)), zorder=6, solid_capstyle='round', dash_capstyle='round')
    league_fits()


def _spread_end_labels(ax, r, fixed, movable):
    """The two end values and the league's 1.42 share one x. When a line ends near the league line or near the
    other line (Chelsea, Newcastle, Spurs) they overprint; push them apart vertically, the 1.42 staying put on
    its line. Arsenal's are far apart and do not move."""
    inv = ax.transData.inverted()

    def half(t):
        bb = t.get_window_extent(renderer=r)
        return (inv.transform((0, bb.y1))[1] - inv.transform((0, bb.y0))[1]) / 2

    gap = _u(0.008)
    items = sorted([(t.get_position()[1], t) for t in (fixed,) + tuple(movable)], key=lambda e: e[0])
    k = next(i for i, (_, t) in enumerate(items) if t is fixed)
    for direction, rng in ((1, range(k + 1, len(items))), (-1, range(k - 1, -1, -1))):
        prev_y, prev_t = items[k]
        for i in rng:
            y, t = items[i]
            need = half(prev_t) + half(t) + gap
            if direction * (y - prev_y) < need:
                y = prev_y + direction * need
                t.set_position((t.get_position()[0], y))
            prev_y, prev_t = y, t


def _spaced(t):
    return (chr(0x2009) * 3).join(chr(0x2009).join(w) for w in t.split(' '))


def _header(fig, h, headline, subline, sub_col=RED):
    fig.text(h['x'], h['yk'], _spaced(KICKER), color=FG2, fontsize=h['fk'], weight='bold', va='center')
    # a portrait frame cannot hold 'WOLVERHAMPTON WANDERERS' at the mock's size; the family's fit_fontsize
    fh = fit_fontsize(fig, headline, h['fh'], max_frac=1 - 2 * h['x'])
    head = fig.text(h['x'], h['yh'], headline, color=FG, fontsize=fh, weight='bold', va='center')
    fig.lines.append(plt.Line2D([h['x'] + 0.002, h['x'] + 0.002 + h['rule_w']], [h['yr'], h['yr']], color=sub_col,
                                lw=h['rule_lw'], transform=fig.transFigure, solid_capstyle='butt'))
    sub = fig.text(h['x'], h['ys'], subline, color=FG2, fontsize=h['fsub'], va='center')
    return head, sub


def _key(fig, k, team, h, head, sub, colours=(RED, BLUE)):
    """The key, at the mock's position when it fits. Long club names (Manchester City, Wolverhampton Wanderers,
    Brighton & Hove Albion) do not; then, in order: right-aligned on its own row; right-aligned on the sub-line
    row; a row of its own under the sub-line; the two entries stacked."""
    r = fig.canvas.get_renderer()
    W, Hpx = fig.bbox.width, fig.bbox.height
    labels = (team, opponents_label(team))
    right = 1 - h['x']

    def width(s_):
        t = fig.text(0, 0, s_, fontsize=k['fs']); w = t.get_window_extent(renderer=r).width / W; t.remove()
        return w

    item = [k['len'] + k['gap'] + width(s_) for s_ in labels]
    sep = 0.02
    head_right = head.get_window_extent(renderer=r).x1 / W
    sub_right = sub.get_window_extent(renderer=r).x1 / W
    on_head_row = abs(k['y'] - h['yh']) < 0.02
    row_h = k['fs'] * 1.7 / 72 / (Hpx / fig.dpi)          # one key row, as a figure fraction

    def ok(x0, x1, y, clear_of):
        return x0 >= h['x'] - 1e-9 and x1 + item[1] <= right + 1e-9 and x0 > clear_of + sep - 1e-9             if clear_of is not None else x0 >= h['x'] - 1e-9 and x1 + item[1] <= right + 1e-9

    x0 = k['x0']; x1 = max(k['x1'], x0 + item[0] + sep)
    head_clear = head_right if on_head_row else None
    rows = [(x0, x1, k['y'], head_clear)]                                  # the mock
    x1r = right - item[1]; rows.append((x1r - sep - item[0], x1r, k['y'], head_clear))     # same row, right
    if on_head_row:
        rows.append((x1r - sep - item[0], x1r, h['ys'], sub_right))       # sub-line row, right
        y_own = h['ys'] - (h['yh'] - h['ys']) * 0.75
        rows.append((h['x'], h['x'] + item[0] + 2 * sep, y_own, None))    # own row under the sub-line
    place = next(((a_, b_, y_) for a_, b_, y_, c_ in rows if ok(a_, b_, y_, c_)), None)
    if place:
        entries = [(place[0], place[2], 0), (place[1], place[2], 1)]
    else:                                                                  # stacked
        y0 = k['y'] if not on_head_row else h['ys'] - (h['yh'] - h['ys']) * 0.75
        entries = [(h['x'], y0, 0), (h['x'], y0 - row_h, 1)]
    out = []
    for x, y, i in entries:
        fig.lines.append(plt.Line2D([x, x + k['len']], [y, y], color=colours[i], lw=k['lw'],
                                    transform=fig.transFigure))
        out.append(fig.text(x + k['len'] + k['gap'], y, labels[i], color=FG, fontsize=k['fs'], va='center'))
    return out


def _clear_key_of_heads(fig, ax, key_texts, heads):
    """A key that wrapped onto two rows (Wolverhampton Wanderers, 9:16) sat 10 px above the column headers. Move
    the plot - headers and all - down until there is a clear gap. Short names never trigger it."""
    r = fig.canvas.get_renderer()
    Hpx = fig.bbox.height
    gap_px = 0.012 * Hpx
    key_bottom = min(t.get_window_extent(renderer=r).y0 for t in key_texts)
    heads_top = max(t.get_window_extent(renderer=r).y1 for t in heads)
    need = heads_top + gap_px - key_bottom
    if need > 0:
        p0 = ax.get_position()
        ax.set_position([p0.x0, p0.y0 - need / Hpx, p0.width, p0.height])


def _clear_footnote(fig, ax):
    """The % labels keep off the footnote when they are set (_draw), but a wrapped key or header then moves the plot
    down (_clear_key_of_heads): a label that now meets the footnote goes, with its gridline (Nottingham Forest 9:8,
    "-20%", after the 9:8 headers went to three lines)."""
    r = fig.canvas.get_renderer()
    parts = [FOOTNOTE] + FOOTNOTE.replace(': ', ':' + NL).split(NL)
    fn = [t.get_window_extent(renderer=r) for t in fig.texts if t.get_text() in parts]
    if not fn:
        return
    top, right = max(b.y1 for b in fn) + 8, max(b.x1 for b in fn) + 8
    for t in list(ax.texts):
        txt = t.get_text()
        if not (txt.endswith('%') and txt[:1] in ('+', MINUS)):
            continue
        b = t.get_window_extent(renderer=r)
        if b.y0 < top and b.x0 < right:
            y = t.get_position()[1]
            for ln in list(ax.lines):
                yd = ln.get_ydata()
                if len(yd) == 2 and yd[0] == yd[1] == y and ln.get_color() == GRID_COLOR:
                    ln.remove()
            t.remove()


def team_colours(team_id, team_name, feed_color=None):
    """(club, opponents) line colours, the family's way for two lines on this background (xg_race_chart).

    The club: the family resolver (registry by team id, then the feed colour, then a neutral slate), lightened
    in HSL until it clears WCAG 3.5:1 on the background (ensure_line_contrast) - hue kept, so Chelsea stays
    blue and Burnley claret. An RGB-distance test misses a third of clubs (see shared.colors).
    The opponents: the mock's blue unless the club's drawn colour is close to it (the family's pair threshold,
    150), then amber - the opponents are many clubs, not one, so they never take a club's colour. Blue and amber
    are pulled apart by hue, so only the blue pair goes through separate_line_luminance, as the xG race does.
    Amber, not the family's grey fallback: this is the one family chart with a GREY reference layer (league
    figures, ranks, dashed line), and grey opponents read as part of it on 6 of 20 PL clubs (Palace, Chelsea,
    Brighton, Everton, Man City, Newcastle; critique rounds 1-7).
    """
    from shared.colors import color_distance, ensure_line_contrast, separate_line_luminance
    from shared.motherduck import resolve_single_team_colour
    sub = ensure_line_contrast(resolve_single_team_colour(team_name, feed_color, team_id=team_id), BG_COLOR)
    if color_distance(sub, BLUE) < CLASH:
        return sub, AMBER
    return separate_line_luminance(sub, BLUE, BG_COLOR)


def create_active_minutes_chart(table, team_id, competition_label, aspect='16x9', team_name=None, colours=None):
    """table: shared.active_minutes.build_active_minutes(season_id)[0]. competition_label: e.g. 'Premier League 2025/26'.

    colours: (club, opponents); None resolves them with team_colours().
    """
    me = table[table.teamId == team_id].iloc[0]
    team = team_name or me['team']
    if colours is None:
        colours = team_colours(team_id, team, me.get('feed_color'))
    D = compute(table, team_id, colours)
    c = LAYOUT[aspect]
    fig = plt.figure(figsize=FIGSIZE[aspect], dpi=100, facecolor=BG_COLOR)
    ax = fig.add_axes(c['axes'])
    _draw(fig, ax, D, c)
    head, sub = _header(fig, c['header'], team.upper(),
                        f'{competition_label.upper()} | PER GAME | VS LEAGUE AVERAGE', sub_col=colours[0])
    key_texts = _key(fig, c['key'], team, c['header'], head, sub, colours)
    heads = [t for t in ax.texts if t.get_text() in c['heads']]
    _clear_key_of_heads(fig, ax, key_texts, heads)
    _clear_footnote(fig, ax)
    add_cbs_footer(fig, x0=c['header']['x'], x1=1 - c['header']['x'], y=c['footer_y'])
    return fig
