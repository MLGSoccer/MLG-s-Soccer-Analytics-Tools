"""
Shared utilities for Streamlit pages
"""
import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.colors import TEAM_COLORS, fuzzy_match_team


def check_team_colors(team_names, csv_colors=None):
    """Pre-check team colors and warn about any that can't be resolved.

    Args:
        team_names: List of team names to check
        csv_colors: Optional dict of {team_name: color} from CSV

    Returns:
        dict of {team_name: color or None}
    """
    if csv_colors is None:
        csv_colors = {}

    results = {}
    missing = []

    for team in team_names:
        # Check CSV first
        if team in csv_colors and csv_colors[team]:
            results[team] = csv_colors[team]
            continue

        # Try fuzzy match
        color, matched_name, _ = fuzzy_match_team(team, TEAM_COLORS)
        if color:
            results[team] = color
        else:
            results[team] = None
            missing.append(team)

    # Show warning for missing colors
    if missing:
        st.warning(
            f"**Color not found for:** {', '.join(missing)}\n\n"
            "Default gray will be used. Add colors to the CSV's `newestTeamColor` column or update the color database."
        )

    return results


def show_color_status(team_names, csv_colors=None):
    """Show color resolution status for teams.

    Args:
        team_names: List of team names to check
        csv_colors: Optional dict of {team_name: color} from CSV
    """
    if csv_colors is None:
        csv_colors = {}

    missing = []
    resolved = []

    for team in team_names:
        # Check CSV first
        if team in csv_colors and csv_colors[team]:
            resolved.append((team, csv_colors[team], "CSV"))
            continue

        # Try fuzzy match
        color, matched_name, _ = fuzzy_match_team(team, TEAM_COLORS)
        if color:
            source = f"Database ({matched_name})" if matched_name != team else "Database"
            resolved.append((team, color, source))
        else:
            missing.append(team)

    # Show warning for missing
    if missing:
        st.warning(
            f"**Color not found for:** {', '.join(missing)}\n\n"
            "Default gray will be used."
        )
