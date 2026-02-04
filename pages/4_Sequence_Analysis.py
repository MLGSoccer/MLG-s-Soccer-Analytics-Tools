"""
Sequence Analysis Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.sequence_analysis_chart import (
    extract_sequences,
    analyze_sequences,
    extract_match_info,
    create_sequence_analysis_chart,
    create_individual_charts
)
from shared.colors import get_team_color

st.set_page_config(page_title="Sequence Analysis", page_icon="🔄", layout="wide")

st.title("Sequence Analysis Chart")
st.markdown("Analyze how possessions build toward shots - sequence length, shot quality, and team comparisons.")

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Event log with sequence IDs and play types"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Extracting sequences..."):
            sequences, csv_team_colors = extract_sequences(tmp_path)

        st.success(f"Found {len(sequences)} sequences")

        with st.spinner("Analyzing sequence patterns..."):
            length_data, team_data, shot_sequences, team_length_data = analyze_sequences(sequences)
            match_info = extract_match_info(tmp_path)

        st.info(f"Found {len(shot_sequences)} shot sequences")

        # Show teams
        teams = list(team_data.keys())
        if len(teams) >= 2:
            col1, col2 = st.columns(2)
            col1.metric("Home Team", teams[0])
            col2.metric("Away Team", teams[1])

        # Pre-check team colors
        from pages.streamlit_utils import check_team_colors
        check_team_colors(teams, csv_team_colors)

        if st.button("Generate Charts", type="primary"):
            with st.spinner("Generating charts..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    # Resolve team colors
                    team_colors = {}
                    for team in teams:
                        if csv_team_colors and team in csv_team_colors:
                            team_colors[team] = csv_team_colors[team]
                        else:
                            team_colors[team] = get_team_color(team, prompt_if_missing=False)

                    output_path = os.path.join(tmp_dir, "sequence_analysis.png")

                    create_sequence_analysis_chart(
                        length_data, team_data, shot_sequences,
                        match_info, output_path,
                        team_colors=team_colors,
                        team_length_data=team_length_data
                    )

                    st.image(output_path, caption="Sequence Analysis - Combined")

                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="Download Combined Chart",
                            data=f.read(),
                            file_name="sequence_analysis.png",
                            mime="image/png"
                        )

                    # Generate individual charts
                    st.markdown("---")
                    st.subheader("Individual Charts")

                    create_individual_charts(
                        length_data, team_data, shot_sequences,
                        match_info, tmp_dir,
                        team_colors=team_colors,
                        team_length_data=team_length_data
                    )

                    col1, col2 = st.columns(2)
                    chart_files = [
                        ("seq_shot_quality_by_length.png", "Shot Quality by Length"),
                        ("seq_team_profiles.png", "Team Profiles"),
                        ("seq_shots_scatter.png", "Shots Scatter"),
                        ("seq_xg_distribution.png", "xG Distribution")
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
                                        file_name=filename,
                                        mime="image/png",
                                        key=f"download_{filename}"
                                    )

                st.success("Charts generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia Event Log CSV to get started")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `sequenceId` - Unique ID for each possession
        - `playType` - Type of event (Pass, Shot, Goal, etc.)
        - `Team` - Team in possession
        - `xG` - Expected goals value for shots

        **Optional columns:**
        - `homeTeam`, `awayTeam`, `Date`
        - `newestTeamColor`
        """)
