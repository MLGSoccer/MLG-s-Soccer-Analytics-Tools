"""
discover_wc_teams.py
---------------------
One-time (or repeat-as-needed) script to populate 2026 World Cup teams in
config.json.

How it works:
  1. Downloads the World Cup 2026 player pool (temporary - deleted after use).
  2. Extracts all unique team IDs + names + abbreviations from the CSV.
  3. Appends those teams to config.json with the WC season ID.
  4. Teams that already exist in config.json are skipped (no duplicates),
     so it is safe to re-run as the tournament progresses and TruMedia
     backfills more national-team rosters into the player pool.

Run from the data_manager folder after pasting a fresh TruMedia cURL:

    py discover_wc_teams.py

It will prompt you to paste a cURL command if no active session is found.

Modeled on discover_championship_teams.py (same flow, different seasonId).
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import (
    parse_cookies_from_curl, create_session,
    download_player_pool,
)

import pandas as pd

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

WC_SEASON_ID = "873cbl9cd9butm4air0mugxzo"


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    existing_ids = {t["team_id"] for t in config["teams"]}

    # -- Auth ---------------------------------------------------------------
    print("Paste a TruMedia cURL command (right-click any dp-proxy request "
          "in DevTools -> Copy as cURL):")
    print("(press Enter twice when done)")
    lines = []
    while True:
        line = input()
        if line == "" and lines:
            break
        lines.append(line)
    curl_string = "\n".join(lines)

    try:
        cookies = parse_cookies_from_curl(curl_string)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    session = create_session(cookies)

    # -- Download player pool ----------------------------------------------
    print("\nDownloading World Cup 2026 player pool...")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        row_count, _ = download_player_pool(session, [WC_SEASON_ID], tmp_path)
        print(f"Downloaded {row_count:,} rows.")
        df = pd.read_csv(tmp_path)
    finally:
        os.unlink(tmp_path)

    # -- Extract teams -----------------------------------------------------
    id_col   = next((c for c in ["newestTeamId", "teamId"] if c in df.columns), None)
    name_col = next((c for c in ["newestTeam", "teamName"] if c in df.columns), None)
    abbr_col = next((c for c in ["teamAbbrevName"] if c in df.columns), None)

    if not id_col or not name_col:
        print(f"ERROR: could not find team ID / name columns. "
              f"Available: {list(df.columns)}")
        sys.exit(1)

    team_rows = (
        df[[id_col, name_col] + ([abbr_col] if abbr_col else [])]
        .dropna(subset=[id_col, name_col])
        .drop_duplicates(subset=[id_col])
    )

    new_teams = []
    for _, row in team_rows.iterrows():
        tid = str(row[id_col]).strip()
        if not tid or tid in existing_ids:
            continue
        name = str(row[name_col]).strip()
        abbr = str(row[abbr_col]).strip() if abbr_col else name[:4].upper()
        new_teams.append({
            "name":       name,
            "abbrev":     abbr,
            "team_id":    tid,
            "season_ids": [WC_SEASON_ID],
        })

    distinct_pool_teams = len(team_rows)
    print(f"\nPool contains {distinct_pool_teams} distinct teams "
          f"({len(existing_ids & set(team_rows[id_col].astype(str)))} already "
          f"in config.json).")

    if not new_teams:
        print("No new teams to add (all already in config.json).")
        return

    print(f"\nFound {len(new_teams)} new World Cup teams:")
    for t in sorted(new_teams, key=lambda x: x["name"]):
        print(f"  {t['abbrev']:<6}  {t['name']:<35}  {t['team_id']}")

    confirm = input(
        f"\nAdd these {len(new_teams)} teams to config.json? "
        "(re-run later to pick up teams TruMedia adds as the tournament "
        "progresses) [y/N] "
    ).strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    config["teams"].extend(new_teams)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\nAdded {len(new_teams)} teams to config.json.")
    print("\nNext steps:")
    print("  1. Open the Data Manager app")
    print("  2. World Cup 2026 expander -> select the new teams -> Download")


if __name__ == "__main__":
    main()
