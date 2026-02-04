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
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Parsing player data..."):
            matches, player_name, team_name, team_color, season, player_info = parse_player_summary_csv(tmp_path, gui_mode=True)

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
            with st.spinner("Generating charts..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    safe_name = player_name.replace(' ', '_').replace('.', '')
                    output_path = os.path.join(tmp_dir, f"{safe_name}_rolling_analysis.png")

                    create_rolling_charts(matches, player_name, team_name, team_color,
                                         season, output_path, window_size, player_info)

                    st.image(output_path, caption=f"{player_name} - Rolling xG Analysis")

                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="Download Combined Chart",
                            data=f.read(),
                            file_name=f"{safe_name}_rolling_xg.png",
                            mime="image/png"
                        )

                    # Generate individual charts
                    st.markdown("---")
                    st.subheader("Individual Charts")

                    create_individual_charts(matches, player_name, team_name, team_color,
                                            season, tmp_dir, window_size, player_info)

                    col1, col2 = st.columns(2)
                    chart_files = [
                        ("player_goals_vs_xg_rolling.png", "Goals vs xG Rolling"),
                        ("player_xg_per90_trend.png", "xG per 90 Trend"),
                        ("player_shot_volume_quality.png", "Shot Volume & Quality"),
                        ("player_last10_vs_avg.png", "Last 10 vs Season Avg")
                    ]

                    for i, (filename, title) in enumerate(chart_files):
                        filepath = os.path.join(tmp_dir, filename)
                        if os.path.exists(filepath):
                            col = col1 if i % 2 == 0 else col2
                            with col:
                                st.image(filepath, caption=title)
                                with open(filepath, "rb") as f:
                                    st.download_button(
                                        label=f"Download {title}",
                                        data=f.read(),
                                        file_name=f"{safe_name}_{filename}",
                                        mime="image/png",
                                        key=f"download_{filename}"
                                    )

                st.success("Charts generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia Player Summary CSV to get started")

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
