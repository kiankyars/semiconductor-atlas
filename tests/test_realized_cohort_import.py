"""Synthetic core-mapping tests; historical subprocess verification has its own suite."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from semiconductor_atlas import database, models, release, repository
from semiconductor_atlas import realized_cohort_import as importer
from semiconductor_atlas import realized_fab_cohort as cohort
from scripts import import_realized_fab_cohort as cli
from tests import test_realized_fab_cohort as fixtures


START = "2026-09-08T12:00:00.100000Z"
ADMITTED = "2026-09-08T12:00:00.200000Z"
BEFORE = "2026-09-08T12:00:00.199999Z"
LATER = "2026-09-08T13:00:00Z"


class RealizedCohortImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-core-realizations-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        review, anchor, body, provenance = fixtures.fixture()
        with mock.patch.object(cohort, "_now", side_effect=fixtures.ADMISSION_CLOCKS):
            self.artifact = cohort.admit(review, anchor=anchor, body=body, provenance=provenance)
        self.paths = {key: self.root / key for key in ("cohort", "source-body", "provenance", "runtime")}
        self.paths["cohort"].write_bytes(cohort.canonical_bytes(self.artifact))
        self.paths["source-body"].write_bytes(body)
        self.paths["provenance"].write_bytes(provenance)
        self.paths["runtime"].write_bytes(b"synthetic runtime identity; verifier mocked only in mapping tests")
        self.options = dict(source_body=self.paths["source-body"], provenance=self.paths["provenance"],
            frozen_runtime_bundle=self.paths["runtime"], expected_runtime_sha256=self.digest("runtime"),
            expected_runtime_commit="a" * 40, expected_cohort_sha256=self.digest("cohort"))
        self.connection, _ = database.initialize(self.root / "core.sqlite")
        self.addCleanup(self.connection.close)
        self.verifier = mock.patch.object(importer.replay, "run_verified", side_effect=self.verify_fixture).start()
        self.addCleanup(mock.patch.stopall)

    def digest(self, key):
        return importer._hash(self.paths[key].read_bytes())

    def verify_fixture(self, bundle, **options):
        self.assertEqual("realized-cohort", options["operation"])
        self.assertEqual(self.paths["runtime"], bundle)
        self.assertEqual(self.options["expected_runtime_sha256"], options["expected_sha256"])
        self.assertEqual(self.options["expected_runtime_commit"], options["expected_commit"])
        raw = self.paths["cohort"].read_bytes()
        verified = cohort.verify(raw, body=self.paths["source-body"].read_bytes(),
                                  provenance=self.paths["provenance"].read_bytes())
        return {"verified": True, "operation": "realized-cohort",
            "runtime_sha256": options["expected_sha256"], "commit": options["expected_commit"],
            "replay_started_at": "2026-09-08T12:00:00.100001Z", "replayed_at": "2026-09-08T12:00:00.110000Z",
            "operation_receipt": {"verified": True, "sha256": self.digest("cohort"),
                "artifact_sha256": verified["sha256"], "bytes": len(raw), "table_rows": verified["table_rows"],
                "counts": verified["counts"], "accepted_source_reports": verified["accepted_source_reports"],
                "source_bytes_verified": self.paths["source-body"].stat().st_size,
                "provenance_sha256": self.digest("provenance"), "database_writes": False,
                "publisher_bodies_embedded": False}}

    def accept(self, **changes):
        with mock.patch.object(importer, "_now", side_effect=[START, ADMITTED]):
            return importer.accept_cohort(self.connection, self.paths["cohort"], **(self.options | changes))

    def verify(self, **changes):
        with mock.patch.object(importer, "_now", return_value=LATER):
            return importer.accept_cohort(self.connection, self.paths["cohort"], verify_only=True,
                                           **(self.options | changes))

    def rows(self):
        return {row[0]: [tuple(item) for item in self.connection.execute(f'SELECT * FROM "{row[0]}" ORDER BY rowid')]
                for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def test_source_native_mapping_keeps_deferred_denominator_and_precise_events(self):
        result = self.accept()
        self.assertEqual(4, result["table_rows"])
        self.assertEqual(3, len(result["claim_ids"]))
        self.assertEqual(4, len(result["record_ids"]))
        self.assertEqual(3, len(result["entity_ids"]))
        for row in self.connection.execute("SELECT * FROM milestone_values"):
            self.assertEqual("commercial_production_commencement", row["milestone_type"])
            self.assertEqual("completed", row["status"])
            self.assertEqual("year", row["date_precision"])
            self.assertIsNone(row["date_base"])
            self.assertEqual(row["date_literal"] + "-01-01", row["date_low"])
            self.assertEqual(row["date_literal"] + "-12-31", row["date_high"])
        self.assertEqual({"1990", "2024"}, {r[0] for r in self.connection.execute("SELECT date_literal FROM milestone_values")})
        for row in self.connection.execute("SELECT * FROM claim_versions"):
            self.assertEqual("source_statement", row["claim_kind"])
            self.assertEqual(ADMITTED, row["recorded_at"])
            self.assertIsNone(row["valid_from"])
            self.assertIsNone(row["confidence"])
        params = json.loads(self.connection.execute("SELECT parameters_json FROM ingestion_runs").fetchone()[0])
        self.assertEqual(self.artifact, params["cohort"])
        self.assertTrue(params["selection"]["survivorship_bias"])
        self.assertEqual([], repository.validate_database(self.connection))

    def test_sidecar_clocks_are_retained_but_never_become_core_knowledge_clocks(self):
        self.accept()
        self.assertEqual([], repository.known_source_claims(self.connection, recorded_at=BEFORE))
        self.assertEqual(3, len(repository.known_source_claims(self.connection, recorded_at=ADMITTED)))
        self.assertEqual([], repository.current_claims(self.connection, as_of="2026-09-08", recorded_at=LATER))
        records = [json.loads(r[0]) for r in self.connection.execute("SELECT payload_json FROM source_records")]
        carried = next(r for r in records if r["case"]["decision"] == "carry_forward")
        self.assertEqual(fixtures.ANCHOR_AT, carried["sidecar_recorded_at"])
        self.assertEqual(ADMITTED, carried["core_admitted_at"])
        deferred = next(r for r in records if r["case"]["decision"] == "defer")
        self.assertIsNone(deferred["sidecar_recorded_at"])

    def test_release_preserves_completed_periods_only_after_core_admission(self):
        result = self.accept()
        before = self.rows()
        for name, cutoff, count in (("before-admission", BEFORE, 0), ("at-admission", ADMITTED, 3)):
            with self.subTest(cutoff=cutoff):
                output = self.root / name
                manifest = release.write_release(self.connection, output,
                    as_of="2026-09-08", recorded_at=cutoff)
                self.assertEqual(count, manifest["source_claims"])
                self.assertEqual(count, manifest["claim_history"])
                self.assertEqual(0, manifest["claims"])
                self.assertEqual(b"", (output / "claims.jsonl").read_bytes())
                rows = [json.loads(line) for line in (output / "source_claims.jsonl").read_text().splitlines()]
                self.assertEqual(count, len(rows))
                by_id = {row["id"]: row for row in rows}
                if not count:
                    self.assertEqual([], json.loads((output / "source_inputs.json").read_bytes()))
                    continue
                self.assertEqual(set(result["claim_ids"].values()), set(by_id))
                for case in self.artifact["cases"]:
                    if case["decision"] == "defer":
                        continue
                    row = by_id[result["claim_ids"][case["source_report_key"]]]
                    self.assertEqual("source_statement", row["claim_kind"])
                    self.assertEqual(ADMITTED, row["recorded_at"])
                    self.assertIsNone(row["valid_from"])
                    self.assertIsNone(row["confidence"])
                    event = case["event"]
                    self.assertEqual({"milestone_type": event["event_type"], "status": "completed",
                        "date_low": event["low"], "date_base": None, "date_high": event["high"],
                        "date_precision": "year", "date_literal": event["literal"]}, row["value"])
        self.assertEqual(before, self.rows())

    def test_complete_evidence_context_without_excerpts_or_unearned_claims(self):
        self.accept()
        evidence = list(self.connection.execute("SELECT * FROM claim_evidence"))
        self.assertEqual(9, len(evidence))
        self.assertEqual(3, sum(r["role"] == "support" for r in evidence))
        self.assertEqual(6, sum(r["role"] == "context" for r in evidence))
        for row in evidence:
            self.assertIsNone(row["excerpt"])
            self.assertIn("UTF-8 bytes [", row["locator"])
            self.assertIn("SHA-256", row["locator"])
        for table in ("geometry_values", "capacity_values", "relationship_values", "source_entity_assignments",
                      "entity_resolution_decisions"):
            self.assertEqual(0, self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], table)

    def test_exact_replay_with_writes_denied_preserves_every_row_and_admission(self):
        original = self.accept()
        before, changes = self.rows(), self.connection.total_changes
        self.connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) else sqlite3.SQLITE_OK)
        try:
            result = self.verify()
        finally:
            self.connection.set_authorizer(None)
        self.assertTrue(result["replayed"])
        self.assertEqual(0, result["database_writes"])
        self.assertEqual(original["admitted_at"], result["admitted_at"])
        self.assertEqual(before, self.rows())
        self.assertEqual(changes, self.connection.total_changes)

    def test_absent_verify_never_admits_and_old_schema_never_migrates(self):
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "no existing core admission"):
            self.verify()
        self.assertEqual(before, self.rows())
        old, _ = database.initialize(":memory:", target_version=5)
        try:
            with self.assertRaisesRegex(ValueError, "schema exactly 6"):
                importer.accept_cohort(old, self.paths["cohort"], **self.options)
            self.assertEqual(5, database.schema_version(old))
        finally:
            old.close()

    def test_trusted_identities_and_input_damage_fail_before_any_writes(self):
        before = self.rows()
        for change in ({"expected_cohort_sha256": "0" * 64}, {"expected_runtime_sha256": "0" * 64},
                       {"expected_runtime_commit": "HEAD"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.accept(**change)
        for key in ("cohort", "source-body", "provenance", "runtime"):
            raw = self.paths[key].read_bytes()
            try:
                self.paths[key].write_bytes(raw + b" ")
                with self.subTest(key=key), self.assertRaises(ValueError): self.accept()
            finally:
                self.paths[key].write_bytes(raw)
        self.assertEqual(before, self.rows())

    def test_verifier_failure_or_forged_result_cannot_produce_an_admission(self):
        before = self.rows()
        self.verifier.side_effect = ValueError("historical verifier failed")
        with self.assertRaisesRegex(ValueError, "historical verifier failed"): self.accept()
        for field, changed in (("verified", False), ("runtime_sha256", "0" * 64),
                               ("operation", "realized-milestone"), ("operation_receipt", {})):
            def wrong(*args, **kwargs):
                result = self.verify_fixture(*args, **kwargs)
                result[field] = changed
                return result
            self.verifier.side_effect = wrong
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "receipt differs"):
                self.accept()
        self.assertEqual(before, self.rows())

    def test_mid_population_failure_rolls_back_every_table(self):
        before = self.rows()
        original = importer._populate
        def fail(*args):
            original(*args)
            raise RuntimeError("after last insert")
        with mock.patch.object(importer, "_populate", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "after last insert"): self.accept()
        self.assertEqual(before, self.rows())
        self.assertFalse(self.connection.in_transaction)

    def test_first_admission_rejects_replay_clock_regression_or_missing_clocks(self):
        before = self.rows()
        for field, value in (("replayed_at", "2026-09-08T12:00:00.300000Z"),
                              ("replay_started_at", "2026-09-08T12:00:00.099999Z"),
                              ("replayed_at", "2026-09-08T12:00:00.100000Z"),
                              ("replayed_at", None)):
            def change(*args, **kwargs):
                receipt = self.verify_fixture(*args, **kwargs)
                receipt[field] = value
                return receipt
            self.verifier.side_effect = change
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.accept()
            self.assertEqual(before, self.rows())
            self.assertFalse(self.connection.in_transaction)

    def test_first_admission_rejects_future_reused_lineage_clock(self):
        ids = importer._ids(self.artifact, self.options["expected_cohort_sha256"])
        repository.add_source_family(self.connection, models.SourceFamily(ids["family"], importer.RULE_VERSION,
            "Reviewed retrospective fab source reports", LATER))
        self.connection.commit()
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "lineage clock postdates"):
            self.accept()
        self.assertEqual(before, self.rows())

    def test_changed_source_at_commit_boundary_rolls_back_every_table(self):
        before = self.rows()
        original = importer._populate
        def change(*args):
            result = original(*args)
            self.paths["source-body"].write_bytes(self.paths["source-body"].read_bytes() + b" ")
            return result
        with mock.patch.object(importer, "_populate", side_effect=change):
            with self.assertRaisesRegex(ValueError, "changed across admission"): self.accept()
        self.assertEqual(before, self.rows())

    def test_clock_regression_caller_transaction_and_disabled_foreign_keys_reject(self):
        before = self.rows()
        with mock.patch.object(importer, "_now", return_value=START):
            with self.assertRaisesRegex(ValueError, "clock must follow"):
                importer.accept_cohort(self.connection, self.paths["cohort"], **self.options)
        self.connection.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "ownership"): self.accept()
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.connection.execute("PRAGMA foreign_keys=OFF")
        with self.assertRaisesRegex(ValueError, "foreign-key"): self.accept()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.assertEqual(before, self.rows())

    def test_changed_cohort_cannot_duplicate_already_imported_report_keys(self):
        self.accept()
        before = self.rows()
        changed = copy.deepcopy(self.artifact)
        changed["review"]["rationale"] += " A second review of the same retained source table."
        checked = cohort.validate_review(changed["review"], anchor=cohort.canonical_bytes(changed["anchor"]),
            body=self.paths["source-body"].read_bytes(), provenance=self.paths["provenance"].read_bytes())
        changed = cohort._materialize(checked, changed["validation_started_at"], changed["admitted_at"], cohort._codes())
        self.paths["cohort"].write_bytes(cohort.canonical_bytes(changed))
        self.options["expected_cohort_sha256"] = self.digest("cohort")
        with self.assertRaisesRegex(ValueError, "overlaps an already imported source report"):
            self.accept()
        self.assertEqual(before, self.rows())

    def test_extra_attached_lineage_is_detected_on_replay(self):
        result = self.accept()
        repository.add_entity(self.connection, models.Entity("unexpected", models.EntityKind.FACILITY,
            "unexpected", ADMITTED, "Unexpected extra facility", result["run_id"]))
        self.connection.commit()
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "unexpected or altered lineage"):
            self.verify()
        self.assertEqual(before, self.rows())

    def test_cli_will_not_create_database(self):
        missing = self.root / "missing.sqlite"
        args = ["admit", "--database", str(missing), "--cohort", str(self.paths["cohort"])]
        args += [item for key, value in self.options.items() for item in ("--" + key.replace("_", "-"), str(value))]
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(1, cli.main(args))
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
