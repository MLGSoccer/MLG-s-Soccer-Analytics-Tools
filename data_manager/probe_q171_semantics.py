"""Does a red card in the data always mean the player was actually sent off?

WHY IT MATTERS
--------------
`shared/motherduck.py` and `PodcastShorts/pipeline/chart_data.py` annotate a
red card on the xG race and momentum charts. The annotation asserts that the
team played the rest of the match a man down. If a card was overturned ON THE
FIELD the player stayed on, it was 11v11 throughout, and the annotation is
simply false.

WHAT THE FIRST VERSION OF THIS PROBE GOT WRONG
----------------------------------------------
It looked only at q171 ("rescinded") events and asked whether the player had
any event afterwards. Three problems:

  1. "no events after" does not mean "sent off" - a substituted player, or one
     who simply does not touch the ball again, looks identical. It is a
     negative signal doing positive work. One case sat at minute 91, where
     nobody has events afterwards.
  2. n = 3 distinct red cards.
  3. WORST: looking only at q171 cannot detect the failure mode that matters.
     If an in-match overturn is recorded as a plain q33 with NO qualifier,
     that probe finds nothing and the chart still annotates a sending-off
     that never happened.

It tested the qualifier that was interesting rather than the failure that is
costly.

THE TEST
--------
Take EVERY red and second yellow, not just the rescinded ones, and settle each
against MINUTES PLAYED - a positive signal that does not care whether the
player touched the ball again.

    minutes ~= card minute      -> he walked. Annotation correct.
    minutes ~= full match       -> he stayed on. ANNOTATION WOULD BE WRONG.

Then cross-tabulate the "stayed on" group against q171, which answers both
questions at once: what q171 means, and whether there is a class of overturned
reds carrying no qualifier at all.

Usage:
    py probe_q171_semantics.py <curl-file> [seasonId ...] [--pairs N]

Capture: log in to TruMedia, DevTools -> Network, filter dp-proxy, right-click
any such request -> Copy -> Copy as cURL, paste into a file. ~4h lifetime.
Delete the file afterwards.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

# game.home / game.away are BOOLEAN side flags, not team names.
CARD_COLS = (
    "game.gameId, game.gameDate, "
    "team.game.fullName AS teamName, opponent.game.fullName AS oppName, "
    "event.playType, event.gameClock, event.period, "
    "lookup(event.primary,abbrevName) AS player, "
    "event.primaryPlayerId AS playerId, "
    "event.q31 AS q31, event.q32 AS q32, event.q33 AS q33, event.q171 AS q171"
)

MIN_COLS = ("playerId, abbrevName AS player, game.gameId, "
            "team.game.teamId AS teamId, [Min] AS minutes")


def main() -> int:
    here = Path(__file__).parent
    sys.path.insert(0, str(here))
    from downloader import (create_session, parse_cookies_from_curl,
                            EXPORT_URL, _load_config_key)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n_pairs = 40
    for a in sys.argv[1:]:
        if a.startswith("--pairs"):
            n_pairs = int(a.split("=", 1)[1]) if "=" in a else n_pairs
    if not args:
        print(__doc__)
        return 1
    curl_path = Path(args[0])
    if not curl_path.exists():
        print(f"ERROR: no such file: {curl_path}")
        return 1
    session = create_session(parse_cookies_from_curl(
        curl_path.read_text(encoding="utf-8", errors="replace")))

    seasons = args[1:]
    if not seasons:
        cfg = _load_config_key("seasons") or {}
        seasons = list(cfg)[:5] if isinstance(cfg, dict) else []
    pairs = _pairs_for(here, seasons, n_pairs)
    if not pairs:
        print(f"No (team, season) pairs in config for: {seasons}")
        return 1
    print(f"scanning {len(pairs)} (team, season) pair(s), 2 requests each\n")

    import pandas as pd

    def post(statement, descriptor="pageSoccerTeamEventLogOverall"):
        r = session.post(EXPORT_URL, json={
            "format": "MIXED", "statement": statement, "export": "csv",
            "pageDescriptorName": descriptor,
            "exportOptions": {"includeCalculations": False,
                              "includeVideoData": False}}, timeout=180)
        if not r.ok:
            return None, f"HTTP {r.status_code}"
        try:
            return pd.read_csv(io.BytesIO(r.content)), None
        except Exception as e:
            return None, f"parse: {e}"

    cards, mins = [], []
    for tid, sid, name in pairs:
        # EVERY sending-off, not just the rescinded ones.
        c, err = post(f"SELECT {CARD_COLS} FROM team BY event "
                      f"WHERE ((team.teamId ='{tid}')) "
                      f"AND ((season.seasonId IN ('{sid}'))) "
                      f"AND ((event.q33) OR (event.q32)) LIMIT 10000")
        if err:
            print(f"    {name[:22]:<22} cards: {err}")
            continue
        m, err = post(f"SELECT {MIN_COLS} FROM player BY game "
                      f"WHERE ((team.game.teamId ='{tid}')) "
                      f"AND ((season.seasonId IN ('{sid}'))) LIMIT 100000",
                      "pageSoccerPlayersInPossession")
        if err:
            print(f"    {name[:22]:<22} minutes: {err}")
            continue
        if c is not None and len(c):
            c = c.copy()
            c["_anchor"] = tid
            cards.append(c)
        if m is not None and len(m):
            mins.append(m)
        print(f"    {name[:22]:<22} {len(c) if c is not None else 0:>3} reds, "
              f"{len(m) if m is not None else 0:>5} minute rows")

    if not cards:
        print("\nNo red cards found. Widen the scan.")
        return 2
    cards = pd.concat(cards, ignore_index=True)
    minutes = (pd.concat(mins, ignore_index=True) if mins
               else pd.DataFrame(columns=["playerId", "gameId", "minutes"]))

    # Full-match length per game, so extra time is not read as "stayed on".
    game_len = minutes.groupby("gameId")["minutes"].max().to_dict()
    mkey = {(str(r.playerId), str(r.gameId)): r.minutes
            for r in minutes.itertuples()}

    print(f"\n{'=' * 74}")
    print(f"{len(cards)} red / second-yellow events")
    print(f"{'=' * 74}\n")

    rows, unresolved = [], 0
    for _, c in cards.iterrows():
        card_min = float(c.get("gameClock") or 0) / 60.0
        played = mkey.get((str(c.get("playerId")), str(c.get("gameId"))))
        if played is None:
            unresolved += 1
            continue
        full = game_len.get(c.get("gameId"), 90)
        after_card = float(played) - card_min
        if after_card <= 3:
            verdict = "SENT OFF"
        elif float(played) >= full - 3:
            verdict = "PLAYED TO THE END"
        else:
            verdict = "AMBIGUOUS"
        rows.append({
            "verdict": verdict, "q171": bool(c.get("q171")),
            "q32": bool(c.get("q32")), "q33": bool(c.get("q33")),
            "player": c.get("player"), "min": round(card_min),
            "played": played, "full": full,
            "match": f"{c.get('teamName')} v {c.get('oppName')}",
            "date": str(c.get("gameDate"))[:10],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        print(f"Could not match any card to minutes ({unresolved} unresolved).")
        return 2

    print("CROSS-TAB  (rows = what the minutes say; cols = q171 flag)\n")
    tab = df.pivot_table(index="verdict", columns="q171", aggfunc="size",
                         fill_value=0)
    print(tab.to_string())
    print(f"\n  unresolved (no minutes row): {unresolved}")

    stayed = df[df["verdict"] == "PLAYED TO THE END"]
    print(f"\n{'=' * 74}\nRED CARDS WHERE THE PLAYER FINISHED THE MATCH: "
          f"{len(stayed)}")
    print("These are the ones a chart would annotate WRONGLY.\n")
    for _, r in stayed.head(15).iterrows():
        print(f"  {r['match'][:44]:<44} {r['date']}")
        print(f"    {str(r['player'])[:22]:<22} card {r['min']}'  "
              f"played {r['played']} of {r['full']}  q171={r['q171']}")

    print(f"\n{'=' * 74}\nVERDICT")
    if stayed.empty:
        print("  Every red card in the sample removed the player. The chart")
        print("  annotation is safe as written, and q171 does not need to be")
        print("  filtered - a rescinded red still sent the player off.")
    elif stayed["q171"].all():
        print("  Every 'played to the end' red carries q171. So q171 marks an")
        print("  IN-MATCH overturn after all, and BOTH readers should filter")
        print("  it out. Reverse the current behaviour.")
    elif not stayed["q171"].any():
        print("  Reds where the player finished exist and NONE carry q171.")
        print("  So the qualifier cannot identify them, and filtering on it")
        print("  would not have helped. The readers should decide from")
        print("  MINUTES, not from the qualifier.")
    else:
        print("  Mixed: some 'played to the end' reds carry q171 and some do")
        print("  not. The qualifier is not a reliable signal on its own -")
        print("  drive the annotation from minutes played.")
    return 0


def _pairs_for(here: Path, seasons, limit):
    """(team_id, season_id, name) pairs that actually exist.

    config.json's teams carry their own `season_ids`; pairing here avoids
    requesting combinations that never happened.
    """
    cfg = json.loads((here / "config.json").read_text(encoding="utf-8"))
    want, out = set(seasons), []
    for t in (cfg.get("teams") or []):
        tid = t.get("team_id")
        if not tid:
            continue
        for sid in (t.get("season_ids") or []):
            if sid in want:
                out.append((tid, sid, t.get("name") or tid))
    return out[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
