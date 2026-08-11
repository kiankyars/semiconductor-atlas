from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from semiconductor_atlas.adapters.eea_industrial import (
    EEA_FILTER_VERSION,
    EEA_RECORD_TYPE,
)
from semiconductor_atlas.eea_industrial_review import (
    EEA_REVIEW_FORMAT,
    EEA_REVIEW_QUEUE_FORMAT,
    build_eea_industrial_review_queue,
    main,
    parse_eea_industrial_review_bytes,
    parse_eea_industrial_review_queue_bytes,
    propose_eea_industrial_review_queue,
    read_eea_industrial_review_file,
    read_eea_industrial_review_queue_file,
)
from semiconductor_atlas.repository import stable_id


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _projection(values: dict[str, object], digest_character: str) -> dict[str, object]:
    return {
        "projected_values_sha256": digest_character * 64,
        "values": values,
    }


def _source_candidate(
    facility_id: str,
    *,
    country: str,
    facility_name: str,
    reason_kinds: tuple[str, ...],
    functions: tuple[tuple[str | None, str | None], ...] = (),
    site_name: str | None = None,
    street_name: str = "Source Street",
) -> dict[str, object]:
    facility = {
        "Facility_INSPIRE_ID": facility_id,
        "buildingNumber": "7",
        "city": "Example City",
        "countryCode": country,
        "nameOfFeature": facility_name,
        "parentCompanyName": "Example Parent",
        "pointGeometryLat": "48.123",
        "pointGeometryLon": "11.456",
        "postalCode": "12345",
        "streetName": street_name,
    }
    return {
        "candidate_reasons": [{"kind": kind} for kind in reason_kinds],
        "facility_detail_rows": [],
        "facility_inspire_id": facility_id,
        "facility_rows": [_projection(facility, "a")],
        "function_rows": [
            _projection(
                {
                    "NACEMainEconomicActivityCode": code,
                    "NACEMainEconomicActivityName": name,
                },
                str((index + 1) % 10),
            )
            for index, (code, name) in enumerate(functions)
        ],
        "join_anomalies": [],
        "latest_source_detail_reporting_year": "2024",
        "metadata_rows": [],
        "record_type": EEA_RECORD_TYPE,
        "site_rows": (
            []
            if site_name is None
            else [
                _projection(
                    {
                        "Site_INSPIRE_ID": facility_id.replace("FACILITY", "SITE"),
                        "nameOfFeature": site_name,
                    },
                    "b",
                )
            ]
        ),
    }


def _snapshot() -> SimpleNamespace:
    candidates = (
        _source_candidate(
            "DE.TEST/3.FACILITY",
            country="DE",
            facility_name="Zeta Semiconductor Plant",
            reason_kinds=(
                "electronic_components_nace_26_11_candidate",
                "explicit_facility_name_candidate",
            ),
            functions=(
                ("99.99", "Other source function"),
                ("26.11", "Manufacture of electronic components"),
            ),
        ),
        _source_candidate(
            "FR.TEST/2.FACILITY",
            country="FR",
            facility_name="Alpha Microelectronics",
            reason_kinds=("explicit_facility_name_candidate",),
            site_name="Alpha Industrial Site",
        ),
        _source_candidate(
            "AT.TEST/1.FACILITY",
            country="AT",
            facility_name="Circuit Board Works",
            reason_kinds=("electronic_components_nace_26_11_candidate",),
            functions=(("26.11", "Manufacture of electronic components"),),
            street_name="\nSource Street\n",
        ),
    )
    return SimpleNamespace(
        accepted_at="2026-07-20T16:00:00Z",
        candidate_count=len(candidates),
        candidate_sha256="b" * 64,
        manifest_sha256="a" * 64,
        scan=SimpleNamespace(candidates=candidates),
    )


def _queue():
    return build_eea_industrial_review_queue(
        _snapshot(),
        generated_at="2026-07-20T18:00:00Z",
        knowledge_cutoff_at="2026-07-20T17:00:00Z",
    )


def _evidence(candidate_id: str) -> dict[str, object]:
    return {
        "accessed_at": "2026-07-20T16:30:00Z",
        "excerpt": "The source describes the activity performed at this facility.",
        "title": "Official facility description",
        "url": f"https://example.org/facilities/{candidate_id}",
    }


def _review_payload(queue) -> dict[str, object]:
    candidate_ids = sorted(item.candidate_id for item in queue.candidates)
    outcomes = ("accept_in_scope", "defer", "reject_out_of_scope")
    decisions = []
    for candidate_id, outcome in zip(candidate_ids, outcomes, strict=True):
        decisions.append(
            {
                "candidate_id": candidate_id,
                "evidence": [] if outcome == "defer" else [_evidence(candidate_id)],
                "outcome": outcome,
                "reason": "Disposition is limited to the reviewed facility scope.",
            }
        )
    return {
        "candidate_count": len(candidate_ids),
        "candidate_queue_sha256": queue.raw_sha256,
        "decisions": decisions,
        "filter_version": EEA_FILTER_VERSION,
        "format": EEA_REVIEW_FORMAT,
        "knowledge_cutoff_at": queue.knowledge_cutoff_at,
        "reviewed_at": "2026-07-20T18:30:00Z",
        "reviewed_by": "reviewer@example.org",
        "snapshot_manifest_sha256": queue.snapshot_manifest_sha256,
        "source_candidate_sha256": queue.source_candidate_sha256,
    }


class EEAIndustrialReviewQueueTests(unittest.TestCase):
    def test_builds_stable_decision_free_queue_with_all_triage_buckets(self) -> None:
        queue = _queue()

        self.assertEqual(EEA_REVIEW_QUEUE_FORMAT, queue.format)
        self.assertEqual((1, 2, 3), tuple(c.review_priority for c in queue.candidates))
        self.assertEqual(
            (
                "explicit_name_and_exact_nace_26_11",
                "explicit_name_only",
                "exact_nace_26_11_only",
            ),
            tuple(c.triage_basis for c in queue.candidates),
        )
        first = queue.candidates[0]
        self.assertEqual(
            stable_id(
                "eea-industrial-review-candidate",
                EEA_FILTER_VERSION,
                first.facility_inspire_id,
            ),
            first.candidate_id,
        )
        self.assertEqual(
            ("26.11", "99.99"),
            tuple(item.nace_code for item in first.functions),
        )
        self.assertEqual("\nSource Street\n", queue.candidates[2].street_name)
        self.assertEqual(hashlib.sha256(queue.raw_bytes).hexdigest(), queue.raw_sha256)
        self.assertEqual(
            queue, parse_eea_industrial_review_queue_bytes(queue.raw_bytes)
        )
        payload = json.loads(queue.raw_bytes)
        self.assertNotIn("decisions", payload)
        self.assertFalse(payload["review_policy"]["queue_priority_is_outcome"])
        self.assertFalse(
            payload["review_policy"]["candidate_membership_is_classification"]
        )
        self.assertTrue(payload["review_policy"]["review_knowledge_cutoff_may_advance"])
        self.assertIn(
            "capacity_or_output",
            payload["review_policy"]["forbidden_inferences"],
        )
        with self.assertRaises(FrozenInstanceError):
            queue.generated_at = "changed"  # type: ignore[misc]

    def test_build_is_deterministic_and_binds_source_candidate_hashes(self) -> None:
        first = _queue()
        second = _queue()

        self.assertEqual(first.raw_bytes, second.raw_bytes)
        self.assertEqual("b" * 64, first.source_candidate_sha256)
        self.assertEqual(
            3, len({item.source_candidate_sha256 for item in first.candidates})
        )

    def test_propose_deep_verifies_before_building(self) -> None:
        snapshot = _snapshot()
        with mock.patch(
            "semiconductor_atlas.eea_industrial_review.verify_eea_industrial_snapshot",
            return_value=snapshot,
        ) as verifier:
            queue = propose_eea_industrial_review_queue(
                "/snapshot",
                generated_at="2026-07-20T18:00:00Z",
                knowledge_cutoff_at="2026-07-20T17:00:00Z",
            )

        verifier.assert_called_once_with("/snapshot")
        self.assertEqual(3, queue.source_candidate_count)

    def test_rejects_noncanonical_duplicate_key_and_schema_drift(self) -> None:
        queue = _queue()
        payload = json.loads(queue.raw_bytes)
        compact = json.dumps(payload, separators=(",", ":")).encode()
        duplicate = queue.raw_bytes.replace(
            b"{\n",
            (b'{\n  "format": "semiconductor-atlas-eea-industrial-review-queue-v1",\n'),
            1,
        )
        extra = copy.deepcopy(payload)
        extra["decisions"] = []

        for label, raw in {
            "noncanonical": compact,
            "duplicate key": duplicate,
            "extra field": _canonical(extra),
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_eea_industrial_review_queue_bytes(raw)

    def test_rejects_policy_id_priority_order_and_count_mutations(self) -> None:
        original = json.loads(_queue().raw_bytes)
        mutations: dict[str, dict[str, object]] = {}

        policy = copy.deepcopy(original)
        policy["review_policy"]["queue_priority_is_outcome"] = True
        mutations["policy"] = policy

        candidate_id = copy.deepcopy(original)
        candidate_id["candidates"][0]["candidate_id"] = stable_id("wrong")
        mutations["candidate id"] = candidate_id

        priority = copy.deepcopy(original)
        priority["candidates"][0]["review_priority"] = 3
        priority["candidates"][0]["triage_basis"] = "exact_nace_26_11_only"
        mutations["priority"] = priority

        ordering = copy.deepcopy(original)
        ordering["candidates"][0], ordering["candidates"][1] = (
            ordering["candidates"][1],
            ordering["candidates"][0],
        )
        mutations["order"] = ordering

        count = copy.deepcopy(original)
        count["source_candidate_count"] = 2
        mutations["count"] = count

        duplicate_digest = copy.deepcopy(original)
        duplicate_digest["candidates"][1]["source_candidate_sha256"] = duplicate_digest[
            "candidates"
        ][0]["source_candidate_sha256"]
        mutations["source digest"] = duplicate_digest

        for label, payload in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_eea_industrial_review_queue_bytes(_canonical(payload))

    def test_rejects_invalid_clocks_and_source_shape(self) -> None:
        for generated, cutoff in {
            "noncanonical": (
                "2026-07-20T18:00:00+00:00",
                "2026-07-20T17:00:00Z",
            ),
            "cutoff after generated": (
                "2026-07-20T18:00:00Z",
                "2026-07-20T18:00:01Z",
            ),
            "snapshot after generated": (
                "2026-07-20T15:59:59Z",
                "2026-07-20T15:00:00Z",
            ),
        }.items():
            snapshot = _snapshot()
            if generated == "2026-07-20T15:59:59Z":
                snapshot.accepted_at = "2026-07-20T16:00:00Z"
            with self.subTest(label=generated), self.assertRaises(ValueError):
                build_eea_industrial_review_queue(
                    snapshot,
                    generated_at=generated,
                    knowledge_cutoff_at=cutoff,
                )

        snapshot = _snapshot()
        snapshot.scan.candidates[0]["record_type"] = "wrong"
        with self.assertRaisesRegex(ValueError, "record_type"):
            build_eea_industrial_review_queue(
                snapshot,
                generated_at="2026-07-20T18:00:00Z",
                knowledge_cutoff_at="2026-07-20T17:00:00Z",
            )


class EEAIndustrialReviewTests(unittest.TestCase):
    def test_parses_complete_evidence_bound_review(self) -> None:
        queue = _queue()
        raw = _canonical(_review_payload(queue))
        review = parse_eea_industrial_review_bytes(raw, queue=queue)

        self.assertEqual(EEA_REVIEW_FORMAT, review.format)
        self.assertEqual(queue.raw_sha256, review.candidate_queue_sha256)
        self.assertEqual(
            ("accept_in_scope", "defer", "reject_out_of_scope"),
            tuple(item.outcome for item in review.decisions),
        )
        self.assertEqual(hashlib.sha256(raw).hexdigest(), review.raw_sha256)
        with self.assertRaises(FrozenInstanceError):
            review.reviewed_by = "changed"  # type: ignore[misc]

    def test_requires_exact_complete_sorted_candidate_set(self) -> None:
        queue = _queue()
        original = _review_payload(queue)
        missing = copy.deepcopy(original)
        missing["decisions"].pop()
        duplicate = copy.deepcopy(original)
        duplicate["decisions"][1]["candidate_id"] = duplicate["decisions"][0][
            "candidate_id"
        ]
        unsorted = copy.deepcopy(original)
        unsorted["decisions"][0], unsorted["decisions"][1] = (
            unsorted["decisions"][1],
            unsorted["decisions"][0],
        )

        for label, payload in {
            "missing": missing,
            "duplicate": duplicate,
            "unsorted": unsorted,
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

    def test_requires_exact_queue_and_snapshot_bindings(self) -> None:
        queue = _queue()
        original = _review_payload(queue)
        mutations = {
            "candidate_count": 2,
            "candidate_queue_sha256": "c" * 64,
            "filter_version": "eea-industrial-semiconductor-candidates-v2",
            "knowledge_cutoff_at": "2026-07-20T16:59:59Z",
            "snapshot_manifest_sha256": "d" * 64,
            "source_candidate_sha256": "e" * 64,
        }
        for field, value in mutations.items():
            payload = copy.deepcopy(original)
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

        boolean_count = copy.deepcopy(original)
        boolean_count["candidate_count"] = True
        with self.assertRaises(ValueError):
            parse_eea_industrial_review_bytes(_canonical(boolean_count), queue=queue)

    def test_accept_and_reject_require_evidence_but_defer_does_not(self) -> None:
        queue = _queue()
        original = _review_payload(queue)
        for outcome in ("accept_in_scope", "reject_out_of_scope"):
            payload = copy.deepcopy(original)
            decision = next(
                item for item in payload["decisions"] if item["outcome"] == outcome
            )
            decision["evidence"] = []
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

        parsed = parse_eea_industrial_review_bytes(_canonical(original), queue=queue)
        deferred = next(item for item in parsed.decisions if item.outcome == "defer")
        self.assertEqual((), deferred.evidence)

    def test_rejects_unsafe_or_out_of_cutoff_evidence(self) -> None:
        queue = _queue()
        original = _review_payload(queue)
        accept_index = next(
            index
            for index, item in enumerate(original["decisions"])
            if item["outcome"] == "accept_in_scope"
        )
        mutations: dict[str, object] = {
            "http": "http://example.org/facility",
            "userinfo": "https://user:password@example.org/facility",
            "secret query": "https://example.org/facility?token=secret",
        }
        for label, url in mutations.items():
            payload = copy.deepcopy(original)
            payload["decisions"][accept_index]["evidence"][0]["url"] = url
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

        after_cutoff = copy.deepcopy(original)
        after_cutoff["decisions"][accept_index]["evidence"][0]["accessed_at"] = (
            "2026-07-20T17:00:01Z"
        )
        with self.assertRaisesRegex(ValueError, "knowledge cutoff"):
            parse_eea_industrial_review_bytes(_canonical(after_cutoff), queue=queue)

    def test_review_can_advance_its_evidence_cutoff(self) -> None:
        queue = _queue()
        payload = _review_payload(queue)
        payload["knowledge_cutoff_at"] = "2026-07-20T18:15:00Z"
        for decision in payload["decisions"]:
            for evidence in decision["evidence"]:
                evidence["accessed_at"] = "2026-07-20T18:10:00Z"

        review = parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

        self.assertEqual("2026-07-20T18:15:00Z", review.knowledge_cutoff_at)

    def test_requires_unique_sorted_evidence_and_canonical_json(self) -> None:
        queue = _queue()
        original = _review_payload(queue)
        accept = next(
            item
            for item in original["decisions"]
            if item["outcome"] == "accept_in_scope"
        )
        duplicate = copy.deepcopy(original)
        duplicate_accept = next(
            item
            for item in duplicate["decisions"]
            if item["outcome"] == "accept_in_scope"
        )
        duplicate_accept["evidence"].append(
            copy.deepcopy(duplicate_accept["evidence"][0])
        )

        unsorted = copy.deepcopy(original)
        unsorted_accept = next(
            item
            for item in unsorted["decisions"]
            if item["outcome"] == "accept_in_scope"
        )
        second = copy.deepcopy(accept["evidence"][0])
        second["url"] = "https://aaa.example.org/facility"
        unsorted_accept["evidence"].append(second)

        noncanonical = json.dumps(original, separators=(",", ":")).encode()
        for label, raw in {
            "duplicate URL": _canonical(duplicate),
            "unsorted": _canonical(unsorted),
            "noncanonical": noncanonical,
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_eea_industrial_review_bytes(raw, queue=queue)

    def test_review_cannot_predate_queue(self) -> None:
        queue = _queue()
        payload = _review_payload(queue)
        payload["reviewed_at"] = "2026-07-20T17:59:59Z"
        with self.assertRaisesRegex(ValueError, "predate"):
            parse_eea_industrial_review_bytes(_canonical(payload), queue=queue)

    def test_regular_file_readers_handle_short_reads_and_reject_symlinks(self) -> None:
        queue = _queue()
        review_raw = _canonical(_review_payload(queue))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "queue.json"
            review_path = root / "review.json"
            queue_path.write_bytes(queue.raw_bytes)
            review_path.write_bytes(review_raw)
            real_read = os.read

            def short_read(descriptor: int, size: int) -> bytes:
                return real_read(descriptor, min(size, 7))

            with mock.patch(
                "semiconductor_atlas.eea_industrial_review.os.read",
                side_effect=short_read,
            ):
                read_queue = read_eea_industrial_review_queue_file(queue_path)
                read_review = read_eea_industrial_review_file(
                    review_path, queue=read_queue
                )
            self.assertEqual(queue_path.absolute(), read_queue.path)
            self.assertEqual(review_path.absolute(), read_review.path)

            link = root / "queue-link.json"
            link.symlink_to(queue_path)
            with self.assertRaises(ValueError):
                read_eea_industrial_review_queue_file(link)

    def test_cli_writes_once_and_refuses_overwrite(self) -> None:
        queue = _queue()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "queue.json"
            arguments = [
                "propose",
                "--snapshot",
                "/snapshot",
                "--generated-at",
                queue.generated_at,
                "--knowledge-cutoff-at",
                queue.knowledge_cutoff_at,
                "--output",
                str(output),
            ]
            with (
                mock.patch(
                    "semiconductor_atlas.eea_industrial_review."
                    "propose_eea_industrial_review_queue",
                    return_value=queue,
                ),
                mock.patch("sys.stdout", new=io.StringIO()),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(0, main(arguments))
                self.assertEqual(2, main(arguments))
            self.assertEqual(queue.raw_bytes, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
