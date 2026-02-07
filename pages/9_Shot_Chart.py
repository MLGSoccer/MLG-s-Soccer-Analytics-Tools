"""
Shot Chart - Streamlit Page
Supports single-match and multi-match (season) CSV files.
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.shot_chart import (
    load_shot_data,
    load_multi_match_shot_data,
    detect_csv_mode,
    create_team_shot_chart,
    create_combined_shot_chart,
    create_multi_match_shot_chart,
    SHOT_TYPES,
    GOAL_TYPES,
    ensure_pitch_contrast
)
from shared.styles import BG_COLOR
import matplotlib.pyplot as plt

st.set_page_config(page_title="Shot Chart", page_icon="🎯", layout="wide")

st.title("Shot Chart")
st.markdown("Visualize shot locations for a single match or full season.")

# Sidebar controls
st.sidebar.header("Settings")
competition = st.sidebar.text_input(
    "Competition Name",
    value="",
    help="e.g., Premier League, Serie A (optional)"
)
if competition:
    st.sidebar.success(f"Competition: {competition}")

exclude_penalties = st.sidebar.checkbox(
    "Exclude Penalties",
    value=False,
    help="Filter out penalty shots from the chart"
)

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Single-match or season-long event log with shot data"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        # Read CSV once for mode detection
        raw_df = pd.read_csv(tmp_path)
        detected_mode = detect_csv_mode(raw_df)

        # Mode toggle
        mode = st.radio(
            "Chart Mode",
            options=["Single Match", "Multi-Match (Season)"],
            index=0 if detected_mode == 'single' else 1,
            horizontal=True,
            help="Auto-detected based on your CSV. Override if needed."
        )

        if mode == "Single Match":
            # ── SINGLE MATCH MODE ──────────────────────────────────────
            # Chart selection
            st.sidebar.header("Chart Output")
            chart_options = st.sidebar.multiselect(
                "Charts to generate",
                options=["Individual Team Charts", "Combined Chart"],
                default=["Individual Team Charts", "Combined Chart"],
                help="Select which chart types to generate"
            )

            with st.spinner("Parsing match data..."):
                shots_df, match_info, team_colors = load_shot_data(tmp_path, exclude_penalties=exclude_penalties)

            if shots_df.empty:
                st.error("No shot data found in CSV.")
            else:
                teams = shots_df['Team'].unique().tolist()
                home_team = match_info.get('home_team', teams[0] if teams else 'Home')
                away_team = match_info.get('away_team', teams[1] if len(teams) > 1 else 'Away')

                # Match team names to shot data
                def match_team_name(target, team_list):
                    for t in team_list:
                        if target.lower() in t.lower() or t.lower() in target.lower():
                            return t
                    return team_list[0] if team_list else target

                team1_name = match_team_name(home_team, teams)
                team2_name = match_team_name(away_team, [t for t in teams if t != team1_name])

                st.success(f"Found {len(shots_df)} shots: **{team1_name}** vs **{team2_name}**")

                # Show match info
                col1, col2, col3 = st.columns(3)
                col1.metric("Date", match_info.get('date', 'Unknown'))
                col2.metric(team1_name, f"{match_info.get('home_score', 0)} goals")
                col3.metric(team2_name, f"{match_info.get('away_score', 0)} goals")

                # Pre-check team colors
                from pages.streamlit_utils import check_team_colors
                check_team_colors([team1_name, team2_name], team_colors)

                if st.button("Generate Charts", type="primary"):
                    with st.spinner("Generating shot charts..."):
                        # Resolve colors
                        from shared.colors import TEAM_COLORS, fuzzy_match_team

                        def resolve_color(team_name, team_colors_dict):
                            if team_name in team_colors_dict:
                                return team_colors_dict[team_name]
                            for csv_team, color in team_colors_dict.items():
                                if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                                    return color
                            color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
                            return color if color else '#888888'

                        team1_color = ensure_pitch_contrast(resolve_color(team1_name, team_colors))
                        team2_color = ensure_pitch_contrast(resolve_color(team2_name, team_colors))

                        # Get team shots
                        team1_shots = shots_df[shots_df['Team'] == team1_name]
                        team2_shots = shots_df[shots_df['Team'] == team2_name]

                        # Determine flip based on shot positions
                        team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
                        team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50
                        team1_flip = team1_avg_x < 50
                        team2_flip = team2_avg_x < 50

                        # Get scores
                        team1_final_score = match_info.get('home_score', 0)
                        team2_final_score = match_info.get('away_score', 0)

                        # Calculate own goals
                        team1_shot_goals = len(team1_shots[team1_shots['playType'].isin(GOAL_TYPES)])
                        team2_shot_goals = len(team2_shots[team2_shots['playType'].isin(GOAL_TYPES)])
                        team1_own_goals = max(0, team1_final_score - team1_shot_goals)
                        team2_own_goals = max(0, team2_final_score - team2_shot_goals)

                        charts_generated = []

                        with tempfile.TemporaryDirectory() as tmp_dir:
                            # Individual team charts
                            if "Individual Team Charts" in chart_options:
                                # Team 1 chart
                                fig1 = create_team_shot_chart(
                                    team1_shots, team1_name, team1_color, match_info,
                                    team2_name, team_final_score=team1_final_score,
                                    opponent_goals=team2_final_score,
                                    own_goals_for=team1_own_goals,
                                    flip_coords=team1_flip, competition=competition,
                                    exclude_penalties=exclude_penalties
                                )
                                path1 = os.path.join(tmp_dir, f"shot_chart_{team1_name.replace(' ', '_')}.png")
                                fig1.savefig(path1, dpi=300, bbox_inches='tight',
                                            facecolor=BG_COLOR, edgecolor='none')
                                plt.close(fig1)
                                charts_generated.append((path1, f"{team1_name} Shot Chart", f"shot_chart_{team1_name.replace(' ', '_')}.png"))

                                # Team 2 chart
                                fig2 = create_team_shot_chart(
                                    team2_shots, team2_name, team2_color, match_info,
                                    team1_name, team_final_score=team2_final_score,
                                    opponent_goals=team1_final_score,
                                    own_goals_for=team2_own_goals,
                                    flip_coords=team2_flip, competition=competition,
                                    exclude_penalties=exclude_penalties
                                )
                                path2 = os.path.join(tmp_dir, f"shot_chart_{team2_name.replace(' ', '_')}.png")
                                fig2.savefig(path2, dpi=300, bbox_inches='tight',
                                            facecolor=BG_COLOR, edgecolor='none')
                                plt.close(fig2)
                                charts_generated.append((path2, f"{team2_name} Shot Chart", f"shot_chart_{team2_name.replace(' ', '_')}.png"))

                            # Combined chart
                            if "Combined Chart" in chart_options:
                                fig_combined = create_combined_shot_chart(
                                    shots_df, team1_name, team1_color, team1_flip,
                                    team2_name, team2_color, team2_flip,
                                    match_info, competition=competition,
                                    exclude_penalties=exclude_penalties
                                )
                                path_combined = os.path.join(tmp_dir, f"shot_chart_combined.png")
                                fig_combined.savefig(path_combined, dpi=300, bbox_inches='tight',
                                                    facecolor=BG_COLOR, edgecolor='none')
                                plt.close(fig_combined)
                                charts_generated.append((path_combined, "Combined Shot Chart", f"shot_chart_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"))

                            # Display charts
                            for path, caption, filename in charts_generated:
                                st.image(path, caption=caption)
                                with open(path, "rb") as f:
                                    st.download_button(
                                        label=f"Download {caption}",
                                        data=f.read(),
                                        file_name=filename,
                                        mime="image/png",
                                        key=filename
                                    )
                                st.markdown("---")

                        st.success(f"Generated {len(charts_generated)} chart(s)!")

        else:
            # ── MULTI-MATCH MODE ───────────────────────────────────────
            with st.spinner("Parsing season data..."):
                shots_df, multi_match_info, team_color_raw = load_multi_match_shot_data(tmp_path, exclude_penalties=exclude_penalties)

            if shots_df.empty:
                st.error("No shot data found in CSV.")
            else:
                team_name = multi_match_info['team_name']
                total_matches = multi_match_info['total_matches']
                player_list = multi_match_info['player_list']
                date_range = multi_match_info.get('date_range', '')

                # Resolve color: database first (brand color), CSV fallback (kit color)
                from shared.colors import TEAM_COLORS, fuzzy_match_team
                color_db, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
                if color_db:
                    team_color = ensure_pitch_contrast(color_db)
                elif team_color_raw and team_color_raw != '#888888':
                    team_color = ensure_pitch_contrast(team_color_raw)
                else:
                    team_color = '#888888'

                # Pre-check team colors
                from pages.streamlit_utils import check_team_colors
                check_team_colors([team_name], {team_name: team_color_raw} if team_color_raw != '#888888' else {})

                # Detect player vs team CSV
                is_player_csv = multi_match_info.get('is_player_csv', False)
                auto_player = multi_match_info.get('player_name')

                if is_player_csv:
                    st.success(f"**{auto_player}** ({team_name}) - {len(shots_df)} shots across {total_matches} matches")
                else:
                    st.success(f"**{team_name}** - {len(shots_df)} shots across {total_matches} matches")

                # Summary metrics
                total_xg = shots_df['xG'].sum()
                total_goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
                shots_per_game = len(shots_df) / total_matches if total_matches > 0 else 0

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Matches", total_matches)
                col2.metric("Shots", len(shots_df))
                col3.metric("xG", f"{total_xg:.1f}")
                col4.metric("Goals", total_goals)

                if date_range:
                    st.caption(f"Date range: {date_range}")

                # Player filter dropdown - only show for team CSVs
                if is_player_csv:
                    selected_player = auto_player
                else:
                    player_filter = st.selectbox(
                        "Filter by Player",
                        options=["All Players"] + player_list,
                        index=0,
                        help="Select a specific player or view all shots"
                    )
                    selected_player = None if player_filter == "All Players" else player_filter

                    # Show filtered stats if player selected
                    if selected_player:
                        shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
                        player_shots = shots_df[shots_df[shooter_col] == selected_player]
                        p_matches = player_shots['_match_id'].nunique()
                        p_xg = player_shots['xG'].sum()
                        p_goals = len(player_shots[player_shots['playType'].isin(GOAL_TYPES)])

                        pcol1, pcol2, pcol3, pcol4 = st.columns(4)
                        pcol1.metric(f"{selected_player} Matches", p_matches)
                        pcol2.metric("Shots", len(player_shots))
                        pcol3.metric("xG", f"{p_xg:.1f}")
                        pcol4.metric("Goals", p_goals)

                if st.button("Generate Shot Map", type="primary"):
                    with st.spinner("Generating shot map..."):
                        # Filter to player if needed (team CSV with player selected)
                        chart_shots = shots_df.copy()
                        chart_info = multi_match_info.copy()

                        if selected_player and not is_player_csv:
                            shooter_col = 'shooter' if 'shooter' in chart_shots.columns else 'Player'
                            chart_shots = chart_shots[chart_shots[shooter_col] == selected_player].copy()
                            chart_info['total_matches'] = chart_shots['_match_id'].nunique()

                        if chart_shots.empty:
                            st.error(f"No shots found for {selected_player}")
                        else:
                            with tempfile.TemporaryDirectory() as tmp_dir:
                                fig = create_multi_match_shot_chart(
                                    chart_shots, team_name, team_color, chart_info,
                                    competition=competition, player_name=selected_player,
                                    exclude_penalties=exclude_penalties
                                )

                                name_part = team_name.replace(' ', '_')
                                if selected_player:
                                    name_part = f"{selected_player.replace(' ', '_')}_{name_part}"
                                filename = f"shot_map_{name_part}_season.png"

                                path = os.path.join(tmp_dir, filename)
                                fig.savefig(path, dpi=300, bbox_inches='tight',
                                           facecolor=BG_COLOR, edgecolor='none')
                                plt.close(fig)

                                caption = f"{selected_player} Shot Map" if selected_player else f"{team_name} Shot Map"
                                st.image(path, caption=caption)
                                with open(path, "rb") as f:
                                    st.download_button(
                                        label="Download Shot Map",
                                        data=f.read(),
                                        file_name=filename,
                                        mime="image/png"
                                    )

                            st.success("Shot map generated!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("Upload a TruMedia Event Log CSV for a single match or full season.")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Single-match CSV columns:**
        - `Date`, `homeTeam`, `awayTeam`
        - `Team`, `xG`, `playType`
        - `EventX`, `EventY` (shot coordinates, 0-100 scale)
        - `newestTeamColor` (optional)
        - `homeFinalScore`, `awayFinalScore` (optional)

        **Multi-match (season) CSV columns:**
        - `Date`, `Team`, `xG`, `playType`
        - `EventX`, `EventY` (shot coordinates, 0-100 scale)
        - `shooter` or `Player` (for player filtering)
        - `gameId` (optional, for match grouping)
        - `newestTeamColor` (optional)
        - `homeTeam`, `awayTeam` (optional, for match grouping)

        **Shot types detected:**
        - Miss, Goal, PenaltyGoal, AttemptSaved, Post
        """)
