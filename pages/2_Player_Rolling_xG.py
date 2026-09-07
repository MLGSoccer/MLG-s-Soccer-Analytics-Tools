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
    create_individual_charts,
    create_aspect_chart,
    InsufficientMatches,
    NoShots,
)
from shared.motherduck import (
    get_teams_by_league, get_players_with_minutes_for_team, get_player_game_log,
    season_label, player_chart_subject,
)
from shared.rolling import longest_segment
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


def _build_chart_images(matches, player_name, team_name, team_color, season,
                        window_size, player_info=None, custom_title=None,
                        custom_subtitle=None, aspect="16:9"):
    """Generate chart images from a matches list. Returns charts dict.

    ONE generation path for both data sources and all three aspects. The DB
    branch used to inline this whole block inside its `st.button`, so the
    review harness could not call the page's own code the way the momentum and
    team harnesses do - it had to reproduce the page's argument derivation and
    keep it in sync by hand. Mirrors `_build_chart_images` on page 1.

    16:9 gives the four-panel dashboard plus its four standalone panels. The
    phone aspects give ONE chart - GOALS/90 vs xG/90 - because a 2x2 grid at
    9:16 produces four panels none of which is readable.
    """
    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        if aspect != "16:9":
            key = {"9:8 (tile)": "9x8", "9:16 (vertical)": "9x16"}[aspect]
            path = os.path.join(tmp_dir, "aspect.png")
            create_aspect_chart(matches, player_name, team_name, team_color,
                                path, window_size, aspect=key,
                                custom_title=custom_title,
                                custom_subtitle=custom_subtitle)
            with open(path, "rb") as f:
                charts["combined"] = f.read()
            return charts

        safe_name = player_name.replace(' ', '_').replace('.', '')
        output_path = os.path.join(tmp_dir, f"{safe_name}_rolling_analysis.png")

        create_rolling_charts(matches, player_name, team_name, team_color,
                              season, output_path, window_size, player_info,
                              custom_title=custom_title,
                              custom_subtitle=custom_subtitle)
        with open(output_path, "rb") as f:
            charts["combined"] = f.read()

        create_individual_charts(matches, player_name, team_name, team_color,
                                 season, tmp_dir, window_size, player_info)

        individual_charts = [
            ("player_goals_vs_xg_rolling.png", "Goals vs xG Rolling"),
            ("player_xg_per90_trend.png", "xG Trend"),
            ("player_shot_volume_quality.png", "Shot Volume & Quality"),
            ("player_last10_vs_avg.png", "Last 10 vs His Average"),
        ]
        for filename, title in individual_charts:
            filepath = os.path.join(tmp_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    charts[filename] = (title, f.read())

    return charts


@st.cache_data
def _generate_player_charts(file_content, player_name, team_name, team_color, season, window_size, player_info,
                            custom_title=None, custom_subtitle=None, aspect="16:9"):
    """CSV-mode wrapper: parse the upload, then build. Cached across reruns."""
    matches, _, _, _, _, _ = _parse_player_csv_cached(file_content)
    return _build_chart_images(matches, player_name, team_name, team_color,
                               season, window_size, player_info,
                               custom_title=custom_title,
                               custom_subtitle=custom_subtitle, aspect=aspect)


def _player_chart_note(matches, window, player_name):
    """Tell the operator up front whether this selection can carry the chart.

    The chart now refuses two selections it used to draw: one with no shots in
    it at all, and one shorter than the rolling window. Both are common - 22.1%
    and 45.4% of selectable players respectively - and every goalkeeper in the
    picker is one click from the first. Saying so before the Generate click is
    cheaper than an error after it. Mirrors `_window_note` on the team page.
    """
    if sum(m["shots"] for m in matches) == 0:
        st.error(
            f"**{player_name}** has no shots in this selection, so there is no "
            f"xG to chart. This is normal for a goalkeeper or a defender - try "
            f"an attacking player, or widen the season filter."
        )
        return False
    usable = longest_segment([{"season": m.get("season_name") or m.get("season", "")}
                              for m in matches])
    if usable < window:
        st.error(
            f"A {window}-game rolling average needs {window} matches inside one "
            f"season. The longest run in this selection is {usable}. Lower the "
            f"Rolling Window slider to {max(usable, 3)} or below, or widen the "
            f"season filter."
        )
        return False
    return True


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

    # The phone aspects return a single chart, so there is no panel section to
    # head. Without this guard they printed an "Individual Charts" subheader
    # over nothing.
    individual_keys = [k for k in charts if k != "combined"]
    if not individual_keys:
        st.success("Chart generated successfully!")
        return

    st.markdown("---")
    st.subheader("Individual Charts")

    col1, col2 = st.columns(2)

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

aspect = st.sidebar.radio(
    "Aspect",
    ["16:9", "9:8 (tile)", "9:16 (vertical)"],
    help=("16:9 is the four-panel dashboard plus its four standalone panels. "
          "The two phone shapes give a single GOALS/90 vs xG/90 chart. Unlike "
          "the team charts these use a per-chart y-scale rather than a shared "
          "one: player rates run from a goalkeeper's zero to a striker's 3.5, "
          "and no shared ceiling fits both."),
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
                players_with_minutes = get_players_with_minutes_for_team(selected_team['team_id'])
            if players_with_minutes:
                player_name_options = [p["player_name"] for p in players_with_minutes]
                selected_player_name = st.selectbox("Player", options=[""] + player_name_options)
            else:
                st.selectbox("Player", options=["No minutes data — download first"], disabled=True)
                selected_player_name = None
        else:
            st.selectbox("Player", options=[], disabled=True)
            selected_player_name = None

    _player_id = None
    if selected_player_name and selected_team:
        _player_entry = next(
            (p for p in players_with_minutes if p["player_name"] == selected_player_name), None
        )
        _player_id = _player_entry["player_id"] if _player_entry else None

    if selected_player_name and _player_id:
        with st.spinner(f"Loading data for {selected_player_name}..."):
            matches = get_player_game_log(_player_id, selected_player_name)

        if not matches:
            st.warning(
                f"No game log found for **{selected_player_name}**. "
                "This usually means minutes data hasn't been downloaded yet — "
                "run the Data Manager → Minutes & Cards Downloads."
            )
        else:
            # The club with the most appearances, resolved on team_id through
            # the registry -- NOT matches[-1], which for 5.2% of players is an
            # international and titled the chart with a national side.
            _tid, team_name, team_color = player_chart_subject(matches)
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
                    seen[sid] = season_label(sid, m["season_name"])
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

            # Re-resolve after the season filter: narrowing to one season can
            # change which club owns most of the selection.
            _tid, team_name, team_color = player_chart_subject(matches)

            custom_title, custom_subtitle = custom_title_inputs(
                "player_rolling_db", selected_player_name.upper()
            )

            if not _player_chart_note(matches, window_size,
                                      selected_player_name):
                st.stop()

            if st.button("Generate Charts", type="primary", key="db_gen"):
                st.session_state["player_rolling_xg_charts"] = None
                st.session_state["player_rolling_xg_name"] = None
                with st.spinner("Generating charts..."):
                    try:
                        charts = _build_chart_images(
                            matches, selected_player_name, team_name,
                            team_color, season, window_size, player_info=None,
                            custom_title=custom_title,
                            custom_subtitle=custom_subtitle, aspect=aspect)
                        st.session_state["player_rolling_xg_charts"] = charts
                        st.session_state["player_rolling_xg_name"] = selected_player_name
                    except (InsufficientMatches, NoShots) as e:
                        # The pre-flight note above should have caught these;
                        # the chart refuses too so a stale session or a code
                        # path that skips the note cannot draw something false.
                        st.error(str(e))
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
                    charts = _generate_player_charts(
                        file_content, player_name, team_name, team_color,
                        season, window_size, player_info,
                        custom_title=custom_title,
                        custom_subtitle=custom_subtitle, aspect=aspect)
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
