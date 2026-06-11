"""Single source of truth for TruMedia seasonId -> league name mapping.

Used by:
- shared/motherduck.py (Streamlit chart pages, team-league bucketing)
- PodcastShorts/pipeline/chart_data.py (chart kicker text + Claude
  subject resolver + parent overlay catalog)

Adding a new league: query MotherDuck for an unknown seasonId via
    SELECT DISTINCT homeTeam FROM games WHERE seasonId='<id>' LIMIT 10
identify the league, and add the entry below. Unknown ids fall back
to "MATCH" in the rendered kicker on the PodcastShorts side and to
"Other" in the Streamlit league bucket.

This module has zero dependencies (pure dict) so it can be imported
from any Python context including the PodcastShorts pipeline, which
shares the SoccerPython parent directory via sys.path injection.
"""

SEASON_TO_LEAGUE: dict[str, str] = {
    # 2025/26 men's
    "51r6ph2woavlbbpk8f29nynf8": "Premier League",
    "bmmk637l2a33h90zlu36kx8no": "Championship",
    "80zg2v1cuqcfhphn56u4qpyqc": "La Liga",
    "2bchmrj23l9u42d68ntcekob8": "Bundesliga",
    "emdmtfr1v8rey2qru3xzfwges": "Serie A",
    "dbxs75cag7zyip5re0ppsanmc": "Ligue 1",
    "6i6n0jkbh9zzij6s8htfjh2j8": "MLS",
    "aegyls91smdw9kipjgbsu8tn8": "Liga MX",
    "8v84l9nq3d5t0j4gb781i3llg": "Liga Profesional",  # Argentine Primera
    "752zalnunu0zkdfbbm915kys4": "Brasileirao",
    # 2025/26 UEFA + secondary competitions
    "2mr0u0l78k2gdsm79q56tb2fo": "Champions League",
    "7ttpe5jzya3vjhjadiemjy7mc": "Europa League",
    "7x2zp2hm4p6wuijwdw3h7a8t0": "Conference League",
    # 2025/26 women's
    "221phckhkd7y6rg3uyava3ifo": "WSL",
    "3ducfa94ga849pfvx8bjjgt1w": "NWSL",
    "br2imckbqwr0wvucakfvdp05w": "Frauen-Bundesliga",
    "2bqrpllc5x3it55paifyfa044": "D1 Arkema",          # French women
    "24f2xd1kljmiu7o0xrpj30kd0": "UWCL",
    "4mrfrvsjf1xhltsvqyb6lx250": "NWSL",               # older NWSL season
    # 2024/25 - kept for older games still in MotherDuck
    "9n12waklv005j8r32sfjj2eqc": "Premier League",
    "4x7uzww3jur4re7sgt3mslyj8": "La Liga",
    "73zebisnu1109jix9yoc09yc4": "Bundesliga",
    "b25u56idqlgo8s1rahhltqd5g": "Serie A",
    "a7htj8rtzib7a2xx7b3xs04d0": "Ligue 1",
    # 2026 World Cup
    "873cbl9cd9butm4air0mugxzo": "World Cup 2026",
}
