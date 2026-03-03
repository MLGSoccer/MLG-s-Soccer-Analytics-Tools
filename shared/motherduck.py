"""
MotherDuck utilities for the CBS Sports Soccer Chart Builder.
Provides connection management and data access functions for chart pages.
"""
import os
import json
import duckdb
import streamlit as st
from datetime import datetime

from shared.colors import fuzzy_match_team, TEAM_COLORS

# ── League configuration ──────────────────────────────────────────────────────

# Season ID → curated league bucket mapping
_SEASON_TO_LEAGUE = {
    "51r6ph2woavlbbpk8f29nynf8": "Premier League",
    "80zg2v1cuqcfhphn56u4qpyqc": "La Liga",
    "2bchmrj23l9u42d68ntcekob8": "Bundesliga",
    "emdmtfr1v8rey2qru3xzfwges": "Serie A",
    "dbxs75cag7zyip5re0ppsanmc": "Ligue 1",
    "6i6n0jkbh9zzij6s8htfjh2j8": "MLS",
    "3ducfa94ga849pfvx8bjjgt1w": "NWSL",
    "221phckhkd7y6rg3uyava3ifo": "WSL",
    "2mr0u0l78k2gdsm79q56tb2fo": "Champions League",
}

# Priority order — first match wins for each team
LEAGUE_ORDER = [
    "Premier League",
    "La Liga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
    "MLS",
    "NWSL",
    "WSL",
    "Champions League",
    "Other",
]

# TruMedia playType → chart outcome mapping
_OUTCOME_MAP = {
    'Goal': 'Goal',
    'PenaltyGoal': 'Goal',
    'AttemptSaved': 'Saved',
    'Miss': 'Miss',
    'Post': 'Post',
    'Blocked': 'Blocked',
}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'data_manager', 'config.json')


def _load_config():
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def _get_team_league(season_ids):
    """Return the highest-priority league bucket for a list of season IDs."""
    matched = set()
    for sid in season_ids:
        league = _SEASON_TO_LEAGUE.get(sid)
        if league:
            matched.add(league)
    for league in LEAGUE_ORDER:
        if league in matched:
            return league
    return "Other"


# ── Connection ────────────────────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    """Open a cached MotherDuck connection for the Streamlit session."""
    token = st.secrets.get("MOTHERDUCK_TOKEN")
    if not token:
        raise ValueError("MOTHERDUCK_TOKEN not found in Streamlit secrets.")
    return duckdb.connect(f"md:soccer?motherduck_token={token}")


# ── Team and league data ──────────────────────────────────────────────────────

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

    for team in config['teams']:
        team_id = team.get('team_id')
        if not team_id:
            continue
        league = _get_team_league(team.get('season_ids', []))
        _, matched_name, _ = fuzzy_match_team(team['name'], TEAM_COLORS)
        display_name = matched_name if matched_name else team['name']
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


# ── Game data ─────────────────────────────────────────────────────────────────

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

        label = f"{date_display}  —  {home_display} {home_score}–{away_score} {away_display}"
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


# ── Shot data ─────────────────────────────────────────────────────────────────

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

    for ex, ey, xg, play_type, team_full, color, date, home, away, shot_style, shooter, h_score, a_score in rows:
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
        })

    return pd.DataFrame(data), meta or {}, team_colors


@st.cache_data(ttl=3600)
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
               ShotPlayStyle, shooter
        FROM events
        WHERE gameId IN ({placeholders})
          AND {team_clause}
          AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
        ORDER BY Date, gameId
    """, list(game_ids_tuple) + [team_id]).fetchall()

    if not rows:
        return pd.DataFrame(), {}, '#888888'

    data = []
    for game_id, ex, ey, xg, play_type, team_full, color, date, home, away, shot_style, shooter in rows:
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
        })

    shots_df = pd.DataFrame(data)

    # Add _match_id and _needs_flip (same logic as load_multi_match_shot_data)
    shots_df['_match_id'] = shots_df['gameId']
    shots_df['_needs_flip'] = False
    for match_id, group in shots_df.groupby('_match_id'):
        if group['EventX'].mean() < 50:
            shots_df.loc[group.index, '_needs_flip'] = True

    # Build multi_match_info
    team_name = shots_df['Team'].mode()[0] if not shots_df.empty else ''
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
    colors = shots_df['newestTeamColor'].dropna()
    team_color = colors.iloc[0] if not colors.empty else '#888888'

    multi_match_info = {
        'team_name': team_name,
        'date_range': date_range,
        'total_matches': total_matches,
        'player_list': player_list,
        'is_player_csv': False,
        'player_name': None,
    }

    return shots_df, multi_match_info, team_color


@st.cache_data(ttl=3600)
def build_shots_for_player(shooter_name):
    """Get all shots for a named player across the entire database.

    Used when a player has transferred — returns their complete shot record
    regardless of which team(s) they played for.

    Returns (shots_df, multi_match_info, team_color) with the same structure
    as build_shot_chart_multi().
    """
    import pandas as pd
    if not shooter_name:
        return pd.DataFrame(), {}, '#888888'

    con = get_connection()
    rows = con.execute("""
        SELECT gameId, EventXDecimal, EventYDecimal, xG, playType,
               teamFullName, newestTeamColor, Date, homeTeam, awayTeam,
               ShotPlayStyle, shooter
        FROM events
        WHERE shooter = ?
          AND playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post')
        ORDER BY Date, gameId
    """, [shooter_name]).fetchall()

    if not rows:
        return pd.DataFrame(), {}, '#888888'

    data = []
    for game_id, ex, ey, xg, play_type, team_full, color, date, home, away, shot_style, shooter in rows:
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

            if period == 2 and minute < 46:
                minute += 45
            elif period == 3 and minute < 91:
                minute += 90
            elif period == 4 and minute < 106:
                minute += 105

            _, clean_name, _ = fuzzy_match_team(team_full_name or '', TEAM_COLORS)
            team_display = clean_name if clean_name else team_full_name

            xg = float(xg) if xg else 0.0
            outcome = _OUTCOME_MAP.get(play_type, play_type or 'Unknown')

            shots.append((minute, team_display, xg, outcome))

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
    """Return own goal events for a game from the own_goals table.

    Returns list of {minute, credited_team} dicts, or empty list if none found.
    """
    if not game_id:
        return []
    con = get_connection()
    rows = con.execute(
        "SELECT minute, credited_team FROM own_goals WHERE gameId = ? ORDER BY minute",
        [game_id]
    ).fetchall()
    return [{"minute": r[0], "credited_team": r[1]} for r in rows]


@st.cache_data(ttl=3600)
def get_player_total_minutes(player_name, game_ids_tuple):
    """Return total minutes played by a player across the specified games.

    game_ids_tuple must be a tuple (not list) for cache hashability.
    Returns an int, or None if no minutes data found for any of the games.
    """
    if not player_name or not game_ids_tuple:
        return None
    con = get_connection()
    placeholders = ",".join("?" * len(game_ids_tuple))
    # Use fuzzy name match to handle API-Football vs TruMedia spelling differences.
    # For each game, pick the player_minutes row whose name best matches, then sum.
    row = con.execute(f"""
        SELECT SUM(minutes)
        FROM (
            SELECT gameId, minutes,
                   ROW_NUMBER() OVER (
                       PARTITION BY gameId
                       ORDER BY jaro_winkler_similarity(lower(playerName), lower(?)) DESC
                   ) AS rn,
                   jaro_winkler_similarity(lower(playerName), lower(?)) AS name_sim
            FROM player_minutes
            WHERE gameId IN ({placeholders})
        ) t
        WHERE rn = 1 AND name_sim > 0.72
    """, [player_name, player_name] + list(game_ids_tuple)).fetchone()
    total = row[0] if row else None
    return int(total) if total else None


@st.cache_data(ttl=3600)
def get_player_game_log(player_name):
    """Return per-game stats for a player joined with minutes played.

    Joins events + games + player_minutes to build a complete per-game record.
    Returns list of dicts matching the format expected by create_rolling_charts():
        {date, opponent, result, minutes, goals, xg, shots, season, team_name, team_color}
    Only includes games where minutes data is available (player_minutes join).
    """
    if not player_name:
        return []
    con = get_connection()
    # Use a fuzzy name match (jaro_winkler > 0.72) to bridge API-Football player
    # names stored in player_minutes and TruMedia names stored in events.shooter.
    # 0.72 chosen to handle "M. Salah" vs "Mohamed Salah" (score ~0.738) while
    # still rejecting clearly wrong matches (typically < 0.60).
    # The ranked_minutes CTE picks the best-matching player per game, so one
    # slightly-different spelling doesn't produce duplicate or missing rows.
    rows = con.execute("""
        WITH ranked_minutes AS (
            SELECT gameId, minutes, name_sim
            FROM (
                SELECT gameId, minutes,
                       jaro_winkler_similarity(lower(playerName), lower(?)) AS name_sim,
                       ROW_NUMBER() OVER (
                           PARTITION BY gameId
                           ORDER BY jaro_winkler_similarity(lower(playerName), lower(?)) DESC
                       ) AS rn
                FROM player_minutes
            ) t
            WHERE rn = 1
        )
        SELECT
            g.Date,
            e.opponent,
            g.homeFinalScore,
            g.awayFinalScore,
            e.homeTeam,
            e.teamFullName,
            rm.minutes,
            SUM(CASE WHEN e.playType IN ('Goal', 'PenaltyGoal') THEN 1 ELSE 0 END) AS goals,
            SUM(COALESCE(e.xG, 0)) AS xg,
            COUNT(*) AS shots,
            e.newestTeamColor,
            g.seasonId
        FROM events e
        JOIN games g ON e.gameId = g.gameId
        JOIN ranked_minutes rm ON e.gameId = rm.gameId AND rm.name_sim > 0.72
        WHERE e.shooter = ?
          AND e.playType IN ('Goal', 'PenaltyGoal', 'AttemptSaved', 'Miss', 'Post',
                             'BlockedShot', 'ShotOnPost', 'OwnGoal')
        GROUP BY g.Date, e.opponent, g.homeFinalScore, g.awayFinalScore,
                 e.homeTeam, e.teamFullName, rm.minutes, e.newestTeamColor, g.seasonId
        ORDER BY g.Date ASC
    """, [player_name, player_name, player_name]).fetchall()

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


@st.cache_data(ttl=3600)
def get_goal_scorers_for_game(game_id):
    """Return goal scorer info for a game from the events table.

    Only includes regular goals and penalties — own goals are handled separately.
    Returns list of {minute, player, team, pen} dicts, sorted by minute.
    """
    if not game_id:
        return []
    con = get_connection()
    rows = con.execute("""
        SELECT gameClock, Period, shooter, teamFullName, playType
        FROM events
        WHERE gameId = ?
          AND playType IN ('Goal', 'PenaltyGoal')
          AND shooter IS NOT NULL AND shooter != ''
        ORDER BY Period, gameClock
    """, [game_id]).fetchall()

    scorers = []
    for game_clock, period, shooter, team_full, play_type in rows:
        try:
            minute = int(float(game_clock or 0) / 60)
            period = int(period or 1)
            if period == 2 and minute < 46:
                minute += 45
            elif period == 3 and minute < 91:
                minute += 90
            elif period == 4 and minute < 106:
                minute += 105
            _, clean_name, _ = fuzzy_match_team(team_full or '', TEAM_COLORS)
            scorers.append({
                'minute': minute,
                'player': shooter,
                'team': clean_name if clean_name else team_full,
                'pen': play_type == 'PenaltyGoal',
            })
        except (ValueError, TypeError):
            continue
    return scorers


@st.cache_data(ttl=3600)
def get_red_cards_for_game(game_id):
    """Return red card events for a game from the cards table.

    Includes red cards and second yellows. Returns empty list if no API data
    was fetched for this game.
    Returns list of {minute, player, team, card_type} dicts, sorted by minute.
    """
    if not game_id:
        return []
    con = get_connection()
    rows = con.execute("""
        SELECT minute, playerName, teamName, card_type
        FROM cards
        WHERE gameId = ? AND card_type IN ('red', 'second_yellow')
        ORDER BY minute
    """, [game_id]).fetchall()
    return [
        {'minute': r[0], 'player': r[1], 'team': r[2], 'card_type': r[3]}
        for r in rows
    ]


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
