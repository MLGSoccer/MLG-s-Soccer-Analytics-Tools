"""
Shot Chart - Streamlit Page
Supports single-match and multi-match (season) views from database or CSV upload.
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.shot_chart import (
    load_shot_data,
    load_multi_match_shot_data,
    detect_csv_mode,
    create_team_shot_chart,
    create_combined_shot_chart,
    create_multi_match_shot_chart,
    SHOT_TYPES,
    GOAL_TYPES,
    HIGHLIGHT_CATEGORIES,
    ensure_pitch_contrast
)
from shared.styles import BG_COLOR
from shared.motherduck import (
    get_teams_by_league, get_games_for_team,
    build_shot_chart_single, build_shot_chart_multi, build_shots_for_player,
    get_player_game_count, get_player_total_minutes,
)
from pages.streamlit_utils import custom_title_inputs
import matplotlib.pyplot as plt

st.set_page_config(page_title="Shot Chart", page_icon="🎯", layout="wide")


# ── Cache helpers (CSV upload) ────────────────────────────────────────────────

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


@st.cache_data
def _load_single_match(file_content, exclude_penalties):
    """Cache single-match shot data loading."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return load_shot_data(tmp_path, exclude_penalties=exclude_penalties)
    finally:
        os.unlink(tmp_path)


@st.cache_data
def _load_multi_match(file_content, exclude_penalties):
    """Cache multi-match shot data loading."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return load_multi_match_shot_data(tmp_path, exclude_penalties=exclude_penalties)
    finally:
        os.unlink(tmp_path)


# ── Chart generation helpers ──────────────────────────────────────────────────

def _generate_single_match_charts(shots_df, match_info, team_colors, chart_options,
                                   team1_name, team2_name, team1_player, team2_player,
                                   competition, exclude_penalties, highlight_mode,
                                   custom_title=None, custom_subtitle=None):
    """Generate single-match shot charts and return image bytes dict."""
    from shared.colors import TEAM_COLORS, fuzzy_match_team

    def resolve_color(team_name, team_colors_dict):
        if team_name in team_colors_dict:
            return team_colors_dict[team_name]
        for csv_team, color in team_colors_dict.items():
            if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                return color
        color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        return color if color else '#888888'

    team1_color = ensure_pitch_contrast(resolve_color(team1_name, team_colors))
    team2_color = ensure_pitch_contrast(resolve_color(team2_name, team_colors))

    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50
    team1_flip = team1_avg_x < 50
    team2_flip = team2_avg_x < 50

    team1_final_score = match_info.get('home_score', 0)
    team2_final_score = match_info.get('away_score', 0)

    team1_shot_goals = len(team1_shots[team1_shots['playType'].isin(GOAL_TYPES)])
    team2_shot_goals = len(team2_shots[team2_shots['playType'].isin(GOAL_TYPES)])
    team1_own_goals = max(0, team1_final_score - team1_shot_goals)
    team2_own_goals = max(0, team2_final_score - team2_shot_goals)

    shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
    player1_name = None
    chart_team1_shots = team1_shots
    if team1_player != "All Players":
        chart_team1_shots = team1_shots[team1_shots[shooter_col] == team1_player]
        player1_name = team1_player

    player2_name = None
    chart_team2_shots = team2_shots
    if team2_player != "All Players":
        chart_team2_shots = team2_shots[team2_shots[shooter_col] == team2_player]
        player2_name = team2_player

    charts = {}

    with tempfile.TemporaryDirectory() as tmp_dir:
        if "Individual Team Charts" in chart_options:
            fig1 = create_team_shot_chart(
                chart_team1_shots, team1_name, team1_color, match_info,
                team2_name, team_final_score=team1_final_score,
                opponent_goals=team2_final_score,
                own_goals_for=team1_own_goals,
                flip_coords=team1_flip, competition=competition,
                exclude_penalties=exclude_penalties,
                highlight_mode=highlight_mode,
                player_name=player1_name,
                custom_title=custom_title, custom_subtitle=custom_subtitle
            )
            caption1 = f"{player1_name} ({team1_name}) Shot Chart" if player1_name else f"{team1_name} Shot Chart"
            fname1 = f"shot_chart_{(player1_name or team1_name).replace(' ', '_').replace('/', '-')}.png"
            path1 = os.path.join(tmp_dir, fname1)
            fig1.savefig(path1, dpi=300, bbox_inches='tight', facecolor=BG_COLOR, edgecolor='none')
            plt.close(fig1)
            with open(path1, "rb") as f:
                charts[fname1] = (caption1, f.read())

            fig2 = create_team_shot_chart(
                chart_team2_shots, team2_name, team2_color, match_info,
                team1_name, team_final_score=team2_final_score,
                opponent_goals=team1_final_score,
                own_goals_for=team2_own_goals,
                flip_coords=team2_flip, competition=competition,
                exclude_penalties=exclude_penalties,
                highlight_mode=highlight_mode,
                player_name=player2_name,
                is_home=False,
                custom_title=custom_title, custom_subtitle=custom_subtitle
            )
            caption2 = f"{player2_name} ({team2_name}) Shot Chart" if player2_name else f"{team2_name} Shot Chart"
            fname2 = f"shot_chart_{(player2_name or team2_name).replace(' ', '_').replace('/', '-')}.png"
            path2 = os.path.join(tmp_dir, fname2)
            fig2.savefig(path2, dpi=300, bbox_inches='tight', facecolor=BG_COLOR, edgecolor='none')
            plt.close(fig2)
            with open(path2, "rb") as f:
                charts[fname2] = (caption2, f.read())

        if "Combined Chart" in chart_options:
            fig_combined = create_combined_shot_chart(
                shots_df, team1_name, team1_color, team1_flip,
                team2_name, team2_color, team2_flip,
                match_info, competition=competition,
                exclude_penalties=exclude_penalties,
                highlight_mode=highlight_mode,
                custom_title=custom_title, custom_subtitle=custom_subtitle
            )
            fname_combined = f"shot_chart_{team1_name.replace(' ', '_').replace('/', '-')}_vs_{team2_name.replace(' ', '_').replace('/', '-')}.png"
            path_combined = os.path.join(tmp_dir, "shot_chart_combined.png")
            fig_combined.savefig(path_combined, dpi=300, bbox_inches='tight', facecolor=BG_COLOR, edgecolor='none')
            plt.close(fig_combined)
            with open(path_combined, "rb") as f:
                charts[fname_combined] = ("Combined Shot Chart", f.read())

    return charts


def _generate_multi_match_chart(chart_shots, team_name, team_color, chart_info,
                                 competition, selected_player, exclude_penalties,
                                 highlight_mode, shots_against=False,
                                 custom_title=None, custom_subtitle=None,
                                 minutes=None):
    """Generate multi-match shot chart and return (img_bytes, filename, caption)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        fig = create_multi_match_shot_chart(
            chart_shots, team_name, team_color, chart_info,
            competition=competition, player_name=selected_player,
            exclude_penalties=exclude_penalties,
            highlight_mode=highlight_mode,
            shots_against=shots_against,
            custom_title=custom_title, custom_subtitle=custom_subtitle,
            minutes=minutes
        )

        name_part = team_name.replace(' ', '_').replace('/', '-')
        if selected_player:
            name_part = f"{selected_player.replace(' ', '_').replace('/', '-')}_{name_part}"
        suffix = "_against" if shots_against else ""
        filename = f"shot_map_{name_part}{suffix}_season.png"

        path = os.path.join(tmp_dir, filename)
        fig.savefig(path, dpi=300, bbox_inches='tight', facecolor=BG_COLOR, edgecolor='none')
        plt.close(fig)

        if shots_against:
            caption = f"{selected_player} Shots Against {team_name}" if selected_player else f"{team_name} Shots Against Map"
        else:
            caption = f"{selected_player} Shot Map" if selected_player else f"{team_name} Shot Map"
        with open(path, "rb") as f:
            return f.read(), filename, caption


def _display_charts(charts_dict, key_prefix=""):
    """Render image + download button for each chart in dict."""
    for filename, (caption, img_bytes) in charts_dict.items():
        st.image(img_bytes, caption=caption)
        st.download_button(
            label=f"Download {caption}",
            data=img_bytes,
            file_name=filename,
            mime="image/png",
            key=f"{key_prefix}_{filename}"
        )
        st.markdown("---")


# ── Page ──────────────────────────────────────────────────────────────────────

st.title("Shot Chart")
st.markdown("Visualize shot locations for a single match or full season.")

data_source = st.radio(
    "Data source",
    options=["Database", "Upload CSV"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

# ── DATABASE MODE ─────────────────────────────────────────────────────────────
if data_source == "Database":
    st.sidebar.header("Settings")
    exclude_penalties = st.sidebar.checkbox(
        "Exclude Penalties", value=False,
        help="Filter out penalty shots from the chart"
    )

    try:
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
        mode = st.selectbox(
            "Mode",
            options=["Single Match", "Season"],
            disabled=not selected_team,
        )

    if selected_team:
        with st.spinner("Loading games..."):
            games = get_games_for_team(selected_team['team_id'])

        if not games:
            st.warning("No games found for this team.")
            st.stop()

        # ── SINGLE MATCH ──────────────────────────────────────────────────────
        if mode == "Single Match":
            game_labels = [g['label'] for g in games]
            selected_game_label = st.selectbox("Game", options=[""] + game_labels)
            selected_game = next(
                (g for g in games if g['label'] == selected_game_label), None
            )

            competition = st.text_input(
                "Competition Name",
                value=selected_game['season_name'] if selected_game and selected_game.get('season_name') else selected_league or "",
                help="Auto-filled from season — edit if needed"
            )

            st.sidebar.header("Chart Output")
            chart_options = st.sidebar.multiselect(
                "Charts to generate",
                options=["Individual Team Charts", "Combined Chart"],
                default=["Individual Team Charts", "Combined Chart"],
            )

            highlight_mode = st.sidebar.radio(
                "Highlight Shot Type",
                options=["All", "Open Play", "Set Piece"],
                index=0,
            )
            _dt_dbs = (f"{selected_game['home_team'].upper()} "
                       f"{selected_game['home_score']}-{selected_game['away_score']} "
                       f"{selected_game['away_team'].upper()}"
                       ) if selected_game else ""
            custom_title_db_single, custom_subtitle_db_single = custom_title_inputs("db_single", _dt_dbs)

            if selected_game:
                with st.spinner("Loading shot data..."):
                    shots_df, match_info, team_colors = build_shot_chart_single(
                        selected_game['game_id']
                    )

                if shots_df.empty:
                    st.warning("No shot data found for this game.")
                else:
                    teams = shots_df['Team'].unique().tolist()
                    home_team = match_info.get('home_team', teams[0] if teams else 'Home')
                    away_team = match_info.get('away_team', teams[1] if len(teams) > 1 else 'Away')

                    def _match_name(target, team_list):
                        for t in team_list:
                            if target.lower() in t.lower() or t.lower() in target.lower():
                                return t
                        return team_list[0] if team_list else target

                    team1_name = _match_name(home_team, teams)
                    team2_name = _match_name(away_team, [t for t in teams if t != team1_name])

                    st.success(
                        f"**{team1_name}** {match_info.get('home_score', 0)}–"
                        f"{match_info.get('away_score', 0)} **{team2_name}**"
                        f"  —  {match_info.get('date_formatted', '')}"
                    )

                    from pages.streamlit_utils import check_team_colors
                    check_team_colors([team1_name, team2_name], team_colors)

                    # Player filters
                    team1_players = sorted(
                        shots_df[shots_df['Team'] == team1_name]['shooter'].dropna().unique()
                    )
                    team2_players = sorted(
                        shots_df[shots_df['Team'] == team2_name]['shooter'].dropna().unique()
                    )
                    if team1_players or team2_players:
                        st.sidebar.header("Player Filter")
                        team1_player = st.sidebar.selectbox(
                            f"{team1_name}", ["All Players"] + list(team1_players),
                            key="db_single_p1"
                        )
                        team2_player = st.sidebar.selectbox(
                            f"{team2_name}", ["All Players"] + list(team2_players),
                            key="db_single_p2"
                        )
                    else:
                        team1_player = team2_player = "All Players"

                    if st.button("Generate Charts", type="primary", key="db_single_gen"):
                        st.session_state["shot_charts"] = None
                        with st.spinner("Generating shot charts..."):
                            charts = _generate_single_match_charts(
                                shots_df, match_info, team_colors, chart_options,
                                team1_name, team2_name, team1_player, team2_player,
                                competition, exclude_penalties, highlight_mode,
                                custom_title=custom_title_db_single,
                                custom_subtitle=custom_subtitle_db_single
                            )
                            st.session_state["shot_charts"] = charts

                    if st.session_state.get("shot_charts"):
                        _display_charts(st.session_state["shot_charts"], key_prefix="db_single")

        # ── SEASON ────────────────────────────────────────────────────────────
        else:
            # Competition filter
            unique_seasons = {}
            for g in games:
                if g.get('season_id') and g.get('season_name'):
                    unique_seasons[g['season_id']] = g['season_name']

            season_filter_col, comp_col = st.columns([1, 2])

            with season_filter_col:
                if unique_seasons:
                    season_display_options = ["All competitions"] + list(unique_seasons.values())
                    selected_season_filter = st.selectbox("Competition filter", options=season_display_options)
                    selected_season_id = None
                    if selected_season_filter != "All competitions":
                        selected_season_id = next(
                            (k for k, v in unique_seasons.items() if v == selected_season_filter), None
                        )
                else:
                    selected_season_filter = "All competitions"
                    selected_season_id = None

            # Filter game list
            filtered_games = (
                [g for g in games if g.get('season_id') == selected_season_id]
                if selected_season_id else games
            )

            with comp_col:
                comp_default = (
                    selected_season_filter if selected_season_filter != "All competitions"
                    else selected_league or ""
                )
                competition = st.text_input(
                    "Competition Name",
                    value=comp_default,
                    help="Shown on the chart — edit if needed"
                )

            # Game multiselect
            if filtered_games:
                game_labels = [g['label'] for g in filtered_games]
                selected_labels = st.multiselect(
                    f"Games  ({len(filtered_games)} available)",
                    options=game_labels,
                    default=game_labels,
                    help="All games pre-selected. Deselect to exclude specific games."
                )
                selected_game_ids = tuple(
                    g['game_id'] for g in filtered_games if g['label'] in selected_labels
                )
            else:
                st.warning("No games found for this selection.")
                selected_game_ids = ()

            # Sidebar controls
            shot_direction = st.sidebar.radio(
                "Shot Direction",
                options=["Shots For", "Shots Against"],
                index=0,
                help="'Shots For' shows the selected team's own shots. 'Shots Against' shows opponent shots in those games."
            )
            shots_against = shot_direction == "Shots Against"

            highlight_mode = st.sidebar.radio(
                "Highlight Shot Type",
                options=["All", "Open Play", "Set Piece"],
                index=0,
            )
            _dt_dbss = selected_team['display_name'].upper() if selected_team else ""
            custom_title_db_season, custom_subtitle_db_season = custom_title_inputs("db_season", _dt_dbss)

            if selected_game_ids:
                with st.spinner("Loading shot data..."):
                    shots_df, multi_match_info, team_color_raw = build_shot_chart_multi(
                        selected_game_ids, selected_team['team_id'], against=shots_against
                    )

                if shots_df is None or shots_df.empty:
                    st.warning("No shot data found for selected games.")
                else:
                    team_name = multi_match_info['team_name']
                    total_matches = multi_match_info['total_matches']
                    player_list = multi_match_info['player_list']
                    date_range = multi_match_info.get('date_range', '')

                    from shared.colors import TEAM_COLORS, fuzzy_match_team
                    color_db, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
                    if color_db:
                        team_color = ensure_pitch_contrast(color_db)
                    elif team_color_raw and team_color_raw != '#888888':
                        team_color = ensure_pitch_contrast(team_color_raw)
                    else:
                        team_color = '#888888'

                    from pages.streamlit_utils import check_team_colors
                    check_team_colors(
                        [team_name],
                        {team_name: team_color_raw} if team_color_raw != '#888888' else {}
                    )

                    label = (
                        f"**{team_name}** — {len(shots_df)} shots faced across {total_matches} matches"
                        if shots_against else
                        f"**{team_name}** — {len(shots_df)} shots across {total_matches} matches"
                    )
                    st.success(label)

                    total_xg = shots_df['xG'].sum()
                    total_goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Matches", total_matches)
                    c2.metric("Shots", len(shots_df))
                    c3.metric("xG", f"{total_xg:.1f}")
                    c4.metric("Goals", total_goals)

                    if date_range:
                        st.caption(f"Date range: {date_range}")

                    # Player filter
                    selected_player = None
                    selected_player_team = None  # only set in Shots Against mode

                    if shots_against:
                        # Build (shooter, Team) pairs so players who moved between two
                        # opponents during the season appear as separate entries.
                        player_team_df = (
                            shots_df.dropna(subset=['shooter', 'Team'])[['shooter', 'Team']]
                            .drop_duplicates()
                            .sort_values(['shooter', 'Team'])
                        )
                        against_options = [
                            f"{r['shooter']} ({r['Team']})"
                            for _, r in player_team_df.iterrows()
                        ]
                        player_filter = st.selectbox(
                            "Filter by Opponent Player",
                            options=["All Players"] + against_options,
                        )
                        if player_filter != "All Players":
                            matched = player_team_df[
                                player_team_df.apply(
                                    lambda r: f"{r['shooter']} ({r['Team']})" == player_filter,
                                    axis=1
                                )
                            ]
                            if not matched.empty:
                                selected_player = matched.iloc[0]['shooter']
                                selected_player_team = matched.iloc[0]['Team']
                    else:
                        player_filter = st.selectbox(
                            "Filter by Player",
                            options=["All Players"] + player_list,
                        )
                        selected_player = None if player_filter == "All Players" else player_filter

                    # For Shots For: load all shots for this player across all teams
                    player_full_shots = None
                    player_full_info = None
                    if selected_player and not shots_against:
                        player_full_shots, player_full_info, _ = build_shots_for_player(selected_player)
                        if player_full_shots is not None and not player_full_shots.empty:
                            player_teams = sorted(player_full_shots['Team'].dropna().unique().tolist())
                            if len(player_teams) > 1:
                                st.info(
                                    f"**{selected_player}** has shots for multiple teams this season: "
                                    f"{', '.join(player_teams)}. Showing all."
                                )

                    p_minutes = None  # total minutes played; populated below if player selected + data available

                    if selected_player:
                        if player_full_shots is not None and not player_full_shots.empty:
                            player_shots = player_full_shots
                        else:
                            player_shots = shots_df[shots_df['shooter'] == selected_player]
                            if selected_player_team:
                                player_shots = player_shots[player_shots['Team'] == selected_player_team]
                        if shots_against:
                            p_matches = player_shots['_match_id'].nunique()
                        else:
                            p_matches = get_player_game_count(selected_player) or player_shots['_match_id'].nunique()
                        p_shots = len(player_shots)
                        p_xg = player_shots['xG'].sum()
                        p_goals = len(player_shots[player_shots['playType'].isin(GOAL_TYPES)])

                        # Try per-90 stats from player_minutes table (Shots For mode only)
                        p_minutes = None
                        if not shots_against:
                            try:
                                p_game_ids = tuple(player_shots['_match_id'].dropna().unique().tolist())
                                p_minutes = get_player_total_minutes(selected_player, p_game_ids)
                            except Exception:
                                pass

                        if p_minutes:
                            pc1, pc2, pc3, pc4, pc5 = st.columns(5)
                            pc1.metric("Matches", p_matches)
                            pc2.metric("Minutes", p_minutes)
                            pc3.metric("Shots/90", f"{p_shots / p_minutes * 90:.2f}")
                            pc4.metric("xG/90", f"{p_xg / p_minutes * 90:.2f}")
                            pc5.metric("Goals/90", f"{p_goals / p_minutes * 90:.2f}")
                        else:
                            pc1, pc2, pc3, pc4 = st.columns(4)
                            pc1.metric("Matches", p_matches)
                            pc2.metric("Shots", p_shots)
                            pc3.metric("xG", f"{p_xg:.2f}")
                            pc4.metric("Goals", p_goals)

                    if st.button("Generate Shot Map", type="primary", key="db_season_gen"):
                        st.session_state["multi_shot_chart"] = None
                        with st.spinner("Generating shot map..."):
                            chart_info = dict(multi_match_info)

                            if selected_player and player_full_shots is not None and not player_full_shots.empty:
                                # Use the full cross-team data for this player
                                chart_shots = player_full_shots.copy()
                                chart_info = dict(player_full_info)
                            elif selected_player:
                                chart_shots = shots_df[shots_df['shooter'] == selected_player].copy()
                                if selected_player_team:
                                    chart_shots = chart_shots[chart_shots['Team'] == selected_player_team].copy()
                                chart_info['total_matches'] = chart_shots['_match_id'].nunique()
                            else:
                                chart_shots = shots_df

                            if chart_shots.empty:
                                st.error(f"No shots found for {selected_player or team_name}")
                            else:
                                img_bytes, filename, caption = _generate_multi_match_chart(
                                    chart_shots, team_name, team_color, chart_info,
                                    competition, selected_player, exclude_penalties,
                                    highlight_mode, shots_against=shots_against,
                                    custom_title=custom_title_db_season,
                                    custom_subtitle=custom_subtitle_db_season,
                                    minutes=p_minutes if selected_player else None
                                )
                                st.session_state["multi_shot_chart"] = {
                                    "img": img_bytes, "filename": filename, "caption": caption,
                                }

                    if st.session_state.get("multi_shot_chart"):
                        chart = st.session_state["multi_shot_chart"]
                        st.image(chart["img"], caption=chart["caption"])
                        st.download_button(
                            label="Download Shot Map",
                            data=chart["img"],
                            file_name=chart["filename"],
                            mime="image/png",
                            key="db_season_dl"
                        )


# ── CSV UPLOAD MODE ───────────────────────────────────────────────────────────
else:
    st.sidebar.header("Settings")
    competition = st.sidebar.text_input(
        "Competition Name",
        value="",
        help="e.g., Premier League, Serie A (optional)"
    )
    if competition:
        st.sidebar.success(f"Competition: {competition}")

    exclude_penalties = st.sidebar.checkbox(
        "Exclude Penalties",
        value=False,
        help="Filter out penalty shots from the chart"
    )

    shot_direction = st.sidebar.radio(
        "Shot Direction",
        options=["Shots For", "Shots Against"],
        index=0,
        help="'Shots For' shows the team's own shots. 'Shots Against' is for CSVs containing opponent shots conceded by the team. (Multi-match mode only)"
    )
    shots_against = shot_direction == "Shots Against"

    highlight_mode = 'All'

    uploaded_file = st.file_uploader(
        "Upload TruMedia Event Log CSV",
        type=["csv"],
        help="Single-match or season-long event log with shot data"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()

        try:
            raw_df = _read_csv_cached(file_content)
            detected_mode = detect_csv_mode(raw_df)

            if 'ShotPlayStyle' in raw_df.columns:
                highlight_mode = st.sidebar.radio(
                    "Highlight Shot Type",
                    options=["All", "Open Play", "Set Piece"],
                    index=0,
                    help="Highlight a specific shot type; other shots appear muted"
                )
            else:
                highlight_mode = 'All'

            mode = st.radio(
                "Chart Mode",
                options=["Single Match", "Multi-Match (Season)"],
                index=0 if detected_mode == 'single' else 1,
                horizontal=True,
                help="Auto-detected based on your CSV. Override if needed."
            )

            if mode == "Single Match":
                st.sidebar.header("Chart Output")
                chart_options = st.sidebar.multiselect(
                    "Charts to generate",
                    options=["Individual Team Charts", "Combined Chart"],
                    default=["Individual Team Charts", "Combined Chart"],
                    help="Select which chart types to generate"
                )
                with st.spinner("Parsing match data..."):
                    shots_df, match_info, team_colors = _load_single_match(file_content, exclude_penalties)

                if shots_df.empty:
                    st.error("No shot data found in CSV.")
                else:
                    teams = shots_df['Team'].unique().tolist()
                    home_team = match_info.get('home_team', teams[0] if teams else 'Home')
                    away_team = match_info.get('away_team', teams[1] if len(teams) > 1 else 'Away')

                    def match_team_name(target, team_list):
                        for t in team_list:
                            if target.lower() in t.lower() or t.lower() in target.lower():
                                return t
                        return team_list[0] if team_list else target

                    team1_name = match_team_name(home_team, teams)
                    team2_name = match_team_name(away_team, [t for t in teams if t != team1_name])

                    _dt_csvs = (f"{team1_name.upper()} "
                                f"{match_info.get('home_score', 0)}-{match_info.get('away_score', 0)} "
                                f"{team2_name.upper()}")
                    custom_title_csv_single, custom_subtitle_csv_single = custom_title_inputs("csv_single", _dt_csvs)

                    st.success(f"Found {len(shots_df)} shots: **{team1_name}** vs **{team2_name}**")

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Date", match_info.get('date', 'Unknown'))
                    col2.metric(team1_name, f"{match_info.get('home_score', 0)} goals")
                    col3.metric(team2_name, f"{match_info.get('away_score', 0)} goals")

                    from pages.streamlit_utils import check_team_colors
                    check_team_colors([team1_name, team2_name], team_colors)

                    shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
                    if shooter_col in shots_df.columns:
                        team1_players = sorted(shots_df[shots_df['Team'] == team1_name][shooter_col].dropna().unique())
                        team2_players = sorted(shots_df[shots_df['Team'] == team2_name][shooter_col].dropna().unique())

                        if team1_players or team2_players:
                            st.sidebar.header("Player Filter")
                            team1_player = st.sidebar.selectbox(
                                f"{team1_name} Player",
                                ["All Players"] + team1_players,
                                key="csv_single_team1_player"
                            )
                            team2_player = st.sidebar.selectbox(
                                f"{team2_name} Player",
                                ["All Players"] + team2_players,
                                key="csv_single_team2_player"
                            )
                        else:
                            team1_player = "All Players"
                            team2_player = "All Players"
                    else:
                        team1_player = "All Players"
                        team2_player = "All Players"

                    if st.button("Generate Charts", type="primary", key="csv_single_gen"):
                        st.session_state["shot_charts"] = None
                        with st.spinner("Generating shot charts..."):
                            charts = _generate_single_match_charts(
                                shots_df, match_info, team_colors, chart_options,
                                team1_name, team2_name, team1_player, team2_player,
                                competition, exclude_penalties, highlight_mode,
                                custom_title=custom_title_csv_single,
                                custom_subtitle=custom_subtitle_csv_single
                            )
                            st.session_state["shot_charts"] = charts

                    if st.session_state.get("shot_charts"):
                        _display_charts(st.session_state["shot_charts"], key_prefix="csv_single")
                        st.success(f"Generated {len(st.session_state['shot_charts'])} chart(s)!")

            else:
                # ── MULTI-MATCH CSV MODE ───────────────────────────────────
                with st.spinner("Parsing season data..."):
                    shots_df, multi_match_info, team_color_raw = _load_multi_match(file_content, exclude_penalties)

                if shots_df.empty:
                    st.error("No shot data found in CSV.")
                else:
                    team_name = multi_match_info['team_name']
                    custom_title_csv_multi, custom_subtitle_csv_multi = custom_title_inputs("csv_multi", team_name.upper())
                    total_matches = multi_match_info['total_matches']
                    player_list = multi_match_info['player_list']
                    date_range = multi_match_info.get('date_range', '')

                    from shared.colors import TEAM_COLORS, fuzzy_match_team
                    color_db, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
                    if color_db:
                        team_color = ensure_pitch_contrast(color_db)
                    elif team_color_raw and team_color_raw != '#888888':
                        team_color = ensure_pitch_contrast(team_color_raw)
                    else:
                        team_color = '#888888'

                    from pages.streamlit_utils import check_team_colors
                    check_team_colors([team_name], {team_name: team_color_raw} if team_color_raw != '#888888' else {})

                    is_player_csv = multi_match_info.get('is_player_csv', False)
                    auto_player = multi_match_info.get('player_name')

                    if is_player_csv:
                        st.success(f"**{auto_player}** ({team_name}) - {len(shots_df)} shots across {total_matches} matches")
                    else:
                        st.success(f"**{team_name}** - {len(shots_df)} shots across {total_matches} matches")

                    total_xg = shots_df['xG'].sum()
                    total_goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Matches", total_matches)
                    col2.metric("Shots", len(shots_df))
                    col3.metric("xG", f"{total_xg:.1f}")
                    col4.metric("Goals", total_goals)

                    if date_range:
                        st.caption(f"Date range: {date_range}")

                    if is_player_csv:
                        selected_player = auto_player
                    else:
                        player_label = "Filter by Opponent Player" if shots_against else "Filter by Player"
                        player_filter = st.selectbox(
                            player_label,
                            options=["All Players"] + player_list,
                            index=0,
                        )
                        selected_player = None if player_filter == "All Players" else player_filter

                        if selected_player:
                            shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
                            player_shots = shots_df[shots_df[shooter_col] == selected_player]
                            p_matches = player_shots['_match_id'].nunique()
                            p_shots = len(player_shots)
                            p_xg = player_shots['xG'].sum()
                            p_goals = len(player_shots[player_shots['playType'].isin(GOAL_TYPES)])

                            pc1, pc2, pc3, pc4 = st.columns(4)
                            pc1.metric("Matches", p_matches)
                            pc2.metric("Shots", p_shots)
                            pc3.metric("xG", f"{p_xg:.2f}")
                            pc4.metric("Goals", p_goals)

                    if st.button("Generate Shot Map", type="primary", key="csv_multi_gen"):
                        st.session_state["multi_shot_chart"] = None
                        with st.spinner("Generating shot map..."):
                            chart_shots = shots_df
                            chart_info = dict(multi_match_info)

                            if selected_player and not is_player_csv:
                                shooter_col = 'shooter' if 'shooter' in chart_shots.columns else 'Player'
                                chart_shots = chart_shots[chart_shots[shooter_col] == selected_player].copy()
                                chart_info['total_matches'] = chart_shots['_match_id'].nunique()

                            if chart_shots.empty:
                                st.error(f"No shots found for {selected_player}")
                            else:
                                img_bytes, filename, caption = _generate_multi_match_chart(
                                    chart_shots, team_name, team_color, chart_info,
                                    competition, selected_player, exclude_penalties,
                                    highlight_mode, shots_against=shots_against,
                                    custom_title=custom_title_csv_multi,
                                    custom_subtitle=custom_subtitle_csv_multi
                                )
                                st.session_state["multi_shot_chart"] = {
                                    "img": img_bytes, "filename": filename, "caption": caption,
                                }

                    if st.session_state.get("multi_shot_chart"):
                        chart = st.session_state["multi_shot_chart"]
                        st.image(chart["img"], caption=chart["caption"])
                        st.download_button(
                            label="Download Shot Map",
                            data=chart["img"],
                            file_name=chart["filename"],
                            mime="image/png",
                            key="csv_multi_dl"
                        )
                        st.success("Shot map generated!")

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")
            import traceback
            st.code(traceback.format_exc())

    else:
        st.info("Upload a TruMedia Event Log CSV for a single match or full season.")

        with st.expander("Expected CSV Format"):
            st.markdown("""
            **Single-match CSV columns:**
            - `Date`, `homeTeam`, `awayTeam`
            - `Team`, `xG`, `playType`
            - `EventX`, `EventY` (shot coordinates, 0-100 scale)
            - `newestTeamColor` (optional)
            - `homeFinalScore`, `awayFinalScore` (optional)

            **Multi-match (season) CSV columns:**
            - `Date`, `Team`, `xG`, `playType`
            - `EventX`, `EventY` (shot coordinates, 0-100 scale)
            - `shooter` or `Player` (for player filtering)
            - `gameId` (optional, for match grouping)
            - `newestTeamColor` (optional)
            - `homeTeam`, `awayTeam` (optional, for match grouping)

            **Shot types detected:**
            - Miss, Goal, PenaltyGoal, AttemptSaved, Post
            """)
