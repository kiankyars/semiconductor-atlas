"""Local command tests using synthetic publisher bodies and temporary files only."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import record_realized_fab_cohort as cli
from semiconductor_atlas import realized_fab_cohort as cohort
from tests.test_realized_fab_cohort import fixture


class RealizedFabCohortCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.review, self.anchor, self.body, self.provenance = fixture()
        self.paths = {"review": self.root / "review.json", "anchor": self.root / "anchor.json",
                      "source-body": self.root / "source.html", "provenance": self.root / "provenance.json"}
        self.inputs = {"review": cohort.canonical_bytes(self.review), "anchor": self.anchor,
                       "source-body": self.body, "provenance": self.provenance}
        for key, raw in self.inputs.items(): self.paths[key].write_bytes(raw)
        self.output = self.root / "cohort.json"

    def call(self, command, *, expected=0, overrides=None):
        paths = {**self.paths, "cohort": self.output, "output": self.output, **(overrides or {})}
        args = [command, "--source-body", str(paths["source-body"]), "--provenance", str(paths["provenance"])]
        if command == "admit":
            for key in ("review", "anchor", "output"):
                args += ["--" + key, str(paths[key])]
        else:
            args += ["--cohort", str(paths["cohort"])]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(args)
        self.assertEqual(code, expected, stderr.getvalue())
        if code:
            self.assertEqual(stdout.getvalue(), "")
            return stderr.getvalue()
        return json.loads(stdout.getvalue())

    def test_admit_verifies_embedded_anchor_and_hashes_actual_new_output(self):
        receipt = self.call("admit")
        raw = self.output.read_bytes()
        self.assertEqual(receipt["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt["bytes"], len(raw))
        self.assertEqual(receipt["counts"], {"newly_accepted": 2, "carried_forward": 1, "deferred": 1})
        self.assertEqual(receipt["table_rows"], 4)
        self.assertFalse(receipt["database_writes"])
        self.assertFalse(receipt["publisher_bodies_embedded"])
        artifact = json.loads(raw)
        self.assertEqual(cohort.canonical_bytes(artifact["anchor"]), self.anchor)
        self.assertNotIn(b"<table>", raw)
        self.assertEqual(raw, cohort.canonical_bytes(artifact))
        self.paths["anchor"].unlink()
        replay = self.call("verify")
        self.assertTrue(replay["verified"])
        self.assertEqual(receipt["sha256"], replay["sha256"])
        self.assertEqual(receipt["artifact_sha256"], replay["artifact_sha256"])

    def test_existing_output_is_never_overwritten_and_all_inputs_preserved(self):
        self.call("admit")
        saved = self.output.read_bytes()
        self.call("admit", expected=1)
        self.assertEqual(self.output.read_bytes(), saved)
        for key, raw in self.inputs.items():
            self.assertEqual(self.paths[key].read_bytes(), raw)

    def test_changed_body_provenance_or_exact_anchor_does_not_publish(self):
        for key in ("source-body", "provenance", "anchor"):
            with self.subTest(key=key):
                self.paths[key].write_bytes(self.inputs[key] + b" ")
                self.call("admit", expected=1)
                self.assertFalse(self.output.exists())
                self.paths[key].write_bytes(self.inputs[key])

    def test_duplicate_review_keys_and_incomplete_inventory_fail_before_output(self):
        self.paths["review"].write_bytes(b'{"format":"one","format":"two"}')
        self.call("admit", expected=1)
        self.review["rows"].pop()
        self.paths["review"].write_bytes(cohort.canonical_bytes(self.review))
        self.call("admit", expected=1)
        self.assertFalse(self.output.exists())

    def test_all_explicit_inputs_reject_symlinks(self):
        for key in self.paths:
            with self.subTest(key=key):
                alias = self.root / (key + "-alias")
                alias.symlink_to(self.paths[key])
                self.call("admit", expected=1, overrides={key: alias})
                self.assertFalse(self.output.exists())

    def test_symlinked_cohort_and_output_rejected(self):
        target = self.root / "sentinel.json"
        target.write_bytes(b"preserve another writer's file")
        self.output.symlink_to(target)
        self.call("admit", expected=1)
        self.assertEqual(target.read_bytes(), b"preserve another writer's file")
        self.output.unlink()
        self.call("admit")
        alias = self.root / "cohort-alias.json"
        alias.symlink_to(self.output)
        self.call("verify", expected=1, overrides={"cohort": alias})

    def test_verify_failure_happens_before_publication(self):
        with mock.patch.object(cohort, "verify", side_effect=ValueError("engineering replay failure")), \
                mock.patch.object(cohort, "write_new") as writer:
            self.assertIn("replay failure", self.call("admit", expected=1))
            writer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_published_bytes_are_not_reported_as_success(self):
        original = cohort.write_new
        def changed(path, artifact):
            original(path, artifact)
            path.write_bytes(b"complete externally replaced output")
        with mock.patch.object(cohort, "write_new", side_effect=changed):
            self.assertIn("published cohort bytes", self.call("admit", expected=1))
        self.assertEqual(self.output.read_bytes(), b"complete externally replaced output")

    def test_missing_empty_and_directory_inputs_fail_without_output(self):
        empty = self.root / "empty.html"
        empty.write_bytes(b"")
        for bad in (empty, self.root, self.root / "missing.html"):
            self.call("admit", expected=1, overrides={"source-body": bad})
            self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
