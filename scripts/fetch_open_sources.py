#!/usr/bin/env python3
"""Fetch the bounded public-source inputs used by the first atlas adapters."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


NIST_AWARDS_URL = "https://www.nist.gov/chips/chips-america-awards"
OVERPASS_ENDPOINTS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
OVERPASS_QUERY = """[out:json][timeout:180];
(
  nwr["industrial"="integrated_circuit"];
  nwr["industrial"="semiconductor"];
  nwr["product"="integrated_circuit"];
  nwr["product"="integrated_circuits"];
  nwr["product"="semiconductor"];
  nwr["product"="semiconductors"];
  nwr["product"="microchip"];
  nwr["product"="microchips"];
  nwr["product"="microprocessor"];
  nwr["product"="microprocessors"];
  nwr["product"="silicon_wafer"];
  nwr["product"="silicon_wafers"];
  nwr["product"="semiconductor_wafer"];
  nwr["product"="semiconductor_wafers"];
  nwr["product"="photomask"];
  nwr["product"="photomasks"];
  nwr["factory"="semiconductor"];
  nwr["construction:industrial"="integrated_circuit"];
  nwr["construction:industrial"="semiconductor"];
  nwr["proposed:industrial"="integrated_circuit"];
  nwr["proposed:industrial"="semiconductor"];
);
out body geom;
"""
USER_AGENT = "semiconductor-atlas/0.1 (+https://www.nist.gov/chips/)"


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--retrieved-at must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _request(url: str, *, data: bytes | None = None, timeout: int = 180) -> tuple[bytes, str]:
    request = Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json" if data is not None else "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded" if data is not None else "text/plain",
        },
        method="POST" if data is not None else "GET",
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def _write(path: Path, raw: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing source input: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def _metadata(path: Path, raw: bytes, *, url: str, content_type: str) -> dict[str, object]:
    return {
        "path": path.name,
        "url": url,
        "content_type": content_type,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _validate_nist_index(raw: bytes, page: int) -> None:
    if b"CHIPS for America Awards" not in raw or b"views-field-field-chipfund-chips-org" not in raw:
        raise ValueError(f"NIST page {page} did not contain the expected awards view")


def _nist_page_count(first_page: bytes) -> int:
    page_numbers = [int(value) for value in re.findall(rb'href="\?page=(\d+)"', first_page)]
    count = max(page_numbers, default=0) + 1
    if count > 100:
        raise ValueError(f"refusing implausible NIST pagination count: {count}")
    return count


class _NistAwardLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.card_depth = 0
        self.organization_depth: int | None = None
        self.organization_parts: list[str] = []
        self.detail_url: str | None = None
        self.cpo_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            if self.card_depth:
                self.card_depth += 1
            elif "margin-top-3" in classes:
                self.card_depth = 1
                self.organization_depth = None
                self.organization_parts = []
                self.detail_url = None
            if self.card_depth and "nist-field__item" in classes:
                self.organization_depth = self.card_depth
        if self.card_depth and tag == "a" and self.detail_url is None:
            href = attributes.get("href") or ""
            if href.startswith("/chips/"):
                self.detail_url = urljoin(NIST_AWARDS_URL, href)

    def handle_data(self, data: str) -> None:
        if self.organization_depth is not None:
            self.organization_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or not self.card_depth:
            return
        if self.organization_depth == self.card_depth:
            self.organization_depth = None
        self.card_depth -= 1
        if self.card_depth == 0:
            organization = html.unescape(" ".join(self.organization_parts)).strip()
            if organization == "CHIPS Program Office" and self.detail_url:
                self.cpo_urls.add(self.detail_url)


def _cpo_detail_urls(pages: list[bytes]) -> list[str]:
    parser = _NistAwardLinkParser()
    for raw in pages:
        parser.feed(raw.decode("utf-8"))
    return sorted(parser.cpo_urls)


def fetch_nist(
    output_dir: Path, *, overwrite: bool, include_details: bool
) -> list[dict[str, object]]:
    inputs: list[dict[str, object]] = []
    first_raw, first_content_type = _request(NIST_AWARDS_URL)
    _validate_nist_index(first_raw, 0)
    page_payloads = [first_raw]
    page_count = _nist_page_count(first_raw)
    for page in range(page_count):
        url = NIST_AWARDS_URL if page == 0 else f"{NIST_AWARDS_URL}?page={page}"
        if page == 0:
            raw, content_type = first_raw, first_content_type
        else:
            raw, content_type = _request(url)
            _validate_nist_index(raw, page)
            page_payloads.append(raw)
        path = output_dir / f"nist-chips-awards-page-{page}.html"
        _write(path, raw, overwrite=overwrite)
        item = _metadata(path, raw, url=url, content_type=content_type)
        item["record_type"] = "award_index_page"
        inputs.append(item)
    if include_details:
        for url in _cpo_detail_urls(page_payloads):
            raw, content_type = _request(url)
            if b"CHIPS" not in raw or b"nist.gov" not in raw:
                raise ValueError(f"NIST detail page did not contain expected content: {url}")
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            path = output_dir / f"nist-chips-detail-{slug}.html"
            _write(path, raw, overwrite=overwrite)
            item = _metadata(path, raw, url=url, content_type=content_type)
            item["record_type"] = "award_detail_page"
            inputs.append(item)
    return inputs


def fetch_osm(output_dir: Path, *, overwrite: bool) -> list[dict[str, object]]:
    encoded = urlencode({"data": OVERPASS_QUERY}).encode("utf-8")
    failures = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            raw, content_type = _request(endpoint, data=encoded)
            payload = json.loads(raw)
            elements = payload.get("elements")
            if not isinstance(elements, list):
                raise ValueError("Overpass response has no elements array")
            if payload.get("remark"):
                raise ValueError(f"Overpass returned a remark: {payload['remark']}")
            path = output_dir / "openstreetmap-semiconductor-explicit.json"
            _write(path, raw, overwrite=overwrite)
            item = _metadata(path, raw, url=endpoint, content_type=content_type)
            item["record_type"] = "osm_overpass_snapshot"
            item["query"] = OVERPASS_QUERY
            item["element_count"] = len(elements)
            return [item]
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"{endpoint}: {exc}")
    raise RuntimeError("all Overpass endpoints failed: " + "; ".join(failures))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a bounded NIST CHIPS awards snapshot and explicit OSM semiconductor works."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--retrieved-at", help="fixed timezone-aware timestamp for the manifest")
    parser.add_argument(
        "--include-nist-details",
        action="store_true",
        help="also fetch each CHIPS Program Office project detail linked from the index",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="deprecated; immutable snapshots must use a new output directory",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stage: Path | None = None
    try:
        if args.retrieved_at:
            raise ValueError(
                "--retrieved-at is not accepted for live acquisition; retrieval time is "
                "measured after every response is archived"
            )
        if args.overwrite:
            raise ValueError(
                "--overwrite is disabled for immutable snapshots; choose a new output directory"
            )
        output_dir = args.output_dir.resolve()
        if output_dir.exists():
            raise FileExistsError(
                f"refusing to overwrite immutable source snapshot: {output_dir}"
            )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{output_dir.name}.stage-",
                dir=output_dir.parent,
            )
        )
        nist_inputs = fetch_nist(
            stage,
            overwrite=False,
            include_details=args.include_nist_details,
        )
        inputs = [*nist_inputs, *fetch_osm(stage, overwrite=False)]
        retrieved_at = _timestamp(None)
        manifest = {
            "format": "semiconductor-atlas-source-inputs-v1",
            "retrieved_at": retrieved_at,
            "retrieval_timestamp_basis": (
                "batch_completion_after_all_source_responses_were_archived"
            ),
            "source_scopes": {
                "nist_chips_awards": {
                    "complete": args.include_nist_details,
                    "index_page_count": sum(
                        item["record_type"] == "award_index_page" for item in nist_inputs
                    ),
                    "detail_page_count": sum(
                        item["record_type"] == "award_detail_page" for item in nist_inputs
                    ),
                    "detail_coverage": (
                        "all_chips_program_office_links_in_archived_index_pages"
                        if args.include_nist_details
                        else "not_acquired"
                    ),
                }
            },
            "inputs": inputs,
        }
        manifest_path = stage / "manifest.json"
        _write(
            manifest_path,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            overwrite=False,
        )
        stage.rename(output_dir)
        stage = None
        print(
            json.dumps(
                {"inputs": len(inputs), "manifest": str((output_dir / "manifest.json").resolve())}
            )
        )
        return 0
    except (FileExistsError, HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    raise SystemExit(main())
