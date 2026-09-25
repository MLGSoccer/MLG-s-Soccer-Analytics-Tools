"""Tests for shared/touch_types.py - the touch partition.

The rows are shaped on what production actually carries (checked 2026-09-25):
kick-offs by q279, corners/throw-ins/goal kicks by PassType, free kicks by q5
or a Direct Free Kick shot, penalties by q9 on a shot, a toucher only on the
fouled player's FreeKick row, and the extras' rows without a toucher.
Run: py -m pytest test_touch_types.py -q
"""
import numpy as np
import pandas as pd
import pytest

from shared import touch_types as tt


def _row(pt, touch=True, **kw):
    r = dict(playType=pt, has_toucher=touch, PassType=None, q5=None, q9=None, q279=None,
             ShotPlayStyle=None, success=None, EventX=50.0, EventY=50.0,
             CarryStartX=None, CarryStartY=None, gameId='g1')
    r.update(kw)
    return r


def _frame():
    rows = [
        _row('Pass'), _row('Pass', q279=True, EventX=50, EventY=50),
        _row('Pass', PassType='Corner', EventX=99.5), _row('Pass', PassType='Throw-In'),
        _row('Pass', PassType='Goal Kick'), _row('Pass', q5=True), _row('OffsidePass', q5=True),
        _row('OffsidePass'), _row('BallTouch'), _row('TakeOn', success=True),
        _row('TakeOn', success=False), _row('Dispossessed'), _row('GoodSkill'),
        _row('Goal'), _row('Goal', PassType='Corner', EventX=94),      # a header FROM a corner
        _row('Goal', ShotPlayStyle='Direct Free Kick'),
        _row('PenaltyGoal', q9=True, ShotPlayStyle='Penalty'),
        _row('AttemptSaved', q9=True), _row('AttemptSaved'), _row('Miss'), _row('Post'),
        _row('OwnGoal'), _row('Tackle'), _row('Interception'), _row('Clearance'),
        _row('BlockedPass'), _row('Save'), _row('Save', q9=True),       # the keeper's pen save
        _row('Claim'), _row('Punch'), _row('Smother'), _row('DropOfBall'),
        _row('FreeKick', success=True), _row('FreeKick', success=True, q9=True),
        _row('BallRecovery', touch=False, success=True),
        _row('Aerial', touch=False, success=True), _row('Aerial', touch=False, success=False),
        _row('Pickup', touch=False), _row('Sweeper', touch=False),
        _row('Pass', CarryStartX=40.0, CarryStartY=20.0),
    ]
    return pd.DataFrame(rows)


EXPECT = ['pass', 'kickoff', 'corner', 'throw_in', 'goal_kick', 'free_kick', 'free_kick',
          'offside_pass', 'ball_touch', 'take_on', 'take_on', 'dispossessed', 'skill',
          'goal', 'goal', 'free_kick', 'penalty', 'penalty', 'saved_shot', 'miss', 'woodwork',
          'own_goal', 'tackle', 'interception', 'clearance', 'block', 'save', 'save',
          'claim', 'punch', 'smother', 'drop', 'foul_won', 'foul_won',
          'recovery', 'aerial_won', 'drop_row', 'pickup', 'sweeper', 'pass']


def test_every_row_gets_exactly_the_right_type():
    got = list(tt.classify(_frame()))
    assert got == EXPECT, [(i, e, g) for i, (e, g) in enumerate(zip(EXPECT, got)) if e != g]


def test_no_standard_touch_falls_to_other():
    assert 'other' not in set(tt.classify(_frame()))


def test_the_carve_outs_hold_their_precedence():
    # a set piece is never also counted as the play type it came from
    c = tt.classify(_frame())
    f = _frame()
    assert (c[f.PassType.eq('Corner') & f.playType.eq('Pass')] == 'corner').all()
    assert (c[f.PassType.eq('Corner') & f.playType.eq('Goal')] == 'goal').all()   # shot from a corner stays a shot
    assert (c[f.playType.eq('Save')] == 'save').all()                              # a keeper's pen save is his touch


def test_annotate_drops_lost_aerials_and_adds_carry_starts_at_the_start():
    a = tt.annotate(_frame())
    assert 'drop_row' not in set(a.touch_type)
    carries = a[a.touch_type == 'carry_start']
    assert len(carries) == 1
    assert carries.iloc[0].EventX == 40.0 and carries.iloc[0].EventY == 20.0
    # the event that ENDED the carry is still there, as itself
    assert len(a) == len(_frame()) - 1 + 1


def test_counts_sum_to_the_rows_drawn():
    a = tt.annotate(_frame())
    chosen = tt.STANDARD
    assert sum(v for k, v in tt.counts(a).items() if k in chosen) == len(tt.select(a, chosen))


def test_the_default_is_the_standard_definition_and_extras_are_off():
    extras = {t.id for t in tt.TYPES if t.group == tt.EXTRAS}
    assert extras == {'carry_start', 'recovery', 'aerial_won', 'pickup', 'sweeper'}
    assert not extras & set(tt.STANDARD)
    a = tt.annotate(_frame())
    std = tt.select(a, tt.STANDARD)
    assert std['has_toucher'].all() and (std.get('source', 'event') != 'carry').all()


def test_describe_names_the_shorter_side_in_football_words():
    s = tt.STANDARD
    assert tt.describe(s) == ''
    assert tt.describe([t for t in s if t not in ('corner', 'kickoff')]) == \
        'Excludes kick-offs and corners'
    set_pieces = {t.id for t in tt.TYPES if t.group == tt.SET_PIECES}
    assert tt.describe([t for t in s if t not in set_pieces]) == 'Excludes set pieces'
    assert tt.describe(['take_on']) == 'Only take-ons'
    assert tt.describe(s + ['recovery']) == 'Plus ball recoveries'
    assert tt.describe([t for t in s if t != 'corner'] + ['carry_start']) == \
        'Excludes corners, plus carry starts'
    # the chart's deck: exclusions only (the count names the extras)
    assert tt.describe(s + ['carry_start'], extras=False) == ''
    assert tt.describe([t for t in s if t != 'corner'] + ['carry_start'], extras=False) == \
        'Excludes corners'


def test_describe_ignores_types_this_scope_does_not_have():
    # an outfielder has no saves: unticking goalkeeping must not print "excludes goalkeeping"
    present = {'pass', 'take_on', 'tackle'}
    keeper = {t.id for t in tt.TYPES if t.group == tt.GOALKEEPING}
    assert tt.describe([t for t in tt.STANDARD if t not in keeper], present=present) == ''


def test_the_module_is_ascii():
    import pathlib
    data = (pathlib.Path(tt.__file__)).read_bytes()
    assert all(b < 128 for b in data)


def test_carry_starts_only_where_they_add_a_location():
    # a carry that began with a take-on / tackle / interception starts ON that
    # touch - drawing its start again would count one event twice
    rows = [_row('Pass', CarryStartX=30.0, CarryStartY=40.0, CarryStartType='PASS_RECEIVED'),
            _row('Pass', CarryStartX=31.0, CarryStartY=41.0, CarryStartType='TAKE_ON'),
            _row('Pass', CarryStartX=32.0, CarryStartY=42.0, CarryStartType='TACKLE'),
            _row('Pass', CarryStartX=33.0, CarryStartY=43.0, CarryStartType='INTERCEPTION'),
            _row('Pass', CarryStartX=34.0, CarryStartY=44.0, CarryStartType='BALL_RECOVERY')]
    a = tt.annotate(pd.DataFrame(rows))
    starts = a[a.touch_type == 'carry_start']
    assert sorted(starts.CarryStartType) == ['BALL_RECOVERY', 'PASS_RECEIVED']


def test_a_recovery_carry_start_is_dropped_when_recoveries_are_ticked_too():
    rows = [_row('Pass', CarryStartX=34.0, CarryStartY=44.0, CarryStartType='BALL_RECOVERY'),
            _row('BallRecovery', touch=False, success=True, EventX=34.0, EventY=44.0)]
    a = tt.annotate(pd.DataFrame(rows))
    both = tt.select(a, tt.STANDARD + ['carry_start', 'recovery'])
    assert (both.touch_type == 'carry_start').sum() == 0          # the recovery draws it once
    only = tt.select(a, tt.STANDARD + ['carry_start'])
    assert (only.touch_type == 'carry_start').sum() == 1
