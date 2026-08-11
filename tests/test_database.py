from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.database import apply_migrations, initialize, schema_version
from semiconductor_atlas.models import (
    CapabilityValue,
    CapacityBasis,
    CapacityValue,
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    ConstraintSeverity,
    ConstraintStatus,
    ConstraintValue,
    DependencyLink,
    Entity,
    EntityKind,
    EvidenceLink,
    EvidenceRole,
    GeometryValue,
    IngestionRun,
    IngestionStatus,
    MilestoneStatus,
    MilestoneValue,
    RelationshipValue,
    ResourceValue,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
    ValueKind,
    source_record_payload_sha256,
)
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    current_claims,
    insert_claim,
    stable_id,
    validate_database,
    value_sha256,
)


RECORDED_AT = "2026-07-17T12:00:00Z"
SOURCE_RETRIEVED_AT = "2026-05-01T00:00:00Z"
IMPORT_STARTED_AT = "2026-05-01T00:00:01Z"
ENTITY_CREATED_AT = "2026-05-01T00:01:00Z"


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "atlas.sqlite"
        self.connection, self.installed = initialize(self.database_path)
        self.family_id = stable_id("source-family", "official-filings")
        self.source_id = stable_id("source", "example-regulator")
        self.document_id = stable_id("document", "permit", RECORDED_AT, "a" * 64)
        self.run_id = stable_id("run", self.source_id, RECORDED_AT)
        self.record_id = stable_id("record", self.run_id, "permit:12")
        self.site_id = stable_id("entity", "site:example")
        self.organization_id = stable_id("entity", "organization:example")
        self.seed_provenance()

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def seed_provenance(self) -> None:
        add_source_family(
            self.connection,
            SourceFamily(
                self.family_id,
                "official-filings",
                "Official filings",
                SOURCE_RETRIEVED_AT,
            ),
        )
        add_source(
            self.connection,
            Source(
                self.source_id,
                self.family_id,
                "example-regulator",
                "Example regulator",
                "Example regulator",
                "https://regulator.example/",
                SOURCE_RETRIEVED_AT,
                license="public-record",
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                self.document_id,
                self.source_id,
                "https://regulator.example/permits/12.pdf",
                "Facility permit 12",
                SOURCE_RETRIEVED_AT,
                "a" * 64,
                published_at="2026-06-01",
                media_type="application/pdf",
                metadata={"jurisdiction": "example"},
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                self.run_id,
                self.source_id,
                IMPORT_STARTED_AT,
                status=IngestionStatus.SUCCEEDED,
                completed_at=ENTITY_CREATED_AT,
                code_version="test",
                input_document_id=self.document_id,
            ),
        )
        add_source_record(
            self.connection,
            SourceRecord(
                self.record_id,
                self.run_id,
                self.document_id,
                "permit:12",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256({"page": 12}),
                payload={"page": 12},
            ),
        )
        add_entity(
            self.connection,
            Entity(
                self.site_id,
                EntityKind.SITE,
                "site:example",
                ENTITY_CREATED_AT,
                display_name="Example Fab",
                created_by_run_id=self.run_id,
            ),
        )
        add_entity(
            self.connection,
            Entity(
                self.organization_id,
                EntityKind.ORGANIZATION,
                "organization:example",
                ENTITY_CREATED_AT,
                display_name="Example Semiconductor",
            ),
        )
        self.connection.commit()

    def add_series(self, suffix: str, value_kind: ValueKind) -> ClaimSeries:
        series = ClaimSeries(
            stable_id("series", self.site_id, suffix),
            self.site_id,
            f"site:example:{suffix}",
            suffix,
            value_kind,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, series)
        return series

    def evidence(self, role: EvidenceRole = EvidenceRole.SUPPORT) -> list[EvidenceLink]:
        return [
            EvidenceLink(
                self.document_id,
                role=role,
                source_record_id=self.record_id,
                locator="page 12",
                excerpt="Authorized production capacity",
            )
        ]

    def test_migration_is_versioned_checksummed_and_idempotent(self) -> None:
        self.assertEqual(self.installed, [1, 2, 3, 4])
        self.assertEqual(schema_version(self.connection), 4)
        self.assertEqual(apply_migrations(self.connection), [])
        rows = self.connection.execute(
            "SELECT version, name, length(sha256) FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                (1, "initial", 64),
                (2, "refresh_indexes", 64),
                (3, "organization_identity", 64),
                (4, "same_kind_entity_identity", 64),
            ],
        )
        indexes = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertIn("claim_versions_open_by_run_idx", indexes)
        self.assertIn("claim_versions_replay_by_run_idx", indexes)

    def test_validation_supports_a_pre_identity_schema(self) -> None:
        for table in (
            "source_entity_assignments",
            "entity_resolution_decisions",
            "entity_resolution_candidates",
            "entity_resolution_run_inputs",
            "entity_resolution_runs",
            "organization_identifier_claim_metadata",
            "organization_name_claim_metadata",
            "ingestion_run_documents",
        ):
            self.connection.execute(f"DROP TABLE {table}")
        self.connection.execute("DELETE FROM schema_migrations WHERE version IN (3, 4)")

        self.assertEqual(2, schema_version(self.connection))
        self.assertEqual([], validate_database(self.connection))

    def test_validation_reports_a_partial_identity_schema(self) -> None:
        self.connection.execute("DROP TABLE source_entity_assignments")

        errors = validate_database(self.connection)

        self.assertEqual(1, len(errors))
        self.assertIn("entity identity schema is incomplete", errors[0])
        self.assertIn("source_entity_assignments", errors[0])

    def test_validation_rejects_outputs_from_non_succeeded_ingestion_runs(self) -> None:
        failed_run_id = stable_id("run", "failed-output-fixture")
        add_ingestion_run(
            self.connection,
            IngestionRun(
                failed_run_id,
                self.source_id,
                "2026-05-01T00:00:02Z",
                status=IngestionStatus.FAILED,
                completed_at="2026-05-01T00:00:03Z",
                error="fixture failure",
                input_document_id=self.document_id,
            ),
        )
        failed_record_id = stable_id("record", failed_run_id, "partial")
        add_source_record(
            self.connection,
            SourceRecord(
                failed_record_id,
                failed_run_id,
                self.document_id,
                "partial",
                SOURCE_RETRIEVED_AT,
                source_record_payload_sha256({"partial": True}),
                payload={"partial": True},
            ),
        )
        failed_entity_id = stable_id("entity", "failed-output-fixture")
        add_entity(
            self.connection,
            Entity(
                failed_entity_id,
                EntityKind.SITE,
                "site:failed-output-fixture",
                ENTITY_CREATED_AT,
                created_by_run_id=failed_run_id,
            ),
        )
        series = ClaimSeries(
            stable_id("series", failed_entity_id, "name"),
            failed_entity_id,
            "site:failed-output-fixture:name",
            "name",
            ValueKind.SCALAR,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, series)
        claim_id = stable_id("claim", series.id)
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series.id,
                "2026-05-01",
                RECORDED_AT,
                ClaimKind.SOURCE_STATEMENT,
                "failed_output_fixture",
                1.0,
                created_by_run_id=failed_run_id,
            ),
            ScalarValue(ScalarType.TEXT, "Partial output"),
            evidence=[
                EvidenceLink(
                    self.document_id,
                    source_record_id=failed_record_id,
                )
            ],
        )

        errors = validate_database(self.connection)

        self.assertIn(
            f"source record {failed_record_id} belongs to non-succeeded ingestion run "
            f"{failed_run_id}",
            errors,
        )
        self.assertIn(
            f"entity {failed_entity_id} was produced by non-succeeded ingestion run "
            f"{failed_run_id}",
            errors,
        )
        self.assertIn(
            f"claim {claim_id} was produced by non-succeeded ingestion run "
            f"{failed_run_id}",
            errors,
        )

    def test_failed_identity_backfill_rolls_back_the_entire_migration(self) -> None:
        migration_directory = (
            Path(__file__).parents[1] / "semiconductor_atlas" / "migrations"
        )
        cases = {
            "cross-source-primary": ("document-b", {}),
            "missing-parameter-document": (
                "document-a",
                {"index_document_ids": ["missing-document"]},
            ),
        }
        for label, (primary_document_id, parameters) in cases.items():
            with self.subTest(label=label):
                path = Path(self.temporary_directory.name) / f"legacy-{label}.sqlite"
                connection = sqlite3.connect(path)
                self.addCleanup(connection.close)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                for version, name in ((1, "initial"), (2, "refresh_indexes")):
                    script = (
                        migration_directory / f"{version:04d}_{name}.sql"
                    ).read_text(encoding="utf-8")
                    connection.executescript(script)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, name, sha256)
                        VALUES (?, ?, ?)
                        """,
                        (
                            version,
                            name,
                            hashlib.sha256(script.encode("utf-8")).hexdigest(),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO source_families(id, stable_key, name, created_at)
                    VALUES ('family', 'family', 'Family', '2026-01-01T00:00:00Z')
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO sources(
                        id, family_id, stable_key, name, publisher, canonical_url, created_at
                    ) VALUES (?, 'family', ?, ?, 'Publisher', ?, '2026-01-01T00:00:00Z')
                    """,
                    (
                        ("source-a", "source-a", "Source A", "https://example.com/a"),
                        ("source-b", "source-b", "Source B", "https://example.com/b"),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO source_documents(
                        id, source_id, document_url, title, retrieved_at, content_sha256
                    ) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', ?)
                    """,
                    (
                        (
                            "document-a",
                            "source-a",
                            "https://example.com/a/document",
                            "Document A",
                            "a" * 64,
                        ),
                        (
                            "document-b",
                            "source-b",
                            "https://example.com/b/document",
                            "Document B",
                            "b" * 64,
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ingestion_runs(
                        id, source_id, input_document_id, started_at, completed_at,
                        status, parameters_json
                    ) VALUES (
                        'run', 'source-a', ?, '2026-01-01T00:00:01Z',
                        '2026-01-01T00:00:02Z', 'succeeded', ?
                    )
                    """,
                    (primary_document_id, json.dumps(parameters)),
                )
                connection.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    apply_migrations(connection)

                self.assertFalse(connection.in_transaction)
                self.assertEqual(2, schema_version(connection))
                self.assertIsNone(
                    connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name = 'ingestion_run_documents'
                        """
                    ).fetchone()
                )
                connection.close()

    def test_stable_ids_and_immutable_source_inputs_are_idempotent(self) -> None:
        self.assertEqual(stable_id("entity", "x"), stable_id("entity", "x"))
        self.assertNotEqual(stable_id("entity", "x"), stable_id("entity", "y"))
        self.assertNotEqual(stable_id("x", "a|b", "c"), stable_id("x", "a", "b|c"))
        self.assertFalse(
            add_source_document(
                self.connection,
                SourceDocument(
                    self.document_id,
                    self.source_id,
                    "https://regulator.example/permits/12.pdf",
                    "Facility permit 12",
                    SOURCE_RETRIEVED_AT,
                    "a" * 64,
                    published_at="2026-06-01",
                    media_type="application/pdf",
                    metadata={"jurisdiction": "example"},
                ),
            )
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE source_documents SET title = 'Changed' WHERE id = ?",
                (self.document_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "DELETE FROM source_records WHERE id = ?", (self.record_id,)
            )

    def test_source_record_hash_must_match_canonical_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "must match"):
            SourceRecord(
                stable_id("record", "mismatched-payload"),
                self.run_id,
                self.document_id,
                "mismatched-payload",
                SOURCE_RETRIEVED_AT,
                "0" * 64,
                payload={"page": 99},
            )

        corrupt_id = stable_id("record", "raw-mismatched-payload")
        self.connection.execute(
            """
            INSERT INTO source_records(
                id, ingestion_run_id, source_document_id, source_record_key,
                observed_at, record_sha256, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrupt_id,
                self.run_id,
                self.document_id,
                "raw-mismatched-payload",
                SOURCE_RETRIEVED_AT,
                "0" * 64,
                '{"page":99}',
            ),
        )
        self.assertTrue(
            any(
                "payload does not match record_sha256" in error
                for error in validate_database(self.connection)
            )
        )

    def test_evidence_fragments_differing_only_by_excerpt_remain_distinct(self) -> None:
        series = self.add_series("evidence-excerpts", ValueKind.SCALAR)
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "excerpt_fixture",
            1.0,
        )
        insert_claim(
            self.connection,
            version,
            ScalarValue(ScalarType.TEXT, "supported"),
            evidence=[
                EvidenceLink(self.document_id, locator="page 12", excerpt="first fragment"),
                EvidenceLink(self.document_id, locator="page 12", excerpt="second fragment"),
            ],
        )
        rows = self.connection.execute(
            """
            SELECT id, excerpt FROM claim_evidence
            WHERE claim_version_id = ? ORDER BY excerpt
            """,
            (version.id,),
        ).fetchall()
        self.assertEqual(
            ["first fragment", "second fragment"],
            [row["excerpt"] for row in rows],
        )
        self.assertEqual(2, len({row["id"] for row in rows}))

    def test_large_integer_scalar_round_trips_without_float_precision_loss(self) -> None:
        exact_value = 9_007_199_254_740_993
        series = self.add_series("large-integer", ValueKind.SCALAR)
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "integer_fixture",
            1.0,
        )
        insert_claim(
            self.connection,
            version,
            ScalarValue(ScalarType.INTEGER, exact_value),
            evidence=self.evidence(),
        )

        stored = self.connection.execute(
            "SELECT integer_value FROM scalar_values WHERE claim_version_id = ?",
            (version.id,),
        ).fetchone()[0]
        self.assertIsInstance(stored, int)
        self.assertEqual(exact_value, stored)
        self.assertEqual([], validate_database(self.connection))
        with self.assertRaisesRegex(ValueError, "signed 64-bit"):
            ScalarValue(ScalarType.INTEGER, 2**63)

    def test_atomic_capacity_claim_is_typed_and_has_many_to_many_evidence(self) -> None:
        series = self.add_series("capacity:qualified", ValueKind.CAPACITY)
        version = ClaimVersion(
            stable_id("claim", series.id, RECORDED_AT),
            series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "permit_extraction",
            0.9,
        )
        value = CapacityValue(
            "wafer_starts_per_month",
            CapacityBasis.QUALIFIED,
            "300mm wafers/month",
            38_000,
            42_000,
            46_000,
        )
        evidence = [
            *self.evidence(EvidenceRole.SUPPORT),
            EvidenceLink(self.document_id, role=EvidenceRole.CONTEXT, locator="appendix A"),
            EvidenceLink(self.document_id, role=EvidenceRole.REFUTE, locator="superseded table"),
        ]
        self.assertTrue(insert_claim(self.connection, version, value, evidence=evidence))
        self.assertFalse(insert_claim(self.connection, version, value, evidence=evidence))
        with self.assertRaisesRegex(ValueError, "immutable lineage"):
            insert_claim(
                self.connection,
                version,
                value,
                evidence=[EvidenceLink(self.document_id, locator="different")],
            )
        capacity = self.connection.execute(
            "SELECT basis, metric, low, base, high FROM capacity_values"
        ).fetchone()
        self.assertEqual(
            tuple(capacity),
            ("qualified", "wafer_starts_per_month", 38_000, 42_000, 46_000),
        )
        roles = {
            row[0] for row in self.connection.execute("SELECT role FROM claim_evidence")
        }
        self.assertEqual(roles, {"support", "context", "refute"})
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE claim_versions SET confidence = 0.1 WHERE id = ?", (version.id,)
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE capacity_values SET base = 1 WHERE claim_version_id = ?",
                (version.id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE claim_evidence SET excerpt = 'rewritten' WHERE claim_version_id = ?",
                (version.id,),
            )
        self.assertEqual(validate_database(self.connection), [])

    def test_every_typed_value_round_trips_through_its_constrained_table(self) -> None:
        values = {
            "scalar": ScalarValue(ScalarType.TEXT, "logic_foundry"),
            "geometry": GeometryValue(
                {"type": "Point", "coordinates": [-112.15, 33.45]}, precision_m=25
            ),
            "relationship": RelationshipValue(
                self.organization_id, "operated_by", {"legal_role": "operator"}
            ),
            "milestone": MilestoneValue(
                "volume_production",
                MilestoneStatus.EXPECTED,
                "2027-01-01",
                "2027-04-01",
                "2027-07-01",
            ),
            "capability": CapabilityValue("process_node", 3.0, unit="nm"),
            "constraint": ConstraintValue(
                "water",
                ConstraintStatus.BINDING,
                ConstraintSeverity.HIGH,
                "Withdrawal permit is below requested volume",
                constrained_entity_id=self.site_id,
            ),
        }
        for name, value in values.items():
            series = self.add_series(f"typed:{name}", value.kind)
            version = ClaimVersion(
                stable_id("claim", series.id, name),
                series.id,
                "2026-06-01",
                RECORDED_AT,
                ClaimKind.DIRECT_OBSERVATION,
                "typed_fixture",
                0.8,
            )
            insert_claim(self.connection, version, value, evidence=self.evidence())
        for table in (
            "scalar_values",
            "geometry_values",
            "relationship_values",
            "milestone_values",
            "capability_values",
            "constraint_values",
        ):
            self.assertEqual(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                1,
            )
        self.assertEqual(validate_database(self.connection), [])

    def test_same_valid_date_correction_preserves_transaction_history(self) -> None:
        series = self.add_series("capacity:announced", ValueKind.CAPACITY)
        first = ClaimVersion(
            stable_id("claim", series.id, "first"),
            series.id,
            "2026-01-01",
            "2026-06-01T00:00:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "company_statement",
            0.6,
        )
        second = ClaimVersion(
            stable_id("claim", series.id, "second"),
            series.id,
            "2026-01-01",
            "2026-07-01T00:00:00Z",
            ClaimKind.RECONCILED_FACT,
            "analyst_reconciliation",
            0.8,
        )
        insert_claim(
            self.connection,
            first,
            CapacityValue(
                "wafer_starts_per_month",
                CapacityBasis.ANNOUNCED,
                "wafers/month",
                80_000,
                100_000,
                120_000,
            ),
            evidence=self.evidence(),
        )
        insert_claim(
            self.connection,
            second,
            CapacityValue(
                "wafer_starts_per_month",
                CapacityBasis.ANNOUNCED,
                "wafers/month",
                75_000,
                90_000,
                105_000,
            ),
            evidence=self.evidence(),
        )
        old_view = current_claims(
            self.connection,
            as_of="2026-06-15",
            recorded_at="2026-06-15T00:00:00Z",
        )
        new_view = current_claims(
            self.connection,
            as_of="2026-06-15",
            recorded_at="2026-07-02T00:00:00Z",
        )
        self.assertEqual([row["id"] for row in old_view], [first.id])
        self.assertEqual([row["id"] for row in new_view], [second.id])
        self.assertIsNone(old_view[0]["superseded_at"])
        superseded = self.connection.execute(
            "SELECT superseded_at FROM claim_versions WHERE id = ?", (first.id,)
        ).fetchone()[0]
        self.assertEqual(superseded, second.recorded_at)
        out_of_order = ClaimVersion(
            stable_id("claim", series.id, "out-of-order"),
            series.id,
            "2026-01-01",
            "2026-06-20T00:00:00Z",
            ClaimKind.RECONCILED_FACT,
            "late_import",
            0.7,
        )
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            insert_claim(
                self.connection,
                out_of_order,
                CapacityValue(
                    "wafer_starts_per_month",
                    CapacityBasis.ANNOUNCED,
                    "wafers/month",
                    70_000,
                    85_000,
                    100_000,
                ),
                evidence=self.evidence(),
            )

    def test_derived_claim_lineage_forms_a_dag(self) -> None:
        source_series = self.add_series("cleanroom_area_reported", ValueKind.SCALAR)
        source_version = ClaimVersion(
            stable_id("claim", source_series.id, "source"),
            source_series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "permit_extraction",
            0.9,
        )
        insert_claim(
            self.connection,
            source_version,
            ScalarValue(ScalarType.NUMBER, 100_000, "m2"),
            evidence=self.evidence(),
        )
        derived_series = self.add_series("cleanroom_area_estimate", ValueKind.SCALAR)
        derived_version = ClaimVersion(
            stable_id("claim", derived_series.id, "derived"),
            derived_series.id,
            "2026-06-01",
            "2026-07-17T12:05:00Z",
            ClaimKind.DERIVED_ESTIMATE,
            "floorplan_model",
            0.7,
        )
        insert_claim(
            self.connection,
            derived_version,
            ScalarValue(ScalarType.NUMBER, 82_000, "m2"),
            dependencies=[DependencyLink(source_version.id)],
        )
        self.assertEqual(validate_database(self.connection), [])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cycle"):
            self.connection.execute(
                """
                INSERT INTO claim_dependencies(
                    claim_version_id, depends_on_claim_version_id, dependency_kind
                ) VALUES (?, ?, 'derived_from')
                """,
                (source_version.id, derived_version.id),
            )

    def test_temporal_lineage_is_enforced_on_insert_and_audited(self) -> None:
        future_document_id = stable_id("document", "future-evidence")
        add_source_document(
            self.connection,
            SourceDocument(
                future_document_id,
                self.source_id,
                "https://regulator.example/future.pdf",
                "Future evidence",
                "2026-07-17T13:00:00Z",
                "d" * 64,
            ),
        )
        evidence_series = self.add_series("future-evidence", ValueKind.SCALAR)
        evidence_version = ClaimVersion(
            stable_id("claim", evidence_series.id),
            evidence_series.id,
            "2026-07-17",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.8,
        )
        with self.assertRaisesRegex(ValueError, "retrieved after"):
            insert_claim(
                self.connection,
                evidence_version,
                ScalarValue(ScalarType.TEXT, "not yet knowable"),
                evidence=[EvidenceLink(future_document_id)],
            )

        parent_series = self.add_series("later-parent", ValueKind.SCALAR)
        parent_version = ClaimVersion(
            stable_id("claim", parent_series.id),
            parent_series.id,
            "2026-07-17",
            "2026-07-17T13:00:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.8,
        )
        insert_claim(
            self.connection,
            parent_version,
            ScalarValue(ScalarType.TEXT, "later fact"),
            evidence=self.evidence(),
        )
        child_series = self.add_series("earlier-child", ValueKind.SCALAR)
        child_version = ClaimVersion(
            stable_id("claim", child_series.id),
            child_series.id,
            "2026-07-17",
            RECORDED_AT,
            ClaimKind.DERIVED_ESTIMATE,
            "test",
            0.7,
        )
        with self.assertRaisesRegex(ValueError, "recorded after"):
            insert_claim(
                self.connection,
                child_version,
                ScalarValue(ScalarType.TEXT, "time travel"),
                dependencies=[DependencyLink(parent_version.id)],
            )

        insert_claim(
            self.connection,
            child_version,
            ScalarValue(ScalarType.TEXT, "valid child"),
            evidence=self.evidence(),
        )
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                id, claim_version_id, source_document_id, role
            ) VALUES (?, ?, ?, 'context')
            """,
            (
                stable_id("claim-evidence", child_version.id, future_document_id),
                child_version.id,
                future_document_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO claim_dependencies(
                claim_version_id, depends_on_claim_version_id, dependency_kind
            ) VALUES (?, ?, 'derived_from')
            """,
            (child_version.id, parent_version.id),
        )
        errors = validate_database(self.connection)
        self.assertTrue(any("retrieved after" in error for error in errors))
        self.assertTrue(any("later-recorded claim" in error for error in errors))

    def test_source_record_and_ingestion_run_must_precede_claim_knowledge_time(self) -> None:
        future_run_id = stable_id("run", "future-evidence-run")
        add_ingestion_run(
            self.connection,
            IngestionRun(
                future_run_id,
                self.source_id,
                "2026-07-17T13:00:00Z",
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-17T13:01:00Z",
                input_document_id=self.document_id,
            ),
        )
        future_run_record_id = stable_id("record", "future-evidence-run")
        add_source_record(
            self.connection,
            SourceRecord(
                future_run_record_id,
                future_run_id,
                self.document_id,
                "future-run-record",
                "2026-07-17T11:00:00Z",
                source_record_payload_sha256({}),
            ),
        )
        future_observation_record_id = stable_id("record", "future-observation")
        add_source_record(
            self.connection,
            SourceRecord(
                future_observation_record_id,
                self.run_id,
                self.document_id,
                "future-observation-record",
                "2026-07-17T13:00:00Z",
                source_record_payload_sha256({}),
            ),
        )
        series = self.add_series("future-source-record", ValueKind.SCALAR)
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            "2026-07-17",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.8,
        )
        with self.assertRaisesRegex(ValueError, "run started after"):
            insert_claim(
                self.connection,
                version,
                ScalarValue(ScalarType.TEXT, "not yet ingestible"),
                evidence=[
                    EvidenceLink(
                        self.document_id,
                        source_record_id=future_run_record_id,
                    )
                ],
            )
        with self.assertRaisesRegex(ValueError, "observed after"):
            insert_claim(
                self.connection,
                version,
                ScalarValue(ScalarType.TEXT, "not yet observed"),
                evidence=[
                    EvidenceLink(
                        self.document_id,
                        source_record_id=future_observation_record_id,
                    )
                ],
            )

        insert_claim(
            self.connection,
            version,
            ScalarValue(ScalarType.TEXT, "valid source record"),
            evidence=self.evidence(),
        )
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                id, claim_version_id, source_document_id, source_record_id,
                role, locator, excerpt
            ) VALUES (?, ?, ?, ?, 'context', NULL, NULL)
            """,
            (
                stable_id("claim-evidence", version.id, future_run_record_id),
                version.id,
                self.document_id,
                future_run_record_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                id, claim_version_id, source_document_id, source_record_id,
                role, locator, excerpt
            ) VALUES (?, ?, ?, ?, 'context', NULL, NULL)
            """,
            (
                stable_id("claim-evidence", version.id, future_observation_record_id),
                version.id,
                self.document_id,
                future_observation_record_id,
            ),
        )
        errors = validate_database(self.connection)
        self.assertTrue(any("later-started ingestion run" in error for error in errors))
        self.assertTrue(any("observed after" in error for error in errors))

    def test_claim_entities_and_series_must_exist_by_recorded_time(self) -> None:
        future_time = "2026-07-17T13:00:00Z"
        future_entity_id = stable_id("entity", "future-target")
        add_entity(
            self.connection,
            Entity(
                future_entity_id,
                EntityKind.INFRASTRUCTURE_ASSET,
                "future-target",
                future_time,
                "Future target",
            ),
        )

        future_series = ClaimSeries(
            stable_id("series", "future-series"),
            self.site_id,
            "future-series",
            "future_series",
            ValueKind.SCALAR,
            future_time,
        )
        add_claim_series(self.connection, future_series)
        with self.assertRaisesRegex(ValueError, "claim series .* created after"):
            insert_claim(
                self.connection,
                ClaimVersion(
                    stable_id("claim", future_series.id),
                    future_series.id,
                    "2026-07-17",
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "test",
                    0.8,
                ),
                ScalarValue(ScalarType.TEXT, "too early"),
                evidence=self.evidence(),
            )

        early_series_on_future_entity = ClaimSeries(
            stable_id("series", "future-subject"),
            future_entity_id,
            "future-subject",
            "future_subject",
            ValueKind.SCALAR,
            ENTITY_CREATED_AT,
        )
        add_claim_series(self.connection, early_series_on_future_entity)
        with self.assertRaisesRegex(ValueError, "subject entity .* created after"):
            insert_claim(
                self.connection,
                ClaimVersion(
                    stable_id("claim", early_series_on_future_entity.id),
                    early_series_on_future_entity.id,
                    "2026-07-17",
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "test",
                    0.8,
                ),
                ScalarValue(ScalarType.TEXT, "too early"),
                evidence=self.evidence(),
            )

        relationship_series = self.add_series("future-target", ValueKind.RELATIONSHIP)
        relationship_value = RelationshipValue(future_entity_id, "depends_on")
        relationship_claim_id = stable_id("claim", relationship_series.id)
        with self.assertRaisesRegex(ValueError, "referenced target entity .* created after"):
            insert_claim(
                self.connection,
                ClaimVersion(
                    relationship_claim_id,
                    relationship_series.id,
                    "2026-07-17",
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "test",
                    0.8,
                ),
                relationship_value,
                evidence=self.evidence(),
            )

        constraint_series = self.add_series("future-constraint", ValueKind.CONSTRAINT)
        with self.assertRaisesRegex(ValueError, "referenced target entity .* created after"):
            insert_claim(
                self.connection,
                ClaimVersion(
                    stable_id("claim", constraint_series.id),
                    constraint_series.id,
                    "2026-07-17",
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "test",
                    0.8,
                ),
                ConstraintValue(
                    "power",
                    ConstraintStatus.POTENTIAL,
                    ConstraintSeverity.MEDIUM,
                    "Future constraint target",
                    constrained_entity_id=future_entity_id,
                ),
                evidence=self.evidence(),
            )

        self.connection.execute(
            """
            INSERT INTO claim_versions(
                id, series_id, value_kind, value_sha256, valid_from, recorded_at,
                claim_kind, method, confidence
            ) VALUES (?, ?, 'relationship', ?, '2026-07-17', ?,
                      'source_statement', 'raw_test', 0.8)
            """,
            (
                relationship_claim_id,
                relationship_series.id,
                value_sha256(relationship_value),
                RECORDED_AT,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO claim_values(claim_version_id, value_kind)
            VALUES (?, 'relationship')
            """,
            (relationship_claim_id,),
        )
        self.connection.execute(
            """
            INSERT INTO relationship_values(
                claim_version_id, object_entity_id, relationship_type
            ) VALUES (?, ?, 'depends_on')
            """,
            (relationship_claim_id, future_entity_id),
        )
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                id, claim_version_id, source_document_id, role
            ) VALUES (?, ?, ?, 'support')
            """,
            (
                stable_id("claim-evidence", relationship_claim_id),
                relationship_claim_id,
                self.document_id,
            ),
        )
        errors = validate_database(self.connection)
        self.assertTrue(any("predates its referenced target entity" in error for error in errors))

    def test_value_registry_prevents_a_second_typed_value(self) -> None:
        series = self.add_series("electricity_use", ValueKind.RESOURCE)
        version = ClaimVersion(
            stable_id("claim", series.id, "resource"),
            series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.DERIVED_ESTIMATE,
            "utility_model",
            0.6,
        )
        insert_claim(
            self.connection,
            version,
            ResourceValue("electricity", "MWh/year", 1, 2, 3),
            evidence=self.evidence(),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO scalar_values(
                    claim_version_id, scalar_type, text_value
                ) VALUES (?, 'text', 'invalid second type')
                """,
                (version.id,),
            )

    def test_validation_finds_claim_without_lineage(self) -> None:
        series = self.add_series("unlined", ValueKind.SCALAR)
        claim_id = stable_id("claim", series.id, "unlined")
        self.connection.execute(
            """
            INSERT INTO claim_versions(
                id, series_id, value_kind, value_sha256, valid_from, recorded_at,
                claim_kind, method, confidence
            ) VALUES (?, ?, 'scalar', ?, '2026-01-01', ?,
                      'source_statement', 'manual_sql', 0.5)
            """,
            (claim_id, series.id, "c" * 64, RECORDED_AT),
        )
        self.connection.execute(
            "INSERT INTO claim_values(claim_version_id, value_kind) VALUES (?, 'scalar')",
            (claim_id,),
        )
        self.connection.execute(
            """
            INSERT INTO scalar_values(claim_version_id, scalar_type, text_value)
            VALUES (?, 'text', 'orphaned assertion')
            """,
            (claim_id,),
        )
        errors = validate_database(self.connection)
        self.assertTrue(any("no evidence or dependency" in error for error in errors))

    def test_all_five_capacity_bases_can_coexist_without_overwriting(self) -> None:
        for basis in CapacityBasis:
            series = self.add_series(f"capacity:{basis.value}", ValueKind.CAPACITY)
            version = ClaimVersion(
                stable_id("claim", series.id, basis.value),
                series.id,
                "2026-06-01",
                RECORDED_AT,
                ClaimKind.RECONCILED_FACT,
                "stage_reconciliation",
                0.75,
            )
            insert_claim(
                self.connection,
                version,
                CapacityValue(
                    "wafer_starts_per_month",
                    basis,
                    "wafers/month",
                    10,
                    20,
                    30,
                ),
                evidence=self.evidence(),
            )
        stored = {
            row[0] for row in self.connection.execute("SELECT basis FROM capacity_values")
        }
        self.assertEqual(stored, {basis.value for basis in CapacityBasis})
        self.assertEqual(len(current_claims(
            self.connection,
            as_of="2026-07-17",
            recorded_at="2026-07-17T13:00:00Z",
            subject_entity_id=self.site_id,
        )), 5)

    def test_failed_typed_insert_rolls_back_the_claim(self) -> None:
        series = self.add_series("bad-value-kind", ValueKind.SCALAR)
        version = ClaimVersion(
            stable_id("claim", series.id, "bad"),
            series.id,
            "2026-06-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.5,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            insert_claim(
                self.connection,
                version,
                ResourceValue("water", "m3/day", 1, 2, 3),
                evidence=self.evidence(),
            )
        self.assertIsNone(
            self.connection.execute(
                "SELECT id FROM claim_versions WHERE id = ?", (version.id,)
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
