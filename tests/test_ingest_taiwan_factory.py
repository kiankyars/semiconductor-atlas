from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from semiconductor_atlas.adapters.taiwan_factory_registry import TAIWAN_FACTORY_FIELDS
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_taiwan_factory import (
    ENTITY_KEY_PREFIX,
    import_taiwan_factory_candidates,
)
from semiconductor_atlas.repository import current_claims, validate_database
from semiconductor_atlas.taiwan_factory_snapshot import create_taiwan_factory_snapshot


RETRIEVED_AT = "2026-07-20T08:20:05Z"
SOURCE_UPDATED_HEADER = "Mon, 20 Jul 2026 03:37:05 GMT"
ACCEPTED_AT = "2026-07-20T09:00:00Z"


def _row(registration_number: str = "94A00001", **overrides: str) -> dict[str, str]:
    row = dict.fromkeys(TAIWAN_FACTORY_FIELDS, "")
    row.update(
        {
            "工廠名稱": "測試半導體股份有限公司一廠",
            "工廠登記編號": registration_number,
            "工廠設立許可案號": "TEST-APPROVAL-01",
            "工廠地址": "新竹市東區測試路1號",
            "工廠市鎮鄉村里": "新竹市東區",
            "工廠負責人姓名": "不應進入資料庫的人名",
            "統一編號": "22099131",
            "工廠組織型態": "股份有限公司",
            "工廠設立核准日期": "0980101000000",
            "工廠登記核准日期": "1090611000000",
            "工廠登記狀態": "生產中",
            "產業類別": "26電子零組件製造業\n",
            "主要產品": "261半導體\n269其他電子零組件\n",
        }
    )
    row.update(overrides)
    return row


def _archive_bytes(rows: list[dict[str, str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow(TAIWAN_FACTORY_FIELDS)
    for row in rows:
        writer.writerow([row[field] for field in TAIWAN_FACTORY_FIELDS])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("11506.csv", b"\xef\xbb\xbf" + text.getvalue().encode("utf-8"))
    return stream.getvalue()


def _snapshot(
    root: Path,
    name: str,
    rows: list[dict[str, str]],
    *,
    retrieved_at: str = RETRIEVED_AT,
    source_updated_header: str = SOURCE_UPDATED_HEADER,
):
    archive = root / f"{name}.zip"
    archive.write_bytes(_archive_bytes(rows))
    return create_taiwan_factory_snapshot(
        archive,
        root / name,
        retrieved_at=retrieved_at,
        upstream_last_modified=source_updated_header,
        upstream_content_type="application/zip",
        upstream_content_length=archive.stat().st_size,
    )


def _scalar_values(connection, predicate: str) -> set[str]:
    return {
        str(row["text_value"])
        for row in connection.execute(
            """
            SELECT scalar.text_value
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            JOIN scalar_values AS scalar ON scalar.claim_version_id = versions.id
            WHERE series.predicate = ?
              AND versions.valid_to IS NULL
              AND versions.superseded_at IS NULL
            """,
            (predicate,),
        )
    }


class TaiwanFactoryImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "atlas.sqlite"
        self.connection, _ = initialize(self.database)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def ingest(
        self,
        snapshot,
        *,
        accepted_at: str = ACCEPTED_AT,
        complete_refresh: bool = True,
    ):
        return import_taiwan_factory_candidates(
            self.connection,
            snapshot,
            accepted_at=accepted_at,
            complete_refresh=complete_refresh,
        )

    def test_imports_source_native_claims_and_only_exact_261_derivation(self) -> None:
        snapshot = _snapshot(self.root, "first", [_row()])
        result = self.ingest(snapshot)

        self.assertEqual(1, result.facilities_imported)
        entity = self.connection.execute("SELECT * FROM entities").fetchone()
        self.assertEqual("facility", entity["kind"])
        self.assertEqual(f"{ENTITY_KEY_PREFIX}94A00001", entity["stable_key"])
        record = self.connection.execute("SELECT * FROM source_records").fetchone()
        self.assertEqual(entity["stable_key"], record["source_record_key"])
        payload = json.loads(record["payload_json"])
        self.assertNotIn("responsible", json.dumps(payload, ensure_ascii=False).lower())
        self.assertNotIn("不應進入資料庫的人名", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(
            "不應進入資料庫的人名", "\n".join(self.connection.iterdump())
        )

        expected = {
            "name": {"測試半導體股份有限公司一廠"},
            "address.street": {"新竹市東區測試路1號"},
            "address.administrative_area": {"新竹市東區"},
            "taiwan_factory_registry.factory_registration_number": {"94A00001"},
            "taiwan_factory_registry.establishment_approval_case_number": {
                "TEST-APPROVAL-01"
            },
            "taiwan_factory_registry.unified_business_number": {"22099131"},
            "taiwan_factory_registry.organization_type": {"股份有限公司"},
            "taiwan_factory_registry.establishment_approved_at_raw": {
                "0980101000000"
            },
            "taiwan_factory_registry.registration_approved_at_raw": {
                "1090611000000"
            },
            "taiwan_factory_registry.registration_status": {"生產中"},
            "taiwan_factory_registry.industry_category_token": {
                "26電子零組件製造業"
            },
            "taiwan_factory_registry.principal_product_token": {
                "261半導體",
                "269其他電子零組件",
            },
            "candidate_classification": {"semiconductor_facility_candidate"},
        }
        for predicate, values in expected.items():
            with self.subTest(predicate=predicate):
                self.assertEqual(values, _scalar_values(self.connection, predicate))

        lineage = self.connection.execute(
            """
            SELECT source_series.predicate, source_scalar.text_value,
                   derived.claim_kind, derived.method
            FROM claim_versions AS derived
            JOIN claim_series AS derived_series ON derived_series.id = derived.series_id
            JOIN claim_dependencies AS dependencies
              ON dependencies.claim_version_id = derived.id
            JOIN claim_versions AS source_claim
              ON source_claim.id = dependencies.depends_on_claim_version_id
            JOIN claim_series AS source_series ON source_series.id = source_claim.series_id
            JOIN scalar_values AS source_scalar ON source_scalar.claim_version_id = source_claim.id
            WHERE derived_series.predicate = 'candidate_classification'
            """
        ).fetchall()
        self.assertEqual(1, len(lineage))
        self.assertEqual(
            (
                "taiwan_factory_registry.principal_product_token",
                "261半導體",
                "derived_estimate",
                "taiwan_factory_registry_exact_principal_product_261_candidate_v1",
            ),
            tuple(lineage[0]),
        )
        predicates = {
            row[0] for row in self.connection.execute("SELECT predicate FROM claim_series")
        }
        for forbidden in (
            "lifecycle_state",
            "operating_status",
            "owner",
            "operator",
            "capacity",
            "utilization",
            "yield",
            "output",
        ):
            self.assertFalse(any(forbidden in predicate for predicate in predicates))

        documents = self.connection.execute(
            "SELECT media_type, metadata_json FROM source_documents ORDER BY media_type"
        ).fetchall()
        self.assertEqual(
            {"application/x-ndjson", "application/zip"},
            {row["media_type"] for row in documents},
        )
        parameters = json.loads(
            self.connection.execute(
                "SELECT parameters_json FROM ingestion_runs"
            ).fetchone()[0]
        )
        self.assertEqual(snapshot.source_updated_at, parameters["source_updated_at"])
        self.assertEqual(snapshot.retrieved_at, parameters["source_retrieved_at"])
        self.assertEqual(ACCEPTED_AT, parameters["accepted_at"])
        self.assertEqual(snapshot.manifest_sha256, parameters["manifest_sha256"])
        self.assertEqual([], validate_database(self.connection))

    def test_identical_replay_is_read_only_and_tamper_is_rejected(self) -> None:
        snapshot = _snapshot(self.root, "first", [_row()])
        first = self.ingest(snapshot)
        self.connection.commit()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()

        replay = self.ingest(snapshot)
        self.connection.commit()
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(first.ingestion_run_id, replay.ingestion_run_id)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

        classification_id = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
            """
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER claim_versions_immutable_delete")
        self.connection.execute("DROP TRIGGER claim_dependencies_immutable_delete")
        self.connection.execute("DROP TRIGGER claim_values_immutable_delete")
        self.connection.execute("DROP TRIGGER scalar_values_immutable_delete")
        self.connection.execute(
            "DELETE FROM claim_versions WHERE id = ?", (classification_id,)
        )
        self.connection.commit()
        tampered = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "missing expected claim series"):
            self.ingest(snapshot)
        self.connection.commit()
        self.assertEqual(tampered, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_raw_only_refresh_reuses_byte_scoped_candidate_document_and_replays(self) -> None:
        selected = _row()
        first = _snapshot(
            self.root,
            "raw-first",
            [
                selected,
                _row(
                    "94A00002",
                    **{
                        "工廠名稱": "第一個未選列",
                        "統一編號": "22099132",
                        "主要產品": "263印刷電路板\n",
                    },
                ),
            ],
        )
        second = _snapshot(
            self.root,
            "raw-second",
            [
                selected,
                _row(
                    "94A00003",
                    **{
                        "工廠名稱": "不同的未選列",
                        "統一編號": "22099133",
                        "主要產品": "264光電材料及元件\n",
                    },
                ),
            ],
        )
        self.assertEqual(first.candidate_sha256, second.candidate_sha256)
        self.assertNotEqual(first.raw_sha256, second.raw_sha256)
        first_result = self.ingest(first)
        second_result = self.ingest(
            second,
            accepted_at="2026-07-20T09:05:00Z",
        )
        self.assertEqual(
            first_result.candidate_document_id,
            second_result.candidate_document_id,
        )
        self.assertNotEqual(first_result.raw_document_id, second_result.raw_document_id)
        self.assertEqual(0, second_result.claims_created)
        self.assertGreater(second_result.unchanged_claims_reused, 0)
        self.assertEqual(
            3,
            self.connection.execute(
                "SELECT COUNT(*) FROM source_documents"
            ).fetchone()[0],
        )
        candidate_metadata = json.loads(
            self.connection.execute(
                "SELECT metadata_json FROM source_documents WHERE id = ?",
                (second_result.candidate_document_id,),
            ).fetchone()[0]
        )
        for run_scoped in (
            "manifest_sha256",
            "filter_version",
            "privacy",
            "rights",
            "raw_archive_sha256",
            "raw_matching_row_count",
            "source_updated_at",
            "retrieval_timestamp_basis",
        ):
            self.assertNotIn(run_scoped, candidate_metadata)
        replay = self.ingest(
            second,
            accepted_at="2026-07-20T09:05:00Z",
        )
        self.assertTrue(replay.replayed_existing_run)

    def test_replay_rejects_tampered_family_source_and_entity_fields(self) -> None:
        snapshot = _snapshot(self.root, "tampered-identities", [_row()])
        result = self.ingest(snapshot)
        family_id = result.source_family_id
        source_id = result.source_id
        entity_id = result.facility_entity_ids[0]
        self.connection.execute("DROP TRIGGER source_families_immutable_update")
        self.connection.execute("DROP TRIGGER sources_immutable_update")
        self.connection.execute("DROP TRIGGER entities_immutable_update")

        cases = (
            (
                "UPDATE source_families SET name = 'tampered' WHERE id = ?",
                family_id,
                "source family conflicts",
                "UPDATE source_families SET name = 'Taiwan IDA Registered Factory Registry' WHERE id = ?",
            ),
            (
                "UPDATE sources SET publisher = 'tampered' WHERE id = ?",
                source_id,
                "source conflicts",
                "UPDATE sources SET publisher = 'Taiwan Ministry of Economic Affairs, Industrial Development Administration' WHERE id = ?",
            ),
            (
                "UPDATE entities SET display_name = 'tampered' WHERE id = ?",
                entity_id,
                "facility entity .* conflicts",
                "UPDATE entities SET display_name = '測試半導體股份有限公司一廠' WHERE id = ?",
            ),
        )
        for mutate_sql, identifier, error, restore_sql in cases:
            with self.subTest(error=error):
                self.connection.execute(mutate_sql, (identifier,))
                self.connection.commit()
                before = "\n".join(self.connection.iterdump())
                with self.assertRaisesRegex(ValueError, error):
                    self.ingest(snapshot)
                self.assertEqual(before, "\n".join(self.connection.iterdump()))
                self.connection.execute(restore_sql, (identifier,))
                self.connection.commit()

    def test_reused_claim_replay_rejects_tampered_origin_evidence(self) -> None:
        first = _snapshot(self.root, "evidence-first", [_row()])
        self.ingest(first)
        second = _snapshot(
            self.root,
            "evidence-second",
            [
                _row(),
                _row(
                    "94A00002",
                    **{
                        "統一編號": "22099132",
                        "主要產品": "263印刷電路板\n",
                    },
                ),
            ],
        )
        second_result = self.ingest(
            second,
            accepted_at="2026-07-20T09:05:00Z",
        )
        evidence = self.connection.execute(
            """
            SELECT evidence.id, evidence.excerpt
            FROM claim_evidence AS evidence
            JOIN claim_versions AS versions ON versions.id = evidence.claim_version_id
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'name'
            LIMIT 1
            """
        ).fetchone()
        self.connection.execute("DROP TRIGGER claim_evidence_immutable_update")
        self.connection.execute(
            "UPDATE claim_evidence SET excerpt = 'tampered' WHERE id = ?",
            (evidence["id"],),
        )
        self.connection.commit()
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "reused claim .*immutable lineage"):
            self.ingest(second, accepted_at="2026-07-20T09:05:00Z")
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        self.assertFalse(second_result.replayed_existing_run)

    def test_later_refresh_rejects_tampered_existing_entity_and_rolls_back(self) -> None:
        first = _snapshot(self.root, "entity-day-one", [_row()])
        self.ingest(first)
        entity_id = self.connection.execute("SELECT id FROM entities").fetchone()[0]
        self.connection.execute("DROP TRIGGER entities_immutable_update")
        self.connection.execute(
            "UPDATE entities SET display_name = 'tampered' WHERE id = ?", (entity_id,)
        )
        self.connection.commit()
        second = _snapshot(
            self.root,
            "entity-day-two",
            [_row()],
            retrieved_at="2026-07-21T08:20:05Z",
            source_updated_header="Tue, 21 Jul 2026 03:37:05 GMT",
        )
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "facility entity .* conflicts"):
            self.ingest(second, accepted_at="2026-07-21T09:00:00Z")
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        self.assertEqual(
            1,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0],
        )

    def test_later_refresh_rejects_tampered_prior_evidence_and_rolls_back(self) -> None:
        first = _snapshot(self.root, "evidence-day-one", [_row()])
        self.ingest(first)
        evidence_id = self.connection.execute(
            """
            SELECT evidence.id
            FROM claim_evidence AS evidence
            JOIN claim_versions AS versions ON versions.id = evidence.claim_version_id
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'name'
            """
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER claim_evidence_immutable_update")
        self.connection.execute(
            "UPDATE claim_evidence SET excerpt = 'tampered' WHERE id = ?",
            (evidence_id,),
        )
        self.connection.commit()
        second = _snapshot(
            self.root,
            "evidence-day-two",
            [_row()],
            retrieved_at="2026-07-21T08:20:05Z",
            source_updated_header="Tue, 21 Jul 2026 03:37:05 GMT",
        )
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "prior claim .*immutable lineage"):
            self.ingest(second, accepted_at="2026-07-21T09:00:00Z")
        self.assertEqual(before, "\n".join(self.connection.iterdump()))
        self.assertEqual(
            1,
            self.connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0],
        )

    def test_closure_replay_checks_notes_evidence_and_dependencies_exactly(self) -> None:
        omitted = _row(
            "94A00002",
            **{"工廠名稱": "第二半導體廠", "統一編號": "22099132"},
        )
        first = _snapshot(self.root, "closure-first", [_row(), omitted])
        self.ingest(first)
        second = _snapshot(
            self.root,
            "closure-second",
            [_row()],
            retrieved_at="2026-07-21T08:20:05Z",
            source_updated_header="Tue, 21 Jul 2026 03:37:05 GMT",
        )
        second_result = self.ingest(
            second,
            accepted_at="2026-07-21T09:00:00Z",
        )
        source_closure = self.connection.execute(
            """
            SELECT versions.id, versions.notes
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE versions.created_by_run_id = ?
              AND versions.valid_to IS NOT NULL
              AND series.predicate = 'name'
            LIMIT 1
            """,
            (second_result.ingestion_run_id,),
        ).fetchone()
        context_evidence = self.connection.execute(
            """
            SELECT id, excerpt FROM claim_evidence
            WHERE claim_version_id = ? AND role = 'context'
            """,
            (source_closure["id"],),
        ).fetchone()
        derived_closure = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE versions.created_by_run_id = ?
              AND versions.valid_to IS NOT NULL
              AND series.predicate = 'candidate_classification'
            """,
            (second_result.ingestion_run_id,),
        ).fetchone()
        dependency = self.connection.execute(
            """
            SELECT depends_on_claim_version_id, dependency_kind
            FROM claim_dependencies WHERE claim_version_id = ?
            """,
            (derived_closure["id"],),
        ).fetchone()
        replacement_dependency = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE versions.created_by_run_id = ?
              AND versions.valid_to IS NOT NULL
              AND series.predicate = 'name'
            """,
            (second_result.ingestion_run_id,),
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER claim_versions_content_immutable")
        self.connection.execute("DROP TRIGGER claim_evidence_immutable_update")
        self.connection.execute("DROP TRIGGER claim_dependencies_immutable_update")

        def rejects_exact_closure() -> None:
            self.connection.commit()
            before = "\n".join(self.connection.iterdump())
            with self.assertRaisesRegex(ValueError, "closure claim .* conflicts"):
                self.ingest(second, accepted_at="2026-07-21T09:00:00Z")
            self.assertEqual(before, "\n".join(self.connection.iterdump()))

        self.connection.execute(
            "UPDATE claim_versions SET notes = 'tampered' WHERE id = ?",
            (source_closure["id"],),
        )
        rejects_exact_closure()
        self.connection.execute(
            "UPDATE claim_versions SET notes = ? WHERE id = ?",
            (source_closure["notes"], source_closure["id"]),
        )

        self.connection.execute(
            "UPDATE claim_evidence SET excerpt = 'tampered' WHERE id = ?",
            (context_evidence["id"],),
        )
        rejects_exact_closure()
        self.connection.execute(
            "UPDATE claim_evidence SET excerpt = ? WHERE id = ?",
            (context_evidence["excerpt"], context_evidence["id"]),
        )

        self.connection.execute(
            """
            UPDATE claim_dependencies
            SET depends_on_claim_version_id = ?
            WHERE claim_version_id = ? AND depends_on_claim_version_id = ?
            """,
            (
                replacement_dependency,
                derived_closure["id"],
                dependency["depends_on_claim_version_id"],
            ),
        )
        rejects_exact_closure()
        self.connection.execute(
            """
            UPDATE claim_dependencies
            SET depends_on_claim_version_id = ?
            WHERE claim_version_id = ? AND depends_on_claim_version_id = ?
            """,
            (
                dependency["depends_on_claim_version_id"],
                derived_closure["id"],
                replacement_dependency,
            ),
        )
        self.connection.commit()

    def test_complete_refresh_closes_omissions_but_partial_refresh_does_not(self) -> None:
        initial_rows = [
            _row("94A00001"),
            _row(
                "94A00002",
                **{
                    "工廠名稱": "第二半導體廠",
                    "統一編號": "22099132",
                },
            ),
        ]
        first = _snapshot(self.root, "first", initial_rows)
        self.ingest(first)
        second = _snapshot(
            self.root,
            "second",
            [initial_rows[0]],
            retrieved_at="2026-07-21T08:20:05Z",
            source_updated_header="Tue, 21 Jul 2026 03:37:05 GMT",
        )
        partial = self.ingest(
            second,
            accepted_at="2026-07-21T09:00:00Z",
            complete_refresh=False,
        )
        self.assertEqual(0, partial.prior_open_claims_closed_or_corrected)
        current = current_claims(
            self.connection,
            as_of="2026-07-21",
            recorded_at="2026-07-21T09:00:00Z",
        )
        self.assertEqual(2, len({row["subject_entity_id"] for row in current}))

        # A separate database demonstrates full same-filter nonselection closure.
        self.connection.close()
        self.database = self.root / "full.sqlite"
        self.connection, _ = initialize(self.database)
        self.ingest(first)
        full = self.ingest(
            second,
            accepted_at="2026-07-21T09:00:00Z",
            complete_refresh=True,
        )
        self.assertEqual(1, full.candidate_entities_no_longer_selected)
        self.assertGreater(full.prior_open_claims_closed_or_corrected, 0)
        current = current_claims(
            self.connection,
            as_of="2026-07-21",
            recorded_at="2026-07-21T09:00:00Z",
        )
        self.assertEqual(1, len({row["subject_entity_id"] for row in current}))
        closure_notes = "\n".join(
            str(row[0])
            for row in self.connection.execute(
                "SELECT notes FROM claim_versions WHERE valid_to IS NOT NULL"
            )
        )
        self.assertIn("does not assert real-world closure", closure_notes)
        self.assertEqual([], validate_database(self.connection))
        self.connection.commit()
        before_replay = hashlib.sha256(self.database.read_bytes()).hexdigest()
        replay = self.ingest(
            second,
            accepted_at="2026-07-21T09:00:00Z",
            complete_refresh=True,
        )
        self.connection.commit()
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(
            before_replay, hashlib.sha256(self.database.read_bytes()).hexdigest()
        )


if __name__ == "__main__":
    unittest.main()
