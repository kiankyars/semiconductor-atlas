from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from dataclasses import replace

from semiconductor_atlas.database import apply_migrations, initialize, schema_version
from semiconductor_atlas.models import (
    CapabilityValue, CapacityBasis, CapacityValue, ClaimKind, ClaimSeries, ClaimVersion,
    ConstraintSeverity, ConstraintStatus, ConstraintValue, DependencyLink, Entity, EntityKind,
    EvidenceLink, GeometryValue, IngestionRun, IngestionStatus, MilestoneStatus, MilestoneValue,
    RelationshipValue, ResourceValue, ScalarType, ScalarValue, Source, SourceDocument,
    SourceFamily, SourceRecord, ValueKind, source_record_payload_sha256,
)
from semiconductor_atlas.repository import (
    add_claim_series, add_entity, add_ingestion_run, add_source, add_source_document,
    add_source_family, add_source_record, current_claims, insert_claim, known_source_claims,
    validate_database, value_sha256,
)
from tests import test_release_identity as identity_fixture


OBSERVED = "2026-07-18T01:54:47Z"
ADMITTED = "2026-09-07T10:00:00Z"
LATER = "2026-09-08T10:00:00Z"


class SourceClaimPrecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection, _ = initialize(":memory:", target_version=4)
        self.addCleanup(self.connection.close)
        add_source_family(self.connection, SourceFamily("family", "official", "Official", ADMITTED))
        add_source(self.connection, Source("source", "family", "source", "Source", "Publisher",
                                           "https://example.org/source", ADMITTED))
        add_source_document(self.connection, SourceDocument(
            "document", "source", "https://example.org/source", "Document", OBSERVED, "a" * 64))
        add_ingestion_run(self.connection, IngestionRun(
            "run", "source", ADMITTED, status=IngestionStatus.SUCCEEDED,
            completed_at="2026-09-07T10:00:01Z", input_document_id="document"))
        payload = {"fixture": "source-native project"}
        add_source_record(self.connection, SourceRecord(
            "record", "run", "document", "project-2", OBSERVED,
            source_record_payload_sha256(payload), payload=payload))
        add_entity(self.connection, Entity("project", EntityKind.PROJECT, "source:project:2",
                                          ADMITTED, "Project 2", "run"))
        self.connection.commit()

    def series(self, name: str, kind: ValueKind = ValueKind.MILESTONE) -> str:
        add_claim_series(self.connection, ClaimSeries(name, "project", name, name, kind, ADMITTED))
        return name

    def claim(self, identifier: str, series: str, value, *, recorded_at=ADMITTED,
              valid_from=None, confidence=None, dependencies=()) -> ClaimVersion:
        version = ClaimVersion(identifier, series, valid_from, recorded_at,
                               ClaimKind.SOURCE_STATEMENT, "reviewed_source", confidence,
                               created_by_run_id="run")
        insert_claim(self.connection, version, value,
                     evidence=[EvidenceLink("document", source_record_id="record", locator="Project 2")],
                     dependencies=dependencies)
        return version

    def migrate(self) -> None:
        self.connection.commit()
        self.assertEqual([5], apply_migrations(self.connection))

    @staticmethod
    def period(**changes) -> MilestoneValue:
        args = dict(milestone_type="volume_production", status=MilestoneStatus.EXPECTED,
                    date_low="2027-07-01", date_base=None, date_high="2027-12-31",
                    date_precision="half_year", date_literal="second half of 2027")
        return MilestoneValue(**(args | changes))

    def test_unknown_effective_statement_is_knowledge_not_physical_world(self) -> None:
        self.migrate()
        series = self.series("production-target")
        self.claim("target", series, self.period())
        self.assertEqual([], known_source_claims(self.connection, recorded_at=OBSERVED))
        known = known_source_claims(self.connection, recorded_at=ADMITTED)
        self.assertEqual(["target"], [row["id"] for row in known])
        self.assertIsNone(known[0]["valid_from"])
        self.assertIsNone(known[0]["confidence"])
        self.assertEqual([], known_source_claims(self.connection, recorded_at=ADMITTED,
                                                subject_entity_id="different"))
        for as_of in ("2026-09-07", "2027-12-31", "2030-01-01"):
            self.assertEqual([], current_claims(self.connection, as_of=as_of, recorded_at=LATER))
        self.assertEqual([], validate_database(self.connection))

    def test_null_effective_corrections_replay_and_do_not_rewrite_history(self) -> None:
        self.migrate()
        series = self.series("production-target")
        old_value = self.period(date_low="2028-01-01", date_high="2028-12-31",
                                date_precision="year", date_literal="2028")
        old = self.claim("old", series, old_value)
        self.claim("new", series, self.period(), recorded_at=LATER)
        earlier = known_source_claims(self.connection, recorded_at=ADMITTED)
        self.assertEqual(["old"], [row["id"] for row in earlier])
        self.assertIsNone(earlier[0]["superseded_at"])
        self.assertEqual(["new"], [row["id"] for row in known_source_claims(
            self.connection, recorded_at=LATER)])
        changes = self.connection.total_changes
        self.assertFalse(insert_claim(self.connection, old, old_value,
                         evidence=[EvidenceLink("document", source_record_id="record", locator="Project 2")]))
        self.assertEqual(changes, self.connection.total_changes)
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            self.claim("backdated", series, old_value)
        self.assertEqual([], validate_database(self.connection))

    def test_null_effective_uniqueness_and_immutable_guards(self) -> None:
        self.migrate()
        self.claim("target", self.series("production-target"), self.period())
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("""
                INSERT INTO claim_versions(id,series_id,value_kind,value_sha256,valid_from,
                    recorded_at,claim_kind,method,confidence)
                SELECT 'duplicate',series_id,value_kind,value_sha256,valid_from,
                    recorded_at,claim_kind,method,confidence FROM claim_versions WHERE id='target'
            """)
        for sql in (
            "UPDATE claim_versions SET valid_from='2026-09-07' WHERE id='target'",
            "UPDATE claim_versions SET confidence=1 WHERE id='target'",
            "DELETE FROM claim_versions WHERE id='target'",
            "UPDATE milestone_values SET date_literal='2028' WHERE claim_version_id='target'",
            "DELETE FROM milestone_values WHERE claim_version_id='target'",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                self.connection.execute(sql)

    def test_microsecond_cutoff_does_not_leak_future_supersession(self) -> None:
        self.migrate()
        series = self.series("production-target")
        self.claim("old", series, self.period(), recorded_at="2026-09-07T10:00:01.123456Z")
        self.claim("new", series, self.period(date_literal="H2 2027"),
                   recorded_at="2026-09-07T10:00:02.123456Z")
        early = known_source_claims(self.connection, recorded_at="2026-09-07T10:00:02.123455Z")
        self.assertEqual(["old"], [row["id"] for row in early])
        self.assertIsNone(early[0]["superseded_at"])
        self.assertEqual(["new"], [row["id"] for row in known_source_claims(
            self.connection, recorded_at="2026-09-07T10:00:02.123456Z")])

    def test_invalid_periods_and_unknown_dates_fail_closed(self) -> None:
        for args in (
            {"date_base": "2027-10-01"}, {"status": MilestoneStatus.COMPLETED},
            {"date_low": "2027-07-02"}, {"date_high": "2027-12-30"},
            {"date_low": "2027-04-01", "date_high": "2027-09-30"},
            {"date_low": "2028-01-01"}, {"date_precision": "fiscal_year"},
            {"date_precision": []}, {"date_precision": {}}, {"date_literal": " "},
            {"date_precision": None}, {"date_low": "2027-02-29"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.period(**args)
        for kind in ClaimKind:
            if kind is ClaimKind.SOURCE_STATEMENT:
                continue
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "unknown effective time"):
                ClaimVersion("id", "series", None, ADMITTED, kind, "fixture", None)
        with self.assertRaisesRegex(ValueError, "unknown effective time"):
            ClaimVersion("id", "series", None, ADMITTED, ClaimKind.SOURCE_STATEMENT,
                         "fixture", None, valid_to="2027-01-01")

    def test_calendar_precision_including_leap_year(self) -> None:
        for precision, low, high in (
            ("day", "2028-02-29", "2028-02-29"),
            ("month", "2028-02-01", "2028-02-29"),
            ("quarter", "2028-10-01", "2028-12-31"),
            ("half_year", "2028-01-01", "2028-06-30"),
            ("year", "2028-01-01", "2028-12-31"),
            ("range", "2028-02-29", "2029-03-17"),
        ):
            with self.subTest(precision=precision):
                value = self.period(date_precision=precision, date_low=low, date_high=high,
                                    date_literal=f"fixture {precision}")
                self.assertIsNone(value.date_base)

    def test_legacy_hash_is_identical_and_new_precision_is_hash_bound(self) -> None:
        value = MilestoneValue("production", MilestoneStatus.EXPECTED,
                               "2027-01-01", "2027-04-01", "2027-07-01")
        payload = dict(kind="milestone", milestone_type="production", status="expected", date_low="2027-01-01",
                       date_base="2027-04-01", date_high="2027-07-01")
        expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.assertEqual(expected, value_sha256(value))
        self.assertNotEqual(value_sha256(self.period()), value_sha256(
            replace(self.period(), date_literal="H2 2027")))

    def test_populated_migration_preserves_all_value_types_lineage_and_triggers(self) -> None:
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
        for value in values:
            self.claim(value.kind.value, self.series(value.kind.value, value.kind), value,
                       valid_from="2026-09-07", confidence=0.5,
                       dependencies=() if value.kind is ValueKind.SCALAR else (DependencyLink("scalar"),))
        tables = [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'")]
        before = {table: [dict(row) for row in self.connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                  for table in tables}
        triggers = dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        self.migrate()
        for table, rows in before.items():
            actual = [dict(row) for row in self.connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            if table == "milestone_values":
                actual = [{key: value for key, value in row.items() if key not in ("date_precision", "date_literal")}
                          for row in actual]
            self.assertEqual(rows, actual, table)
        self.assertEqual(triggers, dict(self.connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'")))
        self.assertEqual([], validate_database(self.connection))
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_populated_identity_migration_preserves_assignments_and_guards(self) -> None:
        fixture = identity_fixture.ReleaseIdentityTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        connection = fixture.connection
        connection.commit()
        tables = ("entity_resolution_candidates", "entity_resolution_decisions", "source_entity_assignments",
                  "organization_name_claim_metadata", "organization_identifier_claim_metadata")
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                  for table in tables}
        self.assertEqual([5], apply_migrations(connection))
        for table in tables:
            self.assertEqual(before[table], [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")])
        self.assertEqual([], validate_database(connection))

    def test_migration_failure_rolls_back_reconstruction_and_restores_foreign_keys(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = OFF")
        self.connection.execute("INSERT INTO claim_series VALUES ('orphan','missing','orphan','name','scalar',?)",
                                (ADMITTED,))
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = ON")
        before = list(self.connection.iterdump())
        with self.assertRaisesRegex(RuntimeError, "foreign key"):
            apply_migrations(self.connection)
        self.assertEqual(before, list(self.connection.iterdump()))
        self.assertEqual(4, schema_version(self.connection))
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_migrations_do_not_commit_caller_transaction_and_reject_downgrades(self) -> None:
        self.series("uncommitted")
        with self.assertRaisesRegex(ValueError, "current transaction"):
            apply_migrations(self.connection)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual([], self.connection.execute("SELECT * FROM claim_series").fetchall())
        for target in (0, True, 99, 3):
            with self.subTest(target=target), self.assertRaises(ValueError):
                apply_migrations(self.connection, target_version=target)
        self.assertEqual([], apply_migrations(self.connection, target_version=4))


if __name__ == "__main__":
    unittest.main()
