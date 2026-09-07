#!/usr/bin/env python3
"""Replay approved read-only Atlas checks in verified, isolated historical code."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import frozen_runtime as runtime


OPERATIONS = {
    "realized-milestone": ("scripts/record_realized_milestone.py", "verify",
                           ("observation", "source-body", "provenance")),
    "realized-cohort": ("scripts/record_realized_fab_cohort.py", "verify",
                        ("cohort", "source-body", "provenance")),
    "target-registration": ("scripts/shadow_source_targets.py", "verify",
                            ("registration", "reference-root")),
    "target-policy": ("scripts/evaluate_prospective_source_targets.py", "verify-policy",
                      ("registration", "policy", "reference-root")),
    "target-population": ("scripts/evaluate_project_targets.py", "verify", ("frozen",)),
}
MAX_OUTPUT = 1_000_000
_LOADED_PRODUCER = runtime.digest(Path(__file__).read_bytes())


def _codes():
    current = runtime.digest(Path(__file__).read_bytes())
    if current != _LOADED_PRODUCER:
        raise ValueError("replay controller changed since module loading")
    return {"scripts/frozen_runtime.py": runtime.producer(), "scripts/replay_frozen_runtime.py": current}


BOOTSTRAP = r'''
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
script = root / sys.argv[2]
arguments = json.loads(sys.argv[3])
sys.path.insert(0, str(root))
sys.argv = [str(script), *arguments]
status = 0
try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as error:
    if error.code is None:
        status = 0
    elif isinstance(error.code, int):
        status = error.code
    else:
        print(error.code, file=sys.stderr)
        status = 1
finally:
    modules, violations = {}, []
    for name, module in list(sys.modules.items()):
        if not any(name == p or name.startswith(p + ".") for p in ("semiconductor_atlas", "scripts", "web")):
            continue
        origin = getattr(module, "__file__", None)
        if origin:
            path = Path(origin).resolve()
            if path.is_relative_to(root):
                modules[name] = path.relative_to(root).as_posix()
            else:
                violations.append(name)
        for location in getattr(module, "__path__", []):
            if not Path(location).resolve().is_relative_to(root):
                violations.append(name)
    (root / ".runtime-imports.json").write_text(json.dumps({"modules": modules, "violations": violations}))
    if violations:
        raise RuntimeError("project imports escaped the verified runtime")
sys.exit(status)
'''


def _capture(command, *, cwd, timeout):
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ValueError("replay timeout must be in (0, 60] seconds")
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "TZ": "UTC"}
    process = subprocess.Popen(command, cwd=cwd, env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams, length, deadline = {"stdout": bytearray(), "stderr": bytearray()}, 0, time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("frozen replay timed out")
                for key, _ in selector.select(min(remaining, 0.5)):
                    block = key.fileobj.read1(65_536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    streams[key.data].extend(block)
                    length += len(block)
                    if length > MAX_OUTPUT:
                        raise ValueError("frozen replay output exceeded its bound")
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise ValueError("frozen replay timed out") from error
        return code, bytes(streams["stdout"]), bytes(streams["stderr"])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def run_verified(bundle, *, expected_sha256, expected_commit, operation, inputs, working_directory, timeout=60):
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    codes = _codes()
    if operation not in OPERATIONS:
        raise ValueError("only named read-only replay operations are permitted")
    script, command, fields = OPERATIONS[operation]
    if not isinstance(inputs, dict) or set(inputs) != set(fields):
        raise ValueError("replay input fields must exactly match the selected operation")
    arguments = [command]
    for field in fields:
        path = Path(inputs[field])
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("replay evidence inputs require explicit absolute paths")
        arguments.extend(["--" + field, str(path)])
    working_directory = Path(working_directory).absolute()
    descriptor = runtime._directory(working_directory)
    os.close(descriptor)
    value, payloads = runtime.verify_runtime(bundle, expected_sha256=expected_sha256, expected_commit=expected_commit)
    if runtime.interpreter() != value["interpreter"]:
        raise ValueError("current interpreter identity differs from the retained runtime")
    if script not in payloads:
        raise ValueError("selected verifier is absent from the retained runtime")
    with tempfile.TemporaryDirectory(prefix="atlas-frozen-replay-") as directory:
        root = Path(directory).resolve()
        for path, data in payloads.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as output:
                output.write(data)
            target.chmod(0o400)
        for path, data in payloads.items():
            if runtime.read(root / path, limit=max(1, len(data)), allow_empty=True) != data:
                raise ValueError("staged runtime bytes changed before execution")
        code, stdout, stderr = _capture(
            [sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP, str(root), script, json.dumps(arguments)],
            cwd=working_directory, timeout=timeout)
        if code:
            raise ValueError("frozen verifier failed: " + stderr.decode("utf-8", errors="replace")[-4_000:])
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
        if actual != set(payloads) | {".runtime-imports.json"}:
            raise ValueError("frozen verifier changed the runtime file inventory")
        for path, data in payloads.items():
            if runtime.read(root / path, limit=max(1, len(data)), allow_empty=True) != data:
                raise ValueError("staged runtime changed during execution")
        audit = json.loads(runtime.read(root / ".runtime-imports.json", limit=MAX_OUTPUT))
        runtime._keys(audit, {"modules", "violations"})
        if (audit["violations"] or not isinstance(audit["modules"], dict)
                or "semiconductor_atlas" not in audit["modules"]
                or any(path not in payloads for path in audit["modules"].values())):
            raise ValueError("project module origins were not completely verified")
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError("verifier did not return an object receipt")
    runtime.verify_runtime(bundle, expected_sha256=expected_sha256, expected_commit=expected_commit)
    if runtime.interpreter() != value["interpreter"]:
        raise ValueError("interpreter identity changed during replay")
    if _codes() != codes:
        raise ValueError("replay controller changed during execution")
    return {"verified": True, "runtime_sha256": expected_sha256, "commit": expected_commit,
            "replay_started_at": started, "replayed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "operation": operation, "operation_receipt": result, "imported_project_modules": audit["modules"],
            "verifier_stderr": stderr.decode("utf-8", errors="replace"),
            "interpreter": value["interpreter"], "isolated_flags": ["-I", "-S", "-B"],
            "replay_code_sha256": codes,
            "network_acquisition_requested": False, "core_migration_requested": False,
            "scheduled_execution_rerouted": False, "hermetic_os_runtime": False, "security_sandbox": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, help="Optional new-only local replay receipt")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name, (_, _, fields) in OPERATIONS.items():
        command = commands.add_parser(name)
        for field in fields:
            command.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = {field: getattr(args, field.replace("-", "_")) for field in OPERATIONS[args.operation][2]}
    try:
        result = run_verified(args.bundle, expected_sha256=args.sha256, expected_commit=args.commit,
                              operation=args.operation, inputs=inputs, working_directory=args.working_directory)
        if args.receipt is not None:
            raw = runtime.canonical(result)
            runtime.write_new(args.receipt, raw)
            if runtime.read(args.receipt) != raw:
                raise ValueError("saved replay receipt differs from verified bytes")
            result = {"receipt": str(args.receipt.absolute()), "sha256": runtime.digest(raw), "bytes": len(raw),
                      "operation": args.operation, "verified": True,
                      "operation_receipt": result["operation_receipt"],
                      "imported_project_modules": len(result["imported_project_modules"])}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
