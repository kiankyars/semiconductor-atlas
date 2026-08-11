from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.moenv_ems import MOENV_FIELDS
from semiconductor_atlas.moenv_snapshot import (
    MOENV_CANDIDATE_FILENAME,
    MOENV_DATASET_UPDATED_AT_BASES,
    MOENV_FULL_PACKAGE_URL,
    MOENV_SCOPE,
    create_moenv_snapshot,
    verify_moenv_snapshot,
)


JSON_MEMBER = "環境保護許可管理系統(暨解除列管)對象基本資料.json"
RETRIEVED_AT = "2026-07-20T06:00:00Z"
DATASET_UPDATED_AT = "2026-07-19T23:15:13Z"
DATASET_UPDATED_AT_BASIS = "official_dataset_page_displayed_asia_taipei"
REQUEST_BODY = {
    "rid": ["56ea8602-c7d5-4c27-ac20-236e51e889c4"],
    "download_type": "json",
    "pid": "816037bc-53f1-4951-b32d-8e607b948344",
}


def _row(
    emsno: str,
    industry_id: str,
    *,
    industry_group: str = "261",
    regulated: bool = True,
    coordinates: bool = True,
) -> dict[str, object]:
    labels = {
        "2611": "積體電路製造業",
        "2612": "分離式元件製造業",
        "2613": "半導體封裝及測試業",
        "2630": "印刷電路板製造業",
    }
    row: dict[str, object] = {field: "" for field in MOENV_FIELDS}
    row.update(
        {
            "emsno": emsno,
            "facilityname": f"測試半導體股份有限公司 {emsno}",
            "uniformno": "00123456",
            "county": "新竹市",
            "township": "東區",
            "facilityaddress": "新竹市東區測試路1號",
            "industryareaname": "新竹科學園區",
            "industryid": industry_id,
            "industryname": labels[industry_id],
            "twd97tm2x": "250000.125" if coordinates else "",
            "twd97tm2y": "2740000.500" if coordinates else "",
            "wgs84lon": "121.012300" if coordinates else "",
            "wgs84lat": "24.800400" if coordinates else "",
            "isair": "1" if regulated else "0",
            "iswater": "0",
            "iswaste": "0",
            "istoxic": "0",
            "issoil": "0",
            "airreleasedate": "",
            "waterreleasedate": "",
            "wastereleasedate": "",
            "toxicreleasedate": "",
            "soilreleasedate": "",
            "industrygroup": industry_group,
            "admino": None,
            "facno": None,
        }
    )
    return row


def _archive_raw(rows: list[dict[str, object]] | None = None) -> bytes:
    source_rows = rows or [
        _row("p5806269", "2611"),
        _row("p5806270", "2613", regulated=False, coordinates=False),
        _row("p5806271", "2630", industry_group="263"),
    ]
    json_raw = json.dumps(
        source_rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    publisher_md5 = hashlib.md5(json_raw).hexdigest()  # noqa: S324 - upstream format
    hash_raw = f"{JSON_MEMBER}：{publisher_md5}，演算法：MD5\n".encode("utf-8")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hash.txt", hash_raw)
        archive.writestr(JSON_MEMBER, json_raw)
    stream.seek(0)
    return stream.read()


def _create(root: Path, *, name: str = "snapshot", archive_raw: bytes | None = None):
    archive_path = root / f"{name}.zip"
    archive_path.write_bytes(archive_raw or _archive_raw())
    return create_moenv_snapshot(
        archive_path,
        root / name,
        retrieved_at=RETRIEVED_AT,
        dataset_updated_at=DATASET_UPDATED_AT,
        dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
        download_url=MOENV_FULL_PACKAGE_URL,
        acquisition_request_body=copy.deepcopy(REQUEST_BODY),
        upstream_etag='"fixture"',
        upstream_last_modified="Sun, 19 Jul 2026 23:15:13 GMT",
    )


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MOENVSnapshotTests(unittest.TestCase):
    def test_creates_closed_snapshot_and_replays_raw_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            replay = verify_moenv_snapshot(snapshot.root)

            self.assertEqual(snapshot.manifest_bytes, replay.manifest_bytes)
            self.assertEqual(snapshot.raw_bytes, replay.raw_bytes)
            self.assertEqual(snapshot.candidate_bytes, replay.candidate_bytes)
            self.assertEqual(DATASET_UPDATED_AT, replay.dataset_updated_at)
            self.assertEqual(DATASET_UPDATED_AT_BASIS, replay.dataset_updated_at_basis)
            self.assertEqual("POST", replay.acquisition.method)
            self.assertEqual(MOENV_FULL_PACKAGE_URL, replay.acquisition.url)
            self.assertEqual(
                json.dumps(
                    REQUEST_BODY,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
                replay.acquisition.canonical_request_body,
            )
            self.assertEqual(3, replay.row_count)
            self.assertEqual(2, replay.candidate_count)
            self.assertEqual(2, replay.facility_count)
            self.assertEqual((('2611', 1), ('2612', 0), ('2613', 1)), replay.code_counts)
            self.assertEqual(1, replay.current_regulation_count)
            self.assertEqual(1, replay.valid_coordinate_count)
            self.assertEqual(0, replay.conflicting_facility_count)
            self.assertIn(b'"admino":null', replay.candidate_bytes)
            self.assertIn(b'"facno":null', replay.candidate_bytes)
            self.assertEqual(
                {
                    "manifest.json",
                    MOENV_CANDIDATE_FILENAME,
                    replay.raw_path,
                },
                set(_tree_bytes(replay.root)),
            )

            scope = _manifest(replay.root)["source_scopes"][MOENV_SCOPE]
            self.assertEqual("POST", scope["acquisition"]["method"])
            self.assertEqual(REQUEST_BODY, scope["acquisition"]["request_body"])
            self.assertFalse(scope["acquisition"]["credential_retained"])
            self.assertTrue(
                scope["completeness"]["source_assertion_interval_closure"]
                ["allowed_after_verified_full_same_filter_refresh"]
            )
            self.assertFalse(
                scope["completeness"]
                ["real_world_facility_closure_or_inactivity_from_absence"]
            )

    def test_fixed_inputs_produce_byte_identical_snapshot_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = _archive_raw()
            first = _create(root, name="first", archive_raw=raw)
            second = _create(root, name="second", archive_raw=raw)
            self.assertEqual(_tree_bytes(first.root), _tree_bytes(second.root))

    def test_verifier_rescans_retained_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            from semiconductor_atlas import moenv_snapshot as module

            with mock.patch.object(
                module,
                "scan_moenv_archive",
                wraps=module.scan_moenv_archive,
            ) as scanner:
                verified = verify_moenv_snapshot(snapshot.root)
            self.assertEqual(snapshot.raw_sha256, verified.scan.archive_sha256)
            scanner.assert_called_once_with(snapshot.root / snapshot.raw_path)

    def test_rejects_candidate_and_raw_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="candidate")
            (snapshot.root / MOENV_CANDIDATE_FILENAME).write_bytes(
                snapshot.candidate_bytes + b"\n"
            )
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                verify_moenv_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="raw")
            raw_path = snapshot.root / snapshot.raw_path
            raw = bytearray(raw_path.read_bytes())
            raw[-1] ^= 1
            raw_path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_moenv_snapshot(snapshot.root)

    def test_rejects_hash_consistent_derivative_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            manifest = _manifest(snapshot.root)
            changed = b'{"invented":true}\n'
            (snapshot.root / MOENV_CANDIDATE_FILENAME).write_bytes(changed)
            entry = manifest["inputs"][0]
            entry["bytes"] = len(changed)
            entry["sha256"] = hashlib.sha256(changed).hexdigest()
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "does not replay"):
                verify_moenv_snapshot(snapshot.root)

    def test_rejects_manifest_lies_after_raw_rescan(self) -> None:
        mutations = (
            (
                "summary",
                lambda scope: scope["summary"].__setitem__("facility_count", 999),
            ),
            (
                "member checksum",
                lambda scope: scope["upstream_member"].__setitem__(
                    "publisher_md5", "0" * 32
                ),
            ),
            (
                "member crc",
                lambda scope: scope["upstream_member"].__setitem__("crc32", 0),
            ),
            (
                "checksum member",
                lambda scope: scope["publisher_checksum_member"].__setitem__(
                    "sha256", "0" * 64
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                snapshot = _create(Path(temporary))
                manifest = _manifest(snapshot.root)
                mutate(manifest["source_scopes"][MOENV_SCOPE])
                _write_manifest(snapshot.root, manifest)
                with self.assertRaisesRegex(ValueError, "rescan"):
                    verify_moenv_snapshot(snapshot.root)

    def test_rejects_secret_query_keys_and_unapproved_origins(self) -> None:
        invalid_urls = (
            MOENV_FULL_PACKAGE_URL + "?api_key=do-not-store",
            "https://example.com/api/frontstage/dataset/resource.download",
            "https://data.moenv.gov.tw/api/v2/ems_s_01",
        )
        for url in invalid_urls:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = root / "source.zip"
                archive_path.write_bytes(_archive_raw())
                with self.assertRaises(ValueError):
                    create_moenv_snapshot(
                        archive_path,
                        root / "snapshot",
                        retrieved_at=RETRIEVED_AT,
                        dataset_updated_at=DATASET_UPDATED_AT,
                        dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                        download_url=url,
                        acquisition_request_body=copy.deepcopy(REQUEST_BODY),
                    )
                self.assertFalse((root / "snapshot").exists())

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            manifest = _manifest(snapshot.root)
            manifest["source_scopes"][MOENV_SCOPE]["acquisition"]["url"] = (
                MOENV_FULL_PACKAGE_URL + "?service_key=do-not-store"
            )
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "secret-like query key"):
                verify_moenv_snapshot(snapshot.root)

    def test_rejects_acquisition_body_drift_and_credential_fields(self) -> None:
        invalid_bodies = (
            {**REQUEST_BODY, "api_key": "secret"},
            {**REQUEST_BODY, "download_type": "csv"},
            {**REQUEST_BODY, "rid": []},
            {
                **REQUEST_BODY,
                "rid": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            },
            {
                **REQUEST_BODY,
                "pid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            },
            {**REQUEST_BODY, "pid": "not-a-uuid"},
        )
        for body in invalid_bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = root / "source.zip"
                archive_path.write_bytes(_archive_raw())
                with self.assertRaises(ValueError):
                    create_moenv_snapshot(
                        archive_path,
                        root / "snapshot",
                        retrieved_at=RETRIEVED_AT,
                        dataset_updated_at=DATASET_UPDATED_AT,
                        dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                        download_url=MOENV_FULL_PACKAGE_URL,
                        acquisition_request_body=body,
                    )

    def test_rejects_extra_missing_and_symlink_tree_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="extra")
            (snapshot.root / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_moenv_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="missing")
            (snapshot.root / MOENV_CANDIDATE_FILENAME).unlink()
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_moenv_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary), name="symlink")
            raw_path = snapshot.root / snapshot.raw_path
            actual = snapshot.root / "actual.zip"
            raw_path.replace(actual)
            raw_path.symlink_to(actual)
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify_moenv_snapshot(snapshot.root)

    def test_creation_failure_is_atomic_and_cleans_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "source.zip"
            archive_path.write_bytes(_archive_raw())
            output = root / "never-installed"
            with (
                mock.patch(
                    "semiconductor_atlas.moenv_snapshot.canonical_candidate_jsonl_bytes",
                    side_effect=ValueError("injected transform failure"),
                ),
                self.assertRaisesRegex(ValueError, "injected transform failure"),
            ):
                create_moenv_snapshot(
                    archive_path,
                    output,
                    retrieved_at=RETRIEVED_AT,
                    dataset_updated_at=DATASET_UPDATED_AT,
                    dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                    download_url=MOENV_FULL_PACKAGE_URL,
                    acquisition_request_body=copy.deepcopy(REQUEST_BODY),
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".never-installed.stage-*")))

    def test_refuses_overwrite_and_unsafe_archive_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = _create(root)
            source = root / "source-again.zip"
            source.write_bytes(_archive_raw())
            with self.assertRaises(FileExistsError):
                create_moenv_snapshot(
                    source,
                    snapshot.root,
                    retrieved_at=RETRIEVED_AT,
                    dataset_updated_at=DATASET_UPDATED_AT,
                    dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                    download_url=MOENV_FULL_PACKAGE_URL,
                    acquisition_request_body=copy.deepcopy(REQUEST_BODY),
                )

            linked = root / "linked.zip"
            linked.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                create_moenv_snapshot(
                    linked,
                    root / "linked-output",
                    retrieved_at=RETRIEVED_AT,
                    dataset_updated_at=DATASET_UPDATED_AT,
                    dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                    download_url=MOENV_FULL_PACKAGE_URL,
                    acquisition_request_body=copy.deepcopy(REQUEST_BODY),
                )

    def test_rejects_source_time_lies_and_manifest_noncanonicality(self) -> None:
        self.assertEqual(
            {DATASET_UPDATED_AT_BASIS}, set(MOENV_DATASET_UPDATED_AT_BASES)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "source.zip"
            archive_path.write_bytes(_archive_raw())
            with self.assertRaisesRegex(ValueError, "later than retrieved"):
                create_moenv_snapshot(
                    archive_path,
                    root / "snapshot",
                    retrieved_at=RETRIEVED_AT,
                    dataset_updated_at="2026-07-20T06:00:01Z",
                    dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
                    download_url=MOENV_FULL_PACKAGE_URL,
                    acquisition_request_body=copy.deepcopy(REQUEST_BODY),
                )

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = _create(Path(temporary))
            manifest = _manifest(snapshot.root)
            (snapshot.root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "not canonical"):
                verify_moenv_snapshot(snapshot.root)


if __name__ == "__main__":
    unittest.main()
