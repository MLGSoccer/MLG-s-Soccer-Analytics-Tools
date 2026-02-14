"""
Passing Flow Chart
Visualizes how a team progresses the ball up the field through passes.
Pitch divided into 15 zones across 5 columns, with flow arrows on a full pitch.

Uses mplsoccer for pitch drawing (same approach as shot_chart.py).
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, Rectangle
from mplsoccer import Pitch

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer
from shared.colors import TEAM_COLORS, fuzzy_match_team, get_team_color


# ── Constants ─────────────────────────────────────────────────────────────────

PITCH_COLOR = '#1E5631'


# ── Zone Definitions ─────────────────────────────────────────────────────────
# 15 zones across 5 columns. Each column has 3 lanes (Left, Center/PA, Right).
# Columns 0 and 4 use penalty area instead of center.

ZONES = [
    # Column 0 - Defensive third (narrow wings, wide PA)
    # In opta coords: low Y = right side, high Y = left side
    {'name': 'Def Wing Right',    'col': 0, 'x_range': (0, 17),    'y_range': (0, 21)},
    {'name': 'Def Penalty Area',  'col': 0, 'x_range': (0, 17),    'y_range': (21, 79)},
    {'name': 'Def Wing Left',     'col': 0, 'x_range': (0, 17),    'y_range': (79, 100)},
    # Column 1 - Defensive mid
    {'name': 'Def Right',         'col': 1, 'x_range': (17, 33.3),  'y_range': (0, 33.3)},
    {'name': 'Def Center',        'col': 1, 'x_range': (17, 33.3),  'y_range': (33.3, 66.7)},
    {'name': 'Def Left',          'col': 1, 'x_range': (17, 33.3),  'y_range': (66.7, 100)},
    # Column 2 - Defensive midfield (own half)
    {'name': 'Mid Def Right',     'col': 2, 'x_range': (33.3, 50),  'y_range': (0, 33.3)},
    {'name': 'Mid Def Center',    'col': 2, 'x_range': (33.3, 50),  'y_range': (33.3, 66.7)},
    {'name': 'Mid Def Left',      'col': 2, 'x_range': (33.3, 50),  'y_range': (66.7, 100)},
    # Column 3 - Attacking midfield (opponent half)
    {'name': 'Mid Att Right',     'col': 3, 'x_range': (50, 66.7),  'y_range': (0, 33.3)},
    {'name': 'Mid Att Center',    'col': 3, 'x_range': (50, 66.7),  'y_range': (33.3, 66.7)},
    {'name': 'Mid Att Left',      'col': 3, 'x_range': (50, 66.7),  'y_range': (66.7, 100)},
    # Column 4 - Attacking mid
    {'name': 'Att Right',         'col': 4, 'x_range': (66.7, 83),  'y_range': (0, 33.3)},
    {'name': 'Att Center',        'col': 4, 'x_range': (66.7, 83),  'y_range': (33.3, 66.7)},
    {'name': 'Att Left',          'col': 4, 'x_range': (66.7, 83),  'y_range': (66.7, 100)},
    # Column 5 - Final third (narrow wings, wide PA)
    {'name': 'Att Wing Right',    'col': 5, 'x_range': (83, 100),   'y_range': (0, 21)},
    {'name': 'Att Penalty Area',  'col': 5, 'x_range': (83, 100),   'y_range': (21, 79)},
    {'name': 'Att Wing Left',     'col': 5, 'x_range': (83, 100),   'y_range': (79, 100)},
]

ZONE_NAMES = [z['name'] for z in ZONES]

# Precompute lookup structures
_ZONE_BY_NAME = {z['name']: z for z in ZONES}


def _zone_center(zone_name):
    """Get the center (x, y) of a zone in opta coordinates (0-100)."""
    z = _ZONE_BY_NAME[zone_name]
    cx = (z['x_range'][0] + z['x_range'][1]) / 2
    cy = (z['y_range'][0] + z['y_range'][1]) / 2
    return cx, cy


def classify_zone(x, y):
    """Map (x, y) coordinates to a zone name.

    Classification logic: determine column by X range first, then lane by Y range.
    Coordinates are 0-100 scale (TruMedia convention).
    """
    for z in ZONES:
        x_lo, x_hi = z['x_range']
        y_lo, y_hi = z['y_range']
        if x_lo <= x < x_hi and y_lo <= y < y_hi:
            return z['name']
    # Edge cases: clamp to boundaries
    x = max(0, min(x, 99.99))
    y = max(0, min(y, 99.99))
    for z in ZONES:
        x_lo, x_hi = z['x_range']
        y_lo, y_hi = z['y_range']
        if x_lo <= x <= x_hi and y_lo <= y <= y_hi:
            return z['name']
    return None


def get_zone_column(zone_name):
    """Return column index (0-4) for a zone name."""
    z = _ZONE_BY_NAME.get(zone_name)
    return z['col'] if z else None


def is_penalty_area(zone_name):
    """Check if a zone is a penalty area."""
    return 'Penalty Area' in zone_name if zone_name else False


# ── Data Pipeline ─────────────────────────────────────────────────────────────

def load_passing_data(df, team_name):
    """Extract all ball progression events (passes + carries) for a team.

    Generates two types of movement:
    - Passes: EventX/Y -> PassEndX/Y (explicit coords), or inferred from next event
    - Carries: PassEndX/Y of pass N -> EventX/Y of the next action in the sequence

    Args:
        df: Raw DataFrame from TruMedia CSV
        team_name: Team name to filter for

    Returns:
        DataFrame with columns: source_x, source_y, dest_x, dest_y,
        move_type ('pass' or 'carry'), source_zone, dest_zone, source_col, dest_col
    """
    required = ['playType', 'Team', 'EventX', 'EventY', 'sequenceId', 'gameEventIndex']
    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    has_pass_end = 'PassEndX' in df.columns and 'PassEndY' in df.columns

    # Coordinate normalization: if team's mean pass EventX < 50, flip X
    team_passes = df[(df['playType'] == 'Pass') & (df['Team'] == team_name)]
    if team_passes.empty:
        return pd.DataFrame()

    mean_x = team_passes['EventX'].mean()
    flip_x = mean_x < 50

    # Get all events in team's sequences, sorted
    team_seqs = df[df['Team'] == team_name]['sequenceId'].dropna().unique()
    events = df[df['sequenceId'].isin(team_seqs)].copy()
    events = events.sort_values(['sequenceId', 'gameEventIndex'])

    if flip_x:
        events['EventX'] = 100 - events['EventX']
        if has_pass_end:
            events['PassEndX'] = 100 - events['PassEndX']

    movements = []

    for seq_id, group in events.groupby('sequenceId'):
        group = group.sort_values('gameEventIndex').reset_index(drop=True)

        prev_end_x = None
        prev_end_y = None

        for i in range(len(group)):
            row = group.iloc[i]

            # Skip events not by this team
            if row['Team'] != team_name:
                prev_end_x = None
                prev_end_y = None
                continue

            ex, ey = row['EventX'], row['EventY']
            if pd.isna(ex) or pd.isna(ey):
                prev_end_x = None
                prev_end_y = None
                continue

            # Carry: ball moved from previous pass end to this event's location
            if prev_end_x is not None:
                dist = ((ex - prev_end_x)**2 + (ey - prev_end_y)**2) ** 0.5
                if dist > 2:  # minimum distance to count as a carry
                    movements.append({
                        'source_x': prev_end_x,
                        'source_y': prev_end_y,
                        'dest_x': ex,
                        'dest_y': ey,
                        'move_type': 'carry',
                        'sequenceId': seq_id,
                    })

            # Pass: use explicit end coords if available, else infer from next event
            if row['playType'] == 'Pass':
                if has_pass_end and not pd.isna(row.get('PassEndX')) and not pd.isna(row.get('PassEndY')):
                    dest_x, dest_y = row['PassEndX'], row['PassEndY']
                elif i + 1 < len(group):
                    nxt = group.iloc[i + 1]
                    dest_x, dest_y = nxt['EventX'], nxt['EventY']
                    if pd.isna(dest_x) or pd.isna(dest_y):
                        prev_end_x = None
                        prev_end_y = None
                        continue
                else:
                    prev_end_x = None
                    prev_end_y = None
                    continue

                movements.append({
                    'source_x': ex,
                    'source_y': ey,
                    'dest_x': dest_x,
                    'dest_y': dest_y,
                    'move_type': 'pass',
                    'sequenceId': seq_id,
                })
                prev_end_x = dest_x
                prev_end_y = dest_y
            else:
                # Non-pass event — no explicit end point
                prev_end_x = None
                prev_end_y = None

    if not movements:
        return pd.DataFrame()

    result = pd.DataFrame(movements)

    # Classify zones
    result['source_zone'] = result.apply(
        lambda r: classify_zone(r['source_x'], r['source_y']), axis=1
    )
    result['dest_zone'] = result.apply(
        lambda r: classify_zone(r['dest_x'], r['dest_y']), axis=1
    )

    # Drop rows where classification failed
    result = result.dropna(subset=['source_zone', 'dest_zone'])

    # Add column indices
    result['source_col'] = result['source_zone'].apply(get_zone_column)
    result['dest_col'] = result['dest_zone'].apply(get_zone_column)

    return result


def build_zone_flows(pass_df, forward_only=False):
    """Aggregate zone-to-zone pass counts, merging both directions per zone pair.

    Each zone pair gets one row. The net direction determines arrow color/direction.

    Args:
        pass_df: DataFrame from load_passing_data
        forward_only: If True, only include forward passes before merging

    Returns:
        DataFrame with columns:
            zone_a, zone_b: zone names (ordered so zone_a col <= zone_b col)
            total: total passes between the pair
            net_direction: weighted column diff (-4 to +4), positive = net forward
            arrow_from, arrow_to: zone names for arrow direction (dominant flow)
    """
    if pass_df.empty:
        return pd.DataFrame(columns=['zone_a', 'zone_b', 'total',
                                     'net_direction', 'arrow_from', 'arrow_to'])

    df = pass_df.copy()

    if forward_only:
        mask = (df['dest_col'] > df['source_col']) | df['dest_zone'].apply(is_penalty_area)
        df = df[mask]

    if df.empty:
        return pd.DataFrame(columns=['zone_a', 'zone_b', 'total',
                                     'net_direction', 'arrow_from', 'arrow_to'])

    # Count directed flows
    directed = df.groupby(['source_zone', 'dest_zone']).size().reset_index(name='count')

    # Merge into unordered pairs
    pairs = {}
    for _, row in directed.iterrows():
        src, dst, cnt = row['source_zone'], row['dest_zone'], row['count']
        # Create a canonical key (alphabetically sorted)
        key = tuple(sorted([src, dst]))
        if key not in pairs:
            pairs[key] = {'fwd': 0, 'bwd': 0, 'fwd_zone': None, 'bwd_zone': None}

        src_col = get_zone_column(src)
        dst_col = get_zone_column(dst)

        if dst_col > src_col or (dst_col == src_col and dst >= src):
            pairs[key]['fwd'] += cnt
            pairs[key]['fwd_zone'] = (src, dst)
        else:
            pairs[key]['bwd'] += cnt
            pairs[key]['bwd_zone'] = (src, dst)

    rows = []
    for (za, zb), data in pairs.items():
        total = data['fwd'] + data['bwd']

        # Determine dominant direction for arrow
        if data['fwd'] >= data['bwd']:
            arrow_from, arrow_to = data['fwd_zone'] or (za, zb)
        else:
            arrow_from, arrow_to = data['bwd_zone'] or (zb, za)

        # Net direction as weighted column difference
        from_col = get_zone_column(arrow_from)
        to_col = get_zone_column(arrow_to)
        col_diff = to_col - from_col

        # Scale by how one-sided the flow is (1.0 = all one way, 0.0 = perfectly balanced)
        dominance = abs(data['fwd'] - data['bwd']) / total if total > 0 else 0
        net_direction = col_diff * dominance

        rows.append({
            'zone_a': za,
            'zone_b': zb,
            'total': total,
            'net_direction': net_direction,
            'arrow_from': arrow_from,
            'arrow_to': arrow_to,
        })

    flows = pd.DataFrame(rows)
    flows = flows.sort_values('total', ascending=False)

    # Filter by minimum percentage threshold
    grand_total = flows['total'].sum()
    if grand_total > 0:
        flows['pct'] = flows['total'] / grand_total * 100
    else:
        flows['pct'] = 0.0

    return flows


def compute_flow_stats(pass_df, flows):
    """Compute summary statistics for the ball progression flow.

    Returns dict with:
        total: total movements
        position: {'left': n, 'center': n, 'right': n} based on dest_y
        direction: {'forward': n, 'sideways': n, 'backward': n} based on dx
        passes_into_pa: count of movements ending in penalty area
    """
    total = len(pass_df) if not pass_df.empty else 0

    # Position: based on where the ball ends up (dest_y thirds)
    pos = {'left': 0, 'center': 0, 'right': 0}
    direction = {'forward': 0, 'sideways': 0, 'backward': 0}

    if not pass_df.empty:
        # Position by dest_y
        pos['left'] = int((pass_df['dest_y'] < 33.3).sum())
        pos['center'] = int(((pass_df['dest_y'] >= 33.3) & (pass_df['dest_y'] < 66.7)).sum())
        pos['right'] = int((pass_df['dest_y'] >= 66.7).sum())

        # Direction by X movement (threshold of 5 units)
        dx = pass_df['dest_x'] - pass_df['source_x']
        direction['forward'] = int((dx > 5).sum())
        direction['backward'] = int((dx < -5).sum())
        direction['sideways'] = int(((dx >= -5) & (dx <= 5)).sum())

    if not pass_df.empty:
        pa_passes = pass_df[pass_df['dest_zone'].apply(is_penalty_area)]
        passes_into_pa = len(pa_passes)
    else:
        passes_into_pa = 0

    # Forward pct for legacy compatibility
    forward_pct = round(direction['forward'] / total * 100, 1) if total > 0 else 0

    return {
        'total_passes': total,
        'forward_pct': forward_pct,
        'passes_into_pa': passes_into_pa,
        'position': pos,
        'direction': direction,
    }


# ── Pitch Flow Chart ─────────────────────────────────────────────────────────

def _short_zone_label(name):
    """Abbreviate zone name for on-pitch display."""
    label = name.replace('Def ', 'D ').replace('Att ', 'A ').replace('Mid ', 'M ')
    label = label.replace('Wing ', 'W ').replace('Penalty Area', 'PA')
    return label


def _direction_color(col_diff):
    """Map column difference to a color on a blue-white-red gradient.

    col_diff: dest_col - source_col (range -4 to +4)
        -4 = deep backward (blue)
         0 = lateral (white)
        +4 = deep forward (red)
    """
    # Normalize to -1..+1 (dividing by 1.5 so even small differences saturate quickly)
    t = max(-1.0, min(1.0, col_diff / 1.5))

    if t < 0:
        # Backward: blue to white
        factor = 1.0 + t  # 0 at t=-1, 1 at t=0
        r = int(80 + 175 * factor)
        g = int(80 + 175 * factor)
        b = 255
    else:
        # Forward: white to red
        factor = t  # 0 at t=0, 1 at t=1
        r = 255
        g = int(255 - 175 * factor)
        b = int(255 - 215 * factor)

    return f'#{r:02x}{g:02x}{b:02x}'


# Variable station/band configuration — fewer bands in defensive third,
# more granularity in the attacking third.
STATION_CONFIG = [
    # Defensive third: 3 bands (wing left, penalty area, wing right)
    {'x': 8,  'band_edges': [0, 21, 79, 100]},
    {'x': 17, 'band_edges': [0, 21, 79, 100]},
    # Defensive midfield: 4 bands
    {'x': 33, 'band_edges': [0, 25, 50, 75, 100]},
    # Midfield: 5 bands
    {'x': 50, 'band_edges': [0, 20, 40, 60, 80, 100]},
    # Attacking midfield: 5 bands
    {'x': 67, 'band_edges': [0, 20, 40, 60, 80, 100]},
    # Attacking third: 5 bands (wings aligned to PA edges)
    {'x': 83, 'band_edges': [0, 21, 40, 60, 79, 100]},
    {'x': 92, 'band_edges': [0, 21, 40, 60, 79, 100]},
]


def _build_station_flows(pass_df, station_config=None):
    """Decompose passes into station-to-station hops for Sankey rendering.

    Each pass is traced through vertical stations across the pitch. At each
    station boundary, the Y-band is interpolated. Stations can have different
    numbers of Y-bands (fewer in defense, more in attack).

    Args:
        pass_df: DataFrame with source_x, source_y, dest_x, dest_y
        station_config: List of dicts with 'x' and 'band_edges' per station.
                        Defaults to STATION_CONFIG.

    Returns:
        station_config: the config used (list of dicts)
        transitions: dict mapping station_idx -> {(src_band, dst_band): {'fwd': n, 'bwd': n}}
    """
    if station_config is None:
        station_config = STATION_CONFIG

    station_xs = np.array([s['x'] for s in station_config])
    n_stations = len(station_config)

    # Convert band edges to numpy arrays once
    for s_cfg in station_config:
        s_cfg['_edges'] = np.array(s_cfg['band_edges'], dtype=float)

    def get_band(y, station_idx):
        edges = station_config[station_idx]['_edges']
        n_b = len(edges) - 1
        idx = np.searchsorted(edges[1:], y, side='left')
        return min(idx, n_b - 1)

    def get_nearest_station(x):
        return int(np.argmin(np.abs(station_xs - x)))

    transitions = {s: {} for s in range(n_stations - 1)}

    for _, row in pass_df.iterrows():
        x1, y1 = row['source_x'], row['source_y']
        x2, y2 = row['dest_x'], row['dest_y']

        s1 = get_nearest_station(x1)
        s2 = get_nearest_station(x2)

        if s1 == s2:
            continue

        is_forward = s2 > s1
        s_lo, s_hi = min(s1, s2), max(s1, s2)

        for s in range(s_lo, s_hi):
            # Interpolate Y at station s and s+1
            if abs(x2 - x1) > 0.1:
                t_s = np.clip((station_xs[s] - x1) / (x2 - x1), 0, 1)
                t_sn = np.clip((station_xs[s + 1] - x1) / (x2 - x1), 0, 1)
            else:
                t_s, t_sn = 0, 1

            ys = y1 + t_s * (y2 - y1)
            yd = y1 + t_sn * (y2 - y1)

            bs = get_band(ys, s)
            bd = get_band(yd, s + 1)

            key = (bs, bd)
            if key not in transitions[s]:
                transitions[s][key] = {'fwd': 0, 'bwd': 0}

            if is_forward:
                transitions[s][key]['fwd'] += 1
            else:
                transitions[s][key]['bwd'] += 1

    return station_config, transitions


def create_passing_flow_chart(pass_df, team_name, team_color, match_info,
                               stats, competition=''):
    """Build a matplotlib pitch chart with edge-bundled pass curves.

    Each pass is drawn as a smooth curve. Nearby passes with similar paths
    are bundled together, creating cohesive flowing rivers across the pitch.
    Color indicates direction (red=forward, blue=backward).

    Args:
        pass_df: DataFrame from load_passing_data with source/dest coordinates
        team_name: Team name for title
        team_color: Hex color for team
        match_info: Dict with opponent, date, etc.
        stats: Dict from compute_flow_stats
        competition: Optional competition name

    Returns:
        matplotlib Figure
    """
    from matplotlib.path import Path as MplPath
    import matplotlib.patches as mpl_patches

    # Create horizontal full pitch
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

    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Green pitch rectangle
    pitch_rect = Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    # Draw pitch lines
    pitch.draw(ax=ax)

    # Build station-based Sankey flows with variable bands per station
    if not pass_df.empty:
        from matplotlib.path import Path as MplPath
        import matplotlib.patches as mpl_patches

        station_config, transitions = _build_station_flows(pass_df)
        station_xs = np.array([s['x'] for s in station_config])
        n_stations = len(station_config)

        # Per-station band info helpers
        def band_center(s_idx, b_idx):
            edges = station_config[s_idx]['band_edges']
            return (edges[b_idx] + edges[b_idx + 1]) / 2

        def band_height(s_idx, b_idx):
            edges = station_config[s_idx]['band_edges']
            return edges[b_idx + 1] - edges[b_idx]

        # Minimum flow to draw a ribbon (filters out wispy thin lines)
        min_flow = 4

        # Precompute total flow through each (station, band) node
        node_flow = {}
        for s in range(n_stations - 1):
            if s not in transitions:
                continue
            for (bs, bd), counts in transitions[s].items():
                total = counts['fwd'] + counts['bwd']
                if total < min_flow:
                    continue
                node_flow[(s, bs)] = node_flow.get((s, bs), 0) + total
                node_flow[(s + 1, bd)] = node_flow.get((s + 1, bd), 0) + total

        max_node_flow = max(node_flow.values()) if node_flow else 1
        max_ribbon_h = 12.0  # global max ribbon height in pitch units

        # Compute node positions: each (station, band) gets a Y center and height
        node_pos = {}
        for (s, b), flow in node_flow.items():
            bh = band_height(s, b)
            cap = bh * 0.55  # don't overflow the band
            h = min(max(flow / max_node_flow * max_ribbon_h, 1.5), cap)
            node_pos[(s, b)] = {'center': band_center(s, b), 'height': h}

        # Assign left-side positions (stacking outgoing flows at each source node)
        left_assignments = {}
        for s in range(n_stations - 1):
            if s not in transitions:
                continue
            by_src = {}
            for (bs, bd), counts in transitions[s].items():
                total = counts['fwd'] + counts['bwd']
                if total < min_flow:
                    continue
                if bs not in by_src:
                    by_src[bs] = []
                by_src[bs].append((bd, total, counts))

            for bs, flows_list in by_src.items():
                flows_list.sort(key=lambda f: f[0])
                node = node_pos.get((s, bs))
                if not node:
                    continue
                total_out = sum(f[1] for f in flows_list)
                node_h = node['height']
                y_bottom = node['center'] - node_h / 2
                for bd, total, counts in flows_list:
                    h = (total / total_out) * node_h if total_out > 0 else 1
                    left_assignments[(s, bs, bd)] = (y_bottom + h / 2, h)
                    y_bottom += h

        # Assign right-side positions (stacking incoming flows at each dest node)
        right_assignments = {}
        for s in range(n_stations - 1):
            if s not in transitions:
                continue
            by_dst = {}
            for (bs, bd), counts in transitions[s].items():
                total = counts['fwd'] + counts['bwd']
                if total < min_flow:
                    continue
                if bd not in by_dst:
                    by_dst[bd] = []
                by_dst[bd].append((bs, total, counts))

            for bd, flows_list in by_dst.items():
                flows_list.sort(key=lambda f: f[0])
                node = node_pos.get((s + 1, bd))
                if not node:
                    continue
                total_in = sum(f[1] for f in flows_list)
                node_h = node['height']
                y_bottom = node['center'] - node_h / 2
                for bs, total, counts in flows_list:
                    h = (total / total_in) * node_h if total_in > 0 else 1
                    right_assignments[(s, bs, bd)] = (y_bottom + h / 2, h)
                    y_bottom += h

        # Compute blended color at each node based on net direction of all flows
        node_color_data = {}
        for s in range(n_stations - 1):
            if s not in transitions:
                continue
            for (bs, bd), counts in transitions[s].items():
                total = counts['fwd'] + counts['bwd']
                if total < min_flow:
                    continue
                for key in [(s, bs), (s + 1, bd)]:
                    if key not in node_color_data:
                        node_color_data[key] = {'fwd_sum': 0, 'total': 0}
                    node_color_data[key]['fwd_sum'] += counts['fwd'] - counts['bwd']
                    node_color_data[key]['total'] += total

        node_colors = {}
        for key, data in node_color_data.items():
            ratio = data['fwd_sum'] / data['total'] if data['total'] > 0 else 0
            node_colors[key] = _direction_color(ratio * 2.0)

        # Draw ribbons — full span from station to station
        for s in range(n_stations - 1):
            x_left = station_xs[s]
            x_right = station_xs[s + 1]
            gap = x_right - x_left

            if s not in transitions or gap <= 0:
                continue

            for (bs, bd), counts in transitions[s].items():
                total = counts['fwd'] + counts['bwd']
                if total < min_flow:
                    continue

                key = (s, bs, bd)
                if key not in left_assignments or key not in right_assignments:
                    continue

                y_left, h_left = left_assignments[key]
                y_right, h_right = right_assignments[key]

                # Ribbon color: blend between source and dest node colors
                left_color = node_colors.get((s, bs), '#ffffff')
                right_color = node_colors.get((s + 1, bd), '#ffffff')
                alpha = 0.7 + (total / max_node_flow) * 0.15

                # Draw ribbon in 2 halves for color transition
                mid_x = x_left + gap * 0.5
                mid_y = (y_left + y_right) / 2
                mid_h = (h_left + h_right) / 2

                # Tighter control points for smoother, less bulgy curves
                cp = gap * 0.35  # control point offset

                codes = [
                    MplPath.MOVETO,
                    MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                    MplPath.LINETO,
                    MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                    MplPath.CLOSEPOLY,
                ]

                # Left half (source node color)
                verts_l = [
                    (x_left, y_left - h_left / 2),
                    (x_left + cp, y_left - h_left / 2),
                    (mid_x - cp * 0.15, mid_y - mid_h / 2),
                    (mid_x, mid_y - mid_h / 2),
                    (mid_x, mid_y + mid_h / 2),
                    (mid_x - cp * 0.15, mid_y + mid_h / 2),
                    (x_left + cp, y_left + h_left / 2),
                    (x_left, y_left + h_left / 2),
                    (x_left, y_left - h_left / 2),
                ]
                # Right half (dest node color)
                verts_r = [
                    (mid_x, mid_y - mid_h / 2),
                    (mid_x + cp * 0.15, mid_y - mid_h / 2),
                    (x_right - cp, y_right - h_right / 2),
                    (x_right, y_right - h_right / 2),
                    (x_right, y_right + h_right / 2),
                    (x_right - cp, y_right + h_right / 2),
                    (mid_x + cp * 0.15, mid_y + mid_h / 2),
                    (mid_x, mid_y + mid_h / 2),
                    (mid_x, mid_y - mid_h / 2),
                ]

                for verts, color in [(verts_l, left_color), (verts_r, right_color)]:
                    path = MplPath(verts, codes)
                    patch = mpl_patches.PathPatch(
                        path, facecolor=color, edgecolor='none',
                        alpha=alpha, zorder=5,
                    )
                    ax.add_patch(patch)

    # Title
    opponent = match_info.get('opponent', '')
    date = match_info.get('date', '')
    score_str = match_info.get('score', '')

    title_text = f"{team_name.upper()} PROGRESSIVE FLOW"
    fig.suptitle(title_text, fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Subtitle
    subtitle_parts = []
    if opponent:
        subtitle_parts.append(f"vs {opponent}")
    if score_str:
        subtitle_parts.append(score_str)
    if date:
        subtitle_parts.append(date)
    if competition:
        subtitle_parts.append(competition.upper())
    if subtitle_parts:
        fig.text(0.5, 0.92, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Color legend (blue = backward, white = lateral, red = forward)
    legend_ax = fig.add_axes([0.30, 0.855, 0.40, 0.025], facecolor='none')
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.axis('off')

    n_steps = 100
    for i in range(n_steps):
        t = i / n_steps
        col_val = (t - 0.5) * 3.0
        legend_ax.axvspan(i / n_steps, (i + 1) / n_steps,
                          facecolor=_direction_color(col_val), alpha=0.85)

    legend_ax.text(0.0, -0.5, 'BACKWARD', ha='center', va='top',
                   fontsize=8, color='#4040ff', fontweight='bold',
                   transform=legend_ax.transAxes)
    legend_ax.text(0.5, -0.5, 'LATERAL', ha='center', va='top',
                   fontsize=8, color=TEXT_SECONDARY, fontweight='bold',
                   transform=legend_ax.transAxes)
    legend_ax.text(1.0, -0.5, 'FORWARD', ha='center', va='top',
                   fontsize=8, color='#ff3010', fontweight='bold',
                   transform=legend_ax.transAxes)

    # Stats box at bottom — two lines: position and direction
    pos = stats.get('position', {'left': 0, 'center': 0, 'right': 0})
    dirn = stats.get('direction', {'forward': 0, 'sideways': 0, 'backward': 0})
    line1 = f"Left: {pos['left']}  |  Center: {pos['center']}  |  Right: {pos['right']}"
    line2 = f"Forward: {dirn['forward']}  |  Sideways: {dirn['sideways']}  |  Backward: {dirn['backward']}"
    stats_text = f"{line1}\n{line2}"
    fig.text(0.5, 0.055, stats_text,
             ha='center', va='center', fontsize=11, fontweight='bold',
             color=TEXT_PRIMARY, linespacing=1.6,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=2, alpha=0.95))

    # Direction arrow below pitch
    ax.annotate('', xy=(85, -3), xytext=(15, -3),
                arrowprops=dict(arrowstyle='->', color='white', lw=1.5),
                annotation_clip=False)
    ax.text(50, -5.5, 'ATTACKING DIRECTION', ha='center', va='top',
            fontsize=8, color=TEXT_SECONDARY, fontweight='bold')

    plt.tight_layout(rect=[0.02, 0.04, 0.98, 0.90])

    add_cbs_footer(fig)

    return fig


# ── Zone Reference Diagram ──────────────────────────────────────────────────

def create_zone_reference_figure(team_color='#6CABDD'):
    """Create a small matplotlib pitch diagram showing zone boundaries.

    Returns a matplotlib Figure.
    """
    fig, ax = plt.subplots(figsize=(6, 4), facecolor=BG_COLOR)
    ax.set_facecolor(PITCH_COLOR)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_aspect('equal')

    # Draw zone boundaries
    for z in ZONES:
        x_lo, x_hi = z['x_range']
        y_lo, y_hi = z['y_range']

        rect = mpatches.FancyBboxPatch(
            (x_lo, y_lo), x_hi - x_lo, y_hi - y_lo,
            boxstyle='round,pad=0.5',
            facecolor=team_color, alpha=0.25,
            edgecolor='white', linewidth=0.8
        )
        ax.add_patch(rect)

        # Zone label
        cx = (x_lo + x_hi) / 2
        cy = (y_lo + y_hi) / 2
        label = _short_zone_label(z['name'])
        ax.text(cx, cy, label, ha='center', va='center',
                fontsize=6, color='white', fontweight='bold')

    # Direction arrow
    ax.annotate('', xy=(90, -5), xytext=(10, -5),
                arrowprops=dict(arrowstyle='->', color='white', lw=1.5),
                annotation_clip=False)
    ax.text(50, -8, 'ATTACKING DIRECTION', ha='center', va='top',
            fontsize=7, color='white', fontweight='bold')

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_title('Zone Reference', color='white', fontsize=10, pad=8)

    fig.tight_layout()
    return fig


# ── Match Info Extraction ────────────────────────────────────────────────────

def extract_match_info(df, team_name):
    """Extract match metadata from a TruMedia event log CSV.

    Returns dict with opponent, date, score, home_team, away_team.
    """
    info = {}

    if 'homeTeam' in df.columns and 'awayTeam' in df.columns:
        home = df['homeTeam'].iloc[0]
        away = df['awayTeam'].iloc[0]
        info['home_team'] = home
        info['away_team'] = away

        if team_name.lower() in home.lower() or home.lower() in team_name.lower():
            info['opponent'] = away
        else:
            info['opponent'] = home

    if 'Date' in df.columns:
        info['date'] = str(df['Date'].iloc[0])

    if 'homeFinalScore' in df.columns and 'awayFinalScore' in df.columns:
        hs = df['homeFinalScore'].iloc[0]
        as_ = df['awayFinalScore'].iloc[0]
        home = info.get('home_team', 'Home')
        away = info.get('away_team', 'Away')
        info['score'] = f"{home} {int(hs)} - {int(as_)} {away}"

    return info


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def run(config):
    """Entry point for CLI launcher.

    config keys:
        file_path: Path to TruMedia event log CSV
        output_folder: Output directory
        team_name: Team to analyze (optional, will prompt if missing)
        forward_only: bool (default True)
        competition: str (optional)
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder', os.path.expanduser('~/Downloads'))
    forward_only = config.get('forward_only', True)
    competition = config.get('competition', '')

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

    print(f"\nAnalyzing passing flow for: {team_name}")

    # Get team color
    team_color = get_team_color(team_name)

    # Extract match info
    match_info = extract_match_info(df, team_name)

    # Load and process pass data
    pass_df = load_passing_data(df, team_name)
    if pass_df.empty:
        print("[ERROR] No pass data found for this team.")
        return

    # Build flows
    flows = build_zone_flows(pass_df, forward_only=forward_only)
    stats = compute_flow_stats(pass_df, flows)

    print(f"  Total passes linked: {stats['total_passes']}")
    print(f"  Forward pass %: {stats['forward_pct']}%")
    print(f"  Passes into PA: {stats['passes_into_pa']}")

    # Filter to forward-only if requested
    chart_df = pass_df
    if forward_only:
        fwd_mask = (pass_df['dest_col'] > pass_df['source_col']) | pass_df['dest_zone'].apply(is_penalty_area)
        chart_df = pass_df[fwd_mask]

    # Create pitch flow chart
    fig = create_passing_flow_chart(chart_df, team_name, team_color, match_info,
                                     stats, competition=competition)

    # Save as PNG
    safe_name = team_name.replace(' ', '_')
    filename = f"passing_flow_{safe_name}.png"
    filepath = os.path.join(output_folder, filename)

    os.makedirs(output_folder, exist_ok=True)
    fig.savefig(filepath, dpi=300, bbox_inches='tight',
                facecolor=BG_COLOR, edgecolor='none')
    plt.close(fig)
    print(f"\n[OK] Saved: {filepath}")
