import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as mpe
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
    prompt_ambiguous_choice, ensure_line_contrast, separate_line_luminance,
)
from shared.styles import (
    footer_y,
    BG_COLOR, SPINE_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    add_cbs_footer, BROADCAST_FIGSIZE, render_two_team_score_header,
    resolve_figsize, fit_fontsize, draw_event_block,
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
                
                # Manual entry has no period info; default to 1. xG race
                # charts from manual data won't have Period 2 events anyway -
                # this default just keeps the tuple shape consistent.
                shots.append((minute, squad, xg, outcome, 1))
                
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
                return None, None, None, None

            shots = []
            goal_scorers = []  # match-side: matches Match Momentum's goal_scorers shape
            team_colors = {}
            match_info = None
            has_extra_time = False
            penalty_shootout_excluded = 0
            first_half_end_minute = 45  # Default, will update based on Period 1 shots

            for row in reader:
                # When the first half ENDED, taken from EVERY period-1 row -
                # not only the shots below. The x-axis shifts period 2+ forward
                # by (first_half_end_minute - 45), so inferring the boundary
                # from shots made the shift an artifact of when someone last
                # had an attempt. Measured over 7,083 matches: shot-derived
                # sits at exactly 45:00 in 50% of them, while only 15% of
                # halves really end between 45 and 46.
                try:
                    _p = int(row[period_idx]) if (
                        period_idx and len(row) > period_idx
                        and row[period_idx]) else 1
                    if _p == 1 and len(row) > game_clock_idx and row[game_clock_idx]:
                        first_half_end_minute = max(
                            first_half_end_minute,
                            float(row[game_clock_idx]) / 60)
                except (ValueError, TypeError):
                    pass

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

                        shots.append((minute, team, xg, outcome, period))

                        # Goal scorers: same shape as get_goal_scorers_for_game
                        # (DB pipeline) and _parse_momentum_csv. Skip rows
                        # without a shooter name even though playType says Goal.
                        if play_type in ('Goal', 'PenaltyGoal'):
                            shooter_name = row[shooter_idx]
                            goal_scorers.append({
                                'minute':  int(minute),
                                'period':  period,
                                'player':  shooter_name,
                                'team':    team,
                                'team_id': None,
                                'pen':     play_type == 'PenaltyGoal',
                            })

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
                # Sort by (period, minute) so first-half stoppage goals
                # (45+4 -> minute 49) precede second-half early goals
                # (46' -> minute 46) instead of inverting on minute alone.
                goal_scorers.sort(key=lambda g: (g.get('period', 1), g['minute']))
                return shots, match_info, team_colors, goal_scorers
            else:
                print("⚠ No shot data found in CSV")
                return None, None, None, None

    except FileNotFoundError:
        print(f"⚠ File not found: {file_path}")
        return None, None, None, None
    except Exception as e:
        print(f"⚠ Error reading CSV: {e}")
        return None, None, None, None

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
            shots, match_info, team_colors, goal_scorers = parse_trumedia_csv(file_path)
            if shots:
                if match_info is not None and goal_scorers:
                    match_info['goal_scorers'] = goal_scorers
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

def _separate_using_secondary(color1, color2, secondary1, secondary2,
                              bg_color=BG_COLOR, min_distance=150):
    """Pull two clashing lines apart using a club's OWN second colour.

    Returns (color1, color2, resolved) - `resolved` False means the registry
    had nothing usable and the caller should fall back to the old guard.

    Only ONE side moves, and it is the second-named team where either would
    work. Moving both makes a chart where neither team wears its own colour,
    to fix a problem that only needed one of them to change.

    EVERY comparison here is made on the LIFTED colours - what will actually be
    painted - never on the stored values. Chelsea `#001489` and Brighton
    `#005DAA` look distinct as stored, but both are below the visibility floor,
    so both get lightened to `#526AFE` and `#0089FA` - distance 21, plainly the
    same blue. Judging the stored values answers a question about colours
    nobody sees.

    A clash is a question of perceptual DISTANCE, not of luminance. This
    originally compared only each colour's contrast ratio against the
    background, which is hue-blind: Strasbourg's light blue and Monaco's red
    land within 1.5x of each other in luminance, so red-versus-blue read as a
    clash and Monaco was silently drawn in its white secondary. Measured over
    479 real matchups, that fired on 58% of them, and in 31% the two drawn
    colours were plainly distinguishable. RGB distance at the old guard's own
    threshold (150, check_color_similarity's) keeps Chelsea-Brighton firing
    and lets red-versus-blue through.
    """
    from shared.colors import color_distance

    def lift(c):
        return ensure_line_contrast(c, bg_color, min_ratio=3.5)

    def separated(a, b):
        return color_distance(a, b) >= min_distance

    drawn1, drawn2 = lift(color1), lift(color2)
    if separated(drawn1, drawn2):
        return color1, color2, False   # no clash; nothing to resolve

    # Try the SECOND team's secondary first, so the first-named team keeps its
    # own colour wherever one move is enough.
    for candidate, keep, swap_first in ((secondary2, color1, False),
                                        (secondary1, color2, True)):
        if not candidate:
            continue
        if separated(lift(keep), lift(candidate)):
            return ((candidate, keep, True) if swap_first
                    else (keep, candidate, True))

    return color1, color2, False


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

        # Two teams that clash reach for the REGISTRY's secondary first - the
        # club's own second colour, authored and sourced. Only if the registry
        # has nothing for either side does this fall back to
        # check_color_similarity, which draws on the old alternate dictionary
        # and is 77% pure white or black.
        #
        # Chelsea v Brighton is the case: #001489 against #005DAA, two dark
        # blues. The old guard resolved it by making Chelsea YELLOW. Chelsea's
        # registered secondary is white.
        secondaries = match_info.get('team_secondaries') or {}
        color1, color2, resolved_by_registry = _separate_using_secondary(
            color1, color2, secondaries.get(team1), secondaries.get(team2)
        )
        if resolved_by_registry:
            use_different_line_styles = False
        else:
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

    # TruMedia's own abbreviations, so the endpoint figures can say WHOSE they
    # are. Absent for non-TruMedia sources, and the chart falls back to
    # unprefixed values rather than inventing one.
    _abbrevs = (match_info or {}).get('team_abbrevs') or {}

    return {
        'team1': {'name': team1, 'color': color1,
                  'abbrev': _abbrevs.get(team1)},
        'team2': {'name': team2, 'color': color2,
                  'abbrev': _abbrevs.get(team2)},
        'competition': competition,
        'date': match_date,
        'own_goals': own_goals,
        'extra_time': has_extra_time,
        'different_line_styles': use_different_line_styles,
        'first_half_end_minute': match_info.get('first_half_end_minute', 45)
    }


# ── Step-line geometry helpers ──────────────────────────────────────────────


def _shot_chrono_x(minute, period, ht_minute):
    """Translate (broadcast_minute, period) into chronological match time.

    Mirrors the momentum chart's _chrono_minute. Period 1 events stay at
    their broadcast minute; Period 2+ shift forward by (ht_minute - 45)
    so they plot AFTER Period 1 ends instead of overlapping with first-
    half stoppage (49' vs 46' problem). Same formula handles extra time
    (period 3, 4) by the same offset since their broadcast clocks
    restart at fixed regulation marks.
    """
    try:
        p = int(period)
    except (ValueError, TypeError):
        p = 1
    if p <= 1:
        return float(minute)
    return float(minute) + (float(ht_minute) - 45.0)


def _cumulative_xg(shots, team_name, ht_minute=45.0):
    """Build (xs, ys, total_xg) for a team's cumulative xG step line.

    Each shot contributes two points at the same minute: (m, before) and
    (m, after). Step-post rendering uses these to produce the vertical
    jump at each shot.

    xs are CHRONOLOGICAL match minutes (period 1 at their broadcast
    minute; period 2+ shifted forward by Period 1 stoppage). This keeps
    the step line strictly monotonic across the half-time boundary -
    Period 2 shots plot to the right of Period 1 stoppage shots even
    when their broadcast minute would put them earlier on the timeline.
    """
    pts = sorted(
        [(_shot_chrono_x(m, p, ht_minute), float(xg))
         for (m, t, xg, _o, p) in shots if t == team_name],
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
    """Return the step-line y-value at a given chronological-minute.

    `xs` and `minute` must both be in the chronological frame produced
    by _shot_chrono_x (so the goal-marker lookup matches the step-line
    coordinates, not the raw broadcast minute).

    Matches the 'post' step semantics: for a minute at which a shot
    occurs, returns the post-step (after-shot) value because the
    duplicate (m, after) point follows the (m, before) point in the
    xs/ys sequence.
    """
    last = 0.0
    for x, y in zip(xs, ys):
        if x <= minute:
            last = y
        else:
            break
    return last


def _precise_goal_minute(shots, team, int_min, period, ht_minute=45.0):
    """Resolve a (period, integer goal minute) to the precise chronological
    float-minute of the matching shot on the step line.

    goal_scorers carries integer broadcast minutes (floor of
    gameClock/60) + period. shots carry exact float minute + period. We
    match on (team, int(broadcast_minute), period) and return the
    chronological x-coordinate so the goal-marker dot lands on the TOP
    of its step on the cumulative xG line.
    """
    for m, t, _xg, outcome, p in shots:
        try:
            shot_period = int(p)
        except (ValueError, TypeError):
            shot_period = 1
        if (t == team and int(m) == int_min
                and shot_period == period
                and outcome == 'Goal'):
            return _shot_chrono_x(m, shot_period, ht_minute)
    return float(int_min)


# Y-axis fractions (axes coords) at which goal labels can stack above the
# plot. Level 0 is closest to the plot; later levels sit further up.
GOAL_LABEL_Y_LEVELS = (1.04, 1.13, 1.22)


def _event_period(ev, ht_minute=None):
    """Period for an event dict, inferring one when the source omits it.

    The split is the minute the first half ACTUALLY ended, which the chart
    already knows from the last period-1 event. The old fallback was a fixed
    50 - a guess that is wrong in both directions. Halves run to 47-50 most
    often but 864 matches in the database pass 50, so a genuine first-half
    stoppage goal past that was read as second-half; and in a half that ended
    at 46, a real second-half event on minute 48 was read as first-half.

    Only manually-entered events reach the inference now: own goals typed into
    the sidebar carry no period, where anything from the database does.
    """
    p = ev.get('period')
    if p is not None:
        return int(p)
    boundary = float(ht_minute) if ht_minute else 50.0
    return 1 if float(ev.get('minute', 0)) <= boundary else 2


# Where each period's regular time ends. Anything past it is stoppage and is
# written the way broadcast writes it.
_PERIOD_REGULAR_END = {1: 45, 2: 90, 3: 105, 4: 120}


def format_broadcast_minute(minute, period):
    """Render a match minute as football writes it: 45+2, not 47.

    The feed gives elapsed match minutes, so a goal in first-half stoppage
    time arrives as 47 and a late winner as 94. Printing those raw states a
    time that does not exist in how anyone reads a match: a 45-minute half
    has no 47th minute, it has 45+2.

    It also contradicted this chart's own axis, which was moved to broadcast
    minutes earlier without the annotations following. On Wolves v Fulham the
    result was a goal labelled 47' sitting beside a HALF TIME line drawn at
    47 - the chart asserting both that the half ended and that a goal came
    afterwards, at the same moment.

    Measured over 7,083 matches: 8.4% carry a first-half stoppage goal, plus
    7.2% of all goals fall past minute 90.

    COUNT THE MINUTE IN PROGRESS, NOT THE ONE COMPLETED. `minute` arrives as
    `int(gameClock / 60)` - the FLOOR of elapsed time - and football numbers
    the minute a goal happens IN. Elapsed 0:30 is the 1st minute, 2:12 is the
    3rd, 44:30 is the 45th. Printing the floor makes a goal 30 seconds in read
    as "0'", and every label one behind the broadcast.

    So the displayed minute is `floor + 1`, and stoppage falls out of the same
    arithmetic: anything past the period's regular end is written base+extra.

        elapsed  0:30  -> floor 0   -> 1        1st minute
        elapsed 44:30  -> floor 44  -> 45       last regular minute
        elapsed 45:54  -> floor 45  -> 45+1     first stoppage minute
        elapsed 47:58  -> floor 47  -> 45+3
        elapsed 95:24  -> floor 95  -> 90+6

    Two earlier versions got this wrong in opposite directions: the first
    tested `m > base` so Saka's 45:54 goal printed "45'", and the second fixed
    stoppage but left regular time a minute behind.
    """
    m = int(minute) + 1
    base = _PERIOD_REGULAR_END.get(int(period)) if period is not None else None
    if base is None or m <= base:
        return str(m)
    return f"{base}+{m - base}"


def _px_per_x_unit(ax):
    """How many pixels one x-axis unit occupies, at current limits."""
    x0, x1 = ax.get_xlim()
    if x1 == x0:
        return None
    p0 = ax.transData.transform((x0, 0))[0]
    p1 = ax.transData.transform((x1, 0))[0]
    return (p1 - p0) / (x1 - x0)


def _measured_width(ax, lines, fontsize=13, fontweight='bold', pad_px=10):
    """Rendered width of a multi-line label, in x-axis DATA units.

    Draws each line off-screen, asks matplotlib for its extent, removes it.
    Returns None if the backend cannot supply a renderer, so callers can fall
    back rather than crash.

    WHY MEASURE. The previous estimator multiplied character count by a
    hardcoded 0.55 x-units per character. Measured on Man Utd 4-4 Bournemouth
    that constant is worth 7.0px per character while the font actually
    delivers 9.2-11.1 - so every label was under-estimated by 12-18%, and the
    12-unit floor swallowed the formula entirely for anything under 17
    characters. Collision detection therefore believed labels were narrower
    than they render and allowed overlaps.

    A constant cannot work here in principle: it is fixed in DATA units while
    text is fixed in PIXELS, so its accuracy depends on the x-range, which
    varies with match length (90 vs 120+ minutes) and on the figure width.
    """
    fig = ax.figure
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:
        try:
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
        except Exception:
            return None
    per_unit = _px_per_x_unit(ax)
    if not per_unit:
        return None
    widest = 0.0
    for line in lines:
        if not line:
            continue
        probe = fig.text(0, 0, line, fontsize=fontsize, fontweight=fontweight)
        try:
            bb = probe.get_window_extent(renderer=renderer)
            widest = max(widest, bb.x1 - bb.x0)
        finally:
            probe.remove()
    return (widest + pad_px) / per_unit


def _place_goal_labels(goals, chart_max, ax=None, near_edge=6, label_width=12,
                       fontsize=13):
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

    The `label_width` parameter is retained as a floor for backward
    compatibility; the active width per label is estimated from the
    label text itself (longer text -> wider range) so collision
    detection reflects what the chart actually renders. Without this
    width estimation a "M. Baturina (35') 1-1" label (~21 chars) gets
    treated as the same 12-unit span as a "OG (45') 1-0" label (~12
    chars), so the algorithm misses real overlaps.
    """
    def _estimate_width(ev):
        if ev.get('type') == 'goal':
            line1 = (f"{ev.get('label','')} "
                     f"({format_broadcast_minute(ev['minute'], _event_period(ev))}')")
            line2 = ev.get('score', '')
        else:  # 'rc'
            player = ev.get('label', '') or ''
            m = format_broadcast_minute(ev['minute'], _event_period(ev))
            if player:
                line1 = f"{player} ({m}')"
                line2 = 'RED CARD'
            else:
                line1 = f"RED CARD ({m}')"
                line2 = ''
        if ax is not None:
            # At the SIZE THE RENDERER WILL DRAW. The default 13 is the 16:9's
            # label size, and it was silently applied to a 20pt portrait label
            # too - a 35% under-estimate, which is the same class of error the
            # character-count estimator made and for the same reason. Caught by
            # the 3-event fixture: "J. Hinshelwood (1')" and "Y. Minteh (86')"
            # were both placed on level 0 and overprinted each other.
            measured = _measured_width(ax, (line1, line2), fontsize=fontsize)
            if measured:
                return measured
        # Fallback only: no axes to measure against (older callers, tests).
        # Known to under-estimate by 12-18% - see _measured_width.
        max_chars = max(len(line1), len(line2 or ''))
        return max(max_chars * 0.55 + 3, float(label_width))

    def _label_range(minute, side, width):
        if side == 'right':
            return (minute, minute + width)
        return (minute - width, minute)

    def _overlaps(m_new, s_new, w_new, placed):
        lo_new, hi_new = _label_range(m_new, s_new, w_new)
        for m_p, s_p, w_p, _lv in placed:
            lo_p, hi_p = _label_range(m_p, s_p, w_p)
            if max(lo_new, lo_p) < min(hi_new, hi_p):
                return True
        return False

    def _xpos(ev):
        # Use chrono_x when present (chart plots events at chronological
        # match time); fall back to broadcast minute for backward
        # compatibility with callers that haven't threaded period yet.
        return ev.get('chrono_x', ev['minute'])

    placed = []  # (xpos, x_side, width, level)
    fixed_side = []  # parallel: True when an edge forced the side
    for ev in goals:
        ev_x = _xpos(ev)
        ev_w = _estimate_width(ev)
        # A label is "near an edge" when IT would overflow, not when its
        # MARKER happens to sit within a fixed distance of one. The flat
        # 6-minute test asked the wrong question: a goal on minute 12 is not
        # near-left by that rule, but "Sergio Camello (12') 1-1" anchored to
        # its left runs past minute 0 and straight through the y-axis tick
        # labels. Measured on Barcelona v Rayo, and the width is already known
        # here - _estimate_width returns real rendered width in data units.
        #
        # The fixed distance is kept as a floor: a marker genuinely hard
        # against an edge should not flip merely because its label is short.
        overflows_left = (ev_x - ev_w) < 0
        overflows_right = (ev_x + ev_w) > chart_max
        near_left = ev_x < near_edge or overflows_left
        near_right = (chart_max - ev_x) < near_edge or overflows_right
        if near_left and near_right:
            # Wider than the space on either side. Spill toward whichever
            # edge is further away rather than picking arbitrarily.
            sides = ['right'] if ev_x < (chart_max - ev_x) else ['left']
        elif near_left:
            sides = ['right']
        elif near_right:
            # Late events: only try left. Falling back to right would push the
            # label past chart_max and visibly extend the chart trailing the
            # actual end of the match. Force vertical stacking instead.
            sides = ['left']
        else:
            sides = ['right', 'left']

        def _free_at(m, w, s, lv, placed=placed):
            same_lv = [(mp, sp, wp, lp) for mp, sp, wp, lp in placed if lp == lv]
            return not _overlaps(m, s, w, same_lv)

        chosen_side, chosen_level = None, None
        # Step 1: natural side, level 0
        for s in sides:
            if _free_at(ev_x, ev_w, s, 0):
                chosen_side, chosen_level = s, 0
                break

        # Step 2: try flipping an earlier level-0 label to keep both at bottom
        #
        # A label whose side was FORCED by an edge must not be a flip target.
        # Step 1 put it there precisely because the other side runs off the
        # chart; flipping it to make room for a later label undoes that and
        # pushes it through the axis furniture. This is how "Sergio Camello
        # (13')" ended up left-anchored at minute 12 despite being correctly
        # placed on the right a moment earlier - the third goal in the cluster
        # flipped it.
        flip_target = None
        if chosen_side is None and not near_left:
            for j, (mp, sp, wp, lp) in enumerate(placed):
                if lp != 0 or fixed_side[j]:
                    continue
                alt_s = 'left' if sp == 'right' else 'right'
                others_lv0 = [(mk, sk, wk, lk)
                              for k, (mk, sk, wk, lk) in enumerate(placed)
                              if k != j and lk == 0]
                if _overlaps(mp, alt_s, wp, others_lv0):
                    continue
                tentative = others_lv0 + [(mp, alt_s, wp, 0)]
                for s in sides:
                    if not _overlaps(ev_x, s, ev_w, tentative):
                        flip_target = (j, mp, alt_s)
                        chosen_side, chosen_level = s, 0
                        break
                if chosen_side is not None:
                    break

        # Step 3: elevate to higher y-levels
        if chosen_side is None:
            for lv in range(len(GOAL_LABEL_Y_LEVELS)):
                for s in sides:
                    if _free_at(ev_x, ev_w, s, lv):
                        chosen_side, chosen_level = s, lv
                        break
                if chosen_side is not None:
                    break

        if chosen_side is None:
            chosen_side, chosen_level = sides[0], len(GOAL_LABEL_Y_LEVELS) - 1

        if flip_target is not None:
            j, mp, alt_s = flip_target
            _, _, wp, _ = placed[j]
            placed[j] = (mp, alt_s, wp, 0)
            goals[j]['x_side'] = alt_s

        placed.append((ev_x, chosen_side, ev_w, chosen_level))
        fixed_side.append(len(sides) == 1)
        ev['x_side'] = chosen_side
        ev['y_level'] = chosen_level


def _draw_endpoint(ax, last_min, xg_val, label_y, color, shots_count,
                   abbrev=None, marker_size=7, xg_size=14, shots_size=11):
    """Draw the endpoint marker plus paired xG + shot-count labels.

    If label_y != xg_val, a small leader line connects the marker on the
    line to the offset label (used when the two teams' endpoint xG values
    are close enough to collide vertically).

    `abbrev` prefixes the figure - "NEW 4.09" rather than "4.09".

    WHY. The endpoint value is where a reader's eye lands: it is the final
    number, set in the largest type on the plot. It carried no team identity
    at all, so answering "whose 4.09 is that?" meant matching its colour
    against a 5.8px rule under the title - the chart's only legend. Colour as
    the sole carrier of identity also fails anyone who cannot separate the two
    hues, which on a red/grey pairing is a live risk.

    Prefixing costs nothing and puts the identity where the eye already is.
    Falls back to no prefix when the source has no abbreviation.
    """
    # The line's terminus. Dropped on the narrow frames (marker_size 0): at
    # phone scale it is a dot of the team's colour sitting a few pixels from
    # the last GOAL's dot, which is also a dot of the team's colour, and two
    # cold readers hit it - one counted a goal that did not exist, the other
    # called it "a stray, no idea". A line that visibly stops inside the plot
    # already reads as a line that ended.
    if marker_size:
        ax.plot(last_min, xg_val, marker='o', markersize=marker_size,
                markerfacecolor=color, markeredgecolor=BG_COLOR,
                markeredgewidth=1.5, zorder=5)
    if label_y != xg_val:
        ax.plot([last_min, last_min + 1.0], [xg_val, label_y],
                color=color, linewidth=0.8, alpha=0.55, zorder=4)
    ax.text(last_min + 1.5, label_y,
            f'{abbrev} {xg_val:.2f}' if abbrev else f'{xg_val:.2f}',
            color=color, fontsize=xg_size, fontweight='bold',
            va='bottom', ha='left')
    ax.text(last_min + 1.5, label_y, f'{shots_count} shots',
            color=color, fontsize=shots_size, alpha=0.9,
            va='top', ha='left')


# ── Aspect layouts ───────────────────────────────────────────────────────────
#
# A race is a TIME SERIES: minutes run left to right and cumulative xG runs up.
# Neither axis can be rotated without fighting a convention every reader has,
# so the portrait aspects do not stretch the plot - they give it a band at a
# workable shape and spend the rest of the frame on what the 16:9 puts ON it.
#
# That split is settled prior art twice over. The DP xG Race family ships all
# three ratios on exactly this rule (16:9 places callouts on the plot, 9:16
# moves them to a list below, 9:8 carries lines, markers and totals only), and
# Match Momentum - this chart's chronological sibling, same axis and the same
# event annotations - reached the same answer independently.
#
# WHAT SURVIVES INTO A NARROW FRAME, and why:
#   endpoint totals   ALWAYS. "STR 1.51" is the number the whole chart builds
#                     to, and the prefix is the only thing tying a line to a
#                     team by something other than colour. Dropping it or
#                     moving it off the line end would undo the 16:9's
#                     finding #2 - see _draw_endpoint.
#   goal markers      ALWAYS. Where a goal fell against the xG curve is the
#                     chart's second subject.
#   event labels      16:9 only. Nine 16pt callouts do not fit 9 inches of
#                     width; 9:16 lists them below the plot, 9:8 drops them
#                     because the host is naming the scorers aloud.
#
# TYPE FLOOR: both portrait aspects are 9in wide delivered on a phone, so
# nothing readable sits below 16pt - the same floor the shot chart, momentum
# and the player charts carry.
#
# The 16:9 entries below are the values this chart already shipped. They are
# transcribed, not retuned: cell 6 builds variants, and the 16:9's own
# critique was signed off 2026-09-03.

_XG_RACE_LAYOUT_DEFAULT = {
    'aspect':          'default',
    'axes_rect':       [0.07, 0.13, 0.88, 0.58],
    'kicker_size':     11,   'title_size':   22,   'fit_title': False,
    'y_kicker':        0.973, 'y_title':     0.942, 'y_bar': 0.912,
    'subtitle_y':      0.885, 'subtitle_size': 11,
    'labels_on_plot':  True,
    'label_y_levels':  GOAL_LABEL_Y_LEVELS,
    'label_leaders':   False,
    'label_one_per_level': False,
    'anchor_dots':     True,
    'event_block':     False,
    'key_y':           None,
    'line_width':      2.9,
    'goal_marker':     11,   'anchor_marker': 8,   'endpoint_marker': 7,
    'ring_width':      2.0,
    'event_label_size': 13,
    'end_xg_size':     14,   'end_shots_size': 11,
    'ht_size':         11,   'ht_below_axis': False,
    'state_emptiness': False,
    'card_h':          0.028, 'card_w': 0.7,       'card_derived': False,
    'tick_size':       10,   'axis_label_size': 11,
    'axis_words':      True,
    'event_line_width': 1.0,
    'anchor_row_dy':   0.0,
    'x_pad_min':       5,
}

# 9:16 fullscreen - the plot takes a 1.5:1 band and the callouts become the
# match timeline underneath, which is what the stacked label band was always
# trying to be. Same block the momentum chart draws, imported rather than
# copied: two charts of one match in one short must not disagree about how
# that match reads.
_XG_RACE_LAYOUT_9X16 = {
    'aspect':          '9x16',
    # Right edge at 0.775. The 0.225 gutter is not margin - it is where the
    # endpoint labels live, and they are drawn at data x = last_min + 1.5 with
    # no clipping, so a frame that does not reserve it does not clip them, it
    # WIDENS on save (bbox_inches='tight') and quietly stops being 9:16.
    'axes_rect':       [0.115, 0.600, 0.660, 0.265],
    'kicker_size':     16,   'title_size':   30,   'fit_title': True,
    'y_kicker':        0.975, 'y_title':     0.945, 'y_bar': 0.918,
    'subtitle_y':      0.900, 'subtitle_size': 16,
    'labels_on_plot':  False,
    'label_y_levels':  GOAL_LABEL_Y_LEVELS,
    'label_leaders':   False,
    'label_one_per_level': False,
    'anchor_dots':     True,
    'event_block':     True,
    'key_y':           None,
    'line_width':      3.4,
    'goal_marker':     12,   'anchor_marker': 9,   'endpoint_marker': 0,
    # A 1.8pt ring stroke is 1.1 delivered px on a phone, so an own
    # goal read as a slightly dimmer dot rather than a different kind
    # of mark. Thicker stroke, and the hole survives.
    'ring_width':      3.0,
    'event_label_size': None,
    'end_xg_size':     18,   'end_shots_size': 16,
    'ht_size':         16,   'ht_below_axis': True,
    'state_emptiness': True,
    'card_h':          0.060, 'card_w': None,      'card_derived': True,
    'tick_size':       16,   'axis_label_size': 16,
    'axis_words':      'y',
    # The leader that ties a goal's dot in the top row to its marker on the
    # line. Measured on the tile at 1.0pt: 2px in the file against a
    # half-time line - furniture - at 3px, so the chart drew its DATA link
    # thinner than its chrome, and at phone scale (divide by 3.3) that link
    # is under one CSS pixel. A cold viewer could not tell whether the two
    # dots were one event drawn twice or two different things.
    'event_line_width': 1.8,
    'anchor_row_dy':   0.055,
    'x_pad_min':       4,
    # Event timeline block - the momentum chart's geometry, so the two charts
    # list a match identically.
    'block_head_y':    0.535, 'block_top': 0.500, 'block_bot': 0.075,
    'row_step_max':    0.048,
    'head_size':       16,   'row_size':  19,
    'min_x':           0.105, 'name_x':   0.225, 'score_x': 0.915,
    'rule_x0':         0.070,
}

# 9:8 tile - the chart shares the frame with the host, who names the scorers.
# The plot keeps its markers and drops its text; a marker key replaces the
# labels, because without them the markers carry the whole vocabulary.
_XG_RACE_LAYOUT_9X8 = {
    'aspect':          '9x8',
    # 0.620 tall, not 0.640: the subtitle this frame gained needs the band
    # above the plot, where a red card's team label also lives.
    'axes_rect':       [0.115, 0.170, 0.655, 0.620],
    'kicker_size':     16,   'title_size':   21,   'fit_title': True,
    # The kicker sits a little higher and the title a little lower than the
    # 9:16's: "ALAVES" with its acute came within 1.1 delivered px of the
    # kicker, and Spanish, French and Portuguese club names carry diacritics
    # constantly. The header measures a cap height, not an accent.
    'y_kicker':        0.980, 'y_title':     0.933, 'y_bar': 0.898,
    # The tile DOES carry competition and date. shared/styles.py's note that
    # a tile needs no subtitle assumed the host supplies the context, but a
    # host names the players, not the competition - and two cold viewers, one
    # per round, reported the league and date as missing from this frame and
    # present on the vertical. The header has the room.
    'subtitle_y':      0.868, 'subtitle_size': 16,
    'labels_on_plot':  False,
    'label_y_levels':  GOAL_LABEL_Y_LEVELS,
    'label_leaders':   False,
    'label_one_per_level': False,
    'anchor_dots':     True,
    'event_block':     False,
    'key_y':           0.045,
    'line_width':      3.4,
    'goal_marker':     12,   'anchor_marker': 9,   'endpoint_marker': 0,
    # A 1.8pt ring stroke is 1.1 delivered px on a phone, so an own
    # goal read as a slightly dimmer dot rather than a different kind
    # of mark. Thicker stroke, and the hole survives.
    'ring_width':      3.0,
    'event_label_size': None,
    'end_xg_size':     18,   'end_shots_size': 16,
    'ht_size':         16,   'ht_below_axis': True,
    # ...printed in the KEY's slot instead - see _draw_marker_key.
    'state_emptiness': False,
    'card_h':          0.060, 'card_w': None,      'card_derived': True,
    'tick_size':       16,   'axis_label_size': 16,
    # The tile keeps CUMULATIVE xG. Dropping it was the momentum chart's call
    # and momentum can afford it - its y-axis is a normalised balance named by
    # the two team labels inside the plot. Here the y values are a real
    # quantity, and a cold viewer called the missing word the single thing the
    # tile most needed. It costs no width: the rotated label sits inside the
    # margin the tick numbers already require.
    'axis_words':      'y',
    'event_line_width': 1.8,
    'anchor_row_dy':   0.055,
    'x_pad_min':       4,
}

# 9:16, quiet match - the callouts go back ON the plot and the plot takes the
# frame.
#
# The event list exists because ten 16pt callouts do not fit 9 inches of width.
# Two of them fit easily. Sizing the list's band to ten rows and then putting
# two in it is what made a 1-1 render as a table with 39% of the frame empty
# underneath - and that is the MAJORITY case, not an edge: 3,303 of 7,083
# matches (46.6%) finish with two goals or fewer, 69.1% with three or fewer.
# Two cold readers called that frame a failed render, independently.
#
# So the rule is content-driven rather than a fixed reserve: below the
# threshold the frame is the 16:9's design at portrait scale, above it the
# list. What is left over lands ABOVE the plot as headroom under the title,
# which is the shape of emptiness this project has already ruled on and
# accepted - see the 16:9's own annotation-zone decision, 2026-09-02.
#
# HALF TIME stays under the axis. It is the plot's crowding that drove it
# there, and a sparse plot is still crossed by whatever leaders it does have.
SPARSE_EVENT_MAX = 3

def _sparse_axes_rect(layout, n_events):
    """Size the plot to the callout band this match actually needs.

    A fixed band is what made the dense layout fail on a quiet match, and
    reserving three stacking levels for a two-goal game repeats the mistake
    one storey up: a 2-goal and a 3-goal match produced the IDENTICAL 719px
    hole between the header and the plot, which is the tell that a reserve is
    fixed rather than content-driven. A cold designer read that hole as a
    failed render - "as if a logo lockup or a stat strip was supposed to be
    there" - in three of four tall frames.

    So the TOP of the band is pinned just under the subtitle and the plot
    grows down to meet it. Capped, because past a point the plot stops being
    a time series and becomes a portrait one.
    """
    levels = layout['label_y_levels']
    top_anchor = levels[max(1, min(n_events, len(levels))) - 1]
    bottom = 0.130          # ticks + the half-time caption live below this
    height = min((0.845 - bottom) / top_anchor, 0.60)
    return [layout['axes_rect'][0], bottom, layout['axes_rect'][2], height]


_XG_RACE_LAYOUT_9X16_SPARSE = dict(
    _XG_RACE_LAYOUT_9X16,
    axes_rect=[0.115, 0.130, 0.660, 0.565],
    labels_on_plot=True,
    event_block=False,
    event_label_size=18,
    # Wider than the 16:9's (1.04, 1.13, 1.22): a level is a fraction of PLOT
    # height, and two lines of 20pt type need more of a 7.5in plot than two
    # lines of 13pt need of a 5.2in one.
    label_y_levels=(1.035, 1.150, 1.265),
    # A leader from the plot's ceiling up to each callout. The 16:9 does
    # without one because its label sits just above a short plot; here a
    # level-2 label floats 340px clear of the rail, and the momentum chart
    # already established what that costs - nine stacked labels with nothing
    # tying them to a dot were called "a colour-guessing game" by a cold
    # designer.
    label_leaders=True,
    # One callout per stacking level, earliest at the TOP. With three levels
    # and at most three events there is never a reason to share one, and the
    # collision solver's own order is wrong twice over: it fills from the
    # bottom, so top-to-bottom reads 2-0 then 1-0 - descending score - and a
    # later goal's leader has to climb PAST the earlier goal's callout,
    # crossing its text. Earliest-highest makes reading order chronological
    # (x is time, so earlier is also further left) and leaves every leader
    # rising into clear air.
    label_one_per_level=True,
    # No marker rail. Every goal on the other frames is drawn TWICE - a dot on
    # a rail at the plot's ceiling and a marker on the curve - and all three
    # cold readers, across both rounds, hit it: one counted a goal that did not
    # exist, one read a fused pair as a single event, one said "I think they're
    # the same goals drawn twice, I am not sure". The rail earns its place
    # where it is the ONLY timeline: the tile has no callouts, and the dense
    # 9:16 lists its events below the plot. Here each callout sits directly
    # above its own goal with a leader running down to it, so the rail is a
    # waypoint in the middle of a line - and two goals four minutes apart put
    # two identical dots 4.4 delivered px apart for no gain.
    anchor_dots=False,
    # The stagger is for a rail with no labels on it. Here every dot has a
    # callout hanging off it, which is what the 16:9 relies on.
    anchor_row_dy=0.0,
)

_XG_RACE_LAYOUTS = {
    'default': _XG_RACE_LAYOUT_DEFAULT,
    '16x9':    _XG_RACE_LAYOUT_DEFAULT,
    '9x16':    _XG_RACE_LAYOUT_9X16,
    '9x8':     _XG_RACE_LAYOUT_9X8,
}


def _count_chart_events(goal_scorers, own_goals, red_cards):
    """How many rows the event list would hold - the same filter the renderer
    applies, so the layout switch and the drawing cannot disagree."""
    return (len(goal_scorers or []) + len(own_goals or [])
            + sum(1 for rc in (red_cards or [])
                  if rc.get('card_type') in ('red', 'second_yellow')))


def _stagger_anchor_rows(events, ax, marker_pt, dpi, rows=2, pad=1.35):
    """Give each event an anchor ROW so the marker rail stops fusing.

    Every goal's dot sits on one horizontal rail above the plot, at its own
    minute - so two goals close in time draw two dots at the same height, and
    they touch. Measured over 4,456 matches carrying two or more goals: 8.2%
    have a pair within 2.2 minutes, which at these marker sizes is contact,
    and 0.7% within 1.2 minutes, which is overlap. The 16:9 gets away with it
    because a LABEL hangs off each dot and says which is which; the narrow
    frames have no labels, and all three cold readers hit it - one counted a
    goal that was not there, one read a white dot and a red dot fused into a
    single two-tone mark as ONE goal, which is a goal attributed to the wrong
    team.

    The x is never moved - that would be a lie about when the goal happened.
    The colliding dot goes UP a row, and its leader follows it, so the minute
    stays true and only the height changes.
    """
    per_unit = _px_per_x_unit(ax)
    if not per_unit:
        for ev in events:
            ev['anchor_row'] = 0
        return
    sep = (marker_pt * dpi / 72) * pad / per_unit   # in x-axis data units
    last = [None] * rows
    for ev in sorted(events, key=lambda e: e.get('chrono_x', e['minute'])):
        x = ev.get('chrono_x', ev['minute'])
        for r in range(rows):
            if last[r] is None or (x - last[r]) >= sep:
                ev['anchor_row'] = r
                last[r] = x
                break
        else:
            # More events in one cluster than there are rows. Put it back on
            # the bottom row rather than inventing a third: a third row runs
            # into the subtitle, and this is 0.7% of matches.
            ev['anchor_row'] = 0
            last[0] = x


def _draw_marker_key(fig, layout, events, rc_color):
    """Marker vocabulary for the tile, which has no event labels.

    Without it the markers are unexplained - dots and a red rectangle floating
    over two lines. Named only for what is actually ON this chart: a key that
    lists an own goal on a match with none is furniture. Drawn item by item so
    the card's swatch can be RED, since a card's whole message is its colour.
    """
    if not events:
        # The key is built from what the match contained, so a goalless match
        # has no key at all - and then nothing on the tile separates "nothing
        # happened" from "the marks failed to draw". Say it, in the key's own
        # slot. Same line the 9:16 prints in its callout band.
        fig.text(0.5, layout['key_y'], 'NO GOALS OR CARDS', ha='center',
                 va='center', fontsize=16, fontweight='bold', color=TEXT_MUTED)
        return
    bits = [('●  GOAL', TEXT_MUTED)]
    if any(e.get('og') for e in events):
        bits.append(('○  OWN GOAL', TEXT_MUTED))
    if any(e['type'] == 'rc' for e in events):
        bits.append(('▮', rc_color))
        bits.append(('RED CARD', TEXT_MUTED))
    fig.canvas.draw()
    inv = fig.transFigure.inverted()
    widths = []
    for txt, _c in bits:
        probe = fig.text(0, -1, txt, fontsize=16)
        widths.append(probe.get_window_extent(
            renderer=fig.canvas.get_renderer()).transformed(inv).width)
        probe.remove()
    gap = 0.018
    x = 0.5 - (sum(widths) + gap * (len(bits) - 1)) / 2
    for (txt, colour), w in zip(bits, widths):
        fig.text(x, layout['key_y'], txt, ha='left', va='center',
                 fontsize=16, color=colour)
        x += w + gap


def create_xg_chart(shots, team_info, goal_scorers=None, red_cards=None,
                    own_goals=None, aspect='default'):
    """Create the xG race chart.

    Design: mockup port from mockups/xg_race_redesign_mockup.py.
      - Dark CBS theme, kicker + centered score title + team-color accent bar
      - Goal labels above the plot with 3-level collision-avoidance stagger
      - Running score in each goal label
      - Red cards as dash-dot line stopping at the affected team's step
      - Endpoint xG + shot count at each line's end
      - HALF TIME marker
      - No redundant bottom stats row

    `aspect` is one of 'default' (16:9), '9x16' (fullscreen phone overlay) or
    '9x8' (SBS tile). See _XG_RACE_LAYOUTS for what each frame keeps and why.
    """
    layout = _XG_RACE_LAYOUTS.get(aspect, _XG_RACE_LAYOUT_DEFAULT)
    goal_scorers = goal_scorers or []
    red_cards = red_cards or []
    # team_info carries own_goals (benefiting-team format) from get_team_info;
    # prefer that over any passed-in list so callers that have both stay consistent.
    own_goals = team_info.get('own_goals', own_goals or [])

    # A quiet match gets the 16:9's design at portrait scale instead of a list
    # sized for a ten-goal thriller. Decided here, before the figure exists,
    # because the switch changes the figure's own geometry.
    _n_events = _count_chart_events(goal_scorers, own_goals, red_cards)
    if aspect == '9x16' and _n_events <= SPARSE_EVENT_MAX:
        layout = dict(_XG_RACE_LAYOUT_9X16_SPARSE,
                      axes_rect=_sparse_axes_rect(_XG_RACE_LAYOUT_9X16_SPARSE,
                                                  _n_events))

    # ── Resolve team identity + colors ──────────────────────────────────────
    home = team_info['team1']['name']
    away = team_info['team2']['name']
    raw_home = team_info['team1']['color']
    raw_away = team_info['team2']['color']
    home_abbrev = team_info['team1'].get('abbrev')
    away_abbrev = team_info['team2'].get('abbrev')

    # Swap one side to its alternate if the two primaries clash, then apply
    # WCAG-based lightening so both lines read against the dark background.
    swapped_home, swapped_away, _ = check_color_similarity(
        raw_home, raw_away, home, away, threshold=150, interactive=False
    )
    home_color = ensure_line_contrast(swapped_home, BG_COLOR)
    away_color = ensure_line_contrast(swapped_away, BG_COLOR)
    # Then pull them apart in LUMINANCE. The step above lightens each colour
    # until it just clears the background threshold and stops, so two colours
    # that both needed lightening land at nearly the same luminance - 63.6% of
    # real fixtures ended isoluminant. Separating here rather than in the
    # earlier pair check because that runs on the RAW colours and never sees
    # what actually gets drawn.
    home_color, away_color = separate_line_luminance(
        home_color, away_color, BG_COLOR)

    # ── Split shots by team, build step-line data ───────────────────────────
    # ht_minute lets _cumulative_xg shift Period 2+ shots forward by
    # (ht_minute - 45) so the step line stays monotonic across half-time.
    ht_minute = team_info.get('first_half_end_minute', 45) or 45
    home_x, home_y, home_xg = _cumulative_xg(shots, home, ht_minute=ht_minute)
    away_x, away_y, away_xg = _cumulative_xg(shots, away, ht_minute=ht_minute)

    # Safety: a match with zero shots on a side shouldn't crash
    home_shots = [s for s in shots if s[1] == home]
    away_shots = [s for s in shots if s[1] == away]
    if not home_shots and not away_shots:
        print("No shots found for either team -- cannot render xG race")
        return None

    # Extend lines to end of regulation / extra time. The x-axis is the
    # chronological match-clock (Period 2+ shifted forward by Period 1
    # stoppage), so "end of regulation" extends to 90 + (ht_minute - 45)
    # so the chart end reflects actual elapsed match minutes.
    has_extra_time = team_info.get('extra_time', False)
    p2_offset = max(0.0, float(ht_minute) - 45.0)
    all_shot_chrono = [
        _shot_chrono_x(s[0], s[4] if len(s) >= 5 else 1, ht_minute)
        for s in shots
    ]
    max_shot_chrono = max(all_shot_chrono) if all_shot_chrono else 0
    if has_extra_time:
        last_min = 125 + p2_offset
    elif max_shot_chrono > 95 + p2_offset:
        last_min = int(max_shot_chrono) + 3
    else:
        last_min = 95 + p2_offset
    last_min = float(last_min)
    home_x.append(last_min); home_y.append(home_xg)
    away_x.append(last_min); away_y.append(away_xg)

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
    fig = plt.figure(figsize=resolve_figsize(layout['aspect']))
    fig.patch.set_facecolor(BG_COLOR)
    ax = fig.add_axes(layout['axes_rect'])
    ax.set_facecolor(BG_COLOR)

    ax.grid(axis='y', color=SPINE_COLOR, alpha=0.25, linewidth=0.6, zorder=0)
    ax.grid(axis='x', color=SPINE_COLOR, alpha=0.12, linewidth=0.5, zorder=0)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(SPINE_COLOR)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=layout['tick_size'])

    # HT line (plain dashed; red cards use dash-dot for visual distinction)
    ht_minute = team_info.get('first_half_end_minute', 45) or 45

    # DECIDED NOT TO MARK first-half stoppage (user, 2026-09-03). The axis
    # stretch between the 45 and 60 ticks varies match to match - median 2.9
    # minutes, up to 8+ - and a shaded band plus a "+4" label was built and
    # rejected: stoppage time is a phenomenon soccer viewers already
    # understand, and the half-time marker is enough on its own. Do not
    # reintroduce it.
    # Visible enough to be the thing its own label points at. At 0.8/0.5 this
    # line measures 1.67:1 against the background while "HALF TIME" measures
    # 6.91:1 - a caption four times more visible than its referent, so the eye
    # attaches it to the nearest line it CAN see, and on a narrow frame that
    # is whichever goal happened near the whistle. The momentum chart measured
    # the same pair and settled on the same weights. Dashed and grey still
    # separates it from the team-coloured dotted goal lines.
    ax.axvline(ht_minute, color=SPINE_COLOR, linestyle='--', linewidth=1.6,
               alpha=0.85, zorder=1)

    # Step lines
    ax.step(home_x, home_y, where='post', color=home_color,
            linewidth=layout['line_width'],
            solid_capstyle='round', zorder=3, label=home)
    ax.step(away_x, away_y, where='post', color=away_color,
            linewidth=layout['line_width'],
            solid_capstyle='round', zorder=3, label=away)

    # ── Endpoint totals: xG + shot count, offset if the two teams' final
    # xG values are close enough to collide vertically ──────────────────────
    max_xg = max(home_xg, away_xg, 0.5) * 1.05
    sep_threshold = max(home_xg, away_xg, 0.5) * 0.07
    # ...but a gap expressed as a fraction of the xG total is a gap sized in
    # DATA units to hold something measured in PIXELS, and the two only agree
    # on the frame it was tuned against. 7% of the total is 35px on the 16:9's
    # 522px-tall plot and 28px on a 9:16 band - while the type it has to clear
    # grew from 25pt to 34pt. Measured on Real Sociedad 3-3 Alaves, where
    # "12 shots" ran straight through "RSO 0.97".
    #
    # Each label is two lines centred on label_y - the xG above it, the shot
    # count below - so the two anchors need their whole stacked height between
    # them (1.2 line-height per line) plus air, or the blocks touch even
    # though the anchors do not.
    _need_px = ((layout['end_xg_size'] + layout['end_shots_size'])
                * fig.dpi / 72 * 1.44)
    _axes_h_px = layout['axes_rect'][3] * fig.get_size_inches()[1] * fig.dpi
    sep_threshold = max(sep_threshold, _need_px * max_xg / _axes_h_px)
    if abs(home_xg - away_xg) < sep_threshold:
        # Split around the two values, but the UPPER label may only rise as
        # far as its own text still fits under the plot ceiling - the rest of
        # the gap comes out of the lower one. Splitting symmetrically pushed
        # the leader line of the winning team clean out of the axes on three
        # of eight variant frames, which is a label that has left the chart to
        # avoid a collision. The upper number also has the better claim on
        # sitting at its true height: it is the one the reader is looking for.
        _txt_up = (layout['end_xg_size'] * fig.dpi / 72 * 1.1
                   * max_xg / _axes_h_px)
        hi, lo = max(home_xg, away_xg), min(home_xg, away_xg)
        rise = min(sep_threshold / 2, max(0.0, max_xg - _txt_up - hi))
        hi_y = hi + rise
        lo_y = max(0.0, hi_y - sep_threshold)
        if home_xg >= away_xg:
            home_label_y, away_label_y = hi_y, lo_y
        else:
            home_label_y, away_label_y = lo_y, hi_y
    else:
        home_label_y = home_xg
        away_label_y = away_xg

    _end_kw = dict(marker_size=layout['endpoint_marker'],
                   xg_size=layout['end_xg_size'],
                   shots_size=layout['end_shots_size'])
    _draw_endpoint(ax, last_min, home_xg, home_label_y, home_color,
                   len(home_shots), abbrev=home_abbrev, **_end_kw)
    _draw_endpoint(ax, last_min, away_xg, away_label_y, away_color,
                   len(away_shots), abbrev=away_abbrev, **_end_kw)

    # ── Goal / own-goal / red-card labels above the plot ────────────────
    # All match events share the events row at chart top and the same
    # collision-avoidance pool. Cards differentiated by shape (rectangle vs
    # circle) and team-colored label vs universal-red marker/line.
    # Sort key carries period so first-half stoppage events (45+4 -> minute
    # 49) sort BEFORE second-half early events (46' -> minute 46). Without
    # period a minute-only sort flips that order, which also breaks the
    # running-score accumulation below.
    # Module-level now, so the label-width estimator in _place_goal_labels
    # uses the same period inference the renderer does. They must agree: the
    # estimator sizes the text that the renderer draws.
    #
    # Bound to THIS match's half-time minute, so an event with no period of
    # its own is split at the whistle that actually blew rather than a fixed 50.
    def _ev_period(ev):
        return _event_period(ev, ht_minute)

    _all_events = []
    for g in goal_scorers:
        _all_events.append({
            'type': 'goal',
            'minute': float(g.get('minute', 0)),
            'period': g.get('period'),
            'team': g.get('team'),
            'label': g.get('player', '') + (' (P)' if g.get('pen') else ''),
            'og': False,
        })
    for og in own_goals:
        # Name the player who put it in, the way broadcast does, rather than a
        # bare "OG" sitting among goals that all name their scorer. The name
        # is the CONCEDING side's player - that is who an own goal belongs to.
        _og_player = (og.get('player') or '').strip()
        _all_events.append({
            'type': 'goal',
            'minute': float(og.get('minute', 0)),
            'period': og.get('period'),
            'team': og.get('team'),  # benefiting team
            'label': f'{_og_player} (OG)' if _og_player else 'OG',
            'og': True,
        })
    for rc in red_cards:
        if rc.get('card_type') not in ('red', 'second_yellow'):
            continue
        _all_events.append({
            'type': 'rc',
            'minute': float(rc.get('minute', 0)),
            'period': rc.get('period'),
            'team': rc.get('team'),
            'label': rc.get('player', ''),
        })
    # Each event's chronological x-position (chrono_x) is what gets used
    # for plotting and collision-detection in _place_goal_labels. Period
    # 1 events stay at their broadcast minute; Period 2+ shift forward by
    # (ht_minute - 45) so the chart x-axis is monotonic across half-time.
    for ev in _all_events:
        ev['chrono_x'] = _shot_chrono_x(ev['minute'], _ev_period(ev), ht_minute)
    _all_events.sort(key=lambda x: (_ev_period(x), x['minute']))

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
    # line for the endpoint marker; no empty sky above. max_xg itself is
    # computed further up, where the endpoint separation needs it.
    # Pad for endpoint label breathing room. The portrait frames reserve their
    # room in the AXES RECT instead (a narrower plot, a wider gutter), because
    # a pad big enough to hold "STR 1.51" on a 9in frame would be ~13% of the
    # match.
    ax.set_xlim(0, last_min + layout['x_pad_min'])
    ax.set_ylim(0, max_xg)
    # X-axis ticks show BROADCAST minute, but positioned at the
    # CHRONOLOGICAL x where that broadcast minute actually occurs. So a "60"
    # tick lands at chrono_x = 60 + (ht_minute - 45), not at chrono_x = 60 -
    # otherwise the tick labels disagree with the goal labels (e.g.
    # "Cunha (59')" sits between "45" and "60" ticks on a pure-chrono axis,
    # which reads as 59 happening before 60).
    broadcast_ticks = [0, 15, 30, 45, 60, 75, 90]
    if has_extra_time:
        broadcast_ticks += [105, 120]
    tick_positions, tick_labels = [], []
    for b in broadcast_ticks:
        pos = b if b <= 45 else b + p2_offset
        if pos <= last_min + layout['x_pad_min']:
            tick_positions.append(pos)
            tick_labels.append(str(b))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)

    # Place all event labels above the plot with collision avoidance (mutates
    # _all_events in place, adding 'x_side' and 'y_level' per event).
    # `ax` is passed so widths are MEASURED from the rendered text rather than
    # estimated from character count. Placement must run after the x-limits are
    # final, since the pixel->data-unit conversion depends on them.
    # Only where the labels are drawn: on the portrait frames the events are
    # listed below the plot or left to the host, so there is nothing to place.
    if layout['labels_on_plot']:
        _place_goal_labels(_all_events, chart_max=float(last_min), ax=ax,
                           fontsize=layout['event_label_size'])
        if (layout['label_one_per_level']
                and len(_all_events) <= len(layout['label_y_levels'])):
            for _i, _ev in enumerate(_all_events):
                _ev['y_level'] = len(_all_events) - 1 - _i
    else:
        _stagger_anchor_rows(_all_events, ax, layout['goal_marker'], fig.dpi)
    _label_transform = blended_transform_factory(ax.transData, ax.transAxes)
    _ROW_DY = layout['anchor_row_dy']

    _RC_COLOR = '#E53935'

    for ev in _all_events:
        side_color = home_color if ev['side'] == 'home' else away_color
        ev['color'] = side_color
        flip_left = ev.get('x_side') == 'left'
        label_y = layout['label_y_levels'][ev.get('y_level', 0)]
        label_ha = 'right' if flip_left else 'left'
        ev_period = _ev_period(ev)

        if ev['type'] == 'goal':
            xs = home_x if ev['side'] == 'home' else away_x
            ys = home_y if ev['side'] == 'home' else away_y

            if ev['og']:
                # OGs aren't in the shots stream (TruMedia doesn't flag
                # them) - place at the chronological x derived from the
                # OG's broadcast minute + period. Line doesn't step.
                marker_m = _shot_chrono_x(ev['minute'], ev_period, ht_minute)
            else:
                marker_m = _precise_goal_minute(
                    shots, ev['team'], int(ev['minute']),
                    period=ev_period, ht_minute=ht_minute,
                )
            y_at = _xg_at_minute(xs, ys, marker_m)

            # Team-colored dotted vertical from top of plot DOWN TO marker on step
            ax.plot([marker_m, marker_m], [y_at, max_xg],
                    color=side_color, linewidth=layout['event_line_width'],
                    linestyle=':', alpha=0.8, zorder=1,
                    solid_capstyle='round')

            # Marker on the step line
            # clip_on=False. A marker is centred ON its value, so a marker at
            # a value ON an axis has half its area outside the axes box and
            # matplotlib cuts that half off. Two cold readers found the same
            # bug from opposite edges: an own goal at minute 2 with no xG
            # behind it drew as a HALF ring sitting on the x-axis, and a goal
            # 35 seconds into a match drew as a half dot against the y-axis
            # that one reader read as "small and plain - if that difference
            # means something it's a bug". It did not mean anything; it was
            # the clip.
            if ev['og']:
                ax.plot(marker_m, y_at, marker='o',
                        markersize=layout['goal_marker'],
                        markerfacecolor=BG_COLOR, markeredgecolor=side_color,
                        markeredgewidth=layout['ring_width'], zorder=6,
                        clip_on=False)
            else:
                ax.plot(marker_m, y_at, marker='o',
                        markersize=layout['goal_marker'],
                        markerfacecolor=side_color, markeredgecolor='white',
                        markeredgewidth=1.5, zorder=6, clip_on=False)

            # Anchor dot at top of plot for the label.
            #
            # An own goal's anchor is a RING, matching its marker on the line.
            # Filled, it was identical to a goal's - which the 16:9 gets away
            # with because the label beneath it reads "(OG)", and the frames
            # without labels do not: on the tile the anchor row is the whole
            # vocabulary, and the key underneath promises a hollow "OWN GOAL"
            # mark that the row never showed. Drawn 1.3x larger because a ring
            # is identified by its HOLE, and the hole is what a shrink to
            # phone size takes first.
            _anchor_y = 1.005 + _ROW_DY * ev.get('anchor_row', 0)
            # Suppressed where the curve itself reaches the ceiling: the rail
            # dot and the goal's own marker then stack a few pixels apart, two
            # identical discs reading as one smear or as two events.
            _crowded = (_anchor_y - y_at / max_xg) < (
                layout['goal_marker'] * fig.dpi / 72
                / max(ax.get_window_extent().height, 1) * 1.6)
            _show_anchor = layout['anchor_dots'] and not _crowded
            if _show_anchor and _anchor_y > 1.005:
                # The leader follows the dot up, so a lifted marker still
                # points at its own minute rather than floating free.
                ax.plot([marker_m, marker_m], [1.0, _anchor_y],
                        transform=_label_transform, color=side_color,
                        linewidth=layout['event_line_width'], linestyle=':',
                        alpha=0.8, clip_on=False, zorder=1)
            if not _show_anchor:
                pass
            elif ev['og']:
                # Filled with the ground, not hollow: the leader runs up
                # THROUGH the ring, and a transparent centre left one dash of
                # it sitting inside the circle like a speck of dirt. Matches
                # the on-line own-goal marker, which was already BG-filled.
                ax.plot(marker_m, _anchor_y, 'o', transform=_label_transform,
                        markerfacecolor=BG_COLOR, markeredgecolor=side_color,
                        markersize=layout['anchor_marker'] * 1.3,
                        markeredgewidth=layout['ring_width'],
                        clip_on=False, zorder=5)
            else:
                ax.plot(marker_m, _anchor_y, 'o', transform=_label_transform,
                        color=side_color, markersize=layout['anchor_marker'],
                        markeredgecolor='white',
                        markeredgewidth=1.0, clip_on=False, zorder=5)

            if layout['labels_on_plot']:
                if layout['label_leaders']:
                    ax.plot([marker_m, marker_m], [1.0, label_y],
                            transform=_label_transform, linestyle=':',
                            color=side_color,
                            linewidth=layout['event_line_width'], alpha=0.45,
                            clip_on=False, zorder=2)
                label_x = marker_m - 0.6 if flip_left else marker_m + 0.6
                text = (f"{ev['label']} "
                        f"({format_broadcast_minute(ev['minute'], ev_period)}')"
                        f"\n{ev['score']}")
                ax.text(label_x, label_y, text,
                        transform=_label_transform, color=side_color,
                        fontsize=layout['event_label_size'], fontweight='bold',
                        va='bottom', ha=label_ha,
                        fontstyle='italic' if ev['og'] else 'normal',
                        clip_on=False, zorder=7)

        else:  # 'rc'
            # Red card x-position is the chronological match-time for the
            # card's broadcast minute (so a 45+4 card plots before a 46'
            # second-half event); the label still shows broadcast minute.
            m = _shot_chrono_x(ev['minute'], ev_period, ht_minute)
            broadcast_m = ev['minute']
            # The card is red on every chart because a red card is red - so
            # where the label is gone, nothing on the plot says WHOSE it was.
            # On the tile that is the only place a card could be attributed at
            # all: no callout, no event list. So the STEM carries the team and
            # the glyph carries the offence, exactly as the momentum chart
            # settled it. On the 16:9 the callout beside the card is already
            # drawn in the team's colour, so the stem stays universal red
            # there and keeps separating a card line from a goal line by more
            # than a dash pattern.
            _stem = _RC_COLOR if layout['labels_on_plot'] else side_color
            ax.axvline(m, color=_stem, linewidth=1.0 if layout['labels_on_plot'] else 1.6,
                       linestyle='-.', alpha=0.75 if layout['labels_on_plot'] else 0.85,
                       zorder=2)

            # Card-shaped marker at chart top edge — distinct from circles
            card_h_axes = layout['card_h']
            if layout['card_derived']:
                # Height is in AXES fraction and width in MINUTES, so a fixed
                # pair that reads as a card on one frame is a squashed sliver
                # on another. Derive the width from the axes' own pixel aspect
                # to hold a constant 1:1.4 portrait shape - the momentum
                # chart's fix, which the narrow frames need far more than the
                # 16:9 does.
                _abox = ax.get_window_extent()
                _xspan = ax.get_xlim()[1] - ax.get_xlim()[0]
                card_w_min = ((card_h_axes * _abox.height / 1.4)
                              / max(_abox.width, 1) * _xspan)
                card_y = (1.005 + _ROW_DY * ev.get('anchor_row', 0)
                          - card_h_axes / 2)
            else:
                card_w_min = layout['card_w']
                card_y = 1.0
            # The EDGE carries the team on frames with no callout beside the
            # card. The fill cannot: a red card is red, and #E53935 sits 18
            # RGB units from a red club's own line colour - indistinguishable,
            # while being the largest, most saturated mark on the rail. Three
            # cold readers across two rounds read the carded side off that
            # fill, and the key teaches them to: it draws GOAL in neutral grey
            # and RED CARD in exactly this red. Where the team plays in red
            # the edge vanishes into the fill, and the instinctive read is
            # right anyway. The 16:9 keeps its white edge - its callout is
            # already drawn in the team's colour a few pixels away.
            _card_edge = ('white' if layout['labels_on_plot']
                          else ensure_line_contrast(side_color, BG_COLOR))
            card = mpatches.Rectangle(
                (m - card_w_min / 2, card_y),
                card_w_min, card_h_axes,
                facecolor=_RC_COLOR, edgecolor=_card_edge, linewidth=2.5,
                transform=_label_transform, clip_on=False, zorder=6,
            )
            ax.add_patch(card)

            if not layout['labels_on_plot']:
                # Three letters, because colour cannot settle this one.
                #
                # A red card is RED on every chart, so the glyph's hue is a
                # category. Every other mark on that rail encodes the team by
                # hue, which pulls a reader hard toward "the red team" - and
                # two independent cold readers did exactly that. Colouring the
                # stem by team was not enough on its own: it gives the reader
                # two channels pointing at different sides and no rule for
                # which wins, and both readers flipped their answer.
                #
                # It is not a callout - no player, no minute, and it appears
                # only on the ~20% of matches with a sending-off. It is the
                # one thing on the frame that says whose.
                _ab = home_abbrev if ev['side'] == 'home' else away_abbrev
                if _ab:
                    ax.text(m, card_y + card_h_axes + 0.012, _ab,
                            transform=_label_transform, color=side_color,
                            fontsize=layout['ht_size'], fontweight='bold',
                            ha='center', va='bottom', clip_on=False, zorder=6)

            if layout['labels_on_plot']:
                if layout['label_leaders']:
                    ax.plot([m, m], [1.0, label_y], transform=_label_transform,
                            linestyle=':', color=_RC_COLOR,
                            linewidth=layout['event_line_width'], alpha=0.45,
                            clip_on=False, zorder=2)
                label_x = m - 0.6 if flip_left else m + 0.6
                player = ev.get('label', '')
                bm = format_broadcast_minute(broadcast_m, ev_period)
                text = (f"{player} ({bm}')\nRED CARD"
                        if player else f"RED CARD ({bm}')")
                ax.text(label_x, label_y, text,
                        transform=_label_transform, color=side_color,
                        fontsize=layout['event_label_size'], fontweight='bold',
                        va='bottom', ha=label_ha, clip_on=False, zorder=7)

    # HT label at the top of the HT axvline
    # A STROKE around the glyphs, not a filled box behind them. Both stop the
    # lines crossing the label from striking the text through, but a box also
    # ERASES whatever else is behind it - and what is behind it here is the
    # dotted leader of whichever goal fell near the whistle. Measured on Real
    # Madrid 4-2 Athletic and Wolves 1-1 Fulham: the box cut a rectangular gap
    # out of a goal's leader line, leaving it reading as two unrelated marks.
    # A stroke hugs the letterforms, so it costs a few pixels around glyphs
    # instead of a rectangle of chart.
    # OUTSIDE the plot on the narrow frames, under the axis, with the rule's
    # own dashes carried down to meet it.
    #
    # Inside, there is no clear band at either end. At the top, every goal's
    # leader runs from the ceiling down to its marker - measured on Real
    # Madrid 4-2 Athletic, FOUR vertical strokes crossed this label and a cold
    # designer called the word "a grey blur" even with the stroke behind it.
    # The bottom is no better: 2,042 of 7,081 matches (28.8%) have a team
    # below a tenth of the chart's y-max at half time, so a solid 3.4pt team
    # line lies exactly where the label would go - and cutting a data line is
    # worse than cutting a leader.
    #
    # Under the axis nothing crosses it at all, and the band is free precisely
    # because these frames drop the word MINUTE. The 16:9 keeps its in-plot
    # placement: it has the width to sit clear, and its critique is signed off.
    if not layout['ht_below_axis']:
        ax.text(ht_minute, max_xg * 0.97, 'HALF TIME', color=TEXT_SECONDARY,
                fontsize=layout['ht_size'], fontweight='bold', ha='center',
                va='top', alpha=0.85, zorder=7,
                path_effects=[mpe.withStroke(linewidth=3.5,
                                             foreground=BG_COLOR)])
    else:
        _ht_tr = blended_transform_factory(ax.transData, ax.transAxes)
        # The drop is expressed in FIGURE height and converted, not fixed in
        # axes fractions: a flat -0.115 is 0.054 of the frame under a 0.470
        # plot and 0.074 under a 0.640 one, so the tile pushed the label twice
        # as far from its own axis as the vertical did and parked it on top of
        # the marker key.
        _ht_dy = 0.038 / layout['axes_rect'][3]
        # The stub starts BELOW the tick numerals. Run from the axis itself
        # and it passes straight through whichever tick sits nearest the
        # whistle - measured on Brighton v Wolves, through the "5" of "45".
        ax.plot([ht_minute, ht_minute], [-_ht_dy * 0.52, -_ht_dy * 0.88],
                transform=_ht_tr, color=SPINE_COLOR, linestyle='--',
                linewidth=1.6, alpha=0.85, clip_on=False, zorder=3)
        # Hung off the rule to its RIGHT, not centred under it. These frames
        # drop the word MINUTE, so a centred caption in the axis-title slot
        # was read as the axis's own label - "the axis LOOKS labelled while
        # nothing says the numbers are minutes". Half time lands near the
        # middle of a match, which is exactly where an axis title sits.
        # Hanging it off its own dashed rule makes it a mark on a POSITION.
        # NOT bold. On the frame that carries an event list this caption
        # landed on the same baseline as "MATCH EVENTS" and "SCORE", in the
        # same grey, same weight and same size - and the table's own rule ran
        # beneath all three, underlining them. A cold designer read the chart's
        # axis annotation as the third column of a table header, and called it
        # the worst structural defect in the set. Weight is what separates an
        # annotation from a heading; the dashes above it do the rest.
        ax.text(ht_minute + 1.2, -_ht_dy, 'HALF TIME', transform=_ht_tr,
                color=TEXT_SECONDARY, fontsize=layout['ht_size'],
                ha='left', va='top', alpha=0.85, clip_on=False, zorder=7)

    # Axis labels -- kicker carries chart-type ID; these describe axes.
    #
    # MINUTE drops on the portrait frames: 0/15/30/45/60/75/90 under a chart
    # headed "xG RACE" is self-evidently a match clock, and at the 16pt floor
    # the word costs a band. CUMULATIVE xG does NOT drop on the 9:16 - the y
    # values are a real quantity and nothing else on the frame names them.
    # (The tile drops it too, where the host is speaking the numbers and the
    # endpoint labels already read "STR 1.51".)
    if layout['axis_words'] is True:
        ax.set_xlabel('MINUTE', color=TEXT_SECONDARY,
                      fontsize=layout['axis_label_size'],
                      fontweight='bold', labelpad=8)
    if layout['axis_words']:
        ax.set_ylabel('CUMULATIVE xG', color=TEXT_SECONDARY,
                      fontsize=layout['axis_label_size'],
                      fontweight='bold', labelpad=10)

    # ── Header: kicker + score title + accent bar + subtitle ────────────────
    custom_title = team_info.get('custom_title')
    custom_subtitle = team_info.get('custom_subtitle')

    _header_kw = {}
    if layout['fit_title']:
        # A 9in frame is a different proposition from a 16in one: "Real
        # Sociedad 2-1 Deportivo Alaves" overruns it at any title size worth
        # using, and matplotlib draws it and lets the ends fall off. Measure
        # before committing. Not applied to the 16:9, whose title has never
        # overrun and whose critique is signed off.
        _title_text = custom_title or (f'{home.upper()} {home_score}-'
                                       f'{away_score} {away.upper()}')
        _header_kw = dict(
            fontsize_kicker=layout['kicker_size'],
            # 0.90, not the helper's 0.94 default. At 0.94 a long pair of
            # club names shrinks to the limit AND still lands 11 delivered px
            # from both edges, while its shorter siblings hold 60 - so the
            # frame with the least room to spare is the one that gives up its
            # margin. Measured on Wolverhampton Wanderers 1-1 Fulham.
            # floor 18, not 16. The title AUTO-FITS to club-name length while
            # the callouts are fixed, so on "Brighton & Hove Albion 3-0
            # Wolverhampton Wanderers" the title shrank to 17pt under 18pt
            # callouts and a cold designer's eye landed on "L. Dunk (5') 2-0"
            # first - a line that restates the score already in the title. At
            # floor 18 the worst case is a tie, never an inversion.
            fontsize_title=fit_fontsize(fig, _title_text,
                                        layout['title_size'], floor=18,
                                        max_frac=0.92),
            y_kicker=layout['y_kicker'], y_title=layout['y_title'],
            y_bar=layout['y_bar'],
        )

    render_two_team_score_header(
        fig,
        home_name=home, home_score=home_score, home_color=home_color,
        away_name=away, away_score=away_score, away_color=away_color,
        kicker='x G   R A C E',
        custom_title=custom_title,
        **_header_kw,
    )

    # Subtitle: competition + date (user override if provided)
    competition = team_info.get('competition', '')
    match_date = team_info.get('date', '')
    subtitle_parts = [p for p in (competition.upper() if competition else '', match_date) if p]
    subtitle_text = custom_subtitle or ' | '.join(subtitle_parts)
    if subtitle_text and layout['subtitle_y'] is not None:
        fig.text(0.5, layout['subtitle_y'], subtitle_text, ha='center',
                 color=TEXT_SECONDARY, fontsize=layout['subtitle_size'])

    # A goalless match STATES that it was goalless.
    #
    # The callout band is empty by construction and there is nothing to put in
    # it - and the emptiness is real information, which is why the plot is not
    # rescaled to swallow it (the 16:9's own ruling on this band, 2026-09-02).
    # But an empty band and a band that failed to draw look identical. Saying
    # it is the DP xG Race's settled answer to the same frame, and it costs one
    # line. Only where the callouts live ON the plot: the list frames print
    # their own header, which already says a list was drawn.
    if layout['state_emptiness'] and not _all_events:
        # Level 0 - which IS the top of the band now that the band is
        # sized to the callouts a match actually has. Pinned to the top
        # level instead, it collided with the subtitle.
        ax.text(0.5, layout['label_y_levels'][0], 'NO GOALS OR CARDS',
                transform=ax.transAxes, color=TEXT_MUTED,
                fontsize=layout['event_label_size'], fontweight='bold',
                ha='center', va='bottom', clip_on=False)

    # ── Portrait: the callouts become a match timeline below the plot ────────
    if layout['event_block']:
        draw_event_block(
            fig, _all_events,
            lambda ev: format_broadcast_minute(ev['minute'], _ev_period(ev)),
            head_y=layout['block_head_y'], top=layout['block_top'],
            bottom=layout['block_bot'], row_step_max=layout['row_step_max'],
            head_size=layout['head_size'], row_size=layout['row_size'],
            min_x=layout['min_x'], name_x=layout['name_x'],
            score_x=layout['score_x'], rule_x0=layout['rule_x0'],
            rc_color=_RC_COLOR,
            # The row leads with the SAME glyph the plot draws, so the list is
            # also the key. The 9:16 has no separate key - the tile needs one
            # because it has no list, and a chart with both would be saying
            # the same thing twice.
            mark_of=lambda ev: ('▮' if ev['type'] == 'rc'
                                else '○' if ev.get('og') else '●'),
        )

    # ── Tile: a marker key, because the tile has no labels ───────────────────
    if layout['key_y'] is not None:
        _draw_marker_key(fig, layout, _all_events, _RC_COLOR)

    # Per aspect, because only one of them cropped. The verticals have always
    # saved uncropped, so their margins were reviewed as they stand -
    # deliberately tight and symmetric, 17px top against 20px bottom on the
    # 9:8's 2400px frame - and the physical floor would treble the bottom while
    # leaving the top, turning a balanced frame into a lopsided one. The 16:9
    # DID crop, so its real margins have never been seen: 50px top against 23px
    # bottom. That one gets the rule.
    add_cbs_footer(fig, y=footer_y(fig) if aspect == 'default' else 0.01)
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
        shots, match_info, team_colors, goal_scorers = parse_trumedia_csv(file_path)
        if not shots:
            print("\nError: No shot data found in CSV.")
            return
    else:
        # For fbref/manual, fall back to interactive mode
        shots, match_info, team_colors, data_source = get_data_source()
        goal_scorers = (match_info or {}).get('goal_scorers', []) or []
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

    fig = create_xg_chart(shots, team_info, goal_scorers=goal_scorers)

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
        fig.savefig(filepath, dpi=300,
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
    goal_scorers = (match_info or {}).get('goal_scorers', []) or []

    # Create and display chart
    print("\n" + "="*60)
    print("GENERATING CBS SPORTS CHART...")
    print("="*60)

    fig = create_xg_chart(shots, team_info, goal_scorers=goal_scorers)

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
        fig.savefig(filename, dpi=300,
                   facecolor='#1A2332', edgecolor='none')
        print(f"[OK] Chart saved as {filename}")

    plt.show()
    print("\nDone!")

if __name__ == "__main__":
    main()