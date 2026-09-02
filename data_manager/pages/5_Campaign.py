"""
Campaign — fixture-driven download.

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
    WORK_OLD_FEED,
    WORK_ONE_SIDED,
    WORK_ANCHORED,
    WORK_ORDER,
    build_work_list,
    create_session,
    discover_fixtures,
    estimate_requests,
    MAX_GAMES_PER_REQUEST,
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
    WORK_OLD_FEED: "Both sides stored, but downloaded under the OLD feed — "
                   "22 play types, no cards, no substitutions. Re-download.",
    WORK_ANCHORED: "Both sides stored, but written by an ANCHORED request — "
                   "21 team columns on the away rows hold the HOME team's "
                   "values (abbreviation, colour, score, formation, assists). "
                   "Re-download.",
    WORK_NOT_PLAYED: "Fixture exists, no result yet. Skip.",
    WORK_COMPLETE: "Both sides present. Skip.",
}
STATE_ICON = {WORK_MISSING: "🔴", WORK_ONE_SIDED: "🟠", WORK_OLD_FEED: "🟡",
              WORK_ANCHORED: "🟣",
              WORK_NOT_PLAYED: "⚪", WORK_COMPLETE: "🟢"}

st.title("Campaign")

# ── Where are we writing? ────────────────────────────────────────────────────
# An explicit choice on the page, defaulting to practice, rather than an
# environment variable you have to remember to set.
#
# The rest of the tool defaults to production deliberately - flipping that
# would mean someone runs a familiar action expecting to update the real
# database and quietly updates a file instead. This page is different: it is
# new, nobody has habits about it, and it can rewrite thousands of matches. A
# wrong default here is not a surprise, it is an accident.
#
# LOCAL_DB_ENV still works and still wins, so scripts and tests are unchanged.
st.header("Target")

_env_db = os.environ.get(LOCAL_DB_ENV)
DEFAULT_PRACTICE = _env_db or os.path.join(
    tempfile.gettempdir(), "data_manager_practice.duckdb")

if _env_db:
    st.info(f"`{LOCAL_DB_ENV}` is set, so everything here writes to "
            f"`{_env_db}` regardless of the choice below.")

target = st.radio(
    "Write to", ["Practice (a local file)", "Production (MotherDuck)"],
    index=0, horizontal=True,
    help="Practice writes to a DuckDB file with the identical schema. "
         "Nothing reaches the real database until you choose Production.")
is_practice = target.startswith("Practice")

if is_practice:
    practice_path = st.text_input("Practice file", value=DEFAULT_PRACTICE)
    write_target = practice_path
    st.success(f"Writing to `{practice_path}`. Production is untouched.")
else:
    write_target = _env_db      # None unless the env var overrides
    if _env_db:
        st.info(f"Overridden by `{LOCAL_DB_ENV}` — still writing locally.")
    else:
        st.error("**Writing to production MotherDuck.** Games are replaced "
                 "at `gameId`. There is no undo.")
        if not st.checkbox("I mean it — write to production"):
            st.stop()


def open_target():
    """The one place a connection is opened, so the choice cannot be bypassed."""
    return get_motherduck_connection(MOTHERDUCK_TOKEN, local_path=write_target)


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
_scol, _acol, _ncol = st.columns([6, 1, 1])
with _scol:
    chosen = st.multiselect(
        "Seasons", all_seasons, format_func=_season_label, key="campaign_scope",
        help="One request per season discovers every fixture in it. No team "
             "list involved — this is what stops config.json driving "
             "downloads.")
with _acol:
    st.write("")
    st.button("All", key="campaign_scope_all",
              help=f"Select all {len(all_seasons)} seasons.",
              on_click=lambda: st.session_state.update(
                  {"campaign_scope": all_seasons}))
with _ncol:
    st.write("")
    st.button("None", key="campaign_scope_none",
              on_click=lambda: st.session_state.update(
                  {"campaign_scope": []}))

if not chosen:
    st.caption("Pick at least one season.")
    st.stop()

# Discovery is one request per season and runs before any cost estimate can
# exist, so it is the one spend the review step below cannot show you first.
st.caption(f"{len(chosen)} of {len(all_seasons)} seasons · "
           f"{len(chosen)} discovery request"
           f"{'s' if len(chosen) != 1 else ''} to build the work list.")

# A work list is only true of the database and the seasons it was built
# against. It is cached in session state, and nothing used to invalidate it,
# so switching Practice -> Production or changing the season left the OLD
# answer on screen: a practice file holds nothing, so every fixture read as
# `missing` and kept reading that way after the target changed. The count
# being identical across different leagues is what gave it away.
_context = (write_target or "production", tuple(sorted(chosen)))
if st.session_state.get("campaign_context") != _context:
    if st.session_state.pop("campaign_work", None) is not None:
        st.warning("Target or seasons changed — the previous work list no "
                   "longer applies. Build it again.")
    st.session_state.pop("campaign_seasons", None)

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
    con = open_target()
    try:
        work = build_work_list(con, fx)
    finally:
        con.close()
    st.session_state["campaign_work"] = work
    st.session_state["campaign_seasons"] = chosen
    # Stamp what it was built against, so the check above can retire it.
    st.session_state["campaign_context"] = _context

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

# A schema change makes stored games stale in a way the work list cannot see:
# both sides are present and the play types are current, so they read as
# COMPLETE. Rather than teach the classifier about column versions - which
# would need a schema_version on `games` and a new state, for a job done once
# - let the operator say "I know, do it anyway".
force = st.checkbox(
    "Re-download games already stored",
    help="For a schema change. The games are complete, but predate columns "
         "we now take, so a backfill means fetching them again with the "
         "widened SELECT. Work season by season — finished games do not "
         "drop off this list, so stopping mid-season means redoing it.")

todo_states = [s for s in (WORK_MISSING, WORK_ONE_SIDED, WORK_OLD_FEED,
                           WORK_ANCHORED)
               + ((WORK_COMPLETE,) if force else ())
               if summary[s]]
if not todo_states:
    st.success("Every played fixture is already stored whole. Nothing to do.")
    st.stop()

with st.expander(f"The {sum(summary[s] for s in todo_states):,} games in "
                 f"scope"):
    show = work[work["state"].isin(todo_states)]
    st.dataframe(
        show[[c for c in ("gameDate", "homeTeam", "awayTeam", "state",
                          "sides_present", "events_stored", "gameId")
              if c in show.columns]],
        hide_index=True, use_container_width=True)

# ── 3. Run ───────────────────────────────────────────────────────────────────
st.header("3 · Run")

# COMPLETE is selectable but never pre-selected. Everything else here is a
# game the database is missing or holding badly; complete games are fine and
# re-fetching them is a deliberate, expensive act. Defaulting it on meant
# ticking one checkbox silently opted you into re-downloading a whole season
# ALONGSIDE the games that genuinely needed work.
picked = st.multiselect(
    "Download which states?", todo_states,
    default=[s for s in todo_states if s != WORK_COMPLETE],
    format_func=lambda s: f"{STATE_ICON[s]} {s} ({summary[s]:,})")
n = sum(summary[s] for s in picked)

if WORK_COMPLETE in picked:
    st.warning(
        f"**Re-downloading {summary[WORK_COMPLETE]:,} games that are already "
        f"stored whole.** They are replaced at `gameId`. Do this for a schema "
        f"change; it is wasted requests otherwise.")

# Cost comes from the runner's own batching, not from arithmetic repeated
# here - the two drift apart otherwise.
_todo = work[work["state"].isin(picked)] if picked else work.iloc[0:0]
n_batches, n_requests = estimate_requests(_todo)

st.caption(
    f"**{n:,} games** in **{n_batches:,} batches** ≈ **{n_requests:,} requests** "
    f"(events + minutes per batch), {MAX_GAMES_PER_REQUEST} games each. "
    f"A request names no team, so any games can share one — and each event "
    f"comes back once per side, which is what keeps both teams' colours, "
    f"abbreviations and scores correct.")
st.caption(
    "Each match is written whole, in a transaction. Stopping is safe — it "
    "finishes the batch in flight, and finished games drop off the list, so "
    "resuming just means running again.")

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
        con = open_target()
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
