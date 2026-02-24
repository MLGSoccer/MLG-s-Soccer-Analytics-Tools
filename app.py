"""
Soccer Charts - Streamlit Web App
CBS Sports styled soccer analytics charts
"""
import streamlit as st

st.set_page_config(
    page_title="Soccer Charts",
    page_icon="⚽",
    layout="wide"
)

pg = st.navigation({
    "": [
        st.Page("pages/5_Player_Comparison.py", title="Player Comparison"),
        st.Page("pages/3_xG_Race.py", title="xG Race"),
        st.Page("pages/1_Team_Rolling_xG.py", title="Team Rolling xG"),
        st.Page("pages/2_Player_Rolling_xG.py", title="Player Rolling xG"),
    ],
    "Other Charts": [
        st.Page("pages/9_Shot_Chart.py", title="Shot Chart"),
        st.Page("pages/10_Progressive_Flow.py", title="Progressive Flow"),
        st.Page("pages/11_Zone_Passing.py", title="Zone Passing"),
        st.Page("pages/4_Sequence_Analysis.py", title="Sequence Analysis"),
        st.Page("pages/6_Set_Piece_Report.py", title="Set Piece Report"),
        st.Page("pages/7_Player_Bar_Chart.py", title="Player Bar Chart"),
        st.Page("pages/8_Team_Chart_Generator.py", title="Team Chart Generator"),
    ],
})

pg.run()
