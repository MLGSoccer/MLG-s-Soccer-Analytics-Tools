"""
Targeted Refresh — pinpoint re-download of a single game's events.

Use case: you spot a chart problem for a specific match (e.g. event ordering
looks wrong, one team's data looks stale). Selecting a game here re-fetches
events for BOTH teams in that match from TruMedia at the same moment, so
their gameEventIndex numbering comes from the same TruMedia snapshot and
the chart-relevant ordering is internally consistent again.

Backed by the same upsert pipeline as the main Downloads page; just scoped
to a single gameId via the new game_ids parameter on download_event_log.
"""
import json
import os
import sys
import tempfile

import streamlit as st

# Reuse the data manager's downloader primitives. Path insert mirrors app.py
# so this page works whether launched via `streamlit run app.py` or directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from downloader import (  # noqa: E402
    create_session,
    download_event_log,
    fetch_and_store_fixture_data,
    upsert_events_to_motherduck,
    get_motherduck_connection,
    load_secrets,
)

st.set_page_config(
    page_title="Targeted Refresh",
    page_icon="⚽",
    layout="wide",
)

# ── Load config + secrets (same paths as app.py) ─────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")

with open(CONFIG_PATH) as f:
    config = json.load(f)

secrets = load_secrets(SECRETS_PATH)
MOTHERDUCK_TOKEN = secrets.get("MOTHERDUCK_TOKEN")
API_FOOTBALL_KEY = secrets.get("API_FOOTBALL_KEY")

_secondary = set(config.get("secondary_seasons", []))
_season_names = config.get("seasons", {})

# ── League grouping (mirrors app.py) ─────────────────────────────────────────
leagues = {}  # league_name -> [team_dict, ...]
for _team in config["teams"]:
    _primary = next((s for s in _team["season_ids"] if s not in _secondary), None)
    if _primary:
        _league_name = _season_names.get(_primary, "Other")
    else:
        _first_sec = next((s for s in _team["season_ids"] if s in _secondary), None)
        _league_name = _season_names.get(_first_sec, "Other") if _first_sec else "Other"
    leagues.setdefault(_league_name, []).append(_team)


# ── Lookup helpers ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_seasonId_for_league(league_name):
    """Find the primary seasonId for a league name from the config mapping.

    seasons map: {season_id: human_name}. Reverse lookup the season_id whose
    name matches and that isn't in the secondary set.
    """
    for sid, name in _season_names.items():
        if name == league_name and sid not in _secondary:
            return sid
    return None


@st.cache_data(ttl=120)
def fetch_games_for_team(team_id, season_id):
    """Pull recent games for a team in a given season from MotherDuck."""
    if not MOTHERDUCK_TOKEN:
        return []
    con = get_motherduck_connection(MOTHERDUCK_TOKEN)
    try:
        rows = con.execute(
            """
            SELECT gameId, Date, homeTeam, awayTeam, homeTeamId, awayTeamId
            FROM games
            WHERE seasonId = ?
              AND (homeTeamId = ? OR awayTeamId = ?)
            ORDER BY Date DESC
            """,
            [season_id, team_id, team_id],
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "gameId": r[0], "Date": str(r[1])[:10] if r[1] else "?",
            "homeTeam": r[2], "awayTeam": r[3],
            "homeTeamId": r[4], "awayTeamId": r[5],
        }
        for r in rows
    ]


@st.cache_data(ttl=60)
def fetch_broken_games_index():
    """Return indices of FIXABLE broken games for visual flagging.

    A broken game = a game where fewer than 2 teams have events. We further
    filter to 'fixable' broken games where both home and away team_ids are
    in our config (so coverage gaps -- e.g. UCL games where only one side is
    in our pipeline -- don't pollute the indicators).

    Returns:
        broken_by_season: {seasonId: count}
        broken_by_team:   {teamId: count}
        broken_game_ids:  set of gameIds
    """
    if not MOTHERDUCK_TOKEN:
        return {}, {}, set()
    config_team_ids = {t["team_id"] for t in config["teams"]}
    con = get_motherduck_connection(MOTHERDUCK_TOKEN)
    try:
        rows = con.execute(
            """
            SELECT g.gameId, g.seasonId, g.homeTeamId, g.awayTeamId
            FROM games g LEFT JOIN events e ON g.gameId = e.gameId
            GROUP BY 1, 2, 3, 4
            HAVING COUNT(DISTINCT e.teamId) < 2
            """
        ).fetchall()
    finally:
        con.close()

    by_season = {}
    by_team = {}
    broken_ids = set()
    for gid, sid, h_id, a_id in rows:
        if h_id not in config_team_ids or a_id not in config_team_ids:
            continue  # Coverage gap, not fixable from our side
        by_season[sid] = by_season.get(sid, 0) + 1
        by_team[h_id] = by_team.get(h_id, 0) + 1
        by_team[a_id] = by_team.get(a_id, 0) + 1
        broken_ids.add(gid)
    return by_season, by_team, broken_ids


def find_team_config(team_id):
    """Look up a team config entry by team_id."""
    return next((t for t in config["teams"] if t["team_id"] == team_id), None)


# ── Refresh worker ───────────────────────────────────────────────────────────
def refresh_game(game_id, home_team_id, away_team_id, home_name, away_name,
                 game_date=None, season_id=None):
    """Download + upsert events for both teams for a single gameId.

    Three-phase to avoid leaving the game in a half-empty state:
      Phase 1 - download both teams' CSVs from TruMedia (same snapshot, same
                moment). If either download fails, abort with no DB changes.
      Phase 2 - wipe the game's events entirely (both teams), then INSERT
                each team's fresh rows. The wide DELETE is necessary because
                stale-snapshot rows from one team would collide with the
                fresh-snapshot eventGuids from the other on insert.
      Phase 3 - re-call API-Football for the game so own_goals / cards /
                player_minutes pick up any post-match revisions (retro-
                credited own goals, rescinded cards, corrected sub times).
                The default API-Football enrichment pass on the main page
                only fetches games whose fixture_id is NULL, so a retro-
                credit silently never lands until we re-fetch from here.
                Best-effort: a failure here is reported but doesn't undo
                the TruMedia refresh.

    `game_date` and `season_id` are needed for Phase 3 (passed straight
    into fetch_and_store_fixture_data). They're optional - omitting them
    skips Phase 3.

    Returns a list of (success_bool, message) tuples.
    """
    if "cookies" not in st.session_state:
        return [(False, "Not authenticated. Authenticate on the main page first.")]
    session = create_session(st.session_state["cookies"])
    results = []

    pairs = [
        (home_team_id, home_name, "home"),
        (away_team_id, away_name, "away"),
    ]

    # Phase 1: download both CSVs first. Bail before touching the DB if either
    # download fails -- we don't want to wipe existing rows we can't replace.
    csvs = []  # list of (team_id, team_name, side, tmp_path, row_count)
    download_failed = False
    for team_id, team_name, side in pairs:
        team_cfg = find_team_config(team_id)
        if not team_cfg:
            results.append((
                False,
                f"{team_name} ({side}): not in download config -- skipped",
            ))
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
                tmp_path = tmp.name
            rows, _ = download_event_log(
                session, team_id, team_cfg["season_ids"], tmp_path,
                game_ids=[game_id],
            )
            csvs.append((team_id, team_name, side, tmp_path, rows))
        except Exception as e:
            results.append((False, f"{team_name} ({side}): download failed: {e}"))
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            download_failed = True

    if download_failed or not csvs:
        # Clean up any temp files we did create, then bail without DB changes.
        for _, _, _, tmp_path, _ in csvs:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        results.append((False, "Aborted -- no DB changes made (downloads incomplete)."))
        return results

    # Phase 2: wide DELETE + per-team INSERTs on a single connection. Wrapped
    # in a transaction so a mid-flight failure rolls back rather than leaving
    # the game half-populated.
    con = get_motherduck_connection(MOTHERDUCK_TOKEN)
    phase2_ok = False
    try:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM events WHERE gameId = ?", [game_id])
            for team_id, team_name, side, tmp_path, rows in csvs:
                upsert_events_to_motherduck(MOTHERDUCK_TOKEN, tmp_path, con=con)
                results.append((
                    True,
                    f"{team_name} ({side}): refreshed {rows:,} events",
                ))
            con.execute("COMMIT")
            phase2_ok = True
        except Exception as e:
            con.execute("ROLLBACK")
            results.append((False, f"DB write failed -- rolled back: {e}"))
    finally:
        for _, _, _, tmp_path, _ in csvs:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        con.close()

    # Phase 3: API-Football refresh. Best-effort - failures here don't undo
    # the TruMedia refresh that already committed. Only runs if Phase 2 was
    # clean (no point pulling fresh fixture data onto half-broken events).
    if phase2_ok and API_FOOTBALL_KEY and game_date:
        con = get_motherduck_connection(MOTHERDUCK_TOKEN)
        try:
            # Reuse the existing fixture_id if we have one, so we skip the
            # name-matching API call (one less request) and head straight to
            # /fixtures/lineups + /fixtures/events.
            row = con.execute(
                "SELECT fixture_id FROM game_fixtures WHERE gameId = ?",
                [game_id],
            ).fetchone()
            existing_fid = row[0] if row else None

            status = fetch_and_store_fixture_data(
                api_key=API_FOOTBALL_KEY,
                token=MOTHERDUCK_TOKEN,
                game_id=game_id,
                date=game_date,
                home=home_name,
                away=away_name,
                con=con,
                season_id=season_id,
                fixture_id=existing_fid,
                force_refresh=True,
            )
            if status == "matched":
                # Read back the new counts so the user can see what landed.
                n_pm = con.execute(
                    "SELECT COUNT(*) FROM player_minutes WHERE gameId = ?",
                    [game_id],
                ).fetchone()[0]
                n_og = con.execute(
                    "SELECT COUNT(*) FROM own_goals WHERE gameId = ?",
                    [game_id],
                ).fetchone()[0]
                n_c = con.execute(
                    "SELECT COUNT(*) FROM cards WHERE gameId = ?",
                    [game_id],
                ).fetchone()[0]
                results.append((
                    True,
                    f"API-Football: refreshed ({n_pm} player_minutes, "
                    f"{n_og} own_goals, {n_c} cards)",
                ))
            elif status == "not_found":
                results.append((
                    False,
                    "API-Football: fixture name lookup failed - "
                    "add a TRUMEDIA_TO_API_NAME override and retry.",
                ))
            else:
                results.append((False, f"API-Football: {status}"))
        except Exception as e:
            results.append((False, f"API-Football: {type(e).__name__}: {e}"))
        finally:
            con.close()
    elif phase2_ok and not API_FOOTBALL_KEY:
        results.append((
            False,
            "API-Football refresh skipped: API_FOOTBALL_KEY not configured.",
        ))

    return results


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("Targeted Refresh")
st.caption(
    "Pinpoint re-download of a single match's events. Refreshes BOTH teams "
    "in the selected game from the current TruMedia snapshot, so their event "
    "ordering stays internally consistent."
)

if not MOTHERDUCK_TOKEN:
    st.error("MotherDuck token not configured — Targeted Refresh requires DB access.")
    st.stop()

if "cookies" not in st.session_state:
    st.warning(
        "Not authenticated. Open the **app** page from the sidebar, paste "
        "your TruMedia cURL command, then return here."
    )
    st.stop()

# Pre-compute broken-games indices so we can decorate the dropdowns with
# warning marks. Cached for 60s; invalidated after any successful refresh.
broken_by_season, broken_by_team, broken_game_ids = fetch_broken_games_index()

# Aggregate per-league counts by walking config teams (each league's broken
# count = sum of its primary seasonId's broken count). One league can map to
# exactly one primary seasonId via get_seasonId_for_league.
broken_by_league = {}
for _lname in leagues:
    _sid = get_seasonId_for_league(_lname)
    if _sid and _sid in broken_by_season:
        broken_by_league[_lname] = broken_by_season[_sid]

total_broken = len(broken_game_ids)
if total_broken:
    st.info(
        f"⚠ {total_broken} fixable broken game"
        f"{'s' if total_broken != 1 else ''} detected in DB "
        f"(missing one team's events). They are flagged below."
    )


def _fmt_league(name):
    if not name:
        return ""
    n = broken_by_league.get(name, 0)
    return f"{name}  ⚠ {n} broken" if n else name


def _fmt_team(name, teams_lookup):
    if not name:
        return ""
    t = teams_lookup.get(name)
    if not t:
        return name
    n = broken_by_team.get(t["team_id"], 0)
    return f"{name}  ⚠ {n} broken" if n else name


def _fmt_game(label, label_to_gameid):
    if not label:
        return ""
    gid = label_to_gameid.get(label)
    return f"🔴 {label}" if gid in broken_game_ids else label


# Cascading selectors -------------------------------------------------------
sel_col1, sel_col2, sel_col3 = st.columns([1, 1, 2])

with sel_col1:
    league_name = st.selectbox(
        "League",
        options=[""] + sorted(leagues.keys()),
        index=0,
        format_func=_fmt_league,
        help="Pick the league of the match you want to refresh.",
    )

with sel_col2:
    if league_name:
        teams_in_league = sorted(leagues[league_name], key=lambda t: t["name"])
        teams_lookup = {t["name"]: t for t in teams_in_league}
        team_name = st.selectbox(
            "Team",
            options=[""] + [t["name"] for t in teams_in_league],
            index=0,
            format_func=lambda n: _fmt_team(n, teams_lookup),
            help="Pick either of the two teams in the match.",
        )
    else:
        team_name = ""
        st.selectbox("Team", options=[""], index=0, disabled=True)

selected_game = None
with sel_col3:
    if team_name and league_name:
        team = next((t for t in leagues[league_name] if t["name"] == team_name), None)
        season_id = get_seasonId_for_league(league_name)
        if team and season_id:
            games = fetch_games_for_team(team["team_id"], season_id)
            if games:
                game_options = [
                    f"{g['Date']} — {g['homeTeam']} vs {g['awayTeam']}"
                    for g in games
                ]
                label_to_gameid = {
                    lbl: g["gameId"] for lbl, g in zip(game_options, games)
                }
                idx = st.selectbox(
                    "Game",
                    options=[""] + game_options,
                    index=0,
                    format_func=lambda lbl: _fmt_game(lbl, label_to_gameid),
                    help="Pick the specific match to refresh. 🔴 = currently broken.",
                )
                if idx:
                    selected_game = games[game_options.index(idx)]
            else:
                st.selectbox("Game", options=["(no games found)"], index=0, disabled=True)
        else:
            st.selectbox("Game", options=["(team/season unresolved)"], index=0, disabled=True)
    else:
        st.selectbox("Game", options=[""], index=0, disabled=True)

# Selected-game summary -----------------------------------------------------
if selected_game:
    st.divider()
    st.subheader("Selected match")

    home_cfg = find_team_config(selected_game["homeTeamId"])
    away_cfg = find_team_config(selected_game["awayTeamId"])

    info_col, action_col = st.columns([3, 1])
    with info_col:
        st.markdown(
            f"**{selected_game['homeTeam']} vs {selected_game['awayTeam']}** "
            f"— {selected_game['Date']}"
        )
        st.caption(f"gameId: `{selected_game['gameId']}`")

        will_refresh = []
        skipped = []
        for tid, tname, side in [
            (selected_game["homeTeamId"], selected_game["homeTeam"], "home"),
            (selected_game["awayTeamId"], selected_game["awayTeam"], "away"),
        ]:
            if find_team_config(tid):
                will_refresh.append(f"{tname} ({side})")
            else:
                skipped.append(f"{tname} ({side})")

        if will_refresh:
            st.markdown("Will refresh: " + ", ".join(f"**{t}**" for t in will_refresh))
        if skipped:
            st.warning(
                "Cannot refresh (not in download config): "
                + ", ".join(skipped)
                + ". This usually happens with a foreign opponent in a UEFA fixture "
                "where we don't track the other side."
            )

    with action_col:
        st.write("")
        if st.button("Refresh this game", type="primary"):
            with st.spinner("Re-fetching events for both teams..."):
                results = refresh_game(
                    selected_game["gameId"],
                    selected_game["homeTeamId"],
                    selected_game["awayTeamId"],
                    selected_game["homeTeam"],
                    selected_game["awayTeam"],
                    game_date=selected_game["Date"],
                    season_id=get_seasonId_for_league(league_name),
                )
            st.session_state["targeted_results"] = results
            # Bust caches so the games list and broken-game indicators
            # both re-reflect the just-refreshed game on next render.
            fetch_games_for_team.clear()
            fetch_broken_games_index.clear()
            st.rerun()

# Result display ------------------------------------------------------------
if "targeted_results" in st.session_state:
    st.divider()
    st.subheader("Last refresh result")
    for ok, msg in st.session_state.pop("targeted_results"):
        (st.success if ok else st.error)(msg)
    st.caption(
        "On the deployed Streamlit Cloud chart app: cached chart pages may "
        "still show the old data until cache TTL expires. Reboot that app "
        "for an immediate refresh."
    )
