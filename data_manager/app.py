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
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import (
    parse_cookies_from_curl, create_session, probe_endpoint_health,
    download_player_pool, upload_to_supabase, load_secrets,
    download_event_log, upsert_events_to_motherduck,
    download_minutes_and_cards, upsert_minutes_to_motherduck,
    get_motherduck_connection, get_all_team_last_dates,
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
MINUTES_LAST_UPDATED_PATH = os.path.join(BASE_DIR, "data", "minutes_last_updated.json")

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
    if os.path.exists(LAST_UPDATED_PATH):
        with open(LAST_UPDATED_PATH) as f:
            return json.load(f)
    return {}


def save_last_updated(data):
    with open(LAST_UPDATED_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def load_minutes_last_updated():
    if os.path.exists(MINUTES_LAST_UPDATED_PATH):
        with open(MINUTES_LAST_UPDATED_PATH) as f:
            return json.load(f)
    return {}


def save_minutes_last_updated(data):
    with open(MINUTES_LAST_UPDATED_PATH, 'w') as f:
        json.dump(data, f, indent=2)


# ── League grouping ───────────────────────────────────────────────────────────
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


def _run_downloads(teams_to_download, incremental=True, download_only=False,
                   do_events=True, do_minutes=True, fetch_player_minutes=True):
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
        if incremental and do_events:
            last_dates = get_all_team_last_dates(con)

    if download_only:
        os.makedirs(TEST_DOWNLOAD_DIR, exist_ok=True)

    for i, team in enumerate(teams_to_download):
        # ── Event log ──────────────────────────────────────────────────────
        if do_events:
            status.text(f"Downloading events: {team['name']}... ({i+1}/{n})")
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
                    results.append((True, f"{team['name']} events: {rows:,} rows saved to data/test_downloads/{team['abbrev']}.csv"))
                else:
                    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                        tmp_path = tmp.name
                    rows, _ = download_event_log(
                        session, team["team_id"], team["season_ids"], tmp_path, since_date=since
                    )
                    upsert_events_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)
                    last_updated[team["abbrev"]] = datetime.now().strftime("%b %d, %Y  %H:%M")
                    label = f"(since {since})" if since else "(full)"
                    results.append((True, f"{team['name']} events: {rows:,} rows upserted {label}"))
            except Exception as e:
                results.append((False, f"{team['name']} events: {e}"))
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        # ── Minutes & cards ────────────────────────────────────────────────
        if do_minutes and not download_only:
            status.text(f"Downloading minutes: {team['name']}... ({i+1}/{n})")
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                    tmp_path = tmp.name
                rows, _ = download_minutes_and_cards(
                    session, team["team_id"], team["season_ids"], tmp_path
                )
                upsert_minutes_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)
                minutes_lu[team["abbrev"]] = datetime.now().strftime("%b %d, %Y  %H:%M")
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
_stale_cutoff = datetime.now() - timedelta(days=7)

# Per-league expanders
for _league_name, _teams in leagues.items():
    with st.expander(f"{_league_name}  ({len(_teams)} teams)"):
        _rows = []
        for t in _teams:
            _last_ev = last_updated_data.get(t["abbrev"], "Never")
            _last_min = minutes_last_updated_data.get(t["abbrev"], "Never")
            _rows.append({
                "Team": t["name"],
                "Events Last Downloaded": _last_ev,
                "Minutes Last Downloaded": _last_min,
            })

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
            _df.style.map(_highlight_stale, subset=["Events Last Downloaded", "Minutes Last Downloaded"]),
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

_opt_col1, _opt_col2, _opt_col3 = st.columns(3)
with _opt_col1:
    _do_events = st.checkbox("Download event logs", value=True)
    _incremental = st.checkbox("Incremental (events only)", value=True,
                               disabled=not _do_events,
                               help="Only download events since last update")
    _download_only = st.checkbox(
        "Save to file only (skip DB upsert)",
        value=False,
        disabled=not _do_events,
        help="Saves event CSVs to data/test_downloads/ for inspection"
    )
with _opt_col2:
    _do_minutes = st.checkbox("Download minutes & cards", value=True)
with _opt_col3:
    _fetch_pm = st.checkbox(
        "Fetch red cards & own goals (API-Football)",
        value=True,
        disabled=not apifootball_configured or not _do_events,
        help="After downloading events, fetch red card timing and own goals for chart annotations."
             + ("" if apifootball_configured else " (API_FOOTBALL_KEY not configured)"),
    )

_col_a, _col_b = st.columns([1, 1])
with _col_a:
    _dl_selected = st.button(
        "Download Selected", type="primary",
        disabled=not authenticated or (not motherduck_configured and not _download_only)
                 or _total == 0 or (not _do_events and not _do_minutes)
    )
with _col_b:
    _dl_all = st.button(
        "Download All Teams",
        disabled=not authenticated or (not motherduck_configured and not _download_only)
                 or (not _do_events and not _do_minutes)
    )

if _dl_selected:
    _run_downloads(
        _selected_teams,
        incremental=_incremental,
        download_only=_download_only,
        do_events=_do_events,
        do_minutes=_do_minutes and not _download_only,
        fetch_player_minutes=_fetch_pm and not _download_only,
    )
if _dl_all:
    _run_downloads(
        list(_name_to_team.values()),
        incremental=_incremental,
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
