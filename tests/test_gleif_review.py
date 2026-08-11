from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from semiconductor_atlas.gleif_review import (
    GLEIF_REVIEW_FORMAT,
    GLEIF_REVIEW_MAX_BYTES,
    GLEIF_REVIEW_MAX_DECISIONS,
    parse_gleif_review_bytes,
    read_gleif_review_file,
)


FIRST_LEI = "2549005GOBWLCSY63Q97"
SECOND_LEI = "529900Z9HRA5529A5V36"


def _decision(
    lei: str = FIRST_LEI,
    *,
    outcome: str = "match",
) -> dict[str, object]:
    return {
        "lei": lei,
        "target_entity_id": f"entity-{lei}",
        "target_entity_stable_key": f"organization:fixture:{lei}",
        "target_evidence_claim_version_ids": ["claim-001", "claim-002"],
        "outcome": outcome,
        "reason": "Legal name and registered address agree.",
        "score": 0.95,
        "candidate_rank": 1,
        "assignment_valid_from": "2026-07-19" if outcome == "match" else None,
    }


def _payload() -> dict[str, object]:
    return {
        "format": GLEIF_REVIEW_FORMAT,
        "reviewed_by": "reviewer@example.com",
        "reviewed_at": "2026-07-20T03:08:57Z",
        "snapshot_manifest_sha256": "a" * 64,
        "golden_copy_publish_date": "2026-07-19T16:00:00Z",
        "decisions": [_decision()],
    }


def _raw(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class GLEIFReviewTests(unittest.TestCase):
    def test_parses_bound_review_and_carries_raw_and_canonical_identity(self) -> None:
        payload = _payload()
        raw = _raw(payload)

        review = parse_gleif_review_bytes(raw)

        self.assertEqual(GLEIF_REVIEW_FORMAT, review.format)
        self.assertEqual("reviewer@example.com", review.reviewed_by)
        self.assertEqual("2026-07-20T03:08:57Z", review.reviewed_at)
        self.assertEqual("a" * 64, review.snapshot_manifest_sha256)
        self.assertEqual(
            "2026-07-19T16:00:00Z", review.golden_copy_publish_date
        )
        self.assertEqual(raw, review.raw_bytes)
        self.assertIsNone(review.path)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), review.raw_sha256)
        self.assertTrue(review.canonical_bytes.endswith(b"\n"))
        self.assertEqual(
            hashlib.sha256(review.canonical_bytes).hexdigest(),
            review.canonical_sha256,
        )
        decision = review.decisions[0]
        self.assertEqual(FIRST_LEI, decision.lei)
        self.assertEqual(
            ("claim-001", "claim-002"),
            decision.target_evidence_claim_version_ids,
        )
        self.assertEqual("2026-07-19", decision.assignment_valid_from)
        with self.assertRaises(FrozenInstanceError):
            review.reviewed_by = "changed"  # type: ignore[misc]

    def test_canonical_bytes_ignore_json_key_order_and_whitespace(self) -> None:
        payload = _payload()
        first = parse_gleif_review_bytes(_raw(payload))
        pretty = json.dumps(
            payload, ensure_ascii=False, indent=4, sort_keys=True
        )
        reordered = parse_gleif_review_bytes(
            (pretty + "\n").encode("utf-8")
        )

        self.assertNotEqual(first.raw_sha256, reordered.raw_sha256)
        self.assertEqual(first.canonical_bytes, reordered.canonical_bytes)
        self.assertEqual(first.canonical_sha256, reordered.canonical_sha256)

    def test_requires_exact_top_level_and_decision_fields(self) -> None:
        top_level = _payload()
        top_level["extra"] = True
        decision_level = _payload()
        del decision_level["decisions"][0]["reason"]  # type: ignore[index]

        for label, payload in {
            "extra top-level": top_level,
            "missing decision field": decision_level,
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

        wrong_format = _payload()
        wrong_format["format"] = "semiconductor-atlas-gleif-review-v2"
        with self.assertRaises(ValueError):
            parse_gleif_review_bytes(_raw(wrong_format))

    def test_rejects_duplicate_keys_nan_and_invalid_utf8(self) -> None:
        duplicate = _raw(_payload()).replace(
            b'"format":',
            b'"format":"semiconductor-atlas-gleif-review-v1","format":',
            1,
        )
        nan = _raw(_payload()).replace(b'"score":0.95', b'"score":NaN')
        infinity = _raw(_payload()).replace(b'"score":0.95', b'"score":1e999')

        for label, raw in {
            "duplicate key": duplicate,
            "NaN": nan,
            "infinite number": infinity,
            "invalid UTF-8": b'{"reviewed_by":"\xff"}',
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(raw)

    def test_requires_clean_strings_and_snapshot_hash(self) -> None:
        mutations = {
            "blank reviewer": ("reviewed_by", " "),
            "control reviewer": ("reviewed_by", "reviewer\u007f"),
            "uppercase hash": ("snapshot_manifest_sha256", "A" * 64),
            "short hash": ("snapshot_manifest_sha256", "a" * 63),
        }
        for label, (field, value) in mutations.items():
            payload = _payload()
            payload[field] = value
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

        for field in (
            "target_entity_id",
            "target_entity_stable_key",
            "reason",
        ):
            payload = _payload()
            payload["decisions"][0][field] = "\t"  # type: ignore[index]
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

    def test_reviewed_at_is_canonical_utc_and_publish_date_is_aware(self) -> None:
        invalid_reviewed_at = (
            "2026-07-20T03:08:57",
            "2026-07-20T03:08:57+00:00",
            "2026-07-19T20:08:57-07:00",
            "2026-07-20 03:08:57Z",
            "2026-07-20T03:08:57.000000Z",
        )
        for timestamp in invalid_reviewed_at:
            payload = _payload()
            payload["reviewed_at"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

        payload = _payload()
        payload["golden_copy_publish_date"] = "2026-07-19T17:00:00+01:00"
        review = parse_gleif_review_bytes(_raw(payload))
        self.assertEqual(
            "2026-07-19T17:00:00+01:00", review.golden_copy_publish_date
        )

        payload["golden_copy_publish_date"] = "2026-07-19T16:00:00"
        with self.assertRaises(ValueError):
            parse_gleif_review_bytes(_raw(payload))

    def test_requires_valid_sorted_unique_lei_decisions(self) -> None:
        invalid_lei = _payload()
        invalid_lei["decisions"][0][  # type: ignore[index]
            "lei"
        ] = "2549005GOBWLCSY63Q96"

        duplicate = _payload()
        duplicate["decisions"] = [_decision(), _decision()]

        unsorted = _payload()
        unsorted["decisions"] = [
            _decision(SECOND_LEI, outcome="defer"),
            _decision(FIRST_LEI),
        ]

        for label, payload in {
            "checksum": invalid_lei,
            "duplicate": duplicate,
            "unsorted": unsorted,
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

    def test_requires_one_to_five_hundred_decisions(self) -> None:
        empty = _payload()
        empty["decisions"] = []
        too_many = _payload()
        too_many["decisions"] = [
            copy.deepcopy(_decision())
            for _ in range(GLEIF_REVIEW_MAX_DECISIONS + 1)
        ]

        for label, payload in {"empty": empty, "too many": too_many}.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

    def test_enforces_outcome_score_rank_and_assignment_date(self) -> None:
        cases: dict[str, tuple[str, object]] = {
            "unknown outcome": ("outcome", "approve"),
            "boolean score": ("score", True),
            "negative score": ("score", -0.01),
            "high score": ("score", 1.01),
            "boolean rank": ("candidate_rank", True),
            "zero rank": ("candidate_rank", 0),
            "fractional rank": ("candidate_rank", 1.5),
            "missing match date": ("assignment_valid_from", None),
            "datetime match date": (
                "assignment_valid_from",
                "2026-07-19T00:00:00Z",
            ),
        }
        for label, (field, value) in cases.items():
            payload = _payload()
            payload["decisions"][0][field] = value  # type: ignore[index]
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

        for outcome in ("reject", "defer"):
            payload = _payload()
            payload["decisions"] = [_decision(outcome=outcome)]
            review = parse_gleif_review_bytes(_raw(payload))
            self.assertIsNone(review.decisions[0].assignment_valid_from)

            payload["decisions"][0][  # type: ignore[index]
                "assignment_valid_from"
            ] = "2026-07-19"
            with self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

        for boundary in (0, 1, 0.0, 1.0):
            payload = _payload()
            payload["decisions"][0]["score"] = boundary  # type: ignore[index]
            with self.subTest(boundary=boundary):
                review = parse_gleif_review_bytes(_raw(payload))
                self.assertEqual(float(boundary), review.decisions[0].score)

    def test_requires_sorted_unique_nonempty_evidence_claim_ids(self) -> None:
        cases = {
            "empty": [],
            "duplicate": ["claim-001", "claim-001"],
            "unsorted": ["claim-002", "claim-001"],
            "blank": [""],
        }
        for label, evidence_ids in cases.items():
            payload = _payload()
            payload["decisions"][0][  # type: ignore[index]
                "target_evidence_claim_version_ids"
            ] = evidence_ids
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_gleif_review_bytes(_raw(payload))

    def test_safe_file_reader_rejects_symlink_fifo_directory_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "review.json"
            actual.write_bytes(_raw(_payload()))
            review = read_gleif_review_file(actual)
            self.assertEqual(FIRST_LEI, review.decisions[0].lei)
            self.assertEqual(actual.absolute(), review.path)

            linked = root / "linked.json"
            linked.symlink_to(actual)
            with self.assertRaises(ValueError):
                read_gleif_review_file(linked)

            fifo = root / "review.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                read_gleif_review_file(fifo)

            with self.assertRaises(ValueError):
                read_gleif_review_file(root)

            oversized = root / "oversized.json"
            oversized.write_bytes(b"x" * (GLEIF_REVIEW_MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                read_gleif_review_file(oversized)

    def test_safe_file_reader_detects_before_after_stat_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "review.json"
            path.write_bytes(_raw(_payload()))
            real_fstat = os.fstat
            calls = 0

            def changing_fstat(descriptor: int) -> object:
                nonlocal calls
                calls += 1
                details = real_fstat(descriptor)
                if calls == 1:
                    return details
                return SimpleNamespace(
                    st_dev=details.st_dev,
                    st_ino=details.st_ino,
                    st_mode=details.st_mode,
                    st_nlink=details.st_nlink,
                    st_size=details.st_size,
                    st_mtime_ns=details.st_mtime_ns + 1,
                    st_ctime_ns=details.st_ctime_ns,
                )

            with mock.patch(
                "semiconductor_atlas.gleif_review.os.fstat",
                side_effect=changing_fstat,
            ), self.assertRaises(ValueError):
                read_gleif_review_file(path)

    def test_safe_file_reader_detects_path_swap_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "review.json"
            replacement = root / "replacement.json"
            displaced = root / "displaced.json"
            path.write_bytes(_raw(_payload()))
            replacement.write_bytes(_raw(_payload()) + b" ")
            real_read = os.read
            swapped = False

            def swapping_read(descriptor: int, size: int) -> bytes:
                nonlocal swapped
                chunk = real_read(descriptor, size)
                if chunk and not swapped:
                    swapped = True
                    path.rename(displaced)
                    replacement.rename(path)
                return chunk

            with mock.patch(
                "semiconductor_atlas.gleif_review.os.read",
                side_effect=swapping_read,
            ), self.assertRaises(ValueError):
                read_gleif_review_file(path)
            self.assertTrue(swapped)


if __name__ == "__main__":
    unittest.main()
