"""New-only, exact-parent admission and replay of a provenance-only family review."""

from __future__ import annotations

import sqlite3
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts import frozen_runtime as runtime
from . import database, repository, source_family_review as review_api


FORMAT = 'semiconductor-atlas-source-family-admission-v1'


def now():
    return datetime.now(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def codes():
    root = Path(__file__).parent.parent
    paths = sorted([*Path(__file__).parent.rglob('*.py'), *Path(__file__).parent.rglob('*.sql'),
                    root / 'scripts/admit_source_family.py', root / 'scripts/frozen_runtime.py'])
    return {p.relative_to(root).as_posix(): runtime.digest(runtime.read(p)) for p in paths}


def open_image(raw):
    connection = sqlite3.connect(':memory:')
    try:
        connection.deserialize(raw)
        connection.row_factory = sqlite3.Row
        database.register_clock_functions(connection)
        connection.execute('PRAGMA foreign_keys=ON')
        if database.schema_version(connection) not in (11, 12):
            raise ValueError('family correction requires an exact schema11 or schema12 parent')
        return connection
    except BaseException:
        connection.close()
        raise


def populate(connection, raw, *, review_sha256, recorded_at):
    database.apply_migrations(connection, target_version=12, applied_at=recorded_at)
    identifier = review_api.insert_review(connection, raw, expected_sha256=review_sha256,
                                         recorded_at=recorded_at)
    errors = repository.validate_database(connection)
    if errors:
        raise ValueError('source family successor validation failed: ' + str(errors[:3]))
    connection.commit()
    return identifier


def retained_evidence(connection, review, reference_root):
    """Verify exact retained bodies, permitting identical bytes shared by duplicate documents."""
    payloads, subject_hashes, unlocated = {}, set(), []
    for doc in review['evidence_documents']:
        row = connection.execute('SELECT metadata_json FROM source_documents WHERE id=?', (doc['id'],)).fetchone()
        if row is None:
            raise ValueError('family review document is not in the pinned parent')
        retained = json.loads(row[0]).get('retained_body')
        if retained is None:
            if doc['source_id'] == review['source']['id']:
                raise ValueError('subject family evidence requires its own retained path')
            unlocated.append(doc['content_sha256'])
            continue
        if isinstance(retained, dict):
            if retained.get('sha256') != doc['content_sha256']:
                raise ValueError('retained family evidence hash metadata differs')
            retained = retained.get('path')
        if (not isinstance(retained, str) or not retained or PurePosixPath(retained).is_absolute()
                or any(p in ('', '.', '..') for p in retained.split('/'))):
            raise ValueError('family evidence requires a safe retained relative path')
        body = runtime.read(Path(reference_root) / retained)
        if runtime.digest(body) != doc['content_sha256']:
            raise ValueError('retained family evidence body hash differs')
        payloads[retained] = body
        if doc['source_id'] == review['source']['id']:
            subject_hashes.add(doc['content_sha256'])
    hashes = {runtime.digest(body) for body in payloads.values()}
    if any(doc['content_sha256'] not in hashes for doc in review['evidence_documents']):
        raise ValueError('every family review document requires verified retained bytes')
    if any(digest not in subject_hashes for digest in unlocated):
        raise ValueError('unlocated context evidence must match verified subject bytes')
    return payloads


def admit(*, parent, expected_parent_sha256, review, expected_review_sha256, reference_root, output,
          verify_only=False, expected_output_sha256=None):
    started, inventory = now(), codes()
    if inventory != _LOADED_CODES:
        raise ValueError('source family implementation changed since loading')
    for value in (expected_parent_sha256, expected_review_sha256):
        runtime._hex(value, 64)
    if verify_only:
        runtime._hex(expected_output_sha256, 64)
    elif expected_output_sha256 is not None:
        raise ValueError('expected output hash is only an input for verification')
    parent_raw = runtime.read(Path(parent), limit=1_000_000_000)
    raw = runtime.read(Path(review))
    if runtime.digest(parent_raw) != expected_parent_sha256:
        raise ValueError('trusted family correction parent hash differs')
    reviewed = review_api.parse_review(raw, expected_review_sha256)
    target = Path(output)
    if not verify_only and (target.exists() or target.is_symlink()):
        raise ValueError('family correction output must not exist')
    connection = open_image(parent_raw)
    try:
        payloads = retained_evidence(connection, reviewed, reference_root)
        if verify_only:
            successor = runtime.read(target, limit=1_000_000_000)
            if runtime.digest(successor) != expected_output_sha256:
                raise ValueError('trusted family correction successor hash differs')
            actual = open_image(successor)
            try:
                identifier = repository.stable_id(review_api.FORMAT, expected_review_sha256)
                row = actual.execute('SELECT recorded_at FROM source_family_assignments WHERE id=?',
                                     (identifier,)).fetchone()
                if row is None or review_api._clock(row[0]) > review_api._clock(started):
                    raise ValueError('missing or future family correction admission')
                recorded_at = row[0]
            finally:
                actual.close()
        else:
            recorded_at = now()
            if review_api._clock(recorded_at) <= review_api._clock(started):
                raise ValueError('actual family admission clock must advance after start')
        if review_api._clock(reviewed['reviewed_at']) > review_api._clock(started):
            raise ValueError('family review must precede actual admission start')
        identifier = populate(connection, raw, review_sha256=expected_review_sha256, recorded_at=recorded_at)
        generated = connection.serialize()
        if verify_only and generated != successor:
            raise ValueError('family successor differs from exact parent-plus-assignment reconstruction')
        if (runtime.read(Path(parent), limit=1_000_000_000) != parent_raw
                or runtime.read(Path(review)) != raw or codes() != inventory
                or retained_evidence(connection, reviewed, reference_root) != payloads):
            raise ValueError('family correction inputs or producer changed during execution')
        if verify_only and runtime.read(target, limit=1_000_000_000) != successor:
            raise ValueError('family correction successor changed during verification')
        if not verify_only:
            runtime.write_new(target, generated)
        return {'format': FORMAT, 'assignment_id': identifier, 'recorded_at': recorded_at,
            'parent_sha256': expected_parent_sha256, 'review_sha256': expected_review_sha256,
            'database_sha256': runtime.digest(generated), 'database_bytes': len(generated),
            'schema_version': 12, 'verified': verify_only, 'code_sha256': inventory,
            'retained_evidence': {path: {'sha256': runtime.digest(body), 'bytes': len(body)}
                                  for path, body in sorted(payloads.items())},
            'interpreter': runtime.interpreter(), 'boundaries': review_api.BOUNDARIES}
    finally:
        connection.close()


_LOADED_CODES = codes()
