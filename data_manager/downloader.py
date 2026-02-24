"""
TruMedia Downloader
Handles authentication via cURL parsing and data downloads via POST requests.
"""
import re
import os
import requests
from datetime import date, timedelta


EXPORT_URL = "https://cbssports.opta.trumediasports.com/dp-proxy-export"
SUPABASE_BUCKET = "player-pools"


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
    Raises requests.HTTPError on failure.
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
    # Normalize Windows CMD escape characters (order matters)
    curl_string = curl_string.replace('^"', '"')
    curl_string = curl_string.replace('^%', '%')
    curl_string = curl_string.replace('^', '')

    # Find -b "..." cookie string
    match = re.search(r'-b\s+"([^"]*)"', curl_string)
    if not match:
        raise ValueError(
            "Could not find cookie string in cURL.\n"
            "Make sure you right-clicked the dp-proxy-export request and chose 'Copy as cURL'."
        )

    cookie_string = match.group(1)

    # Parse individual cookies
    cookies = {}
    for part in cookie_string.split('; '):
        if '=' in part:
            name, _, value = part.partition('=')
            cookies[name.strip()] = value.strip()

    # Validate required cookies are present
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

    # Detect if we got an HTML error page instead of CSV
    if b'<!DOCTYPE html>' in content[:500] or b'<html' in content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired — paste a fresh cURL command."
        )

    # Save to disk
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(content)

    row_count = max(0, content.count(b'\n') - 1)
    size_kb = len(content) / 1024

    return row_count, size_kb
