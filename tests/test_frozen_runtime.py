"""Synthetic Git repositories and isolated subprocesses, without source acquisition."""

import base64
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr

from scripts import frozen_runtime as runtime, replay_frozen_runtime as replay


PROBE = '''import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semiconductor_atlas import marker
print(json.dumps({"marker": marker, "site_loaded": "site" in sys.modules,
                  "pythonpath": os.environ.get("PYTHONPATH"), "arguments": sys.argv[1:]}))
'''


class FrozenRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-runtime-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.files = {"semiconductor_atlas/__init__.py": "from .models import marker\n",
                      "semiconductor_atlas/models.py": "marker = 'committed'\n",
                      "semiconductor_atlas/migrations/0001_initial.sql": "-- original SQL\n",
                      "semiconductor_atlas/adapters/__init__.py": "",
                      "scripts/record_realized_fab_cohort.py": PROBE,
                      "web/atlas-template.html": "<html>template only</html>\n",
                      "web/generate_atlas.py": "# generator\n",
                      "pyproject.toml": "[project]\nname = 'fixture'\nversion = '0'\n",
                      "source_snapshots/not-code.html": "not included in runtime\n"}
        for name, text in self.files.items():
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Runtime tests", "-c", "user.email=runtime@example.test", "commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.bundle = self.root / "runtime.json"
        self.receipt = runtime.retain_runtime(self.repository, commit=self.commit, output=self.bundle)
        self.raw = self.bundle.read_bytes()
        self.value = json.loads(self.raw)
        self.inputs = {key: self.root / key for key in ("cohort", "source-body", "provenance")}

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repository), *arguments], check=True,
                              capture_output=True, text=True).stdout

    def verify(self):
        return runtime.verify_runtime(self.bundle, expected_sha256=self.receipt["sha256"], expected_commit=self.commit)

    def replay(self, **changes):
        options = dict(expected_sha256=self.receipt["sha256"], expected_commit=self.commit,
                       operation="realized-cohort", inputs=self.inputs, working_directory=self.repository)
        options.update(changes)
        return replay.run_verified(self.bundle, **options)

    def decode_changed(self, value):
        raw = runtime.canonical(value)
        return runtime._decode(raw, expected_sha256=runtime.digest(raw), expected_commit=self.commit)

    def test_exact_committed_selection_includes_migrations_and_web_but_not_evidence(self):
        value, payloads = self.verify()
        expected = set(self.files) - {"source_snapshots/not-code.html"}
        self.assertEqual(set(payloads), expected)
        self.assertEqual(value["payload_bytes"], sum(len(self.files[name].encode()) for name in expected))
        self.assertEqual(value["commit"], self.commit)
        self.assertFalse(value["boundaries"]["evidence_data_roots_included"])
        self.assertNotIn("semiconductor_atlas", runtime.__dict__)

    def test_dirty_and_untracked_working_code_cannot_change_snapshot_or_replay(self):
        (self.repository / "semiconductor_atlas/models.py").write_text("raise RuntimeError('evolving checkout')\n")
        (self.repository / "semiconductor_atlas/migrations/0002_new.sql").write_text("-- newer migration\n")
        second = self.root / "second.json"
        result = runtime.retain_runtime(self.repository, commit=self.commit, output=second)
        _, payloads = runtime.verify_runtime(second, expected_sha256=result["sha256"], expected_commit=self.commit)
        self.assertEqual(payloads["semiconductor_atlas/models.py"], b"marker = 'committed'\n")
        self.assertNotIn("semiconductor_atlas/migrations/0002_new.sql", payloads)
        result = self.replay()
        self.assertEqual(result["operation_receipt"]["marker"], "committed")
        self.assertEqual(result["imported_project_modules"]["semiconductor_atlas.models"], "semiconductor_atlas/models.py")

    def test_hostile_cwd_python_environment_and_site_hooks_are_not_loaded(self):
        marker = self.root / "site-loaded"
        (self.repository / "sitecustomize.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n")
        (self.repository / "usercustomize.py").write_text("raise RuntimeError('user hook')\n")
        (self.repository / "semiconductor_atlas/__init__.py").write_text("raise RuntimeError('cwd shadow')\n")
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(self.repository), "PYTHONHOME": str(self.repository),
                                          "PYTHONSTARTUP": str(self.repository / "sitecustomize.py")}):
            result = self.replay()
        self.assertFalse(result["operation_receipt"]["site_loaded"])
        self.assertIsNone(result["operation_receipt"]["pythonpath"])
        self.assertFalse(marker.exists())
        self.assertEqual(result["isolated_flags"], ["-I", "-S", "-B"])

    def test_all_mutation_and_arbitrary_command_routes_are_absent(self):
        for operation in ("poll", "migrate", "advance", "python", "realized-cohort admit"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.replay(operation=operation)
        with self.assertRaises(ValueError):
            self.replay(inputs={**self.inputs, "output": self.root / "new.json"})
        with self.assertRaises(ValueError):
            self.replay(inputs={**self.inputs, "cohort": "relative.json"})

    def test_interpreter_drift_fails_before_running_any_code(self):
        with mock.patch.object(runtime, "interpreter", return_value={}), mock.patch.object(replay, "_capture") as child:
            with self.assertRaisesRegex(ValueError, "interpreter"):
                self.replay()
            child.assert_not_called()

    def test_modified_bundle_cannot_be_laundered_by_resealing_internal_hashes(self):
        value = copy.deepcopy(self.value)
        row = next(r for r in value["files"] if r["path"].endswith("models.py"))
        row["base64"] = base64.b64encode(b"marker = 'substituted'\n").decode()
        self.bundle.write_bytes(runtime.canonical(value))
        with self.assertRaisesRegex(ValueError, "trusted exact file hash"):
            self.verify()

    def test_internal_bytes_git_blobs_counts_and_selection_validate(self):
        mutations = [("sha256", "0" * 64), ("git_blob_sha1", "0" * 40), ("bytes", True),
                     ("mode", "120000"), ("base64", "invalid-base64")]
        for field, value in mutations:
            changed = copy.deepcopy(self.value)
            changed["files"][0][field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                self.decode_changed(changed)
        for field, value in (("payload_bytes", 0), ("selection", []), ("commit", "0" * 40), ("retained_at", None)):
            changed = copy.deepcopy(self.value); changed[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                self.decode_changed(changed)

    def test_member_traversal_duplicate_case_collision_and_missing_package_fail(self):
        for name in ("../escape.py", "/absolute.py", "scripts/../escape.py", "scripts/a\\b.py", "source_snapshots/a.py"):
            changed = copy.deepcopy(self.value); changed["files"][0]["path"] = name
            with self.subTest(name=name), self.assertRaises(ValueError): self.decode_changed(changed)
        changed = copy.deepcopy(self.value); changed["files"].append(changed["files"][0])
        with self.assertRaises(ValueError): self.decode_changed(changed)
        changed = copy.deepcopy(self.value)
        extra = copy.deepcopy(next(r for r in changed["files"] if r["path"].endswith("models.py")))
        extra["path"] = "semiconductor_atlas/MODELS.py"
        changed["files"] = sorted([*changed["files"], extra], key=lambda r: r["path"])
        with self.assertRaisesRegex(ValueError, "case-colliding"): self.decode_changed(changed)
        changed = copy.deepcopy(self.value)
        changed["files"] = [r for r in changed["files"] if r["path"] != "semiconductor_atlas/__init__.py"]
        with self.assertRaisesRegex(ValueError, "missing"): self.decode_changed(changed)

    def test_exact_manifest_hash_canonical_json_and_commit_are_required(self):
        with self.assertRaises(ValueError):
            runtime.verify_runtime(self.bundle, expected_sha256="0" * 64, expected_commit=self.commit)
        with self.assertRaises(ValueError):
            runtime.verify_runtime(self.bundle, expected_sha256=self.receipt["sha256"], expected_commit="HEAD")
        raw = self.raw + b" "
        with self.assertRaises(ValueError):
            runtime._decode(raw, expected_sha256=runtime.digest(raw), expected_commit=self.commit)
        raw = self.raw.replace(b'{\n', b'{\n"format":"duplicate",\n', 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runtime._decode(raw, expected_sha256=runtime.digest(raw), expected_commit=self.commit)

    def test_symlink_members_and_symlink_input_or_parent_are_rejected(self):
        linked = self.repository / "scripts/link.py"
        linked.symlink_to(self.repository / "scripts/record_realized_fab_cohort.py")
        self.git("add", "scripts/link.py")
        self.git("-c", "user.name=Runtime tests", "-c", "user.email=runtime@example.test", "commit", "-qm", "symlink")
        with self.assertRaisesRegex(ValueError, "symlink"):
            runtime.retain_runtime(self.repository, commit=self.git("rev-parse", "HEAD").strip(), output=self.root / "bad.json")
        alias = self.root / "bundle-alias.json"; alias.symlink_to(self.bundle)
        with self.assertRaises(OSError):
            runtime.verify_runtime(alias, expected_sha256=self.receipt["sha256"], expected_commit=self.commit)
        parent = self.root / "parent-alias"; parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): runtime.read(parent / "runtime.json")

    def test_existing_and_competing_outputs_are_preserved(self):
        with self.assertRaises(FileExistsError): runtime.write_new(self.bundle, b"replacement")
        self.assertEqual(self.bundle.read_bytes(), self.raw)
        target = self.root / "race.json"
        original = os.link
        def competitor(source, destination, **kwargs):
            target.write_bytes(b"other writer")
            return original(source, destination, **kwargs)
        with mock.patch.object(os, "link", side_effect=competitor), self.assertRaises(FileExistsError):
            runtime.write_new(target, b"my bytes")
        self.assertEqual(target.read_bytes(), b"other writer")
        self.assertEqual(list(self.root.glob(".atlas-runtime-*")), [])

    def test_bundle_change_during_subprocess_never_returns_success(self):
        original = replay._capture
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            self.bundle.write_bytes(self.raw + b" ")
            return result
        with mock.patch.object(replay, "_capture", side_effect=change), self.assertRaises(ValueError): self.replay()

    def test_staged_code_mutation_and_untracked_bytecode_fail_replay(self):
        original = replay._capture
        for mutation in ("source", "extra"):
            def change(command, **kwargs):
                result = original(command, **kwargs)
                root = Path(command[-3])
                if mutation == "source":
                    path = root / "semiconductor_atlas/models.py"
                    path.chmod(0o600); path.write_text("changed\n")
                else:
                    (root / "extra.pyc").write_bytes(b"not admitted")
                return result
            with self.subTest(mutation=mutation), mock.patch.object(replay, "_capture", side_effect=change):
                with self.assertRaises(ValueError): self.replay()

    def test_failed_process_missing_origin_or_invalid_json_cannot_report_success(self):
        with mock.patch.object(replay, "_capture", return_value=(3, b"", b"failed import")):
            with self.assertRaisesRegex(ValueError, "failed import"): self.replay()
        original = replay._capture
        def no_origins(command, **kwargs):
            result = original(command, **kwargs)
            (Path(command[-3]) / ".runtime-imports.json").write_text('{"modules":{},"violations":[]}')
            return result
        with mock.patch.object(replay, "_capture", side_effect=no_origins), self.assertRaises(ValueError): self.replay()

    def test_subprocess_timeout_and_output_bounds_terminate_without_success(self):
        for code, message in (("import time; time.sleep(2)", "timed out"),
                              ("print('x' * 1100000)", "output exceeded")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                replay._capture([sys.executable, "-I", "-S", "-B", "-c", code], cwd=self.root,
                                timeout=0.1 if "time.sleep" in code else 3)

    def test_cli_saves_actual_receipt_bytes_without_replacing_them(self):
        target = self.root / "receipt.json"
        arguments = ["--bundle", str(self.bundle), "--sha256", self.receipt["sha256"], "--commit", self.commit,
                     "--working-directory", str(self.repository), "--receipt", str(target), "realized-cohort"]
        for key, path in self.inputs.items(): arguments.extend(["--" + key, str(path)])
        stdout = io.StringIO()
        with redirect_stdout(stdout): self.assertEqual(replay.main(arguments), 0)
        saved = target.read_bytes()
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["sha256"], runtime.digest(saved))
        self.assertEqual(summary["bytes"], len(saved))
        self.assertTrue(json.loads(saved)["verified"])
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(replay.main(arguments), 1)
        self.assertEqual(saved, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
