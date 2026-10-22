"""Derive the public facility-registry bundle from retained, hash-verified local evidence.

The bundle republishes registry records already in the local core: Taiwan environmental-registry
and registered-factory records selected by exact semiconductor codes, U.S. CHIPS award pages,
European industrial-reporting facilities admitted after a scope review, and unverified U.S. EPA
FRS leads. It adds no new observations. Every row keeps its source-native key and source link, registry flags stay
registry facts, and no value is promoted to operation, ownership, output or capacity.

Inputs are opened read-only and verified by SHA-256 before anything is derived:

* ``releases/2026-07-20-open-seed-taiwan-tax-relationships`` (manifest pinned in
  ``LOCAL_DATA_SHA256SUMS``), which carries the MOENV, IDA, NIST and EPA FRS entities and the
  reviewed MOENV-to-IDA facility-identity assignments;
* ``artifacts/2026-09-07-eea-scope-revisions-v3.sqlite``, the schema-5 working database with the
  15 accepted in-scope EEA facilities (``docs/eea_scope_revisions.md``);
* ``review_plans/2026-07-20-eea-industrial-v16-candidates.json`` (tracked), for each admitted EEA
  facility's latest reporting year.

The output is deterministic: identical inputs produce byte-identical files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adapters.eea_industrial import EEA_ATTRIBUTION, EEA_DOI_URL, EEA_LICENSE_URL
from .adapters.epa_frs import FRS_ATTRIBUTION, FRS_LANDING_PAGE_URL, FRS_LICENSE_URL
from .adapters.moenv_ems import MOENV_ATTRIBUTION, MOENV_LICENSE_URL
from .adapters.taiwan_factory_registry import TAIWAN_FACTORY_ATTRIBUTION, TAIWAN_FACTORY_LICENSE_URL

FORMAT = "semiconductor-atlas-public-registry-v1"
SOURCE_RELEASE = "releases/2026-07-20-open-seed-taiwan-tax-relationships"
SOURCE_RELEASE_MANIFEST_SHA256 = "6d7bca2cd392d1372774ae227ae9cd6e3a665454e2c25084b357c9b58691f4ce"
EEA_DATABASE = "artifacts/2026-09-07-eea-scope-revisions-v3.sqlite"
EEA_DATABASE_SHA256 = "7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778"
EEA_CANDIDATES = "review_plans/2026-07-20-eea-industrial-v16-candidates.json"
EEA_CANDIDATES_SHA256 = "a5a0738edcbed94f6eb7b75e992e9342a5d1e3b12f04b9789e3191c7bbfaf58d"
NIST_COPYRIGHT_URL = "https://www.nist.gov/copyrights-disclaimers"

# Attribution strings required by docs/data_contract.md, taken from the source adapters.
ATTRIBUTION = {
    "taiwan-moenv-ems": MOENV_ATTRIBUTION,
    "taiwan-ida-factory": TAIWAN_FACTORY_ATTRIBUTION,
    "us-official-semiconductor-awards": (
        "Source: National Institute of Standards and Technology (NIST), CHIPS for America award "
        "disclosures. U.S. government public information; credit NIST. Embedded third-party "
        "materials are not republished."
    ),
    "eea-industrial-reporting": EEA_ATTRIBUTION,
    "epa-frs": FRS_ATTRIBUTION,
}
DATASET_URLS = {
    "taiwan-moenv-ems": "https://data.moenv.gov.tw/dataset/detail/EMS_S_01",
    "taiwan-ida-factory": "https://data.gov.tw/dataset/6569",
    "eea-industrial-reporting": EEA_DOI_URL,
    "epa-frs": FRS_LANDING_PAGE_URL,
}
# Reuse terms for each source family, kept apart from the exact attribution strings above.
LICENSE_URLS = {
    "taiwan-moenv-ems": MOENV_LICENSE_URL,
    "taiwan-ida-factory": TAIWAN_FACTORY_LICENSE_URL,
    "us-official-semiconductor-awards": NIST_COPYRIGHT_URL,
    "eea-industrial-reporting": EEA_LICENSE_URL,
    "epa-frs": FRS_LICENSE_URL,
}
SOURCE_DATES = {
    "taiwan-moenv-ems": "2026-07-20",
    "taiwan-ida-factory": "2026-07-20",
    "us-official-semiconductor-awards": "2026-07-17",
    "eea-industrial-reporting": "2026-02-20",
    "epa-frs": "2026-07-01",
}

TAIWAN_COUNTIES = {
    "臺北市": "Taipei City", "新北市": "New Taipei City", "基隆市": "Keelung City",
    "桃園市": "Taoyuan City", "新竹市": "Hsinchu City", "新竹縣": "Hsinchu County",
    "苗栗縣": "Miaoli County", "臺中市": "Taichung City", "彰化縣": "Changhua County",
    "南投縣": "Nantou County", "雲林縣": "Yunlin County", "嘉義市": "Chiayi City",
    "嘉義縣": "Chiayi County", "臺南市": "Tainan City", "高雄市": "Kaohsiung City",
    "屏東縣": "Pingtung County", "宜蘭縣": "Yilan County", "花蓮縣": "Hualien County",
    "臺東縣": "Taitung County", "澎湖縣": "Penghu County", "金門縣": "Kinmen County",
    "連江縣": "Lienchiang County",
    # MOENV records this value in its county field for a few science-park facilities.
    "新竹科": "Hsinchu Science Park (as recorded)",
}
MOENV_INDUSTRIES = {
    ("2611", "積體電路製造業"): "Integrated circuit manufacturing",
    ("2612", "分離式元件製造業"): "Discrete semiconductor devices",
    ("2613", "半導體封裝及測試業"): "Semiconductor packaging and test",
}
EPA_CODES = {
    "334413": "NAICS 334413 Semiconductor and related device manufacturing",
    "3674": "SIC 3674 Semiconductors and related devices",
}
# Organization types whose names can be personal names; omitted from the public bundle.
PRIVATE_ORGANIZATION_TYPES = {"獨資"}
# EPA FRS leads whose facility name identifies a private individual or a residence. All 3,065
# lead names were screened on 2026-10-22 and every flag was checked independently: a bare
# personal name, a residence, a personal name doing business under a trade name, or initials
# and a surname followed only by a trade description. Incorporated names stay.
EPA_PERSONAL_NAME_EXCLUSIONS = frozenset({
    "110001336778",  # initial and surname + trade description
    "110012431715",  # initials and surname + trade description
    "110038437439",  # initials and surname + generic word
    "110055900930",  # personal name doing business as a trade name
    "110071422778",  # residence
    "110071519379",  # personal name
})
MAX_TEXT = 300

RECORD_COLUMNS = (
    "record_id", "record_type", "source_family", "name", "country_code", "admin1", "admin1_local",
    "locality", "address", "latitude", "longitude", "geometry_basis", "registry_classification",
    "registry_classification_code",
    "registry_status", "environmental_control", "latest_reporting_year", "identifiers",
    "linked_records", "award_statements", "source_url", "source_date",
)
MOENV_CATEGORIES = ("air", "water", "waste", "toxic", "soil")
AWARD_TOPICS = ("recipient", "description", "project_type", "technology", "direct_funding",
                "expected_capex", "jobs", "timeline")
# NIST award-card and Project Summary descriptions are full sentences; other statements are short.
TOPIC_MAX_TEXT = {"description": 1000}
LEAD_COLUMNS = (
    "record_id", "name", "city", "county", "state_code", "postal_code", "frs_registry_id",
    "industry_codes", "source_url", "source_date",
)


class RegistryError(ValueError):
    """Raised when inputs or a bundle do not satisfy the registry contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _values(entity: Mapping[str, Any], predicate: str) -> list[Any]:
    """Distinct source values for one predicate, in first-seen order."""
    seen: list[Any] = []
    for claim in entity["scalar_fields"].get(predicate, []):
        if claim["value"] not in seen:
            seen.append(claim["value"])
    return seen


def _one(entity: Mapping[str, Any], predicate: str) -> Any:
    values = _values(entity, predicate)
    if len(values) > 1:
        raise RegistryError(f"{entity['stable_key']}: conflicting {predicate} values")
    return values[0] if values else None


def _short(values: Iterable[Any]) -> str | None:
    texts = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if any(len(t) > MAX_TEXT for t in texts):
        raise RegistryError(f"source text over {MAX_TEXT} characters; review before publishing")
    return " | ".join(dict.fromkeys(texts)) or None


def _county(value: str | None, context: str) -> str:
    if not value:
        raise RegistryError(f"{context}: missing county")
    for prefix in sorted(TAIWAN_COUNTIES, key=len, reverse=True):
        if value.startswith(prefix):
            return prefix
    raise RegistryError(f"{context}: unmapped Taiwan county {value!r}")


def _load_release(root: Path) -> tuple[dict[str, dict], list[dict], dict]:
    release = root / SOURCE_RELEASE
    manifest_path = release / "manifest.json"
    if _sha256_file(manifest_path) != SOURCE_RELEASE_MANIFEST_SHA256:
        raise RegistryError("source release manifest does not match the pinned hash")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in ("entities.jsonl", "source_entity_assignments.jsonl"):
        expected = manifest["files"][name]
        if _sha256_file(release / name) != expected["sha256"]:
            raise RegistryError(f"{name} does not match the source release manifest")
    entities: dict[str, dict] = {}
    with (release / "entities.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            entity = json.loads(line)
            entities[entity["entity_id"]] = entity
    with (release / "source_entity_assignments.jsonl").open(encoding="utf-8") as stream:
        assignments = [json.loads(line) for line in stream]
    return entities, assignments, manifest


def _claim_index(root: Path, manifest: Mapping[str, Any], claim_ids: set[str]) -> dict[str, dict]:
    """Dependencies and evidence sections for the named claims, from the verified claims file."""
    path = root / SOURCE_RELEASE / "claims.jsonl"
    if _sha256_file(path) != manifest["files"]["claims.jsonl"]["sha256"]:
        raise RegistryError("claims.jsonl does not match the source release manifest")
    index: dict[str, dict] = {}
    anomalies: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            claim = json.loads(line)
            if claim["id"] in claim_ids:
                index[claim["id"]] = {
                    "dependencies": {d["depends_on_claim_version_id"] for d in claim["dependencies"]},
                    "sections": sorted({e["locator"].split(" / ")[0] for e in claim["evidence"]}),
                    "excerpts": sorted({e.get("excerpt") or "" for e in claim["evidence"]}),
                    "documents": sorted({e["document_url"] for e in claim["evidence"]
                                         if e.get("document_url")}),
                    "anomalies": [],
                }
            elif (claim["predicate"] == "data_quality.source_value_anomaly"
                  and claim["superseded_at"] is None and claim["valid_to"] is None):
                # The release's own data-quality flags, tied to the statements they question.
                for dependency in claim["dependencies"]:
                    anomalies.setdefault(dependency["depends_on_claim_version_id"], []).append(
                        claim["value"]["description"])
    if claim_ids - index.keys():
        raise RegistryError("source release is missing claims named by its entities")
    for claim_id, descriptions in anomalies.items():
        if claim_id in index:
            index[claim_id]["anomalies"] = sorted(set(descriptions))
    return index


def _taiwan_rows(entities: dict[str, dict], assignments: list[dict],
                 claims: Mapping[str, dict]) -> tuple[list[dict], dict]:
    by_key = {e["stable_key"]: e for e in entities.values()}
    links: dict[str, list[str]] = {}  # MOENV key -> IDA keys (reviewed facility identity)
    for assignment in assignments:
        if assignment["identity_scope"] != "facility_identity" or assignment["superseded_at"]:
            continue
        observed = assignment["observed_entity_stable_key"]
        canonical = assignment["canonical_entity_stable_key"]
        if not (observed.startswith("taiwan-moenv-ems:") and
                canonical.startswith("taiwan-ida-factory:")):
            raise RegistryError(f"unexpected facility assignment {observed} -> {canonical}")
        links.setdefault(observed, []).append(canonical)
    linked_factories = {key for keys in links.values() for key in keys}
    rows = []
    excluded_private = 0
    for key, entity in sorted(by_key.items()):
        if key.startswith("taiwan-moenv-ems:"):
            code, label = _one(entity, "moenv.industry_code"), _one(entity, "moenv.industry_label")
            if (code, label) not in MOENV_INDUSTRIES:
                raise RegistryError(f"{key}: unexpected industry {code} {label}")
            county = _county(_one(entity, "address.county"), key)
            control, status = _environmental_control(entity, claims)
            geometry = entity["geometry"]
            lon, lat = geometry["coordinates"] if geometry else (None, None)
            if geometry and (geometry["type"] != "Point" or not (21.5 <= lat <= 26.5) or
                             not (118.0 <= lon <= 122.5)):
                raise RegistryError(f"{key}: geometry outside the Taiwan source-point contract")
            factories = sorted(links.get(key, []))
            identifiers = {"moenv_control_number": _one(entity, "moenv.emsno")}
            business_numbers = _values(entity, "moenv.uniformno")
            if business_numbers:
                # Conflicting source variants stay parallel; none is selected.
                identifiers["unified_business_number"] = " | ".join(business_numbers)
            name = _one(entity, "name")
            rows.append({
                "record_id": key,
                "record_type": "registry_record",
                "source_family": "taiwan-moenv-ems",
                "name": name,
                "country_code": "TW",
                "admin1": TAIWAN_COUNTIES[county],
                "admin1_local": county,
                "locality": _one(entity, "address.township"),
                "address": _one(entity, "address.street"),
                "latitude": lat,
                "longitude": lon,
                "geometry_basis": "source_reported_point" if geometry else None,
                "registry_classification": MOENV_INDUSTRIES[(code, label)],
                "registry_classification_code": f"Taiwan industry {code} {label}",
                "registry_status": status,
                "environmental_control": control,
                "latest_reporting_year": None,
                "identifiers": identifiers,
                "linked_records": [_factory_link(by_key[factory], name, business_numbers, claims)
                                   for factory in factories],
                "award_statements": None,
                "source_url": DATASET_URLS["taiwan-moenv-ems"],
                "source_date": SOURCE_DATES["taiwan-moenv-ems"],
            })
        elif key.startswith("taiwan-ida-factory:") and key not in linked_factories:
            org_type = _one(entity, "taiwan_factory_registry.organization_type")
            if org_type in PRIVATE_ORGANIZATION_TYPES:
                excluded_private += 1
                continue
            area = _one(entity, "address.administrative_area")
            county = _county(area, key)
            products = _products(entity, claims)
            if "261半導體" not in products:
                raise RegistryError(f"{key}: missing exact 261 product token")
            rows.append({
                "record_id": key,
                "record_type": "registry_record",
                "source_family": "taiwan-ida-factory",
                "name": _one(entity, "name"),
                "country_code": "TW",
                "admin1": TAIWAN_COUNTIES[county],
                "admin1_local": county,
                "locality": area[len(county):] or None,
                "address": _one(entity, "address.street"),
                "latitude": None,
                "longitude": None,
                "geometry_basis": None,
                "registry_classification": "Semiconductors (registered principal product)",
                "registry_classification_code": "Taiwan registered product " + " / ".join(products),
                # The roster holds only factories whose publisher status is 生產中 ("in production"),
                # an administrative registration status.
                "registry_status": (f"Registered factory, registration status "
                                    f"{_one(entity, 'taiwan_factory_registry.registration_status')} "
                                    f"(administrative status, not operation)"),
                "environmental_control": None,
                "latest_reporting_year": None,
                "identifiers": {
                    "factory_registration_number": _one(
                        entity, "taiwan_factory_registry.factory_registration_number"),
                    "unified_business_number": _one(
                        entity, "taiwan_factory_registry.unified_business_number"),
                },
                "linked_records": [],
                "award_statements": None,
                "source_url": DATASET_URLS["taiwan-ida-factory"],
                "source_date": SOURCE_DATES["taiwan-ida-factory"],
            })
    # Only MOENV rows carrying a factory number were candidates for the reviewed match. Rows that
    # share a normalized name and a business number with an MOENV row are flagged, never merged.
    moenv_index: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row["source_family"] == "taiwan-moenv-ems":
            for number in (row["identifiers"].get("unified_business_number") or "").split(" | "):
                if number:
                    key = (_name_key(row["name"]), number)
                    moenv_index.setdefault(key, []).append(row)
    twins = 0
    for row in rows:
        if row["source_family"] != "taiwan-ida-factory":
            continue
        key = (_name_key(row["name"]), row["identifiers"]["unified_business_number"])
        for moenv in moenv_index.get(key, []):
            row["linked_records"].append({
                "relationship": "unreviewed_same_name_and_business_number",
                "source_key": moenv["record_id"],
                "moenv_control_number": moenv["identifiers"]["moenv_control_number"],
            })
        twins += bool(moenv_index.get(key))
    statuses: dict[str, int] = {}
    for key in [k for k in by_key if k.startswith("taiwan-ida-factory:")]:
        status = _one(by_key[key], "taiwan_factory_registry.registration_status")
        statuses[status] = statuses.get(status, 0) + 1
    return rows, {"excluded_private_organization_rows": excluded_private,
                  "ida_token_261_factories": sum(statuses.values()),
                  "ida_registration_statuses": statuses,
                  "reviewed_identity_links": sum(len(v) for v in links.values()),
                  "unreviewed_same_name_and_business_number": twins}


VARIANT_LOCATOR = re.compile(r"^source_record\.variants\[(\d+)\]\.(\w+)$")


def _environmental_control(entity: Mapping[str, Any],
                           claims: Mapping[str, dict]) -> tuple[dict[str, Any], str]:
    """The five MOENV category flags and release dates, per registry variant, and a summary.

    A flag of 1 lists a category; a release date records release from that category. Neither is
    operation, closure or lifecycle evidence (docs/data_contract.md). A control number can have
    several registry variants; each variant's flag stays paired with that variant's release date
    (or its absence), so variants that disagree stay parallel.
    """
    control, parts, kinds = {}, [], []
    for category in MOENV_CATEGORIES:
        by_variant: dict[int, dict[str, Any]] = {}
        for field, key in ((f"{category}_raw", "flag"), (f"{category}_release_date", "release_date")):
            for claim in entity["scalar_fields"].get(f"moenv.environmental_control.{field}", []):
                for locator in claims[claim["claim_id"]]["sections"]:
                    match = VARIANT_LOCATOR.match(locator)
                    if not match:
                        raise RegistryError(f"{entity['stable_key']}: unexpected locator {locator!r}")
                    variant = by_variant.setdefault(int(match.group(1)), {})
                    if key in variant:
                        raise RegistryError(f"{entity['stable_key']}: two {field} values in one variant")
                    variant[key] = claim["value"]
        if not by_variant or any("flag" not in v for v in by_variant.values()):
            raise RegistryError(f"{entity['stable_key']}: a variant without a {category} flag")
        states = sorted({(v["flag"], v.get("release_date")) for v in by_variant.values()},
                        key=lambda x: (x[0], x[1] or ""))
        if {f for f, _ in states} - {"0", "1"} or any(d and f != "1" for f, d in states):
            raise RegistryError(f"{entity['stable_key']}: unexpected {category} flag or release date")
        control[category] = {
            "flag": _values(entity, f"moenv.environmental_control.{category}_raw"),
            "release_date": _values(entity, f"moenv.environmental_control.{category}_release_date"),
            "variant_states": [{"flag": f, "release_date": d} for f, d in states],
        }
        listed = [d for f, d in states if f == "1"]
        if not listed:
            continue
        dated = sorted({d for d in listed if d})
        text = category + (" (one registry variant flags 0)" if any(f == "0" for f, _ in states) else "")
        if dated and None in listed:
            text += " released " + " or ".join(dated) + " in one registry variant, not in another"
            kinds.append("mixed")
        elif dated:
            text += " released " + " or ".join(dated)
            if len(dated) > 1:
                text += " (registry variants differ)"
            kinds.append("released")
        else:
            kinds.append("listed")
        parts.append(text)
    details = "; ".join(parts)
    if not parts:
        status = "No environmental-control category listed (registry fact)"
    elif "listed" in kinds:
        status = f"Listed for environmental control: {details} (registry fact)"
    elif "mixed" in kinds:
        status = f"Registry variants differ on release: {details} (registry fact)"
    else:
        status = f"Released from every listed control category: {details} (registry fact)"
    return control, status


def _name_key(name: str | None) -> str:
    """A name for flagging possible twins: compatibility-normalized, with all spacing removed."""
    return "".join(unicodedata.normalize("NFKC", name or "").split())


def _same_text(a: str | None, b: str | None) -> bool:
    """Equal after Unicode compatibility normalization (MOENV uses compatibility ideographs)."""
    return unicodedata.normalize("NFKC", a or "") == unicodedata.normalize("NFKC", b or "")


PRODUCT_LOCATOR = re.compile(r"^source_record\.row\.principal_products\[(\d+)\]$")


def _products(entity: Mapping[str, Any], claims: Mapping[str, dict]) -> list[str]:
    """Principal products in the registry's own order, from each claim's position locator."""
    positions = []
    for claim in entity["scalar_fields"].get("taiwan_factory_registry.principal_product_token", []):
        for locator in claims[claim["claim_id"]]["sections"]:
            match = PRODUCT_LOCATOR.match(locator)
            if not match:
                raise RegistryError(f"{entity['stable_key']}: unexpected product locator {locator!r}")
            positions.append((int(match.group(1)), claim["value"]))
    return list(dict.fromkeys(value for _, value in sorted(positions)))


def _factory_link(factory: Mapping[str, Any], moenv_name: str | None,
                  moenv_business_numbers: list[Any], claims: Mapping[str, dict]) -> dict[str, Any]:
    """A reviewed MOENV-to-IDA match, with the factory's own registry values kept in parallel.

    Name and business number are compared; a value present on only one side is reported as
    missing, not as a disagreement. Addresses are kept side by side but not compared, because the
    two registries write the same address differently.
    """
    if _one(factory, "taiwan_factory_registry.organization_type") in PRIVATE_ORGANIZATION_TYPES:
        raise RegistryError(f"{factory['stable_key']}: reviewed match to a sole proprietorship")
    name = _one(factory, "name")
    business_numbers = _values(factory, "taiwan_factory_registry.unified_business_number")
    differs, missing = [], []
    if not _same_text(name, moenv_name):
        differs.append("name")
    if business_numbers and not moenv_business_numbers:
        missing.append("unified_business_number")
    elif business_numbers and not set(business_numbers) & set(moenv_business_numbers):
        differs.append("unified_business_number")
    return {
        "relationship": "reviewed_same_facility",
        "source_key": factory["stable_key"],  # a source-release key; matched factories are not rows
        "factory_registration_number": _one(
            factory, "taiwan_factory_registry.factory_registration_number"),
        "name": name,
        "unified_business_number": " | ".join(business_numbers) or None,
        "address": _one(factory, "address.street"),
        "principal_products": _products(factory, claims),
        "differs_from_environmental_registry": differs,
        "missing_from_environmental_registry": missing,
    }


def _site_label(site: Mapping[str, Any]) -> str | None:
    label = ", ".join(x for x in (_one(site, "location.locality"), _one(site, "location.region"))
                      if x)
    # Sites known only from a detail page's "Location(s)" list carry just the page text.
    return label or _short(_values(site, "location.source_text"))


def _section_scope(section: str, *, program_member: bool, program_entity: bool) -> str:
    """Scope of a NIST statement from the page section its evidence cites.

    ``site``: a program member page's own award card or Project Statistics section.
    ``award``: anything on a page that is not part of a multi-page program; the page describes
    one award, which may cover more than one listed location (Samsung Austin and Taylor).
    ``program``: a program's overview, shared across its member pages and never allocated.
    """
    if program_entity:
        return "program"
    if section == "Recipient":
        # A detail page has at most one Recipient field (27 of the 28 pages of 2026-07-17; SK hynix
        # has none). On every program member page it sits in the shared "<Company> Project
        # Overview" table, so it describes the program.
        return "program" if program_member else "award"
    if section == "Project Summary":
        # The narrative summary sits in each page's own Key Facts block. On a program member page
        # it is provisionally program scope; _nist_rows keeps it there only when every member page
        # carries the same paragraph, and otherwise returns it to its own page as "page" scope (the
        # paragraph may describe more than that page's site).
        return "program" if program_member else "award"
    if section.startswith("Project Statistics") or section in ("award index card amount",
                                                                "award index card description"):
        return "site" if program_member else "award"
    if section.endswith("Project Overview"):
        return "program" if program_member else "award"
    raise RegistryError(f"unclassified NIST evidence section {section!r}")


# Release amount predicates each scope may carry; any other pairing stops the build.
AMOUNT_SCOPES = {"site": {"site"}, "award": {"site", "project"}, "program": {"program"}}


def _award_statements(entity: Mapping[str, Any], claims: Mapping[str, dict], *,
                      program_member: bool, program_entity: bool) -> list[dict]:
    """Every NIST statement, verbatim, with its page sections and any amount derived from it.

    An amount and a qualifier belong to a statement when they were derived from that statement
    claim. Statements that yielded no amount are kept as text, so differing wording stays visible.
    """
    fields = entity["scalar_fields"]
    out = []
    for topic in AWARD_TOPICS:
        amounts = [(scope, claim) for scope in ("site", "project", "program")
                   for claim in fields.get(f"{topic}.{scope}_amount_usd", [])]
        qualifiers = [claim for predicate, values in fields.items()
                      if predicate.startswith(topic + ".") and predicate.endswith("qualifier")
                      for claim in values]
        for statement in fields.get(f"{topic}.source_text", []):
            text = str(statement["value"]).strip()
            if not text:
                continue
            if len(text) > TOPIC_MAX_TEXT.get(topic, MAX_TEXT):
                raise RegistryError(f"{entity['stable_key']}: NIST {topic} statement is too long")
            sections = claims[statement["claim_id"]]["sections"]
            scopes = {_section_scope(section, program_member=program_member,
                                     program_entity=program_entity) for section in sections}
            if len(scopes) != 1:
                raise RegistryError(f"{entity['stable_key']}: statement cites sections of mixed scope")
            scope = scopes.pop()
            paired = [(amount_scope, int(a["value"])) for amount_scope, a in amounts
                      if statement["claim_id"] in claims[a["claim_id"]]["dependencies"]]
            for amount_scope, _ in paired:
                if amount_scope not in AMOUNT_SCOPES[scope]:
                    raise RegistryError(f"{entity['stable_key']}: {amount_scope} amount from a "
                                        f"{scope} statement")
            row = {
                "topic": topic,
                "scope": scope,
                "text": text,
                "sections": sections,
                "pages": claims[statement["claim_id"]]["documents"],
                "source_anomalies": claims[statement["claim_id"]]["anomalies"],
                "amounts_usd": sorted({amount for _, amount in paired}),
                "qualifiers": sorted({q["value"] for q in qualifiers
                                      if statement["claim_id"] in claims[q["claim_id"]]["dependencies"]}),
            }
            _add_statement(out, row)
    # Recipients come only from a page's Recipient field. The awards index card has no recipient
    # field; names derived from its title are not published as recipients.
    for relation in entity["relationships"]:
        if relation["predicate"] != "relationship.recipient":
            continue
        sections = claims[relation["claim_id"]]["sections"]
        if sections == ["award index card recipient"]:
            continue
        scopes = {_section_scope(section, program_member=program_member,
                                 program_entity=program_entity) for section in sections}
        if len(scopes) != 1:
            raise RegistryError(f"{entity['stable_key']}: recipient cites sections of mixed scope")
        scope = scopes.pop()
        for name in claims[relation["claim_id"]]["excerpts"]:  # the page's own wording
            if not name.strip():
                raise RegistryError(f"{entity['stable_key']}: recipient without page text")
            _add_statement(out, {"topic": "recipient", "scope": scope, "text": name.strip(),
                                 "sections": sections, "pages": claims[relation["claim_id"]]["documents"],
                                 "source_anomalies": [], "amounts_usd": [], "qualifiers": []})
    return out


def _add_statement(statements: list[dict], row: dict) -> None:
    """Add a statement; a repeat of the same text, topic and scope merges into the first."""
    for existing in statements:
        if (existing["topic"], existing["scope"], existing["text"]) == \
                (row["topic"], row["scope"], row["text"]):
            for key in ("sections", "pages", "source_anomalies", "amounts_usd", "qualifiers"):
                existing[key] = sorted(set(existing[key]) | set(row[key]))
            return
    statements.append(row)


def _nist_rows(entities: dict[str, dict], claims: Mapping[str, dict]) -> list[dict]:
    named = [e for e in sorted(entities.values(), key=lambda e: e["stable_key"])
             if e["stable_key"].startswith("nist:project:") and _values(e, "name")]
    # Unnamed project entities are multi-site program aggregates, reported as program context.
    membership: dict[str, tuple[dict, int]] = {}
    for entity in named:
        programs = {r["object_entity_id"]: len(r["attributes"]["member_urls"])
                    for r in entity["relationships"] if r["predicate"] == "relationship.award_program"}
        if len(programs) > 1:
            raise RegistryError(f"{entity['stable_key']}: more than one award program")
        for program_id, pages in programs.items():
            membership[entity["entity_id"]] = (entities[program_id], pages)
    # Program statements are shared: every member row shows every member page's wording.
    program_statements: dict[str, list[dict]] = {}
    member_pages: dict[str, set[str]] = {}
    for entity in named:
        if entity["entity_id"] in membership:
            program, _ = membership[entity["entity_id"]]
            member_pages.setdefault(program["entity_id"], set()).add(_one(entity, "canonical_url"))
    for entity in named:
        if entity["entity_id"] not in membership:
            continue
        program, _ = membership[entity["entity_id"]]
        shared = program_statements.setdefault(program["entity_id"], [])
        if not shared:
            for row in _award_statements(program, claims, program_member=True, program_entity=True):
                _add_statement(shared, row)
        for row in _award_statements(entity, claims, program_member=True, program_entity=False):
            if row["scope"] == "program":
                _add_statement(shared, row)
    # A Key Facts summary that differs between member pages describes its own page (Micron).
    page_summaries: dict[str, list[dict]] = {}
    for program_id, shared in program_statements.items():
        for row in list(shared):
            if row["sections"] == ["Project Summary"] and set(row["pages"]) != member_pages[program_id]:
                shared.remove(row)
                for page in row["pages"]:
                    page_summaries.setdefault(page, []).append(dict(row, scope="page", pages=[page]))
    rows = []
    for entity in named:
        own_sites, other_sites = [], []
        for relation in entity["relationships"]:
            other = entities.get(relation["object_entity_id"])
            if other is None:
                continue
            if relation["predicate"] == "relationship.site":
                label = _site_label(other)
                if not label:
                    raise RegistryError(f"{other['stable_key']}: NIST site without a location")
                # "location" is the detail page's own site; "Location(s)" lists every location the
                # page names. Geography never moves from one site to another.
                if relation["attributes"].get("source_field") == "location":
                    own_sites.append(label)
                else:
                    other_sites.append(label)
        own_sites = list(dict.fromkeys(own_sites))
        if not own_sites:
            raise RegistryError(f"{entity['stable_key']}: no detail-page site location")
        other_sites = [x for x in dict.fromkeys(other_sites) if x not in own_sites]
        member = membership.get(entity["entity_id"])
        statements = [row for row in _award_statements(entity, claims, program_member=bool(member),
                                                       program_entity=False)
                      if row["scope"] != "program"]
        statements += page_summaries.get(_one(entity, "canonical_url"), [])
        award: dict[str, Any] = {"statements": statements}
        if member:
            program, pages = member
            award["program_member_pages"] = pages
            award["program_key"] = program["stable_key"]  # shared by the program's member rows
            statements.extend(program_statements[program["entity_id"]])
        statements.sort(key=lambda r: (AWARD_TOPICS.index(r["topic"]),
                                       ("site", "award", "page", "program").index(r["scope"]),
                                       r["amounts_usd"][:1] or [0], r["text"]))
        linked = []
        if other_sites:
            linked.append({"relationship": "other_program_locations" if member
                           else "other_locations_on_award_page", "sites": other_sites})
        updated = _one(entity, "source.updated_at") or _one(entity, "source.published_at") or ""
        rows.append({
            "record_id": entity["stable_key"],
            "record_type": "award_project",
            "source_family": "us-official-semiconductor-awards",
            "name": _one(entity, "name"),
            "country_code": "US",
            "admin1": " / ".join(dict.fromkeys(s.split(", ")[-1] for s in own_sites)),
            "admin1_local": None,
            "locality": " / ".join(s.split(", ")[0] for s in own_sites if ", " in s) or None,
            "address": None,
            "latitude": None,
            "longitude": None,
            "geometry_basis": None,
            # Project type and technology are scoped statements in award_statements.
            "registry_classification": None,
            "registry_classification_code": None,
            "registry_status": _short(_values(entity, "application_stage.source_text")),
            "environmental_control": None,
            "latest_reporting_year": None,
            "identifiers": {},
            "linked_records": linked,
            "award_statements": award,
            "source_url": _one(entity, "canonical_url"),
            # The award page's last-updated date in the captured version (2026-07-17 snapshot).
            "source_date": updated[:10] or None,
        })
    return rows


def _eea_reporting_years(path: Path) -> dict[str, str]:
    if _sha256_file(path) != EEA_CANDIDATES_SHA256:
        raise RegistryError("EEA candidate queue does not match the pinned hash")
    queue = json.loads(path.read_text(encoding="utf-8"))
    return {c["facility_inspire_id"]: c["latest_source_detail_reporting_year"]
            for c in queue["candidates"]}


def _eea_rows(database: Path, reporting_years: Mapping[str, str]) -> list[dict]:
    if _sha256_file(database) != EEA_DATABASE_SHA256:
        raise RegistryError("EEA database does not match the pinned hash")
    con = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        query = """
            SELECT e.stable_key, cs.predicate, sv.text_value
            FROM entities e
            JOIN claim_series cs ON cs.subject_entity_id = e.id
            JOIN claim_versions cv ON cv.series_id = cs.id
            JOIN scalar_values sv ON sv.claim_version_id = cv.id
            WHERE e.kind = 'facility' AND e.stable_key LIKE 'eea-industrial-reporting:%'
              AND cv.superseded_at IS NULL AND cv.valid_to IS NULL
            ORDER BY e.stable_key, cs.predicate, sv.text_value
        """
        fields: dict[str, dict[str, list[str]]] = {}
        for key, predicate, value in con.execute(query):
            fields.setdefault(key, {}).setdefault(predicate, [])
            if value is not None and value not in fields[key][predicate]:
                fields[key][predicate].append(value)
    finally:
        con.close()
    rows = []
    for key, f in sorted(fields.items()):
        def one(predicate: str) -> str | None:
            values = f.get(predicate, [])
            if len(values) > 1:
                raise RegistryError(f"{key}: conflicting {predicate}")
            return values[0] if values else None
        code = one("eea_industrial.nace_main_economic_activity_code")
        inspire_id = one("eea_industrial.facility_inspire_id")
        if inspire_id not in reporting_years:
            raise RegistryError(f"{key}: no reporting year in the EEA candidate queue")
        rows.append({
            "record_id": key,
            "record_type": "registry_record",
            "source_family": "eea-industrial-reporting",
            "name": one("name"),
            "country_code": one("address.country_code"),
            "admin1": None,
            "admin1_local": None,
            "locality": one("address.city"),
            # EEA publishes street, building number and postal code as separate fields; they are
            # joined with semicolons, each unchanged.
            "address": "; ".join(x for x in (one("address.street"), one("address.building_number"),
                                             one("address.postal_code")) if x) or None,
            "latitude": None,
            "longitude": None,
            "geometry_basis": None,
            "registry_classification": one("eea_industrial.nace_main_economic_activity_name"),
            "registry_classification_code": f"NACE {code}" if code else None,
            "registry_status": "Accepted in scope after AI-assisted review (reporting record, not operation)",
            "environmental_control": None,
            # The facility's most recent reporting year in EEA version 16, not an operating date.
            "latest_reporting_year": reporting_years[inspire_id],
            "identifiers": {"inspire_id": inspire_id},
            "linked_records": [],
            "award_statements": None,
            "source_url": DATASET_URLS["eea-industrial-reporting"],
            "source_date": SOURCE_DATES["eea-industrial-reporting"],
        })
    if len(rows) != 15:
        raise RegistryError(f"expected the 15 accepted EEA facilities, found {len(rows)}")
    return rows


def _epa_rows(entities: dict[str, dict]) -> tuple[list[dict], int]:
    rows = []
    excluded = set()
    for entity in sorted(entities.values(), key=lambda e: e["stable_key"]):
        if not entity["stable_key"].startswith("epa:frs:"):
            continue
        if entity["geometry"] is not None:
            raise RegistryError(f"{entity['stable_key']}: EPA FRS rows must not carry geometry")
        registry_id = _one(entity, "frs.registry_id")
        if registry_id in EPA_PERSONAL_NAME_EXCLUSIONS:
            excluded.add(registry_id)
            continue
        # A registry row can carry several codes; only the exact filter codes are labelled.
        codes = [EPA_CODES[c] for c in
                 _values(entity, "frs.naics_code") + _values(entity, "frs.sic_code")
                 if c in EPA_CODES]
        rows.append({
            "record_id": entity["stable_key"],
            "name": _one(entity, "name"),
            "city": _one(entity, "address.city"),
            "county": _one(entity, "address.county"),
            "state_code": _one(entity, "address.state_code"),
            "postal_code": _one(entity, "address.postal_code"),
            "frs_registry_id": registry_id,
            "industry_codes": " | ".join(codes) or None,
            "source_url": DATASET_URLS["epa-frs"],
            "source_date": SOURCE_DATES["epa-frs"],
        })
    if excluded != EPA_PERSONAL_NAME_EXCLUSIONS:
        raise RegistryError("EPA personal-name exclusions no longer match the source release")
    return rows, len(excluded)


def _jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n" for r in rows).encode("utf-8")


def _csv(columns: tuple[str, ...], rows: Iterable[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        out = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""
            text = "" if value is None else str(value)
            if text[:1] in ("=", "+", "-", "@") and column not in ("latitude", "longitude"):
                text = "'" + text  # keep spreadsheets from evaluating source text
            out.append(text)
        writer.writerow(out)
    return buffer.getvalue().encode("utf-8")


def _geojson(rows: Iterable[Mapping[str, Any]]) -> bytes:
    features = []
    for row in rows:
        if row["latitude"] is None:
            continue
        properties = {k: row[k] for k in ("record_id", "name", "admin1", "locality",
                                          "geometry_basis", "source_url", "source_date",
                                          "registry_classification",
                                          "registry_status", "source_family")}
        properties["moenv_control_number"] = row["identifiers"]["moenv_control_number"]
        features.append({"type": "Feature", "id": row["record_id"],
                         "geometry": {"type": "Point",
                                      "coordinates": [row["longitude"], row["latitude"]]},
                         "properties": properties})
    collection = {
        "type": "FeatureCollection",
        # Points are coordinates the MOENV registry reports; none is surveyed or geocoded.
        "geometry_basis": "source_reported_point",
        "attribution": ATTRIBUTION["taiwan-moenv-ems"],
        "license": LICENSE_URLS["taiwan-moenv-ems"],
        "features": features,
    }
    return (json.dumps(collection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def _readme(counts: Mapping[str, Any]) -> bytes:
    text = f"""# Semiconductor Atlas facility registry

Registry records with semiconductor codes and U.S. CHIPS award pages, republished from retained,
hash-verified evidence in the local Semiconductor Atlas core. This bundle adds no new observations.

| Source | Public rows |
| --- | --- |
| Taiwan MOENV environmental registry (every record with exact industry code 2611, 2612 or 2613) | {counts['taiwan-moenv-ems']} |
| Taiwan IDA registered factories (records with exact product token 261 in the in-production factory roster, without a reviewed MOENV match; 2 sole proprietorships withheld) | {counts['taiwan-ida-factory']} |
| NIST CHIPS for America final-award pages linked from the chips-america-awards index in the 17 July 2026 snapshot ({counts['nist_program_member_pages']} of them in {counts['nist_multi_page_programs']} multi-page programs) | {counts['us-official-semiconductor-awards']} |
| EEA industrial reporting, accepted in scope after AI-assisted review | {counts['eea-industrial-reporting']} |
| U.S. EPA FRS unverified leads (exact NAICS 334413 or SIC 3674), in `us_epa_frs_leads.*` | {counts['epa-frs']} |

How to read it:

- Taiwan rows are every record carrying the exact code or token in the archived registry
  packages, with no per-record review of whether it is a semiconductor facility. Some MOENV rows
  coded 2611 are construction works or firms in other trades. The MOENV dataset includes
  deregistered historical entities (see `environmental_control`). The IDA package is the published
  in-production factory roster (生產中工廠清冊); it also lists a few factories under stop-work orders,
  but no other registration statuses. Of its {counts['ida_token_261_factories']} factories with the
  261 token, the registration statuses are {', '.join(f'{k} {v}' for k, v in sorted(counts['ida_registration_statuses'].items()))}
  ("in production" is an administrative status, not observed operation).
  {counts['taiwan-ida-factory']} appear as separate rows,
  {counts['ida_token_261_factories'] - counts['taiwan-ida-factory'] - counts['excluded_private_organization_rows']}
  appear inside {counts['reviewed_identity_links']} reviewed links on MOENV rows, and
  {counts['excluded_private_organization_rows']} sole proprietorships are withheld.
- Reviews named here were AI-assisted: the MOENV-to-IDA identity matches were decided by a Codex
  agent (`review_plans/2026-07-20-taiwan-moenv-factory-review.json`; its reasons say "manual
  review", which was the agent's review), and the EEA scope admission was a Codex-assisted
  collaborative review (`docs/eea_scope_admission_2026-09-07.md`), not independent human sign-off.
- Rows are registry records (`record_type` `registry_record`) or NIST award pages
  (`award_project`), not a census of distinct plants. Several MOENV control numbers can belong to
  one plant, and a registered factory can match more than one MOENV row.
- `registry_classification` is a Semiconductor Atlas label for the registry's classification:
  an English name for the MOENV industry code, "Semiconductors (registered principal product)" for
  IDA rows, and the EEA NACE activity name. `registry_classification_code` holds the registry's own
  values: the MOENV code and Chinese label, every product an IDA factory registered in the
  registry's order (including unrelated products), or the NACE code. Both are null for NIST rows.
  A code is a registration fact, not what a site does.
- `locality` is the MOENV township field for MOENV rows and the IDA administrative-area text after
  the county for IDA rows, which usually adds the village (里 or 村).
- Registry membership is not proof that a facility operates, produces semiconductors at any scale,
  or is owned by the named company. Environmental-control listings and factory-registration status
  are administrative registry facts.
- `environmental_control` gives each MOENV category (air, water, waste, toxic, soil) exactly as
  published: its flags (1 = listed), any release dates (release from that category), and
  `variant_states`, which pairs each registry variant's flag with that variant's release date or
  its absence. `registry_status` summarises them literally and says when registry variants
  differ: a release in one variant but not another, different release dates ("released A or B
  (registry variants differ)"), or a flag of 0 in one variant. A release date is not closure, and a
  listing is not operation.
- Coordinates appear only where the MOENV registry reports one consistent WGS84 point for the
  row. They are source-reported points, not surveyed boundaries. No location was geocoded.
- MOENV rows list the registered factory they match only where a reviewed identity assignment
  exists ({counts['reviewed_identity_links']} links). Each link keeps the factory's own registered
  name, business number, address and products beside the MOENV values.
  `differs_from_environmental_registry` lists a name or business number that disagrees (names are
  compared after Unicode compatibility normalization), and `missing_from_environmental_registry`
  a business number MOENV does not record ({counts['reviewed_links_with_differences']} links have
  either). Addresses are kept side by side but not compared, because the two registries write
  addresses differently. Matched factories appear only inside these links, not as separate rows.
- Only MOENV rows carrying a factory number that matched the IDA roster, exactly or after the
  documented legacy-certificate normalization (for example 95-C00128-00 to 95C00128), were
  candidates for the reviewed match, so "without a reviewed match" does not mean distinct.
  {counts['unreviewed_same_name_and_business_number']} registered-factory rows share a name
  (compatibility-normalized, spacing ignored) and a business number with an MOENV row; they carry
  an `unreviewed_same_name_and_business_number` link and are flagged, not merged.
- NIST `award_statements` keep every captured statement verbatim (recipient, award-card and
  Project Summary description, project type, technology, direct funding, expected capital
  expenditure, jobs, timeline) with its page section,
  any amounts and qualifiers derived from it, and its scope: `site` (a multi-page program member's
  own site section or award card), `award` (a page that is not part of a program; it may name more
  than one location) or `program` (a program overview, shared across its member pages and never
  allocated to a site; a page's Key Facts summary is program scope only when every member page
  carries the same paragraph, and otherwise `page` scope: that member page's own paragraph, which
  may describe more than one site). Each statement lists the `pages` it was read from, so a program statement
  shows which member page said it. Rows of one multi-page program share a `program_key` and give
  `program_member_pages`; this groups pages that share one NIST project overview and is not a count
  of awards (NIST describes separate awards to subsidiaries inside some programs). `source_anomalies` repeats the release's own data-quality
  flags (Infinera's "$456 billion" is flagged as an implausible amount; it is kept, unchanged,
  beside the "$456 million" the other page states). Recipients come only from a page's Recipient
  field. Amounts are never summed and are not evidence of construction or capacity. The source
  release did not capture every NIST field. Missing labels include "Technologies" (plural),
  "Project" (Micron Boise and Manassas site descriptions), "Total Project Cost", "Available Loan"
  and "CHIPS Workforce Funding", so a missing statement means "not captured", not "not stated by
  NIST". Coverage is the final-award pages linked from NIST's chips-america-awards index in the
  snapshot dated 17 July 2026 (downloaded by 01:54 UTC on 18 July). Proposed-award pages, CHIPS
  Program Office pages missing from that index, and NIST's NAPMP, R&D Office and SBIR awards are
  not included. NIST `source_date` is the award page's last-updated date in the captured version.
- EEA `latest_reporting_year` is the facility's most recent reporting year in version 16 of the
  EEA dataset, not an operating date. EEA addresses join the published street, building-number and
  postal-code fields with semicolons.
- EPA FRS rows are discovery leads only and carry no coordinates.
- {counts['excluded_private_organization_rows']} registered-factory rows for sole proprietorships
  and {counts['excluded_personal_name_leads']} EPA leads named after private individuals or
  residences are omitted because they can identify individuals.

Reuse terms for each source are linked in `ATTRIBUTION.md` and in `manifest.json` (`licenses`).

The JSON Lines files hold values exactly. In the CSV files, a cell that would start with `=`, `+`,
`-` or `@` gets a leading apostrophe so spreadsheets do not evaluate it (one EPA postal code,
`-0`, is affected).

Files: `records.jsonl` and `records.csv` (one row per registry record or award page),
`registry_points.geojson` (MOENV rows with a source-reported point, each carrying its geometry
basis, source link and date), `us_epa_frs_leads.jsonl` and
`.csv`, `ATTRIBUTION.md`, and `manifest.json` with SHA-256 hashes for every file.
"""
    return text.encode("utf-8")


def _attribution() -> bytes:
    lines = ["# Attribution", ""]
    for family, text in ATTRIBUTION.items():
        lines += [f"- {text}", f"  Terms: {LICENSE_URLS[family]}"]
    lines += ["", "Registry text is reproduced as published; EEA address fields are joined with "
              "semicolons. Translations of county names, industry codes and status labels were "
              "added by Semiconductor Atlas.", ""]
    return "\n".join(lines).encode("utf-8")


def build(root: Path, output: Path) -> dict[str, Any]:
    """Derive the registry bundle into ``output`` (which must not exist)."""
    if output.exists():
        raise RegistryError("output directory must not already exist")
    entities, assignments, release_manifest = _load_release(root)
    claim_ids = {claim["claim_id"] for entity in entities.values()
                 if entity["stable_key"].startswith("nist:project:")
                 for values in entity["scalar_fields"].values() for claim in values}
    claim_ids |= {claim["claim_id"] for entity in entities.values()
                  if entity["stable_key"].startswith("taiwan-moenv-ems:")
                  for predicate, values in entity["scalar_fields"].items()
                  if predicate.startswith("moenv.environmental_control.") for claim in values}
    claim_ids |= {claim["claim_id"] for entity in entities.values()
                  if entity["stable_key"].startswith("taiwan-ida-factory:")
                  for claim in entity["scalar_fields"].get(
                      "taiwan_factory_registry.principal_product_token", [])}
    claim_ids |= {relation["claim_id"] for entity in entities.values()
                  if entity["stable_key"].startswith("nist:project:")
                  for relation in entity["relationships"]
                  if relation["predicate"] == "relationship.recipient"}
    claims = _claim_index(root, release_manifest, claim_ids)
    taiwan, taiwan_stats = _taiwan_rows(entities, assignments, claims)
    nist = _nist_rows(entities, claims)
    eea = _eea_rows(root / EEA_DATABASE, _eea_reporting_years(root / EEA_CANDIDATES))
    leads, excluded_leads = _epa_rows(entities)
    order = {"taiwan-moenv-ems": 0, "taiwan-ida-factory": 1,
             "us-official-semiconductor-awards": 2, "eea-industrial-reporting": 3}
    records = sorted(taiwan + nist + eea, key=lambda r: (
        order[r["source_family"]], r["country_code"] or "", r["admin1"] or "", r["name"] or "",
        r["record_id"]))
    counts = {family: sum(1 for r in records if r["source_family"] == family) for family in order}
    counts["epa-frs"] = len(leads)
    counts.update(taiwan_stats)
    counts["excluded_personal_name_leads"] = excluded_leads
    # Pages that share one NIST "Project Overview" table. This is a grouping of pages, not a
    # count of awards: NIST describes several awards inside some of these programs.
    programs = [r["award_statements"]["program_key"] for r in nist if "program_key" in r["award_statements"]]
    counts["nist_multi_page_programs"] = len(set(programs))
    counts["nist_program_member_pages"] = len(programs)
    counts["reviewed_links_with_differences"] = sum(
        1 for r in taiwan for link in r["linked_records"]
        if link.get("differs_from_environmental_registry")
        or link.get("missing_from_environmental_registry"))
    counts["source_reported_points"] = sum(1 for r in records if r["latitude"] is not None)
    files = {
        "records.jsonl": _jsonl(records),
        "records.csv": _csv(RECORD_COLUMNS, records),
        "registry_points.geojson": _geojson(records),
        "us_epa_frs_leads.jsonl": _jsonl(leads),
        "us_epa_frs_leads.csv": _csv(LEAD_COLUMNS, leads),
        "README.md": _readme(counts),
        "ATTRIBUTION.md": _attribution(),
    }
    manifest = {
        "format": FORMAT,
        "inputs": {
            "source_release": SOURCE_RELEASE,
            "source_release_manifest_sha256": SOURCE_RELEASE_MANIFEST_SHA256,
            "source_release_recorded_at": release_manifest["recorded_at"],
            "eea_database": EEA_DATABASE,
            "eea_database_sha256": EEA_DATABASE_SHA256,
            "eea_candidates": EEA_CANDIDATES,
            "eea_candidates_sha256": EEA_CANDIDATES_SHA256,
        },
        "source_dates": SOURCE_DATES,
        "counts": counts,
        "attribution": ATTRIBUTION,
        "licenses": LICENSE_URLS,
        "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(files.items())},
    }
    output.mkdir(parents=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def validate(bundle: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    """Verify a tracked bundle against its pinned manifest hash and file hashes."""
    manifest_path = bundle / "manifest.json"
    if _sha256_file(manifest_path) != expected_manifest_sha256:
        raise RegistryError("registry manifest does not match the pinned hash")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise RegistryError("unexpected registry bundle format")
    present = {p.name for p in bundle.iterdir()}
    if present != set(manifest["files"]) | {"manifest.json"}:
        raise RegistryError("registry bundle contains missing or unmanaged files")
    for name, meta in manifest["files"].items():
        data = (bundle / name).read_bytes()
        if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise RegistryError(f"{name} does not match the registry manifest")
    return manifest


def read_records(bundle: Path, name: str = "records.jsonl") -> list[dict]:
    return [json.loads(line) for line in (bundle / name).read_text(encoding="utf-8").splitlines()]
