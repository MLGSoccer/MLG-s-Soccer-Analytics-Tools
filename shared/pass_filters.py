"""Pass-map filter definitions: one source for the predicate, the UI and the caption.

A pass map is a lie without its denominator. Forty arrows on a pitch is
uninterpretable unless the reader knows they are "completed passes into the box
that led to a shot" - otherwise it reads as "this team barely had the ball". So
the active filter has to render as a sentence beside the marks, and that
sentence has to be generated from the same object that did the filtering. Define
a filter in two places and the caption drifts from the cut it describes without
ever looking wrong.

Two layers, and they mean different things:

  POPULATION   which games, whose passes. Applied in SQL by the builder, and it
               sets the DENOMINATOR. "1,004 passes" means "passes in the games
               and situations you selected".
  SELECTION    everything in this module. Applied in pandas, and it cuts the
               NUMERATOR. "218 of 1,004 (21.7%)".

That split is what makes "21.7% of their passes went into the box" a real claim
rather than a ratio between two arbitrary filters.
"""
import numpy as np
import pandas as pd

# ── Pitch geometry ───────────────────────────────────────────────────────────
# Opta 0-100 on both axes. These are the constants mplsoccer draws through
# pitch_type='opta', so the pass map and the shot chart agree by construction
# rather than by coincidence. Verified empirically: the measured penalty spot
# sits at x = 88.501 against mplsoccer's Opta constant of 88.5.
BOX_X, BOX_Y_LO, BOX_Y_HI = 83.0, 21.1, 78.9
SIX_X, SIX_Y_LO, SIX_Y_HI = 94.2, 36.8, 63.2

# The pitch is 105 x 68 m but the grid is 100 x 100, so a "20-unit" pass is a
# different real length sideways than forward. Every distance here converts.
# MetresFromGoal is NOT the cross-check - it implies a ~115 m pitch and reads
# 13.2 m at a penalty spot that is 11 m out. Treat it as an independent vendor
# metric and do not try to reconcile the two.
PITCH_LENGTH_M, PITCH_WIDTH_M = 105.0, 68.0

# TruMedia's ProgPass threshold. Their published equation carries a bare `10`,
# and the field is METRIC despite a description that says yards: 10 m is
# 9.5238 Opta length units, which reproduces their per-player totals at 89.1%
# exact / 99.4% within 5%. Reading it as 10 yards gives 20.0%.
PROG_MIN_X_UNITS = 10.0 / PITCH_LENGTH_M * 100.0


# The separator between qualifier clauses in the header. A middle dot, not
# " and " - see filter_phrase().
SEP = '  ·  '


def _flag(df, col):
    """A boolean column that may not exist on this database, as a real bool.

    Sparse qualifiers arrive NULL rather than False, and a stale mirror may not
    carry the column at all. Both have to read as "did not fire" instead of
    propagating NaN into a mask.
    """
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].fillna(False).astype(bool)


# ── Derived columns ──────────────────────────────────────────────────────────

def annotate_passes(df):
    """Add every computed column the filters and the renderer read.

    Done once, up front, so a filter is always a plain column comparison. The
    alternative - each filter deriving its own geometry - is how two filters end
    up disagreeing about where the final third starts.
    """
    if df.empty:
        return df
    df = df.copy()

    x0, y0 = df['EventX'].astype(float), df['EventY'].astype(float)
    # Ends that left the field overflow the grid, but only to -2 / 102, and
    # 99.75% of them are incomplete passes - the ball genuinely went out. They
    # are left UNCLAMPED and the axes are padded to show them. Clamping was the
    # first instinct and it is worse: 27k passes land on exactly x=100 and
    # composite into a solid wall along the goal line that reads as a finding.
    # A two-unit overshoot past the touchline is what happened.
    x1 = df['PassEndX'].astype(float)
    y1 = df['PassEndY'].astype(float)

    dx, dy = x1 - x0, y1 - y0
    df['dx'], df['dy'] = dx, dy
    df['dx_m'] = dx / 100.0 * PITCH_LENGTH_M
    df['dy_m'] = dy / 100.0 * PITCH_WIDTH_M
    df['length_m'] = np.hypot(df['dx_m'], df['dy_m'])

    # Outcome. `success` is a real column and is TRUE on every OffsidePass -
    # the ball did reach the teammate, the flag went up anyway. Left uncorrected
    # this puts 19k killed passes in the "completed" bucket, so offside is
    # subtracted here rather than at each call site.
    offside = df['playType'].eq('OffsidePass')
    df['is_offside'] = offside
    df['completed'] = df['success'].fillna(False).astype(bool) & ~offside

    df['restart'] = _restart_type(df)
    df['origin_third'] = _third(x0)
    df['dest_third'] = _third(x1)
    df['origin_channel'] = _channel(y0)
    df['dest_channel'] = _channel(y1)
    df['origin_in_box'] = _in_box(x0, y0)
    df['dest_in_box'] = _in_box(x1, y1)
    # The own defensive box is the mirror of the attacking one: coordinates are
    # already normalised so the team always attacks towards x=100.
    df['origin_in_own_box'] = _in_box(100 - x0, y0)
    df['direction'] = _direction(df['dx_m'], df['dy_m'])
    df['progressive'] = _progressive(df, x0, dx)
    df['pressure'] = _pressure(df)
    df['shot_assist'] = _flag(df, 'ChanceCreated') | _flag(df, 'IsAssist')
    df['led_to_shot'] = df.get('SequenceShotCount', pd.Series(np.nan, index=df.index)).fillna(0) > 0
    return df


def _pressure(df):
    """Pressure as four SELECTABLE states plus two that say why they are blank.

    `PressureReceived` is NULL on 52% of passes and that NULL is three
    different things, so it cannot be read as "no pressure" directly:

    1. **Seven competitions never record it at all** - UWCL, both NWSL seasons,
       Frauen-Bundesliga, Premiere Ligue and both WSL seasons, 815,682 passes.
       Every women's competition we hold. A filter reading NULL as "unpressured"
       would report every pass in those as unpressured.
    2. **Dead balls are always NULL** - high/medium/low are 0.0% dead balls,
       because nobody presses a corner. They complete at 72.5%, nothing like an
       unpressured pass.
    3. **What is left IS genuinely unpressured**, and it validates: 1,308,759
       open-play passes completing at 88.0%, above `low` at 87.4%, giving a
       clean monotonic gradient 88.0 / 87.4 / 80.1 / 75.9 across the four levels.

    Coverage is judged PER FRAME, so a chart of a competition that does not
    record pressure reports "not recorded" and selecting "None" returns nothing
    - which is the safe failure, rather than silently returning everything.
    """
    if 'PressureReceived' not in df.columns:
        return pd.Series('Not recorded', index=df.index, dtype=object)
    col = df['PressureReceived']
    if col.notna().sum() == 0:
        return pd.Series('Not recorded', index=df.index, dtype=object)
    out = col.astype(object).map(
        {'high': 'High', 'medium': 'Medium', 'low': 'Low'})
    blank = col.isna()
    open_play = df['restart'].eq('Open play')
    out[blank & open_play] = 'No pressure'
    out[blank & ~open_play] = 'Not applicable (dead ball)'
    return out


def _restart_type(df):
    """How the ball was put back into play - a single enumerated column.

    PassType presents as a complete taxonomy and is not: it is built from
    q6/q107/q124 only, so free-kick deliveries fall into its NULL and read as
    open play. That is the defect that blocked this chart, and q5 closes it.
    Verified mutually exclusive on 5.2M passes - every q5 pass carries a NULL
    PassType.
    """
    out = pd.Series('Open play', index=df.index, dtype=object)
    pt = df['PassType'] if 'PassType' in df.columns else pd.Series(None, index=df.index)
    out[_flag(df, 'q5')] = 'Free kick'
    out[pt.eq('Corner')] = 'Corner'
    out[pt.eq('Throw-In')] = 'Throw-in'
    out[pt.eq('Goal Kick')] = 'Goal kick'
    return out


def _third(x):
    return pd.cut(x, [-5, 100 / 3, 200 / 3, 105],
                  labels=['Defensive', 'Middle', 'Attacking']).astype(object)


def _channel(y):
    """Five lanes. Halfspaces are the point - thirds are too coarse to show
    that a side builds through the inside-left channel rather than the wing."""
    return pd.cut(y, [-5, 21.1, 36.8, 63.2, 78.9, 105],
                  labels=['Right wing', 'Right halfspace', 'Centre',
                          'Left halfspace', 'Left wing']).astype(object)


def _in_box(x, y):
    return (x >= BOX_X) & (y >= BOX_Y_LO) & (y <= BOX_Y_HI)


def _direction(dx_m, dy_m):
    """Forward / square / backward, in real metres.

    Bucketed on the angle rather than on dx alone so a 30 m ball across the back
    four does not read as forward because it drifted two metres upfield. +-22.5
    degrees of the touchline is square.
    """
    ang = np.degrees(np.arctan2(np.abs(dy_m), dx_m))
    return pd.Series(np.select([ang <= 67.5, ang <= 112.5],
                               ['Forward', 'Square'], default='Backward'),
                     index=dx_m.index, dtype=object)


def _progressive(df, x0, dx):
    """TruMedia's ProgPass equation, executed on our columns.

    Their definition, not ours, deliberately: the player comparison chart
    already shows ProgPass, so running their formula keeps the two charts in
    agreement instead of merely sharing a word. Published equation:

        [PsCmp] HAVING (event.passXLength >= 10
            OR ((start outside box) AND (end inside box)))
            AND event.x > 40

    PsCmp excludes crosses (q2), throw-ins (q107) and goalkeeper throws (q123).
    We hold the first two; q123 is the known residual and the reason this
    reproduces at 89.1% rather than 100%.
    """
    base = (df['completed']
            & ~_flag(df, 'IsCross')
            & ~df['restart'].eq('Throw-in'))
    into_box = ~df['origin_in_box'] & df['dest_in_box']
    return base & (x0 > 40) & ((dx >= PROG_MIN_X_UNITS) | into_box)


# ── The filter registry ──────────────────────────────────────────────────────
# Each entry is (id, label, group, kind, options, resolver, phrase).
#   kind      'multi'  - OR within the filter, over `options`
#             'flag'   - a single boolean cut
#             'range'  - a numeric floor
#   resolver  df -> Series to compare against the chosen options (multi), or
#             df -> boolean mask (flag), or a column name (range)
#   phrase    how the filter reads in the subtitle sentence


class Filter:
    """One filter: the predicate, the control, and how it reads in the caption.

    `phrase` is a template. It is the caption fragment, not the control's
    label, because the two want different words - the sidebar says "Origin
    third" and the sentence says "from the attacking third". Writing the
    fragment beside the predicate is what stops the two drifting apart.
    """

    def __init__(self, fid, label, group, kind, resolver,
                 options=None, phrase=None, note=None, option_labels=None,
                 negate=False, keep_case=False):
        self.id, self.label, self.group = fid, label, group
        self.kind, self.resolver = kind, resolver
        self.options = options or []
        self.phrase = phrase or label.lower()
        self.option_labels = option_labels or {}
        self.negate = negate
        # Player names are proper nouns; lower-casing them turned "Mohamed
        # Salah" into "mohamed salah" in the caption.
        self.keep_case = keep_case
        self.note = note

    def mask(self, df, value):
        if self.kind == 'multi':
            hit = self.resolver(df).isin(value)
            return ~hit if self.negate else hit
        if self.kind == 'flag':
            return self.resolver(df)
        if self.kind == 'range':
            return df[self.resolver].fillna(0) >= value
        raise ValueError(self.kind)

    def describe(self, value):
        if self.kind == 'multi':
            words = [self.option_labels.get(
                v, str(v) if self.keep_case else str(v).lower()) for v in value]
            return self.phrase.format(_or_list(words))
        if self.kind == 'flag':
            return self.phrase
        return self.phrase.format(f"{value:g}")


def _or_list(vals):
    vals = list(vals)
    if len(vals) == 1:
        return vals[0]
    return ", ".join(vals[:-1]) + " or " + vals[-1]


OUTCOME = 'Outcome'
ORIGIN = 'Origin'
DESTINATION = 'Destination'
VECTOR = 'Vector'
ATTRIBUTES = 'Pass type'
CONSEQUENCE = 'Consequence'
CONTEXT = 'Match context'
RECEIVER = 'Receiver'

_THIRDS = ['Defensive', 'Middle', 'Attacking']
_CHANNELS = ['Left wing', 'Left halfspace', 'Centre',
             'Right halfspace', 'Right wing']
_RESTARTS = ['Open play', 'Corner', 'Free kick', 'Throw-in', 'Goal kick']

FILTERS = [
    # -- Outcome. Blocked passes are ALREADY here as incomplete ones: the
    # BlockedPass play type is the DEFENDER's event, carries no endpoint, and
    # belongs to the other team. Unioning it in would double-count the pass and
    # credit the defending side's block to the attacking team.
    Filter('completed', 'Completed', OUTCOME, 'flag',
           lambda d: d['completed'], phrase='completed'),
    Filter('incomplete', 'Incomplete', OUTCOME, 'flag',
           lambda d: ~d['completed'], phrase='incomplete'),
    Filter('blocked', 'Blocked', OUTCOME, 'flag',
           lambda d: _flag(d, 'was_blocked'), phrase='blocked',
           note='Derived: an opponent BlockedPass within the next 3 events.'),
    Filter('offside', 'Offside', OUTCOME, 'flag',
           lambda d: d['is_offside'], phrase='offside'),

    # -- Origin
    Filter('origin_third', 'Origin third', ORIGIN, 'multi',
           lambda d: d['origin_third'], _THIRDS, phrase='from the {} third'),
    Filter('origin_channel', 'Origin channel', ORIGIN, 'multi',
           lambda d: d['origin_channel'], _CHANNELS, phrase='from the {}'),
    Filter('origin_own_box', 'From own box', ORIGIN, 'flag',
           lambda d: d['origin_in_own_box'], phrase='from their own box'),

    # -- Destination. The axis a shot chart does not have.
    #
    # "ENDING IN", not "INTO". These select on where the ball FINISHED, and
    # "into" claims it entered - which is false far more often than not.
    # Measured over a Liverpool season: of 7,617 passes ending in the
    # attacking third, 5,176 (68.0%) were ALREADY in the attacking third when
    # struck, so only 32% entered anything. Into the box is milder but real at
    # 26.3%. A cold analyst caught it from the chart alone, reasoning that
    # FORWARD 70% leaves 30% that cannot have crossed a vertical boundary, and
    # that 104 corners start beyond it by definition.
    Filter('dest_box', 'Ends in the box', DESTINATION, 'flag',
           lambda d: d['dest_in_box'], phrase='ending in the box'),
    Filter('dest_third', 'Destination third', DESTINATION, 'multi',
           lambda d: d['dest_third'], _THIRDS, phrase='ending in the {} third'),
    Filter('dest_channel', 'Destination channel', DESTINATION, 'multi',
           lambda d: d['dest_channel'], _CHANNELS, phrase='ending in the {}'),

    # -- Vector
    Filter('progressive', 'Progressive', VECTOR, 'flag',
           lambda d: d['progressive'], phrase='progressive',
           note="TruMedia's ProgPass equation, reproduced at 89.1% exact."),
    Filter('direction', 'Direction', VECTOR, 'multi',
           lambda d: d['direction'], ['Forward', 'Square', 'Backward'],
           phrase='played {}'),
    Filter('length', 'Minimum length (m)', VECTOR, 'range', 'length_m',
           phrase='{} m or longer'),

    # -- Pass type
    Filter('restart', 'Restart', ATTRIBUTES, 'multi',
           lambda d: d['restart'], _RESTARTS, phrase='from {}',
           option_labels={'Open play': 'open play', 'Corner': 'a corner',
                          'Free kick': 'a free kick', 'Throw-in': 'a throw-in',
                          'Goal kick': 'a goal kick'}),
    Filter('cross', 'Cross', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'IsCross'), phrase='crosses'),
    Filter('longball', 'Long ball', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q1'), phrase='long balls',
           note='Vendor judgement tag, not a distance rule - a geometric '
                'threshold reproduces only ~15% of players exactly.'),
    Filter('through', 'Through ball', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q4'), phrase='through balls'),
    Filter('cutback', 'Cutback', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q195'), phrase='cutbacks'),
    Filter('switch', 'Switch of play', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q196'), phrase='switches of play'),
    Filter('headed', 'Headed', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q3'), phrase='headed',
           note='BodyPart cannot answer this - it is a shot field, 96% "Other" '
                'on passes.'),
    Filter('chipped', 'Chipped', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q155'), phrase='chipped'),
    Filter('layoff', 'Lay-off', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q156'), phrase='lay-offs'),
    Filter('launch', 'Launch', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q157'), phrase='launches'),
    Filter('flickon', 'Flick-on', ATTRIBUTES, 'flag',
           lambda d: _flag(d, 'q168'), phrase='flick-ons'),
    Filter('pressure', 'Pressure on the passer', ATTRIBUTES, 'multi',
           lambda d: d.get('pressure'),
           ['No pressure', 'Low', 'Medium', 'High'],
           phrase='under {} pressure',
           option_labels={'No pressure': 'no', 'Low': 'low',
                          'Medium': 'medium', 'High': 'high'},
           note='Four levels. "No pressure" counts open-play passes only - '
                'dead balls never carry a pressure reading, and the seven '
                "women's competitions do not record it at all, so this filter "
                'returns nothing there rather than everything.'),
    Filter('lines_broken', 'Lines broken', ATTRIBUTES, 'range', 'LinesBroken',
           phrase='breaking {}+ lines',
           note='NULL means zero - no explicit zeros are stored.'),
    Filter('last_line', 'Beat the last line', ATTRIBUTES, 'flag',
           lambda d: d.get('LastLineBroken', pd.Series(None, index=d.index)).eq('last'),
           phrase='beating the last line'),

    # -- Receiver. The other end of the pass, and it only exists on completed
    # ones: measured, ZERO of 1.06M failed passes carry a receiver, so naming
    # one implicitly means the ball arrived. The phrase says "completed to"
    # rather than "to" because "to Salah" would otherwise read as "aimed at
    # Salah" and quietly include none of the attempts that missed him.
    Filter('receiver', 'Received by', RECEIVER, 'multi',
           lambda d: d.get('receiver'), [], phrase='completed to {}',
           keep_case=True,
           note='Only completed passes name a receiver, so this always implies '
                'completion. 0.26% of completed passes carry no named receiver '
                'and fall outside any selection here.'),
    Filter('not_receiver', 'Everyone except', RECEIVER, 'multi',
           lambda d: d.get('receiver'), [], phrase='not to {}', negate=True,
           keep_case=True,
           note='Excludes these receivers. Incomplete passes have no receiver, '
                'so they are KEPT by this filter - it removes named targets, '
                'not attempts.'),

    # -- Consequence
    Filter('shot_assist', 'Shot assist', CONSEQUENCE, 'flag',
           lambda d: d['shot_assist'], phrase='shot assists',
           note='ChanceCreated OR IsAssist. Verified disjoint - using either '
                'alone silently drops half the concept.'),
    Filter('assist', 'Assist', CONSEQUENCE, 'flag',
           lambda d: _flag(d, 'IsAssist'), phrase='assists'),
    Filter('led_to_shot', 'Led to a shot', CONSEQUENCE, 'flag',
           lambda d: d['led_to_shot'], phrase='that led to a shot'),
    Filter('led_to_goal', 'Led to a goal', CONSEQUENCE, 'flag',
           lambda d: _flag(d, 'SequenceScoredGoal'), phrase='that led to a goal'),
    Filter('reached_box', 'Sequence reached the box', CONSEQUENCE, 'flag',
           lambda d: _flag(d, 'SequenceReachedBox'), phrase='in moves that reached the box'),
    Filter('big_chance', 'Led to a big chance', CONSEQUENCE, 'flag',
           lambda d: _flag(d, 'shot_q214'), phrase='that led to a big chance'),
    Filter('xa', 'Minimum xA', CONSEQUENCE, 'range', 'xA', phrase='xA {}+'),

    # -- Match context
    Filter('match_state', 'Match state', CONTEXT, 'multi',
           lambda d: d.get('MatchState'), ['Ahead', 'Tied', 'Behind'],
           phrase='while {}'),
    Filter('period', 'Period', CONTEXT, 'multi',
           lambda d: d.get('Period'), [1, 2, 3, 4], phrase='in period {}'),
    Filter('position', 'Passer position', CONTEXT, 'multi',
           lambda d: d.get('PlayerPosition'), [], phrase='by their {}'),
    Filter('starter', 'Starters only', CONTEXT, 'flag',
           lambda d: _flag(d, 'Starter'), phrase='by starters'),
    Filter('venue', 'Home / away', CONTEXT, 'multi',
           lambda d: d['is_home'].map({True: 'Home', False: 'Away'}),
           ['Home', 'Away'], phrase='{}',
           option_labels={'Home': 'at home', 'Away': 'away'},
           note='Needs the games join - not derivable from events alone.'),
]

BY_ID = {f.id: f for f in FILTERS}
GROUPS = [OUTCOME, ORIGIN, DESTINATION, VECTOR, ATTRIBUTES, RECEIVER,
          CONSEQUENCE, CONTEXT]


def apply_filters(df, selections, match_all=True):
    """Cut the numerator out of the population.

    `selections` is {filter_id: value}; a value of None, False or [] is off.
    Returns (filtered_df, phrases). Each filter multi-selects as OR internally;
    `match_all` joins the filters with AND or OR.

    AND is never offered INSIDE a filter - a pass cannot start in two thirds at
    once, so the control would always return nothing.
    """
    if df.empty:
        return df, []
    masks, phrases = [], []
    for fid, value in selections.items():
        if fid not in BY_ID or value in (None, False, [], ''):
            continue
        f = BY_ID[fid]
        try:
            m = f.mask(df, value)
        except (KeyError, AttributeError, TypeError):
            # The column is not on this database. Skip the filter rather than
            # take the chart down, and say so in the caption by omission - the
            # denominator still reconciles.
            continue
        if m is None:
            continue
        masks.append(m.fillna(False).astype(bool))
        # (id, text), not bare text. The chart promotes some filters and
        # demotes others - who passed to whom belongs in the title, where the
        # ball ended up does not - and it cannot tell them apart once the
        # phrases are a flat list of strings.
        phrases.append((f.id, f.describe(value)))
    if not masks:
        return df, []
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if match_all else (combined | m)
    return df[combined], phrases


def _texts(phrases, skip=()):
    """Phrase strings, dropping any filter in `skip`.

    Accepts the (id, text) pairs apply_filters returns, and bare strings, so a
    caller that never needed the ids is unaffected.
    """
    out = []
    for p in phrases:
        if isinstance(p, tuple):
            fid, text = p
            if fid in skip:
                continue
        else:
            text = p
        if text:
            out.append(text)
    return out


def filter_phrase(phrases, match_all=True, skip=()):
    """The qualifier list ALONE, without the ratio - what the chart draws.

    The chart's header and `caption()` want different halves of the same
    statement. The panel already prints "17" over "of 21,950" at 42pt, so a
    header that ALSO says "17 of 21,950 passes (0.1%)" spends its most valuable
    line restating the biggest number on the page - and at five filters that
    restatement pushed the line to 122 characters and off both canvas edges.

    So the header takes this, and the counts stay in the panel where they are
    already large and already explained.

    The connective is not decoration. " and " between five clauses reads as
    prose and scans as none, but dropping to a bare list would silently lose
    the AND/OR distinction - "completed OR into the box" is a different chart
    from "completed AND into the box". AND is the default and a list of
    constraints is read as all-applying, so it goes bare; OR is the surprising
    case and says so.
    """
    phrases = _texts(phrases, skip)
    if not phrases:
        # Sentence case, like every other phrase this returns. In caps it was
        # the only subhead on the family set out differently, and a cold viewer
        # read the inconsistency as "two different people built these".
        return 'All passes'
    if len(phrases) == 1:
        return phrases[0]
    body = SEP.join(phrases)
    return body if match_all else f"ANY OF: {body}"


def caption(n_shown, n_total, phrases, match_all=True):
    """The sentence that makes the marks mean something.

    Always carries the denominator. "218 of 1,004 passes (21.7%)" is the
    difference between a chart and a lie - a shot chart never needs this
    because every shot is on it.

    Selected passers are NOT named here. They are a POPULATION control - they
    set the denominator, so "40 of 131 (30.5%)" already means "of this player's
    passes". The subject is named in the header instead; saying it in both
    places is how the two drift apart.
    """
    phrases = _texts(phrases)
    if not phrases and n_shown == n_total:
        # Nothing is cut, so there is no ratio to state and "554 of 554
        # (100.0%)" is noise that reads like a filter failed. The slot still
        # gets a line - an empty band where the qualifier normally sits looks
        # like a missing element rather than an absent filter.
        return "ALL PASSES"
    pct = (100.0 * n_shown / n_total) if n_total else 0.0
    head = f"{n_shown:,} of {n_total:,} passes ({pct:.1f}%)"
    joiner = " and " if match_all else " or "
    body = joiner.join(phrases)
    return f"{head} - {body}" if body else head
