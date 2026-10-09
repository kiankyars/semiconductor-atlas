"""Command line entry point: ``python -m dcatlas <command>`` (run from ``datacenters/``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import json_schema, validate_collection, validate_project

ROOT = Path(__file__).resolve().parent.parent


def _validate(paths: list[str]) -> int:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    records = []
    failures = 0
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: cannot read JSON: {exc}")
            failures += 1
            continue
        if isinstance(record, dict) and record.get("id") != path.stem:
            print(f"{path}: file name must equal the record id {record.get('id')!r}")
            failures += 1
        for message in validate_project(record):
            print(f"{path}: {message}")
            failures += 1
        records.append(record)
    for message in validate_collection(records):
        if "duplicate project id" in message or "related id" in message:
            print(message)
            failures += 1
    print(f"checked {len(files)} file(s): {'OK' if not failures else f'{failures} problem(s)'}")
    return 1 if failures else 0


def _schema(check: bool) -> int:
    path = ROOT / "schema" / "project.schema.json"
    text = json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"
    if check:
        if path.read_text(encoding="utf-8") != text:
            print(f"{path} is stale; run: python -m dcatlas schema")
            return 1
        print("schema is current")
        return 0
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def _fetch_layer(name: str) -> int:
    from . import layers
    from .geo import CountryLookup

    if name == "osm":
        raw, meta = layers.fetch_osm()
        lookup = CountryLookup.from_file(ROOT / "vendor" / "countries.json")
        features = layers.normalize_osm(raw, lookup)
        base = json.loads(raw).get("osm3s", {}).get("timestamp_osm_base")
        manifest = layers.write_layer(ROOT / "layers" / "osm", "osm", raw, meta, features,
                                      layers.OSM_LICENSE, {"osm_base_timestamp": base})
    else:
        raw, meta = layers.fetch_wikidata()
        features = layers.normalize_wikidata(raw)
        manifest = layers.write_layer(ROOT / "layers" / "wikidata", "wikidata", raw, meta,
                                      features, layers.WIKIDATA_LICENSE)
    print(f"{name}: {manifest['feature_count']} features from {manifest['endpoint']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dcatlas", description="Open Data Center Atlas tools")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate", help="validate project JSON files or directories")
    check.add_argument("paths", nargs="*", default=[str(ROOT / "projects")])
    build = sub.add_parser("build", help="build the static site and data release")
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--base-url", default=None)
    build.add_argument("--commit", default=None)
    schema = sub.add_parser("schema", help="regenerate schema/project.schema.json")
    schema.add_argument("--check", action="store_true", help="fail if the file is stale")
    fetch = sub.add_parser("fetch-layer", help="refresh an uncurated open-data layer")
    fetch.add_argument("layer", choices=("osm", "wikidata"))
    vendor = sub.add_parser("vendor", help="rebuild vendor/ from pinned Natural Earth files")
    vendor.add_argument("--download-dir", type=Path, required=True)
    archive = sub.add_parser("archive-lookup",
                             help="record existing Internet Archive copies of cited sources")
    archive.add_argument("paths", nargs="*", default=[str(ROOT / "projects")])
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.paths)
    if args.command == "schema":
        return _schema(args.check)
    if args.command == "fetch-layer":
        return _fetch_layer(args.layer)
    if args.command == "vendor":
        from .geo import regenerate_vendor

        regenerate_vendor(ROOT / "vendor", args.download_dir)
        print(f"rebuilt {ROOT / 'vendor'}")
        return 0
    if args.command == "archive-lookup":
        from .archive import fill_archived_urls

        for path in args.paths:
            print(path, fill_archived_urls(Path(path)))
        return 0
    from .site import DEFAULT_BASE_URL, build as build_site

    summary = build_site(root=ROOT, out_dir=args.output,
                         base_url=args.base_url or DEFAULT_BASE_URL, commit=args.commit)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
