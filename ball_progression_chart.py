"""
Ball Progression Profile Chart
4-panel visualization showing how a team progresses the ball:
1. Player Progression Leaders (horizontal bar) - top players by progressive actions
2. Team Pass Destination Breakdown (donut chart) - where passes go
3. Progression Style (scatter plot) - passing vs carrying tendencies
4. Penetration by Position Group (grouped bar) - who creates final third entries

Uses CBS Sports styling.
"""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os
import sys

# Add parent directory for shared imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.styles import (
    BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY,
    TEXT_MUTED, add_cbs_footer, style_axis
)
from shared.file_utils import get_file_path, get_output_folder
from shared.colors import ensure_contrast_with_background


# =============================================================================
# CONSTANTS
# =============================================================================
# Colors for progression components
PROG_PASS_COLOR = '#6CABDD'  # Light blue for progressive passes
PROG_CARRY_COLOR = '#2ECC71'  # Green for progressive carries

# Colors for pass destinations (donut chart)
DEST_D3_COLOR = '#3498DB'     # Blue for defensive third
DEST_M3_COLOR = '#F39C12'     # Orange for middle third
DEST_A3_COLOR = '#E74C3C'     # Red for attacking third
DEST_PEN_COLOR = '#9B59B6'    # Purple for penalty area

# Colors for position groups
POS_DEF_COLOR = '#3498DB'     # Blue for defenders
POS_MID_COLOR = '#2ECC71'     # Green for midfielders
POS_FWD_COLOR = '#E74C3C'     # Red for forwards

# Position mapping
POSITION_TO_GROUP = {
    'Goalkeeper': 'GK',
    'Right Centre Back': 'DEF',
    'Left Centre Back': 'DEF',
    'Centre Back': 'DEF',
    'Right Back': 'DEF',
    'Left Back': 'DEF',
    'Right Wing Back': 'DEF',
    'Left Wing Back': 'DEF',
    'Defensive Midfielder': 'MID',
    'Central Midfielder': 'MID',
    'Right Midfielder': 'MID',
    'Left Midfielder': 'MID',
    'Attacking Midfielder': 'MID',
    'Right Attacking Midfielder': 'MID',
    'Left Attacking Midfielder': 'MID',
    'Right Winger': 'FWD',
    'Left Winger': 'FWD',
    'Centre Forward': 'FWD',
    'Striker': 'FWD',
    'Second Striker': 'FWD',
}


def load_data(csv_path):
    """Load and process player progression data from CSV."""
    df = pd.read_csv(csv_path, encoding='utf-8')
    return df


def calculate_per_90(value, minutes):
    """Calculate per-90 value."""
    if pd.isna(value) or pd.isna(minutes) or minutes == 0:
        return 0
    return (value / minutes) * 90


def prepare_progression_data(df, min_minutes=200, max_players=15):
    """Prepare data for the progression chart.

    Args:
        df: DataFrame with player data
        min_minutes: Minimum minutes to include player
        max_players: Maximum players to show

    Returns:
        DataFrame sorted by total progression per 90
    """
    # Filter by minutes
    filtered = df[df['Min'] >= min_minutes].copy()

    # Calculate per-90 values
    filtered['ProgPass_p90'] = filtered.apply(
        lambda r: calculate_per_90(r['ProgPass'], r['Min']), axis=1
    )
    filtered['ProgCarry_p90'] = filtered.apply(
        lambda r: calculate_per_90(r['ProgCarry'], r['Min']), axis=1
    )
    filtered['PsIntoPen_p90'] = filtered.apply(
        lambda r: calculate_per_90(r['PsIntoPen'], r['Min']), axis=1
    )
    filtered['ThrghBlCmp_p90'] = filtered.apply(
        lambda r: calculate_per_90(r['ThrghBlCmp'], r['Min']), axis=1
    )
    filtered['CarryDist_p90'] = filtered.apply(
        lambda r: calculate_per_90(r['CarryDist'], r['Min']), axis=1
    )

    # Total progression for sorting
    filtered['TotalProg_p90'] = filtered['ProgPass_p90'] + filtered['ProgCarry_p90']

    # Parse percentage columns (remove % sign if present)
    for col in ['Ps%ToA3', 'Ps%ToD3']:
        if col in filtered.columns:
            if filtered[col].dtype == 'object':
                filtered[col] = filtered[col].str.rstrip('%').astype(float)

    # Sort by total progression and take top N
    filtered = filtered.sort_values('TotalProg_p90', ascending=False).head(max_players)

    return filtered


def create_progression_chart(df, team_name, output_path, full_df=None, league_df=None):
    """Create the 4-panel ball progression profile chart.

    Args:
        df: Prepared DataFrame with per-90 values (filtered/sorted for bar chart)
        team_name: Team name for title
        output_path: Where to save the chart
        full_df: Full DataFrame for team-level aggregations
        league_df: League-wide team data for comparison (optional)
    """
    if full_df is None:
        full_df = df

    # Create figure with 4 subplots (2x2)
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.patch.set_facecolor(BG_COLOR)

    # ==========================================================================
    # Panel 1: Player Progression Leaders (Horizontal Bar)
    # ==========================================================================
    ax1 = axes[0, 0]
    ax1.set_facecolor(BG_COLOR)

    # Use top 10 for cleaner display
    plot_df = df.head(10).iloc[::-1].reset_index(drop=True)
    num_players = len(plot_df)
    y_positions = np.arange(num_players)
    names = plot_df['Player'].tolist()

    prog_pass = plot_df['ProgPass_p90'].values
    prog_carry = plot_df['ProgCarry_p90'].values

    # Stacked horizontal bars
    ax1.barh(y_positions, prog_pass, height=0.6,
             color=PROG_PASS_COLOR, label='Progressive Passes')
    ax1.barh(y_positions, prog_carry, height=0.6, left=prog_pass,
             color=PROG_CARRY_COLOR, label='Progressive Carries')

    # Add total value labels
    for i, (pp, pc) in enumerate(zip(prog_pass, prog_carry)):
        total = pp + pc
        ax1.text(total + 0.3, i, f'{total:.1f}', va='center', ha='left',
                color='white', fontsize=9, fontweight='bold')

    ax1.set_yticks(y_positions)
    ax1.set_yticklabels(names, fontsize=9, color='white')
    ax1.set_xlabel('Per 90 Minutes', fontsize=10, color=TEXT_SECONDARY)
    ax1.set_title('Progression Leaders', fontsize=13, fontweight='bold',
                  color='white', pad=10)

    ax1.legend(loc='lower right', fontsize=8, facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white')

    style_axis(ax1)
    ax1.tick_params(axis='y', left=False)

    # ==========================================================================
    # Panel 2: Team Pass Destination Breakdown (Donut Chart with Legend)
    # ==========================================================================
    ax2 = axes[0, 1]
    ax2.set_facecolor(BG_COLOR)

    # Aggregate team totals for pass destinations
    total_to_d3 = full_df['PsCmpToD3'].sum()
    total_to_m3 = full_df['PsCmpToM3'].sum()
    total_to_a3 = full_df['PsCmpToA3'].sum()
    total_to_pen = full_df['PsIntoPen'].sum()

    # Order by size (largest first) for cleaner visual
    values = [total_to_m3, total_to_a3, total_to_d3, total_to_pen]
    labels = ['To Middle Third', 'To Attacking Third', 'To Defensive Third', 'Into Penalty Area']
    colors = [DEST_M3_COLOR, DEST_A3_COLOR, DEST_D3_COLOR, DEST_PEN_COLOR]
    total = sum(values)

    # Create donut with percentage labels ON the wedges
    wedges, texts, autotexts = ax2.pie(
        values, labels=None, colors=colors,
        autopct=lambda pct: f'{pct:.1f}%' if pct > 5 else '',
        startangle=90, pctdistance=0.75, radius=1.0,
        wedgeprops=dict(width=0.4, edgecolor=BG_COLOR, linewidth=2),
        textprops=dict(color='white', fontsize=11, fontweight='bold')
    )

    # Style the autopct text
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(11)

    # Center text
    ax2.text(0, 0, f'{total:,.0f}\nPasses', ha='center', va='center',
             fontsize=14, color='white', fontweight='bold')

    ax2.set_title('Pass Destinations', fontsize=13, fontweight='bold',
                  color='white', pad=10)

    # Add legend to the right side
    legend_labels = [f'{labels[i]} ({values[i]:,.0f})' for i in range(len(labels))]
    ax2.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1.0, 0.5),
               fontsize=9, facecolor=BG_COLOR, edgecolor=SPINE_COLOR,
               labelcolor='white', framealpha=0.9)

    ax2.set_aspect('equal')

    # ==========================================================================
    # Panel 3: Team Progression Style (Spectrum Visualization with League Context)
    # ==========================================================================
    ax3 = axes[1, 0]
    ax3.set_facecolor(BG_COLOR)

    # Filter out goalkeepers for these calculations
    outfield = full_df[full_df['Position'] != 'Goalkeeper'].copy()

    def safe_pct_to_float(series):
        """Convert percentage strings to float, handling '-' and other invalid values."""
        def convert(val):
            if pd.isna(val) or val == '-' or val == '':
                return np.nan
            if isinstance(val, str):
                return float(val.rstrip('%'))
            return float(val)
        return series.apply(convert)

    def calculate_percentile(team_value, league_values):
        """Calculate where team ranks in league (0-100 percentile)."""
        if len(league_values) == 0:
            return 50
        below = sum(1 for v in league_values if v < team_value)
        return (below / len(league_values)) * 100

    # Calculate team metrics
    total_carry_dist = outfield['CarryDist'].sum()
    total_carries = outfield['Carries'].sum() if 'Carries' in outfield.columns else 1
    team_dist_per_carry = total_carry_dist / total_carries if total_carries > 0 else 0

    long_pct_series = safe_pct_to_float(outfield['Pass%Long'])
    team_long_pct = long_pct_series.mean() if not long_pct_series.isna().all() else 50

    fwd_series = safe_pct_to_float(outfield['%PassFwd'])
    team_fwd_pct = fwd_series.mean() if not fwd_series.isna().all() else 50

    total_passes = outfield['PsCmp'].sum()
    total_risky = outfield['ThrghBlCmp'].sum() + outfield['PsIntoPen'].sum()
    team_risk_pct = total_risky / total_passes * 100 if total_passes > 0 else 0

    # If we have league data, calculate percentiles
    if league_df is not None and len(league_df) > 0:
        # Calculate league values for each metric
        league_long_pcts = safe_pct_to_float(league_df['Pass%Long']).dropna().tolist()
        league_fwd_pcts = safe_pct_to_float(league_df['%PassFwd']).dropna().tolist()

        # Carry distance per carry for each team
        league_dist_per_carry = []
        for _, row in league_df.iterrows():
            if row['Carries'] > 0:
                league_dist_per_carry.append(row['CarryDist'] / row['Carries'])

        # Risk percentage for each team
        league_risk_pcts = []
        for _, row in league_df.iterrows():
            if row['PsCmp'] > 0:
                risk = (row['ThrghBlCmp'] + row['PsIntoPen']) / row['PsCmp'] * 100
                league_risk_pcts.append(risk)

        # Calculate percentiles
        long_percentile = calculate_percentile(team_long_pct, league_long_pcts)
        fwd_percentile = calculate_percentile(team_fwd_pct, league_fwd_pcts)
        carry_percentile = calculate_percentile(team_dist_per_carry, league_dist_per_carry)
        risk_percentile = calculate_percentile(team_risk_pct, league_risk_pcts)

        # Find team rank for display
        def get_rank(team_value, league_values, higher_is_better=True):
            sorted_vals = sorted(league_values, reverse=higher_is_better)
            for i, v in enumerate(sorted_vals):
                if (higher_is_better and team_value >= v) or (not higher_is_better and team_value <= v):
                    return i + 1
            return len(league_values)

        long_rank = get_rank(team_long_pct, league_long_pcts)
        fwd_rank = get_rank(team_fwd_pct, league_fwd_pcts)
        carry_rank = get_rank(team_dist_per_carry, league_dist_per_carry)
        risk_rank = get_rank(team_risk_pct, league_risk_pcts)
        n_teams = len(league_df)

        # Define spectrums with league context - show actual value and rank
        spectrums = [
            ('Short Pass', 'Long Pass', long_percentile, f'{team_long_pct:.1f}%', f'#{long_rank}/{n_teams}', '#3498DB', '#E74C3C'),
            ('Backward', 'Forward', fwd_percentile, f'{team_fwd_pct:.1f}%', f'#{fwd_rank}/{n_teams}', '#95A5A6', '#2ECC71'),
            ('Short Carry', 'Long Carry', carry_percentile, f'{team_dist_per_carry:.1f}m', f'#{carry_rank}/{n_teams}', '#3498DB', '#2ECC71'),
            ('Safe', 'Risky', risk_percentile, f'{team_risk_pct:.1f}%', f'#{risk_rank}/{n_teams}', '#3498DB', '#E74C3C'),
        ]
        subtitle = f'vs. {n_teams} Premier League Teams'
    else:
        # Fallback without league data - use normalized values
        long_norm = min(max(team_long_pct, 0), 100)
        fwd_norm = min(max(team_fwd_pct, 0), 100)
        carry_norm = min(max((team_dist_per_carry - 3) / 4 * 100, 0), 100)
        risk_norm = min(team_risk_pct * 5, 100)

        spectrums = [
            ('Short Pass', 'Long Pass', long_norm, f'{team_long_pct:.1f}%', '', '#3498DB', '#E74C3C'),
            ('Backward', 'Forward', fwd_norm, f'{team_fwd_pct:.1f}%', '', '#95A5A6', '#2ECC71'),
            ('Short Carry', 'Long Carry', carry_norm, f'{team_dist_per_carry:.1f}m', '', '#3498DB', '#2ECC71'),
            ('Safe', 'Risky', risk_norm, f'{team_risk_pct:.1f}%', '', '#3498DB', '#E74C3C'),
        ]
        subtitle = None

    n_spectrums = len(spectrums)
    y_positions = np.linspace(0.80, 0.20, n_spectrums)

    for i, (left_label, right_label, percentile, actual_value, rank_str, left_color, right_color) in enumerate(spectrums):
        y = y_positions[i]

        # Draw spectrum bar background
        bar_left = 0.18
        bar_right = 0.82
        bar_width = bar_right - bar_left
        bar_height = 0.06

        # Gradient-like effect with two colors
        ax3.add_patch(plt.Rectangle((bar_left, y - bar_height/2), bar_width/2, bar_height,
                                     facecolor=left_color, alpha=0.3, edgecolor='none'))
        ax3.add_patch(plt.Rectangle((bar_left + bar_width/2, y - bar_height/2), bar_width/2, bar_height,
                                     facecolor=right_color, alpha=0.3, edgecolor='none'))

        # Draw marker at percentile position
        marker_x = bar_left + (percentile / 100) * bar_width
        ax3.plot(marker_x, y, 'o', markersize=14, color='white', markeredgecolor=SPINE_COLOR, markeredgewidth=2)

        # Show actual value inside marker
        ax3.text(marker_x, y, actual_value, ha='center', va='center', fontsize=6,
                color=BG_COLOR, fontweight='bold')

        # Show rank above the marker if available
        if rank_str:
            ax3.text(marker_x, y + 0.045, rank_str, ha='center', va='bottom', fontsize=8,
                    color=TEXT_SECONDARY, fontweight='bold')

        # Labels
        ax3.text(bar_left - 0.02, y, left_label, ha='right', va='center',
                fontsize=9, color=left_color, fontweight='bold')
        ax3.text(bar_right + 0.02, y, right_label, ha='left', va='center',
                fontsize=9, color=right_color, fontweight='bold')

    ax3.set_xlim(0, 1)
    ax3.set_ylim(0, 1)
    ax3.axis('off')
    ax3.set_title('Team Progression Style', fontsize=12, fontweight='bold',
                  color='white', pad=10)

    if subtitle:
        ax3.text(0.5, 0.95, subtitle, ha='center', va='top', fontsize=9,
                color=TEXT_MUTED, style='italic', transform=ax3.transAxes)

    # ==========================================================================
    # Panel 4: Player Style Matrix (Heatmap)
    # ==========================================================================
    ax4 = axes[1, 1]
    ax4.set_facecolor(BG_COLOR)

    # Get top 8 players by total progression
    top_for_matrix = outfield.nlargest(8, 'TotalProg_p90').copy()

    # Calculate percentile ranks for each dimension (within the team)
    def percentile_rank(series):
        return series.rank(pct=True) * 100

    # Prepare matrix data - for each player, calculate their tendency on each dimension
    matrix_players = top_for_matrix['Player'].tolist()

    # Calculate player-level metrics (reuse the safe conversion function)
    top_for_matrix['Long%'] = safe_pct_to_float(top_for_matrix['Pass%Long']).fillna(50)
    top_for_matrix['Fwd%'] = safe_pct_to_float(top_for_matrix['%PassFwd']).fillna(50)
    # Distance per carry - how progressive are their carries?
    if 'Carries' in top_for_matrix.columns:
        top_for_matrix['CarryDist_per'] = top_for_matrix['CarryDist'] / top_for_matrix['Carries'].replace(0, 1)
    else:
        top_for_matrix['CarryDist_per'] = 0
    top_for_matrix['Risk%'] = (top_for_matrix['ThrghBlCmp'] + top_for_matrix['PsIntoPen']) / top_for_matrix['PsCmp'].replace(0, 1) * 100

    # Normalize each column to 0-100 within this subset
    dimensions = ['Long%', 'Fwd%', 'CarryDist_per', 'Risk%']
    dim_labels = ['Long\nPass', 'Forward', 'Carry\nDist', 'Risk\nTaker']

    matrix_data = []
    for dim in dimensions:
        col_min = top_for_matrix[dim].min()
        col_max = top_for_matrix[dim].max()
        if col_max > col_min:
            normalized = (top_for_matrix[dim] - col_min) / (col_max - col_min) * 100
        else:
            normalized = pd.Series([50] * len(top_for_matrix))
        matrix_data.append(normalized.tolist())

    matrix_data = np.array(matrix_data).T  # Transpose so rows are players

    # Draw heatmap
    n_players = len(matrix_players)
    n_dims = len(dimensions)

    cell_width = 0.12
    cell_height = 0.08
    start_x = 0.28
    start_y = 0.82

    # Color map - blue (low) to red (high)
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list('custom', ['#3498DB', '#F4D03F', '#E74C3C'])

    for i, player in enumerate(matrix_players):
        y = start_y - i * (cell_height + 0.02)

        # Player name
        ax4.text(start_x - 0.03, y, player, ha='right', va='center',
                fontsize=8, color='white', fontweight='bold')

        for j, dim in enumerate(dimensions):
            x = start_x + j * (cell_width + 0.02)
            value = matrix_data[i, j]

            # Cell color based on value
            color = cmap(value / 100)

            rect = plt.Rectangle((x, y - cell_height/2), cell_width, cell_height,
                                  facecolor=color, edgecolor=BG_COLOR, linewidth=1)
            ax4.add_patch(rect)

            # Value text
            text_color = 'white' if value > 30 and value < 70 else 'white'
            ax4.text(x + cell_width/2, y, f'{value:.0f}', ha='center', va='center',
                    fontsize=8, color=text_color, fontweight='bold')

    # Column headers
    for j, label in enumerate(dim_labels):
        x = start_x + j * (cell_width + 0.02) + cell_width/2
        ax4.text(x, start_y + 0.06, label, ha='center', va='bottom',
                fontsize=8, color=TEXT_SECONDARY, fontweight='bold')

    # Color bar legend
    ax4.text(0.5, 0.02, 'Low ← Style Index → High', ha='center', va='bottom',
            fontsize=8, color=TEXT_MUTED, style='italic')

    ax4.set_xlim(0, 1)
    ax4.set_ylim(0, 1)
    ax4.axis('off')
    ax4.set_title('Player Progression Profiles', fontsize=12, fontweight='bold',
                  color='white', pad=10)

    # ==========================================================================
    # Overall title and footer
    # ==========================================================================
    fig.suptitle(f'{team_name.upper()} - Ball Progression Profile',
                 fontsize=18, fontweight='bold', color='white', y=0.98)

    fig.text(0.5, 0.93, 'How the team advances the ball through passing and carrying',
             ha='center', fontsize=11, color=TEXT_SECONDARY)

    # Add footer
    add_cbs_footer(fig, 'TruMedia')

    # Adjust layout
    plt.tight_layout(rect=[0, 0.02, 1, 0.92])

    # Save
    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR,
                edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


def run(config):
    """Run ball progression chart from config dict.

    Config keys:
        file_path: Path to CSV file
        output_folder: Where to save output
        team_name: Team name for title
        min_minutes: Minimum minutes filter (default 200)
        max_players: Maximum players to show (default 15)
        league_file_path: Path to league-wide team data CSV (optional)
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder')
    team_name = config.get('team_name', 'Team')
    min_minutes = config.get('min_minutes', 200)
    max_players = config.get('max_players', 15)
    league_file_path = config.get('league_file_path')

    # Load data
    print("\nLoading player data...")
    df = load_data(file_path)
    print(f"  Loaded {len(df)} players")

    # Load league data if provided
    league_df = None
    if league_file_path and os.path.exists(league_file_path):
        print("\nLoading league comparison data...")
        league_df = pd.read_csv(league_file_path, encoding='utf-8')
        print(f"  Loaded {len(league_df)} teams for comparison")

    # Prepare data (for player-level charts)
    print("\nPreparing progression data...")
    prepared = prepare_progression_data(df, min_minutes, max_players)
    print(f"  Selected {len(prepared)} players (min {min_minutes} minutes)")

    # Also prepare full data with per-90 calculations for team aggregations
    full_prepared = prepare_progression_data(df, min_minutes=0, max_players=100)

    # Generate output filename
    safe_team = team_name.replace(' ', '_')[:20]
    filename = f"ball_progression_{safe_team}.png"
    output_path = os.path.join(output_folder, filename)

    # Create chart
    print("\nGenerating chart...")
    result = create_progression_chart(prepared, team_name, output_path,
                                       full_df=full_prepared, league_df=league_df)

    return result


def main():
    """Interactive CLI entry point."""
    print("\n" + "=" * 60)
    print("BALL PROGRESSION PROFILE CHART")
    print("=" * 60)
    print("4-panel visualization of how players progress the ball.")

    # Get file
    file_path = get_file_path("TruMedia Player Passing CSV file")
    if not file_path:
        return

    # Load to get team name
    df = pd.read_csv(file_path)

    # Try to auto-detect team name
    team_name = 'Team'
    for col in ['newestTeam', 'teamName', 'Team']:
        if col in df.columns:
            team_name = df[col].iloc[0]
            break

    print(f"\nDetected team: {team_name}")
    custom_team = input(f"Use this team name? (Enter to confirm, or type new name): ").strip()
    if custom_team:
        team_name = custom_team

    # Minimum minutes
    min_min = input("\nMinimum minutes to include (default=200): ").strip()
    min_minutes = int(min_min) if min_min.isdigit() else 200

    # Max players
    max_p = input("Maximum players to show (default=15): ").strip()
    max_players = int(max_p) if max_p.isdigit() else 15

    # Output folder
    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'team_name': team_name,
        'min_minutes': min_minutes,
        'max_players': max_players
    }

    result = run(config)

    if result:
        print("\n" + "=" * 60)
        print("COMPLETE")
        print("=" * 60)

        # Try to open the file
        try:
            os.startfile(result)
        except Exception as e:
            print(f"Could not open chart: {e}")


if __name__ == "__main__":
    main()
