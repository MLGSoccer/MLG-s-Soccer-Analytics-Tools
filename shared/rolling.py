"""Rolling-average and season-segmentation helpers for the xG trend charts.

ONE HOME for machinery that had four. `calculate_rolling_average`,
`find_season_boundaries` and `draw_season_boundaries` were defined separately
in mostly_finished_charts/team_rollingxg_chart.py, .../player_rollingxg_chart.py
and both dp_mostly_finished_charts equivalents. The CBS pair had already
drifted - byte-identical rolling code under differently-named private season
helpers, with `format_season_text` in only one of them. The DP pair belongs to
the deprecated dp_pages app and is deliberately left alone.

The two behaviour changes live here rather than in either chart, so the player
chart inherits them when it migrates:

1. A trailing mean is NaN until the window is genuinely full. The old code
   was expanding-then-rolling, so point 1 of a "10-game rolling average" was
   a single match drawn with the same weight as a true 10-game mean. Measured
   over production: 97 of 298 team-seasons had their y-ceiling set by an
   under-filled window, inflating the drawn peak by more than 15% over the
   real one - Southampton drawn at 4.53 against a true 2.07, Vitoria at 3.15
   against 1.07. Three cold reviews independently read the resulting decay
   curve as a team collapsing.

2. The window never reaches back across a season boundary. It used to:
   Arsenal's All-seasons chart ended at +0.61 from a window holding 8 matches
   of 2025/26 and 2 of 2026/27, while those 2 matches drawn alone read +1.68
   and +1.19. 120 of 458 production teams have at least one such window.
"""
import re

import numpy as np

# Season names arrive as 'Premier League 2025/26', 'MLS 2026' - competition
# first, year last. The year portion is what decides whether two entries are
# the same campaign.
_YEAR_TAIL = re.compile(r"\s*(\d{4}(?:[/-]\d{2,4})?)\s*$")

# The European competition names are long enough on their own to push a named
# pair past a 9:16 frame, which then fell back to "2 COMPETITIONS" - so the
# same chart family named the pair for Real Madrid and shrugged for Arsenal,
# purely because one string was eight characters longer. A cold viewer called
# that out as an inconsistency, and it was.
_SHORT_COMPETITION = {
    "UEFA CHAMPIONS LEAGUE": "UCL",
    "UEFA EUROPA LEAGUE": "UEL",
    "UEFA CONFERENCE LEAGUE": "UECL",
    "UEFA WOMEN'S CHAMPIONS LEAGUE": "UWCL",
}


def season_year(season_name):
    """'Premier League 2025/26' -> '2025/26'. Unparseable -> '' ."""
    m = _YEAR_TAIL.search(season_name or "")
    return m.group(1) if m else ""


def season_competition(season_name):
    """'Premier League 2025/26' -> 'Premier League'."""
    return _YEAR_TAIL.sub("", season_name or "").strip()


def find_season_segments(matches):
    """Split a match list where the season YEAR changes.

    Returns a list of dicts: {start, end, year, names} with `start`/`end` as
    1-indexed inclusive match numbers and `names` the full season names seen
    inside the segment.

    Detection is year-normalised so Premier League and Champions League inside
    one campaign do not split the line - but the FULL name is carried through,
    which the old code threw away before the subtitle could use it. That loss
    is why every single-competition chart was captioned '2025/26' with the
    competition unnamed, and why two-year selections came out as
    'N COMPETITIONS' counting years rather than competitions.
    """
    segments = []
    for i, match in enumerate(matches):
        name = match.get("season", "") or ""
        year = season_year(name)
        if segments and segments[-1]["year"] == year:
            seg = segments[-1]
            seg["end"] = i + 1
            if name and name not in seg["names"]:
                seg["names"].append(name)
        else:
            segments.append({"start": i + 1, "end": i + 1, "year": year,
                             "names": [name] if name else []})
    return segments


def segment_starts(segments):
    """The 1-indexed match numbers a rolling window must not reach back past."""
    return [s["start"] for s in segments]


def rolling_average(values, window=10, starts=None):
    """Trailing mean over `window` values, NaN wherever the window is not full.

    `starts` is the 1-indexed segment-start list from `segment_starts`; the
    window is not allowed to span one. NaN (rather than a shorter mean) is the
    point: matplotlib leaves a gap, so the chart draws the statistic it claims
    to draw and nothing else.
    """
    starts = sorted(starts or [1])
    out = []
    for i in range(len(values)):
        seg_start = 1
        for s in starts:
            if s <= i + 1:
                seg_start = s
            else:
                break
        available = (i + 1) - seg_start + 1
        if available < window:
            out.append(float("nan"))
        else:
            out.append(float(np.mean(values[i - window + 1:i + 1])))
    return out


def partial_rolling_average(values, window=10, starts=None, min_samples=1):
    """The lead-in: an EXPANDING mean where the trailing window is not yet full.

    The complement of `rolling_average` - non-NaN exactly where that returns
    NaN, plus the first full-window point so the two lines join rather than
    leaving a visible break. Drawn as a provisional line: the shape is
    continuous from early in the season, and its styling says the average
    behind it is not yet a full one.

    `min_samples=1`: every match a team has played appears on the chart.
    Starting at 3 was tried and rejected by the user - "I do not want games 1
    and 2 to be absent from the chart, it is too confusing for a viewer" - and
    they are right that a 38-match season whose line begins at match 3 invites
    the question more loudly than the caveat answers it.

    The cost is real and is paid in the AXIS, not here: from match 1 the
    lead-in exceeds the true peak on 48% of series, so the y-limit must be set
    by the full-window series with only a bounded stretch for this one. See
    `_PARTIAL_HEADROOM` in team_rollingxg_chart.py.
    """
    starts = sorted(starts or [1])
    full = rolling_average(values, window, starts)
    out = []
    for i in range(len(values)):
        seg_start = 1
        for s in starts:
            if s <= i + 1:
                seg_start = s
            else:
                break
        available = (i + 1) - seg_start + 1
        first_full = available == window
        if min_samples <= available < window or first_full:
            lo = seg_start - 1
            out.append(full[i] if first_full
                       else float(np.mean(values[lo:i + 1])))
        else:
            out.append(float("nan"))
    return out


def longest_segment(matches):
    """Matches in the longest single-season run - the largest usable window."""
    segs = find_season_segments(matches)
    return max((s["end"] - s["start"] + 1 for s in segs), default=0)


def format_season_text(segments_or_names, compact=False):
    """Caption naming what is actually in the chart.

    - one competition, one year   -> 'PREMIER LEAGUE 2025/26'
    - one competition, many years -> '2024/25 to 2025/26 PREMIER LEAGUE'
    - many competitions           -> '2025/26 to 2026/27 - 3 COMPETITIONS'

    The competition count is a count of COMPETITIONS. The version this
    replaces counted season-years and called them competitions, so Arsenal's
    default chart - Premier League and Champions League across two campaigns,
    three season entries - was captioned '2 COMPETITIONS'.

    Accepts either segments from `find_season_segments` or a bare list of
    season-name strings. The bare-list form is what
    player_rollingxg_chart.py already passes; it is kept working so the player
    chart inherits the fix without being touched. Its old caption was wrong
    too - the regex this replaces expected the year FIRST ('2025/26 Premier
    League') while every name in config.json puts it last, so no name ever
    matched and any two-season player chart also said 'N COMPETITIONS'.
    """
    if segments_or_names and isinstance(segments_or_names[0], dict):
        names = [n for s in segments_or_names for n in s["names"]]
    else:
        names = [n for n in (segments_or_names or []) if n]
    if not names:
        return ""
    competitions = list(dict.fromkeys(season_competition(n) for n in names if season_competition(n)))
    years = sorted({season_year(n) for n in names if season_year(n)})

    span = f"{years[0]} to {years[-1]}" if len(years) >= 2 else (years[0] if years else "")

    if len(competitions) == 1:
        comp = competitions[0].upper()
        if len(years) >= 2:
            return f"{span} {comp}"
        return f"{comp} {years[0]}" if years else comp
    if len(competitions) == 2 and not compact:
        # Name them. "2 COMPETITIONS" told a reader that two different
        # contexts had been averaged together without saying which, and a
        # cold analyst showed the mix drives the headline: Arsenal's
        # defensive overperformance is 9.3 goals across all competitions and
        # 2.2 in the league alone, the rest sitting in the unnamed one.
        #
        # `compact` is the caller's escape when the named pair will not fit -
        # measured over the 55 distinct captions production can currently
        # produce, the longest ("PREMIER LEAGUE · UEFA CONFERENCE LEAGUE
        # 2025/26 to 2026/27") takes 99% of a 9:16 frame's width at the 16pt
        # phone floor, which it cannot go below.
        pair = " · ".join(_SHORT_COMPETITION.get(c.upper(), c.upper())
                          for c in competitions)
        return f"{pair} {span}" if span else pair
    if len(competitions) >= 2:
        n = len(competitions)
        return f"{span} · {n} COMPETITIONS" if span else f"{n} COMPETITIONS"
    return span


def draw_season_boundaries(ax, segments, y_pos="top", fontsize=11,
                           color="white", alpha=0.7, empty_from=None,
                           rule_color="#556B7F",
                           show_count=False, label_pad=0, shade=True):
    """Vertical rule and year label wherever a NEW season starts.

    The first segment is deliberately not labelled. Labelling it was tried and
    reverted: its label lands at the left edge of the axes, in the same band
    as the panel title, and collided with it on three of four panels. The
    first season is not left anonymous by this - `format_season_text` names
    the whole span in the subtitle, which is where a range belongs.

    Label size is a parameter rather than the old hardcoded 8pt, which
    delivered at 13.3px and failed the 16px floor on every render.
    """
    if len(segments) <= 1:
        return
    y = ax.get_ylim()[1] if y_pos == "top" else ax.get_ylim()[0]
    va = "bottom" if y_pos == "top" else "top"
    for seg in segments[1:]:
        # The rule is chrome and must not out-shout the axis it crosses.
        # Drawn white at alpha 0.5 it composited to ~3.9:1 against a
        # 2.85:1 spine and 1.74:1 gridlines - the loudest non-data line
        # on the canvas, marking the least important boundary.
        ax.axvline(x=seg["start"] - 0.5, color=rule_color,
                   linestyle="--", linewidth=1.2, alpha=0.9)
        # Shade a trailing season the rolling window cannot reach. Without it
        # the divider was drawn identically on all four panels while meaning
        # opposite things: on the three rolling panels the line STOPS before
        # it (Real Madrid's ends at match 52.0 against a rule at 52.5) and on
        # the cumulative panel the series crosses straight through. A cold
        # analyst measured it and still read "Madrid have fallen off going
        # into the new season" - from an endpoint containing no 2026/27 match
        # at all. The shading says the period exists and carries no line.
        if shade and empty_from is not None and seg["start"] >= empty_from:
            ax.axvspan(seg["start"] - 0.5, ax.get_xlim()[1], color=color,
                       alpha=0.16, zorder=0, linewidth=0)
        label = seg["year"]
        # The match count and the lifted baseline are BOTH scoped to the
        # phone frames. There the shaded band is prominent and a cold viewer
        # asked whether it was a scrollbar, and its label sat 6px from the
        # top tick. On the 16:9 dashboard the same additions collided with
        # three of the four panel titles - the band is small there and the
        # title owns that strip.
        if show_count and empty_from is not None and seg["start"] >= empty_from:
            played = seg["end"] - seg["start"] + 1
            label = f"{label} · {played} MATCH{'ES' if played != 1 else ''}"
        if label:
            # A boundary in the last stretch of the axis would run the label
            # off the right edge - the lint caught it clipping past x=1.04 on
            # every vertical. Flip it to the inside of the rule instead.
            x0, x1 = ax.get_xlim()
            near_edge = (seg["start"] - 0.5 - x0) / max(x1 - x0, 1e-9) > 0.82
            ax.annotate(
                f"{label} " if near_edge else f" {label}",
                xy=(seg["start"] - 0.5, y),
                xytext=(0, label_pad if va == "bottom" else -label_pad),
                textcoords="offset points", color=color, fontsize=fontsize,
                alpha=alpha, ha="right" if near_edge else "left", va=va)
