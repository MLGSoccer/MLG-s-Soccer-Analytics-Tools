"""
Team Rolling xG Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.team_rollingxg_chart import (
    parse_trumedia_csv,
    create_rolling_charts,
    create_individual_charts
)
from pages.streamlit_utils import custom_title_inputs
from shared.motherduck import get_teams_by_league, get_games_for_team, get_team_rolling_xg_data

st.set_page_config(page_title="Team Rolling xG", page_icon="📈", layout="wide")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_chart_images(matches, team_name, team_color, window_size,
                        custom_title=None, custom_subtitle=None):
    """Generate chart images from a matches list. Returns charts dict."""
    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        combined_path = os.path.join(tmp_dir, "combined.png")
        create_rolling_charts(matches, team_name, team_color, combined_path, window_size,
                              custom_title=custom_title, custom_subtitle=custom_subtitle)
        with open(combined_path, "rb") as f:
            charts["combined"] = f.read()

        create_individual_charts(matches, team_name, team_color, tmp_dir, window_size)

        individual_charts = [
            ("rolling_xg_difference.png", "xG Difference"),
            ("rolling_xg_for_against.png", "xG For & Against"),
            ("rolling_xg_combined.png", "Combined View"),
            ("rolling_xg_cumulative.png", "Cumulative xG vs Goals"),
        ]
        for filename, title in individual_charts:
            filepath = os.path.join(tmp_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    charts[filename] = (title, f.read())

    return charts


# ---------------------------------------------------------------------------
# CSV-mode helpers (cached)
# ---------------------------------------------------------------------------

@st.cache_data
def _parse_csv_cached(file_content):
    """Cache CSV parsing from uploaded bytes."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return parse_trumedia_csv(tmp_path, gui_mode=True)
    finally:
        os.unlink(tmp_path)


@st.cache_data
def _generate_charts_csv(file_content, team_name, team_color, window_size,
                          custom_title=None, custom_subtitle=None):
    """Generate all charts from a CSV upload. Cached to survive reruns."""
    matches, _, _ = _parse_csv_cached(file_content)
    return _build_chart_images(matches, team_name, team_color, window_size,
                               custom_title=custom_title, custom_subtitle=custom_subtitle)


# ---------------------------------------------------------------------------
# Chart display helper
# ---------------------------------------------------------------------------

def _display_charts(charts, team_label):
    st.image(charts["combined"], caption=f"{team_label} - Rolling xG Analysis")
    st.download_button(
        label="Download Combined Chart",
        data=charts["combined"],
        file_name=f"{team_label.replace(' ', '_').replace('/', '-')}_rolling_xg.png",
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
                file_name=f"{team_label.replace(' ', '_').replace('/', '-')}_{key}",
                mime="image/png",
                key=f"download_{key}"
            )

    st.success("Charts generated successfully!")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("Team Rolling xG Analysis")
st.markdown("Analyze team xG performance over a season with rolling averages.")

# Sidebar controls
st.sidebar.header("Settings")
window_size = st.sidebar.slider(
    "Rolling Window (games)",
    min_value=3,
    max_value=15,
    value=10,
    help="Number of games to average over"
)

# Data source toggle
data_source = st.radio("Data source", ["Database", "Upload CSV"], horizontal=True)

# ---------------------------------------------------------------------------
# Database mode
# ---------------------------------------------------------------------------

if data_source == "Database":
    try:
        teams_by_league = get_teams_by_league()
    except Exception as e:
        st.error(f"Could not load teams from database: {e}")
        st.stop()

    league_names = list(teams_by_league.keys())
    selected_league = st.selectbox("League", league_names)

    teams = teams_by_league.get(selected_league, [])
    team_options = {t['display_name']: t['team_id'] for t in teams}
    selected_team_name = st.selectbox("Team", list(team_options.keys()))
    selected_team_id = team_options[selected_team_name]

    # Season filter
    try:
        games = get_games_for_team(selected_team_id)
    except Exception as e:
        st.error(f"Could not load games: {e}")
        st.stop()

    # Build unique season options from games list
    seen_seasons = {}
    for g in games:
        sid = g.get('season_id')
        sname = g.get('season_name') or sid or ''
        if sid and sid not in seen_seasons:
            seen_seasons[sid] = sname

    selected_season_id = None
    if len(seen_seasons) > 1:
        season_display = {name: sid for sid, name in seen_seasons.items()}
        season_options = ["All seasons"] + sorted(season_display.keys(), reverse=True)
        chosen = st.selectbox("Season / Competition", season_options)
        if chosen != "All seasons":
            selected_season_id = season_display[chosen]
    elif len(seen_seasons) == 1:
        only_name = list(seen_seasons.values())[0]
        if only_name:
            st.caption(f"Season: {only_name}")

    # Custom title inputs (reset when team changes)
    custom_title, custom_subtitle = custom_title_inputs(
        f"team_rolling_db_{selected_team_id}", selected_team_name.upper()
    )

    if st.button("Generate Charts", type="primary", key="db_generate"):
        st.session_state["team_rolling_xg_charts"] = None
        with st.spinner("Loading data and generating charts..."):
            try:
                matches, db_team_name, db_team_color = get_team_rolling_xg_data(
                    selected_team_id, season_id=selected_season_id
                )
                if not matches:
                    st.warning("No match data found for this team/season selection.")
                    st.stop()
                if len(matches) < 5:
                    st.warning("Warning: Few matches found. Rolling average may be less meaningful.")
                team_label = db_team_name or selected_team_name
                team_color = db_team_color or "#888888"
                charts = _build_chart_images(
                    matches, team_label, team_color, window_size,
                    custom_title=custom_title, custom_subtitle=custom_subtitle
                )
                st.session_state["team_rolling_xg_charts"] = charts
                st.session_state["team_rolling_xg_team"] = team_label
                st.session_state["team_rolling_xg_count"] = len(matches)
            except Exception as e:
                st.error(f"Error generating charts: {e}")

    if st.session_state.get("team_rolling_xg_charts"):
        charts = st.session_state["team_rolling_xg_charts"]
        stored_team = st.session_state.get("team_rolling_xg_team", "")
        match_count = st.session_state.get("team_rolling_xg_count", 0)
        st.success(f"Found {match_count} matches for **{stored_team}**")
        _display_charts(charts, stored_team)

# ---------------------------------------------------------------------------
# CSV upload mode
# ---------------------------------------------------------------------------

else:
    uploaded_file = st.file_uploader(
        "Upload TruMedia CSV",
        type=["csv"],
        help="Match summary or event log CSV from TruMedia"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()

        try:
            with st.spinner("Parsing match data..."):
                matches, team_name, team_color = _parse_csv_cached(file_content)

            st.success(f"Found {len(matches)} matches for **{team_name}**")

            from pages.streamlit_utils import check_team_colors
            csv_colors = {team_name: team_color} if team_color else {}
            check_team_colors([team_name], csv_colors)

            if len(matches) < 5:
                st.warning("Warning: Few matches found. Rolling average may be less meaningful.")

            custom_title, custom_subtitle = custom_title_inputs("team_rolling_csv", team_name.upper())

            if st.button("Generate Charts", type="primary", key="csv_generate"):
                st.session_state["team_rolling_xg_charts"] = None
                with st.spinner("Generating charts..."):
                    charts = _generate_charts_csv(
                        file_content, team_name, team_color, window_size,
                        custom_title=custom_title, custom_subtitle=custom_subtitle
                    )
                    st.session_state["team_rolling_xg_charts"] = charts
                    st.session_state["team_rolling_xg_team"] = team_name
                    st.session_state["team_rolling_xg_count"] = len(matches)

            if st.session_state.get("team_rolling_xg_charts"):
                charts = st.session_state["team_rolling_xg_charts"]
                stored_team = st.session_state.get("team_rolling_xg_team", "")
                _display_charts(charts, stored_team)

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")

    else:
        st.info("Upload a TruMedia CSV file to get started")

        with st.expander("Expected CSV Format"):
            st.markdown("""
            **Match Summary Format** (one row per match):
            - `Date`, `Team`, `opponent`, `xG`, `xGA`, `GF`, `GA`, `Home`, `seasonName`

            **Event Log Format** (one row per event):
            - `Date`, `homeTeam`, `awayTeam`, `Team`, `xG`, `playType`, `shooter`, `Period`

            The chart will auto-detect which format your CSV uses.
            """)
