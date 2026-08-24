"""Shared store for the Data Manager's config, so it does not travel by git.

config.json is written locally by the Data Manager but the deployed chart maker
used to receive it only via a commit + push. Two transports for one piece of
state, with a human in between - so a season could exist in MotherDuck while the
app had no idea, silently.

MotherDuck is the shared copy now. Both apps already connect to it, so this adds
no service, no credential and no deploy step: the config travels the same way the
match data does.

  data_manager/config.json   local working file (like data/last_updated.json)
  app_config in MotherDuck   the shared copy the chart maker reads

`write_config` is called by the Data Manager in the same action that writes the
file, so the two cannot drift.
"""
import json
from datetime import datetime, timezone

CONFIG_TABLE = "app_config"
CONFIG_KEY = "data_manager_config"

# History is kept in the same table rather than in git: `key` is the live row,
# and prior versions land under "<key>@<timestamp>" so a bad write is
# recoverable without needing the file to have been committed.
CONFIG_DDL = f"""
CREATE TABLE IF NOT EXISTS {CONFIG_TABLE} (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    updated_at TIMESTAMP
)
"""

_KEEP_VERSIONS = 5


def write_config(con, config, keep_versions=_KEEP_VERSIONS):
    """Upsert the live config and retain a few prior versions.

    Returns the timestamp written. Raises on failure - callers should surface it
    rather than swallow it, because a silent failure here recreates exactly the
    divergence this module exists to remove.
    """
    con.execute(CONFIG_DDL)
    now = datetime.now(timezone.utc)
    blob = json.dumps(config, ensure_ascii=False, separators=(",", ":"))

    prev = con.execute(
        f"SELECT value, updated_at FROM {CONFIG_TABLE} WHERE key = ?", [CONFIG_KEY]
    ).fetchone()
    if prev and prev[0] != blob:
        stamp = (prev[1] or now).strftime("%Y%m%dT%H%M%S")
        con.execute(
            f"INSERT OR REPLACE INTO {CONFIG_TABLE} VALUES (?, ?, ?)",
            [f"{CONFIG_KEY}@{stamp}", prev[0], prev[1]],
        )

    con.execute(
        f"INSERT OR REPLACE INTO {CONFIG_TABLE} VALUES (?, ?, ?)",
        [CONFIG_KEY, blob, now],
    )

    olds = con.execute(
        f"SELECT key FROM {CONFIG_TABLE} WHERE key LIKE ? ORDER BY updated_at DESC",
        [f"{CONFIG_KEY}@%"],
    ).fetchall()
    for (k,) in olds[keep_versions:]:
        con.execute(f"DELETE FROM {CONFIG_TABLE} WHERE key = ?", [k])
    return now


def read_config(con):
    """Return the shared config dict, or None if it has never been written.

    None means "fall back to the local file" - it is not an error. Any real
    failure (no table, bad JSON, no connection) also returns None so the caller
    can degrade rather than take the whole app down.
    """
    try:
        row = con.execute(
            f"SELECT value FROM {CONFIG_TABLE} WHERE key = ?", [CONFIG_KEY]
        ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def config_status(con):
    """(updated_at, n_versions) for the shared copy, for the health panel."""
    try:
        row = con.execute(
            f"SELECT updated_at FROM {CONFIG_TABLE} WHERE key = ?", [CONFIG_KEY]
        ).fetchone()
        n = con.execute(
            f"SELECT COUNT(*) FROM {CONFIG_TABLE} WHERE key LIKE ?",
            [f"{CONFIG_KEY}@%"],
        ).fetchone()
    except Exception:
        return None, 0
    return (row[0] if row else None), (n[0] if n else 0)
