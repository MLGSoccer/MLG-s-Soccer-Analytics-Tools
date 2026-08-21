"""
TruMedia Data Manager
Local dashboard for managing player pool and team event log data
for the CBS Sports Soccer Chart Builder.
"""
import streamlit as st
import json
import os
import sys
import tempfile
import subprocess
import pandas as pd
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import (
    parse_cookies_from_curl, create_session, probe_endpoint_health,
    download_player_pool, upload_to_supabase, load_secrets,
    download_event_log, upsert_events_to_motherduck,
    download_minutes_and_cards, upsert_minutes_to_motherduck,
    get_motherduck_connection, get_team_season_last_dates,
    fetch_and_store_fixture_data, get_games_missing_fixture_data,
    group_teams_by_league,
)

st.set_page_config(
    page_title="TruMedia Data Manager",
    page_icon="⚽",
    layout="wide"
)

# ── Load config and secrets ───────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")
LAST_UPDATED_PATH = os.path.join(BASE_DIR, "data", "last_updated.json")
MINUTES_LAST_UPDATED_PATH = os.path.join(BASE_DIR, "data", "minutes_last_updated.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

secrets = load_secrets(SECRETS_PATH)
SUPABASE_URL = secrets.get("SUPABASE_URL")
SUPABASE_KEY = secrets.get("SUPABASE_KEY")
MOTHERDUCK_TOKEN = secrets.get("MOTHERDUCK_TOKEN")
API_FOOTBALL_KEY = secrets.get("API_FOOTBALL_KEY")
supabase_configured = bool(SUPABASE_URL and SUPABASE_KEY)
motherduck_configured = bool(MOTHERDUCK_TOKEN)
apifootball_configured = bool(API_FOOTBALL_KEY)

DATA_DIR = os.path.join(BASE_DIR, "data", "player_pools")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)


# These two files are display-only caches: they drive the "last downloaded"
# columns and nothing else. Incremental cutoffs come from MotherDuck, so a
# lost or malformed file costs a blank column and nothing more.
#
# They were keyed by team abbrev until Aug 2026, which silently merged teams:
# 444 teams share only 432 abbrevs, and POR is Porto, Portsmouth AND Portugal.
# Downloading one stamped all three. Now keyed by team_id, which is unique.
_ABBREV_TO_TEAM_ID = {}
_AMBIGUOUS_ABBREVS = set()
for _t in config.get("teams", []):
    _ab = _t.get("abbrev")
    if _ab in _ABBREV_TO_TEAM_ID:
        _AMBIGUOUS_ABBREVS.add(_ab)
    _ABBREV_TO_TEAM_ID[_ab] = _t["team_id"]

_KNOWN_TEAM_IDS = {t["team_id"] for t in config.get("teams", [])}


def _migrate_timestamp_keys(data):
    """Convert an abbrev-keyed timestamp file to team_id keys.

    Idempotent - entries already keyed by team_id pass straight through, so
    this is safe to run on every load. Colliding abbrevs are DROPPED rather
    than guessed: a single POR timestamp cannot be attributed to Porto,
    Portsmouth or Portugal, and "Never" is more honest than a date belonging
    to another club. Those self-heal on the next download.
    """
    migrated = {}
    for key, value in data.items():
        if key in _KNOWN_TEAM_IDS:
            migrated[key] = value
        elif key in _AMBIGUOUS_ABBREVS:
            continue
        elif key in _ABBREV_TO_TEAM_ID:
            migrated[_ABBREV_TO_TEAM_ID[key]] = value
        # unknown key: a team removed from config. Drop it.
    return migrated


def _load_timestamps(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _migrate_timestamp_keys(json.load(f))
    return {}


def _save_timestamps(path, data):
    with open(path, 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_last_updated():
    return _load_timestamps(LAST_UPDATED_PATH)


def save_last_updated(data):
    _save_timestamps(LAST_UPDATED_PATH, data)


def load_minutes_last_updated():
    return _load_timestamps(MINUTES_LAST_UPDATED_PATH)


def save_minutes_last_updated(data):
    _save_timestamps(MINUTES_LAST_UPDATED_PATH, data)


# ── League grouping ───────────────────────────────────────────────────────────
# Grouped by league NAME via the shared helper, not by season display label.
# Labels carry the season ("Premier League 2025/26"), so a promoted club that
# only holds the 2026/27 id would open a second Premier League expander and
# split the league in two. See downloader.group_teams_by_league.
leagues = group_teams_by_league(config)  # league_name -> [team_dict, ...]

_season_names = config.get("seasons", {})
_season_leagues = config.get("season_leagues", {})

POOL_DISPLAY = {
    "europe": "Europe",
    "north_america": "North America",
    "womens": "Women's Soccer",
}


def run_pool_download(session, pool_key, pool_name):
    """Download a pool and upload to Supabase. Returns (success, message)."""
    csv_path = os.path.join(DATA_DIR, f"{pool_key}.csv")
    season_ids = config["player_pools"][pool_key]["seasons"]

    try:
        row_count, size_kb = download_player_pool(session, season_ids, csv_path)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "expired" in msg.lower():
            return False, "Session expired — paste a fresh cURL command."
        return False, f"Download failed: {msg}"

    if supabase_configured:
        try:
            upload_to_supabase(SUPABASE_URL, SUPABASE_KEY, csv_path, f"{pool_key}.csv")
            return True, f"{row_count:,} players — saved locally and uploaded to Supabase"
        except Exception as e:
            return True, f"{row_count:,} players — saved locally (Supabase upload failed: {e})"
    else:
        return True, f"{row_count:,} players — saved locally (Supabase not configured)"


# ── Header ────────────────────────────────────────────────────────────────────
st.title("TruMedia Data Manager")
st.caption("Manages data downloads for the CBS Sports Soccer Chart Builder")

if not supabase_configured:
    st.warning("Supabase credentials not found in secrets.env — player pool data will only be saved locally.")
if not motherduck_configured:
    st.warning("MotherDuck token not found in secrets.env — event log downloads will not work.")

st.divider()

# ── Authentication ────────────────────────────────────────────────────────────
st.header("Authentication")

with st.expander("How to get your cURL command"):
    st.markdown("""
1. Log into TruMedia in Chrome
2. Open **DevTools** (F12) and go to the **Network** tab
3. On any TruMedia data page (e.g. players-in-possession-stats),
   type `dp-proxy` into the Network filter box
4. **Reload the page** — at least one `dp-proxy-*` request appears
5. Right-click any one of them → **Copy** → **Copy as cURL**
6. Paste the result in the box below

**Note (May 2026):** TruMedia moved CSV exports to client-side in their UI,
so right-clicking Export no longer surfaces a request. Any `dp-proxy-*`
request from a page load carries the same auth cookies the data manager
needs — pick whichever one shows up.
""")

col_input, col_status = st.columns([3, 1])

with col_input:
    curl_input = st.text_area(
        "Paste cURL command",
        height=80,
        placeholder='curl "https://cbssports.opta.trumediasports.com/dp-proxy-..." ...',
    )

with col_status:
    st.write("")
    st.write("")
    if curl_input:
        try:
            cookies = parse_cookies_from_curl(curl_input)
            # Hash the cURL so the endpoint probe only fires once per paste
            # (re-runs of the Streamlit script shouldn't keep re-probing).
            curl_hash = hash(curl_input)
            if st.session_state.get("_probed_curl_hash") != curl_hash:
                with st.spinner("Verifying endpoint..."):
                    sample_season = (
                        config.get("player_pools", {})
                        .get("europe", {})
                        .get("seasons", [None])[0]
                    )
                    if sample_season:
                        probe_session = create_session(cookies)
                        ok, message = probe_endpoint_health(
                            probe_session, sample_season,
                        )
                        st.session_state["_probed_curl_hash"] = curl_hash
                        st.session_state["_probe_ok"] = ok
                        st.session_state["_probe_message"] = message
                    else:
                        st.session_state["_probed_curl_hash"] = curl_hash
                        st.session_state["_probe_ok"] = True
                        st.session_state["_probe_message"] = (
                            "No sample season in config.json - skipped probe"
                        )

            if st.session_state.get("_probe_ok", True):
                st.session_state["cookies"] = cookies
                st.success("Authenticated")
            else:
                st.session_state.pop("cookies", None)
                st.error(st.session_state.get("_probe_message", "Probe failed"))
        except ValueError as e:
            st.session_state.pop("cookies", None)
            st.session_state.pop("_probed_curl_hash", None)
            st.error(str(e))
    elif "cookies" in st.session_state:
        st.info("Session active")
    else:
        st.warning("Not authenticated")

st.divider()

# ── Player Pools ──────────────────────────────────────────────────────────────
st.header("Player Pools")

authenticated = "cookies" in st.session_state
cols = st.columns(3)

for i, (pool_key, pool_name) in enumerate(POOL_DISPLAY.items()):
    with cols[i]:
        csv_path = os.path.join(DATA_DIR, f"{pool_key}.csv")

        st.subheader(pool_name)

        if os.path.exists(csv_path):
            mtime = os.path.getmtime(csv_path)
            last_updated = datetime.fromtimestamp(mtime).strftime("%b %d, %Y  %H:%M")
            size_kb = os.path.getsize(csv_path) / 1024
            try:
                with open(csv_path, encoding="utf-8") as f:
                    row_count = sum(1 for _ in f) - 1
                st.caption(f"Last updated: {last_updated}")
                st.caption(f"{row_count:,} players  •  {size_kb:.0f} KB")
            except Exception:
                st.caption(f"Last updated: {last_updated}")
        else:
            st.caption("Never downloaded")
            st.caption("")

        result_key = f"result_{pool_key}"
        if result_key in st.session_state:
            success, message = st.session_state.pop(result_key)
            if success:
                st.success(message)
            else:
                st.error(message)

        if st.button(f"Download {pool_name}", key=f"dl_{pool_key}", disabled=not authenticated):
            with st.spinner(f"Downloading {pool_name}..."):
                session = create_session(st.session_state["cookies"])
                success, message = run_pool_download(session, pool_key, pool_name)
                st.session_state[result_key] = (success, message)
                st.rerun()

st.divider()

# ── Bulk Actions ──────────────────────────────────────────────────────────────
st.header("Bulk Actions")

if "bulk_results" in st.session_state:
    for pool_name, success, message in st.session_state.pop("bulk_results"):
        if success:
            st.success(f"{pool_name}: {message}")
        else:
            st.error(f"{pool_name}: {message}")

if st.button("Download All Pools", type="primary", disabled=not authenticated):
    session = create_session(st.session_state["cookies"])
    results = []
    progress = st.progress(0)
    status = st.empty()

    for i, (pool_key, pool_name) in enumerate(POOL_DISPLAY.items()):
        status.text(f"Downloading {pool_name}...")
        success, message = run_pool_download(session, pool_key, pool_name)
        results.append((pool_name, success, message))
        progress.progress((i + 1) / len(POOL_DISPLAY))

    status.empty()
    progress.empty()

    st.session_state["bulk_results"] = results
    st.rerun()

st.divider()

# ── Downloads ─────────────────────────────────────────────────────────────────
st.header("Downloads")
st.caption("Downloads event logs and player minutes from TruMedia and upserts to MotherDuck. "
           "Also fetches red card and own goal timing from API-Football for chart annotations.")

TEST_DOWNLOAD_DIR = os.path.join(BASE_DIR, "data", "test_downloads")


def _season_label(season_id):
    """Display name for a season id, falling back to league name then id."""
    return (_season_names.get(season_id)
            or _season_leagues.get(season_id)
            or f"{season_id[:10]}...")


def _run_downloads(teams_to_download, season_filter=None, mode="incremental",
                   date_from=None, date_to=None, download_only=False,
                   do_events=True, do_minutes=True, fetch_player_minutes=True):
    """Download events (and optionally minutes) for the given teams.

    season_filter: a single season_id to restrict to, or None for every season
        each team carries. Events are always fetched ONE SEASON AT A TIME so
        each competition gets its own incremental cutoff - a team-wide cutoff
        takes the latest date across all competitions and silently skips
        anything older that is still missing.

    mode: "incremental" (since each (team, season)'s own last game),
        "full" (re-download the whole season), or "range" (date_from/date_to).

    Re-downloading is safe at any scope: upsert_events_to_motherduck deletes
    only the (gameId, teamId) pairs present in the incoming file, so a
    season-scoped refresh cannot touch another season's rows.
    """
    session = create_session(st.session_state["cookies"])
    progress = st.progress(0)
    status = st.empty()
    results = []
    n = len(teams_to_download)
    last_updated = load_last_updated()
    minutes_lu = load_minutes_last_updated()

    con = None
    last_dates = {}
    if not download_only:
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        if mode == "incremental" and do_events:
            last_dates = get_team_season_last_dates(con)

    if download_only:
        os.makedirs(TEST_DOWNLOAD_DIR, exist_ok=True)

    def _seasons_for(team):
        if season_filter:
            return [season_filter] if season_filter in team["season_ids"] else []
        return list(team["season_ids"])

    for i, team in enumerate(teams_to_download):
        # ── Event log, one request per (team, season) ──────────────────────
        if do_events:
            for season_id in _seasons_for(team):
                sl = _season_label(season_id)
                status.text(
                    f"Downloading events: {team['name']} — {sl}... ({i+1}/{n})"
                )
                tmp_path = None
                try:
                    since = until = None
                    if mode == "incremental":
                        last = last_dates.get((team["team_id"], season_id))
                        if last:
                            since = str(
                                (datetime.strptime(str(last), "%Y-%m-%d")
                                 - timedelta(days=1)).date()
                            )
                    elif mode == "range":
                        since, until = date_from, date_to

                    if download_only:
                        save_path = os.path.join(
                            TEST_DOWNLOAD_DIR, f"{team['abbrev']}_{season_id[:8]}.csv"
                        )
                        rows, _ = download_event_log(
                            session, team["team_id"], [season_id], save_path,
                            since_date=since, until_date=until,
                        )
                        results.append((True, f"{team['name']} — {sl}: {rows:,} rows saved to data/test_downloads/"))
                        continue

                    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                        tmp_path = tmp.name
                    rows, _ = download_event_log(
                        session, team["team_id"], [season_id], tmp_path,
                        since_date=since, until_date=until,
                    )
                    upsert_events_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)
                    last_updated[team["team_id"]] = datetime.now().strftime("%b %d, %Y  %H:%M")
                    scope = f"since {since}" if since else "full season"
                    if until:
                        scope += f" to {until}"
                    results.append((True, f"{team['name']} — {sl}: {rows:,} rows upserted ({scope})"))
                except Exception as e:
                    results.append((False, f"{team['name']} — {sl}: {e}"))
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)

        # ── Minutes & cards ────────────────────────────────────────────────
        # Sent as one request covering the relevant seasons: this endpoint
        # returns season totals per player-game rather than an event stream,
        # so there is no incremental cutoff to get wrong.
        if do_minutes and not download_only:
            status.text(f"Downloading minutes: {team['name']}... ({i+1}/{n})")
            tmp_path = None
            try:
                minutes_seasons = _seasons_for(team)
                if not minutes_seasons:
                    raise ValueError("no matching season for this team")
                with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                    tmp_path = tmp.name
                rows, _ = download_minutes_and_cards(
                    session, team["team_id"], minutes_seasons, tmp_path
                )
                upsert_minutes_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)
                minutes_lu[team["team_id"]] = datetime.now().strftime("%b %d, %Y  %H:%M")
                results.append((True, f"{team['name']} minutes: {rows:,} player-game rows upserted"))
            except Exception as e:
                results.append((False, f"{team['name']} minutes: {e}"))
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        progress.progress((i + 1) / n)

    # ── API-Football: red cards + own goals for chart annotations ──────────
    if not download_only and do_events and fetch_player_minutes and apifootball_configured and con:
        missing = get_games_missing_fixture_data(con)
        if missing:
            pm_matched = 0
            pm_failed_rows: list[dict] = []  # collected for UI surfacing
            for j, game in enumerate(missing):
                status.text(f"Fetching match data (API-Football)... ({j+1}/{len(missing)})")
                game_status = fetch_and_store_fixture_data(
                    api_key=API_FOOTBALL_KEY,
                    token=MOTHERDUCK_TOKEN,
                    game_id=game["gameId"],
                    date=game["Date"],
                    home=game["homeTeam"],
                    away=game["awayTeam"],
                    con=con,
                    season_id=game.get("seasonId"),
                )
                if game_status == "matched":
                    pm_matched += 1
                else:
                    pm_failed_rows.append({
                        "Date": str(game["Date"])[:10],
                        "Home": game["homeTeam"],
                        "Away": game["awayTeam"],
                        "Status": game_status,
                    })
            pm_failed = len(pm_failed_rows)
            results.append((True, f"API-Football: {pm_matched} games fetched"
                            + (f", {pm_failed} not found/failed" if pm_failed else "")))
            # Surface the specific failures prominently so the user can
            # see which TruMedia team names need overrides added to
            # TRUMEDIA_TO_API_NAME in downloader.py. Without this, the
            # aggregate "X not found/failed" message hides the names
            # and own-goals/cards/minutes silently miss those games.
            if pm_failed_rows:
                st.warning(
                    f"{pm_failed} game(s) failed to match an API-Football "
                    "fixture. Listed below - if a name lookup is needed, "
                    "search API-Football for the canonical name and add "
                    "an entry to `TRUMEDIA_TO_API_NAME` in "
                    "`data_manager/downloader.py`. The next run will retry."
                )
                st.dataframe(pm_failed_rows, hide_index=True, use_container_width=True)

    if con:
        con.close()
    if not download_only:
        if do_events:
            save_last_updated(last_updated)
        if do_minutes:
            save_minutes_last_updated(minutes_lu)
    status.empty()
    progress.empty()
    st.session_state["download_results"] = results
    st.rerun()


# Display any results from last run
if "download_results" in st.session_state:
    for _success, _message in st.session_state.pop("download_results"):
        if _success:
            st.success(_message)
        else:
            st.error(_message)

last_updated_data = load_last_updated()
minutes_last_updated_data = load_minutes_last_updated()

# A download answers three independent questions - which season, which teams,
# how much - so the UI asks them in that order. Season comes first because
# after a rollover "refresh Premier League 2026/27" is the actual intent, and
# because scoping by season keeps every request narrow.
#
# The per-league freshness tables that used to sit here now live on the Health
# page: they are a status question, and interleaving them with the selection
# controls was most of what made this page hard to drive.

# Every registered season is listed, including ones no team carries yet. A
# season added through the Add Season form starts with zero teams - listing
# only seasons in use would make it vanish the moment it was created, with
# nothing on screen explaining why.
_teams_per_season = {}
for _t in config["teams"]:
    for _s in _t["season_ids"]:
        _teams_per_season[_s] = _teams_per_season.get(_s, 0) + 1

_season_ids_in_use = list(config.get("seasons", {}))
for _s in _teams_per_season:
    if _s not in _season_ids_in_use:
        _season_ids_in_use.append(_s)
_season_ids_in_use.sort(key=lambda s: _season_label(s))


ALL_SEASONS = "All seasons"


def _season_option_label(season_id):
    if season_id == ALL_SEASONS:
        return season_id
    n = _teams_per_season.get(season_id, 0)
    return _season_label(season_id) + ("" if n else "  ·  no teams yet")


_season_choice = st.selectbox(
    "Season",
    options=[ALL_SEASONS] + _season_ids_in_use,
    format_func=_season_option_label,
    help="Scopes the whole download. 'All seasons' fetches every season each "
         "selected team carries, one request per season.",
)
_season_filter = None if _season_choice == ALL_SEASONS else _season_choice

if _season_filter:
    _eligible = [t for t in config["teams"] if _season_filter in t["season_ids"]]
else:
    _eligible = list(config["teams"])

_stale_cutoff = datetime.now() - timedelta(days=7)


def _is_stale(team):
    val = last_updated_data.get(team["team_id"])
    if not val:
        return True
    try:
        return datetime.strptime(str(val).strip(), "%b %d, %Y  %H:%M") < _stale_cutoff
    except ValueError:
        return True


if _season_filter and not _eligible:
    # Registered but no squad attached yet - the state every freshly added
    # season starts in. Say so here rather than showing an empty team list.
    st.warning(
        f"**{_season_label(_season_filter)} has no teams yet.** The season is "
        f"registered, but no team carries its id, so there is nothing to "
        f"download. Open **Discover Teams**, scan this season, and apply the "
        f"diff — that pulls the squad list from TruMedia and handles "
        f"promotions and relegations."
    )
else:
    _n_stale = sum(1 for t in _eligible if _is_stale(t))
    st.caption(
        f"{len(_eligible)} teams · {_n_stale} not downloaded in the last 7 days"
    )

# ── Teams ─────────────────────────────────────────────────────────────────────
_eligible_names = sorted(t["name"] for t in _eligible)
_sel_key = f"team_sel_{_season_choice}"

_tcol, _bcol1, _bcol2 = st.columns([6, 1, 1])
with _tcol:
    _selected_names = st.multiselect(
        "Teams", options=_eligible_names, key=_sel_key,
        placeholder="Leave empty to download every team in scope",
    )
with _bcol1:
    st.write("")
    st.button("All", key=f"all_{_season_choice}",
              on_click=lambda k=_sel_key, n=_eligible_names:
                  st.session_state.update({k: n}))
with _bcol2:
    st.write("")
    st.button("None", key=f"none_{_season_choice}",
              on_click=lambda k=_sel_key: st.session_state.update({k: []}))

_name_to_team = {t["name"]: t for t in config["teams"]}
_selected_teams = [_name_to_team[n] for n in _selected_names if n in _name_to_team]
_targets = _selected_teams or _eligible

# ── Mode ──────────────────────────────────────────────────────────────────────
_mode_col, _data_col = st.columns([3, 2])
with _mode_col:
    _mode_label = st.radio(
        "Mode",
        ["Incremental", "Full re-download", "Date range"],
        horizontal=False,
        help="Incremental picks up from each team's last game IN THE SELECTED "
             "SEASON, so competitions running concurrently don't shadow each "
             "other. Re-downloading never touches other seasons' rows.",
    )
    _mode = {"Incremental": "incremental",
             "Full re-download": "full",
             "Date range": "range"}[_mode_label]

    _date_from = _date_to = None
    if _mode == "range":
        _d1, _d2 = st.columns(2)
        _date_from = str(_d1.date_input("From", value=date.today() - timedelta(days=30)))
        _date_to = str(_d2.date_input("To", value=date.today()))

with _data_col:
    _do_events = st.checkbox("Event logs", value=True)
    _do_minutes = st.checkbox("Minutes & cards", value=True)
    _fetch_pm = st.checkbox(
        "Red cards & own goals (API-Football)",
        value=True,
        disabled=not apifootball_configured or not _do_events,
        help="Fetched after events, for chart annotations."
             + ("" if apifootball_configured else " (API_FOOTBALL_KEY not configured)"),
    )
    _download_only = st.checkbox(
        "Save to file only (skip DB upsert)", value=False, disabled=not _do_events,
        help="Writes event CSVs to data/test_downloads/ for inspection.",
    )

# ── Summary + go ──────────────────────────────────────────────────────────────
if _season_filter:
    _n_requests = len(_targets)
    _scope = _season_label(_season_filter)
else:
    _n_requests = sum(len(t["season_ids"]) for t in _targets)
    _scope = "all seasons"

_bits = []
if _do_events:
    _bits.append(f"events ({_n_requests} request{'s' if _n_requests != 1 else ''})")
if _do_minutes and not _download_only:
    _bits.append("minutes & cards")
_what = " + ".join(_bits) if _bits else "nothing selected"

_who = (f"{len(_selected_teams)} selected team{'s' if len(_selected_teams) != 1 else ''}"
        if _selected_teams else f"all {len(_targets)} teams")

st.info(f"**{_who} × {_scope}** — {_mode_label.lower()} — {_what}")

if not _selected_teams and len(_targets) > 60:
    st.warning(
        f"No teams selected, so this covers all {len(_targets)} in scope "
        f"({_n_requests} requests). Pick teams above to narrow it."
    )

if st.button(
    "Download", type="primary",
    disabled=not authenticated
             or (not motherduck_configured and not _download_only)
             or not _targets
             or (not _do_events and not _do_minutes),
):
    _run_downloads(
        _targets,
        season_filter=_season_filter,
        mode=_mode,
        date_from=_date_from,
        date_to=_date_to,
        download_only=_download_only,
        do_events=_do_events,
        do_minutes=_do_minutes and not _download_only,
        fetch_player_minutes=_fetch_pm and not _download_only,
    )

# ── Sequence Model ────────────────────────────────────────────────────────────
st.divider()
st.header("Sequence Model")
st.caption("Extract features from MotherDuck and run model inference. Uses Python 3.12 + PyTorch.")

_ROOT_DIR = os.path.dirname(BASE_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
from event_db.seasons import SEASONS

_EVENT_DB_DIR = os.path.join(os.path.dirname(BASE_DIR), "event_db")
_MODEL_SUFFIX  = "_v21a"
_MODEL_CKPT    = "One_Offs/seq_nn_model_v21a.pt"

# Status table — which seasons have data / events parquets
def _season_display_label(yr, info):
    lbl = info["label"]
    return lbl if lbl.startswith("Championship") else f"Big 5 {lbl}"

_status_rows = []
for _yr, _info in SEASONS.items():
    _data_path = os.path.join(_EVENT_DB_DIR, f"seq_nn_data_{_yr}{_MODEL_SUFFIX}.parquet")
    _events_path = os.path.join(_EVENT_DB_DIR, f"seq_nn_events_{_yr}{_MODEL_SUFFIX}.parquet")
    _data_str = (datetime.fromtimestamp(os.path.getmtime(_data_path)).strftime("%b %d  %H:%M")
                 if os.path.exists(_data_path) else "—")
    _events_str = (datetime.fromtimestamp(os.path.getmtime(_events_path)).strftime("%b %d  %H:%M")
                   if os.path.exists(_events_path) else "—")
    _status_rows.append({
        "Season": _season_display_label(_yr, _info),
        "Key": _yr,
        f"Data parquet ({_MODEL_SUFFIX})": _data_str,
        f"Events parquet ({_MODEL_SUFFIX})": _events_str,
    })

def _color_missing(val):
    return "color: #FF6B6B" if val == "—" else ""

_status_df = pd.DataFrame(_status_rows).drop(columns=["Key"])
st.dataframe(
    _status_df.style.map(_color_missing,
                         subset=[f"Data parquet ({_MODEL_SUFFIX})", f"Events parquet ({_MODEL_SUFFIX})"]),
    hide_index=True,
    use_container_width=True,
)

_seq_col1, _seq_col2, _seq_col3 = st.columns(3)
with _seq_col1:
    _season_labels = {r["Key"]: r["Season"] for r in _status_rows}
    _selected_season_label = st.selectbox(
        "Season", options=list(_season_labels.values()), key="seq_season"
    )
    _selected_season_yr = next(k for k, v in _season_labels.items() if v == _selected_season_label)
with _seq_col2:
    _run_extract = st.checkbox("Extract (MotherDuck → parquet)", value=True, key="seq_extract")
    _run_infer = st.checkbox("Infer (parquet → events + deltas)", value=True, key="seq_infer")
    _run_upsert = st.checkbox("Upsert (events → MotherDuck model_delta)", value=True, key="seq_upsert")
with _seq_col3:
    _local_db = st.checkbox(
        "Use local soccer.duckdb instead of MotherDuck",
        value=False, key="seq_local",
        help="Only applies to extract step. Useful if MotherDuck is unavailable.",
        disabled=not _run_extract,
    )
    _incremental = st.checkbox(
        "Incremental (new games only)",
        value=True, key="seq_incremental",
        help="Skip games already scored in model_delta. Recommended for routine updates.",
        disabled=not _run_extract or _local_db,
    )

if st.button("Run", type="primary", key="seq_run",
             disabled=not _run_extract and not _run_infer and not _run_upsert):
    _root = os.path.dirname(BASE_DIR)
    _output = st.empty()
    _log_lines = []

    def _stream_cmd(cmd, label):
        _log_lines.append(f"\n$ {' '.join(cmd)}\n")
        _output.code("".join(_log_lines))
        proc = subprocess.Popen(
            cmd, cwd=_root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            _log_lines.append(line)
            _output.code("".join(_log_lines))
        proc.wait()
        return proc.returncode

    _ok = True
    if _run_extract:
        _extract_cmd = [
            "py", "-3.12", "event_db/extract.py",
            "--season-year", _selected_season_yr,
            "--output-suffix", _MODEL_SUFFIX,
        ]
        if not _local_db:
            _extract_cmd.append("--motherduck")
        if _incremental and not _local_db:
            _extract_cmd.append("--incremental")
        _rc = _stream_cmd(_extract_cmd, "Extract")
        if _rc != 0:
            st.error("Extract failed — see output above.")
            _ok = False

    if _ok and _run_infer:
        _infer_cmd = [
            "py", "-3.12", "event_db/infer.py",
            "--season-year", _selected_season_yr,
            "--data-suffix", _MODEL_SUFFIX,
            "--suffix", _MODEL_SUFFIX,
            "--model-path", _MODEL_CKPT,
        ]
        _rc = _stream_cmd(_infer_cmd, "Infer")
        if _rc != 0:
            st.error("Infer failed — see output above.")
            _ok = False

    if _ok and _run_upsert:
        _upsert_cmd = [
            "py", "-3.12", "event_db/upsert_model_delta.py",
            "--season-year", _selected_season_yr,
            "--suffix", _MODEL_SUFFIX,
        ]
        _rc = _stream_cmd(_upsert_cmd, "Upsert")
        if _rc != 0:
            st.error("Upsert failed — see output above.")
        else:
            st.success("Done.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption("CBS Sports | TruMedia Data Manager")
