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

# Text colors
TEXT_PRIMARY = '#FFFFFF'
TEXT_SECONDARY = '#B8C5D6'
TEXT_MUTED = '#8BA3B8'
TEXT_SUBTLE = '#999999'

# Accent colors
POSITIVE_COLOR = '#2ECC71'  # Green for positive values


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
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    if data_source:
        fig.text(0.98, 0.01, f'DATA: {data_source.upper()}', fontsize=8,
                color=TEXT_SUBTLE, ha='right')
