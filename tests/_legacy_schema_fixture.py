"""Project fixture data into an actual historical schema, without faking its DDL."""

import sqlite3

from semiconductor_atlas.database import initialize


def historical_fixture(source: sqlite3.Connection, version: int) -> sqlite3.Connection:
    target, _ = initialize(":memory:", target_version=version)
    target.execute("PRAGMA foreign_keys = OFF")
    try:
        for (table,) in target.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'"
        ).fetchall():
            columns = ", ".join(f'"{row[1]}"' for row in target.execute(f'PRAGMA table_info("{table}")'))
            rows = source.execute(f'SELECT {columns} FROM "{table}"').fetchall()
            if rows:
                placeholders = ", ".join("?" for _ in rows[0])
                target.executemany(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', rows)
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("fixture projection lost required lineage")
        target.commit()
    except BaseException:
        target.close()
        raise
    target.execute("PRAGMA foreign_keys = ON")
    return target
