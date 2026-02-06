"""
Shot Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shot_chart import (
    load_shot_data,
    create_team_shot_chart,
    create_combined_shot_chart,
    GOAL_TYPES,
    ensure_pitch_contrast
)
from shared.styles import BG_COLOR
import matplotlib.pyplot as plt

st.set_page_config(page_title="Shot Chart", page_icon="🎯", layout="wide")

st.title("Shot Chart")
st.markdown("Visualize shot locations for a single match.")

# Sidebar controls
st.sidebar.header("Settings")
competition = st.sidebar.text_input(
    "Competition Name",
    value="",
    help="e.g., Premier League, Serie A (optional)"
)
if competition:
    st.sidebar.success(f"Competition: {competition}")

# Chart selection
st.sidebar.header("Chart Output")
chart_options = st.sidebar.multiselect(
    "Charts to generate",
    options=["Individual Team Charts", "Combined Chart"],
    default=["Individual Team Charts", "Combined Chart"],
    help="Select which chart types to generate"
)

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Single-match event log with shot data (needs EventX, EventY columns)"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Parsing match data..."):
            shots_df, match_info, team_colors = load_shot_data(tmp_path)

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
                                flip_coords=team1_flip, competition=competition
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
                                flip_coords=team2_flip, competition=competition
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
                                match_info, competition=competition
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

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("Upload a TruMedia Event Log CSV for a single match")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `Date`, `homeTeam`, `awayTeam`
        - `Team`, `xG`, `playType`
        - `EventX`, `EventY` (shot coordinates, 0-100 scale)

        **Optional columns:**
        - `newestTeamColor` (team colors)
        - `homeFinalScore`, `awayFinalScore`

        **Shot types detected:**
        - Miss, Goal, PenaltyGoal, AttemptSaved
        """)
