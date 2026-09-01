"""
TruMedia Downloader
Handles authentication via cURL parsing and data downloads via POST requests.
"""
import io
import re
import os
import json
import tempfile
import difflib
import requests
import duckdb
import pandas as pd
from datetime import date, timedelta, datetime as _dt


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.env")


def save_config(config, con=None, token=None):
    """Write config.json AND mirror it to MotherDuck, in one action.

    Every place that changes the config must go through here. config.json is a
    local file; the deployed chart maker reads the MotherDuck copy. Writing them
    separately - or writing only the file and relying on someone to commit it -
    is what let a downloaded season stay invisible to the chart maker.

    The file write comes first and is not conditional on MotherDuck: losing the
    local copy because the network blipped would be worse than a stale mirror.

    Returns (mirrored_at, error). `mirrored_at` is None if the mirror did not
    happen; `error` carries why. Callers should SURFACE that rather than ignore
    it - a silently failed mirror recreates the exact divergence this avoids.
    """
    import sys
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    try:
        from shared.config_store import write_config
    except Exception as e:                                    # pragma: no cover
        return None, f"config_store unavailable: {e}"

    try:
        if con is None:
            if token is None:
                token = load_secrets(SECRETS_PATH).get("MOTHERDUCK_TOKEN")
            if not token:
                return None, "no MOTHERDUCK_TOKEN - local file written, mirror skipped"
            con = get_motherduck_connection(token)
        return write_config(con, config), None
    except Exception as e:
        return None, f"mirror to MotherDuck failed: {e}"


EXPORT_URL = "https://cbssports.opta.trumediasports.com/dp-proxy-export"
SUPABASE_BUCKET = "player-pools"
MOTHERDUCK_DB = "soccer"


def extract_season_id(url_or_id):
    """Pull a TruMedia season id out of a stats URL, or pass an id through.

    TruMedia encodes filters in a url-escaped JSON `f` parameter; the season
    list lives at `f.sseas`:

        ?f=%7B%22sseas%22%3A%5B%226i6n0jkbh9zzij6s8htfjh2j8%22%5D%7D
        -> {"sseas": ["6i6n0jkbh9zzij6s8htfjh2j8"]}

    Returns the first season id found. Raises ValueError with a message meant
    to be shown to the user if nothing usable is present.
    """
    from urllib.parse import urlparse, parse_qs, unquote

    text = (url_or_id or "").strip()
    if not text:
        raise ValueError("Nothing pasted.")

    # A bare id: TruMedia's are 24-25 chars of lowercase alphanumerics.
    if re.fullmatch(r"[a-z0-9]{20,30}", text):
        return text

    if "://" not in text:
        raise ValueError(
            "Doesn't look like a TruMedia URL or a season id. Paste the full "
            "URL from a TruMedia stats page, or the season id itself."
        )

    params = parse_qs(urlparse(text).query)
    if "f" in params:
        try:
            filters = json.loads(unquote(params["f"][0]))
            seasons = filters.get("sseas") or []
            if seasons:
                return seasons[0]
        except (ValueError, AttributeError):
            pass

    # Fall back to scanning the whole URL - TruMedia has moved this parameter
    # before, and an id-shaped token in the query string is still a good bet.
    for candidate in re.findall(r"[a-z0-9]{20,30}", unquote(text)):
        return candidate

    raise ValueError(
        "No season id found in that URL. Make sure a season is selected on "
        "the TruMedia page before copying the address."
    )


def suggest_next_label(previous_label):
    """Guess the new season's display label from the previous one.

    "Premier League 2025/26" -> "Premier League 2026/27"   (split season)
    "MLS 2026"               -> "MLS 2027"                 (calendar year)

    A guess only - the form leaves it editable, and competitions that brand
    their seasons differently just get retyped.
    """
    if not previous_label:
        return ""

    split = re.search(r"(\d{4})/(\d{2})$", previous_label)
    if split:
        start = int(split.group(1)) + 1
        return f"{previous_label[:split.start()]}{start}/{str(start + 1)[-2:]}"

    single = re.search(r"(\d{4})$", previous_label)
    if single:
        return f"{previous_label[:single.start()]}{int(single.group(1)) + 1}"

    return previous_label


def group_teams_by_league(config):
    """Group config["teams"] into {league name: [team, ...]}.

    Two rules, both of which used to be wrong when this logic lived inline
    in app.py:

    1. Group by LEAGUE NAME (`season_leagues`), not the season's display
       label (`seasons`). Labels carry the season - "Premier League 2025/26"
       - so the moment a promoted club arrives carrying only the 2026/27 id,
       it lands in a second expander and the league splits in two. Names are
       stable across rollovers.

    2. Prefer a PRIMARY season over a secondary one. Most clubs carry both a
       domestic id and one or more UEFA ids; picking naively (say, the
       alphabetically first league name) files every Champions League
       qualifier under "Champions League" and empties out the domestic
       leagues. Teams whose only seasons are secondary - the UEFA-qualifier
       tail that plays no league we track - still group under that
       competition rather than falling into "Other".
    """
    secondary = set(config.get("secondary_seasons", []))
    league_names = config.get("season_leagues", {})

    grouped = {}
    for team in config.get("teams", []):
        season_ids = team.get("season_ids", [])
        primary = next((s for s in season_ids if s not in secondary), None)
        if primary:
            name = league_names.get(primary, "Other")
        else:
            first_secondary = next((s for s in season_ids if s in secondary), None)
            name = league_names.get(first_secondary, "Other") if first_secondary else "Other"
        grouped.setdefault(name, []).append(team)
    return grouped


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


def probe_endpoint_health(session, sample_season_id):
    """Quick health check after cookies are pasted: fire a tiny POST to
    `/dp-proxy-export` and confirm it returns CSV. Catches three failure
    modes before the user kicks off a long download cycle:

      1. Cookies expired / invalid -> 401 / 403
      2. Endpoint removed (TruMedia decommissions it) -> 404 / HTML
      3. Response format changed -> not CSV

    Returns (ok: bool, message: str). On success the message is a one-line
    "endpoint alive" confirmation; on failure it names the failure mode so
    the user knows whether to refresh cookies vs. expect a refactor.

    Uses a 10s timeout so a hung endpoint doesn't block the UI for long.
    """
    statement = build_player_pool_statement([sample_season_id])
    payload = {
        "format": "MIXED",
        "statement": statement,
        "export": "csv",
        "pageDescriptorName": "pageSoccerPlayersInPossession",
        "exportOptions": {"includeCalculations": False, "includeVideoData": False},
    }
    try:
        resp = session.post(EXPORT_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        return False, f"Network error reaching /dp-proxy-export: {type(e).__name__}: {e}"

    if resp.status_code in (401, 403):
        return False, (
            f"Auth failed (HTTP {resp.status_code}). Cookies may have expired - "
            "paste a fresh cURL command."
        )
    if resp.status_code == 404:
        return False, (
            "Endpoint /dp-proxy-export returned 404. TruMedia may have "
            "removed it - data manager refactor likely needed."
        )
    if not resp.ok:
        return False, (
            f"Unexpected HTTP {resp.status_code} from /dp-proxy-export. "
            f"Body preview: {resp.text[:200]!r}"
        )

    body = resp.content[:500]
    if b"<!DOCTYPE html>" in body or b"<html" in body.lower():
        return False, (
            "Endpoint returned HTML (likely a login redirect or generic "
            "error page). Cookies are probably stale - refresh cURL."
        )
    if not (b"," in body and b"\n" in body):
        return False, (
            "Endpoint returned an unexpected format (not CSV). TruMedia may "
            "have changed the response shape - data manager refactor needed. "
            f"Body preview: {body[:200]!r}"
        )

    return True, "Endpoint alive — /dp-proxy-export returned CSV as expected"


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


def _post_export_with_retry(session, payload, *, timeout=120, max_attempts=3):
    """POST to EXPORT_URL with retries on transient upstream failures.

    TruMedia's load balancer occasionally returns 502/503/504 when a heavy
    statement (long season, big team) outruns the backend. A single retry
    after a brief pause clears it without user intervention. Other 4xx
    failures (auth, payload error) are NOT retried - they need a human.

    Raises ValueError with the HTTP status if all retries fail, or
    requests.RequestException on a hard network failure.
    """
    import time
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.post(EXPORT_URL, json=payload, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(2 * attempt)
                continue
            raise
        if resp.status_code in (502, 503, 504) and attempt < max_attempts:
            time.sleep(2 * attempt)
            continue
        return resp
    # Unreachable: loop either returns or raises.
    raise last_exc  # type: ignore[misc]


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

    response = _post_export_with_retry(session, payload)
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


def discover_teams_for_season(session, season_id, config):
    """Download the player pool for one season and diff against config.json.

    Returns a dict:
      {
        "season_id":   <str>,
        "pool_count":  <int>,           # distinct teams in the pool
        "new_teams":   [{name, abbrev, team_id, season_ids: [season_id]}, ...],
        "to_update":   [{team_id, name, current_season_ids, season_id}, ...],
      }

    `new_teams` are team_ids absent from config.json (need a fresh entry).
    `to_update` are team_ids already in config.json under a different
    season - this season_id needs to be appended to their season_ids
    (common case: a Championship team gets promoted to the Premier
    League and we want one entry with both season_ids).

    Caller decides what to do with the dict (UI confirmation, CLI prompt,
    etc.); this function only reads the pool and reports.

    Uses a temp file for the pool CSV and deletes it on the way out, so
    nothing persistent is left behind by a discovery run.
    """
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        download_player_pool(session, [season_id], tmp_path)
        df = pd.read_csv(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    id_col   = next((c for c in ("newestTeamId", "teamId") if c in df.columns), None)
    name_col = next((c for c in ("newestTeam", "teamName") if c in df.columns), None)
    abbr_col = next((c for c in ("teamAbbrevName",) if c in df.columns), None)
    if not id_col or not name_col:
        raise ValueError(
            f"Player pool missing team id/name columns. Got: {list(df.columns)}"
        )

    team_rows = (
        df[[id_col, name_col] + ([abbr_col] if abbr_col else [])]
        .dropna(subset=[id_col, name_col])
        .drop_duplicates(subset=[id_col])
    )

    existing_by_id = {t["team_id"]: t for t in config.get("teams", [])}
    new_teams = []
    to_update = []
    for _, row in team_rows.iterrows():
        tid = str(row[id_col]).strip()
        if not tid:
            continue
        name = str(row[name_col]).strip()
        abbr = str(row[abbr_col]).strip() if abbr_col else name[:4].upper()
        if tid in existing_by_id:
            existing = existing_by_id[tid]
            if season_id not in existing.get("season_ids", []):
                to_update.append({
                    "team_id": tid,
                    "name": existing.get("name", name),
                    "current_season_ids": list(existing.get("season_ids", [])),
                    "season_id": season_id,
                })
        else:
            new_teams.append({
                "name": name,
                "abbrev": abbr,
                "team_id": tid,
                "season_ids": [season_id],
            })

    return {
        "season_id": season_id,
        "pool_count": len(team_rows),
        "new_teams": new_teams,
        "to_update": to_update,
    }


def apply_team_discovery(config, results):
    """Apply discovery results (a list of dicts from `discover_teams_for_season`)
    to a config dict in-place.

    Returns (added, updated) counts. Caller persists the config to disk.
    """
    added = 0
    updated = 0
    existing_by_id = {t["team_id"]: t for t in config.get("teams", [])}
    for res in results:
        for nt in res["new_teams"]:
            if nt["team_id"] in existing_by_id:
                # Edge case: same team_id discovered in two seasons in the
                # same run. Merge into the just-added entry rather than
                # creating a duplicate.
                ex = existing_by_id[nt["team_id"]]
                for sid in nt["season_ids"]:
                    if sid not in ex["season_ids"]:
                        ex["season_ids"].append(sid)
            else:
                config["teams"].append(nt)
                existing_by_id[nt["team_id"]] = nt
                added += 1
        for up in res["to_update"]:
            existing = existing_by_id.get(up["team_id"])
            if not existing:
                continue
            if up["season_id"] not in existing["season_ids"]:
                existing["season_ids"].append(up["season_id"])
                updated += 1
    return added, updated


# ── Event Log ─────────────────────────────────────────────────────────────────

# ── The expanded event model ─────────────────────────────────────────────────
# (select expression, column name, DuckDB type). ONE list, because the SELECT
# and the schema drifting apart is exactly the failure this file already fixed
# once - see the note on _align_to_table.
#
# Chosen from a measured probe of both namespaces, 2026-08-31. The reasoning,
# the traps and the exclusions are in EVENT_MODEL_EXPANSION.md. Two things
# worth repeating here because they are invisible at the call site:
#
#   * NAMED FIELDS (event.foo) return RAW values; STAT TOKENS ([Foo|EVENT])
#     return the catalogue's DISPLAY FORMAT. That is why xG in this database
#     has only 100 distinct values. Prefer the named field where one exists.
#   * event.shooterExpectedGoals is ANCHOR-SCOPED - it populates for the
#     queried team and is NULL for the opponent. Deliberately absent.
EXPANDED_EVENT_FIELDS = [
    # -- shot and chance quality -------------------------------------------
    ("event.expectedGoals",                "xGRaw",              "DOUBLE"),
    ("event.reboundAdjustedExpectedGoals", "xGRebound",          "DOUBLE"),
    ("event.gmY",                          "GoalmouthY",         "DOUBLE"),
    ("event.gmZ",                          "GoalmouthZ",         "DOUBLE"),
    ("[GKx|EVENT]",                        "GKx",                "DOUBLE"),
    ("[GKy|EVENT]",                        "GKy",                "DOUBLE"),
    ("[xGOT|EVENT]",                       "xGOT",               "DOUBLE"),
    ("[BlockX|EVENT]",                     "BlockX",             "INTEGER"),
    ("[BlockY|EVENT]",                     "BlockY",             "INTEGER"),
    ("[PlyrsBtwn|EVENT]",                  "PlayersBetween",     "VARCHAR"),
    ("[Pressure|EVENT]",                   "ShotPressure",       "VARCHAR"),
    ("[Keeper|EVENT]",                     "KeeperName",         "VARCHAR"),
    ("[ShotPatternOfPlay|EVENT]",          "ShotPatternOfPlay",  "VARCHAR"),
    ("[ShotBodyPart|EVENT]",               "ShotBodyPart",       "VARCHAR"),
    # -- pressure, passing, carrying ---------------------------------------
    ("event.remoteEventsPressureReceived", "PressureReceived",   "VARCHAR"),
    ("event.remoteEventsLinesBroken",      "LinesBroken",        "INTEGER"),
    ("event.remoteEventsLastLineBroken",   "LastLineBroken",     "VARCHAR"),
    ("event.carryLength",                  "CarryLength",        "DOUBLE"),
    ("event.carryLengthX",                 "CarryLengthX",       "DOUBLE"),
    ("event.carryStartX",                  "CarryStartX",        "DOUBLE"),
    ("event.carryStartY",                  "CarryStartY",        "DOUBLE"),
    ("event.carryStartType",               "CarryStartType",     "VARCHAR"),
    ("event.cross",                        "IsCross",            "BOOLEAN"),
    ("event.chanceCreated",                "ChanceCreated",      "BOOLEAN"),
    ("event.assist",                       "IsAssist",           "BOOLEAN"),
    ("[CornerType|EVENT]",                 "CornerType",         "VARCHAR"),
    ("[2ndAssisterName|EVENT]",            "SecondAssister",     "VARCHAR"),
    # -- sequence and possession -------------------------------------------
    ("event.possessionValueAdded",         "PossessionValueAdded", "DOUBLE"),
    ("event.sequenceDirectSpeed",          "SequenceDirectSpeed",  "DOUBLE"),
    ("event.sequenceFieldLength",          "SequenceFieldLength",  "DOUBLE"),
    ("event.sequenceReachedPenaltyArea",   "SequenceReachedBox", "BOOLEAN"),
    ("event.possessionShotCount",          "PossessionShotCount", "INTEGER"),
    ("event.sequenceShotCount",            "SequenceShotCount",  "INTEGER"),
    ("event.possessionScoredGoal",         "PossessionScoredGoal", "BOOLEAN"),
    ("event.sequenceScoredGoal",           "SequenceScoredGoal", "BOOLEAN"),
    # -- match context ------------------------------------------------------
    ("[MatchState|EVENT]",                 "MatchState",         "VARCHAR"),
    ("[Starter|EVENT]",                    "Starter",            "VARCHAR"),
    ("[Position|EVENT]",                   "PlayerPosition",     "VARCHAR"),
    ("[Formation|EVENT]",                  "Formation",          "VARCHAR"),
    ("[OppFormation|EVENT]",               "OppFormation",       "VARCHAR"),
    ("[MfromGoal|EVENT]",                  "MetresFromGoal",     "DOUBLE"),
    ("event.secondsUntilNextGoal",         "SecondsUntilNextGoal", "INTEGER"),
    ("[WinProb]",                          "WinProb",            "DOUBLE"),
    ("[DrawProb]",                         "DrawProb",           "DOUBLE"),
    ("[LoseProb]",                         "LoseProb",           "DOUBLE"),
    ("[FieldLocation|EVENT]",              "FieldLocation",      "VARCHAR"),
    ("[FieldWidth|EVENT]",                 "FieldWidth",         "VARCHAR"),
    ("[GoalKick|EVENT]",                   "IsGoalKick",         "VARCHAR"),
    ("[FromCorner|EVENT]",                 "FromCorner",         "VARCHAR"),
    ("[1v1Success|EVENT]",                 "OneVOneSuccess",     "VARCHAR"),
    ("[1v1Next|EVENT]",                    "OneVOneNext",        "VARCHAR"),
    ("[Carry1v1|EVENT]",                   "CarryIs1v1",         "VARCHAR"),
]

_EXPANDED_SELECT = ",".join(f"{expr} AS {name}"
                            for expr, name, _ in EXPANDED_EVENT_FIELDS)


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
    "if(event.q107 OR event.assistq107,'Throw-In',if(event.q6 OR event.assist_q6,'Corner',if(event.q124,'Goal Kick',''))) AS PassType,"
    # Opta qualifier 82 = "Blocked". Set on every shot event whether the
    # block was attributable to a named defender or not. The dedicated
    # `blocker` column misses cases where Opta flagged the shot blocked
    # without identifying the defender; q82 catches both. Required by
    # build_stat_poster_payload's SOT count - if NULL on a shot event
    # (game downloaded before this column landed) the stats payload
    # raises rather than silently undercounting blocks.
    "event.q82 AS qualifierBlocked,"
    # `event.primary` is the event's principal actor - the analogue of
    # event.toucher for events that are not touches. Cards, substitutions and
    # the like leave every existing player-role column NULL, so without this
    # a Dismissal row says only "a red card happened, this minute, this side"
    # and never who. Populated on ordinary touch events too (for a pass it is
    # the passer), so it is safe to select regardless of the WHERE predicate.
    "lookup(event.primary,abbrevName) AS primaryPlayer,"
    "event.primaryPlayerId AS primaryPlayerId,"
    # Opta card qualifiers, from TruMedia's own stat definitions:
    #   q31 yellow   q32 second yellow   q33 red   q171 rescinded
    # playType alone gives Booking vs Dismissal but cannot separate a second
    # yellow from a straight red, and cannot tell that a red was overturned.
    "event.q31 AS qualifierYellow,"
    "event.q32 AS qualifierSecondYellow,"
    "event.q33 AS qualifierRed,"
    "event.q171 AS qualifierCardRescinded,"
    + _EXPANDED_SELECT + ","
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
    'seasonId', 'PassType', 'qualifierBlocked',
    'primaryPlayer', 'primaryPlayerId',
    'qualifierYellow', 'qualifierSecondYellow', 'qualifierRed',
    'qualifierCardRescinded',
] + [_n for _e, _n, _t in EXPANDED_EVENT_FIELDS]
# ^ This list is a THIRD gate the data has to pass, after the SELECT and the
# schema, and it fails SILENTLY: upsert_game_events filters the frame through
# it, then _align_to_table NULL-fills whatever is missing. A widened SELECT
# whose columns are absent here ingests cleanly and stores nothing. Derive
# the expanded half rather than retyping it - the first attempt at this
# expansion wrote 1,581 rows with all 52 new columns NULL and reported
# "INGEST OK".

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
    seasonId VARCHAR,
    PassType VARCHAR,
    qualifierBlocked BOOLEAN,
    primaryPlayer VARCHAR,
    primaryPlayerId VARCHAR,
    qualifierYellow BOOLEAN,
    qualifierSecondYellow BOOLEAN,
    qualifierRed BOOLEAN,
    qualifierCardRescinded BOOLEAN
)
"""


PLAYER_GAME_MINUTES_DDL = """
CREATE TABLE IF NOT EXISTS player_game_minutes (
    playerId VARCHAR,
    gameId VARCHAR,
    playerFullName VARCHAR,
    player VARCHAR,
    teamId VARCHAR,
    teamAbbrevName VARCHAR,
    teamFullName VARCHAR,
    minutes INTEGER,
    yellowCards INTEGER,
    redCards INTEGER,
    date VARCHAR,
    PRIMARY KEY (playerId, gameId)
)
"""


def _ensure_config_table(con):
    """Create the shared-config table. Best effort - never break a data connection."""
    import sys
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    try:
        from shared.config_store import CONFIG_DDL
        con.execute(CONFIG_DDL)
    except Exception:
        pass


# Practice mode. Set this to a file path and every write goes to a local
# DuckDB instead of production MotherDuck.
#
# WHY: git can revert this file, but nothing can revert a bad write to the
# cloud database - and because the events DELETE is scoped to
# (gameId, teamId), a half-finished write leaves a match part-old and
# part-new, which does not look broken. It looks like real data.
#
# So a change to the download path gets developed against a local file,
# checked with smoke_chart_data.py and build_local_fullfeed.py, and only
# pointed at production once it has been seen to work.
#
# DEFAULT IS PRODUCTION, deliberately. Flipping the default would mean
# someone runs the Data Manager expecting to update the real database and
# silently updates a file on their laptop instead - a quieter failure than
# the one this guards against. Practice mode is opt-in.
LOCAL_DB_ENV = "DATA_MANAGER_LOCAL_DB"


def _apply_schema(con):
    """Create/upgrade every table. Identical on local and cloud.

    Shared by both targets on purpose: a practice database that differs from
    production is not a practice database, and any drift here would show up
    as a fake pass or a fake failure in testing.
    """
    con.execute(GAMES_DDL)
    con.execute(EVENTS_DDL)
    con.execute(PLAYER_GAME_MINUTES_DDL)
    _ensure_config_table(con)
    con.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS seasonId VARCHAR")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS seasonId VARCHAR")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS PassType VARCHAR")
    # Opta q82 (Blocked) qualifier: nullable, NULL for rows downloaded
    # before this column landed. build_stat_poster_payload's SOT
    # calculation raises loudly when NULL is encountered on a shot
    # event so we don't ship a quietly-wrong number.
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS qualifierBlocked BOOLEAN")
    # The event's principal actor, and the Opta card qualifiers. Nullable and
    # NULL for every row downloaded before these landed. Needed because cards
    # and substitutions leave toucher/passer/shooter empty, so without
    # primaryPlayer a card event cannot name the player it applies to.
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS primaryPlayer VARCHAR")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS primaryPlayerId VARCHAR")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS qualifierYellow BOOLEAN")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS qualifierSecondYellow BOOLEAN")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS qualifierRed BOOLEAN")
    con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS qualifierCardRescinded BOOLEAN")
    # The expanded event model. Nullable and NULL for every game downloaded
    # before it landed - a backfill is a re-download of those games with the
    # widened SELECT, driven from Campaign's "re-download games already
    # stored" checkbox. See EVENT_MODEL_EXPANSION.md.
    for _, _name, _ddl in EXPANDED_EVENT_FIELDS:
        con.execute(
            f'ALTER TABLE events ADD COLUMN IF NOT EXISTS "{_name}" {_ddl}')
    return con


def _align_to_table(con, df, table):
    """Match a staging frame to a table's real columns. Returns (df, collist).

    Inserts used to be `INSERT INTO events SELECT * FROM staging`, which is
    POSITIONAL: it requires the frame to have every column the table has, in
    the same order. Two ways that bites, and one of them is silent.

    Loud: a CSV missing any column fails the whole team's upload with
    "table events has 63 columns but 56 values were supplied". Only numeric
    columns were backfilled, so a missing VARCHAR or BOOLEAN - exactly what a
    newly-added qualifier is - took the upload down. It works today only
    because EVENT_LOG_SELECT happens to return all 63.

    Silent, and worse: if the column ORDER ever diverges from the frame's,
    positional insert writes each value into its neighbour's column. Same
    types, no error, wrong data.

    Naming the columns fixes both. Anything the table has and the frame lacks
    is filled with NULL, which is what a column added by ALTER means anyway;
    anything the frame has and the table lacks is dropped rather than
    breaking the load.
    """
    table_cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    for col in table_cols:
        if col not in df.columns:
            df[col] = None
    df = df[table_cols]
    return df, ", ".join(f'"{c}"' for c in table_cols)


def get_motherduck_connection(token, local_path=None):
    """Open the write target and ensure its tables exist.

    Production MotherDuck unless a local path is given, either as an argument
    or through the LOCAL_DB_ENV environment variable. See LOCAL_DB_ENV above
    for why practice mode exists and why it is opt-in.

    The token is ignored in practice mode - a local file needs no credential,
    and requiring one would mean a practice run could still fail for a reason
    that has nothing to do with what is being tested.
    """
    local_path = local_path or os.environ.get(LOCAL_DB_ENV)
    if local_path:
        print(f"  [practice mode] writing to local file: {local_path}")
        return _apply_schema(duckdb.connect(local_path))

    # Connect to default database first to create our database if needed
    bootstrap = duckdb.connect(f"md:?motherduck_token={token}")
    bootstrap.execute(f"CREATE DATABASE IF NOT EXISTS {MOTHERDUCK_DB}")
    bootstrap.close()

    return _apply_schema(
        duckdb.connect(f"md:{MOTHERDUCK_DB}?motherduck_token={token}"))


def get_all_team_last_dates(con):
    """Return dict of teamId -> most recent event Date for all teams in MotherDuck.

    Season-blind. Kept for callers that genuinely want one cutoff per team;
    prefer `get_team_season_last_dates` for incremental downloads, which need
    a cutoff per competition (see that function's docstring).
    """
    rows = con.execute(
        "SELECT teamId, MAX(Date) FROM events GROUP BY teamId"
    ).fetchall()
    return {team_id: last_date for team_id, last_date in rows if last_date}


def get_team_season_last_dates(con):
    """Return dict of (teamId, seasonId) -> most recent event Date.

    A single cutoff per team is wrong once a team plays in more than one
    competition, which is nearly all of them. Arsenal's newest event might be
    a Champions League tie in late May while their league season ended a week
    earlier; a team-wide cutoff taken from the later date silently skips the
    gap. Worse, a team with a complete old season and an empty new one gets a
    cutoff from the old season's end, so a hole anywhere earlier can never be
    backfilled incrementally.

    Per (team, season) each competition is fetched from its own last game, and
    a season with no rows yet returns nothing - so the caller passes
    since_date=None and pulls the season whole.

    Safe because events.seasonId is fully populated (verified Aug 2026: zero
    nulls across 5.3M rows).
    """
    rows = con.execute(
        "SELECT teamId, seasonId, MAX(Date) FROM events GROUP BY teamId, seasonId"
    ).fetchall()
    return {(team_id, season_id): last_date
            for team_id, season_id, last_date in rows if last_date}


# ── Per-GAME ingest ──────────────────────────────────────────────────────────
#
# The per-team path below is the one in production. This is its replacement:
# fixtures are discovered from the season, then each GAME is fetched once and
# written atomically. See MIGRATION_PLAN.md.
#
# Why the grain change matters more than it sounds: the per-team DELETE is
# scoped to (gameId, teamId), so refreshing one team leaves a match half-old
# and half-new, which looks like real data rather than missing data. Measured
# 2026-08-29: 1,116 of 4,930 production games (22.6%) hold only ONE side's
# events, and nothing in the tool can currently see that.

FIXTURE_SELECT = (
    "SELECT "
    "game.gameId AS gameId,"
    "game.optaMatchId AS optaMatchId,"
    "game.gameDate AS gameDate,"
    "game.status AS status,"
    "game.week AS week,"
    "game.stage AS stage,"
    "game.venueName AS venueName,"
    "game.attendance AS attendance,"
    "game.neutralSite AS neutralSite,"
    "game.gameMainMatchOfficialName AS referee,"
    "game.p1Start AS p1Start, game.p1End AS p1End,"
    "game.p2Start AS p2Start, game.p2End AS p2End,"
    "game.matchLength AS matchLength,"
    "game.playTimeAnnouncedInjuryTime AS announcedInjuryTime,"
    # game.home / game.away are BOOLEAN side flags for team.game.teamId, NOT
    # team names. Reading them as names printed "True v False" as a fixture
    # in an earlier probe.
    "game.home AS isHome,"
    "team.game.teamId AS teamId,"
    "team.game.fullName AS teamFullName,"
    "team.game.teamColor AS teamColor,"
    "opponent.game.teamId AS opponentId,"
    "opponent.game.fullName AS opponentFullName"
)


def build_fixture_statement(season_ids):
    """Every fixture in a season, with both teamIds. No team predicate.

    `FROM season BY game` and `FROM game BY game` both return HTTP 400 - the
    `FROM team BY <grain>` shape is required even when naming no team.

    Returns TWO rows per game, one per side, distinguished by `isHome`. Either
    row carries both teamIds, so a single row is enough to describe a fixture;
    the pair is deduped in `discover_fixtures`.
    """
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    return (f"{FIXTURE_SELECT} FROM team BY game "
            f"WHERE ((season.seasonId IN ({season_id_str}))) "
            f"LIMIT 100000")


def discover_fixtures(session, season_ids):
    """One request per call -> a DataFrame of fixtures, one row per game.

    Columns: gameId, gameDate, status, homeTeamId, awayTeamId, homeTeam,
    awayTeam, plus the match metadata the per-team feed never captured
    (referee, venue, attendance, period boundaries, injury time, stage, week).

    This replaces config.json's 455-team list as the thing that drives a
    download: adding a league stops needing team enumeration.
    """
    payload = {
        "format": "MIXED",
        "statement": build_fixture_statement(season_ids),
        "export": "csv",
        "pageDescriptorName": "pageSoccerTeamEventLogOverall",
        "exportOptions": {"includeCalculations": False,
                          "includeVideoData": False},
    }
    resp = _post_export_with_retry(session, payload)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code} {resp.reason}: "
                         f"{resp.text[:500]}")
    if b'<!DOCTYPE html>' in resp.content[:500] or b'<html' in resp.content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired - paste a fresh cURL command.")
    df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8")
    if df.empty:
        return df

    # Keep the HOME row of each pair: its teamId is the home side and its
    # opponentId the away side, so the fixture is fully described without
    # having to reconcile the two rows.
    home = df[df["isHome"].fillna(False).astype(bool)].copy()
    if home.empty:                       # neutral-site or malformed feed
        home = df.drop_duplicates("gameId").copy()
        home["homeTeamId"] = home["teamId"]
        home["awayTeamId"] = home["opponentId"]
        home["homeTeam"] = home["teamFullName"]
        home["awayTeam"] = home["opponentFullName"]
    else:
        home["homeTeamId"] = home["teamId"]
        home["awayTeamId"] = home["opponentId"]
        home["homeTeam"] = home["teamFullName"]
        home["awayTeam"] = home["opponentFullName"]
    home = home.drop_duplicates("gameId")
    return home.drop(columns=[c for c in ("isHome", "teamId", "opponentId",
                                          "teamFullName", "opponentFullName")
                             if c in home.columns])


def build_game_event_statement(anchor_team_id, season_ids, game_ids):
    """Every event in the given games, BOTH sides, from one request.

    Two differences from `build_event_log_statement`:

    1. **No `event.toucher` predicate.** That filter is why cards,
       substitutions, corners, ball recoveries and ~20 other types never
       arrive - 22 play types instead of 47.
    2. **`is_team` / `is_opp` as RAW booleans.** They say which side each
       event belongs to, so one request covers the whole match instead of one
       per team.

    THE TRAP: `lookup(team.event.primary, abbrevName)` and
    `lookup(opponent.event.primary, abbrevName)` return IDENTICAL values on
    every row - lookup() resolves "the actor of this event" and discards the
    namespace. The namespaces discriminate ONLY as raw booleans. Select
    predicate fields raw to test membership; use lookup() only to resolve an
    actor to a name.
    """
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    gids_str = ",".join(f"'{g}'" for g in game_ids)
    return (
        f"{EVENT_LOG_SELECT},"
        f"team.event.primary AS is_team,"
        f"opponent.event.primary AS is_opp "
        f"FROM team BY event "
        f"WHERE ((team.teamId ='{anchor_team_id}')) "
        f"AND ((season.seasonId IN ({season_id_str}))) "
        f"AND (game.gameId IN ({gids_str})) "
        f"ORDER BY event.gameEventIndex ASC "
        f"LIMIT 200000"
    )


# Work-list states, in the order a campaign should attack them.
WORK_MISSING = "missing"        # no events at all
WORK_ONE_SIDED = "one_sided"    # half a match - the 22.6% problem
WORK_OLD_FEED = "old_feed"      # both sides, but ingested under event.toucher
WORK_NOT_PLAYED = "not_played"  # fixture exists, no result yet
WORK_COMPLETE = "complete"      # both sides, on the current feed
WORK_ORDER = [WORK_MISSING, WORK_ONE_SIDED, WORK_OLD_FEED,
              WORK_NOT_PLAYED, WORK_COMPLETE]

# The 22 play types `event.toucher` can return. A game holding ONLY these was
# ingested under the old predicate.
#
# WHY THIS IS A SEPARATE STATE. "Both sides present" was the original test for
# complete, and it is true of every game already in production - they were
# downloaded a team at a time, but they were downloaded. It says nothing about
# WHICH FEED they came from. Calling them complete meant a campaign would skip
# them and leave them on 22 play types with no cards and no substitutions,
# forever, while reporting the migration as finished.
#
# Caught before any production run: WSL showed 38 "complete" against 94
# one-sided, and the 38 were old-feed games.
OLD_FEED_PLAY_TYPES = (
    'Pass', 'BallTouch', 'Clearance', 'TakeOn', 'Tackle', 'FreeKick',
    'Dispossessed', 'Interception', 'BlockedPass', 'AttemptSaved', 'Save',
    'Miss', 'OffsidePass', 'Goal', 'Claim', 'DropOfBall', 'Punch', 'Post',
    'PenaltyGoal', 'Smother', 'GoodSkill', 'OwnGoal',
)

# Statuses worth attempting a download for. Checked against every season in
# config on 2026-08-29; the full set seen was Played, Awarded, Fixture,
# Playing.
#
# AWARDED IS INCLUDED, and that is not obvious. It covers three different
# things and only the events can tell them apart - measured on all five
# awarded fixtures in the database:
#
#   PSG v Le Havre W        0 events      never played
#   PSG v Fleury W          0 events      never played
#   Strasbourg W v PSG      0 events      never played
#   Lens W v PSG        1,973 events      PLAYED IN FULL, then awarded
#   Nantes v Toulouse     567 events      ABANDONED after 21 minutes
#
# Excluding the status would silently drop a complete 94-minute match and a
# real abandoned one. The cost of including it is that the three genuinely
# unplayed games stay on the work list and get retried each run - three games
# out of ~4,930, and visible rather than silent, which is the right way round.
#
# PLAYING IS EXCLUDED deliberately. A match in progress has partial events;
# ingesting it would store half a game that then reads as COMPLETE - both
# sides present - and never be refreshed. That is the exact failure this
# rework exists to remove, arriving through the front door.
INGESTABLE_STATUSES = {"played", "awarded"}


def build_work_list(con, fixtures):
    """Classify every fixture against what the database actually holds.

    This is the thing the tool has never had. Today's equivalent is
    "incremental since this team's last game date", which CANNOT SEE a
    one-sided match: both teams were fetched, just never together, so both
    look up to date while the match is half empty. That is how 1,116 of
    4,930 games ended up holding one side with nothing flagging it.

    Returns `fixtures` plus:
        sides_present  0, 1 or 2
        events_stored  row count
        state          one of WORK_*

    `stale` is deliberately NOT computed here. It needs the structural hash
    pull (id + timecode per event, ~7.6% of a full download) and that is a
    network round trip, not a database question - see MIGRATION_PLAN.md. A
    work list that quietly reported "complete" for a game whose source had
    changed would be worse than one that admits it only checks presence.
    """
    if fixtures.empty:
        return fixtures.assign(sides_present=0, events_stored=0,
                               new_feed=False, state=WORK_MISSING)
    gids = list(fixtures["gameId"])
    ph = ",".join("?" * len(gids))
    old_types = ",".join(f"'{t}'" for t in OLD_FEED_PLAY_TYPES)
    have = con.execute(
        f"SELECT gameId, count(DISTINCT teamId) AS sides, count(*) AS n, "
        f"       max(CASE WHEN playType NOT IN ({old_types}) THEN 1 ELSE 0 END)"
        f"       AS new_feed "
        f"FROM events WHERE gameId IN ({ph}) GROUP BY gameId", gids
    ).fetchall()
    sides = {g: s for g, s, _, _ in have}
    counts = {g: n for g, _, n, _ in have}
    newfeed = {g: bool(f) for g, _, _, f in have}

    out = fixtures.copy()
    out["sides_present"] = out["gameId"].map(sides).fillna(0).astype(int)
    out["events_stored"] = out["gameId"].map(counts).fillna(0).astype(int)
    out["new_feed"] = out["gameId"].map(newfeed).fillna(False).astype(bool)

    played = (out["status"].astype(str).str.lower().isin(INGESTABLE_STATUSES)
              if "status" in out.columns else True)

    def _state(row, is_played):
        if row["sides_present"] >= 2:
            # Both sides, but which feed? A game holding only the old 22 play
            # types still needs re-downloading - it has no cards and no
            # substitutions, whatever its row count says.
            return WORK_COMPLETE if row["new_feed"] else WORK_OLD_FEED
        if not is_played:
            return WORK_NOT_PLAYED
        return WORK_ONE_SIDED if row["sides_present"] == 1 else WORK_MISSING

    out["state"] = [
        _state(r, p) for (_, r), p in zip(
            out.iterrows(),
            played if hasattr(played, "__iter__") else [True] * len(out))
    ]
    return out


def work_list_summary(work):
    """Counts per state, in attack order. For the review step before running."""
    if work.empty:
        return {s: 0 for s in WORK_ORDER}
    counts = work["state"].value_counts().to_dict()
    return {s: int(counts.get(s, 0)) for s in WORK_ORDER}


# Batches are grouped by HOME TEAM, and that is not an arbitrary choice.
#
# THE TRAP: the event query anchors on a team, and the anchor FILTERS. A game
# returns events only if the anchor plays in it - an unrelated anchor returns
# zero rows, and a mixed batch silently drops every game the anchor is not in.
# Measured: batching 20 arbitrary games lost Manchester United v Arsenal
# because its batch was anchored on Crystal Palace. Nothing errored. The row
# count looked plausible. Only counting DISTINCT GAMES caught it.
#
# Every game has exactly one home team, so grouping by homeTeamId covers each
# game precisely once with an anchor guaranteed to be in it.
#
# The cost works out the same as the path being replaced: a 380-game season is
# 20 teams x ~19 home games = 20 event requests + 20 minutes requests, against
# the old 20 teams x 2. Batching one game per request would have been 760.
MAX_GAMES_PER_REQUEST = 40


def plan_batches(todo, batch_size=MAX_GAMES_PER_REQUEST):
    """Group games into requestable batches. Returns [(anchor_team_id, rows)].

    Grouped by HOME TEAM: every game has exactly one, so each game is covered
    once by an anchor guaranteed to play in it. See MAX_GAMES_PER_REQUEST for
    what goes wrong otherwise.

    Shared with the UI on purpose. A page that estimates the cost with its own
    arithmetic drifts from what the runner actually does the moment either
    changes - the Campaign page told users "one request each" for a while
    after batching landed, which was off by a factor of nineteen.
    """
    by_anchor = {}
    for idx_row in todo.iterrows():
        by_anchor.setdefault(idx_row[1]["homeTeamId"], []).append(idx_row)
    batches = []
    for anchor_id, rows in by_anchor.items():
        for i in range(0, len(rows), max(1, batch_size)):
            batches.append((anchor_id, rows[i:i + max(1, batch_size)]))
    return batches


def estimate_requests(todo, batch_size=MAX_GAMES_PER_REQUEST,
                      with_minutes=True, n_seasons=0):
    """(batches, total_requests) for a planned campaign."""
    n = len(plan_batches(todo, batch_size))
    return n, n * (2 if with_minutes else 1) + n_seasons


def run_campaign(session, token, fixtures, work, output_dir, season_ids,
                 states=(WORK_MISSING, WORK_ONE_SIDED, WORK_OLD_FEED),
                 con=None,
                 progress=None, stop=None, batch_size=MAX_GAMES_PER_REQUEST,
                 with_minutes=True):
    """Download and write every game in `work` whose state is in `states`.

    Fetches events AND minutes, both at game grain. Minutes are not optional
    in practice: refreshing events alone leaves `player_game_minutes` stale,
    so every per-90 axis divides a new numerator by an old denominator without
    anything appearing wrong.

    Resumable BY CONSTRUCTION: progress is the database, not a side file. Each
    batch is written in a transaction, so re-running recomputes the work list
    and the finished games simply drop out of it. Nothing to corrupt, nothing
    to reconcile - which is what the queued pause/resume tracker was for.

    `progress(done, total, label, state, note)` is called after each batch.

    `stop()` is polled between BATCHES, not between games - so an interrupt
    can take up to `batch_size` games to take effect. That is the price of
    batching and it is a small one: a batch is bounded by the games one club
    hosts in the scoped seasons, and finishes in seconds. What it is NOT is a
    correctness problem - the interrupt still lands between whole matches, and
    the batch that was in flight was written in a transaction.

    Pass `batch_size=1` for a responsive stop at the cost of one request per
    game.

    Returns (written, failed, skipped) counted in GAMES.
    """
    fixture_map = {r["gameId"]: {"homeTeamId": r["homeTeamId"],
                                 "awayTeamId": r["awayTeamId"],
                                 "homeTeam": r["homeTeam"],
                                 "awayTeam": r["awayTeam"]}
                   for _, r in fixtures.iterrows()}
    todo = work[work["state"].isin(states)]
    total = len(todo)
    written = failed = skipped = 0
    batches = plan_batches(todo, batch_size)

    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)
    try:
        done = 0
        for bi, (anchor, batch) in enumerate(batches, 1):
            if stop is not None and stop():
                skipped = total - done
                break
            gids = [r["gameId"] for _, r in batch]
            note = ""
            try:
                ev_path = os.path.join(output_dir, f"batch_{bi}_events.csv")
                download_game_events(session, anchor, season_ids, gids, ev_path)
                _, n = upsert_game_events(token, ev_path, fixture_map, con=con)
                note = f"{n:,} events"
                if with_minutes:
                    mn_path = os.path.join(output_dir, f"batch_{bi}_min.csv")
                    download_game_minutes(session, season_ids, gids, mn_path)
                    m = upsert_game_minutes(token, mn_path, con=con)
                    note += f", {m:,} minute rows"
                    _quiet_remove(mn_path)
                _quiet_remove(ev_path)
                written += len(batch)
            except Exception as e:
                failed += len(batch)
                note = f"{type(e).__name__}: {e}"[:160]
            done += len(batch)
            if progress:
                progress(done, total, f"batch {bi}/{len(batches)}",
                         batch[0][1].get("state"), note)
    finally:
        if own_con:
            con.close()
    return written, failed, skipped


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def download_game_events(session, anchor_team_id, season_ids, game_ids,
                         output_path):
    """Fetch both sides' events for the given games. Returns (rows, size_kb)."""
    payload = {
        "format": "MIXED",
        "statement": build_game_event_statement(anchor_team_id, season_ids,
                                                game_ids),
        "export": "csv",
        "pageDescriptorName": "pageSoccerTeamEventLogOverall",
        "exportOptions": {"includeCalculations": False,
                          "includeVideoData": False},
    }
    resp = _post_export_with_retry(session, payload)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code} {resp.reason}: "
                         f"{resp.text[:500]}")
    content = resp.content
    if b'<!DOCTYPE html>' in content[:500] or b'<html' in content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired - paste a fresh cURL command.")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(content)
    return max(0, content.count(b'\n') - 1), len(content) / 1024


def upsert_game_events(token, csv_path, fixtures, con=None):
    """Write one or more complete games. DELETE is scoped to gameId.

    `fixtures` maps gameId -> {homeTeamId, awayTeamId, homeTeam, awayTeam}
    from `discover_fixtures`, and is what turns the is_team/is_opp booleans
    into a real teamId per event.

    TWO THINGS THIS FIXES, both consequences of the per-team grain:

    1. **Provenance.** `DELETE ... WHERE gameId = ?` means a match is ingested
       or it isn't. The per-team DELETE at (gameId, teamId) leaves a match
       half-old and half-new, which is indistinguishable from real data.
    2. **Attribution.** `events.teamId` currently comes from
       `newest(team.game.teamId)` - the ANCHOR team - which is only correct
       because each request contained one team's events. With both sides in
       one response it has to be derived per row.

    Rows flagged neither is_team nor is_opp are `Sequence` / `Possession`
    aggregate rows (393 of 1,951 in the reference game). They have no owning
    team and are dropped, which matches what `events` holds today.

    Returns (games_written, rows_written).
    """
    df = pd.read_csv(csv_path, encoding='utf-8')
    if df.empty:
        return 0, 0

    for col in _INT_COLS:
        if col not in df.columns:
            df[col] = 0
    for col in _NULLABLE_INT_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    for col in _FLOAT_COLS:
        if col not in df.columns:
            df[col] = float('nan')
    for col in _INT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
    for col in _NULLABLE_INT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
    for col in _FLOAT_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    is_team = df.get("is_team", pd.Series(False, index=df.index)) \
                .fillna(False).astype(bool)
    is_opp = df.get("is_opp", pd.Series(False, index=df.index)) \
               .fillna(False).astype(bool)
    anchor = df["teamId"] if "teamId" in df.columns else None

    # Resolve each event to its own side. The anchor team is whichever of the
    # fixture's two sides the request was made against; is_opp rows belong to
    # the other one.
    def _side(row_gid, anchor_id, team_flag):
        fx = fixtures.get(row_gid)
        if not fx:
            return None
        home, away = fx["homeTeamId"], fx["awayTeamId"]
        other = away if anchor_id == home else home
        return anchor_id if team_flag else other

    df = df[is_team | is_opp].copy()
    if df.empty:
        return 0, 0
    flags = is_team[is_team | is_opp]
    df["teamId"] = [
        _side(g, a, f) for g, a, f in zip(df["gameId"], anchor[df.index], flags)
    ]
    df = df[df["teamId"].notna()].copy()

    # teamFullName has to follow teamId, not the anchor, for the same reason.
    name_by_id = {}
    for fx in fixtures.values():
        name_by_id[fx["homeTeamId"]] = fx["homeTeam"]
        name_by_id[fx["awayTeamId"]] = fx["awayTeam"]
    df["teamFullName"] = df["teamId"].map(name_by_id).fillna(
        df.get("teamFullName"))

    games_df = _games_frame_from_fixtures(df, fixtures)
    events_df = df[[c for c in EVENTS_MD_COLS if c in df.columns]].copy()
    if 'eventGuid' in events_df.columns:
        before = len(events_df)
        events_df = events_df.drop_duplicates(subset=['eventGuid'],
                                              keep='first')
        if before - len(events_df):
            print(f"  [warning] dropped {before - len(events_df)} duplicate "
                  f"eventGuid row(s)")

    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)
    try:
        games_df, gcols = _align_to_table(con, games_df, "games")
        con.register("_g_stage", games_df)
        con.execute(f"INSERT OR REPLACE INTO games ({gcols}) "
                    f"SELECT {gcols} FROM _g_stage")
        con.unregister("_g_stage")

        events_df, ecols = _align_to_table(con, events_df, "events")
        con.register("_e_stage", events_df)
        # The whole match, both sides. This is the point of the rework.
        #
        # In a transaction, because DELETE-then-INSERT is two statements and
        # DuckDB autocommits each one: a failure between them - a dropped
        # connection, an expired session mid-batch - would leave the games
        # deleted and not replaced. That is worse than the half-match state
        # this rework exists to remove.
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM events WHERE gameId IN "
                        "(SELECT DISTINCT gameId FROM _e_stage)")
            con.execute(f"INSERT INTO events ({ecols}) "
                        f"SELECT {ecols} FROM _e_stage")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.unregister("_e_stage")
    finally:
        if own_con:
            con.close()
    return events_df["gameId"].nunique(), len(events_df)


def build_game_minutes_statement(season_ids, game_ids):
    """Minutes and cards for whole games, BOTH sides, no team predicate.

    Verified 2026-08-29 against the per-team pull for the same fixture:
    identical players, identical minutes (max diff 0), 1,980 total = two full
    sides. So this replaces the per-team-season minutes call outright rather
    than approximating it.

    It matters that minutes move to the same grain as events. Left per-team,
    a per-game re-download would refresh `events` and leave
    `player_game_minutes` untouched - so every per-90 axis, the minimum-minutes
    filter and `compute_per90_min_minutes` would be dividing new numerators by
    stale denominators, silently.
    """
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    gids_str = ",".join(f"'{g}'" for g in game_ids)
    return (
        f"{MINUTES_SELECT} "
        f"FROM player 'p' BY game "
        f"WHERE ((season.seasonId IN ({season_id_str}))) "
        f"AND (game.gameId IN ({gids_str})) "
        f"QUALIFY BY [GM] > 0 "
        f"LIMIT 100000 "
        f"CALCULATE total"
    )


def download_game_minutes(session, season_ids, game_ids, output_path):
    """Fetch minutes for whole games. Returns (rows, size_kb)."""
    payload = {
        "format": "MIXED",
        "statement": build_game_minutes_statement(season_ids, game_ids),
        "export": "csv",
        "pageDescriptorName": "pageSoccerPlayersInPossession",
        "exportOptions": {"includeCalculations": False,
                          "includeVideoData": False},
    }
    resp = _post_export_with_retry(session, payload)
    if not resp.ok:
        raise ValueError(f"HTTP {resp.status_code} {resp.reason}: "
                         f"{resp.text[:500]}")
    content = resp.content
    if b'<!DOCTYPE html>' in content[:500] or b'<html' in content[:500]:
        raise ValueError(
            "Received an HTML page instead of CSV data. "
            "Your session has likely expired - paste a fresh cURL command.")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(content)
    return max(0, content.count(b'\n') - 1), len(content) / 1024


def upsert_game_minutes(token, csv_path, con=None):
    """Replace minutes for whole games. DELETE at gameId, like events.

    INSERT OR REPLACE alone would update rows that still exist and leave
    behind any player who no longer appears - a substitute removed from a
    corrected team sheet would keep his minutes forever, and per-90 rates
    would divide by a squad that never played.
    """
    df = pd.read_csv(csv_path, encoding='utf-8')
    if df.empty:
        return 0
    df = df.rename(columns={'Min': 'minutes', 'Yellow': 'yellowCards',
                            'RedCardsTotal': 'redCards'})
    for col in ['playerId', 'gameId', 'playerFullName', 'player', 'teamId',
                'teamAbbrevName', 'teamFullName', 'date']:
        if col not in df.columns:
            df[col] = None
    for col in ['minutes', 'yellowCards', 'redCards']:
        df[col] = (pd.to_numeric(df.get(col), errors='coerce')
                   .fillna(0).astype(int))
    df = df[df["gameId"].notna()]
    if df.empty:
        return 0

    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)
    try:
        df, cols = _align_to_table(con, df, "player_game_minutes")
        con.register("_m_stage", df)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM player_game_minutes WHERE gameId IN "
                        "(SELECT DISTINCT gameId FROM _m_stage)")
            con.execute(f"INSERT INTO player_game_minutes ({cols}) "
                        f"SELECT {cols} FROM _m_stage")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.unregister("_m_stage")
    finally:
        if own_con:
            con.close()
    return len(df)


def _games_frame_from_fixtures(df, fixtures):
    """A `games` row per game, taken from the FIXTURE, not from the events.

    The per-team path derives homeTeamId/awayTeamId by comparing
    `teamFullName` to `homeTeam` - two names written by different fetches, so
    a club TruMedia has renamed resolves to the wrong side. The fixture query
    hands both ids over directly, so the comparison disappears.
    """
    rows = []
    for gid in df["gameId"].unique():
        fx = fixtures.get(gid)
        if not fx:
            continue
        sub = df[df["gameId"] == gid]
        row = {
            "gameId": gid,
            "homeTeamId": fx["homeTeamId"], "awayTeamId": fx["awayTeamId"],
            "homeTeam": fx["homeTeam"], "awayTeam": fx["awayTeam"],
        }
        for src, dst in (("optaMatchId", "optaMatchId"), ("Date", "Date"),
                         ("seasonId", "seasonId"),
                         ("homeFinalScore", "homeFinalScore"),
                         ("awayFinalScore", "awayFinalScore")):
            if src in sub.columns:
                vals = sub[src].dropna()
                if len(vals):
                    row[dst] = vals.iloc[0]
        rows.append(row)
    return pd.DataFrame(rows)


def build_event_log_statement(team_id, season_ids, since_date=None,
                              until_date=None, game_ids=None):
    """Build the SQL statement for a team event log download.

    Filtering options (mutually compatible except where noted):
        since_date:  YYYY-MM-DD or date — events from games on/after this date
        until_date:  YYYY-MM-DD or date — events from games on/before this date
        game_ids:    iterable of gameId strings — restrict to these specific games.
                     Overrides since_date / until_date when provided.
    """
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    filters = []
    if game_ids:
        gids_str = ",".join(f"'{g}'" for g in game_ids)
        filters.append(f"AND (game.gameId IN ({gids_str}))")
    else:
        if since_date:
            filters.append(f"AND (game.gameDate >= '{since_date}')")
        if until_date:
            filters.append(f"AND (game.gameDate <= '{until_date}')")

    return (
        f"{EVENT_LOG_SELECT} "
        f"FROM team BY event "
        f"WHERE ((team.teamId ='{team_id}') AND ((event.toucher))) "
        f"AND ((season.seasonId IN ({season_id_str}))) "
        f"{' '.join(filters)} "
        f"ORDER BY event.gameEventIndex ASC "
        f"LIMIT 100000"
    )


def download_event_log(session, team_id, season_ids, output_path,
                       since_date=None, until_date=None, game_ids=None):
    """Download a team event log CSV and save to output_path.

    Returns (row_count, size_kb) on success.
    Raises on auth failure, network error, or unexpected response.

    See build_event_log_statement for filter semantics.
    """
    statement = build_event_log_statement(
        team_id, season_ids,
        since_date=since_date,
        until_date=until_date,
        game_ids=game_ids,
    )
    payload = {
        "format": "MIXED",
        "statement": statement,
        "export": "csv",
        "pageDescriptorName": "pageSoccerTeamEventLogInPossession",
        "exportOptions": {"includeCalculations": False, "includeVideoData": False},
    }

    response = _post_export_with_retry(session, payload)
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

    # TruMedia occasionally exports the same event twice in a single CSV.
    # Without dedup, the staging->INSERT step fails on the eventGuid PK
    # constraint and rolls back the whole team's upload. Drop any duplicate
    # eventGuids here, keep the first occurrence, and log how many were
    # dropped so we can spot data-quality regressions in the source.
    if 'eventGuid' in events_df.columns:
        before = len(events_df)
        events_df = events_df.drop_duplicates(subset=['eventGuid'], keep='first')
        dropped = before - len(events_df)
        if dropped:
            print(f"  [warning] dropped {dropped} duplicate eventGuid row(s) "
                  f"from staging (TruMedia CSV had repeats)")

    # ── Upsert ────────────────────────────────────────────────────────────────
    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)

    try:
        games_df, games_cols = _align_to_table(con, games_df, "games")
        con.register("_games_staging", games_df)
        con.execute(f"INSERT OR REPLACE INTO games ({games_cols}) "
                    f"SELECT {games_cols} FROM _games_staging")
        con.unregister("_games_staging")

        # Wipe THIS TEAM'S contribution for these games before inserting fresh.
        # Each team's TruMedia event-log export contains only that team's own
        # events for that team's matches -- the opposing team contributes its
        # events via a separate download. A re-fetch can re-number
        # gameEventIndex (the tail half of eventGuid), so INSERT OR REPLACE
        # alone can't dedupe: new rows land under different PKs and layer on
        # top of the old. We scope the DELETE to (gameId, teamId) so a team's
        # re-upload only replaces its own contribution, preserving whatever
        # rows the opposing team's download contributed.
        events_df, events_cols = _align_to_table(con, events_df, "events")
        con.register("_events_staging", events_df)
        con.execute("""
            DELETE FROM events
            WHERE (gameId, teamId) IN (
                SELECT DISTINCT gameId, teamId FROM _events_staging
            )
        """)
        con.execute(f"INSERT INTO events ({events_cols}) "
                    f"SELECT {events_cols} FROM _events_staging")
        con.unregister("_events_staging")
    finally:
        if own_con:
            con.close()

    return len(df)


# ── Minutes & Cards ───────────────────────────────────────────────────────────

MINUTES_SELECT = (
    "SELECT playerId, fullName as playerFullName, abbrevName as player, "
    "game.gameId, "
    "team.game.teamId as teamId, "
    "team.game.abbrevName AS teamAbbrevName, "
    "team.game.fullName AS teamFullName, "
    "[Min], [Yellow], [RedCardsTotal], "
    "format('date','yyyy-MM-dd',game.gameDate) as date"
)

_MINUTES_MD_COLS = [
    'playerId', 'gameId', 'playerFullName', 'player',
    'teamId', 'teamAbbrevName', 'teamFullName',
    'minutes', 'yellowCards', 'redCards', 'date',
]


def build_minutes_statement(team_id, season_ids):
    """Build the SQL statement for a team Minutes & Cards download."""
    season_id_str = ",".join(f"'{s}'" for s in season_ids)
    return (
        f"{MINUTES_SELECT} "
        f"FROM player 'p' BY game "
        f"WHERE (team.game.teamId='{team_id}') "
        f"AND ((season.seasonId IN ({season_id_str}))) "
        f"QUALIFY BY [GM] > 0 "
        f"ORDER BY 'date' DESC "
        f"LIMIT 100000 "
        f"CALCULATE total"
    )


def download_minutes_and_cards(session, team_id, season_ids, output_path):
    """Download a team Minutes & Cards CSV and save to output_path.

    Returns (row_count, size_kb) on success.
    Raises on auth failure, network error, or unexpected response.
    """
    statement = build_minutes_statement(team_id, season_ids)
    payload = {
        "format": "MIXED",
        "statement": statement,
        "export": "csv",
        "pageDescriptorName": "pageSoccerTeamSquadInPossession",
        "exportOptions": {"includeCalculations": False, "includeVideoData": False},
    }

    response = _post_export_with_retry(session, payload)
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


def upsert_minutes_to_motherduck(token, csv_path, con=None):
    """Parse a team Minutes & Cards CSV and upsert into player_game_minutes.

    If con is provided it is reused (caller manages lifecycle).
    Returns row_count on success.
    """
    df = pd.read_csv(csv_path, encoding='utf-8')

    df = df.rename(columns={
        'Min': 'minutes',
        'Yellow': 'yellowCards',
        'RedCardsTotal': 'redCards',
    })

    for col in ['playerId', 'gameId', 'playerFullName', 'player',
                'teamId', 'teamAbbrevName', 'teamFullName', 'date']:
        if col not in df.columns:
            df[col] = None

    for col in ['minutes', 'yellowCards', 'redCards']:
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    df = df[[c for c in _MINUTES_MD_COLS if c in df.columns]]

    own_con = con is None
    if own_con:
        con = get_motherduck_connection(token)

    try:
        df, cols = _align_to_table(con, df, "player_game_minutes")
        con.register("_minutes_staging", df)
        con.execute(f"INSERT OR REPLACE INTO player_game_minutes ({cols}) "
                    f"SELECT {cols} FROM _minutes_staging")
        con.unregister("_minutes_staging")
    finally:
        if own_con:
            con.close()

    return len(df)
