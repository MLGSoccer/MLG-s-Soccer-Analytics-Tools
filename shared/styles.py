"""
Shared styling constants and utilities for soccer chart builders.
CBS Sports theme styling.
"""

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
