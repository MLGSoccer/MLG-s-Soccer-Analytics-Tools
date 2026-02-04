"""
Team Rolling xG Chart - Streamlit Page
"""
import streamlit as st
import matplotlib.pyplot as plt
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
from shared.styles import BG_COLOR

st.set_page_config(page_title="Team Rolling xG", page_icon="📈", layout="wide")

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
    # Save uploaded file to temp location (existing code expects file path)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        # Parse the data
        with st.spinner("Parsing match data..."):
            matches, team_name, team_color = parse_trumedia_csv(tmp_path, gui_mode=True)

        st.success(f"Found {len(matches)} matches for **{team_name}**")

        # Pre-check team color
        from pages.streamlit_utils import check_team_colors
        csv_colors = {team_name: team_color} if team_color else {}
        check_team_colors([team_name], csv_colors)

        if len(matches) < 5:
            st.warning("Warning: Few matches found. Rolling average may be less meaningful.")

        # Generate button
        if st.button("Generate Charts", type="primary"):
            with st.spinner("Generating charts..."):
                # Create combined chart
                fig = plt.figure(figsize=(16, 10))
                fig.patch.set_facecolor(BG_COLOR)

                # We need to modify create_rolling_charts to return the figure
                # For now, save to temp and reload
                with tempfile.TemporaryDirectory() as tmp_dir:
                    output_path = os.path.join(tmp_dir, "combined.png")
                    create_rolling_charts(matches, team_name, team_color, output_path, window_size)

                    # Display the chart
                    st.image(output_path, caption=f"{team_name} - Rolling xG Analysis")

                    # Download button
                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="Download Combined Chart",
                            data=f.read(),
                            file_name=f"{team_name.replace(' ', '_')}_rolling_xg.png",
                            mime="image/png"
                        )

                    # Generate individual charts
                    st.markdown("---")
                    st.subheader("Individual Charts")

                    create_individual_charts(matches, team_name, team_color, tmp_dir, window_size)

                    # Display individual charts in 2x2 grid
                    col1, col2 = st.columns(2)

                    chart_files = [
                        ("rolling_xg_difference.png", "xG Difference"),
                        ("rolling_xg_for_against.png", "xG For & Against"),
                        ("rolling_xg_combined.png", "Combined View"),
                        ("rolling_xg_cumulative.png", "Cumulative xG vs Goals")
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
                                        file_name=f"{team_name.replace(' ', '_')}_{filename}",
                                        mime="image/png",
                                        key=f"download_{filename}"
                                    )

                st.success("Charts generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")

    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia CSV file to get started")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Match Summary Format** (one row per match):
        - `Date`, `Team`, `opponent`, `xG`, `xGA`, `GF`, `GA`, `Home`, `seasonName`

        **Event Log Format** (one row per event):
        - `Date`, `homeTeam`, `awayTeam`, `Team`, `xG`, `playType`, `shooter`, `Period`

        The chart will auto-detect which format your CSV uses.
        """)
