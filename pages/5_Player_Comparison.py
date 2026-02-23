"""
Player Comparison Chart - Streamlit Page
Supports both single-player and multi-player (2-3) comparison modes.
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


@st.cache_data
def _load_player_data_cached(file_content):
    """Cache player data loading from uploaded bytes."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return load_player_data(tmp_path)
    finally:
        os.unlink(tmp_path)


@st.cache_data
def _generate_single_player_charts(file_content, player_name, min_minutes, compare_position):
    """Generate single-player comparison charts and return image bytes."""
    df = _load_player_data_cached(file_content)
    results, player_row, peer_count, final_position = get_player_percentiles(
        df, player_name, min_minutes, compare_position
    )
    if results is None:
        return None, None, None

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_name = player_name.replace(' ', '_').replace('.', '').replace("'", '')

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


@st.cache_data
def _generate_multi_player_charts(file_content, selected_players, min_minutes, compare_position):
    """Generate multi-player comparison charts and return image bytes."""
    df = _load_player_data_cached(file_content)
    results_by_player, player_rows, peer_count, final_position = get_multiple_player_percentiles(
        df, selected_players, min_minutes, compare_position
    )
    if results_by_player is None:
        return None, None, None

    charts = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_names = '_vs_'.join([
            p.replace(' ', '_').replace('.', '').replace("'", '')[:15]
            for p in selected_players
        ])

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


st.title("Player Comparison Chart")
st.markdown("Compare player stats to position peers using percentile rankings.")

# File upload
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

        # Get list of players for selection
        if 'playerFullName' in df.columns:
            player_list = sorted(df['playerFullName'].dropna().unique().tolist())
        elif 'Player' in df.columns:
            player_list = sorted(df['Player'].dropna().unique().tolist())
        else:
            player_list = []

        # Sidebar controls
        st.sidebar.header("Settings")

        # Mode toggle
        comparison_mode = st.sidebar.radio(
            "Comparison Mode",
            options=["Single Player", "Multi-Player (2-3)"],
            help="Single player shows one player vs peers. Multi-player compares 2-3 players directly."
        )

        # Player selection based on mode
        if comparison_mode == "Single Player":
            player_name = st.sidebar.selectbox(
                "Select Player",
                options=[""] + player_list,
                help="Choose a player to analyze"
            )
            selected_players = [player_name] if player_name else []
        else:
            selected_players = st.sidebar.multiselect(
                "Select Players (2-3)",
                options=player_list,
                max_selections=3,
                help="Choose 2-3 players to compare"
            )

        min_minutes = st.sidebar.number_input(
            "Minimum Minutes",
            min_value=0,
            max_value=5000,
            value=900,
            step=100,
            help="Filter peers by minimum minutes played"
        )

        # Position filter - use actual position categories
        position_options = ["Auto (use player's position)"] + POSITION_CATEGORIES
        compare_position = st.sidebar.selectbox(
            "Compare Against Position",
            options=position_options,
            help="Which position group to compare against"
        )
        if compare_position == "Auto (use player's position)":
            compare_position = None

        # Validation and generation
        if comparison_mode == "Single Player":
            can_generate = len(selected_players) == 1 and selected_players[0]
            if not can_generate and selected_players:
                st.sidebar.warning("Select a player to analyze")
        else:
            can_generate = 2 <= len(selected_players) <= 3
            if len(selected_players) == 1:
                st.sidebar.warning("Select at least 2 players for multi-player comparison")
            elif len(selected_players) > 3:
                st.sidebar.warning("Maximum 3 players allowed")
            elif can_generate:
                # Check if players are in different position categories
                player_positions = []
                for pname in selected_players:
                    if 'playerFullName' in df.columns:
                        mask = df['playerFullName'] == pname
                    else:
                        mask = df['Player'] == pname
                    if mask.any():
                        pos = df[mask].iloc[0].get('PositionCategory', None)
                        if pos:
                            player_positions.append(pos)

                # If positions differ and no position selected, require selection
                unique_positions = set(player_positions)
                if len(unique_positions) > 1 and compare_position is None:
                    can_generate = False
                    st.sidebar.error(f"Players are in different positions ({', '.join(unique_positions)}). Please select a position to compare against.")

        if can_generate:
            if st.button("Generate Charts", type="primary"):
                st.session_state["player_comparison_charts"] = None
                with st.spinner(f"Analyzing {'players' if len(selected_players) > 1 else selected_players[0]}..."):

                    if comparison_mode == "Single Player":
                        player_name = selected_players[0]
                        result = _generate_single_player_charts(
                            file_content, player_name, min_minutes, compare_position
                        )
                        charts, peer_count, final_position = result
                        if charts is None:
                            st.error(f"Player '{player_name}' not found or doesn't meet minimum minutes.")
                        else:
                            st.session_state["player_comparison_charts"] = charts
                            st.session_state["player_comparison_meta"] = {
                                "mode": "single",
                                "player_name": player_name,
                                "peer_count": peer_count,
                                "final_position": final_position,
                                "min_minutes": min_minutes,
                            }
                    else:
                        result = _generate_multi_player_charts(
                            file_content, selected_players, min_minutes, compare_position
                        )
                        charts, peer_count, final_position = result
                        if charts is None:
                            st.error("One or more players not found or don't meet minimum minutes.")
                        else:
                            st.session_state["player_comparison_charts"] = charts
                            st.session_state["player_comparison_meta"] = {
                                "mode": "multi",
                                "selected_players": selected_players,
                                "peer_count": peer_count,
                                "final_position": final_position,
                                "min_minutes": min_minutes,
                            }

            # Display from session state
            if st.session_state.get("player_comparison_charts"):
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
                    individual_keys = [k for k in charts if k != "combined"]
                    for i, key in enumerate(individual_keys):
                        title, img_bytes = charts[key]
                        col = col1 if i % 2 == 0 else col2
                        cat_slug = key.replace('.png', '')
                        with col:
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
                    individual_keys = [k for k in charts if k != "combined"]
                    for i, key in enumerate(individual_keys):
                        title, img_bytes = charts[key]
                        col = col1 if i % 2 == 0 else col2
                        cat_slug = key.replace('.png', '')
                        with col:
                            st.image(img_bytes, caption=title)
                            st.download_button(
                                label=f"Download {title}",
                                data=img_bytes,
                                file_name=f"multi_{safe_names}_{cat_slug}.png",
                                mime="image/png",
                                key=f"download_multi_{cat_slug}"
                            )

                    st.success("Multi-player charts generated successfully!")

        else:
            if comparison_mode == "Single Player":
                st.info("Select a player from the sidebar to analyze")
            else:
                st.info("Select 2-3 players from the sidebar to compare")

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
        - Great for individual player analysis

        **Multi-Player Mode (2-3):**
        - Compare 2-3 players side-by-side
        - Each player shown with their team color
        - Bar intensity reflects percentile (lighter = lower, darker = higher)
        - Useful for transfer targets, position battles, or direct comparisons
        """)
