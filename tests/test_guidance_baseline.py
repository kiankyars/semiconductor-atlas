"""Synthetic execution/replay contracts, not measured manufacturing predictions.

The database, complete HTML documents, claim graph, and evidence verification are
real. Only wall clocks and code/runtime identities are fixed for test isolation.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from semiconductor_atlas import guidance_baseline as baseline
from semiconductor_atlas import milestone_benchmark as benchmark
from semiconductor_atlas import repository
from semiconductor_atlas.database import initialize
from semiconductor_atlas.models import (
    ClaimKind, ClaimSeries, ClaimVersion, Entity, EntityKind, EvidenceLink,
    IngestionRun, IngestionStatus, MilestoneStatus, MilestoneValue,
    ScalarType, ScalarValue, Source, SourceDocument, SourceFamily,
)
from scripts import guidance_interval_baseline as cli


CREATED = "2026-01-01T00:00:00Z"
RECORDED = "2026-01-02T00:00:02Z"
SCREENED = "2026-09-08T10:00:00Z"
REVIEWED = "2026-09-08T10:01:00Z"
CUTOFF = "2026-09-08T10:02:00Z"
CLOCKS = ["2026-09-08T10:03:00Z", "2026-09-08T10:04:00Z"]
VERIFIED = "2026-09-08T10:05:00Z"


class _Fixture:
    def __init__(self, *, body_transform=lambda raw: raw, first_year=2028, milestone_scope_scalar=True):
        self.connection, _ = initialize(":memory:", target_version=6)
        self.texts = {
            "a": f"Production will begin in {first_year}",
            "b": "Mass production expected to begin second half of 2028",
            "c": "Fab c: Expected to begin production in second half of 2027",
            "d": "Mass production expected to begin at the end of 2027",
            "e": "Production expected summer of 2026",
            "f": "Production expected summer of 2026",
            "g": "Fab 1: Production began in 2025; Fab 2 remains in planning",
        }
        self.geographies = {key: "US-CA" if key == "e" else "US-PA" if key == "f" else "US" for key in self.texts}
        self.scopes = {key: f"Project {key}; Fab {key}; {self.geographies[key]}" for key in self.texts}
        tables = [
            f'<table id="scope-{key}"><tr><td>Scope</td><td>{self.scopes[key]}</td></tr>'
            f'<tr id="timeline-{key}"><td>Timeline</td><td>{text}</td></tr></table>'
            for key, text in self.texts.items()
        ]
        self.body = body_transform(("<!doctype html><html><body><p>Engineering fixture é 工厂</p>" + "".join(tables) + "</body></html>").encode())
        self.body_hash = benchmark._hash(self.body)
        self.bodies = {self.body_hash: self.body}
        repository.add_source_family(self.connection, SourceFamily("family", "family", "Engineering fixture", CREATED))
        repository.add_source(self.connection, Source("source", "family", "source", "Fixture", "Synthetic publisher", "https://example.test", CREATED))
        repository.add_source_document(self.connection, SourceDocument("doc", "source", "https://example.test/plans", "Synthetic complete plans", CREATED, self.body_hash))
        repository.add_ingestion_run(self.connection, IngestionRun("run", "source", "2026-01-02T00:00:01Z",
            status=IngestionStatus.SUCCEEDED, completed_at="2026-01-02T00:00:03Z", input_document_id="doc",
            parameters={"accepted_at": RECORDED}))
        cases, inputs = [], []
        for key in self.texts:
            entity = "project-" + key
            stable_key = "reviewed-project-source:https://example.test/plans:Fab c" if key == "c" and not milestone_scope_scalar else "source-native:" + key
            event = "mass_production" if key in "bd" else "production_start"
            repository.add_entity(self.connection, Entity(entity, EntityKind.PROJECT, stable_key, CREATED, "Project " + key, "run"))
            self.claim("scope-" + key, entity, ScalarValue(ScalarType.TEXT, self.scopes[key]))
            year = first_year if key == "a" else 2027 if key in "cd" else 2026 if key in "ef" else 2025 if key == "g" else 2028
            low = f"{year}-07-01" if key in "bc" else f"{year}-01-01"
            high = f"{year}-12-31"
            literal = f"second half of {year}" if key in "bc" else str(year)
            period = MilestoneValue(event, MilestoneStatus.EXPECTED, low, None, high,
                                    "half_year" if key in "bc" else "year", literal)
            self.claim("expected-" + key, entity, period)
            timeline = "expected-c" if key == "c" else "timeline-" + key
            if key != "c":
                self.claim(timeline, entity, ScalarValue(ScalarType.TEXT, self.texts[key]))
            expected = {"claim_id": "expected-" + key,
                        "value_sha256": self.ref("expected-" + key)["value_sha256"],
                        **repository._value_payload(period)}
            cases.append({
                "case_id": key, "project_entity_id": entity, "project_stable_key": stable_key,
                "scope": self.scopes[key], "event_type": event,
                "project_group": "related-sites" if key in "ef" else "group-" + key,
                "geography": self.geographies[key], "partition": None,
                "stored_expected_claims": [expected],
                "disposition": "future_literal_interval" if key in "abc" else "screen_out_aggregate_scope" if key == "g" else "abstain_vague_or_stale_guidance",
                "reviewed_source_period": {"low": low, "base": None, "high": high, "literal": literal} if key in "abc" else None,
                "reason": "Synthetic complete-screen scope and literal review.",
            })
            scope_id = None if key == "c" and not milestone_scope_scalar else "scope-" + key
            inputs.append({
                "case_id": key, "input_claims": sorted([self.ref(timeline)] + ([self.ref(scope_id)] if scope_id else []), key=lambda item: item["claim_id"]),
                "timeline_claim_id": timeline, "scope_claim_id": scope_id,
                "selected_document_id": "doc", "document_sha256": self.body_hash,
                "local_source_path": "/synthetic-guidance-fixture/full-plans.html",
                "timeline_span": self.element_span("tr", "timeline-" + key),
                "scope_span": self.element_span("table", "scope-" + key),
                "selection_reason": "Reviewed complete visible project table and timeline row; engineering-only source.",
            })
        self.screen = {"format": baseline.SCREEN_FORMAT,
                       "status": "review_screen_not_registered_study_or_forecast",
                       "reviewed_at": SCREENED, "database": {}, "candidates": cases}
        self.review = {"format": baseline.INPUT_FORMAT, "reviewer": "Synthetic test fixture",
                       "reviewed_at": REVIEWED, "prior_exposure": "Fixture author knows all inputs; not independent or blind.",
                       "corrections": [], "cases": inputs}
        self.materialize()

    def claim(self, identifier, entity, value, *, recorded=RECORDED, document="doc", run="run"):
        series = ClaimSeries("series-" + identifier, entity, "series-" + identifier,
                             "milestone." + value.milestone_type if isinstance(value, MilestoneValue) else "source_text",
                             value.kind, CREATED)
        repository.add_claim_series(self.connection, series)
        repository.insert_claim(self.connection, ClaimVersion(identifier, series.id, None, recorded,
            ClaimKind.SOURCE_STATEMENT, "synthetic-guidance-input", None, created_by_run_id=run),
            value, evidence=[EvidenceLink(document, locator="Complete synthetic source table")])

    def ref(self, identifier):
        return {"claim_id": identifier, "value_sha256": self.connection.execute(
            "SELECT value_sha256 FROM claim_versions WHERE id=?", (identifier,)).fetchone()[0]}

    def element_span(self, tag, identifier):
        match = re.search(rb"<" + tag.encode() + rb'\b[^>]*\bid="' + identifier.encode() + rb'"[^>]*>', self.body)
        if match is None:
            raise AssertionError("synthetic fixture element is missing")
        start = match.start()
        end = self.body.index(b"</" + tag.encode() + b">", match.end()) + len(tag) + 3
        return {"start": start, "end": end, "sha256": benchmark._hash(self.body[start:end])}

    def materialize(self):
        self.connection.commit()
        self.database = self.connection.serialize()
        self.screen["database"]["sha256"] = benchmark._hash(self.database)
        self.screen_raw = benchmark.canonical_bytes(self.screen)
        self.review["database_sha256"] = benchmark._hash(self.database)
        self.review["screen_sha256"] = benchmark._hash(self.screen_raw)
        self.review_raw = benchmark.canonical_bytes(self.review)
        return self.database, self.screen_raw, self.review_raw, self.bodies


class GuidanceBaselineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.make_fixture()
        for name, identity in (
            ("_codes", {"synthetic-test-code-identity": "c" * 64}),
            ("_runtime", {"synthetic_test_runtime": True, "hermetic_environment": False}),
        ):
            patch = mock.patch.object(baseline, name, return_value=identity)
            patch.start()
            self.addCleanup(patch.stop)

    def make_fixture(self, **kwargs):
        fixture = _Fixture(**kwargs)
        self.addCleanup(fixture.connection.close)
        return fixture

    def freeze(self, fixture=None, *, clocks=CLOCKS, cutoff=CUTOFF):
        fixture = fixture or self.fixture
        with mock.patch.object(baseline, "_now", side_effect=clocks):
            return baseline.freeze(*fixture.materialize(), evidence_cutoff_at=cutoff)

    def verify(self, vintage, *, fixture=None, trusted=None, now=VERIFIED):
        fixture = fixture or self.fixture
        raw = vintage if isinstance(vintage, bytes) else benchmark.canonical_bytes(vintage)
        with mock.patch.object(baseline, "_now", return_value=now):
            return baseline.verify(fixture.database, raw, fixture.bodies,
                                   trusted_sha256=trusted or benchmark._hash(raw))

    @staticmethod
    def reseal(value):
        value = copy.deepcopy(value)
        value.pop("sha256", None)
        return benchmark._seal(value)

    def test_execution_preserves_null_midpoints_complete_denominator_and_related_sites(self):
        vintage = self.freeze()
        self.assertEqual({"screened": 7, "retained": 6, "issued": 3, "abstained": 3, "excluded": 1}, vintage["denominators"])
        self.assertEqual(list("abcdefg"), [row["case"]["case_id"] for row in vintage["cases"]])
        self.assertEqual({"low": "2028-01-01", "base": None, "high": "2028-12-31"}, vintage["cases"][0]["prediction"])
        self.assertEqual({"low": "2028-07-01", "base": None, "high": "2028-12-31"}, vintage["cases"][1]["prediction"])
        self.assertEqual({"low": "2027-07-01", "base": None, "high": "2027-12-31"}, vintage["cases"][2]["prediction"])
        self.assertEqual("mass_production", vintage["cases"][1]["case"]["event_type"])
        for row in vintage["cases"]:
            self.assertIsNone(row["case"]["partition"])
            if row["disposition"] != "issued":
                self.assertIsNone(row["prediction"])
        self.assertEqual({"project_group": "related-sites", "case_ids": ["e", "f"], "geographies": ["US-CA", "US-PA"]}, vintage["groups"][-1])
        self.assertIsNone(vintage["training_release"])
        self.assertIsNone(vintage["probability_calibration"])
        self.assertFalse(vintage["boundaries"]["fitted_model"])
        self.assertFalse(vintage["boundaries"]["geographic_holdout"])
        self.assertEqual(CLOCKS[1], vintage["frozen_at"])
        self.assertEqual(benchmark._hash(baseline.POLICY), vintage["policy_sha256"])
        self.assertEqual(20, len(vintage["review_schedule"]))
        self.assertEqual("2026-Q4", vintage["review_schedule"][0]["quarter"])
        self.assertEqual("2027-01-31", vintage["review_schedule"][0]["review_due_by"])
        self.assertEqual("2031-09-30", vintage["horizon_end"])
        self.assertFalse(vintage["rights"]["source_bodies_embedded"])

    def test_exact_reexecution_reproduces_all_vintage_bytes(self):
        vintage = self.freeze()
        self.assertEqual(benchmark.canonical_bytes(vintage), benchmark.canonical_bytes(self.verify(vintage)))

    def test_issued_mass_production_is_not_interchangeable_with_production_start(self):
        for key, wrong in (("a", "mass_production"), ("b", "production_start"), ("c", "mass_production")):
            with self.subTest(case=key):
                fixture = self.make_fixture()
                fixture.screen["candidates"][ord(key) - ord("a")]["event_type"] = wrong
                with self.assertRaisesRegex(ValueError, "exact.*event"):
                    self.freeze(fixture)

    def test_vague_guidance_abstains_even_when_screen_requests_an_interval(self):
        self.fixture.screen["candidates"][3]["disposition"] = "future_literal_interval"
        vintage = self.freeze()
        self.assertEqual("abstained", vintage["cases"][3]["disposition"])
        self.assertIsNone(vintage["cases"][3]["guidance_interval"])
        self.assertIn("No supported literal", vintage["cases"][3]["reason"])

    def test_screen_abstention_does_not_silently_become_an_issued_interval(self):
        self.fixture.screen["candidates"][0]["disposition"] = "abstain_unresolved_scope"
        vintage = self.freeze()
        self.assertEqual("abstained", vintage["cases"][0]["disposition"])
        self.assertIsNotNone(vintage["cases"][0]["guidance_interval"])
        self.assertIsNone(vintage["cases"][0]["prediction"])
        self.assertEqual(7, vintage["denominators"]["screened"])

    def test_elapsed_straddling_and_outside_horizon_intervals_remain_abstentions(self):
        for year in (2025, 2026, 2032):
            with self.subTest(year=year):
                vintage = self.freeze(self.make_fixture(first_year=year))
                self.assertEqual("abstained", vintage["cases"][0]["disposition"])
                self.assertIsNone(vintage["cases"][0]["prediction"])
                self.assertEqual(7, vintage["denominators"]["screened"])

    def test_actual_freeze_day_not_execution_start_controls_future_eligibility(self):
        vintage = self.freeze(self.make_fixture(first_year=2027), clocks=["2026-12-31T23:59:59Z", "2027-01-01T00:00:01Z"])
        self.assertEqual("abstained", vintage["cases"][0]["disposition"])
        self.assertEqual("2027-Q2", vintage["review_schedule"][0]["quarter"])

    def test_future_cutoff_and_regressing_execution_clock_are_rejected(self):
        for cutoff, clocks in (("2026-09-09T00:00:00Z", CLOCKS), (CUTOFF, list(reversed(CLOCKS)))):
            with self.subTest(cutoff=cutoff, clocks=clocks), self.assertRaises(ValueError):
                self.freeze(cutoff=cutoff, clocks=clocks)

    def test_review_must_be_known_at_cutoff_and_follow_screen(self):
        for reviewed in ("2026-09-08T09:59:00Z", "2026-09-08T10:02:01Z"):
            with self.subTest(reviewed=reviewed):
                self.fixture.review["reviewed_at"] = reviewed
                with self.assertRaisesRegex(ValueError, "chronology|cutoff"):
                    self.freeze()

    def test_future_accepted_scalar_claim_cannot_bypass_cutoff(self):
        self.fixture.claim("later-timeline", "project-a", ScalarValue(ScalarType.TEXT, self.fixture.texts["a"]), recorded="2026-09-09T00:00:00Z")
        row = self.fixture.review["cases"][0]
        row["timeline_claim_id"] = "later-timeline"
        row["input_claims"] = [self.fixture.ref("later-timeline"), self.fixture.ref("scope-a")]
        with self.assertRaisesRegex(ValueError, "claim admission is after"):
            self.freeze()

    def test_unselected_future_expected_claim_still_blocks_complete_screen(self):
        value = MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                               "2029-01-01", None, "2029-12-31", "year", "2029")
        self.fixture.claim("future-screened", "project-a", value, recorded="2026-09-09T00:00:00Z")
        self.fixture.screen["candidates"][0]["stored_expected_claims"].append(
            {**self.fixture.ref("future-screened"), **repository._value_payload(value)})
        self.assertNotIn("future-screened", [row["claim_id"] for row in self.fixture.review["cases"][0]["input_claims"]])
        with self.assertRaisesRegex(ValueError, "claim admission is after"):
            self.freeze()

    def test_future_run_admission_or_completion_cannot_bypass_cutoff(self):
        for field in ("accepted_at", "completed_at"):
            with self.subTest(field=field):
                fixture = self.make_fixture()
                repository.add_ingestion_run(fixture.connection, IngestionRun("later-run", "source", "2026-01-02T00:00:01Z",
                    status=IngestionStatus.SUCCEEDED,
                    completed_at="2026-09-09T00:00:00Z" if field == "completed_at" else "2026-01-02T00:00:03Z",
                    input_document_id="doc", parameters={"accepted_at": "2026-09-09T00:00:00Z" if field == "accepted_at" else RECORDED}))
                fixture.claim("later-timeline", "project-a", ScalarValue(ScalarType.TEXT, fixture.texts["a"]), run="later-run")
                row = fixture.review["cases"][0]
                row["timeline_claim_id"] = "later-timeline"
                row["input_claims"] = [fixture.ref("later-timeline"), fixture.ref("scope-a")]
                with self.assertRaisesRegex(ValueError, "after the evidence cutoff"):
                    self.freeze(fixture)

    def test_selected_legacy_midpoint_is_rejected_even_with_valid_core_hash(self):
        value = MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                               "2027-07-01", "2027-10-01", "2027-12-31")
        self.fixture.claim("legacy-midpoint-c", "project-c", value)
        self.fixture.screen["candidates"][2]["stored_expected_claims"].append(
            {**self.fixture.ref("legacy-midpoint-c"), **repository._value_payload(value)})
        row = self.fixture.review["cases"][2]
        row["timeline_claim_id"] = "legacy-midpoint-c"
        row["input_claims"] = [self.fixture.ref("legacy-midpoint-c"), self.fixture.ref("scope-c")]
        with self.assertRaisesRegex(ValueError, "exact expected event with null midpoint"):
            self.freeze()

    def test_named_milestone_without_scalar_scope_binds_exact_publisher_and_phase(self):
        fixture = self.make_fixture(milestone_scope_scalar=False)
        vintage = self.freeze(fixture)
        self.assertEqual("issued", vintage["cases"][2]["disposition"])
        self.assertEqual(vintage, self.verify(vintage, fixture=fixture))
        wrong_phase = self.make_fixture(milestone_scope_scalar=False,
            body_transform=lambda raw: raw.replace(b"Fab c: Expected to begin production", b"Fab z: Expected to begin production"))
        with self.assertRaisesRegex(ValueError, "exact named project clause"):
            self.freeze(wrong_phase)
        fixture = self.make_fixture()
        fixture.review["cases"][2]["scope_claim_id"] = None
        fixture.review["cases"][2]["input_claims"] = [fixture.ref("expected-c")]
        with self.assertRaisesRegex(ValueError, "publisher/phase correspondence"):
            self.freeze(fixture)

    def test_named_milestone_retains_alternative_documents_and_selects_latest(self):
        fixture = self.make_fixture(milestone_scope_scalar=False)
        repository.add_source_document(fixture.connection, SourceDocument("new-doc", "source", "https://example.test/plans", "Later retained statement", "2026-01-03T00:00:00Z", fixture.body_hash))
        repository.add_ingestion_run(fixture.connection, IngestionRun("new-run", "source", "2026-01-04T00:00:01Z",
            status=IngestionStatus.SUCCEEDED, completed_at="2026-01-04T00:00:03Z", input_document_id="new-doc",
            parameters={"accepted_at": "2026-01-04T00:00:02Z"}))
        value = MilestoneValue("production_start", MilestoneStatus.EXPECTED,
                               "2027-07-01", None, "2027-12-31", "half_year", "second half of 2027")
        fixture.claim("expected-c-later", "project-c", value, recorded="2026-01-04T00:00:02Z", document="new-doc", run="new-run")
        fixture.screen["candidates"][2]["stored_expected_claims"].append(
            {**fixture.ref("expected-c-later"), **repository._value_payload(value)})
        with self.assertRaisesRegex(ValueError, "source-version alternatives must remain"):
            self.freeze(fixture)
        row = fixture.review["cases"][2]
        row["input_claims"].append(fixture.ref("expected-c-later"))
        with self.assertRaisesRegex(ValueError, "not the latest retained reviewed statement"):
            self.freeze(fixture)
        row["selected_document_id"] = "new-doc"
        row["timeline_claim_id"] = "expected-c-later"
        vintage = self.freeze(fixture)
        self.assertEqual("issued", vintage["cases"][2]["disposition"])
        self.assertEqual(2, len(vintage["cases"][2]["lineage"]["documents"]))
        self.assertEqual(vintage, self.verify(vintage, fixture=fixture))

    def test_complete_screen_cannot_omit_an_expected_claim(self):
        self.fixture.screen["candidates"][0]["stored_expected_claims"] = []
        with self.assertRaisesRegex(ValueError, "complete expected-claim population"):
            self.freeze()

    def test_complete_screen_cannot_forge_an_expected_value(self):
        self.fixture.screen["candidates"][0]["stored_expected_claims"][0]["date_high"] = "2029-12-31"
        with self.assertRaisesRegex(ValueError, "actual typed database record"):
            self.freeze()

    def test_dropped_case_in_screen_or_review_is_rejected(self):
        for drop_screen, drop_review in ((True, False), (False, True), (True, True)):
            with self.subTest(screen=drop_screen, review=drop_review):
                fixture = self.make_fixture()
                if drop_screen:
                    fixture.screen["candidates"].pop()
                if drop_review:
                    fixture.review["cases"].pop()
                with self.assertRaisesRegex(ValueError, "complete expected-claim population|every case"):
                    self.freeze(fixture)

    def test_duplicate_and_unsorted_input_cases_are_rejected(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                fixture = self.make_fixture()
                if duplicate:
                    fixture.review["cases"][1] = copy.deepcopy(fixture.review["cases"][0])
                else:
                    fixture.review["cases"].reverse()
                with self.assertRaisesRegex(ValueError, "every case exactly once in sorted order"):
                    self.freeze(fixture)

    def test_forged_root_value_hash_and_cross_project_root_are_rejected(self):
        for cross_project in (False, True):
            with self.subTest(cross_project=cross_project):
                fixture = self.make_fixture()
                row = fixture.review["cases"][0]
                if cross_project:
                    row["scope_claim_id"] = "scope-b"
                    row["input_claims"] = [fixture.ref("scope-b"), fixture.ref("timeline-a")]
                else:
                    row["input_claims"][0]["value_sha256"] = "f" * 64
                with self.assertRaisesRegex(ValueError, "value hash differs|another exact project"):
                    self.freeze(fixture)

    def test_altered_body_and_unused_extra_body_are_rejected(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                fixture = self.make_fixture()
                if extra:
                    raw = b"<html><body>unused</body></html>"
                    fixture.bodies[benchmark._hash(raw)] = raw
                else:
                    fixture.bodies[fixture.body_hash] = fixture.body.replace(b"begin in 2028", b"begin in 2029")
                with self.assertRaisesRegex(ValueError, "body hash mismatch|exactly cover"):
                    self.freeze(fixture)

    def test_valid_rehashed_body_cannot_substitute_the_database_document(self):
        raw = self.fixture.body.replace(b"begin in 2028", b"begin in 2029")
        digest = benchmark._hash(raw)
        self.fixture.bodies = {digest: raw}
        for row in self.fixture.review["cases"]:
            row["document_sha256"] = digest
        with self.assertRaisesRegex(ValueError, "not the exact document"):
            self.freeze()

    def test_source_span_hash_and_complete_element_binding_are_required(self):
        for corrupt_hash in (False, True):
            with self.subTest(corrupt_hash=corrupt_hash):
                fixture = self.make_fixture()
                span = fixture.review["cases"][0]["timeline_span"]
                if corrupt_hash:
                    span["sha256"] = "0" * 64
                else:
                    span["start"] += 1
                    span["sha256"] = benchmark._hash(fixture.body[span["start"]:span["end"]])
                with self.assertRaisesRegex(ValueError, "source span differs|one complete visible element"):
                    self.freeze(fixture)

    def test_cryptographically_bound_hidden_elements_and_hidden_text_are_rejected(self):
        for attribute in (b" hidden", b' style="display: none"', b' aria-hidden="true"', b" inert"):
            with self.subTest(attribute=attribute):
                fixture = self.make_fixture(body_transform=lambda raw: raw.replace(b'<tr id="timeline-a">', b'<tr id="timeline-a"' + attribute + b'>'))
                with self.assertRaisesRegex(ValueError, "complete visible element"):
                    self.freeze(fixture)
        fixture = self.make_fixture(body_transform=lambda raw: raw.replace(b"Production will begin in 2028", b"<span hidden>Production will begin in 2028</span>"))
        with self.assertRaisesRegex(ValueError, "absent from the exact visible source span"):
            self.freeze(fixture)

    def test_scope_must_contain_timeline_and_accepted_scope_text(self):
        fixture = self.make_fixture()
        fixture.review["cases"][0]["scope_span"] = fixture.review["cases"][1]["scope_span"]
        with self.assertRaisesRegex(ValueError, "inside its reviewed source-scope element"):
            self.freeze(fixture)
        fixture = self.make_fixture(body_transform=lambda raw: raw.replace(b"Project a; Fab a; US", b"Project z; Fab z; US"))
        with self.assertRaisesRegex(ValueError, "scope scalar is absent"):
            self.freeze(fixture)

    def test_reviewed_source_period_cannot_override_executed_literal(self):
        self.fixture.screen["candidates"][0]["reviewed_source_period"]["base"] = "2028-07-01"
        with self.assertRaisesRegex(ValueError, "differs from the reviewed source period"):
            self.freeze()

    def test_prediction_tampering_is_rejected_with_original_and_resealed_hashes(self):
        original = self.freeze()
        trusted = benchmark._hash(benchmark.canonical_bytes(original))
        altered = copy.deepcopy(original)
        altered["cases"][0]["prediction"]["high"] = "2028-06-30"
        altered = self.reseal(altered)
        with self.assertRaisesRegex(ValueError, "independently retained artifact hash"):
            self.verify(altered, trusted=trusted)
        with self.assertRaisesRegex(ValueError, "differs from frozen bytes"):
            self.verify(altered)

    def test_resealed_dropped_case_or_denominator_cannot_replay(self):
        original = self.freeze()
        for drop in (False, True):
            with self.subTest(drop=drop):
                altered = copy.deepcopy(original)
                if drop:
                    altered["cases"].pop()
                else:
                    altered["denominators"]["abstained"] = 0
                with self.assertRaisesRegex(ValueError, "differs from frozen bytes"):
                    self.verify(self.reseal(altered))

    def test_changed_database_body_code_runtime_or_future_vintage_cannot_replay(self):
        vintage = self.freeze()
        fixture = self.make_fixture(first_year=2029)
        with self.assertRaisesRegex(ValueError, "database hash mismatch|actual typed database record"):
            self.verify(vintage, fixture=fixture)
        self.fixture.bodies[self.fixture.body_hash] += b" "
        with self.assertRaisesRegex(ValueError, "body hash mismatch"):
            self.verify(vintage)
        self.fixture.bodies[self.fixture.body_hash] = self.fixture.body
        for name in ("_codes", "_runtime"):
            with self.subTest(identity=name), mock.patch.object(baseline, name, return_value={"changed": True}):
                with self.assertRaisesRegex(ValueError, "exact frozen code"):
                    self.verify(vintage)
        with self.assertRaisesRegex(ValueError, "freeze is in the future"):
            self.verify(vintage, now=CLOCKS[0])

    def test_freeze_and_replay_preserve_exact_database_bytes_and_open_is_query_only(self):
        original = self.fixture.database
        connection = baseline._open(original)
        try:
            self.assertEqual(1, connection.execute("PRAGMA query_only").fetchone()[0])
            for sql in ("UPDATE entities SET display_name='changed'", "DELETE FROM entities", "CREATE TABLE unexpected (x)"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.OperationalError):
                    connection.execute(sql)
            self.assertEqual(original, connection.serialize())
        finally:
            connection.close()
        vintage = self.freeze()
        self.verify(vintage)
        self.assertEqual(original, self.fixture.database)
        self.assertEqual(original, self.fixture.connection.serialize())

    def test_cli_freeze_and_verify_use_read_only_files_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source.html"
            source.write_bytes(self.fixture.body)
            for row in self.fixture.review["cases"]:
                row["local_source_path"] = str(source)
            self.fixture.materialize()
            database, screen, review, output = (root / name for name in ("fixture.sqlite", "screen.json", "review.json", "vintage.json"))
            for path, raw in ((database, self.fixture.database), (screen, self.fixture.screen_raw), (review, self.fixture.review_raw)):
                path.write_bytes(raw)
            for path in (source, database, screen, review):
                path.chmod(0o400)
            before = {path: path.read_bytes() for path in (source, database, screen, review)}
            args = ["freeze", "--database", str(database), "--screen", str(screen), "--input-review", str(review),
                    "--evidence-cutoff-at", CUTOFF, "--output", str(output)]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), mock.patch.object(baseline, "_now", side_effect=CLOCKS):
                self.assertEqual(0, cli.main(args), stderr.getvalue())
            raw = output.read_bytes()
            receipt = json.loads(stdout.getvalue())
            self.assertEqual(benchmark._hash(raw), receipt["sha256"])
            self.assertFalse(receipt["database_writes"])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr), mock.patch.object(baseline, "_now", return_value=VERIFIED):
                self.assertEqual(0, cli.main(["verify", "--database", str(database), "--vintage", str(output),
                                              "--trusted-vintage-sha256", receipt["sha256"]]), stderr.getvalue())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr), mock.patch.object(baseline, "_now", side_effect=CLOCKS):
                self.assertEqual(1, cli.main(args))
            self.assertEqual(raw, output.read_bytes())
            self.assertEqual(before, {path: path.read_bytes() for path in before})


if __name__ == "__main__":
    unittest.main()
