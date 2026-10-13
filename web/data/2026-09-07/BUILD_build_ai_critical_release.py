#!/usr/bin/env python3
"""Build the bounded AI-critical manufacturing baseline release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from semiconductor_atlas.ai_critical import (
    _open_real_directory_fd,
    _rename_exclusive,
    ensure_real_directory,
    install_directory_exclusive,
    install_file_exclusive,
    load_baseline,
    validate_release,
    write_deterministic_archive,
    write_release,
)
from web.generate_atlas import generate


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino


def _file_matches(path: Path, expected: dict[str, object]) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    raw = path.read_bytes()
    return (
        expected.get("bytes") == len(raw)
        and expected.get("sha256") == hashlib.sha256(raw).hexdigest()
    )


def _write_transaction_marker(path: Path, payload: dict[str, object]) -> tuple[int, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    identity = _path_identity(path)
    if identity is None:
        raise OSError("publication transaction marker disappeared")
    return identity


def _remove_owned_marker(
    path: Path,
    identity: tuple[int, int],
    parent_identity: tuple[int, int],
    quarantine_parent: Path,
    quarantine_parent_identity: tuple[int, int],
) -> None:
    quarantine = quarantine_parent / (
        f".{path.name}.retired-{secrets.token_hex(16)}"
    )
    _rename_exclusive(
        path,
        quarantine,
        expected_source_parent=parent_identity,
        expected_destination_parent=quarantine_parent_identity,
    )
    _, quarantine_descriptor = _open_real_directory_fd(
        quarantine_parent, "publication marker quarantine", create=False
    )
    try:
        current_parent = os.fstat(quarantine_descriptor)
        if (
            current_parent.st_dev,
            current_parent.st_ino,
        ) != quarantine_parent_identity:
            raise OSError("publication marker quarantine identity changed")
        captured = os.stat(
            quarantine.name,
            dir_fd=quarantine_descriptor,
            follow_symlinks=False,
        )
    finally:
        os.close(quarantine_descriptor)
    if (captured.st_dev, captured.st_ino) == identity:
        return
    try:
        _rename_exclusive(
            quarantine,
            path,
            expected_source_parent=quarantine_parent_identity,
            expected_destination_parent=parent_identity,
        )
    except BaseException as restore_error:
        raise OSError(
            "publication transaction marker identity changed; captured entry "
            f"was preserved at {quarantine}"
        ) from restore_error
    raise OSError("publication transaction marker identity changed")


def build(
    input_path: Path,
    source_root: Path,
    output: Path,
    archive: Path | None,
) -> dict[str, object]:
    if not output.name or output.name in {".", ".."}:
        raise ValueError("release output name is unsafe")
    output = ensure_real_directory(
        output.parent, "release output parent"
    ) / output.name
    publication_parent_identity = _path_identity(output.parent)
    if publication_parent_identity is None:
        raise OSError("release output parent disappeared")
    if output.exists() or output.is_symlink():
        raise ValueError("release output must not already exist")
    if archive is not None:
        if not archive.name or archive.name in {".", ".."}:
            raise ValueError("archive output name is unsafe")
        archive = ensure_real_directory(
            archive.parent, "archive output parent"
        ) / archive.name
        if archive.exists() or archive.is_symlink():
            raise ValueError("archive output must not already exist")
        if archive.parent.resolve() != output.parent.resolve():
            raise ValueError("release and archive outputs must use the same parent directory")

    baseline = load_baseline(input_path, source_root)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.build-", dir=output.parent)
    )
    staged_output = stage_parent / output.name
    staged_archive = stage_parent / archive.name if archive is not None else None
    marker = output.parent / f".{output.name}.publish-transaction.json"
    staged_marker = stage_parent / marker.name
    stage_parent_identity = _path_identity(stage_parent)
    if stage_parent_identity is None:
        shutil.rmtree(stage_parent)
        raise OSError("release stage parent disappeared")
    if marker.exists() or marker.is_symlink():
        shutil.rmtree(stage_parent)
        raise ValueError(
            f"unfinished publication transaction must be resolved first: {marker}"
        )
    marker_identity: tuple[int, int] | None = None
    expected_output_identity: tuple[int, int] | None = None
    expected_archive_identity: tuple[int, int] | None = None
    completed = False
    try:
        write_release(baseline, staged_output)
        generate(staged_output / "atlas.geojson", staged_output / "atlas.html")
        manifest = validate_release(staged_output, require_html=True)
        archive_result = None
        if staged_archive is not None:
            archive_result = write_deterministic_archive(staged_output, staged_archive)

        if output.exists() or output.is_symlink():
            raise ValueError("release output appeared during staged build")
        if archive is not None and (archive.exists() or archive.is_symlink()):
            raise ValueError("archive output appeared during staged build")

        expected_output_identity = _path_identity(staged_output)
        if expected_output_identity is None:
            raise OSError("staged release disappeared before publication")
        if staged_archive is not None:
            expected_archive_identity = _path_identity(staged_archive)
            if expected_archive_identity is None:
                raise OSError("staged archive disappeared before publication")
        if _path_identity(output.parent) != publication_parent_identity:
            raise OSError("release output parent identity changed before publication")
        marker_identity = _write_transaction_marker(
            staged_marker,
            {
                "format": "semiconductor-atlas-publication-transaction-v1",
                "state": "publishing",
                "release_id": manifest["release_id"],
                "release_directory": output.name,
                "archive": archive.name if archive is not None else None,
                "stage_directory": stage_parent.name,
                "manifest_sha256": hashlib.sha256(
                    (staged_output / "manifest.json").read_bytes()
                ).hexdigest(),
                "archive_sha256": (
                    archive_result["sha256"] if archive_result is not None else None
                ),
            },
        )
        install_file_exclusive(
            staged_marker,
            marker,
            expected_source_parent=stage_parent_identity,
            expected_destination_parent=publication_parent_identity,
        )
        install_directory_exclusive(
            staged_output,
            output,
            expected_source_parent=stage_parent_identity,
            expected_destination_parent=publication_parent_identity,
        )
        if archive is not None and staged_archive is not None:
            install_file_exclusive(
                staged_archive,
                archive,
                expected_source_parent=stage_parent_identity,
                expected_destination_parent=publication_parent_identity,
            )

        if _path_identity(output.parent) != publication_parent_identity:
            raise OSError("release output parent identity changed during publication")
        if _path_identity(output) != expected_output_identity:
            raise OSError("published release directory identity changed")
        final_manifest = validate_release(output, require_html=True)
        if final_manifest != manifest:
            raise OSError("published release manifest changed")
        if archive is not None and archive_result is not None:
            if _path_identity(archive) != expected_archive_identity:
                raise OSError("published archive identity changed")
            if not _file_matches(archive, archive_result):
                raise OSError("published archive bytes changed")

        _remove_owned_marker(
            marker,
            marker_identity,
            publication_parent_identity,
            stage_parent,
            stage_parent_identity,
        )
        marker_identity = None
        completed = True

        result: dict[str, object] = {
            "output": str(output.resolve()),
            "manifest": final_manifest,
        }
        if archive is not None and archive_result is not None:
            result["archive"] = {
                **archive_result,
                "path": str(archive),
            }
        return result
    except BaseException:
        # Never roll publication paths back by name: another actor may have
        # replaced them. The marker and owned stage are recovery evidence for
        # any commit-then-failure or split-pair state.
        no_commit = (
            staged_output.exists()
            and (staged_archive is None or staged_archive.exists())
            and _path_identity(output) != expected_output_identity
            and (
                archive is None
                or _path_identity(archive) != expected_archive_identity
            )
        )
        if no_commit and marker_identity is not None:
            if _path_identity(marker) == marker_identity:
                _remove_owned_marker(
                    marker,
                    marker_identity,
                    publication_parent_identity,
                    stage_parent,
                    stage_parent_identity,
                )
            marker_identity = None
        raise
    finally:
        if stage_parent.exists() and (completed or marker_identity is None):
            shutil.rmtree(stage_parent)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate AI-Critical Manufacturing Baseline v1"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        result = build(args.input, args.source_root, args.output, args.archive)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
