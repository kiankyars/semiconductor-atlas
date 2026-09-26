"""Engineering fixtures, not historical predictions or manufacturing truth."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind, ClaimSeries, ClaimVersion, DependencyLink, Entity, EntityKind,
    EvidenceLink, IngestionRun, IngestionStatus, MilestoneStatus, MilestoneValue,
    ScalarType, ScalarValue, Source, SourceDocument, SourceFamily, ValueKind,
)
from semiconductor_atlas.repository import (
    add_claim_series, add_entity, add_ingestion_run, add_source,
    add_source_document, add_source_family, insert_claim,
)


CREATED = "2026-01-01T00:00:00Z"
RECORDED = "2026-01-02T00:00:02Z"
STUDY_CLOCKS = ["2026-02-01T00:00:01Z", "2026-02-01T00:00:02Z"]
VINTAGE_CLOCKS = ["2026-02-02T00:00:01Z", "2026-02-02T00:00:02Z"]
REVIEW_CLOCKS = ["2026-05-02T00:00:01Z", "2026-05-02T00:00:02Z"]


class MilestoneBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.connection, _ = initialize(self.root / "fixture.sqlite")
        self.addCleanup(self.connection.close)
        add_source_family(self.connection, SourceFamily("family", "family", "Engineering fixture", CREATED))
        add_source(self.connection, Source("source", "family", "source", "Source", "Fixture publisher", "https://example.test", CREATED))
        add_source_document(self.connection, SourceDocument("doc", "source", "https://example.test/plan", "Fixture plan", CREATED, "a" * 64))
        add_ingestion_run(self.connection, IngestionRun("run", "source", "2026-01-02T00:00:01Z",
            status=IngestionStatus.SUCCEEDED, completed_at="2026-01-02T00:00:03Z", input_document_id="doc",
            parameters={"accepted_at": RECORDED}))
        self.roster = []
        for letter in "abcdefg":
            entity = "project-" + letter
            add_entity(self.connection, Entity(entity, EntityKind.PROJECT, "source-native:" + letter, CREATED, None, "run"))
            self._claim("scope-" + letter, entity, ScalarValue(ScalarType.TEXT, "Fab " + letter))
            self._claim("target-" + letter, entity, MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                "2026-04-01", None, "2026-04-30", "month", "April 2026"))
            self.roster.append({"case_id": letter, "project_entity_id": entity,
                "project_stable_key": "source-native:" + letter, "phase": "Fab " + letter,
                "event_type": "production_start", "project_group": "group-" + letter,
                "geography": "DE" if letter == "g" else "US",
                "partition": "geography_test" if letter == "g" else "time_test" if letter == "f" else "train",
                "scope_reason": "Reviewed engineering project/phase and location fixture.",
                "scope_claims": [self._ref("scope-" + letter)]})
        self.spec = {"study_id": "engineering-only", "roster_scope": "All seven declared synthetic projects, not a publisher census.",
            "reviewer": "Engineering fixture", "reviewed_at": "2026-02-01T00:00:00Z",
            "time_split_at": "2026-03-01T00:00:00Z", "held_out_geographies": ["DE"], "roster": self.roster}
        self.connection.commit()
        self.review_bodies = {}

    def _claim(self, identifier, entity, value, *, recorded=RECORDED, document="doc", parents=(), run="run"):
        series = ClaimSeries("series-" + identifier, entity, "series-" + identifier,
            "milestone.production_start" if isinstance(value, MilestoneValue) else "name", value.kind, CREATED)
        add_claim_series(self.connection, series)
        insert_claim(self.connection, ClaimVersion(identifier, series.id, "2026-01-01" if parents else None, recorded,
            ClaimKind.DERIVED_ESTIMATE if parents else ClaimKind.SOURCE_STATEMENT,
            "engineering-fixture", None, created_by_run_id=run), value,
            evidence=[] if parents else [EvidenceLink(document, locator="fixture", excerpt="Fixture statement")],
            dependencies=[DependencyLink(parent) for parent in parents])

    def _ref(self, identifier):
        row = self.connection.execute("SELECT value_sha256 FROM claim_versions WHERE id=?", (identifier,)).fetchone()
        return {"claim_id": identifier, "value_sha256": row[0]}

    def _study(self, spec=None, clocks=STUDY_CLOCKS):
        with mock.patch.object(benchmark, "_now", side_effect=clocks):
            return benchmark.freeze_study(self.connection, spec or self.spec)

    def _predictions(self, letters="abcde"):
        return [{"case_id": letter, "input_claims": [] if letter == "e" else [self._ref("target-" + letter)],
                 "prediction": None if letter == "e" else {"low": "2026-04-01", "base": "2026-04-15", "high": "2026-04-30"},
                 "abstention_reason": "Insufficient engineering input" if letter == "e" else None} for letter in letters]

    def _vintage(self, study=None, predictions=None, clocks=VINTAGE_CLOCKS, cutoff=STUDY_CLOCKS[1], horizon="2026-06-30"):
        study = study or self._study()
        with mock.patch.object(benchmark, "_now", side_effect=clocks):
            return benchmark.freeze_vintage(self.connection, study, evidence_cutoff_at=cutoff,
                horizon_end=horizon, predictions=predictions if predictions is not None else self._predictions(),
                model_artifact=b"engineering-only unfitted timing scenario", configuration={"method": "explicit_fixture"})

    def _outcomes(self):
        body = "é 工厂 Fab a began production on 2026-04-15. Fab b had not begun production as of 2026-04-30. Fab c was cancelled on 2026-04-02.".encode()
        digest = hashlib.sha256(body).hexdigest()
        excerpts = {"a": "Fab a began production on 2026-04-15.", "b": "Fab b had not begun production as of 2026-04-30.", "c": "Fab c was cancelled on 2026-04-02."}
        rows = []
        for case in self.roster:
            letter = case["case_id"]
            excerpt = excerpts.get(letter)
            evidence = []
            if excerpt:
                start = body.index(excerpt.encode())
                evidence.append({"document_sha256": digest, "document_url": "https://example.test/outcomes",
                    "published_at": "2026-05-01", "retrieved_at": "2026-05-01T10:00:00Z",
                    "start": start, "end": start + len(excerpt.encode()), "excerpt": excerpt,
                    "locator": "Exact engineering UTF-8 span"})
            rows.append({"case_id": letter, "project_entity_id": case["project_entity_id"],
                "phase": case["phase"], "event_type": case["event_type"],
                "status": {"a": "observed", "b": "right_censored", "c": "cancelled"}.get(letter, "unknown"),
                "event_interval": {"low": "2026-04-15", "high": "2026-04-15", "literal": "2026-04-15"} if letter == "a" else None,
                "censor_at": "2026-04-30" if letter == "b" else None,
                "cancellation_interval": {"low": "2026-04-02", "high": "2026-04-02", "literal": "2026-04-02"} if letter == "c" else None,
                "reason": "Separate engineering-only outcome review; unknowns remain unknown.", "evidence": evidence})
        return rows, {digest: body}

    def _review(self, vintage, outcomes=None, bodies=None, clocks=REVIEW_CLOCKS):
        default_rows, default_bodies = self._outcomes()
        local_bodies = default_bodies if bodies is None else bodies
        with mock.patch.object(benchmark, "_now", side_effect=clocks):
            review = benchmark.review_outcomes(vintage, reviewed_by="Engineering outcome fixture",
                prior_exposure="Fixture author saw predictions; not independent or blinded.",
                outcomes=default_rows if outcomes is None else outcomes,
                bodies=local_bodies)
        self.review_bodies[review["sha256"]] = local_bodies
        return review

    def _score(self, vintage, review):
        with mock.patch.object(benchmark, "_now", return_value="2026-05-03T00:00:00Z"):
            return benchmark.score(self.connection, vintage, review, bodies=self.review_bodies[review["sha256"]])

    def test_complete_prospective_chain_preserves_nulls_and_denominator(self):
        vintage = self._vintage()
        report = self._score(vintage, self._review(vintage))
        self.assertEqual(7, report["roster_cases"])
        self.assertEqual(5, report["eligible_cases"])
        self.assertEqual({"observed": 1, "right_censored": 1, "cancelled": 1, "unknown": 1,
                          "abstained": 1, "inactive_partition": 2}, report["counts"])
        self.assertEqual(1, report["exact_date_error_denominator"])
        self.assertEqual(0, report["exact_date_mean_absolute_error_days"])
        self.assertIsNone(report["probability_calibration"])
        self.assertFalse(report["boundaries"]["calibration_established"])
        self.assertIsNone(vintage["cases"][0]["lineage"]["claims"][0]["confidence"])
        self.assertIsNone(vintage["cases"][0]["lineage"]["claims"][0]["valid_from"])
        self.assertEqual(VINTAGE_CLOCKS[1], vintage["frozen_at"])

    def test_all_database_operations_work_with_writes_denied(self):
        before = "\n".join(self.connection.iterdump())
        self.connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in (
            sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        try:
            vintage = self._vintage()
            benchmark.verify_vintage(self.connection, vintage)
            self._score(vintage, self._review(vintage))
        finally:
            self.connection.set_authorizer(None)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        self.assertFalse(self.connection.in_transaction)

    def test_supplied_future_claim_is_rejected_against_database_cutoff(self):
        study = self._study()
        self._claim("future", "project-a", ScalarValue(ScalarType.TEXT, "later knowledge"), recorded="2026-02-03T00:00:00Z")
        self.connection.commit()
        predictions = self._predictions()
        predictions[0]["input_claims"] = [self._ref("future")]
        with self.assertRaisesRegex(ValueError, "claim admission.*after"):
            self._vintage(study, predictions)

    def test_transitive_future_dependency_is_rejected(self):
        study = self._study()
        self._claim("later-parent", "project-a", ScalarValue(ScalarType.TEXT, "future dependency"), recorded="2026-02-03T00:00:00Z")
        self._claim("early-child", "project-a", ScalarValue(ScalarType.TEXT, "misdated child"),
                    parents=("later-parent",), recorded="2026-02-03T00:00:00Z")
        # Deliberately corrupt this temporary fixture to exercise transitive validation.
        self.connection.execute("DROP TRIGGER claim_versions_content_immutable")
        self.connection.execute("UPDATE claim_versions SET recorded_at=? WHERE id='early-child'", (RECORDED,))
        self.connection.commit()
        rows = self._predictions(); rows[0]["input_claims"] = [self._ref("early-child")]
        with self.assertRaisesRegex(ValueError, "claim admission.*after"):
            self._vintage(study, rows)

    def test_document_retrieval_after_cutoff_is_rejected(self):
        study = self._study()
        add_source_document(self.connection, SourceDocument("future-doc", "source", "https://example.test/future", "Later", "2026-02-03T00:00:00Z", "b" * 64))
        self._claim("future-doc-claim", "project-a", ScalarValue(ScalarType.TEXT, "misdated claim"),
                    document="future-doc", recorded="2026-02-03T00:00:00Z")
        self.connection.execute("DROP TRIGGER claim_versions_content_immutable")
        self.connection.execute("UPDATE claim_versions SET recorded_at=? WHERE id='future-doc-claim'", (RECORDED,))
        self.connection.commit()
        rows = self._predictions(); rows[0]["input_claims"] = [self._ref("future-doc-claim")]
        with self.assertRaisesRegex(ValueError, "document retrieval.*after"):
            self._vintage(study, rows)

    def test_future_run_completion_and_actual_admission_are_rejected(self):
        study = self._study()
        for label, completion, admitted in (
            ("completion", "2026-02-03T00:00:00Z", RECORDED),
            ("admission", "2026-01-02T00:00:03Z", "2026-02-03T00:00:00Z"),
        ):
            with self.subTest(label=label):
                add_ingestion_run(self.connection, IngestionRun(label, "source", "2026-01-02T00:00:01Z",
                    status=IngestionStatus.SUCCEEDED, completed_at=completion, input_document_id="doc", parameters={"accepted_at": admitted}))
                self._claim("claim-" + label, "project-a", ScalarValue(ScalarType.TEXT, label), run=label)
                self.connection.commit()
                rows = self._predictions(); rows[0]["input_claims"] = [self._ref("claim-" + label)]
                with self.assertRaisesRegex(ValueError, "after the evidence cutoff"):
                    self._vintage(study, rows)

    def test_supplied_hash_and_cross_project_inputs_are_rejected(self):
        study = self._study()
        for reference in ({"claim_id": "target-a", "value_sha256": "f" * 64}, self._ref("target-b")):
            rows = self._predictions(); rows[0]["input_claims"] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self._vintage(study, rows)

    def test_root_cannot_bypass_cutoff_via_explicit_old_evidence_parameter(self):
        with self.assertRaisesRegex(ValueError, "precedes registration"):
            self._vintage(cutoff="2000-01-01T00:00:00Z")

    def test_clock_precision_and_prospective_horizon(self):
        for cutoff in ("2026-02-02T00:00:01.000001Z", "2026-02-01T00:00:00+00:00"):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                self._vintage(cutoff=cutoff)
        with self.assertRaisesRegex(ValueError, "follow actual freeze"):
            self._vintage(horizon="2024-12-31")
        rows = self._predictions(); rows[0]["prediction"]["low"] = "2026-02-02"
        with self.assertRaisesRegex(ValueError, "after actual freeze"):
            self._vintage(predictions=rows)

    def test_scope_event_geography_and_grouping_mismatch_are_rejected(self):
        mutations = [
            ("project_stable_key", "wrong"), ("event_type", "HVM"),
            ("partition", "geography_test"),
        ]
        for key, value in mutations:
            spec = copy.deepcopy(self.spec); spec["roster"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._study(spec)
        spec = copy.deepcopy(self.spec)
        spec["roster"][5]["project_group"] = spec["roster"][0]["project_group"]
        with self.assertRaisesRegex(ValueError, "group cannot cross"):
            self._study(spec)

    def test_same_project_phases_cannot_cross_splits(self):
        spec = copy.deepcopy(self.spec)
        spec["roster"][5].update(project_entity_id="project-a", project_stable_key="source-native:a",
            phase="Different phase", scope_claims=[self._ref("scope-a")])
        with self.assertRaisesRegex(ValueError, "project cannot cross"):
            self._study(spec)

    def test_milestone_input_cannot_alias_production_to_hvm(self):
        spec = copy.deepcopy(self.spec)
        spec["roster"][0]["event_type"] = "high_volume_production"
        study = self._study(spec)
        with self.assertRaisesRegex(ValueError, "exact expected event"):
            self._vintage(study)

    def test_missing_duplicate_or_inactive_prediction_is_not_quietly_dropped(self):
        for rows in (self._predictions()[:-1], self._predictions() + [self._predictions()[0]], self._predictions("abcdefg")):
            with self.subTest(count=len(rows)), self.assertRaises(ValueError):
                self._vintage(predictions=rows)

    def test_time_and_geography_holdouts_activate_without_reassigning_project_groups(self):
        study = self._study()
        vintage = self._vintage(study, self._predictions("fg"), clocks=["2026-03-02T00:00:01Z", "2026-03-02T00:00:02Z"])
        self.assertEqual(["f", "g"], [row["case"]["case_id"] for row in vintage["cases"] if row["eligible"]])
        self.assertEqual(7, len(vintage["cases"]))
        with self.assertRaises(ValueError):
            self._vintage(study, self._predictions(), clocks=["2026-03-02T00:00:01Z", "2026-03-02T00:00:02Z"])

    def test_study_and_vintage_cannot_cross_time_split_while_freezing(self):
        with self.assertRaisesRegex(ValueError, "before the time split"):
            self._study(clocks=["2026-03-01T00:00:00Z", "2026-03-01T00:00:01Z"])
        with self.assertRaisesRegex(ValueError, "crossed the split"):
            self._vintage(clocks=["2026-02-28T23:59:59.999999Z", "2026-03-01T00:00:00Z"])

    def test_outcome_identity_and_complete_roster_are_enforced(self):
        vintage = self._vintage()
        for key, value in (("project_entity_id", "project-b"), ("phase", "Fab other"), ("event_type", "high_volume_production")):
            rows, bodies = self._outcomes(); rows[0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity mismatch"):
                self._review(vintage, rows, bodies)
        rows, bodies = self._outcomes()
        with self.assertRaisesRegex(ValueError, "every roster case"):
            self._review(vintage, rows[:-1], bodies)

    def test_exact_utf8_evidence_and_body_binding_are_enforced(self):
        vintage = self._vintage()
        rows, bodies = self._outcomes()
        self.assertGreater(rows[0]["evidence"][0]["start"], len("é 工厂 "))
        for key, value in (("start", 0), ("excerpt", "Unsupported fabrication claim"), ("document_sha256", "f" * 64)):
            changed = copy.deepcopy(rows); changed[0]["evidence"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._review(vintage, changed, bodies)
        with self.assertRaisesRegex(ValueError, "missing or hash-mismatched"):
            self._review(vintage, rows, {key: value + b" changed" for key, value in bodies.items()})

    def test_future_outcome_and_source_clocks_cannot_be_admitted(self):
        vintage = self._vintage()
        for key, value in (("retrieved_at", "2027-01-01T00:00:00Z"), ("published_at", "2026-05-02")):
            rows, bodies = self._outcomes(); rows[0]["evidence"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._review(vintage, rows, bodies)
        with self.assertRaisesRegex(ValueError, "must follow"):
            self._review(vintage, clocks=[VINTAGE_CLOCKS[1]])

    def test_outcome_date_states_cannot_be_conflated(self):
        vintage = self._vintage()
        rows, bodies = self._outcomes()
        rows[2]["event_interval"] = rows[2]["cancellation_interval"]
        rows[2]["cancellation_interval"] = None
        with self.assertRaisesRegex(ValueError, "dates must match"):
            self._review(vintage, rows, bodies)
        rows, bodies = self._outcomes(); rows[3]["event_interval"] = rows[0]["event_interval"]
        with self.assertRaisesRegex(ValueError, "dates must match"):
            self._review(vintage, rows, bodies)

    def test_pre_vintage_outcomes_remain_visible_without_fake_historical_accuracy(self):
        vintage = self._vintage()
        rows, bodies = self._outcomes()
        raw = b"Fab a began production in 2024."
        digest = hashlib.sha256(raw).hexdigest()
        rows[0]["event_interval"] = {"low": "2024-01-01", "high": "2024-12-31", "literal": "2024"}
        rows[0]["evidence"] = [{"document_sha256": digest, "document_url": "https://example.test/historical",
            "published_at": "2026-04-01", "retrieved_at": "2026-04-02T00:00:00Z", "start": 0,
            "end": len(raw), "excerpt": raw.decode(), "locator": "Historical fixture"}]
        bodies[digest] = raw
        report = self._score(vintage, self._review(vintage, rows, bodies))
        self.assertEqual(1, report["counts"]["pre_vintage_or_straddling_outcome"])
        self.assertEqual(0, report["exact_date_error_denominator"])
        self.assertIsNone(report["exact_date_mean_absolute_error_days"])
        self.assertEqual(7, report["roster_cases"])

    def test_imprecise_outcome_has_error_bounds_not_invented_midpoint(self):
        vintage = self._vintage()
        rows, bodies = self._outcomes()
        rows[0]["event_interval"] = {"low": "2026-04-01", "high": "2026-04-30", "literal": "2026-04"}
        report = self._score(vintage, self._review(vintage, rows, bodies))
        self.assertEqual({"low": -15, "high": 14, "sign": "positive_means_predicted_later"}, report["cases"][0]["signed_error_days"])
        self.assertEqual(0, report["exact_date_error_denominator"])

    def test_later_unrelated_claims_do_not_change_frozen_source_replay(self):
        vintage = self._vintage()
        self._claim("new-unrelated", "project-a", ScalarValue(ScalarType.TEXT, "Later statement"), recorded="2026-03-01T00:00:00Z")
        self.connection.commit()
        self.assertEqual(vintage, benchmark.verify_vintage(self.connection, vintage))

    def test_tampered_frozen_graph_is_rejected_even_if_outer_hash_is_recomputed(self):
        vintage = self._vintage()
        changed = copy.deepcopy(vintage)
        changed["cases"][0]["lineage"]["claims"][0]["value"]["date_low"] = "2025-01-01"
        changed.pop("sha256")
        with self.assertRaisesRegex(ValueError, "lineage differs"):
            benchmark.verify_vintage(self.connection, benchmark._seal(changed))

    def test_sidecar_roundtrip_is_canonical_and_new_only(self):
        vintage = self._vintage()
        raw = benchmark.canonical_bytes(vintage)
        self.assertEqual(vintage, benchmark.verify_vintage(self.connection, raw))
        destination = self.root / "vintage.json"
        benchmark.write_new(destination, vintage)
        with self.assertRaises(FileExistsError):
            benchmark.write_new(destination, vintage)
        self.assertEqual(raw, destination.read_bytes())
        with self.assertRaisesRegex(ValueError, "canonical"):
            benchmark.verify_vintage(self.connection, json.dumps(vintage).encode())
        changed = copy.deepcopy(vintage); changed["horizon_end"] = "2026-07-01"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            benchmark.verify_vintage(self.connection, changed)

    def test_caller_transaction_is_not_committed_or_rolled_back(self):
        self.connection.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "caller transaction"):
            self._study()
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_returned_artifacts_do_not_mutate_shared_interpretation_boundaries(self):
        study = self._study()
        study["boundaries"]["calibration_established"] = True
        self.assertFalse(benchmark.BOUNDARIES["calibration_established"])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self._vintage(study)

    def test_abstention_requires_an_explicit_array_not_missing_input_state(self):
        rows = self._predictions(); rows[-1]["input_claims"] = None
        with self.assertRaisesRegex(ValueError, "must be an array"):
            self._vintage(predictions=rows)

    def test_horizon_straddling_outcome_is_retained_but_not_scored_as_exact(self):
        vintage = self._vintage()
        rows, bodies = self._outcomes()
        rows[0]["event_interval"] = {"low": "2026-04-01", "high": "2026-07-01", "literal": "2026"}
        for row in rows:
            for item in row["evidence"]:
                item["published_at"] = "2026-08-01"
                item["retrieved_at"] = "2026-08-01T00:00:00Z"
        review = self._review(vintage, rows, bodies, clocks=["2026-08-02T00:00:01Z", "2026-08-02T00:00:02Z"])
        with mock.patch.object(benchmark, "_now", return_value="2026-08-03T00:00:00Z"):
            report = benchmark.score(self.connection, vintage, review, bodies=bodies)
        self.assertEqual(1, report["counts"]["outcome_straddles_horizon"])
        self.assertEqual(0, report["exact_date_error_denominator"])
        self.assertIsNone(report["partitions"]["geography_test"]["exact_date_bias_days"])

    def test_outcome_sidecar_is_reference_only_and_score_requires_local_sources(self):
        vintage = self._vintage()
        review = self._review(vintage)
        _, bodies = self._outcomes()
        self.assertNotIn("bodies", review)
        self.assertNotIn("artifact_base64", review)
        self.assertEqual([{"sha256": digest, "bytes": len(raw)} for digest, raw in sorted(bodies.items())], review["source_bodies"])
        self.assertFalse(review["rights"]["source_bodies_embedded"])
        self.assertFalse(review["rights"]["redistribution_authorized"])
        with mock.patch.object(benchmark, "_now", return_value="2026-05-03T00:00:00Z"):
            with self.assertRaisesRegex(ValueError, "reference-only source manifest"):
                benchmark.score(self.connection, vintage, review, bodies={})
            damaged = {digest: raw[:-1] + b"!" for digest, raw in bodies.items()}
            with self.assertRaisesRegex(ValueError, "missing or hash-mismatched"):
                benchmark.score(self.connection, vintage, review, bodies=damaged)

    def test_transitive_held_out_project_cannot_be_laundered_through_training_root(self):
        study = self._study()
        self._claim("train-with-held-out-parent", "project-a", MilestoneValue(
            "production_start", MilestoneStatus.EXPECTED,
            "2026-04-01", None, "2026-04-30", "month", "April 2026"), parents=("target-g",))
        self.connection.commit()
        rows = self._predictions(); refs = [self._ref("train-with-held-out-parent")]
        rows[0]["input_claims"] = refs
        with self.assertRaisesRegex(ValueError, "frozen split/project-group"):
            self._vintage(study, rows)
        vintage = self._vintage(study)
        vintage["cases"][0]["input"]["input_claims"] = refs
        vintage["cases"][0]["lineage"] = benchmark._graph(
            self.connection, refs, vintage["evidence_cutoff_at"], "project-a")
        vintage.pop("sha256")
        with self.assertRaisesRegex(ValueError, "frozen split/project-group"):
            benchmark.verify_vintage(self.connection, benchmark._seal(vintage))

    def test_documentless_ingestion_run_retains_its_own_source_and_family(self):
        study = self._study()
        add_source_family(self.connection, SourceFamily("model-family", "model-family", "Fixture model family", CREATED))
        add_source(self.connection, Source("model-source", "model-family", "model-source", "Model", "Fixture model publisher", "https://example.test/model", CREATED))
        add_ingestion_run(self.connection, IngestionRun("model-run", "model-source", "2026-01-02T00:00:01Z",
            status=IngestionStatus.SUCCEEDED, completed_at="2026-01-02T00:00:03Z"))
        self._claim("model-derived", "project-a", MilestoneValue("production_start", MilestoneStatus.EXPECTED,
            "2026-04-01", None, "2026-04-30", "month", "April 2026"), parents=("target-a",), run="model-run")
        self.connection.commit()
        rows = self._predictions(); rows[0]["input_claims"] = [self._ref("model-derived")]
        vintage = self._vintage(study, rows)
        graph = vintage["cases"][0]["lineage"]
        self.assertEqual({"source", "model-source"}, {row["id"] for row in graph["sources"]})
        self.assertEqual({"family", "model-family"}, {row["id"] for row in graph["families"]})

    def test_staging_fsync_failure_preserves_a_competing_destination(self):
        destination = self.root / "competing.json"
        sentinel = b"another writer owns this file"
        def fail_before_publication(_descriptor):
            destination.write_bytes(sentinel)
            raise OSError("injected pre-publication fsync failure")
        with mock.patch.object(benchmark.os, "fsync", side_effect=fail_before_publication):
            with self.assertRaisesRegex(OSError, "injected"):
                benchmark.write_new(destination, {"engineering": True})
        self.assertEqual(sentinel, destination.read_bytes())
        self.assertEqual([], list(self.root.glob(".milestone-benchmark-*")))

    def test_no_replace_link_race_preserves_competing_file(self):
        destination = self.root / "race.json"
        original_link = os.link
        def compete(source, target, **kwargs):
            destination.write_bytes(b"competing immutable file")
            return original_link(source, target, **kwargs)
        with mock.patch.object(benchmark.os, "link", side_effect=compete):
            with self.assertRaises(FileExistsError):
                benchmark.write_new(destination, {"engineering": True})
        self.assertEqual(b"competing immutable file", destination.read_bytes())
        self.assertEqual([], list(self.root.glob(".milestone-benchmark-*")))

    def test_post_publication_failure_never_deletes_another_writers_replacement(self):
        destination = self.root / "published.json"
        saved = self.root / "saved-published.json"
        original_fsync = os.fsync
        calls = 0
        def replace_after_publication(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                destination.rename(saved)
                destination.write_bytes(b"replacement belongs to another writer")
                raise OSError("injected directory fsync failure")
            return original_fsync(descriptor)
        with mock.patch.object(benchmark.os, "fsync", side_effect=replace_after_publication):
            with self.assertRaisesRegex(OSError, "injected"):
                benchmark.write_new(destination, {"engineering": True})
        self.assertEqual(b"replacement belongs to another writer", destination.read_bytes())
        self.assertEqual(benchmark.canonical_bytes({"engineering": True}), saved.read_bytes())

    def test_parent_path_swaps_before_and_after_publication_are_detected(self):
        for swap_call in (1, 2):
            with self.subTest(swap_call=swap_call):
                original_parent = self.root / ("parent-" + str(swap_call))
                original_parent.mkdir()
                moved_parent = self.root / ("moved-" + str(swap_call))
                destination = original_parent / "sidecar.json"
                original_fsync = os.fsync
                calls = 0
                def swap_parent(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == swap_call:
                        original_parent.rename(moved_parent)
                        original_parent.mkdir()
                        destination.write_bytes(b"another writer owns the intended path")
                    return original_fsync(descriptor)
                with mock.patch.object(benchmark.os, "fsync", side_effect=swap_parent):
                    with self.assertRaisesRegex(ValueError, "parent pathname changed"):
                        benchmark.write_new(destination, {"engineering": True})
                self.assertEqual(b"another writer owns the intended path", destination.read_bytes())
                self.assertEqual([], list(moved_parent.glob(".milestone-benchmark-*")))
                if swap_call == 1:
                    self.assertFalse((moved_parent / "sidecar.json").exists())
                else:
                    self.assertEqual(benchmark.canonical_bytes({"engineering": True}), (moved_parent / "sidecar.json").read_bytes())

    def test_documentless_run_source_creation_cannot_postdate_cutoff(self):
        study = self._study()
        future = "2026-02-03T00:00:00Z"
        add_source_family(self.connection, SourceFamily("future-family", "future-family", "Future source family", future))
        add_source(self.connection, Source("future-source", "future-family", "future-source", "Future source", "Fixture", "https://example.test/future", future))
        add_ingestion_run(self.connection, IngestionRun("future-source-run", "future-source", "2026-01-02T00:00:01Z",
            status=IngestionStatus.SUCCEEDED, completed_at="2026-01-02T00:00:03Z"))
        self._claim("future-source-derived", "project-a", ScalarValue(ScalarType.TEXT, "Future provenance"),
            parents=("target-a",), run="future-source-run")
        self.connection.commit()
        rows = self._predictions(); rows[0]["input_claims"] = [self._ref("future-source-derived")]
        with self.assertRaisesRegex(ValueError, "source creation.*after"):
            self._vintage(study, rows)


if __name__ == "__main__":
    unittest.main()
