"""
Shared team colors and color utilities for soccer chart builders.
"""
import os
import json


# Team abbreviation to full name mapping
TEAM_ABBREV = {
    # Premier League
    'ARS': 'Arsenal',
    'AVL': 'Aston Villa',
    'BOU': 'AFC Bournemouth',
    'BRE': 'Brentford',
    'BHA': 'Brighton',
    'BUR': 'Burnley',
    'CHE': 'Chelsea',
    'CRY': 'Crystal Palace',
    'EVE': 'Everton',
    'FUL': 'Fulham',
    'IPS': 'Ipswich Town',
    'LEI': 'Leicester City',
    'LIV': 'Liverpool',
    'LUT': 'Luton Town',
    'MCI': 'Manchester City',
    'MUN': 'Manchester United',
    'NEW': 'Newcastle',
    'NFO': 'Nottingham Forest',
    'SHU': 'Sheffield United',
    'SOU': 'Southampton',
    'TOT': 'Tottenham',
    'WHU': 'West Ham',
    'WOL': 'Wolves',
    'LEE': 'Leeds United',
    'WBA': 'West Brom',
    'NOR': 'Norwich City',
    'WAT': 'Watford',
    'HUD': 'Huddersfield',
    'CAR': 'Cardiff City',
    'SWA': 'Swansea City',
    'STK': 'Stoke City',
    'MID': 'Middlesbrough',
    'WIG': 'Wigan Athletic',
    'QPR': 'Queens Park Rangers',
    'REA': 'Reading',
    'POR': 'Portsmouth',
    'BOL': 'Bolton',
    'BLB': 'Blackburn',
    'BLP': 'Blackpool',

    # EFL Championship
    'COV': 'Coventry City',
    'SUN': 'Sunderland',
    'HUL': 'Hull City',
    'PNE': 'Preston North End',
    'SHW': 'Sheffield Wednesday',
    'PLY': 'Plymouth Argyle',
    'DER': 'Derby County',
    'OXF': 'Oxford United',
    'BRC': 'Bristol City',
    'MLW': 'Millwall',
    'BRN': 'Barnsley',
    'ROT': 'Rotherham United',
    'BIR': 'Birmingham City',

    # Serie A
    'JUV': 'Juventus',
    'INT': 'Inter Milan',
    'MIL': 'AC Milan',
    'NAP': 'Napoli',
    'ROM': 'Roma',
    'LAZ': 'Lazio',
    'ATA': 'Atalanta',
    'FIO': 'Fiorentina',
    'COM': 'Como',
    'TOR': 'Torino',
    'BOL': 'Bologna',
    'UDI': 'Udinese',
    'EMP': 'Empoli',
    'SAL': 'Salernitana',
    'SAS': 'Sassuolo',
    'VER': 'Verona',
    'SPE': 'Spezia',
    'SAM': 'Sampdoria',
    'CRE': 'Cremonese',
    'LEC': 'Lecce',
    'MON': 'Monza',
    'CAG': 'Cagliari',
    'GEN': 'Genoa',
    'PAR': 'Parma',
    'VEN': 'Venezia',
    'FRO': 'Frosinone',

    # La Liga
    'RMA': 'Real Madrid',
    'BAR': 'Barcelona',
    'ATM': 'Atletico Madrid',
    'SEV': 'Sevilla',
    'VAL': 'Valencia',
    'VIL': 'Villarreal',
    'RSO': 'Real Sociedad',
    'BET': 'Real Betis',
    'ATH': 'Athletic Bilbao',
    'CEL': 'Celta Vigo',
    'GET': 'Getafe',
    'OSA': 'Osasuna',
    'MAL': 'Mallorca',
    'RAY': 'Rayo Vallecano',
    'ALM': 'Almeria',
    'CAD': 'Cadiz',
    'GRA': 'Granada',
    'ELC': 'Elche',
    'ESP': 'Espanyol',
    'GIR': 'Girona',
    'ALV': 'Alaves',
    'LAS': 'Las Palmas',
    'LEG': 'Leganes',
    'VAD': 'Real Valladolid',

    # Bundesliga
    'BAY': 'Bayern Munich',
    'BVB': 'Borussia Dortmund',
    'RBL': 'RB Leipzig',
    'LEV': 'Bayer Leverkusen',
    'BMG': 'Borussia Monchengladbach',
    'WOB': 'Wolfsburg',
    'SGE': 'Eintracht Frankfurt',
    'SCF': 'Freiburg',
    'HOF': 'Hoffenheim',
    'UNB': 'Union Berlin',
    'KOE': 'FC Koln',
    'MAI': 'Mainz',
    'AUG': 'Augsburg',
    'HER': 'Hertha Berlin',
    'VFB': 'Stuttgart',
    'BOC': 'Bochum',
    'S04': 'Schalke',
    'WER': 'Werder Bremen',
    'DAR': 'Darmstadt',
    'HEI': 'Heidenheim',
    'STH': 'St Pauli',
    'HOL': 'Holstein Kiel',

    # Ligue 1
    'PSG': 'Paris Saint-Germain',
    'OLY': 'Marseille',
    'LYO': 'Lyon',
    'MOC': 'Monaco',
    'LIL': 'Lille',
    'NIC': 'Nice',
    'REN': 'Rennes',
    'LEN': 'Lens',
    'NAN': 'Nantes',
    'MOT': 'Montpellier',
    'STR': 'Strasbourg',
    'REI': 'Reims',
    'BRS': 'Brest',
    'TOU': 'Toulouse',
    'CLE': 'Clermont',
    'AUX': 'Auxerre',
    'ANG': 'Angers',
    'HAV': 'Le Havre',
    'MET': 'Metz',
    'AJA': 'Ajaccio',
    'TRO': 'Troyes',
    'LOR': 'Lorient',

    # Other European
    'AJX': 'Ajax',
    'PSV': 'PSV Eindhoven',
    'FEY': 'Feyenoord',
    'PRT': 'Porto',
    'BEN': 'Benfica',
    'SPO': 'Sporting CP',
    'CLT': 'Celtic',
    'RAN': 'Rangers',
    'GAL': 'Galatasaray',
    'FEN': 'Fenerbahce',
    'BES': 'Besiktas',

    # Liga MX
    'CFA': 'Club América',
    'GDL': 'Guadalajara',
    'MTY': 'Monterrey',
    'TIG': 'Tigres UANL',
    'CRZ': 'Cruz Azul',
    'PUM': 'Pumas UNAM',
    'TOL': 'Toluca',
    'SAN': 'Santos Laguna',
    'LEO': 'León',
    'PAC': 'Pachuca',
    'PUE': 'Puebla',
    'ATL': 'Atlas',
    'NEC': 'Necaxa',
    'QRO': 'Querétaro',
    'MZT': 'Mazatlán',
    'TIJ': 'Tijuana',
    'JSL': 'Juárez',
    'ASL': 'Atlético San Luis',
    'CTJ': 'Club Tijuana',

    # NWSL
    'ANG': 'Angel City FC',
    'BAY': 'Bay FC',
    'CRS': 'Chicago Red Stars',
    'HOU': 'Houston Dash',
    'KC': 'Kansas City Current',
    'LOU': 'Racing Louisville FC',
    'NJY': 'NJ/NY Gotham FC',
    'NCC': 'North Carolina Courage',
    'OLR': 'OL Reign',
    'ORL': 'Orlando Pride',
    'POR': 'Portland Thorns FC',
    'SDW': 'San Diego Wave FC',
    'UTA': 'Utah Royals',
    'WAS': 'Washington Spirit',

    # WSL (women-only clubs)
    'LCL': 'London City Lionesses',
}


# Alternate colors for teams with common primary color conflicts
# Used when two teams have similar primary colors
TEAM_ALTERNATE_COLORS = {
    # Red teams - need alternates for when they play each other
    'Arsenal': '#F0BC42',           # Gold/yellow (away kit)
    'Liverpool': '#00A398',         # Teal (third kit vibe)
    'Manchester United': '#FFE500', # Yellow (away kit)
    'Bayern Munich': '#0066B2',     # Blue (away kit)
    'AC Milan': '#000000',          # Black (third kit)
    'Roma': '#F5A623',              # Orange/gold (away kit)
    'Atletico Madrid': '#272E61',   # Navy blue (away kit)
    'Sevilla': '#000000',           # Black
    'Athletic Bilbao': '#FFFFFF',   # White
    'Monaco': '#FFFFFF',            # White
    'Benfica': '#FFFFFF',           # White
    'Lille': '#0A2240',             # Navy
    'Lyon': '#FFFFFF',              # White
    'Bayer Leverkusen': '#000000',  # Black
    'RB Leipzig': '#FFFFFF',        # White
    'Eintracht Frankfurt': '#FFFFFF', # White
    'AFC Bournemouth': '#000000',   # Black

    # Blue teams (actual colors defined in league sections below)
    'Lazio': '#000080',             # Navy
    'PSG': '#E30613',               # Red
    'Paris Saint-Germain': '#E30613', # Red

    # Other common conflicts
    'Borussia Dortmund': '#000000', # Black (yellow primary)
    'Real Madrid': '#000000',       # Black (gold primary in our db)
    'Barcelona': '#004D98',         # Blue (alternate to burgundy)
    'Juventus': '#FFFFFF',          # White (black primary)
}

# Built-in team color database
TEAM_COLORS = {
    # La Liga
    'Real Madrid': '#FEBE10',
    'Barcelona': '#A50044',
    'Atletico Madrid': '#CB3524',
    'Sevilla': '#F43333',
    'Valencia': '#EE7814',
    'Villarreal': '#FFE667',
    'Real Sociedad': '#0A3A82',
    'Athletic Bilbao': '#EE2523',
    'Athletic Club': '#EE2523',
    'Real Betis': '#00954C',
    'Celta Vigo': '#8AC3EE',
    'Getafe': '#005999',

    # Premier League
    'Manchester United': '#DA291C',
    'Man United': '#DA291C',
    'Manchester City': '#6CABDD',
    'Man City': '#6CABDD',
    'Liverpool': '#C8102E',
    'Chelsea': '#034694',
    'Arsenal': '#EF0107',
    'Tottenham': '#132257',
    'Tottenham Hotspur': '#132257',
    'Newcastle': '#241F20',
    'Aston Villa': '#95BFE5',
    'West Ham': '#7A263A',
    'Leicester City': '#003090',
    'Everton': '#003399',
    'Leeds United': '#FFCD00',
    'Wolves': '#FDB913',
    'Wolverhampton Wanderers': '#FDB913',
    'Brighton': '#0057B8',
    'Brighton and Hove Albion': '#0057B8',
    'Crystal Palace': '#1B458F',
    'AFC Bournemouth': '#B50E12',

    # Serie A
    'Juventus': '#000000',
    'Inter Milan': '#0068A8',
    'Internazionale': '#0068A8',
    'AC Milan': '#FB090B',
    'Napoli': '#00A1DD',
    'Roma': '#8B0304',
    'AS Roma': '#8B0304',
    'Hellas Verona': '#FFED00',
    'Lazio': '#87D8F7',
    'Atalanta': '#1B3B82',
    'Fiorentina': '#512D6D',
    'Como': '#00A1E4',

    # Bundesliga
    'Bayern Munich': '#DC052D',
    'Bayern Munchen': '#DC052D',
    'Borussia Dortmund': '#FDE100',
    'RB Leipzig': '#DD0741',
    'Bayer Leverkusen': '#E32221',
    'Wolfsburg': '#65B32E',
    'Eintracht Frankfurt': '#E1000F',
    'Schalke': '#004D9D',
    'Borussia Monchengladbach': '#000000',
    'FC Koln': '#EC1C24',

    # Ligue 1
    'Paris Saint-Germain': '#004170',
    'PSG': '#004170',
    'Marseille': '#2BB5E8',
    'Olympique de Marseille': '#2BB5E8',
    'Olympique Marseille': '#2BB5E8',
    'Lyon': '#DA020E',
    'Olympique Lyonnais': '#DA020E',
    'Monaco': '#E30613',
    'Lille': '#D01317',

    # EFL Championship
    'Coventry City': '#3AADE8',
    'Sunderland': '#EB172B',
    'Hull City': '#F5A623',
    'Preston North End': '#FFFFFF',
    'Sheffield Wednesday': '#0033A0',
    'Plymouth Argyle': '#00573F',
    'Derby County': '#FFFFFF',
    'Oxford United': '#F5B800',
    'Bristol City': '#E21836',
    'Millwall': '#001D5E',
    'Barnsley': '#E41E26',
    'Rotherham United': '#E21836',
    'Birmingham City': '#0000FF',
    'Burnley': '#6C1D45',
    'Norwich City': '#00A650',
    'Watford': '#FBEE23',
    'Luton Town': '#F78F1E',
    'Sheffield United': '#EE2737',
    'Middlesbrough': '#E11B22',
    'Stoke City': '#E03A3E',
    'Swansea City': '#FFFFFF',
    'Cardiff City': '#0070B5',
    'Queens Park Rangers': '#005CAB',
    'West Brom': '#122F67',
    'Blackburn': '#009EE0',
    'Blackpool': '#F68712',
    'Portsmouth': '#001489',
    'Huddersfield': '#0E63AD',
    'Reading': '#004494',
    'Ipswich Town': '#0044AA',
    'Southampton': '#D71920',
    'Fulham': '#FFFFFF',
    'Brentford': '#E30613',

    # Other
    'Ajax': '#D2122E',
    'Porto': '#003893',
    'Benfica': '#E30613',
    'Celtic': '#00A650',
    'Rangers': '#0000CD',

    # Liga MX
    'Club América': '#FFCD00',
    'Guadalajara': '#E51C23',
    'Monterrey': '#003DA5',
    'Tigres UANL': '#F4C400',
    'Cruz Azul': '#0057A3',
    'Pumas UNAM': '#003366',
    'Toluca': '#C8102E',
    'Santos Laguna': '#00A651',
    'León': '#006633',
    'Pachuca': '#004E9A',
    'Puebla': '#004A8D',
    'Atlas': '#BA1F24',
    'Necaxa': '#C8102E',
    'Querétaro': '#003DA5',
    'Mazatlán': '#6A2382',
    'Tijuana': '#C8102E',
    'Juárez': '#C68E28',
    'Atlético San Luis': '#E31837',
    'Club Tijuana': '#C8102E',

    # NWSL
    'Angel City FC': '#010101',
    'Bay FC': '#051C2C',
    'Chicago Red Stars': '#051C2C',
    'Houston Dash': '#101820',
    'Kansas City Current': '#64CCC9',
    'Racing Louisville FC': '#C5B4E3',
    'NJ/NY Gotham FC': '#9ADBE8',
    'North Carolina Courage': '#01426A',
    'OL Reign': '#003087',
    'Orlando Pride': '#5F249F',
    'Portland Thorns FC': '#93282C',
    'Portland Thorns': '#93282C',
    'San Diego Wave FC': '#041E42',
    'San Diego Wave': '#041E42',
    'Utah Royals': '#001E62',
    'Washington Spirit': '#C8102E',

    # WSL (women-only clubs)
    'London City Lionesses': '#D4AF37',
}


def load_custom_colors():
    """Load user-saved custom colors from file"""
    if os.path.exists('team_colors.json'):
        try:
            with open('team_colors.json', 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_custom_color(team_name, color):
    """Save a custom team color for future use"""
    custom_colors = load_custom_colors()
    custom_colors[team_name] = color
    with open('team_colors.json', 'w') as f:
        json.dump(custom_colors, f, indent=2)


def load_custom_abbrevs():
    """Load user-saved custom abbreviations from file"""
    abbrev_file = 'team_abbrevs.json'
    if os.path.exists(abbrev_file):
        try:
            with open(abbrev_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_custom_abbrev(abbrev, full_name):
    """Save a custom team abbreviation for future use"""
    abbrev_file = 'team_abbrevs.json'
    custom_abbrevs = load_custom_abbrevs()
    custom_abbrevs[abbrev] = full_name
    with open(abbrev_file, 'w') as f:
        json.dump(custom_abbrevs, f, indent=2)


def expand_team_name(abbrev):
    """Convert abbreviation to full team name if known (no prompt)."""
    if abbrev in TEAM_ABBREV:
        return TEAM_ABBREV[abbrev]
    custom_abbrevs = load_custom_abbrevs()
    return custom_abbrevs.get(abbrev, abbrev)


def get_team_abbrev(team_name):
    """Get abbreviation for a team name (reverse lookup).

    Uses fuzzy matching to find the best match.
    Returns the abbreviation if found, or first 3 letters uppercased if not.
    """
    team_lower = team_name.lower().strip()

    # Check for exact match first
    for abbrev, full_name in TEAM_ABBREV.items():
        if full_name.lower() == team_lower:
            return abbrev

    # Score-based matching to find best match
    best_match = None
    best_score = 0

    for abbrev, full_name in TEAM_ABBREV.items():
        full_lower = full_name.lower()
        score = 0

        # Exact match (already checked above, but just in case)
        if team_lower == full_lower:
            return abbrev

        # Full name starts with input or input starts with full name
        if full_lower.startswith(team_lower):
            score = 90 + (1 / len(full_lower))  # Prefer shorter matches
        elif team_lower.startswith(full_lower):
            score = 85 + (1 / len(team_lower))

        # Full name ends with input (e.g., "Milan" matches "AC Milan")
        elif full_lower.endswith(team_lower):
            score = 80 + (len(team_lower) / len(full_lower))  # Prefer closer length matches

        # Input ends with full name
        elif team_lower.endswith(full_lower):
            score = 75

        # All words in input appear in full name
        elif all(word in full_lower for word in team_lower.split()):
            score = 70

        if score > best_score:
            best_score = score
            best_match = abbrev

    if best_match:
        return best_match

    # Fallback: return first 3 characters uppercased
    return team_name[:3].upper()


def expand_team_name_with_prompt(abbrev):
    """Convert abbreviation to full team name, prompting if unknown."""
    if abbrev in TEAM_ABBREV:
        return TEAM_ABBREV[abbrev]

    custom_abbrevs = load_custom_abbrevs()
    if abbrev in custom_abbrevs:
        return custom_abbrevs[abbrev]

    print(f"\n[!] Unknown team abbreviation: {abbrev}")
    full_name = input(f"  Enter full team name (or press Enter to keep '{abbrev}'): ").strip()

    if full_name:
        save_choice = input(f"  Save this for future use? (y/n, default=y): ").strip().lower()
        if save_choice != 'n':
            save_custom_abbrev(abbrev, full_name)
            print(f"  [OK] Saved: {abbrev} -> {full_name}")
        return full_name

    return abbrev


# Women's-only club names (no men's team with the same name)
# Used by normalize_team_name to safely strip "Women" suffix
WOMENS_ONLY_CLUBS = {
    'Angel City FC', 'Bay FC', 'Chicago Red Stars', 'Houston Dash',
    'Kansas City Current', 'Racing Louisville FC', 'NJ/NY Gotham FC',
    'North Carolina Courage', 'OL Reign', 'Orlando Pride',
    'Portland Thorns FC', 'Portland Thorns', 'San Diego Wave FC',
    'San Diego Wave', 'Utah Royals', 'Washington Spirit',
    'London City Lionesses',
}


def normalize_team_name(team_name, color_dict=None):
    """
    Normalize team names from TruMedia CSVs.

    Strips ' Women' suffix when the base name is a women's-only club
    (no men's team shares the name). E.g.:
        'Washington Spirit Women' -> 'Washington Spirit'
        'Chelsea Women' -> 'Chelsea Women' (men's Chelsea exists)
    """
    if not team_name.endswith(' Women'):
        return team_name

    base_name = team_name[:-6]  # Strip ' Women'

    # If base name is a known women's-only club, safe to strip
    if base_name in WOMENS_ONLY_CLUBS:
        return base_name

    # Otherwise keep "Women" suffix (likely shares name with men's team)
    return team_name


def fuzzy_match_team(team_name, color_dict):
    """Try to find a fuzzy match for team name in color dictionary.
    Returns (color, matched_name, ambiguous_candidates) where ambiguous_candidates
    is a list of close matches if the result is ambiguous, or None if clear."""
    team_lower = team_name.lower().strip()
    candidates = []

    for db_team, color in color_dict.items():
        db_lower = db_team.lower()
        db_words = db_lower.split()

        # Exact match - highest priority
        if team_lower == db_lower:
            return color, db_team, None

        # Input matches a complete word in database name (e.g., "Real" in "Real Madrid")
        if team_lower in db_words:
            score = 110 - len(db_lower)
            candidates.append((score, color, db_team))
            continue

        # Input ends with database name or vice versa
        if db_lower.endswith(team_lower) or team_lower.endswith(db_lower):
            score = 100 - abs(len(db_lower) - len(team_lower))
            candidates.append((score, color, db_team))
            continue

        # Database name starts with input
        if db_lower.startswith(team_lower):
            score = 90 - abs(len(db_lower) - len(team_lower))
            candidates.append((score, color, db_team))
            continue

        # Input starts with database name
        if team_lower.startswith(db_lower):
            score = 85 - abs(len(db_lower) - len(team_lower))
            candidates.append((score, color, db_team))
            continue

        # Word-based matching
        team_words = team_lower.split()
        if len(team_words) > 1 and all(tw in db_lower for tw in team_words):
            score = 70
            candidates.append((score, color, db_team))
            continue

        # Substring match at word boundary
        if team_lower in db_lower:
            idx = db_lower.find(team_lower)
            at_word_start = idx == 0 or db_lower[idx-1] == ' '
            at_word_end = idx + len(team_lower) == len(db_lower) or db_lower[idx + len(team_lower)] == ' '
            if at_word_start or at_word_end:
                score = 60 - abs(len(db_lower) - len(team_lower))
                candidates.append((score, color, db_team))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score = candidates[0][0]
        close_matches = [(c[1], c[2]) for c in candidates if top_score - c[0] <= 15]

        if len(close_matches) > 1:
            return candidates[0][1], candidates[0][2], close_matches
        else:
            return candidates[0][1], candidates[0][2], None

    return None, None, None


def color_distance(hex1, hex2):
    """Calculate RGB distance between two hex colors. Lower = more similar."""
    r1, g1, b1 = [int(hex1.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)]
    r2, g2, b2 = [int(hex2.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)]
    return ((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2) ** 0.5


def is_warm_color(hex_color):
    """Determine if a color is warm (red/orange/yellow) or cool (blue/green/purple)."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c

    if diff == 0:
        hue = 0
    elif max_c == r:
        hue = (60 * ((g - b) / diff) + 360) % 360
    elif max_c == g:
        hue = (60 * ((b - r) / diff) + 120) % 360
    else:
        hue = (60 * ((r - g) / diff) + 240) % 360

    return hue < 60 or hue > 300


def get_contrast_color(team_color):
    """Get a contrasting color for xG Against based on team color."""
    if is_warm_color(team_color):
        return '#17A2B8'  # Teal for warm colors
    else:
        return '#E74C3C'  # Red for cool colors


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple (0-1 range)"""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16)/255 for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    """Convert RGB values (0-255) to hex color string."""
    return f'#{int(r):02x}{int(g):02x}{int(b):02x}'


def lighten_color(hex_color, factor=0.4):
    """Lighten a hex color by blending with white.

    Args:
        hex_color: Hex color string (e.g., '#132257')
        factor: How much to lighten (0 = no change, 1 = pure white)

    Returns:
        Lightened hex color string
    """
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Blend with white (255, 255, 255)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)

    return rgb_to_hex(r, g, b)


def darken_color(hex_color, factor=0.3):
    """Darken a hex color by blending toward black.

    Args:
        hex_color: Hex color string (e.g., '#6CABDD')
        factor: How much to darken (0 = no change, 1 = pure black)

    Returns:
        Darkened hex color string
    """
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Blend toward black (0, 0, 0)
    r = int(r * (1 - factor))
    g = int(g * (1 - factor))
    b = int(b * (1 - factor))

    return rgb_to_hex(r, g, b)


def ensure_contrast_with_background(hex_color, bg_color='#1A2332', min_distance=80):
    """Ensure a color has enough contrast against the background.

    If the color is too close to the background, lighten it until readable.

    Args:
        hex_color: The team/element color
        bg_color: Background color (default is CBS dark theme)
        min_distance: Minimum color distance required (default 80)

    Returns:
        Original color if contrast is OK, or lightened version if not
    """
    distance = color_distance(hex_color, bg_color)

    if distance >= min_distance:
        return hex_color

    # Progressively lighten until we have enough contrast
    lightened = hex_color
    factor = 0.1
    while factor <= 0.7:
        lightened = lighten_color(hex_color, factor)
        if color_distance(lightened, bg_color) >= min_distance:
            return lightened
        factor += 0.1

    # If still not enough, return a fairly light version
    return lighten_color(hex_color, 0.5)


def check_colors_need_fix(color1, color2, team1, team2, threshold=50):
    """Check if two team colors need fixing and whether auto-fix is available.

    Args:
        color1: Hex color for team1
        color2: Hex color for team2
        team1: Name of first team
        team2: Name of second team
        threshold: Color distance threshold (default 50)

    Returns:
        dict with keys:
            - 'needs_fix': bool - True if colors are too similar
            - 'can_auto_fix': bool - True if alternate color is available
            - 'distance': float - color distance
            - 'suggested_fix': dict or None - {'team': team_name, 'color': new_color} if auto-fix available
    """
    distance = color_distance(color1, color2)

    if distance >= threshold:
        return {
            'needs_fix': False,
            'can_auto_fix': False,
            'distance': distance,
            'suggested_fix': None
        }

    # Colors are too similar - check for alternates
    alt1 = get_alternate_color(team1)
    alt2 = get_alternate_color(team2)

    best_fix = None
    best_distance = distance

    if alt1:
        alt1_distance = color_distance(alt1, color2)
        if alt1_distance > best_distance:
            best_fix = {'team': team1, 'original': color1, 'color': alt1, 'distance': alt1_distance}
            best_distance = alt1_distance

    if alt2:
        alt2_distance = color_distance(color1, alt2)
        if alt2_distance > best_distance:
            best_fix = {'team': team2, 'original': color2, 'color': alt2, 'distance': alt2_distance}
            best_distance = alt2_distance

    return {
        'needs_fix': True,
        'can_auto_fix': best_fix is not None,
        'distance': distance,
        'suggested_fix': best_fix
    }


def get_alternate_color(team_name):
    """Get alternate color for a team if available.

    Uses fuzzy matching to find the team in TEAM_ALTERNATE_COLORS.
    Returns alternate color or None if not found.
    """
    # Direct match first
    if team_name in TEAM_ALTERNATE_COLORS:
        return TEAM_ALTERNATE_COLORS[team_name]

    # Fuzzy match
    color, matched_name, _ = fuzzy_match_team(team_name, TEAM_ALTERNATE_COLORS)
    return color


def check_color_similarity(color1, color2, team1, team2, threshold=50, interactive=True):
    """Check if two colors are too similar and auto-fix or prompt user.

    Args:
        color1: Hex color for team1
        color2: Hex color for team2
        team1: Name of first team
        team2: Name of second team
        threshold: Color distance threshold (default 50)
        interactive: If False (GUI mode), auto-swap to alternate color without prompting

    Returns (color1, color2, use_different_line_styles)"""
    distance = color_distance(color1, color2)

    if distance < threshold:
        print(f"\n[!] WARNING: Team colors are very similar!")
        print(f"  {team1}: {color1}")
        print(f"  {team2}: {color2}")
        print(f"  Color distance: {distance:.0f} (threshold: {threshold})")

        # Try to auto-fix by using alternate color for one team
        alt1 = get_alternate_color(team1)
        alt2 = get_alternate_color(team2)

        # Check which alternate creates better contrast
        best_fix = None
        best_distance = distance

        if alt1:
            alt1_distance = color_distance(alt1, color2)
            if alt1_distance > best_distance:
                best_fix = ('team1', alt1, alt1_distance)
                best_distance = alt1_distance

        if alt2:
            alt2_distance = color_distance(color1, alt2)
            if alt2_distance > best_distance:
                best_fix = ('team2', alt2, alt2_distance)
                best_distance = alt2_distance

        # In non-interactive (GUI) mode, automatically apply the best fix
        if not interactive:
            if best_fix:
                if best_fix[0] == 'team1':
                    print(f"[OK] Auto-fix: Changed {team1} to alternate color {best_fix[1]} (distance: {best_fix[2]:.0f})")
                    return best_fix[1], color2, False
                else:
                    print(f"[OK] Auto-fix: Changed {team2} to alternate color {best_fix[1]} (distance: {best_fix[2]:.0f})")
                    return color1, best_fix[1], False
            else:
                print(f"[!] No alternate colors available, colors remain similar")
                return color1, color2, True

        # Interactive mode - offer choices
        print(f"\nThis may make the chart hard to read.")

        # Show available alternate colors
        if alt1 or alt2:
            print(f"\nAvailable alternate colors:")
            if alt1:
                print(f"  {team1}: {alt1} (distance from {team2}: {color_distance(alt1, color2):.0f})")
            if alt2:
                print(f"  {team2}: {alt2} (distance from {team1}: {color_distance(color1, alt2):.0f})")

        print(f"\nHow would you like to fix this?")
        options = []
        if best_fix:
            team_to_change = team1 if best_fix[0] == 'team1' else team2
            options.append(f"Use alternate color for {team_to_change} ({best_fix[1]})")
        options.append("Enter a custom color")
        options.append("Use different line styles (solid vs dashed)")
        options.append("Keep as-is (no change)")

        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")

        while True:
            choice = input(f"Choose (1-{len(options)}): ").strip()
            try:
                choice_idx = int(choice)
                if 1 <= choice_idx <= len(options):
                    break
            except ValueError:
                pass
            print("Invalid choice, try again.")

        # Handle choice based on available options
        if best_fix and choice_idx == 1:
            # Use alternate color
            if best_fix[0] == 'team1':
                print(f"[OK] Changed {team1} to alternate color: {color1} -> {best_fix[1]}")
                return best_fix[1], color2, False
            else:
                print(f"[OK] Changed {team2} to alternate color: {color2} -> {best_fix[1]}")
                return color1, best_fix[1], False

        # Custom color option
        custom_idx = 2 if best_fix else 1
        if choice_idx == custom_idx:
            print(f"\nWhich team's color to change?")
            print(f"  1. {team1} ({color1})")
            print(f"  2. {team2} ({color2})")

            while True:
                team_choice = input("Choose (1 or 2): ").strip()
                if team_choice in ('1', '2'):
                    break
                print("Invalid choice, try again.")

            new_color = input("Enter new hex color (e.g., #FF0000): ").strip()
            if new_color:
                if not new_color.startswith('#'):
                    new_color = '#' + new_color
                if team_choice == '1':
                    print(f"[OK] Changed {team1} color: {color1} -> {new_color}")
                    return new_color, color2, False
                else:
                    print(f"[OK] Changed {team2} color: {color2} -> {new_color}")
                    return color1, new_color, False

        # Different line styles option
        line_styles_idx = 3 if best_fix else 2
        if choice_idx == line_styles_idx:
            print(f"[OK] Will use solid line for {team1}, dashed line for {team2}")
            return color1, color2, True

        print(f"[OK] Keeping colors as-is")

    return color1, color2, False


def get_team_color(team_name, csv_color=None, prompt_if_missing=True):
    """Get team color with fallback chain: CSV -> database -> saved -> prompt"""
    if csv_color:
        return csv_color

    color, matched, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if color:
        return color

    custom_colors = load_custom_colors()
    color, matched, _ = fuzzy_match_team(team_name, custom_colors)
    if color:
        return color

    if prompt_if_missing:
        print(f"\n[!] No color found for: {team_name}")
        new_color = input(f"  Enter hex color (e.g., #FF0000) or press Enter for gray: ").strip()
        if new_color:
            if not new_color.startswith('#'):
                new_color = '#' + new_color
            save_choice = input(f"  Save this color for future use? (y/n, default=y): ").strip().lower()
            if save_choice != 'n':
                save_custom_color(team_name, new_color)
                print(f"  [OK] Saved color for {team_name}")
            return new_color

    return '#888888'


def prompt_ambiguous_choice(team_name, candidates):
    """Ask user to choose from ambiguous team name matches"""
    print(f"\n[!] Multiple matches found for '{team_name}':")
    for i, (color, name) in enumerate(candidates, 1):
        print(f"  {i}. {name} ({color})")
    print(f"  {len(candidates) + 1}. None of these (enter custom color)")

    while True:
        choice = input(f"Choose (1-{len(candidates) + 1}): ").strip()
        try:
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1][0], candidates[idx - 1][1]
            elif idx == len(candidates) + 1:
                return None, None
        except ValueError:
            pass
        print("Invalid choice, try again.")


def resolve_team_colors(teams, csv_team_colors=None, interactive=True):
    """Resolve team colors for a list of teams using fallback chain.

    Args:
        teams: List of team names to resolve colors for
        csv_team_colors: Dict of team colors from CSV (optional)
        interactive: If False (GUI mode), auto-resolve similar colors without prompting

    Returns dict mapping team names to colors."""
    if csv_team_colors is None:
        csv_team_colors = {}

    custom_colors = load_custom_colors()
    resolved_colors = {}

    def get_color_with_fallback(team_name):
        # 1. Check CSV color (exact match)
        if team_name in csv_team_colors:
            return csv_team_colors[team_name], "CSV"
        # 2. Check built-in database (fuzzy match)
        color, matched_name, ambiguous = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            if ambiguous and len(ambiguous) > 1:
                chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous)
                if chosen_color:
                    return chosen_color, f"database (matched '{chosen_name}')"
            else:
                if matched_name and matched_name != team_name:
                    return color, f"database (matched '{matched_name}')"
                return color, "database"
        # 3. Check custom saved colors (fuzzy match)
        color, matched_name, ambiguous = fuzzy_match_team(team_name, custom_colors)
        if color:
            if ambiguous and len(ambiguous) > 1:
                chosen_color, chosen_name = prompt_ambiguous_choice(team_name, ambiguous)
                if chosen_color:
                    return chosen_color, f"saved (matched '{chosen_name}')"
            else:
                if matched_name and matched_name != team_name:
                    return color, f"saved (matched '{matched_name}')"
                return color, "saved"
        # 4. No color found
        return None, None

    print("\n" + "="*60)
    print("TEAM COLORS")
    print("="*60)

    for team in teams:
        color, source = get_color_with_fallback(team)
        if color:
            print(f"[OK] {team}: {color} [from {source}]")
            resolved_colors[team] = color
        else:
            print(f"[!] {team}: no color found")
            new_color = input(f"  Enter hex color for {team} (e.g., #FF0000): ").strip()
            if new_color:
                if not new_color.startswith('#'):
                    new_color = '#' + new_color
                resolved_colors[team] = new_color
                save_choice = input(f"  Save this color for future use? (y/n, default=y): ").strip().lower()
                if save_choice != 'n':
                    save_custom_color(team, new_color)
            else:
                resolved_colors[team] = '#888888'

    # Check color similarity if we have 2 teams
    if len(teams) >= 2:
        team1, team2 = teams[0], teams[1]
        color1, color2, use_different_styles = check_color_similarity(
            resolved_colors[team1], resolved_colors[team2], team1, team2,
            interactive=interactive
        )
        resolved_colors[team1] = color1
        resolved_colors[team2] = color2
        resolved_colors['_different_line_styles'] = use_different_styles

    return resolved_colors
