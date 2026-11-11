"""Tests for source-linked manufacturing project records and their download bundle."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from semiconductor_atlas import manufacturing_projects as mp

ROOT = Path(__file__).resolve().parents[1]


def example(**changes) -> dict:
    record = {
        "schema_version": "1.0",
        "id": "example-fab-one",
        "name": "Example Fab One",
        "aliases": ["EF1"],
        "company": "Example",
        "operator": "Example Semiconductor",
        "parties": [
            {"name": "Example Semiconductor", "role": "operator", "share_pct": None,
             "source_ids": ["s1"], "note": None},
            {"name": "State of Example", "role": "government", "share_pct": None,
             "source_ids": ["s2"], "note": "Approved a grant for the first phase."},
        ],
        "facility_type": "wafer_fab",
        "identity_scope": "named_facility",
        "segments": ["logic"],
        "process_nodes": [{"label": "28nm", "source_ids": ["s1"], "note": None}],
        "wafer_sizes": [{"mm": 300, "source_ids": ["s1"]}],
        "summary": "A 300 mm logic fab announced in 2023. Construction began in 2024.",
        "location": {"country": "US", "admin1": "Texas", "locality": "Example City",
                     "site": "Example Technology Park", "address": None, "lat": None, "lon": None,
                     "coordinate_source_ids": [], "precision": "site", "source_ids": ["s1"],
                     "note": None},
        "status": "under_construction",
        "status_as_of": "2024-05-02",
        "status_source_ids": ["s2"],
        "metrics": [
            {"metric": "investment", "value": 10_000_000_000, "value_max": None, "unit": "USD",
             "qualifier": "up_to", "basis": "planned", "applies_to": "project", "phase_label": None,
             "as_of": "2023-03-01", "source_ids": ["s1"], "note": None},
            {"metric": "capacity", "value": 40000, "value_max": None, "unit": "wafers_per_month",
             "qualifier": "approximately", "basis": "planned", "applies_to": "phase",
             "phase_label": "Phase 1", "as_of": "2023-03-01", "source_ids": ["s1"],
             "note": "300 mm wafer starts per month at full ramp."},
        ],
        "milestones": [
            {"event": "announced", "date": "2023-03-01", "planned": False, "phase_label": None,
             "source_ids": ["s1"], "note": None},
            {"event": "groundbreaking", "date": "2024-05", "planned": False, "phase_label": None,
             "source_ids": ["s2"], "note": None},
            {"event": "volume_production", "date": "2027", "planned": True, "phase_label": "Phase 1",
             "source_ids": ["s2"], "note": "Target stated at the groundbreaking."},
        ],
        "sources": [
            {"id": "s1", "title": "Example Semiconductor announces Example Fab One",
             "publisher": "Example Semiconductor", "url": "https://example.com/news/fab-one",
             "type": "company_announcement", "published": "2023-03-01", "accessed": "2026-11-06"},
            {"id": "s2", "title": "Governor attends Example Fab One groundbreaking",
             "publisher": "Office of the Governor of Example",
             "url": "https://gov.example.gov/news/fab-one-groundbreaking", "type": "government",
             "published": "2024-05-02", "accessed": "2026-11-06"},
        ],
        "related": [],
        "notes": None,
    }
    record.update(changes)
    return record


def problems(record: dict) -> list[str]:
    return mp.validate_record(record, stem=record.get("id"))


class DateTests(unittest.TestCase):
    def test_precisions_and_bounds(self):
        self.assertEqual(mp.parse_date("2027"), (date(2027, 1, 1), date(2027, 12, 31), "year"))
        self.assertEqual(mp.parse_date("2027-H2"), (date(2027, 7, 1), date(2027, 12, 31), "half"))
        self.assertEqual(mp.parse_date("2027-Q1"), (date(2027, 1, 1), date(2027, 3, 31), "quarter"))
        self.assertEqual(mp.parse_date("2027-Q4"), (date(2027, 10, 1), date(2027, 12, 31), "quarter"))
        self.assertEqual(mp.parse_date("2024-02"), (date(2024, 2, 1), date(2024, 2, 29), "month"))
        self.assertEqual(mp.parse_date("2024-12"), (date(2024, 12, 1), date(2024, 12, 31), "month"))
        self.assertEqual(mp.parse_date("2024-05-02")[2], "day")
        for bad in ("2024-13", "2024-02-30", "24", "2024-Q5", "2024/05", "", None):
            with self.assertRaises((ValueError, TypeError)):
                mp.parse_date(bad)


class ValidatorTests(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(problems(example()), [])

    def test_missing_and_unknown_fields(self):
        record = example()
        del record["summary"]
        self.assertIn("record: missing 'summary'", problems(record))
        self.assertIn("record: unknown field 'extra'", problems(example(extra=1)))

    def test_construction_progress_supports_construction_but_not_production(self):
        record = example()
        record["milestones"][1].update(event="construction_progress", date="2024-05-02",
                                       note="Physical construction reported underway; start date unknown.")
        self.assertEqual(problems(record), [])
        record["milestones"][1]["planned"] = True
        self.assertTrue(any("under_construction needs" in p for p in problems(record)))
        record["milestones"][1]["planned"] = False
        record["status"] = "operating"
        self.assertTrue(any("operating needs" in p for p in problems(record)))

    def test_id_must_match_file(self):
        self.assertIn("id: 'example-fab-one' does not match the file name 'other'",
                      mp.validate_record(example(), stem="other"))
        self.assertIn("id: must be lowercase kebab-case", problems(example(id="Bad_ID")))

    def test_every_source_must_be_cited_and_every_citation_must_exist(self):
        record = example()
        record["sources"].append({**record["sources"][0], "id": "s3", "url": "https://example.com/other"})
        self.assertIn("sources: s3 not cited by any statement", problems(record))
        record = example(status_source_ids=["s9"])
        self.assertIn("status_source_ids: unknown source id 's9'", problems(record))
        record = example()
        record["sources"][1]["url"] = record["sources"][0]["url"]
        self.assertIn("sources[1].url: listed twice", problems(record))

    def test_status_needs_matching_completed_milestone(self):
        record = example(status="operating")
        self.assertIn("status: operating needs a completed first_production, volume_production or "
                      "opening milestone", problems(record))
        record = example(status="under_construction")
        record["milestones"] = record["milestones"][:1] + record["milestones"][2:]
        self.assertTrue(any("under_construction needs" in p for p in problems(record)))
        self.assertIn("status: cancelled needs a completed cancelled milestone",
                      problems(example(status="cancelled")))
        # A planned (target) event never satisfies a status.
        record = example(status="operating")
        record["milestones"][2]["planned"] = True
        self.assertTrue(any("operating needs" in p for p in problems(record)))

    def test_completed_events_cannot_be_in_the_future(self):
        record = example()
        record["milestones"][2]["planned"] = False
        self.assertIn("milestones[2].date: 2027 is after the access date 2026-11-06", problems(record))

    def test_published_after_access_is_rejected(self):
        record = example()
        record["sources"][0]["published"] = "2027-01-01"
        self.assertTrue(any(p.startswith("sources[0].published") for p in problems(record)))

    def test_figures(self):
        record = example()
        record["metrics"][0]["unit"] = "wafers_per_month"
        self.assertIn("metrics[0].unit: 'wafers_per_month' is not valid for investment", problems(record))
        record = example()
        record["metrics"][0]["qualifier"] = "range"
        self.assertIn("metrics[0].value_max: a range needs value_max greater than value", problems(record))
        record = example()
        record["metrics"][1]["note"] = None
        self.assertIn("metrics[1].note: capacity must state the wafer size or unit basis", problems(record))
        record = example()
        record["metrics"][0]["applies_to"] = "multi_site"
        self.assertIn("metrics[0].note: a multi-site amount must say what it covers", problems(record))
        record = example()
        record["metrics"][1]["phase_label"] = None
        self.assertTrue(any(p.startswith("metrics[1].phase_label") for p in problems(record)))
        record = example()
        record["metrics"][0]["value"] = True
        self.assertIn("metrics[0].value: must be a positive number", problems(record))

    def test_coordinates_need_their_own_source(self):
        record = example()
        record["location"].update(lat=30.1, lon=-97.4)
        self.assertIn("location.coordinate_source_ids: must be a non-empty list of source ids",
                      problems(record))
        record["location"]["coordinate_source_ids"] = ["s2"]
        self.assertEqual(problems(record), [])
        record["location"]["lon"] = None
        self.assertIn("location: lat and lon must both be null or both be numbers", problems(record))

    def test_long_quotations_are_rejected(self):
        quoted = '"' + "word " * 20 + '"'
        self.assertIn("summary: contains a long quoted passage; write it in your own words",
                      problems(example(summary="The company said " + quoted)))

    def test_quote_detection_pairs_marks(self):
        two_short = 'TSMC called it "Fab 21" and the governor, at a ceremony with many local officials there that day, called it "a win".'
        self.assertEqual(problems(example(summary=two_short)), [])
        for opening, closing in (("「", "」"), ("“", "”"), ("«", "»"), ("『", "』")):
            with self.subTest(mark=opening):
                quoted = opening + "word " * 20 + closing
                self.assertIn("summary: contains a long quoted passage; write it in your own words",
                              problems(example(summary="The company said " + quoted)))
        record = example(review={"reviewed_on": "2026-11-06", "method": "Checked.",
                                 "changes": "Noted " + "“" + "word " * 20 + "”"})
        self.assertIn("review.changes: contains a long quoted passage; write it in your own words", problems(record))

    def test_status_must_not_contradict_completed_milestones(self):
        announced = example(status="announced")
        self.assertIn("status: announced cannot have completed construction or production milestones",
                      problems(announced))
        building = example()
        building["milestones"].append({"event": "first_production", "date": "2024-06", "planned": False,
                                       "phase_label": None, "source_ids": ["s2"], "note": None})
        self.assertIn("status: under_construction cannot have a completed production milestone (use partially_operating)",
                      problems(building))
        paused = example()
        paused["milestones"].append({"event": "paused", "date": "2024-05", "planned": False,
                                     "phase_label": None, "source_ids": ["s2"], "note": None})
        self.assertIn("status: under_construction contradicts the completed paused milestone of 2024-05-01",
                      problems(paused))
        paused["milestones"][-1]["phase_label"] = "Phase 2"
        self.assertEqual(problems(paused), [])
        self.assertIn("status: transferred needs a completed sold milestone", problems(example(status="transferred")))

    def test_dates_are_bounded_by_today_and_review_dates_are_full(self):
        future = example()
        future["sources"][1]["accessed"] = "2062-11-06"
        self.assertIn("sources[1].accessed: 2062-11-06 is in the future",
                      mp.validate_record(future, stem="example-fab-one", today=date(2026, 11, 7)))
        for value in ("2026-11", "2099-01-01"):
            with self.subTest(value=value):
                record = example(review={"reviewed_on": value, "method": "Checked.", "changes": None})
                self.assertTrue(any(x.startswith("review.reviewed_on")
                                    for x in mp.validate_record(record, stem="example-fab-one", today=date(2026, 11, 7))))

    def test_default_today_allows_dates_already_begun_east_of_utc(self):
        utc_today = datetime.now(timezone.utc).date()
        self.assertIn(mp.latest_calendar_date() - utc_today, (timedelta(0), timedelta(days=1)))
        record = example()
        record["sources"][1]["accessed"] = mp.latest_calendar_date().isoformat()
        self.assertFalse(any("in the future" in x for x in mp.validate_record(record, stem="example-fab-one")))

    def test_malformed_values_are_reported_not_raised(self):
        for changes in ({"segments": [["logic"]]}, {"status": ["operating"]}, {"aliases": [["x"]]},
                        {"related": 5}, {"facility_type": {}}):
            with self.subTest(changes=changes):
                self.assertTrue(problems(example(**changes)))
        record = example()
        record["metrics"][0]["value"] = float("nan")
        self.assertIn("metrics[0].value: must be a positive number", problems(record))
        record = example()
        record["location"].update(precision="admin1", admin1=None, site=None)
        self.assertIn("location.precision: 'admin1' needs admin1", problems(record))

    def test_text_hygiene(self):
        self.assertIn("name: must not have leading/trailing whitespace, newlines or double spaces",
                      problems(example(name="Example  Fab")))
        self.assertIn("summary: longer than 900 characters", problems(example(summary="x" * 901)))

    def test_collection_links_must_be_two_way(self):
        first = example(related=[{"id": "example-fab-two", "relation": "has_phase"}])
        second = example(id="example-fab-two", related=[])
        self.assertIn("example-fab-one: example-fab-two lacks the inverse 'phase_of' link",
                      mp.validate_collection([first, second]))
        second["related"] = [{"id": "example-fab-one", "relation": "phase_of"}]
        self.assertEqual(mp.validate_collection([first, second]), [])
        self.assertIn("example-fab-one: related id 'missing' does not exist",
                      mp.validate_collection([example(related=[{"id": "missing", "relation": "same_campus"}])]))
        self.assertIn("example-fab-one: duplicate id", mp.validate_collection([example(), example()]))


class BundleTests(unittest.TestCase):
    def records(self) -> list[dict]:
        second = example(id="example-fab-two", name="=HYPERLINK(\"x\")", status="operating")
        second["milestones"].append({"event": "first_production", "date": "2025-06", "planned": False,
                                     "phase_label": None, "source_ids": ["s2"], "note": None})
        return [second, example()]

    def test_deterministic_and_hashed(self):
        files = mp.build_bundle(self.records())
        self.assertEqual(files, mp.build_bundle(list(reversed(self.records()))))
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(set(manifest["files"]), set(files) - {"manifest.json"})
        for name, entry in manifest["files"].items():
            self.assertEqual(entry["sha256"], hashlib.sha256(files[name]).hexdigest())
            self.assertEqual(entry["bytes"], len(files[name]))
        self.assertEqual(manifest["as_of"], "2026-11-06")
        self.assertEqual(manifest["counts"]["records"], 2)
        self.assertEqual(manifest["counts"]["statuses"], {"operating": 1, "under_construction": 1})
        self.assertEqual(manifest["counts"]["primary_source_citations"], 4)

    def test_rows(self):
        files = mp.build_bundle(self.records())
        rows = list(csv.DictReader(io.StringIO(files["manufacturing_projects.csv"].decode())))
        self.assertEqual([row["id"] for row in rows], ["example-fab-one", "example-fab-two"])
        self.assertEqual(rows[0]["investment_value"], "10000000000")
        self.assertEqual(rows[0]["investment_currency"], "USD")
        self.assertEqual(rows[0]["production_started"], "")
        self.assertEqual(rows[0]["production_planned"], "2027")
        self.assertEqual(rows[1]["production_started"], "2025-06")
        self.assertEqual(rows[1]["production_planned"], "")
        self.assertEqual(rows[1]["name"], "'=HYPERLINK(\"x\")")  # formula guard
        self.assertEqual(rows[0]["page_path"], "manufacturing/example-fab-one/")
        milestones = list(csv.DictReader(io.StringIO(files["milestones.csv"].decode())))
        self.assertEqual(milestones[0]["date_precision"], "day")
        self.assertEqual(milestones[1]["source_urls"], "https://gov.example.gov/news/fab-one-groundbreaking")
        jsonl = [json.loads(line) for line in files["manufacturing_projects.jsonl"].decode().splitlines()]
        self.assertEqual(jsonl[0], example())

    def test_headline_investment_prefers_latest_whole_project_amount(self):
        record = example()
        phase = copy.deepcopy(record["metrics"][0])
        phase.update(applies_to="phase", phase_label="Phase 1", as_of="2025-01-01", value=1)
        newer = copy.deepcopy(record["metrics"][0])
        newer.update(as_of="2024-07-01", value=12_000_000_000)
        record["metrics"] += [phase, newer]
        self.assertEqual(mp.headline_investment(record)["value"], 12_000_000_000)

    def test_headline_investment_is_withheld_when_same_date_figures_disagree(self):
        record = example()
        other = copy.deepcopy(record["metrics"][0])
        other.update(unit="JPY", value=282_000_000_000)
        record["metrics"][0].update(unit="JPY", value=465_000_000_000)
        record["metrics"].append(other)
        self.assertIsNone(mp.headline_investment(record))
        dollars = copy.deepcopy(other)
        dollars.update(unit="USD", value=3_000_000_000)
        record["metrics"].append(dollars)
        self.assertEqual(mp.headline_investment(record)["value"], 3_000_000_000)

    def test_production_target_follows_the_newest_source(self):
        record = example()
        record["sources"].append({"id": "s3", "title": "Example delays its fab", "publisher": "Example Semiconductor",
                                  "url": "https://example.com/news/delay", "type": "company_announcement",
                                  "published": "2025-02-01", "accessed": "2026-11-06"})
        record["milestones"].append({"event": "volume_production", "date": "2030", "planned": True,
                                     "phase_label": None, "source_ids": ["s3"], "note": None})
        self.assertEqual(mp.production_dates(record), (None, "2030"))

    def test_load_records_reports_every_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "example-fab-one.json").write_text(json.dumps(example()), encoding="utf-8")
            (path / "wrong-name.json").write_text(json.dumps(example()), encoding="utf-8")
            with self.assertRaises(mp.ValidationError) as caught:
                mp.load_records(path)
            self.assertIn("wrong-name.json: id: 'example-fab-one' does not match", str(caught.exception))
            self.assertIn("example-fab-one: duplicate id", str(caught.exception))


class TrackedRecordTests(unittest.TestCase):
    """The tracked records themselves."""

    @classmethod
    def setUpClass(cls):
        cls.records = mp.load_records(ROOT / mp.RECORDS_DIR)

    def test_records_are_valid_and_reviewed(self):
        self.assertGreater(len(self.records), 0)
        for record in self.records:
            with self.subTest(record=record["id"]):
                self.assertIn("review", record)
                if not mp.has_primary_source(record):
                    # Press-only records are flagged on the site; their notes must explain the gap.
                    self.assertTrue(record.get("notes"))

    def test_files_are_canonical_json(self):
        for path in sorted((ROOT / mp.RECORDS_DIR).glob("*.json")):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n")

    def test_archived_links_point_at_the_archived_release(self):
        facilities = (ROOT / "web/data/2026-09-07/facilities.csv").read_text(encoding="utf-8")
        keys = {row["stable_key"] for row in csv.DictReader(io.StringIO(facilities))}
        for record in self.records:
            if record.get("archived_record"):
                self.assertIn(record["archived_record"], keys)

    def test_no_coordinates_without_a_source(self):
        for record in self.records:
            location = record["location"]
            if location["lat"] is not None:
                self.assertTrue(location["coordinate_source_ids"], record["id"])


if __name__ == "__main__":
    unittest.main()
