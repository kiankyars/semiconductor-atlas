from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semiconductor_atlas.database import initialize
from semiconductor_atlas.ingest_nist import import_nist_awards
from semiconductor_atlas.repository import validate_database


FIXTURES = Path(__file__).parent / "fixtures" / "nist"
RETRIEVED_AT = "2026-07-18T01:51:28Z"
AS_OF_DATE = "2026-07-17"
TI_URL = "https://www.nist.gov/chips/texas-instruments-texas-sherman"
ROCKET_URL = "https://www.nist.gov/chips/rocket-lab-new-mexico-albuquerque"


ROCKET_INDEX = """<!doctype html>
<html><body>
  <div class="margin-top-3">
    <span class="views-field views-field-title"><strong><a href="/chips/rocket-lab-new-mexico-albuquerque">Rocket Lab (New Mexico)</a></strong></span>
    <span class="views-field-field-chipfund-location-locality"><strong> – Albuquerque, </strong></span>
    <span class="views-field-field-chipfund-location-administrative-area"><strong>NM</strong></span>
    <div><strong>Award Amount: </strong>up to $23.9 million in direct funding</div>
    <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
    <div class="views-field-body"><div>Expansion and modernization of Rocket Lab’s existing compound semiconductor production facility.</div></div>
  </div>
</body></html>
"""


ROCKET_DETAIL = """<!doctype html>
<html>
  <head>
    <link rel="canonical" href="https://www.nist.gov/chips/rocket-lab-new-mexico-albuquerque">
    <meta property="article:published_time" content="2024-06-11T09:00-04:00">
    <meta property="article:modified_time" content="2025-01-02T10:00-05:00">
  </head>
  <body>
    <h1>Rocket Lab (New Mexico)</h1>
    <h2>Project Summary</h2>
    <p>The award supports expansion and modernization of an existing compound semiconductor production facility.</p>
    <table>
      <tr><th colspan="2">Project Statistics: Rocket Lab</th></tr>
      <tr><th>Recipient</th><td>Rocket Lab</td></tr>
      <tr><th>Location(s)</th><td>Albuquerque, New Mexico</td></tr>
      <tr><th>Direct Funding Amount</th><td>Up to $23.9 million</td></tr>
      <tr><th>Project Type</th><td>Construction and modernization of a compound semiconductor production facility</td></tr>
      <tr><th>Project Timeline</th><td>Expected to increase the facility’s production capacity by 50% within 3 years</td></tr>
    </table>
    <div class="nist-field__label">Application Stage</div>
    <div class="nist-field__item">Final Award</div>
  </body>
</html>
"""


INFINERA_DETAIL = """<!doctype html>
<html>
  <head><link rel="canonical" href="https://www.nist.gov/chips/infinera-california-san-jose"></head>
  <body>
    <h1>Infinera (California)</h1>
    <table>
      <tr><th colspan="2">Infinera Project Overview</th></tr>
      <tr><th>Recipient</th><td>Infinera</td></tr>
      <tr><th>Location(s)</th><td>San Jose, California</td></tr>
      <tr><th>Direct Funding Amount</th><td>Up to $93 million</td></tr>
    </table>
  </body>
</html>
"""


def award_index_card(
    slug: str,
    title: str,
    locality: str,
    region: str,
    amount_text: str,
) -> str:
    return f"""
    <div class="margin-top-3">
      <span class="views-field views-field-title"><strong><a href="/chips/{slug}">{title}</a></strong></span>
      <span class="views-field-field-chipfund-location-locality"><strong> – {locality}, </strong></span>
      <span class="views-field-field-chipfund-location-administrative-area"><strong>{region}</strong></span>
      <div><strong>Award Amount: </strong>{amount_text}</div>
      <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
      <div class="views-field-body"><div>Expansion of a semiconductor facility.</div></div>
    </div>
    """


def award_detail(
    slug: str,
    recipient: str,
    locations: tuple[str, ...],
    overview: str,
    amount_text: str,
) -> str:
    location_html = "".join(f"<p>{location}</p>" for location in locations)
    return f"""<!doctype html><html><head>
    <link rel="canonical" href="https://www.nist.gov/chips/{slug}">
    </head><body><h1>{recipient}</h1><table>
    <tr><th colspan="2">{overview}</th></tr>
    <tr><th>Recipient</th><td>{recipient}</td></tr>
    <tr><th>Location(s)</th><td>{location_html}</td></tr>
    <tr><th>Direct Funding Amount</th><td>{amount_text}</td></tr>
    </table></body></html>"""


class NISTIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.root = root
        self.connection, _ = initialize(root / "atlas.sqlite")
        self.rocket_index = root / "nist-chips-awards-page-1.html"
        self.rocket_detail = root / "nist-chips-detail-rocket-lab.html"
        self.infinera_detail = root / "nist-chips-detail-infinera.html"
        self.rocket_index.write_text(ROCKET_INDEX, encoding="utf-8")
        self.rocket_detail.write_text(ROCKET_DETAIL, encoding="utf-8")
        self.infinera_detail.write_text(INFINERA_DETAIL, encoding="utf-8")
        self.index_paths = [FIXTURES / "awards.html", self.rocket_index]
        self.detail_paths = [
            FIXTURES / "detail.html",
            self.infinera_detail,
            self.rocket_detail,
        ]

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def import_snapshot(self):
        return import_nist_awards(
            self.connection,
            self.index_paths,
            self.detail_paths,
            RETRIEVED_AT,
            AS_OF_DATE,
        )

    def project_id(self, canonical_url: str) -> str:
        row = self.connection.execute(
            """
            SELECT DISTINCT series.subject_entity_id
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE series.predicate = 'canonical_url'
              AND values_.text_value = ?
            """,
            (canonical_url,),
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def table_counts(self) -> dict[str, int]:
        tables = (
            "ingestion_runs",
            "ingestion_run_documents",
            "source_documents",
            "source_records",
            "entities",
            "claim_series",
            "claim_versions",
            "claim_evidence",
            "claim_dependencies",
        )
        return {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }

    def test_end_to_end_import_is_idempotent_scoped_and_fully_lineaged(self) -> None:
        first = self.import_snapshot()
        first_counts = self.table_counts()
        second = self.import_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(first_counts, self.table_counts())
        self.assertEqual(1, first_counts["ingestion_runs"])
        self.assertEqual(5, first_counts["source_documents"])
        self.assertEqual(6, first_counts["source_records"])
        linked_document_ids = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT DISTINCT source_document_id
                FROM ingestion_run_documents
                WHERE ingestion_run_id = ?
                """,
                (first.run_id,),
            )
        }
        self.assertEqual(set(first.source_document_ids), linked_document_ids)
        parameter_roles = {
            row[0]: row[1]
            for row in self.connection.execute(
                """
                SELECT role, COUNT(*)
                FROM ingestion_run_documents
                WHERE ingestion_run_id = ? AND role LIKE 'parameter:%'
                GROUP BY role
                """,
                (first.run_id,),
            )
        }
        self.assertEqual(
            {
                "parameter:detail_document_ids": 3,
                "parameter:index_document_ids": 2,
            },
            parameter_roles,
        )
        self.assertEqual("succeeded", self.connection.execute(
            "SELECT status FROM ingestion_runs"
        ).fetchone()[0])
        self.assertEqual(
            "nist-awards-import-v3",
            self.connection.execute(
                "SELECT code_version FROM ingestion_runs"
            ).fetchone()[0],
        )

        source = self.connection.execute(
            "SELECT license FROM sources WHERE id = ?", (first.source_id,)
        ).fetchone()
        self.assertIn("credit requested", source[0])
        document_metadata = json.loads(self.connection.execute(
            "SELECT metadata_json FROM source_documents ORDER BY id LIMIT 1"
        ).fetchone()[0])
        self.assertEqual(
            "public_information_unless_marked_otherwise",
            document_metadata["reuse_status"],
        )
        self.assertEqual(
            "National Institute of Standards and Technology",
            document_metadata["attribution"],
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE source_documents SET title = 'changed' WHERE id = ?",
                (first.source_document_ids[0],),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.connection.execute(
                "UPDATE source_records SET source_record_key = 'changed' WHERE id = ?",
                (first.source_record_ids[0],),
            )

        ti_project_id = self.project_id(TI_URL)
        ti_sites = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT relationships.object_entity_id
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN relationship_values AS relationships
                  ON relationships.claim_version_id = versions.id
                WHERE series.subject_entity_id = ?
                  AND relationships.relationship_type = 'applies_to_site'
                """,
                (ti_project_id,),
            )
        }
        self.assertEqual(2, len(ti_sites))
        site_locations = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT values_.text_value
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
                WHERE series.subject_entity_id IN (?, ?)
                  AND series.predicate LIKE 'location.%'
                """,
                tuple(sorted(ti_sites)),
            )
        }
        self.assertIn("Sherman, Texas", site_locations)
        self.assertIn("Lehi, Utah", site_locations)

        detail_funding = self.connection.execute(
            """
            SELECT values_.text_value, documents.document_url, records.source_record_key,
                   evidence.locator, versions.claim_kind
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            JOIN claim_evidence AS evidence ON evidence.claim_version_id = versions.id
            JOIN source_documents AS documents ON documents.id = evidence.source_document_id
            JOIN source_records AS records ON records.id = evidence.source_record_id
            WHERE series.subject_entity_id = ?
              AND series.predicate = 'direct_funding.source_text'
              AND values_.text_value = '$900 million'
            """,
            (ti_project_id,),
        ).fetchone()
        self.assertEqual(
            (
                "$900 million",
                TI_URL,
                f"detail:{TI_URL}",
                "Project Statistics: Sherman, Texas / Direct Funding",
                "source_statement",
            ),
            tuple(detail_funding),
        )

        ti_scopes = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT values_.text_value
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
                WHERE series.subject_entity_id = ?
                  AND series.predicate = 'direct_funding.scope_text'
                """,
                (ti_project_id,),
            )
        }
        self.assertEqual(
            {
                "Texas Instruments Project Overview",
                "Project Statistics: Sherman, Texas",
            },
            ti_scopes,
        )
        ti_amounts = {
            int(row[0])
            for row in self.connection.execute(
                """
                SELECT values_.integer_value
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
                WHERE series.subject_entity_id = ?
                  AND series.predicate IN (
                    'direct_funding.site_amount_usd',
                    'direct_funding.project_amount_usd'
                  )
                """,
                (ti_project_id,),
            )
        }
        self.assertEqual({900_000_000, 1_610_000_000}, ti_amounts)

        infinera_project_id = self.project_id(
            "https://www.nist.gov/chips/infinera-california-san-jose"
        )
        infinera_amounts = {
            int(row[0])
            for row in self.connection.execute(
                """
                SELECT values_.integer_value
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
                WHERE series.predicate = 'direct_funding.program_amount_usd'
                  AND series.subject_entity_id IN (
                    SELECT relationships.object_entity_id
                    FROM claim_series AS links
                    JOIN claim_versions AS link_versions
                      ON link_versions.series_id = links.id
                    JOIN relationship_values AS relationships
                      ON relationships.claim_version_id = link_versions.id
                    WHERE links.subject_entity_id = ?
                      AND relationships.relationship_type = 'part_of_award_program'
                  )
                """,
                (infinera_project_id,),
            )
        }
        self.assertEqual({93_000_000}, infinera_amounts)
        self.assertNotIn(46_500_000, infinera_amounts)
        shared_scope = self.connection.execute(
            """
            SELECT values_.text_value
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE series.predicate = 'direct_funding.scope_text'
              AND values_.text_value LIKE 'split across%'
            """
        ).fetchone()
        self.assertEqual(
            "split across Bethlehem, PA and San Jose, CA",
            shared_scope[0],
        )
        inferred_site_scopes = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE series.predicate = 'direct_funding.scope_classification'
              AND values_.text_value = 'site'
            """
        ).fetchone()[0]
        self.assertEqual(0, inferred_site_scopes)

        capacity_bases = {
            row[0] for row in self.connection.execute("SELECT basis FROM capacity_values")
        }
        self.assertEqual({"announced"}, capacity_bases)
        capacity = self.connection.execute(
            """
            SELECT capacities.low, capacities.base, capacities.high, capacities.unit,
                   versions.claim_kind
            FROM capacity_values AS capacities
            JOIN claim_versions AS versions ON versions.id = capacities.claim_version_id
            """
        ).fetchone()
        self.assertEqual((50.0, 50.0, 50.0, "percent", "derived_estimate"), tuple(capacity))
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM milestone_values"
        ).fetchone()[0])
        self.assertEqual(0, self.connection.execute(
            "SELECT COUNT(*) FROM capacity_values WHERE basis != 'announced'"
        ).fetchone()[0])

        cleanroom = self.connection.execute(
            """
            SELECT values_.integer_value, versions.claim_kind,
                   COUNT(dependencies.depends_on_claim_version_id)
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            JOIN claim_dependencies AS dependencies
              ON dependencies.claim_version_id = versions.id
            WHERE series.predicate = 'cleanroom_area.value_ft2'
            GROUP BY values_.integer_value, versions.claim_kind
            """
        ).fetchone()
        self.assertEqual((40_000, "derived_estimate", 1), tuple(cleanroom))

        literal_claims = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions WHERE claim_kind = 'source_statement'"
        ).fetchone()[0]
        derived_claims = self.connection.execute(
            "SELECT COUNT(*) FROM claim_versions WHERE claim_kind = 'derived_estimate'"
        ).fetchone()[0]
        dependency_count = self.connection.execute(
            "SELECT COUNT(*) FROM claim_dependencies"
        ).fetchone()[0]
        self.assertGreater(literal_claims, 0)
        self.assertGreater(derived_claims, 0)
        self.assertGreaterEqual(dependency_count, derived_claims)
        self.assertEqual([], validate_database(self.connection))

        filtered = self.connection.execute(
            "SELECT COUNT(*) FROM source_records WHERE payload_json LIKE '%CHIPS NAPMP%'"
        ).fetchone()[0]
        self.assertEqual(0, filtered)

    def test_later_snapshot_preserves_entity_identity_and_supersedes_claims(self) -> None:
        first = self.import_snapshot()
        entity_count = self.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

        later = import_nist_awards(
            self.connection,
            self.index_paths,
            self.detail_paths,
            "2026-07-19T01:51:28Z",
            AS_OF_DATE,
            snapshot_is_complete=True,
        )

        self.assertNotEqual(first.run_id, later.run_id)
        self.assertEqual(
            entity_count,
            self.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        )
        self.assertEqual(
            len(later.claim_version_ids),
            self.connection.execute(
                "SELECT COUNT(*) FROM claim_versions WHERE superseded_at IS NULL"
            ).fetchone()[0],
        )
        self.assertTrue(
            all(
                row[0] == "2026-07-19T01:51:28Z"
                for row in self.connection.execute(
                    "SELECT DISTINCT superseded_at FROM claim_versions WHERE created_by_run_id = ?",
                    (first.run_id,),
                )
            )
        )
        self.assertEqual([], validate_database(self.connection))

    def test_complete_snapshot_retires_removed_fields_without_reassigning_reordered_series(self) -> None:
        self.import_snapshot()
        ti_project_id = self.project_id(TI_URL)

        def detail_site_series() -> dict[str, str]:
            return {
                row["series_id"]: row["display_name"]
                for row in self.connection.execute(
                    """
                    SELECT series.id AS series_id, entities.display_name
                    FROM claim_series AS series
                    JOIN claim_versions AS versions ON versions.series_id = series.id
                    JOIN relationship_values AS relationships
                      ON relationships.claim_version_id = versions.id
                    JOIN entities ON entities.id = relationships.object_entity_id
                    JOIN claim_evidence AS evidence
                      ON evidence.claim_version_id = versions.id
                    WHERE series.subject_entity_id = ?
                      AND relationships.relationship_type = 'applies_to_site'
                      AND evidence.locator = 'Location(s)'
                      AND versions.superseded_at IS NULL
                    ORDER BY series.id
                    """,
                    (ti_project_id,),
                )
            }

        original_mapping = detail_site_series()
        self.assertEqual({"Lehi, UT", "Sherman, TX"}, set(original_mapping.values()))
        original = (FIXTURES / "detail.html").read_text(encoding="utf-8")
        reordered = original.replace(
            "<p>Sherman, Texas</p><p>Lehi, Utah</p>",
            "<p>Lehi, Utah</p><p>Sherman, Texas</p>",
        )
        reordered_path = self.root / "reordered-detail.html"
        reordered_path.write_text(reordered, encoding="utf-8")
        import_nist_awards(
            self.connection,
            self.index_paths,
            [reordered_path, self.infinera_detail, self.rocket_detail],
            "2026-07-19T01:51:28Z",
            AS_OF_DATE,
            snapshot_is_complete=True,
        )
        self.assertEqual(original_mapping, detail_site_series())

        removed = reordered.replace("<p>Lehi, Utah</p>", "")
        removed_path = self.root / "removed-location-detail.html"
        removed_path.write_text(removed, encoding="utf-8")
        import_nist_awards(
            self.connection,
            self.index_paths,
            [removed_path, self.infinera_detail, self.rocket_detail],
            "2026-07-20T01:51:28Z",
            AS_OF_DATE,
            snapshot_is_complete=True,
        )
        self.assertEqual({"Sherman, TX"}, set(detail_site_series().values()))
        retired_lehi = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN relationship_values AS relationships
              ON relationships.claim_version_id = versions.id
            JOIN entities ON entities.id = relationships.object_entity_id
            WHERE series.subject_entity_id = ?
              AND relationships.relationship_type = 'applies_to_site'
              AND entities.display_name = 'Lehi, UT'
              AND versions.superseded_at = '2026-07-20T01:51:28Z'
            """,
            (ti_project_id,),
        ).fetchone()[0]
        self.assertEqual(1, retired_lehi)
        self.assertEqual([], validate_database(self.connection))

    def test_display_name_and_casing_changes_do_not_change_entity_identity(self) -> None:
        self.import_snapshot()
        entity_count = self.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        upper_index = self.root / "upper-rocket-index.html"
        upper_detail = self.root / "upper-rocket-detail.html"
        upper_index.write_text(
            ROCKET_INDEX.replace("Rocket Lab", "ROCKET LAB"),
            encoding="utf-8",
        )
        upper_detail.write_text(
            ROCKET_DETAIL.replace("Rocket Lab", "ROCKET LAB"),
            encoding="utf-8",
        )

        import_nist_awards(
            self.connection,
            [FIXTURES / "awards.html", upper_index],
            [FIXTURES / "detail.html", self.infinera_detail, upper_detail],
            "2026-07-19T01:51:28Z",
            AS_OF_DATE,
            snapshot_is_complete=True,
        )

        self.assertEqual(
            entity_count,
            self.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        )
        active_names = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT values_.text_value
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
                WHERE series.predicate = 'name'
                  AND versions.superseded_at IS NULL
                  AND values_.text_value LIKE 'ROCKET LAB%'
                """
            )
        }
        self.assertIn("ROCKET LAB", active_names)
        self.assertIn("ROCKET LAB (New Mexico)", active_names)
        self.assertEqual([], validate_database(self.connection))

    def test_authoritative_detail_url_must_match_archived_canonical(self) -> None:
        mismatched_details = [
            (
                FIXTURES / "detail.html",
                "https://www.nist.gov/chips/infinera-california-san-jose",
            ),
            self.infinera_detail,
            self.rocket_detail,
        ]
        with self.assertRaisesRegex(ValueError, "canonical URL does not match"):
            import_nist_awards(
                self.connection,
                self.index_paths,
                mismatched_details,
                RETRIEVED_AT,
                AS_OF_DATE,
            )

    def test_generic_overviews_with_different_location_sets_do_not_collapse(self) -> None:
        index_path = self.root / "generic-overviews-page-20.html"
        index_path.write_text(
            "<!doctype html><html><body>"
            + award_index_card(
                "acme-nevada-reno",
                "Acme (Nevada)",
                "Reno",
                "NV",
                "$25 million in direct funding",
            )
            + award_index_card(
                "acme-oregon-hillsboro",
                "Acme (Oregon)",
                "Hillsboro",
                "OR",
                "$25 million in direct funding",
            )
            + "</body></html>",
            encoding="utf-8",
        )
        detail_paths = []
        for slug, location in (
            ("acme-nevada-reno", "Reno, Nevada"),
            ("acme-oregon-hillsboro", "Hillsboro, Oregon"),
        ):
            path = self.root / f"generic-{slug}.html"
            path.write_text(
                award_detail(
                    slug,
                    "Acme",
                    (location,),
                    "Acme Project Overview",
                    "$50 million",
                ),
                encoding="utf-8",
            )
            detail_paths.append(path)

        import_nist_awards(
            self.connection,
            [index_path],
            detail_paths,
            RETRIEVED_AT,
            AS_OF_DATE,
        )

        project_ids = tuple(
            self.project_id(f"https://www.nist.gov/chips/{slug}")
            for slug in ("acme-nevada-reno", "acme-oregon-hillsboro")
        )
        program_links = self.connection.execute(
            """
            SELECT relationships.object_entity_id
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN relationship_values AS relationships
              ON relationships.claim_version_id = versions.id
            WHERE series.subject_entity_id IN (?, ?)
              AND relationships.relationship_type = 'part_of_award_program'
              AND versions.superseded_at IS NULL
            """,
            project_ids,
        ).fetchall()
        self.assertEqual([], program_links)
        project_amounts = self.connection.execute(
            """
            SELECT series.subject_entity_id, values_.integer_value
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE series.subject_entity_id IN (?, ?)
              AND series.predicate = 'direct_funding.project_amount_usd'
              AND versions.superseded_at IS NULL
            ORDER BY series.subject_entity_id
            """,
            project_ids,
        ).fetchall()
        self.assertEqual(
            [(project_id, 50_000_000) for project_id in sorted(project_ids)],
            [tuple(row) for row in project_amounts],
        )
        self.assertEqual([], validate_database(self.connection))

    def test_index_only_shared_cards_with_same_phrase_form_one_program(self) -> None:
        shared_phrase = "(split across Reno, NV and Hillsboro, OR)"
        index_path = self.root / "shared-index-only-page-21.html"
        index_path.write_text(
            "<!doctype html><html><body>"
            + award_index_card(
                "acme-nevada-reno",
                "Acme (Nevada)",
                "Reno",
                "NV",
                f"up to $75 million in proposed funding {shared_phrase}",
            )
            + award_index_card(
                "acme-oregon-hillsboro",
                "Acme (Oregon)",
                "Hillsboro",
                "OR",
                f"$75 million in direct funding {shared_phrase}",
            )
            + "</body></html>",
            encoding="utf-8",
        )

        import_nist_awards(
            self.connection,
            [index_path],
            [],
            RETRIEVED_AT,
            AS_OF_DATE,
        )

        program_ids = [
            row[0]
            for row in self.connection.execute(
                """
                SELECT relationships.object_entity_id
                FROM claim_series AS series
                JOIN claim_versions AS versions ON versions.series_id = series.id
                JOIN relationship_values AS relationships
                  ON relationships.claim_version_id = versions.id
                WHERE relationships.relationship_type = 'part_of_award_program'
                  AND versions.superseded_at IS NULL
                ORDER BY series.subject_entity_id
                """
            )
        ]
        self.assertEqual(2, len(program_ids))
        self.assertEqual(1, len(set(program_ids)))
        program_amounts = self.connection.execute(
            """
            SELECT values_.integer_value
            FROM claim_series AS series
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE series.subject_entity_id = ?
              AND series.predicate = 'direct_funding.program_amount_usd'
              AND versions.superseded_at IS NULL
            """,
            (program_ids[0],),
        ).fetchall()
        self.assertEqual([(75_000_000,)], [tuple(row) for row in program_amounts])
        self.assertEqual([], validate_database(self.connection))

    def test_shared_card_merge_fails_closed_for_two_historical_programs(self) -> None:
        shared_phrase = "(split across Reno, NV and Hillsboro, OR)"
        cards = (
            award_index_card(
                "acme-nevada-reno",
                "Acme (Nevada)",
                "Reno",
                "NV",
                f"$75 million in direct funding {shared_phrase}",
            ),
            award_index_card(
                "acme-oregon-hillsboro",
                "Acme (Oregon)",
                "Hillsboro",
                "OR",
                f"$75 million in direct funding {shared_phrase}",
            ),
        )
        for page_number, card in enumerate(cards, start=30):
            path = self.root / f"historical-shared-page-{page_number}.html"
            path.write_text(
                f"<!doctype html><html><body>{card}</body></html>",
                encoding="utf-8",
            )
            import_nist_awards(
                self.connection,
                [path],
                [],
                f"2026-07-{page_number - 12:02d}T01:51:28Z",
                AS_OF_DATE,
            )

        historical_program_ids = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT relationships.object_entity_id
                FROM relationship_values AS relationships
                WHERE relationships.relationship_type = 'part_of_award_program'
                """
            )
        }
        self.assertEqual(2, len(historical_program_ids))
        before = self.table_counts()
        combined = self.root / "historical-shared-page-32.html"
        combined.write_text(
            f"<!doctype html><html><body>{''.join(cards)}</body></html>",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "multiple historical program entities"):
            import_nist_awards(
                self.connection,
                [combined],
                [],
                "2026-07-20T01:51:28Z",
                AS_OF_DATE,
            )
        self.assertEqual(before, self.table_counts())
        self.assertEqual([], validate_database(self.connection))

    def test_distinct_later_award_for_same_recipient_gets_a_new_program_entity(self) -> None:
        self.import_snapshot()
        later_index = self.root / "later-nist-chips-awards-page-0.html"
        later_index.write_text(
            """<!doctype html><html><body>
            <div class="margin-top-3">
              <span class="views-field views-field-title"><strong><a href="/chips/infinera-nevada-reno">Infinera (Nevada)</a></strong></span>
              <span class="views-field-field-chipfund-location-locality"><strong> – Reno, </strong></span>
              <span class="views-field-field-chipfund-location-administrative-area"><strong>NV</strong></span>
              <div><strong>Award Amount: </strong>$25 million in direct funding</div>
              <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
              <div class="views-field-body"><div>Expansion of a photonics facility.</div></div>
            </div>
            <div class="margin-top-3">
              <span class="views-field views-field-title"><strong><a href="/chips/infinera-oregon-hillsboro">Infinera (Oregon)</a></strong></span>
              <span class="views-field-field-chipfund-location-locality"><strong> – Hillsboro, </strong></span>
              <span class="views-field-field-chipfund-location-administrative-area"><strong>OR</strong></span>
              <div><strong>Award Amount: </strong>$25 million in direct funding</div>
              <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
              <div class="views-field-body"><div>Expansion of a photonics facility.</div></div>
            </div>
            <div class="margin-top-3">
              <span class="views-field views-field-title"><strong><a href="/chips/infinera-arizona-mesa">Infinera (Arizona)</a></strong></span>
              <span class="views-field-field-chipfund-location-locality"><strong> – Mesa, </strong></span>
              <span class="views-field-field-chipfund-location-administrative-area"><strong>AZ</strong></span>
              <div><strong>Award Amount: </strong>$25 million in direct funding</div>
              <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
              <div class="views-field-body"><div>Expansion of a separate photonics facility.</div></div>
            </div>
            <div class="margin-top-3">
              <span class="views-field views-field-title"><strong><a href="/chips/infinera-utah-lehi">Infinera (Utah)</a></strong></span>
              <span class="views-field-field-chipfund-location-locality"><strong> – Lehi, </strong></span>
              <span class="views-field-field-chipfund-location-administrative-area"><strong>UT</strong></span>
              <div><strong>Award Amount: </strong>$25 million in direct funding</div>
              <div class="views-field-field-chipfund-chips-org"><div><div class="nist-field__item">CHIPS Program Office</div></div></div>
              <div class="views-field-body"><div>Expansion of a separate photonics facility.</div></div>
            </div>
            </body></html>""",
            encoding="utf-8",
        )
        later_details = []
        for slug, locations, overview in (
            (
                "infinera-nevada-reno",
                ("Reno, Nevada", "Hillsboro, Oregon"),
                "Infinera Second Award Project Overview",
            ),
            (
                "infinera-oregon-hillsboro",
                ("Reno, Nevada", "Hillsboro, Oregon"),
                "Infinera Second Award Project Overview",
            ),
            (
                "infinera-arizona-mesa",
                ("Mesa, Arizona", "Lehi, Utah"),
                "Infinera Third Award Project Overview",
            ),
            (
                "infinera-utah-lehi",
                ("Mesa, Arizona", "Lehi, Utah"),
                "Infinera Third Award Project Overview",
            ),
        ):
            path = self.root / f"later-{slug}.html"
            path.write_text(
                award_detail(slug, "Infinera", locations, overview, "$50 million"),
                encoding="utf-8",
            )
            later_details.append(path)

        import_nist_awards(
            self.connection,
            [later_index],
            later_details,
            "2027-01-02T00:00:00Z",
            "2027-01-01",
        )

        programs = self.connection.execute(
            """
            SELECT entities.id, values_.integer_value
            FROM entities
            JOIN claim_series AS series ON series.subject_entity_id = entities.id
            JOIN claim_versions AS versions ON versions.series_id = series.id
            JOIN scalar_values AS values_ ON values_.claim_version_id = versions.id
            WHERE entities.display_name = 'Infinera CHIPS award program (aggregate)'
              AND series.predicate = 'direct_funding.program_amount_usd'
              AND versions.superseded_at IS NULL
            ORDER BY values_.integer_value
            """
        ).fetchall()
        self.assertEqual(
            [50_000_000, 50_000_000, 93_000_000],
            [row["integer_value"] for row in programs],
        )
        self.assertEqual(3, len({row["id"] for row in programs}))


if __name__ == "__main__":
    unittest.main()
