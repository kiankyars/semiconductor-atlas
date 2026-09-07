from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from semiconductor_atlas.alerts import detect_revision_alerts
from semiconductor_atlas.analytics import forecast_current_capacity
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    CapacityBasis, CapacityValue, ClaimKind, ClaimSeries, ClaimVersion,
    DependencyLink, Entity, EntityKind, EvidenceLink, IngestionRun,
    IngestionStatus, MilestoneStatus, MilestoneValue, ScalarType, ScalarValue,
    Source, SourceDocument, SourceFamily, SourceRecord, source_record_payload_sha256,
)
from semiconductor_atlas.release import write_release
from semiconductor_atlas.repository import (
    add_claim_series, add_entity, add_ingestion_run, add_source,
    add_source_document, add_source_family, add_source_record, insert_claim,
)
from semiconductor_atlas.service import (
    claim_history_records, claim_records, claim_value, source_claim_records,
)


OBSERVED = "2026-07-18T01:54:47Z"
ADMITTED = "2026-09-07T09:00:00Z"
FIRST = "2026-09-07T09:00:02Z"
SECOND = "2026-09-07T09:00:03Z"
BEFORE = "2026-09-07T08:59:59Z"


class SourceClaimViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.connection, _ = initialize(self.root / "atlas.sqlite")
        self.addCleanup(self.connection.close)
        self._seed(self.connection)

    def _seed(self, connection) -> None:
        add_source_family(connection, SourceFamily("family", "fixture", "Fixture", OBSERVED))
        add_source(connection, Source(
            "source", "family", "fixture", "Source", "Fixture",
            "https://example.test/source", OBSERVED,
        ))
        add_source_document(connection, SourceDocument(
            "document", "source", "https://example.test/source", "Source",
            OBSERVED, "a" * 64, metadata={"raw_redistribution": False},
        ))
        add_ingestion_run(connection, IngestionRun(
            "run", "source", ADMITTED, status=IngestionStatus.SUCCEEDED,
            completed_at="2026-09-07T09:00:01Z", input_document_id="document",
            parameters={"acceptance_timestamp_basis": "actual_database_admission"},
        ))
        payload = {"source_native_subject": "Fab 2", "raw_excerpt": "local only"}
        add_source_record(connection, SourceRecord(
            "record", "run", "document", "page:fab-2", OBSERVED,
            source_record_payload_sha256(payload), payload,
        ))
        add_entity(connection, Entity(
            "fab2", EntityKind.PROJECT, "source-native:fab-2", ADMITTED,
            "Source-native Fab 2", "run",
        ))

    def _claim(self, claim_id, value, *, series="target", effective=None,
               confidence=None, recorded=FIRST, connection=None, dependencies=()):
        connection = connection or self.connection
        add_claim_series(connection, ClaimSeries(
            series, "fab2", series, series, value.kind, ADMITTED,
        ))
        version = ClaimVersion(
            claim_id, series, effective, recorded, ClaimKind.SOURCE_STATEMENT,
            "reviewed_source_statement_fixture", confidence, created_by_run_id="run",
        )
        insert_claim(connection, version, value,
                     evidence=[EvidenceLink("document", source_record_id="record", locator="Fab 2 row")],
                     dependencies=dependencies)
        return version

    def _period(self, year=2028, half_year=False):
        return MilestoneValue(
            "production_start", MilestoneStatus.EXPECTED,
            f"{year}-07-01" if half_year else f"{year}-01-01", None,
            f"{year}-12-31", "half_year" if half_year else "year",
            f"second half of {year}" if half_year else str(year),
        )

    def _release(self, name="release", *, recorded=SECOND, as_of="2026-09-07", connection=None):
        output = self.root / name
        manifest = write_release(connection or self.connection, output,
                                 as_of=as_of, recorded_at=recorded)
        return output, manifest

    def _jsonl(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_source_view_uses_actual_admission_not_observation(self):
        self._claim("before", self._period())
        self.assertEqual([], source_claim_records(self.connection, recorded_at=BEFORE))
        self.assertEqual([], source_claim_records(self.connection, recorded_at=ADMITTED))
        rows = source_claim_records(self.connection, recorded_at=FIRST)
        self.assertEqual(["before"], [row["id"] for row in rows])
        self.assertIsNone(rows[0]["valid_from"])
        self.assertIsNone(rows[0]["confidence"])
        self.assertEqual(OBSERVED, rows[0]["evidence"][0]["retrieved_at"])
        self.assertEqual(FIRST, rows[0]["recorded_at"])
        self.assertEqual([], source_claim_records(
            self.connection, recorded_at=FIRST, subject_entity_id="not-fab2"))

    def test_null_effective_target_never_enters_physical_state(self):
        self._claim("target", self._period())
        for as_of in ("2026-01-01", "2026-09-07", "2029-01-01"):
            self.assertEqual([], claim_records(self.connection, as_of=as_of, recorded_at=SECOND))
            history = claim_history_records(self.connection, as_of=as_of, recorded_at=SECOND)
            self.assertEqual(["target"], [row["id"] for row in history])

    def test_supersession_cutoff_preserves_original_statement(self):
        self._claim("before", self._period())
        self._claim("after", self._period(2027, True), recorded=SECOND)
        first = source_claim_records(self.connection, recorded_at=FIRST)
        self.assertEqual(["before"], [row["id"] for row in first])
        self.assertIsNone(first[0]["superseded_at"])
        self.assertEqual(["after"], [row["id"] for row in source_claim_records(
            self.connection, recorded_at=SECOND)])
        history = claim_history_records(self.connection, as_of="2026-09-07", recorded_at=SECOND)
        self.assertEqual(["before", "after"], [row["id"] for row in history])
        self.assertEqual(SECOND, history[0]["superseded_at"])
        self.assertEqual("year", history[0]["value"]["date_precision"])
        self.assertEqual("half_year", history[1]["value"]["date_precision"])
        self.assertTrue(all(row["value"]["date_base"] is None for row in history))

    def test_mixed_effective_history_is_null_safe(self):
        self._claim("unknown", self._period())
        self._claim("known", self._period(2027, True), effective="2026-09-07", recorded=SECOND)
        history = claim_history_records(self.connection, as_of="2026-09-07", recorded_at=SECOND)
        self.assertEqual(["unknown", "known"], [row["id"] for row in history])

    def test_optional_metadata_does_not_change_legacy_value_shape(self):
        legacy = MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                                "2028-01-01", "2028-07-01", "2028-12-31")
        self._claim("legacy", legacy, effective="2026-09-07", confidence=0.8)
        value = claim_value(self.connection, "legacy", "milestone")
        self.assertEqual({"milestone_type", "status", "date_low", "date_base", "date_high"}, set(value))
        self._claim("period", self._period(), series="period")
        value = claim_value(self.connection, "period", "milestone")
        self.assertEqual("2028", value["date_literal"])
        self.assertEqual("year", value["date_precision"])

    def test_schema5_release_separates_source_claims_and_preserves_lineage(self):
        self._claim("before", self._period())
        self._claim("after", self._period(2027, True), recorded=SECOND)
        output, manifest = self._release()
        self.assertEqual(1, manifest["source_claims"])
        self.assertEqual(0, manifest["claims"])
        self.assertEqual(2, manifest["claim_history"])
        self.assertEqual(["after"], [row["id"] for row in self._jsonl(output / "source_claims.jsonl")])
        self.assertEqual([], self._jsonl(output / "claims.jsonl"))
        self.assertEqual(["fab2"], [row["entity_id"] for row in self._jsonl(output / "entities.jsonl")])
        inputs = json.loads((output / "source_inputs.json").read_text())
        self.assertEqual(ADMITTED, inputs[0]["database_accepted_at"])
        self.assertEqual(False, inputs[0]["metadata"]["raw_redistribution"])
        observations = self._jsonl(output / "source_observations.jsonl")
        self.assertEqual(OBSERVED, observations[0]["source_record_observed_at"])
        self.assertNotIn("raw_excerpt", observations[0])
        self.assertNotIn(b"local only", (output / "source_observations.jsonl").read_bytes())
        for filename, metadata in manifest["files"].items():
            self.assertEqual(metadata["sha256"], hashlib.sha256((output / filename).read_bytes()).hexdigest())
        other, other_manifest = self._release("rebuilt")
        self.assertEqual(manifest, other_manifest)
        for filename in manifest["files"]:
            self.assertEqual((output / filename).read_bytes(), (other / filename).read_bytes())

    def test_pre_admission_release_is_empty_even_after_later_import(self):
        self._claim("target", self._period())
        output, manifest = self._release(recorded=BEFORE)
        self.assertEqual(0, manifest["source_claims"])
        self.assertEqual(0, manifest["claim_history"])
        self.assertEqual(0, manifest["source_documents"])
        self.assertEqual([], self._jsonl(output / "source_claims.jsonl"))

    def test_future_effective_source_claim_lineage_is_exported(self):
        parent = self._claim("parent", ScalarValue(ScalarType.TEXT, "source wording"),
                             series="parent", effective="2028-01-01")
        self._claim("future", self._period(), effective="2028-01-01",
                    dependencies=[DependencyLink(parent.id)])
        output, manifest = self._release(as_of="2026-09-07")
        self.assertEqual(0, manifest["claims"])
        self.assertEqual(2, manifest["source_claims"])
        self.assertEqual({"parent", "future"}, {row["id"] for row in self._jsonl(output / "claim_history.jsonl")})

    def test_superseded_future_effective_source_history_is_retained(self):
        self._claim("before", self._period(), effective="2028-01-01")
        self._claim("after", self._period(2027, True), effective="2028-01-01", recorded=SECOND)
        output, manifest = self._release(as_of="2026-09-07")
        self.assertEqual(0, manifest["claims"])
        self.assertEqual(1, manifest["source_claims"])
        self.assertEqual({"before", "after"}, {
            row["id"] for row in self._jsonl(output / "claim_history.jsonl")})

    def test_schema4_release_keeps_legacy_shape(self):
        legacy, _ = initialize(self.root / "legacy.sqlite", target_version=4)
        self.addCleanup(legacy.close)
        self._seed(legacy)
        self._claim("legacy", MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                    "2028-01-01", "2028-07-01", "2028-12-31"),
                    effective="2026-09-07", confidence=0.8, connection=legacy)
        output, manifest = self._release(connection=legacy)
        self.assertEqual(4, manifest["schema_version"])
        self.assertNotIn("source_claims", manifest)
        self.assertFalse((output / "source_claims.jsonl").exists())
        self.assertNotIn("source_claims.jsonl", (output / "README.md").read_text())
        value = self._jsonl(output / "claims.jsonl")[0]["value"]
        self.assertNotIn("date_precision", value)
        self.assertNotIn("date_literal", value)

    def test_legacy_alerts_skip_unknown_confidence(self):
        for claim_id, year, recorded in (("before", 2028, FIRST), ("after", 2027, SECOND)):
            value = MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                                   f"{year}-01-01", f"{year}-07-01", f"{year}-12-31")
            self._claim(claim_id, value, effective="2026-09-07", recorded=recorded)
        self.assertEqual([], detect_revision_alerts(self.connection))

    def test_legacy_alerts_skip_missing_midpoint(self):
        self._claim("before", self._period(), effective="2026-09-07", confidence=0.8)
        self._claim("after", self._period(2027, True), effective="2026-09-07",
                    confidence=0.8, recorded=SECOND)
        self.assertEqual([], detect_revision_alerts(self.connection))

    def test_unknown_effective_target_change_is_not_a_legacy_completion_alert(self):
        self._claim("before", self._period())
        self._claim("after", self._period(2027, True), recorded=SECOND)
        self.assertEqual([], detect_revision_alerts(self.connection))
        self.assertEqual([], detect_revision_alerts(
            self.connection, as_of="2026-09-07", recorded_at=SECOND))

    def test_forecast_skips_source_period_midpoint(self):
        self._claim("target", self._period(), effective="2026-09-07", confidence=0.8)
        self._claim("capacity", CapacityValue("wafers", CapacityBasis.ANNOUNCED,
                    "wafers/month", 100, 100, 100), series="capacity",
                    effective="2026-09-07", confidence=0.8)
        results = forecast_current_capacity(self.connection, as_of="2026-09-07",
                    recorded_at=SECOND, forecast_start=date(2026, 10, 1))
        self.assertEqual(0, len(results))
        self.assertEqual("unsupported_linked_production_milestone", results.exclusions[0].reason)
        self.assertEqual(("target",), results.exclusions[0].related_claim_ids)

    def test_forecast_skips_unknown_milestone_confidence(self):
        self._claim("target", MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                    "2028-01-01", "2028-07-01", "2028-12-31"), effective="2026-09-07")
        self._claim("capacity", CapacityValue("wafers", CapacityBasis.ANNOUNCED,
                    "wafers/month", 100, 100, 100), series="capacity",
                    effective="2026-09-07", confidence=0.8)
        results = forecast_current_capacity(self.connection, as_of="2026-09-07",
                    recorded_at=SECOND, forecast_start=date(2026, 10, 1))
        self.assertEqual(0, len(results))
        self.assertEqual("unsupported_linked_production_milestone", results.exclusions[0].reason)

    def test_unsupported_linked_target_is_not_hidden_by_supported_target(self):
        self._claim("unknown", self._period(), effective="2026-09-07", confidence=0.8)
        self._claim("known", MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                    "2028-01-01", "2028-07-01", "2028-12-31"), series="other-target",
                    effective="2026-09-07", confidence=0.8)
        self._claim("capacity", CapacityValue("wafers", CapacityBasis.ANNOUNCED,
                    "wafers/month", 100, 100, 100), series="capacity",
                    effective="2026-09-07", confidence=0.8)
        results = forecast_current_capacity(self.connection, as_of="2026-09-07",
                    recorded_at=SECOND, forecast_start=date(2026, 10, 1))
        self.assertEqual(0, len(results))
        self.assertEqual(("unknown",), results.exclusions[0].related_claim_ids)

    def test_explicit_source_view_does_not_bypass_forecast_effective_time_guard(self):
        self._claim("capacity", CapacityValue("wafers", CapacityBasis.ECONOMICALLY_USABLE,
                    "wafers/month", 100, 100, 100), series="capacity", confidence=0.8)
        results = forecast_current_capacity(self.connection, as_of="2026-09-07",
                    recorded_at=SECOND, forecast_start=date(2026, 10, 1),
                    claims=source_claim_records(self.connection, recorded_at=SECOND))
        self.assertEqual(0, len(results))
        self.assertEqual("unknown_capacity_evidence_basis", results.exclusions[0].reason)

    def test_forecast_skips_unknown_capacity_confidence(self):
        self._claim("capacity", CapacityValue("wafers", CapacityBasis.ECONOMICALLY_USABLE,
                    "wafers/month", 100, 100, 100), series="capacity", effective="2026-09-07")
        results = forecast_current_capacity(self.connection, as_of="2026-09-07",
                    recorded_at=SECOND, forecast_start=date(2026, 10, 1))
        self.assertEqual(0, len(results))
        self.assertEqual("unknown_capacity_evidence_basis", results.exclusions[0].reason)


if __name__ == "__main__":
    unittest.main()
