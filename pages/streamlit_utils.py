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


def custom_title_inputs(key_prefix="", default_title="", default_subtitle=""):
    """Add optional custom title/subtitle inputs to sidebar.

    Pre-populated with default_title/default_subtitle when provided.
    Uses a hash-based key so the widget resets when defaults change
    (e.g. a new game is selected), while preserving edits within a selection.

    Returns (custom_title, custom_subtitle) — each is None when cleared to blank,
    so callers can do: title = custom_title or auto_title
    """
    import hashlib
    h = hashlib.md5(f"{default_title}|{default_subtitle}".encode()).hexdigest()[:8]
    with st.sidebar.expander("Custom Title (optional)", expanded=False):
        custom_title = st.text_input(
            "Title", value=default_title, key=f"{key_prefix}_ctitle_{h}",
            placeholder="Leave blank for auto-generated"
        )
        custom_subtitle = st.text_input(
            "Subtitle", value=default_subtitle, key=f"{key_prefix}_csubtitle_{h}",
            placeholder="Leave blank for auto-generated"
        )
    # Only override when the user has actually changed the value from the default.
    # If it matches the default (or is blank), return None so the chart's own
    # auto-generated title/subtitle is used (which may include extra info like xG totals).
    out_title = custom_title if (custom_title and custom_title != default_title) else None
    out_subtitle = custom_subtitle if (custom_subtitle and custom_subtitle != default_subtitle) else None
    return out_title, out_subtitle


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
