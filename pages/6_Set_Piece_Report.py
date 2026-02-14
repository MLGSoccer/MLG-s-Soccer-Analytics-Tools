"""
Set Piece Report Chart - Streamlit Page
"""
import streamlit as st
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mostly_finished_charts.setpiece_report_chart import (
    load_setpiece_data,
    create_setpiece_report
)

st.set_page_config(page_title="Set Piece Report", page_icon="🎯", layout="wide")


@st.cache_data
def _load_setpiece_cached(file_content):
    """Cache set piece data loading from uploaded bytes."""
    import tempfile as _tempfile
    with _tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name
    try:
        return load_setpiece_data(tmp_path)
    finally:
        os.unlink(tmp_path)


st.title("Set Piece Report")
st.markdown("Analyze attacking and defensive set piece performance.")

# Sidebar controls
st.sidebar.header("Settings")
report_type = st.sidebar.selectbox(
    "Report Type",
    options=["both", "attacking", "defensive"],
    format_func=lambda x: x.title(),
    help="Which set piece stats to include"
)

# File upload
uploaded_file = st.file_uploader(
    "Upload TruMedia Set Piece Report CSV",
    type=["csv"],
    help="Set piece statistics CSV"
)

if uploaded_file is not None:
    file_content = uploaded_file.getvalue()

    try:
        with st.spinner("Loading set piece data..."):
            df = _load_setpiece_cached(file_content)

        st.success(f"Loaded set piece data ({len(df)} rows)")

        if st.button("Generate Report", type="primary"):
            with st.spinner("Generating set piece report..."):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    saved_files = create_setpiece_report(df, tmp_dir, report_type=report_type)

                    for filepath in saved_files:
                        if os.path.exists(filepath):
                            filename = os.path.basename(filepath)
                            st.image(filepath, caption=filename.replace('_', ' ').replace('.png', '').title())

                            with open(filepath, "rb") as f:
                                st.download_button(
                                    label=f"Download {filename}",
                                    data=f.read(),
                                    file_name=filename,
                                    mime="image/png",
                                    key=f"download_{filename}"
                                )

            st.success("Report generated successfully!")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")

else:
    st.info("👆 Upload a TruMedia Set Piece Report CSV to get started")

    with st.expander("Expected CSV Format"):
        st.markdown("""
        **Expected columns:**
        - Team name column
        - Set piece statistics (corners, free kicks, etc.)
        - xG from set pieces
        - Goals from set pieces
        """)
