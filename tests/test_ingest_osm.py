from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.adapters.osm import OSM_ATTRIBUTION, OSM_LICENSE
from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_osm import import_osm_candidates
from semiconductor_atlas.repository import validate_database


FIXTURE = Path(__file__).parent / "fixtures" / "osm" / "semiconductor.json"
RETRIEVED_AT = "2026-07-17T12:00:00Z"
AS_OF_DATE = "2026-07-17"
QUERY = '[out:json];nwr["industrial"="semiconductor"];out geom;'


class OpenStreetMapImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "atlas.sqlite"
        self.connection, _ = initialize(database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def import_fixture(self):
        return import_osm_candidates(
            self.connection,
            FIXTURE,
            RETRIEVED_AT,
            AS_OF_DATE,
            query=QUERY,
        )

    def test_imports_archived_candidates_with_odbl_provenance(self) -> None:
        result = self.import_fixture()

        self.assertEqual(5, result.elements_examined)
        self.assertEqual(3, result.candidates_imported)
        self.assertEqual(3, result.source_records_created)
        self.assertEqual(3, result.entities_created)
        self.assertEqual(20, result.claims_created)

        source = self.connection.execute(
            "SELECT publisher, canonical_url, license FROM sources WHERE id = ?",
            (result.source_id,),
        ).fetchone()
        self.assertEqual(
            ("OpenStreetMap contributors", "https://www.openstreetmap.org/", OSM_LICENSE),
            tuple(source),
        )
        document = self.connection.execute(
            """
            SELECT document_url, retrieved_at, content_sha256, media_type, license, metadata_json
            FROM source_documents WHERE id = ?
            """,
            (result.source_document_id,),
        ).fetchone()
        self.assertEqual("https://overpass-api.de/api/interpreter", document["document_url"])
        self.assertEqual(RETRIEVED_AT, document["retrieved_at"])
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), document["content_sha256"])
        self.assertEqual("application/json", document["media_type"])
        self.assertEqual(OSM_LICENSE, document["license"])
        metadata = json.loads(document["metadata_json"])
        self.assertEqual(OSM_ATTRIBUTION, metadata["attribution"])
        self.assertEqual(OSM_LICENSE, metadata["license"])
        self.assertEqual("https://www.openstreetmap.org/copyright", metadata["attribution_url"])
        self.assertEqual(
            "https://opendatacommons.org/licenses/odbl/1-0/", metadata["license_url"]
        )
        self.assertEqual(QUERY, metadata["query"])
        self.assertEqual(
            "https://overpass-api.de/api/interpreter", metadata["request_endpoint"]
        )

        node = self.connection.execute(
            """
            SELECT observed_at, payload_json
            FROM source_records WHERE source_record_key = 'osm:node/101'
            """
        ).fetchone()
        self.assertEqual("2026-07-01T00:00:00Z", node["observed_at"])
        node_payload = json.loads(node["payload_json"])
        self.assertEqual("https://www.openstreetmap.org/node/101", node_payload["source_url"])
        self.assertEqual("2026-07-01T00:00:00Z", node_payload["element_timestamp"])
        self.assertEqual(101, node_payload["element"]["id"])
        self.assertEqual(
            {"name": "Explicit Fab", "industrial": "integrated_circuit"},
            node_payload["element"]["tags"],
        )

        stable_keys = {
            row[0] for row in self.connection.execute("SELECT stable_key FROM entities")
        }
        self.assertEqual(
            {
                "osm:site:node/101",
                "osm:site:way/202",
                "osm:site:relation/505",
            },
            stable_keys,
        )
        self.assertEqual([], validate_database(self.connection))

    def test_lifecycle_and_activity_are_raw_tag_dependent_candidate_claims(self) -> None:
        self.import_fixture()

        lifecycle = {
            row["stable_key"]: row["text_value"]
            for row in self.connection.execute(
                """
                SELECT entities.stable_key, scalar_values.text_value
                FROM claim_series
                JOIN entities ON entities.id = claim_series.subject_entity_id
                JOIN claim_versions ON claim_versions.series_id = claim_series.id
                JOIN scalar_values ON scalar_values.claim_version_id = claim_versions.id
                WHERE claim_series.predicate = 'lifecycle_state'
                """
            )
        }
        self.assertEqual(
            {
                "osm:site:node/101": "unknown",
                "osm:site:way/202": "under_construction",
                "osm:site:relation/505": "unknown",
            },
            lifecycle,
        )
        classifications = self.connection.execute(
            """
            SELECT scalar_values.text_value, claim_versions.claim_kind
            FROM claim_series
            JOIN claim_versions ON claim_versions.series_id = claim_series.id
            JOIN scalar_values ON scalar_values.claim_version_id = claim_versions.id
            WHERE claim_series.predicate = 'candidate_classification'
            """
        ).fetchall()
        self.assertEqual(3, len(classifications))
        self.assertEqual(
            {("semiconductor_facility_candidate", "derived_estimate")},
            {tuple(row) for row in classifications},
        )

        derived = self.connection.execute(
            """
            SELECT claim_versions.id, claim_series.subject_entity_id, claim_series.predicate
            FROM claim_versions
            JOIN claim_series ON claim_series.id = claim_versions.series_id
            WHERE claim_series.predicate IN (
                'candidate_classification', 'lifecycle_state',
                'facility_activity', 'construction_status'
            )
            """
        ).fetchall()
        self.assertGreater(len(derived), 0)
        for claim in derived:
            dependencies = self.connection.execute(
                """
                SELECT dependency_series.predicate
                FROM claim_dependencies
                JOIN claim_versions AS dependency
                  ON dependency.id = claim_dependencies.depends_on_claim_version_id
                JOIN claim_series AS dependency_series
                  ON dependency_series.id = dependency.series_id
                WHERE claim_dependencies.claim_version_id = ?
                  AND dependency_series.subject_entity_id = ?
                """,
                (claim["id"], claim["subject_entity_id"]),
            ).fetchall()
            self.assertEqual(["raw_tags"], [row[0] for row in dependencies])

        milestones = self.connection.execute(
            """
            SELECT entities.stable_key, milestone_values.milestone_type,
                   milestone_values.status, milestone_values.date_base
            FROM milestone_values
            JOIN claim_versions ON claim_versions.id = milestone_values.claim_version_id
            JOIN claim_series ON claim_series.id = claim_versions.series_id
            JOIN entities ON entities.id = claim_series.subject_entity_id
            """
        ).fetchall()
        self.assertEqual(
            [("osm:site:way/202", "construction_observed", "started", AS_OF_DATE)],
            [tuple(row) for row in milestones],
        )
        self.assertEqual(0, self.connection.execute("SELECT COUNT(*) FROM capacity_values").fetchone()[0])
        forbidden_values = self.connection.execute(
            """
            SELECT COUNT(*) FROM scalar_values
            WHERE lower(text_value) IN ('operational', 'qualified')
            """
        ).fetchone()[0]
        self.assertEqual(0, forbidden_values)
        self.assertEqual(
            {"candidate"},
            {row[0] for row in self.connection.execute("SELECT qualifier FROM capability_values")},
        )
        self.assertEqual([], validate_database(self.connection))

    def test_repeat_import_is_idempotent_and_later_snapshot_is_versioned(self) -> None:
        first = self.import_fixture()
        counts_before = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "source_families",
                "sources",
                "source_documents",
                "ingestion_runs",
                "source_records",
                "entities",
                "claim_series",
                "claim_versions",
            )
        }

        repeated = self.import_fixture()
        self.assertEqual(first.source_document_id, repeated.source_document_id)
        self.assertEqual(first.ingestion_run_id, repeated.ingestion_run_id)
        self.assertEqual(first.site_entity_ids, repeated.site_entity_ids)
        self.assertEqual(first.source_record_ids, repeated.source_record_ids)
        self.assertEqual(0, repeated.source_records_created)
        self.assertEqual(0, repeated.entities_created)
        self.assertEqual(0, repeated.claim_series_created)
        self.assertEqual(0, repeated.claims_created)
        self.assertEqual(
            counts_before,
            {
                table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in counts_before
            },
        )

        later = import_osm_candidates(
            self.connection,
            FIXTURE,
            "2026-07-18T12:00:00Z",
            AS_OF_DATE,
            query=QUERY,
        )
        self.assertEqual(3, later.source_records_created)
        self.assertEqual(0, later.entities_created)
        self.assertEqual(0, later.claim_series_created)
        self.assertEqual(20, later.claims_created)
        self.assertEqual(1, self.connection.execute("SELECT COUNT(*) FROM source_families").fetchone()[0])
        self.assertEqual(1, self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
        self.assertEqual(2, self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0])
        self.assertEqual(
            20,
            self.connection.execute(
                "SELECT COUNT(*) FROM claim_versions WHERE superseded_at IS NULL"
            ).fetchone()[0],
        )
        self.assertEqual([], validate_database(self.connection))

    def test_imports_optional_address_and_operator_relationship(self) -> None:
        archive_path = Path(self.temporary_directory.name) / "operator.json"
        archive_path.write_text(
            json.dumps(
                {
                    "version": 0.6,
                    "elements": [
                        {
                            "type": "node",
                            "id": 606,
                            "lat": 33.45,
                            "lon": -112.07,
                            "timestamp": "2026-07-16T09:30:00Z",
                            "tags": {
                                "name": "Operator Test Fab",
                                "industrial": "semiconductor",
                                "operator": "Acme Semiconductor",
                                "operator:wikidata": "Q123456",
                                "addr:housenumber": "1",
                                "addr:street": "Silicon Way",
                                "addr:city": "Phoenix",
                                "addr:state": "AZ",
                                "addr:postcode": "85001",
                                "addr:country": "US",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = import_osm_candidates(
            self.connection,
            archive_path,
            RETRIEVED_AT,
            AS_OF_DATE,
        )

        self.assertEqual(1, result.candidates_imported)
        self.assertEqual(2, result.entities_created)
        address = self.connection.execute(
            """
            SELECT scalar_values.text_value
            FROM claim_series
            JOIN claim_versions ON claim_versions.series_id = claim_series.id
            JOIN scalar_values ON scalar_values.claim_version_id = claim_versions.id
            WHERE claim_series.predicate = 'address'
            """
        ).fetchone()[0]
        self.assertEqual("1, Silicon Way, Phoenix, AZ, 85001, US", address)
        relationship = self.connection.execute(
            """
            SELECT relationship_values.object_entity_id,
                   relationship_values.relationship_type,
                   relationship_values.attributes_json
            FROM claim_series
            JOIN claim_versions ON claim_versions.series_id = claim_series.id
            JOIN relationship_values
              ON relationship_values.claim_version_id = claim_versions.id
            WHERE claim_series.predicate = 'operator'
            """
        ).fetchone()
        self.assertEqual("operated_by", relationship["relationship_type"])
        self.assertEqual(
            {"source_tag": "operator", "operator:wikidata": "Q123456"},
            json.loads(relationship["attributes_json"]),
        )
        organization = self.connection.execute(
            "SELECT kind FROM entities WHERE id = ?", (relationship["object_entity_id"],)
        ).fetchone()[0]
        self.assertEqual("organization", organization)
        operator_name = self.connection.execute(
            """
            SELECT scalar_values.text_value
            FROM claim_series
            JOIN claim_versions ON claim_versions.series_id = claim_series.id
            JOIN scalar_values ON scalar_values.claim_version_id = claim_versions.id
            WHERE claim_series.subject_entity_id = ? AND claim_series.predicate = 'name'
            """,
            (relationship["object_entity_id"],),
        ).fetchone()[0]
        self.assertEqual("Acme Semiconductor", operator_name)
        self.assertEqual([], validate_database(self.connection))


if __name__ == "__main__":
    unittest.main()
