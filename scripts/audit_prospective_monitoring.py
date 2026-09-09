#!/usr/bin/env python3
"""Audit monitoring coverage without changing the pinned prospective producer."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import frozen_runtime as runtime, replay_frozen_runtime as replay


WORKER = "scripts/prospective_monitoring_status.py"
_LOADED_CODE = runtime.digest(Path(__file__).read_bytes())


def codes():
    current = runtime.digest(runtime.read(Path(__file__)))
    if current != _LOADED_CODE:
        raise ValueError("monitoring audit controller changed after loading")
    return {**replay._codes(), "scripts/audit_prospective_monitoring.py": current,
            WORKER: runtime.digest(runtime.read(Path(__file__).with_name(Path(WORKER).name)))}


def run_audit(bundle, *, expected_sha256, expected_commit, registration, reference_root):
    identities = codes()
    registration, reference_root = Path(registration), Path(reference_root)
    for path in (registration, reference_root):
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("audit inputs require explicit absolute paths")
    descriptor = runtime._directory(reference_root)
    os.close(descriptor)
    registration_raw = runtime.read(registration)
    value, payloads = runtime.verify_runtime(bundle, expected_sha256=expected_sha256, expected_commit=expected_commit)
    if runtime.interpreter() != value["interpreter"]:
        raise ValueError("audit interpreter differs from the retained runtime")
    if WORKER in payloads:
        raise ValueError("audit worker must remain separate from the registered runtime")
    payloads = {**payloads, WORKER: runtime.read(Path(__file__).with_name(Path(WORKER).name))}
    if runtime.digest(payloads[WORKER]) != identities[WORKER]:
        raise ValueError("audit worker changed before staging")
    with tempfile.TemporaryDirectory(prefix="atlas-monitoring-audit-") as directory:
        root = Path(directory).resolve()
        for name, data in payloads.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(data)
            path.chmod(0o400)
        if any(runtime.read(root / name, allow_empty=True) != raw for name, raw in payloads.items()):
            raise ValueError("staged runtime changed before audit execution")
        status, stdout, stderr = replay._capture(
            [sys.executable, "-I", "-S", "-B", "-c", replay.BOOTSTRAP, str(root), WORKER,
             json.dumps([str(registration), str(reference_root)])], cwd=root, timeout=60)
        if status:
            raise ValueError("monitoring audit failed: " + stderr.decode("utf-8", errors="replace")[-4000:])
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink()}
        if actual != set(payloads) | {".runtime-imports.json"}:
            raise ValueError("audit changed the staged runtime inventory")
        if any(runtime.read(root / name, allow_empty=True) != raw for name, raw in payloads.items()):
            raise ValueError("audit changed the staged runtime source bytes")
        imports = json.loads(runtime.read(root / ".runtime-imports.json"))
        runtime._keys(imports, {"modules", "violations"})
        if (imports["violations"] or not isinstance(imports["modules"], dict)
                or "semiconductor_atlas.prospective_target_review" not in imports["modules"]
                or any(name not in payloads for name in imports["modules"].values())):
            raise ValueError("audit imports escaped the retained source runtime")
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get("format") != "semiconductor-atlas-prospective-monitoring-status-v1":
            raise ValueError("audit worker did not return the operational report")
    runtime.verify_runtime(bundle, expected_sha256=expected_sha256, expected_commit=expected_commit)
    if (identities != codes() or runtime.read(registration) != registration_raw
            or runtime.interpreter() != value["interpreter"]):
        raise ValueError("audit producer, interpreter or registration changed")
    return {**result, "runtime_sha256": expected_sha256, "runtime_commit": expected_commit,
        "audit_code_sha256": identities, "imported_project_modules": imports["modules"],
        "interpreter": value["interpreter"], "isolated_flags": ["-I", "-S", "-B"],
        "hermetic_os_runtime": False, "security_sandbox": False}


def save_report(output, result, *, reference_root):
    output = Path(output).absolute()
    protected_files = {*result["protected_files"], *result["journal_and_registration_files"],
        *(str(Path(reference_root) / name) for name in result["source_snapshot_files"])}
    if (str(output) in protected_files
            or any(output.is_relative_to(Path(name)) for name in result["protected_directories"])):
        raise ValueError("audit output overlaps protected study inputs")
    raw = runtime.canonical(result)
    runtime.write_new(output, raw)
    if runtime.read(output) != raw:
        raise ValueError("saved operational report differs")
    return {"output": str(output), "bytes": len(raw), "sha256": runtime.digest(raw)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_audit(args.bundle, expected_sha256=args.sha256, expected_commit=args.commit,
                           registration=args.registration, reference_root=args.reference_root)
        if args.output:
            saved = save_report(args.output, result, reference_root=args.reference_root)
            result = {**saved, "counts": result["counts"], "batches": result["batches"],
                      "in_protocol": result["in_protocol"], "status": result["status"]}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
