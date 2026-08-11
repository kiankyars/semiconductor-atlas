from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.taiwan_factory_registry import (
    TAIWAN_FACTORY_ARCHIVE_URL,
    TAIWAN_FACTORY_DATASET_URL,
    TAIWAN_FACTORY_FIELDS,
    TAIWAN_FACTORY_FILTER_VERSION,
    TAIWAN_FACTORY_LICENSE_URL,
    TAIWAN_FACTORY_POINTER_URL,
    TAIWAN_FACTORY_PRODUCT_TOKEN,
)
from semiconductor_atlas.taiwan_factory_snapshot import (
    TAIWAN_FACTORY_CANDIDATE_FILENAME,
    TAIWAN_FACTORY_SCOPE,
    create_taiwan_factory_snapshot,
    verify_taiwan_factory_snapshot,
)


RETRIEVED_AT = "2026-07-20T06:57:50Z"
LAST_MODIFIED = "Mon, 20 Jul 2026 03:37:05 GMT"
SOURCE_UPDATED_AT = "2026-07-20T03:37:05Z"


def _row(
    *,
    registration_number: str = "12345678",
    business_number: str = "00123456",
    name: str = "測試半導體股份有限公司一廠",
    products: str = "261半導體\n269其他電子零組件",
) -> dict[str, str]:
    return {
        "工廠名稱": name,
        "工廠登記編號": registration_number,
        "工廠設立許可案號": "TEST-CASE-1",
        "工廠地址": "新竹市東區測試路1號",
        "工廠市鎮鄉村里": "新竹市東區",
        "工廠負責人姓名": "不應出現在衍生資料的人名",
        "統一編號": business_number,
        "工廠組織型態": "股份有限公司",
        "工廠設立核准日期": "2026072000000",
        "工廠登記核准日期": "2026072000000",
        "工廠登記狀態": "生產中",
        "產業類別": "26電子零組件製造業",
        "主要產品": products,
    }


def _archive_raw() -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text,
        fieldnames=TAIWAN_FACTORY_FIELDS,
        dialect="excel",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerow(_row())
    writer.writerow(
        _row(
            registration_number="87654321",
            business_number="00987654",
            name="非半導體工廠",
            products="269其他電子零組件",
        )
    )
    csv_raw = b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("11506.csv", date_time=(2026, 7, 20, 3, 37, 4))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, csv_raw)
    return stream.getvalue()


def _create(root: Path, *, name: str = "snapshot"):
    archive = root / f"{name}.zip"
    raw = _archive_raw()
    archive.write_bytes(raw)
    return create_taiwan_factory_snapshot(
        archive,
        root / name,
        retrieved_at=RETRIEVED_AT,
        upstream_last_modified=LAST_MODIFIED,
        upstream_etag='"fixture-etag"',
        upstream_content_type="application/x-zip-compressed",
        upstream_content_length=len(raw),
    )


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_bytes())


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    raw = (
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    (root / "manifest.json").write_bytes(raw)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TaiwanFactorySnapshotTests(unittest.TestCase):
    def test_creates_closed_content_addressed_privacy_minimized_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            verified = verify_taiwan_factory_snapshot(snapshot.root)

            self.assertEqual(SOURCE_UPDATED_AT, verified.source_updated_at)
            self.assertEqual(RETRIEVED_AT, verified.retrieved_at)
            self.assertNotEqual(verified.source_updated_at, verified.retrieved_at)
            self.assertEqual(2, verified.row_count)
            self.assertEqual(1, verified.candidate_count)
            self.assertEqual(1, verified.business_number_count)
            self.assertEqual((("生產中", 1),), verified.registration_status_counts)
            self.assertEqual(f"raw/sha256/{verified.raw_sha256}.zip", verified.raw_path)
            self.assertEqual(_archive_raw(), verified.raw_bytes)

            derivative = verified.candidate_bytes.decode("utf-8")
            self.assertIn('"factory_registration_number":"12345678"', derivative)
            self.assertNotIn("工廠負責人姓名", derivative)
            self.assertNotIn("不應出現在衍生資料的人名", derivative)

            manifest = json.loads(verified.manifest_bytes)
            scope = manifest["source_scopes"][TAIWAN_FACTORY_SCOPE]
            self.assertEqual(TAIWAN_FACTORY_DATASET_URL, scope["dataset_url"])
            self.assertEqual(TAIWAN_FACTORY_POINTER_URL, scope["pointer_url"])
            self.assertEqual(TAIWAN_FACTORY_ARCHIVE_URL, scope["archive_url"])
            self.assertEqual(TAIWAN_FACTORY_LICENSE_URL, scope["rights"]["license_url"])
            self.assertEqual(
                {
                    "match_rule": "exact_line_token",
                    "source_field": "主要產品",
                    "token": TAIWAN_FACTORY_PRODUCT_TOKEN,
                    "version": TAIWAN_FACTORY_FILTER_VERSION,
                },
                scope["filter"],
            )
            self.assertFalse(scope["privacy"]["field_present_in_public_derivative"])
            self.assertEqual(
                "工廠負責人姓名",
                scope["privacy"]["source_field_present_in_retained_raw_archive"],
            )
            self.assertEqual(
                {
                    "content_length": len(_archive_raw()),
                    "content_type": "application/x-zip-compressed",
                    "etag": '"fixture-etag"',
                    "last_modified": LAST_MODIFIED,
                },
                scope["upstream_archive"]["response_metadata"],
            )

    def test_fixed_inputs_produce_byte_identical_snapshot_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _create(root, name="first")
            second = _create(root, name="second")
            self.assertEqual(_tree_bytes(first.root), _tree_bytes(second.root))

    def test_rejects_raw_candidate_and_hash_consistent_replay_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="raw-tamper")
            raw_path = snapshot.root / snapshot.raw_path
            raw_path.write_bytes(raw_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "archive hash or size mismatch"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="candidate-tamper")
            candidate = snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME
            candidate.write_bytes(
                candidate.read_bytes().replace("測".encode(), "錯".encode())
            )
            with self.assertRaisesRegex(ValueError, "derivative hash or size mismatch"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="replay-tamper")
            candidate = snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME
            raw = candidate.read_bytes().replace("測".encode(), "錯".encode())
            candidate.write_bytes(raw)
            manifest = _manifest(snapshot.root)
            entry = manifest["inputs"][0]
            entry["bytes"] = len(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "does not replay"):
                verify_taiwan_factory_snapshot(snapshot.root)

    def test_rejects_manifest_lies_and_noncanonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="count-lie")
            manifest = _manifest(snapshot.root)
            scope = manifest["source_scopes"][TAIWAN_FACTORY_SCOPE]
            scope["summary"]["candidate_count"] = 2
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "summary metadata"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="url-lie")
            manifest = _manifest(snapshot.root)
            manifest["source_scopes"][TAIWAN_FACTORY_SCOPE]["archive_url"] = (
                TAIWAN_FACTORY_ARCHIVE_URL + "?token=secret"
            )
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "archive_url drifted"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="noncanonical")
            manifest = _manifest(snapshot.root)
            (snapshot.root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "not canonical"):
                verify_taiwan_factory_snapshot(snapshot.root)

    def test_rejects_extra_missing_symlink_fifo_and_symlink_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="extra")
            (snapshot.root / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="missing")
            (snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME).unlink()
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _create(root, name="symlink")
            raw_path = snapshot.root / snapshot.raw_path
            target = root / "actual.zip"
            raw_path.replace(target)
            raw_path.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify_taiwan_factory_snapshot(snapshot.root)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as temporary:
                snapshot = _create(Path(temporary), name="fifo")
                os.mkfifo(snapshot.root / "unexpected.fifo")
                with self.assertRaisesRegex(ValueError, "regular file or directory"):
                    verify_taiwan_factory_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _create(root, name="root-target")
            linked = root / "linked-root"
            linked.symlink_to(snapshot.root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                verify_taiwan_factory_snapshot(linked)

    def test_reopens_files_and_detects_cross_interval_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="mutated")
            original_read = __import__(
                "semiconductor_atlas.taiwan_factory_snapshot",
                fromlist=["_SnapshotReader"],
            )._SnapshotReader.read
            changed = False

            def mutating_read(reader, relative_path, *, maximum_bytes):
                nonlocal changed
                raw = original_read(reader, relative_path, maximum_bytes=maximum_bytes)
                if relative_path == TAIWAN_FACTORY_CANDIDATE_FILENAME and not changed:
                    changed = True
                    path = snapshot.root / TAIWAN_FACTORY_CANDIDATE_FILENAME
                    path.write_bytes(raw.replace("測".encode(), "錯".encode()))
                return raw

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_factory_snapshot._SnapshotReader.read",
                    new=mutating_read,
                ),
                self.assertRaisesRegex(ValueError, "changed during verification"),
            ):
                verify_taiwan_factory_snapshot(snapshot.root)

    def test_creation_is_atomic_no_replace_and_rejects_unsafe_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            source.write_bytes(_archive_raw())
            output = root / "never-installed"
            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_factory_snapshot.canonical_candidate_jsonl_bytes",
                    side_effect=ValueError("injected transform failure"),
                ),
                self.assertRaisesRegex(ValueError, "injected transform failure"),
            ):
                create_taiwan_factory_snapshot(
                    source,
                    output,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=LAST_MODIFIED,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".never-installed.stage-*")))

            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                create_taiwan_factory_snapshot(
                    source,
                    existing,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=LAST_MODIFIED,
                )

            linked = root / "linked.zip"
            linked.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "regular file"):
                create_taiwan_factory_snapshot(
                    linked,
                    root / "linked-output",
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=LAST_MODIFIED,
                )

            if hasattr(os, "mkfifo"):
                fifo = root / "source.fifo"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(ValueError, "regular file"):
                    create_taiwan_factory_snapshot(
                        fifo,
                        root / "fifo-output",
                        retrieved_at=RETRIEVED_AT,
                        upstream_last_modified=LAST_MODIFIED,
                    )

    def test_rejects_source_mutation_while_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            source.write_bytes(_archive_raw())
            real_read = os.read
            changed = False

            def mutating_read(descriptor: int, size: int) -> bytes:
                nonlocal changed
                chunk = real_read(descriptor, size)
                if chunk and not changed:
                    changed = True
                    with source.open("ab") as stream:
                        stream.write(b"x")
                        stream.flush()
                        os.fsync(stream.fileno())
                return chunk

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_factory_snapshot.os.read",
                    side_effect=mutating_read,
                ),
                self.assertRaisesRegex(ValueError, "changed while being copied"),
            ):
                create_taiwan_factory_snapshot(
                    source,
                    root / "output",
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=LAST_MODIFIED,
                )
            self.assertFalse((root / "output").exists())

    def test_rejects_invalid_or_future_last_modified_and_header_controls(self) -> None:
        invalid = (
            "not-a-date",
            "Tue, 21 Jul 2026 03:37:05 GMT",
            "Mon, 20 Jul 2026 03:37:05 GMT\nX-Secret: value",
        )
        for value in invalid:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source.zip"
                source.write_bytes(_archive_raw())
                with self.assertRaises(ValueError):
                    create_taiwan_factory_snapshot(
                        source,
                        root / "output",
                        retrieved_at=RETRIEVED_AT,
                        upstream_last_modified=value,
                    )


if __name__ == "__main__":
    unittest.main()
