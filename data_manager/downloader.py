"""
TruMedia Downloader
Handles authentication via cURL parsing and data downloads via POST requests.
"""
import io
import re
import gzip
import hashlib
import os
import shutil
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
    # -- Opta qualifier flags ------------------------------------------------
    # Added 2026-09-08. Probed over 20 games: 309 of q1..q400 populate; these
    # are the 301 we did not already request. Stored raw as qNNN - the
    # friendly-name map lives in the read layer, so identifying one later is a
    # code change rather than a schema migration. Percentages are fire rates.
    # Sparse booleans cost ~0.023 bytes/col/row; do NOT price them with the
    # mixed-type average, which is ~40x too high for these.
    ("event.q1", "q1", "BOOLEAN"),                  # 5.90%
    ("event.q2", "q2", "BOOLEAN"),                  # 1.67%
    ("event.q3", "q3", "BOOLEAN"),                  # 2.19%
    ("event.q4", "q4", "BOOLEAN"),                  # 0.15%
    ("event.q5", "q5", "BOOLEAN"),                  # 1.28%
    ("event.q8", "q8", "BOOLEAN"),                  # 0.00%
    ("event.q9", "q9", "BOOLEAN"),                  # 0.12%
    ("event.q10", "q10", "BOOLEAN"),                # 0.10%
    ("event.q11", "q11", "BOOLEAN"),                # 0.00%
    ("event.q12", "q12", "BOOLEAN"),                # 0.00%
    ("event.q13", "q13", "BOOLEAN"),                # 2.40%
    ("event.q14", "q14", "BOOLEAN"),                # 0.05%
    ("event.q15", "q15", "BOOLEAN"),                # 1.51%
    ("event.q16", "q16", "BOOLEAN"),                # 0.06%
    ("event.q17", "q17", "BOOLEAN"),                # 0.53%
    ("event.q18", "q18", "BOOLEAN"),                # 0.50%
    ("event.q19", "q19", "BOOLEAN"),                # 0.01%
    ("event.q20", "q20", "BOOLEAN"),                # 1.47%
    ("event.q21", "q21", "BOOLEAN"),                # 0.02%
    ("event.q22", "q22", "BOOLEAN"),                # 0.93%
    ("event.q23", "q23", "BOOLEAN"),                # 0.15%
    ("event.q24", "q24", "BOOLEAN"),                # 0.12%
    ("event.q25", "q25", "BOOLEAN"),                # 0.23%
    ("event.q26", "q26", "BOOLEAN"),                # 0.04%
    ("event.q28", "q28", "BOOLEAN"),                # 0.01%
    ("event.q29", "q29", "BOOLEAN"),                # 1.07%
    ("event.q34", "q34", "BOOLEAN"),                # 0.00%
    ("event.q35", "q35", "BOOLEAN"),                # 0.01%
    ("event.q36", "q36", "BOOLEAN"),                # 0.00%
    ("event.q37", "q37", "BOOLEAN"),                # 0.02%
    ("event.q38", "q38", "BOOLEAN"),                # 0.00%
    ("event.q40", "q40", "BOOLEAN"),                # 0.00%
    ("event.q41", "q41", "BOOLEAN"),                # 0.08%
    ("event.q42", "q42", "BOOLEAN"),                # 0.74%
    ("event.q46", "q46", "DOUBLE"),                 # 0.01%
    ("event.q47", "q47", "DOUBLE"),                 # 0.01%
    ("event.q52", "q52", "BOOLEAN"),                # 0.00%
    ("event.q56", "q56", "VARCHAR"),                # 70.47%
    ("event.q57", "q57", "BOOLEAN"),                # 0.00%
    ("event.q59", "q59", "BOOLEAN"),                # 0.82%
    ("event.q60", "q60", "BOOLEAN"),                # 0.02%
    ("event.q61", "q61", "BOOLEAN"),                # 0.04%
    ("event.q62", "q62", "BOOLEAN"),                # 0.02%
    ("event.q63", "q63", "BOOLEAN"),                # 0.11%
    ("event.q64", "q64", "BOOLEAN"),                # 0.11%
    ("event.q65", "q65", "BOOLEAN"),                # 0.02%
    ("event.q66", "q66", "BOOLEAN"),                # 0.00%
    ("event.q67", "q67", "BOOLEAN"),                # 0.01%
    ("event.q68", "q68", "BOOLEAN"),                # 0.00%
    ("event.q69", "q69", "BOOLEAN"),                # 0.00%
    ("event.q70", "q70", "BOOLEAN"),                # 0.00%
    ("event.q71", "q71", "BOOLEAN"),                # 0.00%
    ("event.q72", "q72", "BOOLEAN"),                # 1.11%
    ("event.q73", "q73", "BOOLEAN"),                # 0.83%
    ("event.q74", "q74", "BOOLEAN"),                # 0.39%
    ("event.q75", "q75", "BOOLEAN"),                # 0.38%
    ("event.q76", "q76", "BOOLEAN"),                # 0.20%
    ("event.q77", "q77", "BOOLEAN"),                # 0.09%
    ("event.q78", "q78", "BOOLEAN"),                # 0.37%
    ("event.q79", "q79", "BOOLEAN"),                # 0.06%
    ("event.q80", "q80", "BOOLEAN"),                # 0.19%
    ("event.q81", "q81", "BOOLEAN"),                # 0.09%
    ("event.q83", "q83", "BOOLEAN"),                # 0.05%
    ("event.q84", "q84", "BOOLEAN"),                # 0.06%
    ("event.q85", "q85", "BOOLEAN"),                # 0.01%
    ("event.q86", "q86", "BOOLEAN"),                # 0.00%
    ("event.q87", "q87", "BOOLEAN"),                # 0.00%
    ("event.q88", "q88", "BOOLEAN"),                # 0.08%
    ("event.q89", "q89", "BOOLEAN"),                # 0.01%
    ("event.q90", "q90", "BOOLEAN"),                # 0.00%
    ("event.q91", "q91", "BOOLEAN"),                # 0.00%
    ("event.q92", "q92", "BOOLEAN"),                # 0.00%
    ("event.q93", "q93", "BOOLEAN"),                # 0.00%
    ("event.q94", "q94", "BOOLEAN"),                # 0.38%
    ("event.q95", "q95", "BOOLEAN"),                # 0.00%
    ("event.q96", "q96", "BOOLEAN"),                # 0.00%
    ("event.q98", "q98", "BOOLEAN"),                # 0.00%
    ("event.q99", "q99", "BOOLEAN"),                # 0.00%
    ("event.q100", "q100", "BOOLEAN"),              # 0.04%
    ("event.q101", "q101", "BOOLEAN"),              # 0.01%
    ("event.q104", "q104", "BOOLEAN"),              # 0.00%
    ("event.q105", "q105", "BOOLEAN"),              # 0.00%
    ("event.q106", "q106", "BOOLEAN"),              # 0.00%
    ("event.q108", "q108", "BOOLEAN"),              # 0.13%
    ("event.q109", "q109", "BOOLEAN"),              # 0.00%
    ("event.q110", "q110", "BOOLEAN"),              # 0.00%
    ("event.q111", "q111", "BOOLEAN"),              # 0.00%
    ("event.q113", "q113", "BOOLEAN"),              # 0.06%
    ("event.q114", "q114", "BOOLEAN"),              # 0.02%
    ("event.q117", "q117", "BOOLEAN"),              # 0.00%
    ("event.q118", "q118", "BOOLEAN"),              # 0.00%
    ("event.q119", "q119", "BOOLEAN"),              # 0.00%
    ("event.q120", "q120", "BOOLEAN"),              # 0.07%
    ("event.q121", "q121", "BOOLEAN"),              # 0.05%
    ("event.q122", "q122", "BOOLEAN"),              # 0.00%
    ("event.q123", "q123", "BOOLEAN"),              # 0.38%
    ("event.q128", "q128", "BOOLEAN"),              # 0.00%
    ("event.q129", "q129", "BOOLEAN"),              # 0.00%
    ("event.q130", "q130", "DOUBLE"),               # 0.16%
    ("event.q132", "q132", "BOOLEAN"),              # 0.00%
    ("event.q133", "q133", "BOOLEAN"),              # 0.03%
    ("event.q136", "q136", "BOOLEAN"),              # 0.02%
    ("event.q137", "q137", "BOOLEAN"),              # 0.00%
    ("event.q138", "q138", "BOOLEAN"),              # 0.02%
    ("event.q139", "q139", "BOOLEAN"),              # 0.02%
    ("event.q150", "q150", "BOOLEAN"),              # 0.00%
    ("event.q152", "q152", "BOOLEAN"),              # 3.49%
    ("event.q153", "q153", "BOOLEAN"),              # 0.08%
    ("event.q154", "q154", "BOOLEAN"),              # 1.35%
    ("event.q155", "q155", "BOOLEAN"),              # 4.22%
    ("event.q156", "q156", "BOOLEAN"),              # 0.64%
    ("event.q157", "q157", "BOOLEAN"),              # 1.67%
    ("event.q158", "q158", "BOOLEAN"),              # 0.00%
    ("event.q159", "q159", "BOOLEAN"),              # 0.00%
    ("event.q160", "q160", "BOOLEAN"),              # 0.04%
    ("event.q161", "q161", "BOOLEAN"),              # 0.00%
    ("event.q162", "q162", "BOOLEAN"),              # 0.00%
    ("event.q163", "q163", "BOOLEAN"),              # 0.00%
    ("event.q164", "q164", "BOOLEAN"),              # 0.00%
    ("event.q165", "q165", "BOOLEAN"),              # 0.00%
    ("event.q166", "q166", "BOOLEAN"),              # 0.00%
    ("event.q167", "q167", "BOOLEAN"),              # 0.69%
    ("event.q168", "q168", "BOOLEAN"),              # 0.44%
    ("event.q169", "q169", "BOOLEAN"),              # 0.05%
    ("event.q170", "q170", "BOOLEAN"),              # 0.02%
    ("event.q172", "q172", "BOOLEAN"),              # 0.00%
    ("event.q173", "q173", "BOOLEAN"),              # 0.14%
    ("event.q174", "q174", "BOOLEAN"),              # 0.08%
    ("event.q175", "q175", "BOOLEAN"),              # 0.00%
    ("event.q176", "q176", "BOOLEAN"),              # 0.02%
    ("event.q177", "q177", "BOOLEAN"),              # 0.11%
    ("event.q178", "q178", "BOOLEAN"),              # 2.05%
    ("event.q179", "q179", "BOOLEAN"),              # 0.16%
    ("event.q180", "q180", "BOOLEAN"),              # 0.05%
    ("event.q181", "q181", "BOOLEAN"),              # 0.03%
    ("event.q182", "q182", "BOOLEAN"),              # 0.31%
    ("event.q183", "q183", "BOOLEAN"),              # 0.02%
    ("event.q184", "q184", "BOOLEAN"),              # 0.03%
    ("event.q185", "q185", "BOOLEAN"),              # 0.14%
    ("event.q186", "q186", "BOOLEAN"),              # 0.03%
    ("event.q187", "q187", "BOOLEAN"),              # 0.00%
    ("event.q188", "q188", "BOOLEAN"),              # 0.00%
    ("event.q189", "q189", "BOOLEAN"),              # 1.43%
    ("event.q190", "q190", "BOOLEAN"),              # 0.00%
    ("event.q191", "q191", "BOOLEAN"),              # 0.00%
    ("event.q192", "q192", "BOOLEAN"),              # 0.00%
    ("event.q193", "q193", "BOOLEAN"),              # 0.00%
    ("event.q195", "q195", "BOOLEAN"),              # 0.08%
    ("event.q196", "q196", "BOOLEAN"),              # 0.20%
    ("event.q197", "q197", "DOUBLE"),               # 0.07%
    ("event.q198", "q198", "BOOLEAN"),              # 0.02%
    ("event.q199", "q199", "BOOLEAN"),              # 0.16%
    ("event.q209", "q209", "BOOLEAN"),              # 0.00%
    ("event.q210", "q210", "BOOLEAN"),              # 1.02%
    ("event.q211", "q211", "BOOLEAN"),              # 0.09%
    ("event.q214", "q214", "BOOLEAN"),              # 0.30%
    ("event.q215", "q215", "BOOLEAN"),              # 0.50%
    ("event.q217", "q217", "BOOLEAN"),              # 0.01%
    ("event.q218", "q218", "BOOLEAN"),              # 0.01%
    ("event.q219", "q219", "BOOLEAN"),              # 0.00%
    ("event.q220", "q220", "BOOLEAN"),              # 0.00%
    ("event.q221", "q221", "BOOLEAN"),              # 0.00%
    ("event.q222", "q222", "BOOLEAN"),              # 0.00%
    ("event.q223", "q223", "BOOLEAN"),              # 0.52%
    ("event.q224", "q224", "BOOLEAN"),              # 0.76%
    ("event.q225", "q225", "BOOLEAN"),              # 0.13%
    ("event.q227", "q227", "BOOLEAN"),              # 0.00%
    ("event.q228", "q228", "BOOLEAN"),              # 0.02%
    ("event.q232", "q232", "BOOLEAN"),              # 0.01%
    ("event.q233", "q233", "BOOLEAN"),              # 0.00%
    ("event.q236", "q236", "BOOLEAN"),              # 0.89%
    ("event.q237", "q237", "BOOLEAN"),              # 0.41%
    ("event.q238", "q238", "BOOLEAN"),              # 0.05%
    ("event.q239", "q239", "BOOLEAN"),              # 0.01%
    ("event.q240", "q240", "BOOLEAN"),              # 0.00%
    ("event.q241", "q241", "BOOLEAN"),              # 0.17%
    ("event.q242", "q242", "BOOLEAN"),              # 0.01%
    ("event.q243", "q243", "BOOLEAN"),              # 0.00%
    ("event.q244", "q244", "BOOLEAN"),              # 0.00%
    ("event.q245", "q245", "BOOLEAN"),              # 0.00%
    ("event.q247", "q247", "BOOLEAN"),              # 0.00%
    ("event.q248", "q248", "BOOLEAN"),              # 0.00%
    ("event.q249", "q249", "BOOLEAN"),              # 0.00%
    ("event.q250", "q250", "BOOLEAN"),              # 0.00%
    ("event.q251", "q251", "BOOLEAN"),              # 0.00%
    ("event.q252", "q252", "BOOLEAN"),              # 0.00%
    ("event.q253", "q253", "BOOLEAN"),              # 0.00%
    ("event.q254", "q254", "BOOLEAN"),              # 0.01%
    ("event.q261", "q261", "BOOLEAN"),              # 0.00%
    ("event.q262", "q262", "BOOLEAN"),              # 0.00%
    ("event.q263", "q263", "BOOLEAN"),              # 0.00%
    ("event.q264", "q264", "BOOLEAN"),              # 0.02%
    ("event.q265", "q265", "BOOLEAN"),              # 1.20%
    ("event.q266", "q266", "BOOLEAN"),              # 0.00%
    ("event.q267", "q267", "BOOLEAN"),              # 0.00%
    ("event.q268", "q268", "BOOLEAN"),              # 0.00%
    ("event.q269", "q269", "BOOLEAN"),              # 0.00%
    ("event.q270", "q270", "BOOLEAN"),              # 0.00%
    ("event.q271", "q271", "BOOLEAN"),              # 0.00%
    ("event.q272", "q272", "BOOLEAN"),              # 0.00%
    ("event.q273", "q273", "BOOLEAN"),              # 0.00%
    ("event.q274", "q274", "BOOLEAN"),              # 0.01%
    ("event.q275", "q275", "BOOLEAN"),              # 0.01%
    ("event.q276", "q276", "BOOLEAN"),              # 0.00%
    ("event.q277", "q277", "BOOLEAN"),              # 0.00%
    ("event.q278", "q278", "BOOLEAN"),              # 0.04%
    ("event.q279", "q279", "BOOLEAN"),              # 0.28%
    ("event.q280", "q280", "BOOLEAN"),              # 0.05%
    ("event.q281", "q281", "BOOLEAN"),              # 0.05%
    ("event.q282", "q282", "BOOLEAN"),              # 0.05%
    ("event.q284", "q284", "BOOLEAN"),              # 0.00%
    ("event.q285", "q285", "BOOLEAN"),              # 5.41%
    ("event.q286", "q286", "BOOLEAN"),              # 5.63%
    ("event.q287", "q287", "BOOLEAN"),              # 0.09%
    ("event.q289", "q289", "BOOLEAN"),              # 0.00%
    ("event.q292", "q292", "BOOLEAN"),              # 0.41%
    ("event.q293", "q293", "BOOLEAN"),              # 0.41%
    ("event.q294", "q294", "BOOLEAN"),              # 0.49%
    ("event.q295", "q295", "BOOLEAN"),              # 0.54%
    ("event.q296", "q296", "BOOLEAN"),              # 0.00%
    ("event.q297", "q297", "BOOLEAN"),              # 0.01%
    ("event.q298", "q298", "BOOLEAN"),              # 0.00%
    ("event.q300", "q300", "BOOLEAN"),              # 0.00%
    ("event.q301", "q301", "BOOLEAN"),              # 0.00%
    ("event.q307", "q307", "BOOLEAN"),              # 0.00%
    ("event.q312", "q312", "BOOLEAN"),              # 0.00%
    ("event.q313", "q313", "BOOLEAN"),              # 0.00%
    ("event.q314", "q314", "BOOLEAN"),              # 0.00%
    ("event.q315", "q315", "BOOLEAN"),              # 0.00%
    ("event.q316", "q316", "BOOLEAN"),              # 0.00%
    ("event.q317", "q317", "BOOLEAN"),              # 0.00%
    ("event.q318", "q318", "BOOLEAN"),              # 36.27%
    ("event.q319", "q319", "BOOLEAN"),              # 0.03%
    ("event.q320", "q320", "BOOLEAN"),              # 0.00%
    ("event.q321", "q321", "DOUBLE"),               # 1.43%
    ("event.q322", "q322", "DOUBLE"),               # 1.43%
    ("event.q323", "q323", "BOOLEAN"),              # 0.03%
    ("event.q324", "q324", "BOOLEAN"),              # 0.05%
    ("event.q326", "q326", "BOOLEAN"),              # 1.43%
    ("event.q327", "q327", "BOOLEAN"),              # 1.43%
    ("event.q328", "q328", "BOOLEAN"),              # 0.66%
    ("event.q329", "q329", "BOOLEAN"),              # 0.00%
    ("event.q330", "q330", "BOOLEAN"),              # 0.00%
    ("event.q331", "q331", "BOOLEAN"),              # 0.00%
    ("event.q332", "q332", "BOOLEAN"),              # 0.00%
    ("event.q333", "q333", "BOOLEAN"),              # 0.00%
    ("event.q334", "q334", "BOOLEAN"),              # 0.00%
    ("event.q335", "q335", "BOOLEAN"),              # 0.00%
    ("event.q336", "q336", "BOOLEAN"),              # 0.01%
    ("event.q338", "q338", "BOOLEAN"),              # 0.03%
    ("event.q341", "q341", "BOOLEAN"),              # 0.00%
    ("event.q342", "q342", "BOOLEAN"),              # 0.00%
    ("event.q343", "q343", "BOOLEAN"),              # 0.02%
    ("event.q344", "q344", "BOOLEAN"),              # 0.00%
    ("event.q345", "q345", "BOOLEAN"),              # 0.05%
    ("event.q346", "q346", "BOOLEAN"),              # 1.45%
    ("event.q347", "q347", "BOOLEAN"),              # 3.51%
    ("event.q348", "q348", "BOOLEAN"),              # 0.02%
    ("event.q353", "q353", "BOOLEAN"),              # 0.04%
    ("event.q355", "q355", "BOOLEAN"),              # 0.00%
    ("event.q356", "q356", "BOOLEAN"),              # 0.00%
    ("event.q357", "q357", "BOOLEAN"),              # 0.00%
    ("event.q358", "q358", "BOOLEAN"),              # 0.00%
    ("event.q359", "q359", "BOOLEAN"),              # 0.00%
    ("event.q360", "q360", "BOOLEAN"),              # 0.00%
    ("event.q361", "q361", "BOOLEAN"),              # 0.03%
    ("event.q362", "q362", "BOOLEAN"),              # 0.01%
    ("event.q363", "q363", "BOOLEAN"),              # 0.01%
    ("event.q365", "q365", "BOOLEAN"),              # 0.01%
    ("event.q367", "q367", "BOOLEAN"),              # 0.00%
    ("event.q368", "q368", "BOOLEAN"),              # 0.00%
    ("event.q369", "q369", "BOOLEAN"),              # 0.00%
    ("event.q370", "q370", "BOOLEAN"),              # 0.00%
    ("event.q371", "q371", "BOOLEAN"),              # 0.00%
    ("event.q372", "q372", "BOOLEAN"),              # 0.00%
    ("event.q373", "q373", "BOOLEAN"),              # 0.00%
    ("event.q374", "q374", "BOOLEAN"),              # 0.20%
    ("event.q375", "q375", "BOOLEAN"),              # 0.20%
    ("event.q376", "q376", "BOOLEAN"),              # 0.40%
    ("event.q377", "q377", "BOOLEAN"),              # 0.20%
    ("event.q378", "q378", "BOOLEAN"),              # 0.27%
    ("event.q379", "q379", "BOOLEAN"),              # 0.00%
    ("event.q380", "q380", "BOOLEAN"),              # 0.02%
    ("event.q381", "q381", "BOOLEAN"),              # 0.01%
    ("event.q383", "q383", "BOOLEAN"),              # 0.57%
    ("event.q384", "q384", "BOOLEAN"),              # 0.57%
    ("event.q385", "q385", "BOOLEAN"),              # 0.69%
    ("event.q386", "q386", "BOOLEAN"),              # 0.09%
    ("event.q387", "q387", "BOOLEAN"),              # 0.03%
    ("event.q388", "q388", "BOOLEAN"),              # 0.02%
    ("event.q389", "q389", "BOOLEAN"),              # 0.18%
    ("event.q390", "q390", "BOOLEAN"),              # 0.02%
    ("event.q391", "q391", "BOOLEAN"),              # 0.08%
    ("event.q392", "q392", "BOOLEAN"),              # 0.06%
    ("event.q393", "q393", "BOOLEAN"),              # 0.10%
    ("event.q394", "q394", "BOOLEAN"),              # 0.00%
    ("event.q395", "q395", "BOOLEAN"),              # 0.16%
    ("event.q396", "q396", "BOOLEAN"),              # 0.16%
    ("event.q397", "q397", "BOOLEAN"),              # 0.06%
    ("event.q398", "q398", "BOOLEAN"),              # 0.02%
    ("event.q399", "q399", "BOOLEAN"),              # 1.28%
    # -- named event fields not previously requested --------------------------
    # Notably event.success / event.fail: outcome for tackles, take-ons and
    # clearances, which we hold ~800k of and cannot currently score.
    # DELIBERATELY EXCLUDED: the dense sequence/possession geometry
    # (sequenceStartX, passLength, passAngle, secondsSincePriorEvent and the
    # rest) - 97% of the storage cost and all derivable from sequenceId,
    # possessionSeqNum and the coordinates. Compute them; do not store them.
    ("event.assist_q107", "assist_q107", "BOOLEAN"),# 0.00%
    ("event.assist_q2", "assist_q2", "BOOLEAN"),    # 0.24%
    ("event.assist_q223", "assist_q223", "BOOLEAN"),# 0.07%
    ("event.assist_q224", "assist_q224", "BOOLEAN"),# 0.10%
    ("event.assist_q225", "assist_q225", "BOOLEAN"),# 0.02%
    ("event.carryContinuation", "carryContinuation", "BOOLEAN"),# 0.66%
    ("event.caughtOffsides", "caughtOffsides", "BOOLEAN"),# 0.07%
    ("event.fail", "fail", "BOOLEAN"),              # 19.27%
    ("event.minusGoal", "minusGoal", "BOOLEAN"),    # 0.09%
    ("event.minusShot", "minusShot", "BOOLEAN"),    # 0.70%
    ("event.onField", "onField", "BOOLEAN"),        # 0.00%
    ("event.optaExpectedGoals", "optaExpectedGoals", "DOUBLE"),# 1.40%
    ("event.passerExpectedGoals", "passerExpectedGoals", "DOUBLE"),# 0.54%
    ("event.plusGoal", "plusGoal", "BOOLEAN"),      # 0.09%
    ("event.possession", "possession", "BOOLEAN"),  # 40.86%
    ("event.possessionStartq107", "possessionStartq107", "BOOLEAN"),# 0.80%
    ("event.possessionStartq124", "possessionStartq124", "BOOLEAN"),# 0.69%
    ("event.possessionStartq2", "possessionStartq2", "BOOLEAN"),# 0.01%
    ("event.possessionStartq24", "possessionStartq24", "BOOLEAN"),# 0.00%
    ("event.possessionStartq5", "possessionStartq5", "BOOLEAN"),# 0.50%
    ("event.possessionStartq6", "possessionStartq6", "BOOLEAN"),# 0.01%
    ("event.possessionStartq74", "possessionStartq74", "BOOLEAN"),# 0.30%
    ("event.save_q176", "save_q176", "BOOLEAN"),    # 0.02%
    ("event.save_q177", "save_q177", "BOOLEAN"),    # 0.10%
    ("event.save_q190", "save_q190", "BOOLEAN"),    # 0.00%
    ("event.scorer", "scorer", "BOOLEAN"),          # 0.09%
    ("event.secondarySuccess", "secondarySuccess", "BOOLEAN"),# 29.60%
    ("event.sequenceOptaExpectedGoalsSum", "sequenceOptaExpectedGoalsSum", "DOUBLE"),# 1.40%
    ("event.sequenceStartq107", "sequenceStartq107", "BOOLEAN"),# 1.74%
    ("event.sequenceStartq124", "sequenceStartq124", "BOOLEAN"),# 0.71%
    ("event.sequenceStartq2", "sequenceStartq2", "BOOLEAN"),# 0.59%
    ("event.sequenceStartq24", "sequenceStartq24", "BOOLEAN"),# 0.04%
    ("event.sequenceStartq5", "sequenceStartq5", "BOOLEAN"),# 1.27%
    ("event.sequenceStartq6", "sequenceStartq6", "BOOLEAN"),# 0.48%
    ("event.sequenceStartq74", "sequenceStartq74", "BOOLEAN"),# 0.32%
    ("event.shooterShotLength", "shooterShotLength", "DOUBLE"),# 0.70%
    ("event.shot_playType", "shot_playType", "VARCHAR"),# 2.49%
    ("event.shot_q15", "shot_q15", "BOOLEAN"),      # 0.42%
    ("event.shot_q160", "shot_q160", "BOOLEAN"),    # 0.06%
    ("event.shot_q214", "shot_q214", "BOOLEAN"),    # 0.50%
    ("event.shot_q22", "shot_q22", "BOOLEAN"),      # 1.55%
    ("event.shot_q23", "shot_q23", "BOOLEAN"),      # 0.25%
    ("event.shot_q24", "shot_q24", "BOOLEAN"),      # 0.20%
    ("event.shot_q25", "shot_q25", "BOOLEAN"),      # 0.37%
    ("event.shot_q26", "shot_q26", "BOOLEAN"),      # 0.04%
    ("event.shot_q328", "shot_q328", "BOOLEAN"),    # 1.18%
    ("event.shotLength", "shotLength", "DOUBLE"),   # 1.40%
    ("event.success", "success", "BOOLEAN"),        # 58.55%
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


PLAYER_POOL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "player_pools")

POOL_DISPLAY = {
    "europe": "Europe",
    "north_america": "North America",
    "womens": "Women's Soccer",
}


def refresh_player_pool(session, pool_key, config, supabase_url=None,
                        supabase_key=None, output_dir=None):
    """Download one player pool and mirror it to Supabase.

    Returns (ok, message). Never raises: a pool refresh runs at the end of a
    campaign and must not lose the run's result by throwing.

    THIS IS BUSINESS LOGIC, NOT PAGE CODE. It used to live in app.py beside
    the superseded per-team downloader, which meant the pools could only be
    refreshed from the page you no longer use to download anything. The pools
    are DERIVED from the games a campaign fetches - a new season's players do
    not appear until this runs - so it belongs where the download happens.

    The pools are read from Supabase by the DP player-compare charts, which
    resolve membership there rather than from `events`; a stale pool silently
    omits players rather than failing.
    """
    out_dir = output_dir or PLAYER_POOL_DIR
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"{pool_key}.csv")
    season_ids = (config.get("player_pools", {})
                  .get(pool_key, {})
                  .get("seasons", []))
    if not season_ids:
        return False, f"no seasons configured for pool {pool_key!r}"

    try:
        row_count, _size_kb = download_player_pool(session, season_ids, csv_path)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "expired" in msg.lower():
            return False, "Session expired - paste a fresh cURL command."
        return False, f"Download failed: {msg}"

    if not (supabase_url and supabase_key):
        return True, f"{row_count:,} players - saved locally (Supabase not configured)"
    try:
        upload_to_supabase(supabase_url, supabase_key, csv_path, f"{pool_key}.csv")
    except Exception as e:
        # The local file is good; only the mirror failed. Say so rather than
        # reporting a clean success the charts will not see.
        return True, f"{row_count:,} players - saved locally (Supabase upload FAILED: {e})"
    return True, f"{row_count:,} players - uploaded to Supabase"


def load_known_empty_games():
    """gameId -> reason, for fixtures that are settled and will never have
    events. Read from config.json so it travels with every other season fact
    and reaches the deployed app through the same `save_config` mirror.

    Missing key returns {} - the feature is additive and its absence must not
    break a work list.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("known_empty_games", {}) or {}
    except Exception:
        return {}


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


def build_game_event_statement(season_ids, game_ids):
    """Every event in the given games, BOTH sides, from one request.

    NO TEAM PREDICATE. That is the whole point, and it is what makes the
    team-level columns correct.

    Three differences from `build_event_log_statement`:

    1. **No `event.toucher` predicate.** That filter is why cards,
       substitutions, corners, ball recoveries and ~20 other types never
       arrive - 22 play types instead of 47.
    2. **`is_team` / `is_opp` as RAW booleans.** They say which side each
       event belongs to.
    3. **No `team.teamId` filter.** Naming a team makes it the ANCHOR, and
       TruMedia then answers every team-scoped column from that team's point
       of view - on the opponent's rows too. That is how 21 columns
       (teamAbbrevName, newestTeamColor, MatchState, Formation, the score
       columns, the assist and chance flags...) came to hold the home side's
       values on 4.3M away rows between 2026-08-29 and 2026-09-01.

    With no team named, TruMedia returns each event ONCE PER SIDE - about 2.5x
    the rows - each copy carrying that side's own values. Keeping the copies
    where `is_team` is true gives every event exactly once, described by the
    team that actually performed it. Verified on three matches (largest in the
    database, a World Cup extra-time tie, an ordinary league game): identical
    event coverage to two anchored pulls, zero duplicates, both sides present.

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
        f"WHERE ((season.seasonId IN ({season_id_str}))) "
        f"AND (game.gameId IN ({gids_str})) "
        f"ORDER BY event.gameEventIndex ASC "
        f"LIMIT 200000"
    )


# Work-list states, in the order a campaign should attack them.
WORK_MISSING = "missing"        # no events at all
WORK_ONE_SIDED = "one_sided"    # half a match - the 22.6% problem
WORK_NOT_PLAYED = "not_played"  # fixture exists, no result yet
WORK_NO_DATA = "no_data"        # decided, and no events will ever exist
WORK_COMPLETE = "complete"      # both sides, correct on every column
WORK_ORDER = [WORK_MISSING, WORK_ONE_SIDED, WORK_NOT_PLAYED,
              WORK_NO_DATA, WORK_COMPLETE]

# WORK_NO_DATA: fixtures that are settled and eventless, listed by gameId in
# config.json's `known_empty_games`. Without it they sit in `missing` forever,
# get re-attempted by every campaign, and every completeness check has to
# explain them away.
#
# IT HAS TO BE PER-GAME, NOT PER-STATUS. `Awarded` covers three different
# things and only the events tell them apart - measured on all five awarded
# fixtures in the database:
#
#   PSG v Le Havre W        0 events      never played
#   PSG v Fleury W          0 events      never played
#   Strasbourg W v PSG      0 events      never played
#   Lens W v PSG        1,973 events      PLAYED IN FULL, then awarded
#   Nantes v Toulouse     567 events      ABANDONED after 21 minutes
#
# Excluding the status wholesale would silently drop a complete 94-minute
# match and a real abandoned one, which is why `INGESTABLE_STATUSES` still
# includes it.
#
# AND IT MUST STAY MANUAL. A game that returns zero events because the session
# expired mid-run looks identical to one that has no events to give;
# auto-listing would permanently skip a real fixture after one bad night. A
# postponed match is NOT this - `not_played` already handles it correctly.

# THE ANCHOR CHECK - an invariant now, not a work-list state.
#
# Games written between 2026-08-29 and 2026-09-01 were fetched while
# `build_game_event_statement` still named a team. Naming one made it the
# ANCHOR, and TruMedia answered every team-scoped column from that team's
# point of view - including on the opponent's rows. 21 columns on 4.3M away
# rows held the home side's values, with nothing erroring.
#
# The detector is one aggregate: a two-sided game whose rows carry only ONE
# distinct `teamAbbrevName` was written by an anchored request. Two distinct
# values means each side kept its own identity, which only the anchor-free
# statement produces.
#
# WHY IT IS NO LONGER A STATE. A state means "select these and re-download",
# and that stopped being the right response once the whole database was
# rebuilt: measured 2026-09-09, all 5,619 games are two-sided with two
# abbreviations, so the state was a branch that could never be taken. But
# deleting the CHECK would make the bug invisible again, and it was silent the
# first time - plausible row counts, no error, wrong values. So it lives on the
# Health page as a pass/fail assertion: if it ever fires the answer is to fix
# the statement, not to re-fetch under a broken one.
#
# `count_anchored_games` is that check. It also subsumes the old
# WORK_OLD_FEED state, which detected an `event.toucher` ingest predicate that
# no longer exists anywhere in the code path - its only route back was the same
# "someone rewrote the statement" scenario this covers.


def count_anchored_games(con):
    """Games whose two sides share one team abbreviation. Should be 0.

    Non-zero means something re-introduced a team name into the event
    statement and the away rows are carrying the home side's values.
    """
    return con.execute(
        "SELECT count(*) FROM ("
        "  SELECT gameId FROM events GROUP BY gameId"
        "  HAVING count(DISTINCT teamId) >= 2"
        "     AND count(DISTINCT \"teamAbbrevName\") < 2)"
    ).fetchone()[0]

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
                               state=WORK_MISSING)
    gids = list(fixtures["gameId"])
    ph = ",".join("?" * len(gids))
    have = con.execute(
        f"SELECT gameId, count(DISTINCT teamId) AS sides, count(*) AS n "
        f"FROM events WHERE gameId IN ({ph}) GROUP BY gameId", gids
    ).fetchall()
    sides = {g: s for g, s, _ in have}
    counts = {g: n for g, _, n in have}

    # Settled, eventless fixtures. Listed by gameId because the status cannot
    # distinguish them - see WORK_NO_DATA above.
    no_data = set(load_known_empty_games())

    out = fixtures.copy()
    out["sides_present"] = out["gameId"].map(sides).fillna(0).astype(int)
    out["events_stored"] = out["gameId"].map(counts).fillna(0).astype(int)

    played = (out["status"].astype(str).str.lower().isin(INGESTABLE_STATUSES)
              if "status" in out.columns else True)

    def _state(row, is_played):
        if row["sides_present"] >= 2:
            return WORK_COMPLETE
        # Checked before `missing` but after `complete`: if a game we thought
        # was eventless turns out to have events, believe the events.
        if row["gameId"] in no_data:
            return WORK_NO_DATA
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


# Batches used to be grouped by HOME TEAM. They are not any more, and the
# reason the grouping existed is worth keeping:
#
#   THE OLD TRAP: the event query named a team, and that name FILTERED. A game
#   returned events only if the named team played in it, so a mixed batch
#   silently dropped every game it was not in. Measured: batching 20 arbitrary
#   games lost Manchester United v Arsenal because its batch was anchored on
#   Crystal Palace. Nothing errored, the row count looked plausible, and only
#   counting DISTINCT GAMES caught it.
#
# `build_game_event_statement` no longer names a team, so nothing filters and
# any games can share a request. That also removes what was really capping
# batch size: a club has at most ~25 home games in a season, so batches were
# capped there and never reached this constant.
#
# 40 is MEASURED, not guessed. On the 40 largest games of a 557-game season,
# one request returned 70,974 own-side rows against 70,974 pulled individually,
# with all 40 gameIds present - exact, no truncation - in a 182,618-row
# response. That is 91% of `LIMIT 200000`, and the largest matches in the whole
# database are bigger still, so the default is HALF the proven ceiling.
MAX_GAMES_PER_REQUEST = 20


def plan_batches(todo, batch_size=MAX_GAMES_PER_REQUEST):
    """Chunk games into requestable batches. Returns [rows, ...].

    No grouping: with no team predicate a request returns whatever games it
    asks for, so batches are filled in order and nothing is at risk of being
    filtered out.

    Shared with the UI on purpose. A page that estimates the cost with its own
    arithmetic drifts from what the runner actually does the moment either
    changes - the Campaign page told users "one request each" for a while
    after batching landed, which was off by a factor of nineteen.
    """
    rows = list(todo.iterrows())
    step = max(1, batch_size)
    return [rows[i:i + step] for i in range(0, len(rows), step)]


def estimate_requests(todo, batch_size=MAX_GAMES_PER_REQUEST,
                      with_minutes=True, n_seasons=0):
    """(batches, total_requests) for a planned campaign."""
    n = len(plan_batches(todo, batch_size))
    return n, n * (2 if with_minutes else 1) + n_seasons


def run_campaign(session, token, fixtures, work, output_dir, season_ids,
                 states=(WORK_MISSING, WORK_ONE_SIDED),
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
        for bi, batch in enumerate(batches, 1):
            if stop is not None and stop():
                skipped = total - done
                break
            gids = [r["gameId"] for _, r in batch]
            note = ""
            try:
                ev_path = os.path.join(output_dir, f"batch_{bi}_events.csv")
                download_game_events(session, season_ids, gids, ev_path)
                _, n = upsert_game_events(token, ev_path, fixture_map, con=con)
                note = f"{n:,} events"
                if with_minutes:
                    mn_path = os.path.join(output_dir, f"batch_{bi}_min.csv")
                    download_game_minutes(session, season_ids, gids, mn_path)
                    m = upsert_game_minutes(token, mn_path, con=con)
                    note += f", {m:,} minute rows"
                    _quiet_remove(mn_path)
                _archive_events(ev_path, gids)
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


# Response cache. Set this to a directory and every downloaded event CSV is
# kept, gzipped, instead of being deleted after the upsert.
#
# WHY: a schema change is otherwise a RE-DOWNLOAD. On 2026-09-08 the event
# model went from 52 expanded fields to 401, and the only reason that cost
# ~1,046 requests is that every prior response had been thrown away. With the
# cache, widening the SELECT means re-parsing files already on disk.
#
# It is also the evidence. The three-gate failure writes NULLs and reports
# "INGEST OK"; comparing the stored rows against the response that produced
# them is how you tell whether the feed or the parser was at fault.
#
# Cheap: responses arrive gzipped at ~0.16 MB/game, so the whole database is
# around 1 GB cached. Uncompressed CSV would be ~30 GB.
EVENT_CACHE_ENV = "DATA_MANAGER_EVENT_CACHE"


def _cache_dir():
    d = os.environ.get(EVENT_CACHE_ENV)
    if d:
        os.makedirs(d, exist_ok=True)
    return d


def _archive_events(csv_path, game_ids, cache_dir=None):
    """Gzip a downloaded event CSV into the cache. Returns the path, or None.

    Named by a hash of the game ids, so the same batch always lands on the
    same file and a re-run overwrites rather than accumulating duplicates.
    Failure here is deliberately non-fatal - the cache is an optimisation, and
    losing it must never fail an ingest that otherwise succeeded.
    """
    cache_dir = cache_dir or _cache_dir()
    if not cache_dir:
        return None
    try:
        key = hashlib.sha1(",".join(sorted(game_ids)).encode()).hexdigest()[:12]
        out = os.path.join(cache_dir, f"ev_{key}_{len(game_ids)}g.csv.gz")
        with open(csv_path, "rb") as fh, gzip.open(out, "wb", compresslevel=6) as gz:
            shutil.copyfileobj(fh, gz)
        return out
    except Exception:
        return None


def fixtures_from_games(con):
    """Rebuild the gameId -> home/away map that upsert_game_events needs.

    `run_campaign` gets this from `discover_fixtures`, which costs a request
    per season. On a re-parse the `games` table already holds it, so the cache
    can be replayed with no network at all.
    """
    import pandas as _pd
    rows = con.execute(
        "SELECT gameId, homeTeamId, awayTeamId, homeTeam, awayTeam FROM games"
    ).fetchall()
    return {r[0]: {"homeTeamId": r[1], "awayTeamId": r[2],
                   "homeTeam": r[3], "awayTeam": r[4]} for r in rows}


def reparse_event_cache(cache_dir, token, con=None, fixtures=None,
                        progress=None, stop=None):
    """Re-ingest every cached response. No network.

    This is what makes a schema change cheap: widen EXPANDED_EVENT_FIELDS,
    run _apply_schema, then replay the cache. Columns the old responses do not
    carry stay NULL - which is correct and visible, not silent, because the
    cached file is right there to check against.

    Idempotent: upsert_game_events DELETEs by gameId before inserting, so
    replaying a file that is already loaded is a no-op in effect.

    Returns (files_done, rows, failed).
    """
    own = con is None
    if own:
        con = get_motherduck_connection(token)
    try:
        fixtures = fixtures if fixtures is not None else fixtures_from_games(con)
        files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".csv.gz"))
        done = rows = failed = 0
        for i, name in enumerate(files, 1):
            if stop is not None and stop():
                break
            src = os.path.join(cache_dir, name)
            tmp = src[:-3]
            try:
                with gzip.open(src, "rb") as gz, open(tmp, "wb") as fh:
                    shutil.copyfileobj(gz, fh)
                _, n = upsert_game_events(token, tmp, fixtures, con=con)
                rows += n
                done += 1
            except Exception as e:
                failed += 1
                if progress:
                    progress(i, len(files), name, f"{type(e).__name__}: {e}"[:160])
                continue
            finally:
                _quiet_remove(tmp)
            if progress:
                progress(i, len(files), name, f"{n:,} events")
        return done, rows, failed
    finally:
        if own:
            con.close()


def download_game_events(session, season_ids, game_ids, output_path):
    """Fetch both sides' events for the given games. Returns (rows, size_kb).

    No anchor team: see `build_game_event_statement` for why naming one
    corrupts every team-scoped column on the opponent's rows.
    """
    payload = {
        "format": "MIXED",
        "statement": build_game_event_statement(season_ids, game_ids),
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

    # KEEP ONLY `is_team` ROWS.
    #
    # With no team predicate the response carries each event TWICE - once
    # described from each side. The `is_team` copy is the one written from the
    # point of view of the team that actually performed the event, so every
    # team-scoped column on it (teamAbbrevName, newestTeamColor, Formation,
    # MatchState, the score columns, the assist and chance flags) already
    # describes the right team. Taking the `is_opp` copy as well would
    # reintroduce exactly the corruption this shape exists to avoid.
    #
    # `teamId` therefore needs no reconstruction: the row states its own team.
    # The previous version derived it from the anchor and the fixture, and
    # fixed `teamFullName` to match - but stopped there, leaving 21 other
    # columns describing the anchor. That is the bug this replaces.
    #
    # Rows flagged neither is_team nor is_opp are `Sequence` / `Possession`
    # aggregate rows. They have no owning team and are dropped, as before.
    df = df[is_team].copy()
    if df.empty:
        return 0, 0
    df = df[df["teamId"].notna()].copy()

    # `teamFullName` is NOT rewritten from the fixture any more. It arrives
    # correct, like every other team-scoped column, because the row is the
    # copy written from its own team's point of view. Overwriting it from
    # config would reintroduce name-based identity, which the migration spent
    # a lot of effort removing.

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
            # ROLLBACK is best-effort. If the server already aborted the
            # transaction - a MotherDuck internal error, a dropped session -
            # this raises "cannot rollback - no transaction is active", and
            # that exception REPLACES the real one, because `raise` below is
            # never reached. Batch 27 of a campaign reported exactly that and
            # the underlying cause was lost.
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
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
            # ROLLBACK is best-effort. If the server already aborted the
            # transaction - a MotherDuck internal error, a dropped session -
            # this raises "cannot rollback - no transaction is active", and
            # that exception REPLACES the real one, because `raise` below is
            # never reached. Batch 27 of a campaign reported exactly that and
            # the underlying cause was lost.
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
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
