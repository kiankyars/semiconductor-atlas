"""SQLite connection and migration management."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path


MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


def connect(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    applied = {
        row["version"]: (row["name"], row["sha256"])
        for row in connection.execute(
            "SELECT version, name, sha256 FROM schema_migrations"
        )
    }
    installed: list[int] = []
    migration_dir = Path(__file__).with_name("migrations")
    for path in sorted(migration_dir.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        name = match.group(2)
        script = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
        if version in applied:
            if applied[version] != (name, checksum):
                raise RuntimeError(f"applied migration {version:04d} no longer matches disk")
            continue
        quoted_name = name.replace("'", "''")
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + "\nINSERT INTO schema_migrations(version, name, sha256) VALUES ("
                + f"{version}, '{quoted_name}', '{checksum}');\nCOMMIT;\n"
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        installed.append(version)
    return installed


def initialize(path: str | Path) -> tuple[sqlite3.Connection, list[int]]:
    connection = connect(path)
    return connection, apply_migrations(connection)


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)
