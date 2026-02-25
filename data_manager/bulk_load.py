"""
Bulk Load — Event Logs to MotherDuck
One-time script to load all existing local event log CSVs into MotherDuck.

Run from the data_manager folder:
    py bulk_load.py

After confirming success, the local CSV files can be deleted.
"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import load_secrets, get_motherduck_connection, upsert_events_to_motherduck

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.env")
EVENT_LOGS_DIR = os.path.join(BASE_DIR, "data", "event_logs")


def main():
    secrets = load_secrets(SECRETS_PATH)
    token = secrets.get("MOTHERDUCK_TOKEN")
    if not token:
        print("ERROR: MOTHERDUCK_TOKEN not found in secrets.env")
        sys.exit(1)

    csv_files = sorted(glob.glob(os.path.join(EVENT_LOGS_DIR, "*.csv")))
    if not csv_files:
        print(f"No CSV files found in {EVENT_LOGS_DIR}")
        sys.exit(0)

    print(f"Found {len(csv_files)} CSV files. Connecting to MotherDuck...")
    con = get_motherduck_connection(token)
    print("Connected.\n")

    success_count = 0
    fail_count = 0
    errors = []

    for i, csv_path in enumerate(csv_files, 1):
        abbrev = os.path.splitext(os.path.basename(csv_path))[0]
        try:
            row_count = upsert_events_to_motherduck(token, csv_path, con=con)
            print(f"[{i:>3}/{len(csv_files)}] {abbrev:<6}  {row_count:>7,} rows")
            success_count += 1
        except Exception as e:
            print(f"[{i:>3}/{len(csv_files)}] {abbrev:<6}  FAILED: {e}")
            errors.append((abbrev, str(e)))
            fail_count += 1

    con.close()

    print(f"\n{'='*40}")
    print(f"Done.  {success_count} succeeded,  {fail_count} failed.")
    if errors:
        print("\nFailed teams:")
        for abbrev, msg in errors:
            print(f"  {abbrev}: {msg}")
    else:
        print("All files loaded successfully.")
        print(f"\nYou can now delete the files in:\n  {EVENT_LOGS_DIR}")


if __name__ == "__main__":
    main()
