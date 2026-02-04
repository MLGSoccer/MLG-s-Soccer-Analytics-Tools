"""
Player Bar Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.player_bar_chart import run as run_player_bar
from shared.stat_mappings import STAT_DISPLAY_NAMES

st.set_page_config(page_title="Player Bar Chart", page_icon="📊", layout="wide")

st.title("Player Bar Chart")
st.markdown("Create leaderboards, team rosters, or individual player comparisons.")

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Player Stats CSV",
    type=["csv"],
    help="Player statistics CSV"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        # Read CSV to get column names and values
        df = pd.read_csv(tmp_path)
        st.success(f"Loaded {len(df)} players")

        # Pre-check team colors (get unique teams and their CSV colors)
        from pages.streamlit_utils import check_team_colors
        if 'teamName' in df.columns:
            team_names = df['teamName'].dropna().unique().tolist()
            csv_colors = {}
            if 'newestTeamColor' in df.columns:
                for _, row in df.drop_duplicates('teamName').iterrows():
                    name = row.get('teamName')
                    color = row.get('newestTeamColor')
                    if name and pd.notna(color):
                        csv_colors[name] = color
            check_team_colors(team_names, csv_colors)

        # Get numeric columns for stat selection
        numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns.tolist()
        # Filter out obvious non-stat columns
        exclude = ['Age', 'age', 'Height', 'Weight', 'height', 'weight']
        stat_cols = [c for c in numeric_cols if c not in exclude]

        # Sidebar controls
        st.sidebar.header("Settings")

        mode = st.sidebar.selectbox(
            "Chart Mode",
            options=["league", "team", "individual"],
            format_func=lambda x: {"league": "League Leaderboard", "team": "Team Roster", "individual": "Individual Players"}[x]
        )

        stat = st.sidebar.selectbox(
            "Statistic",
            options=stat_cols,
            format_func=lambda x: STAT_DISPLAY_NAMES.get(x, x)
        )

        # Mode-specific options
        if mode == "team":
            # Check for league column (try common names)
            league_col = None
            for col in ['League', 'league', 'competitionName', 'compName', 'Competition']:
                if col in df.columns:
                    league_col = col
                    break

            # Filter by league first if available
            if league_col:
                leagues = sorted(df[league_col].dropna().unique().tolist())
                selected_league = st.sidebar.selectbox(
                    "Filter by League",
                    options=["All Leagues"] + leagues
                )
                if selected_league != "All Leagues":
                    filtered_df = df[df[league_col] == selected_league]
                else:
                    filtered_df = df
            else:
                filtered_df = df

            # Get teams from filtered data
            teams = sorted(filtered_df['teamName'].dropna().unique().tolist()) if 'teamName' in filtered_df.columns else []
            team = st.sidebar.selectbox("Select Team", options=[""] + teams, help="Click and type to search")
        else:
            team = None

        if mode == "individual":
            players = sorted(df['Player'].dropna().unique().tolist()) if 'Player' in df.columns else []
            selected_players = st.sidebar.multiselect("Select Players", options=players)
        else:
            selected_players = None

        # Common options
        min_minutes = st.sidebar.number_input("Minimum Minutes", min_value=0, value=450, step=90)
        max_players = st.sidebar.number_input("Max Players to Show", min_value=5, max_value=50, value=15)

        position_options = ["All", "Forward", "Midfielder", "Defender", "Goalkeeper"]
        position = st.sidebar.selectbox("Position Filter", options=position_options)
        if position == "All":
            position = None

        sort_ascending = st.sidebar.checkbox("Sort Ascending (lowest first)", value=False)

        st.sidebar.markdown("---")
        st.sidebar.subheader("Data Format")

        data_format = st.sidebar.selectbox(
            "CSV Data Is",
            options=["per90", "raw"],
            format_func=lambda x: "Already Per 90" if x == "per90" else "Raw Totals",
            help="Is your CSV data already per-90 normalized or raw totals?"
        )

        display_as = st.sidebar.selectbox(
            "Display Values As",
            options=["per90", "raw"],
            format_func=lambda x: "Per 90 Minutes" if x == "per90" else "Raw Totals",
            help="How should values appear on the chart?"
        )

        # Custom title
        custom_title = st.sidebar.text_input("Custom Title (optional)")

        # Generate button
        can_generate = True
        if mode == "team" and (not team or team == ""):
            can_generate = False
            st.warning("Please select a team")
        if mode == "individual" and not selected_players:
            can_generate = False
            st.warning("Please select at least one player")

        if can_generate and st.button("Generate Chart", type="primary"):
            with st.spinner("Generating chart..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    config = {
                        'file_path': tmp_path,
                        'output_folder': tmp_dir,
                        'mode': mode,
                        'stat': stat,
                        'min_minutes': min_minutes,
                        'max_players': max_players,
                        'position': position,
                        'sort_ascending': sort_ascending,
                        'data_format': data_format,
                        'display_as': display_as,
                        'gui_mode': True
                    }

                    if mode == "team":
                        config['team'] = team
                    if mode == "individual":
                        config['players'] = selected_players
                    if custom_title:
                        config['title'] = custom_title

                    result = run_player_bar(config)

                    # Find generated file
                    for f in os.listdir(tmp_dir):
                        if f.endswith('.png'):
                            filepath = os.path.join(tmp_dir, f)
                            st.image(filepath, caption="Player Bar Chart")

                            with open(filepath, "rb") as img:
                                st.download_button(
                                    label="Download Chart",
                                    data=img.read(),
                                    file_name=f,
                                    mime="image/png"
                                )
                            break

            st.success("Chart generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia Player Stats CSV to get started")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `Player` - Player name
        - `teamName` - Team name (for team mode)
        - `Min` - Minutes played
        - Various stat columns (Goals, Assists, xG, etc.)

        **Optional columns:**
        - `Position` or `positionGeneral`
        - `newestTeamColor`
        """)
