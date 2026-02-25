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
    """Return dict of league_name -> list of team dicts, only for teams with data.

    Each team dict has: team_id, display_name, abbrev.
    Teams are assigned to exactly one league bucket by priority order.
    Result is cached for 1 hour.
    """
    con = get_connection()
    rows = con.execute(
        "SELECT DISTINCT teamId, teamFullName, teamAbbrevName FROM events"
    ).fetchall()

    md_teams = {
        team_id: {'team_id': team_id, 'raw_name': full_name, 'abbrev': abbrev}
        for team_id, full_name, abbrev in rows
        if team_id
    }

    config = _load_config()
    abbrev_to_seasons = {t['abbrev']: t['season_ids'] for t in config['teams']}

    league_teams = {league: [] for league in LEAGUE_ORDER}

    for team_id, team in md_teams.items():
        abbrev = team['abbrev']
        season_ids = abbrev_to_seasons.get(abbrev, [])
        league = _get_team_league(season_ids)

        _, matched_name, _ = fuzzy_match_team(team['raw_name'], TEAM_COLORS)
        display_name = matched_name if matched_name else team['raw_name']

        league_teams[league].append({
            'team_id': team_id,
            'display_name': display_name,
            'abbrev': abbrev,
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
                   home_score, away_score, label.
    Result is cached for 1 hour.
    """
    con = get_connection()
    rows = con.execute("""
        SELECT gameId, Date, homeTeam, awayTeam, homeFinalScore, awayFinalScore
        FROM games
        WHERE homeTeamId = ? OR awayTeamId = ?
        ORDER BY Date DESC
    """, [team_id, team_id]).fetchall()

    games = []
    for game_id, date_str, home_team, away_team, home_score, away_score in rows:
        try:
            date_display = datetime.strptime(date_str, '%Y-%m-%d').strftime('%b %d, %Y')
        except Exception:
            date_display = date_str or 'Unknown date'

        _, home_clean, _ = fuzzy_match_team(home_team or '', TEAM_COLORS)
        _, away_clean, _ = fuzzy_match_team(away_team or '', TEAM_COLORS)
        home_display = home_clean if home_clean else home_team
        away_display = away_clean if away_clean else away_team

        label = f"{date_display}  —  {home_display} {home_score}–{away_score} {away_display}"
        games.append({
            'game_id': game_id,
            'date': date_str,
            'date_display': date_display,
            'home_team': home_display,
            'away_team': away_display,
            'home_score': home_score,
            'away_score': away_score,
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
