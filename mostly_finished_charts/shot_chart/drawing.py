"""Low-level marker drawing on the pitch.

Plots individual shot markers (circles for non-goals, stars for goals) sized
by xG, with optional highlight muting. Used by the higher-level chart
assembly functions in shot_charts.
"""
from .data import GOAL_TYPES, classify_highlight


# Styling for muted (non-highlighted) shots when a highlight filter is active.
MUTED_COLOR = '#555555'
MUTED_ALPHA = 0.18
MUTED_ZORDER = 5  # Below highlighted shots (zorder=10)

# How far inside the bottom of a half pitch an out-of-range shot is pinned,
# in opta length units, and how far below it the chevron sits. The pad has to
# clear BOTH — at 2.2 the chevron sat 0.4 units off the floor and the axis
# clipped its point, which looked like a rendering fault rather than a flag.
EDGE_PIN_PAD = 4.0
EDGE_PIN_CHEVRON_DROP = 2.4

# DECIDED, DO NOT REINTRODUCE (user, 2026-09-04): no size floor for goal
# stars, and no dark edges for near-white fills. A cold review flagged a
# low-xG goal star reading as "decoration" and white markers blending with
# the pitch lines - both observations were real, but size = xG is the one
# encoding this chart promises, and a floor makes a 0.03-xG screamer look
# like a half-chance. The tiny star IS the story. Same session, same
# reasoning class: the penalty spot stays on the pitch (a viewer once
# counted it as a shot) because it is pitch anatomy, not decoration.


def compute_ylim_floor(shots_df, flip_coords=False, default_floor=60, margin=3):
    """Return the lower y-axis bound for a vertical half-pitch chart.

    Crops the dead zone between the halfway line and the lowest shot, but never
    cuts off a real shot — expands the floor downward if needed.
    """
    if shots_df.empty:
        return default_floor

    if '_needs_flip' in shots_df.columns:
        x_displayed = shots_df.apply(
            lambda r: 100 - r['EventX'] if r['_needs_flip'] else r['EventX'], axis=1
        )
    elif flip_coords:
        x_displayed = 100 - shots_df['EventX']
    else:
        x_displayed = shots_df['EventX']

    min_x = x_displayed.min()
    return max(50, min(default_floor, min_x - margin))


def plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=False,
                        marker_style='single', highlight_mode='All'):
    """Plot shots on a vertical half-pitch (goal at top).

    TruMedia coordinates: EventX = length (0-100), EventY = width (0-100).
    mplsoccer opta coordinates: x = length, y = width.

    marker_style:
        'single' - circles for non-goals, stars for goals, all in team_color
        'multi'  - all circles; black fill for non-goals, team_color fill for goals

    highlight_mode:
        'All' - normal rendering for all shots
        'Open Play' / 'Set Piece' - matching shots in team color, others muted

    Returns the number of shots pinned to the bottom edge because they were
    struck from outside the visible half — callers can caption that count.
    Requires ax.set_ylim() to have run already; the pin reads the axis.
    """
    if shots_df.empty:
        return 0

    # Classify highlight if not already done
    if '_highlighted' not in shots_df.columns:
        shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Draw smaller (lower-xG) markers first so bigger chances sit on top and stay visible
    shots_df = shots_df.sort_values('xG', ascending=True, kind='stable')

    has_per_row_flip = '_needs_flip' in shots_df.columns

    # A half pitch stops at the halfway line, so a shot struck from a team's
    # own half has nowhere to go and was previously drawn outside the axes —
    # silently, with no gap in the data to notice. Rare in one match; over a
    # season a side will have several, and they are the memorable ones.
    #
    # Pin those to the bottom edge instead and flag each with a chevron
    # beneath it. The position is then wrong by a few yards, which is a
    # smaller lie than the shot not existing: this chart's job is "how many,
    # how good, from roughly where", and an edge marker answers all three
    # while announcing that the third is approximate.
    y_lo, y_hi = ax.get_ylim()
    edge_x = min(y_lo, y_hi) + EDGE_PIN_PAD
    displaced = 0

    for _, shot in shots_df.iterrows():
        x = shot['EventX']  # Length (towards goal)
        y = shot['EventY']  # Width (sideline to sideline)
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES
        is_highlighted = shot.get('_highlighted', True)

        # Per-row flip (multi-match) takes priority; otherwise use flip_coords param
        if has_per_row_flip:
            should_flip = shot['_needs_flip']
        else:
            should_flip = flip_coords

        # TruMedia EventY runs opposite to mplsoccer opta Y on vertical pitch
        y = 100 - y

        if should_flip:
            x = 100 - x

        is_pinned = x < edge_x
        if is_pinned:
            x = edge_x
            displaced += 1

        # Scale marker size by xG
        base_size = 50
        size = base_size + (xg * 700)

        if marker_style == 'multi':
            # Multi-match style: all circles, black vs team_color fill
            marker = 'o'
            fill_color = team_color if is_goal else '#000000'
            edge_width = 1.8
        else:
            # Single-match style: circles for non-goals, stars for goals
            marker = '*' if is_goal else 'o'
            fill_color = team_color
            edge_width = 2.3 if is_goal else 1.8

        # Apply muted styling for non-highlighted shots
        if not is_highlighted:
            fill_color = MUTED_COLOR
            alpha = MUTED_ALPHA
            zorder = MUTED_ZORDER
        else:
            alpha = 0.85
            # Goals render above shots regardless of xG-based draw order
            zorder = 11 if is_goal else 10

        pitch.scatter(
            x, y, s=size, c=fill_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=alpha, zorder=zorder, ax=ax
        )

        if is_pinned:
            # Chevron below the marker: "this one came from further back".
            # Sized off the marker so it stays proportionate to a big chance.
            pitch.scatter(
                x - EDGE_PIN_CHEVRON_DROP, y, s=max(26, size * 0.28),
                c=fill_color, marker='v', edgecolors='white', linewidths=1.0,
                alpha=alpha, zorder=zorder, ax=ax
            )

    return displaced


def plot_shots_horizontal(ax, pitch, shots_df, team_color, flip_x=False,
                          flip_y=False, highlight_mode='All'):
    """Plot shots on a horizontal full pitch.

    flip_y: apply y = 100 - y (needed for home team in combined chart to stay
            consistent with the y = 100 - y applied in plot_shots_vertical).

    highlight_mode:
        'All' - normal rendering for all shots
        'Open Play' / 'Set Piece' - matching shots in team color, others muted
    """
    if shots_df.empty:
        return

    # Classify highlight if not already done
    if '_highlighted' not in shots_df.columns:
        shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Draw smaller (lower-xG) markers first so bigger chances sit on top and stay visible
    shots_df = shots_df.sort_values('xG', ascending=True, kind='stable')

    for _, shot in shots_df.iterrows():
        x = shot['EventX']
        y = shot['EventY']
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES
        is_highlighted = shot.get('_highlighted', True)

        if flip_x:
            x = 100 - x  # Mirror to opposite end

        if flip_y:
            y = 100 - y  # Match y-axis orientation used in plot_shots_vertical

        base_size = 50
        size = base_size + (xg * 700)
        marker = '*' if is_goal else 'o'
        edge_width = 2.3 if is_goal else 1.8

        # Apply muted styling for non-highlighted shots
        if not is_highlighted:
            fill_color = MUTED_COLOR
            alpha = MUTED_ALPHA
            zorder = MUTED_ZORDER
        else:
            fill_color = team_color
            alpha = 0.85
            # Goals render above shots regardless of xG-based draw order
            zorder = 11 if is_goal else 10

        pitch.scatter(
            x, y, s=size, c=fill_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=alpha, zorder=zorder, ax=ax
        )
