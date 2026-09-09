"""Timestamp ordering selects source metadata by instant, not timestamp spelling."""

from __future__ import annotations

import contextlib
import json
import unittest
from datetime import datetime

from semiconductor_atlas import coverage, database, models as m, release, repository as r


CREATED = "2026-01-01T00:00:00Z"
CUTOFF = "2026-09-12T00:00:00Z"
AS_OF = "2026-09-11"
SCOPES = (
    ("epa-frs", "epa_frs_candidate_scope", "snapshot_candidate_count"),
    ("taiwan-moenv-ems", "taiwan_moenv_candidate_scope", "snapshot_candidate_count"),
    ("taiwan-ida-factory", "taiwan_factory_candidate_scope", "snapshot_candidate_count"),
    ("taiwan-mof-tax", "taiwan_mof_tax_registration_scope", "snapshot_matched_count"),
)
PAIRS = {
    "whole-second": (("run-z", "2026-09-09T12:00:00Z", 11),
                     ("run-a", "2026-09-09T12:00:00.000001Z", 22)),
    "offset": (("run-z", "2026-09-09T12:00:00+01:00", 11),
               ("run-a", "2026-09-09T11:00:00.000001Z", 22)),
    "same-instant": (("run-z", "2026-09-09T05:00:00-07:00", 11),
                     ("run-a", "2026-09-09T12:00:00Z", 22)),
}


def instant(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


@contextlib.contextmanager
def fixture(version):
    connection, _ = database.initialize(":memory:", target_version=version)
    try:
        yield connection
    finally:
        connection.close()


def source(connection, family="epa-frs"):
    r.add_source_family(connection, m.SourceFamily(family, family, "Synthetic family", CREATED))
    r.add_source(connection, m.Source(family, family, family, "Synthetic source", "Synthetic publisher",
        "https://example.test", CREATED))
    return family


def document(connection, identifier, *, source_id="epa-frs", retrieved=CREATED):
    # Raw inserts exercise supported aware offset strings without model normalization.
    connection.execute("""INSERT INTO source_documents
        (id,source_id,document_url,title,retrieved_at,content_sha256)
        VALUES (?, ?, ?, 'Synthetic source', ?, ?)""",
        (identifier, source_id, "https://example.test/" + identifier, retrieved, "a" * 64))


def run(connection, identifier, started, count=11, *, source_id="epa-frs", document_id="doc"):
    parameters = dict(as_of_date="2026-09-09", dataset_updated_at="2026-09-09T00:00:00+08:00",
        source_updated_at="2026-09-09T00:00:00+08:00", publisher_date="2026-09-09", candidate_count=count,
        upstream_total_rows=100, deduplicated_variant_count=count, matched_count=count,
        source_scope=identifier, complete_refresh=(count == 22))
    connection.execute("""INSERT INTO ingestion_runs
        (id,source_id,input_document_id,started_at,completed_at,status,code_version,parameters_json)
        VALUES (?, ?, ?, ?, '2026-09-10T00:00:00Z', 'succeeded', ?, ?)""",
        (identifier, source_id, document_id, started, identifier + "-producer", json.dumps(parameters)))
    if database.schema_version(connection) >= 3:
        r.add_ingestion_run_document(connection, m.IngestionRunDocument(identifier, document_id, "primary"))


def report(connection, *, cutoff=CUTOFF):
    return coverage.coverage_report(connection, as_of=AS_OF, recorded_at=cutoff,
        claims=[], entities=[], summary={"schema_version": database.schema_version(connection),
            "source_document_count": connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0],
            "entities_total": 0, "current_claim_count": 0})


def expected_order(rows, version):
    return sorted(rows, key=lambda row: (instant(row[1]) if version >= 10 else row[1], row[0]))


def identity_fixture(connection, pairs):
    source(connection)
    document(connection, "doc")
    r.add_ingestion_run(connection, m.IngestionRun("input", "epa-frs", CREATED,
        m.IngestionStatus.SUCCEEDED, completed_at="2026-01-01T00:00:01Z", input_document_id="doc"))
    r.add_source_record(connection, m.SourceRecord("record", "input", "doc", "record", CREATED,
        m.source_record_payload_sha256({})))
    for identifier in ("observed", "canonical"):
        r.add_entity(connection, m.Entity(identifier, m.EntityKind.ORGANIZATION, identifier, CREATED))
    r.add_claim_series(connection, m.ClaimSeries("name-series", "observed", "name-series", "organization.name",
        m.ValueKind.SCALAR, CREATED))
    r.insert_claim(connection, m.ClaimVersion("name", "name-series", "2026-01-01", "2026-01-01T00:00:02Z",
        m.ClaimKind.SOURCE_STATEMENT, "synthetic", None), m.ScalarValue(m.ScalarType.TEXT, "Synthetic"),
        evidence=[m.EvidenceLink("doc", source_record_id="record")])
    for identifier, started, _ in pairs:
        connection.execute("""INSERT INTO entity_resolution_runs(id,started_at,status,resolver_version)
            VALUES (?, ?, 'running', 'synthetic')""", (identifier, started))
        r.add_entity_resolution_run_input(connection, m.EntityResolutionRunInput(identifier, "input"))
        r.add_entity_resolution_candidate(connection, m.EntityResolutionCandidate("candidate-" + identifier,
            identifier, "record", "observed", "canonical", 1.0, 1, "2026-09-10T00:00:00Z"))
        r.finalize_entity_resolution_run(connection, identifier, status=m.EntityResolutionStatus.SUCCEEDED,
            completed_at="2026-09-10T00:01:00Z")
        connection.execute("""INSERT INTO entity_resolution_decisions
            (id,candidate_id,outcome,decided_at,decided_by,reason)
            VALUES (?, ?, 'match', ?, 'synthetic', 'Synthetic match')""",
            ("decision-" + identifier, "candidate-" + identifier, started.replace("2026-09-09", "2026-09-10")))


class ChronologicalMetadataTests(unittest.TestCase):
    def test_order_helper_retains_every_legacy_schema_and_uses_exact_schema10_clock(self):
        for version in range(1, 11):
            with self.subTest(version=version), fixture(version) as c:
                self.assertEqual(database.chronological_order_sql(c, "runs.started_at"),
                    "atlas_timestamp_us(runs.started_at)" if version >= 10 else "runs.started_at")

    def test_four_scope_selectors_use_latest_run_and_first_acceptance_by_instant(self):
        for version in (9, 10):
            for label, pairs in PAIRS.items():
                for reverse in (False, True):
                    with self.subTest(version=version, case=label, reverse=reverse), fixture(version) as c:
                        for family, _, _ in SCOPES:
                            source(c, family)
                            document(c, family, source_id=family)
                            for identifier, stamp, count in (reversed(pairs) if reverse else pairs):
                                run(c, family + identifier, stamp, count, source_id=family, document_id=family)
                        self.assertEqual(r.validate_database(c), [])
                        result = report(c)
                        selected = expected_order(pairs, version)[-1]
                        first = (expected_order(pairs, version)[0][1] if version >= 10
                                 else min(row[1] for row in pairs))
                        for family, key, count_key in SCOPES:
                            scope = result[key]
                            self.assertEqual(scope["processing_run_id"], family + selected[0])
                            self.assertEqual(scope["processing_run_accepted_at"], selected[1])
                            self.assertEqual(scope[count_key], selected[2])
                            self.assertEqual(scope["source_document_first_accepted_at"], first)
                            if family != "epa-frs":
                                self.assertEqual(scope["source_scope"], family + selected[0])
                                self.assertEqual(scope["complete_refresh"], selected[2] == 22)

    def test_scope_retrieval_tiebreak_uses_instant_before_document_id(self):
        for version in (9, 10):
            for label, pairs in PAIRS.items():
                with self.subTest(version=version, case=label), fixture(version) as c:
                    for family, _, _ in SCOPES:
                        source(c, family)
                        for identifier, stamp, count in pairs:
                            document_id = family + identifier
                            document(c, document_id, source_id=family, retrieved=stamp)
                            run(c, document_id, "2026-09-09T13:00:00Z", count,
                                source_id=family, document_id=document_id)
                    result = report(c)
                    selected = expected_order(pairs, version)[-1]
                    for family, key, count_key in SCOPES:
                        self.assertEqual(result[key]["processing_run_id"], family + selected[0])
                        self.assertEqual(result[key][count_key], selected[2])

    def test_family_first_and_last_retrieval_and_acceptance_preserve_raw_clock_strings(self):
        for version in (9, 10):
            for label, pairs in PAIRS.items():
                for reverse in (False, True):
                    with self.subTest(version=version, case=label, reverse=reverse), fixture(version) as c:
                        source(c)
                        for identifier, stamp, count in (reversed(pairs) if reverse else pairs):
                            document(c, identifier, retrieved=stamp)
                            run(c, identifier, stamp, count, document_id=identifier)
                        family = report(c)["source_families"][0]
                        literals = [row[1] for row in pairs]
                        key = (lambda value: (instant(value), value)) if version >= 10 else (lambda value: value)
                        first, last = min(literals, key=key), max(literals, key=key)
                        self.assertEqual(family["first_retrieved_at"], first)
                        self.assertEqual(family["last_retrieved_at"], last)
                        self.assertEqual(family["first_database_accepted_at"], first)
                        self.assertEqual(family["last_database_accepted_at"], last)
                        self.assertEqual(family["document_count"], 2)
                        self.assertEqual(family["source_count"], 1)

    def test_family_acceptance_uses_each_documents_first_admission_not_its_reprocessing(self):
        for version in (9, 10):
            with self.subTest(version=version), fixture(version) as c:
                source(c)
                for identifier in ("first-doc", "second-doc"):
                    document(c, identifier)
                run(c, "first-run", "2026-09-09T12:00:00Z", document_id="first-doc")
                run(c, "reprocessing", "2026-09-09T12:00:00.000002Z", document_id="first-doc")
                run(c, "second-run", "2026-09-09T12:00:00.000001Z", document_id="second-doc")
                family = report(c)["source_families"][0]
                self.assertEqual(family["first_database_accepted_at"], "2026-09-09T12:00:00Z"
                    if version >= 10 else "2026-09-09T12:00:00.000001Z")
                self.assertEqual(family["last_database_accepted_at"], "2026-09-09T12:00:00.000001Z"
                    if version >= 10 else "2026-09-09T12:00:00.000002Z")

    def test_family_equal_instant_acceptance_literal_ties_are_deterministic(self):
        pairs = PAIRS["same-instant"]
        for reverse in (False, True):
            with self.subTest(reverse=reverse), fixture(10) as c:
                source(c)
                document(c, "doc")
                for identifier, stamp, count in (reversed(pairs) if reverse else pairs):
                    run(c, identifier, stamp, count)
                family = report(c)["source_families"][0]
                self.assertEqual(family["first_database_accepted_at"], min(row[1] for row in pairs))
                self.assertEqual(family["last_database_accepted_at"], min(row[1] for row in pairs))

    def test_scope_and_family_aggregates_do_not_include_future_runs(self):
        with fixture(10) as c:
            source(c)
            document(c, "doc")
            for row in PAIRS["whole-second"]:
                run(c, *row)
            early = report(c, cutoff="2026-09-09T12:00:00Z")
            self.assertEqual(early["epa_frs_candidate_scope"]["processing_run_id"], "run-z")
            self.assertEqual(early["epa_frs_candidate_scope"]["source_document_first_accepted_at"],
                "2026-09-09T12:00:00Z")
            self.assertEqual(early["source_families"][0]["last_database_accepted_at"], "2026-09-09T12:00:00Z")

    def test_release_processing_order_and_acceptance_producer_are_chronological(self):
        for version in (1, 4, 9, 10):
            for label, pairs in PAIRS.items():
                for reverse in (False, True):
                    with self.subTest(version=version, case=label, reverse=reverse), fixture(version) as c:
                        source(c)
                        document(c, "doc")
                        for row in (reversed(pairs) if reverse else pairs):
                            run(c, *row)
                        inputs = release._source_inputs(c, recorded_at=CUTOFF)
                        ordered = expected_order(pairs, version)
                        self.assertEqual(len(inputs), 1)
                        self.assertEqual(inputs[0]["accepted_by_run_id"], ordered[0][0])
                        self.assertEqual(inputs[0]["acceptance_code_version"], ordered[0][0] + "-producer")
                        self.assertEqual(inputs[0]["database_accepted_at"], ordered[0][1])
                        self.assertEqual([item["ingestion_run_id"] for item in inputs[0]["processing_runs"]],
                            [row[0] for row in ordered])

    def test_release_source_observations_follow_acceptance_instant(self):
        for version in (9, 10):
            for label, pairs in PAIRS.items():
                with self.subTest(version=version, case=label), fixture(version) as c:
                    source(c)
                    document(c, "doc")
                    for identifier, stamp, count in pairs:
                        run(c, identifier, stamp, count)
                        r.add_source_record(c, m.SourceRecord(identifier, identifier, "doc", identifier, CREATED,
                            m.source_record_payload_sha256({})))
                    rows = release._source_observations(c, recorded_at=CUTOFF)
                    self.assertEqual([row["ingestion_run_id"] for row in rows],
                        [row[0] for row in expected_order(pairs, version)])

    def test_release_identity_runs_candidates_and_decisions_order_by_instant_then_id(self):
        for version in (9, 10):
            for label, pairs in PAIRS.items():
                for reverse in (False, True):
                    with self.subTest(version=version, case=label, reverse=reverse), fixture(version) as c:
                        identity_fixture(c, tuple(reversed(pairs)) if reverse else pairs)
                        self.assertEqual(r.validate_database(c), [])
                        expected = [row[0] for row in expected_order(pairs, version)]
                        self.assertEqual([row["id"] for row in release._entity_resolution_runs(c,
                            recorded_at=CUTOFF)], expected)
                        self.assertEqual([row["resolution_run_id"] for row in release._entity_resolution_candidates(c,
                            recorded_at=CUTOFF)], expected)
                        self.assertEqual([row["resolution_run_id"] for row in release._entity_resolution_decisions(c,
                            recorded_at=CUTOFF)], expected)

    def test_release_assignment_history_orders_corrections_by_exact_recording_time(self):
        for version in (9, 10):
            for label in ("whole-second", "offset"):
                with self.subTest(version=version, case=label), fixture(version) as c:
                    pairs = PAIRS[label]
                    identity_fixture(c, pairs)
                    first = pairs[0][1].replace("2026-09-09", "2026-09-11")
                    second = pairs[1][1].replace("2026-09-09", "2026-09-11").replace(".000001", ".001")
                    for identifier, recorded, superseded in (("run-z", first, second), ("run-a", second, None)):
                        c.execute("""INSERT INTO source_entity_assignments
                            (id,source_record_id,observed_entity_id,canonical_entity_id,decision_id,
                             valid_from,recorded_at,superseded_at)
                            VALUES (?, 'record', 'observed', 'canonical', ?, '2026-01-01', ?, ?)""",
                            (identifier, "decision-" + identifier, recorded, superseded))
                    self.assertEqual(r.validate_database(c), [])
                    rows = release._source_entity_assignments(c, as_of=AS_OF, recorded_at=CUTOFF)
                    self.assertEqual([row["id"] for row in rows], ["run-z", "run-a"]
                        if version >= 10 else ["run-a", "run-z"])


if __name__ == "__main__":
    unittest.main()
