from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from decimal import localcontext
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import semiconductor_atlas.ai_critical_changes as change_module
import scripts.compare_ai_critical_releases as compare_script
from semiconductor_atlas.ai_critical import (
    _manifest_payload,
    _release_files,
    load_baseline,
    materialize_baseline,
    validate_release,
)
from semiconductor_atlas.ai_critical_changes import (
    compare_releases,
    validate_change_bundle,
    write_change_bundle,
    write_deterministic_change_archive,
)
from web.generate_atlas import generate


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPOSITORY_ROOT / "baselines" / "ai_critical_manufacturing_v1.json"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


class AICriticalChangeDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.production_spec = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _spec(
        self,
        release_id: str,
        *,
        as_of: str,
        recorded_at: str,
        mutate: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        spec = copy.deepcopy(self.production_spec)
        spec["release_id"] = release_id
        spec["as_of"] = as_of
        spec["recorded_at"] = recorded_at
        if mutate is not None:
            mutate(spec)
        return spec

    def _write_release(
        self,
        name: str,
        spec: dict[str, object],
    ) -> Path:
        input_path = self.root / f"{name}.json"
        input_path.write_bytes(_pretty_bytes(spec))
        baseline = load_baseline(
            input_path,
            REPOSITORY_ROOT,
            verify_source_bytes=False,
        )
        materialized = materialize_baseline(baseline)
        files = _release_files(baseline, materialized)
        output = self.root / name
        output.mkdir()
        for filename, raw in sorted(files.items()):
            (output / filename).write_bytes(raw)
        manifest = _manifest_payload(baseline, materialized, files)
        (output / "manifest.json").write_bytes(_pretty_bytes(manifest))
        generate(output / "atlas.geojson", output / "atlas.html")
        validate_release(output, require_html=True)
        return output

    def _release_pair(
        self,
        *,
        mutate_current: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[Path, Path]:
        prior = self._write_release(
            "prior",
            self._spec(
                "change-fixture-prior",
                as_of="2026-08-20",
                recorded_at="2026-08-20T18:00:00Z",
            ),
        )
        current = self._write_release(
            "current",
            self._spec(
                "change-fixture-current",
                as_of="2026-08-21",
                recorded_at="2026-08-21T18:00:00Z",
                mutate=mutate_current,
            ),
        )
        return prior, current

    @staticmethod
    def _facility(spec: dict[str, object], company: str) -> dict[str, object]:
        return next(
            facility
            for facility in spec["facilities"]
            if facility["company"] == company
        )

    @staticmethod
    def _source(spec: dict[str, object], source_id: str) -> dict[str, object]:
        return next(
            source
            for source in spec["sources"]
            if source["source_id"] == source_id
        )

    @staticmethod
    def _evidence(spec: dict[str, object], evidence_id: str) -> dict[str, object]:
        return next(
            evidence
            for evidence in spec["evidence"]
            if evidence["evidence_id"] == evidence_id
        )

    @staticmethod
    def _capacity_assertion(capacity: dict[str, object]) -> dict[str, object]:
        fields = (
            "metric",
            "basis",
            "unit",
            "low",
            "base",
            "high",
            "period_start",
            "period_end",
            "scope_kind",
            "input_output_basis",
            "quantity_semantics",
            "technology_scope",
        )
        return {field: copy.deepcopy(capacity[field]) for field in fields}

    def _refresh_fragment_hash(
        self,
        spec: dict[str, object],
        evidence: dict[str, object],
    ) -> None:
        source = self._source(spec, str(evidence["source_id"]))
        payload = {
            "source_id": evidence["source_id"],
            "source_record_sha256": hashlib.sha256(
                _canonical_bytes(source)
            ).hexdigest(),
            "role": evidence["role"],
            "locator": evidence["locator"],
            "excerpt": evidence["excerpt"],
            "verification": evidence["verification"],
        }
        if "capacity_assertions" in evidence:
            evidence["capacity_assertions"].sort(key=_canonical_bytes)
            payload["capacity_assertions"] = evidence["capacity_assertions"]
        evidence["fragment_sha256"] = hashlib.sha256(
            _canonical_bytes(payload)
        ).hexdigest()

    def _replace_capacity(
        self,
        spec: dict[str, object],
        *,
        capacity_index: int,
        evidence_id: str,
        excerpt: str,
        mutate: Callable[[dict[str, object]], None],
        keep_original: bool = False,
    ) -> None:
        facility = self._facility(spec, "Amkor")
        original = facility["capacities"][capacity_index]
        capacity = copy.deepcopy(original)
        mutate(capacity)
        capacity["evidence_ids"] = [evidence_id]
        evidence = copy.deepcopy(self._evidence(spec, "amkor-peoria-capacity"))
        evidence["evidence_id"] = evidence_id
        evidence["locator"] = f"change fixture {evidence_id}"
        evidence["excerpt"] = excerpt
        evidence["capacity_assertions"] = [self._capacity_assertion(capacity)]
        self._refresh_fragment_hash(spec, evidence)
        spec["evidence"].append(evidence)
        if keep_original:
            facility["capacities"].append(capacity)
        else:
            facility["capacities"][capacity_index] = capacity

    @staticmethod
    def _proposal_for_predicate(
        comparison: dict[str, object], predicate: str
    ) -> dict[str, object]:
        matches = [
            proposal
            for proposal in comparison["alert_proposals"]
            if proposal["predicate"] == predicate
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"expected exactly one {predicate} proposal, found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _changes_for_predicate(
        comparison: dict[str, object], predicate: str
    ) -> list[dict[str, object]]:
        return [
            change
            for change in comparison["changes"]
            if change["series"]["predicate"] == predicate
        ]

    def test_new_run_ids_with_identical_semantics_are_all_reaffirmed(self) -> None:
        prior, current = self._release_pair()
        prior_manifest = validate_release(prior, require_html=True)
        current_manifest = validate_release(current, require_html=True)
        self.assertNotEqual(
            prior_manifest["producing_run_id"], current_manifest["producing_run_id"]
        )

        comparison = compare_releases(prior, current)

        self.assertEqual(prior_manifest["claim_count"], len(comparison["changes"]))
        self.assertTrue(
            all(
                change["status"] == "reaffirmed"
                for change in comparison["changes"]
            )
        )
        self.assertEqual([], comparison["alert_proposals"])
        self.assertEqual(
            len(comparison["changes"]), comparison["summary"]["change_count"]
        )
        self.assertEqual(
            len(comparison["changes"]),
            comparison["summary"]["changes_by_status"]["reaffirmed"],
        )
        self.assertEqual(0, comparison["summary"]["alert_proposal_count"])
        self.assertEqual(
            prior_manifest["release_id"],
            comparison["manifest"]["prior_release"]["release_id"],
        )
        self.assertEqual(
            current_manifest["release_id"],
            comparison["manifest"]["current_release"]["release_id"],
        )

    def test_lifecycle_progress_creates_one_evidence_linked_nondeliverable_proposal(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            self._facility(spec, "Samsung")["lifecycle"]["state"] = (
                "site_preparation"
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        lifecycle_changes = self._changes_for_predicate(
            comparison, "lifecycle.stage"
        )
        revised = [
            row for row in lifecycle_changes if row["status"] == "revised"
        ]

        self.assertEqual(1, len(revised))
        change = revised[0]
        self.assertEqual(
            "announced", change["prior_claim"]["value"]["value"]
        )
        self.assertEqual(
            "site_preparation", change["current_claim"]["value"]["value"]
        )
        self.assertTrue(change["current_claim"]["evidence_links"])
        proposal = self._proposal_for_predicate(comparison, "lifecycle.stage")
        self.assertEqual(
            [proposal["fingerprint"]], change["alert_proposal_fingerprints"]
        )
        self.assertEqual(
            change["prior_claim"]["evidence_links"],
            proposal["evidence_lineage"]["prior"]["evidence_links"],
        )
        self.assertEqual(
            change["current_claim"]["evidence_links"],
            proposal["evidence_lineage"]["current"]["evidence_links"],
        )
        self.assertFalse(proposal["delivery_eligible"])
        self.assertIsNone(proposal["confidence"])
        self.assertEqual("unknown_not_calibrated", proposal["confidence_scope"])
        self.assertEqual(
            "proposal_only_not_production_alert",
            proposal["delivery_ineligibility_reason"],
        )

    def test_comparable_capacity_revision_at_threshold_alerts_without_crossing_series(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            def revise(capacity: dict[str, object]) -> None:
                capacity["low"] = 4_255_000
                capacity["base"] = 4_255_000
                capacity["high"] = 4_255_000

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-revised",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "4,255,000 units/month."
                ),
                mutate=revise,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        capacity_changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )
        revised = [
            row for row in capacity_changes if row["status"] == "revised"
        ]

        self.assertEqual(1, len(revised))
        change = revised[0]
        self.assertEqual(3_700_000, change["prior_claim"]["value"]["base"])
        self.assertEqual(4_255_000, change["current_claim"]["value"]["base"])
        self.assertEqual(
            {
                "metric": "units_throughput",
                "basis": "announced",
                "unit": "units/month",
                "scope_kind": "project_addition",
                "input_output_basis": "source_stated_throughput",
                "quantity_semantics": "monthly_rate",
                "technology_scope": ["advanced_packaging", "advanced_test"],
                "period_start": None,
                "period_end": None,
                "valid_from": "2024-07-24",
            },
            change["series"]["dimensions"],
        )
        proposal = self._proposal_for_predicate(
            comparison, "capacity.units_throughput"
        )
        self.assertEqual("capacity_revision_15_percent", proposal["rule_id"])
        self.assertEqual(change["series_id"], proposal["series_id"])
        self.assertEqual(
            change["current_claim"]["evidence_links"],
            proposal["evidence_lineage"]["current"]["evidence_links"],
        )
        self.assertFalse(proposal["delivery_eligible"])

    def test_capacity_unit_change_is_not_compared_as_a_revision(self) -> None:
        def mutate(spec: dict[str, object]) -> None:
            def change_unit(capacity: dict[str, object]) -> None:
                capacity["unit"] = "units/quarter"
                capacity["quantity_semantics"] = "quarter_total"
                capacity["input_output_basis"] = "source_stated_quarter_total"
                capacity["period_start"] = "2026-07-01"
                capacity["period_end"] = "2026-10-01"

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-quarter",
                excerpt=(
                    "Amkor announced quarter total of 3,700,000 units in 2026 Q3."
                ),
                mutate=change_unit,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )

        self.assertEqual(
            {"added", "not_carried_forward"},
            {row["status"] for row in changes},
        )
        self.assertFalse(any(row["status"] == "revised" for row in changes))
        self.assertFalse(
            any(
                proposal["rule_id"] == "capacity_revision_15_percent"
                for proposal in comparison["alert_proposals"]
            )
        )
        self.assertEqual(
            {"units/month", "units/quarter"},
            {row["series"]["dimensions"]["unit"] for row in changes},
        )

    def test_capacity_scope_change_is_not_compared_as_a_revision(self) -> None:
        def mutate(spec: dict[str, object]) -> None:
            def change_scope(capacity: dict[str, object]) -> None:
                capacity["scope_kind"] = "facility_total"

            self._replace_capacity(
                spec,
                capacity_index=1,
                evidence_id="amkor-peoria-wafer-facility-total",
                excerpt=(
                    "Amkor announced expected facility capacity upon completion of "
                    "14,500 wafers/month."
                ),
                mutate=change_scope,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison, "capacity.wafers_throughput"
        )

        self.assertEqual(
            {"added", "not_carried_forward"},
            {row["status"] for row in changes},
        )
        self.assertFalse(any(row["status"] == "revised" for row in changes))
        self.assertFalse(
            any(
                proposal["rule_id"] == "capacity_revision_15_percent"
                for proposal in comparison["alert_proposals"]
            )
        )
        self.assertEqual(
            {"project_addition", "facility_total"},
            {row["series"]["dimensions"]["scope_kind"] for row in changes},
        )

    def test_capacity_basis_is_part_of_the_semantic_series(self) -> None:
        prior, current = self._release_pair()
        prior_loaded = change_module._load_release(prior, "prior fixture")
        current_loaded = change_module._load_release(current, "current fixture")
        claims = copy.deepcopy(list(current_loaded.claims))
        target = next(
            claim
            for claim in claims
            if claim["predicate"] == "capacity.units_throughput"
        )
        target["value"]["basis"] = "qualified"
        current_loaded = change_module._LoadedRelease(
            path=current_loaded.path,
            manifest=current_loaded.manifest,
            manifest_sha256=current_loaded.manifest_sha256,
            claims=tuple(claims),
        )

        # The v1 input grammar admits only announced numeric claims. Patch only the
        # already-tested release boundary to exercise the comparator's future-safe key.
        with patch.object(
            change_module,
            "_load_release",
            side_effect=(prior_loaded, current_loaded),
        ):
            comparison = compare_releases(prior, current)

        changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )
        self.assertEqual(
            {"added", "not_carried_forward"},
            {row["status"] for row in changes},
        )
        self.assertFalse(any(row["status"] == "revised" for row in changes))
        self.assertEqual(
            {"announced", "qualified"},
            {row["series"]["dimensions"]["basis"] for row in changes},
        )
        self.assertFalse(
            any(
                proposal["rule_id"] == "capacity_revision_15_percent"
                for proposal in comparison["alert_proposals"]
            )
        )

    def test_value_kind_is_part_of_the_semantic_series(self) -> None:
        prior, current = self._release_pair()
        prior_loaded = change_module._load_release(prior, "prior fixture")
        current_loaded = change_module._load_release(current, "current fixture")
        claims = copy.deepcopy(list(current_loaded.claims))
        target = next(
            claim
            for claim in claims
            if claim["predicate"] == "cohort.company"
        )
        target["value_kind"] = "geometry"
        target["value"] = {"coordinates": None}
        current_loaded = change_module._LoadedRelease(
            path=current_loaded.path,
            manifest=current_loaded.manifest,
            manifest_sha256=current_loaded.manifest_sha256,
            claims=tuple(claims),
        )

        # Patch only the validated release boundary to exercise a future schema
        # in which a predicate could migrate between two value representations.
        with patch.object(
            change_module,
            "_load_release",
            side_effect=(prior_loaded, current_loaded),
        ):
            comparison = compare_releases(prior, current)

        changes = self._changes_for_predicate(comparison, "cohort.company")
        changed_series = [row for row in changes if row["status"] != "reaffirmed"]
        self.assertEqual(2, len(changed_series))
        self.assertEqual(
            {"added", "not_carried_forward"},
            {row["status"] for row in changed_series},
        )
        self.assertEqual(
            {"geometry", "scalar"},
            {row["series"]["value_kind"] for row in changed_series},
        )

    def test_missing_current_claim_is_not_carried_forward_without_negative_evidence(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            facility = self._facility(spec, "Amkor")
            facility["capacities"] = facility["capacities"][1:]

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )

        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual("not_carried_forward", change["status"])
        self.assertIsNotNone(change["prior_claim"])
        self.assertIsNone(change["current_claim"])
        self.assertFalse(change["negative_evidence"])
        self.assertEqual([], change["alert_proposal_fingerprints"])
        self.assertIn("not negative evidence", change["interpretation"])
        self.assertFalse(
            any(
                proposal["series_id"] == change["series_id"]
                for proposal in comparison["alert_proposals"]
            )
        )

    def test_capability_readiness_change_is_detected_with_category_bound(self) -> None:
        def mutate(spec: dict[str, object]) -> None:
            self._facility(spec, "TSMC")["capabilities"][0]["readiness"] = (
                "equipment_installed"
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison, "facility.capability"
        )
        revised = [row for row in changes if row["status"] == "revised"]

        self.assertEqual(1, len(revised))
        change = revised[0]
        self.assertEqual(
            {
                "category": "leading_edge_logic",
                "technology": (
                    "Fab 21 / first TSMC Arizona fab; 4nm-5nm leading-edge "
                    "project scope and 5nm most-advanced volume technology"
                ),
                "valid_from": "2025-12-31",
            },
            change["series"]["dimensions"],
        )
        self.assertEqual(
            "production", change["prior_claim"]["value"]["readiness"]
        )
        self.assertEqual(
            "equipment_installed", change["current_claim"]["value"]["readiness"]
        )
        proposal = self._proposal_for_predicate(
            comparison, "facility.capability"
        )
        self.assertEqual("capability_readiness_change", proposal["rule_id"])

    def test_capability_technology_and_valid_from_define_series_slices(self) -> None:
        def mutate(spec: dict[str, object]) -> None:
            facility = self._facility(spec, "TSMC")
            additional = copy.deepcopy(facility["capabilities"][0])
            additional["technology"] = "N2 future technology slice"
            additional["valid_from"] = "2026-08-21"
            facility["capabilities"].append(additional)

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison,
            "facility.capability",
        )
        added = [row for row in changes if row["status"] == "added"]

        self.assertEqual(1, len(added))
        self.assertEqual(
            {
                "category": "leading_edge_logic",
                "technology": "N2 future technology slice",
                "valid_from": "2026-08-21",
            },
            added[0]["series"]["dimensions"],
        )

    def test_later_capability_observation_preserves_slices_and_binds_both_claims(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            facility = self._facility(spec, "Amkor")
            facility["lifecycle"]["state"] = "tools_installing"
            facility["lifecycle"]["as_of"] = "2026-08-21"
            capability = facility["capabilities"][0]
            capability["readiness"] = "equipment_installed"
            capability["valid_from"] = "2026-08-21"

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        changes = [
            row
            for row in self._changes_for_predicate(comparison, "facility.capability")
            if row["status"] != "reaffirmed"
        ]
        self.assertEqual(2, len(changes))
        added = next(row for row in changes if row["status"] == "added")
        omitted = next(row for row in changes if row["status"] == "not_carried_forward")
        proposal = self._proposal_for_predicate(comparison, "facility.capability")
        self.assertEqual("capability_readiness_change", proposal["rule_id"])
        self.assertEqual("added", proposal["change_status"])
        self.assertEqual(added["series_id"], proposal["series_id"])
        self.assertIsNone(added["prior_claim"])
        self.assertEqual([], omitted["alert_proposal_fingerprints"])
        self.assertFalse(omitted["negative_evidence"])
        self.assertEqual(
            [proposal["fingerprint"]], added["alert_proposal_fingerprints"]
        )
        self.assertEqual(
            {
                "comparison": "unambiguous_later_effective_observation",
                "category": "advanced_packaging",
                "technology": "2.5D and other next-generation advanced packaging production lines",
                "prior_readiness": "planned",
                "current_readiness": "equipment_installed",
                "prior_series_id": omitted["series_id"],
                "prior_valid_from": "2024-07-24",
                "current_valid_from": "2026-08-21",
            },
            proposal["reason"],
        )
        for side, snapshot in (
            ("prior", omitted["prior_claim"]),
            ("current", added["current_claim"]),
        ):
            self.assertEqual(snapshot["claim_id"], proposal[f"{side}_claim_id"])
            self.assertEqual(
                snapshot["evidence_links"],
                proposal["evidence_lineage"][side]["evidence_links"],
            )
        output = self.root / "later-capability-bundle"
        manifest = write_change_bundle(prior, current, output)
        self.assertEqual(manifest, validate_change_bundle(output))
        self.assertEqual(manifest, validate_change_bundle(output, prior, current))

    def test_later_capability_pairing_rejects_ambiguity_and_unlike_observations(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            capability = self._facility(spec, "TSMC")["capabilities"][0]
            capability["readiness"] = "equipment_installed"
            capability["valid_from"] = "2026-08-21"

        prior, current = self._release_pair(mutate_current=mutate)
        loaded_prior = change_module._load_release(prior, "prior fixture")
        loaded_current = change_module._load_release(current, "current fixture")
        for case in (
            "earlier_effective_date",
            "unchanged_readiness",
            "different_technology",
            "different_category",
            "different_entity",
            "closed_prior_observation",
            "ambiguous_prior",
            "ambiguous_current_with_carried_forward_prior",
        ):
            with self.subTest(case=case):
                prior_claims = copy.deepcopy(list(loaded_prior.claims))
                current_claims = copy.deepcopy(list(loaded_current.claims))
                prior_claim, current_claim = (
                    next(
                        claim for claim in claims
                        if claim["predicate"] == "facility.capability"
                        and claim["subject_stable_key"].startswith("tsmc:")
                    )
                    for claims in (prior_claims, current_claims)
                )
                if case == "earlier_effective_date":
                    current_claim["valid_from"] = "2025-01-01"
                elif case == "unchanged_readiness":
                    current_claim["value"]["readiness"] = prior_claim["value"]["readiness"]
                elif case == "different_technology":
                    current_claim["value"]["technology"] = "Distinct technology scope"
                elif case == "different_category":
                    current_claim["value"]["category"] = "hbm_fabrication"
                elif case == "different_entity":
                    current_claim["subject_entity_id"] = "different-facility"
                    current_claim["subject_stable_key"] = "tsmc:different-facility"
                elif case == "closed_prior_observation":
                    prior_claim["valid_to"] = "2026-06-01"
                elif case == "ambiguous_prior":
                    additional = copy.deepcopy(prior_claim)
                    additional["claim_id"] += "-another-observation"
                    additional["valid_from"] = "2026-01-01"
                    prior_claims.append(additional)
                else:
                    carried_forward = copy.deepcopy(prior_claim)
                    carried_forward["claim_id"] += "-carried-forward"
                    carried_forward["recorded_at"] = current_claim["recorded_at"]
                    current_claims.append(carried_forward)
                prior_fixture, current_fixture = (
                    change_module._LoadedRelease(
                        path=loaded.path,
                        manifest=loaded.manifest,
                        manifest_sha256=loaded.manifest_sha256,
                        claims=tuple(claims),
                    )
                    for loaded, claims in (
                        (loaded_prior, prior_claims),
                        (loaded_current, current_claims),
                    )
                )
                # Exercise the pairing boundary, including closed intervals that
                # the current v1 release producer does not yet emit.
                with patch.object(
                    change_module,
                    "_load_release",
                    side_effect=(prior_fixture, current_fixture),
                ):
                    comparison = compare_releases(prior, current)
                self.assertFalse(any(
                    proposal["rule_id"] == "capability_readiness_change"
                    for proposal in comparison["alert_proposals"]
                ))

    def test_comparator_rejects_conflicting_capability_readiness_for_one_slice(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            facility = self._facility(spec, "TSMC")
            conflicting = copy.deepcopy(facility["capabilities"][0])
            conflicting["readiness"] = "equipment_installed"
            facility["capabilities"].append(conflicting)

        prior = self._write_release(
            "conflicting-capability-prior",
            self._spec(
                "change-fixture-conflicting-capability-prior",
                as_of="2026-08-20",
                recorded_at="2026-08-20T18:00:00Z",
            ),
        )
        current = self._write_release(
            "conflicting-capability-current",
            self._spec(
                "change-fixture-conflicting-capability-current",
                as_of="2026-08-21",
                recorded_at="2026-08-21T18:00:00Z",
                mutate=mutate,
            ),
        )
        validate_release(current, require_html=True)

        with self.assertRaisesRegex(ValueError, "duplicate semantic series"):
            compare_releases(prior, current)

    def test_manifest_bound_historical_document_rendering_is_comparable(
        self,
    ) -> None:
        prior, current = self._release_pair()
        for filename in ("METHODOLOGY.md", "README.md"):
            path = prior / filename
            text = path.read_text(encoding="utf-8")
            text = text.replace("- Release ID:", "Release ID:")
            text = text.replace("- World-state cutoff:", "World-state cutoff:")
            text = text.replace("- Knowledge cutoff:", "Knowledge cutoff:")
            path.write_text(text, encoding="utf-8")
            self._refresh_manifest_file_metadata(prior, filename)

        with self.assertRaisesRegex(ValueError, "METHODOLOGY.md"):
            validate_release(prior, require_html=True)

        comparison = compare_releases(prior, current)
        self.assertEqual(75, comparison["summary"]["change_count"])
        self.assertEqual(
            75,
            comparison["summary"]["changes_by_status"]["reaffirmed"],
        )
        self.assertEqual([], comparison["alert_proposals"])

    def test_rejects_reverse_or_equal_release_chronology(self) -> None:
        prior, current = self._release_pair()

        with self.assertRaises(ValueError):
            compare_releases(current, prior)
        with self.assertRaises(ValueError):
            compare_releases(prior, prior)

    def test_rejects_tampered_input_release(self) -> None:
        prior, current = self._release_pair()
        claims = current / "claims.jsonl"
        claims.write_bytes(claims.read_bytes() + b"\n")

        with self.assertRaisesRegex(ValueError, "manifest"):
            compare_releases(prior, current)

    def test_rejects_duplicate_derived_semantic_series(self) -> None:
        prior, current = self._release_pair()
        prior_loaded = change_module._load_release(prior, "prior fixture")
        current_loaded = change_module._load_release(current, "current fixture")
        claims = copy.deepcopy(list(current_loaded.claims))
        duplicate = copy.deepcopy(
            next(
                claim
                for claim in claims
                if claim["predicate"] == "capacity.units_throughput"
            )
        )
        duplicate["claim_id"] = "duplicate-semantic-series-claim"
        duplicate["value"]["low"] = 4_255_000
        duplicate["value"]["base"] = 4_255_000
        duplicate["value"]["high"] = 4_255_000
        claims.append(duplicate)
        current_loaded = change_module._LoadedRelease(
            path=current_loaded.path,
            manifest=current_loaded.manifest,
            manifest_sha256=current_loaded.manifest_sha256,
            claims=tuple(claims),
        )

        # The input grammar rejects duplicate capacity slices first. Patch only
        # the validated boundary to retain defense in depth in the comparator.
        with patch.object(
            change_module,
            "_load_release",
            side_effect=(prior_loaded, current_loaded),
        ):
            with self.assertRaisesRegex(ValueError, "semantic series"):
                compare_releases(prior, current)

    @staticmethod
    def _directory_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    @staticmethod
    def _refresh_manifest_file_metadata(directory: Path, filename: str) -> None:
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = (directory / filename).read_bytes()
        manifest["files"][filename] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        manifest_path.write_bytes(_pretty_bytes(manifest))

    @staticmethod
    def _capacity_rule_proposals(
        comparison: dict[str, object],
    ) -> list[dict[str, object]]:
        return [
            proposal
            for proposal in comparison["alert_proposals"]
            if proposal["rule_id"] == "capacity_revision_15_percent"
        ]

    def test_capacity_threshold_is_independent_of_ambient_decimal_context(
        self,
    ) -> None:
        def mutate(spec: dict[str, object]) -> None:
            def revise(capacity: dict[str, object]) -> None:
                capacity["low"] = 4_251_300
                capacity["base"] = 4_251_300
                capacity["high"] = 4_251_300

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-14-9-percent",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "4,251,300 units/month."
                ),
                mutate=revise,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        with localcontext() as context:
            context.prec = 2
            comparison = compare_releases(prior, current)

        changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )
        self.assertEqual(["revised"], [row["status"] for row in changes])
        self.assertEqual([], self._capacity_rule_proposals(comparison))

    def test_huge_capacity_values_remain_finite_and_bundle_validates(self) -> None:
        huge = 10**400

        def mutate(spec: dict[str, object]) -> None:
            def revise(capacity: dict[str, object]) -> None:
                capacity["low"] = huge
                capacity["base"] = huge
                capacity["high"] = huge

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-huge-integer",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    f"{huge} units/month."
                ),
                mutate=revise,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        comparison = compare_releases(prior, current)
        json.dumps(comparison, allow_nan=False)

        bundle = self.root / "huge-capacity-changes"
        written = write_change_bundle(prior, current, bundle)
        self.assertEqual(written, validate_change_bundle(bundle, prior, current))
        for path in bundle.iterdir():
            if path.is_file():
                self.assertNotIn(b"Infinity", path.read_bytes())
                self.assertNotIn(b"NaN", path.read_bytes())

    def test_low_or_high_capacity_bound_change_at_threshold_alerts(self) -> None:
        cases = (
            (
                "low",
                "low",
                3_145_000,
                (
                    "Amkor announced expected capacity upon completion of "
                    "3,145,000 units/month. Amkor announced expected capacity upon "
                    "completion of 3,700,000 units/month."
                ),
            ),
            (
                "high",
                "high",
                4_255_000,
                (
                    "Amkor announced expected capacity upon completion of "
                    "3,700,000 units/month. Amkor announced expected capacity upon "
                    "completion of 4,255,000 units/month."
                ),
            ),
        )
        for index, (label, field, value, excerpt) in enumerate(cases):
            with self.subTest(bound=label):
                def mutate(spec: dict[str, object]) -> None:
                    def revise(capacity: dict[str, object]) -> None:
                        capacity[field] = value

                    self._replace_capacity(
                        spec,
                        capacity_index=0,
                        evidence_id=f"amkor-peoria-capacity-{label}-bound",
                        excerpt=excerpt,
                        mutate=revise,
                    )

                prior = self._write_release(
                    f"{label}-prior",
                    self._spec(
                        f"change-fixture-{label}-prior",
                        as_of="2026-08-20",
                        recorded_at=f"2026-08-20T18:00:0{index}Z",
                    ),
                )
                current = self._write_release(
                    f"{label}-current",
                    self._spec(
                        f"change-fixture-{label}-current",
                        as_of="2026-08-21",
                        recorded_at=f"2026-08-21T18:00:0{index}Z",
                        mutate=mutate,
                    ),
                )

                comparison = compare_releases(prior, current)
                proposals = self._capacity_rule_proposals(comparison)
                self.assertEqual(1, len(proposals))
                self.assertEqual(
                    "capacity.units_throughput", proposals[0]["predicate"]
                )

    def test_capacity_period_and_valid_from_define_distinct_series_slices(
        self,
    ) -> None:
        def prior_mutation(spec: dict[str, object]) -> None:
            def q1(capacity: dict[str, object]) -> None:
                capacity["period_start"] = "2026-01-01"
                capacity["period_end"] = "2026-04-01"
                capacity["valid_from"] = "2025-12-01"

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-2026-q1",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "3,700,000 units/month."
                ),
                mutate=q1,
            )

        def current_mutation(spec: dict[str, object]) -> None:
            def q2(capacity: dict[str, object]) -> None:
                capacity["period_start"] = "2026-04-01"
                capacity["period_end"] = "2026-07-01"
                capacity["valid_from"] = "2026-03-01"

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-2026-q2",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "3,700,000 units/month."
                ),
                mutate=q2,
            )

            def q3(capacity: dict[str, object]) -> None:
                capacity["period_start"] = "2026-07-01"
                capacity["period_end"] = "2026-10-01"
                capacity["valid_from"] = "2026-06-01"

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-2026-q3",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "3,700,000 units/month."
                ),
                mutate=q3,
                keep_original=True,
            )

        prior = self._write_release(
            "period-prior",
            self._spec(
                "change-fixture-period-prior",
                as_of="2026-08-20",
                recorded_at="2026-08-20T18:00:00Z",
                mutate=prior_mutation,
            ),
        )
        current = self._write_release(
            "period-current",
            self._spec(
                "change-fixture-period-current",
                as_of="2026-08-21",
                recorded_at="2026-08-21T18:00:00Z",
                mutate=current_mutation,
            ),
        )

        comparison = compare_releases(prior, current)
        changes = self._changes_for_predicate(
            comparison, "capacity.units_throughput"
        )
        self.assertEqual(
            ["added", "added", "not_carried_forward"],
            sorted(row["status"] for row in changes),
        )
        self.assertFalse(any(row["status"] == "revised" for row in changes))
        temporal_slices = {
            (
                row["series"]["dimensions"]["period_start"],
                row["series"]["dimensions"]["period_end"],
                row["series"]["dimensions"]["valid_from"],
            )
            for row in changes
        }
        self.assertEqual(
            {
                ("2026-01-01", "2026-04-01", "2025-12-01"),
                ("2026-04-01", "2026-07-01", "2026-03-01"),
                ("2026-07-01", "2026-10-01", "2026-06-01"),
            },
            temporal_slices,
        )

    def test_cli_rejects_release_nested_outputs_before_mutation(self) -> None:
        prior, current = self._release_pair()
        prior_bytes = self._directory_bytes(prior)
        current_bytes = self._directory_bytes(current)
        nested_output = prior / "unsafe-change-output"

        with patch.object(
            change_module.tempfile,
            "mkdtemp",
            side_effect=AssertionError("staging mutation attempted"),
        ):
            with self.assertRaises(ValueError):
                compare_script.compare(prior, current, nested_output, None)
        self.assertFalse(nested_output.exists())
        self.assertEqual(prior_bytes, self._directory_bytes(prior))
        self.assertEqual(current_bytes, self._directory_bytes(current))

        safe_output = self.root / "safe-change-output"
        nested_archive = current / "unsafe-change-output.tar.gz"
        with self.assertRaises(ValueError):
            compare_script.compare(
                prior,
                current,
                safe_output,
                nested_archive,
            )
        self.assertFalse(safe_output.exists())
        self.assertFalse(nested_archive.exists())
        self.assertEqual(prior_bytes, self._directory_bytes(prior))
        self.assertEqual(current_bytes, self._directory_bytes(current))

    def test_standalone_validator_rejects_numeric_type_mutations(self) -> None:
        def mutate(spec: dict[str, object]) -> None:
            def revise(capacity: dict[str, object]) -> None:
                capacity["low"] = 4_255_000
                capacity["base"] = 4_255_000
                capacity["high"] = 4_255_000

            self._replace_capacity(
                spec,
                capacity_index=0,
                evidence_id="amkor-peoria-capacity-type-fixture",
                excerpt=(
                    "Amkor announced expected capacity upon completion of "
                    "4,255,000 units/month."
                ),
                mutate=revise,
            )

        prior, current = self._release_pair(mutate_current=mutate)
        base_bundle = self.root / "type-base"
        write_change_bundle(prior, current, base_bundle)

        count_bundle = self.root / "float-count"
        shutil.copytree(base_bundle, count_bundle)
        summary_path = count_bundle / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        count_status = next(
            status
            for status, count in summary["changes_by_status"].items()
            if count
        )
        summary["changes_by_status"][count_status] = float(
            summary["changes_by_status"][count_status]
        )
        summary_path.write_bytes(_pretty_bytes(summary))
        count_manifest_path = count_bundle / "manifest.json"
        count_manifest = json.loads(
            count_manifest_path.read_text(encoding="utf-8")
        )
        count_manifest["changes_by_status"][count_status] = summary[
            "changes_by_status"
        ][count_status]
        raw_summary = summary_path.read_bytes()
        count_manifest["files"]["summary.json"] = {
            "bytes": len(raw_summary),
            "sha256": hashlib.sha256(raw_summary).hexdigest(),
        }
        count_manifest_path.write_bytes(_pretty_bytes(count_manifest))
        with self.assertRaises(ValueError):
            validate_change_bundle(count_bundle)

        reason_bundle = self.root / "float-reason"
        shutil.copytree(base_bundle, reason_bundle)
        proposals_path = reason_bundle / "alert_proposals.jsonl"
        proposals = [
            json.loads(line)
            for line in proposals_path.read_text(encoding="utf-8").splitlines()
        ]
        proposal = next(
            row
            for row in proposals
            if row["rule_id"] == "capacity_revision_15_percent"
        )
        proposal["reason"]["prior_values"]["base"] = float(
            proposal["reason"]["prior_values"]["base"]
        )
        proposals_path.write_bytes(
            b"".join(_canonical_bytes(row) for row in proposals)
        )
        self._refresh_manifest_file_metadata(
            reason_bundle, "alert_proposals.jsonl"
        )
        with self.assertRaises(ValueError):
            validate_change_bundle(reason_bundle)

    def test_standalone_validator_binds_claim_clocks_to_release_clocks(self) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "claim-clock-bundle"
        write_change_bundle(prior, current, bundle)
        changes_path = bundle / "changes.jsonl"
        changes = [
            json.loads(line)
            for line in changes_path.read_text(encoding="utf-8").splitlines()
        ]
        target = next(row for row in changes if row["prior_claim"] is not None)
        target["prior_claim"]["recorded_at"] = "2030-01-01T00:00:00Z"
        changes_path.write_bytes(
            b"".join(_canonical_bytes(row) for row in changes)
        )
        self._refresh_manifest_file_metadata(bundle, "changes.jsonl")

        with self.assertRaisesRegex(ValueError, "claim clock"):
            validate_change_bundle(bundle)

    def test_source_evidence_mutation_after_validation_is_rejected(self) -> None:
        prior, current = self._release_pair()
        evidence_path = prior / "evidence.jsonl"
        actual_validate = change_module.validate_release
        mutated = False

        def validate_then_mutate(
            path: str | Path, *args: object, **kwargs: object
        ) -> dict[str, object]:
            nonlocal mutated
            result = actual_validate(path, *args, **kwargs)
            if Path(path).resolve() == prior and not mutated:
                evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
                mutated = True
            return result

        with patch.object(
            change_module,
            "validate_release",
            side_effect=validate_then_mutate,
        ):
            with self.assertRaises((OSError, ValueError)):
                compare_releases(prior, current)
        self.assertTrue(mutated)

    def test_bundle_validator_rejects_concurrent_unmanaged_file_injection(
        self,
    ) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "injection-bundle"
        write_change_bundle(prior, current, bundle)
        unmanaged = bundle / "concurrent-unmanaged.txt"
        actual_snapshot = change_module._snapshot_open_directory
        injected = False

        def snapshot_then_inject(
            directory_descriptor: int, context: str
        ) -> dict[str, bytes]:
            nonlocal injected
            snapshot = actual_snapshot(directory_descriptor, context)
            if not injected:
                unmanaged.write_text("attacker\n", encoding="utf-8")
                injected = True
            return snapshot

        with patch.object(
            change_module,
            "_snapshot_open_directory",
            side_effect=snapshot_then_inject,
        ):
            with self.assertRaises((OSError, ValueError)):
                validate_change_bundle(bundle)
        self.assertTrue(injected)
        self.assertTrue(unmanaged.is_file())

    def test_archive_rejects_destination_path_replacement_during_final_hash(
        self,
    ) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "archive-race-bundle"
        archive = self.root / "archive-race.tar.gz"
        moved_owned_archive = self.root / "archive-race-owned.tar.gz"
        write_change_bundle(prior, current, bundle)
        actual_hash_stream = change_module._hash_stream
        hash_calls = 0

        def hash_then_replace(stream: object) -> tuple[int, str]:
            nonlocal hash_calls
            hash_calls += 1
            if hash_calls == 2:
                archive.rename(moved_owned_archive)
                archive.write_bytes(b"attacker replacement\n")
            return actual_hash_stream(stream)

        with patch.object(
            change_module,
            "_hash_stream",
            side_effect=hash_then_replace,
        ):
            with self.assertRaises(OSError):
                write_deterministic_change_archive(
                    bundle,
                    archive,
                    protected_directories=(prior, current),
                )
        self.assertEqual(2, hash_calls)
        self.assertEqual(b"attacker replacement\n", archive.read_bytes())
        self.assertTrue(moved_owned_archive.is_file())

    def test_change_bundles_and_archives_are_byte_identical(self) -> None:
        prior, current = self._release_pair()
        bundles: list[Path] = []
        archives: list[Path] = []
        archive_results: list[dict[str, object]] = []
        for index in range(2):
            bundle = self.root / f"changes-{index}"
            archive = self.root / f"changes-{index}.tar.gz"
            written = write_change_bundle(prior, current, bundle)
            validated = validate_change_bundle(bundle, prior, current)
            self.assertEqual(written, validated)
            archive_result = write_deterministic_change_archive(
                bundle,
                archive,
                protected_directories=(prior, current),
            )
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                archive_result["sha256"],
            )
            self.assertEqual(archive.stat().st_size, archive_result["bytes"])
            bundles.append(bundle)
            archives.append(archive)
            archive_results.append(archive_result)

        self.assertEqual(
            self._directory_bytes(bundles[0]), self._directory_bytes(bundles[1])
        )
        self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
        self.assertEqual(
            archive_results[0]["sha256"], archive_results[1]["sha256"]
        )

    def test_fifo_is_rejected_without_waiting_for_a_writer(self) -> None:
        os.mkfifo(self.root / "pipe")
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(ValueError, "regular non-symlink"):
                change_module._read_regular_file_at(descriptor, "pipe", "fixture")
        finally:
            os.close(descriptor)

    def test_bundle_stage_replacement_after_validation_is_not_published(self) -> None:
        prior, current = self._release_pair()
        output = self.root / "replaced-stage-bundle"
        actual_validate = change_module.validate_change_bundle
        replaced = False

        def validate_then_replace(path: Path, *args: object) -> dict[str, object]:
            nonlocal replaced
            result = actual_validate(path, *args)
            if Path(path).name.startswith(f".{output.name}.stage-"):
                saved = self.root / "saved-stage"
                Path(path).rename(saved)
                shutil.copytree(saved, path)
                replaced = True
            return result

        with patch.object(change_module, "validate_change_bundle", side_effect=validate_then_replace):
            with self.assertRaises(OSError):
                write_change_bundle(prior, current, output)
        self.assertTrue(replaced)
        self.assertFalse(output.exists())

    def test_archive_stage_replacement_after_validation_is_not_published(self) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "stage-race-bundle"
        archive = self.root / "stage-race.tar.gz"
        write_change_bundle(prior, current, bundle)
        actual_validate = change_module.validate_change_bundle
        replaced = False

        def validate_then_replace(path: Path, *args: object) -> dict[str, object]:
            nonlocal replaced
            result = actual_validate(path, *args)
            stages = list(self.root.glob(f".{archive.name}.stage-*"))
            if stages and not replaced:
                stage = stages[0]
                stage.rename(self.root / "saved-archive-stage")
                stage.write_bytes(b"replacement")
                replaced = True
            return result

        with patch.object(change_module, "validate_change_bundle", side_effect=validate_then_replace):
            with self.assertRaises(OSError):
                write_deterministic_change_archive(
                    bundle, archive, protected_directories=(prior, current)
                )
        self.assertTrue(replaced)
        self.assertFalse(archive.exists())

    def test_archive_requires_exact_bound_input_releases(self) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "bound-archive-bundle"
        archive = self.root / "bound-archive.tar.gz"
        write_change_bundle(prior, current, bundle)
        for inputs in ((), (prior,), (prior, prior), (current, prior)):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                write_deterministic_change_archive(
                    bundle, archive, protected_directories=inputs
                )
            self.assertFalse(archive.exists())

    def test_archive_rechecks_sources_after_tar_generation(self) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "source-race-bundle"
        archive = self.root / "source-race.tar.gz"
        write_change_bundle(prior, current, bundle)
        actual_hash = change_module._hash_stream

        def hash_then_mutate(stream: object) -> tuple[int, str]:
            result = actual_hash(stream)
            evidence = prior / "evidence.jsonl"
            evidence.write_bytes(evidence.read_bytes() + b"\n")
            return result

        with patch.object(change_module, "_hash_stream", side_effect=hash_then_mutate):
            with self.assertRaises((ValueError, OSError)):
                write_deterministic_change_archive(
                    bundle, archive, protected_directories=(prior, current)
                )
        self.assertFalse(archive.exists())

    def test_change_bundle_validation_rejects_tampering_extra_symlink_and_missing(
        self,
    ) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "changes"
        manifest = write_change_bundle(prior, current, bundle)
        managed_name = sorted(manifest["files"])[0]

        tampered = self.root / "tampered"
        shutil.copytree(bundle, tampered)
        managed = tampered / managed_name
        managed.write_bytes(managed.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            validate_change_bundle(tampered)

        unmanaged = self.root / "unmanaged"
        shutil.copytree(bundle, unmanaged)
        (unmanaged / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_change_bundle(unmanaged)

        symlinked = self.root / "symlinked"
        shutil.copytree(bundle, symlinked)
        symlink_target = self.root / "symlink-target"
        symlink_target.write_text("target\n", encoding="utf-8")
        (symlinked / managed_name).unlink()
        (symlinked / managed_name).symlink_to(symlink_target)
        with self.assertRaises(ValueError):
            validate_change_bundle(symlinked)

        missing = self.root / "missing"
        shutil.copytree(bundle, missing)
        (missing / managed_name).unlink()
        with self.assertRaises(ValueError):
            validate_change_bundle(missing)

    def test_change_outputs_are_no_replace(self) -> None:
        prior, current = self._release_pair()
        bundle = self.root / "changes"
        archive = self.root / "changes.tar.gz"
        write_change_bundle(prior, current, bundle)
        write_deterministic_change_archive(
            bundle,
            archive,
            protected_directories=(prior, current),
        )

        with self.assertRaises(ValueError):
            write_change_bundle(prior, current, bundle)
        with self.assertRaises(ValueError):
            write_deterministic_change_archive(
                bundle,
                archive,
                protected_directories=(prior, current),
            )
