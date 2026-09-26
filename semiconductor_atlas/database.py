"""SQLite connection and migration management."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")
REBUILD_MIGRATIONS = frozenset({5})


def _timestamp_microseconds(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            return None
        elapsed = stamp.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        return ((elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds)
    except (ValueError, OverflowError):
        return None


def knowledge_clock_sql(connection: sqlite3.Connection) -> str:
    """Schema 5 knowledge cutoffs retain microseconds instead of SQLite date rounding."""
    if schema_version(connection) < 5:
        return "julianday"
    connection.create_function("atlas_timestamp_us", 1, _timestamp_microseconds, deterministic=True)
    return "atlas_timestamp_us"


def connect(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def apply_migrations(connection: sqlite3.Connection, *, target_version: int | None = None) -> list[int]:
    if connection.in_transaction:
        raise ValueError("finish the current transaction before applying migrations")
    if target_version is not None and (type(target_version) is not int or target_version < 1):
        raise ValueError("target_version must be a positive schema version")
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
    available_versions = {int(match.group(1)) for path in migration_dir.glob("*.sql")
                          if (match := MIGRATION_PATTERN.match(path.name))}
    if target_version is not None and (target_version not in available_versions
                                       or max(applied, default=0) > target_version):
        raise ValueError("unknown target schema or attempted downgrade")
    for path in sorted(migration_dir.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        if target_version is not None and version > target_version:
            continue
        name = match.group(2)
        script = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
        if version in applied:
            if applied[version] != (name, checksum):
                raise RuntimeError(f"applied migration {version:04d} no longer matches disk")
            continue
        quoted_name = name.replace("'", "''")
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if version in REBUILD_MIGRATIONS:
            connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + "\nINSERT INTO schema_migrations(version, name, sha256) VALUES ("
                + f"{version}, '{quoted_name}', '{checksum}');\n"
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError(f"migration {version:04d} violates a foreign key")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.execute(f"PRAGMA foreign_keys = {foreign_keys}")
        installed.append(version)
    return installed


def initialize(path: str | Path, *, target_version: int | None = None) -> tuple[sqlite3.Connection, list[int]]:
    connection = connect(path)
    try:
        return connection, apply_migrations(connection, target_version=target_version)
    except BaseException:
        connection.close()
        raise


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)
