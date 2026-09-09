"""One-sided source occurrence bounds must not become finite event intervals."""

from dataclasses import asdict, replace
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas import analytics, database, evidence_dossier, repository, service
from semiconductor_atlas.models import (
    ClaimKind, ClaimVersion, EvidenceLink, MilestoneStatus, MilestoneUpperBoundValue, MilestoneValue,
)
from tests import test_source_claim_precision as fixtures


def upper(**changes):
    return MilestoneUpperBoundValue(**(dict(milestone_type="sample_production_start",
        status=MilestoneStatus.COMPLETED, date_low=None, date_base=None, date_high="2026-07-13",
        date_precision="day", date_literal="Sample production has already commenced") | changes))


class MilestoneUpperBoundTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourceClaimPrecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.connection
        database.apply_migrations(self.c, target_version=10)

    def migrate(self):
        self.c.commit()
        self.assertEqual([11], database.apply_migrations(self.c, target_version=11))
        self.assertEqual(11, database.schema_version(self.c))
        self.assertEqual([], database.apply_migrations(self.c, target_version=11))

    def rows(self):
        return {r[0]: [tuple(x) for x in self.c.execute(f'SELECT * FROM "{r[0]}" ORDER BY rowid')]
                for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def add(self, value=None, identifier="bounded", **changes):
        return self.fixture.claim(identifier, self.fixture.series(identifier), value or upper(), **changes)

    def test_roundtrip_hash_and_source_json_preserve_relation(self):
        self.migrate()
        self.add()
        self.assertEqual(upper(), repository._stored_claim_value(self.c, "bounded", "milestone"))
        expected = asdict(upper()) | {"kind": "milestone", "status": "completed"}
        fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, repository.value_sha256(upper()))
        self.assertEqual(asdict(upper()) | {"status": "completed"}, service.claim_value(self.c, "bounded", "milestone"))
        self.assertEqual([], repository.validate_database(self.c))

    def test_model_rejects_invalid_or_ambiguous_bounds(self):
        for change in ({"date_low": "2026-01-01"}, {"date_base": "2026-07-13"}, {"date_high": None},
                {"date_high": "2026-02-29"}, {"date_high": "2026-7-13"}, {"date_high": "0000-01-01"},
                {"date_precision": None}, {"date_precision": "year"}, {"date_relation": None},
                {"date_relation": "observed_by"}, {"date_literal": " "}, {"milestone_type": ""},
                {"status": "completed"}, *[{"status": s} for s in MilestoneStatus if s is not MilestoneStatus.COMPLETED]):
            with self.subTest(change=change), self.assertRaises(ValueError):
                upper(**change)

    def test_legacy_asdict_and_value_hash_unchanged(self):
        value = MilestoneValue("opening", MilestoneStatus.COMPLETED, "2020-01-01", None,
                               "2020-12-31", "year", "2020")
        payload = dict(milestone_type="opening", status="completed", date_low="2020-01-01",
                       date_base=None, date_high="2020-12-31", date_precision="year", date_literal="2020")
        self.assertEqual(payload, asdict(value))
        digest = hashlib.sha256(json.dumps(payload | {"kind": "milestone"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, repository.value_sha256(value))
        self.migrate()
        self.add(value)
        self.assertEqual(payload, service.claim_value(self.c, "bounded", "milestone"))

    def test_schema10_rejects_atomically(self):
        series = self.fixture.series("bound")
        before = self.rows()
        with self.assertRaises(sqlite3.OperationalError):
            self.fixture.claim("bound", series, upper())
        self.assertEqual(before, self.rows())

    def test_populated_migration_preserves_rows_old_fields_hashes_and_foreign_keys(self):
        self.add(MilestoneValue("opening", MilestoneStatus.COMPLETED, "2020-01-01", None,
                               "2020-12-31", "year", "2020"))
        self.c.commit()
        before = self.rows()
        self.migrate()
        after = self.rows()
        self.assertEqual(before["schema_migrations"], after.pop("schema_migrations")[:-1])
        before.pop("schema_migrations")
        self.assertTrue(all(r[-1] is None for r in after["milestone_values"]))
        after["milestone_values"] = [r[:-1] for r in after["milestone_values"]]
        self.assertEqual(before, after)
        self.assertEqual(1, self.c.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual([], repository.validate_database(self.c))

    def test_migration_restores_disabled_foreign_keys(self):
        self.c.execute("PRAGMA foreign_keys=OFF")
        self.migrate()
        self.assertEqual(0, self.c.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_migration_clock_is_explicit_exact_and_replayable(self):
        raw = self.c.serialize()
        images = []
        for _ in range(2):
            c = sqlite3.connect(':memory:')
            try:
                c.deserialize(raw)
                c.row_factory = sqlite3.Row
                database.apply_migrations(c, target_version=11, applied_at='2026-09-09T02:30:00.123456-07:00')
                self.assertEqual(c.execute('SELECT applied_at FROM schema_migrations WHERE version=11').fetchone()[0],
                                 '2026-09-09T02:30:00.123456-07:00')
                images.append(c.serialize())
            finally:
                c.close()
        self.assertEqual(images[0], images[1])

    def test_invalid_migration_clock_rejects_without_writes(self):
        before = self.c.serialize()
        for invalid in ('', '2026-09-09', '2026-09-09T00:00:00', 'bad', True, 3, '2026-02-29T00:00:00Z'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'aware timestamp'):
                database.apply_migrations(self.c, target_version=11, applied_at=invalid)
            self.assertEqual(before, self.c.serialize())

    def test_failed_preflight_rolls_back_schema_rows_and_ledger(self):
        self.add(MilestoneValue('opening', MilestoneStatus.COMPLETED, '2020-01-01', None,
                               '2020-12-31', 'year', '2020'))
        trigger = self.c.execute("SELECT sql FROM sqlite_master WHERE name='milestone_values_immutable_update'").fetchone()[0]
        self.c.execute('DROP TRIGGER milestone_values_immutable_update')
        self.c.execute("UPDATE milestone_values SET date_high='not a date'")
        self.c.execute(trigger)
        self.c.commit()
        before = self.c.serialize()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'migration 0011 milestone upper bound validation failed'):
            database.apply_migrations(self.c, target_version=11)
        self.assertEqual(before, self.c.serialize())
        self.assertFalse(self.c.in_transaction)
        self.assertEqual(10, database.schema_version(self.c))
        self.assertEqual(1, self.c.execute('PRAGMA foreign_keys').fetchone()[0])

    def test_release_explains_superseded_upper_bound_in_history(self):
        from semiconductor_atlas.release import write_release
        self.migrate()
        self.add()
        self.fixture.claim('correction', 'bounded', MilestoneValue('sample_production_start',
            MilestoneStatus.COMPLETED, '2026-07-01', None, '2026-07-13', 'range', 'July 1 to July 13'),
            recorded_at=fixtures.LATER)
        self.c.commit()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'release'
            write_release(self.c, output, as_of='2026-09-08', recorded_at=fixtures.LATER)
            self.assertNotIn('on_or_before', (output / 'source_claims.jsonl').read_text())
            self.assertIn('on_or_before', (output / 'claim_history.jsonl').read_text())
            self.assertIn('lower bound and exact date', (output / 'README.md').read_text())

    def test_source_semantics_enforced_before_writes(self):
        self.migrate()
        series = self.fixture.series("bound")
        base = ClaimVersion("bound", series, None, fixtures.ADMITTED, ClaimKind.SOURCE_STATEMENT,
                            "reviewed_source", None, created_by_run_id="run")
        for change in ({"valid_from": "2026-07-13"}, {"confidence": .9},
                       {"claim_kind": ClaimKind.DIRECT_OBSERVATION, "valid_from": "2026-07-13"}):
            before = self.rows()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "milestone upper bounds require"):
                repository.insert_claim(self.c, replace(base, **change), upper(),
                    evidence=[EvidenceLink("document", source_record_id="record")])
            self.assertEqual(before, self.rows())

    def test_raw_sql_rejects_bad_shape_and_non_source_semantics(self):
        self.migrate()
        series = self.fixture.series("raw")
        cases = [(change, {}) for change in (
            {"date_low": "2026-01-01"}, {"date_base": "2026-07-13"}, {"date_high": None},
            {"date_high": "2026-02-29"}, {"date_high": "2026-13-01"}, {"date_high": "not a date"},
            {"date_high": "0000-01-01"}, {"date_high": "2026-7-13"}, {"date_high": "2026-07-13T00:00:00Z"},
            {"status": "expected"}, {"date_relation": None}, {"date_relation": "observed_by"},
            {"date_precision": None}, {"date_precision": "year"}, {"date_literal": None}, {"date_literal": " "},
            *[{"date_literal": s} for s in ("\t", "\n", "\r\n", "\u00a0", "\u2009", "\u3000")],
            {"date_relation": None, "date_precision": None, "date_literal": None},
            {"date_relation": None, "date_precision": "year", "date_literal": "2026"})]
        cases += [({}, change) for change in ({"confidence": .9}, {"valid_from": "2026-07-13"},
            {"valid_from": "2026-07-13", "valid_to": "2026-07-14"},
            {"claim_kind": "direct_observation", "valid_from": "2026-07-13"})]
        for change, parent in cases:
            self.c.execute("SAVEPOINT raw_test")
            try:
                v = {"claim_kind": "source_statement", "valid_from": None, "valid_to": None, "confidence": None} | parent
                self.c.execute("""INSERT INTO claim_versions
                    (id,series_id,value_kind,value_sha256,recorded_at,method,claim_kind,valid_from,valid_to,confidence)
                    VALUES ('raw',?,'milestone',?,?,'raw test',?,?,?,?)""",
                    (series, "0" * 64, fixtures.ADMITTED, *v.values()))
                self.c.execute("INSERT INTO claim_values VALUES ('raw','milestone')")
                value = asdict(upper()) | {"status": "completed"} | change
                with self.subTest(change=change, parent=parent), self.assertRaises(sqlite3.IntegrityError):
                    self.c.execute("INSERT INTO milestone_values (claim_version_id," + ",".join(value)
                                   + ") VALUES ('raw'," + ",".join("?" for _ in value) + ")", tuple(value.values()))
            finally:
                self.c.execute("ROLLBACK TO raw_test")
                self.c.execute("RELEASE raw_test")

    def test_validator_detects_bypassed_parent_guard(self):
        self.migrate()
        self.add()
        self.c.execute("DROP TRIGGER claim_versions_content_immutable")
        self.c.execute("UPDATE claim_versions SET confidence=.9 WHERE id='bounded'")
        self.assertTrue(any("milestone upper bound requires" in e for e in repository.validate_database(self.c)))

    def test_knowledge_cutoff_and_physical_exclusion(self):
        self.migrate()
        self.add(recorded_at="2026-09-07T10:00:00.123456Z")
        self.assertEqual([], repository.known_source_claims(self.c, recorded_at="2026-09-07T10:00:00.123455Z"))
        self.assertEqual(1, len(repository.known_source_claims(self.c, recorded_at="2026-09-07T10:00:00.123456Z")))
        for day in ("2020-01-01", "2026-07-13", "2030-01-01"):
            self.assertEqual([], repository.current_claims(self.c, as_of=day, recorded_at=fixtures.LATER))

    def test_bound_is_not_a_ramp_or_finite_period(self):
        from semiconductor_atlas.project_target_changes import _period
        self.migrate()
        self.add(upper(milestone_type="production_start"))
        claims = service.source_claim_records(self.c, recorded_at=fixtures.LATER)
        self.assertEqual(({}, ()), analytics._production_milestones(claims))
        with self.assertRaises(ValueError):
            _period(claims[0]["value"])

    def test_prose_cannot_render_null_as_finite_lower_bound(self):
        value = asdict(upper()) | {"status": "completed"}
        summary = evidence_dossier._value_summary({"value_kind": "milestone", "value": value})
        self.assertIn("occurred on or before 2026-07-13", summary)
        self.assertIn("lower bound and exact date unknown", summary)
        self.assertNotIn("None to", summary)

    def test_immutable(self):
        self.migrate()
        self.add()
        for sql in ("DELETE FROM milestone_values", "UPDATE milestone_values SET date_high='2026-07-14'",
                    "UPDATE claim_versions SET valid_from='2026-07-13'"):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                self.c.execute(sql)


if __name__ == "__main__":
    unittest.main()
