"""
Zone Passing Chart
Shows where a team passes from each pitch zone - overview with partial-fill
circles and detail view with directional arrows fanning to destinations.

Uses the same 15-zone system as passing_flow_chart.py.
Data source: TruMedia CSV event logs.
"""
import pandas as pd
import numpy as np
import math
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, Circle, Ellipse, Rectangle
from matplotlib.path import Path as MplPath
from mplsoccer import Pitch

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer
from shared.colors import TEAM_COLORS, fuzzy_match_team, get_team_color

from mostly_finished_charts.passing_flow_chart import (
    ZONES, ZONE_NAMES, classify_zone, get_zone_column,
    _zone_center, _short_zone_label, extract_match_info, PITCH_COLOR,
    _ZONE_BY_NAME,
)



# -- Direction Colors ---------------------------------------------------------

FORWARD_COLOR = '#2ECC71'
LATERAL_COLOR = '#F1C40F'
BACKWARD_COLOR = '#E74C3C'


def _direction_of_flow(src_col, dst_col, dest_zone=None):
    """Classify a flow as forward, lateral, or backward."""
    if dest_zone == 'Att Penalty Area':
        return 'forward'
    if dest_zone == 'Def Penalty Area':
        return 'backward'
    if dst_col > src_col:
        return 'forward'
    elif dst_col < src_col:
        return 'backward'
    return 'lateral'


def _direction_color(direction):
    """Return color for a direction string."""
    if direction == 'forward':
        return FORWARD_COLOR
    elif direction == 'backward':
        return BACKWARD_COLOR
    return LATERAL_COLOR


def _forward_pct_color(fwd_pct):
    """Map forward percentage (0-1) to green-yellow-red color.

    1.0 = fully forward = green
    0.5 = balanced = yellow
    0.0 = fully backward = red
    """
    if fwd_pct >= 0.5:
        t = (fwd_pct - 0.5) * 2  # 0..1
        r = int(241 + (46 - 241) * t)
        g = int(196 + (204 - 196) * t)
        b = int(15 + (113 - 15) * t)
    else:
        t = fwd_pct * 2  # 0..1
        r = int(231 + (241 - 231) * t)
        g = int(76 + (196 - 76) * t)
        b = int(60 + (15 - 60) * t)
    return f'#{r:02x}{g:02x}{b:02x}'


# -- Data Pipeline ------------------------------------------------------------

def load_zone_passes(df, team_name, player_name=None):
    """Extract pass events for a team and classify into zones.

    Args:
        df: Raw DataFrame from TruMedia CSV
        team_name: Team name to filter for
        player_name: Optional player name to further filter

    Returns:
        DataFrame with columns: source_x, source_y, dest_x, dest_y,
        source_zone, dest_zone, source_col, dest_col, completed
    """
    required = ['playType', 'Team', 'EventX', 'EventY']
    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    # Filter to pass-type events for the team
    pass_types = {'Pass', 'BlockedPass', 'OffsidePass'}
    mask = (df['playType'].isin(pass_types)) & (df['Team'] == team_name)
    passes = df[mask].copy()

    if player_name:
        if 'passer' in passes.columns:
            passes = passes[passes['passer'] == player_name]
        elif 'shooter' in passes.columns:
            passes = passes[passes['shooter'] == player_name]
        elif 'player' in passes.columns:
            passes = passes[passes['player'] == player_name]

    if passes.empty:
        return pd.DataFrame()

    # Source coordinates - prefer Decimal variants
    if 'EventXDecimal' in passes.columns and passes['EventXDecimal'].notna().any():
        passes['src_x'] = passes['EventXDecimal']
        if 'EventYDecimal' in passes.columns:
            passes['src_y'] = passes['EventYDecimal']
        elif 'EventYDecimal1' in passes.columns:
            passes['src_y'] = passes['EventYDecimal1']
        else:
            passes['src_y'] = passes['EventY']
    else:
        passes['src_x'] = passes['EventX']
        passes['src_y'] = passes['EventY']

    # Destination coordinates
    if 'PassEndXDecimal' in passes.columns and passes['PassEndXDecimal'].notna().any():
        passes['dst_x'] = passes['PassEndXDecimal']
        if 'PassEndYDecimal' in passes.columns:
            passes['dst_y'] = passes['PassEndYDecimal']
        elif 'PassEndYDecimal1' in passes.columns:
            passes['dst_y'] = passes['PassEndYDecimal1']
        else:
            passes['dst_y'] = passes['PassEndY']
    elif 'PassEndX' in passes.columns:
        passes['dst_x'] = passes['PassEndX']
        passes['dst_y'] = passes['PassEndY']
    else:
        return pd.DataFrame()

    # Ensure numeric types (Decimal columns may be strings)
    for col in ['src_x', 'src_y', 'dst_x', 'dst_y']:
        passes[col] = pd.to_numeric(passes[col], errors='coerce')

    # Drop rows with missing coords
    passes = passes.dropna(subset=['src_x', 'src_y', 'dst_x', 'dst_y'])
    if passes.empty:
        return pd.DataFrame()

    # Coordinate flipping if team attacks left-to-right
    mean_x = passes['src_x'].mean()
    flip_x = mean_x < 50

    if flip_x:
        passes['src_x'] = 100 - passes['src_x']
        passes['dst_x'] = 100 - passes['dst_x']

    # Completion status: receiver column not null = completed
    if 'receiver' in passes.columns:
        passes['completed'] = passes['receiver'].notna()
    else:
        # Fallback: assume completed if not BlockedPass or OffsidePass
        passes['completed'] = ~passes['playType'].isin({'BlockedPass', 'OffsidePass'})

    # Classify zones
    passes['source_zone'] = passes.apply(
        lambda r: classify_zone(r['src_x'], r['src_y']), axis=1
    )
    passes['dest_zone'] = passes.apply(
        lambda r: classify_zone(r['dst_x'], r['dst_y']), axis=1
    )

    passes = passes.dropna(subset=['source_zone', 'dest_zone'])

    passes['source_col'] = passes['source_zone'].apply(get_zone_column)
    passes['dest_col'] = passes['dest_zone'].apply(get_zone_column)

    result = passes[['src_x', 'src_y', 'dst_x', 'dst_y',
                      'source_zone', 'dest_zone', 'source_col', 'dest_col',
                      'completed']].copy()
    result.columns = ['source_x', 'source_y', 'dest_x', 'dest_y',
                       'source_zone', 'dest_zone', 'source_col', 'dest_col',
                       'completed']
    return result.reset_index(drop=True)


def aggregate_zone_passes(pass_df):
    """Group passes by (source_zone, dest_zone) with counts and direction.

    Returns DataFrame with: source_zone, dest_zone, total, completed,
    source_col, dest_col, direction
    """
    if pass_df.empty:
        return pd.DataFrame(columns=['source_zone', 'dest_zone', 'total',
                                     'completed', 'source_col', 'dest_col',
                                     'direction'])

    agg = pass_df.groupby(['source_zone', 'dest_zone']).agg(
        total=('completed', 'size'),
        completed=('completed', 'sum'),
    ).reset_index()

    agg['source_col'] = agg['source_zone'].apply(get_zone_column)
    agg['dest_col'] = agg['dest_zone'].apply(get_zone_column)
    agg['direction'] = agg.apply(
        lambda r: _direction_of_flow(r['source_col'], r['dest_col'], r['dest_zone']), axis=1
    )

    return agg


def compute_zone_summary(zone_agg_df, num_matches=1):
    """Compute per-zone summary stats.

    Returns dict mapping zone_name -> {
        total_passes, completed_passes, completion_pct,
        forward_pct, lateral_pct, backward_pct, per_game
    }
    """
    summary = {}
    for zone_name in ZONE_NAMES:
        zone_rows = zone_agg_df[zone_agg_df['source_zone'] == zone_name]
        total = int(zone_rows['total'].sum())
        completed = int(zone_rows['completed'].sum())
        comp_pct = completed / total * 100 if total > 0 else 0

        # Directional breakdown of all passes, excluding within-zone
        cross_zone = zone_rows[zone_rows['dest_zone'] != zone_name]
        fwd = int(cross_zone[cross_zone['direction'] == 'forward']['total'].sum())
        lat = int(cross_zone[cross_zone['direction'] == 'lateral']['total'].sum())
        bwd = int(cross_zone[cross_zone['direction'] == 'backward']['total'].sum())
        dir_total = fwd + lat + bwd

        summary[zone_name] = {
            'total_passes': total,
            'completed_passes': completed,
            'completion_pct': round(comp_pct, 1),
            'forward_pct': fwd / dir_total if dir_total > 0 else 0,
            'lateral_pct': lat / dir_total if dir_total > 0 else 0,
            'backward_pct': bwd / dir_total if dir_total > 0 else 0,
            'per_game': round(total / num_matches, 1),
        }

    return summary


def compute_zone_detail(zone_agg_df, source_zone, num_matches=1):
    """Get destination flow detail for a single source zone.

    Returns list of dicts: {dest_zone, total, completed, per_game, direction}
    """
    rows = zone_agg_df[zone_agg_df['source_zone'] == source_zone]
    detail = []
    for _, r in rows.iterrows():
        detail.append({
            'dest_zone': r['dest_zone'],
            'total': int(r['total']),
            'completed': int(r['completed']),
            'per_game': round(r['total'] / num_matches, 1),
            'direction': r['direction'],
        })
    detail.sort(key=lambda d: d['total'], reverse=True)
    return detail


# -- Chart Helpers ------------------------------------------------------------

def _compute_aspect_correction(fig, ax):
    """Compute y/x aspect correction so circles appear circular on the pitch.

    Returns factor by which to multiply y-radius (height) of ellipses.
    """
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    fig_w, fig_h = fig.get_size_inches()
    bbox = ax.get_position()
    ax_w = bbox.width * fig_w
    ax_h = bbox.height * fig_h
    x_range = xlim[1] - xlim[0]
    y_range = ylim[1] - ylim[0]
    ppd_x = ax_w / x_range
    ppd_y = ax_h / y_range
    return ppd_x / ppd_y if ppd_y > 0 else 1.0


def _draw_pitch_with_zones(fig, ax):
    """Draw an opta pitch on ax with dashed zone grid lines."""
    pitch = Pitch(
        pitch_type='opta',
        pitch_color='none',
        line_color='white',
        linewidth=2,
        goal_type='box',
        pad_top=1,
        pad_bottom=1,
        pad_left=3,
        pad_right=3,
    )

    # Green pitch background
    pitch_rect = Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)
    pitch.draw(ax=ax)

    # Zone boundary lines (dashed, low alpha)
    zone_x_bounds = [17, 33.3, 50, 66.7, 83]
    zone_y_bounds_main = [33.3, 66.7]
    zone_y_bounds_ends = [21, 79]

    for xb in zone_x_bounds:
        ax.plot([xb, xb], [0, 100], '--', color='white', alpha=0.2, lw=0.8, zorder=2)

    for yb in zone_y_bounds_main:
        ax.plot([17, 83], [yb, yb], '--', color='white', alpha=0.2, lw=0.8, zorder=2)

    for yb in zone_y_bounds_ends:
        ax.plot([0, 17], [yb, yb], '--', color='white', alpha=0.15, lw=0.8, zorder=2)
        ax.plot([83, 100], [yb, yb], '--', color='white', alpha=0.15, lw=0.8, zorder=2)

    return pitch


def _draw_partial_fill_circle(ax, cx, cy, radius, fill_pct, fill_color,
                               aspect_corr=1.0, bg_color='#1A2332',
                               border_color='white', border_width=2.0):
    """Draw a circle with partial fill (liquid gauge effect).

    Uses Ellipse to compensate for pitch aspect ratio so it appears circular.
    fill_pct: 0.0 to 1.0 (fraction filled from bottom)
    aspect_corr: y/x correction factor from _compute_aspect_correction
    """
    w = 2 * radius
    h = 2 * radius * aspect_corr

    # Background ellipse (appears circular on screen)
    bg = Ellipse((cx, cy), w, h, facecolor=bg_color, edgecolor=border_color,
                  linewidth=border_width, alpha=0.85, zorder=10)
    ax.add_patch(bg)

    if fill_pct <= 0:
        return

    fill_pct = min(fill_pct, 1.0)

    # Filled portion clipped to ellipse shape
    ry = radius * aspect_corr
    fill_bottom = cy - ry
    fill_height = 2 * ry * fill_pct

    fill_rect = Rectangle(
        (cx - radius, fill_bottom), w, fill_height,
        facecolor=fill_color, alpha=0.75, zorder=11
    )

    # Clip to ellipse path
    clip_ellipse = MplPath.unit_circle()
    from matplotlib.transforms import Affine2D
    clip_transform = Affine2D().scale(radius, ry).translate(cx, cy) + ax.transData
    fill_rect.set_clip_path(clip_ellipse, clip_transform)
    ax.add_patch(fill_rect)

    # Border on top
    border = Ellipse((cx, cy), w, h, facecolor='none', edgecolor=border_color,
                      linewidth=border_width, zorder=12)
    ax.add_patch(border)


def _draw_zone_arrow(ax, src_center, dst_center, circle_radius, linewidth,
                      color, label_text, rad_offset=0.0, aspect_corr=1.0):
    """Draw a curved arrow from source ellipse edge to destination center.

    Uses the 'simple' filled-polygon arrowstyle so thick arrows render with
    smooth edges instead of rough stroked lines.

    Args:
        src_center: (x, y) center of source ellipse
        dst_center: (x, y) center of destination
        circle_radius: x-radius of source ellipse (start arrow at edge)
        linewidth: arrow thickness (used to compute tail/head widths)
        color: arrow color
        label_text: text for badge near arrow tip
        rad_offset: curvature offset for arc3 connectionstyle
        aspect_corr: y/x aspect correction factor
    """
    sx, sy = src_center
    dx, dy = dst_center

    # Compute angle from source to destination
    angle = math.atan2(dy - sy, dx - sx)

    # Start point on ellipse perimeter: x = rx*cos, y = ry*sin
    rx = circle_radius
    ry = circle_radius * aspect_corr
    start_x = sx + rx * math.cos(angle)
    start_y = sy + ry * math.sin(angle)

    # Curvature
    dist = math.hypot(dx - sx, dy - sy)
    base_rad = 0.15 if dist > 30 else 0.1
    rad = base_rad + rad_offset

    # Scale tail/head from linewidth (filled polygon, not stroked line)
    tail_w = max(1.5, linewidth * 0.7)
    head_w = tail_w * 1.6
    head_l = head_w * 0.8

    arrow = FancyArrowPatch(
        (start_x, start_y), (dx, dy),
        connectionstyle=f'arc3,rad={rad}',
        arrowstyle=f'simple,head_length={head_l:.1f},head_width={head_w:.1f},tail_width={tail_w:.1f}',
        facecolor=color,
        edgecolor='none',
        zorder=15,
    )
    ax.add_patch(arrow)

    # Label badge - position 15% back from destination along the straight line
    lx = dx - 0.15 * (dx - start_x)
    ly = dy - 0.15 * (dy - start_y)

    ax.text(lx, ly, label_text, ha='center', va='center',
            fontsize=7, fontweight='bold', color='white',
            bbox=dict(boxstyle='round,pad=0.25', facecolor=color,
                     edgecolor='white', linewidth=0.8, alpha=0.95),
            zorder=20)


def _draw_same_zone_badge(ax, center, circle_radius, label_text,
                           aspect_corr=1.0):
    """Draw a notification-style badge on the circle edge for same-zone passes.

    Places a small rounded pill at the bottom-right of the circle showing
    the within-zone pass count, like a notification badge on an app icon.
    """
    import math

    cx, cy = center
    ry = circle_radius * aspect_corr

    # Position badge at bottom-right of circle (roughly 315 degrees)
    angle = math.radians(-45)
    badge_x = cx + circle_radius * math.cos(angle)
    badge_y = cy + ry * math.sin(angle)

    display = f"{label_text}\nwithin"
    ax.text(badge_x, badge_y, display,
            ha='center', va='center', fontsize=6, fontweight='bold',
            color='white', linespacing=0.85,
            bbox=dict(boxstyle='round,pad=0.3',
                      facecolor='#555555', edgecolor='white',
                      linewidth=1.2, alpha=0.95),
            zorder=22)


# -- Overview Chart -----------------------------------------------------------

def create_zone_overview_chart(pass_df, zone_agg_df, team_name, team_color,
                                match_info, num_matches=1, player_name=None,
                                competition=''):
    """Build overview chart with partial-fill circles at each zone center.

    Circle size = total passes, fill level = completion %, fill color = directional
    tendency (green=forward, yellow=lateral, red=backward).
    """
    zone_summary = compute_zone_summary(zone_agg_df, num_matches)

    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    fig.subplots_adjust(left=0.05, right=0.95, top=0.90, bottom=0.10)
    _draw_pitch_with_zones(fig, ax)
    ac = _compute_aspect_correction(fig, ax)

    # Compute circle radii scaled to pass volume
    totals = [zone_summary[z]['total_passes'] for z in ZONE_NAMES]
    max_total = max(totals) if totals else 1
    min_total = min(t for t in totals if t > 0) if any(t > 0 for t in totals) else 0

    min_radius = 4.0
    max_radius = 10.0

    for zone_name in ZONE_NAMES:
        stats = zone_summary[zone_name]
        cx, cy = _zone_center(zone_name)
        total = stats['total_passes']
        completed = stats['completed_passes']
        comp_pct = stats['completion_pct'] / 100.0

        if total == 0:
            continue

        # Scale radius, capped to fit within zone
        if max_total > min_total:
            t = (total - min_total) / (max_total - min_total)
        else:
            t = 0.5
        radius = min_radius + t * (max_radius - min_radius)

        zone_info = _ZONE_BY_NAME[zone_name]
        zone_w = zone_info['x_range'][1] - zone_info['x_range'][0]
        zone_h = zone_info['y_range'][1] - zone_info['y_range'][0]
        max_rx = zone_w / 2 * 0.8
        max_ry_as_rx = zone_h / (2 * ac) * 0.8
        radius = min(radius, max_rx, max_ry_as_rx)

        # Fill color based on forward tendency
        fill_color = _forward_pct_color(stats['forward_pct'])

        _draw_partial_fill_circle(ax, cx, cy, radius, comp_pct, fill_color,
                                   aspect_corr=ac)

        # Text inside circle
        ax.text(cx, cy + 1.2, f"{completed}/{total}",
                ha='center', va='center', fontsize=8, fontweight='bold',
                color='white', zorder=13)
        ax.text(cx, cy - 2.0, f"({stats['completion_pct']}%)",
                ha='center', va='center', fontsize=7,
                color='white', zorder=13)

    # Direction arrow below pitch
    ax.annotate('', xy=(85, -3), xytext=(15, -3),
                arrowprops=dict(arrowstyle='->', color='white', lw=1.5),
                annotation_clip=False)
    ax.text(-4, 50, 'OWN\nGOAL', ha='center', va='center', fontsize=7,
            color=TEXT_SECONDARY, fontweight='bold', clip_on=False)
    ax.text(104, 50, 'OPP\nGOAL', ha='center', va='center', fontsize=7,
            color=TEXT_SECONDARY, fontweight='bold', clip_on=False)

    # Title
    if player_name:
        display_name = f"{player_name.upper()} ({team_name})"
    else:
        display_name = team_name.upper()
    title_text = f"{display_name} - ZONE PASSING OVERVIEW"
    fig.suptitle(title_text, fontsize=18, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Subtitle
    subtitle_parts = []
    opponent = match_info.get('opponent', '')
    score = match_info.get('score', '')
    date = match_info.get('date', '')
    if opponent:
        subtitle_parts.append(f"vs {opponent}")
    if score:
        subtitle_parts.append(score)
    if date:
        subtitle_parts.append(date)
    if competition:
        subtitle_parts.append(competition.upper())
    if num_matches > 1:
        subtitle_parts.append(f"{num_matches} matches")
    if subtitle_parts:
        fig.text(0.5, 0.92, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=10, color=TEXT_SECONDARY)

    # Color legend
    legend_y = 0.04
    legend_items = [
        (BACKWARD_COLOR, 'Backward'),
        (LATERAL_COLOR, 'Lateral'),
        (FORWARD_COLOR, 'Forward'),
    ]
    for i, (color, label) in enumerate(legend_items):
        x_pos = 0.35 + i * 0.15
        fig.text(x_pos, legend_y, label, ha='center', va='center',
                 fontsize=9, fontweight='bold', color=color)

    fig.text(0.5, legend_y + 0.025,
             'Circle size = volume | Fill level = completion % | Color = direction tendency',
             ha='center', va='center', fontsize=8, color=TEXT_SECONDARY)

    add_cbs_footer(fig)

    return fig


# -- Detail Chart -------------------------------------------------------------

def create_zone_detail_chart(pass_df, zone_agg_df, source_zone, team_name,
                              team_color, match_info, num_matches=1,
                              player_name=None, competition='',
                              min_per_game=0.5):
    """Build detail chart for a single source zone with arrows to destinations.

    Matches the mockup: large central circle with team color, curved arrows
    fanning out to all destination zones, colored by direction.
    """
    detail = compute_zone_detail(zone_agg_df, source_zone, num_matches)
    zone_summary = compute_zone_summary(zone_agg_df, num_matches)
    src_stats = zone_summary.get(source_zone, {})

    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    fig.subplots_adjust(left=0.05, right=0.95, top=0.90, bottom=0.10)
    _draw_pitch_with_zones(fig, ax)
    ac = _compute_aspect_correction(fig, ax)

    src_cx, src_cy = _zone_center(source_zone)

    # Scale circle radius to fit within the zone boundaries
    zone_info = _ZONE_BY_NAME[source_zone]
    zone_w = zone_info['x_range'][1] - zone_info['x_range'][0]
    zone_h = zone_info['y_range'][1] - zone_info['y_range'][0]
    max_rx = zone_w / 2 * 0.7   # 70% of half-width
    max_ry_as_rx = zone_h / (2 * ac) * 0.7  # 70% of half-height (in x-radius terms)
    circle_radius = min(max_rx, max_ry_as_rx, 9.0)

    # Source zone ellipse - filled with team color (appears circular)
    w = 2 * circle_radius
    h = 2 * circle_radius * ac
    source_ellipse = Ellipse(
        (src_cx, src_cy), w, h,
        facecolor=team_color, edgecolor='white', linewidth=3,
        alpha=0.85, zorder=10
    )
    ax.add_patch(source_ellipse)

    # Outer ring
    outer_ring = Ellipse(
        (src_cx, src_cy), w + 2.4, h + 2.4 * ac,
        facecolor='none', edgecolor='#AAAAAA', linewidth=1.5,
        alpha=0.5, zorder=9
    )
    ax.add_patch(outer_ring)

    # Total pass count and completion inside circle
    total = src_stats.get('total_passes', 0)
    completed = src_stats.get('completed_passes', 0)
    comp_pct = src_stats.get('completion_pct', 0)

    # Show per-game values when aggregating multiple matches
    if num_matches > 1:
        display_total = round(total / num_matches, 1)
        display_completed = round(completed / num_matches, 1)
        circle_text = f"{display_completed}/{display_total}"
    else:
        circle_text = f"{completed}/{total}"

    # Scale font size to circle radius
    main_fs = max(10, min(16, int(circle_radius * 1.8)))
    sub_fs = max(8, min(12, int(circle_radius * 1.3)))
    text_offset = circle_radius * 0.22

    ax.text(src_cx, src_cy + text_offset, circle_text,
            ha='center', va='center', fontsize=main_fs, fontweight='bold',
            color='white', zorder=13)
    ax.text(src_cx, src_cy - text_offset * 2, f"({comp_pct}%)",
            ha='center', va='center', fontsize=sub_fs,
            color='white', zorder=13)

    # Draw arrows to each destination zone (skip same-zone passes)
    outgoing = [d for d in detail if d['dest_zone'] != source_zone]
    same_zone = next((d for d in detail if d['dest_zone'] == source_zone), None)

    if outgoing:
        all_totals = [d['total'] for d in outgoing]
        if same_zone and same_zone['total'] > 0:
            all_totals.append(same_zone['total'])
        max_flow = max(all_totals)

        # Assign slight curvature offsets to reduce overlap
        rad_offsets = {}
        for i, d in enumerate(outgoing):
            rad_offsets[d['dest_zone']] = (i % 3 - 1) * 0.05

        for d in outgoing:
            if d['per_game'] < min_per_game:
                continue

            dest_zone = d['dest_zone']
            color = _direction_color(d['direction'])

            # Line width: linear scaling with wider min/max range
            ratio = d['total'] / max_flow
            lw = 1.0 + (ratio ** 0.75) * 20.0

            label = str(d['per_game'])

            dst_cx, dst_cy = _zone_center(dest_zone)
            _draw_zone_arrow(
                ax, (src_cx, src_cy), (dst_cx, dst_cy),
                circle_radius + 1.5, lw, color, label,
                rad_offset=rad_offsets.get(dest_zone, 0),
                aspect_corr=ac,
            )

    # Same-zone passes: notification badge on circle edge
    if same_zone and same_zone['total'] > 0:
        _draw_same_zone_badge(
            ax, (src_cx, src_cy), circle_radius,
            str(same_zone['per_game']),
            aspect_corr=ac,
        )

    # Goal labels
    ax.text(-4, 50, 'OWN\nGOAL', ha='center', va='center', fontsize=7,
            color=TEXT_SECONDARY, fontweight='bold', clip_on=False)
    ax.text(104, 50, 'OPP\nGOAL', ha='center', va='center', fontsize=7,
            color=TEXT_SECONDARY, fontweight='bold', clip_on=False)

    # Title
    if player_name:
        display_name = f"{player_name.upper()} ({team_name})"
    else:
        display_name = team_name.upper()
    title_text = f"{display_name} - DIRECTIONAL PASSING ANALYSIS"
    fig.suptitle(title_text, fontsize=18, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Subtitle
    subtitle_parts = []
    if num_matches > 1:
        subtitle_parts.append(f"Passes per game | Arrow thickness = volume | {num_matches} matches")
    else:
        subtitle_parts.append("Pass count | Arrow thickness = volume")
    opponent = match_info.get('opponent', '')
    if opponent:
        subtitle_parts.append(f"vs {opponent}")
    date = match_info.get('date', '')
    if date:
        subtitle_parts.append(date)
    if competition:
        subtitle_parts.append(competition.upper())
    fig.text(0.5, 0.92, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=10, color=TEXT_SECONDARY)

    # Direction legend at bottom (left-to-right matches pitch: backward, lateral, forward)
    legend_y = 0.055
    for i, (color, label) in enumerate([
        (BACKWARD_COLOR, 'Backward'),
        (LATERAL_COLOR, 'Lateral'),
        (FORWARD_COLOR, 'Forward'),
    ]):
        x_pos = 0.25 + i * 0.25
        fig.text(x_pos, legend_y, label, ha='center', va='center',
                 fontsize=14, fontweight='bold', color=color)

    add_cbs_footer(fig)

    return fig


# -- CLI Entry Points ---------------------------------------------------------

def run(config):
    """Entry point for CLI launcher.

    config keys:
        file_path: Path to TruMedia event log CSV
        output_folder: Output directory
        team_name: Team to analyze (optional, will prompt if missing)
        player_name: Optional player name filter
        zone: Specific zone name, 'all', or None (overview only)
        competition: str (optional)
        num_matches: int (default 1)
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder', os.path.expanduser('~/Downloads'))
    competition = config.get('competition', '')
    num_matches = config.get('num_matches', 1)
    player_name = config.get('player_name')

    df = pd.read_csv(file_path)

    # Detect teams
    teams = df['Team'].dropna().unique().tolist()
    if not teams:
        print("[ERROR] No teams found in CSV.")
        return

    team_name = config.get('team_name')
    if not team_name:
        print("\nTeams found:")
        for i, t in enumerate(teams, 1):
            print(f"  {i}. {t}")
        choice = input(f"Select team (1-{len(teams)}): ").strip()
        try:
            team_name = teams[int(choice) - 1]
        except (ValueError, IndexError):
            team_name = teams[0]

    print(f"\nAnalyzing zone passing for: {team_name}")

    team_color = get_team_color(team_name)
    match_info = extract_match_info(df, team_name)

    pass_df = load_zone_passes(df, team_name, player_name=player_name)
    if pass_df.empty:
        print("[ERROR] No pass data found for this team.")
        return

    zone_agg_df = aggregate_zone_passes(pass_df)

    print(f"  Total passes: {len(pass_df)}")
    completed = pass_df['completed'].sum()
    print(f"  Completed: {completed} ({completed/len(pass_df)*100:.1f}%)")

    os.makedirs(output_folder, exist_ok=True)
    safe_name = team_name.replace(' ', '_')

    # Overview chart
    fig = create_zone_overview_chart(
        pass_df, zone_agg_df, team_name, team_color, match_info,
        num_matches=num_matches, player_name=player_name,
        competition=competition,
    )
    filename = f"zone_passing_overview_{safe_name}.png"
    filepath = os.path.join(output_folder, filename)
    fig.savefig(filepath, dpi=300, bbox_inches='tight',
                facecolor=BG_COLOR, edgecolor='none')
    plt.close(fig)
    print(f"\n[OK] Saved overview: {filepath}")

    # Detail charts
    zone_arg = config.get('zone')
    if zone_arg:
        if zone_arg.lower() == 'all':
            zones_to_chart = ZONE_NAMES
        else:
            zones_to_chart = [zone_arg]

        for zone_name in zones_to_chart:
            if zone_name not in ZONE_NAMES:
                print(f"[WARN] Unknown zone: {zone_name}")
                continue

            fig = create_zone_detail_chart(
                pass_df, zone_agg_df, zone_name, team_name, team_color,
                match_info, num_matches=num_matches,
                player_name=player_name, competition=competition,
            )
            zone_safe = zone_name.replace(' ', '_')
            filename = f"zone_passing_{zone_safe}_{safe_name}.png"
            filepath = os.path.join(output_folder, filename)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                        facecolor=BG_COLOR, edgecolor='none')
            plt.close(fig)
            print(f"[OK] Saved detail: {filepath}")


def main():
    """Interactive CLI prompts."""
    from shared.file_utils import get_file_path, get_output_folder

    print("\n" + "-" * 60)
    print("ZONE PASSING CHART")
    print("-" * 60)

    file_path = get_file_path("TruMedia Event Log CSV file")
    if not file_path:
        return

    competition = input("Competition (e.g., PREMIER LEAGUE) [optional]: ").strip().upper()

    print("\nGenerate detail charts for which zones?")
    print("  1. Overview only (default)")
    print("  2. All zones")
    print("  3. Specific zone")
    zone_choice = input("Select (1-3, default=1): ").strip()

    zone_arg = None
    if zone_choice == '2':
        zone_arg = 'all'
    elif zone_choice == '3':
        print("\nAvailable zones:")
        for i, z in enumerate(ZONE_NAMES, 1):
            print(f"  {i}. {z}")
        z_choice = input(f"Select zone (1-{len(ZONE_NAMES)}): ").strip()
        try:
            zone_arg = ZONE_NAMES[int(z_choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice, generating overview only.")

    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'competition': competition,
        'zone': zone_arg,
    }

    run(config)


if __name__ == '__main__':
    main()
