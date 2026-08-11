from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import semiconductor_atlas.adapters.eea_industrial as eea_industrial
from semiconductor_atlas.adapters.eea_industrial import (
    EEA_EPRTR_METADATA_TABLE,
    EEA_FACILITY_DETAILS_TABLE,
    EEA_FACILITY_TABLE,
    EEA_FUNCTION_TABLE,
    EEA_METADATA_TABLE,
    EEA_NAME_TERM_LEXICON_SHA256,
    EEA_NULL_SENTINEL,
    EEA_PRODUCTION_VOLUME_TABLE,
    EEA_REQUIRED_TABLES,
    EEA_SITE_TABLE,
    EEA_TABLE_HEADERS,
    EEA_V16_TABLE_CONTRACT_SHA256,
    canonical_candidate_jsonl_bytes,
    scan_eea_industrial_exports,
    scan_eea_industrial_v16_exports,
)


def _metadata(file_id: str = "1", year: str = "2024") -> dict[str, str | None]:
    return {
        "fileId": file_id,
        "envelopeUrl": "https://cdr.eionet.europa.eu/example",
        "filename": "submission.xml",
        "dateSubmitted": "2025-12-01T00:00:00",
        "dateReleased": "2025-12-02T00:00:00",
        "dateImported": "2026-02-10T00:00:00",
        "fileSHA256Hash": "a" * 64,
        "countryCode": "GB",
        "reportingYear": year,
        "obligation": "721",
    }


def _site(
    site_id: str = "GB.TEST/SITE.1",
    *,
    name: str | None = "Example industrial site",
) -> dict[str, str | None]:
    return {
        "fileId_EUReg": "1",
        "Site_INSPIRE_ID": site_id,
        "ProductionSite_thematicId": "SITE-1",
        "ProductionSite_thematicIdScheme": "fixture",
        "pointGeometryLat": "51.5",
        "pointGeometryLon": "-3.1",
        "nameOfFeature": name,
        "countryCode": "GB",
    }


def _facility(
    facility_id: str,
    *,
    site_id: str = "GB.TEST/SITE.1",
    name: str | None = "Example facility",
) -> dict[str, str | None]:
    return {
        "fileId_EUReg": "1",
        "Parent_Site_INSPIRE_ID": site_id,
        "Facility_INSPIRE_ID": facility_id,
        "ProductionFacility_thematicId": "PF-1",
        "ProductionFacility_thematicIdScheme": "fixture",
        "parentCompanyName": "Example Holdings",
        "parentCompany_confidentialityReasonCode": None,
        "parentCompany_confidentialityReasonName": None,
        "nameOfFeature": name,
        "facilityName_confidentialityReasonCode": None,
        "facilityName_confidentialityReasonName": None,
        "facilityType": "eprtr",
        "pointGeometryLat": "51.5000",
        "pointGeometryLon": "-3.1000",
        "streetName": "Test Road",
        "buildingNumber": "1",
        "city": "Cardiff",
        "countryCode": "GB",
        "addressDetails_confidentialityReasonCode": None,
        "addressDetails_confidentialityReasonName": None,
        "mainActivityCode": "6.7",
        "mainActivityName": "Surface treatment",
        "dateOfStartOfOperation": "2001-01-01T00:00:00",
        "RBDSourceCode": "UK01",
        "RBDSourceName": "Fixture basin",
        "NUTSRegionSourceCode": "UKL22",
        "NUTSRegionSourceName": "Cardiff and Vale of Glamorgan",
        "parentCompanyURL": "https://example.invalid/private",
        "postalCode": "CF10 1AA",
    }


def _function(
    function_id: str,
    facility_id: str,
    code: str | None,
) -> dict[str, str | None]:
    return {
        "FunctionId": function_id,
        "Facility_INSPIRE_ID": facility_id,
        "NACEMainEconomicActivityCode": code,
        "NACEMainEconomicActivityName": (
            None if code is None else "Manufacture of electronic components"
        ),
    }


def _detail(
    detail_id: str,
    facility_id: str | None,
) -> dict[str, str | None]:
    return {
        "fileId_EUReg": "1",
        "fileId_EPRTR_LCP": None,
        "ProductionFacilityDetailsID": detail_id,
        "Facility_INSPIRE_ID": facility_id,
        "reportingYear": "2024",
        "status": "functional",
        "remarks": "free text that is never published",
        "numberOfOperatingHours": "8760",
        "numberOfEmployees": "42",
        "stackHeightClass": "10-20",
        "representativeStackHeightM": "12",
        "confidentialityReasonCode": None,
        "confidentialityReasonName": None,
    }


def _default_tables() -> dict[str, list[dict[str, str | None]]]:
    return {
        EEA_METADATA_TABLE: [_metadata()],
        EEA_EPRTR_METADATA_TABLE: [],
        EEA_SITE_TABLE: [_site()],
        EEA_FACILITY_TABLE: [],
        EEA_FACILITY_DETAILS_TABLE: [],
        EEA_FUNCTION_TABLE: [],
        EEA_PRODUCTION_VOLUME_TABLE: [],
    }


def _write_exports(
    root: Path,
    tables: dict[str, list[dict[str, str | None]]],
) -> None:
    root.mkdir()
    for table in EEA_REQUIRED_TABLES:
        with (root / f"{table}.csv").open(
            "x", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream, lineterminator="\n")
            headers = EEA_TABLE_HEADERS[table]
            writer.writerow(headers)
            for row in tables[table]:
                writer.writerow(
                    [
                        EEA_NULL_SENTINEL if row[field] is None else row[field]
                        for field in headers
                    ]
                )


def _jsonl(raw: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in raw.splitlines()]


class EEAIndustrialAdapterTests(unittest.TestCase):
    def test_production_scan_requires_exact_v16_hash_and_count_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = {table: [] for table in EEA_REQUIRED_TABLES}
            root = Path(temporary) / "header-only"
            _write_exports(root, tables)

            generic = scan_eea_industrial_exports(root)
            self.assertEqual(0, generic.facility_row_count)
            with self.assertRaisesRegex(ValueError, "exact reviewed edition 16.00"):
                scan_eea_industrial_v16_exports(root)
            self.assertEqual(
                "0eefb0342cba39115a243c6a05543ddc8e6f821b4ac620a98c173cc2e2b51dad",
                EEA_V16_TABLE_CONTRACT_SHA256,
            )

    def test_exact_nace_and_explicit_names_create_leads_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_FACILITY_TABLE] = [
                _facility("GB.TEST/FACILITY.PCB", name="Printed circuit works"),
                _facility(
                    "GB.TEST/FACILITY.SEMI",
                    name="Plymouth Semiconductor Foundry",
                ),
                _facility(
                    "GB.TEST/FACILITY.GENERIC",
                    name="Silicon Electronics Fab",
                ),
            ]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.PCB", "26.11"),
                _function("2", "GB.TEST/FACILITY.SEMI", "38.21"),
                _function("3", "GB.TEST/FACILITY.GENERIC", "26.12"),
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [
                _detail("1", "GB.TEST/FACILITY.PCB"),
                _detail("2", "GB.TEST/FACILITY.SEMI"),
                _detail("3", None),
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            records = _jsonl(canonical_candidate_jsonl_bytes(scan))

            self.assertEqual(3, scan.facility_row_count)
            self.assertEqual(2, scan.candidate_count)
            self.assertEqual(1, scan.nace_26_11_facility_count)
            self.assertEqual(1, scan.facility_name_candidate_count)
            self.assertEqual(1, scan.orphan_detail_count)
            self.assertEqual(
                ["GB.TEST/FACILITY.PCB", "GB.TEST/FACILITY.SEMI"],
                [record["facility_inspire_id"] for record in records],
            )
            self.assertEqual(
                "electronic_components_nace_26_11_candidate",
                records[0]["candidate_reasons"][0]["kind"],
            )
            self.assertEqual(
                "explicit_facility_name_candidate",
                records[1]["candidate_reasons"][0]["kind"],
            )
            derivative = canonical_candidate_jsonl_bytes(scan).decode("utf-8")
            self.assertNotIn("Silicon Electronics Fab", derivative)
            self.assertNotIn("capacity", derivative.casefold())
            self.assertNotIn("operating_status", derivative)
            self.assertEqual(
                "27cae8879f8997025c9cd5b6652211bc4019664ca3094304b9a38892b1d3aaac",
                EEA_NAME_TERM_LEXICON_SHA256,
            )

    def test_nace_is_exact_and_stmicroelectronics_is_an_explicit_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_FACILITY_TABLE] = [
                _facility("GB.TEST/FACILITY.SPACE"),
                _facility("GB.TEST/FACILITY.FULLWIDTH"),
                _facility(
                    "GB.TEST/FACILITY.ST",
                    name="STMicroelectronics Silicon Carbide AB",
                ),
            ]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.SPACE", " 26.11 "),
                _function("2", "GB.TEST/FACILITY.FULLWIDTH", "２６．１１"),
                _function("3", "GB.TEST/FACILITY.ST", "20.59"),
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            records = _jsonl(canonical_candidate_jsonl_bytes(scan))

            self.assertEqual(0, scan.nace_26_11_row_count)
            self.assertEqual(1, scan.candidate_count)
            self.assertEqual(
                "GB.TEST/FACILITY.ST", records[0]["facility_inspire_id"]
            )
            self.assertEqual(
                "explicit_facility_name_candidate",
                records[0]["candidate_reasons"][0]["kind"],
            )

    def test_parent_site_name_is_context_but_parent_company_alone_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_SITE_TABLE] = [
                _site("GB.TEST/SITE.SEMI", name="St Mellons Semiconductor Plant"),
                _site("GB.TEST/SITE.OTHER", name="Ordinary industrial estate"),
            ]
            site_candidate = _facility(
                "GB.TEST/FACILITY.SITE-CONTEXT",
                site_id="GB.TEST/SITE.SEMI",
                name="Building 4",
            )
            parent_only = _facility(
                "GB.TEST/FACILITY.PARENT-ONLY",
                site_id="GB.TEST/SITE.OTHER",
                name="Chemical treatment works",
            )
            parent_only["parentCompanyName"] = "Example Semiconductors Ltd"
            tables[EEA_FACILITY_TABLE] = [site_candidate, parent_only]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.SITE-CONTEXT", "38.21"),
                _function("2", "GB.TEST/FACILITY.PARENT-ONLY", "38.21"),
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            record = _jsonl(canonical_candidate_jsonl_bytes(scan))[0]

            self.assertEqual(1, scan.candidate_count)
            self.assertEqual("GB.TEST/FACILITY.SITE-CONTEXT", record["facility_inspire_id"])
            self.assertEqual(
                "explicit_parent_site_name_candidate",
                record["candidate_reasons"][0]["kind"],
            )

    def test_confidential_fields_and_unnecessary_free_text_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            facility = _facility(
                "GB.TEST/FACILITY.SECRET",
                name="Secret Semiconductor Facility",
            )
            facility.update(
                {
                    "parentCompanyName": "Secret Parent Company",
                    "parentCompany_confidentialityReasonCode": "C",
                    "parentCompany_confidentialityReasonName": "secret parent reason",
                    "facilityName_confidentialityReasonCode": "C",
                    "facilityName_confidentialityReasonName": "secret name reason",
                    "addressDetails_confidentialityReasonCode": "C",
                    "addressDetails_confidentialityReasonName": "secret address reason",
                    "streetName": "Secret Street",
                }
            )
            detail = _detail("1", "GB.TEST/FACILITY.SECRET")
            detail["confidentialityReasonCode"] = "C"
            detail["confidentialityReasonName"] = "secret detail reason"
            tables[EEA_FACILITY_TABLE] = [facility]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.SECRET", "26.11")
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [detail]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            raw = canonical_candidate_jsonl_bytes(scan)
            record = _jsonl(raw)[0]
            projected_facility = record["facility_rows"][0]
            values = projected_facility["values"]
            projected_detail = record["facility_detail_rows"][0]

            self.assertEqual(1, scan.candidate_count)
            self.assertEqual(1, scan.confidential_facility_name_count)
            self.assertIsNone(values["nameOfFeature"])
            self.assertIsNone(values["parentCompanyName"])
            self.assertIsNone(values["streetName"])
            self.assertNotIn("parentCompanyURL", values)
            self.assertIsNone(projected_detail["values"]["status"])
            self.assertIsNone(projected_detail["values"]["numberOfOperatingHours"])
            for secret in (
                "Secret Semiconductor Facility",
                "Secret Parent Company",
                "Secret Street",
                "secret parent reason",
                "secret name reason",
                "secret address reason",
                "secret detail reason",
                "free text that is never published",
                "example.invalid/private",
            ):
                self.assertNotIn(secret.encode("utf-8"), raw)

    def test_confidentiality_reason_name_or_whitespace_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            facility = _facility(
                "GB.TEST/FACILITY.REASON-NAME",
                name="Leaked Semiconductor Facility",
            )
            facility["facilityName_confidentialityReasonCode"] = None
            facility["facilityName_confidentialityReasonName"] = "protected"
            facility["parentCompany_confidentialityReasonCode"] = "   "
            facility["parentCompany_confidentialityReasonName"] = None
            detail = _detail("1", "GB.TEST/FACILITY.REASON-NAME")
            detail["confidentialityReasonCode"] = None
            detail["confidentialityReasonName"] = "protected detail"
            tables[EEA_FACILITY_TABLE] = [facility]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.REASON-NAME", "26.11")
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [detail]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            raw = canonical_candidate_jsonl_bytes(scan)
            record = _jsonl(raw)[0]

            self.assertEqual(1, scan.confidential_facility_name_count)
            self.assertEqual(1, scan.confidential_parent_company_count)
            self.assertEqual(1, scan.confidential_detail_row_count)
            self.assertIsNone(record["facility_rows"][0]["values"]["nameOfFeature"])
            self.assertIsNone(
                record["facility_rows"][0]["values"]["parentCompanyName"]
            )
            self.assertIsNone(
                record["facility_detail_rows"][0]["values"]["status"]
            )
            self.assertNotIn(b"Leaked Semiconductor Facility", raw)
            self.assertNotIn(b"protected detail", raw)

    def test_null_and_empty_string_remain_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            facility = _facility("GB.TEST/FACILITY.NULLS")
            facility["parentCompanyName"] = ""
            facility["ProductionFacility_thematicId"] = None
            tables[EEA_FACILITY_TABLE] = [facility]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.NULLS", "26.11")
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            record = _jsonl(
                canonical_candidate_jsonl_bytes(
                    scan_eea_industrial_exports(root)
                )
            )[0]
            values = record["facility_rows"][0]["values"]

            self.assertEqual("", values["parentCompanyName"])
            self.assertIsNone(values["ProductionFacility_thematicId"])

    def test_metadata_lineage_unions_site_facility_and_detail_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_METADATA_TABLE] = [
                _metadata("1", "2024"),
                _metadata("2", "2023"),
                _metadata("3", "2022"),
            ]
            tables[EEA_EPRTR_METADATA_TABLE] = [_metadata("100", "2021")]
            site = _site()
            site["fileId_EUReg"] = "2"
            facility = _facility(
                "GB.TEST/FACILITY.LINEAGE", name="Lineage Semiconductor"
            )
            detail = _detail("1", "GB.TEST/FACILITY.LINEAGE")
            detail["fileId_EUReg"] = "3"
            detail["fileId_EPRTR_LCP"] = "100"
            tables[EEA_SITE_TABLE] = [site]
            tables[EEA_FACILITY_TABLE] = [facility]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.LINEAGE", "38.21")
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [detail]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            record = _jsonl(
                canonical_candidate_jsonl_bytes(scan_eea_industrial_exports(root))
            )[0]
            references = [
                (row["source_table"], row["values"]["fileId"])
                for row in record["metadata_rows"]
            ]

            self.assertEqual(
                [
                    (EEA_METADATA_TABLE, "1"),
                    (EEA_METADATA_TABLE, "2"),
                    (EEA_METADATA_TABLE, "3"),
                    (EEA_EPRTR_METADATA_TABLE, "100"),
                ],
                references,
            )
            self.assertEqual("2024", record["latest_source_detail_reporting_year"])

    def test_rejects_missing_detail_metadata_in_either_namespace(self) -> None:
        for field, missing in (
            ("fileId_EUReg", "99"),
            ("fileId_EPRTR_LCP", "100"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                tables = _default_tables()
                tables[EEA_FACILITY_TABLE] = [
                    _facility("GB.TEST/FACILITY.METADATA")
                ]
                detail = _detail("1", "GB.TEST/FACILITY.METADATA")
                detail[field] = missing
                tables[EEA_FACILITY_DETAILS_TABLE] = [detail]
                root = Path(temporary) / "exports"
                _write_exports(root, tables)
                with self.assertRaisesRegex(ValueError, "missing metadata"):
                    scan_eea_industrial_exports(root)

    def test_coordinate_zero_pair_and_exact_fk_drift_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            facility = _facility("GB.EEA/FACILITY.EXACT", name="Exact Semiconductor")
            facility["pointGeometryLat"] = "0"
            facility["pointGeometryLon"] = "0.0"
            tables[EEA_FACILITY_TABLE] = [facility]
            tables[EEA_FACILITY_DETAILS_TABLE] = [
                _detail("1", "gb.eea/facility.exact"),
                _detail("2", "GB.EEA/FACILITY.EXACT "),
                _detail("3", "GB.TEST/FACILITY.UNKNOWN"),
                _detail("4", "GB.EEA/FACILITY.EXACT"),
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            record = _jsonl(canonical_candidate_jsonl_bytes(scan))[0]

            self.assertEqual(1, scan.missing_or_invalid_coordinate_count)
            self.assertIn(
                "missing_or_invalid_representative_point", record["join_anomalies"]
            )
            self.assertEqual(3, scan.detail_exact_fk_drift_count)
            self.assertEqual(1, scan.detail_case_only_fk_drift_count)
            self.assertEqual(1, scan.detail_whitespace_only_fk_drift_count)
            self.assertEqual(1, scan.detail_unresolved_fk_drift_count)
            self.assertEqual(1, len(record["facility_detail_rows"]))
            self.assertEqual(1, scan.publisher_mapped_facility_id_count)
            self.assertEqual(1, scan.publisher_mapped_candidate_id_count)
            self.assertEqual(
                (("2024", 1),), scan.candidate_latest_detail_reporting_year_counts
            )

    def test_candidate_derivative_is_independent_of_export_row_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tables = _default_tables()
            first_facility = _facility("GB.TEST/FACILITY.A", name="Semiconductor A")
            second_facility = _facility("GB.TEST/FACILITY.B", name="Semiconductor B")
            tables[EEA_FACILITY_TABLE] = [second_facility, first_facility]
            tables[EEA_FUNCTION_TABLE] = [
                _function("20", "GB.TEST/FACILITY.B", "26.11"),
                _function("10", "GB.TEST/FACILITY.A", "26.11"),
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [
                _detail("20", "GB.TEST/FACILITY.B"),
                _detail("10", "GB.TEST/FACILITY.A"),
            ]
            first = root / "first"
            _write_exports(first, tables)

            reversed_tables = {
                table: list(reversed(rows)) for table, rows in tables.items()
            }
            second = root / "second"
            _write_exports(second, reversed_tables)

            self.assertEqual(
                canonical_candidate_jsonl_bytes(
                    scan_eea_industrial_exports(first)
                ),
                canonical_candidate_jsonl_bytes(
                    scan_eea_industrial_exports(second)
                ),
            )

    def test_rejects_schema_drift_duplicates_and_unjoinable_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_FACILITY_TABLE] = [
                _facility("GB.TEST/FACILITY.DUP"),
                _facility("GB.TEST/FACILITY.DUP"),
            ]
            root = Path(temporary) / "duplicate"
            _write_exports(root, tables)
            with self.assertRaisesRegex(ValueError, "duplicate Facility_INSPIRE_ID"):
                scan_eea_industrial_exports(root)

        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.MISSING", "26.11")
            ]
            root = Path(temporary) / "orphan"
            _write_exports(root, tables)
            with self.assertRaisesRegex(ValueError, "not joinable"):
                scan_eea_industrial_exports(root)

        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            root = Path(temporary) / "schema"
            _write_exports(root, tables)
            path = root / f"{EEA_FUNCTION_TABLE}.csv"
            path.write_text("wrong,headers\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "headers drifted"):
                scan_eea_industrial_exports(root)

    def test_rejects_extra_files_and_does_not_repair_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tables = _default_tables()
            tables[EEA_FACILITY_TABLE] = [_facility("GB.TEST/FACILITY.EXACT")]
            tables[EEA_FUNCTION_TABLE] = [
                _function("1", "GB.TEST/FACILITY.EXACT", "26.11")
            ]
            tables[EEA_FACILITY_DETAILS_TABLE] = [
                _detail("1", "GB.TEST/FACILITY.EXACT ")
            ]
            root = Path(temporary) / "exports"
            _write_exports(root, tables)

            scan = scan_eea_industrial_exports(root)
            record = _jsonl(canonical_candidate_jsonl_bytes(scan))[0]
            self.assertEqual(1, scan.orphan_detail_count)
            self.assertEqual([], record["facility_detail_rows"])

            (root / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extra or missing"):
                scan_eea_industrial_exports(root)

    @unittest.skipUnless(hasattr(os, "O_DIRECTORY"), "requires directory FDs")
    def test_rejects_root_path_swap_and_reads_only_from_open_directory_fd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "exports"
            checked_root = parent / "checked-exports"
            attacker = parent / "attacker"
            tables = _default_tables()
            tables[EEA_FACILITY_TABLE] = [
                _facility("GB.TEST/FACILITY.SAFE", name="Safe Semiconductor")
            ]
            _write_exports(root, tables)
            _write_exports(attacker, _default_tables())
            original_read_table = eea_industrial._read_table
            swapped = False

            def swap_then_read(root_descriptor: int, table: str):
                nonlocal swapped
                if not swapped:
                    root.rename(checked_root)
                    root.symlink_to(attacker, target_is_directory=True)
                    swapped = True
                return original_read_table(root_descriptor, table)

            with (
                mock.patch.object(
                    eea_industrial, "_read_table", side_effect=swap_then_read
                ),
                self.assertRaisesRegex(ValueError, "root changed"),
            ):
                scan_eea_industrial_exports(root)


if __name__ == "__main__":
    unittest.main()
