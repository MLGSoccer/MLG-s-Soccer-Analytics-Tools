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
from shared.styles import BG_COLOR
from shared.motherduck import get_teams_by_league, get_games_for_team, build_shots_from_game
import matplotlib.pyplot as plt

st.set_page_config(page_title="xG Race Chart", page_icon="🏁", layout="wide")


@st.cache_data
def _parse_xg_race_cached(file_content):
    """Cache xG race CSV parsing from uploaded bytes."""
    import tempfile as _tempfile
    with _tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return parse_trumedia_csv(tmp_path)
    finally:
        os.unlink(tmp_path)


def _generate_chart(shots, match_info, team_colors, competition, own_goals_hashable):
    """Generate xG race chart and return (img_bytes, filename, caption)."""
    config = {
        'competition': competition if competition else None,
        'own_goals': [{'minute': m, 'team': t} for m, t in own_goals_hashable],
    }
    team_info = get_team_info(shots, match_info, team_colors, config)
    team_info['data_source'] = 'trumedia'

    fig = create_xg_chart(shots, team_info)
    if fig is None:
        return None, None, None

    team1 = team_info['team1']['name'].replace(' ', '_').replace('/', '-')
    team2 = team_info['team2']['name'].replace(' ', '_').replace('/', '-')
    filename = f"xg_race_{team1}_vs_{team2}.png"

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = os.path.join(tmp_dir, filename)
        fig.savefig(output_path, dpi=300, bbox_inches='tight',
                    facecolor=BG_COLOR, edgecolor='none')
        plt.close(fig)
        with open(output_path, "rb") as f:
            img_bytes = f.read()

    caption = f"{team_info['team1']['name']} vs {team_info['team2']['name']}"
    return img_bytes, filename, caption


st.title("xG Race Chart")
st.markdown("Visualize how xG accumulates throughout a single match.")

# ── Data source toggle ────────────────────────────────────────────────────────
data_source = st.radio(
    "Data source",
    options=["Database", "Upload CSV"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

# ── Database mode ─────────────────────────────────────────────────────────────
if data_source == "Database":
    try:
        teams_by_league = get_teams_by_league()
    except Exception as e:
        st.error(f"Could not connect to database: {e}")
        st.stop()

    league_names = list(teams_by_league.keys())

    col1, col2, col3 = st.columns(3)

    with col1:
        selected_league = st.selectbox("League", options=[""] + league_names)

    with col2:
        if selected_league:
            team_options = teams_by_league[selected_league]
            team_labels = [t['display_name'] for t in team_options]
            selected_team_name = st.selectbox("Team", options=[""] + team_labels)
            selected_team = next(
                (t for t in team_options if t['display_name'] == selected_team_name), None
            )
        else:
            st.selectbox("Team", options=[], disabled=True)
            selected_team = None

    with col3:
        if selected_team:
            games = get_games_for_team(selected_team['team_id'])
            if games:
                game_labels = [g['label'] for g in games]
                selected_game_label = st.selectbox("Game", options=[""] + game_labels)
                selected_game = next(
                    (g for g in games if g['label'] == selected_game_label), None
                )
            else:
                st.selectbox("Game", options=["No games found"], disabled=True)
                selected_game = None
        else:
            st.selectbox("Game", options=[], disabled=True)
            selected_game = None

    competition = st.text_input(
        "Competition Name",
        value=selected_league if selected_league else "",
        help="Auto-filled from league — edit if this game is from a different competition (e.g. Champions League)"
    )

    if selected_game:
        shots, match_info, team_colors = build_shots_from_game(selected_game['game_id'])

        if not shots:
            st.warning("No shot data found for this game.")
        else:
            home_team = match_info.get('home_team', 'Home')
            away_team = match_info.get('away_team', 'Away')

            st.success(f"**{home_team}** vs **{away_team}**  —  {match_info.get('date', '')}")

            from pages.streamlit_utils import check_team_colors
            check_team_colors([home_team, away_team], team_colors)

            # Own goals
            st.sidebar.header("Own Goals")
            st.sidebar.caption("Add any own goals not in the data")
            num_own_goals = st.sidebar.number_input(
                "Number of own goals", min_value=0, max_value=5, value=0
            )
            own_goals = []
            for i in range(num_own_goals):
                st.sidebar.markdown(f"**Own Goal {i+1}**")
                og_col1, og_col2 = st.sidebar.columns(2)
                with og_col1:
                    minute = st.number_input(
                        "Minute", min_value=1, max_value=120, value=45, key=f"og_minute_{i}"
                    )
                with og_col2:
                    scoring_team = st.selectbox(
                        "Scored by", options=[home_team, away_team], key=f"og_team_{i}"
                    )
                credited_team = away_team if scoring_team == home_team else home_team
                own_goals.append({'minute': minute, 'team': credited_team})
                st.sidebar.caption(f"Goal credited to {credited_team}")

            if st.button("Generate Chart", type="primary"):
                st.session_state["xg_race_chart"] = None
                with st.spinner("Generating xG race chart..."):
                    own_goals_hashable = tuple((og['minute'], og['team']) for og in own_goals)
                    img_bytes, filename, caption = _generate_chart(
                        shots, match_info, team_colors, competition, own_goals_hashable
                    )
                    if img_bytes is None:
                        st.error("Chart generation failed. Please check team names.")
                    else:
                        st.session_state["xg_race_chart"] = {
                            "img": img_bytes,
                            "filename": filename,
                            "caption": caption,
                        }

            if st.session_state.get("xg_race_chart"):
                chart = st.session_state["xg_race_chart"]
                st.image(chart["img"], caption=chart["caption"])
                st.download_button(
                    label="Download Chart",
                    data=chart["img"],
                    file_name=chart["filename"],
                    mime="image/png"
                )

# ── CSV upload mode ───────────────────────────────────────────────────────────
else:
    competition = st.text_input(
        "Competition Name",
        value="",
        help="e.g., Premier League, Champions League (optional)"
    )

    uploaded_file = st.file_uploader(
        "Upload TruMedia Event Log CSV",
        type=["csv"],
        help="Single-match event log with shot data"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()

        try:
            with st.spinner("Parsing match data..."):
                shots, match_info, team_colors = _parse_xg_race_cached(file_content)

            if not shots:
                st.error("No shot data found in CSV.")
            else:
                home_team = match_info.get('home_team', 'Home Team')
                away_team = match_info.get('away_team', 'Away Team')

                st.success(f"Found {len(shots)} shots: **{home_team}** vs **{away_team}**")

                col1, col2, col3 = st.columns(3)
                col1.metric("Date", match_info.get('date', 'Unknown'))
                col2.metric("Home Team", home_team)
                col3.metric("Away Team", away_team)

                from pages.streamlit_utils import check_team_colors
                check_team_colors([home_team, away_team], team_colors)

                st.sidebar.header("Own Goals")
                st.sidebar.caption("Add any own goals not in the data")
                num_own_goals = st.sidebar.number_input(
                    "Number of own goals", min_value=0, max_value=5, value=0
                )
                own_goals = []
                for i in range(num_own_goals):
                    st.sidebar.markdown(f"**Own Goal {i+1}**")
                    og_col1, og_col2 = st.sidebar.columns(2)
                    with og_col1:
                        minute = st.number_input(
                            "Minute", min_value=1, max_value=120, value=45, key=f"og_minute_{i}"
                        )
                    with og_col2:
                        scoring_team = st.selectbox(
                            "Scored by", options=[home_team, away_team], key=f"og_team_{i}"
                        )
                    credited_team = away_team if scoring_team == home_team else home_team
                    own_goals.append({'minute': minute, 'team': credited_team})
                    st.sidebar.caption(f"Goal credited to {credited_team}")

                if st.button("Generate Chart", type="primary"):
                    st.session_state["xg_race_chart"] = None
                    with st.spinner("Generating xG race chart..."):
                        own_goals_hashable = tuple((og['minute'], og['team']) for og in own_goals)
                        img_bytes, filename, caption = _generate_chart(
                            shots, match_info, team_colors, competition, own_goals_hashable
                        )
                        if img_bytes is None:
                            st.error("Chart generation failed. Please check team names.")
                        else:
                            st.session_state["xg_race_chart"] = {
                                "img": img_bytes,
                                "filename": filename,
                                "caption": caption,
                            }

                if st.session_state.get("xg_race_chart"):
                    chart = st.session_state["xg_race_chart"]
                    st.image(chart["img"], caption=chart["caption"])
                    st.download_button(
                        label="Download Chart",
                        data=chart["img"],
                        file_name=chart["filename"],
                        mime="image/png"
                    )

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")
            import traceback
            st.code(traceback.format_exc())

    else:
        st.info("Upload a TruMedia Event Log CSV for a single match")
