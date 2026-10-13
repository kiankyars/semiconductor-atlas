"""Exact-subject dossiers preserve evidence semantics without mutating their inputs."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas.cli import main
from semiconductor_atlas.evidence_dossier import (
    build_evidence_dossier,
    json_bytes,
    markdown_bytes,
    write_dossier,
)
from semiconductor_atlas.source_quantities import add_metadata as add_quantity_metadata
from semiconductor_atlas.source_rates import add_metadata as add_rate_metadata
from tests.test_source_quantities import quantity, wafer_area
from tests.test_source_rates import WORDING, rate


CREATED = "2026-01-01T00:00:00Z"
ADMITTED = "2026-09-09T05:52:00.817388Z"
JUST_BEFORE = "2026-09-09T05:52:00.817387Z"
JUST_AFTER = "2026-09-09T05:52:00.817389Z"
LATER = "2026-09-10T05:52:00.817388Z"
AS_OF = "2026-09-09"


def ids(claims):
    return {claim["id"] for claim in claims}


class EvidenceDossierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Resolve macOS /var before testing explicit non-symlink output parents.
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "atlas.sqlite"
        # These retained-schema reader tests include impossible legacy lineage.
        # Schema-10 admission rejection is covered separately in exact-clock tests.
        self.c, _ = database.initialize(self.path, target_version=9)
        self.addCleanup(self.c.close)
        r.add_source_family(self.c, m.SourceFamily("family", "fixture-family", "Fixture", CREATED))
        r.add_source(self.c, m.Source("source", "family", "fixture-source", "Fixture", "Synthetic publisher",
                                    "https://example.test", CREATED))
        self.document("doc")
        for entity, kind in (("project", m.EntityKind.PROJECT), ("other", m.EntityKind.PROJECT),
                             ("company", m.EntityKind.ORGANIZATION), ("site", m.EntityKind.SITE),
                             ("facility", m.EntityKind.FACILITY)):
            r.add_entity(self.c, m.Entity(entity, kind, "fixture:" + entity, CREATED,
                                         "Synthetic " + entity))

    def document(self, identifier, **changes):
        fields = dict(id=identifier, source_id="source", document_url="https://example.test/" + identifier,
                      title="Synthetic evidence", retrieved_at=CREATED, content_sha256="a" * 64,
                      published_at=None)
        fields.update(changes)
        r.add_source_document(self.c, m.SourceDocument(**fields))

    def claim(self, identifier, *, entity="project", value=None, series=None, series_key=None, predicate=None,
              recorded=ADMITTED, valid_from=None, valid_to=None, kind=m.ClaimKind.SOURCE_STATEMENT,
              dependencies=(), evidence=None, run=None):
        value = value or m.ScalarValue(m.ScalarType.TEXT, "slowing")
        series = series or "series:" + identifier
        r.add_claim_series(self.c, m.ClaimSeries(series, entity, series_key or series, predicate or "source_reported." + identifier,
                                              value.kind, CREATED))
        r.insert_claim(self.c, m.ClaimVersion(identifier, series, valid_from, recorded, kind,
                                             "synthetic_review", None, valid_to=valid_to,
                                             created_by_run_id=run, notes="SECRET_CLAIM_NOTES"), value,
                       dependencies=[m.DependencyLink(parent) for parent in dependencies],
                       evidence=([] if dependencies else [m.EvidenceLink("doc", locator="bytes [1,20)",
                                                                         excerpt="SECRET_EXCERPT")])
                       if evidence is None else evidence)
        return identifier

    def add_run(self, identifier="run", *, completed=ADMITTED, accepted=ADMITTED,
            status=m.IngestionStatus.SUCCEEDED, input_document_id="doc"):
        r.add_ingestion_run(self.c, m.IngestionRun(identifier, "source", CREATED, status,
            completed_at=completed, input_document_id=input_document_id,
            code_version="synthetic-version", parameters={"accepted_at": accepted, "secret": "SECRET_RUN_PARAMETERS"}))
        return identifier

    def report(self, *, entity="project", recorded=ADMITTED, as_of=AS_OF):
        return build_evidence_dossier(self.c, subject_entity_id=entity, as_of=as_of, recorded_at=recorded)

    def cli(self, output, *, source=None, extra=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(["evidence-dossier", "--database", str(source or self.path),
                           "--entity-id", "project", "--as-of", AS_OF, "--recorded-at", ADMITTED,
                           "--output", str(output), *extra])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_dated_source_is_not_world_current_and_estimate_is_labeled(self):
        self.claim("source", valid_from="2026-01-01")
        self.claim("observed", valid_from="2026-01-01", kind=m.ClaimKind.DIRECT_OBSERVATION)
        self.claim("estimated", valid_from="2026-01-01", kind=m.ClaimKind.DERIVED_ESTIMATE,
                   dependencies=["source"])
        report = self.report()
        self.assertEqual(ids(report["source_statements"]), {"source"})
        self.assertEqual(ids(report["world_current_claims"]), {"observed", "estimated"})
        self.assertEqual(ids(report["recorded_history"]), {"source", "observed", "estimated"})
        estimate = next(c for c in report["world_current_claims"] if c["id"] == "estimated")
        self.assertEqual(estimate["claim_kind"], "derived_estimate")
        self.assertIsNone(estimate["confidence"])
        self.assertIn("derived\\_estimate", markdown_bytes(report).decode())

    def test_unknown_and_future_effective_source_statements_use_exact_knowledge_time(self):
        self.claim("unknown")
        self.claim("future", valid_from="2028-01-01")
        self.claim("future-physical", valid_from="2028-01-01", kind=m.ClaimKind.DIRECT_OBSERVATION)
        early = self.report(recorded=JUST_BEFORE)
        self.assertEqual(early["recorded_history"], [])
        admitted = self.report()
        self.assertEqual(ids(admitted["source_statements"]), {"unknown", "future"})
        self.assertEqual(ids(admitted["recorded_history"]), {"unknown", "future"})
        self.assertEqual(admitted["world_current_claims"], [])
        self.assertEqual(next(c for c in admitted["source_statements"] if c["id"] == "future")["valid_from"],
                         "2028-01-01")

    def test_world_expiry_is_exclusive_but_source_and_recorded_history_are_preserved(self):
        self.claim("expired-world", valid_from="2026-01-01", valid_to=AS_OF,
                   kind=m.ClaimKind.DIRECT_OBSERVATION)
        self.claim("expired-source", valid_from="2026-01-01", valid_to=AS_OF)
        report = self.report()
        self.assertEqual(report["world_current_claims"], [])
        self.assertEqual(ids(report["source_statements"]), {"expired-source"})
        self.assertEqual(ids(report["recorded_history"]), {"expired-world", "expired-source"})

    def test_supersession_history_masks_future_knowledge_to_the_microsecond(self):
        self.claim("original", series="revised", recorded="2026-09-08T05:52:00.817388Z")
        self.claim("revision", series="revised", predicate="source_reported.original",
                   recorded=ADMITTED, value=m.ScalarValue(m.ScalarType.TEXT, "reaffirmed"))
        early = self.report(recorded=JUST_BEFORE)
        self.assertEqual(ids(early["recorded_history"]), {"original"})
        self.assertIsNone(early["recorded_history"][0]["superseded_at"])
        later = self.report()
        self.assertEqual(ids(later["source_statements"]), {"revision"})
        self.assertEqual(ids(later["recorded_history"]), {"original", "revision"})
        self.assertEqual(next(c for c in later["recorded_history"] if c["id"] == "original")["superseded_at"],
                         ADMITTED)

    def test_exact_entity_does_not_merge_similar_subjects_and_dependencies_remain_context(self):
        self.claim("ancestor", entity="company")
        self.claim("parent", entity="other", dependencies=["ancestor"])
        self.claim("unrelated", entity="other")
        self.claim("root", dependencies=["parent"], valid_from="2026-01-01",
                   kind=m.ClaimKind.DERIVED_ESTIMATE)
        report = self.report()
        self.assertEqual(ids(report["world_current_claims"]), {"root"})
        self.assertEqual(ids(report["recorded_history"]), {"root"})
        self.assertEqual(ids(report["dependency_context"]), {"ancestor", "parent"})
        self.assertEqual(report["source_statements"], [])
        self.assertEqual(report["entity"]["id"], "project")
        self.assertNotIn("unrelated", json_bytes(report).decode())

    def test_same_entity_future_effective_dependency_is_context_not_selected_history(self):
        self.claim("future-parent", valid_from="2028-01-01", kind=m.ClaimKind.DIRECT_OBSERVATION)
        self.claim("root", dependencies=["future-parent"], valid_from="2026-01-01",
                   kind=m.ClaimKind.DERIVED_ESTIMATE)
        report = self.report()
        self.assertEqual(ids(report["world_current_claims"]), {"root"})
        self.assertEqual(ids(report["recorded_history"]), {"root"})
        self.assertEqual(ids(report["dependency_context"]), {"future-parent"})

    def test_transitive_dependency_admitted_after_cutoff_is_rejected(self):
        # The legacy insertion validator rounds to milliseconds; the reader must not.
        self.claim("future-parent", entity="other", recorded=JUST_AFTER)
        self.claim("root", dependencies=["future-parent"])
        with self.assertRaisesRegex(ValueError, "claim admission"):
            self.report()

    def test_evidence_retrieved_one_microsecond_after_cutoff_is_rejected(self):
        self.document("future-evidence", retrieved_at=JUST_AFTER)
        self.claim("source", evidence=[m.EvidenceLink("future-evidence")])
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            self.report()

    def test_referenced_relationship_entity_cannot_be_future_knowledge(self):
        r.add_entity(self.c, m.Entity("future-target", m.EntityKind.PROJECT, "fixture:future-target", JUST_AFTER))
        self.claim("relationship", value=m.RelationshipValue("future-target", "part_of"))
        with self.assertRaises(ValueError):
            self.report()

    def test_referenced_constraint_entity_cannot_be_future_knowledge(self):
        r.add_entity(self.c, m.Entity("future-target", m.EntityKind.PROJECT, "fixture:future-target", JUST_AFTER))
        self.claim("constraint", value=m.ConstraintValue("water", m.ConstraintStatus.POTENTIAL,
                   m.ConstraintSeverity.UNKNOWN, "Synthetic water constraint", "future-target"))
        with self.assertRaises(ValueError):
            self.report()

    def test_referenced_entity_creation_run_must_be_available_at_cutoff(self):
        self.add_run(accepted=JUST_AFTER)
        r.add_entity(self.c, m.Entity("target", m.EntityKind.PROJECT, "fixture:target", CREATED,
                                     created_by_run_id="run"))
        self.claim("relationship", value=m.RelationshipValue("target", "part_of"))
        with self.assertRaises(ValueError):
            self.report()

    def test_corrupted_typed_hash_fails_closed_without_repairing_database(self):
        self.claim("source")
        # Deliberate corruption is limited to this disposable fixture.
        self.c.execute("DROP TRIGGER scalar_values_immutable_update")
        self.c.execute("UPDATE scalar_values SET text_value='tampered' WHERE claim_version_id='source'")
        before = list(self.c.iterdump())
        with self.assertRaisesRegex(ValueError, "hash"):
            self.report()
        self.assertEqual(before, list(self.c.iterdump()))

    def test_corrupted_cycle_is_rejected_without_hanging(self):
        self.claim("ancestor", entity="other")
        self.claim("root", dependencies=["ancestor"])
        self.c.execute("DROP TRIGGER claim_dependencies_no_cycle")
        self.c.execute("INSERT INTO claim_dependencies VALUES ('ancestor','root','derived_from')")
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.report()

    def test_empty_subject_retains_unknown_not_zero_interpretation(self):
        report = self.report()
        self.assertEqual(report["recorded_history"], [])
        self.assertEqual(report["evidence_summary"]["claim_count_including_dependencies"], 0)
        self.assertIn("not evidence of zero capacity", markdown_bytes(report).decode())

    def test_unknown_subject_and_subject_created_after_cutoff_fail_without_future_label(self):
        r.add_entity(self.c, m.Entity("future-entity", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER,
                                     "SECRET_FUTURE_LABEL"))
        for entity in ("missing", "fixture:project", "future-entity", "", " "):
            with self.subTest(entity=entity), self.assertRaises(ValueError) as error:
                self.report(entity=entity)
            self.assertNotIn("SECRET_FUTURE_LABEL", str(error.exception))

    def test_timezone_offsets_are_normalized_and_render_identically(self):
        self.claim("source")
        a = self.report()
        b = self.report(recorded="2026-09-08T22:52:00.817388-07:00")
        self.assertEqual(json_bytes(a), json_bytes(b))
        self.assertEqual(markdown_bytes(a), markdown_bytes(b))

    def test_invalid_dates_and_naive_knowledge_clock_are_rejected(self):
        for as_of in ("20260909", "2026-9-9", "2026-02-30", "2026-09-09T00:00:00Z"):
            with self.subTest(as_of=as_of), self.assertRaises(ValueError):
                self.report(as_of=as_of)
        for recorded in ("2026-09-09", "2026-09-09T05:52:00", "not-a-date"):
            with self.subTest(recorded=recorded), self.assertRaises(ValueError):
                self.report(recorded=recorded)

    def test_run_admission_completion_and_status_are_enforced_including_microseconds(self):
        for name, kwargs in (("accepted-future", {"accepted": JUST_AFTER}),
                             ("completed-future", {"completed": JUST_AFTER}),
                             ("failed", {"status": m.IngestionStatus.FAILED}),
                             ("running", {"status": m.IngestionStatus.RUNNING, "completed": None})):
            self.add_run(name, **kwargs)
            r.add_entity(self.c, m.Entity(name, m.EntityKind.PROJECT, "fixture:" + name, CREATED))
            self.claim("claim:" + name, entity=name, run=name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.report(entity=name)

    def test_succeeded_run_clocks_are_exposed_without_parameter_payload(self):
        self.add_run()
        self.claim("source", run="run")
        report = self.report()
        self.assertEqual(report["ingestion_runs"], [{"id": "run", "source_id": "source", "started_at": CREATED,
            "completed_at": ADMITTED, "status": "succeeded", "code_version": "synthetic-version",
            "accepted_at": ADMITTED}])
        self.assertNotIn("SECRET_RUN_PARAMETERS", json_bytes(report).decode())

    def test_run_source_and_family_clocks_are_checked_without_documents(self):
        for field in ("source", "family"):
            family, source, run, subject = (field + suffix for suffix in ("-family", "-source", "-run", "-subject"))
            r.add_source_family(self.c, m.SourceFamily(family, family, "Fixture",
                JUST_AFTER if field == "family" else CREATED))
            r.add_source(self.c, m.Source(source, family, source, "Fixture", "Publisher",
                "https://example.test", JUST_AFTER if field == "source" else CREATED))
            r.add_ingestion_run(self.c, m.IngestionRun(run, source, CREATED, m.IngestionStatus.SUCCEEDED,
                completed_at=ADMITTED))
            r.add_entity(self.c, m.Entity(subject, m.EntityKind.PROJECT, subject, CREATED,
                created_by_run_id=run))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "run " + field + "_created"):
                self.report(entity=subject)

    def test_source_record_run_is_checked_even_when_claim_has_no_run(self):
        self.add_run(accepted=JUST_AFTER)
        payload = {"body": "SECRET_SOURCE_RECORD_PAYLOAD"}
        r.add_source_record(self.c, m.SourceRecord("record", "run", "doc", "fixture:record", CREATED,
                            m.source_record_payload_sha256(payload), payload))
        self.claim("source", evidence=[m.EvidenceLink("doc", source_record_id="record")])
        with self.assertRaisesRegex(ValueError, "admission"):
            self.report()
        # A later cutoff cannot make an admission after run completion consistent.
        with self.assertRaisesRegex(ValueError, "processing interval"):
            self.report(recorded=JUST_AFTER)

    def test_explicit_run_acceptance_rejects_null_naive_invalid_and_non_timestamp_values(self):
        for index, accepted in enumerate((None, "2026-09-09T05:52:00.817388", "not-a-date", 0,
                                         "0001-01-01T00:00:00+01:00")):
            identifier = "invalid-acceptance-" + str(index)
            self.add_run(identifier, accepted=accepted)
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED))
            self.claim(identifier, entity=identifier, run=identifier)
            before = list(self.c.iterdump())
            with self.subTest(accepted=accepted), self.assertRaisesRegex(ValueError, "invalid explicit accepted_at"):
                self.report(entity=identifier)
            self.assertEqual(list(self.c.iterdump()), before)

    def test_explicit_run_acceptance_rejects_one_microsecond_outside_processing_interval(self):
        for identifier, accepted in (("before-start", "2025-12-31T23:59:59.999999Z"),
                                      ("after-completion", JUST_AFTER)):
            self.add_run(identifier, accepted=accepted)
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED))
            self.claim(identifier, entity=identifier, run=identifier)
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "processing interval"):
                self.report(entity=identifier, recorded=LATER)

    def test_run_acceptance_can_be_absent_or_equal_to_either_processing_boundary(self):
        options = (("absent", {}), ("start", {"accepted_at": CREATED}),
            ("end", {"accepted_at": ADMITTED}),
            ("just-inside", {"accepted_at": JUST_BEFORE}),
            ("offset-end", {"accepted_at": "2026-09-08T22:52:00.817388-07:00"}))
        for identifier, parameters in options:
            r.add_ingestion_run(self.c, m.IngestionRun(identifier, "source", CREATED, m.IngestionStatus.SUCCEEDED,
                completed_at=ADMITTED, input_document_id="doc", parameters=parameters))
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED))
            self.claim(identifier, entity=identifier, run=identifier)
            with self.subTest(identifier=identifier):
                report = self.report(entity=identifier)
                self.assertEqual(report["ingestion_runs"][0]["accepted_at"], parameters.get("accepted_at"))
                self.assertEqual(ids(report["source_statements"]), {identifier})

    def test_schema10_microsecond_run_acceptance_preserves_offset_literal(self):
        self.c.commit()
        database.apply_migrations(self.c, target_version=10)
        accepted = "2026-09-08T22:52:00.817389-07:00"
        r.add_ingestion_run(self.c, m.IngestionRun("run", "source", ADMITTED, m.IngestionStatus.SUCCEEDED,
            completed_at=JUST_AFTER, input_document_id="doc", parameters={"accepted_at": accepted}))
        self.claim("source", recorded=JUST_AFTER, run="run")
        report = self.report(recorded=JUST_AFTER)
        self.assertEqual(report["ingestion_runs"][0]["accepted_at"], accepted)
        self.assertEqual(ids(report["source_statements"]), {"source"})

    def test_source_record_run_becomes_available_at_valid_acceptance_and_completion(self):
        self.add_run(accepted=JUST_AFTER, completed=JUST_AFTER)
        payload = {"body": "SECRET_SOURCE_RECORD_PAYLOAD"}
        r.add_source_record(self.c, m.SourceRecord("record", "run", "doc", "fixture:record", CREATED,
            m.source_record_payload_sha256(payload), payload))
        self.claim("source", evidence=[m.EvidenceLink("doc", source_record_id="record")])
        with self.assertRaisesRegex(ValueError, "run completed_at"):
            self.report()
        later = self.report(recorded=JUST_AFTER)
        self.assertNotIn("SECRET_SOURCE_RECORD_PAYLOAD", json_bytes(later).decode())
        self.assertEqual(later["source_statements"][0]["evidence"][0]["source_record_id"], "record")

    def test_run_auxiliary_document_clocks_are_checked(self):
        self.document("future-document", retrieved_at=JUST_AFTER)
        self.add_run()
        r.add_ingestion_run_document(self.c, m.IngestionRunDocument("run", "future-document", "auxiliary"))
        self.claim("source", run="run")
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            self.report()

    def test_publication_cannot_postdate_retrieval(self):
        self.document("bad-publication", published_at="2027-01-01")
        self.claim("source", evidence=[m.EvidenceLink("bad-publication")])
        with self.assertRaisesRegex(ValueError, "publication"):
            self.report()

    def test_refutation_only_terminal_lineage_cannot_certify_support(self):
        self.claim("refuted", evidence=[m.EvidenceLink("doc", role=m.EvidenceRole.REFUTE)])
        with self.assertRaisesRegex(ValueError, "supporting evidence"):
            self.report()

    def test_typed_milestone_keeps_event_precision_status_and_unknown_midpoint(self):
        value = m.MilestoneValue("production_commencement", m.MilestoneStatus.COMPLETED,
                                "2024-01-01", None, "2024-12-31", "year", "2024")
        self.claim("milestone", value=value)
        report = self.report()
        self.assertEqual(report["source_statements"][0]["value"], asdict(value) | {"status": "completed"})
        self.assertEqual(report["world_current_claims"], [])
        rendered = markdown_bytes(report).decode()
        self.assertIn("production\\_commencement", rendered)
        self.assertIn("| date\\_base | null (unknown or not applicable) |", rendered)

    def test_source_quantity_preserves_scope_fiscal_period_units_and_qualifiers(self):
        for metadata, entity in ((quantity(), "company"), (wafer_area(claim_version_id="area"), "site")):
            self.claim(metadata.claim_version_id, entity=entity, value=metadata.scalar(),
                       predicate="source_quantity." + metadata.metric,
                       series=r.stable_id(metadata.series_key(entity)), series_key=metadata.series_key(entity))
            add_quantity_metadata(self.c, metadata)
            report = self.report(entity=entity)
            value = report["source_statements"][0]["value"]
            expected = asdict(metadata)
            expected.pop("claim_version_id")
            self.assertEqual({key: value["source_quantity"][key] for key in expected}, expected)
            self.assertIsNone(value["source_quantity"]["physical_capacity_basis"])
            self.assertEqual(report["world_current_claims"], [])
        self.assertEqual(value["source_quantity"]["period_kind"], "fiscal_year")
        self.assertEqual(value["source_quantity"]["source_period_label"], "Geschäftsjahr 2023/24")
        self.assertEqual(value["unit"], "square_centimeters")

    def test_source_rate_keeps_native_units_denominator_unknowns_but_omits_verbatim_wording(self):
        metadata = rate()
        self.claim("rate", entity="facility", value=metadata.scalar(),
                   predicate="source_rate." + metadata.metric, series=r.stable_id(metadata.series_key("facility")),
                   series_key=metadata.series_key("facility"))
        add_rate_metadata(self.c, metadata)
        report = self.report(entity="facility")
        value = report["source_statements"][0]["value"]
        self.assertEqual(value["value"], 21000)
        self.assertEqual(value["unit"], "native_wafers_per_month")
        expected = asdict(metadata)
        expected.pop("claim_version_id")
        expected.pop("source_wording")
        self.assertEqual({key: value["source_rate"][key] for key in expected}, expected)
        self.assertIsNone(value["source_rate"]["native_wafer_diameter_inches"])
        self.assertIsNone(value["source_rate"]["physical_capacity_basis"])
        self.assertNotIn("source_wording", value["source_rate"])
        for raw in (json_bytes(report), markdown_bytes(report)):
            for secret in (WORDING, "SECRET_CLAIM_NOTES", "SECRET_EXCERPT"):
                self.assertNotIn(secret, raw.decode())

    def test_missing_required_source_rate_metadata_fails_closed(self):
        metadata = rate()
        self.claim("rate", entity="facility", value=metadata.scalar(),
                   predicate="source_rate." + metadata.metric, series=r.stable_id(metadata.series_key("facility")),
                   series_key=metadata.series_key("facility"))
        with self.assertRaises(ValueError):
            self.report(entity="facility")

    def test_same_family_documents_do_not_count_as_independent_corroboration(self):
        self.document("doc2")
        self.claim("source", evidence=[m.EvidenceLink("doc"), m.EvidenceLink("doc2")])
        summary = self.report()["evidence_summary"]
        self.assertEqual(summary["source_document_count"], 2)
        self.assertEqual(summary["source_family_count"], 1)
        self.assertEqual(summary["independent_corroboration"], "not_assessed")

    def test_markdown_escapes_hostile_text_and_only_links_safe_absolute_urls(self):
        self.claim("source", value=m.ScalarValue(m.ScalarType.TEXT, "<script>alert(1)</script>\n# INJECTED | x"))
        report = self.report()
        claim = report["source_statements"][0]
        # External metadata can be hostile regardless of the database admission URL validator.
        for url in ("javascript:alert(1)", "data:text/html,<svg/onload=alert(1)>",
                    "https://user:secret@example.test", "https://:secret@example.test"):
            with self.subTest(url=url):
                claim["evidence"][0]["document_url"] = url
                rendered = markdown_bytes(report).decode()
                self.assertNotIn("](<" + url, rendered)
                self.assertNotIn("<script>", rendered)
                self.assertNotIn("\n# INJECTED", rendered)
        claim["evidence"][0]["document_url"] = "https://example.test/a>(b)\n<script>"
        rendered = markdown_bytes(report).decode()
        self.assertIn("https://example.test/a%3E%28b%29%0A%3Cscript%3E", rendered)
        self.assertNotIn("<script>", rendered)

    def test_serialization_is_deterministic_and_does_not_include_generation_clock(self):
        for identifier in ("z", "a", "middle"):
            self.claim(identifier)
        report = self.report()
        self.assertEqual(json_bytes(report), json_bytes(self.report()))
        self.assertEqual(markdown_bytes(report), markdown_bytes(self.report()))
        self.assertEqual(json.loads(json_bytes(report))["recorded_at"], ADMITTED)
        self.assertNotIn("generated_at", report)
        self.assertTrue(json_bytes(report).endswith(b"\n"))

    def test_caller_transaction_preserved_on_success_and_failure(self):
        self.c.commit()
        self.c.execute("BEGIN")
        self.claim("uncommitted")
        self.assertTrue(self.c.in_transaction)
        before = self.c.total_changes
        report = self.report()
        self.assertEqual(ids(report["source_statements"]), {"uncommitted"})
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.total_changes, before)
        with self.assertRaises(ValueError):
            self.report(entity="missing")
        self.assertTrue(self.c.in_transaction)
        self.c.rollback()
        self.assertEqual(self.c.execute("SELECT count(*) FROM claim_versions").fetchone()[0], 0)

    def test_old_schema_is_not_migrated(self):
        old, _ = database.initialize(self.root / "old.sqlite", target_version=4)
        self.addCleanup(old.close)
        before = list(old.iterdump())
        with self.assertRaisesRegex(ValueError, "schema 5"):
            build_evidence_dossier(old, subject_entity_id="missing", as_of=AS_OF, recorded_at=ADMITTED)
        self.assertEqual(before, list(old.iterdump()))
        self.assertEqual(database.schema_version(old), 4)

    def test_cli_writes_markdown_and_json_with_hashes_without_modifying_database(self):
        self.claim("source")
        self.c.commit()
        before = self.path.read_bytes()
        for format_ in ("markdown", "json"):
            with self.subTest(format_=format_):
                output = self.root / ("report." + format_)
                status, stdout, stderr = self.cli(output, extra=["--format", format_])
                self.assertEqual((status, stderr), (0, ""))
                receipt = json.loads(stdout)
                self.assertEqual(receipt["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
                self.assertEqual(receipt["bytes"], output.stat().st_size)
                self.assertEqual(receipt["source_statements"], 1)
                self.assertEqual(receipt["world_current_claims"], 0)
                self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + "-journal").exists())

    def test_cli_missing_database_does_not_create_it_or_output(self):
        missing = self.root / "not-created" / "missing.sqlite"
        output = self.root / "output.md"
        status, _, _ = self.cli(output, source=missing)
        self.assertEqual(status, 1)
        self.assertFalse(missing.parent.exists())
        self.assertFalse(output.exists())

    def test_cli_reads_committed_wal_without_mutating_source_files(self):
        self.c.commit()
        self.assertEqual(self.c.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        self.claim("wal-source")
        self.c.commit()
        wal = Path(str(self.path) + "-wal")
        before_database, before_wal = self.path.read_bytes(), wal.read_bytes()
        output = self.root / "wal-report.json"
        status, stdout, stderr = self.cli(output, extra=["--format", "json"])
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["source_statements"], 1)
        self.assertEqual(ids(json.loads(output.read_text())["source_statements"]), {"wal-source"})
        self.assertEqual(before_database, self.path.read_bytes())
        self.assertEqual(before_wal, wal.read_bytes())

    def test_cli_cannot_create_input_database_sidecars_as_reports(self):
        self.claim("source")
        self.c.commit()
        before = self.path.read_bytes()
        for suffix in ("-wal", "-shm", "-journal"):
            output = Path(str(self.path) + suffix)
            with self.subTest(suffix=suffix):
                self.assertFalse(output.exists())
                status, _, stderr = self.cli(output)
                self.assertEqual(status, 1)
                self.assertIn("sidecars", stderr)
                self.assertFalse(output.exists())
                self.assertEqual(before, self.path.read_bytes())

    def test_cli_refuses_existing_output_including_database_and_symlink_targets(self):
        self.claim("source")
        self.c.commit()
        existing = self.root / "existing.md"
        existing.write_bytes(b"DO NOT OVERWRITE")
        symlink = self.root / "link.md"
        symlink.symlink_to(existing)
        dangling = self.root / "dangling.md"
        dangling.symlink_to(self.root / "absent.md")
        before_database = self.path.read_bytes()
        for output in (existing, self.path, symlink, dangling):
            with self.subTest(output=output):
                status, _, _ = self.cli(output)
                self.assertEqual(status, 1)
                self.assertEqual(existing.read_bytes(), b"DO NOT OVERWRITE")
                self.assertEqual(self.path.read_bytes(), before_database)
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse((self.root / "absent.md").exists())

    def test_cli_rejects_database_symlink_and_output_symlink_parent(self):
        self.claim("source")
        self.c.commit()
        source_link = self.root / "database-link.sqlite"
        source_link.symlink_to(self.path)
        output = self.root / "out.md"
        self.assertEqual(self.cli(output, source=source_link)[0], 1)
        self.assertFalse(output.exists())
        real_directory = self.root / "real-output"
        real_directory.mkdir()
        directory_link = self.root / "linked-output"
        directory_link.symlink_to(real_directory, target_is_directory=True)
        self.assertEqual(self.cli(directory_link / "out.md")[0], 1)
        self.assertEqual(list(real_directory.iterdir()), [])

    def test_write_dossier_rejects_invalid_format_without_output(self):
        self.c.commit()
        output = self.root / "out.html"
        with self.assertRaises(ValueError):
            write_dossier(self.path, output, subject_entity_id="project", as_of=AS_OF,
                          recorded_at=ADMITTED, output_format="html")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
