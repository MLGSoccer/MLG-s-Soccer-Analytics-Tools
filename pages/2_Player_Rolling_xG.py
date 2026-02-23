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
def _generate_player_charts(file_content, player_name, team_name, team_color, season, window_size, player_info):
    """Generate all charts and return image bytes, cached to survive reruns."""
    matches, _, _, _, _, _ = _parse_player_csv_cached(file_content)

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_name = player_name.replace(' ', '_').replace('.', '')
        output_path = os.path.join(tmp_dir, f"{safe_name}_rolling_analysis.png")

        create_rolling_charts(matches, player_name, team_name, team_color,
                             season, output_path, window_size, player_info)
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


st.title("Player Rolling xG Analysis")
st.markdown("Analyze individual player xG, goals, and shots over time with rolling averages.")

# Sidebar controls
st.sidebar.header("Settings")
window_size = st.sidebar.slider(
    "Rolling Window (games)",
    min_value=3,
    max_value=15,
    value=10,
    help="Number of games to average over"
)

# File upload
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

        if st.button("Generate Charts", type="primary"):
            st.session_state["player_rolling_xg_charts"] = None
            with st.spinner("Generating charts..."):
                charts = _generate_player_charts(file_content, player_name, team_name,
                                                 team_color, season, window_size, player_info)
                st.session_state["player_rolling_xg_charts"] = charts
                st.session_state["player_rolling_xg_name"] = player_name

        # Display charts from session state (persists across reruns)
        if st.session_state.get("player_rolling_xg_charts"):
            charts = st.session_state["player_rolling_xg_charts"]
            stored_name = st.session_state.get("player_rolling_xg_name", "")
            safe_name = stored_name.replace(' ', '_').replace('.', '')

            st.image(charts["combined"], caption=f"{stored_name} - Rolling xG Analysis")
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
