"""
Shared styling constants and utilities for soccer chart builders.
CBS Sports theme styling.
"""
from matplotlib.patches import Rectangle

# Background colors
BG_COLOR = '#1A2332'  # Dark blue-gray background

# Axis and spine colors
SPINE_COLOR = '#556B7F'
GRID_COLOR = '#556B7F'

# CBS branding
CBS_BLUE = '#00325B'
CBS_BLUE_LIGHT = '#2D5B8A'  # Readable on dark background — use for footer/accent text

# Text colors
TEXT_PRIMARY = '#FFFFFF'
TEXT_SECONDARY = '#B8C5D6'
TEXT_MUTED = '#8BA3B8'
TEXT_SUBTLE = '#999999'

# Accent colors
POSITIVE_COLOR = '#2ECC71'  # Green for positive values


# ─── Canonical figure sizes ──────────────────────────────────────────────────
# Use these constants for new charts and when auditing existing ones. Each
# size matches the dominant use case for that chart category.
BROADCAST_FIGSIZE = (16, 9)   # 16:9 — single-match overviews, time series.
                              # Matches HD/4K broadcast and standard digital
                              # surfaces (Twitter cards, YouTube, web hero).
PITCH_FIGSIZE     = (12, 9)   # ~4:3 — pitch-based charts (shot chart,
                              # passing flow). Pitch is roughly 1.5:1 so a
                              # square-ish frame avoids wasted margin.
DASHBOARD_FIGSIZE = (16, 10)  # 4-panel dashboards — slightly taller than
                              # 16:9 to give each panel vertical room.

# In-video overlay variants. These aren't standalone social posts -
# they're chart assets that render as overlays inside PodcastShorts
# vertical shorts. Two cases:
#   9x16 = fullscreen overlay (chart fills the entire short)
#   9x8  = SBS tile overlay (chart fills half of the short while the
#          host fills the other half; the chart shape is wider than
#          tall because it's HALF of a 9:16 frame)
# Design implication: viewer has seconds (not minutes), big typography,
# minimal text density (host carries verbal context), no need for a
# subtitle or legend in tile mode.
BROADCAST_FIGSIZE_9X16 = (9, 16)
PITCH_FIGSIZE_9X16     = (9, 16)
DASHBOARD_FIGSIZE_9X16 = (9, 16)
BROADCAST_FIGSIZE_9X8  = (9, 8)
PITCH_FIGSIZE_9X8      = (9, 8)
DASHBOARD_FIGSIZE_9X8  = (9, 8)


# Aspect vocabulary - canonical strings used as the `aspect` parameter on
# chart functions. Keep this small; add new aspects here first, then
# plumb through each chart that should support them.
ASPECTS = ("16x9", "9x16", "9x8")


def resolve_figsize(aspect: str, category: str = "broadcast") -> tuple[float, float]:
    """Map (aspect, chart-category) to a figsize tuple.

    `aspect` is one of ASPECTS. `category` is one of
    {'broadcast', 'pitch', 'dashboard'} and picks the right default
    when aspect is the default '16x9' (where the figsize differs by
    chart family). In 9x16 and 9x8 modes all categories collapse to a
    single frame size - the output aspect is the constraint, not the
    chart family.
    """
    if aspect == "9x16":
        return BROADCAST_FIGSIZE_9X16
    if aspect == "9x8":
        return BROADCAST_FIGSIZE_9X8
    if category == "pitch":
        return PITCH_FIGSIZE
    if category == "dashboard":
        return DASHBOARD_FIGSIZE
    return BROADCAST_FIGSIZE


def fit_fontsize(fig, text, nominal, *, max_frac=0.94, floor=10, bold=True):
    """Largest fontsize <= `nominal` at which `text` fits `max_frac` of the width.

    Landscape frames are 12-16in wide and almost nothing overruns them, so the
    charts have historically hard-coded a title size. A 9in-wide portrait frame
    is a different proposition: "Wolverhampton Wanderers 2-2 Brighton and Hove
    Albion" overruns a 9in frame by a third at any title size worth using, and
    the failure is silent — matplotlib draws it and lets the ends fall off the
    canvas. Measure before committing to a size.

    Returns a size, and draws nothing. `bold` should match how the text will
    actually be drawn; bold is wider, so leaving it True is the safe default.
    """
    probe = fig.text(0.5, 0.5, text, fontsize=nominal,
                     fontweight='bold' if bold else 'normal')
    fig.canvas.draw()
    frac = (probe.get_window_extent(renderer=fig.canvas.get_renderer()).width
            / (fig.get_size_inches()[0] * fig.dpi))
    probe.remove()
    if frac <= max_frac:
        return nominal
    return max(floor, int(nominal * max_frac / frac))


def style_axis(ax):
    """Apply consistent CBS Sports styling to axis."""
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(SPINE_COLOR)
    ax.spines['bottom'].set_color(SPINE_COLOR)
    ax.tick_params(colors=SPINE_COLOR, labelcolor=TEXT_PRIMARY)
    ax.yaxis.grid(True, linestyle='--', alpha=0.3, color=GRID_COLOR)
    ax.set_axisbelow(True)


def style_axis_full_grid(ax):
    """Apply CBS Sports styling with both x and y grid lines."""
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(SPINE_COLOR)
    ax.spines['bottom'].set_color(SPINE_COLOR)
    ax.tick_params(colors=SPINE_COLOR, labelcolor=TEXT_PRIMARY)
    ax.xaxis.grid(True, linestyle='--', alpha=0.3, color=GRID_COLOR)
    ax.yaxis.grid(True, linestyle='--', alpha=0.3, color=GRID_COLOR)
    ax.set_axisbelow(True)


def add_cbs_footer(fig, data_source='Opta/Stats Perform', x0=0.02, x1=0.98):
    """Add CBS Sports branding footer to figure.

    `x0`/`x1` default to the historic hard-coded margins, so every existing
    caller renders unchanged. Pass them when the chart's content block sits on
    a different margin: on the pass map the footer was landing 27px left of the
    pitch and 4px right of the stat column, which reads as a wobble rather than
    a decision.
    """
    fig.text(x0, 0.01, 'CBS SPORTS', fontsize=11, fontweight='bold', color=CBS_BLUE_LIGHT)
    if data_source:
        fig.text(x1, 0.01, f'DATA: {data_source.upper()}', fontsize=9,
                color=TEXT_MUTED, ha='right')


def draw_event_block(
    fig, events, minute_of,
    *,
    head_y, top, bottom, row_step_max,
    head_size, row_size,
    min_x, name_x, score_x, rule_x0,
    color_of=None, rc_color='#E53935',
    head_left='MATCH EVENTS', head_right='SCORE',
    mark_of=None,
):
    """The match timeline: one row per goal/card, in chronological order.

    This is what the portrait aspects do with the callouts a 16:9 puts ON the
    plot - nine 16pt labels cannot fit 9 inches of width, and a list is what
    the label band was always trying to be. Built for Match Momentum, and
    shared so the xG Race's 9:16 lists the same match the same way: two
    charts of one game appearing in the same short must not disagree about
    how a match reads.

    Rows are DISTRIBUTED across the band rather than stepped from its top at
    a fixed pitch - a 1-1 and a 5-4 both have to fill the same space, and a
    fixed step leaves a hole under a short list. Same rule as the shot
    chart's stat block. But the pitch is CAPPED and the list top-aligned:
    pure distribution works on the shot chart because that block always holds
    about five rows, while a match has as few as two events, and a 1-1 spread
    over the whole band put two lines of text in ~1000px of empty navy - a
    legitimate scoreline reading as a failed render. Capped, the spare space
    falls at the BOTTOM, where it is breathing room.

    Each row: an accent bar in the event's team colour, the broadcast minute,
    the player, and the running score (or RED CARD). The accent bar is what
    says WHICH side, so it takes the chrome lift - a raw navy bar vanishes.

    Args:
        events: chronological dicts carrying 'type' ('goal'/'rc'), 'label',
            'score', optional 'og', and a colour reachable via `color_of`.
        minute_of: callable(ev) -> the broadcast minute string, without the
            apostrophe. Passed rather than read off the event because the
            two callers hold period differently.
        color_of: callable(ev) -> the event's team colour. Defaults to
            ev['color'].
        mark_of: callable(ev) -> the glyph to draw in place of the row's plain
            accent bar, so the row shows the SAME mark the plot above uses.
            A cold viewer reading a 9:16 named the hollow circle and the red
            rectangle on the plot as marks "with nothing on the page defining
            either" - the list explains the EVENTS, and without this it never
            explains the MARKS. Return None to keep the bar for that row.
    """
    if not events:
        return
    from shared.colors import ensure_line_contrast
    if color_of is None:
        def color_of(ev):
            return ev.get('color')

    fig.text(rule_x0, head_y, head_left, ha='left', va='center',
             fontsize=head_size, fontweight='bold', color=TEXT_MUTED)
    fig.text(score_x, head_y, head_right, ha='right', va='center',
             fontsize=head_size, fontweight='bold', color=TEXT_MUTED)
    fig.patches.append(Rectangle(
        (rule_x0, head_y - 0.011), score_x - rule_x0, 0.0008,
        transform=fig.transFigure, facecolor='#31435A', edgecolor='none',
        zorder=3))

    step = min((top - bottom) / max(len(events), 1), row_step_max)
    for i, ev in enumerate(events):
        y = top - step * (i + 0.5)
        is_rc = ev['type'] == 'rc'
        accent = ensure_line_contrast(
            rc_color if is_rc else color_of(ev), BG_COLOR)
        glyph = mark_of(ev) if mark_of is not None else None
        if glyph:
            # Left-anchored on the rule and set smaller than the row: a filled
            # bullet's advance width at row size is wider than the accent bar
            # it replaces, and it ran into the minute column.
            #
            # ALWAYS the team's colour, including on a red-card row. Where the
            # leading mark is a bar, red says "card" and nothing is lost; where
            # it is a GLYPH, the shape already says card, so a red glyph
            # overloads the one channel every other row uses for the team - and
            # at this size red is indistinguishable from a red club. Measured:
            # a cold analyst read the carded side off exactly this tick and got
            # the right answer only because that club happened to play in red.
            fig.text(rule_x0, y, glyph, ha='left', va='center',
                     fontsize=row_size * 0.85,
                     color=ensure_line_contrast(color_of(ev), BG_COLOR),
                     zorder=4)
        else:
            fig.patches.append(Rectangle(
                (rule_x0, y - 0.010), 0.005, 0.020, transform=fig.transFigure,
                facecolor=accent, edgecolor='none', zorder=4))
        fig.text(min_x, y, f"{minute_of(ev)}'", ha='left', va='center',
                 fontsize=row_size, color=TEXT_SECONDARY, zorder=4)
        fig.text(name_x, y, (ev.get('label') or '').upper(), ha='left',
                 va='center', fontsize=row_size,
                 fontweight='bold' if not is_rc else 'normal',
                 fontstyle='italic' if ev.get('og') else 'normal',
                 color=TEXT_PRIMARY if not is_rc else TEXT_SECONDARY, zorder=4)
        right = 'RED CARD' if is_rc else ev.get('score', '')
        fig.text(score_x, y, right, ha='right', va='center', fontsize=row_size,
                 fontweight='bold',
                 color=rc_color if is_rc else TEXT_PRIMARY, zorder=4)

    # Close the table. The header rule spans the full width and PROMISES a
    # table; with two events and a capped row pitch, the rows stopped and
    # nothing said so - two lines under an open-ended header is the visual
    # signature of rows that failed to load. A closing rule bounds the list,
    # so the space beneath it is plainly outside the table rather than
    # missing from it.
    last_y = top - step * (len(events) - 0.5)
    fig.patches.append(Rectangle(
        (rule_x0, last_y - step * 0.5), score_x - rule_x0, 0.0008,
        transform=fig.transFigure, facecolor='#31435A', edgecolor='none',
        zorder=3))


def _has_bg_contrast(color, min_distance=100):
    """True if `color` reads clearly on BG_COLOR. Lazy import to avoid a
    cycle: shared.colors imports nothing from this module, but keep the
    import inline so the constant module stays cheap to load."""
    from shared.colors import color_distance
    return color_distance(color, BG_COLOR) >= min_distance


def render_two_team_score_header(
    fig,
    home_name, home_score, home_color,
    away_name, away_score, away_color,
    *,
    kicker=None,
    custom_title=None,
    fontsize_title=22,
    fontsize_kicker=11,
    y_kicker=0.973,
    y_title=0.942,
    y_bar=0.912,
    bar_height=0.005,
    bar_contrast_edge=False,
    gap=0.012,
):
    """Render a CBS-style two-team score header.

    Layout (top → bottom):
        [optional kicker row]      e.g. "MATCH MOMENTUM" or "x G   R A C E"
        HOME 1-0 AWAY              score-anchored title block:
                                     - whole block centred at x=0.5
                                     - score sits at its true position
                                       within the block
                                     - team names grow outward from score
        [accent bar]               two-half team-color stripe; split at the
                                     score's centre, not the bbox midpoint —
                                     fixes asymmetric-name miscentering
                                     (e.g. Everton vs Manchester City).

    Args:
        fig: matplotlib Figure to render onto.
        home_name, home_score, home_color: home team identity.
        away_name, away_score, away_color: away team identity.
        kicker: small uppercase text above the title (optional).
        custom_title: if set, overrides the auto title and falls back to a
            single ha='center' string with bbox-midpoint bar split, since
            arbitrary user text can't be reliably score-anchored.
        bar_contrast_edge: if True, draw a thin white edge on either bar
            half whose colour fails contrast against BG_COLOR. Used by the
            shot chart for low-contrast brand palettes.
        gap: figure-coord whitespace flanking the score.
        y_kicker, y_title, y_bar: vertical anchors in figure coords.

    Returns:
        dict with figure-coord keys: 'bar_left', 'bar_right', 'bar_split',
        'bar_top'. Useful for placing a subtitle or contextual sub-line.
    """
    if kicker:
        fig.text(0.5, y_kicker, kicker, fontsize=fontsize_kicker,
                 fontweight='bold', color=TEXT_SECONDARY,
                 ha='center', va='center')

    if custom_title:
        title_obj = fig.text(0.5, y_title, custom_title,
                             fontsize=fontsize_title, fontweight='bold',
                             color=TEXT_PRIMARY, ha='center', va='center')
        fig.canvas.draw()
        sb_fig = (title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
                  .transformed(fig.transFigure.inverted()))
        bar_left  = sb_fig.x0
        bar_right = sb_fig.x1
        bar_split = sb_fig.x0 + sb_fig.width / 2
    else:
        # Render each piece at x=0 to measure widths, then reposition so the
        # whole {home  score  away} block is centred at x=0.5. Bar split
        # lands at the score's actual centre — offset from x=0.5 in
        # proportion to the home/away name length difference.
        score_only = f"{home_score}-{away_score}"
        home_obj  = fig.text(0, y_title, home_name.upper(),
                             fontsize=fontsize_title, fontweight='bold',
                             color=TEXT_PRIMARY, ha='left', va='center')
        score_obj = fig.text(0, y_title, score_only,
                             fontsize=fontsize_title, fontweight='bold',
                             color=TEXT_PRIMARY, ha='left', va='center')
        away_obj  = fig.text(0, y_title, away_name.upper(),
                             fontsize=fontsize_title, fontweight='bold',
                             color=TEXT_PRIMARY, ha='left', va='center')
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        inv = fig.transFigure.inverted()
        home_w  = home_obj.get_window_extent(renderer=renderer).transformed(inv).width
        score_w = score_obj.get_window_extent(renderer=renderer).transformed(inv).width
        away_w  = away_obj.get_window_extent(renderer=renderer).transformed(inv).width
        total_w  = home_w + gap + score_w + gap + away_w
        title_x0 = 0.5 - total_w / 2
        score_x0 = title_x0 + home_w + gap
        away_x0  = score_x0 + score_w + gap
        home_obj.set_position((title_x0, y_title))
        score_obj.set_position((score_x0, y_title))
        away_obj.set_position((away_x0, y_title))
        bar_left  = title_x0
        bar_right = title_x0 + total_w
        bar_split = score_x0 + score_w / 2

    def _edge_for(c):
        if bar_contrast_edge and not _has_bg_contrast(c):
            return ('white', 0.8)
        return ('none', 0)

    h_edge, h_lw = _edge_for(home_color)
    a_edge, a_lw = _edge_for(away_color)
    fig.patches.append(Rectangle(
        (bar_left, y_bar), bar_split - bar_left, bar_height,
        transform=fig.transFigure, facecolor=home_color,
        edgecolor=h_edge, linewidth=h_lw, zorder=10,
    ))
    fig.patches.append(Rectangle(
        (bar_split, y_bar), bar_right - bar_split, bar_height,
        transform=fig.transFigure, facecolor=away_color,
        edgecolor=a_edge, linewidth=a_lw, zorder=10,
    ))

    return {
        'bar_left':  bar_left,
        'bar_right': bar_right,
        'bar_split': bar_split,
        'bar_top':   y_bar + bar_height,
    }
