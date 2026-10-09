import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from dcatlas.site import build

ROOT = Path(__file__).resolve().parent.parent
SHARED = ("vendor", "layers", "schema", "site", "METHODOLOGY.md", "ABOUT.md", "CONTRIBUTING.md",
          "CHANGELOG.md")
BASE = "https://example.test/atlas/"


def make_root(tmp: Path) -> Path:
    root = tmp / "root"
    root.mkdir()
    for name in SHARED:
        (root / name).symlink_to(ROOT / name)
    shutil.copytree(ROOT / "tests" / "fixtures", root / "projects")
    return root


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.root = make_root(tmp)
        cls.out = tmp / "out"
        cls.summary = build(root=cls.root, out_dir=cls.out, base_url=BASE, commit="abc123")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_expected_files(self):
        for rel in ("index.html", "404.html", "feed.xml", "sitemap.xml", "robots.txt", "llms.txt",
                    ".nojekyll", "projects/index.html", "data/index.html",
                    "methodology/index.html", "about/index.html",
                    "projects/example-cloud-springfield/index.html",
                    "api/v1/index.json", "api/v1/projects.json", "api/v1/stats.json",
                    "api/v1/projects/example-ai-riverside.json",
                    "data/projects.csv", "data/statements.csv", "data/milestones.csv",
                    "data/parties.csv", "data/power_sources.csv", "data/sources.csv",
                    "data/projects.json", "data/projects.jsonl", "data/projects.geojson",
                    "data/datacenters.sqlite", "data/datacenters.xlsx",
                    "data/datapackage.json", "data/manifest.json", "data/project.schema.json",
                    "data/open-data-center-atlas.zip", "assets/basemap.json",
                    "assets/explorer.js", "assets/style.css"):
            self.assertTrue((self.out / rel).is_file(), rel)
        self.assertEqual(self.summary["records"], 2)

    def test_csv_and_json_agree_with_records(self):
        rows = list(csv.DictReader(io.StringIO((self.out / "data/projects.csv").read_text())))
        self.assertEqual([r["id"] for r in rows],
                         ["example-ai-riverside", "example-cloud-springfield"])
        spring = rows[1]
        self.assertEqual(spring["planned_power_mw"], "1000")
        self.assertEqual(spring["country_name"], "United States of America")
        self.assertEqual(spring["url"], BASE + "projects/example-cloud-springfield/")
        records = json.loads((self.out / "data/projects.json").read_text())
        source = json.loads((ROOT / "tests/fixtures/example-cloud-springfield.json").read_text())
        published = {k: v for k, v in records[1].items() if k not in ("derived", "links")}
        self.assertEqual(published, source)
        api = json.loads((self.out / "api/v1/projects.json").read_text())
        self.assertEqual(api["count"], 2)
        self.assertEqual(api["projects"][1]["tenants"], ["Example AI Lab"])
        self.assertEqual(api["columns"][0], "id")
        statements = list(csv.DictReader(io.StringIO(
            (self.out / "data/statements.csv").read_text())))
        self.assertEqual(len(statements), 7)
        self.assertTrue(all(s["source_urls"].startswith("https://") for s in statements))

    def test_hashes_and_package(self):
        manifest = json.loads((self.out / "data/manifest.json").read_text())
        for name, info in manifest["files"].items():
            blob = (self.out / "data" / name).read_bytes()
            self.assertEqual(info["sha256"], hashlib.sha256(blob).hexdigest(), name)
            self.assertEqual(info["bytes"], len(blob), name)
        self.assertEqual(manifest["commit"], "abc123")
        package = json.loads((self.out / "data/datapackage.json").read_text())
        for resource in package["resources"]:
            blob = (self.out / "data" / resource["path"]).read_bytes()
            self.assertEqual(resource["hash"], "sha256:" + hashlib.sha256(blob).hexdigest())
            header = blob.decode().splitlines()[0].split(",")
            self.assertEqual(header, [f["name"] for f in resource["schema"]["fields"]])

    def test_sqlite_and_xlsx(self):
        con = sqlite3.connect(self.out / "data/datacenters.sqlite")
        try:
            self.assertEqual(con.execute("SELECT count(*) FROM projects").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM statements").fetchone()[0], 7)
            stored = con.execute("SELECT json FROM records WHERE id = ?",
                                 ("example-ai-riverside",)).fetchone()[0]
            self.assertEqual(json.loads(stored)["status"], "cancelled")
            self.assertGreater(con.execute("SELECT count(*) FROM columns").fetchone()[0], 50)
        finally:
            con.close()
        with zipfile.ZipFile(self.out / "data/datacenters.xlsx") as zf:
            self.assertIsNone(zf.testzip())
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("Example Cloud Springfield campus", sheet)

    def test_html_is_escaped_and_linked(self):
        page = (self.out / "projects/example-cloud-springfield/index.html").read_text()
        self.assertNotIn("Update <script>", page)
        self.assertIn("Update &lt;script&gt;", page)
        self.assertIn('href="#source-s2"', page)
        self.assertIn('id="source-s2"', page)
        self.assertIn("phase: Phase 1", page)
        self.assertIn('href="../../projects/example-ai-riverside/">Riverside AI campus', page)
        index = (self.out / "index.html").read_text()
        self.assertIn('"@type": "Dataset"', index)
        self.assertIn("data/projects.csv", index)
        self.assertIn('data-id="example-cloud-springfield"', index)
        methodology = (self.out / "methodology/index.html").read_text()
        self.assertIn('id="derived-headline-values"', methodology)

    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            second = Path(tmp) / "out"
            build(root=self.root, out_dir=second, base_url=BASE, commit="abc123")
            first_files = sorted(p.relative_to(self.out) for p in self.out.rglob("*")
                                 if p.is_file())
            second_files = sorted(p.relative_to(second) for p in second.rglob("*")
                                  if p.is_file())
            self.assertEqual(first_files, second_files)
            for rel in first_files:
                self.assertEqual((self.out / rel).read_bytes(), (second / rel).read_bytes(),
                                 str(rel))

    def test_refuses_non_empty_output(self):
        with self.assertRaises(FileExistsError):
            build(root=self.root, out_dir=self.out, base_url=BASE)


class CuratedProjectsTest(unittest.TestCase):
    """The published records themselves must satisfy the contract."""

    def test_all_project_files_validate(self):
        from dcatlas.site import load_records

        records = load_records(ROOT / "projects")
        self.assertEqual(len({r["id"] for r in records}), len(records))


if __name__ == "__main__":
    unittest.main()
