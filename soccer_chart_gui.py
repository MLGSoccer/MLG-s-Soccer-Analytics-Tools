"""
Soccer Chart Generator - Tkinter GUI
A graphical interface for generating CBS Sports soccer analytics charts.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import threading
import os
import sys

# Import color checking utilities (these are lightweight, ok to load at startup)
from shared.file_utils import extract_teams_from_csv
from shared.colors import (
    check_colors_need_fix, get_team_color, color_distance,
    TEAM_COLORS, load_custom_colors, fuzzy_match_team
)

# Chart modules are imported lazily in _get_chart_runner() to speed up startup

# CBS Sports styling colors
BG_COLOR = '#1A2332'
CBS_BLUE = '#00325B'
CONTENT_BG = '#F5F5F5'
ACCENT_COLOR = '#0066CC'


class ChartGeneratorApp:
    """Main application class for the Soccer Chart Generator GUI."""

    # Chart type definitions with metadata
    # Note: 'runner' is now a string key - actual functions are loaded lazily
    CHART_TYPES = {
        'team_rolling': {
            'name': 'Team Rolling xG Analysis',
            'description': 'Rolling xG for/against over a season',
            'has_window': True,
            'has_player_name': False,
            'has_report_type': False,
        },
        'player_rolling': {
            'name': 'Player Rolling xG Analysis',
            'description': 'Rolling xG/goals/shots for individual player',
            'has_window': True,
            'has_player_name': False,
            'has_report_type': False,
        },
        'xg_race': {
            'name': 'xG Race Chart (Single Match)',
            'description': 'Cumulative xG timeline for a single match',
            'has_window': False,
            'has_player_name': False,
            'has_report_type': False,
        },
        'sequence': {
            'name': 'Sequence Analysis',
            'description': 'How possessions build toward shots',
            'has_window': False,
            'has_player_name': False,
            'has_report_type': False,
        },
        'player_comparison': {
            'name': 'Player Comparison',
            'description': 'Compare player vs position peers (percentiles)',
            'has_window': False,
            'has_player_name': True,
            'has_report_type': False,
        },
        'setpiece_report': {
            'name': 'Set Piece Report',
            'description': 'League-wide set piece attacking/defensive analysis',
            'has_window': False,
            'has_player_name': False,
            'has_report_type': True,
            'has_team_chart_config': False,
        },
        'team_chart': {
            'name': 'Team Chart Generator',
            'description': 'Custom scatter/bar charts from team data',
            'has_window': False,
            'has_player_name': False,
            'has_report_type': False,
            'has_team_chart_config': True,
        },
        'player_bar': {
            'name': 'Player Bar Chart',
            'description': 'Compare players on a single stat (leaderboard/team/individual)',
            'has_window': False,
            'has_player_name': False,
            'has_report_type': False,
            'has_team_chart_config': False,
            'has_player_bar_config': True,
        }
    }

    def __init__(self, root):
        """Initialize the application."""
        self.root = root
        self.root.title("MLG's CBS Sports Soccer Chart Generator")
        self.root.resizable(False, False)

        # Set window icon if available
        try:
            # Could add an icon here if one exists
            pass
        except Exception:
            pass

        # Variables
        self.chart_type = tk.StringVar(value='team_rolling')
        self.csv_path = tk.StringVar()
        self.output_folder = tk.StringVar(value=self._get_downloads_folder())
        self.window_size = tk.IntVar(value=10)
        self.status_var = tk.StringVar(value='Ready')

        # xG Race specific variables
        self.competition = tk.StringVar(value='PREMIER LEAGUE')
        self.has_own_goals = tk.BooleanVar(value=False)
        self.own_goals_text = tk.StringVar()

        # Player comparison specific variables
        self.player_name = tk.StringVar()
        self.compare_position = tk.StringVar(value='')  # Empty = use player's natural position

        # Set piece report specific variables
        self.report_type = tk.StringVar(value='both')

        # Team chart generator specific variables
        self.team_chart_style = tk.StringVar(value='scatter')
        self.x_column = tk.StringVar()
        self.y_column = tk.StringVar()
        self.chart_title = tk.StringVar()
        self.x_axis_label = tk.StringVar()
        self.y_axis_label = tk.StringVar()

        # Player bar chart specific variables
        self.player_bar_mode = tk.StringVar(value='league')
        self.player_bar_stat = tk.StringVar()
        self.player_bar_data_format = tk.StringVar(value='per90')  # CSV data format: 'raw' or 'per90'
        self.player_bar_display_as = tk.StringVar(value='per90')   # Display format: 'raw' or 'per90'
        self.player_bar_min_minutes = tk.IntVar(value=900)
        self.player_bar_position = tk.StringVar(value='')
        self.player_bar_max_players = tk.IntVar(value=10)
        self.player_bar_team = tk.StringVar()
        # Individual player fields (up to 10)
        self.player_bar_player_vars = [tk.StringVar() for _ in range(10)]
        # League filter fields (up to 5)
        self.player_bar_league_vars = [tk.StringVar() for _ in range(5)]

        # Track if generation is running
        self.is_generating = False

        # Create widgets
        self._create_widgets()

        # Initial UI state
        self._on_chart_type_change()

    def _get_downloads_folder(self):
        """Get the user's Downloads folder path."""
        if sys.platform == 'win32':
            return os.path.join(os.path.expanduser('~'), 'Downloads')
        elif sys.platform == 'darwin':
            return os.path.join(os.path.expanduser('~'), 'Downloads')
        else:
            return os.path.join(os.path.expanduser('~'), 'Downloads')

    def _create_widgets(self):
        """Create and layout all widgets."""
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky='nsew')

        # Title header
        self._create_header(main_frame)

        # Chart type selection
        self._create_chart_selection(main_frame)

        # Input fields section
        self._create_input_section(main_frame)

        # Generate button
        self._create_generate_button(main_frame)

        # Status bar
        self._create_status_bar(main_frame)

    def _create_header(self, parent):
        """Create the header section."""
        header_frame = ttk.Frame(parent)
        header_frame.grid(row=0, column=0, sticky='ew', pady=(0, 15))

        title_label = ttk.Label(
            header_frame,
            text="MLG's CBS Sports Soccer Chart Generator",
            font=('Segoe UI', 16, 'bold')
        )
        title_label.pack(anchor='w')

        subtitle_label = ttk.Label(
            header_frame,
            text="Generate professional analytics charts from TruMedia data",
            font=('Segoe UI', 9),
            foreground='#666666'
        )
        subtitle_label.pack(anchor='w')

    def _create_chart_selection(self, parent):
        """Create the chart type selection section."""
        # Section frame with label
        section_frame = ttk.LabelFrame(parent, text="SELECT CHART TYPE", padding="10")
        section_frame.grid(row=1, column=0, sticky='ew', pady=(0, 15))

        # Radio buttons for each chart type
        for i, (key, info) in enumerate(self.CHART_TYPES.items()):
            rb = ttk.Radiobutton(
                section_frame,
                text=info['name'],
                value=key,
                variable=self.chart_type,
                command=self._on_chart_type_change
            )
            rb.grid(row=i, column=0, sticky='w', pady=2)

            # Description label
            desc_label = ttk.Label(
                section_frame,
                text=f"  {info['description']}",
                font=('Segoe UI', 8),
                foreground='#888888'
            )
            desc_label.grid(row=i, column=1, sticky='w', padx=(10, 0), pady=2)

    def _create_input_section(self, parent):
        """Create the input fields section."""
        section_frame = ttk.LabelFrame(parent, text="CHART INPUTS", padding="10")
        section_frame.grid(row=2, column=0, sticky='ew', pady=(0, 15))

        # Configure grid columns
        section_frame.columnconfigure(1, weight=1)

        # CSV File row
        ttk.Label(section_frame, text="CSV File:").grid(
            row=0, column=0, sticky='w', pady=(0, 10)
        )

        csv_frame = ttk.Frame(section_frame)
        csv_frame.grid(row=0, column=1, sticky='ew', pady=(0, 10))
        csv_frame.columnconfigure(0, weight=1)

        self.csv_entry = ttk.Entry(csv_frame, textvariable=self.csv_path, width=50)
        self.csv_entry.grid(row=0, column=0, sticky='ew', padx=(5, 5))

        csv_browse_btn = ttk.Button(
            csv_frame,
            text="Browse...",
            command=self._browse_csv,
            width=10
        )
        csv_browse_btn.grid(row=0, column=1)

        # Output Folder row
        ttk.Label(section_frame, text="Output Folder:").grid(
            row=1, column=0, sticky='w', pady=(0, 10)
        )

        output_frame = ttk.Frame(section_frame)
        output_frame.grid(row=1, column=1, sticky='ew', pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)

        self.output_entry = ttk.Entry(output_frame, textvariable=self.output_folder, width=50)
        self.output_entry.grid(row=0, column=0, sticky='ew', padx=(5, 5))

        output_browse_btn = ttk.Button(
            output_frame,
            text="Browse...",
            command=self._browse_output,
            width=10
        )
        output_browse_btn.grid(row=0, column=1)

        # Rolling Window row (conditionally shown)
        self.window_label = ttk.Label(section_frame, text="Rolling Window:")
        self.window_label.grid(row=2, column=0, sticky='w')

        window_frame = ttk.Frame(section_frame)
        window_frame.grid(row=2, column=1, sticky='w')

        self.window_spinbox = ttk.Spinbox(
            window_frame,
            from_=1,
            to=50,
            textvariable=self.window_size,
            width=5
        )
        self.window_spinbox.grid(row=0, column=0, padx=(5, 5))

        self.window_hint = ttk.Label(
            window_frame,
            text="(number of matches for rolling average)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.window_hint.grid(row=0, column=1, padx=(5, 0))

        # Store references for show/hide
        self.window_widgets = [self.window_label, window_frame]

        # Competition row (xG Race only)
        self.competition_label = ttk.Label(section_frame, text="Competition:")
        self.competition_label.grid(row=3, column=0, sticky='w', pady=(10, 0))

        competition_frame = ttk.Frame(section_frame)
        competition_frame.grid(row=3, column=1, sticky='w', pady=(10, 0))

        self.competition_entry = ttk.Entry(
            competition_frame,
            textvariable=self.competition,
            width=25
        )
        self.competition_entry.grid(row=0, column=0, padx=(5, 5))

        self.competition_hint = ttk.Label(
            competition_frame,
            text="(e.g., PREMIER LEAGUE, LA LIGA)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.competition_hint.grid(row=0, column=1, padx=(5, 0))

        self.xg_race_widgets = [self.competition_label, competition_frame]

        # Own Goals row (xG Race only)
        self.own_goals_label = ttk.Label(section_frame, text="Own Goals:")
        self.own_goals_label.grid(row=4, column=0, sticky='w', pady=(5, 0))

        own_goals_frame = ttk.Frame(section_frame)
        own_goals_frame.grid(row=4, column=1, sticky='w', pady=(5, 0))

        self.own_goals_check = ttk.Checkbutton(
            own_goals_frame,
            text="Match had own goals",
            variable=self.has_own_goals,
            command=self._on_own_goals_toggle
        )
        self.own_goals_check.grid(row=0, column=0, padx=(5, 5))

        self.xg_race_widgets.extend([self.own_goals_label, own_goals_frame])

        # Own Goals details (shown when checkbox is checked)
        self.own_goals_details_label = ttk.Label(section_frame, text="Own Goal Details:")
        self.own_goals_details_label.grid(row=5, column=0, sticky='nw', pady=(5, 0))

        own_goals_details_frame = ttk.Frame(section_frame)
        own_goals_details_frame.grid(row=5, column=1, sticky='w', pady=(5, 0))

        self.own_goals_entry = ttk.Entry(
            own_goals_details_frame,
            textvariable=self.own_goals_text,
            width=40
        )
        self.own_goals_entry.grid(row=0, column=0, padx=(5, 5))

        self.own_goals_details_hint = ttk.Label(
            own_goals_details_frame,
            text="Format: minute,team;minute,team  (e.g., 23,home;67,away)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.own_goals_details_hint.grid(row=1, column=0, padx=(5, 0), sticky='w')

        self.own_goals_details_widgets = [self.own_goals_details_label, own_goals_details_frame]

        # Player name row (Player Comparison only)
        self.player_name_label = ttk.Label(section_frame, text="Player Name:")
        self.player_name_label.grid(row=6, column=0, sticky='w', pady=(10, 0))

        player_name_frame = ttk.Frame(section_frame)
        player_name_frame.grid(row=6, column=1, sticky='w', pady=(10, 0))

        self.player_name_entry = ttk.Entry(
            player_name_frame,
            textvariable=self.player_name,
            width=30
        )
        self.player_name_entry.grid(row=0, column=0, padx=(5, 5))

        self.player_name_hint = ttk.Label(
            player_name_frame,
            text="(e.g., Morgan Rogers, Salah, Bruno Fernandes)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.player_name_hint.grid(row=0, column=1, padx=(5, 0))

        self.player_name_widgets = [self.player_name_label, player_name_frame]

        # Compare position row (Player Comparison only)
        self.compare_pos_label = ttk.Label(section_frame, text="Compare As:")
        self.compare_pos_label.grid(row=7, column=0, sticky='w', pady=(5, 0))

        compare_pos_frame = ttk.Frame(section_frame)
        compare_pos_frame.grid(row=7, column=1, sticky='w', pady=(5, 0))

        # Position options - empty string means use player's natural position
        position_options = [
            '',  # Use natural position
            'Center Back',
            'Fullback/Wingback',
            'Defensive Midfielder',
            'Central Midfielder',
            'Attacking Mid/Winger',
            'Striker'
        ]

        self.compare_pos_combo = ttk.Combobox(
            compare_pos_frame,
            textvariable=self.compare_position,
            values=position_options,
            state='readonly',
            width=22
        )
        self.compare_pos_combo.grid(row=0, column=0, padx=(5, 5))

        self.compare_pos_hint = ttk.Label(
            compare_pos_frame,
            text="(leave blank for player's natural position)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.compare_pos_hint.grid(row=0, column=1, padx=(5, 0))

        self.compare_pos_widgets = [self.compare_pos_label, compare_pos_frame]

        # Report type row (Set Piece Report only)
        self.report_type_label = ttk.Label(section_frame, text="Report Type:")
        self.report_type_label.grid(row=8, column=0, sticky='w', pady=(10, 0))

        report_type_frame = ttk.Frame(section_frame)
        report_type_frame.grid(row=8, column=1, sticky='w', pady=(10, 0))

        ttk.Radiobutton(
            report_type_frame,
            text="Attacking",
            value='attacking',
            variable=self.report_type
        ).grid(row=0, column=0, padx=(5, 10))

        ttk.Radiobutton(
            report_type_frame,
            text="Defensive",
            value='defensive',
            variable=self.report_type
        ).grid(row=0, column=1, padx=(0, 10))

        ttk.Radiobutton(
            report_type_frame,
            text="Both",
            value='both',
            variable=self.report_type
        ).grid(row=0, column=2, padx=(0, 10))

        self.report_type_hint = ttk.Label(
            report_type_frame,
            text="(which reports to generate)",
            font=('Segoe UI', 8),
            foreground='#888888'
        )
        self.report_type_hint.grid(row=0, column=3, padx=(5, 0))

        self.report_type_widgets = [self.report_type_label, report_type_frame]

        # Team Chart Generator config (rows 9-14)
        # Chart style
        self.chart_style_label = ttk.Label(section_frame, text="Chart Style:")
        self.chart_style_label.grid(row=9, column=0, sticky='w', pady=(10, 0))

        chart_style_frame = ttk.Frame(section_frame)
        chart_style_frame.grid(row=9, column=1, sticky='w', pady=(10, 0))

        ttk.Radiobutton(
            chart_style_frame, text="Scatter Plot", value='scatter',
            variable=self.team_chart_style
        ).grid(row=0, column=0, padx=(5, 10))

        ttk.Radiobutton(
            chart_style_frame, text="Horizontal Bar", value='horizontal_bar',
            variable=self.team_chart_style
        ).grid(row=0, column=1, padx=(0, 10))

        ttk.Radiobutton(
            chart_style_frame, text="Vertical Bar", value='vertical_bar',
            variable=self.team_chart_style
        ).grid(row=0, column=2, padx=(0, 10))

        # X Column
        self.x_col_label = ttk.Label(section_frame, text="X Column:")
        self.x_col_label.grid(row=10, column=0, sticky='w', pady=(5, 0))

        x_col_frame = ttk.Frame(section_frame)
        x_col_frame.grid(row=10, column=1, sticky='w', pady=(5, 0))

        self.x_col_entry = ttk.Entry(x_col_frame, textvariable=self.x_column, width=25)
        self.x_col_entry.grid(row=0, column=0, padx=(5, 5))

        self.x_col_hint = ttk.Label(
            x_col_frame, text="(for scatter: X axis; for bars: value column)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.x_col_hint.grid(row=0, column=1, padx=(5, 0))

        # Y Column
        self.y_col_label = ttk.Label(section_frame, text="Y Column:")
        self.y_col_label.grid(row=11, column=0, sticky='w', pady=(5, 0))

        y_col_frame = ttk.Frame(section_frame)
        y_col_frame.grid(row=11, column=1, sticky='w', pady=(5, 0))

        self.y_col_entry = ttk.Entry(y_col_frame, textvariable=self.y_column, width=25)
        self.y_col_entry.grid(row=0, column=0, padx=(5, 5))

        self.y_col_hint = ttk.Label(
            y_col_frame, text="(scatter only - leave blank for bar charts)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.y_col_hint.grid(row=0, column=1, padx=(5, 0))

        # Chart Title
        self.title_label = ttk.Label(section_frame, text="Chart Title:")
        self.title_label.grid(row=12, column=0, sticky='w', pady=(5, 0))

        title_frame = ttk.Frame(section_frame)
        title_frame.grid(row=12, column=1, sticky='w', pady=(5, 0))

        self.title_entry = ttk.Entry(title_frame, textvariable=self.chart_title, width=40)
        self.title_entry.grid(row=0, column=0, padx=(5, 5))

        # Axis Labels
        self.axis_labels_label = ttk.Label(section_frame, text="Axis Labels:")
        self.axis_labels_label.grid(row=13, column=0, sticky='w', pady=(5, 0))

        axis_labels_frame = ttk.Frame(section_frame)
        axis_labels_frame.grid(row=13, column=1, sticky='w', pady=(5, 0))

        ttk.Label(axis_labels_frame, text="X:").grid(row=0, column=0, padx=(5, 2))
        self.x_label_entry = ttk.Entry(axis_labels_frame, textvariable=self.x_axis_label, width=18)
        self.x_label_entry.grid(row=0, column=1, padx=(0, 10))

        ttk.Label(axis_labels_frame, text="Y:").grid(row=0, column=2, padx=(5, 2))
        self.y_label_entry = ttk.Entry(axis_labels_frame, textvariable=self.y_axis_label, width=18)
        self.y_label_entry.grid(row=0, column=3, padx=(0, 5))

        # Show Columns button
        self.show_cols_label = ttk.Label(section_frame, text="")
        self.show_cols_label.grid(row=14, column=0, sticky='w', pady=(5, 0))

        show_cols_frame = ttk.Frame(section_frame)
        show_cols_frame.grid(row=14, column=1, sticky='w', pady=(5, 0))

        self.show_cols_btn = ttk.Button(
            show_cols_frame, text="Show Available Columns",
            command=self._show_csv_columns, width=22
        )
        self.show_cols_btn.grid(row=0, column=0, padx=(5, 5))

        self.show_cols_hint = ttk.Label(
            show_cols_frame, text="(load CSV first, then click to see column names)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.show_cols_hint.grid(row=0, column=1, padx=(5, 0))

        self.team_chart_widgets = [
            self.chart_style_label, chart_style_frame,
            self.x_col_label, x_col_frame,
            self.y_col_label, y_col_frame,
            self.title_label, title_frame,
            self.axis_labels_label, axis_labels_frame,
            self.show_cols_label, show_cols_frame
        ]

        # Player Bar Chart config (rows 15-22)
        # Selection mode
        self.pbar_mode_label = ttk.Label(section_frame, text="Selection Mode:")
        self.pbar_mode_label.grid(row=15, column=0, sticky='w', pady=(10, 0))

        pbar_mode_frame = ttk.Frame(section_frame)
        pbar_mode_frame.grid(row=15, column=1, sticky='w', pady=(10, 0))

        ttk.Radiobutton(
            pbar_mode_frame, text="League Leaderboard", value='league',
            variable=self.player_bar_mode, command=self._on_player_bar_mode_change
        ).grid(row=0, column=0, padx=(5, 10))

        ttk.Radiobutton(
            pbar_mode_frame, text="Team Roster", value='team',
            variable=self.player_bar_mode, command=self._on_player_bar_mode_change
        ).grid(row=0, column=1, padx=(0, 10))

        ttk.Radiobutton(
            pbar_mode_frame, text="Individual Players", value='individual',
            variable=self.player_bar_mode, command=self._on_player_bar_mode_change
        ).grid(row=0, column=2, padx=(0, 10))

        # Stat column
        self.pbar_stat_label = ttk.Label(section_frame, text="Stat Column:")
        self.pbar_stat_label.grid(row=16, column=0, sticky='w', pady=(5, 0))

        pbar_stat_frame = ttk.Frame(section_frame)
        pbar_stat_frame.grid(row=16, column=1, sticky='w', pady=(5, 0))

        self.pbar_stat_entry = ttk.Entry(pbar_stat_frame, textvariable=self.player_bar_stat, width=20)
        self.pbar_stat_entry.grid(row=0, column=0, padx=(5, 5))

        self.pbar_show_cols_btn = ttk.Button(
            pbar_stat_frame, text="Show Columns",
            command=self._show_player_csv_columns, width=14
        )
        self.pbar_show_cols_btn.grid(row=0, column=1, padx=(5, 5))

        self.pbar_stat_hint = ttk.Label(
            pbar_stat_frame, text="(e.g., NPxG, Goal, ProgCarry)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_stat_hint.grid(row=0, column=2, padx=(5, 0))

        # Data format (what's in the CSV)
        self.pbar_datafmt_label = ttk.Label(section_frame, text="CSV Data Format:")
        self.pbar_datafmt_label.grid(row=17, column=0, sticky='w', pady=(5, 0))

        pbar_datafmt_frame = ttk.Frame(section_frame)
        pbar_datafmt_frame.grid(row=17, column=1, sticky='w', pady=(5, 0))

        ttk.Radiobutton(
            pbar_datafmt_frame, text="Already Per-90", value='per90',
            variable=self.player_bar_data_format
        ).grid(row=0, column=0, padx=(5, 10))

        ttk.Radiobutton(
            pbar_datafmt_frame, text="Raw Totals", value='raw',
            variable=self.player_bar_data_format
        ).grid(row=0, column=1, padx=(0, 10))

        self.pbar_datafmt_hint = ttk.Label(
            pbar_datafmt_frame, text="(TruMedia exports are usually per-90)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_datafmt_hint.grid(row=0, column=2, padx=(5, 0))

        # Display format (what to show on chart)
        self.pbar_display_label = ttk.Label(section_frame, text="Display As:")
        self.pbar_display_label.grid(row=18, column=0, sticky='w', pady=(5, 0))

        pbar_display_frame = ttk.Frame(section_frame)
        pbar_display_frame.grid(row=18, column=1, sticky='w', pady=(5, 0))

        ttk.Radiobutton(
            pbar_display_frame, text="Per-90", value='per90',
            variable=self.player_bar_display_as
        ).grid(row=0, column=0, padx=(5, 10))

        ttk.Radiobutton(
            pbar_display_frame, text="Raw Totals", value='raw',
            variable=self.player_bar_display_as
        ).grid(row=0, column=1, padx=(0, 10))

        self.pbar_display_hint = ttk.Label(
            pbar_display_frame, text="(how values appear on the chart)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_display_hint.grid(row=0, column=2, padx=(5, 0))

        # Min minutes
        self.pbar_minmin_label = ttk.Label(section_frame, text="Min. Minutes:")
        self.pbar_minmin_label.grid(row=19, column=0, sticky='w', pady=(5, 0))

        pbar_minmin_frame = ttk.Frame(section_frame)
        pbar_minmin_frame.grid(row=19, column=1, sticky='w', pady=(5, 0))

        self.pbar_minmin_spinbox = ttk.Spinbox(
            pbar_minmin_frame, from_=0, to=5000,
            textvariable=self.player_bar_min_minutes, width=8
        )
        self.pbar_minmin_spinbox.grid(row=0, column=0, padx=(5, 5))

        self.pbar_minmin_hint = ttk.Label(
            pbar_minmin_frame, text="(filter out players below this threshold)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_minmin_hint.grid(row=0, column=1, padx=(5, 0))

        # Position filter
        self.pbar_pos_label = ttk.Label(section_frame, text="Position Filter:")
        self.pbar_pos_label.grid(row=20, column=0, sticky='w', pady=(5, 0))

        pbar_pos_frame = ttk.Frame(section_frame)
        pbar_pos_frame.grid(row=20, column=1, sticky='w', pady=(5, 0))

        position_options = [
            '',  # All positions
            'Center Back',
            'Fullback/Wingback',
            'Defensive Midfielder',
            'Central Midfielder',
            'Attacking Mid/Winger',
            'Striker'
        ]

        self.pbar_pos_combo = ttk.Combobox(
            pbar_pos_frame, textvariable=self.player_bar_position,
            values=position_options, state='readonly', width=20
        )
        self.pbar_pos_combo.grid(row=0, column=0, padx=(5, 5))

        self.pbar_pos_hint = ttk.Label(
            pbar_pos_frame, text="(leave blank for all positions)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_pos_hint.grid(row=0, column=1, padx=(5, 0))

        # Max players
        self.pbar_max_label = ttk.Label(section_frame, text="Max Players:")
        self.pbar_max_label.grid(row=21, column=0, sticky='w', pady=(5, 0))

        pbar_max_frame = ttk.Frame(section_frame)
        pbar_max_frame.grid(row=21, column=1, sticky='w', pady=(5, 0))

        self.pbar_max_spinbox = ttk.Spinbox(
            pbar_max_frame, from_=1, to=30,
            textvariable=self.player_bar_max_players, width=5
        )
        self.pbar_max_spinbox.grid(row=0, column=0, padx=(5, 5))

        self.pbar_max_hint = ttk.Label(
            pbar_max_frame, text="(number of players to show)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_max_hint.grid(row=0, column=1, padx=(5, 0))

        # League filter (for league mode) - up to 5 fields
        self.pbar_league_label = ttk.Label(section_frame, text="League Filter:")
        self.pbar_league_label.grid(row=22, column=0, sticky='nw', pady=(5, 0))

        pbar_league_frame = ttk.Frame(section_frame)
        pbar_league_frame.grid(row=22, column=1, sticky='w', pady=(5, 0))

        self.pbar_league_entries = []
        self.pbar_league_rows = []
        for i in range(5):
            row_frame = ttk.Frame(pbar_league_frame)
            row_frame.grid(row=i, column=0, sticky='w', pady=1)

            lbl = ttk.Label(row_frame, text=f"League {i+1}:", width=10)
            lbl.grid(row=0, column=0, padx=(5, 5))

            entry = ttk.Entry(row_frame, textvariable=self.player_bar_league_vars[i], width=25)
            entry.grid(row=0, column=1, padx=(0, 5))
            self.pbar_league_entries.append(entry)
            self.pbar_league_rows.append(row_frame)

            # Add trace to show next field when this one has content
            if i < 4:  # Not the last one
                self.player_bar_league_vars[i].trace_add('write',
                    lambda *args, idx=i: self._on_league_field_change(idx))

        # Add hint after first row
        self.pbar_league_hint = ttk.Label(
            pbar_league_frame, text="(leave blank for all leagues)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_league_hint.grid(row=0, column=1, padx=(5, 0), sticky='w')

        # Team name (for team mode)
        self.pbar_team_label = ttk.Label(section_frame, text="Team Name:")
        self.pbar_team_label.grid(row=23, column=0, sticky='w', pady=(5, 0))

        pbar_team_frame = ttk.Frame(section_frame)
        pbar_team_frame.grid(row=23, column=1, sticky='w', pady=(5, 0))

        self.pbar_team_entry = ttk.Entry(pbar_team_frame, textvariable=self.player_bar_team, width=25)
        self.pbar_team_entry.grid(row=0, column=0, padx=(5, 5))

        self.pbar_team_hint = ttk.Label(
            pbar_team_frame, text="(e.g., Liverpool, Arsenal, Atalanta)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_team_hint.grid(row=0, column=1, padx=(5, 0))

        # Player names (for individual mode) - up to 5 fields
        self.pbar_players_label = ttk.Label(section_frame, text="Player Names:")
        self.pbar_players_label.grid(row=24, column=0, sticky='nw', pady=(5, 0))

        pbar_players_frame = ttk.Frame(section_frame)
        pbar_players_frame.grid(row=24, column=1, sticky='w', pady=(5, 0))

        self.pbar_player_entries = []
        self.pbar_player_rows = []
        for i in range(10):
            row_frame = ttk.Frame(pbar_players_frame)
            row_frame.grid(row=i, column=0, sticky='w', pady=1)

            lbl = ttk.Label(row_frame, text=f"Player {i+1}:", width=10)
            lbl.grid(row=0, column=0, padx=(5, 5))

            entry = ttk.Entry(row_frame, textvariable=self.player_bar_player_vars[i], width=25)
            entry.grid(row=0, column=1, padx=(0, 5))
            self.pbar_player_entries.append(entry)
            self.pbar_player_rows.append(row_frame)

            # Add trace to show next field when this one has content
            if i < 9:  # Not the last one
                self.player_bar_player_vars[i].trace_add('write',
                    lambda *args, idx=i: self._on_player_field_change(idx))

        # Add hint after entries
        self.pbar_players_hint = ttk.Label(
            pbar_players_frame, text="(e.g., Salah, Haaland)",
            font=('Segoe UI', 8), foreground='#888888'
        )
        self.pbar_players_hint.grid(row=0, column=1, padx=(5, 0), sticky='w')

        # Store widget groups for showing/hiding
        self.player_bar_widgets = [
            self.pbar_mode_label, pbar_mode_frame,
            self.pbar_stat_label, pbar_stat_frame,
            self.pbar_datafmt_label, pbar_datafmt_frame,
            self.pbar_display_label, pbar_display_frame,
            self.pbar_minmin_label, pbar_minmin_frame,
            self.pbar_pos_label, pbar_pos_frame,
            self.pbar_max_label, pbar_max_frame,
        ]

        self.player_bar_league_widgets = [self.pbar_league_label, pbar_league_frame]
        self.player_bar_team_widgets = [self.pbar_team_label, pbar_team_frame]
        self.player_bar_players_widgets = [self.pbar_players_label, pbar_players_frame]

        # Initially hide all but the first league/player fields
        self._init_dynamic_fields()

    def _create_generate_button(self, parent):
        """Create the generate button."""
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=3, column=0, pady=(0, 15))

        self.generate_btn = ttk.Button(
            button_frame,
            text="Generate Chart",
            command=self._on_generate_click,
            width=20
        )
        self.generate_btn.pack()

    def _create_status_bar(self, parent):
        """Create the status bar at the bottom."""
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=4, column=0, sticky='ew')

        ttk.Label(status_frame, text="Status:").pack(side='left')
        self.status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            foreground='#666666'
        )
        self.status_label.pack(side='left', padx=(5, 0))

    def _browse_csv(self):
        """Open file dialog to select CSV file."""
        # Start in Downloads folder if no path set
        initial_dir = self._get_downloads_folder()
        if self.csv_path.get():
            dir_path = os.path.dirname(self.csv_path.get())
            if os.path.isdir(dir_path):
                initial_dir = dir_path

        file_path = filedialog.askopenfilename(
            title="Select TruMedia CSV File",
            initialdir=initial_dir,
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*")
            ]
        )

        if file_path:
            self.csv_path.set(file_path)

    def _browse_output(self):
        """Open folder dialog to select output folder."""
        initial_dir = self.output_folder.get() or self._get_downloads_folder()

        folder_path = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=initial_dir
        )

        if folder_path:
            self.output_folder.set(folder_path)

    def _on_chart_type_change(self):
        """Handle chart type selection change."""
        chart_key = self.chart_type.get()
        chart_info = self.CHART_TYPES.get(chart_key, {})

        # Show/hide rolling window based on chart type
        if chart_info.get('has_window', False):
            self.window_label.grid()
            for widget in self.window_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.window_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show/hide xG Race specific fields
        if chart_key == 'xg_race':
            for widget in self.xg_race_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
            # Show own goals details only if checkbox is checked
            self._on_own_goals_toggle()
        else:
            for widget in self.xg_race_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()
            for widget in self.own_goals_details_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show/hide player name field based on chart type
        if chart_info.get('has_player_name', False):
            for widget in self.player_name_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
            # Also show position selector for player comparison
            for widget in self.compare_pos_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.player_name_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()
            for widget in self.compare_pos_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show/hide report type field based on chart type
        if chart_info.get('has_report_type', False):
            for widget in self.report_type_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.report_type_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show/hide team chart config fields
        if chart_info.get('has_team_chart_config', False):
            for widget in self.team_chart_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.team_chart_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show/hide player bar chart config fields
        if chart_info.get('has_player_bar_config', False):
            for widget in self.player_bar_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
            # Also update mode-specific fields
            self._on_player_bar_mode_change()
        else:
            for widget in self.player_bar_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()
            for widget in self.player_bar_league_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()
            for widget in self.player_bar_team_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()
            for widget in self.player_bar_players_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

    def _show_csv_columns(self):
        """Show available columns from the selected CSV file."""
        csv_path = self.csv_path.get().strip()
        if not csv_path or not os.path.isfile(csv_path):
            messagebox.showwarning("No CSV", "Please select a CSV file first.")
            return

        try:
            # Lazy import to avoid loading heavy libs at startup
            from mostly_finished_charts import team_chart_generator
            df, team_info = team_chart_generator.load_csv_data(csv_path)
            numeric_cols = team_chart_generator.get_numeric_columns(df)

            # Build message
            msg_parts = [f"Loaded {len(df)} rows\n"]
            msg_parts.append("=" * 40)
            msg_parts.append("\nDETECTED TEAM COLUMNS:")
            msg_parts.append(f"  Name: {team_info['name_col'] or 'Not found'}")
            msg_parts.append(f"  Abbreviation: {team_info['abbrev_col'] or 'Not found'}")
            msg_parts.append(f"  Color: {team_info['color_col'] or 'Not found'}")
            msg_parts.append("\n" + "=" * 40)
            msg_parts.append(f"\nNUMERIC COLUMNS ({len(numeric_cols)}):")
            for col in numeric_cols:
                msg_parts.append(f"  • {col}")

            messagebox.showinfo("CSV Columns", "\n".join(msg_parts))
        except Exception as e:
            messagebox.showerror("Error", f"Could not read CSV:\n{e}")

    def _on_own_goals_toggle(self):
        """Show/hide own goals details based on checkbox."""
        if self.has_own_goals.get() and self.chart_type.get() == 'xg_race':
            for widget in self.own_goals_details_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.own_goals_details_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

    def _on_player_bar_mode_change(self):
        """Show/hide team/player/league fields based on player bar mode."""
        mode = self.player_bar_mode.get()

        # Show league field only for league mode
        if mode == 'league':
            for widget in self.player_bar_league_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.player_bar_league_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show team field only for team mode
        if mode == 'team':
            for widget in self.player_bar_team_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.player_bar_team_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

        # Show players field only for individual mode
        if mode == 'individual':
            for widget in self.player_bar_players_widgets:
                if hasattr(widget, 'grid'):
                    widget.grid()
        else:
            for widget in self.player_bar_players_widgets:
                if hasattr(widget, 'grid_remove'):
                    widget.grid_remove()

    def _init_dynamic_fields(self):
        """Initialize dynamic fields - show only first field for leagues and players."""
        # Hide all but first league field
        for i in range(1, 5):
            self.pbar_league_rows[i].grid_remove()

        # Hide all but first player field
        for i in range(1, 10):
            self.pbar_player_rows[i].grid_remove()

    def _on_league_field_change(self, idx):
        """Show next league field when current one has content."""
        if idx < 4 and self.player_bar_league_vars[idx].get().strip():
            self.pbar_league_rows[idx + 1].grid()

    def _on_player_field_change(self, idx):
        """Show next player field when current one has content."""
        if idx < 9 and self.player_bar_player_vars[idx].get().strip():
            self.pbar_player_rows[idx + 1].grid()

    def _reset_dynamic_fields(self):
        """Reset dynamic fields to initial state (only first visible, all cleared)."""
        # Clear and hide league fields
        for i in range(5):
            self.player_bar_league_vars[i].set('')
            if i > 0:
                self.pbar_league_rows[i].grid_remove()

        # Clear and hide player fields
        for i in range(10):
            self.player_bar_player_vars[i].set('')
            if i > 0:
                self.pbar_player_rows[i].grid_remove()

    def _show_player_csv_columns(self):
        """Show available columns from the selected player CSV file."""
        csv_path = self.csv_path.get().strip()
        if not csv_path or not os.path.isfile(csv_path):
            messagebox.showwarning("No CSV", "Please select a CSV file first.")
            return

        try:
            # Lazy import to avoid loading heavy libs at startup
            from mostly_finished_charts import player_bar_chart
            df = player_bar_chart.load_player_data(csv_path)
            available_stats = player_bar_chart.get_available_stats(df)

            # Build message with stat display names
            from shared.stat_mappings import get_stat_display_name
            msg_parts = [f"Loaded {len(df)} players\n"]
            msg_parts.append("=" * 40)
            msg_parts.append(f"\nAVAILABLE STATS ({len(available_stats)}):\n")

            for col in available_stats[:30]:  # Limit to 30
                display = get_stat_display_name(col)
                msg_parts.append(f"  {col:15} -> {display}")

            if len(available_stats) > 30:
                msg_parts.append(f"\n  ... and {len(available_stats) - 30} more")

            messagebox.showinfo("Available Stats", "\n".join(msg_parts))
        except Exception as e:
            messagebox.showerror("Error", f"Could not read CSV:\n{e}")

    def _validate_inputs(self):
        """Validate all inputs before generation. Returns (valid, error_message)."""
        # Check CSV file
        csv_path = self.csv_path.get().strip()
        if not csv_path:
            return False, "Please select a CSV file."
        if not os.path.isfile(csv_path):
            return False, f"CSV file not found:\n{csv_path}"
        if not csv_path.lower().endswith('.csv'):
            return False, "Selected file is not a CSV file."

        # Check output folder
        output_folder = self.output_folder.get().strip()
        if not output_folder:
            return False, "Please select an output folder."

        # Create output folder if it doesn't exist
        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder)
            except Exception as e:
                return False, f"Could not create output folder:\n{e}"

        # Validate rolling window for applicable charts
        chart_key = self.chart_type.get()
        chart_info = self.CHART_TYPES.get(chart_key, {})
        if chart_info.get('has_window', False):
            try:
                window = self.window_size.get()
                if window < 1 or window > 50:
                    return False, "Rolling window must be between 1 and 50."
            except Exception:
                return False, "Invalid rolling window value."

        # Validate player name for applicable charts
        if chart_info.get('has_player_name', False):
            player_name = self.player_name.get().strip()
            if not player_name:
                return False, "Please enter a player name."

        # Validate team chart config
        if chart_info.get('has_team_chart_config', False):
            x_col = self.x_column.get().strip()
            y_col = self.y_column.get().strip()
            chart_style = self.team_chart_style.get()

            if not x_col:
                return False, "Please enter an X column name."

            if chart_style == 'scatter' and not y_col:
                return False, "Scatter plots require both X and Y columns."

        # Validate player bar chart config
        if chart_info.get('has_player_bar_config', False):
            stat = self.player_bar_stat.get().strip()
            if not stat:
                return False, "Please enter a stat column name."

            mode = self.player_bar_mode.get()
            if mode == 'team':
                team = self.player_bar_team.get().strip()
                if not team:
                    return False, "Please enter a team name for Team Roster mode."
            elif mode == 'individual':
                # Check if at least one player is entered
                players = [v.get().strip() for v in self.player_bar_player_vars if v.get().strip()]
                if not players:
                    return False, "Please enter at least one player name for Individual mode."

        return True, ""

    def _on_generate_click(self):
        """Handle generate button click."""
        if self.is_generating:
            return

        # Validate inputs
        valid, error_msg = self._validate_inputs()
        if not valid:
            messagebox.showerror("Validation Error", error_msg)
            return

        # Build configuration
        chart_key = self.chart_type.get()
        chart_info = self.CHART_TYPES[chart_key]
        csv_path = self.csv_path.get().strip()

        # Check for color conflicts and prompt user if needed
        color_fix = self._check_and_fix_colors(csv_path, chart_key)
        if color_fix is None:
            # User cancelled
            return

        config = {
            'file_path': csv_path,
            'output_folder': self.output_folder.get().strip()
        }

        # Add color overrides if any
        if 'team_colors' in color_fix:
            config['team_colors'] = color_fix['team_colors']

        # Add chart-specific config
        if chart_key == 'team_rolling':
            config['window'] = self.window_size.get()
            config['gui_mode'] = True
        elif chart_key == 'player_rolling':
            config['window'] = self.window_size.get()
            config['gui_mode'] = True
        elif chart_key == 'xg_race':
            config['data_source'] = 'trumedia'
            config['save'] = True
            config['gui_mode'] = True  # Non-interactive mode for color similarity
            config['competition'] = self.competition.get().strip().upper() or 'FRIENDLY'
            # Parse own goals from text input
            config['own_goals'] = self._parse_own_goals()
        elif chart_key == 'sequence':
            config['gui_mode'] = True  # Non-interactive mode for color similarity
        elif chart_key == 'player_comparison':
            config['player_name'] = self.player_name.get().strip()
            config['min_minutes'] = 900
            # Add position override if selected
            compare_pos = self.compare_position.get().strip()
            if compare_pos:
                config['compare_position'] = compare_pos
        elif chart_key == 'setpiece_report':
            config['report_type'] = self.report_type.get()
        elif chart_key == 'team_chart':
            chart_style = self.team_chart_style.get()
            x_col = self.x_column.get().strip()
            y_col = self.y_column.get().strip()

            config['chart_type'] = chart_style

            if chart_style == 'scatter':
                config['x_col'] = x_col
                config['y_col'] = y_col
            else:
                # For bar charts, x_col is the value column
                config['value_col'] = x_col

            # Title and labels (use defaults if empty)
            config['title'] = self.chart_title.get().strip() or f"{y_col or x_col} by Team"
            config['x_label'] = self.x_axis_label.get().strip() or x_col
            config['y_label'] = self.y_axis_label.get().strip() or (y_col if chart_style == 'scatter' else x_col)
        elif chart_key == 'player_bar':
            config['mode'] = self.player_bar_mode.get()
            config['stat'] = self.player_bar_stat.get().strip()
            config['data_format'] = self.player_bar_data_format.get()  # 'raw' or 'per90'
            config['display_as'] = self.player_bar_display_as.get()    # 'raw' or 'per90'
            config['min_minutes'] = self.player_bar_min_minutes.get()
            config['max_players'] = self.player_bar_max_players.get()

            # Position filter (empty string means all positions)
            position = self.player_bar_position.get().strip()
            if position:
                config['position'] = position

            # Mode-specific config
            mode = self.player_bar_mode.get()
            if mode == 'league':
                # Collect league names from individual fields
                leagues = [v.get().strip() for v in self.player_bar_league_vars if v.get().strip()]
                if leagues:
                    config['leagues'] = leagues
            elif mode == 'team':
                config['team'] = self.player_bar_team.get().strip()
            elif mode == 'individual':
                # Collect player names from individual fields
                players = [v.get().strip() for v in self.player_bar_player_vars if v.get().strip()]
                config['players'] = players

            config['gui_mode'] = True

        # Start generation in thread
        self.is_generating = True
        self.generate_btn.config(state='disabled')
        self.status_var.set(f"Generating {chart_info['name']}...")
        self.status_label.config(foreground='#0066CC')

        # Run in separate thread to prevent UI freeze
        thread = threading.Thread(
            target=self._run_generation,
            args=(config, chart_key),
            daemon=True
        )
        thread.start()

    def _get_chart_runner(self, chart_key):
        """Lazily import and return the chart runner function.

        This defers heavy imports (matplotlib, pandas, numpy) until
        the user actually clicks Generate, making startup much faster.
        """
        if chart_key == 'team_rolling':
            from mostly_finished_charts import run_team_rolling
            return run_team_rolling
        elif chart_key == 'player_rolling':
            from mostly_finished_charts import run_player_rolling
            return run_player_rolling
        elif chart_key == 'xg_race':
            from mostly_finished_charts import run_xg_race
            return run_xg_race
        elif chart_key == 'sequence':
            from mostly_finished_charts import run_sequence
            return run_sequence
        elif chart_key == 'player_comparison':
            from mostly_finished_charts import player_comparison_chart
            return player_comparison_chart.run
        elif chart_key == 'setpiece_report':
            from mostly_finished_charts import run_setpiece_report
            return run_setpiece_report
        elif chart_key == 'team_chart':
            from mostly_finished_charts import team_chart_generator
            return team_chart_generator.run
        elif chart_key == 'player_bar':
            from mostly_finished_charts import run_player_bar
            return run_player_bar
        else:
            raise ValueError(f"Unknown chart type: {chart_key}")

    def _run_generation(self, config, chart_key):
        """Run chart generation in separate thread."""
        import traceback
        import glob
        chart_info = self.CHART_TYPES[chart_key]
        runner = self._get_chart_runner(chart_key)
        output_folder = config['output_folder']

        try:
            # Record modification times of existing PNG files before generation
            existing_files = {}
            if os.path.isdir(output_folder):
                for f in os.listdir(output_folder):
                    if f.lower().endswith('.png'):
                        filepath = os.path.join(output_folder, f)
                        existing_files[filepath] = os.path.getmtime(filepath)

            print(f"\n{'='*60}")
            print(f"Starting {chart_key} generation...")
            print(f"Config: {config}")
            print(f"Tracking {len(existing_files)} existing PNG files")
            print(f"{'='*60}\n")

            # Run the chart generator and capture returned file list if any
            runner_result = runner(config)

            print(f"\n{'='*60}")
            print("Generation complete, checking for new/modified files...")
            print(f"{'='*60}\n")

            # Find files that are new or have different modification times
            new_or_modified = []
            if os.path.isdir(output_folder):
                for f in os.listdir(output_folder):
                    if f.lower().endswith('.png'):
                        filepath = os.path.join(output_folder, f)
                        current_mtime = os.path.getmtime(filepath)
                        # File is new or was modified
                        if filepath not in existing_files or current_mtime != existing_files[filepath]:
                            new_or_modified.append(f)

            new_chart_count = len(new_or_modified)
            print(f"New/modified files: {new_or_modified}")

            # Main chart identifiers for each chart type
            main_chart_names = {
                'team_rolling': 'xg_rolling_analysis.png',
                'player_rolling': '_rolling_analysis.png',  # partial match
                'sequence': 'sequence_analysis.png',
                'xg_race': 'xg_race_',  # partial match
                'player_comparison': 'player_comparison_',  # partial match
                'setpiece_report': 'setpiece_',  # partial match
                'team_chart': 'team_chart_',  # partial match
                'player_bar': 'player_bar_'  # partial match
            }

            # Find the main chart to open
            main_chart = None

            # First, check if the runner returned a list of files (setpiece_report does this)
            if runner_result and isinstance(runner_result, list) and len(runner_result) > 0:
                # Use the first file from the returned list (which is the main combined chart)
                main_chart = runner_result[0]
                print(f"Main chart from runner result: {main_chart}")
            else:
                # Fallback: Find the main chart among new/modified files
                target = main_chart_names.get(chart_key, '')
                for png in new_or_modified:
                    if target in png:
                        main_chart = os.path.join(output_folder, png)
                        print(f"Main chart found: {main_chart}")
                        break

                # Fallback to first new/modified PNG if no match
                if not main_chart and new_or_modified:
                    main_chart = os.path.join(output_folder, new_or_modified[0])
                    print(f"Fallback main chart: {main_chart}")

            # Success - update UI from main thread
            self.root.after(0, self._on_generation_complete, True,
                          f"{new_chart_count} chart(s) saved to:\n{config['output_folder']}", main_chart)
        except Exception as e:
            # Error - update UI from main thread
            error_msg = f"{str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            print(f"\nERROR: {error_msg}")
            self.root.after(0, self._on_generation_complete, False, error_msg, None)

    def _on_generation_complete(self, success, message, main_chart=None):
        """Handle generation completion (called from main thread)."""
        self.is_generating = False
        self.generate_btn.config(state='normal')

        if success:
            self.status_var.set("Chart generated successfully!")
            self.status_label.config(foreground='#228B22')
            messagebox.showinfo("Success", message)

            # Open the main chart with default viewer
            if main_chart and os.path.isfile(main_chart):
                try:
                    os.startfile(main_chart)
                except Exception as e:
                    print(f"Could not open chart: {e}")
        else:
            self.status_var.set("Generation failed")
            self.status_label.config(foreground='#CC0000')
            messagebox.showerror("Error", f"Chart generation failed:\n\n{message}")

        # Reset status color after delay
        self.root.after(5000, self._reset_status)

    def _reset_status(self):
        """Reset status bar to default state."""
        if not self.is_generating:
            self.status_var.set("Ready")
            self.status_label.config(foreground='#666666')

    def _resolve_team_color(self, team_name, csv_colors):
        """Get team color using fallback chain: CSV -> database -> saved."""
        # Check CSV first
        if team_name in csv_colors:
            return csv_colors[team_name]

        # Check built-in database (fuzzy match)
        color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            return color

        # Check saved custom colors
        custom_colors = load_custom_colors()
        color, _, _ = fuzzy_match_team(team_name, custom_colors)
        if color:
            return color

        return '#888888'  # Default gray

    def _check_and_fix_colors(self, csv_path, chart_key):
        """Check team colors and prompt user to fix if too similar.

        Returns:
            dict with 'team_colors' override if colors were fixed, empty dict otherwise.
            Returns None if user cancelled.
        """
        # Only check for charts that use team colors
        if chart_key not in ('sequence', 'xg_race'):
            return {}

        # Extract teams from CSV
        team_info = extract_teams_from_csv(csv_path)
        teams = team_info['teams']

        if len(teams) < 2:
            return {}

        # Get colors for each team
        csv_colors = team_info['colors']
        team1, team2 = teams[0], teams[1]
        color1 = self._resolve_team_color(team1, csv_colors)
        color2 = self._resolve_team_color(team2, csv_colors)

        # Check if colors need fixing
        check_result = check_colors_need_fix(color1, color2, team1, team2)

        if not check_result['needs_fix']:
            return {}

        # Colors are too similar
        if check_result['can_auto_fix']:
            # Auto-fix available - apply it automatically
            fix = check_result['suggested_fix']
            fixed_colors = {team1: color1, team2: color2}
            fixed_colors[fix['team']] = fix['color']
            return {'team_colors': fixed_colors}

        # No auto-fix available - show dialog
        return self._show_color_conflict_dialog(
            team1, color1, team2, color2, check_result['distance']
        )

    def _show_color_conflict_dialog(self, team1, color1, team2, color2, distance):
        """Show dialog for user to resolve color conflict.

        Returns:
            dict with 'team_colors' if resolved, empty dict if kept as-is, None if cancelled.
        """
        # Create dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title("Color Conflict Detected")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center the dialog
        dialog.geometry("450x350")
        dialog_x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 225
        dialog_y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 175
        dialog.geometry(f"+{dialog_x}+{dialog_y}")

        result = {'action': None, 'colors': {team1: color1, team2: color2}}

        # Warning message
        ttk.Label(
            dialog,
            text="Team colors are too similar!",
            font=('Segoe UI', 12, 'bold')
        ).pack(pady=(20, 10))

        ttk.Label(
            dialog,
            text=f"Color distance: {distance:.0f} (minimum: 50)",
            font=('Segoe UI', 9),
            foreground='#666666'
        ).pack()

        # Team color frames
        colors_frame = ttk.Frame(dialog)
        colors_frame.pack(pady=20, padx=20, fill='x')

        # Team 1
        team1_frame = ttk.Frame(colors_frame)
        team1_frame.pack(fill='x', pady=5)

        team1_color_var = tk.StringVar(value=color1)
        team1_swatch = tk.Label(
            team1_frame, width=4, height=2, bg=color1, relief='solid', borderwidth=1
        )
        team1_swatch.pack(side='left', padx=(0, 10))

        ttk.Label(team1_frame, text=team1, font=('Segoe UI', 10, 'bold')).pack(side='left')
        ttk.Label(team1_frame, text=color1, font=('Segoe UI', 9), foreground='#666666').pack(side='left', padx=(10, 0))

        def pick_color1():
            color = colorchooser.askcolor(color=color1, title=f"Choose color for {team1}")
            if color[1]:
                team1_color_var.set(color[1])
                team1_swatch.config(bg=color[1])
                result['colors'][team1] = color[1]

        ttk.Button(team1_frame, text="Change", command=pick_color1, width=8).pack(side='right')

        # Team 2
        team2_frame = ttk.Frame(colors_frame)
        team2_frame.pack(fill='x', pady=5)

        team2_color_var = tk.StringVar(value=color2)
        team2_swatch = tk.Label(
            team2_frame, width=4, height=2, bg=color2, relief='solid', borderwidth=1
        )
        team2_swatch.pack(side='left', padx=(0, 10))

        ttk.Label(team2_frame, text=team2, font=('Segoe UI', 10, 'bold')).pack(side='left')
        ttk.Label(team2_frame, text=color2, font=('Segoe UI', 9), foreground='#666666').pack(side='left', padx=(10, 0))

        def pick_color2():
            color = colorchooser.askcolor(color=color2, title=f"Choose color for {team2}")
            if color[1]:
                team2_color_var.set(color[1])
                team2_swatch.config(bg=color[1])
                result['colors'][team2] = color[1]

        ttk.Button(team2_frame, text="Change", command=pick_color2, width=8).pack(side='right')

        # Distance indicator (updates as colors change)
        distance_var = tk.StringVar(value=f"Current distance: {distance:.0f}")
        distance_label = ttk.Label(dialog, textvariable=distance_var, font=('Segoe UI', 9))
        distance_label.pack(pady=10)

        def update_distance(*args):
            new_dist = color_distance(result['colors'][team1], result['colors'][team2])
            status = "OK" if new_dist >= 50 else "Too similar"
            distance_var.set(f"Current distance: {new_dist:.0f} ({status})")

        team1_color_var.trace('w', update_distance)
        team2_color_var.trace('w', update_distance)

        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=20)

        def on_apply():
            result['action'] = 'apply'
            dialog.destroy()

        def on_keep():
            result['action'] = 'keep'
            dialog.destroy()

        def on_cancel():
            result['action'] = 'cancel'
            dialog.destroy()

        ttk.Button(button_frame, text="Apply Changes", command=on_apply, width=15).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Keep As-Is", command=on_keep, width=15).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Cancel", command=on_cancel, width=10).pack(side='left', padx=5)

        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

        # Wait for dialog to close
        self.root.wait_window(dialog)

        if result['action'] == 'cancel':
            return None
        elif result['action'] == 'apply':
            return {'team_colors': result['colors']}
        else:
            return {}

    def _parse_own_goals(self):
        """Parse own goals from text input.

        Format: minute,team;minute,team
        Example: 23,home;67,away

        Returns list of dicts with 'minute' and 'team' keys.
        'team' will be 'home' or 'away' - converted to actual team names later.
        """
        if not self.has_own_goals.get():
            return []

        text = self.own_goals_text.get().strip()
        if not text:
            return []

        own_goals = []
        for entry in text.split(';'):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(',')
            if len(parts) >= 2:
                try:
                    minute = float(parts[0].strip())
                    team = parts[1].strip().lower()  # 'home' or 'away'
                    own_goals.append({
                        'minute': minute,
                        'team': team,
                        'player': 'Unknown'
                    })
                except ValueError:
                    continue

        return own_goals


def main():
    """Main entry point."""
    root = tk.Tk()

    # Configure ttk style for a cleaner look
    style = ttk.Style()

    # Try to use a more modern theme if available
    available_themes = style.theme_names()
    if 'vista' in available_themes:
        style.theme_use('vista')
    elif 'clam' in available_themes:
        style.theme_use('clam')

    # Create and run the application
    app = ChartGeneratorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
