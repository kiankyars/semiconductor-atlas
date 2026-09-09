"""Synthetic scope-gate and installed-command routing tests, not source evidence."""
import contextlib
import hashlib
import io
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from semiconductor_atlas import analytics, cli, forecast_scope
from semiconductor_atlas.models import CapacityBasis, MilestoneStatus
from tests import test_analytics as fixtures

RECORDED_AT = fixtures.RECORDED_AT


class ForecastScopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.connection = self.fixture.connection
        self.milestone = self.fixture._milestone(MilestoneStatus.EXPECTED)
        self.capacity = self.fixture._capacity(name="phase-two-capacity", basis=CapacityBasis.ANNOUNCED)
        self.connection.commit()

    def forecast(self, **kwargs):
        return analytics.forecast_current_capacity(self.connection, as_of="2026-07-17",
            recorded_at=RECORDED_AT, forecast_start=date(2026, 7, 1), **kwargs)

    def decision(self, status="accepted", capacity=None):
        return {"capacity_claim_id": (capacity or self.capacity).id,
            "milestone_claim_id": self.milestone.id, "project_scope": "Synthetic campus",
            "phase_scope": "Explicitly reviewed synthetic phase", "event_type": "production_start",
            "decision": status, "rationale": "Explicit synthetic semantic decision; not a shared-document inference."}

    def review(self, decisions=None):
        with mock.patch.object(forecast_scope, "_now", return_value=RECORDED_AT):
            result = forecast_scope.create_scope_review(self.connection, as_of="2026-07-17",
                recorded_at=RECORDED_AT, reviewer="Synthetic reviewer",
                decisions=decisions or [self.decision()])
        raw = forecast_scope.canonical_bytes(result)
        return {"scope_review": raw, "scope_review_sha256": hashlib.sha256(raw).hexdigest()}

    def test_shared_document_without_scope_attestation_does_not_emit_ramp(self):
        selection = self.forecast()
        self.assertEqual(0, len(selection))
        self.assertEqual("capacity_event_scope_unreviewed", selection.exclusions[0].reason)
        self.assertEqual(self.capacity.id, selection.exclusions[0].capacity_claim_id)
        self.assertEqual((self.milestone.id,), selection.exclusions[0].related_claim_ids)
        self.assertEqual((), selection.pairings)

    def test_direct_dependency_does_not_substitute_for_semantic_review(self):
        direct = self.fixture._capacity(name="direct-capacity", basis=CapacityBasis.ANNOUNCED,
            milestone=self.milestone)
        result = self.forecast()
        self.assertEqual(0, len(result))
        self.assertEqual({self.capacity.id, direct.id}, {x.capacity_claim_id for x in result.exclusions})
        self.assertEqual({"capacity_event_scope_unreviewed"}, {x.reason for x in result.exclusions})

    def test_deferred_pair_preserves_capacity_in_exclusions_not_zero_points(self):
        before = self.connection.total_changes
        decision = self.decision("deferred")
        decision["rationale"] = "Capacity is Phase Two; retained timing is Phase One. Correspondence unresolved."
        result = self.forecast(**self.review([decision]))
        self.assertEqual(0, len(result))
        self.assertEqual("capacity_event_scope_deferred", result.exclusions[0].reason)
        self.assertEqual(decision["rationale"], result.exclusions[0].detail)
        self.assertEqual(before, self.connection.total_changes)
        self.assertEqual(100, self.connection.execute("SELECT base FROM capacity_values WHERE claim_version_id=?",
            (self.capacity.id,)).fetchone()[0])

    def test_accepted_scope_is_hash_visible_in_every_forecast_export(self):
        reviewed = self.review()
        result = self.forecast(**reviewed)
        self.assertEqual(1, len(result))
        self.assertEqual(20, len(result[0].points))
        self.assertEqual("reviewed_scope_with_same_entity_shared_transitive_source_document", result.pairings[0].method)
        for raw in (analytics.forecast_csv(result), analytics.forecast_jsonl(result), analytics.forecast_summary(result)):
            self.assertIn(reviewed["scope_review_sha256"].encode(), raw)
            self.assertIn(b"Synthetic campus", raw)
        self.assertEqual((), result.exclusions)

    def test_partial_review_cannot_hide_unreviewed_capacity(self):
        second = self.fixture._capacity(name="another-phase-capacity", basis=CapacityBasis.ANNOUNCED)
        result = self.forecast(**self.review())
        self.assertEqual(1, len(result))
        self.assertEqual(second.id, result.exclusions[0].capacity_claim_id)
        self.assertEqual("capacity_event_scope_unreviewed", result.exclusions[0].reason)

    def test_scope_file_and_trusted_hash_are_both_required(self):
        reviewed = self.review()
        for kwargs in ({"scope_review": reviewed["scope_review"]},
                       {"scope_review_sha256": reviewed["scope_review_sha256"]}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "supplied together"):
                self.forecast(**kwargs)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.forecast(scope_review=reviewed["scope_review"], scope_review_sha256="0" * 64)

    def test_cli_retains_new_review_and_requires_hash_for_release(self):
        root = Path(self.fixture.temporary.name).resolve()
        decisions_path, review_path = root / "decisions.json", root / "scope-review.json"
        decisions_path.write_bytes(forecast_scope.canonical_bytes([self.decision("deferred")]))
        database_path = Path(self.connection.execute("PRAGMA database_list").fetchone()[2])
        before = hashlib.sha256(database_path.read_bytes()).hexdigest()
        command = ["review-forecast-scope", "--database", str(database_path),
            "--decisions", str(decisions_path), "--as-of", "2026-07-17",
            "--recorded-at", RECORDED_AT, "--reviewer", "Synthetic CLI reviewer", "--output", str(review_path)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(forecast_scope, "_now", return_value=RECORDED_AT):
            self.assertEqual(0, cli.main(command))
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["sha256"], hashlib.sha256(review_path.read_bytes()).hexdigest())
        release_path = root / "release"
        release = ["release", "--database", str(database_path), "--as-of", "2026-07-17",
            "--recorded-at", RECORDED_AT, "--forecast-start", "2026-07-01", "--output", str(release_path),
            "--forecast-scope-review", str(review_path)]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, cli.main(release))
        self.assertFalse(release_path.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(release + ["--forecast-scope-review-sha256", receipt["sha256"]]))
        self.assertEqual(review_path.read_bytes(), (release_path / "experimental_capacity_scope_review.json").read_bytes())
        self.assertEqual(before, hashlib.sha256(database_path.read_bytes()).hexdigest())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, cli.main(command))
        self.assertEqual(receipt["sha256"], hashlib.sha256(review_path.read_bytes()).hexdigest())

    def test_cli_database_aliases_cannot_target_database_or_missing_sidecars(self):
        root = Path(self.fixture.temporary.name).resolve()
        database_path = Path(self.connection.execute("PRAGMA database_list").fetchone()[2]).resolve()
        decisions_path = root / "decisions.json"
        decisions_path.write_bytes(forecast_scope.canonical_bytes([self.decision("deferred")]))
        via = root / "via"
        via.mkdir()
        alias = root / "alias"
        alias.symlink_to(database_path.parent, target_is_directory=True)
        aliases = (database_path, via / ".." / database_path.name, alias / database_path.name)
        before = database_path.read_bytes()
        for database_alias in aliases:
            for suffix in ("", "-wal", "-shm", "-journal"):
                target = Path(str(database_path) + suffix)
                retained = target.read_bytes() if target.exists() else None
                with self.subTest(database=database_alias, suffix=suffix):
                    error = io.StringIO()
                    with contextlib.redirect_stderr(error):
                        code = cli.main(["review-forecast-scope", "--database", str(database_alias),
                            "--decisions", str(decisions_path), "--as-of", "2026-07-17",
                            "--recorded-at", RECORDED_AT, "--reviewer", "Synthetic reviewer",
                            "--output", str(target)])
                    self.assertEqual(1, code)
                    self.assertIn("database or a SQLite sidecar", error.getvalue())
                    if retained is None:
                        self.assertFalse(target.exists())
                    else:
                        self.assertEqual(retained, target.read_bytes())
        self.assertEqual(before, database_path.read_bytes())

    def test_cli_alias_protection_preserves_live_wal_and_shm_bytes(self):
        root = Path(self.fixture.temporary.name).resolve()
        database_path = Path(self.connection.execute("PRAGMA database_list").fetchone()[2]).resolve()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE scope_safety_sentinel (value TEXT)")
        self.connection.execute("INSERT INTO scope_safety_sentinel VALUES ('preserved')")
        self.connection.commit()
        decisions_path = root / "decisions.json"
        decisions_path.write_bytes(forecast_scope.canonical_bytes([self.decision("deferred")]))
        via = root / "via"
        via.mkdir()
        before = {suffix: Path(str(database_path) + suffix).read_bytes() for suffix in ("", "-wal", "-shm")}
        for suffix in ("-wal", "-shm"):
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["review-forecast-scope", "--database", str(via / ".." / database_path.name),
                    "--decisions", str(decisions_path), "--as-of", "2026-07-17",
                    "--recorded-at", RECORDED_AT, "--reviewer", "Synthetic reviewer",
                    "--output", str(Path(str(database_path) + suffix))])
            self.assertEqual(1, code)
            for protected_suffix, raw in before.items():
                self.assertEqual(raw, Path(str(database_path) + protected_suffix).read_bytes())
        self.assertEqual("preserved", self.connection.execute("SELECT value FROM scope_safety_sentinel").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
