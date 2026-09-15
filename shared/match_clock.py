"""The match minute, written the way football writes it.

ONE HOME for the broadcast-minute rule. It lived in
mostly_finished_charts/xg_race_chart.py and the momentum page imported it
from there; the shot chart, needing the same rule for its per-shot rows,
would have been a third chart reaching into the race chart for a clock.
The race chart re-exports these names, so its own callers are unchanged.
"""

# Where each period's regular time ends. Anything past it is stoppage and is
# written the way broadcast writes it.
PERIOD_REGULAR_END = {1: 45, 2: 90, 3: 105, 4: 120}


def format_broadcast_minute(minute, period):
    """Render a match minute as football writes it: 45+2, not 47.

    The feed gives elapsed match minutes, so a goal in first-half stoppage
    time arrives as 47 and a late winner as 94. Printing those raw states a
    time that does not exist in how anyone reads a match: a 45-minute half
    has no 47th minute, it has 45+2.

    It also contradicted the xG race's own axis, which was moved to
    broadcast minutes earlier without the annotations following. On Wolves v
    Fulham the result was a goal labelled 47' sitting beside a HALF TIME line
    drawn at 47 - the chart asserting both that the half ended and that a
    goal came afterwards, at the same moment.

    Measured over 7,083 matches: 8.4% carry a first-half stoppage goal, plus
    7.2% of all goals fall past minute 90.

    COUNT THE MINUTE IN PROGRESS, NOT THE ONE COMPLETED. `minute` arrives as
    `int(gameClock / 60)` - the FLOOR of elapsed time - and football numbers
    the minute a goal happens IN. Elapsed 0:30 is the 1st minute, 2:12 is the
    3rd, 44:30 is the 45th. Printing the floor makes a goal 30 seconds in read
    as "0'", and every label one behind the broadcast.

    So the displayed minute is `floor + 1`, and stoppage falls out of the same
    arithmetic: anything past the period's regular end is written base+extra.

        elapsed  0:30  -> floor 0   -> 1        1st minute
        elapsed 44:30  -> floor 44  -> 45       last regular minute
        elapsed 45:54  -> floor 45  -> 45+1     first stoppage minute
        elapsed 47:58  -> floor 47  -> 45+3
        elapsed 95:24  -> floor 95  -> 90+6

    Two earlier versions got this wrong in opposite directions: the first
    tested `m > base` so Saka's 45:54 goal printed "45'", and the second fixed
    stoppage but left regular time a minute behind.

    A `period` of None means the source carries no period (a CSV, a legacy
    table): the minute is written flat, which is right for regular time and
    the best available past it.
    """
    m = int(minute) + 1
    base = PERIOD_REGULAR_END.get(int(period)) if period is not None else None
    if base is None or m <= base:
        return str(m)
    return f"{base}+{m - base}"
