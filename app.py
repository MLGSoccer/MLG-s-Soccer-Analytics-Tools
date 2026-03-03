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

if not st.session_state.get("authenticated"):
    st.title("Soccer Charts")
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

pg = st.navigation({
    "": [
        st.Page("pages/3_xG_Race.py", title="xG Race"),
        st.Page("pages/4_Shot_Chart.py", title="Shot Chart"),
        st.Page("pages/6_Player_Comparison.py", title="Player Comparison"),
    ],
    "xG Trend Lines": [
        st.Page("pages/1_Team_Rolling_xG.py", title="Team Rolling xG"),
        st.Page("pages/2_Player_Rolling_xG.py", title="Player Rolling xG"),
    ],
    "Other Charts": [
        st.Page("pages/5_Sequence_Analysis.py", title="Sequence Analysis"),
        st.Page("pages/7_Set_Piece_Report.py", title="Set Piece Report"),
        st.Page("pages/8_Player_Bar_Chart.py", title="Player Bar Chart"),
        st.Page("pages/9_Team_Chart_Generator.py", title="Team Chart Generator"),
        st.Page("pages/10_Progressive_Flow.py", title="Progressive Flow"),
        st.Page("pages/11_Zone_Passing.py", title="Zone Passing"),
    ],
})

pg.run()
