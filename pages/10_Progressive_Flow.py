"""
Progressive Flow - Streamlit Page
Visualizes how a team progresses the ball (passes + carries) across pitch zones.
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.passing_flow_chart import (
    load_passing_data,
    build_zone_flows,
    compute_flow_stats,
    create_passing_flow_chart,
    create_zone_reference_figure,
    extract_match_info,
)
from shared.styles import BG_COLOR
from shared.colors import TEAM_COLORS, fuzzy_match_team
import matplotlib.pyplot as plt

st.set_page_config(page_title="Progressive Flow", page_icon="", layout="wide")


@st.cache_data
def _read_csv_cached(file_content):
    """Read and cache raw CSV from uploaded bytes."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return pd.read_csv(tmp_path)
    finally:
        os.unlink(tmp_path)


st.title("Progressive Flow")
st.markdown("Visualizes how a team progresses the ball (passes + carries) across pitch zones.")

# Sidebar controls
st.sidebar.header("Settings")
competition = st.sidebar.text_input(
    "Competition Name",
    value="",
    help="e.g., Premier League, Serie A (optional)"
)

forward_only = st.sidebar.checkbox(
    "Forward Only",
    value=False,
    help="Show only forward passes (destination column > source column, or passes into penalty area)"
)

# Zone reference in sidebar expander
with st.sidebar.expander("Zone Reference"):
    st.markdown("""
    The pitch is divided into **15 zones** across 5 columns:

    | Column | Zones |
    |--------|-------|
    | Defensive Third | Wing Left, Penalty Area, Wing Right |
    | Def Mid | Left, Center, Right |
    | Midfield | Left, Center, Right |
    | Att Mid | Left, Center, Right |
    | Attacking Third | Wing Left, Penalty Area, Wing Right |

    **Forward pass rule:** destination column > source column, OR destination is a penalty area.
    """)

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Single-match event log with pass data (needs playType, EventX, EventY, sequenceId, gameEventIndex)"
)

if uploaded_file is not None:
    file_content = uploaded_file.getvalue()

    try:
        raw_df = _read_csv_cached(file_content)

        # Detect teams
        teams = raw_df['Team'].dropna().unique().tolist()
        if not teams:
            st.error("No teams found in the CSV (missing 'Team' column).")
        else:
            selected_team = st.selectbox(
                "Select Team",
                options=teams,
                help="Choose which team's passing flow to visualize"
            )

            # Match info
            match_info = extract_match_info(raw_df, selected_team)
            opponent = match_info.get('opponent', '')
            date = match_info.get('date', '')
            score = match_info.get('score', '')

            info_parts = []
            if opponent:
                info_parts.append(f"vs **{opponent}**")
            if score:
                info_parts.append(score)
            if date:
                info_parts.append(date)
            if info_parts:
                st.markdown(" | ".join(info_parts))

            # Resolve team color
            db_color, _, _ = fuzzy_match_team(selected_team, TEAM_COLORS)
            if not db_color and 'newestTeamColor' in raw_df.columns:
                team_rows = raw_df[raw_df['Team'] == selected_team]
                csv_color = team_rows['newestTeamColor'].dropna()
                db_color = csv_color.iloc[0] if not csv_color.empty else None
            team_color = db_color or '#6CABDD'

            if st.button("Generate Progressive Flow", type="primary"):
                with st.spinner("Analyzing ball progression..."):
                    pass_df = load_passing_data(raw_df, selected_team)

                    if pass_df.empty:
                        st.error("No data found. Ensure the CSV has 'playType', 'EventX', 'EventY', 'sequenceId', and 'gameEventIndex' columns.")
                    else:
                        # Filter to forward only if requested
                        chart_df = pass_df
                        if forward_only:
                            mask = (pass_df['dest_col'] > pass_df['source_col']) | pass_df['dest_zone'].str.contains('Penalty Area')
                            chart_df = pass_df[mask]

                        flows = build_zone_flows(pass_df, forward_only=forward_only)
                        stats = compute_flow_stats(pass_df, flows)

                        if chart_df.empty:
                            st.warning("No movements found with current filters. Try unchecking 'Forward Only'.")
                        else:
                            # Stats display
                            pos = stats.get('position', {})
                            dirn = stats.get('direction', {})

                            st.markdown("**Position (by destination)**")
                            c1, c2, c3 = st.columns(3)
                            c1.metric("Left", pos.get('left', 0))
                            c2.metric("Center", pos.get('center', 0))
                            c3.metric("Right", pos.get('right', 0))

                            st.markdown("**Direction**")
                            d1, d2, d3 = st.columns(3)
                            d1.metric("Forward", dirn.get('forward', 0))
                            d2.metric("Sideways", dirn.get('sideways', 0))
                            d3.metric("Backward", dirn.get('backward', 0))

                            # Create and display chart
                            fig = create_passing_flow_chart(
                                chart_df, selected_team, team_color, match_info,
                                stats, competition=competition,
                            )

                            st.pyplot(fig)

                            # Download PNG
                            with tempfile.TemporaryDirectory() as tmp_dir:
                                safe_name = selected_team.replace(' ', '_')
                                filename = f"progressive_flow_{safe_name}.png"
                                filepath = os.path.join(tmp_dir, filename)

                                fig.savefig(filepath, dpi=300, bbox_inches='tight',
                                            facecolor=BG_COLOR, edgecolor='none')
                                plt.close(fig)

                                with open(filepath, "rb") as f:
                                    st.download_button(
                                        label="Download PNG",
                                        data=f.read(),
                                        file_name=filename,
                                        mime="image/png"
                                    )

                            # Zone reference diagram
                            with st.expander("Zone Reference Diagram"):
                                ref_fig = create_zone_reference_figure(team_color)
                                st.pyplot(ref_fig)
                                plt.close(ref_fig)

                            st.success("Progressive flow chart generated!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

else:
    st.info("Upload a TruMedia Event Log CSV to get started.")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `Team` - Team name
        - `playType` - Event type (must include 'Pass')
        - `EventX`, `EventY` - Coordinates (0-100 scale)
        - `sequenceId` - Possession sequence identifier
        - `gameEventIndex` - Event ordering within sequences

        **Optional columns:**
        - `homeTeam`, `awayTeam` - For match info display
        - `Date` - Match date
        - `homeFinalScore`, `awayFinalScore` - Final score
        - `newestTeamColor` - Team color from CSV
        """)
