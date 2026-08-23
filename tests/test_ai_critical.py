from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from semiconductor_atlas.ai_critical import (
    CAPACITY_BASES,
    COMPANIES,
    INPUT_FORMAT,
    RELEASE_FORMAT,
    SELECTION_VERSION,
    SOURCE_ADAPTER_VERSION,
    _capacity_assertion_matches_excerpt,
    _capacity_assertion_signature,
    _link_file_exclusive,
    _read_safe_source_bytes,
    _rename_exclusive,
    install_directory_exclusive,
    install_file_exclusive,
    load_baseline,
    materialize_baseline,
    validate_release,
    write_deterministic_archive,
    write_release,
)
from web.generate_atlas import generate
from scripts.build_ai_critical_release import _remove_owned_marker, build


class AICriticalBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.source_path = self.root / "source_snapshots" / "official.html"
        self.source_path.parent.mkdir(parents=True)
        source_statements = [
            f"Official scoped statement for {company}."
            for company in COMPANIES
            if company != "Amkor"
        ]
        source_statements.extend(
            [
                "Official scoped statement for Amkor: upon completion, the facility "
                "is expected to produce 3,700,000 units/month and 14,500 wafers/month.",
                "Official revised scoped statement for TSMC.",
                "Amkor announced quarter total: 3,700,000 units in 2026 Q1.",
            ]
        )
        self.source_raw = (
            "<html><body>" + " ".join(source_statements) + "</body></html>\n"
        ).encode("utf-8")
        self.source_path.write_bytes(self.source_raw)
        self.spec = self._spec()
        self.input_path = self.root / "baseline.json"
        self._write_spec()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _production_source_paths(self, repository_root: Path) -> list[Path]:
        input_path = (
            repository_root / "baselines" / "ai_critical_manufacturing_v1.json"
        )
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        return [
            repository_root / source["archive_path"]
            for source in payload["sources"]
        ]

    def _source(self) -> dict[str, object]:
        return {
            "source_id": "official-fixture",
            "source_family": "official-company",
            "source_type": "official_primary",
            "publisher": "Official Publisher",
            "title": "Official facility evidence",
            "url": "https://example.com/official-facility-evidence",
            "published_at": "2024-01-02",
            "published_at_precision": "day",
            "published_at_basis": "Fixture publication date.",
            "retrieved_at": "2026-08-20T16:00:00Z",
            "acquired_at": "2026-08-20T16:00:00Z",
            "archive_path": "source_snapshots/official.html",
            "content_sha256": hashlib.sha256(self.source_raw).hexdigest(),
            "bytes": len(self.source_raw),
            "media_type": "text/html",
            "language": "en",
            "adapter_version": SOURCE_ADAPTER_VERSION,
            "ingestion_run_id": "fixture-ingest:20260820",
            "rights": {
                "access": "public",
                "license": "Public information fixture",
                "license_url": "https://example.com/rights",
                "redistribution": "metadata_and_excerpt_only",
                "attribution": "Source: Official Publisher",
                "reviewed_at": "2026-08-20",
            },
        }

    def _facility(
        self,
        company: str,
        category: str,
        *,
        second_category: str | None = None,
        capacities: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        slug = company.casefold().replace(" ", "-")
        evidence_id = f"evidence-{slug}"
        categories = sorted({category, *(() if second_category is None else (second_category,))})
        capacity_rows = capacities or []
        supported = {str(row["basis"]) for row in capacity_rows}
        return {
            "facility_key": f"fixture:{slug}",
            "company": company,
            "name": f"{company} official facility scope",
            "identity_scope": "project_site_scope",
            "identity_valid_from": "2024-01-02",
            "identity_evidence_ids": [evidence_id],
            "geography": {
                "country_code": "US",
                "country": "United States",
                "admin1": "Arizona",
                "city": "Phoenix",
                "latitude": None,
                "longitude": None,
                "precision_m": None,
                "geometry_scope": "unresolved",
                "valid_from": "2024-01-02",
                "evidence_ids": [evidence_id],
            },
            "scope_categories": categories,
            "lifecycle": {
                "state": "announced",
                "statement": "The official source announced this bounded project scope.",
                "as_of": "2024-01-02",
                "claim_kind": "source_statement",
                "method": "official_statement_capture_v1",
                "evidence_ids": [evidence_id],
            },
            "capabilities": [
                {
                    "category": scoped_category,
                    "technology": f"Official {scoped_category} statement",
                    "readiness": "planned",
                    "valid_from": "2024-01-02",
                    "claim_kind": "source_statement",
                    "method": "official_capability_capture_v1",
                    "evidence_ids": [evidence_id],
                }
                for scoped_category in categories
            ],
            "capacities": capacity_rows,
            "unknowns": {
                "yield": "unknown",
                "utilization": "unknown",
                "qualification": "unknown",
                "capacity_bases": sorted(set(CAPACITY_BASES) - supported),
            },
        }

    def _amkor_capacities(self) -> list[dict[str, object]]:
        common = {
            "basis": "announced",
            "period_start": None,
            "period_end": None,
            "scope_kind": "project_addition",
            "input_output_basis": "source_stated_throughput",
            "quantity_semantics": "monthly_rate",
            "technology_scope": ["advanced_packaging", "advanced_test"],
            "valid_from": "2024-01-02",
            "claim_kind": "source_statement",
            "method": "official_numeric_statement_capture_v1",
            "notes": "Future announced throughput; not observed, installed, qualified, or economically usable capacity.",
            "evidence_ids": ["evidence-amkor"],
        }
        return [
            {
                **common,
                "metric": "units_throughput",
                "unit": "units/month",
                "low": 3_700_000,
                "base": 3_700_000,
                "high": 3_700_000,
            },
            {
                **common,
                "metric": "wafers_throughput",
                "unit": "wafers/month",
                "low": 14_500,
                "base": 14_500,
                "high": 14_500,
            },
        ]

    @staticmethod
    def _capacity_assertion(capacity: dict[str, object]) -> dict[str, object]:
        return {
            key: copy.deepcopy(capacity[key])
            for key in (
                "metric",
                "basis",
                "unit",
                "low",
                "base",
                "high",
                "period_start",
                "period_end",
                "scope_kind",
                "input_output_basis",
                "quantity_semantics",
                "technology_scope",
            )
        }

    def _spec(self) -> dict[str, object]:
        categories = {
            "TSMC": ("leading_edge_logic", None),
            "Samsung": ("leading_edge_logic", None),
            "Intel": ("leading_edge_logic", None),
            "Micron": ("hbm_packaging", None),
            "SK hynix": ("hbm_fabrication", None),
            "Amkor": ("advanced_packaging", "advanced_test"),
            "ASE": ("advanced_packaging", "advanced_test"),
        }
        source = self._source()
        ingestion_configuration = {
            "method": "official_primary_exact_byte_local_replay",
            "selection_version": SELECTION_VERSION,
            "source_count": 1,
        }
        ingestion_run = {
            "format": "semiconductor-atlas-source-ingestion-run-v1",
            "run_id": source["ingestion_run_id"],
            "source_id": source["source_id"],
            "code_version": SOURCE_ADAPTER_VERSION,
            "environment": "unittest-local-python",
            "configuration": ingestion_configuration,
            "configuration_sha256": hashlib.sha256(
                (
                    json.dumps(
                        ingestion_configuration,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest(),
            "inputs": [
                {
                    "source_id": source["source_id"],
                    "document_role": "primary",
                    "content_sha256": source["content_sha256"],
                }
            ],
            "started_at": "2026-08-20T16:29:00Z",
            "finished_at": "2026-08-20T16:29:30Z",
            "outcome": "succeeded",
        }
        evidence = []
        for company in COMPANIES:
            excerpt = (
                "Official scoped statement for Amkor: upon completion, the facility "
                "is expected to produce 3,700,000 units/month and 14,500 wafers/month."
                if company == "Amkor"
                else f"Official scoped statement for {company}."
            )
            fragment = {
                "evidence_id": f"evidence-{company.casefold().replace(' ', '-')}",
                "source_id": "official-fixture",
                "role": "support",
                "locator": f"facility section for {company}",
                "excerpt": excerpt,
                "verification": {
                    "method": "normalized_text_segments_v1",
                    "reviewer": "local:unittest-review",
                    "reviewed_at": "2026-08-20T16:20:00Z",
                    "result": "matched_archived_source",
                },
            }
            if company == "Amkor":
                fragment["capacity_assertions"] = [
                    self._capacity_assertion(capacity)
                    for capacity in self._amkor_capacities()
                ]
                fragment["capacity_assertions"].sort(
                    key=lambda assertion: json.dumps(
                        assertion,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            fragment_payload = {
                "source_id": fragment["source_id"],
                "source_record_sha256": hashlib.sha256(
                    (
                        json.dumps(
                            source,
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                ).hexdigest(),
                "role": fragment["role"],
                "locator": fragment["locator"],
                "excerpt": fragment["excerpt"],
                "verification": fragment["verification"],
            }
            if "capacity_assertions" in fragment:
                fragment_payload["capacity_assertions"] = fragment[
                    "capacity_assertions"
                ]
            fragment["fragment_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        fragment_payload,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            evidence.append(fragment)
        facilities = []
        for company in COMPANIES:
            category, second = categories[company]
            facilities.append(
                self._facility(
                    company,
                    category,
                    second_category=second,
                    capacities=self._amkor_capacities() if company == "Amkor" else None,
                )
            )
        return {
            "format": INPUT_FORMAT,
            "release_id": "ai-critical-fixture-v1",
            "as_of": "2026-08-20",
            "recorded_at": "2026-08-20T16:30:00Z",
            "selection_version": SELECTION_VERSION,
            "atlas_template_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "web" / "atlas-template.html").read_bytes()
            ).hexdigest(),
            "code_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "semiconductor_atlas" / "ai_critical.py").read_bytes()
            ).hexdigest(),
            "builder_script_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "scripts" / "build_ai_critical_release.py").read_bytes()
            ).hexdigest(),
            "atlas_generator_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "web" / "generate_atlas.py").read_bytes()
            ).hexdigest(),
            "companies": list(COMPANIES),
            "sources": [source],
            "ingestion_runs": [ingestion_run],
            "evidence": evidence,
            "facilities": facilities,
        }

    def _write_spec(self, spec: dict[str, object] | None = None) -> None:
        self.input_path.write_text(
            json.dumps(spec or self.spec, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _refresh_evidence_hashes(self, spec: dict[str, object]) -> None:
        sources = {str(row["source_id"]): row for row in spec["sources"]}
        for fragment in spec["evidence"]:
            source = sources[str(fragment["source_id"])]
            source_record_sha256 = hashlib.sha256(
                (
                    json.dumps(
                        source,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
            fragment_payload = {
                "source_id": fragment["source_id"],
                "source_record_sha256": source_record_sha256,
                "role": fragment["role"],
                "locator": fragment["locator"],
                "excerpt": fragment["excerpt"],
                "verification": fragment["verification"],
            }
            if "capacity_assertions" in fragment:
                fragment_payload["capacity_assertions"] = fragment[
                    "capacity_assertions"
                ]
            fragment["fragment_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        fragment_payload,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()

    def test_materializes_complete_bounded_contract_with_unknowns(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        materialized = materialize_baseline(baseline)

        self.assertEqual(7, len(materialized["facilities"]))
        self.assertEqual(2, len(materialized["capacity"]))
        self.assertEqual([], materialized["supply_intelligence"])
        self.assertEqual(7, len(materialized["supply_intelligence_facilities"]))
        self.assertTrue(
            all(
                row["identity_scope"] == "project_site_scope"
                for row in materialized["supply_intelligence_facilities"]
            )
        )
        self.assertEqual(
            2,
            len(
                materialized["supply_intelligence_contract"][
                    "excluded_capacity_claims"
                ]
            ),
        )
        self.assertEqual(
            {row["claim_id"] for row in materialized["capacity"]},
            {
                row["claim_id"]
                for row in materialized["supply_intelligence_contract"][
                    "excluded_capacity_claims"
                ]
            },
        )
        self.assertEqual(
            {"announced": 2, "physical_construction": 0, "tool_installed": 0, "qualified": 0, "economically_usable": 0},
            materialized["coverage"]["capacity_claims_by_basis"],
        )
        self.assertTrue(
            all(row["yield"] == "unknown" for row in materialized["facilities"])
        )
        self.assertTrue(
            all(
                claim["evidence_ids"]
                for claim in materialized["claims"]
            )
        )
        self.assertTrue(
            all(
                claim["claim_confidence"] is None
                and claim["extraction_confidence"] is None
                for claim in materialized["claims"]
            )
        )
        self.assertTrue(
            all(feature["geometry"] is None for feature in materialized["geojson"]["features"])
        )
        self.assertEqual("2026-08-20", materialized["geojson"]["atlas_as_of"])
        self.assertEqual(
            "2026-08-20T16:30:00Z",
            materialized["geojson"]["atlas_recorded_at"],
        )
        self.assertTrue(materialized["geojson"]["attribution"])
        self.assertTrue(
            all(
                feature["properties"]["entity_kind"] == "project_site"
                for feature in materialized["geojson"]["features"]
            )
        )
        self.assertTrue(
            all(
                capability.get("technology") and capability.get("readiness")
                for feature in materialized["geojson"]["features"]
                for capability in feature["properties"]["capabilities"]
            )
        )

    def test_end_to_end_release_map_archive_and_rebuild_are_deterministic(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundles = []
        archives = []
        for index in range(2):
            parent = self.root / f"build-{index}"
            parent.mkdir()
            bundle = parent / f"release-{index}"
            archive = parent / "release.tar.gz"
            write_release(baseline, bundle)
            generate(bundle / "atlas.geojson", bundle / "atlas.html")
            manifest = validate_release(bundle, require_html=True)
            archive_result = write_deterministic_archive(bundle, archive)
            self.assertEqual(RELEASE_FORMAT, manifest["format"])
            self.assertEqual(7, manifest["facility_count"])
            self.assertLess(
                (bundle / "supply_intelligence.jsonl").stat().st_size,
                (bundle / "claims.jsonl").stat().st_size,
            )
            producing_run = json.loads(
                (bundle / "producing_run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["producing_run_id"], producing_run["run_id"])
            self.assertEqual(b"", (bundle / "supply_intelligence.jsonl").read_bytes())
            supply_contract = json.loads(
                (bundle / "supply_intelligence_contract.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(0, supply_contract["record_count"])
            self.assertEqual(
                {
                    "intended_consumer": "Supply Intelligence handoff",
                    "adapter_compatibility": "new_schema_not_legacy_atlas_adapter",
                    "confidence_policy": "null_means_unknown_not_calibrated",
                },
                supply_contract["consumer_boundary"],
            )
            self.assertEqual(
                7,
                len(
                    (bundle / "supply_intelligence_facilities.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
            )
            bundles.append(bundle)
            archives.append((archive, archive_result))

        first_files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in bundles[0].iterdir()
        }
        second_files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in bundles[1].iterdir()
        }
        self.assertEqual(first_files, second_files)
        self.assertEqual(archives[0][1]["sha256"], archives[1][1]["sha256"])
        self.assertEqual(archives[0][0].read_bytes(), archives[1][0].read_bytes())
        with tarfile.open(archives[0][0], "r:gz") as tar:
            self.assertTrue(all(member.mtime == 0 for member in tar.getmembers()))
            self.assertTrue(all(member.uid == 0 and member.gid == 0 for member in tar.getmembers()))
            self.assertTrue(
                all(
                    member.name.startswith("ai-critical-fixture-v1/")
                    for member in tar.getmembers()
                )
            )

    def test_source_bytes_and_release_files_fail_closed_on_mutation(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "release"
        write_release(baseline, bundle)
        (bundle / "claims.jsonl").write_bytes((bundle / "claims.jsonl").read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "does not match manifest"):
            validate_release(bundle)

        self.source_path.write_bytes(self.source_raw + b"mutation")
        with self.assertRaisesRegex(ValueError, "archived bytes do not match"):
            load_baseline(self.input_path, self.root)

        unverified = load_baseline(
            self.input_path,
            self.root,
            verify_source_bytes=False,
        )
        with self.assertRaisesRegex(ValueError, "requires verified archived source bytes"):
            write_release(unverified, self.root / "unverified-release")

    def test_release_reconciles_exports_and_archive_stays_outside_bundle(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "release"
        write_release(baseline, bundle)
        generate(bundle / "atlas.geojson", bundle / "atlas.html")

        with self.assertRaisesRegex(ValueError, "outside the release directory"):
            write_deterministic_archive(bundle, bundle / "release.tar.gz")
        self.assertFalse((bundle / "release.tar.gz").exists())
        self.assertFalse((bundle / ".release.tar.gz.stage").exists())
        validate_release(bundle, require_html=True)

        extra_path = bundle / "payload.sh"
        extra_path.write_bytes(b"echo unexpected\n")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["payload.sh"] = {
            "bytes": extra_path.stat().st_size,
            "sha256": hashlib.sha256(extra_path.read_bytes()).hexdigest(),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "unmanaged file entries"):
            validate_release(bundle, require_html=True)
        extra_path.unlink()
        del manifest["files"]["payload.sh"]
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        html_path = bundle / "atlas.html"
        html_raw = html_path.read_bytes()
        html_path.write_bytes(b"not a standalone atlas\n")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["atlas.html"].update(
            {
                "bytes": html_path.stat().st_size,
                "sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "inconsistent with atlas.geojson"):
            validate_release(bundle, require_html=True)

        html_path.write_bytes(html_raw)
        manifest["files"]["atlas.html"].update(
            {
                "bytes": len(html_raw),
                "sha256": hashlib.sha256(html_raw).hexdigest(),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        facilities = bundle / "facilities.csv"
        facilities.write_bytes(facilities.read_bytes().splitlines(keepends=True)[0])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = facilities.read_bytes()
        manifest["files"]["facilities.csv"] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "inconsistent with cohort"):
            validate_release(bundle, require_html=True)

    def test_build_script_is_runnable_from_repository_root(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/build_ai_critical_release.py", "--help"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("AI-Critical Manufacturing Baseline v1", result.stdout)

    def test_cohort_input_bytes_are_preserved(self) -> None:
        compact = json.dumps(self.spec, sort_keys=False, separators=(",", ":")) + "\n"
        self.input_path.write_text(compact, encoding="utf-8")
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "compact-release"
        write_release(baseline, bundle)
        generate(bundle / "atlas.geojson", bundle / "atlas.html")

        manifest = validate_release(bundle, require_html=True)
        self.assertEqual(compact.encode("utf-8"), (bundle / "cohort.json").read_bytes())
        self.assertEqual(
            hashlib.sha256(compact.encode("utf-8")).hexdigest(),
            manifest["input_sha256"],
        )

    def test_rejects_pre_2024_out_of_scope_and_incomplete_company_inputs(self) -> None:
        pre_2024 = copy.deepcopy(self.spec)
        pre_2024["sources"][0]["published_at"] = "2023-12-31"
        self._write_spec(pre_2024)
        with self.assertRaisesRegex(ValueError, "predates 2024"):
            load_baseline(self.input_path, self.root)

        out_of_scope = copy.deepcopy(self.spec)
        out_of_scope["facilities"][0]["scope_categories"] = ["nand"]
        self._write_spec(out_of_scope)
        with self.assertRaisesRegex(ValueError, "out-of-scope"):
            load_baseline(self.input_path, self.root)

        missing_company = copy.deepcopy(self.spec)
        missing_company["facilities"] = missing_company["facilities"][:-1]
        missing_company["evidence"] = missing_company["evidence"][:-1]
        self._write_spec(missing_company)
        with self.assertRaisesRegex(ValueError, "exactly one row per company"):
            load_baseline(self.input_path, self.root)

        extra_company_row = copy.deepcopy(self.spec)
        duplicate_tsmc = copy.deepcopy(extra_company_row["facilities"][0])
        duplicate_tsmc["facility_key"] = "fixture:tsmc:second"
        extra_company_row["facilities"].insert(1, duplicate_tsmc)
        self._write_spec(extra_company_row)
        with self.assertRaisesRegex(ValueError, "TSMC=2"):
            load_baseline(self.input_path, self.root)

    def test_rejects_announcement_promotion_and_capacity_basis_drift(self) -> None:
        promoted = copy.deepcopy(self.spec)
        promoted["facilities"][0]["capabilities"][0]["readiness"] = "production"
        self._write_spec(promoted)
        with self.assertRaisesRegex(ValueError, "cannot promote"):
            load_baseline(self.input_path, self.root)

        unsupported_basis = copy.deepcopy(self.spec)
        capacity = copy.deepcopy(self._amkor_capacities()[0])
        capacity["basis"] = "nameplate"
        unsupported_basis["facilities"][5]["capacities"] = [capacity]
        unsupported_basis["facilities"][5]["unknowns"]["capacity_bases"] = sorted(CAPACITY_BASES)
        self._write_spec(unsupported_basis)
        with self.assertRaisesRegex(ValueError, "five capacity bases"):
            load_baseline(self.input_path, self.root)

        qualified_unknown = copy.deepcopy(self.spec)
        qualified_capacity = copy.deepcopy(self._amkor_capacities()[0])
        qualified_capacity["basis"] = "qualified"
        qualified_unknown["facilities"][5]["lifecycle"]["state"] = "customer_qualification"
        qualified_unknown["facilities"][5]["capacities"] = [qualified_capacity]
        qualified_unknown["facilities"][5]["unknowns"]["capacity_bases"] = sorted(
            set(CAPACITY_BASES) - {"qualified"}
        )
        self._write_spec(qualified_unknown)
        with self.assertRaisesRegex(ValueError, "qualification is unknown"):
            load_baseline(self.input_path, self.root)

        incompatible_unit = copy.deepcopy(self.spec)
        incompatible_unit["facilities"][5]["capacities"][0]["unit"] = "MW"
        self._write_spec(incompatible_unit)
        with self.assertRaisesRegex(ValueError, "capacity registry"):
            load_baseline(self.input_path, self.root)

        derived = copy.deepcopy(self.spec)
        derived["facilities"][5]["capacities"][0]["claim_kind"] = "derived_estimate"
        self._write_spec(derived)
        with self.assertRaisesRegex(ValueError, "claim_kind is invalid"):
            load_baseline(self.input_path, self.root)

        later_version = copy.deepcopy(self.spec)
        repeated_slice = copy.deepcopy(
            later_version["facilities"][5]["capacities"][0]
        )
        repeated_slice["valid_from"] = "2025-01-02"
        later_version["facilities"][5]["capacities"].append(repeated_slice)
        self._write_spec(later_version)
        self.assertEqual(
            3,
            len(
                materialize_baseline(
                    load_baseline(self.input_path, self.root)
                )["capacity"]
            ),
        )

    def test_rejects_future_scope_drift_and_unresolved_qualification(self) -> None:
        future = copy.deepcopy(self.spec)
        future["facilities"][0]["capabilities"][0]["valid_from"] = "2027-01-01"
        self._write_spec(future)
        with self.assertRaisesRegex(ValueError, "world-state cutoff"):
            load_baseline(self.input_path, self.root)

        scope_drift = copy.deepcopy(self.spec)
        scope_drift["facilities"][0]["scope_categories"].append("hbm_fabrication")
        scope_drift["facilities"][0]["scope_categories"].sort()
        self._write_spec(scope_drift)
        with self.assertRaisesRegex(ValueError, "exactly match capability categories"):
            load_baseline(self.input_path, self.root)

        qualified = copy.deepcopy(self.spec)
        qualified["facilities"][0]["lifecycle"]["state"] = "customer_qualification"
        qualified["facilities"][0]["capabilities"][0]["readiness"] = "qualified"
        self._write_spec(qualified)
        with self.assertRaisesRegex(ValueError, "qualified readiness"):
            load_baseline(self.input_path, self.root)

        future_rights = copy.deepcopy(self.spec)
        future_rights["sources"][0]["rights"]["reviewed_at"] = "2027-01-01"
        self._write_spec(future_rights)
        with self.assertRaisesRegex(ValueError, "rights review exceeds"):
            load_baseline(self.input_path, self.root)

        duplicate = copy.deepcopy(self.spec)
        duplicate["facilities"][0]["capabilities"].append(
            copy.deepcopy(duplicate["facilities"][0]["capabilities"][0])
        )
        self._write_spec(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate capability"):
            load_baseline(self.input_path, self.root)

    def test_publication_precision_is_preserved_in_release_views(self) -> None:
        monthly = copy.deepcopy(self.spec)
        monthly["sources"][0]["published_at"] = "2025-08-01"
        monthly["sources"][0]["published_at_precision"] = "month"
        monthly["sources"][0]["published_at_basis"] = (
            "Source states issuance in August 2025; canonical month start."
        )
        self._refresh_evidence_hashes(monthly)
        self._write_spec(monthly)
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "monthly-release"
        write_release(baseline, bundle)

        evidence = [
            json.loads(line)
            for line in (bundle / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(all(row["published_at_precision"] == "month" for row in evidence))
        self.assertTrue(
            all("canonical month start" in row["published_at_basis"] for row in evidence)
        )
        attribution = (bundle / "ATTRIBUTION.md").read_text(encoding="utf-8")
        self.assertIn("month precision", attribution)
        self.assertIn("canonical month start", attribution)

    def test_rejects_symlinked_source_and_formula_csv_cells_are_escaped(self) -> None:
        symlink = self.root / "source_snapshots" / "linked.html"
        symlink.symlink_to(self.source_path)
        symlinked = copy.deepcopy(self.spec)
        symlinked["sources"][0]["archive_path"] = "source_snapshots/linked.html"
        self._write_spec(symlinked)
        with self.assertRaisesRegex(ValueError, "symlink"):
            load_baseline(self.input_path, self.root)

        traversal = copy.deepcopy(self.spec)
        traversal["sources"][0]["archive_path"] = "../../outside.html"
        self._refresh_evidence_hashes(traversal)
        self._write_spec(traversal)
        with self.assertRaisesRegex(ValueError, "safe relative POSIX path"):
            load_baseline(
                self.input_path,
                self.root,
                verify_source_bytes=False,
            )

        self._write_spec(self.spec)
        baseline = load_baseline(self.input_path, self.root)
        injected = copy.deepcopy(baseline.spec)
        injected["facilities"][0]["name"] = "=1+1"
        self._write_spec(injected)
        injection_baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "formula-release"
        write_release(injection_baseline, bundle)
        rows = list(csv.DictReader(io.StringIO((bundle / "facilities.csv").read_text())))
        self.assertEqual("'=1+1", rows[0]["facility_name"])

    def test_manifest_and_managed_file_schemas_are_exact(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "exact-manifest-release"
        write_release(baseline, bundle)
        manifest_path = bundle / "manifest.json"
        original = manifest_path.read_bytes()

        for mutation in ("unknown_manifest_field", "code_sha256", "file_metadata"):
            manifest = json.loads(original)
            if mutation == "unknown_manifest_field":
                manifest["unrecognized_release_assertion"] = "publish_ready"
            elif mutation == "code_sha256":
                manifest["code_sha256"] = "0" * 64
            else:
                manifest["files"]["claims.jsonl"]["unrecognized"] = True
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_release(bundle)
            manifest_path.write_bytes(original)

    def test_evidence_fragment_identity_and_support_role_fail_closed(self) -> None:
        original = load_baseline(self.input_path, self.root)
        original_claims = {
            (row["subject_stable_key"], row["predicate"]): row["claim_id"]
            for row in materialize_baseline(original)["claims"]
        }

        stale = copy.deepcopy(self.spec)
        stale["evidence"][0]["locator"] += " changed"
        self._write_spec(stale)
        with self.assertRaisesRegex(ValueError, "does not bind the evidence content"):
            load_baseline(self.input_path, self.root)

        changed = copy.deepcopy(stale)
        changed["evidence"][0]["excerpt"] = (
            "Official revised scoped statement for TSMC."
        )
        self._refresh_evidence_hashes(changed)
        self._write_spec(changed)
        changed_claims = {
            (row["subject_stable_key"], row["predicate"]): row["claim_id"]
            for row in materialize_baseline(
                load_baseline(self.input_path, self.root)
            )["claims"]
        }
        self.assertNotEqual(
            original_claims[("fixture:tsmc", "facility.name")],
            changed_claims[("fixture:tsmc", "facility.name")],
        )

        unverified_text = copy.deepcopy(self.spec)
        unverified_text["evidence"][0]["excerpt"] = (
            "This text does not occur in the archived source."
        )
        self._refresh_evidence_hashes(unverified_text)
        self._write_spec(unverified_text)
        with self.assertRaisesRegex(ValueError, "do not resolve in one archived source"):
            load_baseline(self.input_path, self.root)

        for vacuous_excerpt in ("...", "…", "Official"):
            vacuous = copy.deepcopy(self.spec)
            vacuous["evidence"][0]["excerpt"] = vacuous_excerpt
            self._refresh_evidence_hashes(vacuous)
            self._write_spec(vacuous)
            with self.subTest(vacuous_excerpt=vacuous_excerpt):
                with self.assertRaisesRegex(ValueError, "substantive source text"):
                    load_baseline(self.input_path, self.root)

        cross_field = copy.deepcopy(self.spec)
        cross_field_raw = json.dumps(
            {"title": "alpha source", "unrelated": "statement beta"}
        ).encode("utf-8")
        self.source_path.write_bytes(cross_field_raw)
        cross_field_source = cross_field["sources"][0]
        cross_field_source["archive_path"] = "source_snapshots/official.html"
        cross_field_source["media_type"] = "application/json"
        cross_field_source["bytes"] = len(cross_field_raw)
        cross_field_source["content_sha256"] = hashlib.sha256(
            cross_field_raw
        ).hexdigest()
        cross_field["ingestion_runs"][0]["inputs"][0]["content_sha256"] = (
            cross_field_source["content_sha256"]
        )
        cross_field["evidence"][0]["excerpt"] = "alpha source statement beta"
        self._refresh_evidence_hashes(cross_field)
        self._write_spec(cross_field)
        with self.assertRaisesRegex(
            ValueError, "do not resolve in one archived source record"
        ):
            load_baseline(self.input_path, self.root)

        self.source_path.write_bytes(self.source_raw)

        hash_then_swap = copy.deepcopy(self.spec)
        invented_excerpt = (
            "Invented long evidence text that exists only in replacement bytes."
        )
        hash_then_swap["evidence"][0]["excerpt"] = invented_excerpt
        self._refresh_evidence_hashes(hash_then_swap)
        self._write_spec(hash_then_swap)
        actual_read = _read_safe_source_bytes

        def read_then_replace(*args: object, **kwargs: object) -> bytes:
            raw = actual_read(*args, **kwargs)
            self.source_path.write_bytes(
                self.source_raw + invented_excerpt.encode("utf-8")
            )
            return raw

        with patch(
            "semiconductor_atlas.ai_critical._read_safe_source_bytes",
            side_effect=read_then_replace,
        ):
            with self.assertRaisesRegex(
                ValueError, "do not resolve in one archived source record"
            ):
                load_baseline(self.input_path, self.root)
        self.source_path.write_bytes(self.source_raw)

        future_review = copy.deepcopy(self.spec)
        future_review["evidence"][0]["verification"]["reviewed_at"] = (
            "2026-08-20T16:31:00Z"
        )
        self._refresh_evidence_hashes(future_review)
        self._write_spec(future_review)
        with self.assertRaisesRegex(ValueError, "exceeds the knowledge cutoff"):
            load_baseline(self.input_path, self.root)

        wrong_method = copy.deepcopy(self.spec)
        wrong_method["evidence"][0]["verification"]["method"] = (
            "manual_pdf_visual_review_v1"
        )
        self._refresh_evidence_hashes(wrong_method)
        self._write_spec(wrong_method)
        with self.assertRaisesRegex(ValueError, "invalid for the source media"):
            load_baseline(self.input_path, self.root)

        refute_only = copy.deepcopy(self.spec)
        refute_only["evidence"][0]["role"] = "refute"
        self._refresh_evidence_hashes(refute_only)
        self._write_spec(refute_only)
        with self.assertRaisesRegex(ValueError, "must be support"):
            load_baseline(self.input_path, self.root)

        mutated_source = copy.deepcopy(self.spec)
        mutated_raw = self.source_raw + b"new source version\n"
        self.source_path.write_bytes(mutated_raw)
        mutated_source["sources"][0]["bytes"] = len(mutated_raw)
        mutated_source["sources"][0]["content_sha256"] = hashlib.sha256(
            mutated_raw
        ).hexdigest()
        mutated_source["ingestion_runs"][0]["inputs"][0]["content_sha256"] = (
            mutated_source["sources"][0]["content_sha256"]
        )
        self._write_spec(mutated_source)
        with self.assertRaisesRegex(ValueError, "does not bind the evidence content"):
            load_baseline(self.input_path, self.root)

    def test_source_records_are_reusable_across_release_runs(self) -> None:
        first = load_baseline(self.input_path, self.root)
        first_bundle = self.root / "first-release"
        write_release(first, first_bundle)

        second_spec = copy.deepcopy(self.spec)
        second_spec["release_id"] = "ai-critical-fixture-v1-second-run"
        self._write_spec(second_spec)
        second = load_baseline(self.input_path, self.root)
        second_bundle = self.root / "second-release"
        write_release(second, second_bundle)

        first_sources = json.loads(
            (first_bundle / "source_inputs.json").read_text(encoding="utf-8")
        )
        second_sources = json.loads(
            (second_bundle / "source_inputs.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            first_sources[0]["source_record_sha256"],
            second_sources[0]["source_record_sha256"],
        )
        self.assertEqual(
            first_sources[0]["ingestion_run_id"],
            second_sources[0]["ingestion_run_id"],
        )
        first_run = json.loads(
            (first_bundle / "producing_run.json").read_text(encoding="utf-8")
        )
        second_run = json.loads(
            (second_bundle / "producing_run.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(first_run["run_id"], second_run["run_id"])

    def test_claim_version_identity_binds_system_time_and_producing_run(self) -> None:
        first_claims = {
            row["claim_id"]
            for row in materialize_baseline(
                load_baseline(self.input_path, self.root)
            )["claims"]
        }
        later = copy.deepcopy(self.spec)
        later["recorded_at"] = "2026-08-20T16:31:00Z"
        self._write_spec(later)
        second_claims = {
            row["claim_id"]
            for row in materialize_baseline(
                load_baseline(self.input_path, self.root)
            )["claims"]
        }
        self.assertTrue(first_claims.isdisjoint(second_claims))

    def test_supply_export_never_relabels_monthly_rates_as_quarter_totals(self) -> None:
        materialized = materialize_baseline(
            load_baseline(self.input_path, self.root)
        )
        self.assertEqual([], materialized["supply_intelligence"])
        self.assertTrue(
            all(
                row["reason"] == "quantity_semantics_not_quarter_total"
                for row in materialized["supply_intelligence_contract"][
                    "excluded_capacity_claims"
                ]
            )
        )

        quarterly_period = copy.deepcopy(self.spec)
        for capacity in quarterly_period["facilities"][5]["capacities"]:
            capacity["period_start"] = "2026-01-01"
            capacity["period_end"] = "2026-04-01"
        self._write_spec(quarterly_period)
        with self.assertRaisesRegex(
            ValueError, "not bound to an exact reviewed capacity assertion"
        ):
            load_baseline(self.input_path, self.root)

        relabelled_monthly_evidence = copy.deepcopy(self.spec)
        relabelled = relabelled_monthly_evidence["facilities"][5]["capacities"][0]
        relabelled.update(
            {
                "unit": "units/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
            }
        )
        self._write_spec(relabelled_monthly_evidence)
        with self.assertRaisesRegex(
            ValueError, "not bound to an exact reviewed capacity assertion"
        ):
            load_baseline(self.input_path, self.root)

        cross_metric_swap = copy.deepcopy(self.spec)
        swapped_capacity = cross_metric_swap["facilities"][5]["capacities"][0]
        swapped_capacity.update({"low": 14_500, "base": 14_500, "high": 14_500})
        assertions = next(
            evidence["capacity_assertions"]
            for evidence in cross_metric_swap["evidence"]
            if evidence["evidence_id"] == "evidence-amkor"
        )
        swapped_assertion = next(
            assertion
            for assertion in assertions
            if assertion["metric"] == "units_throughput"
        )
        swapped_assertion.update({"low": 14_500, "base": 14_500, "high": 14_500})
        assertions.sort(
            key=lambda assertion: json.dumps(
                assertion,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        self._refresh_evidence_hashes(cross_metric_swap)
        self._write_spec(cross_metric_swap)
        with self.assertRaisesRegex(
            ValueError, "does not match the verified evidence excerpt"
        ):
            load_baseline(self.input_path, self.root)

        probe_assertion = self._capacity_assertion(
            self._amkor_capacities()[0]
        )
        probe_assertion.update({"low": 14_500, "base": 14_500, "high": 14_500})
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                probe_assertion,
                "Amkor announced 3,700,000 units/month, 14,500 wafers/month upon completion.",
            )
        )
        original_units_assertion = self._capacity_assertion(
            self._amkor_capacities()[0]
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                original_units_assertion,
                "The project was announced. A separate qualified line supports 3,700,000 units/month.",
            )
        )
        quarter_probe = copy.deepcopy(original_units_assertion)
        quarter_probe.update(
            {
                "unit": "units/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
            }
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                quarter_probe,
                "Amkor has not announced a 2026 Q1 quarter total of 3,700,000 units.",
            )
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                quarter_probe,
                "Amkor reported an unannounced quarter total of 3,700,000 units in 2026 Q1.",
            )
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                quarter_probe,
                "Amkor denied that it announced a quarter total of 3,700,000 units in 2026 Q1.",
            )
        )
        wafers_quarter_probe = self._capacity_assertion(
            self._amkor_capacities()[1]
        )
        wafers_quarter_probe.update(
            {
                "unit": "wafers/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
            }
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                wafers_quarter_probe,
                "Amkor announced 14,500 wafers (per month), while a separate "
                "2026 Q1 quarter total was 3,700,000 units.",
            )
        )
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                wafers_quarter_probe,
                "Amkor announced a quarter total of 14,500 wafers produced each "
                "month in 2026 Q1.",
            )
        )
        for misleading_rate in (
            "Amkor announced a quarter total of 3,700,000 units, while annual "
            "output is 14,500 wafers in 2026 Q1.",
            "Amkor announced a quarter total of 14,500 wafers per annum in 2026 Q1.",
            "Amkor announced a quarter total of 3,700,000 units, while lifetime "
            "output is 14,500 wafers in 2026 Q1.",
            "Amkor announced a quarter total of 14,500 wafers in 2025 Q4, "
            "versus 3,700,000 units in 2026 Q1.",
        ):
            self.assertFalse(
                _capacity_assertion_matches_excerpt(
                    wafers_quarter_probe,
                    misleading_rate,
                )
            )
        tool_quarter_probe = copy.deepcopy(quarter_probe)
        tool_quarter_probe["basis"] = "tool_installed"
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                tool_quarter_probe,
                "Equipment installed for a legacy line, while Amkor announced "
                "a quarter total of 3,700,000 units in 2026 Q1 for a future project.",
            )
        )
        tool_monthly_probe = copy.deepcopy(original_units_assertion)
        tool_monthly_probe["basis"] = "tool_installed"
        self.assertFalse(
            _capacity_assertion_matches_excerpt(
                tool_monthly_probe,
                "Equipment installed for a legacy line, while the future project "
                "is expected to produce 3,700,000 units/month.",
            )
        )
        large_assertion = copy.deepcopy(original_units_assertion)
        large_capacity = copy.deepcopy(original_units_assertion)
        large_assertion.update(
            {
                "low": 9_007_199_254_740_992,
                "base": 9_007_199_254_740_992,
                "high": 9_007_199_254_740_992,
            }
        )
        large_capacity.update(
            {
                "low": 9_007_199_254_740_993,
                "base": 9_007_199_254_740_993,
                "high": 9_007_199_254_740_993,
            }
        )
        self.assertNotEqual(
            _capacity_assertion_signature(large_assertion),
            _capacity_assertion_signature(large_capacity),
        )
        invalid_large_range = copy.deepcopy(self.spec)
        invalid_values = {
            "low": 9_007_199_254_740_993,
            "base": 9_007_199_254_740_992,
            "high": 9_007_199_254_740_992,
        }
        invalid_large_range["facilities"][5]["capacities"][0].update(
            invalid_values
        )
        invalid_assertions = next(
            evidence["capacity_assertions"]
            for evidence in invalid_large_range["evidence"]
            if evidence["evidence_id"] == "evidence-amkor"
        )
        next(
            assertion
            for assertion in invalid_assertions
            if assertion["metric"] == "units_throughput"
        ).update(invalid_values)
        self._refresh_evidence_hashes(invalid_large_range)
        self._write_spec(invalid_large_range)
        with self.assertRaisesRegex(ValueError, "0 <= low <= base <= high"):
            load_baseline(self.input_path, self.root)

        omission_join = copy.deepcopy(self.spec)
        wafers_capacity = omission_join["facilities"][5]["capacities"][1]
        wafers_capacity.update(
            {
                "unit": "wafers/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
            }
        )
        amkor_fragment = next(
            evidence
            for evidence in omission_join["evidence"]
            if evidence["evidence_id"] == "evidence-amkor"
        )
        wafers_assertion = next(
            assertion
            for assertion in amkor_fragment["capacity_assertions"]
            if assertion["metric"] == "wafers_throughput"
        )
        wafers_assertion.update(
            {
                "unit": "wafers/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
            }
        )
        amkor_fragment["excerpt"] = (
            "Official scoped statement for Amkor: upon completion, the facility "
            "is expected to produce 3,700,000 units/month and 14,500 wafers/month. ... "
            "Amkor announced quarter total: 3,700,000 units in 2026 Q1."
        )
        self._refresh_evidence_hashes(omission_join)
        self._write_spec(omission_join)
        with self.assertRaisesRegex(
            ValueError, "does not match the verified evidence excerpt"
        ):
            load_baseline(self.input_path, self.root)

        eligible = copy.deepcopy(self.spec)
        capacity = eligible["facilities"][5]["capacities"][0]
        quarter_evidence_id = "evidence-amkor-quarter-total"
        eligible["evidence"].append(
            {
                "evidence_id": quarter_evidence_id,
                "source_id": "official-fixture",
                "role": "support",
                "locator": "explicit 2026 Q1 quarter-total statement",
                "excerpt": "Amkor announced quarter total: 3,700,000 units in 2026 Q1.",
                "verification": {
                    "method": "normalized_text_segments_v1",
                    "reviewer": "local:unittest-review",
                    "reviewed_at": "2026-08-20T16:20:00Z",
                    "result": "matched_archived_source",
                },
                "fragment_sha256": "0" * 64,
            }
        )
        capacity.update(
            {
                "unit": "units/quarter",
                "quantity_semantics": "quarter_total",
                "input_output_basis": "source_stated_quarter_total",
                "period_start": "2026-01-01",
                "period_end": "2026-04-01",
                "evidence_ids": [quarter_evidence_id],
            }
        )
        eligible["evidence"][-1]["capacity_assertions"] = [
            self._capacity_assertion(capacity)
        ]
        self._refresh_evidence_hashes(eligible)
        self._write_spec(eligible)
        materialized = materialize_baseline(
            load_baseline(self.input_path, self.root)
        )
        self.assertEqual(1, len(materialized["supply_intelligence"]))
        exported = materialized["supply_intelligence"][0]
        self.assertEqual(
            "source_stated_quarter_total", exported["input_output_basis"]
        )
        self.assertEqual("project_addition", exported["scope_kind"])
        self.assertEqual(
            ["advanced_packaging", "advanced_test"],
            exported["technology_scope"],
        )
        self.assertEqual([quarter_evidence_id], exported["evidence_ids"])
        self.assertEqual(
            set(exported),
            set(
                materialized["supply_intelligence_contract"]["eligibility"][
                    "required_fields"
                ]
            ),
        )

    def test_exclusive_install_primitives_do_not_replace_existing_paths(self) -> None:
        staged_directory = self.root / "staged-directory"
        staged_directory.mkdir()
        (staged_directory / "payload").write_text("staged", encoding="utf-8")
        destination_directory = self.root / "destination-directory"
        destination_directory.mkdir()
        (destination_directory / "owner").write_text("user", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            install_directory_exclusive(staged_directory, destination_directory)
        self.assertEqual(
            "user",
            (destination_directory / "owner").read_text(encoding="utf-8"),
        )
        self.assertTrue(staged_directory.is_dir())

        staged_file = self.root / "staged-file"
        staged_file.write_text("staged", encoding="utf-8")
        destination_file = self.root / "destination-file"
        destination_file.write_text("user", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            install_file_exclusive(staged_file, destination_file)
        self.assertEqual("user", destination_file.read_text(encoding="utf-8"))
        self.assertEqual("staged", staged_file.read_text(encoding="utf-8"))

        cleanup_source = self.root / "cleanup-source"
        cleanup_source.write_text("owned", encoding="utf-8")
        cleanup_destination = self.root / "cleanup-destination"
        original_unlink = os.unlink

        def fail_source_unlink(path: object, *args: object, **kwargs: object) -> None:
            if path == cleanup_source.name and kwargs.get("dir_fd") is not None:
                raise OSError("injected source cleanup failure")
            original_unlink(path, *args, **kwargs)

        with patch("os.unlink", side_effect=fail_source_unlink):
            install_file_exclusive(cleanup_source, cleanup_destination)
        self.assertTrue(cleanup_source.is_file())
        self.assertTrue(cleanup_destination.samefile(cleanup_source))
        cleanup_source.unlink()
        self.assertEqual("owned", cleanup_destination.read_text(encoding="utf-8"))

    def test_rejects_reviewed_point_misrepresented_as_source_statement(self) -> None:
        reviewed = copy.deepcopy(self.spec)
        geography = reviewed["facilities"][0]["geography"]
        geography.update(
            {
                "latitude": 33.4484,
                "longitude": -112.074,
                "precision_m": 100,
                "geometry_scope": "reviewed_facility_point",
            }
        )
        self._write_spec(reviewed)
        with self.assertRaisesRegex(ValueError, "not facility-safe"):
            load_baseline(self.input_path, self.root)

    def test_build_race_does_not_clobber_appearing_destinations(self) -> None:
        output = self.root / "raced-release"

        def create_competing_directory(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            destination.mkdir()
            (destination / "owner").write_text("user", encoding="utf-8")
            install_directory_exclusive(source, destination, **kwargs)

        with patch(
            "scripts.build_ai_critical_release.install_directory_exclusive",
            side_effect=create_competing_directory,
        ):
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                build(self.input_path, self.root, output, None)
        self.assertEqual("user", (output / "owner").read_text(encoding="utf-8"))
        self.assertFalse(any(self.root.glob(".raced-release.build-*")))

        archive_output = self.root / "archive-race-release"
        archive = self.root / "archive-race-release.tar.gz"

        def create_competing_archive(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            if destination.name.endswith(".publish-transaction.json"):
                install_file_exclusive(source, destination, **kwargs)
                return
            destination.write_text("user", encoding="utf-8")
            install_file_exclusive(source, destination, **kwargs)

        with patch(
            "scripts.build_ai_critical_release.install_file_exclusive",
            side_effect=create_competing_archive,
        ):
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                build(self.input_path, self.root, archive_output, archive)
        validate_release(archive_output, require_html=True)
        self.assertEqual("user", archive.read_text(encoding="utf-8"))
        self.assertTrue(
            (self.root / ".archive-race-release.publish-transaction.json").is_file()
        )
        self.assertTrue(any(self.root.glob(".archive-race-release.build-*")))

        replacement_output = self.root / "replacement-race-release"
        replacement_archive = self.root / "replacement-race-release.tar.gz"
        moved_owned_output = self.root / "moved-owned-release"

        def replace_output_before_archive(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            if destination.name.endswith(".publish-transaction.json"):
                install_file_exclusive(source, destination, **kwargs)
                return
            replacement_output.rename(moved_owned_output)
            replacement_output.mkdir()
            (replacement_output / "owner").write_text("user", encoding="utf-8")
            destination.write_text("user archive", encoding="utf-8")
            install_file_exclusive(source, destination, **kwargs)

        with patch(
            "scripts.build_ai_critical_release.install_file_exclusive",
            side_effect=replace_output_before_archive,
        ):
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                build(
                    self.input_path,
                    self.root,
                    replacement_output,
                    replacement_archive,
                )
        self.assertEqual(
            "user", (replacement_output / "owner").read_text(encoding="utf-8")
        )
        self.assertTrue(moved_owned_output.is_dir())
        self.assertEqual(
            "user archive", replacement_archive.read_text(encoding="utf-8")
        )
        self.assertTrue(
            (self.root / ".replacement-race-release.publish-transaction.json").is_file()
        )

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        alias_parent = self.root / "alias-parent"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not traverse a symlink"):
            build(
                self.input_path,
                self.root,
                alias_parent / "nested" / "release",
                None,
            )
        self.assertFalse((real_parent / "nested").exists())

    def test_build_preserves_a_marked_recovery_state_after_commit_failure(self) -> None:
        output = self.root / "interrupted-release"
        actual_install = install_directory_exclusive

        def commit_then_interrupt(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            actual_install(source, destination, **kwargs)
            raise KeyboardInterrupt("injected commit interruption")

        with patch(
            "scripts.build_ai_critical_release.install_directory_exclusive",
            side_effect=commit_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                build(self.input_path, self.root, output, None)
        validate_release(output, require_html=True)
        self.assertTrue(
            (self.root / ".interrupted-release.publish-transaction.json").is_file()
        )
        self.assertTrue(any(self.root.glob(".interrupted-release.build-*")))

        mutated_output = self.root / "mutated-release"

        def commit_then_mutate(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            actual_install(source, destination, **kwargs)
            claims = destination / "claims.jsonl"
            claims.write_bytes(claims.read_bytes() + b"\n")

        with patch(
            "scripts.build_ai_critical_release.install_directory_exclusive",
            side_effect=commit_then_mutate,
        ):
            with self.assertRaisesRegex(ValueError, "does not match manifest"):
                build(self.input_path, self.root, mutated_output, None)
        with self.assertRaisesRegex(ValueError, "does not match manifest"):
            validate_release(mutated_output, require_html=True)
        self.assertTrue(
            (self.root / ".mutated-release.publish-transaction.json").is_file()
        )

    def test_marker_cleanup_preserves_a_concurrent_replacement(self) -> None:
        marker = self.root / ".release.publish-transaction.json"
        marker.write_text("owned", encoding="utf-8")
        marker_status = marker.lstat()
        marker_identity = (marker_status.st_dev, marker_status.st_ino)
        parent_status = self.root.lstat()
        parent_identity = (parent_status.st_dev, parent_status.st_ino)
        quarantine_parent = self.root / "marker-stage"
        quarantine_parent.mkdir()
        quarantine_status = quarantine_parent.lstat()
        quarantine_identity = (
            quarantine_status.st_dev,
            quarantine_status.st_ino,
        )
        moved_owned = self.root / "owned-marker-aside"

        def replace_before_capture(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            if source == marker:
                marker.rename(moved_owned)
                marker.write_text("replacement", encoding="utf-8")
            _rename_exclusive(source, destination, **kwargs)

        with patch(
            "scripts.build_ai_critical_release._rename_exclusive",
            side_effect=replace_before_capture,
        ):
            with self.assertRaisesRegex(OSError, "marker identity changed"):
                _remove_owned_marker(
                    marker,
                    marker_identity,
                    parent_identity,
                    quarantine_parent,
                    quarantine_identity,
                )
        self.assertEqual("replacement", marker.read_text(encoding="utf-8"))
        self.assertEqual("owned", moved_owned.read_text(encoding="utf-8"))
        self.assertEqual([], list(quarantine_parent.iterdir()))

    def test_build_rejects_archive_replaced_before_owned_hash(self) -> None:
        output = self.root / "archive-prehash-race-release"
        archive = self.root / "archive-prehash-race-release.tar.gz"

        def link_then_replace(
            source: Path, destination: Path, **kwargs: object
        ) -> None:
            _link_file_exclusive(source, destination, **kwargs)
            if destination.name == archive.name:
                destination.rename(destination.with_name(destination.name + ".owned"))
                destination.write_bytes(b"not a tar archive\n")

        with patch(
            "semiconductor_atlas.ai_critical._link_file_exclusive",
            side_effect=link_then_replace,
        ):
            with self.assertRaisesRegex(OSError, "archive output identity changed"):
                build(self.input_path, self.root, output, archive)
        self.assertFalse(output.exists())
        self.assertFalse(archive.exists())
        self.assertFalse(any(self.root.glob(".archive-prehash-race-release.build-*")))

    def test_build_rejects_an_ancestor_retarget_before_publication(self) -> None:
        outer = self.root / "retarget"
        live = outer / "live"
        release_parent = live / "releases"
        release_parent.mkdir(parents=True)
        moved_live = outer / "live-original"
        attacker = outer / "attacker"
        (attacker / "releases").mkdir(parents=True)
        output = release_parent / "release"
        actual_generate = generate

        def generate_then_retarget(source: Path, destination: Path) -> None:
            actual_generate(source, destination)
            stage_name = destination.parent.parent.name
            live.rename(moved_live)
            live.symlink_to(attacker, target_is_directory=True)
            original_stage = moved_live / "releases" / stage_name
            redirected_stage = attacker / "releases" / stage_name
            shutil.copytree(original_stage, redirected_stage)

        with patch(
            "scripts.build_ai_critical_release.generate",
            side_effect=generate_then_retarget,
        ):
            with self.assertRaisesRegex(OSError, "parent identity changed"):
                build(self.input_path, self.root, output, None)
        self.assertFalse((attacker / "releases" / "release").exists())
        self.assertFalse((moved_live / "releases" / "release").exists())

    def test_archive_detects_managed_file_removed_after_initial_validation(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "archive-race-bundle"
        archive = self.root / "archive-race.tar.gz"
        write_release(baseline, bundle)
        generate(bundle / "atlas.geojson", bundle / "atlas.html")
        real_validate = validate_release
        calls = 0

        def validate_then_remove(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            result = real_validate(*args, **kwargs)
            calls += 1
            if calls == 1:
                (bundle / "claims.jsonl").unlink()
            return result

        with patch(
            "semiconductor_atlas.ai_critical.validate_release",
            side_effect=validate_then_remove,
        ):
            with self.assertRaisesRegex(ValueError, "claims.jsonl"):
                write_deterministic_archive(bundle, archive)
        self.assertFalse(archive.exists())
        self.assertFalse(any(self.root.glob(".archive-race.tar.gz.stage-*")))

    def test_archive_binds_the_validated_manifest_bytes(self) -> None:
        baseline = load_baseline(self.input_path, self.root)
        bundle = self.root / "manifest-race-bundle"
        archive = self.root / "manifest-race.tar.gz"
        write_release(baseline, bundle)
        generate(bundle / "atlas.geojson", bundle / "atlas.html")
        manifest_path = bundle / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        actual_validate = validate_release
        calls = 0

        def validate_then_replace(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            result = actual_validate(*args, **kwargs)
            calls += 1
            if calls == 1:
                manifest_path.write_bytes(b'{"attacker":"not the release manifest"}\n')
            return result

        try:
            with patch(
                "semiconductor_atlas.ai_critical.validate_release",
                side_effect=validate_then_replace,
            ):
                with self.assertRaisesRegex(ValueError, "manifest changed"):
                    write_deterministic_archive(bundle, archive)
        finally:
            manifest_path.write_bytes(original_manifest)
        self.assertFalse(archive.exists())
        self.assertFalse(any(self.root.glob(".manifest-race.tar.gz.stage-*")))

    def test_ingestion_run_contract_and_clocks_fail_closed(self) -> None:
        cross_source = copy.deepcopy(self.spec)
        second_source = copy.deepcopy(cross_source["sources"][0])
        second_source["source_id"] = "official-fixture-two"
        cross_source["sources"].append(second_source)
        cross_source["ingestion_runs"][0]["source_id"] = second_source["source_id"]
        self._write_spec(cross_source)
        with self.assertRaisesRegex(ValueError, "must match the ingestion run source_id"):
            load_baseline(self.input_path, self.root)

        stale_configuration = copy.deepcopy(self.spec)
        stale_configuration["ingestion_runs"][0]["configuration"]["source_count"] = 2
        self._write_spec(stale_configuration)
        with self.assertRaisesRegex(ValueError, "configuration_sha256 is inconsistent"):
            load_baseline(self.input_path, self.root)

        false_configuration = copy.deepcopy(self.spec)
        configuration = false_configuration["ingestion_runs"][0]["configuration"]
        configuration["source_count"] = 9
        false_configuration["ingestion_runs"][0]["configuration_sha256"] = (
            hashlib.sha256(
                (
                    json.dumps(
                        configuration,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()
        )
        self._write_spec(false_configuration)
        with self.assertRaisesRegex(ValueError, "source_count is inconsistent"):
            load_baseline(self.input_path, self.root)

        nonterminal = copy.deepcopy(self.spec)
        nonterminal["ingestion_runs"][0]["outcome"] = "accepted"
        self._write_spec(nonterminal)
        with self.assertRaisesRegex(ValueError, "outcome must be succeeded"):
            load_baseline(self.input_path, self.root)

        before_acquisition = copy.deepcopy(self.spec)
        before_acquisition["ingestion_runs"][0]["started_at"] = (
            "2026-08-20T15:59:59Z"
        )
        before_acquisition["ingestion_runs"][0]["finished_at"] = (
            "2026-08-20T16:00:01Z"
        )
        self._write_spec(before_acquisition)
        with self.assertRaisesRegex(ValueError, "acquired after the run started"):
            load_baseline(self.input_path, self.root)

        zero_duration = copy.deepcopy(self.spec)
        zero_duration["ingestion_runs"][0]["finished_at"] = zero_duration[
            "ingestion_runs"
        ][0]["started_at"]
        self._write_spec(zero_duration)
        with self.assertRaisesRegex(ValueError, "must finish after it starts"):
            load_baseline(self.input_path, self.root)

    def test_checked_in_production_cohort_contract(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        baseline = load_baseline(
            repository_root / "baselines" / "ai_critical_manufacturing_v1.json",
            repository_root,
            verify_source_bytes=False,
        )
        materialized = materialize_baseline(baseline)
        self.assertEqual(
            {
                (
                    "amkor:peoria-advanced-packaging-project",
                    "Amkor",
                    "Amkor Peoria advanced packaging and test project",
                    "project_site_scope",
                    "announced",
                    ("advanced_packaging", "advanced_test"),
                ),
                (
                    "ase:kaohsiung-site",
                    "ASE",
                    "ASE Kaohsiung Site aggregate",
                    "campus_scope",
                    "complete",
                    ("advanced_packaging", "advanced_test"),
                ),
                (
                    "intel:fab52-chandler",
                    "Intel",
                    "Intel Fab 52",
                    "named_facility",
                    "ramping",
                    ("leading_edge_logic",),
                ),
                (
                    "micron:singapore-hbm-packaging-project",
                    "Micron",
                    "Micron Singapore HBM advanced packaging facility project",
                    "project_site_scope",
                    "site_preparation",
                    ("advanced_packaging", "hbm_packaging"),
                ),
                (
                    "samsung:taylor-leading-edge-project",
                    "Samsung",
                    "Samsung Taylor leading-edge logic project (two-fab aggregate)",
                    "project_site_scope",
                    "announced",
                    ("leading_edge_logic",),
                ),
                (
                    "sk-hynix:m15x-cheongju",
                    "SK hynix",
                    "SK hynix M15X",
                    "named_facility",
                    "tools_installing",
                    ("hbm_fabrication",),
                ),
                (
                    "tsmc:fab21-arizona",
                    "TSMC",
                    "TSMC Fab 21 (first Arizona fab)",
                    "named_facility",
                    "complete",
                    ("leading_edge_logic",),
                ),
            },
            {
                (
                    row["facility_key"],
                    row["company"],
                    row["name"],
                    row["identity_scope"],
                    row["lifecycle"]["state"],
                    tuple(row["scope_categories"]),
                )
                for row in baseline.facilities
            },
        )
        self.assertEqual(
            {
                "ase-kaohsiung-2024-report",
                "intel-fab52-2025",
                "micron-2025-q3-10q",
                "nist-amkor-peoria",
                "nist-samsung-texas",
                "nist-tsmc-arizona",
                "sk-hynix-m15x-2024",
                "sk-hynix-q3-2025",
                "tsmc-2025-20f",
            },
            set(baseline.sources),
        )
        self.assertEqual(
            {
                "amkor-peoria-capacity",
                "amkor-peoria-technology",
                "ase-kaohsiung-capability",
                "ase-kaohsiung-identity",
                "intel-fab52-18a-production",
                "micron-singapore-hbm-groundbreaking",
                "samsung-taylor-leading-edge",
                "sk-m15x-hbm-scope",
                "sk-m15x-tools-installing",
                "tsmc-fab21-hvm",
                "tsmc-fab21-leading-edge",
                "tsmc-fab21-operating-table",
            },
            set(baseline.evidence),
        )
        self.assertEqual(
            {
                (
                    "Amkor",
                    "Amkor Peoria advanced packaging and test project",
                    "units_throughput",
                    "announced",
                    "units/month",
                    3_700_000.0,
                    3_700_000.0,
                    3_700_000.0,
                ),
                (
                    "Amkor",
                    "Amkor Peoria advanced packaging and test project",
                    "wafers_throughput",
                    "announced",
                    "wafers/month",
                    14_500.0,
                    14_500.0,
                    14_500.0,
                ),
            },
            {
                (
                    row["company"],
                    row["facility_name"],
                    row["metric"],
                    row["basis"],
                    row["unit"],
                    row["low"],
                    row["base"],
                    row["high"],
                )
                for row in materialized["capacity"]
            },
        )
        self.assertEqual(7, len(materialized["facilities"]))
        self.assertEqual(75, len(materialized["claims"]))
        self.assertEqual(12, len(materialized["evidence"]))
        self.assertEqual(9, len(baseline.sources))
        self.assertEqual(
            Counter(
                {
                    "named_facility": 3,
                    "project_site_scope": 3,
                    "campus_scope": 1,
                }
            ),
            Counter(
                row["identity_scope"]
                for row in materialized["supply_intelligence_facilities"]
            ),
        )
        self.assertEqual([], materialized["supply_intelligence"])
        canonical_predicates = {
            "facility.name",
            "geography.country_code",
            "geography.country",
            "geography.admin1",
            "geography.city",
            "lifecycle.evidence_summary",
        }
        self.assertTrue(
            all(
                row["claim_kind"] == "reconciled_fact"
                for row in materialized["claims"]
                if row["predicate"] in canonical_predicates
            )
        )
        self.assertFalse(
            any(
                row["predicate"] == "lifecycle.source_statement"
                for row in materialized["claims"]
            )
        )
        self.assertEqual(
            {
                "announced": 2,
                "physical_construction": 0,
                "tool_installed": 0,
                "qualified": 0,
                "economically_usable": 0,
            },
            materialized["coverage"]["capacity_claims_by_basis"],
        )

    def test_checked_in_production_cohort_builds_end_to_end(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source_paths = self._production_source_paths(repository_root)
        if source_paths and not any(
            path.exists() or path.is_symlink() for path in source_paths
        ):
            self.skipTest(
                "requires ignored local source payload; all archived source files "
                "are unavailable"
            )
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            output = parent / "production-release"
            archive = parent / "production-release.tar.gz"
            result = build(
                repository_root / "baselines" / "ai_critical_manufacturing_v1.json",
                repository_root,
                output,
                archive,
            )
            manifest = validate_release(output, require_html=True)
            self.assertEqual(7, manifest["facility_count"])
            self.assertEqual(75, manifest["claim_count"])
            self.assertEqual(12, manifest["evidence_fragment_count"])
            self.assertEqual(9, manifest["source_document_count"])
            self.assertIn(
                "numeric-claim grammar admits only the cohort's directly "
                "supported `announced` basis",
                (output / "METHODOLOGY.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                result["archive"]["sha256"],
            )
            self.assertFalse(
                (parent / ".production-release.publish-transaction.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
