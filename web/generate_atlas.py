#!/usr/bin/env python3
"""Embed an exported atlas GeoJSON file in a standalone HTML interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


TEMPLATE_PATH = Path(__file__).with_name("atlas-template.html")
PLACEHOLDER = "__SEMICONDUCTOR_ATLAS_DATA__"
RELEASE_FORMAT = "semiconductor-atlas-release-v1"
STAGE_OWNER_SUFFIX = ".owner"
TRANSACTION_FORMAT = "semiconductor-atlas-web-transaction-v1"


def load_geojson(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ValueError("input must be a GeoJSON FeatureCollection")
    if not isinstance(data.get("features"), list):
        raise ValueError("FeatureCollection.features must be an array")
    for index, feature in enumerate(data["features"]):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"features[{index}] must be a GeoJSON Feature")
        if not isinstance(feature.get("properties"), dict):
            raise ValueError(f"features[{index}].properties must be an object")
    return data


def safe_json(data: dict[str, Any]) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render(data: dict[str, Any], template: str) -> str:
    if template.count(PLACEHOLDER) != 1:
        raise ValueError("template must contain exactly one atlas data placeholder")
    return template.replace(PLACEHOLDER, safe_json(data))


def _validate_output_path(input_path: str | Path, output_path: str | Path) -> tuple[Path, Path]:
    source = Path(input_path)
    output = Path(output_path)
    resolved_output = output.resolve()
    collisions = {
        source.resolve(): "input GeoJSON",
        TEMPLATE_PATH.resolve(): "HTML template",
        (output.parent / "manifest.json").resolve(): "release manifest",
    }
    if resolved_output in collisions:
        raise ValueError(f"output path conflicts with {collisions[resolved_output]}")
    if output.suffix.casefold() != ".html":
        raise ValueError("output path must use an .html filename")
    if source.resolve().parent != output.resolve().parent:
        raise ValueError("input GeoJSON and output HTML must be in the same release directory")
    return source, output


def _verified_release_manifest(source: Path, output: Path) -> tuple[Path, dict[str, Any]]:
    if source.is_symlink() or not source.is_file():
        raise ValueError("input GeoJSON must be a regular release file")
    if source.name != "atlas.geojson":
        raise ValueError("input must be the release atlas.geojson file")
    manifest_path = output.parent / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("release manifest is missing or is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("release manifest is missing or unreadable") from error
    files = manifest.get("files")
    if manifest.get("format") != RELEASE_FORMAT or not isinstance(files, dict):
        raise ValueError("release manifest has an unsupported format")
    source_metadata = files.get(source.name)
    if not isinstance(source_metadata, dict):
        raise ValueError("release manifest does not track atlas.geojson")
    source_raw = source.read_bytes()
    if (
        source_metadata.get("bytes") != len(source_raw)
        or source_metadata.get("sha256") != hashlib.sha256(source_raw).hexdigest()
    ):
        raise ValueError("atlas.geojson does not match the release manifest")

    output_metadata = files.get(output.name)
    if output_metadata is not None:
        recognized = (
            isinstance(output_metadata, dict)
            and (
                output_metadata.get("role") == "standalone_atlas_html"
                or output.name == "atlas.html"
            )
        )
        if not recognized:
            raise ValueError(f"output conflicts with a managed release file: {output.name}")
        if output.is_symlink() or not output.is_file():
            raise ValueError("managed atlas HTML output is missing or is not a regular file")
        current = output.read_bytes()
        if (
            output_metadata.get("bytes") != len(current)
            or output_metadata.get("sha256") != hashlib.sha256(current).hexdigest()
        ):
            raise ValueError("managed atlas HTML differs from the release manifest")
    elif output.exists() or output.is_symlink():
        raise ValueError("refusing to overwrite an unmanaged HTML file")
    return manifest_path, manifest


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _write_durable(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    with path.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            directories.append(path)
            continue
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    for path in reversed(directories):
        _fsync_directory(path)


def _backup_path(release_directory: Path) -> Path:
    return release_directory.parent / (
        f".{release_directory.name}.semiconductor-atlas-web-previous"
    )


def _transaction_path(release_directory: Path) -> Path:
    return release_directory.parent / (
        f".{release_directory.name}.semiconductor-atlas-web-transaction.json"
    )


def _stage_prefix(release_directory: Path) -> str:
    return f".{release_directory.name}.semiconductor-atlas-web-stage-"


def _stage_owner_path(stage: Path) -> Path:
    return stage.with_name(stage.name + STAGE_OWNER_SUFFIX)


def _rename_path(source: Path, destination: Path) -> None:
    source.rename(destination)


def _remove_owned_stages(release_directory: Path) -> None:
    owner = str(release_directory.absolute())
    owner_pattern = _stage_prefix(release_directory) + "*" + STAGE_OWNER_SUFFIX
    for marker in release_directory.parent.glob(owner_pattern):
        if marker.is_symlink() or not marker.is_file():
            continue
        try:
            marker_owner = marker.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if marker_owner != owner:
            continue
        stage_name = marker.name[: -len(STAGE_OWNER_SUFFIX)]
        candidate = marker.with_name(stage_name)
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError(f"web recovery stage is not a directory: {candidate}")
            shutil.rmtree(candidate)
        marker.unlink()


def _verify_managed_bundle(release_directory: Path) -> None:
    manifest_path = release_directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("release manifest is missing or is not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("release manifest is missing or unreadable") from error
    files = manifest.get("files")
    if manifest.get("format") != RELEASE_FORMAT or not isinstance(files, dict):
        raise ValueError("release manifest has an unsupported format")
    for name, metadata in files.items():
        if (
            not isinstance(name, str)
            or name in {".", "..", "manifest.json"}
            or Path(name).name != name
            or not isinstance(metadata, dict)
        ):
            raise ValueError("release manifest contains an invalid managed file entry")
        expected_bytes = metadata.get("bytes")
        expected_sha256 = metadata.get("sha256")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ValueError(f"release manifest has invalid metadata for {name}")
        managed_path = release_directory / name
        if managed_path.is_symlink() or not managed_path.is_file():
            raise ValueError(f"managed release file is missing or unsafe: {name}")
        raw = managed_path.read_bytes()
        if (
            len(raw) != expected_bytes
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError(f"managed release file does not match manifest: {name}")


def _manifest_sha256(release_directory: Path) -> str:
    manifest_path = release_directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("release manifest is missing or is not a regular file")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _transaction_bytes(
    release_directory: Path,
    stage: Path,
    *,
    old_manifest_sha256: str,
    new_manifest_sha256: str,
) -> bytes:
    transaction = {
        "format": TRANSACTION_FORMAT,
        "release_directory": str(release_directory.absolute()),
        "backup_name": _backup_path(release_directory).name,
        "stage_name": stage.name,
        "old_manifest_sha256": old_manifest_sha256,
        "new_manifest_sha256": new_manifest_sha256,
    }
    return (json.dumps(transaction, sort_keys=True) + "\n").encode("utf-8")


def _load_transaction(release_directory: Path) -> dict[str, str] | None:
    path = _transaction_path(release_directory)
    if path.is_symlink():
        raise ValueError(f"web transaction marker is not a regular file: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"web transaction marker is not a regular file: {path}")
    try:
        transaction = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"web transaction marker is unreadable: {path}") from error
    expected_keys = {
        "format",
        "release_directory",
        "backup_name",
        "stage_name",
        "old_manifest_sha256",
        "new_manifest_sha256",
    }
    if not isinstance(transaction, dict) or set(transaction) != expected_keys:
        raise ValueError(f"web transaction marker has an invalid schema: {path}")
    if any(not isinstance(transaction[key], str) for key in expected_keys):
        raise ValueError(f"web transaction marker has an invalid schema: {path}")
    if (
        transaction["format"] != TRANSACTION_FORMAT
        or transaction["release_directory"] != str(release_directory.absolute())
        or transaction["backup_name"] != _backup_path(release_directory).name
        or not transaction["stage_name"].startswith(_stage_prefix(release_directory))
    ):
        raise ValueError(f"web transaction marker does not match this release: {path}")
    for key in ("old_manifest_sha256", "new_manifest_sha256"):
        digest = transaction[key]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"web transaction marker has an invalid digest: {path}")
    return transaction


def _recover_interrupted_install(release_directory: Path) -> None:
    if release_directory.is_symlink():
        raise ValueError(f"release path must not be a symlink: {release_directory}")
    backup = _backup_path(release_directory)
    transaction_path = _transaction_path(release_directory)
    transaction = _load_transaction(release_directory)
    if (backup.exists() or backup.is_symlink()) and transaction is None:
        raise ValueError(
            f"web recovery backup exists without a transaction marker: {backup}; "
            "inspect it before retrying"
        )
    if transaction is not None:
        if backup.is_symlink() or (backup.exists() and not backup.is_dir()):
            raise ValueError(f"web recovery path is not a directory: {backup}")
        release_exists = release_directory.exists()
        backup_exists = backup.exists()
        if release_exists:
            if release_directory.is_symlink() or not release_directory.is_dir():
                raise ValueError(f"release path is not a directory: {release_directory}")
            _verify_managed_bundle(release_directory)
            release_manifest_sha256 = _manifest_sha256(release_directory)
        else:
            release_manifest_sha256 = None
        if backup_exists:
            _verify_managed_bundle(backup)
            backup_manifest_sha256 = _manifest_sha256(backup)
        else:
            backup_manifest_sha256 = None

        old_digest = transaction["old_manifest_sha256"]
        new_digest = transaction["new_manifest_sha256"]
        if release_exists and backup_exists:
            if release_manifest_sha256 != new_digest or backup_manifest_sha256 != old_digest:
                raise ValueError("web transaction state is ambiguous; preserving current and backup")
            shutil.rmtree(backup)
            _fsync_directory(release_directory.parent)
        elif backup_exists:
            if backup_manifest_sha256 != old_digest:
                raise ValueError("web transaction backup does not match the transaction marker")
            _rename_path(backup, release_directory)
            _fsync_directory(release_directory.parent)
        elif release_exists:
            if release_manifest_sha256 not in {old_digest, new_digest}:
                raise ValueError("web transaction current release does not match the marker")
        else:
            raise ValueError("web transaction has neither a current release nor a backup")

        _remove_owned_stages(release_directory)
        transaction_path.unlink()
        _fsync_directory(release_directory.parent)
        return
    _remove_owned_stages(release_directory)


def _install_updated_bundle(
    release_directory: Path,
    output_name: str,
    rendered: bytes,
    manifest: dict[str, Any],
) -> None:
    if not release_directory.name or release_directory.name in {".", ".."}:
        raise ValueError(f"unsafe release directory: {release_directory}")
    _recover_interrupted_install(release_directory)
    if release_directory.is_symlink() or not release_directory.is_dir():
        raise ValueError(f"release path is not a directory: {release_directory}")
    for path in release_directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"release bundle entries must be regular files before web generation: {path.name}"
            )

    backup = _backup_path(release_directory)
    transaction_path = _transaction_path(release_directory)
    stage = Path(
        tempfile.mkdtemp(
            prefix=_stage_prefix(release_directory),
            dir=release_directory.parent,
        )
    )
    owner_marker = _stage_owner_path(stage)
    swap_started = False
    try:
        _write_durable(owner_marker, str(release_directory.absolute()).encode("utf-8"))
        _fsync_directory(release_directory.parent)
        shutil.copytree(
            release_directory,
            stage,
            dirs_exist_ok=True,
            symlinks=True,
            copy_function=shutil.copy2,
        )
        _write_durable(stage / output_name, rendered)
        _write_durable(stage / "manifest.json", _manifest_bytes(manifest))
        _verify_managed_bundle(stage)
        _fsync_tree(stage)

        _write_durable(
            transaction_path,
            _transaction_bytes(
                release_directory,
                stage,
                old_manifest_sha256=_manifest_sha256(release_directory),
                new_manifest_sha256=_manifest_sha256(stage),
            ),
        )
        _fsync_directory(release_directory.parent)

        swap_started = True
        _rename_path(release_directory, backup)
        try:
            _fsync_directory(release_directory.parent)
            _rename_path(stage, release_directory)
            _fsync_directory(release_directory.parent)
        except BaseException:
            if not release_directory.exists() and backup.exists():
                _rename_path(backup, release_directory)
                _fsync_directory(release_directory.parent)
            raise
        owner_marker.unlink()
        _fsync_directory(release_directory.parent)
        if backup.exists():
            shutil.rmtree(backup)
            _fsync_directory(release_directory.parent)
        transaction_path.unlink()
        _fsync_directory(release_directory.parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if owner_marker.exists():
            owner_marker.unlink()
        if (
            transaction_path.exists()
            and (
                not swap_started
                or (release_directory.exists() and not backup.exists())
            )
        ):
            transaction_path.unlink()


def generate(input_path: str | Path, output_path: str | Path) -> None:
    source, output = _validate_output_path(input_path, output_path)
    _recover_interrupted_install(source.parent)
    manifest_path, manifest = _verified_release_manifest(source, output)
    rendered = render(load_geojson(source), TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw = rendered.encode("utf-8")
    manifest["files"][output.name] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "role": "standalone_atlas_html",
        "source": "atlas.geojson",
    }
    _install_updated_bundle(
        manifest_path.parent,
        output.name,
        raw,
        manifest,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the standalone Semiconductor Atlas UI")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.input, args.output)


if __name__ == "__main__":
    main()
