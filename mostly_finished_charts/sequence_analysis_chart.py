"""
Sequence Analysis Chart Builder
Analyzes how possessions build toward shots and visualizes the relationship
between sequence length and shot quality.
"""
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon, Rectangle
import numpy as np
from collections import defaultdict
import os

# Import shared utilities
from shared.colors import (
    TEAM_COLORS, load_custom_colors, save_custom_color,
    fuzzy_match_team, check_color_similarity, resolve_team_colors
)
from shared.styles import BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_SUBTLE, style_axis, style_axis_full_grid
from shared.file_utils import get_file_path, get_output_folder


def draw_striped_bar(ax, x, width, y_bottom, y_top, color1, color2, stripe_width=0.015):
    """Draw a bar with alternating diagonal stripes of two colors for overlap regions."""
    if y_top <= y_bottom:
        return

    height = y_top - y_bottom

    # Draw the full bar in color1 as background
    ax.add_patch(Rectangle((x, y_bottom), width, height,
                           facecolor=color1, edgecolor='none', zorder=1))

    # Draw diagonal stripes of color2
    stripe_spacing = stripe_width * 2

    # Starting point for stripes (extend past left edge to cover full bar)
    start_offset = -height

    stripe_patches = []
    offset = start_offset
    while offset < width:
        # Each stripe is a parallelogram going bottom-left to top-right
        x1 = x + offset
        y1 = y_bottom
        x2 = x + offset + height
        y2 = y_top
        x3 = x + offset + height + stripe_width
        y3 = y_top
        x4 = x + offset + stripe_width
        y4 = y_bottom

        stripe = Polygon([(x1, y1), (x2, y2), (x3, y3), (x4, y4)], closed=True)
        stripe_patches.append(stripe)
        offset += stripe_spacing

    # Add all stripes with clipping to the bar bounds
    for stripe in stripe_patches:
        stripe.set_facecolor(color2)
        stripe.set_edgecolor('none')
        stripe.set_zorder(2)
        ax.add_patch(stripe)
        clip_rect = Rectangle((x, y_bottom), width, height, transform=ax.transData)
        stripe.set_clip_path(clip_rect)

    # Add border around the bar
    ax.add_patch(Rectangle((x, y_bottom), width, height,
                           facecolor='none', edgecolor='white', linewidth=0.5, zorder=3))


def draw_histogram_with_stripes(ax, t1_xgs, t2_xgs, bins, color1, color2, team1_name, team2_name):
    """Draw histogram bars with striped overlap regions."""
    # Calculate histogram counts
    t1_counts, _ = np.histogram(t1_xgs, bins=bins)
    t2_counts, _ = np.histogram(t2_xgs, bins=bins)

    bar_width = bins[1] - bins[0]

    # Draw bars for each bin
    for i, bin_start in enumerate(bins[:-1]):
        t1_count = t1_counts[i]
        t2_count = t2_counts[i]

        if t1_count > 0 and t2_count > 0:
            # Both teams have shots - draw striped overlap
            min_count = min(t1_count, t2_count)

            # Draw overlap region with stripes
            draw_striped_bar(ax, bin_start, bar_width, 0, min_count, color1, color2, stripe_width=0.015)

            # Draw remainder as solid color
            if t1_count > t2_count:
                ax.add_patch(Rectangle((bin_start, min_count), bar_width, t1_count - min_count,
                                       facecolor=color1, edgecolor='white', linewidth=0.5, zorder=3))
            elif t2_count > t1_count:
                ax.add_patch(Rectangle((bin_start, min_count), bar_width, t2_count - min_count,
                                       facecolor=color2, edgecolor='white', linewidth=0.5, zorder=3))
        elif t1_count > 0:
            ax.add_patch(Rectangle((bin_start, 0), bar_width, t1_count,
                                   facecolor=color1, edgecolor='white', linewidth=0.5))
        elif t2_count > 0:
            ax.add_patch(Rectangle((bin_start, 0), bar_width, t2_count,
                                   facecolor=color2, edgecolor='white', linewidth=0.5))

    # Set axis limits
    max_count = max(max(t1_counts) if len(t1_counts) > 0 else 0,
                    max(t2_counts) if len(t2_counts) > 0 else 0)
    ax.set_xlim(bins[0], bins[-1])
    ax.set_ylim(0, max_count + 1 if max_count > 0 else 1)

    # Create legend patches
    patch1 = mpatches.Patch(facecolor=color1, edgecolor='white', label=f'{team1_name} ({len(t1_xgs)} shots)')
    patch2 = mpatches.Patch(facecolor=color2, edgecolor='white', label=f'{team2_name} ({len(t2_xgs)} shots)')

    return [patch1, patch2]


def extract_sequences(filepath):
    """Extract and analyze sequences from TruMedia CSV.
    Returns (sequences, team_colors) where team_colors is extracted from CSV."""

    f = open(filepath, encoding='utf-8')
    reader = csv.reader(f)
    header = next(reader)

    # Helper to safely get column index
    def get_idx(col_name):
        try:
            return header.index(col_name)
        except ValueError:
            return None

    seq_id_idx = get_idx('sequenceId')
    playtype_idx = get_idx('playType')
    team_idx = get_idx('teamAbbrevName')
    team_full_idx = get_idx('Team')  # Full team name
    xg_idx = get_idx('xG')
    direction_idx = get_idx('PsDirection')
    gameclock_idx = get_idx('gameClock')
    period_idx = get_idx('Period')
    color_idx = get_idx('newestTeamColor')

    sequences = defaultdict(list)
    team_colors = {}
    penalty_shootout_excluded = 0

    for row in reader:
        if len(row) < len(header):
            continue

        # Filter out penalty shootout (Period > 4)
        if period_idx is not None and len(row) > period_idx and row[period_idx]:
            try:
                period = int(row[period_idx])
                if period > 4:
                    penalty_shootout_excluded += 1
                    continue
            except ValueError:
                pass

        seq_id = row[seq_id_idx] if seq_id_idx is not None else None
        if seq_id:
            # Prefer full team name, fallback to abbreviation
            team = ''
            if team_full_idx is not None and len(row) > team_full_idx and row[team_full_idx]:
                team = row[team_full_idx]
            elif team_idx is not None and len(row) > team_idx:
                team = row[team_idx]

            sequences[seq_id].append({
                'playType': row[playtype_idx] if playtype_idx else '',
                'team': team,
                'xG': float(row[xg_idx]) if xg_idx and row[xg_idx] else 0,
                'direction': row[direction_idx] if direction_idx and len(row) > direction_idx else '',
                'gameClock': float(row[gameclock_idx]) if gameclock_idx and row[gameclock_idx] else 0
            })

            # Capture team color from CSV
            if color_idx is not None and len(row) > color_idx and row[color_idx] and team:
                team_colors[team] = row[color_idx]

    f.close()

    if penalty_shootout_excluded > 0:
        print(f"  Excluded {penalty_shootout_excluded} penalty shootout events (Period 5+)")

    return sequences, team_colors


def extract_match_info(filepath):
    """Extract match metadata from TruMedia CSV"""

    f = open(filepath, encoding='utf-8')
    reader = csv.reader(f)
    header = next(reader)

    def get_idx(col_name):
        try:
            return header.index(col_name)
        except ValueError:
            return None

    home_idx = get_idx('homeTeam')
    away_idx = get_idx('awayTeam')
    home_score_idx = get_idx('homeFinalScore')
    away_score_idx = get_idx('awayFinalScore')
    date_idx = get_idx('Date')

    row = next(reader)
    f.close()

    return {
        'home_team': row[home_idx] if home_idx else 'Home',
        'away_team': row[away_idx] if away_idx else 'Away',
        'home_score': row[home_score_idx] if home_score_idx else '0',
        'away_score': row[away_score_idx] if away_score_idx else '0',
        'date': row[date_idx] if date_idx else ''
    }


def analyze_sequences(sequences):
    """Analyze sequences and return structured data for visualization"""

    shot_types = ['AttemptSaved', 'Goal', 'PenaltyGoal', 'Miss', 'Post', 'Blocked']

    # By sequence length (aggregated)
    length_data = defaultdict(lambda: {'count': 0, 'xG': 0, 'goals': 0})

    # By sequence length, split by team
    team_length_data = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'xG': 0, 'goals': 0}))

    # By team
    team_data = defaultdict(lambda: {
        'sequences': 0,
        'shots': 0,
        'xG': 0,
        'goals': 0,
        'total_events': 0,
        'shot_sequences_lengths': []
    })

    # Individual shot sequences for scatter
    shot_sequences = []

    for seq_id, events in sequences.items():
        if not events:
            continue

        team = events[0]['team']
        play_types = [e['playType'] for e in events]
        has_shot = any(pt in shot_types for pt in play_types)
        length = len(events)

        team_data[team]['sequences'] += 1
        team_data[team]['total_events'] += length

        if has_shot:
            seq_xg = sum(e['xG'] for e in events)
            goals = sum(1 for e in events if e['playType'] in ('Goal', 'PenaltyGoal'))

            team_data[team]['shots'] += 1
            team_data[team]['xG'] += seq_xg
            team_data[team]['goals'] += goals
            team_data[team]['shot_sequences_lengths'].append(length)

            # Bucket by length
            if length <= 3:
                bucket = '1-3'
            elif length <= 6:
                bucket = '4-6'
            elif length <= 10:
                bucket = '7-10'
            else:
                bucket = '11+'

            # Aggregated
            length_data[bucket]['count'] += 1
            length_data[bucket]['xG'] += seq_xg
            length_data[bucket]['goals'] += goals

            # By team
            team_length_data[team][bucket]['count'] += 1
            team_length_data[team][bucket]['xG'] += seq_xg
            team_length_data[team][bucket]['goals'] += goals

            shot_sequences.append({
                'team': team,
                'length': length,
                'xG': seq_xg,
                'goal': goals > 0
            })

    return length_data, team_data, shot_sequences, team_length_data


def create_sequence_analysis_chart(length_data, team_data, shot_sequences, match_info, output_path, team_colors=None, team_length_data=None):
    """Create a multi-panel sequence analysis chart"""

    if team_colors is None:
        team_colors = {}
    teams = list(team_data.keys())

    # Match team1/team2 to home/away order from match_info
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    # Find which team in our data matches home/away
    team1, team2 = teams[0], teams[1] if len(teams) > 1 else teams[0]
    for t in teams:
        if t.lower() == home_team.lower() or home_team.lower() in t.lower() or t.lower() in home_team.lower():
            team1 = t
        elif t.lower() == away_team.lower() or away_team.lower() in t.lower() or t.lower() in away_team.lower():
            team2 = t

    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor(BG_COLOR)

    # Create grid with more top margin for title
    gs = fig.add_gridspec(2, 2, hspace=0.3, wspace=0.25, top=0.84)

    # ============ Panel 1: Shots by Sequence Length (top left) ============
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_COLOR)

    buckets = ['1-3', '4-6', '7-10', '11+']
    x_pos = np.arange(len(buckets))
    width = 0.35

    # Get data for each team
    t1_counts = [team_length_data[team1][b]['count'] if team_length_data else 0 for b in buckets]
    t1_xgs = [team_length_data[team1][b]['xG'] / team_length_data[team1][b]['count']
              if team_length_data and team_length_data[team1][b]['count'] > 0 else 0 for b in buckets]
    t1_goals = [team_length_data[team1][b]['goals'] if team_length_data else 0 for b in buckets]

    t2_counts = [team_length_data[team2][b]['count'] if team_length_data else 0 for b in buckets]
    t2_xgs = [team_length_data[team2][b]['xG'] / team_length_data[team2][b]['count']
              if team_length_data and team_length_data[team2][b]['count'] > 0 else 0 for b in buckets]
    t2_goals = [team_length_data[team2][b]['goals'] if team_length_data else 0 for b in buckets]

    # Draw side-by-side bars
    bars1 = ax1.bar(x_pos - width/2, t1_counts, width, label=team1,
                    color=team_colors.get(team1, '#888888'), edgecolor='white', linewidth=1)
    bars2 = ax1.bar(x_pos + width/2, t2_counts, width, label=team2,
                    color=team_colors.get(team2, '#666666'), edgecolor='white', linewidth=1)

    # Add xG/shot labels above bars (only if there are shots)
    for bar, count, xg in zip(bars1, t1_counts, t1_xgs):
        if count > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    f'{xg:.2f}\nxG/shot', ha='center', va='bottom',
                    fontsize=6, color='white', fontweight='bold')

    for bar, count, xg in zip(bars2, t2_counts, t2_xgs):
        if count > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    f'{xg:.2f}\nxG/shot', ha='center', va='bottom',
                    fontsize=6, color='white', fontweight='bold')

    ax1.set_xticks(x_pos)
    ax1.set_xticklabels([f'{b}\nevents' for b in buckets], fontsize=11, color='white')
    ax1.set_ylabel('SHOTS', fontsize=12, fontweight='bold', color='white')
    ax1.set_title('SHOTS BY SEQUENCE LENGTH', fontsize=14, fontweight='bold', color='white', pad=10)
    max_count = max(max(t1_counts) if t1_counts else 0, max(t2_counts) if t2_counts else 0)
    ax1.set_ylim(0, max_count * 1.3 if max_count > 0 else 10)

    # Add legend
    ax1.legend(loc='upper right', fontsize=9, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')
    style_axis(ax1)

    # ============ Panel 2: Team Comparison (top right) ============
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(BG_COLOR)

    metrics = ['Sequences', 'Avg Length', 'Shot Rate %', 'xG/Shot']
    x_pos2 = np.arange(len(metrics))

    t1_data = team_data[team1]
    t2_data = team_data[team2]

    t1_values = [
        t1_data['sequences'],
        t1_data['total_events'] / t1_data['sequences'] if t1_data['sequences'] > 0 else 0,
        t1_data['shots'] / t1_data['sequences'] * 100 if t1_data['sequences'] > 0 else 0,
        t1_data['xG'] / t1_data['shots'] if t1_data['shots'] > 0 else 0
    ]

    t2_values = [
        t2_data['sequences'],
        t2_data['total_events'] / t2_data['sequences'] if t2_data['sequences'] > 0 else 0,
        t2_data['shots'] / t2_data['sequences'] * 100 if t2_data['sequences'] > 0 else 0,
        t2_data['xG'] / t2_data['shots'] if t2_data['shots'] > 0 else 0
    ]

    # Normalize for display (different scales)
    max_vals = [max(t1_values[i], t2_values[i]) for i in range(len(metrics))]
    t1_norm = [t1_values[i] / max_vals[i] if max_vals[i] > 0 else 0 for i in range(len(metrics))]
    t2_norm = [t2_values[i] / max_vals[i] if max_vals[i] > 0 else 0 for i in range(len(metrics))]

    bars1 = ax2.bar(x_pos2 - width/2, t1_norm, width, label=team1,
                    color=team_colors.get(team1, '#888888'), edgecolor='white', linewidth=1)
    bars2 = ax2.bar(x_pos2 + width/2, t2_norm, width, label=team2,
                    color=team_colors.get(team2, '#666666'), edgecolor='white', linewidth=1)

    # Add actual values as labels
    for i, (bar, val) in enumerate(zip(bars1, t1_values)):
        label = f'{val:.2f}' if i == 3 else (f'{val:.1f}' if i > 0 else f'{int(val)}')
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                label, ha='center', va='bottom', fontsize=10, color='white', fontweight='bold')

    for i, (bar, val) in enumerate(zip(bars2, t2_values)):
        label = f'{val:.2f}' if i == 3 else (f'{val:.1f}' if i > 0 else f'{int(val)}')
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                label, ha='center', va='bottom', fontsize=10, color='white', fontweight='bold')

    ax2.set_xticks(x_pos2)
    ax2.set_xticklabels(metrics, fontsize=11, color='white')
    ax2.set_title('TEAM SEQUENCE PROFILES', fontsize=14, fontweight='bold', color='white', pad=10)
    ax2.set_ylim(0, 1.35)
    ax2.legend(loc='upper right', fontsize=11, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')
    ax2.set_yticklabels([])
    ax2.set_yticks([])

    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color(SPINE_COLOR)
    ax2.spines['bottom'].set_color(SPINE_COLOR)
    ax2.tick_params(colors=SPINE_COLOR, labelcolor='white')
    ax2.set_axisbelow(True)

    # ============ Panel 3: Shot Scatter (bottom left) ============
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(BG_COLOR)

    for shot in shot_sequences:
        color = team_colors.get(shot['team'], '#888888')
        marker = 'o' if not shot['goal'] else '*'
        size = 100 if not shot['goal'] else 300
        alpha = 0.7 if not shot['goal'] else 1.0
        ax3.scatter(shot['length'], shot['xG'], c=color, s=size, marker=marker,
                   alpha=alpha, edgecolors='white', linewidths=1)

    ax3.set_xlabel('SEQUENCE LENGTH (EVENTS)', fontsize=12, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=12, fontweight='bold', color='white')
    ax3.set_title('INDIVIDUAL SHOTS: LENGTH vs QUALITY', fontsize=14, fontweight='bold', color='white', pad=10)

    # Dynamic axis limits based on actual data
    if shot_sequences:
        max_length = max(s['length'] for s in shot_sequences)
        max_xg = max(s['xG'] for s in shot_sequences)
        ax3.set_xlim(0, max_length + 1)
        ax3.set_ylim(0, max_xg * 1.15)

    style_axis_full_grid(ax3)

    # Legend for scatter
    legend_elements = [
        plt.scatter([], [], c=team_colors.get(team1, '#888888'), s=100, label=team1, edgecolors='white'),
        plt.scatter([], [], c=team_colors.get(team2, '#666666'), s=100, label=team2, edgecolors='white'),
        plt.scatter([], [], c='white', s=100, marker='o', label='Shot', edgecolors=SPINE_COLOR),
        plt.scatter([], [], c='white', s=200, marker='*', label='Goal', edgecolors=SPINE_COLOR),
    ]
    ax3.legend(handles=legend_elements, loc='upper right', fontsize=9,
              facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')

    # ============ Panel 4: xG Distribution (bottom right) ============
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(BG_COLOR)

    # Extract xG values for each team from shot sequences
    t1_xgs = [shot['xG'] for shot in shot_sequences if shot['team'] == team1 and shot['xG'] > 0]
    t2_xgs = [shot['xG'] for shot in shot_sequences if shot['team'] == team2 and shot['xG'] > 0]

    # Create histogram bins - use finer granularity for low xG matches
    all_xgs = t1_xgs + t2_xgs
    max_xg = max(all_xgs) if all_xgs else 1.0
    bin_width = 0.05 if max_xg < 0.5 else 0.1
    bin_end = max_xg + bin_width  # One bin width of padding
    bins = np.arange(0, bin_end + bin_width, bin_width)

    # Plot histograms with striped overlaps
    color1 = team_colors.get(team1, '#888888')
    color2 = team_colors.get(team2, '#666666')
    legend_patches = draw_histogram_with_stripes(ax4, t1_xgs, t2_xgs, bins, color1, color2, team1, team2)

    ax4.set_xlabel('xG PER SHOT', fontsize=12, fontweight='bold', color='white')
    ax4.set_ylabel('COUNT', fontsize=12, fontweight='bold', color='white')
    ax4.set_title('SHOT QUALITY DISTRIBUTION', fontsize=14, fontweight='bold', color='white', pad=10)

    ax4.legend(handles=legend_patches, loc='upper right', fontsize=10, facecolor=BG_COLOR,
              edgecolor=SPINE_COLOR, labelcolor='white')
    style_axis(ax4)

    # Main title with xG totals
    title = f"{match_info['home_team'].upper()} {match_info['home_score']}-{match_info['away_score']} {match_info['away_team'].upper()}"
    t1_total_xg = team_data[team1]['xG']
    t2_total_xg = team_data[team2]['xG']
    xg_line = f"{team1} {t1_total_xg:.2f} xG  -  {team2} {t2_total_xg:.2f} xG"
    fig.text(0.5, 0.97, title, ha='center', fontsize=22, fontweight='bold', color='white')
    fig.text(0.5, 0.935, xg_line, ha='center', fontsize=14, fontweight='bold', color='#B8C5D6')
    fig.text(0.5, 0.905, 'SEQUENCE ANALYSIS: HOW POSSESSIONS BUILD TO SHOTS',
            ha='center', fontsize=12, color='#8BA3B8', style='italic')

    # Footer
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.01, 'DATA: OPTA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_individual_charts(length_data, team_data, shot_sequences, match_info, output_folder, team_colors=None, team_length_data=None):
    """Create each panel as a standalone chart"""

    if team_colors is None:
        team_colors = {}
    teams = list(team_data.keys())

    # Match team1/team2 to home/away order from match_info
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    # Find which team in our data matches home/away
    team1, team2 = teams[0], teams[1] if len(teams) > 1 else teams[0]
    for t in teams:
        if t.lower() == home_team.lower() or home_team.lower() in t.lower() or t.lower() in home_team.lower():
            team1 = t
        elif t.lower() == away_team.lower() or away_team.lower() in t.lower() or t.lower() in away_team.lower():
            team2 = t

    t1_data = team_data[team1]
    t2_data = team_data[team2]

    title = f"{match_info['home_team'].upper()} {match_info['home_score']}-{match_info['away_score']} {match_info['away_team'].upper()}"
    xg_line = f"{team1} {t1_data['xG']:.2f} xG  -  {team2} {t2_data['xG']:.2f} xG"

    # ============ Chart 1: Shots by Sequence Length (side-by-side by team) ============
    fig1, ax1 = plt.subplots(figsize=(10, 7))
    fig1.patch.set_facecolor(BG_COLOR)
    ax1.set_facecolor(BG_COLOR)

    buckets = ['1-3', '4-6', '7-10', '11+']
    x_pos = np.arange(len(buckets))
    width = 0.35

    # Get data for each team
    t1_counts = [team_length_data[team1][b]['count'] if team_length_data else 0 for b in buckets]
    t1_xgs = [team_length_data[team1][b]['xG'] / team_length_data[team1][b]['count']
              if team_length_data and team_length_data[team1][b]['count'] > 0 else 0 for b in buckets]

    t2_counts = [team_length_data[team2][b]['count'] if team_length_data else 0 for b in buckets]
    t2_xgs = [team_length_data[team2][b]['xG'] / team_length_data[team2][b]['count']
              if team_length_data and team_length_data[team2][b]['count'] > 0 else 0 for b in buckets]

    # Draw side-by-side bars
    bars1 = ax1.bar(x_pos - width/2, t1_counts, width, label=team1,
                    color=team_colors.get(team1, '#888888'), edgecolor='white', linewidth=1)
    bars2 = ax1.bar(x_pos + width/2, t2_counts, width, label=team2,
                    color=team_colors.get(team2, '#666666'), edgecolor='white', linewidth=1)

    # Add xG/shot labels above bars (only if there are shots)
    for bar, count, xg in zip(bars1, t1_counts, t1_xgs):
        if count > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    f'{xg:.2f} xG', ha='center', va='bottom',
                    fontsize=8, color='white', fontweight='bold')

    for bar, count, xg in zip(bars2, t2_counts, t2_xgs):
        if count > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
                    f'{xg:.2f} xG', ha='center', va='bottom',
                    fontsize=8, color='white', fontweight='bold')

    ax1.set_xticks(x_pos)
    ax1.set_xticklabels([f'{b}\nevents' for b in buckets], fontsize=12, color='white')
    ax1.set_ylabel('SHOTS', fontsize=14, fontweight='bold', color='white')
    max_count = max(max(t1_counts) if t1_counts else 0, max(t2_counts) if t2_counts else 0)
    ax1.set_ylim(0, max_count * 1.3 if max_count > 0 else 10)

    # Add legend
    ax1.legend(loc='upper right', fontsize=10, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')
    style_axis(ax1)

    fig1.text(0.5, 0.96, title, ha='center', fontsize=20, fontweight='bold', color='white')
    fig1.text(0.5, 0.915, xg_line, ha='center', fontsize=12, fontweight='bold', color='#B8C5D6')
    fig1.text(0.5, 0.88, 'SHOTS BY SEQUENCE LENGTH', ha='center', fontsize=11, color='#8BA3B8', style='italic')
    fig1.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig1.text(0.98, 0.01, 'DATA: OPTA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=[0, 0.03, 1, 0.85])
    path1 = os.path.join(output_folder, "seq_shot_quality_by_length.png")
    plt.savefig(path1, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path1}")
    plt.close()

    # ============ Chart 2: Team Sequence Profiles ============
    fig2, ax2 = plt.subplots(figsize=(10, 7))
    fig2.patch.set_facecolor(BG_COLOR)
    ax2.set_facecolor(BG_COLOR)

    metrics = ['Sequences', 'Avg Length', 'Shot Rate %', 'xG/Shot']
    x_pos2 = np.arange(len(metrics))
    width = 0.35

    t1_values = [
        t1_data['sequences'],
        t1_data['total_events'] / t1_data['sequences'] if t1_data['sequences'] > 0 else 0,
        t1_data['shots'] / t1_data['sequences'] * 100 if t1_data['sequences'] > 0 else 0,
        t1_data['xG'] / t1_data['shots'] if t1_data['shots'] > 0 else 0
    ]
    t2_values = [
        t2_data['sequences'],
        t2_data['total_events'] / t2_data['sequences'] if t2_data['sequences'] > 0 else 0,
        t2_data['shots'] / t2_data['sequences'] * 100 if t2_data['sequences'] > 0 else 0,
        t2_data['xG'] / t2_data['shots'] if t2_data['shots'] > 0 else 0
    ]

    max_vals = [max(t1_values[i], t2_values[i]) for i in range(len(metrics))]
    t1_norm = [t1_values[i] / max_vals[i] if max_vals[i] > 0 else 0 for i in range(len(metrics))]
    t2_norm = [t2_values[i] / max_vals[i] if max_vals[i] > 0 else 0 for i in range(len(metrics))]

    bars1 = ax2.bar(x_pos2 - width/2, t1_norm, width, label=team1,
                    color=team_colors.get(team1, '#888888'), edgecolor='white', linewidth=1)
    bars2 = ax2.bar(x_pos2 + width/2, t2_norm, width, label=team2,
                    color=team_colors.get(team2, '#666666'), edgecolor='white', linewidth=1)

    for i, (bar, val) in enumerate(zip(bars1, t1_values)):
        label = f'{val:.2f}' if i == 3 else (f'{val:.1f}' if i > 0 else f'{int(val)}')
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                label, ha='center', va='bottom', fontsize=11, color='white', fontweight='bold')
    for i, (bar, val) in enumerate(zip(bars2, t2_values)):
        label = f'{val:.2f}' if i == 3 else (f'{val:.1f}' if i > 0 else f'{int(val)}')
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                label, ha='center', va='bottom', fontsize=11, color='white', fontweight='bold')

    ax2.set_xticks(x_pos2)
    ax2.set_xticklabels(metrics, fontsize=12, color='white')
    ax2.set_ylim(0, 1.35)
    ax2.legend(loc='upper right', fontsize=12, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')
    ax2.set_yticklabels([])
    ax2.set_yticks([])

    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color(SPINE_COLOR)
    ax2.spines['bottom'].set_color(SPINE_COLOR)
    ax2.tick_params(colors=SPINE_COLOR, labelcolor='white')
    ax2.set_axisbelow(True)

    fig2.text(0.5, 0.96, title, ha='center', fontsize=20, fontweight='bold', color='white')
    fig2.text(0.5, 0.915, xg_line, ha='center', fontsize=12, fontweight='bold', color='#B8C5D6')
    fig2.text(0.5, 0.88, 'TEAM SEQUENCE PROFILES', ha='center', fontsize=11, color='#8BA3B8', style='italic')
    fig2.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig2.text(0.98, 0.01, 'DATA: OPTA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=[0, 0.03, 1, 0.85])
    path2 = os.path.join(output_folder, "seq_team_profiles.png")
    plt.savefig(path2, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path2}")
    plt.close()

    # ============ Chart 3: Individual Shots Scatter ============
    fig3, ax3 = plt.subplots(figsize=(10, 7))
    fig3.patch.set_facecolor(BG_COLOR)
    ax3.set_facecolor(BG_COLOR)

    for shot in shot_sequences:
        color = team_colors.get(shot['team'], '#888888')
        marker = 'o' if not shot['goal'] else '*'
        size = 100 if not shot['goal'] else 300
        alpha = 0.7 if not shot['goal'] else 1.0
        ax3.scatter(shot['length'], shot['xG'], c=color, s=size, marker=marker,
                   alpha=alpha, edgecolors='white', linewidths=1)

    ax3.set_xlabel('SEQUENCE LENGTH (EVENTS)', fontsize=14, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=14, fontweight='bold', color='white')

    # Dynamic axis limits based on actual data
    if shot_sequences:
        max_length = max(s['length'] for s in shot_sequences)
        max_xg = max(s['xG'] for s in shot_sequences)
        ax3.set_xlim(0, max_length + 1)
        ax3.set_ylim(0, max_xg * 1.15)

    style_axis_full_grid(ax3)

    legend_elements = [
        plt.scatter([], [], c=team_colors.get(team1, '#888888'), s=100, label=team1, edgecolors='white'),
        plt.scatter([], [], c=team_colors.get(team2, '#666666'), s=100, label=team2, edgecolors='white'),
        plt.scatter([], [], c='white', s=100, marker='o', label='Shot', edgecolors=SPINE_COLOR),
        plt.scatter([], [], c='white', s=200, marker='*', label='Goal', edgecolors=SPINE_COLOR),
    ]
    ax3.legend(handles=legend_elements, loc='upper right', fontsize=11,
              facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white')

    fig3.text(0.5, 0.96, title, ha='center', fontsize=20, fontweight='bold', color='white')
    fig3.text(0.5, 0.915, xg_line, ha='center', fontsize=12, fontweight='bold', color='#B8C5D6')
    fig3.text(0.5, 0.88, 'INDIVIDUAL SHOTS: LENGTH vs QUALITY', ha='center', fontsize=11, color='#8BA3B8', style='italic')
    fig3.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig3.text(0.98, 0.01, 'DATA: OPTA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=[0, 0.03, 1, 0.85])
    path3 = os.path.join(output_folder, "seq_shots_scatter.png")
    plt.savefig(path3, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path3}")
    plt.close()

    # ============ Chart 4: xG Distribution ============
    fig4, ax4 = plt.subplots(figsize=(10, 7))
    fig4.patch.set_facecolor(BG_COLOR)
    ax4.set_facecolor(BG_COLOR)

    t1_xgs = [shot['xG'] for shot in shot_sequences if shot['team'] == team1 and shot['xG'] > 0]
    t2_xgs = [shot['xG'] for shot in shot_sequences if shot['team'] == team2 and shot['xG'] > 0]

    all_xgs = t1_xgs + t2_xgs
    max_xg = max(all_xgs) if all_xgs else 1.0
    bin_width = 0.05 if max_xg < 0.5 else 0.1
    bin_end = max_xg + bin_width  # One bin width of padding
    bins = np.arange(0, bin_end + bin_width, bin_width)

    # Plot histograms with striped overlaps
    color1 = team_colors.get(team1, '#888888')
    color2 = team_colors.get(team2, '#666666')
    legend_patches = draw_histogram_with_stripes(ax4, t1_xgs, t2_xgs, bins, color1, color2, team1, team2)

    ax4.set_xlabel('xG PER SHOT', fontsize=14, fontweight='bold', color='white')
    ax4.set_ylabel('COUNT', fontsize=14, fontweight='bold', color='white')

    ax4.legend(handles=legend_patches, loc='upper right', fontsize=12, facecolor=BG_COLOR,
              edgecolor=SPINE_COLOR, labelcolor='white')
    style_axis(ax4)

    fig4.text(0.5, 0.96, title, ha='center', fontsize=20, fontweight='bold', color='white')
    fig4.text(0.5, 0.915, xg_line, ha='center', fontsize=12, fontweight='bold', color='#B8C5D6')
    fig4.text(0.5, 0.88, 'SHOT QUALITY DISTRIBUTION', ha='center', fontsize=11, color='#8BA3B8', style='italic')
    fig4.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig4.text(0.98, 0.01, 'DATA: OPTA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=[0, 0.03, 1, 0.85])
    path4 = os.path.join(output_folder, "seq_xg_distribution.png")
    plt.savefig(path4, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path4}")
    plt.close()


def run(config):
    """Entry point for launcher - config contains all needed params.

    Config keys:
        file_path: str - Path to TruMedia CSV file
        output_folder: str - Where to save charts
        gui_mode: bool - If True, skip interactive prompts for color similarity (default False)
        team_colors: dict - Optional pre-resolved team colors (overrides auto-detection)
    """
    file_path = config['file_path']
    output_folder = config['output_folder']
    gui_mode = config.get('gui_mode', False)
    team_colors_override = config.get('team_colors', None)

    print("\nExtracting sequences...")
    sequences, csv_team_colors = extract_sequences(file_path)
    print(f"  Found {len(sequences)} sequences")
    if csv_team_colors:
        print(f"[OK] Team colors auto-detected from CSV: {', '.join(csv_team_colors.keys())}")

    print("\nAnalyzing sequence patterns...")
    length_data, team_data, shot_sequences, team_length_data = analyze_sequences(sequences)
    print(f"  Found {len(shot_sequences)} shot sequences")

    print("\nExtracting match info...")
    match_info = extract_match_info(file_path)
    print(f"  {match_info['home_team']} {match_info['home_score']}-{match_info['away_score']} {match_info['away_team']}")

    # Use pre-resolved colors if provided (from GUI color picker), otherwise resolve
    teams = list(team_data.keys())
    if team_colors_override:
        print("\n[OK] Using colors from GUI color picker")
        for team, color in team_colors_override.items():
            print(f"  {team}: {color}")
        resolved_colors = team_colors_override
    else:
        # Resolve team colors with fallback chain (non-interactive in GUI mode)
        resolved_colors = resolve_team_colors(teams, csv_team_colors, interactive=not gui_mode)

    output_path = os.path.join(output_folder, "sequence_analysis.png")

    print("\nGenerating charts...")
    create_sequence_analysis_chart(length_data, team_data, shot_sequences, match_info, output_path, resolved_colors, team_length_data)

    print("\nGenerating individual charts...")
    create_individual_charts(length_data, team_data, shot_sequences, match_info, output_folder, resolved_colors, team_length_data)

    print("\nDone!")


def main():
    """Standalone entry point - prompts user for inputs."""
    print("\n" + "="*60)
    print("SEQUENCE ANALYSIS CHART BUILDER")
    print("="*60)
    print("Analyzes how possessions build toward shots.")
    print("Requires TruMedia CSV with sequence data.")

    event_path = get_file_path("TruMedia CSV file")
    if not event_path:
        return

    output_folder = get_output_folder()

    config = {
        'file_path': event_path,
        'output_folder': output_folder
    }
    run(config)


if __name__ == "__main__":
    main()
