"""
Shared stat name mappings for soccer chart builders.
Maps CSV column names to human-readable display names.
"""

# Map CSV column names to display names
STAT_DISPLAY_NAMES = {
    # Scoring
    'GoalExPn': 'Goals (non-pen)',
    'NPxG': 'Non-Penalty xG',
    'Shot': 'Shots',
    'ExpG': 'Expected Goals',
    'Goal': 'Goals',
    'xG': 'xG',
    'PnGoal': 'Penalty Goals',
    'PnMiss': 'Penalties Missed',

    # Chance Creation
    'Ast': 'Assists',
    'xA': 'Expected Assists',
    'Chance': 'Key Passes',
    'ShotAst': 'Shot-Creating Actions',
    'GoalAst': 'Goal-Creating Actions',

    # Passing
    'PsAtt': 'Passes Attempted',
    'PsCmp': 'Passes Completed',
    'Pass%': 'Pass Completion %',
    'ProgPass': 'Progressive Passes',
    'PsIntoA3rd': 'Final Third Passes',
    'PsIntoPen': 'Passes Into Penalty Area',
    'Cross': 'Crosses',
    'CrossCmp': 'Crosses Completed',
    'LongBall': 'Long Balls',
    'LongBallCmp': 'Long Balls Completed',
    'ThroughBall': 'Through Balls',

    # Dribbling
    'ProgCarry': 'Progressive Carries',
    'TakeOn': 'Take-Ons',
    'TakeOnWon': 'Take-Ons Won',
    'TakeOn%': 'Take-On %',
    'Carry': 'Carries',
    'CarryDist': 'Carry Distance',
    'Dispossess': 'Dispossessions',
    'Miscontrols': 'Miscontrols',

    # Defensive
    'TcklAtt': 'Tackles',
    'TcklWon': 'Tackles Won',
    'Tackle%': 'Tackle %',
    'Int': 'Interceptions',
    'Aerials': 'Aerial Duels',
    'AerialsWon': 'Aerial Duels Won',
    'Aerial%': 'Aerial %',
    'ShtBlk': 'Blocks',
    'PassBlk': 'Passes Blocked',
    'Clearance': 'Clearances',
    'Recovery': 'Recoveries',
    'Foul': 'Fouls Committed',
    'FoulSuf': 'Fouls Suffered',

    # Duels
    'Duel': 'Duels',
    'DuelWon': 'Duels Won',
    'Duel%': 'Duel %',

    # Other
    'Min': 'Minutes',
    'GM': 'Games',
    'Start': 'Starts',
    'Sub': 'Substitute Appearances',
    'YC': 'Yellow Cards',
    'RC': 'Red Cards',
    'Touch': 'Touches',
    'TouchA3rd': 'Touches in Final Third',
    'TouchPen': 'Touches in Penalty Area',

    # Goalkeeping
    'Save': 'Saves',
    'Save%': 'Save %',
    'GA': 'Goals Against',
    'GAExPn': 'Goals Against (non-pen)',
    'PSxG': 'Post-Shot xG',
    'CS': 'Clean Sheets',
}

# Stats that are already percentages in CSV (skip per-90 normalization)
ALREADY_PER_90 = {
    'Pass%',
    'TakeOn%',
    'Tackle%',
    'Duel%',
    'Aerial%',
    'Save%',
}

# Stats where lower is better (for sorting context/display)
LOWER_IS_BETTER = {
    'Dispossess',
    'Miscontrols',
    'Foul',
    'YC',
    'RC',
    'GA',
    'GAExPn',
}


def get_stat_display_name(csv_column):
    """Get the human-readable display name for a stat.

    Args:
        csv_column: The CSV column name

    Returns:
        Display name if found, or the original column name formatted
    """
    if csv_column in STAT_DISPLAY_NAMES:
        return STAT_DISPLAY_NAMES[csv_column]

    # Fallback: convert camelCase to Title Case with spaces
    # e.g., 'progPass' -> 'Prog Pass'
    import re
    # Insert space before uppercase letters
    spaced = re.sub(r'([A-Z])', r' \1', csv_column)
    return spaced.strip().title()


def is_per_90_stat(csv_column):
    """Check if a stat should be shown as per-90 or is already a rate.

    Args:
        csv_column: The CSV column name

    Returns:
        False if stat is already a percentage/rate, True otherwise
    """
    return csv_column not in ALREADY_PER_90


def is_lower_better(csv_column):
    """Check if lower values are better for this stat.

    Args:
        csv_column: The CSV column name

    Returns:
        True if lower is better, False otherwise
    """
    return csv_column in LOWER_IS_BETTER
