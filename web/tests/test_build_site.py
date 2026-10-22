import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from web import build_site


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])


def row(page: str, name: str, nth: int = 0) -> str:
    marker = '<span class="facility-name">' + html.escape(name, quote=True) + "<"
    name_at = -1
    for _ in range(nth + 1):
        name_at = page.index(marker, name_at + 1)
    start = page.rindex("<tr data-reg ", 0, name_at)
    return page[start:page.index("</tr>", name_at)]


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
            self.assertIn('<th scope="col"><span class="visually-hidden">Details</span></th>', index)
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

    def test_discovery_metadata_and_correction_links(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            build_site.build(output)
            index = (output / "index.html").read_text()
            match = re.search(r'<script type="application/ld\+json">(.*?)</script>', index, re.S)
            self.assertIsNotNone(match)
            dataset = json.loads(match.group(1))
            self.assertEqual(dataset["@type"], "Dataset")
            self.assertEqual(dataset["url"], build_site.BASE_URL)
            self.assertNotIn("license", dataset)  # reuse terms are per source
            self.assertEqual(len(dataset["spatialCoverage"]), 4)
            for download in dataset["distribution"]:
                relative = urlsplit(download["contentUrl"]).path.removeprefix(
                    urlsplit(build_site.BASE_URL).path)
                self.assertTrue((output / relative).is_file(), download["contentUrl"])
            self.assertIn('<meta property="og:title" content="Semiconductor Atlas">', index)
            self.assertIn('href="' + build_site.BASE_URL + 'favicon.svg"', index)
            self.assertTrue((output / "favicon.svg").read_text().startswith("<svg"))
            self.assertNotIn("__HEAD__", index)
            detail = (output / "facilities/tsmc-fab21-arizona/index.html").read_text()
            self.assertNotIn("application/ld+json", detail)
            correction = re.search(r'href="([^"]+)">Report a correction', detail).group(1)
            query = parse_qs(urlsplit(html.unescape(correction)).query)
            self.assertEqual(query["template"], ["correction.yml"])
            self.assertEqual(query["record"], ["tsmc:fab21-arizona"])
            template = (build_site.ROOT / ".github/ISSUE_TEMPLATE/correction.yml").read_text()
            self.assertIn("id: record", template)

    def test_registry_page_publishes_verified_registry_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            result = build_site.build(output)
            self.assertEqual(result["registry_records"], 1177)
            self.assertEqual(result["registry_leads"], 3059)
            registry = (output / "registry/index.html").read_text()
            self.assertEqual(registry.count("<tr data-reg "), 1177)
            self.assertEqual(registry.count("<circle "), 618)
            # Map dots are a pointer shortcut; they stay out of the tab order and accessibility tree.
            self.assertEqual(registry.count('tabindex="-1" aria-hidden="true" data-dot>'), 618)
            self.assertNotIn('data-source="epa-frs"', registry)  # leads stay out of the facility table
            for source in ("taiwan-moenv-ems", "taiwan-ida-factory",
                           "us-official-semiconductor-awards", "eea-industrial-reporting"):
                self.assertIn(f'data-source="{source}"', registry)
            manifest = json.loads((build_site.REGISTRY / "manifest.json").read_text())
            for family, text in manifest["attribution"].items():
                self.assertIn(build_site.e(text) + ' <a href="' + manifest["licenses"][family] + '">Terms', registry)
            # NIST statements are quoted verbatim; program figures are labelled as shared.
            self.assertIn("Shared program across 4 award pages, not allocated to this site", registry)
            vermont = row(registry, "GlobalFoundries (Vermont)")
            self.assertIn("Jobs for this site (NIST): “1,000 construction jobs”", vermont)
            self.assertIn("not allocated to this site (NIST): recipients “GlobalFoundries”; summary “The U.S. "
                          "Department of Commerce awarded GlobalFoundries (GF) up to $1.587 billion", vermont)
            self.assertIn("; direct funding “$1.587 billion”; expected investment “$14 billion”; jobs “1,100 "
                          "manufacturing jobs and 9,000 construction jobs”", vermont)
            self.assertIn("Summary for this site (NIST): “The CHIPS for America award will provide", vermont)
            self.assertIn("page updated 3 June 2026", vermont)
            self.assertIn("Direct funding (NIST award page): “Up to $75 million and $2 million in dedicated "
                          "workforce funding”; “Award Amount: $77 million in direct funding”",
                          row(registry, "Entegris (Colorado)"))
            self.assertIn("“Nearly $90 billion over 5 years (Part of Intel’s $100+ billion expansion plan)”",
                          row(registry, "Intel Corporation (Arizona)"))
            infinera = row(registry, "Infinera (Pennsylvania)")
            self.assertIn("“Award Amount: up to $93 million in proposed funding (split across Bethlehem, PA and "
                          "San Jose, CA)”", infinera)
            self.assertIn("expected investment “$456 billion” (source release flag: Quarantined implausible "
                          "monetary disclosure: $456 billion); “$456 million”", infinera)
            self.assertIn("“Approximately 500 manufacturing jobs", infinera)
            self.assertIn("“Over 500 manufacturing jobs", infinera)
            samsung = row(registry, "Samsung Electronics (Texas)")
            self.assertIn("Other locations on this award page: Taylor, Texas", samsung)
            self.assertIn("Direct funding (NIST award page): “Award Amount: $4.745 billion in direct funding”",
                          samsung)
            self.assertNotIn("for this site", samsung)
            # Reviewed factory matches show the factory's own values, and they are searchable.
            powertech = row(registry, "力成科技股份有限公司竹科三廠")
            self.assertIn("Factory registry: name 力成科技股份有限公司竹科三廠; business number 16082267 (differs); "
                          "address 新竹科學園區新竹市東區力行四路29號; products 261半導體", powertech)
            search = re.search(r'data-search="([^"]*)"', powertech).group(1)
            for text in ("95c01098", "16082267", "力行四路29號"):
                self.assertIn(text, search)
            self.assertIn("MOENV control number O17N2439 · Unified business number 28113814", powertech)
            self.assertIn("business number 84750054 (not in the environmental registry)", registry)
            self.assertIn("Released from every listed control category: waste released 2004-04-16 (registry fact)",
                          row(registry, "炬鑫科技股份有限公司"))
            # Recipients are labelled and scoped; card-title names are not presented as recipients.
            hp = row(registry, "HP Inc. (Oregon)")
            self.assertIn("Recipients (NIST award page): “HPI Federal LLC”", hp)
            self.assertNotIn("“HP Inc.”", hp)
            idaho = row(registry, "Micron (Idaho)")
            self.assertIn("Shared program across 3 award pages, not allocated to this site (NIST): recipients "
                          "“Micron Idaho Semiconductor Manufacturing (TRITON), LLC”", idaho)
            self.assertIn("Boise, ID", idaho)
            self.assertIn("Other program locations: Clay, NY, Manassas, VA", idaho)
            self.assertIn("Project type for this site (NIST): “Modernization of 2 fabs into an advanced packaging "
                          "facility", row(registry, "Intel Corporation (New Mexico)"))
            self.assertIn("Application stage (NIST): Final Award", idaho)
            # NIST facts cells hold only the stage and labelled source statements: no totals or
            # derived figures.
            prefixes = tuple(f"{label} " for label in build_site.AWARD_TOPICS.values()) + ("Shared program ",)
            for cell in re.findall(r'<td>Application stage \(NIST\): Final Award(.*?)</td>', registry):
                lines = re.findall(r'<span class="secondary">([^<]*)</span>', cell)
                self.assertTrue(lines)
                self.assertEqual("".join(f'<span class="secondary">{x}</span>' for x in lines), cell)
                for line in lines:
                    self.assertTrue(line.startswith(prefixes), line)
                    ours = re.sub(r"“[^”]*”", "", line).lower()  # our labels, outside quoted NIST text
                    self.assertNotRegex(ours, r"\btotal\b|combined|sum of")
            # Registry classifications and statuses are labelled as registry facts, never operation.
            self.assertIn('<th scope="col">Registry classification</th>', registry)
            self.assertIn("Colour shows the registered MOENV industry code, not what a site does.", registry)
            self.assertIn("The dataset includes deregistered historical entities.", registry)
            self.assertIn("Reviews named on this page were AI-assisted, not independent human sign-off", registry)
            self.assertIn("with no review of whether each is a semiconductor facility", registry)
            self.assertIn("28</strong>U.S. CHIPS final-award pages", registry)
            self.assertNotIn("awards)", registry)
            self.assertIn("registered-factory roster (status 生產中)", registry)
            self.assertIn("Registered factory, registration status 生產中 (administrative status, not operation)",
                          registry)
            self.assertIn("matched the factory roster, exactly or after the documented legacy-certificate "
                          "normalization", registry)
            self.assertIn("the file's reasons say \"manual review\"; that was the agent's review", html.unescape(registry))
            self.assertIn("Of the 618 points, 334 are filled", registry)
            self.assertIn("284 are rings: records released from every listed category (272, latest release dates "
                          "2002 to 2026) or with none listed (12)", registry)
            self.assertIn("For 63 of the points (72 of all 724 records) the registry variants differ on a flag "
                          "or release date", registry)
            self.assertIn("410</strong>Taiwan registered-factory records without a reviewed match", registry)
            self.assertIn("Of the 553 roster factories with the semiconductor token, 141 appear inside these links "
                          "and 412 have no reviewed match", registry)
            boise = row(registry, "Micron (Idaho)")
            self.assertNotIn("Manassas, Virginia", boise.split("Shared program")[1])
            self.assertIn("Summary (this NIST page, may cover other program sites)", boise)
            self.assertIn("412 have no reviewed match; 410 appear separately", registry)
            # Every rendered status is exactly the bundle's registry status (no added or changed words).
            records = {build_site.anchor(r): r for r in
                       (json.loads(line) for line in (build_site.REGISTRY / "records.jsonl").read_text().splitlines())}
            statuses = re.findall(r'<tr data-reg id="([^"]+)".*?</td><td class="activity">.*?</td><td>([^<]*)',
                                  registry)
            self.assertEqual(len(statuses), 1177)
            for anchor_id, status in statuses:
                expected = records[anchor_id]["registry_status"]
                if records[anchor_id]["source_family"] == "us-official-semiconductor-awards":
                    expected = "Application stage (NIST): " + expected
                self.assertEqual(html.unescape(status), expected)
            # Map points: filled when some registry variant lists a category with no release date,
            # rings otherwise, coloured by code. The oracle reads the published control data directly.
            colors = {"Integrated circuit manufacturing": "#2a78d6", "Semiconductor packaging and test": "#eb6834",
                      "Discrete semiconductor devices": "#1baf7a"}
            styled = re.findall(r'<a href="#([^"]+)" tabindex="-1" aria-hidden="true" data-dot>'
                                r'<circle cx="[\d.]+" cy="[\d.]+" r="3.6" ([^>]*)>', registry)
            self.assertEqual(len(styled), 618)
            for target, style in styled:
                record = records[target]
                listed = any(state["flag"] == "1" and state["release_date"] is None
                             for control in record["environmental_control"].values()
                             for state in control["variant_states"])
                color = colors[record["registry_classification"]]
                self.assertEqual(style.startswith(f'fill="{color}"'), listed, (target, style))
                self.assertIn(color, style)
            for name, color in colors.items():
                points = sum(1 for r in records.values() if r["registry_classification"] == name
                             and r["latitude"] is not None)
                self.assertIn(f'<span class="swatch" style="background:{color}"></span>{name} ({points})', registry)
            # Each row links to its own record's source.
            hrefs = re.findall(r'<tr data-reg id="([^"]+)".*?<td><a href="([^"]+)">', registry)
            self.assertEqual(len(hrefs), 1177)
            for anchor_id, href in hrefs:
                self.assertEqual(html.unescape(href), records[anchor_id]["source_url"])
            self.assertIn("54942513 | 5494251", row(registry, records[next(
                a for a, r in records.items() if r["record_id"].endswith("H43F3888"))]["name"]))
            winbond = row(registry, "華邦電子股份有限公司")
            self.assertIn("Factory registry: name 華邦電子股份有限公司中科廠 (differs)", winbond)
            self.assertIn("Same name and business number as environmental-registry record O1751197 (not reviewed "
                          "as the same facility)", row(registry, "世界先進積體電路股份有限公司晶圓二廠", nth=1))
            self.assertIn("Direct funding (NIST award page): “$32 million”; “Award Amount: up to $32 million in "
                          "direct funding”", row(registry, "Corning (New York)"))
            self.assertIn("Registry variants differ on release: waste released 2024-10-17 in one registry "
                          "variant, not in another (registry fact)", registry)
            self.assertIn("Latest EEA reporting year 2016", registry)
            self.assertIn("version 16 published 20 February 2026", registry)
            self.assertIn("Source dates<strong>20 February – 20 July 2026</strong>", registry)
            self.assertIn("6 rows named after private individuals or residences are omitted", registry)
            self.assertIn("toLocaleString('en-US')", registry)
            # Every same-page link, map dots and jump links included, resolves to an element id.
            ids = re.findall(r'<tr data-reg id="([^"]+)"', registry)
            self.assertEqual(len(ids), len(set(ids)))
            dots = re.findall(r'<a href="#([^"]+)" tabindex="-1" aria-hidden="true" data-dot><circle cx="([\d.]+)" '
                              r'cy="([\d.]+)"[^>]*><title>([^<]*)</title>', registry)
            self.assertEqual(len(dots), 618)
            names = dict(re.findall(r'<tr data-reg id="([^"]+)"[^>]*><td><span class="facility-name">([^<]*)<',
                                    registry))
            for target, _, _, title in dots:
                self.assertTrue(title.startswith(names[target] + " · "), title)  # each dot points at its own row
            self.assertIn(("80.3", "326.1"), [(x, y) for target, x, y, title in dots
                                               if title.startswith("川遠電容股份有限公司芳苑廠")])
            page_ids = set(re.findall(r' id="([^"]+)"', registry))
            for target in re.findall(r'href="#([^"]+)"', registry):
                self.assertIn(target, page_ids)
            for target in ("taiwan-map", "registry-table", "leads", "registry-downloads", "attribution"):
                self.assertIn(f'href="#{target}"', registry)
            parser = Links()
            parser.feed(registry)
            for link in parser.links:
                self.assertTrue(link.startswith(("https://", "#")), link)
                if link.startswith(build_site.BASE_URL):
                    relative = urlsplit(link).path.removeprefix(urlsplit(build_site.BASE_URL).path)
                    target = output / relative
                    if target.is_dir():
                        target /= "index.html"
                    self.assertTrue(target.is_file(), f"broken {link}")
            index = (output / "index.html").read_text()
            self.assertIn('href="' + build_site.BASE_URL + 'registry/"', index)
            self.assertIn("724 Taiwan environmental-registry records", index)
            self.assertIn("410 registered-factory records", index)
            self.assertNotIn("<svg", index)
            self.assertIn(build_site.BASE_URL + "registry/</loc>", (output / "sitemap.xml").read_text())
            download = output / build_site.REGISTRY_DOWNLOAD_PATH
            self.assertEqual({x.name for x in download.iterdir()},
                             {x.name for x in build_site.REGISTRY.iterdir()})
            for source in build_site.REGISTRY.iterdir():
                self.assertEqual(source.read_bytes(), (download / source.name).read_bytes())

    def test_tampered_registry_rejected_before_output_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry"
            shutil.copytree(build_site.REGISTRY, registry)
            with (registry / "records.jsonl").open("a") as stream:
                stream.write("{}\n")
            output = root / "site"
            with self.assertRaisesRegex(ValueError, "does not match the registry manifest"):
                build_site.build(output, registry=registry)
            self.assertFalse(output.exists())

    def test_registry_statements_are_verbatim_and_escaped(self):
        statements = [
            {"text": "Award Amount: $125 million in direct funding", "qualifiers": ["unqualified"],
             "amounts_usd": [125_000_000]},
            # Contained in the statement above and adds nothing, so not repeated.
            {"text": "$125 million", "qualifiers": ["unqualified"], "amounts_usd": []},
            {"text": "1,000 jobs\n200 more", "qualifiers": [], "amounts_usd": []},
            {"text": "$126 MILLION", "qualifiers": [], "amounts_usd": []},
        ]
        self.assertEqual(build_site.award_line(statements),
                         "“Award Amount: $125 million in direct funding”; “1,000 jobs / 200 more”; “$126 MILLION”")
        # A contained statement with a different qualifier stays visible (Corning).
        corning = [{"text": "Award Amount: up to $32 million in direct funding", "qualifiers": ["up_to"],
                    "amounts_usd": [32_000_000]},
                   {"text": "$32 million", "qualifiers": ["unqualified"], "amounts_usd": [32_000_000]}]
        self.assertEqual(build_site.award_line(corning),
                         "“Award Amount: up to $32 million in direct funding”; “$32 million”")
        # Containment respects number boundaries.
        jobs = [{"text": "12,000 jobs"}, {"text": "2,000 jobs"}]
        self.assertEqual(build_site.award_line(jobs), "“12,000 jobs”; “2,000 jobs”")
        hostile = '"><script>alert(1)</script>&'
        record = {
            "record_id": "taiwan-moenv-ems:test:" + hostile, "source_family": "taiwan-moenv-ems",
            "name": hostile, "country_code": "TW", "admin1": "Taipei City", "admin1_local": hostile,
            "locality": hostile, "address": hostile, "latitude": 25.0, "longitude": 121.5,
            "registry_classification": "Integrated circuit manufacturing", "registry_classification_code": hostile,
            "registry_status": hostile, "environmental_control": None, "latest_reporting_year": hostile,
            "identifiers": {"moenv_control_number": hostile},
            "linked_records": [{"relationship": "reviewed_same_facility", "factory_registration_number": hostile,
                                "name": hostile, "unified_business_number": hostile, "address": hostile,
                                "principal_products": [hostile], "differs_from_environmental_registry": ["name"],
                                "missing_from_environmental_registry": []}],
            "award_statements": {"statements": [{"topic": "jobs", "scope": "award", "text": hostile},
                                                {"topic": "recipient", "scope": "award", "text": hostile}]},
            "source_url": "https://example.org/" + hostile, "source_date": "2026-07-20",
        }
        eea = dict(record, record_id="eea-industrial-reporting:test", source_family="eea-industrial-reporting",
                   registry_classification=hostile, latitude=None, longitude=None, linked_records=[])
        manifest = json.loads((build_site.REGISTRY / "manifest.json").read_text())
        rendered = build_site.registry_body([record, eea], [{"state_code": hostile}], manifest, build_site.BASE_URL)
        rendered += build_site.registry_map([record])
        self.assertNotIn("<script", rendered)
        search = re.search(r'data-search="([^"]*)" data-source=', rendered)
        self.assertIsNotNone(search)  # a raw quote would end the attribute early
        self.assertNotIn("<", search.group(1))
        self.assertIn("&quot;&gt;&lt;script&gt;", rendered)
        with self.assertRaises(ValueError):
            build_site.source_href("javascript:alert(1)")

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
