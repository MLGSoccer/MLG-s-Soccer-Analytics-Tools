# Soccer Charts Project

This project contains Python scripts for generating soccer analytics charts with CBS Sports styling.

## Current Charts

1. **team_rollingxg_chart.py** - Rolling average xG analysis for teams over a season (4-panel chart)
2. **player_rollingxg_chart.py** - Rolling average xG/goals/shots analysis for individual players (4-panel chart)
3. **xg_race_chart.py** - Single-match xG race/timeline chart
4. **sequence_analysis_chart.py** - Analyzes how possessions build toward shots (4-panel chart)

## Data Input

All charts use **TruMedia CSV** files as their primary data source. The CSV files contain columns like:
- `Team`, `xG`, `xGA`, `Date`, `homeTeam`, `awayTeam`
- `sequenceId`, `playType`, `shooter`, `gameClock`, `Period`
- `newestTeamColor` (auto-detected team colors)

## Shared Module (`shared/`)

Common utilities have been extracted into a `shared/` module. New charts should import from here:

### shared/colors.py
Team color management and utilities:
- `TEAM_COLORS` - Built-in color dictionary (50+ teams)
- `TEAM_ABBREV` - Abbreviation to full name mapping (160+ teams)
- `load_custom_colors()` / `save_custom_color()` - Persist user color choices
- `fuzzy_match_team()` - Match team names flexibly, returns `(color, matched_name, ambiguous_candidates)`
- `check_color_similarity()` - Warn if two team colors are too similar
- `color_distance()` - Calculate RGB distance between colors
- `hex_to_rgb()` - Convert hex to RGB tuple
- `get_team_color()` - Get color with fallback chain (CSV -> database -> saved -> prompt)
- `prompt_ambiguous_choice()` - Ask user to choose from ambiguous matches
- `resolve_team_colors()` - Resolve colors for multiple teams at once

### shared/styles.py
CBS Sports styling constants and utilities:
- `BG_COLOR = '#1A2332'` - Dark blue-gray background
- `SPINE_COLOR = '#556B7F'` - Axis/spine color
- `CBS_BLUE = '#00325B'` - CBS branding color
- `TEXT_PRIMARY = '#FFFFFF'`, `TEXT_SECONDARY = '#B8C5D6'`, etc.
- `style_axis()` - Apply consistent styling to matplotlib axis
- `style_axis_full_grid()` - Style with both x and y grid lines
- `add_cbs_footer()` - Add "CBS SPORTS" branding footer

### shared/file_utils.py
File handling utilities:
- `get_file_path()` - Prompt for file in Downloads folder
- `get_output_folder()` - Prompt for output folder

### Example Import
```python
from shared.colors import (
    TEAM_COLORS, load_custom_colors, fuzzy_match_team,
    check_color_similarity, get_team_color
)
from shared.styles import BG_COLOR, style_axis, add_cbs_footer
from shared.file_utils import get_file_path, get_output_folder
```

## Conventions for New Charts

### Structure
- Each chart should have a `main()` function as the entry point
- Import utilities from `shared/` module (do NOT duplicate code)
- Generate both a combined multi-panel chart AND individual standalone charts

### Styling (CBS Sports theme)
Use constants from `shared/styles.py`:
- Background color: `BG_COLOR` (`#1A2332`)
- Axis/spine color: `SPINE_COLOR` (`#556B7F`)
- CBS branding color: `CBS_BLUE` (`#00325B`)
- White text for labels on dark backgrounds
- Use `add_cbs_footer()` for consistent branding

### Team Colors
Use utilities from `shared/colors.py`:
- Check CSV first (`newestTeamColor` column)
- Use `get_team_color()` for automatic fallback chain
- Use `fuzzy_match_team()` for flexible team name matching
- Use `check_color_similarity()` to warn about similar colors

### Output
- Save to user-specified folder (default: Downloads)
- Use 300 DPI for saved images
- Generate both combined and individual chart versions

### Mockups
- All chart mockups should be saved to the `mockups/` folder
- Use descriptive names like `panel4_mockup_v1.png`

## Future Plans

- Build a unified launcher program to select and generate charts
- Eventually convert to a Streamlit web app
