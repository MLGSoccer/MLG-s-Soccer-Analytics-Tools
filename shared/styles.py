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


def add_cbs_footer(fig, data_source='Opta/Stats Perform'):
    """Add CBS Sports branding footer to figure."""
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=11, fontweight='bold', color=CBS_BLUE_LIGHT)
    if data_source:
        fig.text(0.98, 0.01, f'DATA: {data_source.upper()}', fontsize=9,
                color=TEXT_MUTED, ha='right')


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
