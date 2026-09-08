"""Schema-6 completed source periods, distinct from physical-world verification."""

from dataclasses import replace
import hashlib
import json
import sqlite3
import unittest

from semiconductor_atlas.database import apply_migrations, schema_version
from semiconductor_atlas.models import (
    CapabilityValue, CapacityBasis, CapacityValue, ConstraintSeverity, ConstraintStatus,
    ConstraintValue, DependencyLink, GeometryValue, MilestoneStatus, MilestoneValue,
    RelationshipValue, ResourceValue, ScalarType, ScalarValue, ValueKind,
)
from semiconductor_atlas.repository import (
    _stored_claim_value, current_claims, known_source_claims, validate_database, value_sha256,
)
from tests import test_source_claim_precision as fixtures


ADMITTED, LATER = fixtures.ADMITTED, fixtures.LATER


class CompletedMilestonePrecisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourceClaimPrecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.migrate()
        self.connection = self.fixture.connection

    @staticmethod
    def completed(**changes):
        value = MilestoneValue("commercial_production_commencement", MilestoneStatus.COMPLETED,
                               "2024-01-01", None, "2024-12-31", "year", "2024")
        return replace(value, **changes)

    def migrate(self):
        self.connection.commit()
        self.assertEqual([6], apply_migrations(self.connection, target_version=6))
        self.assertEqual(6, schema_version(self.connection))
        self.assertEqual([], apply_migrations(self.connection, target_version=6))

    def rows(self):
        tables = [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations' ORDER BY name")]
        return {name: [tuple(row) for row in self.connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in tables}

    def test_completed_year_round_trips_with_no_midpoint_or_confidence(self):
        self.migrate()
        self.fixture.claim("completed", self.fixture.series("commencement"), self.completed())
        row = self.connection.execute("SELECT * FROM milestone_values WHERE claim_version_id='completed'").fetchone()
        self.assertEqual(("completed", "2024-01-01", None, "2024-12-31", "year", "2024"),
                         tuple(row[key] for key in ("status", "date_low", "date_base", "date_high", "date_precision", "date_literal")))
        self.assertEqual(self.completed(), _stored_claim_value(self.connection, "completed", "milestone"))
        known = known_source_claims(self.connection, recorded_at=ADMITTED)
        self.assertEqual(["completed"], [row["id"] for row in known])
        self.assertIsNone(known[0]["valid_from"])
        self.assertIsNone(known[0]["confidence"])
        self.assertEqual("source_statement", known[0]["claim_kind"])
        self.assertEqual([], validate_database(self.connection))

    def test_completed_source_report_does_not_become_a_physical_world_claim(self):
        self.migrate()
        self.fixture.claim("completed", self.fixture.series("commencement"), self.completed())
        for day in ("2024-01-01", "2024-12-31", "2026-09-07", "2030-01-01"):
            with self.subTest(day=day):
                self.assertEqual([], current_claims(self.connection, as_of=day, recorded_at=LATER))
        self.assertEqual([], known_source_claims(self.connection, recorded_at="2026-09-07T09:59:59.999999Z"))

    def test_populated_migration_preserves_every_row_trigger_and_existing_hash(self):
        self.fixture.claim("expected", self.fixture.series("expected"), self.fixture.period())
        self.fixture.claim("legacy", self.fixture.series("legacy"), MilestoneValue(
            "opening", MilestoneStatus.COMPLETED, "2020-01-01", "2020-01-02", "2020-01-03"))
        self.fixture.claim("scalar", self.fixture.series("scalar", ValueKind.SCALAR), ScalarValue(ScalarType.TEXT, "old report"))
        before = self.rows()
        triggers = dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        self.migrate()
        self.assertEqual(before, self.rows())
        self.assertEqual(triggers, dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")))
        self.assertEqual([], validate_database(self.connection))
        self.assertEqual([], self.connection.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_old_schema_refuses_new_completed_period_without_partial_claim_writes(self):
        series = self.fixture.series("commencement")
        before = self.rows()
        with self.assertRaises(sqlite3.IntegrityError):
            self.fixture.claim("completed", series, self.completed())
        self.assertEqual(before, self.rows())
        self.assertEqual(5, schema_version(self.connection))

    def test_migration_preserves_all_value_kinds_and_both_foreign_key_settings(self):
        values = (
            ScalarValue(ScalarType.TEXT, "Project 2"),
            GeometryValue({"type": "Point", "coordinates": [0, 0]}),
            RelationshipValue("project", "fixture_relation"),
            MilestoneValue("production", MilestoneStatus.EXPECTED,
                           "2027-01-01", "2027-04-01", "2027-07-01"),
            CapabilityValue("process", "3nm"),
            CapacityValue("output", CapacityBasis.ANNOUNCED, "units/month", 1, 2, 3),
            ResourceValue("water", "m3/day", 1, 2, 3),
            ConstraintValue("power", ConstraintStatus.POTENTIAL, ConstraintSeverity.UNKNOWN, "Fixture"),
        )
        self.assertEqual(set(ValueKind), {value.kind for value in values})
        for foreign_keys in (0, 1):
            with self.subTest(foreign_keys=foreign_keys):
                fixture = fixtures.SourceClaimPrecisionTests()
                fixture.setUp()
                try:
                    fixture.migrate()
                    connection = fixture.connection
                    for value in values:
                        fixture.claim(value.kind.value, fixture.series(value.kind.value, value.kind), value,
                                      valid_from="2026-09-07", confidence=0.5,
                                      dependencies=() if value.kind is ValueKind.SCALAR else (DependencyLink("scalar"),))
                    connection.commit()
                    connection.execute(f"PRAGMA foreign_keys={foreign_keys}")
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations' ORDER BY name")]
                    before = {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                              for name in tables}
                    triggers = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
                    self.assertEqual([6], apply_migrations(connection, target_version=6))
                    self.assertEqual(6, schema_version(connection))
                    after = {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                             for name in tables}
                    self.assertEqual(before, after)
                    self.assertEqual(triggers, dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")))
                    self.assertEqual(foreign_keys, connection.execute("PRAGMA foreign_keys").fetchone()[0])
                    self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
                    self.assertEqual([], validate_database(connection))
                finally:
                    fixture.doCleanups()

    def test_completed_period_corrections_preserve_actual_knowledge_time(self):
        self.migrate()
        series = self.fixture.series("commencement")
        self.fixture.claim("first", series, self.completed(), recorded_at="2026-09-07T10:00:00.123456Z")
        revised = self.completed(date_low="2025-01-01", date_high="2025-12-31", date_literal="2025")
        self.fixture.claim("revised", series, revised, recorded_at="2026-09-07T10:00:01.123456Z")
        prior = known_source_claims(self.connection, recorded_at="2026-09-07T10:00:01.123455Z")
        self.assertEqual(["first"], [row["id"] for row in prior])
        self.assertIsNone(prior[0]["superseded_at"])
        current = known_source_claims(self.connection, recorded_at="2026-09-07T10:00:01.123456Z")
        self.assertEqual(["revised"], [row["id"] for row in current])
        self.assertEqual([], validate_database(self.connection))

    def test_all_calendar_precisions_accept_completed_and_preserve_literal(self):
        for precision, low, high in (("day", "2024-02-29", "2024-02-29"),
                                    ("month", "2024-02-01", "2024-02-29"),
                                    ("quarter", "2024-10-01", "2024-12-31"),
                                    ("half_year", "2024-07-01", "2024-12-31"),
                                    ("year", "2024-01-01", "2024-12-31"),
                                    ("range", "2023-02-01", "2024-07-19")):
            with self.subTest(precision=precision):
                value = self.completed(date_low=low, date_high=high, date_precision=precision, date_literal="source wording")
                self.assertIsNone(value.date_base)
                self.assertEqual("source wording", value.date_literal)

    def test_bad_calendar_periods_midpoints_and_other_statuses_still_reject(self):
        cases = [{"date_low": "2024-01-02"}, {"date_high": "2024-12-30"}, {"date_base": "2024-07-01"},
                 {"date_precision": "fiscal_year"}, {"date_low": "2025-02-29"}, {"date_literal": " "},
                 {"date_high": "2023-12-31"}, {"status": "completed"},
                 *[{"status": status} for status in (MilestoneStatus.STARTED, MilestoneStatus.DELAYED, MilestoneStatus.CANCELLED)]]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError): self.completed(**change)

    def test_status_precision_literal_and_event_are_hash_bound_without_changing_legacy_hashes(self):
        value = self.completed()
        self.assertNotEqual(value_sha256(value), value_sha256(replace(value, status=MilestoneStatus.EXPECTED)))
        self.assertNotEqual(value_sha256(value), value_sha256(replace(value, date_literal="in 2024")))
        self.assertNotEqual(value_sha256(value), value_sha256(replace(value, milestone_type="high_volume_production")))
        legacy = MilestoneValue("opening", MilestoneStatus.COMPLETED, "2024-01-01", "2024-07-01", "2024-12-31")
        payload = {"kind": "milestone", "milestone_type": "opening", "status": "completed",
                   "date_low": "2024-01-01", "date_base": "2024-07-01", "date_high": "2024-12-31"}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(expected, value_sha256(legacy))

    def test_completed_values_and_parent_lineage_remain_immutable(self):
        self.migrate()
        self.fixture.claim("completed", self.fixture.series("commencement"), self.completed())
        for sql in ("UPDATE milestone_values SET status='expected' WHERE claim_version_id='completed'",
                    "UPDATE milestone_values SET date_literal='2025' WHERE claim_version_id='completed'",
                    "DELETE FROM milestone_values WHERE claim_version_id='completed'",
                    "UPDATE claim_versions SET confidence=1 WHERE id='completed'",
                    "DELETE FROM claim_versions WHERE id='completed'"):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                self.connection.execute(sql)

    def test_sql_keeps_completed_period_midpoint_and_status_shape_constraints(self):
        self.migrate()
        series = self.fixture.series("commencement")
        for changes in ({"status": "started"}, {"date_base": "2024-07-01"},
                        {"date_precision": None}, {"date_literal": None}, {"date_high": "2023-12-31"}):
            values = dict(milestone_type="commercial_production_commencement", status="completed",
                          date_low="2024-01-01", date_base=None, date_high="2024-12-31",
                          date_precision="year", date_literal="2024") | changes
            self.connection.execute("SAVEPOINT shape_test")
            try:
                self.connection.execute("""INSERT INTO claim_versions
                    (id,series_id,value_kind,value_sha256,recorded_at,claim_kind,method)
                    VALUES ('raw',?,'milestone',?,?,'source_statement','shape test')""", (series, "0" * 64, ADMITTED))
                self.connection.execute("INSERT INTO claim_values VALUES ('raw','milestone')")
                with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute("""INSERT INTO milestone_values
                        (claim_version_id,milestone_type,status,date_low,date_base,date_high,date_precision,date_literal)
                        VALUES ('raw',?,?,?,?,?,?,?)""", tuple(values.values()))
            finally:
                self.connection.execute("ROLLBACK TO shape_test")
                self.connection.execute("RELEASE shape_test")

    def test_validation_rejects_malformed_completed_calendar_periods_inserted_by_sql(self):
        self.migrate()
        series = self.fixture.series("commencement")
        cases = (
            ("day", "2024-02-28", "2024-02-29"),
            ("month", "2024-02-02", "2024-02-29"),
            ("quarter", "2024-02-01", "2024-04-30"),
            ("half_year", "2024-02-01", "2024-07-31"),
            ("year", "2024-01-01", "2024-12-30"),
            ("range", "2024-02-30", "2024-03-01"),
            ("day", "2023-02-29", "2023-02-29"),
            ("range", "20240101", "20241231"),
        )
        for precision, low, high in cases:
            with self.subTest(precision=precision, low=low, high=high):
                values = dict(milestone_type="commercial_production_commencement", status="completed",
                              date_low=low, date_base=None, date_high=high,
                              date_precision=precision, date_literal="source wording")
                payload = dict(kind="milestone", **values)
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                self.connection.execute("SAVEPOINT malformed_calendar_test")
                try:
                    self.connection.execute("""INSERT INTO claim_versions
                        (id,series_id,value_kind,value_sha256,recorded_at,claim_kind,method,created_by_run_id)
                        VALUES ('raw',?,'milestone',?,?,'source_statement','calendar test','run')""",
                                            (series, digest, ADMITTED))
                    self.connection.execute("INSERT INTO claim_values VALUES ('raw','milestone')")
                    self.connection.execute("""INSERT INTO claim_evidence
                        (id,claim_version_id,source_document_id,source_record_id,role,locator)
                        VALUES ('raw-evidence','raw','document','record','support','Project 2')""")
                    self.connection.execute("""INSERT INTO milestone_values
                        (claim_version_id,milestone_type,status,date_low,date_base,date_high,date_precision,date_literal)
                        VALUES ('raw',?,?,?,?,?,?,?)""", tuple(values.values()))
                    self.assertEqual([], self.connection.execute("PRAGMA foreign_key_check").fetchall())
                    errors = validate_database(self.connection)
                    self.assertEqual(1, len(errors), errors)
                    self.assertTrue(errors[0].startswith("claim raw value cannot be reconstructed:"), errors)
                finally:
                    self.connection.execute("ROLLBACK TO malformed_calendar_test")
                    self.connection.execute("RELEASE malformed_calendar_test")
                self.assertEqual([], validate_database(self.connection))

    def test_failed_populated_migration_rolls_back_all_ddl_and_rows(self):
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute("INSERT INTO claim_series VALUES ('orphan','missing','orphan','name','scalar',?)", (ADMITTED,))
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys=ON")
        before = list(self.connection.iterdump())
        with self.assertRaisesRegex(RuntimeError, "foreign key"):
            apply_migrations(self.connection)
        self.assertEqual(before, list(self.connection.iterdump()))
        self.assertEqual(5, schema_version(self.connection))
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
