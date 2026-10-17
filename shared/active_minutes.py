"""Active minutes in possession: the stat, from the live event log.

The stat is not in the feed. TruMedia's possessionSeconds / sequenceSeconds are
the clock span of a possession and include dead-ball time inside it, so active
time with the ball has to be BUILT from the events. The rules below were
settled with the owner on 2026-10-13 and measured on PL 2025/26 (380 games;
home + away + contested + dead = clock exactly) in One_Offs/inplay_possession.py,
which this module supersedes for the app.

Rules
1. A team's possession time runs from the event that opens its numbered
   possession (possessionSeqNum) to the opponent's action that ends control.
   Every inter-event interval whose earlier event carries a possession number
   is credited to that possession's team.
2. Bookend credit: an in-play gap whose earlier event carries NO possession
   number goes to team A if A has a numbered possession on both sides of the
   gap and the opponent has none in between.
3. Everything else in play is 'contested'.
Dead: a STOP event kills the ball until the next ball event (the restart).
Non-ball rows (subs, cards, referee notes) never open or close an interval.
The clock is integer seconds, cumulative across periods, but each period
restarts at its nominal start, so match length = sum of per-period spans.

Shots are playType in SHOT_TYPES (penalties included); xG is summed over them.
`xG is not null` is NOT a shot marker - hundreds of thousands of non-shot rows
carry it.

Data rule (owner): chart data is never saved for reuse. The one read per
league-season below is cached IN-PROCESS with an expiry, like every other
reader in shared/motherduck.py.
"""
import pandas as pd
import streamlit as st

from shared.motherduck import get_connection, team_label

STOP = {'Out', 'FreeKick', 'Corner', 'Goal', 'PenaltyGoal', 'OwnGoal', 'OffsidePass', 'OffsideProvoked',
        'GameInterruption', 'FoulThrowIn', 'ShootoutGoal', 'MissedShootout'}
NONBALL = {'SubstitutionOut', 'SubstitutionIn', 'Booking', 'Dismissal', 'NonPlayerYellowCard',
           'NonPlayerRedCard', 'ContentiousRefereeDecision', 'OfficialChange', 'Retired', 'ConditionsChange',
           'PenaltyFaced', 'ChanceMissed', 'Challenge', 'Obstacle', 'Shield', 'Error', 'GoodSkill',
           'OffsideProvoked', 'CrossNotClaimed', 'PlayerToKeeper'}
SHOT_TYPES = ('Goal', 'Miss', 'AttemptSaved', 'Post', 'PenaltyGoal')


def game_buckets(g):
    """One game's events, sorted by Period, gameClock, gameEventIndex -> seconds per bucket.

    Keys: each teamId, plus 'dead', 'contested', 'bookend', 'clock'.
    Moved unchanged from One_Offs/inplay_possession.py (the measured version).
    """
    out = {'dead': 0.0, 'contested': 0.0, 'bookend': 0.0, 'clock': 0.0}
    for t in g.teamId.unique():
        out[t] = 0.0
    for p, gp in g.groupby('Period'):
        if p == 0:
            continue
        gp = gp[~gp.playType.isin(NONBALL)].reset_index(drop=True)
        if len(gp) == 0:
            continue
        out['clock'] += gp.gameClock.max() - gp.gameClock.min()
        pt = gp.playType.tolist(); tc = gp.gameClock.tolist()
        tm = gp.teamId.tolist(); pn = gp.possessionSeqNum.tolist()
        tagged = [pd.notna(x) for x in pn]
        n = len(gp); k = 0
        while k < n - 1:
            if pt[k] in STOP:
                j = k + 1
                while j < n and pt[j] in STOP:
                    j += 1
                if j >= n:
                    out['dead'] += tc[n - 1] - tc[k]
                    break
                out['dead'] += tc[j] - tc[k]
                k = j
                continue
            if tagged[k]:
                out[tm[k]] += tc[k + 1] - tc[k]
                k += 1
                continue
            j = k
            while j < n and not tagged[j] and pt[j] not in STOP:
                j += 1
            span = tc[j] - tc[k] if j < n else tc[n - 1] - tc[k]
            i = k - 1
            while i >= 0 and not tagged[i] and pt[i] not in STOP:
                i -= 1
            before = tm[i] if i >= 0 and tagged[i] else None
            after = tm[j] if j < n and tagged[j] else None
            if before is not None and before == after:
                out[before] += span; out['bookend'] += span
            else:
                out['contested'] += span
            k = j if j > k else k + 1
    return out


def team_games(ev):
    """Events of many games -> one row per (game, team): own and opponent active minutes, shots, xG."""
    shot = ev[ev.playType.isin(SHOT_TYPES)]
    sh = shot.groupby(['gameId', 'teamId']).agg(shots=('playType', 'size'), xg=('xG', 'sum'))
    rows = []
    for gid, g in ev.groupby('gameId', sort=False):
        teams = [t for t in g.teamId.unique() if pd.notna(t)]
        if len(teams) != 2:
            continue                       # a one-sided or broken download is not a match
        b = game_buckets(g)
        for t, o in ((teams[0], teams[1]), (teams[1], teams[0])):
            rows.append(dict(gameId=gid, teamId=t, oppId=o, poss=b[t] / 60, opp_poss=b[o] / 60))
    tg = pd.DataFrame(rows)
    if tg.empty:
        return tg
    tg = tg.join(sh, on=['gameId', 'teamId'])
    tg = tg.join(sh.rename(columns={'shots': 'shots_allowed', 'xg': 'xg_allowed'}), on=['gameId', 'oppId'])
    return tg.fillna({'shots': 0, 'xg': 0.0, 'shots_allowed': 0, 'xg_allowed': 0.0})


def season_table(tg, names, feed_colors=None):
    """Team-games -> one row per team, in the units the chart draws. Per game = mean over the team's games."""
    s = tg.groupby('teamId').agg(
        games=('gameId', 'size'), mip=('poss', 'mean'), opp_mip=('opp_poss', 'mean'),
        shots=('shots', 'mean'), xg=('xg', 'mean'),
        opp_shots=('shots_allowed', 'mean'), opp_xg=('xg_allowed', 'mean')).reset_index()
    s['team'] = [names.get(t, t) for t in s.teamId]
    s['feed_color'] = [(feed_colors or {}).get(t) for t in s.teamId]
    s = s[(s.shots > 0) & (s.opp_shots > 0)].copy()
    s['mps'] = s.mip / s.shots                 # active minutes in possession per shot
    s['xps'] = s.xg / s.shots                  # xG per shot
    s['opp_mps'] = s.opp_mip / s.opp_shots
    s['opp_xps'] = s.opp_xg / s.opp_shots
    return s.reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def build_active_minutes(season_id):
    """One league-season, one read. Returns (team table, n_games).

    The league averages need every team in the competition, so the read is the
    whole season's events, narrowed to the eight columns the rules and the shot
    counts use.
    """
    con = get_connection()
    ev = con.execute(
        """select gameId, Period, gameClock, gameEventIndex, teamId, playType, possessionSeqNum, xG
           from events where seasonId = ?
           order by gameId, Period, gameClock, gameEventIndex""", [season_id]).df()
    if ev.empty:
        return pd.DataFrame(), 0
    meta = con.execute(
        "select teamId, any_value(teamFullName), any_value(newestTeamColor) from events where seasonId = ? group by 1",
        [season_id]).fetchall()
    names = {t: team_label(t, n) for t, n, _ in meta}
    feed_colors = {t: c for t, _, c in meta}
    tg = team_games(ev)
    return season_table(tg, names, feed_colors), int(tg.gameId.nunique()) if not tg.empty else 0
