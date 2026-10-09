"""Bulk open-data layers: OpenStreetMap and Wikidata data center features.

These layers give breadth (thousands of mapped facilities) but are not curated build records.
They keep each publisher's license: OpenStreetMap-derived files are ODbL 1.0 and Wikidata-derived
files are CC0 1.0. Each layer is stored as normalized GeoJSON plus a retrieval manifest that
records the exact query, endpoint, retrieval time and SHA-256 of the raw response.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .geo import CountryLookup

USER_AGENT = "open-data-center-atlas/1.0 (+https://github.com/kiankyars/semiconductor-atlas)"

OSM_QUERY = """[out:json][timeout:900];
(
  nwr["telecom"="data_center"];
  nwr["building"="data_center"];
);
out center tags;
"""
OSM_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OSM_LICENSE = {
    "id": "ODbL-1.0",
    "name": "Open Data Commons Open Database License 1.0",
    "url": "https://opendatacommons.org/licenses/odbl/1-0/",
    "attribution": "© OpenStreetMap contributors",
    "attribution_url": "https://www.openstreetmap.org/copyright",
}

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = """SELECT ?item ?label ?coord ?country ?inception ?website ?operators ?owners WHERE {
  {
    SELECT ?item (SAMPLE(?c) AS ?coord) (SAMPLE(?cc) AS ?country) (MIN(?inc) AS ?inception)
           (SAMPLE(?site) AS ?website)
           (GROUP_CONCAT(DISTINCT ?opLabel; separator="; ") AS ?operators)
           (GROUP_CONCAT(DISTINCT ?owLabel; separator="; ") AS ?owners)
    WHERE {
      ?item wdt:P31/wdt:P279* wd:Q671224 .
      OPTIONAL { ?item wdt:P625 ?c . }
      OPTIONAL { ?item wdt:P17 ?countryItem . ?countryItem wdt:P297 ?cc . }
      OPTIONAL { ?item wdt:P571 ?inc . }
      OPTIONAL { ?item wdt:P856 ?site . }
      OPTIONAL { ?item wdt:P137 ?op . ?op rdfs:label ?opLabel . FILTER(LANG(?opLabel) = "en") }
      OPTIONAL { ?item wdt:P127 ?ow . ?ow rdfs:label ?owLabel . FILTER(LANG(?owLabel) = "en") }
    }
    GROUP BY ?item
  }
  OPTIONAL { ?item rdfs:label ?label . FILTER(LANG(?label) = "en") }
}
ORDER BY ?item
"""
WIKIDATA_LICENSE = {
    "id": "CC0-1.0",
    "name": "Creative Commons CC0 1.0 Universal",
    "url": "https://creativecommons.org/publicdomain/zero/1.0/",
    "attribution": "Wikidata",
    "attribution_url": "https://www.wikidata.org/",
}

OSM_COLUMNS = (
    "name",
    "operator",
    "owner",
    "brand",
    "website",
    "start_date",
    "addr:street",
    "addr:city",
    "addr:state",
    "addr:postcode",
    "addr:country",
    "building:levels",
    "telecom",
    "building",
)


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _http(url: str, *, data: bytes | None = None, accept: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, "Accept": accept}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed URLs
        return response.read()


def fetch_osm(endpoints: tuple[str, ...] = OSM_ENDPOINTS, timeout: int = 960) -> tuple[bytes, dict]:
    """Run the fixed Overpass query, trying mirrors in order, and return raw bytes + metadata."""
    errors = []
    for endpoint in endpoints:
        retrieved_at = _utc_now()
        try:
            raw = _http(endpoint, data=urllib.parse.urlencode({"data": OSM_QUERY}).encode(),
                        accept="application/json", timeout=timeout)
            json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - try the next mirror, report all failures
            errors.append(f"{endpoint}: {exc}")
            continue
        return raw, {"endpoint": endpoint, "retrieved_at": retrieved_at, "query": OSM_QUERY}
    raise RuntimeError("all Overpass endpoints failed: " + "; ".join(errors))


def fetch_wikidata(timeout: int = 300) -> tuple[bytes, dict]:
    retrieved_at = _utc_now()
    url = WIKIDATA_ENDPOINT + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY})
    raw = _http(url, accept="application/sparql-results+json", timeout=timeout)
    json.loads(raw)
    return raw, {"endpoint": WIKIDATA_ENDPOINT, "retrieved_at": retrieved_at,
                 "query": WIKIDATA_QUERY}


def _country(lookup: CountryLookup | None, lon: float, lat: float) -> str | None:
    if lookup is None:
        return None
    code = lookup.country_at(lon, lat)
    if code:
        return code
    # Coastal and small-island points can fall outside 1:50m polygons; probe a ~5 km ring.
    for dlon, dlat in ((.05, 0), (-.05, 0), (0, .05), (0, -.05),
                       (.05, .05), (-.05, .05), (.05, -.05), (-.05, -.05)):
        code = lookup.country_at(lon + dlon, lat + dlat)
        if code:
            return code
    return None


def normalize_osm(raw: bytes, lookup: CountryLookup | None) -> list[dict[str, Any]]:
    data = json.loads(raw)
    features = []
    for element in data.get("elements", []):
        kind = element.get("type")
        if kind == "node":
            lat, lon = element.get("lat"), element.get("lon")
        else:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        tags = dict(sorted((element.get("tags") or {}).items()))
        addr_country = (tags.get("addr:country") or "").strip().upper()
        if len(addr_country) == 2 and addr_country.isalpha():
            country, country_basis = addr_country, "addr:country tag"
        else:
            country = _country(lookup, lon, lat)
            country_basis = "Natural Earth boundary lookup" if country else None
        props: dict[str, Any] = {
            "id": f"osm-{kind}-{element['id']}",
            "osm_type": kind,
            "osm_id": element["id"],
            "osm_url": f"https://www.openstreetmap.org/{kind}/{element['id']}",
            "country": country,
            "country_basis": country_basis,
        }
        for key in OSM_COLUMNS:
            props[key.replace(":", "_")] = tags.get(key)
        props["tags"] = tags
        features.append({
            "type": "Feature",
            "id": props["id"],
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": props,
        })
    features.sort(key=lambda f: (f["properties"]["osm_type"], f["properties"]["osm_id"]))
    return features


def _wkt_point(value: str | None) -> tuple[float, float] | None:
    if not value or not value.startswith("Point("):
        return None
    lon, lat = value[6:-1].split()
    return float(lon), float(lat)


def normalize_wikidata(raw: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)
    features = []
    for row in data["results"]["bindings"]:
        value = {key: cell.get("value") for key, cell in row.items()}
        point = _wkt_point(value.get("coord"))
        if point is None:
            continue
        qid = value["item"].rsplit("/", 1)[-1]
        inception = value.get("inception")
        props = {
            "id": f"wikidata-{qid}",
            "wikidata_id": qid,
            "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
            "name": value.get("label") or qid,
            "country": value.get("country"),
            "inception": inception[:10] if inception else None,
            "website": value.get("website"),
            "operators": value.get("operators") or None,
            "owners": value.get("owners") or None,
        }
        features.append({
            "type": "Feature",
            "id": props["id"],
            "geometry": {"type": "Point",
                         "coordinates": [round(point[0], 6), round(point[1], 6)]},
            "properties": props,
        })
    features.sort(key=lambda f: int(f["properties"]["wikidata_id"][1:]))
    return features


def write_layer(directory: Path, name: str, raw: bytes, meta: dict, features: list[dict],
                license_info: dict, extra: dict | None = None) -> dict:
    """Write normalized GeoJSON and a retrieval manifest; raw bytes are hashed, not kept."""
    directory.mkdir(parents=True, exist_ok=True)
    # One feature per line keeps refreshes reviewable as ordinary line diffs.
    lines = [json.dumps(f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
             for f in features]
    body = '{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + "\n]}"
    (directory / "features.geojson").write_text(body + "\n", encoding="utf-8")
    manifest = {
        "layer": name,
        "endpoint": meta["endpoint"],
        "query": meta["query"],
        "retrieved_at": meta["retrieved_at"],
        "raw_response_bytes": len(raw),
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "feature_count": len(features),
        "features_sha256": hashlib.sha256((body + "\n").encode()).hexdigest(),
        "license": license_info,
    }
    manifest.update(extra or {})
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def load_layer(directory: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw = (directory / "features.geojson").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["features_sha256"]:
        raise ValueError(f"{directory}: features.geojson does not match its manifest")
    return manifest, json.loads(raw)["features"]
