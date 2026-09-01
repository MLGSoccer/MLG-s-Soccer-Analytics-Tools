"""
Add Season — register a new TruMedia season id everywhere it needs to exist.

Doing this by hand meant editing four separate lists, and nothing verified
you had hit all four. The August 2026 audit found two seasons that had been
added incompletely and gone unnoticed for months: MLS 2026 was wired
everywhere except the player pool, and four leagues had 746 games of events
with no display label.

The form removes the chance to miss one. Paste the TruMedia URL, say which
league this is a new season of, and every other field is cloned from that
league's previous season - the league name, pool assignment, API-Football id
and whether it is a secondary competition are all properties of the league,
not the season, so they carry over unchanged. Only the display label needs
typing, and even that is guessed.

Everything lands in config.json. Nothing here touches Python source.

Teams are a separate step by design: promotions and relegations are not
knowable from a season id, and the Discover Teams page already handles that
diff properly. This page links there when it is done.
"""
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from downloader import (  # noqa: E402
    extract_season_id,
    load_secrets,
    save_config,
    suggest_next_label,
)

st.set_page_config(page_title="Add Season", page_icon="⚽", layout="wide")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

seasons        = config.get("seasons", {})
season_leagues = config.get("season_leagues", {})
pools          = config.get("player_pools", {})
secondary      = set(config.get("secondary_seasons", []))
pool_excluded  = set(config.get("pool_excluded_seasons", []))

pool_of = {sid: pool
           for pool, spec in pools.items()
           for sid in (spec.get("seasons") or [])}

st.title("Add Season")
st.markdown(
    "Paste a TruMedia URL with the new season selected. Everything except the "
    "display label is cloned from that league's previous season."
)


# ── 1. The season id ──────────────────────────────────────────────────────────

st.subheader("1 · Season")

url = st.text_input(
    "TruMedia URL (or a raw season id)",
    placeholder="https://cbssports.opta.trumediasports.com/soccer/...?f=%7B%22sseas%22...",
    help="Open any TruMedia stats page with the new season selected and copy "
         "the address bar. The season id is read from the `f.sseas` parameter.",
)

if not url.strip():
    st.info("Paste a URL above to begin.")
    st.stop()

try:
    season_id = extract_season_id(url)
except ValueError as e:
    st.error(str(e))
    st.stop()

st.success(f"Season id: `{season_id}`")

if season_id in seasons:
    st.warning(
        f"**Already registered** as *{seasons[season_id]}*. Nothing to add — "
        f"use the Health page to check it is wired into every list."
    )
    st.stop()


# ── 2. Which league ───────────────────────────────────────────────────────────

st.subheader("2 · Which league is this?")

# Newest previous season per league, so cloned values come from the most
# recent precedent rather than an arbitrary one.
def _sort_key(sid):
    return seasons.get(sid, "")


latest_by_league = {}
for sid, league_name in season_leagues.items():
    if sid not in seasons:
        continue  # retired pool ids carry no usable settings
    current = latest_by_league.get(league_name)
    if current is None or _sort_key(sid) > _sort_key(current):
        latest_by_league[league_name] = sid

league_names = sorted(latest_by_league)
NEW_LEAGUE = "— a league not listed here —"

choice = st.selectbox(
    "A new season of…",
    options=league_names + [NEW_LEAGUE],
    help="Settings are copied from this league's most recent season.",
)

is_new_league = choice == NEW_LEAGUE

if is_new_league:
    st.info(
        "No precedent to clone from, so every field needs a value. If this is "
        "actually a new season of an existing competition, pick it above instead."
    )
    league_name = st.text_input(
        "League name",
        help="Stable across seasons — no year. Used for chart kickers and "
             "league grouping. e.g. \"Eredivisie\", not \"Eredivisie 2026/27\".",
    )
    prev_id = None
    prev_label = ""
    default_pool = "— none —"
    default_secondary = False
else:
    prev_id = latest_by_league[choice]
    prev_label = seasons.get(prev_id, "")
    league_name = choice
    default_pool = pool_of.get(prev_id, "— none —")
    default_secondary = prev_id in secondary

    pool_note = (f"`{default_pool}`" if default_pool != "— none —"
                 else ("*none — excluded by design*" if prev_id in pool_excluded
                       else "*none*"))
    st.caption(
        f"Cloning from **{prev_label}** — pool {pool_note}"
        + (", secondary competition" if default_secondary else "")
    )


# ── 3. Confirm the details ────────────────────────────────────────────────────

st.subheader("3 · Details")

c1, c2 = st.columns(2)

with c1:
    label = st.text_input(
        "Display label",
        value=suggest_next_label(prev_label),
        help="Shown in the Downloads season picker and in Claude's "
             "DATA AVAILABLE block. Convention: \"Competition 2026/27\" for "
             "split seasons, \"Competition 2027\" for calendar-year ones.",
    )
    pool_choice = st.selectbox(
        "Player pool",
        options=list(pools.keys()) + ["— none —"],
        index=(list(pools.keys()) + ["— none —"]).index(default_pool)
        if default_pool in list(pools.keys()) + ["— none —"] else len(pools),
        help="Feeds the percentile charts. Leave as none only if this "
             "competition's players are already pooled through their domestic "
             "league — that is why the UEFA competitions are unpooled.",
    )

with c2:
    is_secondary = st.checkbox(
        "Secondary competition",
        value=default_secondary,
        help="UEFA competitions and other cups. Secondary seasons never "
             "determine which league a team is grouped under.",
    )

if not label.strip():
    st.error("A display label is required.")
    st.stop()
if is_new_league and not league_name.strip():
    st.error("A league name is required for a league with no precedent.")
    st.stop()


# ── 4. Preview and apply ──────────────────────────────────────────────────────

st.subheader("4 · Review")

changes = [
    ("seasons", f'"{season_id}": "{label}"'),
    ("season_leagues", f'"{season_id}": "{league_name}"'),
]
if pool_choice != "— none —":
    changes.append((f"player_pools.{pool_choice}.seasons", f'append "{season_id}"'))
else:
    changes.append(("pool_excluded_seasons", f'append "{season_id}"'))
if is_secondary:
    changes.append(("secondary_seasons", f'append "{season_id}"'))

st.dataframe(
    [{"config.json key": k, "Change": v} for k, v in changes],
    hide_index=True, use_container_width=True,
)

warnings = []
if pool_choice == "— none —":
    warnings.append(
        f"No player pool — recorded in `pool_excluded_seasons` so the Health "
        f"page treats it as deliberate. Percentile charts will not resolve "
        f"players whose only season is this one."
    )
for w in warnings:
    st.warning(w)

if st.button("Add season to config.json", type="primary"):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        fresh = json.load(f)

    if season_id in fresh.get("seasons", {}):
        st.error("Already added — config.json changed since this page loaded.")
        st.stop()

    fresh.setdefault("seasons", {})[season_id] = label
    fresh.setdefault("season_leagues", {})[season_id] = league_name
    if pool_choice != "— none —":
        pool_seasons = fresh["player_pools"][pool_choice].setdefault("seasons", [])
        if season_id not in pool_seasons:
            pool_seasons.append(season_id)
    else:
        excluded = fresh.setdefault("pool_excluded_seasons", [])
        if season_id not in excluded:
            excluded.append(season_id)
    if is_secondary:
        sec = fresh.setdefault("secondary_seasons", [])
        if season_id not in sec:
            sec.append(season_id)

    _mirrored, _mirror_err = save_config(fresh)

    st.success(f"Added **{label}**.")
    if _mirror_err:
        st.warning(
            f"Saved locally, but the shared copy in MotherDuck was not updated "
            f"- the chart maker will not see this season yet. {_mirror_err}"
        )
    st.markdown(
        "**Next:** no teams carry this season yet, so downloads will not find "
        "it. Open **Discover Teams**, scan this season, and apply the diff — "
        "that handles promotions and relegations. Then **Health** should show "
        "it fully wired."
    )
    st.balloons()
