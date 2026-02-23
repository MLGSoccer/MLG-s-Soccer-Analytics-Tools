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

st.set_page_config(page_title="Team Rolling xG", page_icon="📈", layout="wide")


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
def _generate_charts(file_content, team_name, team_color, window_size):
    """Generate all charts and return image bytes, cached to survive reruns."""
    # Re-parse (cached separately) to get matches
    matches, _, _ = _parse_csv_cached(file_content)

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Combined chart
        combined_path = os.path.join(tmp_dir, "combined.png")
        create_rolling_charts(matches, team_name, team_color, combined_path, window_size)
        with open(combined_path, "rb") as f:
            charts["combined"] = f.read()

        # Individual charts
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

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia CSV",
    type=["csv"],
    help="Match summary or event log CSV from TruMedia"
)

if uploaded_file is not None:
    file_content = uploaded_file.getvalue()

    try:
        # Parse the data (cached)
        with st.spinner("Parsing match data..."):
            matches, team_name, team_color = _parse_csv_cached(file_content)

        st.success(f"Found {len(matches)} matches for **{team_name}**")

        # Pre-check team color
        from pages.streamlit_utils import check_team_colors
        csv_colors = {team_name: team_color} if team_color else {}
        check_team_colors([team_name], csv_colors)

        if len(matches) < 5:
            st.warning("Warning: Few matches found. Rolling average may be less meaningful.")

        # Generate button
        if st.button("Generate Charts", type="primary"):
            st.session_state["team_rolling_xg_charts"] = None  # clear stale charts
            with st.spinner("Generating charts..."):
                charts = _generate_charts(file_content, team_name, team_color, window_size)
                st.session_state["team_rolling_xg_charts"] = charts
                st.session_state["team_rolling_xg_team"] = team_name

        # Display charts from session state (persists across reruns)
        if st.session_state.get("team_rolling_xg_charts"):
            charts = st.session_state["team_rolling_xg_charts"]
            stored_team = st.session_state.get("team_rolling_xg_team", "")

            # Combined chart
            st.image(charts["combined"], caption=f"{stored_team} - Rolling xG Analysis")
            st.download_button(
                label="Download Combined Chart",
                data=charts["combined"],
                file_name=f"{stored_team.replace(' ', '_')}_rolling_xg.png",
                mime="image/png"
            )

            # Individual charts
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
                        file_name=f"{stored_team.replace(' ', '_')}_{key}",
                        mime="image/png",
                        key=f"download_{key}"
                    )

            st.success("Charts generated successfully!")

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
