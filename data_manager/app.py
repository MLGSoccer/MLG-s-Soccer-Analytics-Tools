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
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import (
    parse_cookies_from_curl, create_session,
    download_player_pool, upload_to_supabase, load_secrets,
    download_event_log, upsert_events_to_motherduck,
    get_motherduck_connection, get_all_team_last_dates,
    backfill_season_ids,
    fetch_and_store_fixture_data, get_games_missing_fixture_data,
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

with open(CONFIG_PATH) as f:
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


def load_last_updated():
    """Load the last-updated timestamps for event log downloads."""
    if os.path.exists(LAST_UPDATED_PATH):
        with open(LAST_UPDATED_PATH) as f:
            return json.load(f)
    return {}


def save_last_updated(data):
    """Persist the last-updated timestamps."""
    with open(LAST_UPDATED_PATH, 'w') as f:
        json.dump(data, f, indent=2)


# ── League grouping (for Event Log UI) ───────────────────────────────────────
_secondary = set(config.get("secondary_seasons", []))
_season_names = config.get("seasons", {})

leagues = {}  # league_name -> [team_dict, ...]
for _team in config["teams"]:
    _primary = next((s for s in _team["season_ids"] if s not in _secondary), None)
    if _primary:
        _league_name = _season_names.get(_primary, "Other")
    else:
        _first_sec = next((s for s in _team["season_ids"] if s in _secondary), None)
        _league_name = _season_names.get(_first_sec, "Other") if _first_sec else "Other"
    leagues.setdefault(_league_name, []).append(_team)

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
3. On any TruMedia page, trigger a CSV export
4. Find the **dp-proxy-export** request in the Network tab
5. Right-click it → **Copy** → **Copy as cURL**
6. Paste the result in the box below
""")

col_input, col_status = st.columns([3, 1])

with col_input:
    curl_input = st.text_area(
        "Paste cURL command",
        height=80,
        placeholder='curl "https://cbssports.opta.trumediasports.com/dp-proxy-export" ...',
    )

with col_status:
    st.write("")
    st.write("")
    if curl_input:
        try:
            cookies = parse_cookies_from_curl(curl_input)
            st.session_state["cookies"] = cookies
            st.success("Authenticated")
        except ValueError as e:
            st.session_state.pop("cookies", None)
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

# ── Event Log Downloads ────────────────────────────────────────────────────────
st.header("Event Log Downloads")
st.caption("Downloads event-by-event data for each team and upserts to MotherDuck.")


TEST_DOWNLOAD_DIR = os.path.join(BASE_DIR, "data", "test_downloads")


def _run_event_log_downloads(teams_to_download, incremental=True, download_only=False,
                             fetch_player_minutes=True):
    session = create_session(st.session_state["cookies"])
    progress = st.progress(0)
    status = st.empty()
    results = []
    n = len(teams_to_download)
    last_updated = load_last_updated()

    con = None
    last_dates = {}
    if not download_only:
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        if incremental:
            last_dates = get_all_team_last_dates(con)

    if download_only:
        os.makedirs(TEST_DOWNLOAD_DIR, exist_ok=True)

    for i, team in enumerate(teams_to_download):
        status.text(f"Downloading {team['name']}... ({i+1}/{n})")
        tmp_path = None
        try:
            since = None
            if incremental and team["team_id"] in last_dates:
                last = last_dates[team["team_id"]]
                since = str((datetime.strptime(last, "%Y-%m-%d") - timedelta(days=1)).date())

            if download_only:
                save_path = os.path.join(TEST_DOWNLOAD_DIR, f"{team['abbrev']}.csv")
                rows, _ = download_event_log(
                    session, team["team_id"], team["season_ids"], save_path, since_date=since
                )
                results.append((True, f"{team['name']}: {rows:,} rows saved to data/test_downloads/{team['abbrev']}.csv"))
            else:
                with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                    tmp_path = tmp.name

                rows, _ = download_event_log(
                    session, team["team_id"], team["season_ids"], tmp_path, since_date=since
                )
                upsert_events_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)

                last_updated[team["abbrev"]] = datetime.now().strftime("%b %d, %Y  %H:%M")
                label = f"(since {since})" if since else "(full)"
                results.append((True, f"{team['name']}: {rows:,} rows upserted {label}"))
        except Exception as e:
            results.append((False, f"{team['name']}: {e}"))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        progress.progress((i + 1) / n)

    # After all team downloads, fetch player minutes/own goals/cards for any new games
    if not download_only and fetch_player_minutes and apifootball_configured and con:
        missing = get_games_missing_fixture_data(con)
        if missing:
            pm_matched = 0
            pm_failed = 0
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
                    pm_failed += 1
            results.append((True, f"API-Football: {pm_matched} games fetched"
                            + (f", {pm_failed} not found/failed" if pm_failed else "")))

    if con:
        con.close()
    if not download_only:
        save_last_updated(last_updated)
    status.empty()
    progress.empty()
    st.session_state["event_log_results"] = results
    st.rerun()


# Display any results from last run
if "event_log_results" in st.session_state:
    for _success, _message in st.session_state.pop("event_log_results"):
        if _success:
            st.success(_message)
        else:
            st.error(_message)

last_updated_data = load_last_updated()
_stale_cutoff = datetime.now() - timedelta(days=7)

# Per-league expanders
for _league_name, _teams in leagues.items():
    with st.expander(f"{_league_name}  ({len(_teams)} teams)"):
        import pandas as pd
        _rows = []
        for t in _teams:
            _last = last_updated_data.get(t["abbrev"], "Never")
            _rows.append({"Team": t["name"], "Last Downloaded": _last})

        def _highlight_stale(val):
            if val == "Never":
                return "color: #FF6B6B"
            try:
                if datetime.strptime(val.strip(), "%b %d, %Y  %H:%M") < _stale_cutoff:
                    return "color: #FF6B6B"
            except ValueError:
                pass
            return ""

        _df = pd.DataFrame(_rows)
        st.dataframe(
            _df.style.map(_highlight_stale, subset=["Last Downloaded"]),
            hide_index=True,
            use_container_width=True,
        )

        _col1, _col2 = st.columns([4, 1])
        with _col1:
            st.multiselect("Teams", options=[t["name"] for t in _teams],
                           key=f"sel_{_league_name}")
        with _col2:
            st.write("")
            st.button(
                "Select All",
                key=f"all_{_league_name}",
                on_click=lambda names=_teams, ln=_league_name: st.session_state.update(
                    {f"sel_{ln}": [t["name"] for t in names]}
                ),
            )

# Build full selected list
_name_to_team = {t["name"]: t for t in config["teams"]}
_selected_teams = []
for _league_name in leagues:
    for _name in st.session_state.get(f"sel_{_league_name}", []):
        if _name in _name_to_team:
            _selected_teams.append(_name_to_team[_name])

_total = len(_selected_teams)
st.write(f"**{_total} team{'s' if _total != 1 else ''} selected**")

_incremental = st.checkbox("Incremental — only download since last update", value=True)
_download_only = st.checkbox(
    "Download to file only — skip database upsert",
    value=False,
    help=f"Saves CSVs to data/test_downloads/ for inspection. Does not update last-downloaded timestamps."
)
_fetch_pm = st.checkbox(
    "Fetch player minutes, own goals & cards (API-Football)",
    value=True,
    disabled=not apifootball_configured,
    help="After downloading, fetch lineups + match events for new games to compute player minutes, own goals, and cards."
         + ("" if apifootball_configured else " (API_FOOTBALL_KEY not configured)"),
)

_col_a, _col_b = st.columns([1, 1])
with _col_a:
    _dl_selected = st.button("Download Selected", type="primary",
                             disabled=not authenticated or (not motherduck_configured and not _download_only) or _total == 0)
with _col_b:
    _dl_all = st.button("Download All Teams",
                        disabled=not authenticated or (not motherduck_configured and not _download_only))

if _dl_selected:
    _run_event_log_downloads(_selected_teams, incremental=_incremental, download_only=_download_only,
                             fetch_player_minutes=_fetch_pm and not _download_only)
if _dl_all:
    _run_event_log_downloads(list(_name_to_team.values()), incremental=_incremental, download_only=_download_only,
                             fetch_player_minutes=_fetch_pm and not _download_only)

# ── Database Maintenance ──────────────────────────────────────────────────────
st.divider()
st.header("Database Maintenance")

st.caption("Backfill season IDs for existing games that predate season tracking.")
if st.button("Backfill Season IDs", disabled=not motherduck_configured):
    try:
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        updated, skipped, skipped_details = backfill_season_ids(con, config)
        con.close()
        st.session_state["backfill_result"] = (True, updated, skipped, skipped_details)
    except Exception as e:
        st.session_state["backfill_result"] = (False, str(e), 0, [])
    st.rerun()

if "backfill_result" in st.session_state:
    success, val1, val2, details = st.session_state.pop("backfill_result")
    if success:
        st.success(f"Done — {val1} games updated, {val2} skipped.")
        if details:
            import pandas as pd
            st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)
    else:
        st.error(f"Backfill failed: {val1}")

# ── Fix Missing API-Football Data ─────────────────────────────────────────────
st.divider()
st.header("Fix Missing API-Football Data")
st.caption("Games with no player minutes, own goals, or cards data in the database.")

if not motherduck_configured or not apifootball_configured:
    st.info("Requires both MotherDuck and API-Football to be configured.")
else:
    # League filter — uses the same season name list as the rest of the app
    _pm_league_filter = st.multiselect(
        "Filter by league (leave blank for all)",
        options=sorted(_season_names.values()),
        key="pm_league_filter",
    )
    _pm_season_ids = (
        [sid for sid, name in _season_names.items() if name in _pm_league_filter]
        if _pm_league_filter else None
    )

    def _query_missing_and_failed(season_ids):
        """Return (missing_list, failed_list) filtered by season_ids (None = all)."""
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        missing = get_games_missing_fixture_data(con, season_ids=season_ids)
        if season_ids:
            _ph = ",".join("?" * len(season_ids))
            failed_rows = con.execute(f"""
                SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, gf.fetch_status, g.seasonId
                FROM games g
                JOIN game_fixtures gf ON g.gameId = gf.gameId
                WHERE gf.fetch_status != 'matched'
                  AND g.seasonId IN ({_ph})
                ORDER BY g.Date DESC
            """, season_ids).fetchall()
        else:
            failed_rows = con.execute("""
                SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, gf.fetch_status, g.seasonId
                FROM games g
                JOIN game_fixtures gf ON g.gameId = gf.gameId
                WHERE gf.fetch_status != 'matched'
                ORDER BY g.Date DESC
            """).fetchall()
        con.close()
        failed = [
            {"gameId": r[0], "Date": r[1], "homeTeam": r[2], "awayTeam": r[3],
             "status": r[4], "seasonId": r[5]}
            for r in failed_rows
        ]
        return missing, failed

    def _run_backfill(games_to_fetch, label=""):
        """Fetch player minutes for a list of game dicts. Shows inline progress."""
        total = len(games_to_fetch)
        if total == 0:
            st.info("Nothing to fetch.")
            return
        progress = st.progress(0)
        status = st.empty()
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        pm_matched = pm_not_found = pm_errors = 0
        for i, g in enumerate(games_to_fetch, 1):
            status.text(f"{label}Fetching {i}/{total}:  {g['homeTeam']} vs {g['awayTeam']}  ({g['Date']})")
            s = fetch_and_store_fixture_data(
                api_key=API_FOOTBALL_KEY, token=MOTHERDUCK_TOKEN,
                game_id=g["gameId"], date=g["Date"],
                home=g["homeTeam"], away=g["awayTeam"], con=con,
                season_id=g.get("seasonId"),
                fixture_id=g.get("fixture_id"),
            )
            if s == "matched":
                pm_matched += 1
            elif s == "not_found":
                pm_not_found += 1
            else:
                pm_errors += 1
            progress.progress(i / total)
        con.close()
        status.empty()
        progress.empty()
        parts = [f"{pm_matched} matched"]
        if pm_not_found:
            parts.append(f"{pm_not_found} not found")
        if pm_errors:
            parts.append(f"{pm_errors} errors")
        st.success(f"Done — {', '.join(parts)}")
        st.session_state.pop("missing_games", None)
        st.session_state.pop("failed_games", None)

    def _query_matched(season_ids):
        """Return all games already matched in game_fixtures (to allow force re-fetch).
        Includes the stored fixture_id so the re-fetch can skip the API lookup."""
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        if season_ids:
            _ph = ",".join("?" * len(season_ids))
            rows = con.execute(f"""
                SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, g.seasonId, gf.fixture_id
                FROM games g
                JOIN game_fixtures gf ON g.gameId = gf.gameId
                WHERE gf.fetch_status = 'matched'
                  AND g.seasonId IN ({_ph})
                ORDER BY g.Date DESC
            """, season_ids).fetchall()
        else:
            rows = con.execute("""
                SELECT g.gameId, g.Date, g.homeTeam, g.awayTeam, g.seasonId, gf.fixture_id
                FROM games g
                JOIN game_fixtures gf ON g.gameId = gf.gameId
                WHERE gf.fetch_status = 'matched'
                ORDER BY g.Date DESC
            """).fetchall()
        con.close()
        return [
            {"gameId": r[0], "Date": r[1], "homeTeam": r[2], "awayTeam": r[3],
             "seasonId": r[4], "fixture_id": r[5]}
            for r in rows
        ]

    _btn_col1, _btn_col2, _btn_col3 = st.columns([1, 1, 1])
    with _btn_col1:
        if st.button("Check missing games", key="check_missing"):
            missing, failed = _query_missing_and_failed(_pm_season_ids)
            st.session_state["missing_games"] = missing
            st.session_state["failed_games"] = failed
    with _btn_col2:
        _fetch_label = (
            f"Fetch for {', '.join(_pm_league_filter)}" if _pm_league_filter else "Fetch All Missing"
        )
        if st.button(_fetch_label, type="primary", key="fetch_missing_now"):
            missing, failed = _query_missing_and_failed(_pm_season_ids)
            all_games = missing + [
                {"gameId": g["gameId"], "Date": g["Date"],
                 "homeTeam": g["homeTeam"], "awayTeam": g["awayTeam"]}
                for g in failed
            ]
            _run_backfill(all_games)
            st.rerun()
    with _btn_col3:
        _refetch_label = (
            f"Re-fetch Matched ({', '.join(_pm_league_filter)})" if _pm_league_filter else "Re-fetch All Matched"
        )
        if st.button(_refetch_label, key="refetch_matched_now",
                     help="Re-download API-Football data for already-matched games (e.g. to backfill cards)"):
            matched = _query_matched(_pm_season_ids)
            _run_backfill(matched, label="Re-fetching: ")
            st.rerun()

    if "missing_games" in st.session_state:
        import pandas as pd
        missing = st.session_state["missing_games"]
        failed = st.session_state.get("failed_games", [])

        st.write(f"**{len(missing)}** games never attempted  •  **{len(failed)}** previously failed")

        if missing or failed:
            retry_col, _ = st.columns([1, 3])
            with retry_col:
                if st.button("Fetch Shown Games", type="primary", key="retry_all_missing"):
                    all_games = missing + [
                        {"gameId": g["gameId"], "Date": g["Date"],
                         "homeTeam": g["homeTeam"], "awayTeam": g["awayTeam"]}
                        for g in failed
                    ]
                    _run_backfill(all_games)
                    st.rerun()

            if missing:
                st.subheader("Never attempted")
                st.dataframe(
                    pd.DataFrame([{
                        "League": _season_names.get(g.get("seasonId"), "Unknown"),
                        "Date": g["Date"],
                        "Home": g["homeTeam"],
                        "Away": g["awayTeam"],
                    } for g in missing]),
                    hide_index=True, use_container_width=True,
                )
            if failed:
                st.subheader("Previously failed")
                st.dataframe(
                    pd.DataFrame([{
                        "League": _season_names.get(g.get("seasonId"), "Unknown"),
                        "Date": g["Date"],
                        "Home": g["homeTeam"],
                        "Away": g["awayTeam"],
                        "Status": g["status"],
                    } for g in failed]),
                    hide_index=True, use_container_width=True,
                )

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption("CBS Sports | TruMedia Data Manager")
