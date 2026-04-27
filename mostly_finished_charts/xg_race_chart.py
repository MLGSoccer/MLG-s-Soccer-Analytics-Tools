import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory
import numpy as np
import os
from datetime import datetime
import re

# Import shared utilities
from shared.colors import (
    TEAM_COLORS, load_custom_colors, save_custom_color, get_team_color,
    hex_to_rgb, color_distance, check_color_similarity, fuzzy_match_team,
    prompt_ambiguous_choice, ensure_line_contrast,
)
from shared.styles import (
    BG_COLOR, SPINE_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    add_cbs_footer, BROADCAST_FIGSIZE,
)

# Try to import web scraping libraries
try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:
    SCRAPING_AVAILABLE = False

def parse_fbref_url(url):
    """Extract match date from FBref URL"""
    # URL format: /matches/ID/YYYY-MM-DD/Team1-Team2-Competition
    match = re.search(r'/(\d{4}-\d{2}-\d{2})/([^/]+)', url)
    if match:
        date_str = match.group(1)  # YYYY-MM-DD
        
        # Convert date to American format
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%b %d, %Y').upper()
        except:
            formatted_date = None
        
        return formatted_date
    return None

def parse_minute(minute_str):
    """Convert minute string like '45+3' to 45.3"""
    if '+' in minute_str:
        parts = minute_str.split('+')
        return float(parts[0]) + float(parts[1]) / 10
    return float(minute_str)

def fetch_fbref_data(url):
    """Fetch and parse FBref match data"""
    if not SCRAPING_AVAILABLE:
        print("\n⚠ Web scraping libraries not installed.")
        print("To enable automatic FBref fetching:")
        print("1. Open terminal/command prompt")
        print("2. Run: python -m pip install requests beautifulsoup4")
        print("3. Restart the script")
        print("\nFalling back to manual data entry...\n")
        return None
    
    try:
        print("✓ Fetching FBref page...")
        
        # More comprehensive headers to avoid blocking
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        }
        
        # Add a small delay to be respectful
        import time
        time.sleep(1)
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        print("✓ Parsing shot data...")
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the shots table
        shots_table = soup.find('table', {'id': 'shots_all'})
        
        if not shots_table:
            print("⚠ Could not find shots table on page.")
            print("\nNote: FBref may be blocking automated requests.")
            print("Please use manual data entry (option 2) instead.")
            return None
        
        # Extract shot data
        shots = []
        rows = shots_table.find('tbody').find_all('tr')
        
        for row in rows:
            # Skip header rows
            if row.get('class') and 'thead' in row.get('class'):
                continue
            
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            
            try:
                # Extract data from cells
                minute_cell = cells[0].text.strip()
                player_cell = cells[1].text.strip()
                squad_cell = cells[2].text.strip()
                xg_cell = cells[3].text.strip()
                psxg_cell = cells[4].text.strip() if len(cells) > 4 else ''
                outcome_cell = cells[5].text.strip() if len(cells) > 5 else ''
                
                # Parse minute
                minute = parse_minute(minute_cell)
                
                # Parse xG
                xg = float(xg_cell) if xg_cell else 0.0
                
                # Parse outcome
                outcome = outcome_cell if outcome_cell else 'Unknown'
                
                # Clean up squad name
                squad = squad_cell.replace('Club Crest ', '').strip()
                
                shots.append((minute, squad, xg, outcome))
                
            except (ValueError, IndexError) as e:
                # Skip rows that can't be parsed
                continue
        
        if shots:
            print(f"✓ Extracted {len(shots)} shots")
            return shots
        else:
            print("⚠ No shot data found in table.")
            return None
            
    except requests.HTTPError as e:
        if e.response.status_code == 403:
            print(f"⚠ FBref is blocking automated requests (403 Forbidden).")
            print("\nWorkaround: Use manual data entry (option 2)")
            print("I can still help format the data if you copy/paste the shot table!")
        else:
            print(f"⚠ HTTP Error: {e}")
        return None
    except requests.RequestException as e:
        print(f"⚠ Error fetching page: {e}")
        return None
    except Exception as e:
        print(f"⚠ Error parsing data: {e}")
        return None

def parse_trumedia_csv(file_path):
    """Parse TruMedia event log CSV to extract shot data"""
    import csv

    print(f"\n✓ Loading TruMedia CSV: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)

            # Find column indices (handle duplicate column names by taking first occurrence)
            def find_col(name):
                for i, h in enumerate(header):
                    if h == name:
                        return i
                return None

            # Key columns
            shooter_idx = find_col('shooter')
            play_type_idx = find_col('playType')
            game_clock_idx = find_col('gameClock')
            period_idx = find_col('Period')
            team_idx = find_col('Team')
            xg_idx = find_col('xG')
            color_idx = find_col('newestTeamColor')
            date_idx = find_col('Date')
            home_idx = find_col('homeTeam')
            away_idx = find_col('awayTeam')

            if shooter_idx is None or xg_idx is None:
                print("⚠ Could not find required columns (shooter, xG)")
                return None, None, None

            shots = []
            team_colors = {}
            match_info = None
            has_extra_time = False
            penalty_shootout_excluded = 0
            first_half_end_minute = 45  # Default, will update based on Period 1 shots

            for row in reader:
                # Only process rows with a shooter (shots)
                if len(row) > shooter_idx and row[shooter_idx]:
                    try:
                        # Calculate minute from gameClock (seconds)
                        game_clock = float(row[game_clock_idx]) if row[game_clock_idx] else 0
                        minute = game_clock / 60

                        # Get team name
                        team = row[team_idx] if team_idx and len(row) > team_idx else "Unknown"

                        # Get xG value
                        xg = float(row[xg_idx]) if row[xg_idx] else 0.0

                        # Check period and filter out penalty shootout (Period > 4)
                        period = 1  # Default to first half
                        if period_idx and len(row) > period_idx and row[period_idx]:
                            try:
                                period = int(row[period_idx])
                                if period > 4:
                                    # Skip penalty shootout shots entirely
                                    penalty_shootout_excluded += 1
                                    continue
                                if period > 2:
                                    has_extra_time = True
                            except ValueError:
                                pass

                        # Track first half end minute (based on raw gameClock)
                        if period == 1:
                            first_half_end_minute = max(first_half_end_minute, minute)

                        # Map playType to outcome
                        play_type = row[play_type_idx] if play_type_idx and len(row) > play_type_idx else "Unknown"
                        outcome_map = {
                            'Goal': 'Goal',
                            'PenaltyGoal': 'Goal',
                            'AttemptSaved': 'Saved',
                            'Miss': 'Miss',
                            'Post': 'Post',
                            'Blocked': 'Blocked'
                        }
                        outcome = outcome_map.get(play_type, play_type)

                        shots.append((minute, team, xg, outcome))

                        # Capture team color
                        if color_idx and len(row) > color_idx and row[color_idx]:
                            team_colors[team] = row[color_idx]

                        # Capture match info from first shot
                        if match_info is None:
                            match_date = row[date_idx] if date_idx and len(row) > date_idx else None
                            home_team = row[home_idx] if home_idx and len(row) > home_idx else None
                            away_team = row[away_idx] if away_idx and len(row) > away_idx else None

                            # Format date
                            if match_date:
                                try:
                                    date_obj = datetime.strptime(match_date, '%Y-%m-%d')
                                    formatted_date = date_obj.strftime('%b %d, %Y').upper()
                                except:
                                    formatted_date = match_date
                            else:
                                formatted_date = None

                            match_info = {
                                'date': formatted_date,
                                'home_team': home_team,
                                'away_team': away_team,
                                'has_extra_time': False  # Will be updated after all rows
                            }

                    except (ValueError, IndexError) as e:
                        continue

            if shots:
                # Update match_info with extra time detection and halftime position
                if match_info:
                    match_info['has_extra_time'] = has_extra_time
                    match_info['first_half_end_minute'] = first_half_end_minute

                print(f"✓ Extracted {len(shots)} shots from CSV")
                print(f"✓ First half ended at minute {first_half_end_minute:.1f}")
                print(f"✓ Teams found: {', '.join(team_colors.keys())}")
                if team_colors:
                    print(f"✓ Team colors auto-detected")
                if has_extra_time:
                    print(f"✓ Extra time detected (Period 3/4 found)")
                if penalty_shootout_excluded > 0:
                    print(f"✓ Excluded {penalty_shootout_excluded} penalty shootout shots (Period 5+)")
                return shots, match_info, team_colors
            else:
                print("⚠ No shot data found in CSV")
                return None, None, None

    except FileNotFoundError:
        print(f"⚠ File not found: {file_path}")
        return None, None, None
    except Exception as e:
        print(f"⚠ Error reading CSV: {e}")
        return None, None, None

def get_data_source():
    """Ask user how they want to provide data.
    Returns (shots, match_info, team_colors, data_source)
    where data_source is 'fbref', 'trumedia', or 'manual'"""
    print("\n" + "="*60)
    print("DATA INPUT METHOD")
    print("="*60)
    print("1. Paste FBref URL (automatic extraction)")
    print("2. Paste formatted shot data (manual)")
    print("3. Load TruMedia CSV file")

    choice = input("\nChoose method (1, 2, or 3, default=2): ").strip()

    if choice == "1":
        url = input("\nPaste FBref match URL: ").strip()
        if url:
            # Try to extract date from URL
            match_date = parse_fbref_url(url)

            # Try to fetch data
            data = fetch_fbref_data(url)
            if data:
                return data, match_date, None, 'fbref'
            else:
                print("\nFalling back to manual data entry...")
                shots, match_date = parse_shot_data_manual()
                return shots, match_date, None, 'manual'
        else:
            print("\nNo URL provided. Using manual entry...")
            shots, match_date = parse_shot_data_manual()
            return shots, match_date, None, 'manual'
    elif choice == "3":
        file_path = input("\nEnter path to TruMedia CSV file: ").strip()
        # Remove quotes if user copied path with quotes
        file_path = file_path.strip('"').strip("'")
        if file_path:
            shots, match_info, team_colors = parse_trumedia_csv(file_path)
            if shots:
                return shots, match_info, team_colors, 'trumedia'
            else:
                print("\nFalling back to manual data entry...")
                shots, match_date = parse_shot_data_manual()
                return shots, match_date, None, 'manual'
        else:
            print("\nNo file path provided. Using manual entry...")
            shots, match_date = parse_shot_data_manual()
            return shots, match_date, None, 'manual'
    else:
        shots, match_date = parse_shot_data_manual()
        return shots, match_date, None, 'manual'

def get_own_goals(team1_name, team2_name):
    """Ask user about own goals in the match"""
    print("\n" + "="*60)
    print("OWN GOALS")
    print("="*60)
    
    has_og = input("Were there any own goals in this match? (y/n): ").strip().lower()
    
    own_goals = []
    
    if has_og == 'y':
        while True:
            minute = input("\nOwn goal minute (or press Enter if done): ").strip()
            if not minute:
                break
            
            try:
                minute_float = float(minute.replace('+', '.'))
            except ValueError:
                print("Invalid minute format. Try again.")
                continue
            
            print(f"Which team benefited from this own goal?")
            print(f"1. {team1_name}")
            print(f"2. {team2_name}")
            choice = input("Choice (1 or 2): ").strip()
            
            if choice == '1':
                benefiting_team = team1_name
            elif choice == '2':
                benefiting_team = team2_name
            else:
                print("Invalid choice. Skipping this own goal.")
                continue
            
            player = input("Player who scored own goal (optional, press Enter to skip): ").strip()
            
            own_goals.append({
                'minute': minute_float,
                'team': benefiting_team,
                'player': player if player else 'Unknown'
            })
            
            print(f"✓ Added own goal at minute {minute_float} benefiting {benefiting_team}")
    
    return own_goals

def get_team_info(shots, auto_date=None, csv_team_colors=None, config=None):
    """Extract unique team names and get user preferences"""
    teams = list(set(shot[1] for shot in shots))

    if len(teams) != 2:
        print(f"\nWarning: Found {len(teams)} teams instead of 2")

    print(f"\nTeams found: {', '.join(teams)}")

    # Check if this is TruMedia data (has full match info and colors)
    is_trumedia = isinstance(auto_date, dict) and auto_date.get('home_team') and csv_team_colors

    if is_trumedia:
        # AUTOMATED FLOW for TruMedia CSV
        return get_team_info_trumedia(shots, auto_date, csv_team_colors, teams, config)

    # MANUAL FLOW for FBref/manual entry continues below
    print("\n" + "="*60)
    print("TEAM SETUP")
    print("="*60)

    # Load any previously saved custom colors
    custom_colors = load_custom_colors()

    # Merge CSV colors into custom_colors (CSV takes priority for this session)
    if csv_team_colors:
        for team, color in csv_team_colors.items():
            if team not in custom_colors:
                custom_colors[team] = color

    # Fuzzy matching helper
    def find_closest_team(input_name, available_teams):
        """Find closest matching team name"""
        input_lower = input_name.lower().strip()
        for team in available_teams:
            team_lower = team.lower()
            # Exact match
            if input_lower == team_lower:
                return team
            # Contains match
            if input_lower in team_lower or team_lower in input_lower:
                return team
        return None

    # Determine default home/away from match_info if available
    default_team1 = teams[0]
    default_team2 = teams[1] if len(teams) > 1 else 'Team 2'

    # If auto_date is a dict (TruMedia match_info), extract home/away
    if isinstance(auto_date, dict) and auto_date.get('home_team'):
        home = auto_date.get('home_team')
        away = auto_date.get('away_team')
        # Match to teams in shot data
        for t in teams:
            if home and home.lower() in t.lower():
                default_team1 = t
            if away and away.lower() in t.lower():
                default_team2 = t

    team1_input = input(f"\nHome team name (default: {default_team1}): ").strip()
    if team1_input:
        matched = find_closest_team(team1_input, teams)
        if matched:
            team1 = matched
            print(f"  Matched to: {matched}")
        else:
            print(f"  Warning: '{team1_input}' not found in shot data. Using anyway.")
            team1 = team1_input
    else:
        team1 = default_team1

    # Use CSV color if available, otherwise look up
    if csv_team_colors and team1 in csv_team_colors:
        color1 = csv_team_colors[team1]
        print(f"\n✓ Using color from CSV for {team1}: {color1}")
    else:
        print(f"\nLooking up color for {team1}...")
        color1 = get_team_color(team1)

    team2_input = input(f"\nAway team name (default: {default_team2}): ").strip()
    if team2_input:
        matched = find_closest_team(team2_input, [t for t in teams if t != team1])
        if matched:
            team2 = matched
            print(f"  Matched to: {matched}")
        else:
            print(f"  Warning: '{team2_input}' not found in shot data. Using anyway.")
            team2 = team2_input
    else:
        team2 = default_team2

    # Use CSV color if available, otherwise look up
    if csv_team_colors and team2 in csv_team_colors:
        color2 = csv_team_colors[team2]
        print(f"\n✓ Using color from CSV for {team2}: {color2}")
    else:
        print(f"\nLooking up color for {team2}...")
        color2 = get_team_color(team2)

    # Check if colors are too similar
    color1, color2, use_different_line_styles = check_color_similarity(color1, color2, team1, team2)

    # Get own goals
    own_goals = get_own_goals(team1, team2)

    # Get match info
    print("\n" + "="*60)
    print("MATCH INFO")
    print("="*60)
    competition = input("Competition (e.g., LALIGA, PREMIER LEAGUE): ").strip().upper() or "FRIENDLY"
    
    # Ask about extra time
    extra_time = input("Did this match have extra time? (y/n, default=n): ").strip().lower()
    has_extra_time = extra_time == 'y'
    
    # Smart date default - handle both string (FBref) and dict (TruMedia) formats
    extracted_date = None
    if isinstance(auto_date, dict):
        extracted_date = auto_date.get('date')
    elif isinstance(auto_date, str):
        extracted_date = auto_date

    if extracted_date:
        print(f"\nDate extracted: {extracted_date}")
        use_auto = input("Use this date? (y/n, default=y): ").strip().lower()
        if use_auto != 'n':
            match_date = extracted_date
        else:
            default_date = datetime.now().strftime("%b %d, %Y").upper()
            match_date_input = input(f"Match date (default: {default_date}, press Enter to accept): ").strip().upper()
            match_date = match_date_input if match_date_input else default_date
    else:
        default_date = datetime.now().strftime("%b %d, %Y").upper()
        match_date_input = input(f"Match date (default: {default_date}, press Enter to accept): ").strip().upper()
        match_date = match_date_input if match_date_input else default_date

    return {
        'team1': {'name': team1, 'color': color1},
        'team2': {'name': team2, 'color': color2},
        'competition': competition,
        'date': match_date,
        'own_goals': own_goals,
        'extra_time': has_extra_time,
        'different_line_styles': use_different_line_styles,
        'first_half_end_minute': 45  # Default for non-TruMedia sources
    }

def get_team_info_trumedia(shots, match_info, csv_team_colors, teams, config=None):
    """Automated team info extraction for TruMedia CSV data.

    If config contains 'competition' and/or 'own_goals', those prompts are skipped.
    If config contains 'gui_mode': True, color similarity checks auto-resolve without prompts.
    If config contains 'team_colors': dict, those colors are used (from GUI color picker).
    """
    if config is None:
        config = {}

    # Determine if we're in interactive mode (not GUI)
    interactive = not config.get('gui_mode', False)
    team_colors_override = config.get('team_colors', None)

    print("\n" + "="*60)
    print("TRUMEDIA AUTO-SETUP")
    print("="*60)

    # Match home/away from CSV to teams in shot data
    home = match_info.get('home_team')
    away = match_info.get('away_team')

    team1 = None
    team2 = None
    for t in teams:
        if home and home.lower() in t.lower():
            team1 = t
        if away and away.lower() in t.lower():
            team2 = t

    # Fallback if matching failed
    if not team1:
        team1 = teams[0]
    if not team2:
        team2 = teams[1] if len(teams) > 1 else 'Team 2'

    # Check if colors were pre-resolved by GUI
    if team_colors_override:
        print("[OK] Using colors from GUI color picker")
        color1 = team_colors_override.get(team1, '#888888')
        color2 = team_colors_override.get(team2, '#888888')
        print(f"  {team1}: {color1}")
        print(f"  {team2}: {color2}")
        use_different_line_styles = False
    else:
        # Get colors with fallback chain: CSV -> TEAM_COLORS database -> prompt user
        custom_colors = load_custom_colors()

        def get_color_with_fallback(team_name):
            # 1. Check CSV color (exact match)
            if team_name in csv_team_colors:
                return csv_team_colors[team_name], "CSV", None
            # 2. Check built-in database (fuzzy match)
            color, matched_name, ambiguous = fuzzy_match_team(team_name, TEAM_COLORS)
            if color:
                if ambiguous and len(ambiguous) > 1:
                    # Prompt user to choose (or auto-select in gui_mode)
                    chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous, gui_mode=not interactive)
                    if chosen_color:
                        return chosen_color, "database", chosen_name
                    # User chose "none of these" - fall through to manual entry
                else:
                    return color, "database", matched_name
            # 3. Check custom saved colors (fuzzy match)
            color, matched_name, ambiguous = fuzzy_match_team(team_name, custom_colors)
            if color:
                if ambiguous and len(ambiguous) > 1:
                    chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous, gui_mode=not interactive)
                    if chosen_color:
                        return chosen_color, "saved", chosen_name
                else:
                    return color, "saved", matched_name
            # 4. No color found
            return None, None, None

        color1, source1, matched1 = get_color_with_fallback(team1)
        color2, source2, matched2 = get_color_with_fallback(team2)

        # Display auto-detected info
        if color1:
            if matched1 and matched1 != team1:
                print(f"✓ Home: {team1} ({color1}) [from {source1}, matched '{matched1}']")
            else:
                print(f"✓ Home: {team1} ({color1}) [from {source1}]")
        else:
            print(f"⚠ Home: {team1} - no color found")
            color1 = get_team_color(team1)

        if color2:
            if matched2 and matched2 != team2:
                print(f"✓ Away: {team2} ({color2}) [from {source2}, matched '{matched2}']")
            else:
                print(f"✓ Away: {team2} ({color2}) [from {source2}]")
        else:
            print(f"⚠ Away: {team2} - no color found")
            color2 = get_team_color(team2)

        # Check if colors are too similar (auto-resolve in GUI mode)
        color1, color2, use_different_line_styles = check_color_similarity(
            color1, color2, team1, team2, interactive=interactive
        )

    # Get date from CSV
    match_date = match_info.get('date', datetime.now().strftime("%b %d, %Y").upper())

    print(f"✓ Date: {match_date}")

    # Get competition from config or prompt
    if 'competition' in config:
        competition = config['competition'] or ''
        print(f"✓ Competition: {competition}")
    else:
        competition = input("\nCompetition (e.g., PREMIER LEAGUE): ").strip().upper() or "PREMIER LEAGUE"

    # Get extra time from match_info (detected via Period column)
    has_extra_time = match_info.get('has_extra_time', False)
    if has_extra_time:
        print(f"✓ Extra time detected (Period 3/4 in data)")

    # Get own goals from config or prompt
    if 'own_goals' in config:
        own_goals_raw = config['own_goals']
        # Convert 'home'/'away' to actual team names
        own_goals = []
        for og in own_goals_raw:
            team_key = og.get('team', '').lower()
            if team_key == 'home':
                benefiting_team = team1
            elif team_key == 'away':
                benefiting_team = team2
            else:
                benefiting_team = og.get('team', team1)
            own_goals.append({
                'minute': og.get('minute', 0),
                'team': benefiting_team,
                'player': og.get('player', 'Unknown')
            })
        if own_goals:
            print(f"✓ Own goals: {len(own_goals)} provided")
    else:
        own_goals = get_own_goals(team1, team2)

    return {
        'team1': {'name': team1, 'color': color1},
        'team2': {'name': team2, 'color': color2},
        'competition': competition,
        'date': match_date,
        'own_goals': own_goals,
        'extra_time': has_extra_time,
        'different_line_styles': use_different_line_styles,
        'first_half_end_minute': match_info.get('first_half_end_minute', 45)
    }


# ── Step-line geometry helpers ──────────────────────────────────────────────

def _cumulative_xg(shots, team_name):
    """Build (xs, ys, total_xg) for a team's cumulative xG step line.

    Each shot contributes two points at the same minute: (m, before) and
    (m, after). Step-post rendering uses these to produce the vertical jump
    at each shot.
    """
    pts = sorted(
        [(float(m), float(xg)) for (m, t, xg, _o) in shots if t == team_name],
        key=lambda x: x[0],
    )
    xs = [0.0]
    ys = [0.0]
    running = 0.0
    for m, xg in pts:
        xs.append(m)
        ys.append(running)
        running += xg
        xs.append(m)
        ys.append(running)
    return xs, ys, running


def _xg_at_minute(xs, ys, minute):
    """Return the step-line y-value at a given minute.

    Matches the 'post' step semantics: for a minute at which a shot occurs,
    returns the post-step (after-shot) value because the duplicate (m, after)
    point follows the (m, before) point in the xs/ys sequence.
    """
    last = 0.0
    for x, y in zip(xs, ys):
        if x <= minute:
            last = y
        else:
            break
    return last


def _precise_goal_minute(shots, team, int_min):
    """Resolve an integer goal minute to the precise float minute of the shot.

    goal_scorers carries integer minutes (floor of gameClock/60) but shots
    carry the exact float — we need the float so a goal marker lands on the
    TOP of its step, not on the pre-step line value.
    """
    for m, t, _xg, outcome in shots:
        if t == team and int(m) == int_min and outcome == 'Goal':
            return m
    return float(int_min)


# Y-axis fractions (axes coords) at which goal labels can stack above the
# plot. Level 0 is closest to the plot; later levels sit further up.
GOAL_LABEL_Y_LEVELS = (1.04, 1.13, 1.22)


def _place_goal_labels(goals, chart_max, near_edge=6, label_width=12):
    """Assign each goal an (x_side, y_level) so labels don't collide.

    Mutates each goal dict in place, adding:
      - 'x_side': 'left' or 'right' — which side of the minute marker to
        anchor the label text.
      - 'y_level': int index into GOAL_LABEL_Y_LEVELS — which stacking row
        the label occupies.

    Placement rules:
      - Near-left goals (< near_edge minutes in) prefer right-side labels
        so they don't run off the chart.
      - Near-right goals (within near_edge of chart_max) prefer left-side.
      - Prefer level 0 (closest to plot) on the natural side first.
      - If level 0 conflicts, try flipping an earlier level-0 label's side
        so both stay at the bottom stack instead of elevating this one.
      - Fall back to higher y-levels only if flipping can't resolve.
    """
    def _label_range(minute, side):
        if side == 'right':
            return (minute, minute + label_width)
        return (minute - label_width, minute)

    def _overlaps(m_new, s_new, placed):
        lo_new, hi_new = _label_range(m_new, s_new)
        for m_p, s_p, _lv in placed:
            lo_p, hi_p = _label_range(m_p, s_p)
            if max(lo_new, lo_p) < min(hi_new, hi_p):
                return True
        return False

    placed = []  # (minute, x_side, level)
    for ev in goals:
        near_right = (chart_max - ev['minute']) < near_edge
        near_left = ev['minute'] < near_edge
        if near_left:
            sides = ['right']
        elif near_right:
            sides = ['left', 'right']
        else:
            sides = ['right', 'left']

        def _free_at(m, s, lv, placed=placed):
            return not _overlaps(m, s, [(mp, sp, lp) for mp, sp, lp in placed if lp == lv])

        chosen_side, chosen_level = None, None
        # Step 1: natural side, level 0
        for s in sides:
            if _free_at(ev['minute'], s, 0):
                chosen_side, chosen_level = s, 0
                break

        # Step 2: try flipping an earlier level-0 label to keep both at bottom
        flip_target = None
        if chosen_side is None and not near_left:
            for j, (mp, sp, lp) in enumerate(placed):
                if lp != 0:
                    continue
                alt_s = 'left' if sp == 'right' else 'right'
                others_lv0 = [(mk, sk, lk) for k, (mk, sk, lk) in enumerate(placed)
                              if k != j and lk == 0]
                if _overlaps(mp, alt_s, others_lv0):
                    continue
                tentative = others_lv0 + [(mp, alt_s, 0)]
                for s in sides:
                    if not _overlaps(ev['minute'], s, tentative):
                        flip_target = (j, mp, alt_s)
                        chosen_side, chosen_level = s, 0
                        break
                if chosen_side is not None:
                    break

        # Step 3: elevate to higher y-levels
        if chosen_side is None:
            for lv in range(len(GOAL_LABEL_Y_LEVELS)):
                for s in sides:
                    if _free_at(ev['minute'], s, lv):
                        chosen_side, chosen_level = s, lv
                        break
                if chosen_side is not None:
                    break

        if chosen_side is None:
            chosen_side, chosen_level = sides[0], len(GOAL_LABEL_Y_LEVELS) - 1

        if flip_target is not None:
            j, mp, alt_s = flip_target
            placed[j] = (mp, alt_s, 0)
            goals[j]['x_side'] = alt_s

        placed.append((ev['minute'], chosen_side, chosen_level))
        ev['x_side'] = chosen_side
        ev['y_level'] = chosen_level


def _draw_endpoint(ax, last_min, xg_val, label_y, color, shots_count):
    """Draw the endpoint marker plus paired xG + shot-count labels.

    If label_y != xg_val, a small leader line connects the marker on the
    line to the offset label (used when the two teams' endpoint xG values
    are close enough to collide vertically).
    """
    ax.plot(last_min, xg_val, marker='o', markersize=7,
            markerfacecolor=color, markeredgecolor=BG_COLOR,
            markeredgewidth=1.5, zorder=5)
    if label_y != xg_val:
        ax.plot([last_min, last_min + 1.0], [xg_val, label_y],
                color=color, linewidth=0.8, alpha=0.55, zorder=4)
    ax.text(last_min + 1.5, label_y, f'{xg_val:.2f}',
            color=color, fontsize=14, fontweight='bold',
            va='bottom', ha='left')
    ax.text(last_min + 1.5, label_y, f'{shots_count} shots',
            color=color, fontsize=11, alpha=0.9,
            va='top', ha='left')


def create_xg_chart(shots, team_info, goal_scorers=None, red_cards=None, own_goals=None):
    """Create the xG race chart.

    Design: mockup port from mockups/xg_race_redesign_mockup.py.
      - Dark CBS theme, kicker + centered score title + team-color accent bar
      - Goal labels above the plot with 3-level collision-avoidance stagger
      - Running score in each goal label
      - Red cards as dash-dot line stopping at the affected team's step
      - Endpoint xG + shot count at each line's end
      - HALF TIME marker
      - No redundant bottom stats row
    """
    goal_scorers = goal_scorers or []
    red_cards = red_cards or []
    # team_info carries own_goals (benefiting-team format) from get_team_info;
    # prefer that over any passed-in list so callers that have both stay consistent.
    own_goals = team_info.get('own_goals', own_goals or [])

    # ── Resolve team identity + colors ──────────────────────────────────────
    home = team_info['team1']['name']
    away = team_info['team2']['name']
    raw_home = team_info['team1']['color']
    raw_away = team_info['team2']['color']

    # Swap one side to its alternate if the two primaries clash, then apply
    # WCAG-based lightening so both lines read against the dark background.
    swapped_home, swapped_away, _ = check_color_similarity(
        raw_home, raw_away, home, away, threshold=50, interactive=False
    )
    home_color = ensure_line_contrast(swapped_home, BG_COLOR)
    away_color = ensure_line_contrast(swapped_away, BG_COLOR)

    # ── Split shots by team, build step-line data ───────────────────────────
    home_x, home_y, home_xg = _cumulative_xg(shots, home)
    away_x, away_y, away_xg = _cumulative_xg(shots, away)

    # Safety: a match with zero shots on a side shouldn't crash
    home_shots = [s for s in shots if s[1] == home]
    away_shots = [s for s in shots if s[1] == away]
    if not home_shots and not away_shots:
        print("No shots found for either team -- cannot render xG race")
        return None

    # Extend lines to end of regulation / extra time
    has_extra_time = team_info.get('extra_time', False)
    all_shot_minutes = [s[0] for s in shots]
    max_shot_minute = max(all_shot_minutes) if all_shot_minutes else 0
    if has_extra_time:
        last_min = 125
    elif max_shot_minute > 95:
        last_min = int(max_shot_minute) + 3
    else:
        last_min = 95
    home_x.append(float(last_min)); home_y.append(home_xg)
    away_x.append(float(last_min)); away_y.append(away_xg)

    # ── Derive score from goals + own goals ─────────────────────────────────
    home_score = sum(1 for g in goal_scorers if g.get('team') == home)
    away_score = sum(1 for g in goal_scorers if g.get('team') == away)
    for og in own_goals:
        # own_goals['team'] is BENEFITING team (get_team_info convention)
        if og.get('team') == home:
            home_score += 1
        elif og.get('team') == away:
            away_score += 1

    # ── Figure + axes ───────────────────────────────────────────────────────
    fig = plt.figure(figsize=BROADCAST_FIGSIZE)
    fig.patch.set_facecolor(BG_COLOR)
    ax = fig.add_axes([0.07, 0.13, 0.88, 0.58])
    ax.set_facecolor(BG_COLOR)

    ax.grid(axis='y', color=SPINE_COLOR, alpha=0.25, linewidth=0.6, zorder=0)
    ax.grid(axis='x', color=SPINE_COLOR, alpha=0.12, linewidth=0.5, zorder=0)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(SPINE_COLOR)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=10)

    # HT line (plain dashed; red cards use dash-dot for visual distinction)
    ht_minute = team_info.get('first_half_end_minute', 45) or 45
    ax.axvline(ht_minute, color=SPINE_COLOR, linestyle='--', linewidth=0.8,
               alpha=0.5, zorder=1)

    # Step lines
    ax.step(home_x, home_y, where='post', color=home_color, linewidth=2.9,
            solid_capstyle='round', zorder=3, label=home)
    ax.step(away_x, away_y, where='post', color=away_color, linewidth=2.9,
            solid_capstyle='round', zorder=3, label=away)

    # ── Endpoint totals: xG + shot count, offset if the two teams' final
    # xG values are close enough to collide vertically ──────────────────────
    sep_threshold = max(home_xg, away_xg, 0.5) * 0.07
    if abs(home_xg - away_xg) < sep_threshold:
        delta = sep_threshold
        if home_xg >= away_xg:
            home_label_y = home_xg + delta / 2
            away_label_y = away_xg - delta / 2
        else:
            home_label_y = home_xg - delta / 2
            away_label_y = away_xg + delta / 2
    else:
        home_label_y = home_xg
        away_label_y = away_xg

    _draw_endpoint(ax, last_min, home_xg, home_label_y, home_color, len(home_shots))
    _draw_endpoint(ax, last_min, away_xg, away_label_y, away_color, len(away_shots))

    # ── Goal / own-goal / red-card labels above the plot ────────────────
    # All match events share the events row at chart top and the same
    # collision-avoidance pool. Cards differentiated by shape (rectangle vs
    # circle) and team-colored label vs universal-red marker/line.
    _all_events = []
    for g in goal_scorers:
        _all_events.append({
            'type': 'goal',
            'minute': float(g.get('minute', 0)),
            'team': g.get('team'),
            'label': g.get('player', '') + (' (P)' if g.get('pen') else ''),
            'og': False,
        })
    for og in own_goals:
        _all_events.append({
            'type': 'goal',
            'minute': float(og.get('minute', 0)),
            'team': og.get('team'),  # benefiting team
            'label': 'OG',
            'og': True,
        })
    for rc in red_cards:
        if rc.get('card_type') not in ('red', 'second_yellow'):
            continue
        _all_events.append({
            'type': 'rc',
            'minute': float(rc.get('minute', 0)),
            'team': rc.get('team'),
            'label': rc.get('player', ''),
        })
    _all_events.sort(key=lambda x: x['minute'])

    # Running score + side classification (only goals contribute to score)
    _h = _a = 0
    for ev in _all_events:
        if ev['type'] == 'goal':
            if ev['team'] == home:
                _h += 1
                ev['side'] = 'home'
            else:
                _a += 1
                ev['side'] = 'away'
            ev['score'] = f"{_h}-{_a}"
        else:
            # Red card: side via fuzzy match on home/away name
            t = (ev.get('team') or '').lower()
            affected_home = t and (t in home.lower() or home.lower() in t)
            ev['side'] = 'home' if affected_home else 'away'

    # Axes ranges. 1.05 y-multiplier = just enough sliver above the winning
    # line for the endpoint marker; no empty sky above.
    max_xg = max(home_xg, away_xg, 0.5) * 1.05
    ax.set_xlim(0, last_min + 5)  # +5 for endpoint label breathing room
    ax.set_ylim(0, max_xg)
    if last_min <= 95:
        ax.set_xticks([0, 15, 30, 45, 60, 75, 90])
    else:
        ax.set_xticks(list(range(0, last_min + 1, 15)))

    # Place all event labels above the plot with collision avoidance (mutates
    # _all_events in place, adding 'x_side' and 'y_level' per event).
    _place_goal_labels(_all_events, chart_max=float(last_min))
    _label_transform = blended_transform_factory(ax.transData, ax.transAxes)

    _RC_COLOR = '#E53935'

    for ev in _all_events:
        side_color = home_color if ev['side'] == 'home' else away_color
        flip_left = ev['x_side'] == 'left'
        label_y = GOAL_LABEL_Y_LEVELS[ev['y_level']]
        label_ha = 'right' if flip_left else 'left'

        if ev['type'] == 'goal':
            xs = home_x if ev['side'] == 'home' else away_x
            ys = home_y if ev['side'] == 'home' else away_y

            if ev['og']:
                marker_m = ev['minute']  # no shot event for OG; line doesn't step
            else:
                marker_m = _precise_goal_minute(shots, ev['team'], int(ev['minute']))
            y_at = _xg_at_minute(xs, ys, marker_m)

            # Team-colored dotted vertical from top of plot DOWN TO marker on step
            ax.plot([marker_m, marker_m], [y_at, max_xg],
                    color=side_color, linewidth=1.0, linestyle=':',
                    alpha=0.8, zorder=1, solid_capstyle='round')

            # Marker on the step line
            if ev['og']:
                ax.plot(marker_m, y_at, marker='o', markersize=11,
                        markerfacecolor=BG_COLOR, markeredgecolor=side_color,
                        markeredgewidth=2.0, zorder=6)
            else:
                ax.plot(marker_m, y_at, marker='o', markersize=11,
                        markerfacecolor=side_color, markeredgecolor='white',
                        markeredgewidth=1.5, zorder=6)

            # Anchor dot at top of plot for the label
            ax.plot(marker_m, 1.005, 'o', transform=_label_transform,
                    color=side_color, markersize=8, markeredgecolor='white',
                    markeredgewidth=1.0, clip_on=False, zorder=5)

            label_x = marker_m - 0.6 if flip_left else marker_m + 0.6
            text = f"{ev['label']} ({int(ev['minute'])}')\n{ev['score']}"
            ax.text(label_x, label_y, text,
                    transform=_label_transform, color=side_color,
                    fontsize=13, fontweight='bold', va='bottom', ha=label_ha,
                    fontstyle='italic' if ev['og'] else 'normal',
                    clip_on=False)

        else:  # 'rc'
            m = ev['minute']
            # Universal-red dash-dot line spanning the chart
            ax.axvline(m, color=_RC_COLOR, linewidth=1.0,
                       linestyle='-.', alpha=0.75, zorder=2)

            # Card-shaped marker at chart top edge — distinct from circles
            card_w_min = 0.7
            card_h_axes = 0.028
            card = mpatches.Rectangle(
                (m - card_w_min / 2, 1.0),
                card_w_min, card_h_axes,
                facecolor=_RC_COLOR, edgecolor='white', linewidth=1.5,
                transform=_label_transform, clip_on=False, zorder=6,
            )
            ax.add_patch(card)

            label_x = m - 0.6 if flip_left else m + 0.6
            player = ev.get('label', '')
            text = (f"{player} ({int(m)}')\nRED CARD"
                    if player else f"RED CARD ({int(m)}')")
            ax.text(label_x, label_y, text,
                    transform=_label_transform, color=side_color,
                    fontsize=13, fontweight='bold', va='bottom', ha=label_ha,
                    clip_on=False)

    # HT label at the top of the HT axvline
    ax.text(ht_minute, max_xg * 0.97, 'HALF TIME', color=TEXT_SECONDARY,
            fontsize=11, fontweight='bold', ha='center', va='top',
            alpha=0.85, bbox=dict(facecolor=BG_COLOR, edgecolor='none', pad=2))

    # Axis labels -- kicker carries chart-type ID; these describe axes
    ax.set_xlabel('MINUTE', color=TEXT_SECONDARY, fontsize=11,
                  fontweight='bold', labelpad=8)
    ax.set_ylabel('CUMULATIVE xG', color=TEXT_SECONDARY, fontsize=11,
                  fontweight='bold', labelpad=10)

    # ── Header: kicker + score title + accent bar + subtitle ────────────────
    score_title = f"{home.upper()} {home_score}-{away_score} {away.upper()}"
    custom_title = team_info.get('custom_title')
    custom_subtitle = team_info.get('custom_subtitle')

    # Kicker -- small, uppercase, letter-spaced, muted
    fig.text(0.5, 0.973, 'x G   R A C E', fontsize=11, fontweight='bold',
             color=TEXT_SECONDARY, ha='center', va='center')

    # Score title (user override if provided)
    title_text = custom_title or score_title
    score_obj = fig.text(0.5, 0.942, title_text, fontsize=22, fontweight='bold',
                          color=TEXT_PRIMARY, ha='center', va='center')
    fig.canvas.draw()
    sb = score_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    sb_fig = sb.transformed(fig.transFigure.inverted())

    # Two-color accent bar under score title (team identity)
    bar_y = 0.912
    bar_h = 0.005
    mid = sb_fig.x0 + sb_fig.width / 2
    fig.patches.append(Rectangle(
        (sb_fig.x0, bar_y), sb_fig.width / 2, bar_h,
        transform=fig.transFigure, facecolor=home_color,
        edgecolor='none', zorder=10,
    ))
    fig.patches.append(Rectangle(
        (mid, bar_y), sb_fig.width / 2, bar_h,
        transform=fig.transFigure, facecolor=away_color,
        edgecolor='none', zorder=10,
    ))

    # Subtitle: competition + date (user override if provided)
    competition = team_info.get('competition', '')
    match_date = team_info.get('date', '')
    subtitle_parts = [p for p in (competition.upper() if competition else '', match_date) if p]
    subtitle_text = custom_subtitle or ' | '.join(subtitle_parts)
    if subtitle_text:
        fig.text(0.5, 0.885, subtitle_text, ha='center',
                 color=TEXT_SECONDARY, fontsize=11)

    add_cbs_footer(fig)
    return fig


def run(config):
    """Entry point for launcher - config contains all needed params.

    Config keys:
        data_source: str - 'trumedia', 'fbref', or 'manual'
        file_path: str - Path to TruMedia CSV (required for 'trumedia')
        output_folder: str - Where to save charts
        save: bool - Whether to save the chart (default True)
        competition: str - Competition name (optional, skips prompt if provided)
        own_goals: list - List of own goal dicts with 'minute' and 'team' keys (optional)
    """
    data_source = config.get('data_source', 'trumedia')
    output_folder = config.get('output_folder', '.')
    save = config.get('save', True)

    # Get shot data based on data source
    if data_source == 'trumedia':
        file_path = config['file_path']
        shots, match_info, team_colors = parse_trumedia_csv(file_path)
        if not shots:
            print("\nError: No shot data found in CSV.")
            return
    else:
        # For fbref/manual, fall back to interactive mode
        shots, match_info, team_colors, data_source = get_data_source()
        if not shots:
            print("\nError: No shot data found. Please try again.")
            return

    print(f"\n[OK] Loaded {len(shots)} shots")

    # Get team information (pass team_colors for auto-detection)
    # Also pass config for competition and own_goals to skip prompts
    team_info = get_team_info(shots, match_info, team_colors, config)
    team_info['data_source'] = data_source

    # Create chart
    print("\n" + "="*60)
    print("GENERATING CBS SPORTS CHART...")
    print("="*60)

    fig = create_xg_chart(shots, team_info)

    # Check if chart creation failed
    if fig is None:
        print("\n[ERROR] Chart generation failed. Please check team names and try again.")
        return

    # Save if requested
    if save:
        # Build filename from team names
        team1 = team_info['team1']['name'].replace(' ', '_')
        team2 = team_info['team2']['name'].replace(' ', '_')
        filename = f"xg_race_{team1}_vs_{team2}.png"
        filepath = os.path.join(output_folder, filename)
        fig.savefig(filepath, dpi=300, bbox_inches='tight',
                   facecolor='#1A2332', edgecolor='none')
        print(f"\n[OK] Chart saved as {filepath}")

    plt.close(fig)
    print("\nDone!")


def main():
    """Standalone entry point - prompts user for inputs."""
    print("\n" + "="*60)
    print("CBS SPORTS xG RACE CHART BUILDER")
    print("="*60)
    print("This tool creates CBS Sports styled xG race charts")

    # Get data (from URL, manual paste, or TruMedia CSV)
    shots, match_info, team_colors, data_source = get_data_source()

    if not shots:
        print("\nError: No shot data found. Please try again.")
        return

    print(f"\n[OK] Loaded {len(shots)} shots")

    # Get team information (pass team_colors for auto-detection)
    team_info = get_team_info(shots, match_info, team_colors)
    team_info['data_source'] = data_source

    # Create and display chart
    print("\n" + "="*60)
    print("GENERATING CBS SPORTS CHART...")
    print("="*60)

    fig = create_xg_chart(shots, team_info)

    # Check if chart creation failed
    if fig is None:
        print("\n[ERROR] Chart generation failed. Please check team names and try again.")
        return

    # Ask if user wants to save
    save = input("\nSave chart as image? (y/n): ").strip().lower()
    if save == 'y':
        filename = input("Filename (default: xg_chart.png): ").strip() or "xg_chart.png"
        if not filename.endswith('.png'):
            filename += '.png'
        fig.savefig(filename, dpi=300, bbox_inches='tight',
                   facecolor='#1A2332', edgecolor='none')
        print(f"[OK] Chart saved as {filename}")

    plt.show()
    print("\nDone!")

if __name__ == "__main__":
    main()