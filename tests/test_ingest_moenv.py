from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from semiconductor_atlas import ingest_moenv as moenv_ingest
from semiconductor_atlas.adapters.moenv_ems import MOENV_FIELDS, MOENV_INDUSTRY_LABELS
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_moenv import import_moenv_ems_candidates
from semiconductor_atlas.moenv_snapshot import (
    MOENV_FULL_PACKAGE_URL,
    MOENV_SCOPE,
    create_moenv_snapshot,
)
from semiconductor_atlas.repository import current_claims, validate_database


JSON_MEMBER = "環境保護許可管理系統(暨解除列管)對象基本資料.json"
DATASET_UPDATED_AT_BASIS = "official_dataset_page_displayed_asia_taipei"
REQUEST_BODY = {
    "rid": ["56ea8602-c7d5-4c27-ac20-236e51e889c4"],
    "download_type": "json",
    "pid": "816037bc-53f1-4951-b32d-8e607b948344",
}


def _row(
    emsno: str,
    *,
    industry_id: str = "2611",
    name: str | None = None,
    regulated: bool = True,
    longitude: str = "121.012300",
    latitude: str = "24.800400",
    **overrides: str | None,
) -> dict[str, str | None]:
    row: dict[str, str | None] = {field: "" for field in MOENV_FIELDS}
    row.update(
        {
            "emsno": emsno,
            "facilityname": name or f"測試半導體 {emsno}",
            "uniformno": "00123456",
            "county": "新竹市",
            "township": "東區",
            "facilityaddress": "新竹市東區測試路1號",
            "industryareaname": "新竹科學園區",
            "industryid": industry_id,
            "industryname": MOENV_INDUSTRY_LABELS[industry_id],
            "twd97tm2x": "250000.125",
            "twd97tm2y": "2740000.500",
            "wgs84lon": longitude,
            "wgs84lat": latitude,
            "isair": "1" if regulated else "0",
            "iswater": "0",
            "iswaste": "0",
            "istoxic": "0",
            "issoil": "0",
            "airreleasedate": "",
            "waterreleasedate": "",
            "wastereleasedate": "",
            "toxicreleasedate": "",
            "soilreleasedate": "",
            "industrygroup": "261",
            "admino": None,
            "facno": None,
        }
    )
    row.update(overrides)
    return row


def _archive_bytes(rows: list[dict[str, str | None]]) -> bytes:
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    publisher_md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    hash_raw = f"{JSON_MEMBER}：{publisher_md5}，演算法：MD5\n".encode("utf-8")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(JSON_MEMBER, raw)
        archive.writestr("hash.txt", hash_raw)
    stream.seek(0)
    return stream.read()


def _scalar_values(connection, predicate: str) -> set[object]:
    rows = connection.execute(
        """
        SELECT scalar.scalar_type, scalar.text_value, scalar.boolean_value,
               scalar.number_value, scalar.integer_value
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN scalar_values AS scalar ON scalar.claim_version_id = versions.id
        WHERE series.predicate = ?
          AND versions.valid_to IS NULL
          AND versions.superseded_at IS NULL
        """,
        (predicate,),
    )
    values: set[object] = set()
    for row in rows:
        if row["scalar_type"] == "boolean":
            values.add(bool(row["boolean_value"]))
        elif row["scalar_type"] == "number":
            values.add(float(row["number_value"]))
        elif row["scalar_type"] == "integer":
            values.add(int(row["integer_value"]))
        else:
            values.add(row["text_value"])
    return values


class MOENVImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "atlas.sqlite"
        self.connection, _ = initialize(self.database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def snapshot(
        self,
        name: str,
        rows: list[dict[str, str | None]],
        *,
        retrieved_at: str = "2026-07-20T06:00:00Z",
        dataset_updated_at: str = "2026-07-19T23:15:13Z",
    ):
        archive_path = self.root / f"{name}.zip"
        archive_path.write_bytes(_archive_bytes(rows))
        return create_moenv_snapshot(
            archive_path,
            self.root / name,
            retrieved_at=retrieved_at,
            dataset_updated_at=dataset_updated_at,
            dataset_updated_at_basis=DATASET_UPDATED_AT_BASIS,
            download_url=MOENV_FULL_PACKAGE_URL,
            acquisition_request_body=copy.deepcopy(REQUEST_BODY),
            upstream_etag=f'"{name}"',
            upstream_last_modified="Sun, 19 Jul 2026 23:15:13 GMT",
        )

    def ingest(
        self,
        snapshot,
        *,
        accepted_at: str = "2026-07-20T06:05:00Z",
        complete_refresh: bool = True,
    ):
        return import_moenv_ems_candidates(
            self.connection,
            snapshot,
            accepted_at=accepted_at,
            complete_refresh=complete_refresh,
        )

    def test_imports_all_exact_codes_historical_lowercase_and_leading_zero_ids(self) -> None:
        rows = [
            _row("p5806269", industry_id="2611"),
            _row(
                "00000001",
                industry_id="2612",
                regulated=False,
                airreleasedate="2025-06-30",
                waterreleasedate="2024-01-02",
            ),
            _row("B0000003", industry_id="2613"),
        ]
        snapshot = self.snapshot("all-codes", rows)
        result = self.ingest(snapshot)

        self.assertEqual(3, result.facilities_imported)
        self.assertEqual(3, result.variants_imported)
        self.assertEqual(3, result.entities_created)
        self.assertEqual(
            {
                "taiwan-moenv-ems:ems_s_01:p5806269",
                "taiwan-moenv-ems:ems_s_01:00000001",
                "taiwan-moenv-ems:ems_s_01:B0000003",
            },
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT stable_key FROM entities WHERE kind = 'facility'"
                )
            },
        )
        self.assertEqual({"2611", "2612", "2613"}, _scalar_values(
            self.connection, "moenv.industry_code"
        ))
        self.assertEqual(
            {
                "integrated_circuit_manufacturing_facility_candidate",
                "discrete_semiconductor_manufacturing_facility_candidate",
                "semiconductor_packaging_and_test_facility_candidate",
            },
            _scalar_values(self.connection, "candidate_classification"),
        )
        self.assertEqual(
            {"2025-06-30", "2024-01-02"},
            _scalar_values(
                self.connection, "moenv.environmental_control.air_release_date"
            )
            | _scalar_values(
                self.connection, "moenv.environmental_control.water_release_date"
            ),
        )
        self.assertEqual({False, True}, _scalar_values(
            self.connection, "currently_environmentally_regulated"
        ))
        self.assertEqual(3, self.connection.execute(
            "SELECT COUNT(*) FROM geometry_values"
        ).fetchone()[0])
        self.assertEqual([], validate_database(self.connection))

    def test_registers_both_exact_documents_and_grouped_record_provenance(self) -> None:
        snapshot = self.snapshot("provenance", [_row("A0000001")])
        result = self.ingest(snapshot, accepted_at="2026-07-20T07:00:00Z")

        documents = {
            row["id"]: row
            for row in self.connection.execute("SELECT * FROM source_documents")
        }
        self.assertEqual(2, len(documents))
        self.assertEqual(snapshot.candidate_sha256, documents[
            result.candidate_document_id
        ]["content_sha256"])
        self.assertEqual(snapshot.raw_sha256, documents[
            result.raw_document_id
        ]["content_sha256"])
        self.assertEqual(snapshot.retrieved_at, documents[
            result.candidate_document_id
        ]["retrieved_at"])
        roles = {
            (row["source_document_id"], row["role"])
            for row in self.connection.execute(
                "SELECT source_document_id, role FROM ingestion_run_documents"
            )
        }
        self.assertEqual(
            {
                (result.candidate_document_id, "primary"),
                (result.candidate_document_id, "candidate_derivative"),
                (result.candidate_document_id, "source_record"),
                (result.raw_document_id, "raw_archive"),
            },
            roles,
        )
        run = self.connection.execute(
            "SELECT started_at, parameters_json FROM ingestion_runs WHERE id = ?",
            (result.ingestion_run_id,),
        ).fetchone()
        parameters = json.loads(run["parameters_json"])
        self.assertEqual("2026-07-20T07:00:00Z", run["started_at"])
        self.assertNotEqual(snapshot.retrieved_at, run["started_at"])
        self.assertEqual(snapshot.manifest_sha256, parameters["manifest_sha256"])
        self.assertEqual(snapshot.raw_sha256, parameters["raw_archive"]["sha256"])
        self.assertEqual(
            "pass_with_required_attribution", parameters["rights"]["decision"]
        )
        self.assertEqual(1, parameters["upstream_row_count"])
        self.assertEqual(1, parameters["industry_group_row_count"])
        self.assertEqual(1, parameters["raw_matching_row_count"])
        self.assertEqual(0, parameters["conflicting_facility_count"])
        self.assertEqual(1, parameters["current_regulation_count"])
        self.assertEqual(1, parameters["valid_coordinate_count"])
        self.assertEqual(
            {"2611": 1, "2612": 0, "2613": 0},
            parameters["exact_industry_code_variant_counts"],
        )
        self.assertEqual(
            {"2026-07-20"},
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT valid_from FROM claim_versions"
                )
            },
        )
        candidate_metadata = json.loads(
            documents[result.candidate_document_id]["metadata_json"]
        )
        for key in (
            "upstream_row_count",
            "industry_group_row_count",
            "raw_matching_row_count",
            "conflicting_facility_count",
            "current_regulation_count",
            "valid_coordinate_count",
            "exact_industry_code_variant_counts",
        ):
            self.assertEqual(parameters[key], candidate_metadata[key])
        raw_metadata = json.loads(
            documents[result.raw_document_id]["metadata_json"]
        )
        self.assertEqual("POST", raw_metadata["acquisition_method"])
        self.assertEqual(MOENV_FULL_PACKAGE_URL, raw_metadata["acquisition_url"])
        self.assertEqual(REQUEST_BODY, raw_metadata["acquisition_request_body"])
        self.assertFalse(
            any(
                token in key.casefold()
                for key in raw_metadata["acquisition_request_body"]
                for token in ("credential", "password", "secret", "token", "key")
            )
        )
        payload = json.loads(
            self.connection.execute(
                "SELECT payload_json FROM source_records"
            ).fetchone()[0]
        )
        self.assertEqual("A0000001", payload["emsno"])
        self.assertEqual(1, len(payload["variants"]))
        evidence = self.connection.execute(
            """
            SELECT locator, excerpt FROM claim_evidence
            WHERE source_record_id IS NOT NULL
            ORDER BY locator LIMIT 1
            """
        ).fetchone()
        self.assertRegex(evidence["locator"], r"source_record\.variants\[0\]\.")
        self.assertEqual(0, json.loads(evidence["excerpt"])["variant_index"])

    def test_address_only_zero_point_keeps_raw_coordinates_without_geometry(self) -> None:
        snapshot = self.snapshot(
            "address-only",
            [
                _row(
                    "A0000001",
                    longitude="0",
                    latitude="0",
                    twd97tm2x="",
                    twd97tm2y="",
                )
            ],
        )
        self.ingest(snapshot)
        self.assertEqual({"新竹市東區測試路1號"}, _scalar_values(
            self.connection, "address.street"
        ))
        self.assertEqual({"0"}, _scalar_values(
            self.connection, "moenv.wgs84_longitude_raw"
        ))
        self.assertEqual({"0"}, _scalar_values(
            self.connection, "moenv.wgs84_latitude_raw"
        ))
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM geometry_values"
        ).fetchone()[0])

    def test_duplicate_rows_collapse_and_conflicting_variants_are_parallel_claims(self) -> None:
        exact = _row("A0000001", name="Alpha", isair="0")
        conflict = _row(
            "A0000001",
            name="Alpha variant",
            isair="1",
            longitude="121.112300",
        )
        snapshot = self.snapshot(
            "conflicts", [exact, dict(reversed(tuple(exact.items()))), conflict]
        )
        result = self.ingest(snapshot)

        self.assertEqual(1, result.facilities_imported)
        self.assertEqual(2, result.variants_imported)
        self.assertEqual(1, result.source_records_created)
        payload = json.loads(self.connection.execute(
            "SELECT payload_json FROM source_records"
        ).fetchone()[0])
        self.assertEqual(2, len(payload["variants"]))
        self.assertEqual({"Alpha", "Alpha variant"}, _scalar_values(
            self.connection, "name"
        ))
        self.assertEqual({"0", "1"}, _scalar_values(
            self.connection, "moenv.environmental_control.air_raw"
        ))
        self.assertEqual({True}, _scalar_values(
            self.connection, "currently_environmentally_regulated"
        ))
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM geometry_values"
        ).fetchone()[0])
        name_series = self.connection.execute(
            "SELECT COUNT(*) FROM claim_series WHERE predicate = 'name'"
        ).fetchone()[0]
        self.assertEqual(2, name_series)

    def test_geometry_depends_only_on_invariant_valid_raw_wgs84_strings(self) -> None:
        first = _row("A0000001", name="Alpha")
        second = _row("A0000001", name="Alpha", uniformno="00999999")
        snapshot = self.snapshot("geometry", [first, second])
        self.ingest(snapshot)

        geometry = self.connection.execute(
            "SELECT geometry_json, crs FROM geometry_values"
        ).fetchone()
        self.assertEqual(
            {"type": "Point", "coordinates": [121.0123, 24.8004]},
            json.loads(geometry["geometry_json"]),
        )
        self.assertEqual("EPSG:4326", geometry["crs"])
        dependency_predicates = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT parents.predicate
                FROM claim_dependencies AS dependencies
                JOIN claim_versions AS child ON child.id = dependencies.claim_version_id
                JOIN claim_series AS child_series ON child_series.id = child.series_id
                JOIN claim_versions AS parent
                  ON parent.id = dependencies.depends_on_claim_version_id
                JOIN claim_series AS parents ON parents.id = parent.series_id
                WHERE child_series.predicate = 'geometry'
                """
            )
        }
        self.assertEqual(
            {"moenv.wgs84_longitude_raw", "moenv.wgs84_latitude_raw"},
            dependency_predicates,
        )

    def test_exact_replay_is_read_only_and_database_hash_stable(self) -> None:
        snapshot = self.snapshot("replay", [_row("A0000001"), _row("B0000002")])
        first = self.ingest(snapshot)
        self.connection.commit()
        before = hashlib.sha256(self.database_path.read_bytes()).hexdigest()

        replay = self.ingest(snapshot)
        self.connection.commit()
        after = hashlib.sha256(self.database_path.read_bytes()).hexdigest()

        self.assertEqual(first.ingestion_run_id, replay.ingestion_run_id)
        self.assertTrue(replay.replayed_existing_run)
        self.assertEqual(0, replay.source_documents_created)
        self.assertEqual(0, replay.source_records_created)
        self.assertEqual(0, replay.entities_created)
        self.assertEqual(0, replay.claim_series_created)
        self.assertEqual(0, replay.claims_created)
        self.assertEqual(before, after)

    def test_replay_recomputes_claim_ledger_and_document_metadata(self) -> None:
        snapshot = self.snapshot("replay-tampering", [_row("A0000001")])
        result = self.ingest(snapshot)
        original_metadata = self.connection.execute(
            "SELECT metadata_json FROM source_documents WHERE id = ?",
            (result.candidate_document_id,),
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER source_documents_immutable_update")
        self.connection.execute(
            "UPDATE source_documents SET metadata_json = '{}' WHERE id = ?",
            (result.candidate_document_id,),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "source document .* conflicts"):
            self.ingest(snapshot)

        self.connection.execute(
            "UPDATE source_documents SET metadata_json = ? WHERE id = ?",
            (original_metadata, result.candidate_document_id),
        )
        self.connection.execute("DROP TRIGGER claim_versions_immutable_delete")
        self.connection.execute("DROP TRIGGER claim_dependencies_immutable_delete")
        self.connection.execute("DROP TRIGGER claim_values_immutable_delete")
        self.connection.execute("DROP TRIGGER scalar_values_immutable_delete")
        classification_id = self.connection.execute(
            """
            SELECT versions.id
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'candidate_classification'
            """
        ).fetchone()[0]
        self.connection.execute(
            "DELETE FROM claim_versions WHERE id = ?", (classification_id,)
        )
        self.connection.commit()
        before = "\n".join(self.connection.iterdump())
        with self.assertRaisesRegex(ValueError, "missing expected claim series"):
            self.ingest(snapshot)
        self.assertEqual(before, "\n".join(self.connection.iterdump()))

    def test_complete_refresh_versions_change_and_closes_omitted_facility(self) -> None:
        first = self.snapshot(
            "refresh-first",
            [_row("A0000001", name="Before"), _row("B0000002", name="Omitted")],
        )
        self.ingest(first)
        second = self.snapshot(
            "refresh-second",
            [_row("A0000001", name="After")],
            retrieved_at="2026-08-20T06:00:00Z",
            dataset_updated_at="2026-08-19T23:15:13Z",
        )
        result = self.ingest(second, accepted_at="2026-08-20T06:05:00Z")

        self.assertGreater(result.prior_open_claims_closed_or_corrected, 0)
        self.assertEqual(1, result.candidate_entities_no_longer_selected)
        august_names = {
            self.connection.execute(
                "SELECT text_value FROM scalar_values WHERE claim_version_id = ?",
                (claim["id"],),
            ).fetchone()[0]
            for claim in current_claims(
                self.connection,
                as_of="2026-08-20",
                recorded_at="2026-08-20T06:05:00Z",
                predicate="name",
            )
        }
        july_names = {
            self.connection.execute(
                "SELECT text_value FROM scalar_values WHERE claim_version_id = ?",
                (claim["id"],),
            ).fetchone()[0]
            for claim in current_claims(
                self.connection,
                as_of="2026-07-20",
                recorded_at="2026-08-20T06:05:00Z",
                predicate="name",
            )
        }
        self.assertEqual({"After"}, august_names)
        self.assertEqual({"Before", "Omitted"}, july_names)
        self.assertEqual(
            {"2026-07-20", "2026-08-20"},
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT valid_from FROM claim_versions"
                )
            },
        )
        closure_notes = {
            row[0]
            for row in self.connection.execute(
                "SELECT notes FROM claim_versions WHERE valid_to IS NOT NULL"
            )
        }
        self.assertTrue(all("does not assert real-world facility closure" in note for note in closure_notes))
        self.assertEqual([], validate_database(self.connection))
        before_replays = "\n".join(self.connection.iterdump())
        replay_second = self.ingest(
            second, accepted_at="2026-08-20T06:05:00Z"
        )
        replay_first = self.ingest(first)
        self.assertTrue(replay_second.replayed_existing_run)
        self.assertTrue(replay_first.replayed_existing_run)
        self.assertEqual(before_replays, "\n".join(self.connection.iterdump()))

    def test_partial_refresh_preserves_prior_values_and_nonselection(self) -> None:
        first = self.snapshot(
            "partial-first",
            [_row("A0000001", name="Before"), _row("B0000002", name="Preserved")],
        )
        self.ingest(first)
        second = self.snapshot(
            "partial-second",
            [_row("A0000001", name="After")],
            retrieved_at="2026-08-20T06:00:00Z",
            dataset_updated_at="2026-08-19T23:15:13Z",
        )
        result = self.ingest(
            second,
            accepted_at="2026-08-20T06:05:00Z",
            complete_refresh=False,
        )

        self.assertEqual(0, result.prior_open_claims_closed_or_corrected)
        self.assertEqual(0, result.candidate_entities_no_longer_selected)
        self.assertEqual(
            {"Before", "After", "Preserved"}, _scalar_values(self.connection, "name")
        )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM claim_versions WHERE valid_to IS NOT NULL"
            ).fetchone()[0],
        )

    def test_failed_transaction_leaves_no_failed_run_lineage_or_partial_artifacts(self) -> None:
        snapshot = self.snapshot("failure", [_row("A0000001")])
        with mock.patch.object(
            moenv_ingest,
            "insert_claim",
            side_effect=RuntimeError("injected claim failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected claim failure"):
                self.ingest(snapshot)

        for table in (
            "source_families",
            "sources",
            "source_documents",
            "ingestion_runs",
            "ingestion_run_documents",
            "source_records",
            "entities",
            "claim_series",
            "claim_versions",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                table,
            )
        self.assertEqual([], validate_database(self.connection))

    def test_final_snapshot_reverification_failure_rolls_back_every_database_write(self) -> None:
        snapshot = self.snapshot("final-reverify-failure", [_row("A0000001")])
        real_verify = moenv_ingest.verify_moenv_snapshot
        calls = 0

        def fail_second_verification(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("snapshot changed during import")
            return real_verify(root)

        with mock.patch.object(
            moenv_ingest,
            "verify_moenv_snapshot",
            side_effect=fail_second_verification,
        ):
            with self.assertRaisesRegex(ValueError, "changed during import"):
                self.ingest(snapshot)
        self.assertEqual(2, calls)
        for table in (
            "source_families",
            "sources",
            "source_documents",
            "ingestion_runs",
            "source_records",
            "entities",
            "claim_series",
            "claim_versions",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                table,
            )

    def test_rejects_hash_rights_and_manifest_provenance_mismatch_before_writes(self) -> None:
        for field, value in (
            ("raw_sha256", "0" * 64),
            ("manifest_sha256", "f" * 64),
            ("dataset_updated_at_basis", "invented_basis"),
        ):
            with self.subTest(field=field):
                snapshot = self.snapshot(
                    f"mismatch-{field}", [_row("A0000001")]
                )
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    self.ingest(replace(snapshot, **{field: value}))
                self.assertEqual(0, self.connection.execute(
                    "SELECT COUNT(*) FROM source_documents"
                ).fetchone()[0])

        snapshot = self.snapshot("rights", [_row("A0000001")])
        manifest_path = snapshot.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_scopes"][MOENV_SCOPE]["rights"]["license_url"] = (
            "https://example.test/invented-license"
        )
        manifest_path.write_text(
            json.dumps(
                manifest,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "rights metadata"):
            self.ingest(snapshot)
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM source_documents"
        ).fetchone()[0])

    def test_importer_upgrade_reuses_documents_and_source_claims(self) -> None:
        snapshot = self.snapshot("upgrade", [_row("A0000001")])
        with mock.patch.object(
            moenv_ingest, "IMPORTER_VERSION", "taiwan-moenv-ems-s-01-import-v0"
        ):
            first = self.ingest(snapshot)
        claim_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0]

        second = self.ingest(snapshot, accepted_at="2026-07-20T07:05:00Z")

        self.assertNotEqual(first.ingestion_run_id, second.ingestion_run_id)
        self.assertEqual(first.candidate_document_id, second.candidate_document_id)
        self.assertEqual(first.raw_document_id, second.raw_document_id)
        self.assertEqual(2, self.connection.execute(
            "SELECT COUNT(*) FROM source_documents"
        ).fetchone()[0])
        self.assertEqual(2, self.connection.execute(
            "SELECT COUNT(*) FROM ingestion_runs"
        ).fetchone()[0])
        self.assertEqual(0, second.claims_created)
        self.assertEqual(claim_count, second.unchanged_claims_reused)
        self.assertEqual(claim_count, self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions"
        ).fetchone()[0])

    def test_import_never_emits_operation_lifecycle_ownership_or_capacity(self) -> None:
        snapshot = self.snapshot("negative-space", [_row("A0000001")])
        self.ingest(snapshot)
        predicates = {
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT predicate FROM claim_series"
            )
        }
        forbidden_fragments = (
            "operating",
            "operation",
            "production",
            "capacity",
            "owner",
            "operator",
            "lifecycle",
        )
        self.assertFalse(
            any(fragment in predicate for predicate in predicates for fragment in forbidden_fragments)
        )
        for table in (
            "capacity_values",
            "relationship_values",
            "milestone_values",
            "capability_values",
            "resource_values",
            "source_entity_assignments",
        ):
            self.assertEqual(
                0,
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
            )
        derived = self.connection.execute(
            """
            SELECT versions.claim_kind, versions.notes
            FROM claim_versions AS versions
            JOIN claim_series AS series ON series.id = versions.series_id
            WHERE series.predicate = 'currently_environmentally_regulated'
            """
        ).fetchone()
        self.assertEqual("derived_estimate", derived["claim_kind"])
        self.assertIn("not facility operating", derived["notes"])


if __name__ == "__main__":
    unittest.main()
