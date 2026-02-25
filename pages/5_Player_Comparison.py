"""
Player Comparison Chart - Streamlit Page
Supports both single-player and multi-player (2-3) comparison modes.
Data source: Supabase (auto) or manual CSV upload.
"""
import streamlit as st
import tempfile
import os
import sys
import io
import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.motherduck import get_player_current_team
from shared.colors import fuzzy_match_team, TEAM_COLORS
from mostly_finished_charts.player_comparison_chart import (
    load_player_data,
    get_player_percentiles,
    get_multiple_player_percentiles,
    create_comparison_chart,
    create_category_chart,
    create_multi_player_comparison_chart,
    create_multi_player_category_chart,
    POSITION_CATEGORIES
)

st.set_page_config(page_title="Player Comparison", page_icon="📈", layout="wide")

SUPABASE_BUCKET = "player-pools"
POOL_LABELS = {
    "europe": "Europe",
    "north_america": "North America",
    "womens": "Women's Soccer",
}
MEN_POOLS = {"europe", "north_america"}


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _get_supabase_creds():
    """Get Supabase credentials from Streamlit secrets."""
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except (KeyError, FileNotFoundError):
        return None, None


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pool_from_supabase(pool_key, supabase_url, supabase_key):
    """Fetch a player pool CSV from Supabase Storage. Cached for 1 hour."""
    url = f"{supabase_url}/storage/v1/object/{SUPABASE_BUCKET}/{pool_key}.csv"
    headers = {"Authorization": f"Bearer {supabase_key}"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.content


# ── Shared cache functions ────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_player_data_cached(file_content):
    """Cache player data loading from CSV bytes."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return load_player_data(tmp_path)
    finally:
        os.unlink(tmp_path)


@st.cache_data(show_spinner=False)
def _generate_single_player_charts(file_content, player_name, min_minutes, compare_position, color_overrides=()):
    """Generate single-player comparison charts and return image bytes."""
    df = _load_player_data_cached(file_content)
    results, player_row, peer_count, final_position = get_player_percentiles(
        df, player_name, min_minutes, compare_position
    )
    if results is None:
        return None, None, None

    # Apply current team name and color from MotherDuck if available
    overrides = {name: (team, color) for name, team, color in color_overrides}
    if player_name in overrides:
        team_name, color = overrides[player_name]
        player_row = player_row.copy()
        if team_name:
            player_row['newestTeam'] = team_name
            player_row['teamName'] = team_name
        if color:
            player_row['newestTeamColor'] = color

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = os.path.join(tmp_dir, "player_comparison.png")
        create_comparison_chart(results, player_row, peer_count, output_path, final_position)
        with open(output_path, "rb") as f:
            charts["combined"] = f.read()

        categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'DRIBBLING', 'DEFENSIVE']
        for category in categories:
            if category in results:
                cat_slug = category.lower().replace(' ', '_')
                cat_path = os.path.join(tmp_dir, f"{cat_slug}.png")
                create_category_chart(category, results[category], player_row, peer_count, cat_path, final_position)
                with open(cat_path, "rb") as f:
                    charts[f"{cat_slug}.png"] = (category.title(), f.read())

    return charts, peer_count, final_position


@st.cache_data(show_spinner=False)
def _generate_multi_player_charts(file_content, selected_players, min_minutes, compare_position, color_overrides=()):
    """Generate multi-player comparison charts and return image bytes."""
    df = _load_player_data_cached(file_content)
    results_by_player, player_rows, peer_count, final_position = get_multiple_player_percentiles(
        df, selected_players, min_minutes, compare_position
    )
    if results_by_player is None:
        return None, None, None

    # Apply current team name and color from MotherDuck if available
    overrides = {name: (team, color) for name, team, color in color_overrides}
    for pname, (team_name, color) in overrides.items():
        if pname in player_rows:
            player_rows[pname] = player_rows[pname].copy()
            if team_name:
                player_rows[pname]['newestTeam'] = team_name
                player_rows[pname]['teamName'] = team_name
            if color:
                player_rows[pname]['newestTeamColor'] = color

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = os.path.join(tmp_dir, "multi_player_comparison.png")
        create_multi_player_comparison_chart(results_by_player, player_rows, peer_count, final_position, output_path)
        with open(output_path, "rb") as f:
            charts["combined"] = f.read()

        categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'DRIBBLING', 'DEFENSIVE']
        for category in categories:
            cat_slug = category.lower().replace(' ', '_')
            cat_path = os.path.join(tmp_dir, f"multi_{cat_slug}.png")
            create_multi_player_category_chart(category, results_by_player, player_rows, peer_count, final_position, cat_path)
            with open(cat_path, "rb") as f:
                charts[f"multi_{cat_slug}.png"] = (category.title(), f.read())

    return charts, peer_count, final_position


# ── Chart display (shared by both modes) ─────────────────────────────────────

def _display_charts():
    """Render charts and download buttons from session state."""
    if not st.session_state.get("player_comparison_charts"):
        return

    charts = st.session_state["player_comparison_charts"]
    meta = st.session_state.get("player_comparison_meta", {})

    if meta.get("mode") == "single":
        player_name = meta["player_name"]
        safe_name = player_name.replace(' ', '_').replace('.', '').replace("'", '')
        st.info(f"Comparing against {meta['peer_count']} {meta['final_position']}s with {meta['min_minutes']}+ minutes")

        st.image(charts["combined"], caption=f"{player_name} - Percentile Rankings")
        st.download_button(
            label="Download Combined Chart",
            data=charts["combined"],
            file_name=f"{safe_name}_comparison.png",
            mime="image/png"
        )

        st.markdown("---")
        st.subheader("Individual Category Charts")
        col1, col2 = st.columns(2)
        for i, key in enumerate([k for k in charts if k != "combined"]):
            title, img_bytes = charts[key]
            cat_slug = key.replace('.png', '')
            with (col1 if i % 2 == 0 else col2):
                st.image(img_bytes, caption=title)
                st.download_button(
                    label=f"Download {title}",
                    data=img_bytes,
                    file_name=f"{safe_name}_{cat_slug}.png",
                    mime="image/png",
                    key=f"download_{cat_slug}"
                )
        st.success("Charts generated successfully!")

    elif meta.get("mode") == "multi":
        players = meta["selected_players"]
        player_names_str = ', '.join(players)
        safe_names = '_vs_'.join([
            p.replace(' ', '_').replace('.', '').replace("'", '')[:15]
            for p in players
        ])
        st.info(f"Comparing {player_names_str} against {meta['peer_count']} {meta['final_position']}s with {meta['min_minutes']}+ minutes")

        st.image(charts["combined"], caption=f"Multi-Player Comparison - {player_names_str}")
        st.download_button(
            label="Download Combined Chart",
            data=charts["combined"],
            file_name=f"multi_comparison_{safe_names}.png",
            mime="image/png"
        )

        st.markdown("---")
        st.subheader("Individual Category Charts")
        col1, col2 = st.columns(2)
        for i, key in enumerate([k for k in charts if k != "combined"]):
            title, img_bytes = charts[key]
            cat_slug = key.replace('.png', '')
            with (col1 if i % 2 == 0 else col2):
                st.image(img_bytes, caption=title)
                st.download_button(
                    label=f"Download {title}",
                    data=img_bytes,
                    file_name=f"multi_{safe_names}_{cat_slug}.png",
                    mime="image/png",
                    key=f"download_multi_{cat_slug}"
                )
        st.success("Multi-player charts generated successfully!")


def _get_team_overrides(selected_players, df):
    """Look up current team name and color from MotherDuck for selected players.

    Returns a tuple of (player_name, team_name, color) triples for use as a cache key.
    Falls back gracefully if a player isn't found in MotherDuck.
    """
    id_col = 'playerId' if 'playerId' in df.columns else None
    if id_col is None:
        return ()

    name_col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
    overrides = []

    for player_name in selected_players:
        mask = df[name_col] == player_name
        if not mask.any():
            continue
        player_id = df[mask].iloc[0].get(id_col)
        if not player_id:
            continue
        try:
            result = get_player_current_team(str(player_id))
            if result and result.get('team_name'):
                _, clean_name, _ = fuzzy_match_team(result['team_name'], TEAM_COLORS)
                team_name = clean_name if clean_name else result['team_name']
                color = result.get('color', '')
                overrides.append((player_name, team_name, color))
        except Exception:
            pass

    return tuple(overrides)


def _run_generation(file_content, comparison_mode, selected_players, min_minutes, compare_position, df=None):
    """Run chart generation and store results in session state."""
    st.session_state["player_comparison_charts"] = None

    # Look up current team name and color from MotherDuck
    color_overrides = _get_team_overrides(selected_players, df) if df is not None else ()

    with st.spinner(f"Analyzing {'players' if len(selected_players) > 1 else selected_players[0]}..."):
        if comparison_mode == "Single Player":
            player_name = selected_players[0]
            charts, peer_count, final_position = _generate_single_player_charts(
                file_content, player_name, min_minutes, compare_position, color_overrides
            )
            if charts is None:
                st.error(f"Player '{player_name}' not found or doesn't meet minimum minutes.")
                return
            st.session_state["player_comparison_charts"] = charts
            st.session_state["player_comparison_meta"] = {
                "mode": "single",
                "player_name": player_name,
                "peer_count": peer_count,
                "final_position": final_position,
                "min_minutes": min_minutes,
            }
        else:
            charts, peer_count, final_position = _generate_multi_player_charts(
                file_content, tuple(selected_players), min_minutes, compare_position, color_overrides
            )
            if charts is None:
                st.error("One or more players not found or don't meet minimum minutes.")
                return
            st.session_state["player_comparison_charts"] = charts
            st.session_state["player_comparison_meta"] = {
                "mode": "multi",
                "selected_players": selected_players,
                "peer_count": peer_count,
                "final_position": final_position,
                "min_minutes": min_minutes,
            }


def _sidebar_controls(player_list, df=None):
    """Render sidebar controls and return (comparison_mode, selected_players, min_minutes, compare_position, can_generate)."""
    st.sidebar.header("Settings")

    comparison_mode = st.sidebar.radio(
        "Comparison Mode",
        options=["Single Player", "Multi-Player (2-3)"],
    )

    if comparison_mode == "Single Player":
        player_name = st.sidebar.selectbox(
            "Select Player",
            options=[""] + player_list,
        )
        selected_players = [player_name] if player_name else []
    else:
        selected_players = st.sidebar.multiselect(
            "Select Players (2-3)",
            options=player_list,
            max_selections=3,
        )

    min_minutes = st.sidebar.number_input(
        "Minimum Minutes",
        min_value=0, max_value=5000, value=900, step=100,
    )

    compare_position = st.sidebar.selectbox(
        "Compare Against Position",
        options=["Auto (use player's position)"] + POSITION_CATEGORIES,
    )
    if compare_position == "Auto (use player's position)":
        compare_position = None

    # Validation
    can_generate = False
    if comparison_mode == "Single Player":
        can_generate = len(selected_players) == 1 and selected_players[0]
    else:
        can_generate = 2 <= len(selected_players) <= 3
        if len(selected_players) == 1:
            st.sidebar.warning("Select at least 2 players")
        elif df is not None and can_generate and compare_position is None:
            player_positions = []
            for pname in selected_players:
                col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
                mask = df[col] == pname
                if mask.any():
                    pos = df[mask].iloc[0].get('PositionCategory', None)
                    if pos:
                        player_positions.append(pos)
            if len(set(player_positions)) > 1:
                can_generate = False
                st.sidebar.error(f"Players in different positions ({', '.join(set(player_positions))}). Select a position above.")

    return comparison_mode, selected_players, min_minutes, compare_position, can_generate


# ── Page ──────────────────────────────────────────────────────────────────────

st.title("Player Comparison Chart")
st.markdown("Compare player stats to position peers using percentile rankings.")

# Data source toggle
use_manual = st.toggle("Manual data upload", value=False)
st.divider()

# ── AUTO MODE ─────────────────────────────────────────────────────────────────
if not use_manual:
    supabase_url, supabase_key = _get_supabase_creds()

    if not supabase_url or not supabase_key:
        st.error("Supabase credentials not configured. Switch to manual mode or contact your administrator.")
        st.stop()

    # Load all three pools
    with st.spinner("Loading player pools..."):
        pools = {}
        load_errors = []
        for pool_key in POOL_LABELS:
            try:
                content = _fetch_pool_from_supabase(pool_key, supabase_url, supabase_key)
                pools[pool_key] = {"content": content, "df": _load_player_data_cached(content)}
            except Exception as e:
                load_errors.append(f"{POOL_LABELS[pool_key]}: {e}")

    if load_errors:
        st.warning("Some pools could not be loaded: " + " | ".join(load_errors))

    if not pools:
        st.error("No player pools available. Use manual mode or run the Data Manager to update pools.")
        st.stop()

    # Build player → pool(s) lookup
    player_to_pools = {}
    for pool_key, data in pools.items():
        df = data["df"]
        col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
        for player in df[col].dropna().unique():
            if player not in player_to_pools:
                player_to_pools[player] = []
            player_to_pools[player].append(pool_key)

    all_players = sorted(player_to_pools.keys())

    # Sidebar controls
    comparison_mode, selected_players, min_minutes, compare_position, can_generate = _sidebar_controls(
        all_players
    )

    # Pool routing for selected players
    file_content = None
    if selected_players:
        # Find which pools the selected players are in
        player_pools_found = set()
        for p in selected_players:
            for pk in player_to_pools.get(p, []):
                player_pools_found.add(pk)

        if not player_pools_found:
            st.warning("Selected player(s) not found in any pool.")
            can_generate = False

        elif len(player_pools_found) == 1:
            # Normal case — single pool
            pool_key = list(player_pools_found)[0]
            file_content = pools[pool_key]["content"]

        elif player_pools_found <= MEN_POOLS:
            # Europe + North America overlap — offer choice
            can_generate = False
            st.info(f"Player found in multiple pools. Which pool should be used for comparison?")
            pool_options = [POOL_LABELS[k] for k in player_pools_found] + ["Combined (Europe + North America)"]
            pool_choice = st.radio("Compare against:", pool_options, horizontal=True)

            if pool_choice == "Combined (Europe + North America)":
                # Merge the two men's pools
                dfs = [pools[k]["df"] for k in player_pools_found]
                combined_df = pd.concat(dfs, ignore_index=True).drop_duplicates()
                combined_bytes = combined_df.to_csv(index=False).encode("utf-8")
                file_content = combined_bytes
                can_generate = True
            else:
                chosen_key = [k for k, v in POOL_LABELS.items() if v == pool_choice][0]
                file_content = pools[chosen_key]["content"]
                can_generate = True

        elif "womens" in player_pools_found and (player_pools_found & MEN_POOLS):
            # Should never happen — data error
            st.error("Data error: player found in both Women's and men's pools. Check the Data Manager.")
            can_generate = False

        else:
            # Unexpected combination
            st.warning("Player found in unexpected pool combination.")
            can_generate = False

    if can_generate and file_content is not None:
        if st.button("Generate Charts", type="primary"):
            # Determine which df to use for player ID lookup
            _df = None
            if selected_players:
                pool_keys = set()
                for p in selected_players:
                    for pk in player_to_pools.get(p, []):
                        pool_keys.add(pk)
                if pool_keys:
                    _df = pools[next(iter(pool_keys))]["df"]
            _run_generation(file_content, comparison_mode, selected_players, min_minutes, compare_position, df=_df)
    elif not selected_players:
        if comparison_mode == "Single Player":
            st.info("Select a player from the sidebar to analyze")
        else:
            st.info("Select 2-3 players from the sidebar to compare")

    _display_charts()

# ── MANUAL MODE ───────────────────────────────────────────────────────────────
else:
    uploaded_file = st.file_uploader(
        "Upload TruMedia Player Stats CSV",
        type=["csv"],
        help="League-wide player stats (last 365 days recommended)"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()

        try:
            with st.spinner("Loading player data..."):
                df = _load_player_data_cached(file_content)

            st.success(f"Loaded {len(df)} players")

            col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
            player_list = sorted(df[col].dropna().unique().tolist())

            comparison_mode, selected_players, min_minutes, compare_position, can_generate = _sidebar_controls(
                player_list, df
            )

            if can_generate:
                if st.button("Generate Charts", type="primary"):
                    _run_generation(file_content, comparison_mode, selected_players, min_minutes, compare_position)
            else:
                if comparison_mode == "Single Player":
                    st.info("Select a player from the sidebar to analyze")
                else:
                    st.info("Select 2-3 players from the sidebar to compare")

            _display_charts()

        except Exception as e:
            st.error(f"Error processing file: {str(e)}")
            import traceback
            st.code(traceback.format_exc())

    else:
        st.info("Upload a TruMedia Player Stats CSV to get started")

        with st.expander("Expected CSV Format"):
            st.markdown("""
            **Required columns:**
            - `Player` or `playerFullName`
            - `Min` - Minutes played
            - `Position` or `positionGeneral`

            **Stat columns** (will be used for percentile comparison):
            - Goals, Assists, xG, xA
            - Shots, Key Passes, Progressive Passes
            - Tackles, Interceptions, etc.
            """)

        with st.expander("Comparison Modes"):
            st.markdown("""
            **Single Player Mode:**
            - Compare one player's stats against all position peers
            - Shows percentile rankings with color-coded bars

            **Multi-Player Mode (2-3):**
            - Compare 2-3 players side-by-side
            - Each player shown with their team color
            """)
