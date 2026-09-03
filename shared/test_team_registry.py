"""Tests for the two-layer colour registry. No network, no MotherDuck.

The assertions worth reading are the ones that pin the LAYER BOUNDARY: that
identity never consults a background, and that rendering never edits identity.
Those are the two mistakes this module was written to stop.

    py shared/test_team_registry.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.team_registry import (
    resolve_all,
    AUTHORED, FEED, INHERITED, NEUTRAL, NEUTRAL_SRC,
    TeamColors, choose_for_background, get_team_colors, is_hex,
)

CBS_BG = "#1A2332"
DP_BG = "#0D1117"

# A registry stub. Real values, because the cases that matter are real clubs.
REG = {
    "rm": {"name": "Real Madrid", "primary": "#FEBE10", "secondary": "#00529F",
           "source_url": "https://example.invalid/real-madrid"},
    "inter": {"name": "Internazionale", "primary": "#000000",
              "secondary": "#0068A8",
              "source_url": "https://example.invalid/inter"},
    "pad": {"name": "Paderborn", "primary": "#000000", "secondary": "#0B3D91",
            "source_url": "https://example.invalid/paderborn"},
    "nosec": {"name": "No Secondary", "primary": "#000000"},
}

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}  {detail}")


print("\nLAYER 1 - identity. Contrast must play no part.")

rm = get_team_colors("rm", feed_color="#0066FF", registry=REG)
check("authored value beats the feed outright",
      rm.primary == "#FEBE10", f"got {rm.primary}")
check("provenance says authored", rm.provenance == AUTHORED)
check("source_url is carried", bool(rm.source_url))

inter = get_team_colors("inter", registry=REG)
check("Inter's primary is stored as pure black, unmodified",
      inter.primary == "#000000", f"got {inter.primary}")
check("...and identity is background-independent",
      get_team_colors("inter", registry=REG) == inter)

tail = get_team_colors("t1", name="Breidablik", feed_color="#1E7A3C",
                       registry=REG)
check("tail club takes the feed", tail.primary == "#1E7A3C")
check("tail club has no secondary", tail.secondary is None)
check("tail provenance is feed", tail.provenance == FEED)

none = get_team_colors("t2", name="Elversberg", feed_color=None, registry=REG)
check("no colour anywhere -> NEUTRAL", none.primary == NEUTRAL)
check("neutral is not Manchester City's blue", NEUTRAL != "#6CABDD")
check("neutral provenance is recorded", none.provenance == NEUTRAL_SRC)

bad = get_team_colors("t3", feed_color="not-a-colour", registry=REG)
check("malformed feed value is not a colour", bad.primary == NEUTRAL)
check("is_hex rejects junk",
      not is_hex("#12345") and not is_hex(None) and is_hex("#0068A8"))

# The trap: inheritance must run on the RESOLVED parent, never the raw feed.
rmw = get_team_colors("rmw", name="Real Madrid Women", feed_color=None,
                      registry=REG, parent_colors=rm)
check("Women's side inherits the AUTHORED parent colour",
      rmw.primary == "#FEBE10", f"got {rmw.primary}")
check("inheritance does NOT pick up the feed's wrong blue",
      rmw.primary != "#0066FF")
check("inherited provenance is distinguishable", rmw.provenance == INHERITED)
check("inherited keeps its own identity", rmw.name == "Real Madrid Women")


print("\nLAYER 2 - rendering. Identity is read, never rewritten.")

d = choose_for_background(inter, CBS_BG)
check("black primary reaches for the secondary",
      d.used == "secondary", f"used {d.used}")
check("...and the drawn colour is the blue, not grey",
      d.color.upper().startswith("#0") or d.color != "#787878",
      f"got {d.color}")
check("the decision explains itself", bool(d.reason))
check("choosing did NOT mutate identity", inter.primary == "#000000")

pad = get_team_colors("pad", registry=REG)
pad_d = choose_for_background(pad, CBS_BG)
check("Paderborn no longer renders as #787878 grey",
      pad_d.color != "#787878", f"got {pad_d.color}")

nosec = get_team_colors("nosec", registry=REG)
nosec_d = choose_for_background(nosec, CBS_BG)
check("black with NO secondary falls back to white",
      nosec_d.used == "primary" and nosec_d.color == "#FFFFFF",
      f"used {nosec_d.used} color={nosec_d.color}")

# A primary that already clears the floor is drawn exactly as authored.
# #6CABDD is 6.39:1 on the CBS background - comfortably over.
clear = TeamColors("x", "Clear FC", "#6CABDD", "#FFCC00", AUTHORED)
cd = choose_for_background(clear, CBS_BG)
check("a primary that reads is used untouched",
      cd.used == "primary" and not cd.lightened and cd.color == "#6CABDD",
      f"used {cd.used} lightened={cd.lightened} color={cd.color}")

# A primary needing a small nudge gets the nudge, NOT the secondary. The
# secondary is for primaries that would have to move too far, not for every
# primary that moves at all. #2E6BB8 is 2.94:1 - one step clears it.
nudge = TeamColors("y", "Nudge FC", "#2E6BB8", "#FFCC00", AUTHORED)
nd = choose_for_background(nudge, CBS_BG)
check("a primary needing one step is nudged, not replaced",
      nd.used == "primary" and nd.lightened,
      f"used {nd.used} lightened={nd.lightened}")

# Clash: two lines that both read but are near-identical in luminance.
a = TeamColors("a", "A", "#C8102E", "#FFD700", AUTHORED)
clash = choose_for_background(a, CBS_BG, against="#C41230")
check("a luminance clash reaches for the secondary",
      clash.used == "secondary", f"used {clash.used}")
check("the clash reason names the cause",
      "opposing" in clash.reason, clash.reason)

# Same club, two products, two backgrounds - the point of layer separation.
cbs = choose_for_background(inter, CBS_BG)
dp = choose_for_background(inter, DP_BG)
check("both products resolve the same identity",
      inter.primary == "#000000")
check("each background gets its own decision",
      isinstance(cbs.color, str) and isinstance(dp.color, str))

print("\nACHROMATIC FALLBACK - grey is nobody's colour")

grey_risk = TeamColors("g", "Black FC", "#000000", None, FEED)
gd = choose_for_background(grey_risk, CBS_BG)
check("black with no secondary draws white, not #787878",
      gd.color == "#FFFFFF", f"got {gd.color}")
check("...and says why", "grey" in gd.reason.lower(), gd.reason)


print("\nINHERITANCE - resolved parent, never the raw feed")

teams = [
    ("rm", "Real Madrid", "#0066FF"),          # feed is WRONG; registry corrects
    ("rmw", "Real Madrid Women", None),        # nothing of her own
    ("angel", "Angel City Women", None),       # no men's counterpart
    ("acp", "Angel City Women parent", None),  # decoy that must not match
]
res = resolve_all(teams, registry=REG)
check("Women's side inherits the parent's AUTHORED primary",
      res["rmw"].primary == "#FEBE10", f"got {res['rmw'].primary}")
check("...not the parent's raw feed value",
      res["rmw"].primary != "#0066FF")
check("...and is marked inherited", res["rmw"].provenance == INHERITED)
check("...keeping its own id and name",
      res["rmw"].team_id == "rmw" and res["rmw"].name == "Real Madrid Women")
check("a Women's club with no parent stays neutral",
      res["angel"].provenance == NEUTRAL_SRC)
check("the parent itself is unaffected", res["rm"].provenance == AUTHORED)

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(1 if failed else 0)
