"""
Health — cross-references between config.json, MotherDuck and the player
pools that otherwise have no owner.

Every wiring mistake this page catches is one that fails SILENTLY. A season
can sit in config with 30 teams attached, events flowing into MotherDuck,
and still be invisible to half the pipeline because one list never got the
id. Two real examples, both found by hand in August 2026:

  - MLS 2026 was in `seasons`, on all 30 teams, in `season_leagues` and in
    `season_api_leagues` - but missing from the north_america player pool.
    The pool held only the tail of MLS 2025, capped at 1533 minutes, and
    was months from holding no MLS at all.
  - Liga Profesional, Brasileirao, Frauen-Bundesliga and Premiere Ligue had
    746 games of events and no display label, which hid them from Claude's
    chart suggestions AND dumped all 76 of their teams into a single
    "Other" expander on the Downloads page.

Neither was detectable without manually diffing four lists. This page does
that diff on every load.

Read-only: nothing here writes. Needs no TruMedia session - config is local
and MotherDuck authenticates from secrets.env - so it works without pasting
a cURL first.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from downloader import (  # noqa: E402
    CALENDAR_YEAR_LEAGUES,
    get_motherduck_connection,
    group_teams_by_league,
    load_secrets,
)

st.set_page_config(page_title="Health", page_icon="⚽", layout="wide")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")
LAST_UPDATED_PATH = os.path.join(BASE_DIR, "data", "last_updated.json")
MINUTES_LAST_UPDATED_PATH = os.path.join(BASE_DIR, "data", "minutes_last_updated.json")

POOL_WINDOW_DAYS = 365  # mirrors build_player_pool_statement's rolling window
STALE_DAYS = 14         # a live competition quiet this long is worth a look

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

secrets = load_secrets(SECRETS_PATH)
MOTHERDUCK_TOKEN = os.getenv("MOTHERDUCK_TOKEN") or secrets.get("MOTHERDUCK_TOKEN")

seasons          = config.get("seasons", {})
season_leagues   = config.get("season_leagues", {})
season_api       = config.get("season_api_leagues", {})
pools            = config.get("player_pools", {})
pool_excluded    = set(config.get("pool_excluded_seasons", []))
teams            = config.get("teams", [])

pool_of = {sid: pool
           for pool, spec in pools.items()
           for sid in (spec.get("seasons") or [])}

seasons_on_teams = {sid for t in teams for sid in t.get("season_ids", [])}

def label(sid):
    """Human name for a season id, always disambiguated.

    A league name alone is ambiguous: the europe pool carries both the 25/26
    and 24/25 Premier League ids, and rendering both as "Premier League"
    makes a finding about the retired one look like it concerns the live one.
    """
    if sid in seasons:
        return seasons[sid]
    league = season_leagues.get(sid)
    if league:
        return f"{league} ({sid[:8]}…)"
    return f"({sid[:12]}…)"


# ── Data from MotherDuck ─────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def load_season_games(token):
    """seasonId -> (game count, first date, last date). Empty on failure."""
    if not token:
        return {}
    con = get_motherduck_connection(token)
    rows = con.execute(
        "SELECT seasonId, COUNT(*), MIN(Date), MAX(Date) FROM games GROUP BY seasonId"
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


@st.cache_data(ttl=600)
def load_shared_config(token):
    """(mirror, updated_at, n_versions) for the copy the chart maker reads.

    mirror is None when nothing has been written yet. Everything else on this
    page checks config.json, which is the file the DATA MANAGER reads - the
    chart maker reads the MotherDuck mirror, so the two being out of step is a
    silent failure this page would otherwise miss entirely.
    """
    if not token:
        return None, None, 0
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from shared.config_store import config_status, read_config
    con = get_motherduck_connection(token)
    updated_at, n_versions = config_status(con)
    return read_config(con), updated_at, n_versions


@st.cache_data(ttl=600)
def load_window_counts(token, window_start):
    """seasonId -> games inside the rolling player-pool window."""
    if not token:
        return {}
    con = get_motherduck_connection(token)
    rows = con.execute(
        "SELECT seasonId, COUNT(*) FROM games WHERE Date >= ? GROUP BY seasonId",
        [str(window_start)],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


st.title("Health")
st.markdown(
    "Cross-references between `config.json`, MotherDuck and the player pools. "
    "Every check here catches a wiring gap that fails silently."
)

if not MOTHERDUCK_TOKEN:
    st.error(
        "MOTHERDUCK_TOKEN not set — config-only checks still run, but anything "
        "needing game data is skipped. Add it to `data_manager/secrets.env`."
    )
    season_games, window_counts = {}, {}
else:
    today = date.today()
    window_start = today - timedelta(days=POOL_WINDOW_DAYS)
    try:
        season_games = load_season_games(MOTHERDUCK_TOKEN)
        window_counts = load_window_counts(MOTHERDUCK_TOKEN, window_start)
    except Exception as e:
        st.error(f"MotherDuck query failed: {type(e).__name__}: {e}")
        season_games, window_counts = {}, {}

today = date.today()
window_start = today - timedelta(days=POOL_WINDOW_DAYS)


# ── Build the findings ───────────────────────────────────────────────────────
# Each finding: (severity, check name, subject, detail). Severity drives both
# the summary tiles and the ordering, so the loudest thing is always on top.

findings = []

# 1. On teams but in no pool. The MLS bug. pool_excluded_seasons declares the
#    deliberate omissions so this check stays quiet about them.
for sid in sorted(seasons_on_teams, key=label):
    if sid in pool_of or sid in pool_excluded:
        continue
    n_teams = sum(1 for t in teams if sid in t.get("season_ids", []))
    findings.append((
        "critical", "Not in any player pool", label(sid),
        f"{n_teams} teams carry this season, but it feeds no pool. Player "
        f"percentile charts cannot resolve anyone in it. Add it to a "
        f"`player_pools` list, or to `pool_excluded_seasons` if deliberate.",
    ))

# 2. Has games but no display label. Hides the league from Claude's
#    DATA AVAILABLE block and dumps its teams into "Other" on Downloads.
for sid, (n, _first, _last) in sorted(season_games.items(), key=lambda kv: -kv[1][0]):
    if sid in seasons:
        continue
    findings.append((
        "critical", "No display label", label(sid),
        f"{n} games in MotherDuck with no entry in `seasons`. Invisible to "
        f"PodcastShorts chart suggestions; its teams fall into the Downloads "
        f'"Other" bucket.',
    ))

# 3 & 4. Labelled but missing from a lookup the pipeline reads.
for sid in seasons:
    if sid not in season_leagues:
        findings.append((
            "critical", "No league mapping", label(sid),
            "Missing from `season_leagues`. Charts fall back to a generic "
            '"MATCH" kicker and the Streamlit league bucket resolves to "Other".',
        ))
    if sid not in season_api:
        findings.append((
            "warning", "No API-Football league id", label(sid),
            "Missing from `season_api_leagues`. Fixture matching runs without "
            "its league filter, so red cards and own goals can cross-match to "
            "another competition on the same date.",
        ))

# 5. Pool ids the rolling window has already moved past. Only flagged when
#    MotherDuck can prove it: the pool downloads live from TruMedia and never
#    consults the warehouse, so "no games here" is not evidence of anything.
#    Ids we cannot verify are left to the matrix below rather than reported.
for sid, pool in sorted(pool_of.items(), key=lambda kv: kv[1]):
    if sid in season_games and window_counts.get(sid, 0) == 0:
        _n, _first, last = season_games[sid]
        findings.append((
            "info", "Pool id aged out", f"{label(sid)} — {pool} pool",
            f"Every game predates the {POOL_WINDOW_DAYS}-day window (last was "
            f"{last}), so it contributes nothing now. Harmless; prunable.",
        ))


def _parse_day(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# 6. Currency, asked per LEAGUE rather than per season. A finished season is
#    not a fault - the real question is whether a league has any current data.
#    The answer splits by calendar shape, and the two halves need opposite
#    responses:
#      calendar-year competitions (MLS, NWSL, Brasileirao, Liga Profesional)
#      should be mid-season in August, so silence means a missed download;
#      Aug-May competitions are legitimately between seasons, and silence
#      means the new season id has not been added yet.
league_latest = {}
for sid, league_name in season_leagues.items():
    if sid not in season_games:
        continue
    last_d = _parse_day(season_games[sid][2])
    if last_d and (league_name not in league_latest
                   or last_d > league_latest[league_name][1]):
        league_latest[league_name] = (sid, last_d)

for league_name, (sid, last_d) in sorted(league_latest.items()):
    gap = (today - last_d).days
    if gap <= STALE_DAYS:
        continue
    # One-off tournaments are year-tagged by convention ("World Cup 2026" -
    # see LEAGUE_ORDER in shared/motherduck.py, where the tag exists because
    # the tournament is quadrennial). A finished tournament is not a league
    # that has gone quiet, and it has no successor to wait for.
    if re.search(r"\b(19|20)\d{2}\b", league_name):
        continue
    if season_api.get(sid) in CALENDAR_YEAR_LEAGUES:
        findings.append((
            "critical", "In-season league gone quiet", league_name,
            f"Newest season ({label(sid)}) last played {last_d}, {gap} days ago. "
            f"This competition runs on the calendar year and should be mid-season "
            f"now, so this is a missed download rather than an off-season gap.",
        ))
    else:
        findings.append((
            "warning", "No current season loaded", league_name,
            f"Newest season ({label(sid)}) ended {last_d}, {gap} days ago, and no "
            f"successor is loaded. Needs its new season id once TruMedia publishes one.",
        ))


# 7. Is the shared copy in step with the local file? The chart maker reads the
#    MotherDuck mirror, not config.json - so if the mirror is behind, everything
#    on this page can look correct while the deployed app runs on older wiring.
#    That gap used to be permanent (config.json travelled by git push) and is the
#    reason the 2026/27 seasons were invisible in the chart maker for three days.
config_mirror, config_synced_at, config_versions = None, None, 0
config_in_sync = None
if MOTHERDUCK_TOKEN:
    try:
        config_mirror, config_synced_at, config_versions = \
            load_shared_config(MOTHERDUCK_TOKEN)
        config_in_sync = (config_mirror == config)
        if config_mirror is None:
            findings.append((
                "critical", "Shared config never written", "MotherDuck",
                "The chart maker reads its config from MotherDuck and there is "
                "nothing there, so it is falling back to whatever config.json was "
                "last deployed. Save any change in Add Season or Discover Teams "
                "to write it.",
            ))
        elif not config_in_sync:
            missing = sorted(set(config.get("seasons", {}))
                             - set(config_mirror.get("seasons", {})))
            extra = sorted(set(config_mirror.get("seasons", {}))
                           - set(config.get("seasons", {})))
            bits = []
            if missing:
                bits.append(f"{len(missing)} season(s) local-only: "
                            + ", ".join(label(s) for s in missing[:4]))
            if extra:
                bits.append(f"{len(extra)} season(s) only in the shared copy")
            n_local, n_mirror = len(config.get("teams", [])), \
                len(config_mirror.get("teams", []))
            if n_local != n_mirror:
                bits.append(f"teams: {n_mirror} shared vs {n_local} local")
            findings.append((
                "critical", "Shared config is behind config.json", "MotherDuck",
                "The chart maker is running on older wiring than this file. "
                + ("; ".join(bits) if bits else "The two copies differ.")
                + f"  Shared copy last written {config_synced_at}. Re-save in "
                "Add Season or Discover Teams to bring it level.",
            ))
    except Exception as e:
        findings.append((
            "warning", "Could not check the shared config", "MotherDuck",
            f"{type(e).__name__}: {e}",
        ))


# ── Summary tiles ────────────────────────────────────────────────────────────

sev_rank = {"critical": 0, "warning": 1, "info": 2}
findings.sort(key=lambda f: (sev_rank[f[0]], f[1], f[2]))

n_crit = sum(1 for f in findings if f[0] == "critical")
n_warn = sum(1 for f in findings if f[0] == "warning")
n_info = sum(1 for f in findings if f[0] == "info")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Critical", n_crit, help="Silently breaks part of the pipeline")
c2.metric("Warning", n_warn, help="Degrades quality or hides a gap")
c3.metric("Advisory", n_info, help="Worth knowing; nothing is broken")
c4.metric("Seasons tracked", len(seasons))

# The mirror is what the chart maker actually runs on, so state it plainly even
# when nothing is wrong: a tick here right after adding a league is the
# confirmation that it is already live, without going near the Cloud logs.
if config_in_sync:
    st.caption(
        f"Shared config (MotherDuck): in step with config.json  ·  "
        f"last written {config_synced_at}  ·  "
        f"{config_versions} prior version(s) retained"
    )
elif config_in_sync is False:
    st.caption(
        f"Shared config (MotherDuck): **behind** config.json  ·  "
        f"last written {config_synced_at} — see the critical finding below"
    )

if not findings:
    st.success("All checks clean — config, MotherDuck and the pools agree.")
else:
    st.dataframe(
        pd.DataFrame(
            [{"Severity": s, "Check": c, "Season": subj, "What it means": d}
             for s, c, subj, d in findings]
        ),
        hide_index=True,
        use_container_width=True,
    )

st.divider()


# ── Season matrix: every season against every list it should be in ───────────

st.header("Season wiring")
st.caption(
    "One row per season, one column per list it needs to appear in. "
    "This is the diff that would have caught both August 2026 bugs."
)

all_sids = set(seasons) | set(season_leagues) | set(season_games) | seasons_on_teams
matrix = []
for sid in sorted(all_sids, key=label):
    n_teams = sum(1 for t in teams if sid in t.get("season_ids", []))
    games, _first, last = season_games.get(sid, (0, None, None))
    if sid in pool_of:
        pool_cell = pool_of[sid]
    elif sid in pool_excluded:
        pool_cell = "— by design"
    else:
        pool_cell = "MISSING"

    # A season id sitting in a pool allowlist with no label, no teams and no
    # games is a previous season kept on the list. The rolling window has
    # already moved past it, so it contributes nothing and needs nothing -
    # name that state rather than red-flagging it as a gap.
    retired = (sid not in seasons and n_teams == 0 and games == 0)

    matrix.append({
        "Season": label(sid),
        "Label": "retired" if retired else ("yes" if sid in seasons else "MISSING"),
        "League map": "yes" if sid in season_leagues else "MISSING",
        "API id": season_api.get(sid, "—" if retired else "MISSING"),
        "Teams": n_teams,
        "Pool": pool_cell,
        "Games": games,
        "Last game": last or "—",
    })

matrix_df = pd.DataFrame(matrix)

def _flag_missing(val):
    return "color: #FF6B6B" if str(val) == "MISSING" else ""

st.dataframe(
    matrix_df.style.map(_flag_missing,
                        subset=["Label", "League map", "API id", "Pool"]),
    hide_index=True,
    use_container_width=True,
)

st.divider()


# ── Match coverage ───────────────────────────────────────────────────────────
# The question the freshness table below cannot answer: is each match WHOLE?
#
# Events are fetched one team at a time and DELETEd at (gameId, teamId), so a
# match can hold one side and both of its teams still look up to date - each
# was downloaded, just never together. Measured 2026-08-29: 1,116 of 4,930
# games (22.6%) hold ONE side, and nothing in this tool could see it. Every
# chart reading those games is reading half a match.
#
# Computed from `games` and `events` alone, deliberately: this page works
# without a TruMedia session and that is worth keeping. The cost is stated in
# the caption - a fixture never downloaded at all is not in `games`, so it
# cannot be counted here. The Campaign page discovers fixtures and can.

st.header("Match coverage")

OLD_FEED_TYPES = (
    'Pass', 'BallTouch', 'Clearance', 'TakeOn', 'Tackle', 'FreeKick',
    'Dispossessed', 'Interception', 'BlockedPass', 'AttemptSaved', 'Save',
    'Miss', 'OffsidePass', 'Goal', 'Claim', 'DropOfBall', 'Punch', 'Post',
    'PenaltyGoal', 'Smother', 'GoodSkill', 'OwnGoal',
)


@st.cache_data(ttl=300)
def load_coverage(token):
    con = get_motherduck_connection(token)
    try:
        types_sql = ",".join(f"'{t}'" for t in OLD_FEED_TYPES)
        return con.execute(f"""
            WITH per_game AS (
                SELECT gameId,
                       count(DISTINCT teamId) AS sides,
                       max(CASE WHEN playType NOT IN ({types_sql})
                                THEN 1 ELSE 0 END) AS new_feed
                FROM events GROUP BY gameId
            )
            SELECT g.seasonId,
                   count(*)                                        AS games,
                   count(*) FILTER (WHERE p.sides = 2)             AS complete,
                   count(*) FILTER (WHERE p.sides = 1)             AS one_sided,
                   count(*) FILTER (WHERE p.sides IS NULL)         AS no_events,
                   count(*) FILTER (WHERE p.new_feed = 1)          AS per_game_feed
            FROM games g LEFT JOIN per_game p ON p.gameId = g.gameId
            GROUP BY 1
        """).df()
    finally:
        con.close()


try:
    cov = load_coverage(MOTHERDUCK_TOKEN)
except Exception as e:
    cov = None
    st.error(f"Could not read coverage: {e}")

if cov is not None and not cov.empty:
    tot = int(cov["games"].sum())
    whole = int(cov["complete"].sum())
    half = int(cov["one_sided"].sum())
    none_ = int(cov["no_events"].sum())
    newf = int(cov["per_game_feed"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games", f"{tot:,}")
    c2.metric("Whole", f"{whole:,}", f"{100.0 * whole / max(tot, 1):.1f}%")
    c3.metric("Half a match", f"{half:,}",
              f"-{100.0 * half / max(tot, 1):.1f}%" if half else "0%",
              delta_color="inverse")
    c4.metric("No events", f"{none_:,}")

    if half:
        st.warning(
            f"**{half:,} games hold only one team's events.** Every chart "
            f"reading them is reading half a match. The Campaign page finds "
            f"and fixes these; the freshness table below cannot see them, "
            f"because both teams were downloaded — just never together.")

    st.caption(
        f"**Feed vintage:** {newf:,} of {tot:,} games "
        f"({100.0 * newf / max(tot, 1):.1f}%) carry play types the old "
        f"`event.toucher` feed could not return — cards, substitutions, "
        f"corners. That is migration progress, per game.")
    st.caption(
        "Counted from `games`, so a fixture that was never downloaded at all "
        "is invisible here. The Campaign page discovers fixtures from the "
        "season and can see those.")

    rows = []
    for _, r in cov.iterrows():
        sid = r["seasonId"]
        rows.append({
            "Season": seasons.get(sid) or season_leagues.get(sid) or f"{str(sid)[:10]}…",
            "Games": int(r["games"]),
            "Whole": int(r["complete"]),
            "Half a match": int(r["one_sided"]),
            "No events": int(r["no_events"]),
            "New feed": int(r["per_game_feed"]),
            "Complete %": round(100.0 * r["complete"] / max(r["games"], 1), 1),
        })
    df_cov = pd.DataFrame(rows).sort_values(
        ["Half a match", "Complete %"], ascending=[False, True])

    def _flag_half(v):
        return "color: #FF6B6B" if isinstance(v, (int, float)) and v else ""

    st.dataframe(df_cov.style.map(_flag_half, subset=["Half a match", "No events"]),
                 hide_index=True, use_container_width=True)


# ── Download freshness, per league ───────────────────────────────────────────
# Moved here from the Downloads page: this is a health question, and it kept
# the selection UI over there tangled up with a status table.
#
# SUPERSEDED by Match coverage above, and kept only while Bulk Actions still
# runs. "When was this team last fetched" is the wrong question once ingest is
# per game: it is exactly the measure under which a half-ingested match looks
# fine. Retire it with Bulk Actions.

st.header("Download freshness")
st.caption("Per TEAM, and superseded by Match coverage above — a team can be "
           "freshly downloaded while its matches are half empty. Kept while "
           "Bulk Actions is still in use.")


def _load_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


events_lu = _load_json(LAST_UPDATED_PATH)
minutes_lu = _load_json(MINUTES_LAST_UPDATED_PATH)

# Both files were keyed by team abbrev until Aug 2026; 11 abbrevs collide
# across 444 teams (POR is Porto, Portsmouth AND Portugal), so a lookup by
# abbrev could report another team's timestamp. Prefer team_id, fall back to
# abbrev so a not-yet-migrated file still shows something.
def _lookup(store, team):
    return store.get(team["team_id"]) or store.get(team["abbrev"]) or "Never"


stale_cutoff = datetime.now() - timedelta(days=7)


def _highlight_stale(val):
    if val == "Never":
        return "color: #FF6B6B"
    try:
        if datetime.strptime(str(val).strip(), "%b %d, %Y  %H:%M") < stale_cutoff:
            return "color: #FF6B6B"
    except ValueError:
        pass
    return ""


by_league = group_teams_by_league(config)

for league_name in sorted(by_league):
    league_teams = by_league[league_name]
    rows = [{
        "Team": t["name"],
        "Events last downloaded": _lookup(events_lu, t),
        "Minutes last downloaded": _lookup(minutes_lu, t),
    } for t in sorted(league_teams, key=lambda x: x["name"])]
    n_stale = sum(1 for r in rows
                  if _highlight_stale(r["Events last downloaded"]))
    header = f"{league_name}  ({len(league_teams)} teams"
    header += f", {n_stale} stale)" if n_stale else ")"
    with st.expander(header):
        st.dataframe(
            pd.DataFrame(rows).style.map(
                _highlight_stale,
                subset=["Events last downloaded", "Minutes last downloaded"],
            ),
            hide_index=True,
            use_container_width=True,
        )
