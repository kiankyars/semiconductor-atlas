"""Publisher regrouping changes cutoff-aware attribution, never inherited evidence."""

import csv
import hashlib
import json
import unittest

from semiconductor_atlas import coverage, database, models as m, release, repository as r, service
from semiconductor_atlas import source_family_review as families
from semiconductor_atlas.evidence_changes import evidence_changes
from semiconductor_atlas.evidence_dossier import build_evidence_dossier
from semiconductor_atlas.evidence_search import search_evidence
from tests import test_exact_knowledge_clocks as fixture


CREATED = fixture.CREATED
CLAIM_AT = "2026-09-08T00:00:00Z"
REVIEWED = "2026-09-09T05:51:59Z"
BEFORE, AT, AFTER = fixture.JUST_BEFORE, fixture.ADMITTED, fixture.JUST_AFTER
OFFSET_AT = fixture.OFFSET_ADMITTED
AS_OF = fixture.AS_OF
ORIGINAL, TARGET = "fixture-family", "publisher-family"


class SourceFamilyReadviewTests(fixture._ClockFixture, unittest.TestCase):
    target_version = 12

    def setUp(self):
        super().setUp()
        r.add_source_family(self.c, m.SourceFamily(
            "publisher-family", TARGET, "Publisher grouping", CREATED))
        for source, family, document, subject in (
                ("source", "family", "doc", "project"),
                ("sibling", "family", "sibling-doc", "other"),
                ("publisher-source", "publisher-family", "publisher-doc", "project")):
            if source != "source":
                r.add_source(self.c, m.Source(source, family, source, source, "Publisher",
                    "https://example.test", CREATED))
                self.document(document, source_id=source, document_url="https://example.test/doc")
            run = source + "-run"
            r.add_ingestion_run(self.c, m.IngestionRun(run, source, CREATED,
                m.IngestionStatus.SUCCEEDED, completed_at=CLAIM_AT,
                input_document_id=document, code_version="synthetic-importer-v1",
                parameters={"accepted_at": CLAIM_AT, "workflow": "immutable-importer-route"}))
            payload = {"fixture_source": source}
            record = source + "-record"
            r.add_source_record(self.c, m.SourceRecord(record, run, document, record, CREATED,
                m.source_record_payload_sha256(payload), payload))
            self.claim(source + "-claim", entity=subject, recorded=CLAIM_AT,
                evidence=[m.EvidenceLink(document, source_record_id=record)], run=run)

    def assign(self, *, source="source", target="publisher-family", recorded=AT,
               reviewed=REVIEWED, predecessor=None):
        review = {
            "format": families.FORMAT,
            "reviewer": "synthetic-reviewer",
            "reviewed_at": reviewed,
            "reason": "Reviewed attribution to the same original publisher.",
            "prior_exposure": "Synthetic fixture; not an independent corroboration review.",
            "source": dict(self.c.execute("SELECT * FROM sources WHERE id=?", (source,)).fetchone()),
            "target_family": dict(self.c.execute(
                "SELECT * FROM source_families WHERE id=?", (target,)).fetchone()),
            "expected_predecessor_id": predecessor,
            "evidence_documents": [dict(row) for row in self.c.execute(
                "SELECT id,source_id,document_url,content_sha256,retrieved_at "
                "FROM source_documents WHERE source_id=? ORDER BY id", (source,))],
            "boundaries": families.BOUNDARIES.copy(),
        }
        raw = json.dumps(review, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        identifier = families.insert_review(self.c, raw, expected_sha256=digest, recorded_at=recorded)
        return identifier, digest

    def source_rows(self, cutoff=AT):
        return service.source_claim_records(self.c, recorded_at=cutoff)

    def dossier(self, cutoff=AT, entity="project", as_of=AS_OF):
        return build_evidence_dossier(self.c, subject_entity_id=entity,
            as_of=as_of, recorded_at=cutoff)

    def search(self, cutoff=AT, query=None):
        return search_evidence(self.c, as_of=AS_OF, recorded_at=cutoff, query=query)

    def coverage(self, cutoff=AT):
        return coverage.coverage_report(self.c, as_of=AS_OF, recorded_at=cutoff)

    def feed(self, cutoff=AT, since=CREATED, **options):
        return evidence_changes(self.c, since=since, recorded_at=cutoff, as_of=AS_OF, **options)

    def projections(self, cutoff):
        return {
            "sources": self.source_rows(cutoff),
            "history": service.claim_history_records(self.c, as_of=AS_OF, recorded_at=cutoff),
            "dossier": self.dossier(cutoff),
            "search": self.search(cutoff),
            "coverage": self.coverage(cutoff),
            "feed": self.feed(cutoff),
        }

    def evidence(self, claims, identifier="source-claim"):
        return next(row for row in claims if row["id"] == identifier)["evidence"][0]

    def legacy_rows(self):
        tables = [row[0] for row in self.c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name!='source_family_assignments'")]
        return {table: sorted((tuple(row) for row in self.c.execute('SELECT * FROM "' + table + '"')),
                              key=repr) for table in tables}

    def test_future_assignment_leaves_all_earlier_readviews_exactly_unchanged(self):
        before = self.projections(BEFORE)
        self.assign()
        self.assertEqual(self.projections(BEFORE), before)
        self.assertNotIn("source_family_resolution", json.dumps(before))
        self.assertEqual(self.dossier(BEFORE, as_of="2030-01-01")["evidence_summary"],
                         before["dossier"]["evidence_summary"])

    def test_source_claims_change_at_exact_microsecond_with_auditable_original_family(self):
        identifier, digest = self.assign()
        for cutoff, expected in ((BEFORE, ORIGINAL), (AT, TARGET), (OFFSET_AT, TARGET)):
            with self.subTest(cutoff=cutoff):
                rows = self.source_rows(cutoff)
                link = self.evidence(rows)
                self.assertEqual(link["source_family"], expected)
                self.assertEqual({row["id"] for row in rows},
                    {"source-claim", "sibling-claim", "publisher-source-claim"})
                self.assertTrue(all(row["confidence"] is None and row["valid_from"] is None
                                    and row["claim_kind"] == "source_statement" for row in rows))
                if cutoff == BEFORE:
                    self.assertNotIn("source_family_resolution", link)
                else:
                    self.assertEqual(link["source_family_resolution"], {
                        "assignment_id": identifier, "recorded_at": AT, "review_sha256": digest,
                        "original_source_family_id": "family", "original_source_family": ORIGINAL,
                        "original_source_family_name": "Fixture"})
                sibling = self.evidence(rows, "sibling-claim")
                self.assertEqual(sibling["source_family"], ORIGINAL)
                self.assertNotIn("source_family_resolution", sibling)
        self.assertEqual(self.source_rows(AT), self.source_rows(OFFSET_AT))

    def test_dossier_collapses_grouping_without_deduplicating_documents_or_claims(self):
        before = self.dossier(BEFORE)
        self.assign()
        after = self.dossier()
        self.assertEqual(before["evidence_summary"]["source_family_count"], 2)
        self.assertEqual(after["evidence_summary"]["source_families"], [TARGET])
        self.assertEqual(after["evidence_summary"]["source_family_count"], 1)
        for report in (before, after):
            self.assertEqual(report["evidence_summary"]["source_document_count"], 2)
            self.assertEqual(report["evidence_summary"]["evidence_link_count"], 2)
            self.assertEqual(report["evidence_summary"]["independent_corroboration"], "not_assessed")
            self.assertEqual(report["world_current_claims"], [])
            self.assertEqual(report["dependency_context"], [])
            self.assertEqual({claim["id"] for claim in report["source_statements"]},
                             {"source-claim", "publisher-source-claim"})
        self.assertIn("source_family_resolution", self.evidence(after["recorded_history"]))
        self.assertEqual(self.dossier(entity="other")["evidence_summary"]["source_families"], [ORIGINAL])

    def test_search_matches_resolved_family_but_preserves_other_source_in_original_group(self):
        self.assign()
        for cutoff in (BEFORE, AT):
            with self.subTest(cutoff=cutoff):
                original = {row["id"]: row for row in self.search(cutoff, ORIGINAL)["results"]}
                self.assertEqual(set(original), {"project", "other"})
                self.assertEqual(original["project"]["matched_fields"], [
                    "direct_sources.source." + ("source_family" if cutoff == BEFORE
                    else "source_family_resolution.original_source_family")])
                self.assertEqual(original["other"]["matched_fields"],
                                 ["direct_sources.sibling.source_family"])
                self.assertEqual({row["id"] for row in self.search(cutoff, TARGET)["results"]}, {"project"})
                project = next(row for row in self.search(cutoff)["results"] if row["id"] == "project")
                self.assertEqual(project["direct_source_family_count"], 2 if cutoff == BEFORE else 1)
                self.assertEqual(project["direct_source_document_count"], 2)
                self.assertEqual(project["known_source_statement_count"], 2)
                self.assertEqual(project["world_current_claim_count"], 0)
                self.assertEqual(len(project["direct_sources"]), 2)
                source = next(row for row in project["direct_sources"] if row["source_id"] == "source")
                self.assertEqual(source["source_family_id"], "family" if cutoff == BEFORE else "publisher-family")
                self.assertEqual(source["source_family_name"], "Fixture" if cutoff == BEFORE else "Publisher grouping")
                self.assertEqual("source_family_resolution" in source, cutoff != BEFORE)
        self.assertEqual(self.search(AT), self.search(OFFSET_AT))

    def test_new_family_search_association_is_not_visible_before_assignment(self):
        self.assign(source="sibling")
        self.assertEqual({row["id"] for row in self.search(BEFORE, TARGET)["results"]}, {"project"})
        self.assertEqual({row["id"] for row in self.search(AT, TARGET)["results"]}, {"project", "other"})
        row = next(row for row in self.search(AT, TARGET)["results"] if row["id"] == "other")
        self.assertEqual(row["matched_fields"], ["direct_sources.sibling.source_family"])
        self.assertEqual(self.evidence(self.source_rows())["source_family"], ORIGINAL)

    def test_coverage_regroups_only_reviewed_source_and_preserves_document_denominator(self):
        self.assign()
        for cutoff, expected in ((BEFORE, {ORIGINAL: (2, 2), TARGET: (1, 1)}),
                                  (AT, {ORIGINAL: (1, 1), TARGET: (2, 2)})):
            report = self.coverage(cutoff)
            with self.subTest(cutoff=cutoff):
                self.assertEqual(report["source_family_count"], 2)
                self.assertEqual(report["source_document_count"], 3)
                self.assertEqual({row["source_family"]: (row["source_count"], row["document_count"])
                                  for row in report["source_families"]}, expected)
                self.assertTrue(all(row["first_retrieved_at"] == CREATED
                                    and row["last_retrieved_at"] == CREATED
                                    and row["first_database_accepted_at"] == CLAIM_AT
                                    and row["last_database_accepted_at"] == CLAIM_AT
                                    for row in report["source_families"]))

    def test_changefeed_relabels_old_event_evidence_without_inventing_claim_events(self):
        before = self.feed(BEFORE)
        self.assign()
        after = self.feed()
        self.assertEqual([event["event_id"] for event in after["events"]],
                         [event["event_id"] for event in before["events"]])
        self.assertEqual(after["event_counts"], {"claim_admitted": 3, "claim_superseded": 0})
        for feed, expected in ((before, ORIGINAL), (after, TARGET)):
            event = next(row for row in feed["events"] if row["claim"]["id"] == "source-claim")
            self.assertEqual(event["event_at"], CLAIM_AT)
            self.assertEqual(event["claim"]["evidence"][0]["source_family"], expected)
        correction_window = self.feed(since=BEFORE)
        self.assertEqual(correction_window["total_events"], 0)
        self.assertEqual(correction_window["events"], [])
        self.assertEqual(correction_window["event_counts"], {"claim_admitted": 0, "claim_superseded": 0})
        self.assertEqual(self.feed(AT), self.feed(OFFSET_AT))

    def test_changefeed_page_and_dependency_attribution_use_same_assignment_cutoff(self):
        self.claim("dependent", entity="other", recorded=CLAIM_AT,
                   dependencies=["source-claim"], kind=m.ClaimKind.DERIVED_ESTIMATE,
                   valid_from="2026-01-01")
        self.assign()
        for cutoff, expected in ((BEFORE, ORIGINAL), (AT, TARGET)):
            with self.subTest(cutoff=cutoff):
                feed = self.feed(cutoff, subject_entity_id="other", limit=1)
                self.assertEqual(feed["events"][0]["claim"]["id"], "dependent")
                self.assertEqual(self.evidence(feed["dependency_context"])["source_family"], expected)
                page = self.feed(cutoff, subject_entity_id="other", limit=1,
                    offset=feed["next_offset"], population_sha256=feed["population_sha256"])
                self.assertEqual(page["events"][0]["claim"]["id"], "sibling-claim")
                self.assertEqual(page["events"][0]["claim"]["evidence"][0]["source_family"], ORIGINAL)
                dossier = self.dossier(cutoff, entity="other")
                self.assertEqual(self.evidence(dossier["dependency_context"])["source_family"], expected)

    def test_next_assignment_selects_latest_group_without_rewriting_first_cutoff(self):
        first, _ = self.assign()
        first_views = self.projections(AT)
        second, _ = self.assign(target="family", recorded=AFTER, reviewed=AT, predecessor=first)
        self.assertEqual(self.projections(AT), first_views)
        after = self.evidence(self.source_rows(AFTER))
        self.assertEqual(after["source_family"], ORIGINAL)
        self.assertEqual(after["source_family_resolution"]["assignment_id"], second)
        self.assertEqual(after["source_family_resolution"]["original_source_family"], ORIGINAL)
        self.assertEqual(self.dossier(AFTER)["evidence_summary"]["source_family_count"], 2)

    def test_readers_and_assignment_preserve_raw_importer_membership_and_all_inherited_rows(self):
        def importer_membership():
            return [tuple(row) for row in self.c.execute(
                "SELECT i.id,i.source_id,i.code_version,i.parameters_json,f.stable_key "
                "FROM ingestion_runs i JOIN sources s ON s.id=i.source_id "
                "JOIN source_families f ON f.id=s.family_id ORDER BY i.id")]
        inherited, membership = self.legacy_rows(), importer_membership()
        self.assign()
        self.c.commit()
        self.c.execute("BEGIN")
        changes = self.c.total_changes
        for cutoff in (BEFORE, AT, AFTER):
            self.projections(cutoff)
        self.assertTrue(self.c.in_transaction)
        self.assertEqual(self.c.total_changes, changes)
        self.assertEqual(self.legacy_rows(), inherited)
        self.assertEqual(importer_membership(), membership)
        self.assertEqual(self.c.execute("SELECT family_id FROM sources WHERE id='source'").fetchone()[0], "family")
        self.assertEqual(r.validate_database(self.c), [])

    def test_release_conditionally_exports_exact_review_and_evidence_annotation(self):
        baseline = self.path.parent / "baseline"
        historical = self.path.parent / "historical"
        corrected = self.path.parent / "corrected"
        release.write_release(self.c, baseline, as_of=AS_OF, recorded_at=BEFORE)
        identifier, digest = self.assign()
        release.write_release(self.c, historical, as_of=AS_OF, recorded_at=BEFORE)
        release.write_release(self.c, corrected, as_of=AS_OF, recorded_at=AT)
        self.assertEqual({p.name: p.read_bytes() for p in baseline.iterdir()},
                         {p.name: p.read_bytes() for p in historical.iterdir()})
        self.assertFalse((historical / "source_family_assignments.jsonl").exists())
        self.assertNotIn("source_family_resolution_json", (historical / "evidence.csv").read_text().splitlines()[0])
        ledger = [json.loads(line) for line in (corrected / "source_family_assignments.jsonl").read_text().splitlines()]
        self.assertEqual(len(ledger), 1)
        assignment = ledger[0]
        self.assertEqual(assignment["id"], identifier)
        self.assertEqual(assignment["recorded_at"], AT)
        self.assertEqual(assignment["review_sha256"], digest)
        self.assertEqual(hashlib.sha256(assignment["review_json"].encode()).hexdigest(), digest)
        self.assertEqual(json.loads(assignment["review_json"]), assignment["review"])
        with (corrected / "evidence.csv").open(newline="") as stream:
            evidence = {row["claim_id"]: row for row in csv.DictReader(stream)}
        annotation = json.loads(evidence["source-claim"]["source_family_resolution_json"])
        self.assertEqual(annotation["assignment_id"], identifier)
        self.assertEqual(annotation["original_source_family"], ORIGINAL)
        self.assertEqual(evidence["source-claim"]["source_family"], TARGET)
        self.assertEqual(evidence["sibling-claim"]["source_family"], ORIGINAL)
        self.assertEqual(evidence["sibling-claim"]["source_family_resolution_json"], "")
        self.assertEqual(evidence["publisher-source-claim"]["source_family_resolution_json"], "")
        for name in ("source_inputs.json", "source_observations.jsonl"):
            def read_rows(directory):
                text = (directory / name).read_text()
                return json.loads(text) if name.endswith(".json") else list(map(json.loads, text.splitlines()))
            old = {row["source_key"]: row for row in read_rows(historical)}
            new = {row["source_key"]: row for row in read_rows(corrected)}
            with self.subTest(surface=name):
                self.assertEqual(set(old), {"fixture-source", "sibling", "publisher-source"})
                self.assertEqual(new["fixture-source"]["source_family"], TARGET)
                self.assertEqual(new["fixture-source"]["source_family_resolution"], annotation)
                self.assertEqual(new["sibling"], old["sibling"])
                self.assertEqual(new["publisher-source"], old["publisher-source"])
                unannotated = dict(new["fixture-source"])
                unannotated.pop("source_family_resolution")
                unannotated["source_family"] = ORIGINAL
                self.assertEqual(unannotated, old["fixture-source"])

    def test_release_retains_original_importer_attribution_after_regrouping(self):
        family = "openstreetmap"
        source = "osm-source"
        document = "osm-doc"
        r.add_source_family(self.c, m.SourceFamily(family, family, "OpenStreetMap", CREATED))
        r.add_source(self.c, m.Source(source, family, source, "Synthetic OSM source",
            "OpenStreetMap contributors", "https://example.test", CREATED))
        self.document(document, source_id=source)
        r.add_ingestion_run(self.c, m.IngestionRun("osm-run", source, CREATED,
            m.IngestionStatus.SUCCEEDED, completed_at=CLAIM_AT, input_document_id=document))
        self.assign(source=source)
        corrected = self.path.parent / "attribution"
        release.write_release(self.c, corrected, as_of=AS_OF, recorded_at=AT)
        self.assertIn("© OpenStreetMap contributors (ODbL 1.0)", (corrected / "ATTRIBUTION.txt").read_text())
        inputs = json.loads((corrected / "source_inputs.json").read_text())
        item = next(row for row in inputs if row["source_key"] == source)
        self.assertEqual(item["source_family"], TARGET)
        self.assertEqual(item["source_family_resolution"]["original_source_family"], family)
        self.assertNotIn("source_family_resolution_json", (corrected / "evidence.csv").read_text().splitlines()[0])


class SourceFamilyImporterPopulationTests(unittest.TestCase):
    def test_reviewed_project_route_inventory_remains_original_after_assignment(self):
        from semiconductor_atlas import project_target_population as population
        from tests import test_project_target_review as projects

        project = projects.ProjectTargetReviewTests("runTest")
        project.setUp()
        self.addCleanup(project.doCleanups)
        accepted = project.accept()
        connection = project.connection
        database.apply_migrations(connection, target_version=12)
        r.add_source_family(connection, m.SourceFamily("nist-family", "us-official-semiconductor-awards",
            "NIST publisher grouping", CREATED))
        source = connection.execute("SELECT * FROM sources").fetchone()
        original = population._inventory(connection)
        raw = json.dumps({
            "format": families.FORMAT, "reviewer": "fixture-reviewer", "reviewed_at": REVIEWED,
            "reason": "Regroup NIST publisher, not the importer route.", "prior_exposure": "Synthetic fixture.",
            "source": dict(source),
            "target_family": dict(connection.execute("SELECT * FROM source_families WHERE id='nist-family'").fetchone()),
            "expected_predecessor_id": None,
            "evidence_documents": [dict(row) for row in connection.execute(
                "SELECT id,source_id,document_url,content_sha256,retrieved_at FROM source_documents ORDER BY id")],
            "boundaries": families.BOUNDARIES,
        }, sort_keys=True).encode()
        families.insert_review(connection, raw, expected_sha256=hashlib.sha256(raw).hexdigest(), recorded_at=AT)
        after = population._inventory(connection)
        self.assertEqual(after, original)
        route = next(row for row in after if row["run_id"] == accepted["run_id"])
        self.assertTrue(route["project_route"])
        self.assertTrue(all(route["markers"].values()))
        resolved = families.resolutions(connection, recorded_at=AT)[source["id"]]
        self.assertEqual(resolved["source_family"], "us-official-semiconductor-awards")
        self.assertEqual(resolved["source_family_resolution"]["original_source_family"], "reviewed-source-project-targets")


if __name__ == "__main__":
    unittest.main()
