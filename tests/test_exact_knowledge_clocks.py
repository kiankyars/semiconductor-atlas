"""Schema 10 uses exact knowledge clocks without rewriting legacy payloads."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas import database, models as m, repository as r
from semiconductor_atlas import release, service
from tests import test_evidence_dossier as fixture


CREATED = fixture.CREATED
ADMITTED = fixture.ADMITTED
JUST_BEFORE = fixture.JUST_BEFORE
JUST_AFTER = fixture.JUST_AFTER
AS_OF = fixture.AS_OF
OFFSET_ADMITTED = "2026-09-08T22:52:00.817388-07:00"
TEXT = m.ScalarValue(m.ScalarType.TEXT, "synthetic source statement")


class _ClockFixture:
    target_version = 10
    document = fixture.EvidenceDossierTests.document
    claim = fixture.EvidenceDossierTests.claim
    report = fixture.EvidenceDossierTests.report
    add_run = fixture.EvidenceDossierTests.add_run

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "atlas.sqlite"
        self.c, _ = database.initialize(self.path, target_version=self.target_version)
        self.addCleanup(self.c.close)
        r.add_source_family(self.c, m.SourceFamily("family", "fixture-family", "Fixture", CREATED))
        r.add_source(self.c, m.Source("source", "family", "fixture-source", "Fixture", "Publisher",
                                    "https://example.test", CREATED))
        self.document("doc")
        for entity, kind in (("project", m.EntityKind.PROJECT), ("other", m.EntityKind.PROJECT),
                             ("company", m.EntityKind.ORGANIZATION), ("canonical", m.EntityKind.ORGANIZATION)):
            r.add_entity(self.c, m.Entity(entity, kind, "fixture:" + entity, CREATED, entity))

    def series(self, identifier, *, entity="project", created=CREATED, value=TEXT):
        r.add_claim_series(self.c, m.ClaimSeries(identifier, entity, identifier,
            "source_reported." + identifier, value.kind, created))
        return identifier

    def insert(self, identifier, series, *, recorded=ADMITTED, value=TEXT):
        return r.insert_claim(self.c, m.ClaimVersion(identifier, series, None, recorded,
            m.ClaimKind.SOURCE_STATEMENT, "synthetic_review", None), value,
            evidence=[m.EvidenceLink("doc")])

    def raw_claim(self, identifier, series, *, recorded=ADMITTED, value=TEXT):
        self.c.execute("""INSERT INTO claim_versions
            (id, series_id, value_kind, value_sha256, valid_from, recorded_at, claim_kind, method)
            VALUES (?, ?, ?, ?, NULL, ?, 'source_statement', 'raw_test')""",
            (identifier, series, value.kind.value, r.value_sha256(value), recorded))

    def source_record(self, identifier="record", *, observed=CREATED, run="run"):
        r.add_source_record(self.c, m.SourceRecord(identifier, run, "doc", identifier, observed,
            m.source_record_payload_sha256({})))

    def identity_decision(self, *, completed=JUST_BEFORE, decided=ADMITTED):
        """A completed, source-backed same-kind decision, known at ADMITTED."""
        self.add_run(completed="2026-01-01T00:00:01Z", accepted="2026-01-01T00:00:01Z")
        self.source_record()
        self.claim("name", entity="company", recorded="2026-09-08T00:00:00Z",
            evidence=[m.EvidenceLink("doc", source_record_id="record")])
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("resolution",
            "2026-09-08T01:00:00Z", "synthetic-resolver"))
        r.add_entity_resolution_run_input(self.c, m.EntityResolutionRunInput("resolution", "run"))
        r.add_entity_resolution_candidate(self.c, m.EntityResolutionCandidate("candidate", "resolution",
            "record", "company", "canonical", 1.0, 1, "2026-09-08T02:00:00Z"))
        r.finalize_entity_resolution_run(self.c, "resolution", status=m.EntityResolutionStatus.SUCCEEDED,
            completed_at=completed)
        r.add_entity_resolution_decision(self.c, m.EntityResolutionDecision("decision", "candidate",
            m.ResolutionDecisionOutcome.MATCH, decided, "synthetic-reviewer", "Synthetic exact match"))

    def assignment(self, *, recorded=ADMITTED):
        return r.add_source_entity_assignment(self.c, m.SourceEntityAssignment("assignment", "record",
            "company", "canonical", "decision", "2026-01-01", recorded))

    def reviewed_run(self, identifier, *, completed, parameters=None):
        r.add_source_family(self.c, m.SourceFamily("review-family", release.REVIEWED_RELATIONSHIP_FAMILY_KEY,
            "Synthetic reviews", CREATED))
        r.add_source(self.c, m.Source("review-source", "review-family", "fixture-reviews", "Reviews",
            "Synthetic publisher", "https://example.test/reviews", CREATED))
        r.add_ingestion_run(self.c, m.IngestionRun(identifier, "review-source", CREATED,
            m.IngestionStatus.SUCCEEDED, completed_at=completed, parameters=parameters or {
                "created_claims": [], "reaffirmed_claims": [], "superseded_claim_ids": []}))


class ExactKnowledgeClockTests(_ClockFixture, unittest.TestCase):
    def test_connect_registers_deterministic_function_without_read_side_effect(self):
        connection = database.connect(":memory:")
        self.addCleanup(connection.close)
        row = connection.execute("SELECT atlas_timestamp_us(?), atlas_timestamp_us(?), "
            "atlas_timestamp_us(?)", (ADMITTED, JUST_AFTER, OFFSET_ADMITTED)).fetchone()
        self.assertEqual(row[1] - row[0], 1)
        self.assertEqual(row[0], row[2])
        function = connection.execute("SELECT flags FROM pragma_function_list "
            "WHERE name='atlas_timestamp_us' AND narg=1").fetchone()
        self.assertIsNotNone(function)
        self.assertTrue(function[0] & 0x800, "SQLite must know the clock UDF is deterministic")

    def test_apply_migrations_registers_function_on_plain_sqlite_connection(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        installed = database.apply_migrations(connection, target_version=10)
        self.assertIn(10, installed)
        self.assertEqual(connection.execute("SELECT atlas_timestamp_us(?) - atlas_timestamp_us(?)",
            (JUST_AFTER, ADMITTED)).fetchone()[0], 1)

    def test_clock_function_rejects_naive_and_invalid_values(self):
        for timestamp in (None, 0, "", "not-a-date", "2026-09-09", "2026-09-09T05:52:00.817388",
                          "2026-02-30T00:00:00Z"):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(self.c.execute("SELECT atlas_timestamp_us(?)", (timestamp,)).fetchone()[0])

    def test_future_evidence_one_microsecond_is_rejected_by_repository(self):
        self.document("future", retrieved_at=JUST_AFTER)
        with self.assertRaises(ValueError):
            self.claim("claim", evidence=[m.EvidenceLink("future")])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0], 0)

    def test_same_instant_timezone_offset_evidence_is_accepted(self):
        self.document("same", retrieved_at=OFFSET_ADMITTED)
        self.claim("claim", evidence=[m.EvidenceLink("same")])
        self.assertEqual(fixture.ids(r.known_source_claims(self.c, recorded_at=ADMITTED)), {"claim"})
        self.assertEqual(r.validate_database(self.c), [])

    def test_future_dependency_one_microsecond_is_rejected_by_repository(self):
        self.claim("parent", entity="other", recorded=JUST_AFTER)
        with self.assertRaises(ValueError):
            self.claim("child", dependencies=["parent"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM claim_versions").fetchone()[0], 1)

    def test_future_subject_one_microsecond_is_rejected_by_repository(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        with self.assertRaises(ValueError):
            self.claim("claim", entity="future")

    def test_future_series_one_microsecond_is_rejected_by_repository(self):
        self.series("future", created=JUST_AFTER)
        with self.assertRaises(ValueError):
            self.insert("claim", "future")

    def test_future_relationship_and_constraint_targets_are_rejected_by_repository(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        values = (m.RelationshipValue("future", "part_of"), m.ConstraintValue("water",
            m.ConstraintStatus.POTENTIAL, m.ConstraintSeverity.UNKNOWN, "Synthetic", "future"))
        for value in values:
            with self.subTest(kind=value.kind), self.assertRaises(ValueError):
                self.claim(value.kind.value, value=value)

    def test_future_source_record_observation_and_run_start_are_rejected(self):
        self.add_run()
        self.source_record("future-observation", observed=JUST_AFTER)
        r.add_ingestion_run(self.c, m.IngestionRun("future-run", "source", JUST_AFTER,
            m.IngestionStatus.SUCCEEDED, completed_at=fixture.LATER))
        self.source_record("future-run-record", run="future-run")
        for record in ("future-observation", "future-run-record"):
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.claim(record, evidence=[m.EvidenceLink("doc", source_record_id=record)])

    def test_one_microsecond_corrections_preserve_source_and_world_cutoffs(self):
        for suffix, kind, valid_from in (("source", m.ClaimKind.SOURCE_STATEMENT, None),
                                        ("world", m.ClaimKind.DIRECT_OBSERVATION, "2026-01-01")):
            self.claim("old-" + suffix, series=suffix, kind=kind, valid_from=valid_from)
            self.claim("new-" + suffix, series=suffix, kind=kind, valid_from=valid_from,
                predicate="source_reported.old-" + suffix, recorded=JUST_AFTER,
                value=m.ScalarValue(m.ScalarType.TEXT, "corrected"))
        for cutoff, expected in ((JUST_BEFORE, set()), (ADMITTED, {"old-source"}),
                                 (JUST_AFTER, {"new-source"})):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(fixture.ids(r.known_source_claims(self.c, recorded_at=cutoff)), expected)
        for cutoff, expected in ((JUST_BEFORE, set()), (ADMITTED, {"old-world"}),
                                 (JUST_AFTER, {"new-world"})):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(fixture.ids(r.current_claims(self.c, as_of=AS_OF,
                    recorded_at=cutoff)), expected)
        early = self.report(recorded=ADMITTED)
        self.assertEqual(fixture.ids(early["recorded_history"]), {"old-source", "old-world"})
        self.assertTrue(all(claim["superseded_at"] is None for claim in early["recorded_history"]))
        later = self.report(recorded=JUST_AFTER)
        self.assertEqual(fixture.ids(later["recorded_history"]),
            {"old-source", "new-source", "old-world", "new-world"})
        self.assertEqual({claim["superseded_at"] for claim in later["recorded_history"]
            if claim["id"].startswith("old-")}, {JUST_AFTER})
        self.assertEqual(r.validate_database(self.c), [])

    def test_equal_instant_correction_with_timezone_offset_is_not_later(self):
        self.claim("old", series="revised")
        before = list(self.c.iterdump())
        with self.assertRaises(ValueError):
            self.claim("new", series="revised", predicate="source_reported.old", recorded=OFFSET_ADMITTED,
                value=m.ScalarValue(m.ScalarType.TEXT, "corrected"))
        self.assertEqual(list(self.c.iterdump()), before)

    def test_ingestion_duration_one_microsecond_is_valid(self):
        r.add_ingestion_run(self.c, m.IngestionRun("short", "source", ADMITTED,
            m.IngestionStatus.SUCCEEDED, completed_at=JUST_AFTER))
        self.assertEqual(self.c.execute("SELECT completed_at FROM ingestion_runs WHERE id='short'").fetchone()[0],
            JUST_AFTER)
        self.assertEqual(r.validate_database(self.c), [])

    def test_raw_ingestion_duration_and_clock_checks(self):
        sql = "INSERT INTO ingestion_runs(id,source_id,started_at,status,completed_at) VALUES (?, 'source', ?, 'succeeded', ?)"
        self.c.execute(sql, ("valid", ADMITTED, JUST_AFTER))
        for identifier, start, end in (("reverse", ADMITTED, JUST_BEFORE), ("equal", ADMITTED, OFFSET_ADMITTED),
            ("naive-start", "2026-09-09T05:52:00.817388", JUST_AFTER),
            ("naive-end", ADMITTED, "2026-09-09T05:52:00.817389"), ("invalid", ADMITTED, "bad")):
            with self.subTest(identifier=identifier), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute(sql, (identifier, start, end))

    def test_raw_claim_clock_and_subject_series_guards(self):
        self.series("normal")
        self.series("future-series", created=JUST_AFTER)
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        self.series("future-subject", entity="future")
        for identifier, series, recorded in (("future-series", "future-series", ADMITTED),
            ("future-subject", "future-subject", ADMITTED), ("naive", "normal", "2026-09-09T05:52:00.817388"),
            ("invalid", "normal", "not-a-date")):
            with self.subTest(identifier=identifier), self.assertRaises(sqlite3.IntegrityError):
                self.raw_claim(identifier, series, recorded=recorded)

    def test_raw_future_evidence_and_dependency_are_rejected(self):
        self.claim("child")
        self.claim("parent", recorded=JUST_AFTER)
        self.document("future", retrieved_at=JUST_AFTER)
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO claim_evidence(id,claim_version_id,source_document_id,role) "
                "VALUES ('future-evidence','child','future','context')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO claim_dependencies VALUES ('child','parent','derived_from')")

    def test_raw_future_source_record_and_run_are_rejected(self):
        self.claim("child")
        self.add_run()
        self.source_record("future-observation", observed=JUST_AFTER)
        r.add_ingestion_run(self.c, m.IngestionRun("future-run", "source", JUST_AFTER,
            m.IngestionStatus.SUCCEEDED, completed_at=fixture.LATER))
        self.source_record("future-run-record", run="future-run")
        for record in ("future-observation", "future-run-record"):
            with self.subTest(record=record), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute("INSERT INTO claim_evidence "
                    "(id,claim_version_id,source_document_id,source_record_id,role) "
                    "VALUES (?, 'child', 'doc', ?, 'context')", (record, record))

    def test_validator_independently_detects_microsecond_lineage_corruption(self):
        self.claim("child")
        self.claim("parent", recorded=JUST_AFTER)
        self.document("future", retrieved_at=JUST_AFTER)
        self.add_run()
        self.source_record("future-observation", observed=JUST_AFTER)
        r.add_ingestion_run(self.c, m.IngestionRun("future-run", "source", JUST_AFTER,
            m.IngestionStatus.SUCCEEDED, completed_at=fixture.LATER))
        self.source_record("future-run-record", run="future-run")
        self.assertEqual(r.validate_database(self.c), [])
        cases = (
            ("claim_evidence", "INSERT INTO claim_evidence(id,claim_version_id,source_document_id,role) "
                "VALUES ('bad','child','future','context')", "retrieved after"),
            ("claim_dependencies", "INSERT INTO claim_dependencies VALUES ('child','parent','derived_from')",
                "later-recorded claim"),
            ("claim_evidence", "INSERT INTO claim_evidence "
                "(id,claim_version_id,source_document_id,source_record_id,role) "
                "VALUES ('bad','child','doc','future-observation','context')", "observed after"),
            ("claim_evidence", "INSERT INTO claim_evidence "
                "(id,claim_version_id,source_document_id,source_record_id,role) "
                "VALUES ('bad','child','doc','future-run-record','context')", "later-started ingestion run"),
        )
        for table, statement, expected in cases:
            with self.subTest(expected=expected):
                # Deliberate corruption stays inside a rollback-only disposable fixture.
                self.c.execute("SAVEPOINT corrupt_fixture")
                try:
                    triggers = [row[0] for row in self.c.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,))]
                    for trigger in triggers:
                        self.c.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
                    self.c.execute(statement)
                    self.assertTrue(any("child" in error and expected in error
                        for error in r.validate_database(self.c)))
                finally:
                    self.c.execute("ROLLBACK TO corrupt_fixture")
                    self.c.execute("RELEASE corrupt_fixture")
        self.assertEqual(r.validate_database(self.c), [])

    def test_raw_future_relationship_and_constraint_targets_are_rejected(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        values = (m.RelationshipValue("future", "part_of"), m.ConstraintValue("water",
            m.ConstraintStatus.POTENTIAL, m.ConstraintSeverity.UNKNOWN, "Synthetic", "future"))
        for value in values:
            identifier = value.kind.value
            self.series(identifier, value=value)
            self.raw_claim(identifier, identifier, value=value)
            self.c.execute("INSERT INTO claim_values VALUES (?, ?)", (identifier, identifier))
            with self.subTest(kind=identifier), self.assertRaises(sqlite3.IntegrityError):
                if identifier == "relationship":
                    self.c.execute("INSERT INTO relationship_values "
                        "(claim_version_id,object_entity_id,relationship_type) VALUES (?, 'future', 'part_of')",
                        (identifier,))
                else:
                    self.c.execute("INSERT INTO constraint_values "
                        "(claim_version_id,constraint_type,status,severity,description,constrained_entity_id) "
                        "VALUES (?, 'water', 'potential', 'unknown', 'Synthetic', 'future')", (identifier,))

    def test_raw_supersession_one_microsecond_is_valid_and_equal_or_naive_is_not(self):
        self.claim("valid")
        self.c.execute("UPDATE claim_versions SET superseded_at=? WHERE id='valid'", (JUST_AFTER,))
        for identifier, superseded in (("equal", OFFSET_ADMITTED), ("before", JUST_BEFORE),
            ("naive", "2026-09-09T05:52:00.817389"), ("invalid", "bad")):
            self.claim(identifier)
            with self.subTest(identifier=identifier), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute("UPDATE claim_versions SET superseded_at=? WHERE id=?", (superseded, identifier))

    def test_resolution_run_one_microsecond_completion_is_valid(self):
        self.add_run(completed=JUST_BEFORE, accepted=JUST_BEFORE)
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("resolution", ADMITTED, "synthetic"))
        r.add_entity_resolution_run_input(self.c, m.EntityResolutionRunInput("resolution", "run"))
        self.assertTrue(r.finalize_entity_resolution_run(self.c, "resolution",
            status=m.EntityResolutionStatus.SUCCEEDED, completed_at=JUST_AFTER))
        self.assertEqual(r.validate_database(self.c), [])

    def test_resolution_input_completed_one_microsecond_after_start_is_rejected(self):
        self.add_run(completed=JUST_AFTER, accepted=JUST_AFTER)
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("resolution", ADMITTED, "synthetic"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO entity_resolution_run_inputs VALUES ('resolution','run')")

    def test_raw_resolution_run_rejects_naive_or_invalid_start(self):
        for timestamp in ("2026-09-09T05:52:00.817388", "not-a-date"):
            with self.subTest(timestamp=timestamp), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute("INSERT INTO entity_resolution_runs(id,started_at,status,resolver_version) "
                    "VALUES ('resolution', ?, 'running', 'synthetic')", (timestamp,))

    def test_assignment_one_microsecond_supersession_and_exact_cutoffs(self):
        self.identity_decision()
        self.assignment()
        self.assertTrue(r.supersede_source_entity_assignment(self.c, "assignment", JUST_AFTER))
        for cutoff, expected in ((JUST_BEFORE, set()), (ADMITTED, {"assignment"}), (JUST_AFTER, set())):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(fixture.ids(r.current_source_entity_assignments(self.c,
                    as_of=AS_OF, recorded_at=cutoff)), expected)
        self.assertEqual(r.validate_database(self.c), [])

    def test_raw_assignment_predating_decision_one_microsecond_is_rejected(self):
        self.identity_decision()
        sql = """INSERT INTO source_entity_assignments
            (id,source_record_id,observed_entity_id,canonical_entity_id,decision_id,valid_from,recorded_at)
            VALUES ('assignment','record','company','canonical','decision','2026-01-01',?)"""
        for timestamp in (JUST_BEFORE, "2026-09-09T05:52:00.817388", "not-a-date"):
            with self.subTest(timestamp=timestamp), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute(sql, (timestamp,))
        self.c.execute(sql, (OFFSET_ADMITTED,))

    def test_release_runs_and_candidates_wait_for_exact_completion(self):
        self.identity_decision(completed=ADMITTED, decided=JUST_AFTER)
        for reader, expected in ((release._entity_resolution_runs, {"resolution"}),
                                 (release._entity_resolution_candidates, {"candidate"})):
            for cutoff, visible in ((JUST_BEFORE, set()), (ADMITTED, expected), (OFFSET_ADMITTED, expected)):
                with self.subTest(reader=reader.__name__, cutoff=cutoff):
                    self.assertEqual(fixture.ids(reader(self.c, recorded_at=cutoff)), visible)
        self.assertEqual(r.validate_database(self.c), [])

    def test_release_decisions_wait_for_exact_decision_clock(self):
        self.identity_decision()
        self.assertEqual(release._entity_resolution_decisions(self.c, recorded_at=JUST_BEFORE), [])
        for cutoff in (ADMITTED, OFFSET_ADMITTED):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(fixture.ids(release._entity_resolution_decisions(self.c,
                    recorded_at=cutoff)), {"decision"})

    def test_release_assignments_wait_for_admission_and_mask_future_supersession(self):
        self.identity_decision()
        self.assignment()
        r.supersede_source_entity_assignment(self.c, "assignment", JUST_AFTER)
        self.assertEqual(release._source_entity_assignments(self.c, as_of=AS_OF, recorded_at=JUST_BEFORE), [])
        for cutoff, expected_supersession in ((ADMITTED, None), (OFFSET_ADMITTED, None),
                                              (JUST_AFTER, JUST_AFTER)):
            with self.subTest(cutoff=cutoff):
                rows = release._source_entity_assignments(self.c, as_of=AS_OF, recorded_at=cutoff)
                self.assertEqual(fixture.ids(rows), {"assignment"})
                self.assertEqual(rows[0]["superseded_at"], expected_supersession)
        self.assertEqual(r.validate_database(self.c), [])

    def test_release_assignment_admission_is_separate_from_known_decision(self):
        self.identity_decision()
        self.assignment(recorded=JUST_AFTER)
        self.assertEqual(fixture.ids(release._entity_resolution_decisions(self.c,
            recorded_at=ADMITTED)), {"decision"})
        self.assertEqual(release._source_entity_assignments(self.c, as_of=AS_OF, recorded_at=ADMITTED), [])
        self.assertEqual(fixture.ids(release._source_entity_assignments(self.c, as_of=AS_OF,
            recorded_at=JUST_AFTER)), {"assignment"})

    def test_reviewed_relationship_runs_use_exact_completion_and_chronological_order(self):
        self.reviewed_run("z-earlier", completed=ADMITTED)
        self.reviewed_run("a-later", completed=JUST_AFTER)
        for cutoff, expected in ((JUST_BEFORE, []), (ADMITTED, ["z-earlier"]),
                                 (JUST_AFTER, ["z-earlier", "a-later"])):
            with self.subTest(cutoff=cutoff):
                rows, claims = release._reviewed_relationship_runs(self.c, recorded_at=cutoff)
                self.assertEqual([row["review_ingestion_run_id"] for row in rows], expected)
                self.assertEqual(claims, set())

    def test_reviewed_relationship_runs_wait_for_every_effect_to_be_known(self):
        self.claim("review-effect", recorded=JUST_AFTER)
        self.reviewed_run("review", completed=ADMITTED, parameters={
            "created_claims": [{"claim_id": "review-effect", "recorded_at": JUST_AFTER}],
            "reaffirmed_claims": [], "superseded_claim_ids": []})
        self.assertEqual(release._reviewed_relationship_runs(self.c, recorded_at=ADMITTED), ([], set()))
        rows, claims = release._reviewed_relationship_runs(self.c, recorded_at=JUST_AFTER)
        self.assertEqual([row["review_ingestion_run_id"] for row in rows], ["review"])
        self.assertEqual(claims, {"review-effect"})

    def test_geojson_source_terms_truncate_future_dependency_and_its_ancestors(self):
        self.document("ancestor-document", license="ancestor-license", metadata={"attribution": "Ancestor"})
        self.document("parent-document", license="parent-license", metadata={"attribution": "Parent"})
        self.claim("ancestor", recorded=JUST_BEFORE, evidence=[m.EvidenceLink("ancestor-document")])
        self.claim("parent", dependencies=["ancestor"], evidence=[m.EvidenceLink("parent-document")])
        self.claim("seed", recorded=fixture.LATER, dependencies=["parent"])
        self.assertEqual(service._geojson_source_terms(self.c, claim_ids=["seed"],
            recorded_at=JUST_BEFORE), ([], []))
        self.assertEqual(service._geojson_source_terms(self.c, claim_ids=["seed"], recorded_at=ADMITTED),
            (["Ancestor", "Parent"], ["ancestor-license", "parent-license"]))
        self.assertEqual(r.validate_database(self.c), [])

    def test_geojson_source_terms_wait_for_exact_document_retrieval(self):
        self.document("terms", retrieved_at=ADMITTED, license="synthetic-license",
            metadata={"attribution": "Synthetic attribution"})
        self.claim("seed", recorded=JUST_AFTER, evidence=[m.EvidenceLink("terms")])
        self.assertEqual(service._geojson_source_terms(self.c, claim_ids=["seed"],
            recorded_at=JUST_BEFORE), ([], []))
        for cutoff in (ADMITTED, OFFSET_ADMITTED):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(service._geojson_source_terms(self.c, claim_ids=["seed"], recorded_at=cutoff),
                    (["Synthetic attribution"], ["synthetic-license"]))
        self.assertEqual(r.validate_database(self.c), [])


class LegacyKnowledgeClockMigrationTests(_ClockFixture, unittest.TestCase):
    target_version = 9

    def test_schema9_retains_legacy_admission_and_correction_behavior(self):
        self.document("future", retrieved_at=JUST_AFTER)
        self.claim("legacy", evidence=[m.EvidenceLink("future")])
        self.claim("old", series="revised")
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            self.claim("new", series="revised", predicate="source_reported.old", recorded=JUST_AFTER,
                value=m.ScalarValue(m.ScalarType.TEXT, "corrected"))
        self.assertEqual(fixture.ids(r.known_source_claims(self.c, recorded_at=JUST_BEFORE)), set())
        self.assertEqual(fixture.ids(r.known_source_claims(self.c, recorded_at=ADMITTED)), {"legacy", "old"})
        self.assertEqual(database.schema_version(self.c), 9)

    def test_schema9_release_identity_readers_retain_legacy_rounded_cutoffs(self):
        self.identity_decision(completed=ADMITTED, decided=ADMITTED)
        self.assignment()
        r.supersede_source_entity_assignment(self.c, "assignment", fixture.LATER)
        self.assertEqual(fixture.ids(release._entity_resolution_runs(self.c,
            recorded_at=JUST_BEFORE)), {"resolution"})
        self.assertEqual(fixture.ids(release._entity_resolution_candidates(self.c,
            recorded_at=JUST_BEFORE)), {"candidate"})
        self.assertEqual(fixture.ids(release._entity_resolution_decisions(self.c,
            recorded_at=JUST_BEFORE)), {"decision"})
        assignments = release._source_entity_assignments(self.c, as_of=AS_OF, recorded_at=JUST_BEFORE)
        self.assertEqual(fixture.ids(assignments), {"assignment"})
        self.assertIsNone(assignments[0]["superseded_at"])

    def test_schema9_review_runs_retain_legacy_rounded_selection_and_order(self):
        self.reviewed_run("z-earlier", completed=ADMITTED)
        self.reviewed_run("a-later", completed=JUST_AFTER)
        rows, claims = release._reviewed_relationship_runs(self.c, recorded_at=JUST_BEFORE)
        self.assertEqual([row["review_ingestion_run_id"] for row in rows], ["a-later", "z-earlier"])
        self.assertEqual(claims, set())

    def test_schema9_geojson_source_terms_retain_legacy_rounded_dependency_and_document_cutoffs(self):
        self.document("terms", retrieved_at=ADMITTED, license="synthetic-license",
            metadata={"attribution": "Synthetic attribution"})
        self.claim("parent", evidence=[m.EvidenceLink("terms")])
        self.claim("seed", recorded=fixture.LATER, dependencies=["parent"])
        self.assertEqual(service._geojson_source_terms(self.c, claim_ids=["seed"], recorded_at=JUST_BEFORE),
            (["Synthetic attribution"], ["synthetic-license"]))

    def test_valid_migration_preserves_every_payload_row_and_foreign_key(self):
        self.identity_decision()
        self.assignment()
        self.claim("ordinary")
        self.claim("derived", dependencies=["ordinary"], recorded=fixture.LATER)
        self.c.commit()
        def payload_rows():
            tables = [row[0] for row in self.c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' ORDER BY name")]
            return {table: sorted([tuple(row) for row in self.c.execute('SELECT * FROM "' + table + '"')], key=repr)
                    for table in tables}
        before = payload_rows()
        ledger = [tuple(row) for row in self.c.execute("SELECT * FROM schema_migrations ORDER BY version")]
        self.assertEqual(database.apply_migrations(self.c, target_version=10), [10])
        self.assertEqual(payload_rows(), before)
        self.assertEqual([tuple(row) for row in self.c.execute("SELECT * FROM schema_migrations "
            "WHERE version < 10 ORDER BY version")], ledger)
        self.assertEqual(self.c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(r.validate_database(self.c), [])
        self.assertEqual(database.apply_migrations(self.c, target_version=10), [])

    def test_migration_rejects_existing_future_evidence_atomically(self):
        self.document("future", retrieved_at=JUST_AFTER)
        self.claim("legacy", evidence=[m.EvidenceLink("future")])
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_future_dependency_atomically(self):
        self.claim("parent", recorded=JUST_AFTER)
        self.claim("child", dependencies=["parent"])
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_future_subject_atomically(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        self.claim("legacy", entity="future")
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_future_series_atomically(self):
        self.series("future", created=JUST_AFTER)
        self.insert("legacy", "future")
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_future_relationship_target_atomically(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        self.claim("legacy", value=m.RelationshipValue("future", "part_of"))
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_future_constraint_target_atomically(self):
        r.add_entity(self.c, m.Entity("future", m.EntityKind.PROJECT, "fixture:future", JUST_AFTER))
        self.claim("legacy", value=m.ConstraintValue("water", m.ConstraintStatus.POTENTIAL,
            m.ConstraintSeverity.UNKNOWN, "Synthetic", "future"))
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_existing_naive_ingestion_start_atomically(self):
        self.c.execute("INSERT INTO ingestion_runs(id,source_id,started_at,status) "
            "VALUES ('naive','source','2026-09-09T05:52:00.817388','running')")
        self.assert_failed_migration_preserves_state()

    def legacy_candidate(self, *, started=ADMITTED, recorded=JUST_BEFORE, created=ADMITTED,
                         observed=CREATED, target_created=CREATED):
        self.add_run(completed="2026-01-01T00:00:01Z", accepted="2026-01-01T00:00:01Z")
        self.source_record(observed=observed)
        self.claim("name", entity="company", recorded=recorded,
            evidence=[m.EvidenceLink("doc", source_record_id="record")])
        target = "canonical"
        if target_created != CREATED:
            target = "future-canonical"
            r.add_entity(self.c, m.Entity(target, m.EntityKind.ORGANIZATION,
                "fixture:" + target, target_created))
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("resolution", started, "synthetic-resolver"))
        r.add_entity_resolution_run_input(self.c, m.EntityResolutionRunInput("resolution", "run"))
        r.add_entity_resolution_candidate(self.c, m.EntityResolutionCandidate("candidate", "resolution",
            "record", "company", target, 1.0, 1, created))

    def test_migration_rejects_existing_future_resolution_input_atomically(self):
        self.add_run(completed=JUST_AFTER, accepted=JUST_AFTER)
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("resolution", ADMITTED, "synthetic"))
        r.add_entity_resolution_run_input(self.c, m.EntityResolutionRunInput("resolution", "run"))
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_candidate_predating_resolution_one_microsecond_atomically(self):
        self.legacy_candidate(created=JUST_BEFORE)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_candidate_using_future_claim_one_microsecond_atomically(self):
        self.legacy_candidate(recorded=JUST_AFTER, created=JUST_AFTER)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_candidate_using_future_record_one_microsecond_atomically(self):
        self.legacy_candidate(recorded=JUST_AFTER, observed=JUST_AFTER, created=JUST_AFTER)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_candidate_using_future_target_one_microsecond_atomically(self):
        self.legacy_candidate(target_created=JUST_AFTER, created=JUST_AFTER)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_completion_predating_candidate_one_microsecond_atomically(self):
        self.legacy_candidate(started="2026-09-08T01:00:00Z", recorded="2026-09-08T00:00:00Z",
            created=JUST_AFTER)
        r.finalize_entity_resolution_run(self.c, "resolution", status=m.EntityResolutionStatus.SUCCEEDED,
            completed_at=ADMITTED)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_decision_predating_completion_one_microsecond_atomically(self):
        self.identity_decision(completed=JUST_AFTER)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_assignment_predating_decision_one_microsecond_atomically(self):
        self.identity_decision()
        self.assignment(recorded=JUST_BEFORE)
        self.assert_failed_migration_preserves_state()

    def test_migration_rejects_assignments_overlapping_one_microsecond_atomically(self):
        self.identity_decision(completed="2026-09-08T05:00:00Z", decided="2026-09-08T06:00:00Z")
        self.assignment(recorded="2026-09-08T07:00:00Z")
        r.supersede_source_entity_assignment(self.c, "assignment", JUST_AFTER)
        r.add_entity_resolution_run(self.c, m.EntityResolutionRun("second-resolution",
            "2026-09-08T03:00:00Z", "synthetic-resolver"))
        r.add_entity_resolution_run_input(self.c, m.EntityResolutionRunInput("second-resolution", "run"))
        r.add_entity_resolution_candidate(self.c, m.EntityResolutionCandidate("second-candidate",
            "second-resolution", "record", "company", "canonical", 1.0, 1, "2026-09-08T04:00:00Z"))
        r.finalize_entity_resolution_run(self.c, "second-resolution", status=m.EntityResolutionStatus.SUCCEEDED,
            completed_at="2026-09-08T05:00:00Z")
        r.add_entity_resolution_decision(self.c, m.EntityResolutionDecision("second-decision", "second-candidate",
            m.ResolutionDecisionOutcome.MATCH, "2026-09-08T06:00:00Z", "reviewer", "Synthetic match"))
        r.add_source_entity_assignment(self.c, m.SourceEntityAssignment("second-assignment", "record", "company",
            "canonical", "second-decision", "2026-01-01", ADMITTED))
        self.assert_failed_migration_preserves_state()

    def assert_failed_migration_preserves_state(self):
        self.c.commit()
        before = list(self.c.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            database.apply_migrations(self.c, target_version=10)
        self.assertEqual(database.schema_version(self.c), 9)
        self.assertEqual(list(self.c.iterdump()), before)
        self.assertFalse(self.c.in_transaction)
        self.assertEqual(self.c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.c.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
