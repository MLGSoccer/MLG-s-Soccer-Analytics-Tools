"""
Discover Teams — find teams in a TruMedia player pool that aren't in
config.json yet.

Use case: every season transition (Premier League promotions in August,
NWSL / MLS expansion, World Cup squads landing match-by-match) requires
adding the new teams' team_ids to config.json before downloads can find
them. Doing it manually means hunting down each team URL one by one.

This page automates the diff: pick one or more seasons, click Discover,
see the new + needs-update teams that turned up in TruMedia's pool but
aren't yet wired into config.json. Click Apply to write the changes.

The user picks WHICH seasons to scan, so daily runs during a tournament
(WC 2026 currently) don't have to crawl every league pool.
"""
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from downloader import (  # noqa: E402
    create_session,
    discover_teams_for_season,
    apply_team_discovery,
)

st.set_page_config(
    page_title="Discover Teams",
    page_icon="⚽",
    layout="wide",
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH) as f:
    config = json.load(f)

st.title("Discover Teams")
st.markdown(
    "Scan a season's TruMedia player pool for teams that aren't in "
    "`config.json` yet, and add the new entries. Existing teams gain "
    "the selected season_id appended to their `season_ids` list "
    "(handles promoted teams cleanly)."
)

# ── Auth (reuses cookies from app.py) ────────────────────────────────────────
cookies = st.session_state.get("cookies")
if not cookies:
    st.warning(
        "Not authenticated. Go to the main **TruMedia Data Manager** page "
        "and paste a cURL command in the Authentication box first."
    )
    st.stop()
st.success("Session active")

st.divider()

# ── Season selection ─────────────────────────────────────────────────────────
season_labels = config.get("seasons", {})
if not season_labels:
    st.error("No seasons defined in config.json `seasons` block.")
    st.stop()

# Pre-sort by label so the dropdown is alphabetical and stable.
season_options = sorted(season_labels.items(), key=lambda kv: kv[1])
label_to_id = {label: sid for sid, label in season_options}

st.subheader("Pick seasons to scan")
default_selection = []
# Default to the most recently-added season (last in dict insertion order)
# so the common "just added a new season, find its teams" path is one click.
if season_options:
    last_label = season_labels[list(season_labels.keys())[-1]]
    default_selection = [last_label]

selected_labels = st.multiselect(
    "Seasons",
    options=[lbl for _sid, lbl in season_options],
    default=default_selection,
    help="Pick one or more. Each scan is one TruMedia round trip per "
         "season, so picking only what you need keeps daily runs cheap.",
)

col_run, col_clear = st.columns([1, 1])
with col_run:
    run = st.button(
        "Discover",
        type="primary",
        disabled=not selected_labels,
    )
with col_clear:
    if st.button("Clear results"):
        st.session_state.pop("_discovery_results", None)
        st.rerun()

# ── Run discovery ────────────────────────────────────────────────────────────
if run:
    session = create_session(cookies)
    results = []
    progress = st.progress(0.0)
    for i, label in enumerate(selected_labels):
        season_id = label_to_id[label]
        with st.spinner(f"Scanning {label}..."):
            try:
                res = discover_teams_for_season(session, season_id, config)
                res["label"] = label
                results.append(res)
            except Exception as e:
                st.error(f"{label}: {type(e).__name__}: {e}")
        progress.progress((i + 1) / len(selected_labels))
    progress.empty()
    st.session_state["_discovery_results"] = results

# ── Display + apply ──────────────────────────────────────────────────────────
results = st.session_state.get("_discovery_results")
if results:
    st.divider()
    st.subheader("Findings")

    total_new = sum(len(r["new_teams"]) for r in results)
    total_updates = sum(len(r["to_update"]) for r in results)
    st.markdown(
        f"**{total_new}** new team(s) to add &middot; "
        f"**{total_updates}** existing team(s) gaining a season_id"
    )

    for res in results:
        with st.expander(
            f"{res['label']} — pool has {res['pool_count']} team(s), "
            f"{len(res['new_teams'])} new, {len(res['to_update'])} to update",
            expanded=(len(res["new_teams"]) + len(res["to_update"])) > 0,
        ):
            if res["new_teams"]:
                st.markdown("**New teams**")
                st.dataframe(
                    [
                        {
                            "abbrev": t["abbrev"],
                            "name": t["name"],
                            "team_id": t["team_id"],
                        }
                        for t in sorted(res["new_teams"], key=lambda t: t["name"])
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            if res["to_update"]:
                st.markdown("**Existing teams gaining this season_id**")
                st.dataframe(
                    [
                        {
                            "name": t["name"],
                            "team_id": t["team_id"],
                            "currently_in": ", ".join(t["current_season_ids"]),
                        }
                        for t in sorted(res["to_update"], key=lambda t: t["name"])
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            if not res["new_teams"] and not res["to_update"]:
                st.info("Nothing to add for this season — config is in sync.")

    if total_new + total_updates > 0:
        if st.button(
            f"Apply ({total_new} added, {total_updates} updated)",
            type="primary",
        ):
            added, updated = apply_team_discovery(config, results)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            st.success(
                f"Wrote config.json: {added} new team(s), {updated} season_id "
                "addition(s) to existing team(s)."
            )
            st.session_state.pop("_discovery_results", None)
            st.balloons()
