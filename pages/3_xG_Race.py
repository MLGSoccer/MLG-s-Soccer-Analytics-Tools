"""
xG Race Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.xg_race_chart import (
    parse_trumedia_csv,
    get_team_info,
    create_xg_chart
)
import matplotlib.pyplot as plt

st.set_page_config(page_title="xG Race Chart", page_icon="🏁", layout="wide")

st.title("xG Race Chart")
st.markdown("Visualize how xG accumulates throughout a single match.")

# Sidebar controls
st.sidebar.header("Settings")
competition = st.sidebar.text_input(
    "Competition Name",
    value="",
    help="e.g., Premier League, Champions League (optional)"
)
if competition:
    st.sidebar.success(f"Competition: {competition}")

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Single-match event log with shot data"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Parsing match data..."):
            shots, match_info, team_colors = parse_trumedia_csv(tmp_path)

        if not shots:
            st.error("No shot data found in CSV.")
        else:
            home_team = match_info.get('home_team', 'Home Team')
            away_team = match_info.get('away_team', 'Away Team')

            st.success(f"Found {len(shots)} shots: **{home_team}** vs **{away_team}**")

            # Show match info
            col1, col2, col3 = st.columns(3)
            col1.metric("Date", match_info.get('date', 'Unknown'))
            col2.metric("Home Team", home_team)
            col3.metric("Away Team", away_team)

            # Pre-check team colors
            from pages.streamlit_utils import check_team_colors
            check_team_colors([home_team, away_team], team_colors)

            # Own goals section in sidebar (now that we know team names)
            st.sidebar.header("Own Goals")
            st.sidebar.caption("Add any own goals not in the data")

            num_own_goals = st.sidebar.number_input(
                "Number of own goals",
                min_value=0,
                max_value=5,
                value=0,
                help="How many own goals to add"
            )

            own_goals = []
            for i in range(num_own_goals):
                st.sidebar.markdown(f"**Own Goal {i+1}**")
                og_col1, og_col2 = st.sidebar.columns(2)
                with og_col1:
                    minute = st.number_input(
                        "Minute",
                        min_value=1,
                        max_value=120,
                        value=45,
                        key=f"og_minute_{i}"
                    )
                with og_col2:
                    # Team that SCORED the own goal (goal credited to opponent)
                    scoring_team = st.selectbox(
                        "Scored by",
                        options=[home_team, away_team],
                        key=f"og_team_{i}",
                        help="Team that scored the own goal"
                    )
                # The goal is credited to the OTHER team
                credited_team = away_team if scoring_team == home_team else home_team
                own_goals.append({'minute': minute, 'team': credited_team})
                st.sidebar.caption(f"Goal credited to {credited_team}")

            if st.button("Generate Chart", type="primary"):
                with st.spinner("Generating xG race chart..."):
                    # Build config for get_team_info
                    config = {
                        'competition': competition if competition else None,
                        'own_goals': own_goals
                    }

                    # Get team info (this resolves colors, names, etc.)
                    team_info = get_team_info(shots, match_info, team_colors, config)
                    team_info['data_source'] = 'trumedia'

                    # Create the chart
                    fig = create_xg_chart(shots, team_info)

                    if fig is None:
                        st.error("Chart generation failed. Please check team names.")
                    else:
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            # Save the figure
                            team1 = team_info['team1']['name'].replace(' ', '_')
                            team2 = team_info['team2']['name'].replace(' ', '_')
                            output_path = os.path.join(tmp_dir, f"xg_race_{team1}_vs_{team2}.png")

                            fig.savefig(output_path, dpi=300, bbox_inches='tight',
                                       facecolor='#1A2332', edgecolor='none')
                            plt.close(fig)

                            st.image(output_path, caption="xG Race Chart")

                            with open(output_path, "rb") as f:
                                st.download_button(
                                    label="Download Chart",
                                    data=f.read(),
                                    file_name=f"xg_race_{team1}_vs_{team2}.png",
                                    mime="image/png"
                                )

                        st.success("Chart generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia Event Log CSV for a single match")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `Date`, `homeTeam`, `awayTeam`
        - `Team`, `shooter`, `xG`
        - `gameClock` or `Period` + minute info

        **Optional columns:**
        - `playType` (Goal, Shot, etc.)
        - `newestTeamColor`
        """)
