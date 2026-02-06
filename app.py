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

st.title("Soccer Charts")
st.markdown("**CBS Sports styled soccer analytics charts**")

st.markdown("---")

st.markdown("""
### Available Charts

Use the sidebar to navigate between chart types.

| Chart | Description |
|-------|-------------|
| **Team Rolling xG** | Rolling average xG analysis for teams over a season (4-panel chart) |
| **Player Rolling xG** | Rolling average xG/goals/shots for individual players |
| **xG Race** | Single-match xG timeline chart |
| **Sequence Analysis** | How possessions build toward shots |
| **Player Comparison** | Compare player stats to position peers |
| **Set Piece Report** | Attacking/defensive set piece analysis |
| **Player Bar Chart** | Leaderboard or team roster stats |
| **Team Chart Generator** | Custom scatter/bar charts from any data |
| **Shot Chart** | Shot locations on pitch for a single match |

### Data Format

All charts use **TruMedia CSV** files. Upload your CSV and the app will auto-detect the format.

### Getting Started

1. Select a chart type from the sidebar
2. Upload your TruMedia CSV file
3. Adjust parameters as needed
4. Click Generate to create your chart
5. Download the result
""")

st.markdown("---")
st.caption("CBS SPORTS | Data: TruMedia")
