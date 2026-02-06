import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
from matplotlib import patheffects
import numpy as np
import os
from datetime import datetime
import re

# Import shared utilities
from shared.colors import (
    TEAM_COLORS, load_custom_colors, save_custom_color, get_team_color,
    hex_to_rgb, color_distance, check_color_similarity, fuzzy_match_team,
    prompt_ambiguous_choice
)
from shared.styles import BG_COLOR

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

def parse_shot_data_manual():
    """Get shot data from user input (manual method)"""
    print("\n" + "="*60)
    print("PASTE YOUR SHOT DATA")
    print("="*60)
    print("Paste the formatted shot list, then press Enter twice:\n")
    print("Example format:")
    print("shots = [")
    print('    (22, "Real Madrid", 0.54, "Goal"),')
    print('    (38, "Barcelona", 0.36, "Goal"),')
    print("]")
    print("\nPaste here:")

    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)

    # Parse the pasted data
    full_text = '\n'.join(lines)

    # Execute the pasted code to extract the shots list
    local_vars = {}
    exec(full_text, {}, local_vars)
    shots = local_vars.get('shots', [])

    return shots, None

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

                        # Track first half end minute
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
                    # Prompt user to choose
                    chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous)
                    if chosen_color:
                        return chosen_color, "database", chosen_name
                    # User chose "none of these" - fall through to manual entry
                else:
                    return color, "database", matched_name
            # 3. Check custom saved colors (fuzzy match)
            color, matched_name, ambiguous = fuzzy_match_team(team_name, custom_colors)
            if color:
                if ambiguous and len(ambiguous) > 1:
                    chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous)
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
    if 'competition' in config and config['competition']:
        competition = config['competition']
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


def create_gradient_background(ax, color1='#FFFFFF', color2='#F0F0F0'):
    """Create a glossy gradient background with shine effect"""
    gradient = np.linspace(0, 1, 512).reshape(512, 1)
    gradient = np.hstack((gradient, gradient))
    
    rgb1 = hex_to_rgb(color1)
    rgb2 = hex_to_rgb(color2)
    
    # Create color array with glossy curve
    colors = np.zeros((512, 2, 3))
    for i in range(3):
        # Add a glossy curve to the gradient
        glossy_curve = gradient ** 0.7  # Makes it more glossy
        colors[:, :, i] = rgb1[i] + (rgb2[i] - rgb1[i]) * glossy_curve
    
    ax.imshow(colors, extent=[ax.get_xlim()[0], ax.get_xlim()[1], 
                              ax.get_ylim()[0], ax.get_ylim()[1]], 
              aspect='auto', zorder=0, alpha=0.5)

def create_xg_chart(shots, team_info):
    """Create the xG race chart with CBS Sports styling and enhancements"""
    # Separate shots by team
    team1_name = team_info['team1']['name']
    team2_name = team_info['team2']['name']

    # Check if we need to use different line styles for similar colors
    use_different_styles = team_info.get('different_line_styles', False)
    team2_linestyle = '--' if use_different_styles else '-'

    team1_shots = sorted([(m, xg, outcome) for m, team, xg, outcome in shots if team == team1_name], key=lambda x: x[0])
    team2_shots = sorted([(m, xg, outcome) for m, team, xg, outcome in shots if team == team2_name], key=lambda x: x[0])
    
    # SAFETY CHECK: Handle teams with zero shots
    if not team1_shots and not team2_shots:
        print("\n⚠ Error: No shots found for either team!")
        print("This might be due to team name mismatch.")
        print(f"Expected: {team1_name} and {team2_name}")
        print(f"Found in data: {list(set(shot[1] for shot in shots))}")
        return None
    
    # Handle case where one team has no shots (rare but possible)
    if not team1_shots:
        print(f"\n⚠ Warning: {team1_name} has ZERO shots in this match!")
        team1_shots = [(0, 0.0, "None")]  # Dummy shot to prevent crashes
    if not team2_shots:
        print(f"\n⚠ Warning: {team2_name} has ZERO shots in this match!")
        team2_shots = [(0, 0.0, "None")]  # Dummy shot to prevent crashes
    
    # Calculate cumulative xG
    team1_minutes = [0] + [shot[0] for shot in team1_shots]
    team1_xg_cumulative = [0]
    for shot in team1_shots:
        team1_xg_cumulative.append(team1_xg_cumulative[-1] + shot[1])
    
    team2_minutes = [0] + [shot[0] for shot in team2_shots]
    team2_xg_cumulative = [0]
    for shot in team2_shots:
        team2_xg_cumulative.append(team2_xg_cumulative[-1] + shot[1])
    
    # Count goals and calculate final xG
    team1_goals = sum(1 for shot in team1_shots if shot[2] == "Goal")
    team2_goals = sum(1 for shot in team2_shots if shot[2] == "Goal")
    
    # Add own goals to goal count
    own_goals = team_info.get('own_goals', [])
    for og in own_goals:
        if og['team'] == team1_name:
            team1_goals += 1
        elif og['team'] == team2_name:
            team2_goals += 1
    
    final_xg1 = team1_xg_cumulative[-1]
    final_xg2 = team2_xg_cumulative[-1]
    
    # Determine x-axis limit based on extra time and actual shot data
    has_extra_time = team_info.get('extra_time', False)

    # Find the latest shot minute from actual data
    all_shot_minutes = [shot[0] for shot in team1_shots] + [shot[0] for shot in team2_shots]
    max_shot_minute = max(all_shot_minutes) if all_shot_minutes else 0

    if has_extra_time:
        x_limit = 125  # Extra time extends to ~120 minutes
        print("✓ Chart adjusted for extra time (0-125 minutes)")
    elif max_shot_minute > 95:
        # Extend for late stoppage time shots
        x_limit = max_shot_minute + 3
        print(f"✓ Chart extended for stoppage time (0-{x_limit} minutes)")
    else:
        x_limit = 95  # Regular time
    
    # Extend lines to end of match
    team1_minutes.append(x_limit)
    team1_xg_cumulative.append(final_xg1)
    team2_minutes.append(x_limit)
    team2_xg_cumulative.append(final_xg2)
    
    # SAFETY CHECK: Ensure reasonable y-axis scaling
    max_xg = max(final_xg1, final_xg2)
    if max_xg < 0.1:
        # Very low xG match - set minimum y-axis to 0.5 for readability
        y_limit = 0.5
        print("✓ Chart adjusted for low xG match (minimum y-axis: 0.5)")
    else:
        y_limit = max_xg * 1.15
    
    # CBS Sports styling setup
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor('#1A2332')  # Darker cinematic outer
    ax.set_facecolor('#FFFFFF')  # Pure white inner for contrast
    
    # Set axis limits first for gradient
    ax.set_xlim(0, x_limit)
    ax.set_ylim(0, y_limit)
    
    # CINEMATIC EFFECT 1: Rich gradient background
    create_gradient_background(ax, '#F8FBFF', '#D5E5F5')
    
    # Vignette effect rectangles adjusted for dynamic x_limit
    from matplotlib.patches import Rectangle
    # Top vignette
    for i in range(20):
        alpha = 0.015 * (i / 20)
        rect = Rectangle((0, ax.get_ylim()[1] * (1 - i/40)), x_limit, ax.get_ylim()[1] * (i/40),
                        facecolor='#1A2332', alpha=alpha, zorder=0.3, transform=ax.transData)
        ax.add_patch(rect)
    # Bottom vignette
    for i in range(20):
        alpha = 0.015 * (i / 20)
        rect = Rectangle((0, 0), x_limit, ax.get_ylim()[1] * (i/40),
                        facecolor='#1A2332', alpha=alpha, zorder=0.3, transform=ax.transData)
        ax.add_patch(rect)
    # Left vignette
    for i in range(15):
        alpha = 0.02 * (i / 15)
        rect = Rectangle((0, 0), i*2, ax.get_ylim()[1],
                        facecolor='#1A2332', alpha=alpha, zorder=0.3, transform=ax.transData)
        ax.add_patch(rect)
    # Right vignette
    for i in range(15):
        alpha = 0.02 * (i / 15)
        rect = Rectangle((x_limit - i*2, 0), i*2, ax.get_ylim()[1],
                        facecolor='#1A2332', alpha=alpha, zorder=0.3, transform=ax.transData)
        ax.add_patch(rect)
    
    # Add subtle radial glow from top center
    y_vals = np.linspace(0, ax.get_ylim()[1], 100)
    for i, y in enumerate(y_vals[:40]):
        alpha = 0.015 * (40 - i) / 40
        ax.axhspan(y, y + (ax.get_ylim()[1] / 100), color='white', alpha=alpha, zorder=0.5)
    
    # CINEMATIC EFFECT 3: Inner glow effect (darker edges, lighter center)
    # Draw darker outer edge first
    ax.step(team1_minutes, team1_xg_cumulative, where='post',
            color='#000000', linewidth=8, linestyle='-',
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.25, zorder=2.3)
    ax.step(team2_minutes, team2_xg_cumulative, where='post',
            color='#000000', linewidth=8, linestyle=team2_linestyle,
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.25, zorder=2.3)

    # Draw slightly lighter middle layer
    ax.step(team1_minutes, team1_xg_cumulative, where='post',
            color=team_info['team1']['color'], linewidth=7, linestyle='-',
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.6, zorder=2.5)
    ax.step(team2_minutes, team2_xg_cumulative, where='post',
            color=team_info['team2']['color'], linewidth=7, linestyle=team2_linestyle,
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.6, zorder=2.5)

    # CINEMATIC EFFECT 4: Bright center glow for inner glow effect
    # Brightest center layer (creates the "inner glow")
    ax.step(team1_minutes, team1_xg_cumulative, where='post',
            color=team_info['team1']['color'], linewidth=4, linestyle='-',
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.9, zorder=2.7)
    ax.step(team2_minutes, team2_xg_cumulative, where='post',
            color=team_info['team2']['color'], linewidth=4, linestyle=team2_linestyle,
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.9, zorder=2.7)

    # Thin bright highlight in the very center
    ax.step(team1_minutes, team1_xg_cumulative, where='post',
            color='white', linewidth=1.5, linestyle='-',
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.4, zorder=2.8)
    ax.step(team2_minutes, team2_xg_cumulative, where='post',
            color='white', linewidth=1.5, linestyle=team2_linestyle,
            solid_capstyle='round', solid_joinstyle='round',
            alpha=0.4, zorder=2.8)
    
    # Plot the main xG lines on top
    line1 = ax.step(team1_minutes, team1_xg_cumulative, where='post',
            color=team_info['team1']['color'], linewidth=6,
            linestyle='-',
            solid_capstyle='round', solid_joinstyle='round',
            label=team1_name.upper(), zorder=3, antialiased=True)
    line2 = ax.step(team2_minutes, team2_xg_cumulative, where='post',
            color=team_info['team2']['color'], linewidth=6,
            linestyle=team2_linestyle,
            solid_capstyle='round', solid_joinstyle='round',
            label=team2_name.upper(), zorder=3, antialiased=True)
    
    # CINEMATIC EFFECT 5: Enhanced goal markers with dramatic glow
    for shot in team1_shots:
        if shot[2] == "Goal":
            idx = team1_shots.index(shot) + 1
            # Dramatic multi-layer glow
            ax.plot(team1_minutes[idx], team1_xg_cumulative[idx], 
                   'o', color=team_info['team1']['color'], markersize=40, 
                   alpha=0.08, zorder=4)
            ax.plot(team1_minutes[idx], team1_xg_cumulative[idx], 
                   'o', color=team_info['team1']['color'], markersize=32, 
                   alpha=0.15, zorder=4.1)
            ax.plot(team1_minutes[idx], team1_xg_cumulative[idx], 
                   'o', color=team_info['team1']['color'], markersize=24, 
                   alpha=0.35, zorder=4.2)
            # Main goal marker with thick glossy border
            ax.plot(team1_minutes[idx], team1_xg_cumulative[idx], 
                   'o', color=team_info['team1']['color'], markersize=16, 
                   markeredgecolor='white', markeredgewidth=4, zorder=5)
            # Glossy highlight - larger and more prominent
            ax.plot(team1_minutes[idx], team1_xg_cumulative[idx], 
                   'o', color='white', markersize=7, 
                   alpha=0.8, zorder=5.5)
    
    for shot in team2_shots:
        if shot[2] == "Goal":
            idx = team2_shots.index(shot) + 1
            # Dramatic multi-layer glow
            ax.plot(team2_minutes[idx], team2_xg_cumulative[idx], 
                   'o', color=team_info['team2']['color'], markersize=40, 
                   alpha=0.08, zorder=4)
            ax.plot(team2_minutes[idx], team2_xg_cumulative[idx], 
                   'o', color=team_info['team2']['color'], markersize=32, 
                   alpha=0.15, zorder=4.1)
            ax.plot(team2_minutes[idx], team2_xg_cumulative[idx], 
                   'o', color=team_info['team2']['color'], markersize=24, 
                   alpha=0.35, zorder=4.2)
            # Main goal marker with thick glossy border
            ax.plot(team2_minutes[idx], team2_xg_cumulative[idx], 
                   'o', color=team_info['team2']['color'], markersize=16, 
                   markeredgecolor='white', markeredgewidth=4, zorder=5)
            # Glossy highlight
            ax.plot(team2_minutes[idx], team2_xg_cumulative[idx], 
                   'o', color='white', markersize=7, 
                   alpha=0.8, zorder=5.5)
    
    # Mark own goals with dramatic glossy square markers
    for og in own_goals:
        minute = og['minute']
        team = og['team']
        
        # Determine which team's line to mark and what color to use
        if team == team1_name:
            # Find xG value at this minute on team1's line
            xg_at_minute = team1_xg_cumulative[0]
            for i, m in enumerate(team1_minutes[1:], 1):
                if m <= minute:
                    xg_at_minute = team1_xg_cumulative[i]
            color = team_info['team1']['color']
        else:
            # Find xG value at this minute on team2's line
            xg_at_minute = team2_xg_cumulative[0]
            for i, m in enumerate(team2_minutes[1:], 1):
                if m <= minute:
                    xg_at_minute = team2_xg_cumulative[i]
            color = team_info['team2']['color']
        
        # Dramatic multi-layer glow for own goal
        ax.plot(minute, xg_at_minute, 's', color=color, markersize=40, 
               alpha=0.08, zorder=4)
        ax.plot(minute, xg_at_minute, 's', color=color, markersize=32, 
               alpha=0.15, zorder=4.1)
        ax.plot(minute, xg_at_minute, 's', color=color, markersize=24, 
               alpha=0.35, zorder=4.2)
        # Main own goal marker with thick glossy border
        ax.plot(minute, xg_at_minute, 's', color=color, markersize=16, 
               markeredgecolor='white', markeredgewidth=4, zorder=5)
        # Glossy highlight
        ax.plot(minute, xg_at_minute, 's', color='white', markersize=7, 
               alpha=0.8, zorder=5.5)
    
    # ENHANCEMENT 3: Half-time marker with cinematic style (dynamic position)
    ht_minute = team_info.get('first_half_end_minute', 45)
    ax.axvline(x=ht_minute, color='#00325B', linestyle='--', linewidth=2.5,
               alpha=0.4, zorder=1)
    # Add glow to HT marker
    ax.axvline(x=ht_minute, color='white', linestyle='--', linewidth=1,
               alpha=0.2, zorder=1.1)
    ax.text(ht_minute, ax.get_ylim()[1] * 0.98, 'HT', ha='center', va='top',
           fontsize=11, fontweight='bold', color='#00325B',
           bbox=dict(boxstyle='round,pad=0.4', facecolor='white', 
                    edgecolor='#00325B', linewidth=2, alpha=0.95))
    
    # CBS Sports axis styling - brighter for visibility
    ax.set_xlabel('MINUTE', fontsize=14, fontweight='bold', 
                 color='#FFFFFF', fontfamily='sans-serif', labelpad=10)
    ax.set_ylabel('CUMULATIVE xG', fontsize=14, fontweight='bold', 
                 color='#FFFFFF', fontfamily='sans-serif', labelpad=10)
    
    # CBS Sports title structure
    # Main title with score - WHITE for contrast against dark background
    main_title = f'{team1_name.upper()} {team1_goals}-{team2_goals} {team2_name.upper()}'
    fig.text(0.5, 0.97, main_title, 
            ha='center', fontsize=24, fontweight='bold', 
            color='#FFFFFF', fontfamily='sans-serif',
            path_effects=[patheffects.withStroke(linewidth=3, foreground='#1A2332')])
    
    # Chart type label
    fig.text(0.5, 0.94, 'xG RACE CHART', 
            ha='center', fontsize=12, fontweight='normal', 
            color='#8BA3B8', fontfamily='sans-serif', style='italic')
    
    # Subtitle with competition, date, and xG totals - Light gray for readability
    if team_info.get('date'):
        subtitle = f'{team_info["competition"]} | {team_info["date"]} | xG: {final_xg1:.2f} - {final_xg2:.2f}'
    else:
        subtitle = f'{team_info["competition"]} | xG: {final_xg1:.2f} - {final_xg2:.2f}'
    fig.text(0.5, 0.915, subtitle, 
            ha='center', fontsize=13, color='#B8C5D6', 
            fontfamily='sans-serif')
    
    # CBS Sports grid - REMOVED for cleaner picture-like aesthetic
    # Grid removed entirely
    ax.set_axisbelow(True)
    
    # CBS Sports legend - add own goal marker if present
    legend_elements = [
        plt.Line2D([0], [0], color=team_info['team1']['color'], linewidth=4, label=team1_name.upper()),
        plt.Line2D([0], [0], color=team_info['team2']['color'], linewidth=4, label=team2_name.upper())
    ]
    
    if own_goals:
        legend_elements.append(
            plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='gray', 
                      markersize=10, label='GOAL', linestyle='None',
                      markeredgecolor='white', markeredgewidth=2)
        )
        legend_elements.append(
            plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='gray', 
                      markersize=10, label='OWN GOAL', linestyle='None',
                      markeredgecolor='white', markeredgewidth=2)
        )
    
    legend = ax.legend(handles=legend_elements, fontsize=12, loc='upper left', 
                      framealpha=1, edgecolor='#00325B', fancybox=False, 
                      frameon=True, handlelength=1.5)
    legend.get_frame().set_facecolor('white')
    legend.get_frame().set_linewidth(2)
    
    # CBS Sports branding footer
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10,
            fontweight='bold', color='#00325B', fontfamily='sans-serif')

    # Data source label based on input method (TruMedia = no label)
    data_source = team_info.get('data_source', 'fbref')
    if data_source == 'fbref':
        fig.text(0.98, 0.01, 'DATA: FBREF', fontsize=8,
                color='#999999', ha='right', fontfamily='sans-serif')
    elif data_source == 'manual':
        fig.text(0.98, 0.01, 'DATA: MANUAL', fontsize=8,
                color='#999999', ha='right', fontfamily='sans-serif')
    # TruMedia: no data source label displayed
    
    # CBS Sports spines - softer, more artistic
    ax.spines['top'].set_visible(False)
    ax.spines['left'].set_linewidth(2.5)
    ax.spines['bottom'].set_linewidth(2.5)
    ax.spines['right'].set_visible(False)  # Remove right spine for cleaner look
    ax.spines['left'].set_color('#00325B')
    ax.spines['bottom'].set_color('#00325B')
    ax.spines['left'].set_alpha(0.6)  # Softer opacity
    ax.spines['bottom'].set_alpha(0.6)
    
    # CBS Sports ticks - keep but softer
    ax.tick_params(axis='both', labelsize=11, width=1.5, colors='#556B7F', length=5)
    # Make tick labels slightly transparent by using a lighter color
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_alpha(0.8)
    
    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
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