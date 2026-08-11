"""Conservative parser for archived Overpass JSON semiconductor candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_LICENSE = "ODbL-1.0"
DIRECT_INDUSTRIAL_VALUES = {"integrated_circuit", "semiconductor"}
PRODUCT_VALUES = {
    "integrated_circuit",
    "integrated_circuits",
    "microchip",
    "microchips",
    "microprocessor",
    "microprocessors",
    "photomask",
    "photomasks",
    "semiconductor",
    "semiconductors",
    "semiconductor_wafer",
    "semiconductor_wafers",
    "silicon_wafer",
    "silicon_wafers",
}
PRODUCTION_MARKERS = {"factory", "industrial", "landuse", "man_made"}
NON_FACILITY_AMENITIES = {"parking", "parking_entrance", "parking_space"}


@dataclass(frozen=True, slots=True)
class OSMCandidate:
    element_type: str
    element_id: int
    source_url: str
    name: str
    tags: dict[str, str]
    geometry: dict[str, Any] | None
    latitude: float | None
    longitude: float | None
    lifecycle: str
    lifecycle_method: str
    lifecycle_confidence: float
    facility_activities: tuple[str, ...]
    published_at: str | None


def _normalized(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _values(value: object) -> set[str]:
    return {_normalized(part) for part in str(value).split(";") if str(part).strip()}


def is_explicit_semiconductor(tags: dict[str, str]) -> bool:
    normalized = {_normalized(key): _normalized(value) for key, value in tags.items()}
    if normalized.get("amenity") in NON_FACILITY_AMENITIES:
        return False
    if _values(normalized.get("industrial", "")) & DIRECT_INDUSTRIAL_VALUES:
        return True
    if "semiconductor" in _values(normalized.get("factory", "")):
        return True
    if any(
        _values(value) & DIRECT_INDUSTRIAL_VALUES
        for key, value in normalized.items()
        if key in {"construction:industrial", "proposed:industrial"}
    ):
        return True
    products = _values(normalized.get("product", "")) & PRODUCT_VALUES
    has_production_marker = (
        normalized.get("man_made") == "works"
        or normalized.get("landuse") == "industrial"
        or any(key in normalized for key in {"industrial", "factory"})
        or normalized.get("building") in {"factory", "industrial"}
    )
    return bool(products and has_production_marker)


def infer_lifecycle(tags: dict[str, str]) -> tuple[str, float, str]:
    normalized = {_normalized(key): _normalized(value) for key, value in tags.items()}
    negative_values = {"", "0", "false", "no", "none", "not_proposed", "not_construction"}
    if any(
        (key == "proposed" or key.startswith("proposed:"))
        and value not in negative_values
        for key, value in normalized.items()
    ):
        return "announced", 0.65, "osm_explicit_proposed_tag"
    if any(
        (key == "construction" or key.startswith("construction:"))
        and value not in negative_values
        for key, value in normalized.items()
    ):
        return "under_construction", 0.72, "osm_explicit_construction_tag"
    if normalized.get("building") == "construction":
        return "under_construction", 0.65, "osm_building_construction_tag"
    return "unknown", 0.35, "osm_candidate_does_not_prove_operations"


def infer_facility_activities(tags: dict[str, str]) -> tuple[str, ...]:
    values = set()
    for key in ("industrial", "factory", "product", "construction:industrial", "proposed:industrial"):
        values.update(_values(tags.get(key, "")))
    activities = set()
    if values & {"photomask", "photomasks"}:
        activities.add("photomask")
    if values & {"silicon_wafer", "silicon_wafers", "semiconductor_wafer", "semiconductor_wafers"}:
        activities.add("materials")
    if values & {
        "integrated_circuit",
        "integrated_circuits",
        "microchip",
        "microchips",
        "microprocessor",
        "microprocessors",
        "semiconductor",
        "semiconductors",
    }:
        activities.add("semiconductor_manufacturing_candidate")
    return tuple(sorted(activities))


def _points(geometry: Iterable[dict[str, Any]]) -> list[list[float]]:
    return [
        [float(point["lon"]), float(point["lat"])]
        for point in geometry
        if "lat" in point and "lon" in point
    ]


def _bounds_geometry(bounds: dict[str, Any]) -> dict[str, Any] | None:
    required = ("minlat", "minlon", "maxlat", "maxlon")
    if not all(key in bounds for key in required):
        return None
    minlat, minlon, maxlat, maxlon = (float(bounds[key]) for key in required)
    return {
        "type": "Polygon",
        "coordinates": [[
            [minlon, minlat],
            [maxlon, minlat],
            [maxlon, maxlat],
            [minlon, maxlat],
            [minlon, minlat],
        ]],
    }


def extract_geometry(element: dict[str, Any]) -> dict[str, Any] | None:
    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return {"type": "Point", "coordinates": [float(element["lon"]), float(element["lat"])]}
    points = _points(element.get("geometry") or [])
    if points:
        if len(points) >= 4 and points[0] == points[-1]:
            return {"type": "Polygon", "coordinates": [points]}
        return {"type": "LineString", "coordinates": points}
    if element.get("type") == "relation":
        rings = []
        lines = []
        for member in element.get("members") or []:
            member_points = _points(member.get("geometry") or [])
            if len(member_points) >= 4 and member_points[0] == member_points[-1] and member.get("role") != "inner":
                rings.append(member_points)
            elif member_points:
                lines.append(member_points)
        if len(rings) == 1:
            return {"type": "Polygon", "coordinates": rings}
        if rings:
            return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}
        if lines:
            return {"type": "MultiLineString", "coordinates": lines}
    return _bounds_geometry(element.get("bounds") or {})


def _all_coordinates(geometry: dict[str, Any] | None) -> list[list[float]]:
    points: list[list[float]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
            points.append([float(value[0]), float(value[1])])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    if geometry:
        visit(geometry.get("coordinates"))
    return points


def extract_center(element: dict[str, Any], geometry: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    points = _all_coordinates(geometry)
    if not points:
        return None, None
    return (
        (min(point[1] for point in points) + max(point[1] for point in points)) / 2,
        (min(point[0] for point in points) + max(point[0] for point in points)) / 2,
    )


def parse_overpass(payload: dict[str, Any]) -> list[OSMCandidate]:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ValueError("OSM input must contain an elements array")
    records = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_type = element.get("type")
        element_id = element.get("id")
        if element_type not in {"node", "way", "relation"} or not isinstance(element_id, int):
            continue
        tags = {str(key): str(value) for key, value in (element.get("tags") or {}).items()}
        if not is_explicit_semiconductor(tags):
            continue
        geometry = extract_geometry(element)
        latitude, longitude = extract_center(element, geometry)
        lifecycle, confidence, method = infer_lifecycle(tags)
        records.append(
            OSMCandidate(
                element_type=element_type,
                element_id=element_id,
                source_url=f"https://www.openstreetmap.org/{element_type}/{element_id}",
                name=tags.get("name") or f"OpenStreetMap {element_type} {element_id}",
                tags=tags,
                geometry=geometry,
                latitude=latitude,
                longitude=longitude,
                lifecycle=lifecycle,
                lifecycle_method=method,
                lifecycle_confidence=confidence,
                facility_activities=infer_facility_activities(tags),
                published_at=str(element.get("timestamp")) if element.get("timestamp") else None,
            )
        )
    return records


def parse_overpass_file(path: str | Path) -> list[OSMCandidate]:
    return parse_overpass(json.loads(Path(path).read_text(encoding="utf-8")))
