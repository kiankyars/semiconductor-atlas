import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from semiconductor_atlas import public_registry
from semiconductor_atlas.public_registry import RegistryError

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web/data/registry-2026-07-20"
PINNED = "f006e842958f818ea004d2022b9597c974475ca5f3353c4feea464020b75221a"  # web/build_site.py
INPUTS_PRESENT = ((ROOT / public_registry.SOURCE_RELEASE / "manifest.json").is_file()
                  and (ROOT / public_registry.EEA_DATABASE).is_file())


class TrackedBundleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = public_registry.validate(BUNDLE, PINNED)
        self.records = public_registry.read_records(BUNDLE)
        self.leads = public_registry.read_records(BUNDLE, "us_epa_frs_leads.jsonl")

    def test_counts_and_identifiers_match_the_manifest(self):
        counts = self.manifest["counts"]
        for family in ("taiwan-moenv-ems", "taiwan-ida-factory",
                       "us-official-semiconductor-awards", "eea-industrial-reporting"):
            self.assertEqual(sum(r["source_family"] == family for r in self.records), counts[family])
        self.assertEqual(len(self.leads), counts["epa-frs"])
        ids = [r["record_id"] for r in self.records + self.leads]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.manifest["attribution"], public_registry.ATTRIBUTION)
        self.assertEqual(self.manifest["licenses"], public_registry.LICENSE_URLS)
        # The exact strings come from the source adapters and docs/data_contract.md.
        from semiconductor_atlas.adapters.eea_industrial import EEA_ATTRIBUTION
        from semiconductor_atlas.adapters.epa_frs import FRS_ATTRIBUTION
        from semiconductor_atlas.adapters.moenv_ems import MOENV_ATTRIBUTION
        from semiconductor_atlas.adapters.taiwan_factory_registry import TAIWAN_FACTORY_ATTRIBUTION
        self.assertEqual(self.manifest["attribution"]["eea-industrial-reporting"], EEA_ATTRIBUTION)
        self.assertEqual(self.manifest["attribution"]["epa-frs"], FRS_ATTRIBUTION)
        self.assertEqual(self.manifest["attribution"]["taiwan-moenv-ems"], MOENV_ATTRIBUTION)
        self.assertEqual(self.manifest["attribution"]["taiwan-ida-factory"], TAIWAN_FACTORY_ATTRIBUTION)
        contract = " ".join((ROOT / "docs/data_contract.md").read_text(encoding="utf-8").split())
        for family in ("taiwan-moenv-ems", "taiwan-ida-factory", "epa-frs"):
            self.assertIn(" ".join(self.manifest["attribution"][family].split()), contract)
        self.assertIn("no EPA endorsement", FRS_ATTRIBUTION)
        self.assertIn("changes were made", EEA_ATTRIBUTION)
        attribution = (BUNDLE / "ATTRIBUTION.md").read_text(encoding="utf-8")
        for family, text in public_registry.ATTRIBUTION.items():
            self.assertIn(f"- {text}\n  Terms: {public_registry.LICENSE_URLS[family]}", attribution)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", attribution)
        for record in self.records:
            self.assertEqual(tuple(record), tuple(sorted(public_registry.RECORD_COLUMNS)))
            self.assertTrue(record["source_url"].startswith("https://"))

    def test_source_links_terms_and_classifications_are_pinned(self):
        self.assertEqual(public_registry.DATASET_URLS, {
            "taiwan-moenv-ems": "https://data.moenv.gov.tw/dataset/detail/EMS_S_01",
            "taiwan-ida-factory": "https://data.gov.tw/dataset/6569",
            "eea-industrial-reporting": "https://doi.org/10.2909/657ac3cb-affa-4295-a4a9-27b4f539adab",
            "epa-frs": "https://www.epa.gov/frs/epa-frs-facilities-state-single-file-csv-download"})
        self.assertEqual(public_registry.LICENSE_URLS, {
            "taiwan-moenv-ems": "https://data.gov.tw/license", "taiwan-ida-factory": "https://data.gov.tw/license",
            "us-official-semiconductor-awards": "https://www.nist.gov/copyrights-disclaimers",
            "eea-industrial-reporting": "https://creativecommons.org/licenses/by/4.0/",
            "epa-frs": "https://edg.epa.gov/epa_data_license.html"})
        self.assertTrue(self.manifest["attribution"]["us-official-semiconductor-awards"].startswith(
            "Source: National Institute of Standards and Technology (NIST), CHIPS for America award disclosures."))
        self.assertEqual(self.manifest["source_dates"], {
            "taiwan-moenv-ems": "2026-07-20", "taiwan-ida-factory": "2026-07-20",
            "us-official-semiconductor-awards": "2026-07-17", "eea-industrial-reporting": "2026-02-20",
            "epa-frs": "2026-07-01"})
        expected = {"2611": "Integrated circuit manufacturing", "2612": "Discrete semiconductor devices",
                    "2613": "Semiconductor packaging and test"}
        for record in self.records:
            if record["source_family"] == "taiwan-moenv-ems":
                code = record["registry_classification_code"].split()[2]
                self.assertEqual(record["registry_classification"], expected[code])
        for record in self.records:
            if record["source_family"] in ("taiwan-moenv-ems", "taiwan-ida-factory"):
                self.assertEqual(record["source_url"], public_registry.DATASET_URLS[record["source_family"]])
            elif record["source_family"] == "us-official-semiconductor-awards":
                self.assertRegex(record["source_url"], r"^https://www\.nist\.gov/chips/[a-z0-9-]+$")
        for lead in self.leads:
            self.assertEqual(tuple(sorted(lead)), tuple(sorted(public_registry.LEAD_COLUMNS)))
            self.assertRegex(lead["industry_codes"] or "", r"^(NAICS 334413[^|]*|SIC 3674[^|]*)( \| (NAICS 334413|SIC 3674)[^|]*)?$")
        readme = (BUNDLE / "README.md").read_text(encoding="utf-8")
        for sentence in ("with no per-record review", "AI-assisted", "not a census of distinct plants",
                         "deregistered historical entities", "not captured", "leading apostrophe",
                         "never allocated", "`source_anomalies`", "in-production factory roster", "stop-work orders",
                         "legacy-certificate normalization", "not a count of awards", "Proposed-award pages",
                         "never summed", "not independent human sign-off", "flagged, not merged",
                         "A release date is not closure", "not what a site does"):
            self.assertIn(sentence, " ".join(readme.split()))

    def test_golden_rows_keep_their_source_geography(self):
        records = {r["record_id"]: r for r in self.records}
        golden = {
            "taiwan-moenv-ems:ems_s_01:O17N2439": ("Hsinchu City", "新竹市", "東區", "新竹市東區力行四路二九號", None, None),
            "taiwan-moenv-ems:ems_s_01:J6301107": ("Hsinchu County", "新竹縣", "寶山鄉", "新竹縣寶山鄉研新一路九號",
                                                   24.77516464062, 120.991626072435),
            "taiwan-moenv-ems:ems_s_01:B9406500": ("Taichung City", "臺中市", "潭子區",
                                                   "臺中市潭子區建國路二一、二九之二、二九之三、三一之二、三一之三、三三之三、三五之三號",
                                                   24.218488, 120.700338),
            "taiwan-ida-factory:registered-factories:95B00098": ("Hsinchu County", "新竹縣", "寶山鄉",
                                                                 "新竹科學園區新竹縣寶山鄉研新一路9號", None, None),
        }
        for key, values in golden.items():
            r = records[key]
            self.assertEqual((r["admin1"], r["admin1_local"], r["locality"], r["address"], r["latitude"],
                              r["longitude"]), values, key)
        austria = records["eea-industrial-reporting:eea_t_ied-eprtr_p_2007-2024_v16_r00:production-facility:"
                           "AT.CAED/9008392397730.FACILITY"]
        self.assertEqual((austria["name"], austria["locality"], austria["address"], austria["country_code"]),
                         ("Infineon Technologies Austria AG", "Villach-Auen", "Siemensstr; 2; 9500", "AT"))
        intel = [lead for lead in self.leads if lead["frs_registry_id"] == "110000309470"][0]
        self.assertEqual((intel["name"], intel["city"], intel["county"], intel["state_code"], intel["postal_code"]),
                         ("INTEL FAB 17 SEMICONDUCTOR MANUFACTURING", "HUDSON", "MIDDLESEX", "MA", "01749-2895"))
        for key, english in (("新竹市", "Hsinchu City"), ("新竹縣", "Hsinchu County"), ("臺中市", "Taichung City"),
                             ("臺南市", "Tainan City"), ("高雄市", "Kaohsiung City"), ("苗栗縣", "Miaoli County")):
            self.assertEqual(public_registry.TAIWAN_COUNTIES[key], english)
        for r in self.records:  # the translated county always matches the county written in the address
            if r["source_family"].startswith("taiwan"):
                self.assertEqual(r["admin1"], public_registry.TAIWAN_COUNTIES[r["admin1_local"]])
                if r["source_family"] == "taiwan-moenv-ems" and r["address"]:
                    # One row (O1705864) writes 新竹市 first and its county 新竹縣 later; both stay.
                    self.assertIn(r["admin1_local"], r["address"], r["record_id"])

    def test_geometry_is_only_a_source_reported_taiwan_point(self):
        points = [r for r in self.records if r["latitude"] is not None]
        self.assertEqual(len(points), self.manifest["counts"]["source_reported_points"])
        for record in self.records:
            if record["latitude"] is None:
                self.assertIsNone(record["longitude"])
                self.assertIsNone(record["geometry_basis"])
                continue
            self.assertEqual(record["source_family"], "taiwan-moenv-ems")
            self.assertEqual(record["geometry_basis"], "source_reported_point")
            self.assertTrue(21.5 <= record["latitude"] <= 26.5)
            self.assertTrue(118.0 <= record["longitude"] <= 122.5)
        for lead in self.leads:
            self.assertFalse({"latitude", "longitude", "geometry"} & set(lead))
        geojson = json.loads((BUNDLE / "registry_points.geojson").read_text(encoding="utf-8"))
        self.assertEqual(len(geojson["features"]), len(points))
        by_id = {f["id"]: f for f in geojson["features"]}
        for record in points:  # GeoJSON order is [longitude, latitude], values unchanged
            feature = by_id[record["record_id"]]
            self.assertEqual(feature["geometry"]["coordinates"], [record["longitude"], record["latitude"]])
            self.assertEqual(feature["properties"]["geometry_basis"], "source_reported_point")
            self.assertEqual(feature["properties"]["source_url"], record["source_url"])
            self.assertEqual(feature["properties"]["moenv_control_number"],
                             record["identifiers"]["moenv_control_number"])
        self.assertEqual(geojson["attribution"], public_registry.ATTRIBUTION["taiwan-moenv-ems"])

    def test_registry_flags_are_not_promoted_and_private_fields_are_absent(self):
        expected = {
                    "eea-industrial-reporting": "Accepted in scope after AI-assisted review (reporting record, not operation)"}
        expected["taiwan-ida-factory"] = ("Registered factory, registration status 生產中 "
                                          "(administrative status, not operation)")
        for record in self.records:
            status = record["registry_status"] or ""
            if record["source_family"] in expected:
                self.assertEqual(status, expected[record["source_family"]])
            elif record["source_family"] == "taiwan-moenv-ems":
                self.assertRegex(status, r"^(Listed for environmental control: .+|Released from every listed "
                                         r"control category: .+|Registry variants differ on release: .+|No "
                                         r"environmental-control category listed) \(registry fact\)$")
            # No status claims operation, ownership, output or capacity.
            self.assertNotRegex(status.replace("not operation", "").lower(),
                                r"operat|owned|owner|capacity|production|closed|closure")
        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)
        names = {key for row in self.records + self.leads for key in keys(row)}
        for forbidden in ("responsible", "capital", "invoice", "organization", "owner", "phone", "email"):
            self.assertFalse([key for key in names if forbidden in key], forbidden)
        self.assertNotIn("獨資", (BUNDLE / "records.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.manifest["counts"]["excluded_private_organization_rows"], 2)

    def test_environmental_control_keeps_each_category_flag_and_release_date(self):
        moenv = {r["record_id"].rsplit(":", 1)[1]: r for r in self.records
                 if r["source_family"] == "taiwan-moenv-ems"}
        for record in moenv.values():
            control = record["environmental_control"]
            self.assertEqual(sorted(control), sorted(public_registry.MOENV_CATEGORIES))
            for category in control.values():
                self.assertTrue(set(category["flag"]) <= {"0", "1"})
                states = category["variant_states"]
                self.assertEqual({s["flag"] for s in states}, set(category["flag"]))
                self.assertEqual({s["release_date"] for s in states} - {None}, set(category["release_date"]))
                for state in states:
                    if state["release_date"]:
                        self.assertEqual(state["flag"], "1")  # a release follows a listing
        # A listing with a release date for every listed category is not shown as current control.
        self.assertEqual(moenv["A3505430"]["registry_status"],
                         "Released from every listed control category: waste released 2004-04-16 "
                         "(registry fact)")
        self.assertEqual(moenv["A3505430"]["environmental_control"]["waste"],
                         {"flag": ["1"], "release_date": ["2004-04-16"],
                          "variant_states": [{"flag": "1", "release_date": "2004-04-16"}]})
        # Registry variants that disagree on a release stay parallel (J55A1131 water).
        self.assertEqual(moenv["J55A1131"]["environmental_control"]["water"]["variant_states"],
                         [{"flag": "1", "release_date": None}, {"flag": "1", "release_date": "2006-01-24"}])
        self.assertTrue(moenv["J55A1131"]["registry_status"].startswith("Registry variants differ on release:"))
        self.assertIn("toxic released 2024-10-16 in one registry variant, not in another",
                      moenv["O1744021"]["registry_status"])
        self.assertEqual(moenv["J63A5593"]["registry_status"],
                         "Listed for environmental control: soil (one registry variant flags 0) (registry fact)")
        self.assertIn("water released 1981-01-21 or 2012-03-29 (registry variants differ)",
                      moenv["O1702532"]["registry_status"])
        # Every category whose variants differ is labelled as differing in the status text.
        for record in moenv.values():
            for category, control in record["environmental_control"].items():
                states = control["variant_states"]
                if len(states) > 1 and "1" in {s["flag"] for s in states}:
                    self.assertRegex(record["registry_status"],
                                     category + r" \(one registry variant flags 0\)|"
                                     + category + r"[^;]* (in one registry variant, not in another|"
                                                  r"\(registry variants differ\))")
        self.assertEqual(moenv["H43F3888"]["identifiers"]["unified_business_number"], "54942513 | 5494251")
        self.assertEqual((moenv["N2902270"]["latitude"], moenv["N2902270"]["longitude"]),
                         (23.9176304130666, 120.348281099433))  # the source-reported point, unchanged
        statuses = [r["registry_status"].split(":")[0].split(" (")[0] for r in moenv.values()]
        self.assertEqual(statuses.count("No environmental-control category listed"), 25)
        self.assertEqual(statuses.count("Released from every listed control category"), 288)
        self.assertEqual(statuses.count("Registry variants differ on release"), 4)
        self.assertEqual(statuses.count("Listed for environmental control"), 407)
        self.assertFalse([r for r in self.records if r["source_family"] != "taiwan-moenv-ems"
                          and r["environmental_control"] is not None])

    def test_epa_leads_exclude_reviewed_personal_names_and_cite_the_dataset_page(self):
        self.assertEqual(self.manifest["counts"]["excluded_personal_name_leads"], 6)
        published = {lead["frs_registry_id"] for lead in self.leads}
        self.assertFalse(published & public_registry.EPA_PERSONAL_NAME_EXCLUSIONS)
        for name in ("us_epa_frs_leads.jsonl", "us_epa_frs_leads.csv"):
            text = (BUNDLE / name).read_text(encoding="utf-8")
            for registry_id in public_registry.EPA_PERSONAL_NAME_EXCLUSIONS:
                self.assertNotIn(registry_id, text)
            self.assertNotIn("RESIDENCE", text)
        self.assertEqual({lead["source_url"] for lead in self.leads},
                         {"https://www.epa.gov/frs/epa-frs-facilities-state-single-file-csv-download"})

    def test_reviewed_factory_links_keep_differing_registry_values_visible(self):
        links = {link["factory_registration_number"]: (record, link) for record in self.records
                 for link in record["linked_records"] if link["relationship"] == "reviewed_same_facility"}
        self.assertEqual(len(links), 141)  # 142 reviewed links; one factory matches two MOENV rows
        for record, link in links.values():
            self.assertTrue(link["name"] and link["address"] and link["principal_products"])
            self.assertEqual(link["principal_products"][0], link["principal_products"][0].strip())
        record, link = links["95C01098"]
        self.assertEqual(record["identifiers"]["unified_business_number"], "28113814")
        self.assertEqual(link["unified_business_number"], "16082267")
        self.assertEqual(link["differs_from_environmental_registry"], ["unified_business_number"])
        winbond = [r for r in self.records if r["record_id"] == "taiwan-moenv-ems:ems_s_01:L95A1142"][0]
        self.assertEqual(winbond["name"], "華邦電子股份有限公司")
        self.assertEqual(winbond["linked_records"][0]["name"], "華邦電子股份有限公司中科廠")
        self.assertIn("name", winbond["linked_records"][0]["differs_from_environmental_registry"])
        # A business number only the factory registry records is missing, not conflicting.
        record, link = links["99702507"]
        self.assertNotIn("unified_business_number", record["identifiers"])
        self.assertEqual(link["differs_from_environmental_registry"], [])
        self.assertEqual(link["missing_from_environmental_registry"], ["unified_business_number"])
        # Names equal after Unicode compatibility normalization are not a difference (U+F937).
        record, link = links["94Q00001"]
        self.assertNotEqual(record["name"], link["name"])
        self.assertNotIn("name", link["differs_from_environmental_registry"])
        flagged = sum(1 for r in self.records for l in r["linked_records"]
                      if l.get("differs_from_environmental_registry")
                      or l.get("missing_from_environmental_registry"))
        self.assertEqual(flagged, self.manifest["counts"]["reviewed_links_with_differences"])
        factory_rows = {r["identifiers"].get("factory_registration_number") for r in self.records}
        self.assertFalse(set(links) & factory_rows)  # matched factories are not duplicated as rows

    def test_factory_rows_keep_registry_product_order_and_flag_unreviewed_twins(self):
        ida = {r["identifiers"]["factory_registration_number"]: r for r in self.records
               if r["source_family"] == "taiwan-ida-factory"}
        self.assertEqual(ida["99668785"]["registry_classification_code"],
                         "Taiwan registered product 261半導體 / 269其他電子零組件 / 259其他金屬製品 / "
                         "275量測、導航、控制設備及鐘錶 / 277光學儀器及設備 / 292其他專用機械設備 / 293通用機械設備")
        self.assertTrue(ida["99680763"]["registry_classification_code"].endswith("220塑膠製品 / 261半導體"))
        twins = [r for r in ida.values() if r["linked_records"]]
        self.assertEqual(len(twins), 162)
        self.assertEqual(self.manifest["counts"]["unreviewed_same_name_and_business_number"], 162)
        # Spacing inside a name does not hide a twin (IDA 04000264 / MOENV J59A7436).
        self.assertEqual([l["moenv_control_number"] for l in ida["04000264"]["linked_records"]], ["J59A7436"])
        self.assertEqual(ida["95B00267"]["linked_records"], [{
            "moenv_control_number": "O1751197",
            "relationship": "unreviewed_same_name_and_business_number",
            "source_key": "taiwan-moenv-ems:ems_s_01:O1751197"}])

    def test_nist_statements_keep_their_scope_and_are_never_summed(self):
        nist = {r["name"]: r for r in self.records
                if r["source_family"] == "us-official-semiconductor-awards"}
        self.assertEqual(len(nist), 28)
        for record in nist.values():
            award = record["award_statements"]
            member = "program_member_pages" in award
            # Nothing is summed or totalled: statements only, each with its own derived values.
            self.assertTrue(set(award) <= {"statements", "program_member_pages", "program_key"})
            for statement in award["statements"]:
                self.assertEqual(set(statement), {"topic", "scope", "text", "sections", "pages",
                                                  "source_anomalies", "amounts_usd", "qualifiers"})
                self.assertTrue(statement["pages"])
            self.assertIsNone(record["registry_classification"])
            self.assertEqual(record["identifiers"], {})
            for statement in award["statements"]:
                self.assertIn(statement["scope"], ("site", "page", "program") if member else ("award",))
                if statement["scope"] == "page":  # a member page's own Key Facts summary
                    self.assertEqual((statement["topic"], statement["sections"], statement["pages"]),
                                     ("description", ["Project Summary"], [record["source_url"]]))
                self.assertTrue(all(isinstance(a, int) for a in statement["amounts_usd"]))
                self.assertTrue(statement["text"] and statement["sections"])
            self.assertRegex(record["source_date"], r"^20\d\d-\d\d-\d\d$")

        def statements(name, topic, scope):
            return [s for s in nist[name]["award_statements"]["statements"]
                    if s["topic"] == topic and s["scope"] == scope]
        # Site statistics and program overviews stay apart (docs/source_registry.md).
        vermont = "GlobalFoundries (Vermont)"
        self.assertEqual([s["text"] for s in statements(vermont, "jobs", "site")], ["1,000 construction jobs"])
        self.assertEqual([s["text"] for s in statements(vermont, "jobs", "program")],
                         ["1,100 manufacturing jobs and 9,000 construction jobs"])
        self.assertEqual(nist[vermont]["source_date"], "2026-06-03")  # page updated, not first published
        # Each amount carries the qualifier of the statement it came from (Entegris).
        entegris = {s["text"]: (s["amounts_usd"], s["qualifiers"])
                    for s in statements("Entegris (Colorado)", "direct_funding", "award")}
        self.assertEqual(entegris, {
            "Award Amount: $77 million in direct funding": ([77_000_000], ["unqualified"]),
            "Up to $75 million and $2 million in dedicated workforce funding": ([75_000_000], ["up_to"])})
        # Conflicting NIST wording stays parallel on every member row of a program.
        for name in ("Infinera (California)", "Infinera (Pennsylvania)"):
            funding = statements(name, "direct_funding", "program")
            self.assertIn(["proposed_up_to"], [s["qualifiers"] for s in funding])
            capex = {s["text"]: s["amounts_usd"] for s in statements(name, "expected_capex", "program")}
            self.assertEqual(capex, {"$456 million": [456_000_000], "$456 billion": []})
            jobs = [s["text"].split("\n")[0] for s in statements(name, "jobs", "program")]
            self.assertEqual(sorted(jobs), ["Approximately 500 manufacturing jobs", "Over 500 manufacturing jobs"])
        # Geography stays with its own page: a member row keeps its own site, others are listed.
        idaho = nist["Micron (Idaho)"]
        self.assertEqual((idaho["locality"], idaho["admin1"]), ("Boise", "ID"))
        self.assertEqual(idaho["linked_records"],
                         [{"relationship": "other_program_locations", "sites": ["Clay, NY", "Manassas, VA"]}])
        # Recipients come only from a page's Recipient field, scoped like every statement.
        self.assertEqual([(s["scope"], s["text"]) for s in statements("Micron (Idaho)", "recipient", "program")],
                         [("program", "Micron Idaho Semiconductor Manufacturing (TRITON), LLC"),
                          ("program", "Micron New York Semiconductor Manufacturing, LLC"),
                          ("program", "Micron Virginia Semiconductor Manufacturing, LLC")])
        self.assertEqual([s["text"] for s in statements("HP Inc. (Oregon)", "recipient", "award")], ["HPI Federal LLC"])
        self.assertEqual([s["text"] for s in statements("Intel Corporation (New Mexico)", "project_type", "site")],
                         ["Modernization of 2 fabs into an advanced packaging facility, which will be the "
                          "largest advanced packaging facility in the U.S."])
        # Program statements keep their page of origin and the release's own anomaly flags.
        capex = {s["text"]: s for s in statements("Infinera (California)", "expected_capex", "program")}
        self.assertEqual(capex["$456 billion"]["pages"], ["https://www.nist.gov/chips/infinera-pennsylvania-bethlehem"])
        self.assertEqual(capex["$456 billion"]["source_anomalies"],
                         ["Quarantined implausible monetary disclosure: $456 billion"])
        self.assertEqual(capex["$456 million"]["pages"], ["https://www.nist.gov/chips/infinera-california-san-jose"])
        self.assertEqual(sum(1 for r in nist.values() for s in r["award_statements"]["statements"]
                             if s["source_anomalies"]), 2)  # the one flagged statement, on both member rows
        # A Key Facts summary that differs between member pages stays on its own page (Micron).
        manassas = [s for s in statements("Micron (Idaho)", "description", "program")
                    if "Manassas" in s["text"]]
        self.assertEqual(manassas, [])
        self.assertTrue([s for s in statements("Micron (Virginia)", "description", "page")
                         if "Micron Virginia Semiconductor Manufacturing" in s["text"]])
        self.assertTrue(statements("Intel Corporation (Ohio)", "description", "program"))  # identical on all 4
        self.assertEqual(self.manifest["counts"]["ida_token_261_factories"], 553)
        self.assertEqual(self.manifest["counts"]["ida_registration_statuses"], {"生產中": 553})
        self.assertEqual(self.manifest["counts"]["nist_multi_page_programs"], 6)
        self.assertEqual(self.manifest["counts"]["nist_program_member_pages"], 15)
        # Every statement cites NIST pages, and a site statement cites only the row's own page or
        # the awards index card; program statements cite member pages of that program.
        for record in nist.values():
            award = record["award_statements"]
            for statement in award["statements"]:
                for page in statement["pages"]:
                    self.assertTrue(page.startswith("https://www.nist.gov/chips/"), page)
                if statement["scope"] == "site":
                    self.assertTrue(set(statement["pages"]) <= {record["source_url"]} | {
                        p for p in statement["pages"] if "chips-america-awards" in p}, statement)
        intel = {s["text"][:40]: s for s in statements("Intel Corporation (Arizona)", "description", "site")}
        self.assertIn("The CHIPS for America award will provide", intel)
        self.assertIn("up to $3.94 billion", intel["The CHIPS for America award will provide"]["text"])
        # A two-location award page without a program stays one award (Samsung Austin and Taylor).
        samsung = nist["Samsung Electronics (Texas)"]
        self.assertEqual(samsung["locality"], "Austin")
        self.assertEqual(samsung["linked_records"],
                         [{"relationship": "other_locations_on_award_page", "sites": ["Taylor, Texas"]}])
        self.assertEqual({s["scope"] for s in samsung["award_statements"]["statements"]}, {"award"})

    def test_eea_records_carry_their_latest_reporting_year(self):
        eea = [r for r in self.records if r["source_family"] == "eea-industrial-reporting"]
        self.assertEqual(len(eea), 15)
        self.assertEqual(sorted({r["latest_reporting_year"] for r in eea if r["country_code"] == "GB"}),
                         ["2016", "2018", "2019"])
        self.assertFalse([r for r in self.records
                          if r["latest_reporting_year"] and r["source_family"] != "eea-industrial-reporting"])
        newport = [r for r in eea if "NRW170252" in r["identifiers"]["inspire_id"]][0]
        self.assertEqual(newport["address"], "Cardiff Road, Newport; Newport Semiconductor Plant Cardiff Road; NP10 8YJ")
        self.assertEqual({r["record_type"] for r in self.records if r["source_family"] != "us-official-semiconductor-awards"},
                         {"registry_record"})

    def test_csv_cells_cannot_start_formulas(self):
        for name in ("records.csv", "us_epa_frs_leads.csv"):
            rows = csv.DictReader(io.StringIO((BUNDLE / name).read_text(encoding="utf-8")))
            for row in rows:
                for column, value in row.items():
                    if column not in ("latitude", "longitude"):
                        self.assertNotIn(value[:1], ("=", "+", "-", "@"), (name, column, value))


class ValidationTests(unittest.TestCase):
    def test_tampering_and_unmanaged_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(BUNDLE, bundle)
            (bundle / "extra.txt").write_text("x")
            with self.assertRaisesRegex(RegistryError, "unmanaged"):
                public_registry.validate(bundle, PINNED)
            (bundle / "extra.txt").unlink()
            data = (bundle / "records.csv").read_bytes()
            (bundle / "records.csv").write_bytes(data.replace(b"Taipei", b"Taipel", 1))
            with self.assertRaisesRegex(RegistryError, "records.csv does not match"):
                public_registry.validate(bundle, PINNED)
            with self.assertRaisesRegex(RegistryError, "pinned hash"):
                public_registry.validate(bundle, "0" * 64)

    def test_csv_guard_and_conflicting_values(self):
        data = public_registry._csv(("name", "latitude"), [{"name": "=HYPERLINK(1)", "latitude": -1.5}])
        self.assertEqual(data.decode(), "name,latitude\n'=HYPERLINK(1),-1.5\n")
        entity = {"stable_key": "k", "scalar_fields": {"name": [{"value": "a"}, {"value": "b"}]}}
        self.assertEqual(public_registry._values(entity, "name"), ["a", "b"])
        with self.assertRaisesRegex(RegistryError, "conflicting name"):
            public_registry._one(entity, "name")

    @unittest.skipUnless(INPUTS_PRESENT, "retained registry inputs are local-only")
    def test_rebuild_from_retained_inputs_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            public_registry.build(ROOT, output)
            self.assertEqual(sorted(p.name for p in output.iterdir()),
                             sorted(p.name for p in BUNDLE.iterdir()))
            for path in BUNDLE.iterdir():
                self.assertEqual(path.read_bytes(), (output / path.name).read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()


class BuilderUnitTests(unittest.TestCase):
    """Builder rules on small synthetic inputs, so they run without the local source release."""

    @staticmethod
    def entity(key, fields, relationships=()):
        claims, scalar = {}, {}
        for predicate, values in fields.items():
            for number, (value, locators, dependencies) in enumerate(values):
                claim_id = f"{key}:{predicate}:{number}"
                scalar.setdefault(predicate, []).append({"claim_id": claim_id, "value": value})
                claims[claim_id] = {"sections": sorted({l.split(" / ")[0] for l in locators}),
                                    "dependencies": set(dependencies), "excerpts": [str(value)],
                                    "documents": [f"https://example.org/{key}"], "anomalies": []}
        return {"stable_key": key, "scalar_fields": scalar, "relationships": list(relationships),
                "geometry": None}, claims

    def test_environmental_control_pairs_flags_with_their_own_variant(self):
        v = "source_record.variants[{}].{}"
        entity, claims = self.entity("taiwan-moenv-ems:t:1", {
            **{f"moenv.environmental_control.{c}_raw": [("0", [v.format(0, "is" + c), v.format(1, "is" + c)], [])]
               for c in ("air", "toxic", "soil")},
            "moenv.environmental_control.water_raw": [("1", [v.format(0, "iswater"), v.format(1, "iswater")], [])],
            "moenv.environmental_control.water_release_date": [("2006-01-24", [v.format(1, "waterreleasedate")], [])],
            "moenv.environmental_control.waste_raw": [("1", [v.format(0, "iswaste"), v.format(1, "iswaste")], [])],
            "moenv.environmental_control.waste_release_date": [
                ("2020-01-01", [v.format(0, "wastereleasedate"), v.format(1, "wastereleasedate")], [])],
        })
        control, status = public_registry._environmental_control(entity, claims)
        self.assertEqual(control["water"]["variant_states"],
                         [{"flag": "1", "release_date": None}, {"flag": "1", "release_date": "2006-01-24"}])
        self.assertEqual(status, "Registry variants differ on release: water released 2006-01-24 in one registry "
                                 "variant, not in another; waste released 2020-01-01 (registry fact)")
        claims[entity["scalar_fields"]["moenv.environmental_control.water_release_date"][0]["claim_id"]][
            "sections"] = [v.format(0, "waterreleasedate"), v.format(1, "waterreleasedate")]
        _, status = public_registry._environmental_control(entity, claims)
        self.assertTrue(status.startswith("Released from every listed control category"))

    def test_factory_link_normalizes_names_and_separates_missing_numbers(self):
        factory, claims = self.entity("taiwan-ida-factory:t:1", {
            "name": [("台灣積體電路製造股份有限公司先進封測八廠", [], [])],
            "taiwan_factory_registry.unified_business_number": [("22099131", [], [])],
            "taiwan_factory_registry.factory_registration_number": [("94Q00001", [], [])],
            "taiwan_factory_registry.principal_product_token": [
                ("293", ["source_record.row.principal_products[10]"], []),
                ("269", ["source_record.row.principal_products[2]"], []),
                ("261半導體", ["source_record.row.principal_products[0]"], [])],
            "address.street": [("新竹市", [], [])],
        })
        link = public_registry._factory_link(factory, "台灣積體電\uf937製造股份有限公司先進封測八廠", [], claims)
        self.assertEqual(link["differs_from_environmental_registry"], [])
        self.assertEqual(link["missing_from_environmental_registry"], ["unified_business_number"])
        self.assertEqual(link["principal_products"], ["261半導體", "269", "293"])  # numeric, not text, order
        link = public_registry._factory_link(factory, "其他公司", ["12345678"], claims)
        self.assertEqual(link["differs_from_environmental_registry"], ["name", "unified_business_number"])

    def test_nist_scope_rules_and_amount_pairing(self):
        scope = public_registry._section_scope
        self.assertEqual(scope("Project Statistics: Boise, Idaho", program_member=True, program_entity=False), "site")
        self.assertEqual(scope("award index card amount", program_member=True, program_entity=False), "site")
        self.assertEqual(scope("Micron Project Overview", program_member=True, program_entity=False), "program")
        self.assertEqual(scope("Samsung Project Overview", program_member=False, program_entity=False), "award")
        self.assertEqual(scope("Recipient", program_member=True, program_entity=False), "program")
        with self.assertRaises(RegistryError):
            scope("Somewhere else", program_member=False, program_entity=False)
        entity, claims = self.entity("nist:project:t", {
            "direct_funding.source_text": [
                ("Award Amount: $77 million in direct funding", ["award index card amount"], []),
                ("Up to $75 million", ["X Project Overview / Direct Funding Amount"], [])],
        })
        card, overview = (c["claim_id"] for c in entity["scalar_fields"]["direct_funding.source_text"])
        more, more_claims = self.entity("nist:project:t", {
            "direct_funding.site_amount_usd": [(77_000_000, [], [card])],
            "direct_funding.project_amount_usd": [(75_000_000, [], [overview])],
            "direct_funding.qualifier": [("unqualified", [], [card]), ("up_to", [], [overview])],
        })
        entity["scalar_fields"].update(more["scalar_fields"])
        claims.update(more_claims)
        rows = public_registry._award_statements(entity, claims, program_member=False, program_entity=False)
        self.assertEqual([(r["scope"], r["amounts_usd"], r["qualifiers"]) for r in rows],
                         [("award", [77_000_000], ["unqualified"]), ("award", [75_000_000], ["up_to"])])
        # A project amount derived from a program member's own site section stops the build.
        with self.assertRaises(RegistryError):
            public_registry._award_statements(entity, claims, program_member=True, program_entity=False)

    def test_epa_exclusions_must_all_match_the_release(self):
        entities = {}
        for registry_id in sorted(public_registry.EPA_PERSONAL_NAME_EXCLUSIONS)[1:]:
            entity, _ = self.entity(f"epa:frs:{registry_id}", {"frs.registry_id": [(registry_id, [], [])]})
            entities[registry_id] = entity
        with self.assertRaisesRegex(RegistryError, "exclusions no longer match"):
            public_registry._epa_rows(entities)
