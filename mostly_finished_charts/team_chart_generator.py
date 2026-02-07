"""
Team Chart Generator
Flexible tool for creating scatter plots and bar charts from CSV data.
Auto-detects team information and applies CBS Sports styling.
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
import sys
from adjustText import adjust_text

# Import shared utilities
from shared.styles import (
    BG_COLOR, SPINE_COLOR, GRID_COLOR, CBS_BLUE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    style_axis, style_axis_full_grid, add_cbs_footer
)
from shared.colors import (
    get_team_color, ensure_contrast_with_background
)
from shared.file_utils import get_file_path, get_output_folder


# =============================================================================
# DATA LOADING
# =============================================================================
def load_csv_data(file_path):
    """Load CSV and detect team-related columns."""
    df = pd.read_csv(file_path)

    # Detect team columns
    team_info = {
        'name_col': None,
        'abbrev_col': None,
        'color_col': None,
    }

    # Common team name column names
    name_candidates = ['Team', 'team', 'teamName', 'teamFullName', 'Club', 'club']
    for col in name_candidates:
        if col in df.columns:
            team_info['name_col'] = col
            break

    # Common abbreviation column names
    abbrev_candidates = ['teamAbbrevName', 'Abbrev', 'abbrev', 'teamAbbrev', 'Short', 'shortName']
    for col in abbrev_candidates:
        if col in df.columns:
            team_info['abbrev_col'] = col
            break

    # Common color column names
    color_candidates = ['newestTeamColor', 'teamColor', 'color', 'Color']
    for col in color_candidates:
        if col in df.columns:
            team_info['color_col'] = col
            break

    return df, team_info


def get_numeric_columns(df, exclude_cols=None):
    """Get list of numeric columns suitable for chart axes."""
    if exclude_cols is None:
        exclude_cols = []

    # Common columns to exclude (IDs, ranks, etc.)
    default_exclude = [
        'Rank', 'rank', 'teamId', 'teamImageId', 'leagueId', 'optaTeamId',
        'playerId', 'playerImageId', 'id', 'ID'
    ]
    exclude_set = set(exclude_cols + default_exclude)

    numeric_cols = []
    for col in df.columns:
        if col in exclude_set:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)

    return numeric_cols


def get_team_colors_from_df(df, team_info):
    """Extract team colors from dataframe."""
    colors = {}
    name_col = team_info['name_col']
    color_col = team_info['color_col']

    if not name_col:
        return colors

    for _, row in df.iterrows():
        team = row[name_col]

        # Try to get color from CSV
        if color_col and pd.notna(row.get(color_col)):
            color = row[color_col]
        else:
            # Fallback to database
            color = get_team_color(team, prompt_if_missing=False)

        # Ensure contrast with dark background
        color = ensure_contrast_with_background(color)
        colors[team] = color

    return colors


# =============================================================================
# CHART CREATION
# =============================================================================
def create_scatter_chart(df, team_info, x_col, y_col, title, x_label, y_label, output_path):
    """Create a scatter plot with team labels and average lines."""

    # Get team colors
    team_colors = get_team_colors_from_df(df, team_info)
    name_col = team_info['name_col']
    abbrev_col = team_info['abbrev_col']

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 10))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Title
    fig.suptitle(title, fontsize=18, fontweight='bold', color=TEXT_PRIMARY, y=0.95)

    # Plot points and collect text labels
    texts = []
    for _, row in df.iterrows():
        team = row[name_col] if name_col else f"Team {_}"
        color = team_colors.get(team, '#888888')

        ax.scatter(row[x_col], row[y_col],
                   c=color, s=150, alpha=0.9, edgecolors='white', linewidth=0.5)

        # Get label (abbreviation or first 3 chars of name)
        if abbrev_col and pd.notna(row.get(abbrev_col)):
            label = row[abbrev_col]
        elif name_col:
            label = row[name_col][:3].upper()
        else:
            label = ''

        if label:
            txt = ax.text(row[x_col], row[y_col], label,
                         fontsize=9, color=TEXT_PRIMARY, fontweight='bold')
            texts.append(txt)

    # Average lines
    avg_x = df[x_col].mean()
    avg_y = df[y_col].mean()
    ax.axvline(avg_x, color=SPINE_COLOR, linestyle='--', alpha=0.6, linewidth=1)
    ax.axhline(avg_y, color=SPINE_COLOR, linestyle='--', alpha=0.6, linewidth=1)

    # Add average value annotations
    ax.text(avg_x, ax.get_ylim()[1], f'Avg: {avg_x:.2f}',
            fontsize=8, color=TEXT_MUTED, ha='center', va='bottom')
    ax.text(ax.get_xlim()[1], avg_y, f'Avg: {avg_y:.2f}',
            fontsize=8, color=TEXT_MUTED, ha='right', va='center')

    # Labels
    ax.set_xlabel(x_label, color=TEXT_PRIMARY, fontsize=12)
    ax.set_ylabel(y_label, color=TEXT_PRIMARY, fontsize=12)

    # Style
    style_axis_full_grid(ax)

    # Adjust text to avoid overlaps
    if texts:
        adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))

    # Footer
    plt.tight_layout(rect=[0, 0.03, 1, 0.93])
    add_cbs_footer(fig, data_source='Opta/STATS Perform')

    # Save
    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close(fig)

    return output_path


def create_horizontal_bar_chart(df, team_info, value_col, title, x_label, output_path, sort_ascending=True):
    """Create a horizontal bar chart sorted by value."""

    # Get team colors
    team_colors = get_team_colors_from_df(df, team_info)
    name_col = team_info['name_col']
    abbrev_col = team_info['abbrev_col']

    # Sort data
    df_sorted = df.sort_values(value_col, ascending=sort_ascending)

    # Create figure - height based on number of teams
    fig_height = max(8, len(df) * 0.4)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Title
    fig.suptitle(title, fontsize=18, fontweight='bold', color=TEXT_PRIMARY, y=0.98)

    # Get labels and colors
    y_pos = range(len(df_sorted))

    if abbrev_col and abbrev_col in df_sorted.columns:
        labels = df_sorted[abbrev_col].tolist()
    elif name_col:
        labels = df_sorted[name_col].tolist()
    else:
        labels = [f"Team {i+1}" for i in range(len(df_sorted))]

    colors = [team_colors.get(row[name_col], '#888888') if name_col else '#888888'
              for _, row in df_sorted.iterrows()]

    # Create bars
    bars = ax.barh(y_pos, df_sorted[value_col], color=colors, alpha=0.9)

    # Labels on y-axis
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)

    # Value labels at end of bars
    for bar, val in zip(bars, df_sorted[value_col]):
        # Format value
        if abs(val) < 1 and val != 0:
            val_str = f'{val:.2f}'
        elif abs(val) < 10:
            val_str = f'{val:.1f}'
        else:
            val_str = f'{val:.0f}'

        ax.text(bar.get_width() + (df_sorted[value_col].max() * 0.01),
                bar.get_y() + bar.get_height()/2,
                val_str, va='center', fontsize=9, color=TEXT_PRIMARY)

    # Average line
    avg_val = df[value_col].mean()
    ax.axvline(avg_val, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax.text(avg_val, len(df) - 0.5, f'Avg: {avg_val:.2f}',
            fontsize=9, color=TEXT_MUTED, ha='center')

    # Labels
    ax.set_xlabel(x_label, color=TEXT_PRIMARY, fontsize=12)

    # Style
    style_axis(ax)
    ax.set_xlim(0, df_sorted[value_col].max() * 1.12)

    # Footer
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    add_cbs_footer(fig, data_source='Opta/STATS Perform')

    # Save
    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close(fig)

    return output_path


def create_vertical_bar_chart(df, team_info, value_col, title, y_label, output_path, sort_ascending=False):
    """Create a vertical bar chart sorted by value."""

    # Get team colors
    team_colors = get_team_colors_from_df(df, team_info)
    name_col = team_info['name_col']
    abbrev_col = team_info['abbrev_col']

    # Sort data (descending by default for vertical - highest on left)
    df_sorted = df.sort_values(value_col, ascending=sort_ascending)

    # Create figure - width based on number of teams
    fig_width = max(12, len(df) * 0.5)
    fig, ax = plt.subplots(figsize=(fig_width, 8))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Title
    fig.suptitle(title, fontsize=18, fontweight='bold', color=TEXT_PRIMARY, y=0.95)

    # Get labels and colors
    x_pos = range(len(df_sorted))

    if abbrev_col and abbrev_col in df_sorted.columns:
        labels = df_sorted[abbrev_col].tolist()
    elif name_col:
        labels = [name[:3].upper() for name in df_sorted[name_col].tolist()]
    else:
        labels = [f"T{i+1}" for i in range(len(df_sorted))]

    colors = [team_colors.get(row[name_col], '#888888') if name_col else '#888888'
              for _, row in df_sorted.iterrows()]

    # Create bars
    bars = ax.bar(x_pos, df_sorted[value_col], color=colors, alpha=0.9)

    # Labels on x-axis
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=9, rotation=45 if len(df) > 15 else 0, ha='right' if len(df) > 15 else 'center')

    # Value labels on top of bars
    for bar, val in zip(bars, df_sorted[value_col]):
        # Format value
        if abs(val) < 1 and val != 0:
            val_str = f'{val:.2f}'
        elif abs(val) < 10:
            val_str = f'{val:.1f}'
        else:
            val_str = f'{val:.0f}'

        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + (df_sorted[value_col].max() * 0.01),
                val_str, ha='center', va='bottom', fontsize=8, color=TEXT_PRIMARY)

    # Average line
    avg_val = df[value_col].mean()
    ax.axhline(avg_val, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax.text(len(df) - 0.5, avg_val, f'Avg: {avg_val:.2f}',
            fontsize=9, color=TEXT_MUTED, va='center')

    # Labels
    ax.set_ylabel(y_label, color=TEXT_PRIMARY, fontsize=12)

    # Style
    style_axis(ax)
    ax.set_ylim(0, df_sorted[value_col].max() * 1.12)

    # Footer
    plt.tight_layout(rect=[0, 0.02, 1, 0.93])
    add_cbs_footer(fig, data_source='Opta/STATS Perform')

    # Save
    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close(fig)

    return output_path


# =============================================================================
# USER INTERACTION
# =============================================================================
def prompt_with_default(prompt_text, default_value):
    """Prompt user with a default value."""
    user_input = input(f"{prompt_text} [{default_value}]: ").strip()
    return user_input if user_input else default_value


def select_from_list(items, prompt_text, allow_multiple=False):
    """Display a list and let user select item(s)."""
    print(f"\n{prompt_text}")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")

    while True:
        selection = input("Enter number: ").strip()
        try:
            idx = int(selection) - 1
            if 0 <= idx < len(items):
                return items[idx]
        except ValueError:
            pass
        print("Invalid selection. Please enter a valid number.")


def get_chart_type():
    """Prompt user to select chart type."""
    print("\nSelect chart type:")
    print("  1. Scatter Plot (requires X and Y columns)")
    print("  2. Horizontal Bar Chart (single value column)")
    print("  3. Vertical Bar Chart (single value column)")

    while True:
        choice = input("Enter number (1-3): ").strip()
        if choice == '1':
            return 'scatter'
        elif choice == '2':
            return 'horizontal_bar'
        elif choice == '3':
            return 'vertical_bar'
        print("Invalid choice. Please enter 1, 2, or 3.")


# =============================================================================
# MAIN FUNCTIONS
# =============================================================================
def _get_downloads_folder():
    """Get the user's Downloads folder path."""
    if sys.platform == 'win32':
        return os.path.join(os.path.expanduser('~'), 'Downloads')
    elif sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Downloads')
    else:
        return os.path.join(os.path.expanduser('~'), 'Downloads')


def run(config):
    """Run function for launcher/GUI integration."""
    file_path = config.get('file_path')
    output_folder = config.get('output_folder') or _get_downloads_folder()
    chart_type = config.get('chart_type', 'scatter')
    x_col = config.get('x_col')
    y_col = config.get('y_col')
    value_col = config.get('value_col')
    title = config.get('title', 'Team Chart')
    x_label = config.get('x_label', x_col or value_col)
    y_label = config.get('y_label', y_col or value_col)

    # Load data
    df, team_info = load_csv_data(file_path)

    # Generate filename
    safe_title = title.replace(' ', '_').replace(':', '').replace('/', '-')[:50]
    output_path = os.path.join(output_folder, f"team_chart_{safe_title}.png")

    # Create chart based on type
    if chart_type == 'scatter':
        return create_scatter_chart(df, team_info, x_col, y_col, title, x_label, y_label, output_path)
    elif chart_type == 'horizontal_bar':
        return create_horizontal_bar_chart(df, team_info, value_col, title, x_label, output_path)
    elif chart_type == 'vertical_bar':
        return create_vertical_bar_chart(df, team_info, value_col, title, y_label, output_path)

    return None


def main():
    """Main entry point for standalone usage."""
    print("\n" + "=" * 60)
    print("TEAM CHART GENERATOR")
    print("=" * 60)
    print("Create scatter plots or bar charts from team data.")

    # Get CSV file
    file_path = get_file_path("Team data CSV file")
    if not file_path:
        return

    # Load data
    print("\nLoading data...")
    df, team_info = load_csv_data(file_path)
    print(f"  Loaded {len(df)} rows")

    # Show detected team columns
    print("\n" + "-" * 40)
    print("DETECTED TEAM COLUMNS:")
    print(f"  Team name: {team_info['name_col'] or 'Not found'}")
    print(f"  Abbreviation: {team_info['abbrev_col'] or 'Not found'}")
    print(f"  Team color: {team_info['color_col'] or 'Not found'}")

    # Get numeric columns
    numeric_cols = get_numeric_columns(df)
    print(f"\n  Found {len(numeric_cols)} numeric columns available for charting")

    # Select chart type
    chart_type = get_chart_type()

    # Select columns based on chart type
    print("\n" + "-" * 40)
    print("AVAILABLE COLUMNS:")

    if chart_type == 'scatter':
        x_col = select_from_list(numeric_cols, "Select X-axis column:")
        y_col = select_from_list(numeric_cols, "Select Y-axis column:")
        value_col = None

        # Suggest labels
        default_x_label = x_col.replace('_', ' ').replace('pg', 'per Game')
        default_y_label = y_col.replace('_', ' ').replace('pg', 'per Game')
        default_title = f"{y_col} vs {x_col}"

        x_label = prompt_with_default("X-axis label", default_x_label)
        y_label = prompt_with_default("Y-axis label", default_y_label)

    else:
        value_col = select_from_list(numeric_cols, "Select value column:")
        x_col = None
        y_col = None

        # Suggest labels
        default_label = value_col.replace('_', ' ').replace('pg', 'per Game')
        default_title = f"{value_col} by Team"

        if chart_type == 'horizontal_bar':
            x_label = prompt_with_default("X-axis label (value)", default_label)
            y_label = None
        else:
            y_label = prompt_with_default("Y-axis label (value)", default_label)
            x_label = None

    # Chart title
    title = prompt_with_default("Chart title", default_title)

    # Output folder
    output_folder = get_output_folder()

    # Generate filename
    safe_title = title.replace(' ', '_').replace(':', '').replace('/', '-')[:50]
    output_path = os.path.join(output_folder, f"team_chart_{safe_title}.png")

    # Create chart
    print("\n" + "-" * 40)
    print("GENERATING CHART...")

    if chart_type == 'scatter':
        create_scatter_chart(df, team_info, x_col, y_col, title, x_label, y_label, output_path)
    elif chart_type == 'horizontal_bar':
        create_horizontal_bar_chart(df, team_info, value_col, title, x_label, output_path)
    elif chart_type == 'vertical_bar':
        create_vertical_bar_chart(df, team_info, value_col, title, y_label, output_path)

    # Open the chart
    print(f"\nOpening chart...")
    try:
        os.startfile(output_path)
    except Exception as e:
        print(f"Could not open chart: {e}")

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
