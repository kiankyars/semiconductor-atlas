from __future__ import annotations

import copy
import hashlib
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas import eea_scope_history as history
from semiconductor_atlas import eea_scope_revisions as revisions
from semiconductor_atlas.database import initialize
from semiconductor_atlas.repository import validate_database
from tests import test_eea_scope_revisions as fixtures


def _replace_exact(value, old, new):
    if isinstance(value, dict):
        return {key: _replace_exact(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_exact(item, old, new) for item in value]
    return new if value == old else value


class EEAScopeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EEAScopeRevisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.root = self.fixture.fixture.root
        self.queue = self.fixture.fixture.queue
        self.parent = self.fixture.parent
        self.cutoff = self.fixture.genesis.accepted_at

    def _export(self):
        return history.export_history(
            self.root / "atlas.sqlite", parent_database=self.parent,
            candidate_queue=self.queue, recorded_at=self.cutoff,
        )

    def _packet_file(self, packet, name="history.json"):
        path = self.root / name
        path.write_bytes(revisions._raw(packet))
        return path

    @contextmanager
    def _deny_writes(self, connection=None):
        connection = self.db if connection is None else connection
        before = "\n".join(connection.iterdump())
        changes = connection.total_changes
        connection.set_authorizer(
            lambda action, *_: sqlite3.SQLITE_DENY if action in (
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
            ) else sqlite3.SQLITE_OK
        )
        try:
            yield
        finally:
            connection.set_authorizer(None)
            self.assertEqual(changes, connection.total_changes)
            self.assertEqual(before, "\n".join(connection.iterdump()))

    def _corrupt_scalar(self):
        # Deliberately corrupt only the disposable synthetic fixture, not producers.
        for (name,) in self.db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='scalar_values'"
        ).fetchall():
            self.db.execute(f'DROP TRIGGER "{name}"')
        claim_id = self.db.execute(
            "SELECT claim_version_id FROM scalar_values LIMIT 1"
        ).fetchone()[0]
        self.db.execute(
            "UPDATE scalar_values SET text_value=? WHERE claim_version_id=?",
            ("CORRUPTED NOT SOURCE TEXT", claim_id),
        )
        self.db.commit()
        self.assertTrue(any("typed value does not match" in issue
                            for issue in validate_database(self.db)))

    def test_history_verification_rejects_corrupted_typed_scalar_without_repair(self):
        self._corrupt_scalar()
        with self._deny_writes(), self.assertRaises(ValueError):
            revisions.validate_history(self.db, candidate_queue=self.queue)

    def test_scoped_report_never_emits_corrupted_typed_scalar(self):
        self._corrupt_scalar()
        with self._deny_writes(), self.assertRaises(ValueError):
            self.fixture.report(self.cutoff)

    def test_exact_revision_replay_rejects_corrupted_typed_scalar_without_repair(self):
        review = self.fixture.review(1, {})
        admitted = self.fixture.accept(review, self.fixture.genesis.ingestion_run_id, 1)
        self._corrupt_scalar()
        with self._deny_writes(), self.assertRaises(ValueError):
            self.fixture.accept(
                review, self.fixture.genesis.ingestion_run_id, 2,
                accepted_at=admitted["accepted_at"],
            )

    def test_restore_rejects_coherently_forged_source_metadata(self):
        original = self._export()
        source_before = hashlib.sha256((self.root / "atlas.sqlite").read_bytes()).hexdigest()
        parent_before = hashlib.sha256(self.parent.read_bytes()).hexdigest()
        cases = (
            ("source_documents", "document_url", "https://example.org/forged-official-source"),
            ("source_documents", "license", "CC0 fabricated rights"),
            ("sources", "publisher", "Forged Publisher"),
            ("sources", "stable_key", "forged:source"),
            ("source_families", "stable_key", "forged-family"),
        )
        for table, column, forged in cases:
            indexes = range(len(original["tables"][table]["rows"]))
            for row_index in indexes:
                with self.subTest(table=table, column=column, row=row_index):
                    packet = copy.deepcopy(original)
                    entry = packet["tables"][table]
                    index = entry["columns"].index(column)
                    old = entry["rows"][row_index][index]
                    entry["rows"][row_index][index] = forged
                    if table == "source_documents":
                        document_id = entry["rows"][row_index][entry["columns"].index("id")]
                        for statement in packet["scope_report"]["source_statements"]:
                            for evidence in statement["evidence"]:
                                if evidence["source_document_id"] == document_id:
                                    self.assertEqual(old, evidence[column])
                                    evidence[column] = forged
                    else:
                        packet["scope_report"] = _replace_exact(packet["scope_report"], old, forged)
                    packet["tables_sha256"] = revisions._hash(packet["tables"])
                    name = f"{table}-{column}-{row_index}"
                    packet_path = self._packet_file(packet, name + ".json")
                    output = self.root / (name + ".sqlite")
                    with self.assertRaises(ValueError):
                        history.restore_history(
                            self.parent, history_file=packet_path, output_database=output,
                        )
                    self.assertFalse(output.exists())
        self.assertEqual(source_before, hashlib.sha256((self.root / "atlas.sqlite").read_bytes()).hexdigest())
        self.assertEqual(parent_before, hashlib.sha256(self.parent.read_bytes()).hexdigest())

    def test_future_supersession_is_rejected_instead_of_leaking_into_old_cutoff(self):
        original = self._export()
        claim_id = self.db.execute("SELECT id FROM claim_versions LIMIT 1").fetchone()[0]
        # This update is allowed by the intact core schema but not by this route.
        self.db.execute("UPDATE claim_versions SET superseded_at=? WHERE id=?",
                        ("2099-01-01T00:00:00Z", claim_id))
        self.db.commit()
        self.assertEqual([], validate_database(self.db))
        with self._deny_writes():
            with self.assertRaises(ValueError):
                revisions.validate_history(self.db, candidate_queue=self.queue)
            with self.assertRaises(ValueError):
                self.fixture.report(self.cutoff)
            with self.assertRaises(ValueError):
                self._export()
        self.assertTrue(all(row[original["tables"]["claim_versions"]["columns"].index("superseded_at")] is None
                            for row in original["tables"]["claim_versions"]["rows"]))

    def test_restore_binds_consumed_packet_buffer_to_checked_file_hash(self):
        original = self._export()
        packet_path = self._packet_file(original)
        alternate = copy.deepcopy(original)
        alternate["recorded_at"] = "2026-07-20T19:00:01Z"
        alternate["scope_report"]["recorded_at"] = alternate["recorded_at"]
        alternate_raw = revisions._raw(alternate)
        actual_read = Path.read_bytes
        file_sha = hashlib.sha256(actual_read(packet_path)).hexdigest()
        injected = []

        def different_buffer(path):
            if path == packet_path:
                injected.append(path)
                return alternate_raw
            return actual_read(path)

        output = self.root / "different-buffer.sqlite"
        with patch.object(Path, "read_bytes", different_buffer), self.assertRaises(ValueError):
            history.restore_history(self.parent, history_file=packet_path, output_database=output)
        self.assertTrue(injected)
        self.assertFalse(output.exists())
        self.assertEqual(file_sha, hashlib.sha256(actual_read(packet_path)).hexdigest())

    def _assert_directory_swap_rejected(self, mode, *, after_link=False):
        packet = self._packet_file(self._export())
        publish = self.root / "publish"
        publish.mkdir()
        moved = self.root / "renamed-publish"
        suffix = "sqlite" if mode == "restore" else "json"
        output = publish / ("result." + suffix)
        parent_before = hashlib.sha256(self.parent.read_bytes()).hexdigest()
        original_temporary_directory = history.tempfile.TemporaryDirectory
        original_link = history.os.link
        swapped = []

        def swap():
            publish.rename(moved)
            publish.mkdir()
            swapped.append(True)

        def replace_parent(*args, **kwargs):
            if not after_link and kwargs.get("prefix") == ".eea-" + mode + "-":
                swap()
            return original_temporary_directory(*args, **kwargs)

        def link_then_replace(*args, **kwargs):
            result = original_link(*args, **kwargs)
            if after_link:
                swap()
            return result

        with patch.object(history.tempfile, "TemporaryDirectory", side_effect=replace_parent), \
                patch.object(history.os, "link", side_effect=link_then_replace):
            with self.assertRaises((ValueError, OSError)):
                if mode == "restore":
                    history.restore_history(self.parent, history_file=packet, output_database=output)
                else:
                    history.write_history(
                        self.root / "atlas.sqlite", parent_database=self.parent,
                        candidate_queue=self.queue, recorded_at=self.cutoff, output_file=output,
                    )
        self.assertEqual([True], swapped)
        self.assertFalse(output.exists())
        self.assertFalse((moved / output.name).exists())
        self.assertEqual(parent_before, hashlib.sha256(self.parent.read_bytes()).hexdigest())

    def test_restore_directory_swap_publishes_neither_destination(self):
        self._assert_directory_swap_rejected("restore")

    def test_json_export_directory_swap_publishes_neither_destination(self):
        self._assert_directory_swap_rejected("export")

    def test_restore_postlink_directory_swap_removes_only_its_publication(self):
        self._assert_directory_swap_rejected("restore", after_link=True)

    def test_json_export_postlink_directory_swap_removes_only_its_publication(self):
        self._assert_directory_swap_rejected("export", after_link=True)

    def test_existing_restore_and_export_outputs_are_never_overwritten(self):
        packet = self._packet_file(self._export())
        sentinel = b"Existing user data must remain byte-identical.\n"
        for mode in ("restore", "export"):
            with self.subTest(mode=mode):
                output = self.root / ("existing-" + mode)
                output.write_bytes(sentinel)
                with self.assertRaises(ValueError):
                    if mode == "restore":
                        history.restore_history(self.parent, history_file=packet, output_database=output)
                    else:
                        history.write_history(
                            self.root / "atlas.sqlite", parent_database=self.parent,
                            candidate_queue=self.queue, recorded_at=self.cutoff, output_file=output,
                        )
                self.assertEqual(sentinel, output.read_bytes())

    def test_legitimate_v1_genesis_preserves_source_metadata_and_historical_claims(self):
        legacy, _ = initialize(self.root / "legacy.sqlite")
        self.addCleanup(legacy.close)
        original = self.fixture.fixture.connection
        self.fixture.fixture.connection = legacy
        try:
            self.fixture.fixture._seed_legacy_v1()
        finally:
            self.fixture.fixture.connection = original
        with self._deny_writes(legacy):
            revisions.validate_history(legacy, candidate_queue=self.queue)
            report = revisions.scope_records(legacy, candidate_queue=self.queue, recorded_at=self.cutoff)
        self.assertEqual(9, len(report["source_statements"]))
        self.assertEqual({("2026-02-20", 1.0)},
                         {(row["valid_from"], row["confidence"]) for row in report["source_statements"]})

    def test_legitimate_preexisting_source_creation_clocks_remain_unchanged(self):
        prior, _ = initialize(self.root / "preexisting-source.sqlite")
        self.addCleanup(prior.close)
        earlier = "2026-07-20T17:00:00Z"
        for table in ("source_families", "sources"):
            row = dict(self.db.execute(f'SELECT * FROM "{table}"').fetchone())
            row["created_at"] = earlier
            names = ",".join(row)
            markers = ",".join("?" for _ in row)
            prior.execute(f'INSERT INTO "{table}" ({names}) VALUES ({markers})', tuple(row.values()))
        prior.commit()
        original = self.fixture.fixture.connection
        self.fixture.fixture.connection = prior
        try:
            self.fixture.fixture._import()
        finally:
            self.fixture.fixture.connection = original
        with self._deny_writes(prior):
            revisions.validate_history(prior, candidate_queue=self.queue)
            report = revisions.scope_records(prior, candidate_queue=self.queue, recorded_at=self.cutoff)
        self.assertEqual(9, len(report["source_statements"]))
        for table in ("source_families", "sources"):
            self.assertEqual(earlier, prior.execute(f'SELECT created_at FROM "{table}"').fetchone()[0])


if __name__ == "__main__":
    unittest.main()
