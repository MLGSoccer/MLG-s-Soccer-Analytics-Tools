"""
Soccer Chart Launcher
Unified menu to generate soccer analytics charts.
"""
import os
import sys

# Import shared utilities
from shared.file_utils import get_file_path, get_output_folder

# Import run() functions from chart modules
from mostly_finished_charts import (
    run_team_rolling,
    run_player_rolling,
    run_sequence,
    run_xg_race,
    run_setpiece_report,
    run_player_bar
)
import mostly_finished_charts.player_comparison_chart as player_comparison_chart
import mostly_finished_charts.team_chart_generator as team_chart_generator
import mostly_finished_charts.player_bar_chart as player_bar_chart


def display_menu():
    """Display the main menu and return user choice."""
    print("\n" + "=" * 60)
    print("        SOCCER CHART GENERATOR")
    print("=" * 60)
    print()
    print("  1. Team Rolling xG Analysis")
    print("     - Rolling xG for/against over a season")
    print("     - Requires: TruMedia match summary or event log CSV")
    print()
    print("  2. Player Rolling xG Analysis")
    print("     - Rolling xG/goals/shots for an individual player")
    print("     - Requires: TruMedia player summary CSV")
    print()
    print("  3. xG Race Chart (Single Match)")
    print("     - Cumulative xG timeline for a single match")
    print("     - Requires: TruMedia event log CSV")
    print()
    print("  4. Sequence Analysis")
    print("     - How possessions build toward shots")
    print("     - Requires: TruMedia event log CSV with sequence data")
    print()
    print("  5. Player Comparison")
    print("     - Compare a player vs position peers (percentile rankings)")
    print("     - Requires: TruMedia player stats CSV (last 365 days)")
    print()
    print("  6. Set Piece Report")
    print("     - League-wide set piece attacking/defensive analysis")
    print("     - Requires: TruMedia Set Piece Report CSV")
    print()
    print("  7. Team Chart Generator")
    print("     - Create custom scatter/bar charts from any team CSV")
    print("     - Requires: Any CSV with team data and numeric columns")
    print()
    print("  8. Player Bar Chart")
    print("     - Compare multiple players on a single stat")
    print("     - Modes: individual players, team roster, or league leaderboard")
    print("     - Requires: TruMedia player stats CSV")
    print()
    print("  9. Exit")
    print()
    print("-" * 60)

    while True:
        choice = input("Select chart type (1-9): ").strip()
        if choice in ['1', '2', '3', '4', '5', '6', '7', '8', '9']:
            return choice
        print("Invalid choice. Please enter 1-9.")


def prompt_rolling_window():
    """Prompt user for rolling window size."""
    window_input = input("\nRolling window size (default=10): ").strip()
    return int(window_input) if window_input.isdigit() else 10


def run_team_rolling_chart():
    """Collect inputs and run team rolling xG chart."""
    print("\n" + "-" * 60)
    print("TEAM ROLLING xG ANALYSIS")
    print("-" * 60)
    print("Analyzes team xG performance over a season.")

    file_path = get_file_path("TruMedia CSV file")
    if not file_path:
        return

    window = prompt_rolling_window()
    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'window': window
    }

    try:
        run_team_rolling(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_player_rolling_chart():
    """Collect inputs and run player rolling xG chart."""
    print("\n" + "-" * 60)
    print("PLAYER ROLLING xG ANALYSIS")
    print("-" * 60)
    print("Analyzes individual player performance over a season.")

    file_path = get_file_path("TruMedia Player Summary CSV file")
    if not file_path:
        return

    window = prompt_rolling_window()
    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'window': window
    }

    try:
        run_player_rolling(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_xg_race_chart():
    """Collect inputs and run xG race chart."""
    print("\n" + "-" * 60)
    print("xG RACE CHART")
    print("-" * 60)
    print("Creates a single-match xG timeline chart.")

    file_path = get_file_path("TruMedia Event Log CSV file")
    if not file_path:
        return

    output_folder = get_output_folder()

    config = {
        'data_source': 'trumedia',
        'file_path': file_path,
        'output_folder': output_folder,
        'save': True
    }

    try:
        run_xg_race(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_sequence_chart():
    """Collect inputs and run sequence analysis chart."""
    print("\n" + "-" * 60)
    print("SEQUENCE ANALYSIS")
    print("-" * 60)
    print("Analyzes how possessions build toward shots.")

    file_path = get_file_path("TruMedia Event Log CSV file")
    if not file_path:
        return

    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder
    }

    try:
        run_sequence(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_player_comparison_chart():
    """Collect inputs and run player comparison chart."""
    print("\n" + "-" * 60)
    print("PLAYER COMPARISON")
    print("-" * 60)
    print("Compare a player against position peers using percentile rankings.")

    file_path = get_file_path("TruMedia Player Stats CSV file")
    if not file_path:
        return

    # Load data first to show position counts
    print("\nLoading player data...")
    df = player_comparison_chart.load_player_data(file_path)
    print(f"  Loaded {len(df)} players")

    # Show available positions
    print("\n" + "-" * 40)
    print("POSITION CATEGORIES:")
    for i, pos in enumerate(player_comparison_chart.POSITION_CATEGORIES, 1):
        count = len(df[df['PositionCategory'] == pos])
        print(f"  {i}. {pos} ({count} players)")

    # Get player name
    print("\n" + "-" * 40)
    player_name = input("Enter player name to analyze: ").strip()

    if not player_name:
        print("No player name entered.")
        return

    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'player_name': player_name,
        'min_minutes': 900
    }

    try:
        player_comparison_chart.run(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_setpiece_report_chart():
    """Collect inputs and run set piece report chart."""
    print("\n" + "-" * 60)
    print("SET PIECE REPORT")
    print("-" * 60)
    print("League-wide set piece attacking and defensive analysis.")

    file_path = get_file_path("TruMedia Set Piece Report CSV file")
    if not file_path:
        return

    # Ask which report to generate
    print("\nWhich report would you like to generate?")
    print("  1. Attacking (set piece xG created)")
    print("  2. Defensive (set piece xG conceded)")
    print("  3. Both")

    while True:
        choice = input("Select (1-3, default=3): ").strip()
        if choice == '' or choice == '3':
            report_type = 'both'
            break
        elif choice == '1':
            report_type = 'attacking'
            break
        elif choice == '2':
            report_type = 'defensive'
            break
        print("Invalid choice. Please enter 1, 2, or 3.")

    output_folder = get_output_folder()

    config = {
        'file_path': file_path,
        'output_folder': output_folder,
        'report_type': report_type
    }

    try:
        run_setpiece_report(config)
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_team_chart_generator():
    """Run the team chart generator (handles its own prompts)."""
    try:
        team_chart_generator.main()
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def run_player_bar_chart_menu():
    """Run the player bar chart (handles its own prompts)."""
    try:
        player_bar_chart.main()
    except Exception as e:
        print(f"\n[ERROR] Chart generation failed: {e}")


def main():
    """Main launcher loop."""
    print("\n" + "=" * 60)
    print("   Welcome to MLG's CBS Sports Soccer Chart Generator")
    print("=" * 60)

    while True:
        choice = display_menu()

        if choice == '1':
            run_team_rolling_chart()
        elif choice == '2':
            run_player_rolling_chart()
        elif choice == '3':
            run_xg_race_chart()
        elif choice == '4':
            run_sequence_chart()
        elif choice == '5':
            run_player_comparison_chart()
        elif choice == '6':
            run_setpiece_report_chart()
        elif choice == '7':
            run_team_chart_generator()
        elif choice == '8':
            run_player_bar_chart_menu()
        elif choice == '9':
            print("\nGoodbye!")
            sys.exit(0)

        # After generating a chart, prompt to continue
        input("\nPress Enter to return to the menu...")


if __name__ == "__main__":
    main()
