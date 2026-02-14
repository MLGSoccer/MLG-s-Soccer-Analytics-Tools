"""
Zone Passing - Streamlit Page
Shows where a team passes from each pitch zone with overview and detail views.
Supports single-match and multi-match CSVs, with optional player filtering.
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.zone_passing_chart import (
    load_zone_passes,
    aggregate_zone_passes,
    compute_zone_summary,
    create_zone_overview_chart,
    create_zone_detail_chart,
    ZONE_NAMES,
)
from mostly_finished_charts.passing_flow_chart import (
    ZONES, extract_match_info, _short_zone_label,
)
from shared.styles import BG_COLOR
from shared.colors import TEAM_COLORS, fuzzy_match_team

st.set_page_config(page_title="Zone Passing", page_icon="", layout="wide")


def _count_matches(df):
    """Count unique matches in the DataFrame."""
    if 'gameId' in df.columns:
        return df['gameId'].nunique()
    elif 'Date' in df.columns:
        return df['Date'].nunique()
    return 1


def _get_player_names(df, team_name):
    """Extract unique player names from pass events for a team."""
    pass_types = {'Pass', 'BlockedPass', 'OffsidePass'}
    if 'playType' not in df.columns or 'Team' not in df.columns:
        return []
    mask = (df['playType'].isin(pass_types)) & (df['Team'] == team_name)
    team_passes = df[mask]
    # Check columns in order: passer (most common for passes), then shooter, player
    for col in ['passer', 'shooter', 'player']:
        if col in team_passes.columns:
            names = team_passes[col].dropna().unique().tolist()
            if names:
                return sorted(names)
    return []


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


_CACHE_VERSION = 6  # bump to invalidate cached results after logic changes


@st.cache_data
def _load_and_aggregate(file_content, team_name, player_name=None,
                        _version=_CACHE_VERSION):
    """Cache pass loading and aggregation."""
    df = _read_csv_cached(file_content)
    pass_df = load_zone_passes(df, team_name, player_name=player_name)
    if pass_df.empty:
        return pass_df, pd.DataFrame()
    zone_agg_df = aggregate_zone_passes(pass_df)
    return pass_df, zone_agg_df


def _render_chart_with_download(fig, filename):
    """Display a chart and provide a download button."""
    st.pyplot(fig)

    with tempfile.TemporaryDirectory() as tmp_dir:
        filepath = os.path.join(tmp_dir, filename)
        fig.savefig(filepath, dpi=300, bbox_inches='tight',
                    facecolor=BG_COLOR, edgecolor='none')
        plt.close(fig)

        with open(filepath, "rb") as f:
            st.download_button(
                label=f"Download {filename}",
                data=f.read(),
                file_name=filename,
                mime="image/png",
            )


# -- Zone Grid Layout ---------------------------------------------------------
# Pitch zones laid out as 3 rows x 5 columns matching the pitch orientation.
# Row 0 = Left side (top of pitch), Row 2 = Right side (bottom of pitch).

ZONE_GRID = [
    # Row 0: Left/Wing Left zones (top = left side of pitch)
    ['Def Wing Left', 'Def Left', 'Mid Def Left', 'Mid Att Left', 'Att Left', 'Att Wing Left'],
    # Row 1: Center/PA zones
    ['Def Penalty Area', 'Def Center', 'Mid Def Center', 'Mid Att Center', 'Att Center', 'Att Penalty Area'],
    # Row 2: Right/Wing Right zones (bottom = right side of pitch)
    ['Def Wing Right', 'Def Right', 'Mid Def Right', 'Mid Att Right', 'Att Right', 'Att Wing Right'],
]


st.title("Zone Passing")
st.markdown("Analyze where passes go from each pitch zone.")

# Sidebar controls
st.sidebar.header("Settings")
competition = st.sidebar.text_input(
    "Competition Name",
    value="",
    help="e.g., Premier League, Serie A (optional)"
)

min_per_game = st.sidebar.slider(
    "Min passes/game for detail arrows",
    min_value=0.0, max_value=5.0, value=0.5, step=0.5,
    help="Hide arrows below this threshold in zone detail view"
)

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Event Log CSV",
    type=["csv"],
    help="Single-match or multi-match event log with pass data (needs playType, Team, EventX, EventY, PassEndX, PassEndY)"
)

if uploaded_file is not None:
    file_content = uploaded_file.getvalue()

    try:
        raw_df = _read_csv_cached(file_content)

        # Detect match count
        num_matches = _count_matches(raw_df)

        # Detect teams
        teams = raw_df['Team'].dropna().unique().tolist()
        if not teams:
            st.error("No teams found in the CSV (missing 'Team' column).")
        else:
            selected_team = st.selectbox(
                "Select Team",
                options=teams,
                help="Choose which team's zone passing to visualize"
            )

            # Player selector
            player_names = _get_player_names(raw_df, selected_team)
            if len(player_names) == 1:
                # Single-player CSV: auto-select, no dropdown
                player_name = player_names[0]
                st.markdown(f"**Player:** {player_name}")
            elif len(player_names) > 1:
                player_options = ["All Players"] + player_names
                selected_player_option = st.selectbox(
                    "Select Player",
                    options=player_options,
                    help="Filter to a specific player's passes, or view all"
                )
                player_name = None if selected_player_option == "All Players" else selected_player_option
            else:
                player_name = None

            # Match info (only meaningful for single-match)
            match_info = extract_match_info(raw_df, selected_team)
            if num_matches > 1:
                # For multi-match, clear single-match fields that don't apply
                match_info = {'opponent': '', 'date': '', 'score': ''}

            if num_matches == 1:
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
            else:
                if player_name:
                    st.markdown(f"**{num_matches} matches** - showing totals")
                else:
                    st.markdown(f"**{num_matches} matches** - showing per-game averages")

            # Resolve team color
            db_color, _, _ = fuzzy_match_team(selected_team, TEAM_COLORS)
            if not db_color and 'newestTeamColor' in raw_df.columns:
                team_rows = raw_df[raw_df['Team'] == selected_team]
                csv_color = team_rows['newestTeamColor'].dropna()
                db_color = csv_color.iloc[0] if not csv_color.empty else None
            team_color = db_color or '#6CABDD'

            # Generate button - stores results in session state
            if st.button("Generate Zone Passing Charts", type="primary"):
                with st.spinner("Loading pass data..."):
                    pass_df, zone_agg_df = _load_and_aggregate(
                        file_content, selected_team, player_name=player_name)

                if pass_df.empty:
                    st.error("No pass data found. Ensure the CSV has playType, Team, EventX/Y, PassEndX/Y columns.")
                else:
                    st.session_state.zp_generated = True
                    st.session_state.zp_team = selected_team
                    st.session_state.zp_team_color = team_color
                    st.session_state.zp_file_content = file_content
                    st.session_state.zp_match_info = match_info
                    # Teams use per-game averages; players show totals
                    st.session_state.zp_num_matches = num_matches if not player_name else 1
                    st.session_state.zp_player_name = player_name
                    st.session_state.zp_selected_zone = None
                    st.rerun()

            # Display charts if data has been generated
            if (st.session_state.get('zp_generated')
                    and st.session_state.get('zp_team') == selected_team
                    and st.session_state.get('zp_file_content') == file_content
                    and st.session_state.get('zp_player_name') == player_name):

                stored_num_matches = st.session_state.get('zp_num_matches', 1)
                pass_df, zone_agg_df = _load_and_aggregate(
                    file_content, selected_team, player_name=player_name)
                stored_match_info = st.session_state.zp_match_info
                stored_color = st.session_state.zp_team_color
                stored_player = st.session_state.get('zp_player_name')

                total_passes = len(pass_df)
                completed = int(pass_df['completed'].sum())
                comp_pct = completed / total_passes * 100

                display_label = stored_player if stored_player else selected_team
                st.markdown(f"**{display_label}:** {total_passes} passes | {completed} completed ({comp_pct:.1f}%)")

                # Overview chart
                st.subheader("Zone Overview")
                overview_fig = create_zone_overview_chart(
                    pass_df, zone_agg_df, selected_team, stored_color,
                    stored_match_info, num_matches=stored_num_matches,
                    player_name=stored_player, competition=competition,
                )

                safe_name = (stored_player or selected_team).replace(' ', '_')
                _render_chart_with_download(
                    overview_fig,
                    f"zone_passing_overview_{safe_name}.png"
                )

                # Zone selector grid
                st.subheader("Zone Detail")
                st.markdown("Select a zone to see where passes go from that zone:")

                zone_summary = compute_zone_summary(zone_agg_df,
                                                    num_matches=stored_num_matches)

                # Draw 3-row x 5-column grid of zone buttons
                for row_idx, row_zones in enumerate(ZONE_GRID):
                    cols = st.columns(6)
                    for col_idx, zone_name in enumerate(row_zones):
                        stats = zone_summary.get(zone_name, {})
                        total = stats.get('total_passes', 0)
                        pct = stats.get('completion_pct', 0)
                        label = _short_zone_label(zone_name)

                        with cols[col_idx]:
                            btn_label = f"{label}\n{total} ({pct}%)"
                            if st.button(btn_label, key=f"zone_{zone_name}",
                                         use_container_width=True):
                                st.session_state.zp_selected_zone = zone_name

                # Show detail chart for selected zone
                sel_zone = st.session_state.get('zp_selected_zone')
                if sel_zone:
                    st.markdown(f"**Passes from: {sel_zone}**")

                    detail_fig = create_zone_detail_chart(
                        pass_df, zone_agg_df, sel_zone, selected_team,
                        stored_color, stored_match_info,
                        num_matches=stored_num_matches,
                        player_name=stored_player,
                        competition=competition,
                        min_per_game=min_per_game,
                    )

                    zone_safe = sel_zone.replace(' ', '_')
                    detail_safe = (stored_player or selected_team).replace(' ', '_')
                    _render_chart_with_download(
                        detail_fig,
                        f"zone_passing_{zone_safe}_{detail_safe}.png"
                    )

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

else:
    # Clear state when file is removed
    st.session_state.pop('zp_generated', None)
    st.session_state.pop('zp_selected_zone', None)

    st.info("Upload a TruMedia Event Log CSV to get started.")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required columns:**
        - `Team` - Team name
        - `playType` - Event type (must include 'Pass')
        - `EventX`, `EventY` - Source coordinates (0-100 scale)
        - `PassEndX`, `PassEndY` - Pass destination coordinates

        **Optional columns:**
        - `receiver` - Receiver name (for completion detection)
        - `homeTeam`, `awayTeam` - For match info display
        - `Date` - Match date
        - `gameId` - Game identifier (for multi-match detection)
        - `homeFinalScore`, `awayFinalScore` - Final score
        - `newestTeamColor` - Team color from CSV
        - `shooter` / `player` - Player name (for player filtering)
        """)
