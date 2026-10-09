import json
import tempfile
import unittest
from pathlib import Path

from dcatlas.archive import acceptable
from dcatlas.derive import headline
from dcatlas.geo import CountryLookup, PLANE_HEIGHT, PLANE_WIDTH, project
from dcatlas.layers import load_layer, normalize_osm, normalize_wikidata, write_layer
from dcatlas.markdown import render

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class HeadlineTest(unittest.TestCase):
    def test_prefers_demand_scope_then_latest_and_ignores_phases(self):
        h = headline(load("example-cloud-springfield"))
        # The 1,200 MW IT-load figure is newer, but facility/unspecified scopes win; among those
        # the newest statement (1,000 MW facility) beats the older 300 MW one; the phase is out.
        self.assertEqual(h["planned_power_mw"], 1000)
        self.assertEqual(h["planned_power_scope"], "facility")
        self.assertEqual(h["planned_power_qualifier"], "approximately")
        self.assertEqual(h["planned_power_as_of"], "2026-03-01")
        self.assertIsNone(h["operational_power_mw"])
        self.assertEqual((h["investment_value"], h["investment_value_high"]),
                         (10_000_000_000, 12_000_000_000))
        self.assertEqual(h["investment_currency"], "USD")
        self.assertEqual(h["announced"], "2025-05-01")
        self.assertEqual(h["construction_started"], "2025-Q3")
        self.assertIsNone(h["first_operational"])
        self.assertEqual(h["first_operational_target"], "2027")
        self.assertEqual(h["developers"], ["Example Cloud"])
        self.assertEqual(h["tenants"], ["Example AI Lab"])

    def test_it_load_used_when_it_is_the_only_scope(self):
        h = headline(load("example-ai-riverside"))
        self.assertIsNone(h["planned_power_mw"])
        self.assertEqual(h["operational_power_mw"], 50)
        self.assertEqual(h["operational_power_scope"], "it_load")
        self.assertEqual(h["first_operational"], "2024-02")
        self.assertIsNone(h["first_operational_target"])
        self.assertEqual(h["accelerators"], 100000)


class GeoTest(unittest.TestCase):
    def test_projection_extents(self):
        x, y = project(-180, 0)
        self.assertAlmostEqual(x, 0, places=6)
        x, y = project(180, 0)
        self.assertAlmostEqual(x, PLANE_WIDTH, places=6)
        self.assertAlmostEqual(project(0, 90)[1], 0, places=6)
        self.assertAlmostEqual(project(0, -90)[1], PLANE_HEIGHT, places=0)

    def test_country_lookup(self):
        lookup = CountryLookup.from_file(ROOT / "vendor" / "countries.json")
        self.assertEqual(lookup.country_at(-99.73, 32.45), "US")
        self.assertEqual(lookup.country_at(2.35, 48.85), "FR")
        self.assertIsNone(lookup.country_at(-30.0, 0.0))


class LayersTest(unittest.TestCase):
    OSM = {
        "osm3s": {"timestamp_osm_base": "2026-10-09T00:00:00Z"},
        "elements": [
            {"type": "way", "id": 7, "center": {"lat": 48.85, "lon": 2.35},
             "tags": {"telecom": "data_center", "name": "Paris DC", "operator": "Op"}},
            {"type": "node", "id": 3, "lat": 39.8, "lon": -89.64,
             "tags": {"building": "data_center", "addr:country": "us"}},
            {"type": "relation", "id": 9, "tags": {"telecom": "data_center"}},
        ],
    }

    def test_normalize_osm(self):
        lookup = CountryLookup.from_file(ROOT / "vendor" / "countries.json")
        features = normalize_osm(json.dumps(self.OSM).encode(), lookup)
        self.assertEqual([f["id"] for f in features], ["osm-node-3", "osm-way-7"])
        node, way = (f["properties"] for f in features)
        self.assertEqual((node["country"], node["country_basis"]), ("US", "addr:country tag"))
        self.assertEqual((way["country"], way["country_basis"]),
                         ("FR", "Natural Earth boundary lookup"))
        self.assertEqual(way["operator"], "Op")
        self.assertEqual(way["osm_url"], "https://www.openstreetmap.org/way/7")

    def test_normalize_wikidata(self):
        raw = {"results": {"bindings": [
            {"item": {"value": "http://www.wikidata.org/entity/Q20"},
             "label": {"value": "B"}, "coord": {"value": "Point(10.5 50.25)"},
             "country": {"value": "DE"}, "inception": {"value": "2001-01-01T00:00:00Z"}},
            {"item": {"value": "http://www.wikidata.org/entity/Q3"}, "label": {"value": "A"},
             "coord": {"value": "Point(1 2)"}},
            {"item": {"value": "http://www.wikidata.org/entity/Q4"}, "label": {"value": "C"}},
        ]}}
        features = normalize_wikidata(json.dumps(raw).encode())
        self.assertEqual([f["id"] for f in features], ["wikidata-Q3", "wikidata-Q20"])
        self.assertEqual(features[1]["properties"]["inception"], "2001-01-01")
        self.assertEqual(features[1]["geometry"]["coordinates"], [10.5, 50.25])

    def test_layer_roundtrip_and_tamper_detection(self):
        features = normalize_osm(json.dumps(self.OSM).encode(), None)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "osm"
            meta = {"endpoint": "https://example.invalid", "query": "q",
                    "retrieved_at": "2026-10-09T00:00:00Z"}
            manifest = write_layer(directory, "osm", b"raw", meta, features,
                                   {"id": "ODbL-1.0"})
            self.assertEqual(manifest["feature_count"], 2)
            loaded_manifest, loaded = load_layer(directory)
            self.assertEqual(loaded, features)
            text = (directory / "features.geojson").read_text()
            (directory / "features.geojson").write_text(text.replace("Paris", "Lyon"))
            with self.assertRaises(ValueError):
                load_layer(directory)


class ArchiveRuleTest(unittest.TestCase):
    def test_snapshot_window(self):
        self.assertTrue(acceptable("20250601120000", "2025-05-21", "2026-10-09"))
        self.assertFalse(acceptable("20240101000000", "2025-05", "2026-10-09"))
        self.assertFalse(acceptable("20261201000000", None, "2026-10-09"))
        self.assertTrue(acceptable("20260101000000", None, "2026-10-09"))
        self.assertFalse(acceptable("20240101000000", None, "2026-10-09"))


class MarkdownTest(unittest.TestCase):
    def test_render_subset_and_escaping(self):
        html = render("# Title\n\nSome **bold** `<code>` and [a link](x.md).\n\n"
                      "- one\n- two\n\n| a | b |\n| --- | --- |\n| 1 | <b> |\n\n"
                      "```sh\necho <hi>\n```\n\n> quoted\n",
                      link=lambda u: "L:" + u, shift=1)
        self.assertIn('<h2 id="title">Title</h2>', html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code>&lt;code&gt;</code>", html)
        self.assertIn('<a href="L:x.md">a link</a>', html)
        self.assertIn("<ul><li>one</li><li>two</li></ul>", html)
        self.assertIn("<td>&lt;b&gt;</td>", html)
        self.assertIn("echo &lt;hi&gt;", html)
        self.assertIn("<blockquote><p>quoted</p></blockquote>", html)


if __name__ == "__main__":
    unittest.main()
