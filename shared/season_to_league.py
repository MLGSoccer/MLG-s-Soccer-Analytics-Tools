"""Single source of truth for TruMedia seasonId -> league name mapping.

The mapping itself now lives in `data_manager/config.json` under the
`season_leagues` key, alongside every other season fact (display label,
player-pool membership, API-Football league id, team roster). This module
loads it and exposes it under the name callers already import.

Used by:
- shared/motherduck.py (Streamlit chart pages, team-league bucketing)
- PodcastShorts/pipeline/chart_data.py (chart kicker text + Claude
  subject resolver + parent overlay catalog)

Adding a new league: add the entry to `season_leagues` in config.json -
or let the Data Manager's Add Season form write it for you. Unknown ids
fall back to "MATCH" in the rendered kicker on the PodcastShorts side
and to "Other" in the Streamlit league bucket.

WHY CONFIG AND NOT A LITERAL HERE
---------------------------------
A season rollover used to mean six edits across three files, two of them
Python source that Streamlit hot-reloads while it is running. Keeping the
mapping in JSON means a rollover is one write to one data file, and the
accented names (Premiere Ligue) that would be risky in .py source are
safe in JSON.

The trade: this module used to be a dependency-free dict literal that
could not fail to import. It now reads a file. That file is committed to
git and sits at a fixed relative path, and both other consumers -
shared/motherduck.py and PodcastShorts/pipeline/chart_data.py - already
read it from that same path in production.
"""
from __future__ import annotations

import json
import os

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data_manager", "config.json",
)


def load_season_leagues(config_path: str | None = None) -> dict[str, str]:
    """Read the seasonId -> league name mapping from config.json.

    encoding is explicit: league names carry accents (Premiere Ligue), and
    open() would otherwise use the platform default - UTF-8 on Streamlit
    Cloud but cp1252 on Windows, which mojibakes them locally.
    """
    path = config_path or _CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("season_leagues", {})


SEASON_TO_LEAGUE: dict[str, str] = load_season_leagues()
