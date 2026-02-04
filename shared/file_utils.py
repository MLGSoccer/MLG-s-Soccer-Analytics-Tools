"""
Shared file utilities for soccer chart builders.
"""
import os
import csv


def get_file_path(prompt, default_folder="Downloads"):
    """Get file path from user input.

    Args:
        prompt: Description of what file is needed
        default_folder: Folder to look in (default: Downloads)

    Returns:
        Full path to file if found, None otherwise
    """
    print(f"\n{prompt}")
    filename = input(f"Enter filename (in {default_folder}): ").strip()

    # Remove quotes if user copied path with quotes
    filename = filename.strip('"').strip("'")

    if default_folder == "Downloads":
        home = os.path.expanduser("~")
        full_path = os.path.join(home, "Downloads", filename)
    else:
        full_path = filename

    if not full_path.endswith('.csv'):
        full_path += '.csv'

    if os.path.exists(full_path):
        print(f"  Found: {full_path}")
        return full_path
    else:
        print(f"  File not found: {full_path}")
        return None


def extract_teams_from_csv(filepath):
    """Extract team names and colors from a TruMedia CSV file.

    Args:
        filepath: Path to the CSV file

    Returns:
        dict with keys:
            - 'teams': list of team names found
            - 'colors': dict mapping team name to hex color
            - 'home_team': home team name (if available)
            - 'away_team': away team name (if available)
    """
    result = {
        'teams': [],
        'colors': {},
        'home_team': None,
        'away_team': None
    }

    try:
        with open(filepath, encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)

            # Find relevant column indices
            def get_idx(col_name):
                try:
                    return header.index(col_name)
                except ValueError:
                    return None

            team_idx = get_idx('Team')
            team_abbrev_idx = get_idx('teamAbbrevName')
            color_idx = get_idx('newestTeamColor')
            home_idx = get_idx('homeTeam')
            away_idx = get_idx('awayTeam')

            teams_seen = set()
            first_row = True

            for row in reader:
                if len(row) < len(header):
                    continue

                # Get home/away from first row
                if first_row:
                    if home_idx is not None and row[home_idx]:
                        result['home_team'] = row[home_idx]
                    if away_idx is not None and row[away_idx]:
                        result['away_team'] = row[away_idx]
                    first_row = False

                # Get team name (prefer full name over abbreviation)
                team = None
                if team_idx is not None and row[team_idx]:
                    team = row[team_idx]
                elif team_abbrev_idx is not None and row[team_abbrev_idx]:
                    team = row[team_abbrev_idx]

                if team and team not in teams_seen:
                    teams_seen.add(team)
                    result['teams'].append(team)

                    # Get color if available
                    if color_idx is not None and row[color_idx]:
                        result['colors'][team] = row[color_idx]

    except Exception as e:
        print(f"Error extracting teams from CSV: {e}")

    return result


def get_output_folder(prompt="Output folder (press Enter for Downloads): "):
    """Get output folder from user input.

    Args:
        prompt: Prompt to display

    Returns:
        Path to output folder
    """
    output_folder = input(f"\n{prompt}").strip()
    if not output_folder:
        output_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    return output_folder
