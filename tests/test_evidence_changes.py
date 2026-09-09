"""Knowledge events preserve complete accounting without certifying physical change."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas.cli import main
from semiconductor_atlas.evidence_changes import evidence_changes
from semiconductor_atlas.evidence_dossier import readonly_database
from tests import test_evidence_dossier as fixtures


CREATED, T, BEFORE, AFTER, LATER = (fixtures.CREATED, fixtures.ADMITTED,
    fixtures.JUST_BEFORE, fixtures.JUST_AFTER, fixtures.LATER)
AS_OF = fixtures.AS_OF


class EvidenceChangesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EvidenceDossierTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.c

    def claim(self, *args, **kwargs):
        return self.fixture.claim(*args, **kwargs)

    def feed(self, **changes):
        options = dict(since=CREATED, recorded_at=T, as_of=AS_OF)
        options.update(changes)
        return evidence_changes(self.c, **options)

    def event_keys(self, report):
        return [(e["event_type"], e["claim"]["id"]) for e in report["events"]]

    def test_empty_inventory_is_not_a_no_change_or_zero_capacity_assertion(self):
        report = self.feed()
        self.assertEqual(report["total_events"], 0)
        self.assertEqual(report["events"], [])
        self.assertEqual(report["distinct_subjects"], 0)
        self.assertEqual(report["event_counts"], dict(claim_admitted=0, claim_superseded=0))
        self.assertTrue(any("not no real-world change" in b for b in report["boundaries"]))
        self.assertIsNone(report["next_offset"])

    def test_exact_exclusive_start_and_inclusive_end(self):
        for identifier, recorded in (("start", BEFORE), ("end", T), ("future", AFTER)):
            self.claim(identifier, recorded=recorded)
        report = self.feed(since=BEFORE)
        self.assertEqual(self.event_keys(report), [("claim_admitted", "end")])
        advanced = self.feed(since=T, recorded_at=AFTER)
        self.assertEqual(self.event_keys(advanced), [("claim_admitted", "future")])
        self.assertNotEqual(report["events"][0]["event_id"], advanced["events"][0]["event_id"])

    def test_order_crosses_whole_second_and_normalizes_timezone_offsets(self):
        self.claim("whole", recorded="2026-09-09T05:52:00Z")
        self.claim("fraction", recorded="2026-09-08T22:52:00.000001-07:00")
        self.assertEqual([e["claim"]["id"] for e in self.feed()["events"]], ["whole", "fraction"])
        self.assertEqual(self.feed(), self.feed(recorded_at="2026-09-08T22:52:00.817388-07:00"))

    def test_same_instant_admission_and_supersession_are_distinct_ordered_events(self):
        self.claim("old", series="revised", recorded="2026-09-08T00:00:00Z")
        self.claim("new", series="revised", predicate="source_reported.old")
        report = self.feed()
        self.assertEqual(self.event_keys(report), [("claim_admitted", "old"),
            ("claim_admitted", "new"), ("claim_superseded", "old")])
        self.assertEqual(report["distinct_claims"], 2)
        self.assertEqual(report["distinct_subjects"], 1)
        self.assertEqual(report["event_counts"], dict(claim_admitted=2, claim_superseded=1))
        self.assertEqual(len({e["event_id"] for e in report["events"]}), 3)
        self.assertNotIn("replacement_claim_id", json.dumps(report))

    def test_old_claim_supersession_is_retained_even_when_its_admission_precedes_window(self):
        self.claim("old", series="revised", recorded=CREATED)
        self.claim("new", series="revised", predicate="source_reported.old")
        report = self.feed(since=BEFORE)
        self.assertEqual(self.event_keys(report), [("claim_admitted", "new"), ("claim_superseded", "old")])
        self.assertEqual(report["events"][1]["claim"]["recorded_at"], CREATED)

    def test_future_supersession_and_replacement_do_not_leak_or_change_prior_digest(self):
        self.claim("old", series="revised", recorded="2026-09-08T00:00:00Z")
        before = self.feed()
        self.claim("new", series="revised", predicate="source_reported.old", recorded=AFTER)
        self.assertEqual(self.feed(), before)
        self.assertIsNone(before["events"][0]["claim"]["superseded_at"])

    def test_recurrence_is_not_deduplicated_by_value_or_subject(self):
        self.claim("first", recorded=BEFORE)
        self.claim("repeat")
        self.assertEqual(self.feed()["total_events"], 2)

    def test_world_date_does_not_hide_late_arrivals_future_claims_or_expiry(self):
        self.claim("unknown")
        self.claim("expired", kind=m.ClaimKind.DIRECT_OBSERVATION,
                   valid_from="2024-01-01", valid_to="2025-01-01")
        self.claim("future", kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from="2028-01-01")
        self.claim("historical-source", valid_from="2020-01-01", valid_to="2021-01-01")
        report = self.feed(as_of="2019-01-01")
        self.assertEqual(report["total_events"], 4)
        self.assertEqual(self.event_keys(report), self.event_keys(self.feed(as_of="2030-01-01")))
        self.assertTrue(all(e["dossier_selection"]["as_of"] == "2019-01-01" for e in report["events"]))
        unknown = next(e for e in report["events"] if e["claim"]["id"] == "unknown")
        self.assertIsNone(unknown["claim"]["valid_from"])
        self.assertIsNone(unknown["claim"]["confidence"])

    def test_exact_subject_and_kind_filters_do_not_merge_duplicate_labels(self):
        self.claim("p", entity="project")
        self.claim("o", entity="other")
        self.claim("f", entity="facility")
        self.assertEqual(self.feed(kind="project")["distinct_subjects"], 2)
        self.assertEqual(self.event_keys(self.feed(subject_entity_id="project")), [("claim_admitted", "p")])
        self.assertEqual(self.feed(subject_entity_id="missing")["total_events"], 0)
        self.assertEqual(self.feed(kind="facility", subject_entity_id="project")["total_events"], 0)

    def test_stable_pagination_retains_complete_population_counts_and_ids(self):
        for identifier in ("z", "a", "c", "b", "x"):
            self.claim(identifier)
        first = self.feed(limit=2)
        second = self.feed(limit=2, offset=first["next_offset"], population_sha256=first["population_sha256"])
        third = self.feed(limit=2, offset=second["next_offset"], population_sha256=first["population_sha256"])
        self.assertEqual(first["events"] + second["events"] + third["events"], self.feed()["events"])
        for page in (first, second, third):
            self.assertEqual(page["total_events"], 5)
            self.assertEqual(page["population_sha256"], first["population_sha256"])
        self.assertIsNone(third["next_offset"])
        outside = self.feed(offset=100, population_sha256=first["population_sha256"])
        self.assertEqual((outside["returned_events"], outside["total_events"]), (0, 5))

    def test_backdated_insert_before_or_after_page_cursor_rejects_old_digest(self):
        self.claim("middle")
        first = self.feed(limit=1)
        for identifier in ("a-before", "z-after"):
            self.claim(identifier)
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "inventory or selectors changed"):
                self.feed(offset=1, population_sha256=first["population_sha256"])
            first = self.feed(limit=1)

    def test_population_digest_binds_all_selectors_but_not_page_size(self):
        self.claim("source")
        digest = self.feed()["population_sha256"]
        self.assertEqual(self.feed(limit=1)["population_sha256"], digest)
        for change in (dict(since=BEFORE), dict(recorded_at=AFTER), dict(as_of="2026-09-08"),
                       dict(kind="project"), dict(subject_entity_id="project")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "selectors changed"):
                self.feed(population_sha256=digest, **change)

    def test_backdated_insert_outside_window_is_an_explicit_cursor_limitation(self):
        self.claim("selected")
        before = self.feed(since=BEFORE)
        self.claim("backdated", recorded=CREATED)
        self.assertEqual(self.feed(since=BEFORE), before)
        self.assertTrue(any("cannot discover later backdated writes" in b for b in before["boundaries"]))

    def test_deleted_selected_row_rejects_old_digest_without_inventing_deletion_event(self):
        self.claim("first")
        self.claim("second")
        digest = self.feed()["population_sha256"]
        self.c.commit()
        self.c.execute("PRAGMA foreign_keys=OFF")
        self.c.execute("DROP TRIGGER claim_versions_immutable_delete")
        self.c.execute("DELETE FROM claim_versions WHERE id='first'")
        with self.assertRaisesRegex(ValueError, "inventory or selectors changed"):
            self.feed(offset=1, population_sha256=digest)
        self.assertEqual(self.event_keys(self.feed()), [("claim_admitted", "second")])

    def test_population_digest_is_not_a_commitment_to_all_provenance_bytes(self):
        self.claim("source")
        before = self.feed()
        self.fixture.document("extra")
        self.c.execute("INSERT INTO claim_evidence (id,claim_version_id,source_document_id,role) "
                       "VALUES ('extra-link','source','extra','support')")
        after = self.feed(population_sha256=before["population_sha256"])
        self.assertEqual(before["population_sha256"], after["population_sha256"])
        self.assertNotEqual(before["events"], after["events"])
        self.assertTrue(any("not all database bytes or provenance" in b for b in after["boundaries"]))

    def test_selected_page_lineage_errors_do_not_hide_event_from_population(self):
        self.claim("a-good")
        self.claim("z-unsupported", evidence=[m.EvidenceLink("doc", role=m.EvidenceRole.REFUTE)])
        first = self.feed(limit=1)
        self.assertEqual(first["total_events"], 2)
        with self.assertRaisesRegex(ValueError, "no supporting evidence"):
            self.feed(limit=1, offset=1, population_sha256=first["population_sha256"])

    def test_creator_run_completion_after_admission_rejects_then_succeeds_with_later_upper_cutoff(self):
        self.fixture.add_run(completed=AFTER)
        self.claim("pending", run="run")
        with self.assertRaisesRegex(ValueError, "run completed_at"):
            self.feed()
        report = self.feed(recorded_at=AFTER)
        self.assertEqual(report["events"][0]["event_at"], T)
        self.assertEqual(report["page_validation_cutoff"], AFTER)

    def test_invalid_explicit_run_acceptance_rejects_page_instead_of_returning_events(self):
        cases = (("null", None), ("naive", "2026-09-09T05:52:00.817388"),
                 ("before-start", "2025-12-31T23:59:59.999999Z"), ("after-completion", AFTER))
        for identifier, accepted in cases:
            self.fixture.add_run(identifier, accepted=accepted)
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED))
            self.claim(identifier, entity=identifier, run=identifier)
            before = list(self.c.iterdump())
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "explicit accepted_at"):
                self.feed(subject_entity_id=identifier, recorded_at=LATER)
            self.assertEqual(list(self.c.iterdump()), before)

    def test_absent_and_exact_boundary_run_acceptances_remain_valid(self):
        for identifier, parameters in (("absent", {}), ("start", {"accepted_at": CREATED}),
                ("end", {"accepted_at": T}),
                ("offset", {"accepted_at": "2026-09-08T22:52:00.817388-07:00"})):
            r.add_ingestion_run(self.c, m.IngestionRun(identifier, "source", CREATED, m.IngestionStatus.SUCCEEDED,
                completed_at=T, input_document_id="doc", parameters=parameters))
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED))
            self.claim(identifier, entity=identifier, run=identifier)
            with self.subTest(identifier=identifier):
                report = self.feed(subject_entity_id=identifier)
                self.assertEqual(report["total_events"], 1)
                self.assertEqual(report["ingestion_runs"][0]["accepted_at"], parameters.get("accepted_at"))

    def test_bad_later_page_acceptance_raises_with_population_digest_without_skipping_event(self):
        self.claim("a-good")
        self.fixture.add_run(accepted=None)
        self.claim("z-invalid", run="run")
        first = self.feed(limit=1)
        self.assertEqual(first["total_events"], 2)
        self.assertEqual(first["next_offset"], 1)
        with self.assertRaisesRegex(ValueError, "invalid explicit accepted_at"):
            self.feed(limit=1, offset=1, population_sha256=first["population_sha256"])

    def test_dangling_evidence_is_not_silently_omitted_when_valid_support_remains(self):
        self.fixture.document("removed")
        self.claim("source", evidence=[m.EvidenceLink("doc"), m.EvidenceLink("removed")])
        self.c.commit()
        self.c.execute("PRAGMA foreign_keys=OFF")
        self.c.execute("DROP TRIGGER source_documents_immutable_delete")
        self.c.execute("DELETE FROM source_documents WHERE id='removed'")
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            self.feed()

    def test_future_direct_document_transitive_dependency_and_run_admission_fail_closed(self):
        self.fixture.document("future", retrieved_at=AFTER)
        self.claim("direct", evidence=[m.EvidenceLink("future")])
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            self.feed()
        self.claim("future-parent", entity="other", recorded=AFTER)
        self.claim("dependent", entity="facility", dependencies=["future-parent"])
        with self.assertRaisesRegex(ValueError, "claim admission"):
            self.feed(subject_entity_id="facility")
        self.fixture.add_run(accepted=AFTER)
        self.claim("late-acceptance", entity="site", run="run")
        with self.assertRaisesRegex(ValueError, "actual run admission"):
            self.feed(subject_entity_id="site")

    def test_auxiliary_run_document_is_checked_without_exposing_parameters(self):
        self.fixture.document("future", retrieved_at=AFTER)
        self.fixture.add_run()
        r.add_ingestion_run_document(self.c, m.IngestionRunDocument("run", "future", "auxiliary"))
        self.claim("source", run="run")
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            self.feed()
        report = self.feed(recorded_at=AFTER)
        self.assertNotIn("SECRET_RUN_PARAMETERS", json.dumps(report))

    def test_transitive_support_is_separate_from_event_population(self):
        self.claim("grandparent", entity="company", recorded=CREATED)
        self.claim("parent", entity="other", recorded=CREATED, dependencies=["grandparent"])
        self.claim("root", dependencies=["parent"])
        report = self.feed()
        self.assertEqual(report["total_events"], 1)
        self.assertEqual({c["id"] for c in report["dependency_context"]}, {"parent", "grandparent"})
        raw = json.dumps(report)
        self.assertNotIn("SECRET_CLAIM_NOTES", raw)
        self.assertNotIn("SECRET_EXCERPT", raw)
        self.assertEqual(report["distinct_subjects"], 1)

    def test_quantities_rates_units_qualifiers_and_unknowns_survive(self):
        for metadata, subject, annotate, prefix in (
                (fixtures.quantity(), "company", fixtures.add_quantity_metadata, "source_quantity."),
                (fixtures.wafer_area(claim_version_id="area"), "site", fixtures.add_quantity_metadata, "source_quantity."),
                (fixtures.rate(), "facility", fixtures.add_rate_metadata, "source_rate.")):
            self.claim(metadata.claim_version_id, entity=subject, value=metadata.scalar(),
                       series_key=metadata.series_key(subject), predicate=prefix + metadata.metric)
            annotate(self.c, metadata)
        by_id = {e["claim"]["id"]: e["claim"] for e in self.feed()["events"]}
        self.assertEqual(by_id["quantity"]["value"]["source_quantity"]["numeric_qualifier"], "greater_than")
        self.assertEqual(by_id["area"]["value"]["source_quantity"]["unit"], "square_centimeters")
        self.assertEqual(by_id["rate"]["value"]["source_rate"]["denominator_unit"], "month")
        self.assertIsNone(by_id["rate"]["value"]["source_rate"]["native_wafer_diameter_inches"])
        self.assertNotIn(fixtures.WORDING, json.dumps(by_id))
        self.assertTrue(all(c["claim_kind"] == "source_statement" for c in by_id.values()))

    def test_one_wal_snapshot_spans_population_and_hydration(self):
        self.c.commit()
        self.c.execute("PRAGMA journal_mode=WAL")
        self.claim("first")
        self.c.commit()
        writer = database.connect(self.fixture.path)
        self.addCleanup(writer.close)
        from semiconductor_atlas.evidence_changes import _page_claims

        def concurrent_write(connection, identifiers, recorded_at):
            previous = self.fixture.c
            self.fixture.c = writer
            try:
                self.fixture.claim("later-commit")
                writer.commit()
            finally:
                self.fixture.c = previous
            return _page_claims(connection, identifiers, recorded_at)

        with readonly_database(self.fixture.path) as reader:
            with patch("semiconductor_atlas.evidence_changes._page_claims", side_effect=concurrent_write):
                first = evidence_changes(reader, since=CREATED, recorded_at=T, as_of=AS_OF)
            self.assertEqual(first["total_events"], 1)
            with self.assertRaisesRegex(ValueError, "inventory or selectors changed"):
                evidence_changes(reader, since=CREATED, recorded_at=T, as_of=AS_OF,
                    population_sha256=first["population_sha256"])

    def test_success_and_failure_preserve_caller_savepoint_and_uncommitted_work(self):
        self.c.commit()
        self.c.execute("SAVEPOINT caller")
        self.claim("uncommitted")
        before = self.c.total_changes
        self.assertEqual(self.feed()["total_events"], 1)
        with self.assertRaisesRegex(ValueError, "inventory or selectors changed"):
            self.feed(population_sha256="0" * 64)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.total_changes, before)
        self.c.execute("ROLLBACK TO caller")
        self.c.execute("RELEASE caller")
        self.assertEqual(self.feed()["total_events"], 0)

    def test_schema10_and_schema9_have_same_events_without_implicit_migration(self):
        self.claim("source")
        old = self.feed()
        self.c.commit()
        database.apply_migrations(self.c)
        current = self.feed()
        self.assertEqual(current["schema_version"], 10)
        self.assertEqual(old["events"], current["events"])
        self.assertNotEqual(old["population_sha256"], current["population_sha256"])

    def test_schema4_rejected_and_unchanged(self):
        connection, _ = database.initialize(":memory:", target_version=4)
        self.addCleanup(connection.close)
        before = list(connection.iterdump())
        with self.assertRaisesRegex(ValueError, "schema 5"):
            evidence_changes(connection, since=CREATED, recorded_at=T, as_of=AS_OF)
        self.assertEqual(list(connection.iterdump()), before)

    def test_invalid_options_and_unbound_pagination_are_rejected(self):
        for options in (dict(since=T), dict(since=AFTER), dict(since="2026-01-01"),
                dict(since="0001-01-01T00:00:00+01:00"), dict(recorded_at="9999-12-31T23:59:59-01:00"),
                dict(as_of="20260101"), dict(limit=0), dict(limit=True), dict(limit=201),
                dict(offset=-1), dict(offset=True), dict(offset=1), dict(kind="made-up"),
                dict(subject_entity_id=""), dict(population_sha256="A" * 64), dict(population_sha256=1)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.feed(**options)

    def test_cli_reads_wal_and_does_not_create_missing_database_or_emit_partial_page(self):
        self.c.commit()
        self.c.execute("PRAGMA journal_mode=WAL")
        self.claim("source")
        self.c.commit()
        before = self.fixture.path.read_bytes()
        args = ["evidence-changes", "--database", str(self.fixture.path), "--since", CREATED,
                "--recorded-at", T, "--as-of", AS_OF]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(stdout.getvalue())["total_events"], 1)
        self.assertEqual(before, self.fixture.path.read_bytes())
        bad_stdout = io.StringIO()
        with contextlib.redirect_stdout(bad_stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main([*args, "--offset", "1"]), 1)
        self.assertEqual(bad_stdout.getvalue(), "")
        missing = self.fixture.root / "missing" / "atlas.sqlite"
        args[2] = str(missing)
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(args), 1)
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
