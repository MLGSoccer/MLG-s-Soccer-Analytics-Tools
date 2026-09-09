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

    from shared.motherduck import registry_colour_by_name

    for team in team_names:
        # The registry outranks the CSV. An authored colour is a decision
        # someone made and sourced; the CSV column is whatever TruMedia sent,
        # which is the thing being corrected. Real Madrid arrives as blue.
        authored, _secondary = registry_colour_by_name(team)
        if authored:
            results[team] = authored
            continue

        if team in csv_colors and csv_colors[team]:
            results[team] = csv_colors[team]
            continue

        # Try fuzzy match
        color, matched_name, _ = fuzzy_match_team(team, TEAM_COLORS)
        if color:
            results[team] = color
        else:
            # Still None, not a fallback colour: the caller warns on this, and
            # a club we have simply never decided about should say so.
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


def own_goals_sidebar(home_team, away_team, auto_ogs, key_prefix, game_id=None):
    """Render the own-goals sidebar and return [{minute, team, period, player}].

    `team` is the BENEFITING side, which is what the chart builders expect,
    while both data sources name the CONCEDING one - so the conversion happens
    here, once, rather than in each page.

    Lifted out of the xG Race page after Match Momentum grew a second copy of
    it. The two had already drifted: the momentum copy was the one that
    carried `period` and `player` through an untouched edit, and losing either
    forces the chart back onto minute-based period inference and a bare "OG"
    label - a pair of defects this project has now fixed twice. One home for
    it, so a third page cannot inherit the old version.

    `game_id` resolves an own goal's `teamId` to a side. The CSV path has no
    game id, but it also passes an empty `auto_ogs`, so nothing needs it.
    """
    from shared.motherduck import own_goal_conceding_side

    st.sidebar.header("Own Goals")
    num_own_goals = st.sidebar.number_input(
        "Number of own goals", min_value=0, max_value=5,
        value=len(auto_ogs), key=f"num_og_{key_prefix}"
    )
    own_goals = []
    for i in range(num_own_goals):
        st.sidebar.markdown(f"**Own Goal {i+1}**")
        og_col1, og_col2 = st.sidebar.columns(2)
        if i < len(auto_ogs):
            default_minute = auto_ogs[i]["minute"]
            # "Scored by" means the own-goal scorer, i.e. the CONCEDING side -
            # which is what both sources name.
            _side = own_goal_conceding_side(
                game_id, auto_ogs[i].get("teamId"),
                auto_ogs[i].get("credited_team"), home_team, away_team)
            default_scorer_idx = 1 if _side == "away" else 0
        else:
            default_minute = 45
            default_scorer_idx = 0
        with og_col1:
            og_minute = st.number_input(
                "Minute", min_value=1, max_value=120,
                value=default_minute, key=f"og_min_{key_prefix}_{i}"
            )
        with og_col2:
            scoring_team = st.selectbox(
                "Scored by", options=[home_team, away_team],
                index=default_scorer_idx, key=f"og_team_{key_prefix}_{i}"
            )
        credited_team = away_team if scoring_team == home_team else home_team
        # Carry the data's period and player through an untouched edit. An
        # edited minute drops the period - it may no longer be true - but
        # keeps the player.
        og_period = og_player = None
        if i < len(auto_ogs):
            og_player = auto_ogs[i].get("player")
            if og_minute == default_minute:
                og_period = auto_ogs[i].get("period")
        own_goals.append({"minute": og_minute, "team": credited_team,
                          "period": og_period, "player": og_player})
        st.sidebar.caption(f"Goal credited to {credited_team}")
    return own_goals
