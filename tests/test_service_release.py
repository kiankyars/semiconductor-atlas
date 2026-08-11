from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import release as release_module
from semiconductor_atlas.adapters.osm import OSM_ATTRIBUTION, OSM_LICENSE
from semiconductor_atlas.database import connect, initialize
from semiconductor_atlas.coverage import coverage_report
from semiconductor_atlas.ingest_osm import import_osm_candidates
from semiconductor_atlas.models import (
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
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    RelationshipValue,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    ValueKind,
)
from semiconductor_atlas.release import (
    EPA_FRS_ATTRIBUTION,
    EPA_FRS_FAMILY_KEY,
    EPA_FRS_SOURCE_KEY,
    FORMAT,
    write_release,
)
from semiconductor_atlas.repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_ingestion_run_document,
    add_source,
    add_source_document,
    add_source_family,
    insert_claim,
    stable_id,
    validate_database,
)
from semiconductor_atlas.service import (
    claim_records,
    claim_history_records,
    export_geojson,
    materialize_entities,
    summarize,
    validate_semantics,
)


FIXTURE = Path(__file__).parent / "fixtures" / "osm" / "semiconductor.json"
AS_OF = "2026-07-17"
RECORDED_AT = "2026-07-17T12:00:00Z"
QUERY = '[out:json];nwr["industrial"="semiconductor"];out geom;'


class ServiceReleaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.connection, _ = initialize(self.root / "atlas.sqlite")
        self.import_result = import_osm_candidates(
            self.connection,
            FIXTURE,
            RECORDED_AT,
            AS_OF,
            query=QUERY,
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def _add_epa_frs_candidate_fixture(self) -> str:
        family_id = stable_id("source-family", EPA_FRS_FAMILY_KEY)
        source_id = stable_id("source", EPA_FRS_SOURCE_KEY)
        document_id = stable_id("document", source_id, "fixture")
        add_source_family(
            self.connection,
            SourceFamily(
                family_id,
                EPA_FRS_FAMILY_KEY,
                "EPA Facility Registry Service",
                RECORDED_AT,
            ),
        )
        add_source(
            self.connection,
            Source(
                source_id,
                family_id,
                EPA_FRS_SOURCE_KEY,
                "EPA FRS National Single File semiconductor direct-code candidates",
                "U.S. Environmental Protection Agency",
                "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip",
                RECORDED_AT,
                license="https://edg.epa.gov/EPA_Data_License.html",
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                document_id,
                source_id,
                "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip",
                "EPA FRS National Single File fixture",
                RECORDED_AT,
                "e" * 64,
                media_type="application/zip",
                license="https://edg.epa.gov/EPA_Data_License.html",
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                stable_id("run", document_id),
                source_id,
                RECORDED_AT,
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-17T12:00:01Z",
                code_version="epa-frs-fixture-v1",
                input_document_id=document_id,
                parameters={
                    "acceptance_timestamp_basis": "explicit_operator_supplied"
                },
            ),
        )
        entity_key = "epa:frs:110000000001"
        entity_id = stable_id("entity", entity_key)
        add_entity(
            self.connection,
            Entity(
                entity_id,
                EntityKind.SITE,
                entity_key,
                RECORDED_AT,
                "FRS fixture candidate",
            ),
        )
        for predicate, scalar in (
            (
                "candidate_classification",
                ScalarValue(ScalarType.TEXT, "semiconductor_facility_candidate"),
            ),
            ("epa_frs_latitude83", ScalarValue(ScalarType.NUMBER, 33.123)),
            ("epa_frs_longitude83", ScalarValue(ScalarType.NUMBER, -112.123)),
        ):
            series = ClaimSeries(
                stable_id("series", entity_id, predicate),
                entity_id,
                f"{EPA_FRS_FAMILY_KEY}:110000000001:{predicate}",
                predicate,
                ValueKind.SCALAR,
                RECORDED_AT,
            )
            add_claim_series(self.connection, series)
            insert_claim(
                self.connection,
                ClaimVersion(
                    stable_id("claim", series.id),
                    series.id,
                    AS_OF,
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "epa_frs_semiconductor_direct_v1",
                    0.5,
                ),
                scalar,
                evidence=[EvidenceLink(document_id)],
            )
        return entity_id

    def test_osm_candidates_materialize_without_operational_or_capacity_promotion(self) -> None:
        self.assertEqual([], validate_semantics(self.connection))
        claims = claim_records(
            self.connection,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        self.assertEqual(20, len(claims))
        self.assertFalse(any(claim["value_kind"] == "capacity" for claim in claims))
        self.assertTrue(
            all(
                claim["dependencies"]
                for claim in claims
                if claim["claim_kind"] == "derived_estimate"
            )
        )
        self.assertTrue(
            all(
                claim["evidence"]
                for claim in claims
                if claim["claim_kind"] == "source_statement"
            )
        )

        node_name = next(
            claim
            for claim in claims
            if claim["predicate"] == "name"
            and claim["value"]["value"] == "Explicit Fab"
        )
        node_evidence = node_name["evidence"][0]
        self.assertEqual("https://www.openstreetmap.org/node/101", node_evidence["locator"])
        self.assertEqual("2026-07-01T00:00:00Z", node_evidence["source_record_observed_at"])
        self.assertEqual(64, len(node_evidence["source_record_sha256"]))
        self.assertEqual(OSM_LICENSE, node_evidence["license"])

        entities = materialize_entities(
            self.connection,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        self.assertEqual(3, len(entities))
        by_key = {entity["stable_key"]: entity for entity in entities}
        expected_lifecycle = {
            "osm:site:node/101": "unknown",
            "osm:site:way/202": "under_construction",
            "osm:site:relation/505": "unknown",
        }
        for stable_key, lifecycle in expected_lifecycle.items():
            entity = by_key[stable_key]
            self.assertEqual([], entity["capacities"])
            self.assertEqual(
                "semiconductor_facility_candidate",
                entity["scalar_fields"]["candidate_classification"][0]["value"],
            )
            self.assertEqual(
                lifecycle,
                entity["scalar_fields"]["lifecycle_state"][0]["value"],
            )
            self.assertTrue(
                all(capability["qualifier"] == "candidate" for capability in entity["capabilities"])
            )
        self.assertEqual(
            ["construction_observed"],
            [
                milestone["milestone_type"]
                for milestone in by_key["osm:site:way/202"]["milestones"]
            ],
        )
        self.assertIn(
            "https://www.openstreetmap.org/node/101",
            by_key["osm:site:node/101"]["source_urls"],
        )

        summary = summarize(self.connection, as_of=AS_OF, recorded_at=RECORDED_AT)
        self.assertEqual(3, summary["entities_total"])
        self.assertEqual(3, summary["entities_with_geometry"])
        self.assertEqual(20, summary["current_claim_count"])
        self.assertEqual({}, summary["capacity_claims_by_basis"])

    def test_geojson_is_deterministic_attributed_and_candidate_only(self) -> None:
        first = export_geojson(self.connection, as_of=AS_OF, recorded_at=RECORDED_AT)
        second = export_geojson(self.connection, as_of=AS_OF, recorded_at=RECORDED_AT)

        self.assertEqual(first, second)
        self.assertEqual("FeatureCollection", first["type"])
        self.assertEqual([OSM_ATTRIBUTION], first["attribution"])
        self.assertEqual([OSM_LICENSE], first["licenses"])
        self.assertEqual(3, len(first["features"]))
        self.assertEqual(
            sorted(feature["id"] for feature in first["features"]),
            [feature["id"] for feature in first["features"]],
        )
        for feature in first["features"]:
            self.assertEqual("Feature", feature["type"])
            self.assertIn(feature["geometry"]["type"], {"Point", "Polygon"})
            properties = feature["properties"]
            self.assertEqual([], properties["capacities"])
            self.assertEqual(
                "semiconductor_facility_candidate",
                properties["scalar_fields"]["candidate_classification"][0]["value"],
            )
            self.assertNotIn(
                properties["scalar_fields"]["lifecycle_state"][0]["value"],
                {"operational", "qualified"},
            )

    def test_release_bytes_and_manifest_hashes_are_deterministic(self) -> None:
        first_dir = self.root / "release-one"
        second_dir = self.root / "release-two"
        first = write_release(
            self.connection,
            first_dir,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        second = write_release(
            self.connection,
            second_dir,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )

        self.assertEqual(first, second)
        self.assertEqual(FORMAT, first["format"])
        self.assertEqual(3, first["entities"])
        self.assertEqual(20, first["claims"])
        self.assertEqual(20, first["claim_history"])
        self.assertEqual(0, first["capacity_claims"])
        self.assertEqual(
            sorted(path.name for path in first_dir.iterdir()),
            sorted(path.name for path in second_dir.iterdir()),
        )
        for name, metadata in first["files"].items():
            first_bytes = (first_dir / name).read_bytes()
            second_bytes = (second_dir / name).read_bytes()
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(len(first_bytes), metadata["bytes"])
            self.assertEqual(hashlib.sha256(first_bytes).hexdigest(), metadata["sha256"])
        self.assertEqual(
            json.loads((first_dir / "manifest.json").read_text(encoding="utf-8")),
            first,
        )
        self.assertEqual(
            (first_dir / "manifest.json").read_bytes(),
            (second_dir / "manifest.json").read_bytes(),
        )

        with (first_dir / "capacity.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual([], list(csv.DictReader(stream)))
        with (first_dir / "evidence.csv").open(newline="", encoding="utf-8") as stream:
            evidence_rows = list(csv.DictReader(stream))
        node_rows = [
            row
            for row in evidence_rows
            if row["locator"] == "https://www.openstreetmap.org/node/101"
        ]
        self.assertGreater(len(node_rows), 0)
        self.assertTrue(
            all(row["source_record_observed_at"] == "2026-07-01T00:00:00Z" for row in node_rows)
        )
        self.assertTrue(all(len(row["source_record_sha256"]) == 64 for row in node_rows))
        self.assertTrue(all(row["license"] == OSM_LICENSE for row in node_rows))

        source_inputs = json.loads(
            (first_dir / "source_inputs.json").read_text(encoding="utf-8")
        )
        self.assertEqual(OSM_ATTRIBUTION, source_inputs[0]["metadata"]["attribution"])
        self.assertEqual(OSM_LICENSE, source_inputs[0]["license"])
        attribution = (first_dir / "ATTRIBUTION.txt").read_text(encoding="utf-8")
        self.assertIn(OSM_ATTRIBUTION, attribution)
        self.assertIn("ODbL 1.0", attribution)
        readme = (first_dir / "README.md").read_text(encoding="utf-8")
        self.assertIn("OpenStreetMap records are candidate leads only", readme)

        exported_claims = [
            json.loads(line)
            for line in (first_dir / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertFalse(any(claim["value_kind"] == "capacity" for claim in exported_claims))
        lifecycle_values = {
            claim["value"]["value"]
            for claim in exported_claims
            if claim["predicate"] == "lifecycle_state"
        }
        self.assertEqual({"unknown", "under_construction"}, lifecycle_values)

    def test_release_uses_the_run_document_ledger_for_zero_row_inputs(self) -> None:
        auxiliary_document_id = stable_id("document", "release-ledger-auxiliary")
        add_source_document(
            self.connection,
            SourceDocument(
                auxiliary_document_id,
                self.import_result.source_id,
                "https://www.openstreetmap.org/release-ledger-auxiliary",
                "Zero-row auxiliary input",
                RECORDED_AT,
                "f" * 64,
                media_type="application/json",
                license=OSM_LICENSE,
                metadata={"attribution": OSM_ATTRIBUTION},
            ),
        )
        add_ingestion_run_document(
            self.connection,
            IngestionRunDocument(
                self.import_result.ingestion_run_id,
                auxiliary_document_id,
                "auxiliary_zero_row_input",
            ),
        )

        output = self.root / "release-run-document-ledger"
        manifest = write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        source_inputs = json.loads(
            (output / "source_inputs.json").read_text(encoding="utf-8")
        )
        by_id = {item["id"]: item for item in source_inputs}
        self.assertIn(auxiliary_document_id, by_id)
        self.assertEqual(
            self.import_result.ingestion_run_id,
            by_id[auxiliary_document_id]["accepted_by_run_id"],
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        coverage = json.loads(
            (output / "coverage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(source_inputs), manifest["source_documents"])
        self.assertEqual(len(source_inputs), summary["source_document_count"])
        self.assertEqual(len(source_inputs), coverage["source_document_count"])
        osm_family = next(
            family
            for family in coverage["source_families"]
            if family["source_family"] == "openstreetmap"
        )
        self.assertEqual(len(source_inputs), osm_family["document_count"])

    def test_future_supersession_is_hidden_until_the_correction_cutoff(self) -> None:
        site_id = self.import_result.site_entity_ids[0]
        series = ClaimSeries(
            stable_id("series", "cutoff-supersession"),
            site_id,
            "test:cutoff-supersession",
            "cutoff_supersession",
            ValueKind.SCALAR,
            "2026-07-17T12:05:00Z",
        )
        add_claim_series(self.connection, series)
        prior = ClaimVersion(
            stable_id("claim", series.id, "prior"),
            series.id,
            AS_OF,
            "2026-07-17T12:05:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "test",
            1.0,
        )
        correction = ClaimVersion(
            stable_id("claim", series.id, "correction"),
            series.id,
            AS_OF,
            "2026-07-17T12:30:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "test",
            1.0,
        )
        evidence = [EvidenceLink(self.import_result.source_document_id)]
        insert_claim(
            self.connection,
            prior,
            ScalarValue(ScalarType.TEXT, "prior"),
            evidence=evidence,
        )
        insert_claim(
            self.connection,
            correction,
            ScalarValue(ScalarType.TEXT, "corrected"),
            evidence=evidence,
        )

        cutoff = "2026-07-17T12:15:00Z"
        current = claim_records(self.connection, as_of=AS_OF, recorded_at=cutoff)
        history = claim_history_records(
            self.connection, as_of=AS_OF, recorded_at=cutoff
        )
        self.assertIsNone(next(row for row in current if row["id"] == prior.id)["superseded_at"])
        self.assertIsNone(next(row for row in history if row["id"] == prior.id)["superseded_at"])
        at_correction = claim_history_records(
            self.connection,
            as_of=AS_OF,
            recorded_at=correction.recorded_at,
        )
        self.assertEqual(
            correction.recorded_at,
            next(row for row in at_correction if row["id"] == prior.id)[
                "superseded_at"
            ],
        )

    def test_document_accounting_includes_parameter_only_inputs_and_excludes_failed_runs(
        self,
    ) -> None:
        source_id = self.connection.execute(
            "SELECT source_id FROM source_documents WHERE id = ?",
            (self.import_result.source_document_id,),
        ).fetchone()[0]
        parameter_only_id = stable_id("document", "parameter-only")
        failed_id = stable_id("document", "failed-only")
        for identifier, suffix in (
            (parameter_only_id, "parameter-only"),
            (failed_id, "failed-only"),
        ):
            add_source_document(
                self.connection,
                SourceDocument(
                    identifier,
                    source_id,
                    f"https://example.com/{suffix}.json",
                    suffix,
                    RECORDED_AT,
                    ("a" if identifier == parameter_only_id else "b") * 64,
                    media_type="application/json",
                    license=OSM_LICENSE,
                    metadata={"attribution": OSM_ATTRIBUTION},
                ),
            )
        parameter_run_id = stable_id("run", "parameter-only")
        add_ingestion_run(
            self.connection,
            IngestionRun(
                parameter_run_id,
                source_id,
                RECORDED_AT,
                status=IngestionStatus.SUCCEEDED,
                completed_at="2026-07-17T12:00:01Z",
                code_version="multi-input-fixture-v1",
                input_document_id=self.import_result.source_document_id,
                parameters={"index_document_ids": [parameter_only_id]},
            ),
        )
        add_ingestion_run_document(
            self.connection,
            IngestionRunDocument(
                parameter_run_id,
                parameter_only_id,
                "parameter:index_document_ids",
            ),
        )
        add_ingestion_run(
            self.connection,
            IngestionRun(
                stable_id("run", "failed-only"),
                source_id,
                RECORDED_AT,
                status=IngestionStatus.FAILED,
                completed_at="2026-07-17T12:00:01Z",
                code_version="failed-fixture-v1",
                input_document_id=failed_id,
                error="fixture failure",
            ),
        )

        output = self.root / "document-accounting-release"
        manifest = write_release(
            self.connection, output, as_of=AS_OF, recorded_at=RECORDED_AT
        )
        inputs = json.loads(
            (output / "source_inputs.json").read_text(encoding="utf-8")
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        input_ids = {item["id"] for item in inputs}
        self.assertIn(parameter_only_id, input_ids)
        self.assertNotIn(failed_id, input_ids)
        self.assertEqual(2, manifest["source_documents"])
        self.assertEqual(2, summary["source_document_count"])
        self.assertEqual(2, coverage["source_document_count"])

    def test_release_rejects_claim_output_from_a_failed_ingestion_run(self) -> None:
        source_id = self.connection.execute(
            "SELECT source_id FROM source_documents WHERE id = ?",
            (self.import_result.source_document_id,),
        ).fetchone()[0]
        failed_run_id = stable_id("run", "failed-claim-output")
        add_ingestion_run(
            self.connection,
            IngestionRun(
                failed_run_id,
                source_id,
                "2026-07-17T11:59:00Z",
                status=IngestionStatus.FAILED,
                completed_at="2026-07-17T11:59:01Z",
                error="fixture failure",
                input_document_id=self.import_result.source_document_id,
            ),
        )
        site_id = self.import_result.site_entity_ids[0]
        series = ClaimSeries(
            stable_id("series", "failed-claim-output", site_id),
            site_id,
            "test:failed-claim-output",
            "failed_claim_output",
            ValueKind.SCALAR,
            RECORDED_AT,
        )
        add_claim_series(self.connection, series)
        claim_id = stable_id("claim", "failed-claim-output", site_id)
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series.id,
                AS_OF,
                RECORDED_AT,
                ClaimKind.SOURCE_STATEMENT,
                "failed_claim_output_fixture",
                1.0,
                created_by_run_id=failed_run_id,
            ),
            ScalarValue(ScalarType.TEXT, "must not be released"),
            evidence=[EvidenceLink(self.import_result.source_document_id)],
        )

        expected_error = (
            f"claim {claim_id} was produced by non-succeeded ingestion run "
            f"{failed_run_id}"
        )
        self.assertIn(expected_error, validate_database(self.connection))
        output = self.root / "failed-claim-output-release"
        with self.assertRaisesRegex(ValueError, "database validation failed"):
            write_release(
                self.connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
        self.assertFalse(output.exists())

    def test_release_uses_one_database_snapshot_during_concurrent_commit(self) -> None:
        self.connection.commit()
        self.connection.execute("PRAGMA journal_mode = WAL")
        baseline = len(
            claim_records(self.connection, as_of=AS_OF, recorded_at=RECORDED_AT)
        )
        writer = connect(self.root / "atlas.sqlite")
        writer.execute("PRAGMA journal_mode = WAL")
        site_id = self.import_result.site_entity_ids[0]
        series = ClaimSeries(
            stable_id("series", "concurrent-release"),
            site_id,
            "test:concurrent-release",
            "concurrent_release",
            ValueKind.SCALAR,
            RECORDED_AT,
        )
        version = ClaimVersion(
            stable_id("claim", series.id),
            series.id,
            AS_OF,
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "test",
            1.0,
        )
        original_claim_records = release_module.claim_records
        committed = False

        def commit_during_first_claim_read(*args, **kwargs):
            nonlocal committed
            if not committed:
                add_claim_series(writer, series)
                insert_claim(
                    writer,
                    version,
                    ScalarValue(ScalarType.TEXT, "committed concurrently"),
                    evidence=[EvidenceLink(self.import_result.source_document_id)],
                )
                writer.commit()
                committed = True
            return original_claim_records(*args, **kwargs)

        try:
            with mock.patch(
                "semiconductor_atlas.release.claim_records",
                side_effect=commit_during_first_claim_read,
            ):
                output = self.root / "snapshot-release"
                manifest = write_release(
                    self.connection,
                    output,
                    as_of=AS_OF,
                    recorded_at=RECORDED_AT,
                )
            summary = json.loads(
                (output / "summary.json").read_text(encoding="utf-8")
            )
            self.assertTrue(committed)
            self.assertEqual(baseline, manifest["claims"])
            self.assertEqual(baseline, manifest["claim_history"])
            self.assertEqual(baseline, summary["current_claim_count"])
            self.assertEqual(
                baseline + 1,
                len(claim_records(writer, as_of=AS_OF, recorded_at=RECORDED_AT)),
            )
        finally:
            writer.close()

    def test_release_marks_epa_frs_as_scalar_only_candidate_input(self) -> None:
        entity_id = self._add_epa_frs_candidate_fixture()
        output = self.root / "epa-frs-release"

        manifest = write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )

        self.assertEqual(0, manifest["capacity_claims"])
        source_inputs = json.loads(
            (output / "source_inputs.json").read_text(encoding="utf-8")
        )
        frs_input = next(
            item for item in source_inputs if item["source_family"] == EPA_FRS_FAMILY_KEY
        )
        self.assertEqual(EPA_FRS_SOURCE_KEY, frs_input["source_key"])
        self.assertEqual(
            "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip",
            frs_input["document_url"],
        )

        attribution = (output / "ATTRIBUTION.txt").read_text(encoding="utf-8")
        self.assertIn(EPA_FRS_ATTRIBUTION, attribution)
        readme = (output / "README.md").read_text(encoding="utf-8")
        self.assertIn("U.S. registry candidate leads only", readme)
        self.assertIn("operation, lifecycle state, nor capacity", readme)
        self.assertIn("Raw NAD83 latitude/longitude scalars", readme)
        self.assertIn("not GeoJSON geometry", readme)

        coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(1, coverage["entity_namespace_counts"]["epa_frs"])
        geojson = json.loads((output / "atlas.geojson").read_text(encoding="utf-8"))
        feature = next(item for item in geojson["features"] if item["id"] == entity_id)
        self.assertIsNone(feature["geometry"])
        scalar_fields = feature["properties"]["scalar_fields"]
        self.assertEqual(33.123, scalar_fields["epa_frs_latitude83"][0]["value"])
        self.assertEqual(-112.123, scalar_fields["epa_frs_longitude83"][0]["value"])
        self.assertEqual([], feature["properties"]["capacities"])

    def test_materialization_preserves_cutoff_reference_closure(self) -> None:
        site_id = self.import_result.site_entity_ids[0]
        operator_id = stable_id("entity", "test:operator")
        infrastructure_id = stable_id("entity", "test:infrastructure")
        orphan_id = stable_id("entity", "test:orphan")
        for identifier, kind, key, name in (
            (operator_id, EntityKind.ORGANIZATION, "test:operator", "Test Operator"),
            (
                infrastructure_id,
                EntityKind.INFRASTRUCTURE_ASSET,
                "test:infrastructure",
                "Test Substation",
            ),
            (orphan_id, EntityKind.SITE, "test:orphan", "Unreferenced Site"),
        ):
            add_entity(self.connection, Entity(identifier, kind, key, RECORDED_AT, name))

        values = (
            (
                "test_operator",
                ValueKind.RELATIONSHIP,
                RelationshipValue(operator_id, "operated_by"),
            ),
            (
                "test_constraint",
                ValueKind.CONSTRAINT,
                ConstraintValue(
                    "electricity_interconnection",
                    ConstraintStatus.POTENTIAL,
                    ConstraintSeverity.MEDIUM,
                    "Substation upgrade may be required",
                    constrained_entity_id=infrastructure_id,
                ),
            ),
        )
        for predicate, value_kind, value in values:
            series = ClaimSeries(
                stable_id("series", site_id, predicate),
                site_id,
                f"test:{predicate}",
                predicate,
                value_kind,
                RECORDED_AT,
            )
            add_claim_series(self.connection, series)
            insert_claim(
                self.connection,
                ClaimVersion(
                    stable_id("claim", series.id),
                    series.id,
                    AS_OF,
                    RECORDED_AT,
                    ClaimKind.SOURCE_STATEMENT,
                    "test_reference",
                    0.8,
                ),
                value,
                evidence=[EvidenceLink(self.import_result.source_document_id)],
            )

        entities = materialize_entities(
            self.connection,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        by_id = {entity["entity_id"]: entity for entity in entities}
        self.assertIn(operator_id, by_id)
        self.assertIn(infrastructure_id, by_id)
        self.assertNotIn(orphan_id, by_id)
        self.assertEqual(0, by_id[operator_id]["claim_count"])
        self.assertEqual([], by_id[operator_id]["source_urls"])
        self.assertEqual("Test Substation", by_id[infrastructure_id]["name"])

        coverage = coverage_report(
            self.connection,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        self.assertEqual(
            {"openstreetmap": 3, "other": 2},
            coverage["entity_namespace_counts"],
        )

    def test_geojson_attribution_only_uses_cutoff_reachable_evidence(self) -> None:
        add_source_document(
            self.connection,
            SourceDocument(
                stable_id("document", "unrelated"),
                self.import_result.source_id,
                "https://example.com/unrelated",
                "Unrelated",
                RECORDED_AT,
                "c" * 64,
                metadata={"attribution": "UNRELATED ATTRIBUTION", "license": "unrelated"},
            ),
        )
        add_source_document(
            self.connection,
            SourceDocument(
                stable_id("document", "future"),
                self.import_result.source_id,
                "https://example.com/future",
                "Future",
                "2026-07-18T00:00:00Z",
                "d" * 64,
                metadata={"attribution": "FUTURE ATTRIBUTION", "license": "future"},
            ),
        )

        geojson = export_geojson(self.connection, as_of=AS_OF, recorded_at=RECORDED_AT)
        self.assertEqual([OSM_ATTRIBUTION], geojson["attribution"])
        self.assertEqual([OSM_LICENSE], geojson["licenses"])

    def test_release_history_closes_lineage_and_escapes_csv_formulas(self) -> None:
        site_id = self.import_result.site_entity_ids[0]
        revision_series = ClaimSeries(
            stable_id("series", "test-revision"),
            site_id,
            "test:revision",
            "test_revision",
            ValueKind.SCALAR,
            RECORDED_AT,
        )
        add_claim_series(self.connection, revision_series)
        prior = ClaimVersion(
            stable_id("claim", revision_series.id, "prior"),
            revision_series.id,
            AS_OF,
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.7,
        )
        current = ClaimVersion(
            stable_id("claim", revision_series.id, "current"),
            revision_series.id,
            AS_OF,
            "2026-07-17T12:10:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.8,
        )
        insert_claim(
            self.connection,
            prior,
            ScalarValue(ScalarType.TEXT, "prior"),
            evidence=[
                EvidenceLink(
                    self.import_result.source_document_id,
                    locator="+SUM(1,1)",
                    excerpt='=HYPERLINK("https://example.com")',
                )
            ],
        )
        insert_claim(
            self.connection,
            current,
            ScalarValue(ScalarType.TEXT, "current"),
            evidence=[EvidenceLink(self.import_result.source_document_id)],
        )

        future_parent_series = ClaimSeries(
            stable_id("series", "future-parent"),
            site_id,
            "test:future-parent",
            "future_parent",
            ValueKind.SCALAR,
            RECORDED_AT,
        )
        add_claim_series(self.connection, future_parent_series)
        future_parent = ClaimVersion(
            stable_id("claim", future_parent_series.id),
            future_parent_series.id,
            "2027-01-01",
            "2026-07-17T12:20:00Z",
            ClaimKind.SOURCE_STATEMENT,
            "test",
            0.7,
        )
        insert_claim(
            self.connection,
            future_parent,
            ScalarValue(ScalarType.TEXT, "known future input"),
            evidence=[EvidenceLink(self.import_result.source_document_id)],
        )
        derived_series = ClaimSeries(
            stable_id("series", "derived-child"),
            site_id,
            "test:derived-child",
            "derived_child",
            ValueKind.SCALAR,
            RECORDED_AT,
        )
        add_claim_series(self.connection, derived_series)
        derived = ClaimVersion(
            stable_id("claim", derived_series.id),
            derived_series.id,
            AS_OF,
            "2026-07-17T12:30:00Z",
            ClaimKind.DERIVED_ESTIMATE,
            "test",
            0.6,
        )
        insert_claim(
            self.connection,
            derived,
            ScalarValue(ScalarType.TEXT, "derived"),
            dependencies=[DependencyLink(future_parent.id)],
        )

        output = self.root / "history-release"
        manifest = write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at="2026-07-17T13:00:00Z",
        )
        current_ids = {
            json.loads(line)["id"]
            for line in (output / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        }
        history = [
            json.loads(line)
            for line in (output / "claim_history.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        history_ids = {claim["id"] for claim in history}
        self.assertNotIn(prior.id, current_ids)
        self.assertNotIn(future_parent.id, current_ids)
        self.assertIn(prior.id, history_ids)
        self.assertIn(future_parent.id, history_ids)
        self.assertEqual(len(history), manifest["claim_history"])
        for claim in history:
            self.assertTrue(
                all(
                    dependency["depends_on_claim_version_id"] in history_ids
                    for dependency in claim["dependencies"]
                )
            )

        with (output / "evidence.csv").open(newline="", encoding="utf-8") as stream:
            evidence = list(csv.DictReader(stream))
        evidence_ids = {row["claim_id"] for row in evidence}
        self.assertIn(prior.id, evidence_ids)
        self.assertIn(future_parent.id, evidence_ids)
        prior_row = next(row for row in evidence if row["claim_id"] == prior.id)
        self.assertEqual("'+SUM(1,1)", prior_row["locator"])
        self.assertEqual("'=HYPERLINK(\"https://example.com\")", prior_row["excerpt"])

    def test_release_replaces_only_prior_managed_files(self) -> None:
        output = self.root / "managed-release"
        write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
            extra_files={"obsolete.txt": b"managed\n"},
        )
        notes = output / "user-notes.txt"
        notes.write_bytes(b"preserve me\n")
        write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        self.assertFalse((output / "obsolete.txt").exists())
        self.assertEqual(b"preserve me\n", notes.read_bytes())

        unmanaged = self.root / "unmanaged-output"
        unmanaged.mkdir()
        user_file = unmanaged / "notes.txt"
        user_file.write_bytes(b"untouched\n")
        with self.assertRaisesRegex(ValueError, "lacks a prior manifest"):
            write_release(
                self.connection,
                unmanaged,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
        self.assertEqual([user_file], list(unmanaged.iterdir()))
        self.assertEqual(b"untouched\n", user_file.read_bytes())

    def test_release_rejects_a_forged_prior_managed_file_without_deleting_it(self) -> None:
        output = self.root / "forged-release"
        write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
            extra_files={"obsolete.txt": b"managed\n"},
        )
        protected = output / "protected.txt"
        protected.write_bytes(b"user data\n")
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][protected.name] = {"bytes": 0, "sha256": "0" * 64}
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "differs from prior manifest"):
            write_release(
                self.connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
        self.assertEqual(b"user data\n", protected.read_bytes())
        self.assertEqual(b"managed\n", (output / "obsolete.txt").read_bytes())

    def test_release_rejects_a_missing_prior_managed_file(self) -> None:
        output = self.root / "missing-managed-release"
        write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        missing = output / "claims.jsonl"
        missing.unlink()
        manifest_before = (output / "manifest.json").read_bytes()

        with self.assertRaisesRegex(ValueError, "prior manifest are missing"):
            write_release(
                self.connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )

        self.assertFalse(missing.exists())
        self.assertEqual(manifest_before, (output / "manifest.json").read_bytes())

    def test_release_rejects_an_output_directory_symlink(self) -> None:
        victim = self.root / "victim-release"
        victim.mkdir()
        sentinel = victim / "sentinel.txt"
        sentinel.write_bytes(b"do not touch\n")
        output = self.root / "release-link"
        output.symlink_to(victim, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            write_release(
                self.connection,
                output,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )

        self.assertTrue(output.is_symlink())
        self.assertEqual(b"do not touch\n", sentinel.read_bytes())
        self.assertEqual([sentinel], list(victim.iterdir()))

    def test_release_entities_close_expired_history_references(self) -> None:
        subject_id = stable_id("entity", "history-only-subject")
        target_id = stable_id("entity", "history-only-target")
        add_entity(
            self.connection,
            Entity(
                subject_id,
                EntityKind.PROJECT,
                "history-only-subject",
                RECORDED_AT,
                "Historical Project",
            ),
        )
        add_entity(
            self.connection,
            Entity(
                target_id,
                EntityKind.ORGANIZATION,
                "history-only-target",
                RECORDED_AT,
                "Historical Operator",
            ),
        )
        series = ClaimSeries(
            stable_id("series", "history-only-relationship"),
            subject_id,
            "history-only-relationship",
            "relationship.operator",
            ValueKind.RELATIONSHIP,
            RECORDED_AT,
        )
        add_claim_series(self.connection, series)
        version = ClaimVersion(
            stable_id("claim", "history-only-relationship"),
            series.id,
            "2026-01-01",
            RECORDED_AT,
            ClaimKind.SOURCE_STATEMENT,
            "history_fixture",
            1.0,
            valid_to="2026-06-01",
        )
        insert_claim(
            self.connection,
            version,
            RelationshipValue(target_id, "operated_by"),
            evidence=[EvidenceLink(self.import_result.source_document_id)],
        )

        output = self.root / "history-entity-closure"
        write_release(
            self.connection,
            output,
            as_of=AS_OF,
            recorded_at=RECORDED_AT,
        )
        current_ids = {
            json.loads(line)["id"]
            for line in (output / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        }
        history_ids = {
            json.loads(line)["id"]
            for line in (output / "claim_history.jsonl").read_text(encoding="utf-8").splitlines()
        }
        entities = {
            row["entity_id"]: row
            for row in (
                json.loads(line)
                for line in (output / "entities.jsonl").read_text(encoding="utf-8").splitlines()
            )
        }
        self.assertNotIn(version.id, current_ids)
        self.assertIn(version.id, history_ids)
        self.assertIn(subject_id, entities)
        self.assertIn(target_id, entities)
        self.assertEqual(0, entities[subject_id]["claim_count"])
        self.assertEqual(0, entities[target_id]["claim_count"])

    def test_semantic_validation_blocks_a_derived_claim_without_dependencies(self) -> None:
        self.assertEqual([], validate_database(self.connection))
        site_id = self.import_result.site_entity_ids[0]
        series_id = stable_id("series", "invalid-derived", site_id)
        add_claim_series(
            self.connection,
            ClaimSeries(
                series_id,
                site_id,
                "test:invalid-derived",
                "invalid_derived",
                ValueKind.SCALAR,
                RECORDED_AT,
            ),
        )
        claim_id = stable_id("claim", "invalid-derived", site_id)
        insert_claim(
            self.connection,
            ClaimVersion(
                claim_id,
                series_id,
                AS_OF,
                RECORDED_AT,
                ClaimKind.DERIVED_ESTIMATE,
                "invalid_test_method",
                0.5,
            ),
            ScalarValue(ScalarType.TEXT, "unsupported inference"),
            evidence=(
                EvidenceLink(
                    self.import_result.source_document_id,
                    source_record_id=self.import_result.source_record_ids[0],
                ),
            ),
        )

        self.assertEqual([], validate_database(self.connection))
        errors = validate_semantics(self.connection)
        self.assertIn(f"derived_estimate claim {claim_id} requires claim dependencies", errors)
        output_dir = self.root / "invalid-release"
        with self.assertRaisesRegex(ValueError, "database validation failed"):
            write_release(
                self.connection,
                output_dir,
                as_of=AS_OF,
                recorded_at=RECORDED_AT,
            )
        self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
