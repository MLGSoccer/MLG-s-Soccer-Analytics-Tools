"""
MotherDuck utilities for the CBS Sports Soccer Chart Builder.
Provides connection management and data access functions for chart pages.
"""
import os
import json
import difflib
import duckdb
import streamlit as st
from datetime import datetime

from shared.colors import fuzzy_match_team, TEAM_COLORS
from shared.season_to_league import SEASON_TO_LEAGUE as _SEASON_TO_LEAGUE

# -- League configuration ------------------------------------------------------

# _SEASON_TO_LEAGUE is the single-source-of-truth mapping in
# shared/season_to_league.py. Imported above as `_SEASON_TO_LEAGUE` to
# preserve the existing private-name reference used by _get_team_league.
# LEAGUE_ORDER below remains the Streamlit-side curated UI bucket list -
# season IDs in _SEASON_TO_LEAGUE but not in LEAGUE_ORDER fall through
# to the "Other" bucket.

# Priority order -- first match wins for each team
# Note: WC bucket is year-tagged (not just "World Cup") because the
# tournament is quadrennial; a future "World Cup 2030" gets its own
# bucket rather than merging with 2026.
LEAGUE_ORDER = [
    "Premier League",
    "Championship",
    "La Liga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
    "MLS",
    "NWSL",
    "WSL",
    "Champions League",
    "World Cup 2026",
    "Other",
]

# Competitions played by NATIONAL teams rather than clubs.
#
# These have to be held out of any lookup that spans a player's whole record,
# because such a lookup is asking "what does this footballer do for his club"
# and a World Cup is a different question with different opposition, a
# different squad and a separate minutes pool. Today it is one competition;
# qualifiers, Nations League and continental tournaments will land in the same
# bucket, which is why this is a set of LEAGUE NAMES and not a season id.
#
# Left in place everywhere the competition is chosen explicitly: picking the
# World Cup in the Season/Competition dropdown scopes the whole page to it, so
# international charts stay fully reachable. This only bites the unscoped
# career path, where the two were being silently added together - 488 players
# carry both, and it was 16% of Mbappe's shot map, 39% of De Bruyne's and 93%
# of Lamine Yamal's, with 211,418 international minutes free to pool into club
# per-90 rates.
INTERNATIONAL_LEAGUES = {"World Cup 2026"}


def international_season_ids():
    """Season ids belonging to national-team competitions."""
    return {sid for sid, lg in _season_leagues().items()
            if lg in INTERNATIONAL_LEAGUES}


# TruMedia playType -> chart outcome mapping
_OUTCOME_MAP = {
    'Goal': 'Goal',
    'PenaltyGoal': 'Goal',
    'AttemptSaved': 'Saved',
    'Miss': 'Miss',
    'Post': 'Post',
    'Blocked': 'Blocked',
}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'data_manager', 'config.json')


def _load_config_file():
    # encoding is explicit: config.json holds accented team and league names
    # (Bayern Munchen, Atletico de Madrid, Premiere Ligue). Without it, open()
    # uses the platform default - UTF-8 on Streamlit Cloud but cp1252 on
    # Windows, which mojibakes every one of them locally.
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=3600)
def _load_config():
    """Config for the chart maker, read from MotherDuck first.

    The Data Manager writes config.json locally and mirrors it to MotherDuck in
    the same action. This app reads the mirror, so a new season or a promoted
    team is live as soon as it is downloaded - no commit, no deploy, no human
    step in between. That gap is what made downloaded seasons invisible here.

    The local file is the fallback: it covers a fresh database with nothing
    written yet, and any moment MotherDuck cannot be reached. If both fail there
    is nothing to degrade to, so the error propagates.

    Logs which source won, at WARNING for BOTH outcomes. Nothing in this app
    configures a logging level, so Python falls back to its last-resort
    handler at WARNING and silently drops INFO - which meant the success line
    never reached the Streamlit Cloud log and only a failure was visible. A
    diagnostic that is silent when things work cannot answer "which copy did
    this app read", which is the only question it exists to answer. The
    hour-long cache keeps it to one line an hour, not one per page.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from shared.config_store import read_config, config_status
        con = get_connection()
        cfg = read_config(con)
        if cfg:
            updated_at, _ = config_status(con)
            log.warning("config source=motherduck updated_at=%s seasons=%d teams=%d",
                     updated_at, len(cfg.get("seasons", {})), len(cfg.get("teams", [])))
            return cfg
        log.warning("config source=file reason=motherduck app_config empty")
    except Exception as e:
        log.warning("config source=file reason=%s: %s", type(e).__name__, e)
    cfg = _load_config_file()
    log.warning("config source=file seasons=%d teams=%d",
                len(cfg.get("seasons", {})), len(cfg.get("teams", [])))
    return cfg


def _season_leagues():
    """seasonId -> league name, from whichever config source is live.

    Deliberately not the module-level import from shared.season_to_league: that
    one reads the file at import time, which is right for the local tools but
    would pin this app to a file that no longer ships.
    """
    try:
        return _load_config().get("season_leagues", {}) or _SEASON_TO_LEAGUE
    except Exception:
        return _SEASON_TO_LEAGUE


def season_label(season_id, season_name=None):
    """Display label for a season. Never returns empty for a real season id.

    `season_name` comes from config.json's `seasons` map, so a season that has
    games in MotherDuck but no config entry resolves to ''. Callers used to test
    `if season_id and season_name`, which silently dropped that season from the
    Season dropdown and made its games unreachable in the UI - the failure looked
    like missing data rather than a missing label.

    Falling back to a marked id keeps the games reachable and points at the real
    cause, which is a config.json that has not been committed/deployed.
    """
    if season_name:
        return season_name
    if not season_id:
        return ""
    return f"Unlabelled season ({season_id[:8]})"


def _get_team_league(season_ids):
    """Return the highest-priority league bucket for a list of season IDs."""
    season_leagues = _season_leagues()
    matched = set()
    for sid in season_ids:
        league = season_leagues.get(sid)
        if league:
            matched.add(league)
    for league in LEAGUE_ORDER:
        if league in matched:
            return league
    return "Other"


# -- Connection ----------------------------------------------------------------

@st.cache_resource
def get_connection():
    """Open a cached MotherDuck connection for the Streamlit session.

    SOCCER_DB_PATH overrides the cloud connection with a local DuckDB file.
    The local mirror uses identical table and column names, so every query in
    this module works unchanged against it. Used to exercise the chart code
    against a candidate schema without touching production, and to develop
    locally without spending MotherDuck quota. Unset = normal cloud behaviour.
    """
    local = os.environ.get("SOCCER_DB_PATH")
    if local:
        return duckdb.connect(local, read_only=True)
    token = st.secrets.get("MOTHERDUCK_TOKEN")
    if not token:
        raise ValueError("MOTHERDUCK_TOKEN not found in Streamlit secrets.")
    return duckdb.connect(f"md:soccer?motherduck_token={token}")


# -- Team and league data ------------------------------------------------------

@st.cache_data(ttl=3600)
def get_teams_by_league():
    """Return dict of league_name -> list of team dicts.

    Each team dict has: team_id, display_name, abbrev.
    Built entirely from config.json (no DB query) so the initial page load is fast.
    Teams are assigned to exactly one league bucket by priority order.
    Result is cached for 1 hour.
    """
    config = _load_config()
    league_teams = {league: [] for league in LEAGUE_ORDER}

    # Build set of base names (with " Women" stripped) that conflict with a
    # non-women's team — those need to keep "Women" to disambiguate.
    all_names = [t['name'] for t in config['teams'] if t.get('team_id')]
    base_names = {n.replace(' Women', '') for n in all_names if ' Women' not in n}
    needs_women = {n for n in all_names if n.endswith(' Women') and n[:-6] in base_names}

    for team in config['teams']:
        team_id = team.get('team_id')
        if not team_id:
            continue
        league = _get_team_league(team.get('season_ids', []))
        _, matched_name, _ = fuzzy_match_team(team['name'], TEAM_COLORS)
        display_name = matched_name if matched_name else team['name']
        if display_name.endswith(' Women') and team['name'] not in needs_women:
            display_name = display_name[:-6]
        league_teams[league].append({
            'team_id': team_id,
            'display_name': display_name,
            'abbrev': team.get('abbrev', ''),
        })

    result = {}
    for league in LEAGUE_ORDER:
        teams = sorted(league_teams[league], key=lambda t: t['display_name'])
        if teams:
            result[league] = teams

    return result


# -- Game data -----------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_games_for_team(team_id):
    """Return list of game dicts for a team, sorted most recent first.

    Each dict has: game_id, date, home_team, away_team,
                   home_score, away_score, season_id, season_name, label.
    Result is cached for 1 hour.
    """
    con = get_connection()
    rows = con.execute("""
        SELECT gameId, Date, homeTeam, awayTeam, homeFinalScore, awayFinalScore, seasonId
        FROM games
        WHERE homeTeamId = ? OR awayTeamId = ?
        ORDER BY Date DESC
    """, [team_id, team_id]).fetchall()

    config = _load_config()
    season_names = config.get('seasons', {})

    games = []
    for game_id, date_str, home_team, away_team, home_score, away_score, season_id in rows:
        try:
            date_display = datetime.strptime(date_str, '%Y-%m-%d').strftime('%b %d, %Y')
        except Exception:
            date_display = date_str or 'Unknown date'

        _, home_clean, _ = fuzzy_match_team(home_team or '', TEAM_COLORS)
        _, away_clean, _ = fuzzy_match_team(away_team or '', TEAM_COLORS)
        home_display = home_clean if home_clean else home_team
        away_display = away_clean if away_clean else away_team

        season_name = season_names.get(season_id, '') if season_id else ''

        label = f"{date_display}  --  {home_display} {home_score}-{away_score} {away_display}"
        games.append({
            'game_id': game_id,
            'date': date_str,
            'date_display': date_display,
            'home_team': home_display,
            'away_team': away_display,
            'home_score': home_score,
            'away_score': away_score,
            'season_id': season_id,
            'season_name': season_name,
            'label': label,
        })

    return games


# -- Shot data -----------------------------------------------------------------

@st.cache_data(ttl=3600)
def get_player_current_team(player_id):
    """Return current team info for a player from MotherDuck.

    Matches on toucherId (= TruMedia playerId from player pool).
    Returns dict with 'color', 'team_name', 'abbrev', or None if not found.
    """
    if not player_id:
        return None
    con = get_connection()
    row = con.execute("""
        SELECT newestTeamColor, teamFullName, teamAbbrevName
        FROM events
        WHERE toucherId = ?
        AND newestTeamColor IS NOT NULL
        AND newestTeamColor != ''
        ORDER BY Date DESC
        LIMIT 1
    """, [str(player_id)]).fetchone()

    if row:
        return {'color': row[0], 'team_name': row[1], 'abbrev': row[2]}
    return None


@st.cache_data(ttl=3600)
def build_shot_chart_single(game_id):
    """Build shot chart data for a single game from MotherDuck.

    Returns (shots_df, match_info, team_colors) compatible with the shot
    chart helper functions in pages/4_Shot_Chart.py.
    """
    import pandas as pd
    con = get_connection()
    rows = con.execute("""
        SELECT e.EventXDecimal, e.EventYDecimal, e.xG, e.playType,
               e.teamFullName, e.newestTeamColor, e.Date,
               e.homeTeam, e.awayTeam, e.ShotPlayStyle, e.shooter,
               e.gameClock,
               g.homeFinalScore, g.awayFinalScore
        FROM events e
        JOIN games g ON e.gameId = g.gameId
        WHERE e.gameId = ?
          AND e.playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
    """, [game_id]).fetchall()

    if not rows:
        return pd.DataFrame(), {}, {}

    data = []
    team_colors = {}
    meta = None

    for (ex, ey, xg, play_type, team_full, color, date, home, away,
         shot_style, shooter, game_clock, h_score, a_score) in rows:
        _, clean_name, _ = fuzzy_match_team(team_full or '', TEAM_COLORS)
        team_display = clean_name if clean_name else team_full

        if color and team_display:
            team_colors[team_display] = color

        if meta is None:
            _, home_clean, _ = fuzzy_match_team(home or '', TEAM_COLORS)
            _, away_clean, _ = fuzzy_match_team(away or '', TEAM_COLORS)
            try:
                date_formatted = datetime.strptime(date, '%Y-%m-%d').strftime('%b %d, %Y').upper()
            except Exception:
                date_formatted = date or ''
            meta = {
                'date': date or '',
                'date_formatted': date_formatted,
                'home_team': home_clean if home_clean else home,
                'away_team': away_clean if away_clean else away,
                'home_score': int(h_score or 0),
                'away_score': int(a_score or 0),
            }

        data.append({
            'EventX': float(ex) if ex is not None else 50.0,
            'EventY': float(ey) if ey is not None else 50.0,
            'xG': float(xg) if xg else 0.0,
            'playType': play_type,
            'Team': team_display,
            'ShotPlayStyle': shot_style,
            'shooter': shooter,
            # gameClock is seconds elapsed; the per-shot block wants a minute.
            'minute': (float(game_clock) / 60) if game_clock is not None else None,
        })

    return pd.DataFrame(data), meta or {}, team_colors


@st.cache_data(ttl=3600)

def season_span_label(season_ids):
    """Label the season(s) a set of shots actually spans.

    Inferring this from the min and max YEAR of the shot dates - which is what
    the shot charts did - is wrong for the first half of every split-year
    season: every Premier League match played before January falls in one
    calendar year, so a 2025/26 chart reads "2025" until a January fixture
    lands, then silently corrects itself.

    The data already knows. `events.seasonId` is on every row and fully
    populated, so the honest answer is the distinct seasons present, resolved
    through config. That is also the only approach that survives the two ways
    these charts are scoped: an arbitrary set of gameIds, and
    build_shots_for_player, which spans a player's whole record across clubs
    and seasons.

    Returns the YEARS only. The competition is printed separately by the
    charts, and repeating it here would duplicate it in the common case and
    produce "Bundesliga 2023/24 - Premier League 2025/26" in the rare one.
    """
    ids = [s for s in dict.fromkeys(season_ids) if s]
    if not ids:
        return ''
    cfg = _load_config()
    names = cfg.get('seasons', {})
    leagues = cfg.get('season_leagues', {})

    def _years(sid):
        label = names.get(sid)
        if not label:
            return ''
        league = leagues.get(sid)
        # The config label fuses competition and years ("Premier League
        # 2025/26"). Removing a KNOWN prefix is safe; parsing years out of an
        # arbitrary string would not be.
        if league and label.startswith(league):
            return label[len(league):].strip() or label
        return label

    spans = sorted({y for y in (_years(s) for s in ids) if y})
    if not spans:
        return ''
    return spans[0] if len(spans) == 1 else f"{spans[0]}-{spans[-1]}"


def build_shot_chart_multi(game_ids_tuple, team_id, against=False):
    """Build multi-match shot chart data for a team from MotherDuck.

    game_ids_tuple: tuple of game IDs (tuple required for st.cache_data hashability).
    against: if True, returns opponent shots in those games instead of team's shots.

    Returns (shots_df, multi_match_info, team_color) compatible with
    create_multi_match_shot_chart() in the shot chart module.
    """
    import pandas as pd
    if not game_ids_tuple:
        return pd.DataFrame(), {}, '#888888'

    con = get_connection()
    placeholders = ','.join(['?' for _ in game_ids_tuple])
    team_clause = "teamId != ?" if against else "teamId = ?"

    rows = con.execute(f"""
        SELECT gameId, EventXDecimal, EventYDecimal, xG, playType,
               teamFullName, newestTeamColor, Date, homeTeam, awayTeam,
               ShotPlayStyle, shooter, seasonId, shooterId
        FROM events
        WHERE gameId IN ({placeholders})
          AND {team_clause}
          AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
        ORDER BY Date, gameId
    """, list(game_ids_tuple) + [team_id]).fetchall()

    if not rows:
        return pd.DataFrame(), {}, '#888888'

    data = []
    for (game_id, ex, ey, xg, play_type, team_full, color, date, home,
         away, shot_style, shooter, season_id, shooter_id) in rows:
        _, clean_name, _ = fuzzy_match_team(team_full or '', TEAM_COLORS)
        team_display = clean_name if clean_name else team_full
        data.append({
            'gameId': game_id,
            'EventX': float(ex) if ex is not None else 50.0,
            'EventY': float(ey) if ey is not None else 50.0,
            'xG': float(xg) if xg else 0.0,
            'playType': play_type,
            'Team': team_display,
            'newestTeamColor': color,
            'Date': date,
            'homeTeam': home,
            'awayTeam': away,
            'ShotPlayStyle': shot_style,
            'shooter': shooter,
            'shooterId': shooter_id,
            'seasonId': season_id,
        })

    shots_df = pd.DataFrame(data)

    # Add _match_id and _needs_flip (same logic as load_multi_match_shot_data)
    shots_df['_match_id'] = shots_df['gameId']
    shots_df['_needs_flip'] = False
    for match_id, group in shots_df.groupby('_match_id'):
        if group['EventX'].mean() < 50:
            shots_df.loc[group.index, '_needs_flip'] = True

    # Build multi_match_info
    # In shots-against mode the `Team` and `newestTeamColor` columns hold the
    # OPPONENTS' values (we queried teamId != ?), so deriving from shots_df
    # would label the chart with whichever opponent took the most shots. Look
    # up the selected team's own name and color directly instead.
    if against:
        own_row = con.execute(
            "SELECT teamFullName, newestTeamColor FROM events "
            "WHERE teamId = ? AND teamFullName IS NOT NULL "
            "ORDER BY newestTeamColor IS NULL LIMIT 1",
            [team_id]
        ).fetchone()
        if own_row:
            raw_name = own_row[0] or ''
            _, clean_name, _ = fuzzy_match_team(raw_name, TEAM_COLORS)
            team_name = clean_name if clean_name else raw_name
            team_color = own_row[1] or '#888888'
        else:
            team_name = ''
            team_color = '#888888'
    else:
        team_name = shots_df['Team'].mode()[0] if not shots_df.empty else ''
        colors = shots_df['newestTeamColor'].dropna()
        team_color = colors.iloc[0] if not colors.empty else '#888888'

    dates = shots_df['Date'].dropna().sort_values()
    date_range = ''
    if len(dates) > 0:
        try:
            first = datetime.strptime(dates.iloc[0], '%Y-%m-%d').strftime('%b %d').upper()
            last = datetime.strptime(dates.iloc[-1], '%Y-%m-%d').strftime('%b %d, %Y').upper()
            date_range = f"{first} - {last}" if dates.iloc[0] != dates.iloc[-1] else last
        except Exception:
            pass

    total_matches = shots_df['_match_id'].nunique()
    player_list = sorted(shots_df['shooter'].dropna().unique().tolist())

    multi_match_info = {
        'team_name': team_name,
        'date_range': date_range,
        'total_matches': total_matches,
        'player_list': player_list,
        'is_player_csv': False,
        'player_name': None,
        'season_span': season_span_label(shots_df['seasonId'].dropna()),
    }

    return shots_df, multi_match_info, team_color


@st.cache_data(ttl=3600)
def build_shots_for_player(shooter_name, shooter_id=None,
                           include_international=False):
    """Get all shots for one player across the entire database.

    Used when a player has transferred -- returns their complete shot record
    regardless of which team(s) they played for. That deliberate abandonment
    of team scope is also what made matching on the NAME unsafe: the caller
    picks a player from ONE team's list, and this then searched every team in
    the database for that string.

    Concretely, before this took an id: choosing Chelsea and then
    "Joao Pedro" returned 172 shots belonging to SIX different players
    (Chelsea, Gremio, Vasco da Gama, Corinthians, Remo, Atletico San Luis).
    The Chelsea player took 91. 511 (team, player) selections across 261
    teams were affected, including 24 in the Premier League. The chart's
    own "has shots for multiple teams -- showing all" notice made it read as
    one man's transfer history.

    Name matching fails the other way too: one player recorded under two
    spellings gets split. A single season holds 95 shots under
    "F. Azeez"/"O. Azeez"; asking by name for the first returned two of them.

    `shooter_id` fixes both, because an id merges every spelling of one
    player and separates players who happen to share a name. Callers should
    pass it -- it is on every row of the frame the player was chosen from,
    and no team anywhere in this database fields two different players with
    the same name, so the team the caller already picked identifies the
    player completely.

    Falling back to the name is kept for callers that have no id to hand. It
    resolves to an id when the name is unambiguous (so the split is fixed for
    them too) and reports the ambiguity in multi_match_info when it is not.

    National-team competitions are held out by default - see
    INTERNATIONAL_LEAGUES. This function is unscoped by season precisely so a
    transfer is not cut in half, and that same reach was quietly folding a
    World Cup into a club record. The count that was dropped comes back in
    multi_match_info so the page can say so rather than silently shrink.
    """
    import pandas as pd
    if not shooter_name and not shooter_id:
        return pd.DataFrame(), {}, '#888888'

    con = get_connection()
    ambiguous = None
    if not shooter_id:
        ids = [r[0] for r in con.execute("""
            SELECT DISTINCT shooterId FROM events
            WHERE shooter = ? AND shooterId IS NOT NULL
              AND playType IN ('Goal','PenaltyGoal','AttemptSaved','Miss','Post')
        """, [shooter_name]).fetchall()]
        if len(ids) == 1:
            shooter_id = ids[0]
        elif len(ids) > 1:
            ambiguous = len(ids)

    intl = set() if include_international else international_season_ids()
    key_col = "shooterId" if shooter_id else "shooter"
    key_val = shooter_id or shooter_name
    rows = con.execute(f"""
        SELECT gameId, EventXDecimal, EventYDecimal, xG, playType,
               teamFullName, newestTeamColor, Date, homeTeam, awayTeam,
               ShotPlayStyle, shooter, seasonId, shooterId
        FROM events
        WHERE {key_col} = ?
          AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
        ORDER BY Date, gameId
    """, [key_val]).fetchall()
    intl_shots = sum(1 for r in rows if r[12] in intl)
    if intl:
        rows = [r for r in rows if r[12] not in intl]

    if not rows:
        return pd.DataFrame(), {}, '#888888'

    data = []
    for (game_id, ex, ey, xg, play_type, team_full, color, date, home,
         away, shot_style, shooter, season_id, shooter_id) in rows:
        _, clean_name, _ = fuzzy_match_team(team_full or '', TEAM_COLORS)
        team_display = clean_name if clean_name else team_full
        data.append({
            'gameId': game_id,
            'EventX': float(ex) if ex is not None else 50.0,
            'EventY': float(ey) if ey is not None else 50.0,
            'xG': float(xg) if xg else 0.0,
            'playType': play_type,
            'Team': team_display,
            'newestTeamColor': color,
            'Date': date,
            'homeTeam': home,
            'awayTeam': away,
            'ShotPlayStyle': shot_style,
            'shooter': shooter,
            'shooterId': shooter_id,
            'seasonId': season_id,
        })

    shots_df = pd.DataFrame(data)
    shots_df['_match_id'] = shots_df['gameId']
    shots_df['_needs_flip'] = False
    for match_id, group in shots_df.groupby('_match_id'):
        if group['EventX'].mean() < 50:
            shots_df.loc[group.index, '_needs_flip'] = True

    dates = shots_df['Date'].dropna().sort_values()
    date_range = ''
    if len(dates) > 0:
        try:
            first = datetime.strptime(dates.iloc[0], '%Y-%m-%d').strftime('%b %d').upper()
            last = datetime.strptime(dates.iloc[-1], '%Y-%m-%d').strftime('%b %d, %Y').upper()
            date_range = f"{first} - {last}" if dates.iloc[0] != dates.iloc[-1] else last
        except Exception:
            pass

    total_matches = shots_df['_match_id'].nunique()
    # Most recent team = last row's team (data is ordered by Date)
    team_name = shots_df['Team'].iloc[-1] if not shots_df.empty else ''
    colors = shots_df['newestTeamColor'].dropna()
    team_color = colors.iloc[-1] if not colors.empty else '#888888'

    multi_match_info = {
        'team_name': team_name,
        'date_range': date_range,
        'total_matches': total_matches,
        'player_list': [shooter_name],
        'is_player_csv': False,
        'player_name': shooter_name,
        # Set only when a name had to stand in for an id AND matched several
        # players. The page shows it instead of the transfer notice, which
        # would otherwise present strangers as one man's career.
        'ambiguous_players': ambiguous,
        'international_shots_excluded': intl_shots,
        'season_span': season_span_label(shots_df['seasonId'].dropna()),
    }

    return shots_df, multi_match_info, team_color


@st.cache_data(ttl=3600)
def get_player_game_count(player_name):
    """Return the number of distinct games a player appeared in across the entire database.

    Uses the toucher column, which captures all player involvements (not just shots).
    Returns an int, or None if the player is not found.
    """
    if not player_name:
        return None
    con = get_connection()
    row = con.execute(
        "SELECT COUNT(DISTINCT gameId) FROM events WHERE toucher = ?",
        [player_name]
    ).fetchone()
    count = row[0] if row else 0
    return count if count > 0 else None


@st.cache_data(ttl=3600)
def build_shots_from_game(game_id):
    """Query MotherDuck for a game and return (shots, match_info, team_colors).

    Returns the same structure as parse_trumedia_csv() so it plugs directly
    into the existing chart infrastructure without modification.
    """
    con = get_connection()
    rows = con.execute("""
        SELECT
            gameClock, Period, teamFullName, xG, playType, newestTeamColor,
            Date, homeTeam, awayTeam
        FROM events
        WHERE gameId = ? AND shooter IS NOT NULL AND shooter != ''
        ORDER BY Period, gameClock
    """, [game_id]).fetchall()

    if not rows:
        return None, None, None

    shots = []
    team_colors = {}
    match_info = None
    has_extra_time = False
    first_half_end_minute = 45.0

    for game_clock, period, team_full_name, xg, play_type, team_color, date_str, home_team, away_team in rows:
        try:
            game_clock = float(game_clock or 0)
            minute = game_clock / 60
            period = int(period or 1)

            if period > 4:
                continue
            if period > 2:
                has_extra_time = True

            if period == 1:
                first_half_end_minute = max(first_half_end_minute, minute)

            _, clean_name, _ = fuzzy_match_team(team_full_name or '', TEAM_COLORS)
            team_display = clean_name if clean_name else team_full_name

            xg = float(xg) if xg else 0.0
            outcome = _OUTCOME_MAP.get(play_type, play_type or 'Unknown')

            # Tuple is (minute, team, xg, outcome, period). period flows
            # through to xg_race_chart's chrono-minute shift so Period 2
            # plots after Period 1 ends on the chart x-axis even when raw
            # broadcast minute would put them earlier (45+4 vs 46').
            shots.append((minute, team_display, xg, outcome, period))

            if team_color and team_display:
                team_colors[team_display] = team_color

            if match_info is None:
                try:
                    formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%b %d, %Y').upper()
                except Exception:
                    formatted_date = date_str

                _, home_clean, _ = fuzzy_match_team(home_team or '', TEAM_COLORS)
                _, away_clean, _ = fuzzy_match_team(away_team or '', TEAM_COLORS)

                match_info = {
                    'date': formatted_date,
                    'home_team': home_clean if home_clean else home_team,
                    'away_team': away_clean if away_clean else away_team,
                    'has_extra_time': False,
                    'first_half_end_minute': first_half_end_minute,
                }

        except (ValueError, TypeError):
            continue

    if match_info:
        match_info['has_extra_time'] = has_extra_time
        match_info['first_half_end_minute'] = first_half_end_minute

    return shots, match_info, team_colors


@st.cache_data(ttl=3600)
def get_own_goals_for_game(game_id):
    """Return own goal events for a game. Events first, own_goals as fallback.

    Returns [{minute, period, teamId, credited_team, source}].

    `teamId` is the CONCEDING side - the team whose player put it in their
    own net - which is the same thing `own_goals.credited_team` means.
    Verified against production 2026-08-29: 329 same side, 0 opposite.

    PREFER teamId. `credited_team` is a NAME, and it comes from
    API-Football's naming while `games` uses TruMedia's, so consumers have
    had to fuzzy-match the two. That is the bug class fixed across the shot
    maps on 2026-08-29; it is kept here only so existing callers do not
    break, and it is empty on the events path.

    The events path is also strictly more accurate: `Period` is real rather
    than inferred, because `own_goals.minute` collapses elapsed + extra so
    a 45+2 own goal is indistinguishable from a 45th-minute one.
    """
    if not game_id:
        return []
    con = get_connection()

    rows = con.execute("""
        SELECT CAST(gameClock AS DOUBLE) / 60.0 AS minute, teamId, Period
        FROM events
        WHERE gameId = ? AND playType = 'OwnGoal'
        ORDER BY Period, gameClock
    """, [game_id]).fetchall()
    if rows:
        return [{"minute": int(r[0]) if r[0] is not None else None,
                 "period": int(r[2]) if r[2] is not None else 1,
                 "teamId": r[1],
                 "credited_team": None,
                 "source": "events"}
                for r in rows]

    # Fallback: API-Football's table, for games not yet re-ingested.
    return [{"minute": r[0],
             "period": _infer_period_from_minute(r[0]),
             "teamId": None,
             "credited_team": r[1],
             "source": "own_goals"}
            for r in _fallback_rows(
                con,
                "SELECT minute, credited_team FROM own_goals "
                "WHERE gameId = ? ORDER BY minute", [game_id])]


@st.cache_data(ttl=3600)
def get_game_team_ids(game_id):
    """(homeTeamId, awayTeamId) for a game, or (None, None) if unknown."""
    if not game_id:
        return (None, None)
    row = get_connection().execute(
        "SELECT homeTeamId, awayTeamId FROM games WHERE gameId = ?",
        [game_id]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def own_goal_conceding_side(game_id, team_id, credited_team,
                            home_name, away_name):
    """Which SIDE put it in their own net: 'home', 'away', or None.

    Both own-goal sources name the CONCEDING team, but by different means:
    the events path sets `teamId` and leaves `credited_team` None, while the
    legacy `own_goals` table gives a NAME and no id.

    Callers used to fuzzy-match the name unconditionally, which raises
    AttributeError the moment the events path answers - and after the
    per-game migration that is every game with an own goal. This exists so
    that resolution lives in ONE place; the same block was duplicated across
    four Streamlit pages, which is why one miss became four.

    PREFER the id. The name path matches API-Football's spelling against
    TruMedia's, the bug class the migration removed everywhere else, so it
    is kept only for rows the events path cannot answer.
    """
    if team_id:
        home_id, away_id = get_game_team_ids(game_id)
        if team_id == home_id:
            return 'home'
        if team_id == away_id:
            return 'away'
    if credited_team:
        cr = credited_team.lower()
        h = difflib.SequenceMatcher(None, cr, (home_name or '').lower()).ratio()
        a = difflib.SequenceMatcher(None, cr, (away_name or '').lower()).ratio()
        return 'home' if h >= a else 'away'
    return None


@st.cache_data(ttl=3600)
def get_players_with_minutes_for_team(team_id):
    """Return players with minutes data for a team, from player_game_minutes.

    Returns list of {"player_id": ..., "player_name": ...} sorted by name.
    Returns empty list if no minutes data has been downloaded for this team.
    """
    if not team_id:
        return []
    con = get_connection()
    rows = con.execute("""
        SELECT DISTINCT playerId, playerFullName
        FROM player_game_minutes
        WHERE teamId = ?
          AND playerFullName IS NOT NULL
        ORDER BY playerFullName
    """, [team_id]).fetchall()
    return [{"player_id": r[0], "player_name": r[1]} for r in rows]


@st.cache_data(ttl=3600)
def get_player_total_minutes(player_name, game_ids_tuple, shooter_id=None):
    """Return (total_minutes, games_played) for a player across the specified games.

    game_ids_tuple must be a tuple (not list) for cache hashability.
    Sums player_game_minutes by playerId. Returns (int, int) or (None, None).

    The docstring used to say "no fuzzy name matching required", and that was
    true of the second step only: the id came out of a subquery keyed on the
    NAME, so the id step inherited every ambiguity the name had. Summed across
    the seven players called "J. Rodriguez" that produced 9,977 minutes over
    142 games. Pass shooter_id where you have one -- and every caller reading
    from a shot frame does, since shooterId is on the row.
    """
    if (not player_name and not shooter_id) or not game_ids_tuple:
        return None, None
    con = get_connection()
    placeholders = ",".join("?" * len(game_ids_tuple))
    if shooter_id:
        row = con.execute(f"""
            SELECT SUM(pgm.minutes), COUNT(DISTINCT pgm.gameId)
            FROM player_game_minutes pgm
            WHERE pgm.gameId IN ({placeholders}) AND pgm.playerId = ?
        """, list(game_ids_tuple) + [shooter_id]).fetchone()
        if not row or not row[0]:
            return None, None
        return int(row[0]), int(row[1])
    row = con.execute(f"""
        SELECT SUM(pgm.minutes), COUNT(DISTINCT pgm.gameId)
        FROM player_game_minutes pgm
        WHERE pgm.gameId IN ({placeholders})
          AND pgm.playerId IN (
              SELECT DISTINCT shooterId
              FROM events
              WHERE shooter = ?
                AND gameId IN ({placeholders})
                AND shooterId IS NOT NULL
          )
    """, list(game_ids_tuple) + [player_name] + list(game_ids_tuple)).fetchone()
    if not row or not row[0]:
        return None, None
    return int(row[0]), int(row[1])


@st.cache_data(ttl=3600)
def get_player_all_minutes(player_name, shooter_id=None,
                           include_international=False):
    """Return (total_minutes, games_played) for a player across all games in the DB.

    Used for multi-team players where the selected team's game_ids don't capture
    the player's full playing time (e.g. a mid-season transfer showing shots for
    both clubs). Matches the scope of build_shots_for_player() -- including its
    id, which matters here more than anywhere: this is unscoped by team AND by
    season, so a shared name gathers every namesake's minutes in the database.
    """
    if not player_name and not shooter_id:
        return None, None
    con = get_connection()
    intl = () if include_international else tuple(international_season_ids())
    excl = ""
    if intl:
        excl = ("AND pgm.gameId NOT IN (SELECT gameId FROM games WHERE seasonId "
                "IN (" + ",".join("?" * len(intl)) + "))")
    if shooter_id:
        row = con.execute(f"""
            SELECT SUM(pgm.minutes), COUNT(DISTINCT pgm.gameId)
            FROM player_game_minutes pgm
            WHERE pgm.playerId = ? {excl}
        """, [shooter_id, *intl]).fetchone()
        if not row or not row[0]:
            return None, None
        return int(row[0]), int(row[1])
    row = con.execute(f"""
        SELECT SUM(pgm.minutes), COUNT(DISTINCT pgm.gameId)
        FROM player_game_minutes pgm
        WHERE pgm.playerId IN (
            SELECT DISTINCT shooterId
            FROM events
            WHERE shooter = ?
              AND shooterId IS NOT NULL
        ) {excl}
    """, [player_name, *intl]).fetchone()
    if not row or not row[0]:
        return None, None
    return int(row[0]), int(row[1])


@st.cache_data(ttl=3600)
def get_player_game_log(player_id, player_name):
    """Return per-game stats for a player joined with minutes played.

    Drives from player_game_minutes (keyed by playerId -- no fuzzy matching) so
    shot-free games are included (shown as 0 shots/xg/goals).
    Returns list of dicts matching the format expected by create_rolling_charts():
        {date, opponent, result, minutes, goals, xg, shots, season, team_name, team_color}
    Only includes games where minutes data is available in player_game_minutes.
    """
    if not player_id:
        return []
    con = get_connection()
    rows = con.execute("""
        WITH shot_stats AS (
            SELECT gameId,
                   SUM(CASE WHEN playType IN ('Goal', 'PenaltyGoal') THEN 1 ELSE 0 END) AS goals,
                   SUM(COALESCE(xG, 0)) AS xg,
                   COUNT(*) AS shots
            FROM events
            WHERE shooterId = ?
              AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post',
                               'BlockedShot', 'ShotOnPost', 'OwnGoal')
            GROUP BY gameId
        ),
        team_info AS (
            SELECT gameId,
                   ANY_VALUE(newestTeamColor) AS newestTeamColor,
                   ANY_VALUE(opponent)        AS opponent
            FROM events
            WHERE toucherId = ?
            GROUP BY gameId
        )
        SELECT
            g.Date,
            COALESCE(
                ti.opponent,
                CASE WHEN pgm.teamFullName = g.homeTeam THEN g.awayTeam
                     WHEN pgm.teamFullName = g.awayTeam THEN g.homeTeam
                     ELSE 'Unknown' END
            ) AS opponent,
            g.homeFinalScore,
            g.awayFinalScore,
            g.homeTeam,
            pgm.teamFullName,
            pgm.minutes,
            COALESCE(ss.goals, 0) AS goals,
            COALESCE(ss.xg,    0.0) AS xg,
            COALESCE(ss.shots, 0) AS shots,
            ti.newestTeamColor,
            g.seasonId
        FROM player_game_minutes pgm
        JOIN games g ON pgm.gameId = g.gameId
        LEFT JOIN shot_stats ss ON pgm.gameId = ss.gameId
        LEFT JOIN team_info ti ON pgm.gameId = ti.gameId
        WHERE pgm.playerId = ?
        ORDER BY g.Date ASC
    """, [player_id, player_id, player_id]).fetchall()

    season_names = _load_config().get('seasons', {})

    matches = []
    for (date_str, opponent, home_score, away_score, home_team, team_full,
         minutes, goals, xg, shots, team_color, season_id) in rows:
        is_home = team_full == home_team
        team_score = home_score if is_home else away_score
        opp_score = away_score if is_home else home_score
        if team_score is not None and opp_score is not None:
            if team_score > opp_score:
                result = "W"
            elif team_score < opp_score:
                result = "L"
            else:
                result = "D"
        else:
            result = "?"
        matches.append({
            "date": date_str,
            "opponent": opponent or "Unknown",
            "result": result,
            "minutes": int(minutes or 0),
            "goals": int(goals or 0),
            "xg": float(xg or 0),
            "shots": int(shots or 0),
            "season": season_id or "",
            "season_name": season_names.get(season_id, "") if season_id else "",
            "team_name": team_full or "",
            "team_color": team_color or "#808080",
        })
    return matches


def _events_has(con, *cols):
    """Does `events` carry these columns?

    The card qualifiers were added in August 2026, so any database predating
    that - the local mirror, an old snapshot, a partially-migrated copy -
    does not have them. Querying for them there raises a BinderException,
    which would take a chart down rather than falling back.

    Checked rather than caught, so a genuine SQL error in the query still
    surfaces instead of being swallowed as "no data".
    """
    try:
        have = {r[0] for r in con.execute("DESCRIBE events").fetchall()}
    except Exception:
        return False
    return set(cols) <= have


def _fallback_rows(con, sql, params):
    """Query a table that API-Football owns and that is scheduled for deletion.

    Returns [] rather than raising if the table is gone. The migration drops
    `own_goals`, `cards`, `player_minutes` and `game_fixtures` once the feed
    carries their content; until then these readers try `events` first and
    come here. Afterwards the tables vanish and this returns nothing, which
    is correct - by then `events` is answering.

    Without this the drop in step A3 would take the charts down with it.
    """
    try:
        return con.execute(sql, params).fetchall()
    except Exception:
        return []


def _infer_period_from_minute(minute):
    """Heuristic: minute <= 50 = first half, else second half.

    Used when the underlying source (API-Football cards / own_goals, or
    user-entered own_goals) doesn't carry an explicit period flag.

    The ambiguity zone is minute 46-50: a card displayed as "49'" could
    be either first-half stoppage (elapsed=45 + extra=4) or a true
    49th-minute second-half event (elapsed=49 + extra=0). API-Football
    stores them collapsed as minute=49 either way. The threshold of 50
    catches typical 1-5 minute first-half stoppage; it misclassifies the
    rare case of >5min first-half stoppage as second-half. For events
    sourced from the events table directly (goals), use the explicit
    Period column instead of this heuristic.
    """
    if minute is None:
        return 1
    return 1 if minute <= 50 else 2


@st.cache_data(ttl=3600)
def get_goal_scorers_for_game(game_id):
    """Return goal scorer info for a game from the events table.

    Only includes regular goals and penalties -- own goals are handled separately.
    Returns list of {minute, period, player, team, pen} dicts, sorted by
    (period, minute) so a first-half stoppage goal (45+4) sorts BEFORE a
    second-half early goal (46') even though minute values would otherwise
    invert that order (49 > 46).
    """
    if not game_id:
        return []
    con = get_connection()
    rows = con.execute("""
        SELECT gameClock, Period, shooter, teamFullName, teamId, playType
        FROM events
        WHERE gameId = ?
          AND playType IN ('Goal', 'PenaltyGoal')
          AND shooter IS NOT NULL AND shooter != ''
        ORDER BY Period, gameClock
    """, [game_id]).fetchall()

    scorers = []
    for game_clock, period, shooter, team_full, team_id, play_type in rows:
        try:
            minute = int(float(game_clock or 0) / 60)
            _, clean_name, _ = fuzzy_match_team(team_full or '', TEAM_COLORS)
            scorers.append({
                'minute': minute,
                'period': int(period) if period is not None else 1,
                'player': shooter,
                'team': clean_name if clean_name else team_full,
                'team_id': team_id,
                'pen': play_type == 'PenaltyGoal',
            })
        except (ValueError, TypeError):
            continue
    return scorers


@st.cache_data(ttl=3600)
def get_red_cards_for_game(game_id):
    """Return red cards and second yellows. Events first, `cards` as fallback.

    Returns [{minute, period, player, playerId, teamId, team, card_type,
    rescinded, source}]. Empty when neither source has anything.

    Rescinded cards ARE included - see the note on the query below.

    On the events path `playerId` and `teamId` are populated and `Period` is
    real; on the fallback they are None and the period is inferred, because
    `cards.minute` collapses elapsed + extra. PREFER the ids - `team` is a
    name, and it is API-Football's name, not TruMedia's.
    """
    if not game_id:
        return []
    con = get_connection()

    # Events path. Nothing returns here until the predicate swap: cards are
    # filtered out by `AND ((event.toucher))` before they ever reach the CSV,
    # so no card event exists in production today regardless of the qualifier
    # columns being present. This is live the moment a game is re-ingested.
    #
    # RESCINDED CARDS ARE INCLUDED. The chart depicts the match, and a red
    # card sent a player off: the team played the rest of it a man down, and
    # that is the fact the annotation exists to explain. Rescission is an
    # administrative act taken days later and does not restore the eleventh
    # player retroactively. Dropping the card would leave an xG race or a
    # momentum chart with an hour of one-sided play and nothing to account
    # for it.
    #
    # `rescinded` is returned so a caller could surface it, but these are
    # ordinary red cards as far as the charts are concerned.
    #
    # VERIFIED 2026-08-29 (`data_manager/probe_q171_semantics.py`, big-5
    # 25/26): 760 reds and second yellows, 696 settled against minutes played.
    # ALL 696 were sent off; NONE finished the match. The 4 carrying q171 were
    # sent off too - so q171 is a post-match rescission, and there is no class
    # of red where the player stayed on. An in-match VAR overturn never
    # reaches the data as a red card event, so nothing here needs filtering.
    #
    # Minutes played is the signal, not "did he have events afterwards" - a
    # substituted player and a dismissed one look identical by that measure.
    rows = []
    if _events_has(con, 'qualifierRed', 'qualifierSecondYellow',
                   'qualifierCardRescinded', 'primaryPlayer',
                   'primaryPlayerId'):
        rows = con.execute("""
            SELECT CAST(gameClock AS DOUBLE) / 60.0 AS minute,
                   primaryPlayer, primaryPlayerId, teamId, teamFullName, Period,
                   CASE WHEN qualifierSecondYellow THEN 'second_yellow'
                        ELSE 'red' END AS card_type,
                   COALESCE(qualifierCardRescinded, FALSE) AS rescinded
            FROM events
            WHERE gameId = ?
              AND (qualifierRed OR qualifierSecondYellow)
            ORDER BY Period, gameClock
        """, [game_id]).fetchall()
    if rows:
        return [{'minute': int(r[0]) if r[0] is not None else None,
                 'period': int(r[5]) if r[5] is not None else 1,
                 'player': r[1], 'playerId': r[2],
                 'teamId': r[3], 'team': r[4],
                 'card_type': r[6], 'rescinded': bool(r[7]),
                 'source': 'events'}
                for r in rows]

    return [{'minute': r[0],
             'period': _infer_period_from_minute(r[0]),
             'player': r[1], 'playerId': None,
             'teamId': None, 'team': r[2],
             'card_type': r[3], 'rescinded': False, 'source': 'cards'}
            for r in _fallback_rows(
                con,
                "SELECT minute, playerName, teamName, card_type FROM cards "
                "WHERE gameId = ? AND card_type IN ('red', 'second_yellow') "
                "ORDER BY minute", [game_id])]


@st.cache_data(ttl=3600)
def get_team_rolling_xg_data(team_id, season_id=None):
    """Return per-game xG data for a team, ready for the rolling xG chart.

    Aggregates shot xG from the events table joined with games.
    season_id=None returns all seasons; pass a specific season_id to filter.

    Returns (matches, team_name, team_color) where matches is a list of dicts:
        {date, opponent, is_home, xg_for, xg_against, goals_for, goals_against, season}
    """
    con = get_connection()
    season_filter = "AND g.seasonId = ?" if season_id else ""
    params = [team_id, team_id, team_id, team_id]
    if season_id:
        params.append(season_id)

    rows = con.execute(f"""
        SELECT
            g.gameId, g.Date, g.homeTeam, g.awayTeam,
            g.homeTeamId, g.awayTeamId,
            g.homeFinalScore, g.awayFinalScore, g.seasonId,
            SUM(CASE WHEN e.teamId = g.homeTeamId THEN COALESCE(e.xG, 0) ELSE 0 END) AS home_xg,
            SUM(CASE WHEN e.teamId = g.awayTeamId THEN COALESCE(e.xG, 0) ELSE 0 END) AS away_xg,
            MAX(CASE WHEN e.teamId = ? THEN e.newestTeamColor END) AS team_color,
            MAX(CASE WHEN e.teamId = ? THEN e.teamFullName END) AS team_full_name
        FROM games g
        LEFT JOIN events e ON g.gameId = e.gameId
            AND e.playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
        WHERE (g.homeTeamId = ? OR g.awayTeamId = ?)
        {season_filter}
        GROUP BY g.gameId, g.Date, g.homeTeam, g.awayTeam, g.homeTeamId, g.awayTeamId,
                 g.homeFinalScore, g.awayFinalScore, g.seasonId
        ORDER BY g.Date ASC
    """, params).fetchall()

    season_names = _load_config().get('seasons', {})
    matches = []
    team_name = None
    team_color = None

    for (game_id, date_str, home_team, away_team, home_team_id, away_team_id,
         home_goals, away_goals, sid, home_xg, away_xg, color, full_name) in rows:
        is_home = (home_team_id == team_id)
        _, opp_clean, _ = fuzzy_match_team(
            (away_team if is_home else home_team) or '', TEAM_COLORS
        )
        opponent = opp_clean or (away_team if is_home else home_team) or 'Unknown'
        xg_for = float(home_xg or 0) if is_home else float(away_xg or 0)
        xg_against = float(away_xg or 0) if is_home else float(home_xg or 0)
        goals_for = int(home_goals or 0) if is_home else int(away_goals or 0)
        goals_against = int(away_goals or 0) if is_home else int(home_goals or 0)
        season_label = season_names.get(sid, '') if sid else ''

        if color and not team_color:
            team_color = color
        if full_name and not team_name:
            team_name = full_name

        matches.append({
            'date': date_str,
            'opponent': opponent,
            'is_home': is_home,
            'xg_for': xg_for,
            'xg_against': xg_against,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'season': season_label,
        })

    return matches, team_name, team_color


@st.cache_data(ttl=3600)
def get_shooters_for_team(team_id):
    """Return sorted list of distinct shooter names for a team.

    Only includes players who have at least one shot-type event for this team.
    """
    if not team_id:
        return []
    con = get_connection()
    rows = con.execute("""
        SELECT DISTINCT shooter
        FROM events
        WHERE teamId = ?
          AND shooter IS NOT NULL
          AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post',
                           'BlockedShot', 'ShotOnPost', 'OwnGoal')
        ORDER BY shooter
    """, [team_id]).fetchall()
    return [r[0] for r in rows]


# The 22 playTypes the event feed carries today. The downloader filters on
# `event.toucher` - ball-touch events only - and the migration to
# `event.primary` widens that to 47 types: cards, substitutions, corners, ball
# recoveries, aerials and more.
#
# Those extra types are not inert for the two charts below. They carry
# coordinates and sequenceIds, so an unpinned query silently re-bases both the
# sequence composition and the zone denominators - the new rows change which
# sequences clear `HAVING COUNT(*) > 1` and add touches to zones that never
# had them. Measured at ~4% movement in the choke-point values and +111 rows
# in the momentum feed: a change in what the chart SAYS, arriving as a side
# effect of an ingest change nobody would connect to it.
#
# Pinning holds both charts on the semantics they were designed and reviewed
# against. Widening them is an editorial decision to take deliberately, with a
# fresh look at the chart - not one to inherit from a downloader predicate.
CURRENT_FEED_PLAY_TYPES = (
    'Pass', 'BallTouch', 'Clearance', 'TakeOn', 'Tackle', 'FreeKick',
    'Dispossessed', 'Interception', 'BlockedPass', 'AttemptSaved', 'Save',
    'Miss', 'OffsidePass', 'Goal', 'Claim', 'DropOfBall', 'Punch', 'Post',
    'PenaltyGoal', 'Smother', 'GoodSkill', 'OwnGoal',
)
_FEED_TYPES_SQL = "(" + ", ".join(f"'{t}'" for t in CURRENT_FEED_PLAY_TYPES) + ")"


@st.cache_data(ttl=3600)
def get_sequence_choke_data(game_ids_tuple, team_id):
    """Return data for the Sequence Choke Point chart.

    Two queries:
      1. League-wide shot rate per pitch zone (5x4 = 20 zones, all teams).
      2. Per-touch rows for the target team: player, coordinates, seq_key, has_shot.

    game_ids_tuple must be a tuple (not list) for cache hashability.

    Returns (team_df, zone_rates_df, match_info):
      team_df columns: player, x, y, seq_key, has_shot
      zone_rates_df columns: zone_x, zone_y, shot_rate
      match_info dict: team_name, total_matches
    """
    import pandas as pd
    if not game_ids_tuple:
        return pd.DataFrame(), pd.DataFrame(), {}

    con = get_connection()

    # Query 1: league-wide zone shot rates (all teams, all games)
    zone_rows = con.execute(f"""
        WITH seq_info AS (
            SELECT gameId, sequenceId,
                   MAX(CASE WHEN playType IN ('Goal','PenaltyGoal','AttemptSaved','Miss','Post')
                            THEN 1 ELSE 0 END) as has_shot
            FROM events
            WHERE playType IN {_FEED_TYPES_SQL}
            GROUP BY gameId, sequenceId
            HAVING COUNT(*) > 1
        ),
        touches AS (
            SELECT e.gameId, e.sequenceId,
                   FLOOR(LEAST(e.EventXDecimal, 99.9) / 20.0) AS zone_x,
                   FLOOR(LEAST(e.EventYDecimal, 99.9) / 25.0) AS zone_y,
                   s.has_shot
            FROM events e
            JOIN seq_info s ON e.gameId = s.gameId AND e.sequenceId = s.sequenceId
            WHERE e.EventXDecimal IS NOT NULL AND e.EventYDecimal IS NOT NULL
              AND e.EventXDecimal >= 0 AND e.EventYDecimal >= 0
              AND e.playType IN {_FEED_TYPES_SQL}
        ),
        deduped AS (
            SELECT DISTINCT gameId, sequenceId, zone_x, zone_y, has_shot
            FROM touches
        )
        SELECT zone_x, zone_y,
               SUM(has_shot) * 1.0 / COUNT(*) AS shot_rate
        FROM deduped
        GROUP BY zone_x, zone_y
    """).fetchall()
    zone_rates_df = pd.DataFrame(zone_rows, columns=['zone_x', 'zone_y', 'shot_rate'])

    # Query 2: target team player touches with spatial coords and shot outcome
    game_ids = list(game_ids_tuple)
    ph = ','.join(['?'] * len(game_ids))
    touch_rows = con.execute(f"""
        WITH seq_info AS (
            SELECT gameId, sequenceId,
                   MAX(CASE WHEN playType IN ('Goal','PenaltyGoal','AttemptSaved','Miss','Post')
                            THEN 1 ELSE 0 END) as has_shot
            FROM events
            WHERE teamId = ? AND gameId IN ({ph})
              AND playType IN {_FEED_TYPES_SQL}
            GROUP BY gameId, sequenceId
            HAVING COUNT(*) > 1
        ),
        passer_t AS (
            SELECT e.gameId, e.sequenceId, e.passer AS player,
                   e.EventXDecimal AS x, e.EventYDecimal AS y
            FROM events e
            WHERE e.teamId = ? AND e.gameId IN ({ph})
              AND e.passer IS NOT NULL AND e.passer != ''
              AND e.EventXDecimal IS NOT NULL
              AND e.playType IN {_FEED_TYPES_SQL}
        ),
        receiver_t AS (
            SELECT e.gameId, e.sequenceId, e.receiver AS player,
                   e.PassEndXDecimal AS x, e.PassEndYDecimal AS y
            FROM events e
            WHERE e.teamId = ? AND e.gameId IN ({ph})
              AND e.receiver IS NOT NULL AND e.receiver != ''
              AND e.PassEndXDecimal IS NOT NULL
              AND e.playType IN {_FEED_TYPES_SQL}
        ),
        toucher_t AS (
            SELECT e.gameId, e.sequenceId, e.toucher AS player,
                   e.EventXDecimal AS x, e.EventYDecimal AS y
            FROM events e
            WHERE e.teamId = ? AND e.gameId IN ({ph})
              AND e.toucher IS NOT NULL AND e.toucher != ''
              AND e.EventXDecimal IS NOT NULL
              AND e.playType IN {_FEED_TYPES_SQL}
        ),
        all_touches AS (
            SELECT gameId, sequenceId, player, x, y FROM passer_t   UNION
            SELECT gameId, sequenceId, player, x, y FROM receiver_t UNION
            SELECT gameId, sequenceId, player, x, y FROM toucher_t
        )
        SELECT t.player, t.x, t.y,
               t.gameId || '_' || CAST(t.sequenceId AS VARCHAR) AS seq_key,
               s.has_shot
        FROM all_touches t
        JOIN seq_info s ON t.gameId = s.gameId AND t.sequenceId = s.sequenceId
        WHERE t.x >= 0 AND t.y >= 0
    """, [team_id] + game_ids + [team_id] + game_ids +
         [team_id] + game_ids + [team_id] + game_ids).fetchall()

    team_df = pd.DataFrame(touch_rows, columns=['player', 'x', 'y', 'seq_key', 'has_shot'])

    # Fetch team name from events
    name_row = con.execute(
        "SELECT teamFullName FROM events WHERE teamId = ? AND teamFullName IS NOT NULL LIMIT 1",
        [team_id]
    ).fetchone()
    team_name = name_row[0] if name_row else ''

    match_info = {
        'team_name': team_name,
        'total_matches': len(game_ids_tuple),
    }

    return team_df, zone_rates_df, match_info


@st.cache_data(ttl=3600)
def get_momentum_events(game_id):
    """Return momentum events for a single game.

    Returns (events_df, match_info) where events_df has columns:
      minute (float), team_side ('home'/'away'), event_type ('shot'/'corner'/'final_third')

    Coordinates are normalised attacking-direction so EventXDecimal > 66 = final third.
    """
    import pandas as pd
    if not game_id:
        return pd.DataFrame(), {}
    con = get_connection()

    rows = con.execute(f"""
        SELECT
            e.gameClock,
            e.Period,
            e.teamId,
            e.newestTeamColor,
            e.teamFullName,
            g.homeTeamId,
            g.awayTeamId,
            g.homeTeam,
            g.awayTeam,
            g.homeFinalScore,
            g.awayFinalScore,
            g.Date,
            CASE
                WHEN e.playType IN ('AttemptSaved','Miss','Post','Goal','PenaltyGoal','OwnGoal')
                     THEN 'shot'
                WHEN e.PassType = 'Corner'
                     THEN 'corner'
                ELSE 'final_third'
            END AS event_type
        FROM events e
        JOIN games g ON e.gameId = g.gameId
        WHERE e.gameId = ?
          -- `EventXDecimal > 66` is a catch-all on territory, not on action,
          -- so it admits whatever the feed happens to contain. Under
          -- `event.primary` that becomes 47 play types instead of 22 and the
          -- final-third bucket quietly swells. Pin it.
          AND e.playType IN {_FEED_TYPES_SQL}
          AND (
              e.playType IN ('AttemptSaved','Miss','Post','Goal','PenaltyGoal','OwnGoal')
              OR e.PassType = 'Corner'
              OR e.EventXDecimal > 66
          )
          AND e.Period IN (1, 2, 3, 4)
        ORDER BY e.Period, e.gameClock
    """, [game_id]).fetchall()

    if not rows:
        return pd.DataFrame(), {}

    import pandas as _pd
    cols = ['game_clock','period','team_id','color','team_name',
            'home_team_id','away_team_id','home_team','away_team',
            'home_score','away_score','date','event_type']
    df = _pd.DataFrame(rows, columns=cols)

    # gameClock is cumulative seconds across the whole match
    df['minute'] = df['game_clock'] / 60.0
    df['team_side'] = _pd.NA
    df.loc[df['team_id'] == df['home_team_id'], 'team_side'] = 'home'
    df.loc[df['team_id'] == df['away_team_id'], 'team_side'] = 'away'
    df = df.dropna(subset=['team_side'])

    # Team colors
    home_color = df.loc[df['team_side'] == 'home', 'color'].dropna().iloc[0] if not df[df['team_side']=='home']['color'].dropna().empty else '#4A90D9'
    away_color = df.loc[df['team_side'] == 'away', 'color'].dropna().iloc[0] if not df[df['team_side']=='away']['color'].dropna().empty else '#E05C5C'

    r = df.iloc[0]
    try:
        date_str = str(r['date'])[:10]
        from datetime import datetime as _dt
        date_display = _dt.strptime(date_str, '%Y-%m-%d').strftime('%B %d, %Y')
    except Exception:
        date_display = str(r['date'])

    # Real half-time minute = last Period 1 event's gameClock in minutes.
    # Default 45.0 if no Period 1 events found.
    p1 = df[df['period'] == 1]
    ht_minute = float(p1['minute'].max()) if not p1.empty else 45.0

    match_info = {
        'home_team':    r['home_team'],
        'away_team':    r['away_team'],
        'home_score':   int(r['home_score']) if r['home_score'] is not None else 0,
        'away_score':   int(r['away_score']) if r['away_score'] is not None else 0,
        'home_team_id': r['home_team_id'],
        'away_team_id': r['away_team_id'],
        'home_color':   home_color,
        'away_color':   away_color,
        'date':         date_display,
        'ht_minute':    ht_minute,
    }

    return df[['minute','period','team_side','event_type']].reset_index(drop=True), match_info
