from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.snapshot import SOURCE_SNAPSHOT_FORMAT, verify_snapshot


class SnapshotTests(unittest.TestCase):
    def _snapshot(self, root: Path) -> None:
        raw = b"archived source"
        (root / "input.html").write_bytes(raw)
        manifest = {
            "format": SOURCE_SNAPSHOT_FORMAT,
            "retrieved_at": "2026-07-17T12:00:00Z",
            "inputs": [
                {
                    "path": "input.html",
                    "record_type": "award_index_page",
                    "url": "https://example.test/source",
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content_type": "text/html",
                }
            ],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _complete_nist_snapshot(self, root: Path) -> dict[str, object]:
        index_raw = b"""
        <div class="margin-top-3">
          <a href="/chips/example-award">Example award</a>
          <div class="nist-field__item">CHIPS Program Office</div>
        </div>
        """
        detail_raw = b"archived award detail"
        (root / "input.html").write_bytes(index_raw)
        (root / "detail.html").write_bytes(detail_raw)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["inputs"] = [
            {
                "path": "input.html",
                "record_type": "award_index_page",
                "url": "https://www.nist.gov/chips/chips-america-awards",
                "bytes": len(index_raw),
                "sha256": hashlib.sha256(index_raw).hexdigest(),
                "content_type": "text/html",
            },
            {
                "path": "detail.html",
                "record_type": "award_detail_page",
                "url": "https://www.nist.gov/chips/example-award",
                "bytes": len(detail_raw),
                "sha256": hashlib.sha256(detail_raw).hexdigest(),
                "content_type": "text/html",
            },
        ]
        manifest["source_scopes"] = {
            "nist_chips_awards": {
                "complete": True,
                "index_page_count": 1,
                "detail_page_count": 1,
                "detail_coverage": (
                    "all_chips_program_office_links_in_archived_index_pages"
                ),
            }
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_verifies_and_routes_immutable_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            snapshot = verify_snapshot(root)
            self.assertEqual(snapshot.retrieved_at, "2026-07-17T12:00:00Z")
            self.assertFalse(snapshot.scope_is_complete("nist_chips_awards"))
            self.assertEqual(snapshot.paths("award_index_page"), [(root / "input.html").resolve()])
            self.assertEqual(snapshot.one("award_index_page").metadata["content_type"], "text/html")

    def test_explicit_source_scope_completeness_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            manifest = self._complete_nist_snapshot(root)
            manifest_path = root / "manifest.json"

            snapshot = verify_snapshot(root)

            self.assertTrue(snapshot.scope_is_complete("nist_chips_awards"))

            manifest["source_scopes"]["nist_chips_awards"]["detail_coverage"] = (
                "not_acquired"
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must declare detail_coverage"):
                verify_snapshot(root)

            manifest["source_scopes"]["nist_chips_awards"]["detail_coverage"] = (
                "all_chips_program_office_links_in_archived_index_pages"
            )
            manifest["source_scopes"]["nist_chips_awards"]["complete"] = "yes"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-boolean complete"):
                verify_snapshot(root)

    def test_rejects_source_scope_counts_that_do_not_match_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_scopes"] = {
                "nist_chips_awards": {
                    "complete": False,
                    "index_page_count": 2,
                    "detail_page_count": 0,
                    "detail_coverage": "not_acquired",
                }
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "declares index_page_count 2"):
                verify_snapshot(root)

            manifest["source_scopes"]["nist_chips_awards"]["index_page_count"] = 1
            manifest["source_scopes"]["nist_chips_awards"]["detail_page_count"] = 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "declares detail_page_count 1"):
                verify_snapshot(root)

    def test_rejects_complete_nist_scope_without_detail_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_scopes"] = {
                "nist_chips_awards": {
                    "complete": True,
                    "index_page_count": 1,
                    "detail_page_count": 0,
                    "detail_coverage": (
                        "all_chips_program_office_links_in_archived_index_pages"
                    ),
                }
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "requires award index and detail inputs"):
                verify_snapshot(root)

    def test_rejects_complete_nist_scope_with_incomplete_link_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            manifest = self._complete_nist_snapshot(root)
            manifest_path = root / "manifest.json"
            manifest["inputs"][1]["url"] = "https://www.nist.gov/chips/unlinked-award"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not archive exactly"):
                verify_snapshot(root)

    def test_rejects_mutated_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._snapshot(root)
            (root / "input.html").write_bytes(b"mutated")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                verify_snapshot(root)

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root.parent / "outside.html"
            outside.write_bytes(b"outside")
            manifest = {
                "format": SOURCE_SNAPSHOT_FORMAT,
                "retrieved_at": "2026-07-17T12:00:00Z",
                "inputs": [
                    {
                        "path": "../outside.html",
                        "record_type": "award_index_page",
                        "url": "https://example.test/source",
                        "bytes": 7,
                        "sha256": hashlib.sha256(b"outside").hexdigest(),
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            try:
                with self.assertRaisesRegex(ValueError, "escapes"):
                    verify_snapshot(root)
            finally:
                outside.unlink(missing_ok=True)

    def test_rejects_retrieval_cutoff_before_osm_source_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = json.dumps(
                {
                    "osm3s": {"timestamp_osm_base": "2026-07-17T12:01:00Z"},
                    "elements": [],
                }
            ).encode("utf-8")
            (root / "osm.json").write_bytes(raw)
            manifest = {
                "format": SOURCE_SNAPSHOT_FORMAT,
                "retrieved_at": "2026-07-17T12:00:00Z",
                "inputs": [
                    {
                        "path": "osm.json",
                        "record_type": "osm_overpass_snapshot",
                        "url": "https://overpass-api.de/api/interpreter",
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "predates"):
                verify_snapshot(root)


if __name__ == "__main__":
    unittest.main()
