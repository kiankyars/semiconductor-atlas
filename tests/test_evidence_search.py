"""Entity discovery is cutoff-aware metadata matching, never identity resolution."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas.cli import main
from semiconductor_atlas.evidence_search import search_evidence
from tests import test_evidence_dossier as fixtures


CREATED, ADMITTED, BEFORE, AFTER, LATER = (
    fixtures.CREATED, fixtures.ADMITTED, fixtures.JUST_BEFORE, fixtures.JUST_AFTER, fixtures.LATER)
AS_OF = fixtures.AS_OF


class EvidenceSearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EvidenceDossierTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.c

    def search(self, query=None, **changes):
        options = dict(as_of=AS_OF, recorded_at=ADMITTED, query=query)
        options.update(changes)
        return search_evidence(self.c, **options)

    def claim(self, *args, **kwargs):
        return self.fixture.claim(*args, **kwargs)

    def source(self, identifier="second", *, publisher="SecondPublisherToken", source_created=CREATED,
               family_created=CREATED, retrieved=CREATED, published=None):
        r.add_source_family(self.c, m.SourceFamily(identifier, identifier, identifier, family_created))
        r.add_source(self.c, m.Source(identifier, identifier, identifier, identifier, publisher,
                                     "https://example.test", source_created))
        r.add_source_document(self.c, m.SourceDocument(identifier, identifier, "https://example.test/" + identifier,
                              "SECRET_DOCUMENT_TITLE", retrieved, "b" * 64, published_at=published))
        return identifier

    def row(self, query="fixture:project", **changes):
        result = self.search(query, **changes)
        self.assertEqual(result["total_matched_entities"], 1)
        return result["results"][0]

    def test_browse_preserves_known_empty_entities_and_exact_kind_filter(self):
        report = self.search()
        self.assertEqual(report["visible_registry_entities"], 5)
        self.assertEqual(report["total_matched_entities"], 5)
        self.assertTrue(all(e["known_source_statement_count"] == 0 for e in report["results"]))
        self.assertTrue(all(e["matched_fields"] == [] for e in report["results"]))
        projects = self.search(kind="project")
        self.assertEqual(projects["visible_registry_entities"], 5)
        self.assertEqual({e["id"] for e in projects["results"]}, {"project", "other"})

    def test_exact_ids_names_keys_are_matched_without_merging_duplicate_labels(self):
        for identifier in ("city", "nist"):
            r.add_entity(self.c, m.Entity(identifier, m.EntityKind.PROJECT, identifier, CREATED, "Ohio One"))
        report = self.search("ohio ONE")
        self.assertEqual([e["id"] for e in report["results"]], ["city", "nist"])
        self.assertTrue(all(e["matched_fields"] == ["display_name"] for e in report["results"]))
        self.assertEqual(self.row("city")["id"], "city")
        self.assertEqual(self.row("fixture:facility")["id"], "facility")

    def test_unicode_normalization_casefold_and_punctuation_are_literal(self):
        r.add_entity(self.c, m.Entity("literal", m.EntityKind.PROJECT, "literal", CREATED,
                                     "Straße Café 100%_done [x] 'quote'"))
        for query in ("STRASSE", "CAFE\u0301", "%_", "[x]", "'quote'"):
            with self.subTest(query=query):
                self.assertEqual(self.row(query)["id"], "literal")
        for query in (".*", "%anything%", "' OR 1=1 --"):
            with self.subTest(query=query):
                self.assertEqual(self.search(query)["total_matched_entities"], 0)

    def test_query_is_one_substring_not_an_implicit_token_or_alias_search(self):
        self.assertEqual(self.search("Synthetic project")["total_matched_entities"], 1)
        self.assertEqual(self.search("project Synthetic")["total_matched_entities"], 0)
        self.assertEqual(self.search(" PROJECT ")["query"], "PROJECT")

    def test_source_publisher_and_family_metadata_require_direct_selected_evidence(self):
        self.assertEqual(self.search("Synthetic publisher")["total_matched_entities"], 0)
        self.claim("source")
        row = self.row("Synthetic publisher")
        self.assertEqual(row["id"], "project")
        self.assertTrue(any(field.endswith(".publisher") for field in row["matched_fields"]))
        self.assertEqual(self.row("fixture-family")["id"], "project")
        self.assertEqual(self.row("fixture-source")["id"], "project")

    def test_multiple_claims_documents_and_roles_do_not_multiply_counts(self):
        self.fixture.document("doc2")
        evidence = [m.EvidenceLink("doc"), m.EvidenceLink("doc", role=m.EvidenceRole.CONTEXT),
                    m.EvidenceLink("doc2")]
        self.claim("one", evidence=evidence)
        self.claim("two", evidence=evidence)
        self.claim("estimate", kind=m.ClaimKind.DERIVED_ESTIMATE, valid_from="2026-01-01",
                   dependencies=["one"])
        row = self.row("Synthetic publisher")
        self.assertEqual(row["known_source_statement_count"], 2)
        self.assertEqual(row["world_current_claim_count"], 1)
        self.assertEqual(row["direct_source_document_count"], 2)
        self.assertEqual(row["direct_source_family_count"], 1)
        self.assertEqual(row["direct_sources"][0]["document_ids"], ["doc", "doc2"])
        self.assertEqual(row["direct_sources"][0]["evidence_roles"], ["context", "support"])

    def test_world_ranking_precedes_kind_exclusion_without_resurrecting_observation(self):
        self.claim("observation", series="mixed", valid_from="2025-01-01", kind=m.ClaimKind.DIRECT_OBSERVATION)
        self.claim("statement", series="mixed", predicate="source_reported.observation", valid_from="2026-01-01")
        row = self.row()
        self.assertEqual(row["world_current_claim_count"], 0)
        self.assertEqual(row["known_source_statement_count"], 1)
        self.assertEqual(self.fixture.report()["world_current_claims"], [])

    def test_unknown_future_and_expired_source_statements_ignore_world_time(self):
        self.claim("unknown")
        self.claim("future", valid_from="2028-01-01")
        self.claim("expired", valid_from="2024-01-01", valid_to="2025-01-01")
        self.claim("future-observation", valid_from="2028-01-01", kind=m.ClaimKind.DIRECT_OBSERVATION)
        row = self.row(as_of="2020-01-01")
        self.assertEqual(row["known_source_statement_count"], 3)
        self.assertEqual(row["world_current_claim_count"], 0)

    def test_all_unsuperseded_source_versions_remain_counted(self):
        self.claim("one", series="same", valid_from="2024-01-01")
        self.claim("two", series="same", predicate="source_reported.one", valid_from="2025-01-01")
        self.assertEqual(self.row()["known_source_statement_count"], 2)

    def test_current_source_labels_do_not_include_superseded_versions(self):
        old = self.source("old", publisher="FormerPublisherToken")
        self.claim("old", series="revised", recorded=CREATED, evidence=[m.EvidenceLink(old)])
        self.claim("new", series="revised", predicate="source_reported.old")
        self.assertEqual(self.search("FormerPublisherToken", recorded_at=BEFORE)["total_matched_entities"], 1)
        self.assertEqual(self.search("FormerPublisherToken")["total_matched_entities"], 0)
        self.assertEqual(self.row()["known_source_statement_count"], 1)

    def test_world_expiry_removes_direct_source_association_at_exclusive_boundary(self):
        self.claim("world", kind=m.ClaimKind.DIRECT_OBSERVATION, valid_from="2026-01-01", valid_to=AS_OF)
        self.assertEqual(self.search("Synthetic publisher", as_of="2026-09-08")["total_matched_entities"], 1)
        self.assertEqual(self.search("Synthetic publisher")["total_matched_entities"], 0)
        self.assertEqual(self.row()["world_current_claim_count"], 0)

    def test_dependency_publishers_are_not_attributed_to_the_dependent_entity(self):
        self.claim("parent", entity="company")
        self.claim("child", dependencies=["parent"], kind=m.ClaimKind.DERIVED_ESTIMATE, valid_from="2026-01-01")
        self.assertEqual([e["id"] for e in self.search("Synthetic publisher")["results"]], ["company"])
        row = self.row()
        self.assertEqual(row["world_current_claim_count"], 1)
        self.assertEqual(row["direct_sources"], [])

    def test_refutation_is_a_labeled_association_not_corroboration(self):
        self.claim("refuted", evidence=[m.EvidenceLink("doc", role=m.EvidenceRole.REFUTE)])
        row = self.row("Synthetic publisher")
        self.assertEqual(row["direct_sources"][0]["evidence_roles"], ["refute"])
        self.assertNotIn("confidence", row)

    def test_values_excerpts_notes_titles_and_dependencies_are_not_search_fields(self):
        document = self.source()
        self.claim("source", value=m.ScalarValue(m.ScalarType.TEXT, "SECRET_SCALAR_VALUE"),
                   evidence=[m.EvidenceLink(document, excerpt="SECRET_EXCERPT")])
        for query in ("SECRET_SCALAR_VALUE", "SECRET_EXCERPT", "SECRET_DOCUMENT_TITLE", "SECRET_CLAIM_NOTES"):
            with self.subTest(query=query):
                self.assertEqual(self.search(query)["total_matched_entities"], 0)
        raw = json.dumps(self.search())
        self.assertNotIn("SECRET_SCALAR_VALUE", raw)
        self.assertNotIn("SECRET_EXCERPT", raw)

    def test_claim_admission_cutoff_uses_microseconds(self):
        self.claim("source", recorded=AFTER)
        self.assertEqual(self.row()["known_source_statement_count"], 0)
        self.assertEqual(self.search("Synthetic publisher")["total_matched_entities"], 0)
        self.assertEqual(self.row(recorded_at=AFTER)["known_source_statement_count"], 1)

    def test_later_entity_does_not_leak_into_earlier_labels_or_denominator(self):
        before = self.search()
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "future", AFTER, "SECRET_FUTURE"))
        self.assertEqual(self.search(), before)
        self.assertEqual(self.search("SECRET_FUTURE")["total_matched_entities"], 0)
        self.assertEqual(self.search("SECRET_FUTURE", recorded_at=AFTER)["total_matched_entities"], 1)

    def test_creator_run_admission_filters_entity_and_claim_visibility(self):
        self.fixture.add_run(accepted=AFTER)
        self.claim("future-run-claim", run="run")
        r.add_entity(self.c, m.Entity("future-run-entity", m.EntityKind.PROJECT, "future-run-entity", CREATED,
                                     created_by_run_id="run"))
        self.assertEqual(self.search()["visible_registry_entities"], 5)
        self.assertEqual(self.row()["known_source_statement_count"], 0)
        self.assertEqual(self.row(recorded_at=AFTER)["known_source_statement_count"], 1)
        self.assertEqual(self.search(recorded_at=AFTER)["visible_registry_entities"], 6)

    def test_future_document_source_family_and_publication_metadata_are_not_searchable(self):
        mutations = [dict(retrieved=AFTER), dict(source_created=AFTER), dict(family_created=AFTER),
                     dict(published="2027-01-01")]
        for index, mutation in enumerate(mutations):
            token = "HiddenPublisher" + str(index)
            document = self.source(str(index), publisher=token, **mutation)
            self.claim("source" + str(index), evidence=[m.EvidenceLink(document)])
            with self.subTest(mutation=mutation):
                self.assertEqual(self.search(token)["total_matched_entities"], 0)
        self.assertEqual(self.row()["direct_source_document_count"], 0)

    def test_record_observation_and_record_run_admission_gate_direct_labels(self):
        for index, (observed, accepted) in enumerate(((AFTER, ADMITTED), (CREATED, AFTER))):
            run = self.fixture.add_run("run" + str(index), accepted=accepted)
            r.add_source_record(self.c, m.SourceRecord("record" + str(index), run, "doc", "record" + str(index),
                                 observed, m.source_record_payload_sha256({}), {}))
            self.claim("record-claim" + str(index), evidence=[m.EvidenceLink("doc", source_record_id="record" + str(index))])
        self.assertEqual(self.search("Synthetic publisher")["total_matched_entities"], 0)
        self.assertEqual(self.search("Synthetic publisher", recorded_at=AFTER)["total_matched_entities"], 1)

    def test_run_auxiliary_input_cannot_supply_future_knowledge(self):
        self.fixture.document("future-document", retrieved_at=AFTER)
        self.fixture.add_run()
        r.add_ingestion_run_document(self.c, m.IngestionRunDocument("run", "future-document", "auxiliary"))
        self.claim("source", run="run")
        self.assertEqual(self.row()["known_source_statement_count"], 0)
        self.assertEqual(self.search("Synthetic publisher")["total_matched_entities"], 0)
        self.assertEqual(self.row(recorded_at=AFTER)["known_source_statement_count"], 1)

    def test_cli_reads_committed_wal_without_ignoring_new_claims(self):
        self.c.commit()
        self.c.execute("PRAGMA journal_mode=WAL")
        self.claim("wal-source")
        self.c.commit()
        before_database = self.fixture.path.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["evidence-search", "--database", str(self.fixture.path),
                "--as-of", AS_OF, "--recorded-at", ADMITTED, "--query", "Synthetic publisher"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["results"][0]["known_source_statement_count"], 1)
        self.assertEqual(before_database, self.fixture.path.read_bytes())

    def test_pagination_preserves_full_denominator_order_and_out_of_range_counts(self):
        first = self.search(limit=2)
        second = self.search(limit=2, offset=first["next_offset"])
        third = self.search(limit=2, offset=second["next_offset"])
        self.assertEqual(third["next_offset"], None)
        pages = first["results"] + second["results"] + third["results"]
        self.assertEqual(pages, self.search()["results"])
        self.assertEqual(len({e["id"] for e in pages}), 5)
        out = self.search(limit=2, offset=100)
        self.assertEqual((out["total_matched_entities"], out["returned_entities"], out["next_offset"]), (5, 0, None))

    def test_all_entity_counts_are_not_restricted_to_the_matching_source(self):
        second = self.source()
        self.claim("one")
        self.claim("two", evidence=[m.EvidenceLink(second)])
        row = self.row("SecondPublisherToken")
        self.assertEqual(row["known_source_statement_count"], 2)
        self.assertEqual(row["direct_source_document_count"], 2)
        self.assertEqual(row["direct_source_family_count"], 2)

    def test_invalid_query_kind_and_pagination_are_rejected(self):
        for query in ("", "   ", "x" * 201, 123, []):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.search(query)
        for options in (dict(kind=[]), dict(kind="fabrication"), dict(limit=True), dict(limit=0),
                        dict(limit=201), dict(limit=1.5), dict(offset=True), dict(offset=-1), dict(offset=1.5)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.search(**options)

    def test_normalized_cutoffs_and_repeated_search_are_deterministic(self):
        self.claim("source")
        first = self.search("SYNTHETIC")
        self.assertEqual(first, self.search("SYNTHETIC", recorded_at="2026-09-08T22:52:00.817388-07:00"))
        self.assertEqual(first, self.search("SYNTHETIC"))
        for result in first["results"]:
            self.assertEqual(result["dossier_selection"], dict(entity_id=result["id"], as_of=AS_OF, recorded_at=ADMITTED))

    def test_search_does_not_hydrate_claim_values_or_commit_caller_transaction(self):
        self.c.commit()
        self.c.execute("BEGIN")
        self.claim("uncommitted")
        before = self.c.total_changes
        with patch("semiconductor_atlas.service._hydrate_claim_rows", side_effect=AssertionError("no global hydration")):
            self.assertEqual(self.row()["known_source_statement_count"], 1)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.total_changes, before)
        self.c.rollback()
        self.assertEqual(self.row()["known_source_statement_count"], 0)

    def test_older_schema_is_not_migrated(self):
        connection, _ = database.initialize(":memory:", target_version=4)
        self.addCleanup(connection.close)
        before = list(connection.iterdump())
        with self.assertRaisesRegex(ValueError, "schema 5"):
            search_evidence(connection, as_of=AS_OF, recorded_at=ADMITTED)
        self.assertEqual(before, list(connection.iterdump()))

    def test_cli_search_then_dossier_uses_exact_id_and_preserves_database(self):
        self.claim("source")
        self.c.commit()
        before = self.fixture.path.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main(["evidence-search", "--database", str(self.fixture.path), "--as-of", AS_OF,
                           "--recorded-at", ADMITTED, "--query", "Synthetic publisher"])
        self.assertEqual(status, 0)
        result = json.loads(stdout.getvalue())["results"][0]
        selected = result["dossier_selection"]
        self.assertEqual(selected["entity_id"], "project")
        self.assertEqual(self.fixture.report(entity=selected["entity_id"])["entity"]["id"], "project")
        self.assertEqual(before, self.fixture.path.read_bytes())

    def test_cli_missing_database_and_invalid_limit_do_not_create_files(self):
        self.c.commit()
        missing = self.fixture.root / "missing" / "atlas.sqlite"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["evidence-search", "--database", str(missing), "--as-of", AS_OF,
                                   "--recorded-at", ADMITTED]), 1)
        self.assertFalse(missing.parent.exists())
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["evidence-search", "--database", str(self.fixture.path), "--as-of", AS_OF,
                                   "--recorded-at", ADMITTED, "--limit", "0"]), 1)


if __name__ == "__main__":
    unittest.main()
