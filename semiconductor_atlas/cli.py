"""Command-line workflows for archived-source ingestion and auditable releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Sequence

from .alerts import detect_revision_alerts, serialize_alerts
from .analytics import (
    forecast_csv,
    forecast_current_capacity,
    forecast_exclusions_jsonl,
    forecast_jsonl,
    forecast_summary,
)
from .database import connect, initialize, schema_version
from .eea_industrial_review import (
    read_eea_industrial_review_file,
    read_eea_industrial_review_queue_file,
)
from .eea_industrial_snapshot import verify_eea_industrial_snapshot
from .ingest_epa_frs import import_epa_frs_candidates
from .ingest_eea_industrial import accept_eea_industrial_review
from .ingest_gleif import import_gleif_level1
from .ingest_moenv import import_moenv_ems_candidates
from .ingest_nist import import_nist_awards
from .ingest_osm import import_osm_candidates
from .ingest_taiwan_factory import import_taiwan_factory_candidates
from .ingest_taiwan_mof import import_taiwan_mof_snapshot
from .gleif_review import read_gleif_review_file
from .gleif_snapshot import verify_gleif_snapshot
from .moenv_snapshot import MOENV_CANDIDATE_FILENAME, verify_moenv_snapshot
from .taiwan_factory_snapshot import (
    TAIWAN_FACTORY_CANDIDATE_FILENAME,
    verify_taiwan_factory_snapshot,
)
from .taiwan_mof_snapshot import (
    TAIWAN_MOF_ALLOWLIST_FILENAME,
    TAIWAN_MOF_MATCHED_FILENAME,
    verify_taiwan_mof_allowlist_sources,
)
from .release import write_release
from .service import claim_records, summarize, validate_semantics
from .snapshot import (
    EPA_FRS_RAW_RECORD_TYPE,
    EPA_FRS_RECORD_TYPE,
    EPA_FRS_SCOPE,
    verify_snapshot,
)


def _date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD")
    return parsed.isoformat()


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _existing_database(path: Path):
    if not path.is_file():
        raise ValueError(f"database does not exist: {path}")
    connection = connect(path)
    try:
        version = schema_version(connection)
    except sqlite3.Error:
        connection.close()
        raise ValueError(
            f"database has no Semiconductor Atlas schema: {path}"
        ) from None
    if version == 0:
        connection.close()
        raise ValueError(f"database has no Semiconductor Atlas schema: {path}")
    return connection


def _result_counts(value: object) -> dict[str, Any]:
    result = asdict(value)
    return {
        key: len(item) if isinstance(item, tuple) else item
        for key, item in result.items()
    }


def _assert_imported_hashes(connection, snapshot, nist, osm) -> None:
    expected_pairs = [
        (item.url, item.sha256)
        for item in snapshot.inputs
        if item.record_type in {"award_index_page", "award_detail_page"}
    ]
    expected_nist = dict(expected_pairs)
    if len(expected_nist) != len(expected_pairs):
        raise ValueError("snapshot contains duplicate NIST source URLs")
    placeholders = ",".join("?" for _ in nist.source_document_ids)
    actual_pairs = [
        (row[0], row[1])
        for row in connection.execute(
            f"SELECT document_url, content_sha256 FROM source_documents WHERE id IN ({placeholders})",
            nist.source_document_ids,
        )
    ]
    actual_nist = dict(actual_pairs)
    osm_hash = connection.execute(
        "SELECT document_url, content_sha256 FROM source_documents WHERE id = ?",
        (osm.source_document_id,),
    ).fetchone()
    if len(actual_nist) != len(actual_pairs) or expected_nist != actual_nist:
        raise ValueError(
            "imported NIST URL-to-byte provenance does not match the verified snapshot manifest"
        )
    osm_input = snapshot.one("osm_overpass_snapshot")
    if osm_hash is None or tuple(osm_hash) != (osm_input.url, osm_input.sha256):
        raise ValueError(
            "imported OSM bytes do not match the verified snapshot manifest"
        )


def _assert_snapshot_file_unchanged(
    path: Path,
    relative_path: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Recheck an input at the database acceptance boundary.

    Snapshot verification can be expensive, so an input could otherwise be replaced
    after verification and before the transaction commits. Open without following a
    final symlink, hash through that descriptor, and confirm that the path still names
    the same regular file after the read.
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(
            f"verified snapshot file is unavailable at acceptance: {relative_path}"
        ) from error
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(
                    "verified snapshot file is not a regular file at acceptance: "
                    f"{relative_path}"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno())
    except BaseException:
        # os.fdopen owns and closes the descriptor once it succeeds.
        raise
    try:
        path_after = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ValueError(
            f"verified snapshot file disappeared at acceptance: {relative_path}"
        ) from error
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if (
        not stat.S_ISREG(path_after.st_mode)
        or any(
            getattr(before, field) != getattr(after, field) for field in stable_fields
        )
        or any(
            getattr(after, field) != getattr(path_after, field)
            for field in stable_fields
        )
        or size != expected_size
        or digest.hexdigest() != expected_sha256
    ):
        raise ValueError(
            f"verified snapshot file changed before acceptance: {relative_path}"
        )


def _assert_snapshot_input_unchanged(item) -> None:
    _assert_snapshot_file_unchanged(
        item.path,
        item.relative_path,
        item.sha256,
        item.size,
    )


def _assert_snapshot_manifest_unchanged(snapshot) -> None:
    _assert_snapshot_file_unchanged(
        snapshot.root / "manifest.json",
        "manifest.json",
        snapshot.manifest_sha256,
        snapshot.manifest_size,
    )


def _assert_imported_frs_hashes(connection, snapshot, result) -> None:
    frs_input = snapshot.one(EPA_FRS_RECORD_TYPE)
    raw_input = snapshot.one(EPA_FRS_RAW_RECORD_TYPE)
    scope = snapshot.source_scopes[EPA_FRS_SCOPE]
    archive = scope["upstream_archive"]
    raw_retention = scope["raw_retention"]
    if (
        (raw_input.url, raw_input.sha256, raw_input.size)
        != (archive["url"], archive["sha256"], archive["bytes"])
        or raw_retention.get("blob_locator") != raw_input.relative_path
        or raw_retention.get("upstream_sha256") != raw_input.sha256
        or raw_retention.get("bytes") != raw_input.size
    ):
        raise ValueError(
            "EPA FRS retained raw input disagrees with the verified snapshot scope"
        )
    document = connection.execute(
        """
        SELECT documents.document_url, documents.content_sha256,
               runs.parameters_json
        FROM source_documents AS documents
        JOIN ingestion_runs AS runs ON runs.input_document_id = documents.id
        WHERE documents.id = ? AND runs.id = ?
        """,
        (result.source_document_id, result.ingestion_run_id),
    ).fetchone()
    if document is None:
        raise ValueError("EPA FRS import did not create its upstream source document")
    if (document["document_url"], document["content_sha256"]) != (
        archive["url"],
        archive["sha256"],
    ):
        raise ValueError(
            "imported EPA FRS upstream archive provenance does not match the snapshot scope"
        )
    parameters = json.loads(document["parameters_json"])
    expected_snapshot = {
        "directory_name": snapshot.root.name,
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_archive_relative_path": raw_input.relative_path,
    }
    if (
        parameters.get("selected_artifact_sha256") != frs_input.sha256
        or parameters.get("selected_artifact_bytes") != frs_input.size
        or parameters.get("source_snapshot") != expected_snapshot
    ):
        raise ValueError(
            "imported EPA FRS processing provenance does not match the verified snapshot"
        )
    _assert_snapshot_input_unchanged(frs_input)
    _assert_snapshot_input_unchanged(raw_input)
    _assert_snapshot_manifest_unchanged(snapshot)


def _init(args: argparse.Namespace) -> dict[str, object]:
    connection, installed = initialize(args.database)
    try:
        return {
            "database": str(args.database.resolve()),
            "installed_migrations": installed,
            "schema_version": schema_version(connection),
        }
    finally:
        connection.close()


def _ingest_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_snapshot(args.snapshot)
    index_inputs = [
        (item.path, item.url)
        for item in snapshot.inputs
        if item.record_type == "award_index_page"
    ]
    detail_inputs = [
        (item.path, item.url)
        for item in snapshot.inputs
        if item.record_type == "award_detail_page"
    ]
    osm_input = snapshot.one("osm_overpass_snapshot")
    if not index_inputs:
        raise ValueError("snapshot contains no NIST award index pages")

    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            nist = import_nist_awards(
                connection,
                index_inputs,
                detail_inputs,
                snapshot.retrieved_at,
                args.as_of,
                snapshot_is_complete=snapshot.scope_is_complete("nist_chips_awards"),
            )
            osm = import_osm_candidates(
                connection,
                osm_input.path,
                snapshot.retrieved_at,
                args.as_of,
                query=osm_input.metadata.get("query"),
                document_url=osm_input.url,
            )
            _assert_imported_hashes(connection, snapshot, nist, osm)
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "as_of": args.as_of,
            "database": str(args.database.resolve()),
            "installed_migrations": installed,
            "nist": _result_counts(nist),
            "osm": _result_counts(osm),
            "retrieved_at": snapshot.retrieved_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": len(snapshot.inputs),
        }
    finally:
        connection.close()


def _ingest_frs_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_snapshot(args.snapshot)
    accepted_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if args.accept_now
        else args.accepted_at
    )
    acceptance_timestamp_basis = (
        "process_clock_after_snapshot_verification"
        if args.accept_now
        else "explicit_operator_supplied"
    )
    unexpected = [
        item.record_type
        for item in snapshot.inputs
        if item.record_type not in {EPA_FRS_RECORD_TYPE, EPA_FRS_RAW_RECORD_TYPE}
    ]
    if unexpected:
        raise ValueError(
            "EPA FRS snapshot command does not consume record types: "
            + ", ".join(sorted(set(unexpected)))
        )
    frs_input = snapshot.one(EPA_FRS_RECORD_TYPE)
    raw_input = snapshot.one(EPA_FRS_RAW_RECORD_TYPE)
    scope = snapshot.source_scopes.get(EPA_FRS_SCOPE)
    if scope is None:
        raise ValueError("EPA FRS snapshot lacks required source scope metadata")
    scope = dict(scope)
    scope["source_snapshot"] = {
        "directory_name": snapshot.root.name,
        "manifest_sha256": snapshot.manifest_sha256,
        "raw_archive_relative_path": raw_input.relative_path,
    }

    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            frs = import_epa_frs_candidates(
                connection,
                frs_input.path,
                snapshot.retrieved_at,
                args.as_of,
                accepted_at=accepted_at,
                acceptance_timestamp_basis=acceptance_timestamp_basis,
                document_url=frs_input.url,
                scope_metadata=scope,
                snapshot_is_complete=snapshot.scope_is_complete(EPA_FRS_SCOPE),
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
            _assert_imported_frs_hashes(connection, snapshot, frs)
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "as_of": args.as_of,
            "accepted_at": accepted_at,
            "acceptance_timestamp_basis": acceptance_timestamp_basis,
            "database": str(args.database.resolve()),
            "epa_frs": _result_counts(frs),
            "installed_migrations": installed,
            "retrieved_at": snapshot.retrieved_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": len(snapshot.inputs),
        }
    finally:
        connection.close()


def _assert_imported_gleif_hashes(connection, snapshot, review, result) -> None:
    canonical = connection.execute(
        """
        SELECT content_sha256
        FROM source_documents
        WHERE id = ?
        """,
        (result.canonical_document_id,),
    ).fetchone()
    if canonical is None or canonical["content_sha256"] != snapshot.canonical_sha256:
        raise ValueError(
            "imported GLEIF canonical provenance does not match the verified snapshot"
        )
    if len(result.raw_document_ids) != len(snapshot.responses):
        raise ValueError("GLEIF import did not retain exactly one raw document per LEI")
    actual_raw = {
        tuple(row)
        for row in connection.execute(
            """
            SELECT document_url, retrieved_at, content_sha256
            FROM source_documents
            WHERE id IN ({})
            """.format(",".join("?" for _ in result.raw_document_ids)),
            result.raw_document_ids,
        )
    }
    expected_raw = {
        (response.url, response.retrieved_at, response.sha256)
        for response in snapshot.responses
    }
    if actual_raw != expected_raw:
        raise ValueError(
            "imported GLEIF raw provenance does not match the verified snapshot"
        )
    resolution = connection.execute(
        "SELECT parameters_json FROM entity_resolution_runs WHERE id = ?",
        (result.resolution_run_id,),
    ).fetchone()
    if resolution is None:
        raise ValueError("GLEIF import did not retain its review resolution run")
    parameters = json.loads(resolution["parameters_json"])
    if (
        parameters.get("manifest_sha256") != snapshot.manifest_sha256
        or parameters.get("review_raw_sha256") != review.raw_sha256
        or parameters.get("review_canonical_sha256") != review.canonical_sha256
    ):
        raise ValueError(
            "imported GLEIF resolution provenance does not match the review"
        )


def _assert_moenv_snapshot_unchanged(snapshot) -> None:
    """Bind database acceptance to the exact verified MOENV snapshot bytes."""

    _assert_snapshot_file_unchanged(
        snapshot.root / MOENV_CANDIDATE_FILENAME,
        MOENV_CANDIDATE_FILENAME,
        snapshot.candidate_sha256,
        snapshot.candidate_size,
    )
    _assert_snapshot_file_unchanged(
        snapshot.root / snapshot.raw_path,
        snapshot.raw_path,
        snapshot.raw_sha256,
        snapshot.raw_size,
    )
    _assert_snapshot_manifest_unchanged(snapshot)


def _assert_taiwan_factory_snapshot_unchanged(snapshot) -> None:
    """Bind database acceptance to all verified factory snapshot bytes."""

    _assert_snapshot_file_unchanged(
        snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME,
        TAIWAN_FACTORY_CANDIDATE_FILENAME,
        snapshot.candidate_sha256,
        snapshot.candidate_size,
    )
    _assert_snapshot_file_unchanged(
        snapshot.root / snapshot.raw_path,
        snapshot.raw_path,
        snapshot.raw_sha256,
        snapshot.raw_size,
    )
    _assert_snapshot_manifest_unchanged(snapshot)


def _assert_taiwan_mof_snapshot_unchanged(
    snapshot,
    *,
    moenv_snapshot: Path,
    factory_snapshot: Path,
) -> None:
    """Re-verify the MOF tree and both allowlist sources before commit."""

    try:
        refreshed = verify_taiwan_mof_allowlist_sources(
            snapshot.root, moenv_snapshot, factory_snapshot
        )
    except ValueError as error:
        raise ValueError(
            f"Taiwan MOF snapshot or bound sources changed before acceptance: {error}"
        ) from error
    if refreshed != snapshot:
        raise ValueError("Taiwan MOF snapshot identity changed before acceptance")


def _ingest_gleif_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_gleif_snapshot(args.snapshot)
    review = read_gleif_review_file(args.review_plan)
    accepted_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if args.accept_now
        else args.accepted_at
    )
    acceptance_timestamp_basis = (
        "process_clock_after_snapshot_and_review_verification"
        if args.accept_now
        else "explicit_operator_supplied"
    )
    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            gleif = import_gleif_level1(
                connection,
                snapshot,
                review,
                accepted_at=accepted_at,
                acceptance_timestamp_basis=acceptance_timestamp_basis,
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
            _assert_imported_gleif_hashes(connection, snapshot, review, gleif)
            refreshed_snapshot = verify_gleif_snapshot(snapshot.root)
            refreshed_review = read_gleif_review_file(review.path or args.review_plan)
            if (
                refreshed_snapshot.manifest_sha256 != snapshot.manifest_sha256
                or refreshed_snapshot.canonical_sha256 != snapshot.canonical_sha256
                or tuple(
                    (item.lei, item.sha256, item.size)
                    for item in refreshed_snapshot.responses
                )
                != tuple(
                    (item.lei, item.sha256, item.size) for item in snapshot.responses
                )
                or refreshed_review.raw_sha256 != review.raw_sha256
                or refreshed_review.canonical_sha256 != review.canonical_sha256
            ):
                raise ValueError(
                    "GLEIF snapshot or review changed at the database acceptance boundary"
                )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "accepted_at": accepted_at,
            "acceptance_timestamp_basis": acceptance_timestamp_basis,
            "database": str(args.database.resolve()),
            "gleif": _result_counts(gleif),
            "installed_migrations": installed,
            "knowledge_cutoff_at": gleif.knowledge_cutoff_at,
            "retrieved_at": snapshot.retrieved_at,
            "review_plan": str((review.path or args.review_plan).resolve()),
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": len(snapshot.responses) + 2,
        }
    finally:
        connection.close()


def _ingest_eea_industrial_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_eea_industrial_snapshot(args.snapshot)
    queue = read_eea_industrial_review_queue_file(args.candidate_queue)
    review = read_eea_industrial_review_file(args.review, queue=queue)
    if args.database.is_symlink() or not args.database.is_file():
        raise ValueError("EEA admission requires an existing regular working database")
    connection = connect(args.database)
    installed: list[int] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            imported = accept_eea_industrial_review(
                connection,
                snapshot=snapshot,
                candidate_queue=queue,
                review=review,
                accepted_at=args.accepted_at,
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "accepted_at": imported.accepted_at,
            "acceptance_timestamp_basis": json.loads(connection.execute(
                "SELECT parameters_json FROM ingestion_runs WHERE id = ?",
                (imported.ingestion_run_id,),
            ).fetchone()[0])["acceptance_timestamp_basis"],
            "candidate_queue": str((queue.path or args.candidate_queue).resolve()),
            "candidate_queue_sha256": queue.raw_sha256,
            "database": str(args.database.resolve()),
            "eea_industrial": _result_counts(imported),
            "installed_migrations": installed,
            "knowledge_cutoff_at": review.knowledge_cutoff_at,
            "review": str((review.path or args.review).resolve()),
            "review_sha256": review.raw_sha256,
            "reviewed_at": review.reviewed_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
            "source_candidate_count": snapshot.candidate_count,
            "source_candidate_sha256": snapshot.candidate_sha256,
        }
    finally:
        connection.close()


def _ingest_moenv_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_moenv_snapshot(args.snapshot)
    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            moenv = import_moenv_ems_candidates(
                connection,
                snapshot,
                accepted_at=args.accepted_at,
                acceptance_timestamp_basis="explicit_operator_supplied",
                complete_refresh=args.complete_refresh,
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
            _assert_moenv_snapshot_unchanged(snapshot)
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "accepted_at": moenv.accepted_at,
            "acceptance_timestamp_basis": moenv.acceptance_timestamp_basis,
            "complete_refresh": moenv.complete_refresh,
            "database": str(args.database.resolve()),
            "dataset_updated_at": snapshot.dataset_updated_at,
            "dataset_updated_at_basis": snapshot.dataset_updated_at_basis,
            "installed_migrations": installed,
            "moenv": _result_counts(moenv),
            "retrieved_at": snapshot.retrieved_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": 3,
            "snapshot_integrity": {
                "candidate_derivative": {
                    "bytes": snapshot.candidate_size,
                    "facility_count": snapshot.facility_count,
                    "path": MOENV_CANDIDATE_FILENAME,
                    "record_count": snapshot.candidate_count,
                    "sha256": snapshot.candidate_sha256,
                },
                "manifest": {
                    "bytes": snapshot.manifest_size,
                    "path": "manifest.json",
                    "sha256": snapshot.manifest_sha256,
                },
                "raw_archive": {
                    "bytes": snapshot.raw_size,
                    "path": snapshot.raw_path,
                    "row_count": snapshot.row_count,
                    "sha256": snapshot.raw_sha256,
                },
            },
        }
    finally:
        connection.close()


def _ingest_taiwan_factory_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_taiwan_factory_snapshot(args.snapshot)
    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            factory = import_taiwan_factory_candidates(
                connection,
                snapshot,
                accepted_at=args.accepted_at,
                acceptance_timestamp_basis="explicit_operator_supplied",
                complete_refresh=args.complete_refresh,
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
            _assert_taiwan_factory_snapshot_unchanged(snapshot)
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "accepted_at": factory.accepted_at,
            "acceptance_timestamp_basis": factory.acceptance_timestamp_basis,
            "complete_refresh": factory.complete_refresh,
            "database": str(args.database.resolve()),
            "installed_migrations": installed,
            "retrieved_at": snapshot.retrieved_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": 3,
            "snapshot_integrity": {
                "candidate_derivative": {
                    "bytes": snapshot.candidate_size,
                    "path": TAIWAN_FACTORY_CANDIDATE_FILENAME,
                    "record_count": snapshot.candidate_count,
                    "sha256": snapshot.candidate_sha256,
                },
                "manifest": {
                    "bytes": snapshot.manifest_size,
                    "path": "manifest.json",
                    "sha256": snapshot.manifest_sha256,
                },
                "raw_archive": {
                    "bytes": snapshot.raw_size,
                    "path": snapshot.raw_path,
                    "row_count": snapshot.row_count,
                    "sha256": snapshot.raw_sha256,
                },
            },
            "source_updated_at": snapshot.source_updated_at,
            "source_updated_at_basis": snapshot.source_updated_at_basis,
            "taiwan_factory": _result_counts(factory),
        }
    finally:
        connection.close()


def _ingest_taiwan_mof_snapshot(args: argparse.Namespace) -> dict[str, object]:
    snapshot = verify_taiwan_mof_allowlist_sources(
        args.snapshot, args.moenv_snapshot, args.factory_snapshot
    )
    connection, installed = initialize(args.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            imported = import_taiwan_mof_snapshot(
                connection,
                snapshot,
                moenv_snapshot_dir=args.moenv_snapshot,
                factory_snapshot_dir=args.factory_snapshot,
                accepted_at=args.accepted_at,
                acceptance_timestamp_basis="explicit_operator_supplied",
                complete_refresh=args.complete_refresh,
            )
            errors = validate_semantics(connection)
            if errors:
                raise ValueError("database validation failed: " + "; ".join(errors))
            _assert_taiwan_mof_snapshot_unchanged(
                snapshot,
                moenv_snapshot=args.moenv_snapshot,
                factory_snapshot=args.factory_snapshot,
            )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return {
            "accepted_at": imported.accepted_at,
            "acceptance_timestamp_basis": imported.acceptance_timestamp_basis,
            "bound_source_snapshots_verified": 2,
            "complete_refresh": imported.complete_refresh,
            "database": str(args.database.resolve()),
            "installed_migrations": installed,
            "publisher_date": snapshot.publisher_date,
            "retrieved_at": snapshot.retrieved_at,
            "schema_version": schema_version(connection),
            "snapshot": str(snapshot.root),
            "snapshot_inputs_verified": 4,
            "snapshot_integrity": {
                "allowlist": {
                    "bytes": snapshot.allowlist_size,
                    "path": TAIWAN_MOF_ALLOWLIST_FILENAME,
                    "record_count": snapshot.allowlist_count,
                    "sha256": snapshot.allowlist_sha256,
                },
                "manifest": {
                    "bytes": snapshot.manifest_size,
                    "path": "manifest.json",
                    "sha256": snapshot.manifest_sha256,
                },
                "matched_derivative": {
                    "bytes": snapshot.matched_size,
                    "path": TAIWAN_MOF_MATCHED_FILENAME,
                    "record_count": snapshot.matched_count,
                    "sha256": snapshot.matched_sha256,
                },
                "raw_archive": {
                    "bytes": snapshot.raw_size,
                    "path": snapshot.raw_path,
                    "row_count": snapshot.row_count,
                    "sha256": snapshot.raw_sha256,
                },
            },
            "source_updated_at": snapshot.source_updated_at,
            "source_updated_at_basis": snapshot.source_updated_at_basis,
            "taiwan_mof": _result_counts(imported),
        }
    finally:
        connection.close()


def _validate(args: argparse.Namespace) -> dict[str, object]:
    connection = _existing_database(args.database)
    try:
        errors = validate_semantics(connection)
        return {
            "database": str(args.database.resolve()),
            "errors": errors,
            "ok": not errors,
            "schema_version": schema_version(connection),
        }
    finally:
        connection.close()


def _accept_project_targets(args: argparse.Namespace) -> dict[str, object]:
    from .project_target_review import accept_review

    connection = _existing_database(args.database)
    try:
        return accept_review(connection, args.review, reference_root=args.reference_root,
                             source_queue=args.source_queue)
    finally:
        connection.close()


def _summary(args: argparse.Namespace) -> dict[str, object]:
    connection = _existing_database(args.database)
    try:
        return summarize(connection, as_of=args.as_of, recorded_at=args.recorded_at)
    finally:
        connection.close()


def _release(args: argparse.Namespace) -> dict[str, object]:
    connection = _existing_database(args.database)
    try:
        with tempfile.TemporaryDirectory(
            prefix="semiconductor-atlas-release-snapshot-"
        ) as temporary:
            snapshot_connection = connect(Path(temporary) / "release.sqlite")
            connection.backup(snapshot_connection)
            snapshot_connection.commit()
            try:
                claims = claim_records(
                    snapshot_connection,
                    as_of=args.as_of,
                    recorded_at=args.recorded_at,
                )
                results = forecast_current_capacity(
                    snapshot_connection,
                    as_of=args.as_of,
                    recorded_at=args.recorded_at,
                    forecast_start=date.fromisoformat(args.forecast_start),
                    claims=claims,
                )
                alerts = detect_revision_alerts(
                    snapshot_connection,
                    as_of=args.as_of,
                    recorded_at=args.recorded_at,
                )
                extras = {
                    "alerts.jsonl": serialize_alerts(alerts),
                    "experimental_forecast_exclusions.jsonl": forecast_exclusions_jsonl(
                        results
                    ),
                    "experimental_forecast_summary.json": forecast_summary(results),
                    "experimental_forecasts.csv": forecast_csv(results),
                    "experimental_forecasts.jsonl": forecast_jsonl(results),
                }
                manifest = write_release(
                    snapshot_connection,
                    args.output,
                    as_of=args.as_of,
                    recorded_at=args.recorded_at,
                    extra_files=extras,
                )
            finally:
                snapshot_connection.close()
        return {
            **manifest,
            "alerts": len(alerts),
            "forecast_series": len(results),
            "forecast_exclusions": len(results.exclusions),
            "output": str(args.output.resolve()),
        }
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="semiconductor-atlas",
        description="Build and export the provenance-first Semiconductor Atlas claim store.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize an empty database")
    init_parser.add_argument("--database", required=True, type=Path)
    init_parser.set_defaults(handler=_init)

    ingest = subparsers.add_parser(
        "ingest-snapshot", help="verify and import one archived NIST and OSM snapshot"
    )
    ingest.add_argument("--database", required=True, type=Path)
    ingest.add_argument("--snapshot", required=True, type=Path)
    ingest.add_argument("--as-of", required=True, type=_date)
    ingest.set_defaults(handler=_ingest_snapshot)

    ingest_frs = subparsers.add_parser(
        "ingest-frs-snapshot",
        help="verify and import one filtered EPA FRS national candidate snapshot",
    )
    ingest_frs.add_argument("--database", required=True, type=Path)
    ingest_frs.add_argument("--snapshot", required=True, type=Path)
    ingest_frs.add_argument("--as-of", required=True, type=_date)
    acceptance = ingest_frs.add_mutually_exclusive_group(required=True)
    acceptance.add_argument(
        "--accepted-at",
        type=_timestamp,
        help="explicit database acceptance timestamp for deterministic rebuilds",
    )
    acceptance.add_argument(
        "--accept-now",
        action="store_true",
        help="sample the process clock after snapshot verification for a live refresh",
    )
    ingest_frs.set_defaults(handler=_ingest_frs_snapshot)

    ingest_gleif = subparsers.add_parser(
        "ingest-gleif-snapshot",
        help="verify and import one exact-allowlist GLEIF Level 1 snapshot and review",
    )
    ingest_gleif.add_argument("--database", required=True, type=Path)
    ingest_gleif.add_argument("--snapshot", required=True, type=Path)
    ingest_gleif.add_argument("--review-plan", required=True, type=Path)
    gleif_acceptance = ingest_gleif.add_mutually_exclusive_group(required=True)
    gleif_acceptance.add_argument(
        "--accepted-at",
        type=_timestamp,
        help="explicit database acceptance timestamp for deterministic rebuilds",
    )
    gleif_acceptance.add_argument(
        "--accept-now",
        action="store_true",
        help="sample the process clock after snapshot and review verification",
    )
    ingest_gleif.set_defaults(handler=_ingest_gleif_snapshot)

    ingest_eea = subparsers.add_parser(
        "ingest-eea-industrial-snapshot",
        help=(
            "verify and import only explicitly accepted EEA Industrial Reporting "
            "candidates"
        ),
    )
    ingest_eea.add_argument("--database", required=True, type=Path)
    ingest_eea.add_argument("--snapshot", required=True, type=Path)
    ingest_eea.add_argument("--candidate-queue", required=True, type=Path)
    ingest_eea.add_argument("--review", required=True, type=Path)
    eea_clock = ingest_eea.add_mutually_exclusive_group(required=True)
    eea_clock.add_argument(
        "--accepted-at",
        type=_timestamp,
        help="original acceptance timestamp for exact replay only",
    )
    eea_clock.add_argument(
        "--accept-now",
        action="store_true",
        help="sample actual admission after transaction-bound input validation",
    )
    ingest_eea.set_defaults(handler=_ingest_eea_industrial_snapshot)

    ingest_moenv = subparsers.add_parser(
        "ingest-moenv-snapshot",
        help="verify and import one Taiwan MOENV EMS_S_01 candidate snapshot",
    )
    ingest_moenv.add_argument("--database", required=True, type=Path)
    ingest_moenv.add_argument("--snapshot", required=True, type=Path)
    ingest_moenv.add_argument(
        "--accepted-at",
        required=True,
        type=_timestamp,
        help="explicit database acceptance timestamp for deterministic rebuilds",
    )
    completeness = ingest_moenv.add_mutually_exclusive_group()
    completeness.add_argument(
        "--complete",
        dest="complete_refresh",
        action="store_true",
        help="close absent prior same-filter assertions (default)",
    )
    completeness.add_argument(
        "--partial",
        dest="complete_refresh",
        action="store_false",
        help="retain absent prior assertions because this refresh is incomplete",
    )
    ingest_moenv.set_defaults(
        complete_refresh=True,
        handler=_ingest_moenv_snapshot,
    )

    ingest_taiwan_factory = subparsers.add_parser(
        "ingest-taiwan-factory-snapshot",
        help=(
            "verify and import one Taiwan registered-factory exact-261 "
            "candidate snapshot"
        ),
    )
    ingest_taiwan_factory.add_argument("--database", required=True, type=Path)
    ingest_taiwan_factory.add_argument("--snapshot", required=True, type=Path)
    ingest_taiwan_factory.add_argument(
        "--accepted-at",
        required=True,
        type=_timestamp,
        help="explicit database acceptance timestamp for deterministic rebuilds",
    )
    factory_completeness = ingest_taiwan_factory.add_mutually_exclusive_group()
    factory_completeness.add_argument(
        "--complete",
        dest="complete_refresh",
        action="store_true",
        help="close absent prior same-filter assertions (default)",
    )
    factory_completeness.add_argument(
        "--partial",
        dest="complete_refresh",
        action="store_false",
        help="retain omitted prior assertions because this refresh is incomplete",
    )
    ingest_taiwan_factory.set_defaults(
        complete_refresh=True,
        handler=_ingest_taiwan_factory_snapshot,
    )

    ingest_taiwan_mof = subparsers.add_parser(
        "ingest-taiwan-mof-snapshot",
        help=(
            "verify and import one source-bound Taiwan MOF BGMOPEN1 "
            "allowlist-matched active-tax snapshot"
        ),
    )
    ingest_taiwan_mof.add_argument("--database", required=True, type=Path)
    ingest_taiwan_mof.add_argument("--snapshot", required=True, type=Path)
    ingest_taiwan_mof.add_argument(
        "--moenv-snapshot",
        required=True,
        type=Path,
        help="accepted MOENV snapshot bound into the MOF allowlist",
    )
    ingest_taiwan_mof.add_argument(
        "--factory-snapshot",
        required=True,
        type=Path,
        help="accepted registered-factory snapshot bound into the MOF allowlist",
    )
    ingest_taiwan_mof.add_argument(
        "--accepted-at",
        required=True,
        type=_timestamp,
        help="explicit database acceptance timestamp for deterministic rebuilds",
    )
    mof_completeness = ingest_taiwan_mof.add_mutually_exclusive_group()
    mof_completeness.add_argument(
        "--complete",
        dest="complete_refresh",
        action="store_true",
        help="close absent prior same-source assertion intervals (default)",
    )
    mof_completeness.add_argument(
        "--partial",
        dest="complete_refresh",
        action="store_false",
        help="retain omitted prior assertions because this refresh is incomplete",
    )
    ingest_taiwan_mof.set_defaults(
        complete_refresh=True,
        handler=_ingest_taiwan_mof_snapshot,
    )

    project_targets = subparsers.add_parser(
        "accept-project-targets", help="accept an explicitly reviewed source-native project target pair")
    project_targets.add_argument("--database", type=Path, required=True,
                                 help="existing schema-5 working database; no implicit migration")
    project_targets.add_argument("--review", type=Path, required=True)
    project_targets.add_argument("--reference-root", type=Path, required=True)
    project_targets.add_argument("--source-queue", type=Path, required=True)
    project_targets.set_defaults(handler=_accept_project_targets)

    validate = subparsers.add_parser(
        "validate", help="validate integrity and claim lineage"
    )
    validate.add_argument("--database", required=True, type=Path)
    validate.set_defaults(handler=_validate)

    summary_parser = subparsers.add_parser(
        "summary", help="summarize a bitemporal view"
    )
    summary_parser.add_argument("--database", required=True, type=Path)
    summary_parser.add_argument("--as-of", required=True, type=_date)
    summary_parser.add_argument("--recorded-at", required=True, type=_timestamp)
    summary_parser.set_defaults(handler=_summary)

    release = subparsers.add_parser(
        "release",
        help="write a deterministic evidence, map, alert, and forecast bundle",
    )
    release.add_argument("--database", required=True, type=Path)
    release.add_argument("--output", required=True, type=Path)
    release.add_argument("--as-of", required=True, type=_date)
    release.add_argument("--recorded-at", required=True, type=_timestamp)
    release.add_argument("--forecast-start", required=True, type=_date)
    release.set_defaults(handler=_release)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
        _print(result)
        if args.command == "validate" and not result["ok"]:
            return 1
        return 0
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
