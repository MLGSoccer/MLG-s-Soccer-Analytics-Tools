"""Shot chart subpackage.

Organized into focused modules; this __init__ re-exports the public surface
so `from mostly_finished_charts.shot_chart import X` continues to work:

    .colors    - color math (contrast, lightening, pitch/bg checks)
    .data      - CSV/DB loading, reconciliation, types, highlight classification
    .drawing   - low-level marker drawing + pitch ylim cropping
    .charts    - high-level chart assembly + orchestrators
"""
from .colors import (
    FALLBACK_COLOR,
    PITCH_COLOR,
    _lighten_hex,
    check_bg_contrast,
    color_distance,
    ensure_bg_readable,
    ensure_pitch_contrast,
    hex_to_rgb,
)
from .data import (
    GOAL_TYPES,
    HIGHLIGHT_CATEGORIES,
    SHOT_TYPES,
    MatchInfo,
    PenStats,
    TeamGoalBreakdown,
    classify_highlight,
    compute_highlight_stats,
    compute_pen_stats,
    detect_csv_mode,
    load_multi_match_shot_data,
    load_shot_data,
    reconcile_team_goals,
)
from .drawing import (
    MUTED_ALPHA,
    MUTED_COLOR,
    MUTED_ZORDER,
    compute_ylim_floor,
    plot_shots_horizontal,
    plot_shots_vertical,
)
from .charts import (
    create_combined_shot_chart,
    create_multi_match_charts,
    create_multi_match_shot_chart,
    create_shot_charts,
    create_team_shot_chart,
    main,
    run,
)


if __name__ == "__main__":
    main()
