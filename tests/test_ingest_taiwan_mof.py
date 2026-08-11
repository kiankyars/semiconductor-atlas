from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from semiconductor_atlas.adapters.taiwan_mof_tax_registry import TAIWAN_MOF_FIELDS
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_taiwan_mof import (
    ENTITY_KEY_PREFIX,
    SOURCE_FAMILY_KEY,
    SOURCE_KEY,
    import_taiwan_mof_snapshot,
)
from semiconductor_atlas.repository import current_claims, validate_database
from semiconductor_atlas.taiwan_mof_snapshot import create_taiwan_mof_snapshot

from tests._taiwan_mof_fixtures import create_source_snapshots


RETRIEVED_AT = "2026-07-20T06:57:50Z"
LAST_MODIFIED = "Sun, 19 Jul 2026 21:12:27 GMT"
ACCEPTED_AT = "2026-07-20T07:00:00Z"


def _row(
    ubn: str,
    *,
    name: str = "允許半導體股份有限公司",
    head_office: str = "",
    organization_type: str = "股份有限公司",
    industry_code: str = "261100",
    industry_name: str = "積體電路製造",
) -> list[str]:
    row = {field: "" for field in TAIWAN_MOF_FIELDS}
    row.update(
        {
            "營業地址": "新竹市東區測試路1號",
            "統一編號": ubn,
            "總機構統一編號": head_office,
            "營業人名稱": name,
            "資本額": "SECRET_CAPITAL_NOT_IMPORTED",
            "設立日期": "1100101",
            "組織別名稱": organization_type,
            "使用統一發票": "SECRET_INVOICE_NOT_IMPORTED",
            "行業代號": industry_code,
            "名稱": industry_name,
        }
    )
    return [row[field] for field in TAIWAN_MOF_FIELDS]


def _archive(rows: list[list[str]], *, publisher_date: str = "20-JUL-26") -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow(TAIWAN_MOF_FIELDS)
    writer.writerow([publisher_date, *("" for _ in range(15))])
    writer.writerows(rows)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "BGMOPEN1.csv", b"\xef\xbb\xbf" + text.getvalue().encode("utf-8")
        )
    return stream.getvalue()


def _snapshot(
    root: Path,
    name: str,
    rows: list[list[str]],
    *,
    moenv: Path,
    factory: Path,
    publisher_date: str = "20-JUL-26",
    retrieved_at: str = RETRIEVED_AT,
    last_modified: str = LAST_MODIFIED,
):
    archive = root / f"{name}.zip"
    raw = _archive(rows, publisher_date=publisher_date)
    archive.write_bytes(raw)
    return create_taiwan_mof_snapshot(
        archive,
        root / name,
        moenv_snapshot_dir=moenv,
        factory_snapshot_dir=factory,
        retrieved_at=retrieved_at,
        upstream_last_modified=last_modified,
        upstream_content_type="application/zip",
        upstream_content_length=len(raw),
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


class TaiwanMOFImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.moenv, self.factory = create_source_snapshots(self.root)
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
        return import_taiwan_mof_snapshot(
            self.connection,
            snapshot,
            moenv_snapshot_dir=self.moenv,
            factory_snapshot_dir=self.factory,
            accepted_at=accepted_at,
            complete_refresh=complete_refresh,
        )

    def test_imports_tax_units_as_organizations_with_exact_claims_and_lineage(
        self,
    ) -> None:
        snapshot = _snapshot(
            self.root,
            "first",
            [
                _row("00123456"),
                _row(
                    "00888888",
                    name="允許分公司",
                    head_office="00123456",
                    organization_type="本國公司設立之分公司",
                ),
                _row("00777777", name="不在允許清單"),
            ],
            moenv=self.moenv,
            factory=self.factory,
        )
        result = self.ingest(snapshot)

        self.assertEqual(2, result.tax_units_imported)
        self.assertEqual(3, result.source_documents_created)
        entities = self.connection.execute(
            "SELECT kind, stable_key FROM entities ORDER BY stable_key"
        ).fetchall()
        self.assertEqual(
            [
                ("organization", f"{ENTITY_KEY_PREFIX}00123456"),
                ("organization", f"{ENTITY_KEY_PREFIX}00888888"),
            ],
            [tuple(row) for row in entities],
        )
        family = self.connection.execute(
            "SELECT stable_key FROM source_families"
        ).fetchone()
        source = self.connection.execute("SELECT stable_key FROM sources").fetchone()
        self.assertEqual(SOURCE_FAMILY_KEY, family[0])
        self.assertEqual(SOURCE_KEY, source[0])

        expected = {
            "organization.name": {"允許半導體股份有限公司", "允許分公司"},
            "address.street": {"新竹市東區測試路1號"},
            "organization.identifier.tw_ubn": {"00123456", "00888888"},
            "taiwan_mof_tax.head_office_unified_business_number": {"00123456"},
            "taiwan_mof_tax.established_date_raw": {"1100101"},
            "taiwan_mof_tax.organization_type": {
                "股份有限公司",
                "本國公司設立之分公司",
            },
            "taiwan_mof_tax.industry_activity": {
                '{"code":"261100","name":"積體電路製造"}'
            },
            "candidate_classification": {"semiconductor_tax_registration_candidate"},
        }
        for predicate, values in expected.items():
            with self.subTest(predicate=predicate):
                self.assertEqual(values, _scalar_values(self.connection, predicate))

        identifier_metadata = self.connection.execute(
            """
            SELECT metadata.scheme, metadata.normalized_value, metadata.jurisdiction
            FROM organization_identifier_claim_metadata AS metadata
            ORDER BY metadata.normalized_value
            """
        ).fetchall()
        self.assertEqual(
            [("TW_UBN", "00123456", "TW"), ("TW_UBN", "00888888", "TW")],
            [tuple(row) for row in identifier_metadata],
        )
        dependency = self.connection.execute(
            """
            SELECT source_series.predicate, evidence.source_document_id
            FROM claim_versions AS derived
            JOIN claim_series AS derived_series ON derived_series.id = derived.series_id
            JOIN claim_dependencies AS dependencies
              ON dependencies.claim_version_id = derived.id
            JOIN claim_versions AS source_claim
              ON source_claim.id = dependencies.depends_on_claim_version_id
            JOIN claim_series AS source_series ON source_series.id = source_claim.series_id
            JOIN claim_evidence AS evidence ON evidence.claim_version_id = derived.id
            WHERE derived_series.predicate = 'candidate_classification'
            ORDER BY derived.id
            """
        ).fetchall()
        self.assertEqual(2, len(dependency))
        self.assertTrue(
            all(
                row["predicate"] == "organization.identifier.tw_ubn"
                for row in dependency
            )
        )
        self.assertTrue(
            all(
                row["source_document_id"] == result.allowlist_document_id
                for row in dependency
            )
        )

        roles = {
            (row["source_document_id"], row["role"])
            for row in self.connection.execute(
                "SELECT source_document_id, role FROM ingestion_run_documents"
            )
        }
        self.assertEqual(
            {
                (result.matched_document_id, "primary"),
                (result.matched_document_id, "matched_derivative"),
                (result.matched_document_id, "source_record"),
                (result.allowlist_document_id, "allowlist"),
                (result.allowlist_document_id, "filter_input"),
                (result.raw_document_id, "raw_archive"),
            },
            roles,
        )
        database_dump = "\n".join(self.connection.iterdump())
        self.assertNotIn("SECRET_CAPITAL_NOT_IMPORTED", database_dump)
        self.assertNotIn("SECRET_INVOICE_NOT_IMPORTED", database_dump)
        predicates = {
            row[0]
            for row in self.connection.execute("SELECT predicate FROM claim_series")
        }
        for forbidden in (
            "facility",
            "owner",
            "operator",
            "capacity",
            "operation",
            "lifecycle",
            "invoice",
            "capital",
        ):
            self.assertFalse(any(forbidden in predicate for predicate in predicates))

        parameters = json.loads(
            self.connection.execute(
                "SELECT parameters_json FROM ingestion_runs"
            ).fetchone()[0]
        )
        self.assertEqual(snapshot.manifest_sha256, parameters["manifest_sha256"])
        self.assertEqual(snapshot.allowlist_sha256, parameters["allowlist"]["sha256"])
        self.assertEqual(
            snapshot.matched_sha256, parameters["matched_derivative"]["sha256"]
        )
        self.assertEqual(snapshot.raw_sha256, parameters["raw_archive"]["sha256"])
        self.assertIn("sources", parameters["allowlist"]["derivation"])
        self.assertIn("rights", parameters)
        self.assertIn("filter", parameters)
        self.assertEqual([], validate_database(self.connection))

    def test_identical_replay_is_read_only_and_rejects_identifier_metadata_tamper(
        self,
    ) -> None:
        snapshot = _snapshot(
            self.root,
            "replay",
            [_row("00123456")],
            moenv=self.moenv,
            factory=self.factory,
        )
        first = self.ingest(snapshot)
        self.connection.commit()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        replay = self.ingest(snapshot)
        self.connection.commit()
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(first.ingestion_run_id, replay.ingestion_run_id)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

        self.connection.execute(
            "DROP TRIGGER organization_identifier_claim_metadata_immutable_update"
        )
        self.connection.execute(
            "UPDATE organization_identifier_claim_metadata SET scheme = 'tampered'"
        )
        self.connection.commit()
        tampered = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "identifier metadata conflicts"):
            self.ingest(snapshot)
        self.connection.commit()
        self.assertEqual(
            tampered, hashlib.sha256(self.database.read_bytes()).hexdigest()
        )

    def test_commit_boundary_source_aware_recheck_rolls_back_database(self) -> None:
        snapshot = _snapshot(
            self.root,
            "commit-race",
            [_row("00123456")],
            moenv=self.moenv,
            factory=self.factory,
        )
        matched_path = snapshot.root / "taiwan-mof-active-tax-registrations.jsonl"
        real_validate = validate_database
        changed = False

        def mutate_after_validation(connection):
            nonlocal changed
            errors = real_validate(connection)
            if not changed:
                matched_path.write_bytes(matched_path.read_bytes() + b"tamper")
                changed = True
            return errors

        with (
            mock.patch(
                "semiconductor_atlas.ingest_taiwan_mof.validate_database",
                side_effect=mutate_after_validation,
            ),
            self.assertRaisesRegex(ValueError, "derivative hash or size mismatch"),
        ):
            self.ingest(snapshot)
        self.assertTrue(changed)
        for table in ("ingestion_runs", "source_records", "entities", "claim_versions"):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
            )

    def test_raw_only_refresh_reuses_acquired_derivative_documents(self) -> None:
        selected = _row("00123456")
        first = _snapshot(
            self.root,
            "raw-first",
            [selected, _row("00777777", name="第一個未選列")],
            moenv=self.moenv,
            factory=self.factory,
        )
        second = _snapshot(
            self.root,
            "raw-second",
            [selected, _row("00666666", name="不同的未選列")],
            moenv=self.moenv,
            factory=self.factory,
        )
        self.assertEqual(first.matched_sha256, second.matched_sha256)
        self.assertEqual(first.allowlist_sha256, second.allowlist_sha256)
        self.assertNotEqual(first.raw_sha256, second.raw_sha256)
        first_result = self.ingest(first)
        second_result = self.ingest(second, accepted_at="2026-07-20T07:05:00Z")
        self.assertEqual(
            first_result.matched_document_id, second_result.matched_document_id
        )
        self.assertEqual(
            first_result.allowlist_document_id, second_result.allowlist_document_id
        )
        self.assertNotEqual(first_result.raw_document_id, second_result.raw_document_id)
        self.assertEqual(0, second_result.claims_created)
        self.assertGreater(second_result.unchanged_claims_reused, 0)
        self.assertEqual(
            4,
            self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[
                0
            ],
        )

    def test_complete_refresh_closes_only_source_intervals_while_partial_retains_omissions(
        self,
    ) -> None:
        first = _snapshot(
            self.root,
            "day-one",
            [_row("00123456"), _row("00888888", name="第二稅籍單位")],
            moenv=self.moenv,
            factory=self.factory,
        )
        second = _snapshot(
            self.root,
            "day-two",
            [_row("00123456")],
            moenv=self.moenv,
            factory=self.factory,
            publisher_date="21-JUL-26",
            retrieved_at="2026-07-21T06:57:50Z",
            last_modified="Mon, 20 Jul 2026 21:12:27 GMT",
        )
        self.ingest(first)
        partial = self.ingest(
            second,
            accepted_at="2026-07-21T07:00:00Z",
            complete_refresh=False,
        )
        self.assertEqual(0, partial.prior_open_claims_closed_or_corrected)
        current = current_claims(
            self.connection,
            as_of="2026-07-21",
            recorded_at="2026-07-21T07:00:00Z",
        )
        self.assertEqual(2, len({row["subject_entity_id"] for row in current}))

        self.connection.close()
        self.database = self.root / "full.sqlite"
        self.connection, _ = initialize(self.database)
        self.ingest(first)
        full = self.ingest(
            second,
            accepted_at="2026-07-21T07:00:00Z",
            complete_refresh=True,
        )
        self.assertEqual(1, full.tax_units_no_longer_matched)
        self.assertGreater(full.prior_open_claims_closed_or_corrected, 0)
        current = current_claims(
            self.connection,
            as_of="2026-07-21",
            recorded_at="2026-07-21T07:00:00Z",
        )
        self.assertEqual(1, len({row["subject_entity_id"] for row in current}))
        notes = "\n".join(
            str(row[0])
            for row in self.connection.execute(
                "SELECT notes FROM claim_versions WHERE valid_to IS NOT NULL"
            )
        )
        self.assertIn("does not assert legal closure", notes)
        self.assertNotIn("legal_entity_closed", notes)
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
