"""
TruMedia Downloader
Handles authentication via cURL parsing and data downloads via POST requests.
"""
import re
import os
import difflib
import requests
import duckdb
import pandas as pd
from datetime import date, timedelta, datetime as _dt


EXPORT_URL = "https://cbssports.opta.trumediasports.com/dp-proxy-export"
SUPABASE_BUCKET = "player-pools"
MOTHERDUCK_DB = "soccer"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# Known TruMedia → API-Football team name mismatches (add more as discovered).
# Use this when the fuzzy match fails because the two sources use significantly
# different names for the same club (e.g. short name vs full official name).
TRUMEDIA_TO_API_NAME = {
    "Olympique Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon",
    "Brest": "Stade Brestois 29",
    "Köln": "FC Köln",
    "Internazionale": "Inter",
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "PSV": "PSV Eindhoven",
    "Union Saint-Gilloise": "Union St. Gilloise",
    "København": "FC Copenhagen",
    "Malmö FF": "Malmo FF",
    "Servette": "Servette FC",
    "Viktoria Plzen": "Plzen",
    "Basel": "FC Basel 1893",
    # Premier League — API-Football uses short names
    "AFC Bournemouth": "Bournemouth",
    "Brighton & Hove Albion": "Brighton",
    "Wolverhampton Wanderers": "Wolves",
    "Newcastle United": "Newcastle",
    "West Ham United": "West Ham",
    "Tottenham Hotspur": "Tottenham",
    "Leeds United": "Leeds",
    # Liga MX — API-Football uses different club names
    "Pumas UNAM": "U.N.A.M. - Pumas",
    "Juárez": "FC Juarez",
    "Querétaro": "Club Queretaro",
    # NWSL — TruMedia uses "X Women", API-Football uses inconsistent naming
    "Kansas City Current Women": "Kansas City W",
    "Gotham FC Women": "NJ/NY Gotham FC W",
    "Seattle Reign Women": "Seattle Reign FC",
    "Bay Women": "Bay FC",
    "Angel City Women": "Angel City W",
    "Chicago Stars Women": "Chicago Red Stars W",
}

# Maps TruMedia season IDs → API-Football league IDs.
# Passed as ?league= filter on /fixtures queries to prevent cross-competition
# false matches (e.g. men's vs women's Champions League on the same date).
# None = skip league filtering for that season (safe fallback).
# To find an unknown ID: GET /leagues?name=<name>&season=2025 on the API.
SEASON_TO_API_LEAGUE = {
    "51r6ph2woavlbbpk8f29nynf8": 39,    # Premier League 2025/26
    "80zg2v1cuqcfhphn56u4qpyqc": 140,   # La Liga 2025/26
    "2bchmrj23l9u42d68ntcekob8": 78,    # Bundesliga 2025/26
    "emdmtfr1v8rey2qru3xzfwges": 135,   # Serie A 2025/26
    "dbxs75cag7zyip5re0ppsanmc": 61,    # Ligue 1 2025/26
    "aegyls91smdw9kipjgbsu8tn8": 262,   # Liga MX 2025/26
    "6i6n0jkbh9zzij6s8htfjh2j8": 253,   # MLS 2026
    "3ducfa94ga849pfvx8bjjgt1w": None,  # NWSL 2025 — verify ID
    "221phckhkd7y6rg3uyava3ifo": None,  # WSL 2025/26 — verify ID
    "2mr0u0l78k2gdsm79q56tb2fo": 2,     # UEFA Champions League 2025/26
    "7ttpe5jzya3vjhjadiemjy7mc": 3,     # UEFA Europa League 2025/26
    "7x2zp2hm4p6wuijwdw3h7a8t0": None, # UEFA Conference League 2025/26 — verify ID
    "24f2xd1kljmiu7o0xrpj30kd0": None, # UEFA Women's Champions League 2025/26 — verify ID
}


def load_secrets(secrets_path):
    """Load credentials from a simple KEY=VALUE file."""
    secrets = {}
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    secrets[key.strip()] = value.strip()
    return secrets


def upload_to_supabase(supabase_url, supabase_key, local_path, filename):
    """Upload a local CSV file to Supabase Storage.

    Uses upsert so repeated uploads overwrite the previous file cleanly.
    Raises ValueError on failure.
    """
    url = f"{supabase_url}/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
    with open(local_path, 'rb') as f:
        content = f.read()

    headers = {
        'Authorization': f'Bearer {supabase_key}',
        'Content-Type': 'text/csv',
        'x-upsert': 'true',
    }

    response = requests.post(url, headers=headers, data=content, timeout=60)
    if not response.ok:
        raise ValueError(f"{response.status_code} {response.reason}: {response.text}")
    return len(content) / 1024


# ── Player Pool ───────────────────────────────────────────────────────────────

PLAYER_POOL_SELECT = (
    "SELECT playerId,scout7PlayerId as playerImageId,abbrevName as player,"
    "fullName as playerFullName,mode(game.gameDetailedPosition) as pos,"
    "newest(team.game.optaTeamId) as teamImageId,"
    "newest(team.game.fullName) as teamName,"
    "newest(team.game.shortName) as teamShortName,"
    "newest(team.game.abbrevName) AS teamAbbrevName,"
    "newest(team.game.teamId) as newestTeamId,"
    "newest(team.game.fullName) as newestTeam,"
    "newest(team.game.teamColor) as newestTeamColor,"
    "newest(season.leagueId) as newestLeagueId,"
    "newest(season.leagueName) as newestLeague,"
    "newest(game.gameDate) as lastGameDate,"
    "optaPersonId,firstName,lastName,"
    "mode(game.gameDetailedPosition) as Position,"
    "[GM],[Min],[Age],[NPxG],[GoalExPn],[Weight],[Height],[Nation],"
    "[ShtBlk],[Int],[TcklAtt],[PsIntoA3rd],[TakeOn%],[TakeOn],"
    "[ProgCarry],[ProgPass],[Duels],[Aerials],[Position],[PsAtt],[Pass%],"
    "[Chance],[Ast],[xA],[Goal],[ExpG],[ShtIncBl] AS Shot,"
    "[Tackle%],[Duel%],[Aerial%]"
)


def parse_cookies_from_curl(curl_string):
    """Extract auth cookies from a cURL command copied from Chrome DevTools.

    Handles Windows-style cURL (with ^ escapes) and Unix-style.
    Returns dict of cookie name -> value.
    Raises ValueError if required cookies are missing or can't be parsed.
    """
    curl_string = curl_string.replace('^"', '"')
    curl_string = curl_string.replace('^%', '%')
    curl_string = curl_string.replace('^', '')

    match = re.search(r'-b\s+"([^"]*)"', curl_string) or re.search(r"-b\s+'([^']*)'", curl_string)
    if not match:
        raise ValueError(
            "Could not find cookie string in cURL.\n"
            "Make sure you right-clicked the dp-proxy-export request and chose 'Copy as cURL'."
        )

    cookie_string = match.group(1)

    cookies = {}
    for part in cookie_string.split('; '):
        if '=' in part:
            name, _, value = part.partition('=')
            cookies[name.strip()] = value.strip()

    required = ['accessToken', 'auth-ns:session', 'auth-ns:session.sig']
    missing = [c for c in required if c not in cookies]
    if missing:
        raise ValueError(
            f"Missing required cookies: {', '.join(missing)}\n"
            "Make sure you copied the cURL from a TruMedia export request, not another page."
        )

    return {k: cookies[k] for k in required}


def create_session(cookies):
    """Create a requests session with TruMedia auth cookies."""
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain='cbssports.opta.trumediasports.com')
    session.headers.update({
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'origin': 'https://cbssports.opta.trumediasports.com',
        'user-agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/145.0.0.0 Safari/537.36'
        ),
    })
    return session


def build_player_pool_statement(season_ids):
    """Build the SQL statement for a player pool download with today's date range."""
    today = date.today()
    start = today - timedelta(days=365)
    season_id_str = ",".join(f"'{s}'" for s in season_ids)

    return (
        f"{PLAYER_POOL_SELECT}, "
        f"FROM player 'p' SHOW 'Per90' "
        f"WHERE ((game.player)) AND "
        f"((game.gameDate >= '{start}') AND "
        f"(game.gameDate <= '{today} 23:59:59') AND "
        f"((player.position='Defender') OR "
        f"((player.position='Forward' OR player.position='Attacker')) OR "
        f"(player.position='Midfielder')) AND "
        f"(season.seasonId IN ({season_id_str}))) "
        f"RANK order ORDER BY 'Min' DESC  LIMIT 100000 CALCULATE total average"
    )


def download_player_pool(session, season_ids, output_path):
    """Download a player pool CSV and save to output_path.

    Returns (row_count, size_kb) on success.
    Raises on auth failure, network error, or unexpected response.
    """
    statement = build_player_pool_statement(season_ids)
    payload = {
        "format": "MIXED",
        "statement": statement,
        "export": "csv",
        "pageDescriptorName": "pageSoccerPlayersInPossession",
        "exportOptions": {"includeCalculations": False, "includeVideoData": False},
    }

    response = session.post(EXPORT_URL, json=payload, timeout=120)
    response.raise_for_status()

    content = response.content

    if b'<!DOCTYPE html>' in content[:500] or b'<html' in content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired — paste a fresh cURL command."
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(content)

    row_count = max(0, content.count(b'\n') - 1)
    size_kb = len(content) / 1024

    return row_count, size_kb


# ── Event Log ─────────────────────────────────────────────────────────────────

EVENT_LOG_SELECT = (
    "SELECT "
    "game.gameId+':'+event.gameEventIndex as eventGuid,"
    "game.gameId,"
    "game.optaMatchId,"
    "event.gameEventIndex,"
    "event.sequenceId,"
    "event.sequenceEventNum,"
    "event.possessionSeqNum,"
    "event.possessionNumInPeriod,"
    "event.possessionSeconds,"
    "event.sequenceSeconds,"
    "event.playType,"
    "event.period as Period,"
    "event.gameClock,"
    "lookup(event.toucher,abbrevName) AS toucher,"
    "lookup(event.passer,abbrevName) AS passer,"
    "lookup(event.receiver,abbrevName) AS receiver,"
    "lookup(event.shooter,abbrevName) AS shooter,"
    "lookup(event.goalie,abbrevName) AS goalie,"
    "lookup(event.assister,abbrevName) AS assister,"
    "lookup(event.blocker,abbrevName) AS blocker,"
    "event.toucherPlayerId AS toucherId,"
    "event.passerPlayerId AS passerId,"
    "event.receiverPlayerId AS receiverId,"
    "event.shooterPlayerId AS shooterId,"
    "event.assisterPlayerId AS assisterId,"
    "event.blockerPlayerId AS blockerId,"
    "event.goaliePlayerId AS goalieId,"
    "newest(team.game.teamId) AS teamId,"
    "newest(team.game.fullName) as teamFullName,"
    "newest(team.game.abbrevName) as teamAbbrevName,"
    "newest(team.game.teamColor) as newestTeamColor,"
    "newest(team.game.optaTeamId) AS optaTeamId,"
    "format(\"date\",\"yyyy-MM-dd\",game.gameDate) as Date,"
    "if(team.game.home,team.game.fullName,opponent.game.fullName) as homeTeam,"
    "if(team.game.home,opponent.game.fullName,team.game.fullName) as awayTeam,"
    "if(team.game.home,team.event.currentScore,opponent.event.currentScore) as homeCurrentScore,"
    "if(team.game.home,opponent.event.currentScore,team.event.currentScore) as awayCurrentScore,"
    "if(team.game.home,team.game.finalScore,opponent.game.finalScore) as homeFinalScore,"
    "if(team.game.home,opponent.game.finalScore,team.game.finalScore) as awayFinalScore,"
    "team.game.finalScore as teamFinalScore,"
    "opponent.game.finalScore as opponentFinalScore,"
    "team.event.currentScore as teamCurrentScore,"
    "team.event.currentScoreOpponent as opponentCurrentScore,"
    "opponent.game.abbrevName as opponent,"
    "newest(opponent.game.teamId) AS opponentId,"
    "[EventY|EVENT] AS STAT('EventYDecimal', 'EventYDecimal', 'EventYDecimal', true, false, 'TeamStats|OpponentStats', NUMBER|0.00|- ),"
    "[EventX|EVENT] AS STAT('EventXDecimal', 'EventXDecimal', 'EventXDecimal', true, false, 'TeamStats|OpponentStats', NUMBER|0.00|- ),"
    "[PassEndY|EVENT] AS STAT('PassEndYDecimal', 'PassEndYDecimal', 'PassEndYDecimal', true, false, 'TeamStats|OpponentStats', NUMBER|0.00|- ),"
    "[PassEndX|EVENT] AS STAT('PassEndXDecimal', 'PassEndXDecimal', 'PassEndXDecimal', true, false, 'TeamStats|OpponentStats', NUMBER|0.00|- ),"
    "[xG|EVENT],[xA|EVENT],[ShotDist|EVENT],[BodyPart|EVENT],[ShotPlayStyle|EVENT],"
    "season.seasonId as seasonId,"
    "season.seasonName as seasonName"
)

_INT_COLS = {
    'optaMatchId', 'gameEventIndex', 'Period', 'gameClock', 'optaTeamId',
    'homeCurrentScore', 'awayCurrentScore', 'homeFinalScore', 'awayFinalScore',
    'teamCurrentScore', 'opponentCurrentScore', 'teamFinalScore', 'opponentFinalScore',
}
_NULLABLE_INT_COLS = {
    'sequenceId', 'sequenceEventNum', 'possessionSeqNum', 'possessionNumInPeriod',
}
_FLOAT_COLS = {
    'possessionSeconds', 'sequenceSeconds',
    'EventXDecimal', 'EventYDecimal', 'PassEndXDecimal', 'PassEndYDecimal',
    'xG', 'xA', 'ShotDist',
}

# Columns stored in the MotherDuck events table (matches EVENTS_DDL exactly)
EVENTS_MD_COLS = [
    'eventGuid', 'gameId', 'optaMatchId', 'gameEventIndex',
    'sequenceId', 'sequenceEventNum', 'possessionSeqNum', 'possessionNumInPeriod',
    'possessionSeconds', 'sequenceSeconds',
    'playType', 'Period', 'gameClock',
    'toucher', 'passer', 'receiver', 'shooter', 'goalie', 'assister', 'blocker',
    'toucherId', 'passerId', 'receiverId', 'shooterId', 'assisterId', 'blockerId', 'goalieId',
    'teamId', 'teamFullName', 'teamAbbrevName', 'newestTeamColor', 'optaTeamId',
    'Date', 'homeTeam', 'awayTeam',
    'homeCurrentScore', 'awayCurrentScore', 'homeFinalScore', 'awayFinalScore',
    'teamCurrentScore', 'opponentCurrentScore', 'teamFinalScore', 'opponentFinalScore',
    'opponent', 'opponentId',
    'EventXDecimal', 'EventYDecimal', 'PassEndXDecimal', 'PassEndYDecimal',
    'xG', 'xA', 'ShotDist', 'BodyPart', 'ShotPlayStyle',
    'seasonId',
]

GAMES_DDL = """
CREATE TABLE IF NOT EXISTS games (
    gameId VARCHAR PRIMARY KEY,
    optaMatchId INTEGER,
    Date VARCHAR,
    homeTeam VARCHAR,
    awayTeam VARCHAR,
    homeTeamId VARCHAR,
    awayTeamId VARCHAR,
    homeFinalScore INTEGER,
    awayFinalScore INTEGER,
    seasonId VARCHAR
)
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    eventGuid VARCHAR PRIMARY KEY,
    gameId VARCHAR,
    optaMatchId INTEGER,
    gameEventIndex INTEGER,
    sequenceId INTEGER,
    sequenceEventNum INTEGER,
    possessionSeqNum INTEGER,
    possessionNumInPeriod INTEGER,
    possessionSeconds DOUBLE,
    sequenceSeconds DOUBLE,
    playType VARCHAR,
    Period INTEGER,
    gameClock INTEGER,
    toucher VARCHAR,
    passer VARCHAR,
    receiver VARCHAR,
    shooter VARCHAR,
    goalie VARCHAR,
    assister VARCHAR,
    blocker VARCHAR,
    toucherId VARCHAR,
    passerId VARCHAR,
    receiverId VARCHAR,
    shooterId VARCHAR,
    assisterId VARCHAR,
    blockerId VARCHAR,
    goalieId VARCHAR,
    teamId VARCHAR,
    teamFullName VARCHAR,
    teamAbbrevName VARCHAR,
    newestTeamColor VARCHAR,
    optaTeamId INTEGER,
    Date VARCHAR,
    homeTeam VARCHAR,
    awayTeam VARCHAR,
    homeCurrentScore INTEGER,
    awayCurrentScore INTEGER,
    homeFinalScore INTEGER,
    awayFinalScore INTEGER,
    teamCurrentScore INTEGER,
    opponentCurrentScore INTEGER,
    teamFinalScore INTEGER,
    opponentFinalScore INTEGER,
    opponent VARCHAR,
    opponentId VARCHAR,
    EventXDecimal DOUBLE,
    EventYDecimal DOUBLE,
    PassEndXDecimal DOUBLE,
    PassEndYDecimal DOUBLE,
    xG DOUBLE,
    xA DOUBLE,
    ShotDist DOUBLE,
    BodyPart VARCHAR,
    ShotPlayStyle VARCHAR,
    seasonId VARCHAR
)
"""


GAME_FIXTURES_DDL = """
CREATE TABLE IF NOT EXISTS game_fixtures (
    gameId VARCHAR PRIMARY KEY,
    fixture_id INTEGER,
    fetch_status VARCHAR,
    fetched_at VARCHAR
)
"""

PLAYER_MINUTES_DDL = """
CREATE TABLE IF NOT EXISTS player_minutes (
    gameId VARCHAR,
    playerName VARCHAR,
    teamName VARCHAR,
    minutes INTEGER,
    started BOOLEAN,
    PRIMARY KEY (gameId, playerName)
)
"""

OWN_GOALS_DDL = """
CREATE TABLE IF NOT EXISTS own_goals (
    gameId VARCHAR,
    minute INTEGER,
    credited_team VARCHAR,
    PRIMARY KEY (gameId, minute)
)
"""

CARDS_DDL = """
CREATE TABLE IF NOT EXISTS cards (
    gameId VARCHAR,
    minute INTEGER,
    playerName VARCHAR,
    teamName VARCHAR,
    card_type VARCHAR,
    PRIMARY KEY (gameId, minute, playerName)
)
"""


def get_motherduck_connection(token):
    """Open a connection to MotherDuck and ensure the database and tables exist."""
    # Connect to default database first to create our database if needed
    bootstrap = duckdb.connect(f"md:?motherduck_token={token}")
    bootstrap.execute(f"CREATE DATABASE IF NOT EXISTS {MOTHERDUCK_DB}")
    bootstrap.close()

    con = duckdb.connect(f"md:{MOTHERDUCK_DB}?motherduck_token={token}")
    con.execute(GAMES_DDL)
    con.execute(EVENTS_DDL)
    con.execute(GAME_FIXTURES_DDL)
    con.execute(PLAYER_MINUTES_DDL)
    con.execute(OWN_GOALS_DDL)
    con.execute(CARDS_DDL)
    con.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS seasonId VARCHAR")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS seasonId VARCHAR")
    return con


def get_all_team_last_dates(con):
    """Return dict of teamId -> most recent event Date for all teams in MotherDuck."""
    rows = con.execute(
        "SELECT teamId, MAX(Date) FROM events GROUP BY teamId"
    ).fetchall()
    return {team_id: last_date for team_id, last_date in rows if last_date}


# ── API-Football ──────────────────────────────────────────────────────────────

def _apifootball_get(api_key, endpoint, params):
    """Make a GET request to the API-Football v3 API."""
    headers = {"x-apisports-key": api_key}
    r = requests.get(
        f"{API_FOOTBALL_BASE}/{endpoint}",
        headers=headers,
        params=params,
        timeout=15,
    )
    r.raise_for_status()
    result = r.json()
    errors = result.get("errors")
    if errors:
        raise RuntimeError(f"API-Football error: {errors}")
    return result


def fetch_fixture_id(api_key, date_str, home_team, away_team, league_id=None):
    """Match a TruMedia game to an API-Football fixture ID by date + team names.

    Normalizes team names through TRUMEDIA_TO_API_NAME before scoring.
    If league_id is provided, restricts the query to that competition only
    (prevents false matches across competitions on the same date).
    Returns (fixture_id, score) or (None, 0.0) if no reliable match found.
    """
    params = {"date": date_str}
    if league_id is not None:
        params["league"] = league_id
        # API-Football requires season when league is provided.
        # Derive season year: for games Aug-Dec use the game year;
        # for games Jan-Jul use game year - 1 (covers European split seasons).
        try:
            game_date = _dt.strptime(date_str[:10], "%Y-%m-%d")
            params["season"] = game_date.year if game_date.month >= 7 else game_date.year - 1
        except ValueError:
            pass
    result = _apifootball_get(api_key, "fixtures", params)
    fixtures = result.get("response", [])
    if not fixtures:
        return None, 0.0

    norm_home = TRUMEDIA_TO_API_NAME.get(home_team, home_team)
    norm_away = TRUMEDIA_TO_API_NAME.get(away_team, away_team)

    best_score = 0.0
    best_fixture_id = None
    best_api_home = best_api_away = ""
    for f in fixtures:
        api_home = f["teams"]["home"]["name"]
        api_away = f["teams"]["away"]["name"]
        score = (
            difflib.SequenceMatcher(None, norm_home.lower(), api_home.lower()).ratio() +
            difflib.SequenceMatcher(None, norm_away.lower(), api_away.lower()).ratio()
        ) / 2
        if score > best_score:
            best_score = score
            best_fixture_id = f["fixture"]["id"]
            best_api_home = api_home
            best_api_away = api_away

    if best_score >= 0.70:
        return best_fixture_id, best_score

    # Print the near-miss so the user can add the right mapping to TRUMEDIA_TO_API_NAME
    if best_api_home:
        print(
            f"  near-miss ({best_score:.2f}): '{home_team}' vs '{away_team}'  →  "
            f"API had '{best_api_home}' vs '{best_api_away}'"
        )
    return None, best_score


def compute_player_minutes(lineups_response, events_response):
    """Compute minutes played per player from API-Football lineups + events.

    Returns:
        player_minutes_list: [{playerName, teamName, minutes, started}]
        own_goals_list: [{minute, credited_team}]
        cards_list: [{minute, playerName, teamName, card_type}]
    """
    lineups = lineups_response.get("response", [])
    events = events_response.get("response", [])

    team_names = [t["team"]["name"] for t in lineups if "team" in t]

    # Build player registry from lineups
    players = {}
    for team_data in lineups:
        team_name = team_data["team"]["name"]
        for entry in team_data.get("startXI", []):
            name = entry["player"]["name"]
            players[name] = {"teamName": team_name, "started": True, "minutes": 90}
        for entry in team_data.get("substitutes", []):
            name = entry["player"]["name"]
            players[name] = {"teamName": team_name, "started": False, "minutes": 0}

    if not players:
        return [], [], []

    # Determine actual game duration (handles extra time)
    game_duration = 90
    for ev in events:
        elapsed = ev.get("time", {}).get("elapsed") or 0
        extra = ev.get("time", {}).get("extra") or 0
        effective = elapsed + extra
        if effective > game_duration:
            game_duration = effective

    # Update all starters to actual game duration
    for p in players.values():
        if p["started"]:
            p["minutes"] = game_duration

    own_goals_list = []
    cards_list = []
    for ev in events:
        ev_type = ev.get("type", "")
        elapsed = ev.get("time", {}).get("elapsed") or 0
        extra = ev.get("time", {}).get("extra") or 0
        effective_minute = elapsed + extra

        if ev_type == "subst":
            player_in = (ev.get("player") or {}).get("name", "")
            player_out = (ev.get("assist") or {}).get("name", "")
            team_name = (ev.get("team") or {}).get("name", "")

            if player_out in players:
                players[player_out]["minutes"] = effective_minute
            elif player_out:
                players[player_out] = {"teamName": team_name, "started": True,
                                       "minutes": effective_minute}

            if player_in in players:
                players[player_in]["minutes"] = game_duration - effective_minute
            elif player_in:
                players[player_in] = {"teamName": team_name, "started": False,
                                      "minutes": game_duration - effective_minute}

        elif ev_type == "Goal" and ev.get("detail") == "Own Goal":
            scoring_team = (ev.get("team") or {}).get("name", "")
            credited_team = next((t for t in team_names if t != scoring_team), scoring_team)
            own_goals_list.append({"minute": effective_minute, "credited_team": credited_team})

        elif ev_type == "Card":
            detail = ev.get("detail", "")
            # Normalize to consistent names
            card_type = {
                "Yellow Card": "yellow",
                "Red Card": "red",
                "Yellow Red Card": "second_yellow",
            }.get(detail, detail.lower().replace(" ", "_"))
            player_name = (ev.get("player") or {}).get("name", "")
            team_name = (ev.get("team") or {}).get("name", "")
            if player_name:
                cards_list.append({
                    "minute": effective_minute,
                    "playerName": player_name,
                    "teamName": team_name,
                    "card_type": card_type,
                })

    player_minutes_list = [
        {
            "playerName": name,
            "teamName": p["teamName"],
            "minutes": max(0, p["minutes"]),
            "started": p["started"],
        }
        for name, p in players.items()
        if p["minutes"] > 0
    ]
    return player_minutes_list, own_goals_list, cards_list


def fetch_and_store_fixture_data(api_key, token, game_id, date, home, away, con=None, season_id=None, fixture_id=None):
    """Fetch lineups + events from API-Football for one game and store to MotherDuck.

    Writes to game_fixtures, player_minutes, own_goals, and cards tables.
    season_id is used to look up the API-Football league ID for filtered queries.
    If fixture_id is provided, the API-Football fixture lookup is skipped entirely.
    Returns fetch_status string: 'matched', 'not_found', or 'error: ...'
    """
    fetched_at = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)

    league_id = SEASON_TO_API_LEAGUE.get(season_id) if season_id else None

    try:
        if fixture_id is None:
            fixture_id, _score = fetch_fixture_id(api_key, str(date)[:10], home, away, league_id=league_id)

        if fixture_id is None:
            con.execute(
                "INSERT OR REPLACE INTO game_fixtures VALUES (?, ?, ?, ?)",
                [game_id, None, "not_found", fetched_at],
            )
            return "not_found"

        lineups_resp = _apifootball_get(api_key, "fixtures/lineups", {"fixture": fixture_id})
        events_resp = _apifootball_get(api_key, "fixtures/events", {"fixture": fixture_id})

        player_minutes_list, own_goals_list, cards_list = compute_player_minutes(lineups_resp, events_resp)

        if player_minutes_list:
            pm_df = pd.DataFrame(player_minutes_list)
            pm_df["gameId"] = game_id
            pm_df = pm_df[["gameId", "playerName", "teamName", "minutes", "started"]]
            con.register("_pm_staging", pm_df)
            con.execute("INSERT OR REPLACE INTO player_minutes SELECT * FROM _pm_staging")
            con.unregister("_pm_staging")

        if own_goals_list:
            og_df = pd.DataFrame(own_goals_list)
            og_df["gameId"] = game_id
            og_df = og_df[["gameId", "minute", "credited_team"]]
            con.register("_og_staging", og_df)
            con.execute("INSERT OR REPLACE INTO own_goals SELECT * FROM _og_staging")
            con.unregister("_og_staging")

        if cards_list:
            c_df = pd.DataFrame(cards_list)
            c_df["gameId"] = game_id
            c_df = c_df[["gameId", "minute", "playerName", "teamName", "card_type"]]
            con.register("_c_staging", c_df)
            con.execute("INSERT OR REPLACE INTO cards SELECT * FROM _c_staging")
            con.unregister("_c_staging")

        con.execute(
            "INSERT OR REPLACE INTO game_fixtures VALUES (?, ?, ?, ?)",
            [game_id, fixture_id, "matched", fetched_at],
        )
        return "matched"

    except Exception as e:
        try:
            con.execute(
                "INSERT OR REPLACE INTO game_fixtures VALUES (?, ?, ?, ?)",
                [game_id, None, f"error: {str(e)[:200]}", fetched_at],
            )
        except Exception:
            pass
        return f"error: {e}"

    finally:
        if own_con:
            con.close()


def get_games_missing_fixture_data(con, season_ids=None):
    """Return games that have no entry in game_fixtures (fetch never attempted).

    Args:
        season_ids: optional list/set of seasonId strings to restrict results.

    Returns list of dicts: {gameId, Date, homeTeam, awayTeam, seasonId}.
    """
    if season_ids:
        placeholders = ",".join("?" * len(season_ids))
        rows = con.execute(f"""
            SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, g.seasonId
            FROM games g
            LEFT JOIN game_fixtures gf ON g.gameId = gf.gameId
            WHERE gf.gameId IS NULL
              AND g.seasonId IN ({placeholders})
            ORDER BY g.Date DESC
        """, list(season_ids)).fetchall()
    else:
        rows = con.execute("""
            SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, g.seasonId
            FROM games g
            LEFT JOIN game_fixtures gf ON g.gameId = gf.gameId
            WHERE gf.gameId IS NULL
            ORDER BY g.Date DESC
        """).fetchall()
    return [
        {"gameId": r[0], "Date": r[1], "homeTeam": r[2], "awayTeam": r[3], "seasonId": r[4]}
        for r in rows
    ]


def build_event_log_statement(team_id, season_ids, since_date=None):
    """Build the SQL statement for a team event log download.

    If since_date is provided (a date object or 'YYYY-MM-DD' string),
    only events from games on or after that date are returned.
    """
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    date_filter = f"AND (game.gameDate >= '{since_date}') " if since_date else ""
    return (
        f"{EVENT_LOG_SELECT} "
        f"FROM team BY event "
        f"WHERE ((team.teamId ='{team_id}') AND ((event.toucher))) "
        f"AND ((season.seasonId IN ({season_id_str}))) "
        f"{date_filter}"
        f"ORDER BY event.gameEventIndex ASC "
        f"LIMIT 100000"
    )


def download_event_log(session, team_id, season_ids, output_path, since_date=None):
    """Download a team event log CSV and save to output_path.

    Returns (row_count, size_kb) on success.
    Raises on auth failure, network error, or unexpected response.
    """
    statement = build_event_log_statement(team_id, season_ids, since_date=since_date)
    payload = {
        "format": "MIXED",
        "statement": statement,
        "export": "csv",
        "pageDescriptorName": "pageSoccerTeamEventLogInPossession",
        "exportOptions": {"includeCalculations": False, "includeVideoData": False},
    }

    response = session.post(EXPORT_URL, json=payload, timeout=120)
    if not response.ok:
        raise ValueError(
            f"HTTP {response.status_code} {response.reason}: {response.text[:500]}"
        )

    content = response.content

    if b'<!DOCTYPE html>' in content[:500] or b'<html' in content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired — paste a fresh cURL command."
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(content)

    row_count = max(0, content.count(b'\n') - 1)
    size_kb = len(content) / 1024

    return row_count, size_kb


def backfill_season_ids(con, config):
    """Infer and populate seasonId for games that don't have one yet.

    Uses the intersection of both teams' season_ids from config to determine
    which season each game belongs to. When two teams share both a domestic
    league season and a UEFA competition season, the domestic (primary) season
    is preferred.

    Returns (updated, skipped, skipped_details).
    """
    team_id_to_seasons = {t['team_id']: t['season_ids'] for t in config['teams']}
    secondary_seasons = set(config.get('secondary_seasons', []))

    rows = con.execute("""
        SELECT gameId, homeTeamId, awayTeamId
        FROM games
        WHERE seasonId IS NULL OR seasonId = ''
    """).fetchall()

    updated = 0
    skipped = 0
    updates = []
    skipped_details = []

    for game_id, home_team_id, away_team_id in rows:
        home_seasons = set(team_id_to_seasons.get(home_team_id, []))
        away_seasons = set(team_id_to_seasons.get(away_team_id, []))
        intersection = home_seasons & away_seasons

        chosen = None
        if len(intersection) == 1:
            chosen = list(intersection)[0]
        elif len(intersection) == 2:
            # If exactly one season is a primary (domestic) league, prefer it
            primary_in_intersection = [s for s in intersection if s not in secondary_seasons]
            if len(primary_in_intersection) == 1:
                chosen = primary_in_intersection[0]

        if chosen is not None:
            updates.append((chosen, game_id))
            updated += 1
        else:
            reason = "no teams in config" if not home_seasons and not away_seasons \
                else "home team not in config" if not home_seasons \
                else "away team not in config" if not away_seasons \
                else f"ambiguous ({len(intersection)} matching seasons)"
            skipped_details.append({
                'game_id': game_id,
                'home_team_id': home_team_id,
                'away_team_id': away_team_id,
                'reason': reason,
            })
            skipped += 1

    if updates:
        con.executemany("UPDATE games SET seasonId = ? WHERE gameId = ?", updates)
        con.execute("""
            UPDATE events SET seasonId = g.seasonId
            FROM games g
            WHERE events.gameId = g.gameId
              AND (events.seasonId IS NULL OR events.seasonId = '')
              AND g.seasonId IS NOT NULL
        """)

    return updated, skipped, skipped_details


def upsert_events_to_motherduck(token, csv_path, con=None):
    """Parse a team event log CSV and upsert into MotherDuck games + events tables.

    If con is provided it is reused (caller manages lifecycle).
    Otherwise a connection is opened and closed after the upsert.

    Returns row_count on success.
    """
    df = pd.read_csv(csv_path, encoding='utf-8')

    # Fill missing schema columns with safe defaults
    for col in _INT_COLS:
        if col not in df.columns:
            df[col] = 0
    for col in _NULLABLE_INT_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    for col in _FLOAT_COLS:
        if col not in df.columns:
            df[col] = float('nan')

    # Type coercions
    for col in _INT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
    for col in _NULLABLE_INT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
    for col in _FLOAT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # ── Build games DataFrame ──────────────────────────────────────────────────
    game_src_cols = ['gameId', 'optaMatchId', 'Date', 'homeTeam', 'awayTeam',
                     'homeFinalScore', 'awayFinalScore', 'teamFullName', 'teamId', 'opponentId',
                     'seasonId']
    games_df = df[[c for c in game_src_cols if c in df.columns]].drop_duplicates('gameId').copy()

    if all(c in games_df.columns for c in ('teamFullName', 'teamId', 'opponentId', 'homeTeam')):
        is_home = games_df['teamFullName'] == games_df['homeTeam']
        games_df['homeTeamId'] = games_df['teamId'].where(is_home, games_df['opponentId'])
        games_df['awayTeamId'] = games_df['opponentId'].where(is_home, games_df['teamId'])

    games_final = ['gameId', 'optaMatchId', 'Date', 'homeTeam', 'awayTeam',
                   'homeTeamId', 'awayTeamId', 'homeFinalScore', 'awayFinalScore', 'seasonId']
    games_df = games_df[[c for c in games_final if c in games_df.columns]]

    # ── Build events DataFrame ─────────────────────────────────────────────────
    events_df = df[[c for c in EVENTS_MD_COLS if c in df.columns]].copy()

    # ── Upsert ────────────────────────────────────────────────────────────────
    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)

    try:
        con.register("_games_staging", games_df)
        con.execute("INSERT OR REPLACE INTO games SELECT * FROM _games_staging")
        con.unregister("_games_staging")

        con.register("_events_staging", events_df)
        con.execute("INSERT OR REPLACE INTO events SELECT * FROM _events_staging")
        con.unregister("_events_staging")
    finally:
        if own_con:
            con.close()

    return len(df)
