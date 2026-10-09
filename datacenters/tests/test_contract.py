import copy
import datetime as dt
import json
import unittest
from pathlib import Path

from dcatlas.contract import (
    json_schema,
    partial_date_bounds,
    validate_collection,
    validate_project,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class PartialDateTest(unittest.TestCase):
    def test_bounds(self):
        cases = {
            "2025": (dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
            "2024-02": (dt.date(2024, 2, 1), dt.date(2024, 2, 29)),
            "2025-Q3": (dt.date(2025, 7, 1), dt.date(2025, 9, 30)),
            "2026-H2": (dt.date(2026, 7, 1), dt.date(2026, 12, 31)),
            "2026-03-01": (dt.date(2026, 3, 1), dt.date(2026, 3, 1)),
        }
        for literal, expected in cases.items():
            self.assertEqual(partial_date_bounds(literal), expected, literal)

    def test_rejects_malformed(self):
        for literal in ("2025-13", "2025-Q5", "25", "2025/01", "2025-02-30"):
            with self.assertRaises(ValueError, msg=literal):
                partial_date_bounds(literal)


class ValidateProjectTest(unittest.TestCase):
    def setUp(self):
        self.record = load("example-cloud-springfield")

    def assertViolation(self, record, fragment):
        errors = validate_project(record)
        self.assertTrue(any(fragment in e for e in errors), f"{fragment!r} not in {errors}")

    def test_fixtures_are_valid(self):
        records = [load("example-cloud-springfield"), load("example-ai-riverside")]
        for record in records:
            self.assertEqual(validate_project(record), [], record["id"])
        self.assertEqual(validate_collection(records), [])

    def test_unknown_and_unused_sources(self):
        record = copy.deepcopy(self.record)
        record["metrics"][0]["source_ids"] = ["s9"]
        self.assertViolation(record, "unknown source id 's9'")
        record = copy.deepcopy(self.record)
        record["sources"].append({**record["sources"][0], "id": "s3",
                                  "url": "https://example.com/unused"})
        self.assertViolation(record, "'s3' is not cited")

    def test_range_requires_high_value(self):
        record = copy.deepcopy(self.record)
        del record["metrics"][4]["value_high"]
        self.assertViolation(record, "value_high greater than value")
        record = copy.deepcopy(self.record)
        record["metrics"][0]["value_high"] = 900
        self.assertViolation(record, "only allowed with qualifier 'range'")

    def test_metric_units_bases_and_scopes(self):
        record = copy.deepcopy(self.record)
        record["metrics"][4]["unit"] = "usd"
        self.assertViolation(record, "ISO 4217")
        record = copy.deepcopy(self.record)
        record["metrics"][0]["basis"] = "announced"
        self.assertViolation(record, "metrics[0].basis")
        record = copy.deepcopy(self.record)
        del record["metrics"][0]["power_scope"]
        self.assertViolation(record, "power_scope")
        record = copy.deepcopy(self.record)
        record["metrics"][4]["power_scope"] = "facility"
        self.assertViolation(record, "only allowed on power_capacity")
        record = copy.deepcopy(self.record)
        record["metrics"][0]["phase_label"] = "Phase 9"
        self.assertViolation(record, "only allowed when applies_to is 'phase'")

    def test_dates_cannot_postdate_review(self):
        record = copy.deepcopy(self.record)
        record["milestones"][0]["date"] = "2027-01"
        self.assertViolation(record, "actual milestone cannot start after last_reviewed")
        record = copy.deepcopy(self.record)
        record["status_as_of"] = "2026-12-01"
        self.assertViolation(record, "status_as_of: later than last_reviewed")
        record = copy.deepcopy(self.record)
        record["sources"][1]["published"] = "2026-11"
        self.assertViolation(record, "published after it was accessed")

    def test_structure(self):
        record = copy.deepcopy(self.record)
        record["unexpected"] = 1
        self.assertViolation(record, "unknown key 'unexpected'")
        record = copy.deepcopy(self.record)
        record["id"] = "Bad_ID"
        self.assertViolation(record, "kebab-case")
        record = copy.deepcopy(self.record)
        record["location"]["lat"] = 123
        self.assertViolation(record, "location.lat")
        record = copy.deepcopy(self.record)
        record["parties"].append(dict(record["parties"][0]))
        self.assertViolation(record, "duplicate name and role")
        record = copy.deepcopy(self.record)
        record["summary"] = " padded"
        self.assertViolation(record, "leading or trailing whitespace")

    def test_rejects_hostile_values(self):
        record = copy.deepcopy(self.record)
        record["metrics"][0]["value"] = float("inf")
        self.assertViolation(record, "metrics[0].value")
        record = copy.deepcopy(self.record)
        record["summary"] = "bad\x0bcontrol"
        self.assertViolation(record, "control or invalid Unicode")
        record = copy.deepcopy(self.record)
        record["name"] = "lone \ud800 surrogate"
        self.assertViolation(record, "control or invalid Unicode")
        for path, value in ((("location", "country"), "US\n"), (("metrics", 4, "unit"), "USD\n"),
                            (("sources", 0, "id"), "s1\n"), (("status_as_of",), "2026-03-01\n")):
            record = copy.deepcopy(self.record)
            target = record
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            self.assertTrue(validate_project(record), path)
        record = copy.deepcopy(self.record)
        record["aliases"] = [["nested"]]
        self.assertViolation(record, "aliases")
        record = copy.deepcopy(self.record)
        record["metrics"][0]["source_ids"] = [["s1"]]
        self.assertViolation(record, "metrics[0].source_ids")
        record = copy.deepcopy(self.record)
        del record["metrics"][3]["phase_label"]
        self.assertViolation(record, "required when applies_to is 'phase'")
        record = copy.deepcopy(self.record)
        record["last_reviewed"] = "2099-01-01"
        self.assertViolation(record, "last_reviewed: cannot be in the future")

    def test_collection_checks_identity_and_relations(self):
        a, b = load("example-cloud-springfield"), load("example-ai-riverside")
        self.assertTrue(any("duplicate project id" in e for e in validate_collection([a, a, b])))
        self.assertTrue(any("does not exist" in e for e in validate_collection([a])))


class SchemaFileTest(unittest.TestCase):
    def test_committed_schema_matches_contract(self):
        committed = json.loads((ROOT / "schema" / "project.schema.json").read_text("utf-8"))
        self.assertEqual(committed, json_schema(),
                         "schema/project.schema.json is stale; run python -m dcatlas schema")


if __name__ == "__main__":
    unittest.main()
