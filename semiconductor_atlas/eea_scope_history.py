"""Portable, parent-bound restoration of EEA scope history, not backdated admission."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import eea_scope_revisions as scope
from . import ingest_eea_industrial as seed
from .ai_critical_changes import _open_real_directory_fd, _strict_json
from .eea_industrial_review import parse_eea_industrial_review_queue_bytes
from .repository import stable_id, validate_database


FORMAT = "semiconductor-atlas-eea-scope-history-v1"
_TABLES = (
    "source_families", "sources", "source_documents", "ingestion_runs",
    "ingestion_run_documents", "source_records", "entities", "claim_series",
    "claim_versions", "claim_values", "scalar_values", "claim_evidence",
)


def _file(path_value) -> Path:
    path = Path(path_value).absolute()
    _, descriptor = _open_real_directory_fd(path.parent, "EEA history parent", create=False)
    os.close(descriptor)
    if path.is_symlink() or not path.is_file():
        raise ValueError("EEA history input must be a regular non-symlink file")
    if Path(str(path) + "-wal").exists() or Path(str(path) + "-journal").exists():
        raise ValueError("close/checkpoint EEA history input before portable export or restore")
    return path


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _state(path: Path) -> tuple:
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, _sha(path)


@contextmanager
def _connection(path: Path, *, write=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        db.close()


def _schema(db) -> str:
    rows = [list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name")]
    return scope._hash(rows)


def _parent(db) -> None:
    if db.execute("SELECT 1 FROM sources WHERE id = ?",
                  (stable_id("source", seed.SOURCE_KEY),)).fetchone() is not None:
        raise ValueError("portable parent must precede the EEA genesis import")
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or validate_database(db):
        raise ValueError("portable parent database failed validation")


def _table(db, table: str, where: str, values: tuple) -> dict:
    columns = [row["name"] for row in db.execute('PRAGMA table_info("' + table + '")')]
    rows = [list(row) for row in db.execute('SELECT * FROM "' + table + '" WHERE ' + where, values)]
    rows.sort(key=scope._raw)
    return {"columns": columns, "rows": rows}


def _graph(db, run_ids: list[str]) -> dict:
    if not run_ids:
        return {table: _table(db, table, "0", ()) for table in _TABLES}
    marks = ",".join("?" for _ in run_ids)
    run_condition = f"IN ({marks})"
    source_id = stable_id("source", seed.SOURCE_KEY)
    family_id = stable_id("source-family", seed.SOURCE_FAMILY_KEY)
    queries = {
        "source_families": ("id = ?", (family_id,)),
        "sources": ("id = ?", (source_id,)),
        "source_documents": ("source_id = ?", (source_id,)),
        "ingestion_runs": ("id " + run_condition, tuple(run_ids)),
        "ingestion_run_documents": ("ingestion_run_id " + run_condition, tuple(run_ids)),
        "source_records": ("ingestion_run_id " + run_condition, tuple(run_ids)),
        "entities": ("created_by_run_id " + run_condition, tuple(run_ids)),
        "claim_series": (f"subject_entity_id IN (SELECT id FROM entities WHERE created_by_run_id {run_condition})", tuple(run_ids)),
        "claim_versions": ("created_by_run_id " + run_condition, tuple(run_ids)),
    }
    for table in ("claim_values", "scalar_values", "claim_evidence"):
        queries[table] = (f"claim_version_id IN (SELECT id FROM claim_versions WHERE created_by_run_id {run_condition})", tuple(run_ids))
    return {table: _table(db, table, *queries[table]) for table in _TABLES}


def export_history(database, *, parent_database, candidate_queue, recorded_at: str) -> dict:
    path, parent = _file(database), _file(parent_database)
    states = {path: _state(path), parent: _state(parent)}
    queue = seed._load_queue(candidate_queue)
    with _connection(path) as db, _connection(parent) as base:
        _parent(base)
        if _schema(db) != _schema(base):
            raise ValueError("scope history and portable parent schemas differ")
        db.execute("ATTACH DATABASE ? AS parent", (parent.as_uri() + "?mode=ro",))
        for (table,) in db.execute("SELECT name FROM parent.sqlite_master WHERE type='table'"):
            if not table.replace("_", "").isalnum():
                raise ValueError("unsupported parent table name")
            missing = db.execute(f'SELECT COUNT(*) FROM (SELECT * FROM parent."{table}" '
                                 f'EXCEPT SELECT * FROM main."{table}")').fetchone()[0]
            if missing:
                raise ValueError("scope history does not preserve every parent row")
        report = scope.scope_records(db, candidate_queue=queue, recorded_at=recorded_at)
        graph = _graph(db, [run["run_id"] for run in report["history"]])
        artifact = {"format": FORMAT, "recorded_at": recorded_at,
            "parent": {"sha256": states[parent][-1], "bytes": parent.stat().st_size,
                       "schema_sha256": _schema(base)},
            "candidate_queue_utf8": queue.raw_bytes.decode("utf-8"),
            "candidate_queue_sha256": queue.raw_sha256, "scope_report": report,
            "tables": graph, "tables_sha256": scope._hash(graph),
            "restoration_only": True}
    for item, state in states.items():
        if _file(item) != item or _state(item) != state:
            raise ValueError("scope history input changed during export")
    return artifact


def restore_history(parent_database, *, history_file, output_database) -> dict:
    parent, packet = _file(parent_database), _file(history_file)
    states = {parent: _state(parent), packet: _state(packet)}
    raw = packet.read_bytes()
    artifact = _strict_json(raw, "EEA scope history")
    fields = {"format", "recorded_at", "parent", "candidate_queue_utf8",
              "candidate_queue_sha256", "scope_report", "tables", "tables_sha256", "restoration_only"}
    if (not isinstance(artifact, dict) or set(artifact) != fields or artifact["format"] != FORMAT
            or artifact["restoration_only"] is not True or raw != scope._raw(artifact)):
        raise ValueError("invalid canonical EEA scope history artifact")
    if artifact["tables_sha256"] != scope._hash(artifact["tables"]):
        raise ValueError("EEA history table hash differs")
    queue = parse_eea_industrial_review_queue_bytes(artifact["candidate_queue_utf8"].encode("utf-8"))
    if queue.raw_sha256 != artifact["candidate_queue_sha256"]:
        raise ValueError("EEA history queue hash differs")
    if artifact["parent"]["sha256"] != states[parent][-1] or artifact["parent"]["bytes"] != parent.stat().st_size:
        raise ValueError("EEA history requires the exact verified parent database")
    output = Path(output_database).absolute()
    _, parent_fd = _open_real_directory_fd(output.parent, "restored EEA database parent", create=False)
    try:
        if output.exists() or output.is_symlink():
            raise ValueError("EEA restoration output already exists")
        with _connection(parent) as base:
            _parent(base)
            if _schema(base) != artifact["parent"]["schema_sha256"]:
                raise ValueError("EEA portable parent schema hash differs")
            if set(artifact["tables"]) != set(_TABLES):
                raise ValueError("EEA history has unsupported row tables")
            for table in _TABLES:
                actual = [row["name"] for row in base.execute('PRAGMA table_info("' + table + '")')]
                entry = artifact["tables"][table]
                if (not isinstance(entry, dict) or set(entry) != {"columns", "rows"}
                        or entry["columns"] != actual or not isinstance(entry["rows"], list)
                        or any(not isinstance(row, list) or len(row) != len(actual) for row in entry["rows"])):
                    raise ValueError("EEA history table columns or rows differ")
            with tempfile.TemporaryDirectory(prefix=".eea-restore-", dir=output.parent) as stage:
                staged = Path(stage) / "restored.sqlite"
                base.execute("VACUUM INTO ?", (str(staged),))
                with _connection(staged, write=True) as db:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        for table in _TABLES:
                            entry = artifact["tables"][table]
                            marks = ",".join("?" for _ in entry["columns"])
                            db.executemany('INSERT INTO "' + table + '" VALUES (' + marks + ')', entry["rows"])
                        if validate_database(db):
                            raise ValueError("restored EEA database fails semantic validation")
                        report = scope.scope_records(db, candidate_queue=queue, recorded_at=artifact["recorded_at"])
                        if report != artifact["scope_report"]:
                            raise ValueError("restored EEA history does not replay the scoped report")
                        if _graph(db, [row["run_id"] for row in report["history"]]) != artifact["tables"]:
                            raise ValueError("restored EEA row graph differs")
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
                for item, state in states.items():
                    if _file(item) != item or _state(item) != state:
                        raise ValueError("EEA restoration input changed before publication")
                with staged.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.link(staged, output.name, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
        return {"database": str(output), "head_run_id": report["head_run_id"],
                "restored_runs": len(report["history"]), "historical_restoration": True,
                "parent_sha256": states[parent][-1], "history_sha256": scope._hash(raw)}
    finally:
        os.close(parent_fd)


def write_history(database, *, parent_database, candidate_queue, recorded_at: str, output_file) -> dict:
    artifact = export_history(database, parent_database=parent_database,
                              candidate_queue=candidate_queue, recorded_at=recorded_at)
    raw = scope._raw(artifact)
    output = Path(output_file).absolute()
    _, descriptor = _open_real_directory_fd(output.parent, "EEA export parent", create=False)
    try:
        if output.exists() or output.is_symlink():
            raise ValueError("EEA export output already exists")
        with tempfile.TemporaryDirectory(prefix=".eea-export-", dir=output.parent) as stage:
            staged = Path(stage) / "history.json"
            with staged.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(staged, output.name, dst_dir_fd=descriptor)
            os.fsync(descriptor)
        return {"path": str(output), "sha256": scope._hash(raw), "bytes": len(raw),
                "head_run_id": artifact["scope_report"]["head_run_id"],
                "recorded_at": recorded_at, "parent_sha256": artifact["parent"]["sha256"]}
    finally:
        os.close(descriptor)
