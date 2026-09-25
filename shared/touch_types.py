"""Touch types: one source for the partition, the sidebar and the chart's deck.

WHAT A TOUCH IS. TruMedia's own Touches stat is `count(event.toucher)` - every
event whose toucher field is set. On production (Premier League 2025/26,
checked 2026-09-25) that is exactly 22 play types, and the toucher always
belongs to the event's team. Everything below is those 22 types, split into
football words - plus five OPT-IN extras that TruMedia does not count, which
are off by default so that "everything ticked" means TruMedia's number.

A PARTITION, NOT A LIST OF FILTERS. Every row lands in exactly one type, so the
sidebar's counts always sum to the total on the chart. That forces the set
pieces to be carved OUT of the play types they belong to: a corner is a Pass
and a penalty is a shot, and without the carve-out a corner would be counted
under "open-play passes" and "corners" at once. Carve-outs win (see
`classify`), in this order: kick-off, corner, throw-in, goal kick, free kick,
penalty.

How each is tagged (all confirmed on production):
  kick-off     Pass with q279 - 1,805 in the season = 760 half starts + 1,045
               goals, exactly
  corner       Pass with PassType 'Corner' (taken at x 99.5). SHOTS tagged
               'Corner' are shots from corner SITUATIONS, in the box - they
               stay shots
  throw-in     Pass with PassType 'Throw-In'
  goal kick    Pass with PassType 'Goal Kick'
  free kick    Pass/OffsidePass with q5, or a shot with ShotPlayStyle
               'Direct Free Kick'
  penalty      a shot with q9. A Save with q9 is the KEEPER'S touch and stays a
               save; a FreeKick with q9 is the foul that won it
  fouls won    FreeKick rows carry a toucher only for the player FOULED (7,925
               of 8,226 fouls won, none of the fouls committed) - so this is
               the player who had the ball, never the fouler

The extras come from rows without a toucher, attributed by primaryPlayerId:
ball recoveries, aerials won, keeper pick-ups and sweeps - and carry starts,
which are not rows at all but a location carried on the event that ENDS a
carry (CarryStartX/Y: 62% of passes, 74% of take-ons).
"""
import pandas as pd

ON_THE_BALL = 'On the ball'
SHOOTING = 'Shooting'
DEFENDING = 'Defending'
GOALKEEPING = 'Goalkeeping'
FOULS = 'Fouls'
SET_PIECES = 'Set pieces'
EXTRAS = 'Extras'
GROUPS = [ON_THE_BALL, SHOOTING, DEFENDING, GOALKEEPING, FOULS, SET_PIECES, EXTRAS]

SHOTS = {'Goal', 'AttemptSaved', 'Miss', 'Post', 'PenaltyGoal'}
# Rows the builder fetches WITHOUT a toucher, for the opt-in extras.
EXTRA_PLAY_TYPES = ('BallRecovery', 'Aerial', 'Pickup', 'Sweeper')


class TouchType:
    """One checkbox. `phrase` is how it reads in the chart's deck ("excludes
    corners"); `note` is the sidebar tooltip - the one place a definition is
    allowed, because a label names and a tooltip explains."""

    def __init__(self, tid, label, group, phrase=None, default=True, note=None):
        self.id, self.label, self.group = tid, label, group
        self.phrase = phrase or label.lower()
        self.default = default
        self.note = note


TYPES = [
    TouchType('pass', 'Open-play passes', ON_THE_BALL, phrase='open-play passes'),
    TouchType('offside_pass', 'Offside passes', ON_THE_BALL),
    TouchType('ball_touch', 'Ball touches', ON_THE_BALL,
              note="Opta's BallTouch: a touch that does not keep the ball - a "
                   "miscontrol, or the ball striking the player."),
    TouchType('take_on', 'Take-ons', ON_THE_BALL,
              note='Every attempt to beat a man, won or lost.'),
    TouchType('dispossessed', 'Dispossessed', ON_THE_BALL,
              phrase='times dispossessed'),
    TouchType('skill', 'Skill moves', ON_THE_BALL),
    TouchType('goal', 'Goals', SHOOTING),
    TouchType('saved_shot', 'Saved or blocked', SHOOTING,
              phrase='saved or blocked shots'),
    TouchType('miss', 'Off target', SHOOTING, phrase='shots off target'),
    TouchType('woodwork', 'Woodwork', SHOOTING, phrase='shots off the woodwork'),
    TouchType('own_goal', 'Own goals', SHOOTING),
    TouchType('tackle', 'Tackles', DEFENDING),
    TouchType('interception', 'Interceptions', DEFENDING),
    TouchType('clearance', 'Clearances', DEFENDING),
    TouchType('block', 'Blocks', DEFENDING, note='Passes blocked.'),
    TouchType('save', 'Saves', GOALKEEPING),
    TouchType('claim', 'Claims', GOALKEEPING),
    TouchType('punch', 'Punches', GOALKEEPING),
    TouchType('smother', 'Smothers', GOALKEEPING),
    TouchType('drop', 'Drops', GOALKEEPING, phrase='dropped catches'),
    TouchType('foul_won', 'Fouls won', FOULS,
              note='Credited to the player fouled - never the fouler.'),
    TouchType('kickoff', 'Kick-offs', SET_PIECES),
    TouchType('corner', 'Corners', SET_PIECES),
    TouchType('throw_in', 'Throw-ins', SET_PIECES),
    TouchType('goal_kick', 'Goal kicks', SET_PIECES),
    TouchType('free_kick', 'Free kicks', SET_PIECES,
              note='Free kicks taken - passed or shot.'),
    TouchType('penalty', 'Penalties', SET_PIECES, note='Penalties taken.'),
    # Anything the feed adds later lands here rather than vanishing.
    TouchType('other', 'Other', ON_THE_BALL),
    TouchType('carry_start', 'Carry starts', EXTRAS, default=False,
              note='Where he picked the ball up and ran with it: the start of '
                   'every carry. Not a touch in the standard count.'),
    TouchType('recovery', 'Ball recoveries', EXTRAS, default=False,
              note='Not a touch in the standard count.'),
    TouchType('aerial_won', 'Aerials won', EXTRAS, default=False,
              note='Not a touch in the standard count.'),
    TouchType('pickup', 'Keeper pick-ups', EXTRAS, default=False,
              note='Not a touch in the standard count.'),
    TouchType('sweeper', 'Keeper sweeps', EXTRAS, default=False,
              note='Not a touch in the standard count.'),
]
BY_ID = {t.id: t for t in TYPES}
STANDARD = [t.id for t in TYPES if t.default]

_BY_PLAY_TYPE = {
    'Pass': 'pass', 'OffsidePass': 'offside_pass', 'BallTouch': 'ball_touch',
    'TakeOn': 'take_on', 'Dispossessed': 'dispossessed', 'GoodSkill': 'skill',
    'Goal': 'goal', 'AttemptSaved': 'saved_shot', 'Miss': 'miss', 'Post': 'woodwork',
    'OwnGoal': 'own_goal', 'PenaltyGoal': 'penalty',
    'Tackle': 'tackle', 'Interception': 'interception', 'Clearance': 'clearance',
    'BlockedPass': 'block',
    'Save': 'save', 'Claim': 'claim', 'Punch': 'punch', 'Smother': 'smother',
    'DropOfBall': 'drop',
    'FreeKick': 'foul_won',
}
_EXTRA_BY_PLAY_TYPE = {'BallRecovery': 'recovery', 'Aerial': 'aerial_won',
                       'Pickup': 'pickup', 'Sweeper': 'sweeper'}


def _flag(df, col):
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].eq(True)             # NULL / NaN / False -> False, no downcasting


def _str(df, col):
    if col not in df.columns:
        return pd.Series('', index=df.index)
    return df[col].fillna('').astype(str)


def classify(df):
    """One type id per row. `df` is build_touch_map's frame: a `has_toucher`
    flag separates the standard touches from the extras' rows, and a `source`
    of 'carry' marks the carry-start rows `expand_carries` adds."""
    pt = _str(df, 'playType')
    touch = _flag(df, 'has_toucher')
    out = pd.Series('other', index=df.index, dtype=object)

    mapped = pt.map(_BY_PLAY_TYPE)
    out[touch & mapped.notna()] = mapped[touch & mapped.notna()]

    # Carve-outs, applied lowest precedence first so the strongest writes last.
    is_pass = pt.isin(['Pass', 'OffsidePass'])
    is_shot = pt.isin(list(SHOTS))
    pass_type = _str(df, 'PassType')
    style = _str(df, 'ShotPlayStyle')
    carves = [
        ('penalty', touch & is_shot & _flag(df, 'q9')),
        ('free_kick', touch & ((is_pass & _flag(df, 'q5'))
                               | (is_shot & style.eq('Direct Free Kick')))),
        ('goal_kick', touch & pt.eq('Pass') & pass_type.eq('Goal Kick')),
        ('throw_in', touch & pt.eq('Pass') & pass_type.eq('Throw-In')),
        ('corner', touch & pt.eq('Pass') & pass_type.eq('Corner')),
        ('kickoff', touch & pt.eq('Pass') & _flag(df, 'q279')),
    ]
    for tid, mask in carves:
        out[mask] = tid

    extra = ~touch
    ex = pt.map(_EXTRA_BY_PLAY_TYPE)
    out[extra & ex.notna()] = ex[extra & ex.notna()]
    # An aerial is an extra only when WON; the loser's row is not a touch.
    out[extra & pt.eq('Aerial') & ~_flag(df, 'success')] = 'drop_row'
    if 'source' in df.columns:
        out[df['source'].eq('carry')] = 'carry_start'
    return out


def expand_carries(df):
    """Add one row per carry start, at the carry's START location.

    A carry is not an event: TruMedia attaches it to the event that ENDS it,
    as CarryStartX/Y. So the start becomes a row of its own here, with the
    fixture columns copied from the event that ended it."""
    if 'CarryStartX' not in df.columns:
        return df
    has = df['CarryStartX'].notna() & df['CarryStartY'].notna() & df['has_toucher'].eq(True)
    # Only where the start is a location nothing else draws. A carry that began
    # with a take-on, tackle or interception starts ON that touch (all 442 of
    # Liverpool's 2025/26 sat exactly on it) - drawing it again counts one
    # event twice. A received pass is not an event, so its start is new.
    if 'CarryStartType' in df.columns:
        has &= ~df['CarryStartType'].isin(['TAKE_ON', 'TACKLE', 'INTERCEPTION'])
    if not has.any():
        return df
    starts = df[has].copy()
    starts['EventX'] = starts['CarryStartX'].astype(float)
    starts['EventY'] = starts['CarryStartY'].astype(float)
    starts['source'] = 'carry'
    base = df.copy()
    if 'source' not in base.columns:
        base['source'] = 'event'
    return pd.concat([base, starts], ignore_index=True)


def annotate(df):
    """The frame with a `touch_type` column, carry starts expanded, and the
    losing aerials dropped. Everything downstream counts `touch_type`."""
    if df is None or df.empty:
        return df
    out = expand_carries(df)
    out = out.assign(touch_type=classify(out))
    return out[out['touch_type'] != 'drop_row'].reset_index(drop=True)


def counts(df):
    """{type id: n} over an annotated frame."""
    if df is None or df.empty:
        return {}
    return df['touch_type'].value_counts().to_dict()


def select(df, chosen):
    """The rows whose type is ticked.

    A carry that began with a ball recovery starts exactly on the recovery
    (838 of 838 checked) - so with Ball recoveries ticked too, those carry
    starts would draw the same event twice, and they go."""
    chosen = set(chosen)
    out = df[df['touch_type'].isin(chosen)]
    if {'carry_start', 'recovery'} <= chosen and 'CarryStartType' in out.columns:
        out = out[~(out['touch_type'].eq('carry_start')
                    & out['CarryStartType'].eq('BALL_RECOVERY'))]
    return out


def describe(chosen, present=None, extras=True):
    """The deck line: what differs from the standard count, in football words.

    Empty when exactly the standard types are ticked. Otherwise it names the
    SHORTER side - "Excludes corners and kick-offs" when a few are off,
    "Only take-ons and shots off target" when a few are on - and any extras
    ticked ("plus ball recoveries"). A whole group off or on is named as the
    group ("Excludes set pieces"). `present` limits the wording to types this
    scope actually has, so a keeper's absent saves are not "excluded" from an
    outfielder's map. `extras=False` is the CHART's deck: there the count
    already names what was added ("1,650 CARRY STARTS"), so the deck carries
    only what the count cannot show - what was left out."""
    chosen = set(chosen)
    standard = [t for t in TYPES if t.default]
    if present is not None:
        standard = [t for t in standard if t.id in present]
    off = [t for t in standard if t.id not in chosen]
    on = [t for t in standard if t.id in chosen]
    extras_on = [t for t in TYPES if not t.default and t.id in chosen]

    def named(items):
        by_group = {}
        for t in items:
            by_group.setdefault(t.group, []).append(t)
        words = []
        for g in GROUPS:
            members = [t for t in standard if t.group == g]
            got = by_group.get(g, [])
            if got and len(got) == len(members) and len(members) > 1:
                words.append(g.lower())
            else:
                words += [t.phrase for t in got]
        return words

    def join(words):
        if len(words) <= 1:
            return ''.join(words)
        return ', '.join(words[:-1]) + ' and ' + words[-1]

    parts = []
    if off:
        if len(on) < len(off) and on:
            parts.append('Only ' + join(named(on)))
        elif not on:
            parts.append('No standard touches')
        else:
            parts.append('Excludes ' + join(named(off)))
    if extras_on and extras:
        tail = join([t.phrase for t in extras_on])
        parts.append(('plus ' if parts else 'Plus ') + tail)
    return ', '.join(parts) if len(parts) > 1 else ''.join(parts)
