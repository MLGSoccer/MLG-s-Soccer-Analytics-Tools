"""
TruMedia Downloader
Handles authentication via cURL parsing and data downloads via POST requests.
"""
import re
import os
import requests
import duckdb
import pandas as pd
from datetime import date, timedelta


EXPORT_URL = "https://cbssports.opta.trumediasports.com/dp-proxy-export"
SUPABASE_BUCKET = "player-pools"
MOTHERDUCK_DB = "soccer"


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
    "[xG|EVENT],[xA|EVENT],[ShotDist|EVENT],[BodyPart|EVENT],[ShotPlayStyle|EVENT]"
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
    awayFinalScore INTEGER
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
    ShotPlayStyle VARCHAR
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
    return con


def get_all_team_last_dates(con):
    """Return dict of teamId -> most recent event Date for all teams in MotherDuck."""
    rows = con.execute(
        "SELECT teamId, MAX(Date) FROM events GROUP BY teamId"
    ).fetchall()
    return {team_id: last_date for team_id, last_date in rows if last_date}


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
                     'homeFinalScore', 'awayFinalScore', 'teamFullName', 'teamId', 'opponentId']
    games_df = df[[c for c in game_src_cols if c in df.columns]].drop_duplicates('gameId').copy()

    if all(c in games_df.columns for c in ('teamFullName', 'teamId', 'opponentId', 'homeTeam')):
        is_home = games_df['teamFullName'] == games_df['homeTeam']
        games_df['homeTeamId'] = games_df['teamId'].where(is_home, games_df['opponentId'])
        games_df['awayTeamId'] = games_df['opponentId'].where(is_home, games_df['teamId'])

    games_final = ['gameId', 'optaMatchId', 'Date', 'homeTeam', 'awayTeam',
                   'homeTeamId', 'awayTeamId', 'homeFinalScore', 'awayFinalScore']
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
