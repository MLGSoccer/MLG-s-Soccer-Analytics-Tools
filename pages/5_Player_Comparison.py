"""
Player Comparison Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.player_comparison_chart import (
    load_player_data,
    get_player_percentiles,
    create_comparison_chart,
    create_category_chart,
    POSITION_CATEGORIES
)

st.set_page_config(page_title="Player Comparison", page_icon="📈", layout="wide")

st.title("Player Comparison Chart")
st.markdown("Compare a player's stats to position peers using percentile rankings.")

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Player Stats CSV",
    type=["csv"],
    help="League-wide player stats (last 365 days recommended)"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Loading player data..."):
            df = load_player_data(tmp_path)

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

        player_name = st.sidebar.selectbox(
            "Select Player",
            options=[""] + player_list,
            help="Choose a player to analyze"
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

        if player_name:
            if st.button("Generate Charts", type="primary"):
                with st.spinner(f"Analyzing {player_name}..."):
                    results, player_row, peer_count, comparison_position = get_player_percentiles(
                        df, player_name, min_minutes, compare_position
                    )

                    if results is None:
                        st.error(f"Player '{player_name}' not found or doesn't meet minimum minutes.")
                    else:
                        st.info(f"Comparing against {peer_count} {comparison_position}s with {min_minutes}+ minutes")

                        with tempfile.TemporaryDirectory() as tmp_dir:
                            safe_name = player_name.replace(' ', '_').replace('.', '').replace("'", '')

                            # Main combined chart
                            output_path = os.path.join(tmp_dir, "player_comparison.png")
                            create_comparison_chart(
                                results, player_row, peer_count,
                                output_path, comparison_position
                            )

                            st.image(output_path, caption=f"{player_name} - Percentile Rankings")

                            with open(output_path, "rb") as f:
                                st.download_button(
                                    label="Download Combined Chart",
                                    data=f.read(),
                                    file_name=f"{safe_name}_comparison.png",
                                    mime="image/png"
                                )

                            # Individual category charts
                            st.markdown("---")
                            st.subheader("Individual Category Charts")

                            categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'DRIBBLING', 'DEFENSIVE']

                            col1, col2 = st.columns(2)
                            for i, category in enumerate(categories):
                                if category in results:
                                    cat_slug = category.lower().replace(' ', '_')
                                    cat_path = os.path.join(tmp_dir, f"{cat_slug}.png")

                                    create_category_chart(
                                        category, results[category], player_row,
                                        peer_count, cat_path, comparison_position
                                    )

                                    col = col1 if i % 2 == 0 else col2
                                    with col:
                                        st.image(cat_path, caption=category.title())
                                        with open(cat_path, "rb") as f:
                                            st.download_button(
                                                label=f"Download {category.title()}",
                                                data=f.read(),
                                                file_name=f"{safe_name}_{cat_slug}.png",
                                                mime="image/png",
                                                key=f"download_{cat_slug}"
                                            )

                        st.success("Charts generated successfully!")

        else:
            st.info("👈 Select a player from the sidebar to analyze")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a TruMedia Player Stats CSV to get started")

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
