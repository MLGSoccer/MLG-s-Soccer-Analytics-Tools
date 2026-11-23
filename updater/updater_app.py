"""
Data Updater - TruMedia -> MotherDuck + Supabase, the short version.

A second operator's copy of the routine update, for when the Data Manager's
owner is not around: paste a cURL, find the games that are missing, download
them, refresh the player pools. Nothing else - no Health page, no Discover
Teams, no seasons, no re-downloads. Those stay in the Data Manager.

It is the Campaign page's flow on the same downloader functions, with three
differences:

- Config comes from MotherDuck (`app_config`), never from config.json. The
  config.json in git is stale by design (it is never committed), and this app
  runs from git on Streamlit Cloud. It only READS the config.
- Scope is every season in the config, cut to the games of the last N days
  before the database is asked about them. Discovery is one TruMedia request
  however many seasons it covers; the work list is a MotherDuck query over
  the games it is given, so the date cut is what keeps that query small.
- It lives in its own folder on purpose. Streamlit turns a `pages/` folder
  beside the entry file into pages, so an entry file in data_manager/ would
  have shipped the whole Data Manager - Health, Discover Teams, Add Season -
  to whoever has this app's password.

Secrets (Streamlit Cloud -> app settings -> Secrets):
    UPDATER_PASSWORD, MOTHERDUCK_TOKEN, SUPABASE_URL, SUPABASE_KEY

Practice mode: set DATA_MANAGER_LOCAL_DB to a local DuckDB file and the games
are written there instead (the downloader honours it), and the player pools
are NOT uploaded to Supabase. The config is still read from MotherDuck.
"""
import hmac
import os
import sys
import tempfile
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "data_manager"))
sys.path.insert(0, ROOT)
import downloader as dl  # noqa: E402
from shared.config_store import read_config  # noqa: E402

st.set_page_config(page_title="Data Updater", page_icon="🔄", layout="centered")

LOOKBACK = {"Last 3 days": 3, "Last 7 days": 7, "Last 30 days": 30,
            "Whole season": None}
TODO_STATES = (dl.WORK_MISSING, dl.WORK_ONE_SIDED)
# Games per request. The Data Manager uses 20; measured 2026-11-23 on the same
# games, a 20-game batch is a 230 MB response peaking at ~600 MB of memory and
# took 47-173 s - the 173 s fits one 120 s read timeout plus a retry, and a
# batch that times out three times fails (one in five did, in the first
# practice run). Five games: 60 MB, ~250 MB peak, 13-22 s. Streamlit Cloud
# gives an app far less memory than a laptop, so the smaller batch is the safe
# one; it costs four times the requests, which a matchday update can afford.
BATCH_SIZE = 5
PRACTICE_DB = os.environ.get(dl.LOCAL_DB_ENV)


def _secret(name):
    try:
        return st.secrets.get(name)
    except Exception:          # no secrets file at all
        return None


# -- Password -----------------------------------------------------------------
# The whole app is one file, so this gate cannot be stepped around by opening a
# page's URL directly (the chart maker's can - see its memory note). It writes
# to the production database; the gate has to hold.
if not st.session_state.get("updater_ok"):
    st.title("Data Updater")
    expected = _secret("UPDATER_PASSWORD")
    if not expected:
        st.error("UPDATER_PASSWORD is not set in this app's secrets.")
        st.stop()
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if hmac.compare_digest(pwd.encode(), str(expected).encode()):
            st.session_state["updater_ok"] = True
            st.rerun()
        st.error("Incorrect password")
    st.stop()

TOKEN = _secret("MOTHERDUCK_TOKEN")
SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_KEY = _secret("SUPABASE_KEY")

st.title("Data Updater")
st.caption("Download the newest TruMedia games into the database, then "
           "refresh the player pools.")

if PRACTICE_DB:
    st.info(f"Practice mode: games are written to `{PRACTICE_DB}`, and the "
            f"player pools are not uploaded.")
if not TOKEN:
    st.error("MOTHERDUCK_TOKEN is not set in this app's secrets.")
    st.stop()


@st.cache_data(ttl=600, show_spinner=False)
def load_config(token):
    """The Data Manager's config, from its MotherDuck copy. Only read.

    NOT `read_only=True`, though that is the intent: DuckDB fixes a
    database's settings for the whole process at the first connection, so a
    read-only open here made the downloader's write connection fail with "a
    different configuration than existing connections" - every production
    download, caught on the first production run (2026-11-23). This opens
    exactly as get_motherduck_connection does.
    """
    con = duckdb.connect(f"md:{dl.MOTHERDUCK_DB}?motherduck_token={token}")
    try:
        return read_config(con)
    finally:
        con.close()


config = load_config(TOKEN)
if not config:
    st.error("Could not read the Data Manager's config from MotherDuck.")
    st.stop()
season_ids = list(config.get("seasons", {}))


# -- 1. Log in ----------------------------------------------------------------
st.header("1 · Log in to TruMedia")
with st.expander("How to copy the cURL", expanded="cookies" not in st.session_state):
    _safari, _mac_chrome, _win = st.tabs(
        ["Safari (Mac)", "Chrome or Edge (Mac)", "Chrome or Edge (Windows)"])
    _common = ("Type **dp-proxy** in the Network tab's filter box, right-click "
               "a **dp-proxy** row and choose ")
    _safari.markdown(
        "1. **Once only:** Safari menu → **Settings → Advanced**, tick "
        "**Show features for web developers**.\n"
        "2. Log in to TruMedia and open any player or team page.\n"
        "3. Press **⌥⌘I** (Option-Command-I), open the **Network** tab, then "
        "reload the page with **⌘R**.\n"
        f"4. {_common}**Copy as cURL**.")
    _mac_chrome.markdown(
        "1. Log in to TruMedia and open any player or team page.\n"
        "2. Press **⌥⌘I** (Option-Command-I), open the **Network** tab, then "
        "reload the page with **⌘R**.\n"
        f"3. {_common}**Copy → Copy as cURL**.")
    _win.markdown(
        "1. Log in to TruMedia and open any player or team page.\n"
        "2. Press **F12**, open the **Network** tab, then reload the page.\n"
        f"3. {_common}**Copy → Copy as cURL (cmd)**.")
    st.caption("Paste it below. It is your TruMedia login and keeps working "
               "for weeks, so don't send it to anyone.")
curl = st.text_area("cURL", height=100, label_visibility="collapsed",
                    placeholder="curl --url \"https://...\" ...")
if st.button("Connect", disabled=not curl.strip()):
    try:
        cookies = dl.parse_cookies_from_curl(curl)
    except ValueError as e:
        st.error(str(e))
        st.stop()
    with st.spinner("Checking the login..."):
        ok, msg = dl.probe_endpoint_health(dl.create_session(cookies), season_ids[0])
    if not ok:
        st.error(msg)
        st.stop()
    st.session_state["cookies"] = cookies
    st.session_state.pop("work", None)

if "cookies" not in st.session_state:
    st.stop()
st.success("Connected to TruMedia.")


def session():
    return dl.create_session(st.session_state["cookies"])


# -- 2. Find the games --------------------------------------------------------
st.header("2 · Find the missing games")
span = st.selectbox("Look at games from", list(LOOKBACK), index=1,
                    help="Only games in this window are checked. Use a longer "
                         "window if an update was missed.")

if st.button("Find games", type="primary"):
    with st.spinner("Asking TruMedia for the fixtures..."):
        try:
            fx = dl.discover_fixtures(session(), season_ids)
        except Exception as e:
            st.error(f"Could not get the fixtures: {e}")
            st.stop()
    days = LOOKBACK[span]
    if days is not None and not fx.empty:
        when = pd.to_datetime(fx["gameDate"], errors="coerce")
        fx = fx[when >= pd.Timestamp(datetime.now() - timedelta(days=days)).normalize()]
    if fx.empty:
        st.session_state["work"] = (fx, fx)
    else:
        with st.spinner("Checking which games the database already has..."):
            con = dl.get_motherduck_connection(TOKEN)
            try:
                work = dl.build_work_list(
                    con, fx, known_empty=config.get("known_empty_games", {}))
            finally:
                con.close()
        st.session_state["work"] = (fx, work)

# No st.stop() from here down: step 4 has to stay reachable whether or not a
# list has been found - a download clears the list, and stopping on "no list"
# made the pools step unreachable straight after the run it follows.
found = st.session_state.get("work")
fx, work = found if found is not None else (None, None)
todo = (work[work["state"].isin(TODO_STATES)]
        if work is not None and not work.empty else work)

if work is None:
    st.caption("Press Find games to see what is missing.")
elif todo.empty:
    st.success("The database already has every finished game in that window.")
else:
    st.write(f"**{len(todo):,} games to download.**")
    st.dataframe(todo[["gameDate", "homeTeam", "awayTeam"]]
                 .rename(columns={"gameDate": "Date", "homeTeam": "Home",
                                  "awayTeam": "Away"}),
                 hide_index=True, use_container_width=True)

    # -- 3. Download ----------------------------------------------------------
    st.header("3 · Download them")
    st.caption("Each game is saved whole. If this stops partway, find the "
               "games again and the finished ones will be gone from the list.")
    if st.button(f"Download {len(todo):,} games", type="primary"):
        bar = st.progress(0.0)
        problems = []

        def _progress(done, total, label, state, note):
            bar.progress(done / max(total, 1), text=f"{done:,} of {total:,} games")
            if note and not note.endswith("rows"):
                problems.append(note)

        with tempfile.TemporaryDirectory() as tmp:
            con = dl.get_motherduck_connection(TOKEN)
            try:
                written, failed, _ = dl.run_campaign(
                    session(), TOKEN, fx, work, tmp, season_ids,
                    states=TODO_STATES, con=con, progress=_progress,
                    batch_size=BATCH_SIZE)
            finally:
                con.close()
        bar.empty()
        if failed:
            st.error(f"Saved {written:,} games; {failed:,} failed. Press "
                     f"Find games and download again - the saved games drop "
                     f"off the list, and a second try usually works. If it "
                     f"keeps failing, connect again with a new cURL.")
            with st.expander("What went wrong"):
                for p in problems[:50]:
                    st.write(p)
        else:
            st.success(f"Saved {written:,} games.")
        st.session_state.pop("work", None)

# -- 4. Player pools ----------------------------------------------------------
st.header("4 · Refresh the player pools")
st.caption("Do this after downloading. The player charts read the pools, so "
           "new players stay hidden until they are refreshed.")
upload = not PRACTICE_DB
if upload and not (SUPABASE_URL and SUPABASE_KEY):
    # Without these a refresh "succeeds" into a temporary folder that is
    # deleted straight after, and the charts never see it.
    st.error("SUPABASE_URL and SUPABASE_KEY are not set in this app's secrets, "
             "so the pools cannot be uploaded.")
elif st.button("Refresh player pools"):
    with tempfile.TemporaryDirectory() as tmp:
        for key, name in dl.POOL_DISPLAY.items():
            with st.spinner(f"{name}..."):
                ok, msg = dl.refresh_player_pool(
                    session(), key, config,
                    supabase_url=SUPABASE_URL if upload else None,
                    supabase_key=SUPABASE_KEY if upload else None,
                    output_dir=tmp)
            (st.success if ok and "FAILED" not in msg else st.error)(f"{name}: {msg}")
    st.info("Done. In the chart maker, press **Refresh data** in the sidebar "
            "so the new games show up straight away.")
