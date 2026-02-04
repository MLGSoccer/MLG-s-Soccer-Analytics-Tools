"""
Team Chart Generator - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.team_chart_generator import (
    load_csv_data,
    create_scatter_chart,
    create_horizontal_bar_chart,
    create_vertical_bar_chart
)

st.set_page_config(page_title="Team Chart Generator", page_icon="📉", layout="wide")

st.title("Team Chart Generator")
st.markdown("Create custom scatter or bar charts from any team data.")

# File upload
uploaded_file = st.file_uploader(
    "Upload CSV with Team Data",
    type=["csv"],
    help="Any CSV with team names and numeric columns"
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Loading data..."):
            df, team_info = load_csv_data(tmp_path)

        st.success(f"Loaded {len(df)} teams")

        # Pre-check team colors
        from pages.streamlit_utils import check_team_colors
        if team_info.get('name_col'):
            team_names = df[team_info['name_col']].dropna().unique().tolist()
            csv_colors = {}
            if team_info.get('color_col'):
                for _, row in df.iterrows():
                    name = row.get(team_info['name_col'])
                    color = row.get(team_info['color_col'])
                    if name and color:
                        csv_colors[name] = color
            check_team_colors(team_names, csv_colors)

        # Get numeric columns
        numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns.tolist()

        # Sidebar controls
        st.sidebar.header("Chart Settings")

        chart_type = st.sidebar.selectbox(
            "Chart Type",
            options=["scatter", "horizontal_bar", "vertical_bar"],
            format_func=lambda x: {"scatter": "Scatter Plot", "horizontal_bar": "Horizontal Bar", "vertical_bar": "Vertical Bar"}[x]
        )

        if chart_type == "scatter":
            x_col = st.sidebar.selectbox("X-Axis Column", options=numeric_cols)
            y_col = st.sidebar.selectbox("Y-Axis Column", options=numeric_cols, index=min(1, len(numeric_cols)-1))
            value_col = None
        else:
            x_col = None
            y_col = None
            value_col = st.sidebar.selectbox("Value Column", options=numeric_cols)

        # Labels
        st.sidebar.header("Labels")
        title = st.sidebar.text_input("Chart Title", value="Team Chart")

        if chart_type == "scatter":
            x_label = st.sidebar.text_input("X-Axis Label", value=x_col or "")
            y_label = st.sidebar.text_input("Y-Axis Label", value=y_col or "")
        else:
            x_label = st.sidebar.text_input("Value Label", value=value_col or "")
            y_label = ""

        # Generate button
        if st.button("Generate Chart", type="primary"):
            with st.spinner("Generating chart..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    safe_title = title.replace(' ', '_').replace(':', '').replace('/', '-')[:50]
                    output_path = os.path.join(tmp_dir, f"team_chart_{safe_title}.png")

                    if chart_type == "scatter":
                        create_scatter_chart(df, team_info, x_col, y_col, title, x_label, y_label, output_path)
                    elif chart_type == "horizontal_bar":
                        create_horizontal_bar_chart(df, team_info, value_col, title, x_label, output_path)
                    else:
                        create_vertical_bar_chart(df, team_info, value_col, title, y_label, output_path)

                    st.image(output_path, caption=title)

                    with open(output_path, "rb") as f:
                        st.download_button(
                            label="Download Chart",
                            data=f.read(),
                            file_name=f"{safe_title}.png",
                            mime="image/png"
                        )

            st.success("Chart generated successfully!")

        # Preview data
        with st.expander("Preview Data"):
            st.dataframe(df.head(20))

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

else:
    st.info("👆 Upload a CSV with team data to get started")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Required:**
        - A column with team names (`Team`, `teamName`, `teamAbbrevName`, etc.)
        - At least one numeric column for charting

        **Optional:**
        - `newestTeamColor` - Team colors will be auto-detected

        **Example columns:**
        - xG, xGA, Goals, Points, Wins, etc.
        """)
