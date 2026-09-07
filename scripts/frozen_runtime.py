#!/usr/bin/env python3
"""Retain committed Atlas source code for separately pinned historical replay.

This controller deliberately imports no Atlas package or repository helpers.
It isolates source bytes, not the complete Python/OS environment or permissions.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


FORMAT = "semiconductor-atlas-frozen-source-runtime-v1"
SELECTION = ["semiconductor_atlas/", "scripts/", "pyproject.toml",
             "web/atlas-template.html", "web/generate_atlas.py"]
MAX_BYTES = 32_000_000
MAX_PAYLOAD = 16_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


_LOADED_PRODUCER = digest(Path(__file__).read_bytes())


def producer():
    current = digest(Path(__file__).read_bytes())
    if current != _LOADED_PRODUCER:
        raise ValueError("runtime controller changed since module loading")
    return current


def canonical(value):
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("runtime manifest exceeds byte limit")
    return raw


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{" + str(length) + "}", value):
        raise ValueError("invalid exact hexadecimal identity")
    return value


def _keys(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("unexpected runtime contract fields")


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 300:
        raise ValueError("invalid runtime member path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("unsafe runtime member path")
    if not (value in SELECTION or any(value.startswith(prefix) for prefix in SELECTION if prefix.endswith("/"))):
        raise ValueError("runtime member outside source selection")
    return value


def _directory(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is unsupported")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _same_parent(path, original):
    current = _directory(path.parent)
    try:
        a, b = os.fstat(current), os.fstat(original)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise ValueError("runtime parent pathname changed")
    finally:
        os.close(current)


def read(path, *, limit=MAX_BYTES, allow_empty=False):
    path = Path(path).absolute()
    parent = _directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit
                    or (before.st_size == 0 and not allow_empty)):
                raise ValueError("expected a bounded nonempty regular runtime input")
            result = bytearray()
            while block := os.read(descriptor, min(65_536, limit + 1 - len(result))):
                result.extend(block)
                if len(result) > limit:
                    raise ValueError("runtime input grew beyond limit")
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (_identity(before) != _identity(os.fstat(descriptor))
                    or _identity(before) != _identity(named) or len(result) != before.st_size):
                raise ValueError("runtime input changed while reading")
            _same_parent(path, parent)
            return bytes(result)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def write_new(path, raw):
    path = Path(path).absolute()
    parent = _directory(path.parent)
    staging = ".atlas-runtime-" + secrets.token_hex(16)
    descriptor = None
    try:
        descriptor = os.open(staging, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        identity = os.fstat(descriptor)
        pending = memoryview(raw)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError("short runtime staging write")
            pending = pending[written:]
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        retained = bytearray()
        while block := os.read(descriptor, 65_536):
            retained.extend(block)
        staged = os.stat(staging, dir_fd=parent, follow_symlinks=False)
        if bytes(retained) != raw or (staged.st_dev, staged.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("runtime staging changed")
        _same_parent(path, parent)
        os.link(staging, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.fsync(parent)
        _same_parent(path, parent)
        published = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (published.st_dev, published.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("published runtime pathname changed")
    finally:
        if descriptor is not None:
            try:
                staged = os.stat(staging, dir_fd=parent, follow_symlinks=False)
                if (staged.st_dev, staged.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(staging, dir_fd=parent)
            except FileNotFoundError:
                pass
            finally:
                os.close(descriptor)
        os.close(parent)


def interpreter():
    return {"implementation": platform.python_implementation(), "version": platform.python_version(),
            "executable_sha256": digest(Path(sys.executable).resolve().read_bytes()),
            "sqlite_version": sqlite3.sqlite_version}


def _git(repository, *arguments):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(repository), *arguments],
                            check=True, capture_output=True, timeout=30)
    if len(result.stdout) > MAX_PAYLOAD:
        raise ValueError("Git object exceeds runtime payload limit")
    return result.stdout


def retain_runtime(repository, *, commit, output):
    _hex(commit, 40)
    producer_hash = producer()
    if _git(repository, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise ValueError("v1 supports explicit SHA-1 Git commits only")
    if _git(repository, "cat-file", "-t", commit).strip() != b"commit":
        raise ValueError("runtime source must be an exact commit, not a branch or tree")
    entries = _git(repository, "ls-tree", "-r", "-l", "-z", commit, "--", *SELECTION)
    files, length = [], 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob, size = metadata.decode("ascii").split()
        path = _path(name.decode("utf-8"))
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("runtime selection contains a symlink, gitlink or nonregular file")
        if len(files) >= 2_000 or not 0 <= int(size) <= MAX_PAYLOAD - length:
            raise ValueError("runtime selection exceeds file or payload limits")
        raw = _git(repository, "cat-file", "blob", _hex(blob, 40))
        length += len(raw)
        if length > MAX_PAYLOAD:
            raise ValueError("combined runtime source exceeds limit")
        files.append({"path": path, "mode": mode, "git_blob_sha1": blob, "bytes": len(raw),
                      "sha256": digest(raw), "base64": base64.b64encode(raw).decode("ascii")})
    value = {"format": FORMAT, "commit": commit,
             "tree": _git(repository, "rev-parse", commit + "^{tree}").decode().strip(),
             "selection": SELECTION, "files": sorted(files, key=lambda row: row["path"]),
             "payload_bytes": length, "retained_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
             "interpreter": interpreter(), "producer_sha256": producer_hash,
             "boundaries": {"evidence_data_roots_included": False, "hermetic_os_runtime": False,
                            "security_sandbox": False, "scheduled_execution_rerouted": False}}
    raw = canonical(value)
    _decode(raw, expected_sha256=digest(raw), expected_commit=commit)
    if producer() != producer_hash:
        raise ValueError("runtime producer changed during retention")
    write_new(output, raw)
    verify_runtime(output, expected_sha256=digest(raw), expected_commit=commit)
    return {"output": str(Path(output).absolute()), "sha256": digest(raw), "bytes": len(raw),
            "commit": commit, "files": len(files), "payload_bytes": length}


def _decode(raw, *, expected_sha256, expected_commit):
    if digest(raw) != _hex(expected_sha256, 64):
        raise ValueError("runtime differs from the trusted exact file hash")
    _hex(expected_commit, 40)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate runtime JSON key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    _keys(value, {"format", "commit", "tree", "selection", "files", "payload_bytes", "retained_at",
                  "interpreter", "producer_sha256", "boundaries"})
    if canonical(value) != raw or value["format"] != FORMAT or value["commit"] != expected_commit or value["selection"] != SELECTION:
        raise ValueError("unsupported or noncanonical frozen runtime")
    _hex(value["tree"], 40)
    _hex(value["producer_sha256"], 64)
    _keys(value["interpreter"], {"implementation", "version", "executable_sha256", "sqlite_version"})
    _hex(value["interpreter"]["executable_sha256"], 64)
    for field in ("implementation", "version", "sqlite_version"):
        if not isinstance(value["interpreter"][field], str) or not 1 <= len(value["interpreter"][field]) <= 100:
            raise ValueError("invalid interpreter identity")
    if not isinstance(value["retained_at"], str):
        raise ValueError("runtime retention time must be explicit")
    stamp = datetime.fromisoformat(value["retained_at"].replace("Z", "+00:00"))
    if stamp.utcoffset() is None or stamp > datetime.now(UTC):
        raise ValueError("invalid or future runtime retention time")
    expected_boundaries = {"evidence_data_roots_included": False, "hermetic_os_runtime": False,
                           "security_sandbox": False, "scheduled_execution_rerouted": False}
    if canonical(value["boundaries"]) != canonical(expected_boundaries):
        raise ValueError("runtime limitations cannot be changed")
    if not isinstance(value["files"], list) or not 1 <= len(value["files"]) <= 2_000:
        raise ValueError("runtime requires a bounded file inventory")
    payloads, length = {}, 0
    for row in value["files"]:
        _keys(row, {"path", "mode", "git_blob_sha1", "bytes", "sha256", "base64"})
        path = _path(row["path"])
        if path in payloads or row["mode"] not in {"100644", "100755"}:
            raise ValueError("duplicate or nonregular runtime file")
        if type(row["bytes"]) is not int or not 0 <= row["bytes"] <= MAX_PAYLOAD:
            raise ValueError("invalid runtime member byte count")
        data = base64.b64decode(row["base64"], validate=True)
        length += len(data)
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if (len(data) != row["bytes"] or length > MAX_PAYLOAD or digest(data) != _hex(row["sha256"], 64)
                or blob != _hex(row["git_blob_sha1"], 40) or base64.b64encode(data).decode() != row["base64"]):
            raise ValueError("runtime source content binding mismatch")
        payloads[path] = data
    paths = list(payloads)
    if paths != sorted(paths) or len({p.casefold() for p in paths}) != len(paths):
        raise ValueError("unordered or case-colliding runtime paths")
    if any(str(parent) in payloads for p in paths for parent in PurePosixPath(p).parents):
        raise ValueError("runtime file and directory paths collide")
    if not {"pyproject.toml", "semiconductor_atlas/__init__.py"}.issubset(payloads):
        raise ValueError("runtime is missing its package or project metadata")
    if type(value["payload_bytes"]) is not int or value["payload_bytes"] != length:
        raise ValueError("runtime payload total mismatch")
    return value, payloads


def verify_runtime(path, *, expected_sha256, expected_commit):
    producer_hash = producer()
    result = _decode(read(path), expected_sha256=expected_sha256, expected_commit=expected_commit)
    if producer() != producer_hash:
        raise ValueError("runtime controller changed during verification")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("retain")
    freeze.add_argument("--repository", type=Path, required=True)
    freeze.add_argument("--commit", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "retain":
            result = retain_runtime(args.repository, commit=args.commit, output=args.output)
        else:
            value, files = verify_runtime(args.bundle, expected_sha256=args.sha256, expected_commit=args.commit)
            result = {"verified": True, "sha256": args.sha256, "commit": value["commit"],
                      "files": len(files), "payload_bytes": value["payload_bytes"],
                      "git_membership_reverified": False}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
