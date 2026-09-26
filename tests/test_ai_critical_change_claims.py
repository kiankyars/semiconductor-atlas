from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import semiconductor_atlas.ai_critical_changes as changes
from semiconductor_atlas.ai_critical import (
    CAPACITY_BASES,
    CLAIM_KINDS,
    load_baseline,
    materialize_baseline,
)
from semiconductor_atlas.ai_critical_change_claims import validate_claim_snapshot


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class AICriticalChangeClaimValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        baseline = load_baseline(
            REPOSITORY_ROOT / "baselines" / "ai_critical_manufacturing_v1.json",
            REPOSITORY_ROOT,
            verify_source_bytes=False,
        )
        cls.snapshots = [
            changes._claim_snapshot(claim)
            for claim in materialize_baseline(baseline)["claims"]
        ]

    def _snapshot(self, predicate: str) -> dict:
        return copy.deepcopy(next(
            snapshot for snapshot in self.snapshots
            if snapshot["predicate"] == predicate
        ))

    def test_all_75_real_baseline_snapshots_validate(self) -> None:
        self.assertEqual(75, len(self.snapshots))
        for snapshot in self.snapshots:
            with self.subTest(predicate=snapshot["predicate"], claim=snapshot["claim_id"]):
                validate_claim_snapshot(snapshot)

    def test_explicit_unknowns_five_bases_and_known_claim_kinds(self) -> None:
        lifecycle = self._snapshot("lifecycle.stage")
        lifecycle["value"]["value"] = "unknown"
        validate_claim_snapshot(lifecycle)
        capability = self._snapshot("facility.capability")
        capability["value"]["readiness"] = "unknown"
        validate_claim_snapshot(capability)
        capacity = self._snapshot("capacity.units_throughput")
        for basis in CAPACITY_BASES:
            with self.subTest(basis=basis):
                capacity["value"]["basis"] = basis
                validate_claim_snapshot(capacity)
        for kind in CLAIM_KINDS:
            with self.subTest(claim_kind=kind):
                lifecycle["claim_kind"] = kind
                validate_claim_snapshot(lifecycle)

    def test_invalid_claim_metadata_and_evidence(self) -> None:
        for field, bad in (
            ("claim_kind", "not-a-real-kind"),
            ("method", "method with spaces"),
            ("method", ""),
            ("valid_from", "2026-02-30"),
            ("recorded_at", "2026-08-20"),
            ("notes", {"bogus": True}),
            ("value_kind", "not-a-real-kind"),
        ):
            with self.subTest(field=field):
                snapshot = self._snapshot("facility.name")
                snapshot[field] = bad
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)
        for role in ("not-a-real-role", "context", "refute"):
            with self.subTest(role=role):
                snapshot = self._snapshot("facility.name")
                for link in snapshot["evidence_links"]:
                    link["role"] = role
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)
        for mutation in ("empty", "duplicate", "missing_link", "bad_hash"):
            with self.subTest(mutation=mutation):
                snapshot = self._snapshot("facility.name")
                if mutation == "empty":
                    snapshot["evidence_ids"] = []
                    snapshot["evidence_links"] = []
                elif mutation == "duplicate":
                    snapshot["evidence_ids"].append(snapshot["evidence_ids"][0])
                    snapshot["evidence_links"].append(copy.deepcopy(snapshot["evidence_links"][0]))
                elif mutation == "missing_link":
                    snapshot["evidence_links"].pop()
                else:
                    snapshot["evidence_links"][0]["fragment_sha256"] = "bogus"
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)

    def test_support_with_supplementary_context_and_refute_roles(self) -> None:
        snapshot = self._snapshot("facility.name")
        for role in ("context", "refute"):
            snapshot["evidence_links"].append({
                "evidence_id": f"zz-{role}", "role": role, "fragment_sha256": "a" * 64,
            })
        snapshot["evidence_links"].sort(key=lambda link: link["evidence_id"])
        snapshot["evidence_ids"] = [link["evidence_id"] for link in snapshot["evidence_links"]]
        validate_claim_snapshot(snapshot)

    def test_scalar_and_capability_values_are_typed_and_vocabularies_bounded(self) -> None:
        cases = (
            ("facility.name", {"bogus": True}),
            ("facility.name", {"scalar_type": "text", "value": True, "unit": None}),
            ("lifecycle.stage", {"scalar_type": "text", "value": "complete", "unit": None}),
            ("lifecycle.stage", {"scalar_type": "controlled_concept", "value": "made_up", "unit": None}),
            ("facility.identity_scope", {"scalar_type": "controlled_concept", "value": "company", "unit": None}),
            ("geography.country_code", {"scalar_type": "text", "value": "USA", "unit": None}),
        )
        for predicate, value in cases:
            with self.subTest(predicate=predicate, value=value):
                snapshot = self._snapshot(predicate)
                snapshot["value"] = value
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)
        for field, bad in (
            ("capability_type", "generic"), ("category", "solar"),
            ("technology", ""), ("readiness", "almost"),
        ):
            with self.subTest(field=field):
                snapshot = self._snapshot("facility.capability")
                snapshot["value"][field] = bad
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)

    def test_capacity_rejects_invalid_bounds_and_dimensions(self) -> None:
        for bad in ("not-a-number", True, None, float("nan"), float("inf"), -1):
            with self.subTest(bad=bad):
                snapshot = self._snapshot("capacity.units_throughput")
                snapshot["value"].update(low=bad, base=bad, high=bad)
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)
        for field, bad in (
            ("metric", "energy"), ("basis", "not-a-basis"),
            ("unit", "wafers/month"), ("scope_kind", "company_total"),
            ("input_output_basis", "estimated"), ("quantity_semantics", "quarter_total"),
            ("technology_scope", []), ("technology_scope", ["solar"]),
            ("period_start", "2026-13-01"),
        ):
            with self.subTest(field=field):
                snapshot = self._snapshot("capacity.units_throughput")
                snapshot["value"][field] = bad
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(snapshot)
        snapshot = self._snapshot("capacity.units_throughput")
        snapshot["value"].update(low=2, base=1, high=3)
        with self.assertRaises(ValueError):
            validate_claim_snapshot(snapshot)
        snapshot["value"].update(low=0, base=0, high=0)
        validate_claim_snapshot(snapshot)
        snapshot["value"].update(period_start="2026-04-01", period_end="2026-01-01")
        with self.assertRaises(ValueError):
            validate_claim_snapshot(snapshot)

    def test_facility_point_schema_requires_real_finite_bounded_coordinates(self) -> None:
        snapshot = self._snapshot("facility.name")
        snapshot.update(predicate="geography.point", value_kind="geometry", value={
            "type": "Point", "coordinates": [-112.0, 33.0], "crs": "EPSG:4326",
            "precision_m": 100, "geometry_scope": "source_reported_facility_point",
        })
        validate_claim_snapshot(snapshot)
        for field, bad in (
            ("coordinates", [181, 33]), ("coordinates", [-112, 91]),
            ("coordinates", [True, 33]), ("coordinates", [None, None]),
            ("coordinates", [-112, 33, 0]), ("coordinates", [float("nan"), 33]),
            ("precision_m", -1), ("precision_m", None), ("crs", "EPSG:3857"),
            ("type", "Polygon"), ("geometry_scope", "city_centroid"),
        ):
            with self.subTest(field=field, bad=bad):
                malformed = copy.deepcopy(snapshot)
                malformed["value"][field] = bad
                with self.assertRaises(ValueError):
                    validate_claim_snapshot(malformed)

    def _write_unbound_fixture(self, output: Path) -> None:
        """Real r3 claims, synthetic later vintage; no claim of source verification."""
        prior = {
            "release_id": "claim-validation-prior", "as_of": "2026-08-20",
            "recorded_at": self.snapshots[0]["recorded_at"], "manifest_sha256": "a" * 64,
        }
        current = {
            "release_id": "claim-validation-current", "as_of": "2026-08-21",
            "recorded_at": "2026-08-21T18:00:00Z", "manifest_sha256": "b" * 64,
        }
        indexes = []
        for side in (prior, current):
            index = {}
            for original in self.snapshots:
                snapshot = copy.deepcopy(original)
                snapshot["recorded_at"] = side["recorded_at"]
                if side is current:
                    snapshot["claim_id"] = "current-" + snapshot["claim_id"]
                descriptor = changes._series_descriptor(snapshot)
                index[changes._semantic_series_id(descriptor)] = (descriptor, snapshot)
            indexes.append(index)
        rows = changes._changes(*indexes)
        proposals = changes._derive_alert_proposals(rows, prior, current)
        self.assertEqual([], proposals)
        self.assertTrue(all(row["status"] == "reaffirmed" for row in rows))
        files, manifest = changes._bundle_payload({
            "changes": rows, "alert_proposals": proposals,
            "summary": changes._summary(rows, proposals, prior, current),
            "manifest": changes._manifest_metadata(prior, current),
        })
        output.mkdir()
        for filename, raw in files.items():
            (output / filename).write_bytes(raw)
        (output / "manifest.json").write_bytes(changes._pretty_bytes(manifest))
        changes.validate_change_bundle(output)

    def test_rehashed_unbound_reaffirmations_reject_forged_claims(self) -> None:
        cases = (
            ("capacity.units_throughput", "capacity", "capacity.low"),
            ("facility.name", "scalar", "scalar is missing fields"),
            ("facility.name", "claim_kind", "claim.claim_kind"),
            ("facility.name", "method", "claim.method"),
            ("facility.name", "role", "role"),
            ("facility.name", "missing_support", "support evidence"),
            ("facility.name", "duplicate", "unique|duplicate"),
        )
        for predicate, mutation, error in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary).resolve() / "bundle"
                self._write_unbound_fixture(output)
                rows = [json.loads(line) for line in (output / "changes.jsonl").read_text().splitlines()]
                row = next(row for row in rows if row["predicate"] == predicate)
                for side in ("prior_claim", "current_claim"):
                    snapshot = row[side]
                    if mutation == "capacity":
                        snapshot["value"].update(low="not-a-number", base="not-a-number", high="not-a-number")
                    elif mutation == "scalar":
                        snapshot["value"] = {"bogus": True}
                    elif mutation == "claim_kind":
                        snapshot["claim_kind"] = "not-a-real-kind"
                    elif mutation == "method":
                        snapshot["method"] = "method with spaces"
                    elif mutation in {"role", "missing_support"}:
                        for link in snapshot["evidence_links"]:
                            link["role"] = "not-a-real-role" if mutation == "role" else "context"
                    else:
                        snapshot["evidence_ids"].append(snapshot["evidence_ids"][0])
                        snapshot["evidence_links"].append(copy.deepcopy(snapshot["evidence_links"][0]))
                self.assertEqual("reaffirmed", changes._change_status(row["prior_claim"], row["current_claim"]))
                raw = changes._jsonl_bytes(rows)
                (output / "changes.jsonl").write_bytes(raw)
                manifest = json.loads((output / "manifest.json").read_bytes())
                manifest["files"]["changes.jsonl"] = changes._file_metadata(raw)
                (output / "manifest.json").write_bytes(changes._pretty_bytes(manifest))
                with self.assertRaisesRegex(ValueError, error):
                    changes.validate_change_bundle(output)


if __name__ == "__main__":
    unittest.main()
