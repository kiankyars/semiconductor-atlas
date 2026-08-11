"""Parse archived NIST CHIPS for America award index pages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin


NIST_AWARDS_URL = "https://www.nist.gov/chips/chips-america-awards"


@dataclass(frozen=True, slots=True)
class AwardAmount:
    amount_usd: int
    qualifier: str
    allocation_scope: str
    source_text: str


@dataclass(frozen=True, slots=True)
class AwardRecord:
    source_url: str
    title: str
    recipient: str
    locality: str
    region: str
    chips_organization: str
    description: str
    amount: AwardAmount | None
    facility_activities: tuple[str, ...]
    wafer_sizes_mm: tuple[int, ...]
    process_nodes_nm: tuple[float, ...]
    cleanroom_area_ft2: int | None
    technologies: tuple[str, ...]


def _clean(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def _recipient(title: str) -> str:
    return re.sub(r"\s+\([^()]*(?:state|territory|district|arizona|california|colorado|florida|georgia|idaho|indiana|iowa|kansas|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|hampshire|jersey|mexico|york|carolina|dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|tennessee|texas|utah|vermont|virginia|washington|wisconsin|wyoming)\)$", "", title, flags=re.IGNORECASE)


def parse_award_amount(text: str) -> AwardAmount | None:
    match = re.search(
        r"Award Amount:\s*((?:up to\s+)?)\$([\d,.]+)\s*(million|billion)\s+"
        r"((?:[^()]*?\bfunding\b)(?:\s*\([^)]*\))?)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    multiplier = Decimal("1000000000") if match.group(3).lower() == "billion" else Decimal("1000000")
    amount = int(Decimal(match.group(2).replace(",", "")) * multiplier)
    source_text = _clean(match.group(0))
    lowered = source_text.lower()
    if "proposed" in lowered:
        qualifier = "proposed_up_to" if match.group(1).strip() else "proposed"
    elif match.group(1).strip():
        qualifier = "up_to"
    else:
        qualifier = "reported_amount"
    allocation_scope = "multi_site_shared" if "split across" in lowered else "site"
    return AwardAmount(amount, qualifier, allocation_scope, source_text)


def classify_facility_activities(description: str) -> tuple[str, ...]:
    text = description.lower()
    activities = set()
    if re.search(
        r"\b(fab|foundry|fabrication facilit(?:y|ies)|wafer fabrication|chip manufacturing)\b",
        text,
    ):
        activities.add("wafer_fabrication")
    if "advanced packaging" in text or "packaging facility" in text:
        activities.add("advanced_packaging")
    if re.search(r"\b(testing facility|test and packaging|assembly and test|packaging and testing)\b", text):
        activities.add("test")
    if "assembly" in text:
        activities.add("assembly")
    if "substrate" in text:
        activities.add("substrate")
    if "photomask" in text or "mask blank" in text:
        activities.add("photomask")
    if re.search(r"\b(polysilicon|semiconductor-grade material|semiconductor material|chemical|specialty gas|quartz|fused silica|silicon wafer)\b", text):
        activities.add("materials")
    if re.search(r"\b(semiconductor manufacturing equipment|vacuum pump|production equipment)\b", text):
        activities.add("equipment_manufacturing")
    if re.search(r"\b(photonics?|inp pic|photonic integrated circuit)\b", text):
        activities.add("photonics")
    if re.search(r"\b(silicon carbide|sic|gallium nitride|gan)\b", text):
        activities.add("power_semiconductor")
    return tuple(sorted(activities))


def extract_capabilities(description: str) -> tuple[tuple[int, ...], tuple[float, ...], int | None, tuple[str, ...]]:
    wafer_sizes = {
        int(value)
        for value in re.findall(r"\b(100|150|200|300|450)\s*[- ]?mm\b", description, flags=re.IGNORECASE)
    }
    nodes = {
        float(value)
        for value in re.findall(r"\b(\d+(?:\.\d+)?)\s*nm\b", description, flags=re.IGNORECASE)
    }
    cleanroom_match = re.search(
        r"\b([\d,]+)\s+square feet of cleanroom", description, flags=re.IGNORECASE
    )
    cleanroom_area = int(cleanroom_match.group(1).replace(",", "")) if cleanroom_match else None
    technology_patterns = {
        "advanced_packaging": r"\badvanced packaging\b",
        "analog": r"\banalog\b",
        "dram": r"\bdram\b",
        "fd_soi": r"\bfd[- ]soi\b",
        "gallium_nitride": r"\b(?:gallium nitride|gan)\b",
        "indium_phosphide": r"\b(?:indium phosphide|inp)\b",
        "mems": r"\bmems\b",
        "nand": r"\bnand\b",
        "silicon_carbide": r"\b(?:silicon carbide|sic)\b",
        "silicon_photonics": r"\bsilicon photonics\b",
    }
    technologies = tuple(
        sorted(name for name, pattern in technology_patterns.items() if re.search(pattern, description, re.IGNORECASE))
    )
    return tuple(sorted(wafer_sizes)), tuple(sorted(nodes)), cleanroom_area, technologies


class _AwardsParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.records: list[AwardRecord] = []
        self.card_depth = 0
        self.current: dict[str, object] | None = None
        self.capture_field: str | None = None
        self.capture_end_tag: str | None = None
        self.capture_depth = 0
        self.capture_parts: list[str] = []

    def _start_capture(self, field: str, tag: str) -> None:
        self.capture_field = field
        self.capture_end_tag = tag
        self.capture_depth = 1
        self.capture_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        capture_was_active = self.capture_field is not None
        if tag == "div":
            if self.current is not None:
                self.card_depth += 1
            elif "margin-top-3" in classes:
                self.current = {"all_text": []}
                self.card_depth = 1
        if self.current is None:
            return
        if capture_was_active and tag == self.capture_end_tag:
            self.capture_depth += 1
            return
        if self.capture_field is not None:
            return
        if tag == "a" and not self.current.get("source_url"):
            href = attributes.get("href") or ""
            if href.startswith("/chips/"):
                self.current["source_url"] = urljoin(self.page_url, href)
                self._start_capture("title", tag)
        elif tag == "span" and "views-field-field-chipfund-location-locality" in classes:
            self._start_capture("locality", tag)
        elif tag == "span" and "views-field-field-chipfund-location-administrative-area" in classes:
            self._start_capture("region", tag)
        elif tag == "div" and "nist-field__item" in classes:
            self._start_capture("chips_organization", tag)
        elif tag == "div" and "views-field-body" in classes:
            self._start_capture("description", tag)

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        all_text = self.current["all_text"]
        assert isinstance(all_text, list)
        all_text.append(data)
        if self.capture_field is not None:
            self.capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture_field is not None and tag == self.capture_end_tag:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                self.current[self.capture_field] = _clean(" ".join(self.capture_parts))
                self.capture_field = None
                self.capture_end_tag = None
                self.capture_parts = []
        if tag == "div":
            self.card_depth -= 1
            if self.card_depth == 0:
                self._finish_card()

    def _finish_card(self) -> None:
        assert self.current is not None
        organization = str(self.current.get("chips_organization") or "")
        if organization == "CHIPS Program Office":
            title = str(self.current.get("title") or "")
            description = str(self.current.get("description") or "")
            source_url = str(self.current.get("source_url") or self.page_url)
            all_text = _clean(" ".join(str(part) for part in self.current["all_text"]))
            wafer_sizes, process_nodes, cleanroom_area, technologies = extract_capabilities(description)
            self.records.append(
                AwardRecord(
                    source_url=source_url,
                    title=title,
                    recipient=_recipient(title),
                    locality=str(self.current.get("locality") or "").lstrip("–- ").rstrip(", "),
                    region=str(self.current.get("region") or ""),
                    chips_organization=organization,
                    description=description,
                    amount=parse_award_amount(all_text),
                    facility_activities=classify_facility_activities(description),
                    wafer_sizes_mm=wafer_sizes,
                    process_nodes_nm=process_nodes,
                    cleanroom_area_ft2=cleanroom_area,
                    technologies=technologies,
                )
            )
        self.current = None
        self.capture_field = None
        self.capture_end_tag = None
        self.capture_depth = 0
        self.capture_parts = []


def parse_awards_html(raw: str | bytes, *, page_url: str = NIST_AWARDS_URL) -> list[AwardRecord]:
    parser = _AwardsParser(page_url)
    parser.feed(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    parser.close()
    return parser.records


def parse_award_files(paths: Iterable[str | Path]) -> list[AwardRecord]:
    records = []
    seen_urls = set()
    for page_number, path in enumerate(paths):
        input_path = Path(path)
        page_url = NIST_AWARDS_URL if page_number == 0 else f"{NIST_AWARDS_URL}?page={page_number}"
        for record in parse_awards_html(input_path.read_bytes(), page_url=page_url):
            if record.source_url not in seen_urls:
                records.append(record)
                seen_urls.add(record.source_url)
    return records
