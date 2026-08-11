from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.taiwan_mof_tax_registry import (
    TAIWAN_MOF_ARCHIVE_URL,
    TAIWAN_MOF_DATASET_URL,
    TAIWAN_MOF_LICENSE_URL,
)
from semiconductor_atlas.moenv_snapshot import verify_moenv_snapshot
from semiconductor_atlas.taiwan_factory_snapshot import verify_taiwan_factory_snapshot
from semiconductor_atlas.taiwan_mof_snapshot import (
    TAIWAN_MOF_ALLOWLIST_FILENAME,
    TAIWAN_MOF_MATCHED_FILENAME,
    TAIWAN_MOF_SCOPE,
    build_taiwan_mof_allowlist,
    create_taiwan_mof_snapshot,
    verify_taiwan_mof_allowlist_sources,
    verify_taiwan_mof_snapshot,
)

from tests._taiwan_mof_fixtures import (
    MOF_LAST_MODIFIED,
    RETRIEVED_AT,
    create_source_snapshots,
    mof_archive_raw,
)


def _create(root: Path, *, name: str = "mof-snapshot"):
    moenv, factory = create_source_snapshots(root)
    archive = root / f"{name}.zip"
    raw = mof_archive_raw()
    archive.write_bytes(raw)
    snapshot = create_taiwan_mof_snapshot(
        archive,
        root / name,
        moenv_snapshot_dir=moenv,
        factory_snapshot_dir=factory,
        retrieved_at=RETRIEVED_AT,
        upstream_last_modified=MOF_LAST_MODIFIED,
        upstream_etag='W/"fixture"',
        upstream_content_type="application/zip",
        upstream_content_length=len(raw),
    )
    return snapshot, moenv, factory


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
    ).encode()
    (root / "manifest.json").write_bytes(raw)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TaiwanMOFSnapshotTests(unittest.TestCase):
    def test_builds_source_bound_allowlist_with_exclusion_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv_root, factory_root = create_source_snapshots(root)
            allowlist = build_taiwan_mof_allowlist(moenv_root, factory_root)
            moenv = verify_moenv_snapshot(moenv_root)
            factory = verify_taiwan_factory_snapshot(factory_root)

            self.assertEqual(("00123456", "00888888", "00999999"), allowlist.values)
            self.assertEqual(moenv.manifest_sha256, allowlist.moenv.manifest_sha256)
            self.assertEqual(factory.manifest_sha256, allowlist.factory.manifest_sha256)
            self.assertEqual(1, allowlist.blank_source_value_count)
            self.assertEqual("1234567", allowlist.malformed_values[0].value)
            self.assertEqual(
                ("00999999", "1234567"), allowlist.conflicting_records[0].values
            )
            self.assertEqual(2, len(allowlist.conflicting_values))
            self.assertIn(b"00999999\n", allowlist.raw_bytes)

    def test_creates_closed_snapshot_with_three_distinct_source_clocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, moenv, factory = _create(Path(temporary))
            verified = verify_taiwan_mof_allowlist_sources(
                snapshot.root, moenv, factory
            )
            self.assertEqual("2026-07-20T06:57:50Z", verified.retrieved_at)
            self.assertEqual("2026-07-19T21:12:27Z", verified.source_updated_at)
            self.assertEqual("20-JUL-26", verified.publisher_date_raw)
            self.assertEqual("2026-07-20", verified.publisher_date)
            self.assertEqual(4, verified.row_count)
            self.assertEqual(3, verified.business_row_count)
            self.assertEqual(3, verified.allowlist_count)
            self.assertEqual(2, verified.matched_count)
            self.assertEqual(1, verified.missing_count)
            self.assertEqual(("00999999",), verified.missing_business_numbers)
            self.assertEqual(f"raw/sha256/{verified.raw_sha256}.zip", verified.raw_path)

            manifest = json.loads(verified.manifest_bytes)
            scope = manifest["source_scopes"][TAIWAN_MOF_SCOPE]
            self.assertEqual(TAIWAN_MOF_DATASET_URL, scope["dataset_url"])
            self.assertEqual(TAIWAN_MOF_ARCHIVE_URL, scope["archive_url"])
            self.assertEqual(TAIWAN_MOF_LICENSE_URL, scope["rights"]["license_url"])
            self.assertEqual(3, scope["allowlist_derivation"]["output"]["record_count"])
            self.assertFalse(
                scope["privacy"]["full_active_tax_roster_derivative_created"]
            )
            self.assertIn("資本額", scope["privacy"]["omitted_source_fields"])
            derivative = verified.matched_bytes.decode()
            self.assertNotIn("00777777", derivative)
            self.assertNotIn("capital", derivative)

    def test_fixed_inputs_produce_byte_identical_snapshot_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            raw = mof_archive_raw()
            archive = root / "source.zip"
            archive.write_bytes(raw)
            roots: list[Path] = []
            for name in ("first", "second"):
                snapshot = create_taiwan_mof_snapshot(
                    archive,
                    root / name,
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                    upstream_content_type="application/zip",
                    upstream_content_length=len(raw),
                )
                roots.append(snapshot.root)
            self.assertEqual(_tree_bytes(roots[0]), _tree_bytes(roots[1]))

    def test_rejects_raw_allowlist_derivative_and_replay_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="raw-tamper")
            raw_path = snapshot.root / snapshot.raw_path
            raw_path.write_bytes(raw_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "archive hash or size mismatch"):
                verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="allowlist-tamper")
            allowlist = snapshot.root / TAIWAN_MOF_ALLOWLIST_FILENAME
            allowlist.write_bytes(
                allowlist.read_bytes().replace(b"00999999", b"00666666")
            )
            with self.assertRaisesRegex(ValueError, "allowlist hash or size mismatch"):
                verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="derivative-tamper")
            matched = snapshot.root / TAIWAN_MOF_MATCHED_FILENAME
            matched.write_bytes(
                matched.read_bytes().replace("允".encode(), "錯".encode())
            )
            with self.assertRaisesRegex(ValueError, "derivative hash or size mismatch"):
                verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="replay-tamper")
            matched = snapshot.root / TAIWAN_MOF_MATCHED_FILENAME
            raw = matched.read_bytes().replace("允".encode(), "錯".encode())
            matched.write_bytes(raw)
            manifest = _manifest(snapshot.root)
            entry = manifest["inputs"][1]
            entry["bytes"] = len(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            _write_manifest(snapshot.root, manifest)
            with self.assertRaisesRegex(ValueError, "does not replay"):
                verify_taiwan_mof_snapshot(snapshot.root)

    def test_rejects_manifest_count_url_license_and_source_binding_lies(self) -> None:
        mutations = (
            lambda scope: scope["summary"].__setitem__("matched_count", 3),
            lambda scope: scope.__setitem__(
                "archive_url", TAIWAN_MOF_ARCHIVE_URL + "?token=secret"
            ),
            lambda scope: scope["rights"].__setitem__(
                "license_url", "https://example.com/license"
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                snapshot, _, _ = _create(Path(temporary), name=f"lie-{index}")
                manifest = _manifest(snapshot.root)
                mutate(manifest["source_scopes"][TAIWAN_MOF_SCOPE])
                _write_manifest(snapshot.root, manifest)
                with self.assertRaises(ValueError):
                    verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot, moenv, factory = _create(Path(temporary), name="binding-lie")
            manifest = _manifest(snapshot.root)
            binding = manifest["source_scopes"][TAIWAN_MOF_SCOPE][
                "allowlist_derivation"
            ]["sources"]["moenv"]
            binding["manifest_sha256"] = "0" * 64
            _write_manifest(snapshot.root, manifest)
            # The self-contained verifier can validate the binding's shape, but the
            # source-aware verifier must reproduce and compare the actual hashes.
            verify_taiwan_mof_snapshot(snapshot.root)
            with self.assertRaisesRegex(ValueError, "bindings or diagnostics"):
                verify_taiwan_mof_allowlist_sources(snapshot.root, moenv, factory)

    def test_rejects_extra_missing_symlink_fifo_and_symlink_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="extra")
            (snapshot.root / "extra").write_text("unexpected")
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="missing")
            (snapshot.root / TAIWAN_MOF_MATCHED_FILENAME).unlink()
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = _create(root, name="symlink")
            raw_path = snapshot.root / snapshot.raw_path
            target = root / "actual.zip"
            raw_path.replace(target)
            raw_path.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify_taiwan_mof_snapshot(snapshot.root)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as temporary:
                snapshot, _, _ = _create(Path(temporary), name="fifo")
                os.mkfifo(snapshot.root / "unexpected.fifo")
                with self.assertRaisesRegex(ValueError, "regular file or directory"):
                    verify_taiwan_mof_snapshot(snapshot.root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, _, _ = _create(root, name="root-target")
            linked = root / "linked-root"
            linked.symlink_to(snapshot.root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "non-symlink directory"):
                verify_taiwan_mof_snapshot(linked)

    def test_reopens_files_and_detects_cross_interval_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, _, _ = _create(Path(temporary), name="mutated")
            module = __import__(
                "semiconductor_atlas.taiwan_mof_snapshot",
                fromlist=["_SnapshotReader"],
            )
            original_read = module._SnapshotReader.read
            changed = False

            def mutating_read(reader, relative_path, *, maximum_bytes):
                nonlocal changed
                raw = original_read(reader, relative_path, maximum_bytes=maximum_bytes)
                if relative_path == TAIWAN_MOF_MATCHED_FILENAME and not changed:
                    changed = True
                    path = snapshot.root / TAIWAN_MOF_MATCHED_FILENAME
                    path.write_bytes(raw.replace("允".encode(), "錯".encode()))
                return raw

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot._SnapshotReader.read",
                    new=mutating_read,
                ),
                self.assertRaisesRegex(ValueError, "changed during verification"),
            ):
                verify_taiwan_mof_snapshot(snapshot.root)

    def test_source_aware_verifier_rechecks_target_after_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, moenv, factory = _create(Path(temporary), name="target-race")
            module = __import__(
                "semiconductor_atlas.taiwan_mof_snapshot",
                fromlist=["build_taiwan_mof_allowlist"],
            )
            original_build = module.build_taiwan_mof_allowlist
            changed = False

            def mutating_build(*args, **kwargs):
                nonlocal changed
                rebuilt = original_build(*args, **kwargs)
                matched = snapshot.root / TAIWAN_MOF_MATCHED_FILENAME
                matched.write_bytes(
                    matched.read_bytes().replace("允".encode(), "錯".encode())
                )
                changed = True
                return rebuilt

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot.build_taiwan_mof_allowlist",
                    new=mutating_build,
                ),
                self.assertRaisesRegex(ValueError, "derivative hash or size mismatch"),
            ):
                verify_taiwan_mof_allowlist_sources(snapshot.root, moenv, factory)
            self.assertTrue(changed)

    def test_allowlist_builder_detects_source_snapshot_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            original = verify_moenv_snapshot(moenv)
            changed = dataclasses.replace(original, candidate_sha256="0" * 64)
            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot.verify_moenv_snapshot",
                    side_effect=[original, changed],
                ),
                self.assertRaisesRegex(ValueError, "changed while deriving"),
            ):
                build_taiwan_mof_allowlist(moenv, factory)

    def test_creation_is_atomic_no_replace_and_rejects_unsafe_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            source = root / "source.zip"
            source.write_bytes(mof_archive_raw())
            output = root / "never-installed"
            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot.canonical_matched_jsonl_bytes",
                    side_effect=ValueError("injected transform failure"),
                ),
                self.assertRaisesRegex(ValueError, "injected transform failure"),
            ):
                create_taiwan_mof_snapshot(
                    source,
                    output,
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".never-installed.stage-*")))

            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                create_taiwan_mof_snapshot(
                    source,
                    existing,
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )

            linked = root / "linked.zip"
            linked.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "regular file"):
                create_taiwan_mof_snapshot(
                    linked,
                    root / "linked-output",
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )

            if hasattr(os, "mkfifo"):
                fifo = root / "source.fifo"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(ValueError, "regular file"):
                    create_taiwan_mof_snapshot(
                        fifo,
                        root / "fifo-output",
                        moenv_snapshot_dir=moenv,
                        factory_snapshot_dir=factory,
                        retrieved_at=RETRIEVED_AT,
                        upstream_last_modified=MOF_LAST_MODIFIED,
                    )

    def test_source_aware_verification_finishes_before_atomic_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            source = root / "source.zip"
            source.write_bytes(mof_archive_raw())
            output = root / "output"
            checked_roots: list[Path] = []

            def fail_source_verification(candidate, *_args):
                checked_roots.append(Path(candidate))
                raise ValueError("injected source-aware verification failure")

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot.verify_taiwan_mof_allowlist_sources",
                    side_effect=fail_source_verification,
                ),
                self.assertRaisesRegex(
                    ValueError, "injected source-aware verification failure"
                ),
            ):
                create_taiwan_mof_snapshot(
                    source,
                    output,
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )
            self.assertEqual(1, len(checked_roots))
            self.assertNotEqual(output, checked_roots[0])
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_post_install_identity_failure_rolls_back_new_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            source = root / "source.zip"
            source.write_bytes(mof_archive_raw())
            output = root / "output"
            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot._assert_installed_tree_identity",
                    side_effect=ValueError("injected post-install identity failure"),
                ),
                self.assertRaisesRegex(
                    ValueError, "injected post-install identity failure"
                ),
            ):
                create_taiwan_mof_snapshot(
                    source,
                    output,
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".output.stage-*")))

    def test_rejects_archive_mutation_while_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            moenv, factory = create_source_snapshots(root)
            source = root / "source.zip"
            source.write_bytes(mof_archive_raw())
            source_stat = source.stat()
            source_identity = (source_stat.st_dev, source_stat.st_ino)
            real_read = os.read
            changed = False

            def mutating_read(descriptor: int, size: int) -> bytes:
                nonlocal changed
                chunk = real_read(descriptor, size)
                descriptor_stat = os.fstat(descriptor)
                descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
                if chunk and not changed and descriptor_identity == source_identity:
                    changed = True
                    with source.open("ab") as stream:
                        stream.write(b"x")
                        stream.flush()
                        os.fsync(stream.fileno())
                return chunk

            with (
                mock.patch(
                    "semiconductor_atlas.taiwan_mof_snapshot.os.read",
                    side_effect=mutating_read,
                ),
                self.assertRaisesRegex(ValueError, "changed while being copied"),
            ):
                create_taiwan_mof_snapshot(
                    source,
                    root / "output",
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=RETRIEVED_AT,
                    upstream_last_modified=MOF_LAST_MODIFIED,
                )
            self.assertTrue(changed)
            self.assertFalse((root / "output").exists())

    def test_rejects_future_or_unsafe_last_modified(self) -> None:
        invalid = (
            "not-a-date",
            "Tue, 21 Jul 2026 03:37:05 GMT",
            "Sun, 19 Jul 2026 21:12:27 GMT\nX-Secret: value",
        )
        for value in invalid:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                moenv, factory = create_source_snapshots(root)
                source = root / "source.zip"
                source.write_bytes(mof_archive_raw())
                with self.assertRaises(ValueError):
                    create_taiwan_mof_snapshot(
                        source,
                        root / "output",
                        moenv_snapshot_dir=moenv,
                        factory_snapshot_dir=factory,
                        retrieved_at=RETRIEVED_AT,
                        upstream_last_modified=value,
                    )

    def test_publisher_date_is_bound_to_source_and_taiwan_local_clocks(self) -> None:
        accepted = (
            (
                "20-JUL-26",
                "Mon, 20 Jul 2026 15:59:59 GMT",
                "2026-07-20T16:00:00Z",
            ),
            (
                "21-JUL-26",
                "Mon, 20 Jul 2026 16:00:00 GMT",
                "2026-07-20T16:00:01Z",
            ),
            ("19-JUL-26", MOF_LAST_MODIFIED, RETRIEVED_AT),
        )
        rejected = (
            (
                "21-JUL-26",
                "Mon, 20 Jul 2026 15:59:59 GMT",
                "2026-07-20T16:00:00Z",
            ),
            ("18-JUL-26", MOF_LAST_MODIFIED, RETRIEVED_AT),
            ("31-DEC-99", MOF_LAST_MODIFIED, RETRIEVED_AT),
        )
        for index, (publisher_date, last_modified, retrieved_at) in enumerate(accepted):
            with (
                self.subTest(accepted=publisher_date),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                moenv, factory = create_source_snapshots(root)
                source = root / "source.zip"
                source.write_bytes(mof_archive_raw(publisher_date=publisher_date))
                snapshot = create_taiwan_mof_snapshot(
                    source,
                    root / f"accepted-{index}",
                    moenv_snapshot_dir=moenv,
                    factory_snapshot_dir=factory,
                    retrieved_at=retrieved_at,
                    upstream_last_modified=last_modified,
                )
                self.assertEqual(
                    datetime.strptime(publisher_date, "%d-%b-%y")
                    .date()
                    .replace(year=2000 + int(publisher_date[-2:]))
                    .isoformat(),
                    snapshot.publisher_date,
                )

        for index, (publisher_date, last_modified, retrieved_at) in enumerate(rejected):
            with (
                self.subTest(rejected=publisher_date),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                moenv, factory = create_source_snapshots(root)
                source = root / "source.zip"
                source.write_bytes(mof_archive_raw(publisher_date=publisher_date))
                output = root / f"rejected-{index}"
                with self.assertRaisesRegex(ValueError, "publisher CSV date"):
                    create_taiwan_mof_snapshot(
                        source,
                        output,
                        moenv_snapshot_dir=moenv,
                        factory_snapshot_dir=factory,
                        retrieved_at=retrieved_at,
                        upstream_last_modified=last_modified,
                    )
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
