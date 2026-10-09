"""Map projection, basemap generation and country lookup.

The site draws its own map from public-domain Natural Earth boundaries, so it needs no tile
service. Coordinates are projected with the Equal Earth projection (Šavrič, Patterson and Jenny,
2018) into a fixed plane whose width is ``PLANE_WIDTH`` units.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
_M = math.sqrt(3) / 2
_X_MAX = math.pi / (_M * A1)  # x at the equator on the antimeridian
_Y_MAX = 1.3173627591574  # y at the poles

PLANE_WIDTH = 2000.0
SCALE = PLANE_WIDTH / (2 * _X_MAX)
PLANE_HEIGHT = round(2 * _Y_MAX * SCALE, 1)


def project(lon: float, lat: float) -> tuple[float, float]:
    """Project WGS84 degrees to plane units (origin top-left, y down)."""
    lam = math.radians(lon)
    theta = math.asin(_M * math.sin(math.radians(lat)))
    t2 = theta * theta
    t6 = t2 * t2 * t2
    x = lam * math.cos(theta) / (_M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return (x + _X_MAX) * SCALE, (_Y_MAX - y) * SCALE


def _rings(geometry: dict[str, Any]) -> Iterator[list[list[float]]]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        yield from coords
    elif kind == "MultiPolygon":
        for polygon in coords:
            yield from polygon
    elif kind == "LineString":
        yield coords
    elif kind == "MultiLineString":
        yield from coords


def _path(rings: Iterable[list[list[float]]], *, closed: bool, precision: int = 1) -> str:
    """Encode rings as compact relative SVG path data in plane units."""
    factor = 10 ** precision
    parts: list[str] = []
    for ring in rings:
        points: list[tuple[int, int]] = []
        for lon, lat, *_ in ring:
            x, y = project(lon, lat)
            q = (round(x * factor), round(y * factor))
            if not points or q != points[-1]:
                points.append(q)
        if closed and len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) < (3 if closed else 2):
            continue
        cmds = [f"M{_num(points[0][0], factor)} {_num(points[0][1], factor)}"]
        px, py = points[0]
        rel: list[str] = []
        for x, y in points[1:]:
            rel.append(f"{_num(x - px, factor)} {_num(y - py, factor)}")
            px, py = x, y
        cmds.append("l" + " ".join(rel))
        if closed:
            cmds.append("z")
        parts.append("".join(cmds))
    return "".join(parts).replace(" -", "-")


def _num(value: int, factor: int) -> str:
    if value % factor == 0:
        return str(value // factor)
    text = f"{value / factor:.{len(str(factor)) - 1}f}".rstrip("0")
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


def build_basemap(countries_geojson: Path, admin1_lines_geojson: Path) -> dict[str, Any]:
    """Return projected basemap paths for the site's map component."""
    countries = json.loads(countries_geojson.read_text(encoding="utf-8"))
    lines = json.loads(admin1_lines_geojson.read_text(encoding="utf-8"))
    land = _path(
        (ring for f in countries["features"] for ring in _rings(f["geometry"])), closed=True
    )
    admin1 = _path(
        (ring for f in lines["features"] for ring in _rings(f["geometry"])), closed=False
    )
    graticule_rings = []
    for lon in range(-180, 181, 30):
        graticule_rings.append([[lon, lat / 2] for lat in range(-180, 181, 4)])
    for lat in range(-60, 61, 30):
        graticule_rings.append([[lon / 2, lat] for lon in range(-360, 361, 4)])
    graticule = _path(graticule_rings, closed=False)
    outline = _path([[[-180, lat / 2] for lat in range(-180, 181, 2)]
                     + [[180, lat / 2] for lat in range(180, -181, -2)]], closed=True)
    return {
        "projection": "Equal Earth",
        "width": PLANE_WIDTH,
        "height": PLANE_HEIGHT,
        "land": land,
        "admin1": admin1,
        "graticule": graticule,
        "outline": outline,
        "source": "Natural Earth 1:50m admin-0 countries and admin-1 lines, v5.1.2 (public domain)",
    }


def simplify_countries(countries_geojson: Path, *, digits: int = 2) -> dict[str, Any]:
    """Reduce Natural Earth countries to ISO code plus rounded polygons for point lookup."""
    data = json.loads(countries_geojson.read_text(encoding="utf-8"))
    out = []
    for feature in data["features"]:
        props = feature["properties"]
        code = props.get("ISO_A2_EH")
        if not code or code == "-99":
            continue
        polygons = []
        geometry = feature["geometry"]
        raw = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in raw:
            rings = []
            for ring in polygon:
                pts: list[list[float]] = []
                for lon, lat, *_ in ring:
                    p = [round(lon, digits), round(lat, digits)]
                    if not pts or p != pts[-1]:
                        pts.append(p)
                if len(pts) >= 4:
                    rings.append(pts)
            if rings:
                polygons.append(rings)
        out.append({"iso_a2": code, "name": props.get("NAME"), "region": props.get("REGION_UN"),
                    "subregion": props.get("SUBREGION"), "polygons": polygons})
    out.sort(key=lambda c: (c["iso_a2"], c["name"] or ""))
    return {"source": "Natural Earth 1:50m admin-0 countries v5.1.2 (public domain)",
            "countries": out}


class CountryLookup:
    """Point-in-polygon lookup over simplified Natural Earth countries."""

    def __init__(self, simplified: dict[str, Any]):
        self._entries = []
        for country in simplified["countries"]:
            for polygon in country["polygons"]:
                outer = polygon[0]
                lons = [p[0] for p in outer]
                lats = [p[1] for p in outer]
                bbox = (min(lons), min(lats), max(lons), max(lats))
                self._entries.append((bbox, polygon, country["iso_a2"]))

    @classmethod
    def from_file(cls, path: Path) -> "CountryLookup":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def country_at(self, lon: float, lat: float) -> str | None:
        for (x0, y0, x1, y1), polygon, code in self._entries:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_polygon(lon, lat, polygon):
                return code
        return None


def _in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(x: float, y: float, polygon: list[list[list[float]]]) -> bool:
    if not _in_ring(x, y, polygon[0]):
        return False
    return not any(_in_ring(x, y, hole) for hole in polygon[1:])


NATURAL_EARTH_BASE = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
)
NATURAL_EARTH_FILES = {
    "ne_50m_admin_0_countries.geojson":
        "3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb",
    "ne_50m_admin_1_states_provinces_lines.geojson":
        "72cca93c850d412628a5da4bc5ebfe21ba4d376eb34611bde6b623ee73f0fdcf",
}


def regenerate_vendor(vendor_dir: Path, download_dir: Path) -> None:
    """Download the pinned Natural Earth files, verify their hashes, rebuild vendor files."""
    download_dir.mkdir(parents=True, exist_ok=True)
    for name, digest in NATURAL_EARTH_FILES.items():
        target = download_dir / name
        if not target.exists():
            with urllib.request.urlopen(NATURAL_EARTH_BASE + name, timeout=120) as response:
                target.write_bytes(response.read())
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"{name}: SHA-256 {actual} does not match pinned {digest}")
    countries = download_dir / "ne_50m_admin_0_countries.geojson"
    lines = download_dir / "ne_50m_admin_1_states_provinces_lines.geojson"
    (vendor_dir / "basemap.json").write_text(
        json.dumps(build_basemap(countries, lines), separators=(",", ":")), encoding="utf-8")
    (vendor_dir / "countries.json").write_text(
        json.dumps(simplify_countries(countries), separators=(",", ":")), encoding="utf-8")
