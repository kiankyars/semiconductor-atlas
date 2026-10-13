from html.parser import HTMLParser
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.parse import urlsplit

from web import build_site


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])


class PublicSiteTests(unittest.TestCase):
    def test_build_has_semiconductor_records_evidence_downloads_and_working_links(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            result = build_site.build(output)
            self.assertEqual(result["facilities"], 7)
            self.assertEqual(result["sources"], 10)
            self.assertEqual(result["as_of"], "2026-09-07")
            self.assertEqual(result["recorded_at"], "2026-09-07T02:31:58Z")
            index = (output / "index.html").read_text()
            self.assertIn("<title>Semiconductor Atlas</title>", index)
            self.assertEqual(index.count("<tr data-record"), 7)
            self.assertIn("7 September 2026", index)
            self.assertIn("August 2025", index)
            self.assertNotIn("1 August 2025", index)
            self.assertIn('id="search"', index)
            self.assertNotIn("window.location.replace", index)
            self.assertNotIn("<svg", index)
            for placeholder in ("__PAGE_TITLE__", "__DESCRIPTION__", "__BODY__", "__SCRIPT__"):
                self.assertNotIn(placeholder, index)
            for path in [output / "index.html", *output.glob("facilities/*/index.html")]:
                parser = Links()
                parser.feed(path.read_text())
                for link in parser.links:
                    if not link.startswith(build_site.BASE_URL):
                        continue
                    relative = urlsplit(link).path.removeprefix(urlsplit(build_site.BASE_URL).path)
                    target = output / relative
                    if target.is_dir():
                        target /= "index.html"
                    self.assertTrue(target.is_file(), f"broken {link} from {path}")
            detail = (output / "facilities/tsmc-fab21-arizona/index.html").read_text()
            self.assertIn("28 February 2026", detail)
            self.assertIn("https://www.sec.gov/Archives/edgar/", detail)
            self.assertIn("All 10 sourced claims", detail)
            self.assertIn("Evidence location", detail)
            ase = (output / "facilities/ase-kaohsiung-site/index.html").read_text()
            self.assertIn("August 2025", ase)
            self.assertNotIn("1 August 2025", ase)
            download = output / build_site.DOWNLOAD_PATH
            self.assertEqual({x.name for x in download.iterdir()}, {x.name for x in build_site.RELEASE.iterdir()})
            for source in build_site.RELEASE.iterdir():
                self.assertEqual(source.read_bytes(), (download / source.name).read_bytes())

    def test_migration_routes_have_destination_fallback_and_root_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            projects.mkdir()
            (projects / "former-campus.json").write_text("{}")
            output = root / "site"
            build_site.build(output, legacy_projects=projects)
            destinations = {
                "projects/": "sites/", "projects/former-campus/": "projects/former-campus/",
                "data/": "data/", "coverage/": "countries/", "about/": "about/",
                "methodology/": "methodology/",
            }
            for route, destination_path in destinations.items():
                contents = (output / route / "index.html").read_text()
                destination = build_site.DATACENTER_URL + destination_path
                self.assertIn('window.location.replace("' + destination + '");', contents)
                self.assertIn('href="' + destination + '">Open Data Center Atlas', contents)
                self.assertIn('<link rel="canonical" href="' + destination + '">', contents)
            self.assertNotIn("window.location.replace", (output / "index.html").read_text())
            missing = (output / "404.html").read_text()
            self.assertNotIn("window.location.replace", missing)
            self.assertIn("Browse semiconductor facilities", missing)
            self.assertIn(build_site.DATACENTER_URL, missing)
            self.assertIn('<link rel="canonical" href="' + build_site.BASE_URL + '404.html">', missing)

    def test_tampered_public_input_rejected_before_output_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            shutil.copytree(build_site.RELEASE, release)
            (release / "atlas.geojson").write_text('{"type":"FeatureCollection","features":[]}')
            output = root / "site"
            with self.assertRaisesRegex(ValueError, "does not match manifest"):
                build_site.build(output, release=release)
            self.assertFalse(output.exists())

    def test_changed_manifest_cannot_replace_pinned_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            shutil.copytree(build_site.RELEASE, release)
            with (release / "manifest.json").open("a") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "pinned snapshot"):
                build_site.build(root / "site", release=release)

    def test_build_preserves_existing_output_and_escapes_content(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "keep.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                build_site.build(output)
            self.assertEqual((output / "keep.txt").read_text(), "keep")
        self.assertEqual(build_site.e('<img src=x onerror="alert(1)">'),
                         '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;')

    def test_subpath_and_loopback_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            base = "http://127.0.0.1:8765/preview/"
            output = Path(directory) / "site"
            build_site.build(output, base_url=base)
            contents = (output / "index.html").read_text()
            self.assertIn('href="' + base + 'facilities/tsmc-fab21-arizona/"', contents)
            self.assertIn('href="' + base + 'downloads/2026-09-07/facilities.csv"', contents)
        with self.assertRaises(ValueError):
            build_site.safe_url('javascript:alert(1)')
        with self.assertRaises(ValueError):
            build_site.safe_url('https://example.org/?unsafe="')


if __name__ == "__main__":
    unittest.main()
