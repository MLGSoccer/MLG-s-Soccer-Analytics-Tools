"""
Player Rolling xG Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.player_rollingxg_chart import (
    parse_player_summary_csv,
    create_rolling_charts,
    create_individual_charts
)
from shared.motherduck import (
    get_teams_by_league, get_shooters_for_team, get_player_game_log,
)
from pages.streamlit_utils import custom_title_inputs

st.set_page_config(page_title="Player Rolling xG", page_icon="📊", layout="wide")


@st.cache_data
def _parse_player_csv_cached(file_content):
    """Cache player CSV parsing from uploaded bytes."""
    import tempfile as _tempfile
    with _tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return parse_player_summary_csv(tmp_path, gui_mode=True)
    finally:
        os.unlink(tmp_path)


@st.cache_data
def _generate_player_charts(file_content, player_name, team_name, team_color, season, window_size, player_info,
                            custom_title=None, custom_subtitle=None):
    """Generate all charts and return image bytes, cached to survive reruns."""
    matches, _, _, _, _, _ = _parse_player_csv_cached(file_content)

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_name = player_name.replace(' ', '_').replace('.', '')
        output_path = os.path.join(tmp_dir, f"{safe_name}_rolling_analysis.png")

        create_rolling_charts(matches, player_name, team_name, team_color,
                             season, output_path, window_size, player_info,
                             custom_title=custom_title, custom_subtitle=custom_subtitle)
        with open(output_path, "rb") as f:
            charts["combined"] = f.read()

        create_individual_charts(matches, player_name, team_name, team_color,
                                season, tmp_dir, window_size, player_info)

        individual_charts = [
            ("player_goals_vs_xg_rolling.png", "Goals vs xG Rolling"),
            ("player_xg_per90_trend.png", "xG per 90 Trend"),
            ("player_shot_volume_quality.png", "Shot Volume & Quality"),
            ("player_last10_vs_avg.png", "Last 10 vs Season Avg"),
        ]
        for filename, title in individual_charts:
            filepath = os.path.join(tmp_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    charts[filename] = (title, f.read())

    return charts


def _render_chart_outputs(charts, player_name):
    """Display combined + individual charts with download buttons."""
    safe_name = player_name.replace(' ', '_').replace('.', '')

    st.image(charts["combined"], caption=f"{player_name} - Rolling xG Analysis")
    st.download_button(
        label="Download Combined Chart",
        data=charts["combined"],
        file_name=f"{safe_name}_rolling_xg.png",
        mime="image/png"
    )

    st.markdown("---")
    st.subheader("Individual Charts")

    col1, col2 = st.columns(2)
    individual_keys = [k for k in charts if k != "combined"]

    for i, key in enumerate(individual_keys):
        title, img_bytes = charts[key]
        col = col1 if i % 2 == 0 else col2
        with col:
            st.image(img_bytes, caption=title)
            st.download_button(
                label=f"Download {title}",
                data=img_bytes,
                file_name=f"{safe_name}_{key}",
                mime="image/png",
                key=f"download_{key}"
            )

    st.success("Charts generated successfully!")


st.title("Player Rolling xG Analysis")
st.markdown("Analyze individual player xG, goals, and shots over time with rolling averages.")

# ── Data source toggle ─────────────────────────────────────────────────────────
data_source = st.radio(
    "Data source",
    options=["Database", "Upload CSV"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

# Sidebar controls (shared between modes)
st.sidebar.header("Settings")
window_size = st.sidebar.slider(
    "Rolling Window (games)",
    min_value=3,
    max_value=15,
    value=10,
    help="Number of games to average over"
)

# ── DATABASE MODE ──────────────────────────────────────────────────────────────
if data_source == "Database":
    try:
        with st.spinner("Loading teams..."):
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
            with st.spinner("Loading players..."):
                shooters = get_shooters_for_team(selected_team['team_id'])
            if shooters:
                selected_player_name = st.selectbox("Player", options=[""] + shooters)
            else:
                st.selectbox("Player", options=["No players found"], disabled=True)
                selected_player_name = None
        else:
            st.selectbox("Player", options=[], disabled=True)
            selected_player_name = None

    if selected_player_name:
        with st.spinner(f"Loading data for {selected_player_name}..."):
            matches = get_player_game_log(selected_player_name)

        if not matches:
            st.warning(
                f"No game log found for **{selected_player_name}**. "
                "This usually means minutes data hasn't been fetched yet — "
                "run the Data Manager to download player minutes."
            )
        else:
            # Derive team info from most recent match
            team_name = matches[-1]['team_name']
            team_color = matches[-1]['team_color']
            season = ""  # DB mode spans multiple seasons

            st.success(
                f"**{selected_player_name}** ({team_name}) — "
                f"{len(matches)} games with minutes data"
            )

            # Season filter — only shown when player has data across multiple seasons
            seen = {}
            for m in matches:
                sid = m["season"]
                if sid and sid not in seen:
                    seen[sid] = m["season_name"] or sid
            # seen is ordered by first appearance (chronological since matches sorted ASC)
            season_options = list(seen.values())
            if len(season_options) > 1:
                selected_seasons = st.multiselect(
                    "Filter by Season",
                    options=season_options,
                    default=season_options,
                )
                selected_season_ids = {sid for sid, name in seen.items() if name in selected_seasons}
                matches = [m for m in matches if m["season"] in selected_season_ids]
            else:
                selected_seasons = season_options

            if not matches:
                st.warning("No games match the selected seasons.")
                st.stop()

            if len(matches) < 5:
                st.warning("Warning: Few matches found. Rolling average may be less meaningful.")

            custom_title, custom_subtitle = custom_title_inputs(
                "player_rolling_db", selected_player_name.upper()
            )

            if st.button("Generate Charts", type="primary", key="db_gen"):
                st.session_state["player_rolling_xg_charts"] = None
                st.session_state["player_rolling_xg_name"] = None
                with st.spinner("Generating charts..."):
                    try:
                        charts = {}
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            safe_name = selected_player_name.replace(' ', '_').replace('.', '')
                            output_path = os.path.join(tmp_dir, f"{safe_name}_rolling_analysis.png")

                            create_rolling_charts(
                                matches, selected_player_name, team_name, team_color,
                                season, output_path, window_size, player_info=None,
                                custom_title=custom_title, custom_subtitle=custom_subtitle
                            )
                            with open(output_path, "rb") as f:
                                charts["combined"] = f.read()

                            create_individual_charts(
                                matches, selected_player_name, team_name, team_color,
                                season, tmp_dir, window_size
                            )

                            individual_charts = [
                                ("player_goals_vs_xg_rolling.png", "Goals vs xG Rolling"),
                                ("player_xg_per90_trend.png", "xG per 90 Trend"),
                                ("player_shot_volume_quality.png", "Shot Volume & Quality"),
                                ("player_last10_vs_avg.png", "Last 10 vs Season Avg"),
                            ]
                            for filename, title in individual_charts:
                                filepath = os.path.join(tmp_dir, filename)
                                if os.path.exists(filepath):
                                    with open(filepath, "rb") as f:
                                        charts[filename] = (title, f.read())

                        st.session_state["player_rolling_xg_charts"] = charts
                        st.session_state["player_rolling_xg_name"] = selected_player_name
                    except Exception as e:
                        st.error(f"Chart generation failed: {e}")

            if st.session_state.get("player_rolling_xg_charts"):
                _render_chart_outputs(
                    st.session_state["player_rolling_xg_charts"],
                    st.session_state.get("player_rolling_xg_name", selected_player_name)
                )

# ── CSV UPLOAD MODE ────────────────────────────────────────────────────────────
else:
    uploaded_file = st.file_uploader(
        "Upload TruMedia Player Summary CSV",
        type=["csv"],
        help="Player summary CSV with match-by-match stats"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()

        try:
            with st.spinner("Parsing player data..."):
                matches, player_name, team_name, team_color, season, player_info = _parse_player_csv_cached(file_content)

            st.success(f"Found {len(matches)} matches for **{player_name}** ({team_name})")

            # Show player info if available
            if player_info:
                cols = st.columns(4)
                if player_info.get('age'):
                    cols[0].metric("Age", player_info['age'])
                if player_info.get('nationality'):
                    cols[1].metric("Nationality", player_info['nationality'])
                if player_info.get('height'):
                    cols[2].metric("Height", player_info['height'])
                if player_info.get('weight'):
                    cols[3].metric("Weight", player_info['weight'])

            # Pre-check team color
            from pages.streamlit_utils import check_team_colors
            csv_colors = {team_name: team_color} if team_color else {}
            check_team_colors([team_name], csv_colors)

            if len(matches) < 5:
                st.warning("Warning: Few matches found. Rolling average may be less meaningful.")

            custom_title, custom_subtitle = custom_title_inputs("player_rolling", player_name.upper())

            if st.button("Generate Charts", type="primary"):
                st.session_state["player_rolling_xg_charts"] = None
                with st.spinner("Generating charts..."):
                    charts = _generate_player_charts(file_content, player_name, team_name,
                                                     team_color, season, window_size, player_info,
                                                     custom_title=custom_title, custom_subtitle=custom_subtitle)
                    st.session_state["player_rolling_xg_charts"] = charts
                    st.session_state["player_rolling_xg_name"] = player_name

            # Display charts from session state (persists across reruns)
            if st.session_state.get("player_rolling_xg_charts"):
                _render_chart_outputs(
                    st.session_state["player_rolling_xg_charts"],
                    st.session_state.get("player_rolling_xg_name", player_name)
                )

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")

    else:
        st.info("Upload a TruMedia Player Summary CSV to get started")

        with st.expander("Expected CSV Format"):
            st.markdown("""
            **Required columns:**
            - `Date`, `playerFullName` (or `Player`)
            - `newestTeam` (or `teamName`), `opponent`
            - `Min`, `Goal`, `ExpG`, `Shot`

            **Optional columns:**
            - `Age`, `Nationality`, `Height`, `Weight`
            - `seasonName`, `newestTeamColor`
            """)
