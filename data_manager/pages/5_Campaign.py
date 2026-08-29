"""
Campaign — fixture-driven download, one request per game.

The replacement for Bulk Actions. Bulk Actions loops TEAMS and asks "what has
changed since this team's last game?"; this loops FIXTURES and asks "which
matches does the database not have whole?"

Those are different questions, and the second one is the one that matters.
A match fetched one team at a time leaves both teams looking up to date while
the match itself holds one side - which is how 1,116 of 4,930 production games
(22.6%) came to be half a match with nothing flagging it.

The flow is deliberately: discover -> classify -> REVIEW -> run. Nothing is
downloaded until the counts have been shown.

Resumability is not a feature here, it is a consequence: each game is written
atomically and progress IS the database, so closing this page mid-campaign and
reopening it recomputes the list with the finished games already gone.
"""
import json
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from downloader import (  # noqa: E402
    LOCAL_DB_ENV,
    WORK_COMPLETE,
    WORK_MISSING,
    WORK_NOT_PLAYED,
    WORK_ONE_SIDED,
    WORK_ORDER,
    build_work_list,
    create_session,
    discover_fixtures,
    get_motherduck_connection,
    load_secrets,
    run_campaign,
    work_list_summary,
)

st.set_page_config(page_title="Campaign", page_icon="⚽", layout="wide")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)
secrets = load_secrets(SECRETS_PATH)
MOTHERDUCK_TOKEN = secrets.get("MOTHERDUCK_TOKEN")

_season_names = config.get("seasons", {})
_season_leagues = config.get("season_leagues", {})

STATE_HELP = {
    WORK_MISSING: "No events at all. Download.",
    WORK_ONE_SIDED: "Only ONE team's events are stored — half a match. "
                    "Download. This is the case the old tool cannot see.",
    WORK_NOT_PLAYED: "Fixture exists, no result yet. Skip.",
    WORK_COMPLETE: "Both sides present. Skip.",
}
STATE_ICON = {WORK_MISSING: "🔴", WORK_ONE_SIDED: "🟠",
              WORK_NOT_PLAYED: "⚪", WORK_COMPLETE: "🟢"}

st.title("Campaign")

# ── Where are we writing? ────────────────────────────────────────────────────
local_db = os.environ.get(LOCAL_DB_ENV)
if local_db:
    st.info(f"**Practice mode** — writing to `{local_db}`, not production. "
            f"Unset `{LOCAL_DB_ENV}` to write to MotherDuck.")
else:
    st.warning("Writing to **production MotherDuck**. Set "
               f"`{LOCAL_DB_ENV}` to a file path to practise first.")

if "cookies" not in st.session_state:
    st.error("Not authenticated — paste a cURL command on the main page first.")
    st.stop()

# ── 1. Scope ─────────────────────────────────────────────────────────────────
st.header("1 · Scope")


def _season_label(sid):
    name = _season_names.get(sid)
    league = _season_leagues.get(sid)
    if name and league and name != league:
        return f"{league} — {name}"
    return name or league or f"{sid[:10]}…"


all_seasons = sorted(_season_names, key=_season_label)
chosen = st.multiselect(
    "Seasons", all_seasons, format_func=_season_label,
    help="One request per season discovers every fixture in it. No team list "
         "involved — this is what stops config.json driving downloads.")

if not chosen:
    st.caption("Pick at least one season.")
    st.stop()

# ── 2. Discover + classify ───────────────────────────────────────────────────
st.header("2 · What needs doing")

if st.button("Build the work list", type="primary"):
    session = create_session(st.session_state["cookies"])
    with st.spinner(f"Discovering fixtures for {len(chosen)} season(s)…"):
        try:
            fx = discover_fixtures(session, chosen)
        except Exception as e:
            st.error(f"Fixture discovery failed: {e}")
            st.stop()
    if fx.empty:
        st.warning("No fixtures returned for those seasons.")
        st.stop()
    con = get_motherduck_connection(MOTHERDUCK_TOKEN)
    try:
        work = build_work_list(con, fx)
    finally:
        con.close()
    st.session_state["campaign_work"] = work
    st.session_state["campaign_seasons"] = chosen

work = st.session_state.get("campaign_work")
if work is None:
    st.caption("Nothing discovered yet.")
    st.stop()

summary = work_list_summary(work)
cols = st.columns(len(WORK_ORDER))
for col, state in zip(cols, WORK_ORDER):
    col.metric(f"{STATE_ICON[state]} {state.replace('_', ' ')}",
               f"{summary[state]:,}")
for state in WORK_ORDER:
    if summary[state]:
        st.caption(f"{STATE_ICON[state]} **{state}** — {STATE_HELP[state]}")

todo_states = [s for s in (WORK_MISSING, WORK_ONE_SIDED) if summary[s]]
if not todo_states:
    st.success("Every played fixture is already stored whole. Nothing to do.")
    st.stop()

with st.expander(f"The {sum(summary[s] for s in todo_states):,} games that "
                 f"need downloading"):
    show = work[work["state"].isin(todo_states)]
    st.dataframe(
        show[[c for c in ("gameDate", "homeTeam", "awayTeam", "state",
                          "sides_present", "events_stored", "gameId")
              if c in show.columns]],
        hide_index=True, use_container_width=True)

# ── 3. Run ───────────────────────────────────────────────────────────────────
st.header("3 · Run")

picked = st.multiselect(
    "Download which states?", todo_states, default=todo_states,
    format_func=lambda s: f"{STATE_ICON[s]} {s} ({summary[s]:,})")
n = sum(summary[s] for s in picked)

st.caption(f"{n:,} games · one request each · written atomically per match. "
           f"Safe to stop and resume — finished games drop out of the list.")

if st.button(f"Download {n:,} games", type="primary", disabled=not n):
    session = create_session(st.session_state["cookies"])
    bar = st.progress(0.0)
    status = st.empty()
    log = st.container()
    seen = []

    def _progress(done, total, gid, state, note):
        bar.progress(done / max(total, 1))
        status.write(f"{done:,} / {total:,} — {gid}")
        if note and not note.endswith("rows"):
            seen.append(f"⚠️ `{gid}` {note}")

    with tempfile.TemporaryDirectory() as tmp:
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        try:
            written, failed, skipped = run_campaign(
                session, MOTHERDUCK_TOKEN, work, work, tmp,
                st.session_state.get("campaign_seasons", chosen),
                states=tuple(picked), con=con, progress=_progress)
        finally:
            con.close()

    bar.progress(1.0)
    st.success(f"Wrote {written:,} games." +
               (f" {failed:,} failed." if failed else "") +
               (f" {skipped:,} skipped." if skipped else ""))
    if seen:
        with log.expander(f"{len(seen)} problem(s)"):
            for line in seen[:100]:
                st.write(line)
    # The work list is now stale by definition - rebuild it rather than
    # letting the page show counts that no longer describe the database.
    st.session_state.pop("campaign_work", None)
    st.caption("Work list cleared — rebuild it above to confirm what landed.")
