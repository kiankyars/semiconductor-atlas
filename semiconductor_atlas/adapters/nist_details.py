"""Parse archived NIST CHIPS Program Office project detail pages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin


@dataclass(frozen=True, slots=True)
class TextDisclosure:
    label: str
    source_text: str
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class FundingDisclosure:
    label: str
    source_text: str
    scope: str | None
    qualifier: str


@dataclass(frozen=True, slots=True)
class NISTProjectDetail:
    canonical_url: str | None
    title: str | None
    recipients: tuple[str, ...]
    locations: tuple[str, ...]
    award_stage: TextDisclosure | None
    direct_funding: tuple[FundingDisclosure, ...]
    expected_capex: tuple[TextDisclosure, ...]
    jobs: tuple[TextDisclosure, ...]
    project_description: TextDisclosure | None
    published_at: str | None
    updated_at: str | None
    capabilities: tuple[TextDisclosure, ...]


@dataclass(frozen=True, slots=True)
class _Cell:
    tag: str
    colspan: int
    text: str


@dataclass(frozen=True, slots=True)
class _Table:
    rows: tuple[tuple[_Cell, ...], ...]


def _clean(parts: list[str] | tuple[str, ...] | str) -> str:
    raw = parts if isinstance(parts, str) else "".join(parts)
    raw = html.unescape(raw).replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in raw.splitlines())
    return "\n".join(line for line in lines if line)


def _normalized_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().rstrip(":"), flags=re.UNICODE).casefold()


def _funding_qualifier(label: str, source_text: str) -> str:
    wording = f"{label} {source_text}".casefold()
    proposed = re.search(r"\bproposed\b", wording) is not None
    final = re.search(r"\bfinal\b", wording) is not None
    up_to = re.search(r"\bup\s+to\b", wording) is not None
    if proposed:
        return "proposed_up_to" if up_to else "proposed"
    if final:
        return "final_up_to" if up_to else "final"
    return "up_to" if up_to else "unqualified"


def _split_explicit_lines(source_text: str) -> tuple[str, ...]:
    return tuple(line for line in source_text.splitlines() if line)


def _append_break(parts: list[str]) -> None:
    if parts and not parts[-1].endswith("\n"):
        parts.append("\n")


class _DetailParser(HTMLParser):
    def __init__(self, page_url: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.canonical_url: str | None = None
        self.title: str | None = None
        self.published_at: str | None = None
        self.updated_at: str | None = None

        self._title_parts: list[str] | None = None
        self._h2_parts: list[str] | None = None
        self._summary_label: str | None = None
        self._summary_parts: list[str] | None = None
        self.project_description: TextDisclosure | None = None

        self._field_kind: str | None = None
        self._field_end_tag: str | None = None
        self._field_depth = 0
        self._field_parts: list[str] = []
        self._pending_field_label: str | None = None
        self.field_pairs: list[tuple[str, str]] = []

        self._table_depth = 0
        self._table_rows: list[tuple[_Cell, ...]] | None = None
        self._row: list[_Cell] | None = None
        self._cell_tag: str | None = None
        self._cell_colspan = 1
        self._cell_parts: list[str] = []
        self.tables: list[_Table] = []

    def _finish_summary(self) -> None:
        if self._summary_parts is None or self._summary_label is None:
            return
        source_text = _clean(self._summary_parts)
        if source_text and self.project_description is None:
            self.project_description = TextDisclosure(self._summary_label, source_text)
        self._summary_label = None
        self._summary_parts = None

    def _start_field_capture(self, kind: str, tag: str) -> None:
        self._field_kind = kind
        self._field_end_tag = tag
        self._field_depth = 1
        self._field_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "link" and "canonical" in (attributes.get("rel") or "").casefold().split():
            href = (attributes.get("href") or "").strip()
            if href:
                self.canonical_url = urljoin(self.page_url, href) if self.page_url else href
        elif tag == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").casefold()
            content = (attributes.get("content") or "").strip()
            if name == "article:published_time" and content:
                self.published_at = content
            elif name == "article:modified_time" and content:
                self.updated_at = content

        if tag == "h1" and self._title_parts is None:
            self._title_parts = []

        if tag == "h2":
            self._finish_summary()
            self._h2_parts = []
        elif self._summary_parts is not None and (tag == "table" or re.fullmatch(r"h[1-6]", tag)):
            self._finish_summary()

        field_was_active = self._field_kind is not None
        if field_was_active and tag == self._field_end_tag:
            self._field_depth += 1
        elif not field_was_active and tag == "div":
            if "nist-field__label" in classes:
                self._start_field_capture("label", tag)
            elif "nist-field__item" in classes:
                self._start_field_capture("item", tag)

        if tag == "table":
            if self._table_depth == 0:
                self._table_rows = []
            self._table_depth += 1
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and self._row is not None and tag in {"th", "td"}:
            self._cell_tag = tag
            try:
                self._cell_colspan = int(attributes.get("colspan") or "1")
            except ValueError:
                self._cell_colspan = 1
            self._cell_parts = []

        if tag in {"br", "li", "p"}:
            if self._summary_parts is not None:
                _append_break(self._summary_parts)
            if self._field_kind is not None:
                _append_break(self._field_parts)
            if self._cell_tag is not None:
                _append_break(self._cell_parts)

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)
        if self._h2_parts is not None:
            self._h2_parts.append(data)
        elif self._summary_parts is not None:
            self._summary_parts.append(data)
        if self._field_kind is not None:
            self._field_parts.append(data)
        if self._cell_tag is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"li", "p"}:
            if self._summary_parts is not None:
                _append_break(self._summary_parts)
            if self._field_kind is not None:
                _append_break(self._field_parts)
            if self._cell_tag is not None:
                _append_break(self._cell_parts)

        if tag == "h1" and self._title_parts is not None:
            title = _clean(self._title_parts)
            self.title = title or None
            self._title_parts = None

        if tag == "h2" and self._h2_parts is not None:
            label = _clean(self._h2_parts)
            self._h2_parts = None
            if _normalized_label(label) in {"project summary", "project description"}:
                self._summary_label = label
                self._summary_parts = []

        if self._field_kind is not None and tag == self._field_end_tag:
            self._field_depth -= 1
            if self._field_depth == 0:
                value = _clean(self._field_parts)
                if self._field_kind == "label":
                    self._pending_field_label = value or None
                elif self._pending_field_label and value:
                    self.field_pairs.append((self._pending_field_label, value))
                    self._pending_field_label = None
                self._field_kind = None
                self._field_end_tag = None
                self._field_parts = []

        if self._table_depth == 1 and tag in {"th", "td"} and self._cell_tag == tag:
            assert self._row is not None
            self._row.append(_Cell(tag, self._cell_colspan, _clean(self._cell_parts)))
            self._cell_tag = None
            self._cell_colspan = 1
            self._cell_parts = []
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                assert self._table_rows is not None
                self._table_rows.append(tuple(self._row))
            self._row = None

        if tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                assert self._table_rows is not None
                self.tables.append(_Table(tuple(self._table_rows)))
                self._table_rows = None

    def close(self) -> None:
        super().close()
        self._finish_summary()


def _row_field(row: tuple[_Cell, ...]) -> tuple[str, str] | None:
    for index, cell in enumerate(row):
        if cell.tag != "th" or not cell.text:
            continue
        values = tuple(candidate.text for candidate in row[index + 1 :] if candidate.text)
        if values:
            return cell.text, "\n".join(values)
    populated = tuple(cell.text for cell in row if cell.text)
    if len(populated) >= 2:
        return populated[-2], populated[-1]
    return None


def _row_scope(row: tuple[_Cell, ...]) -> str | None:
    spanning_headings = tuple(
        cell.text for cell in row if cell.tag == "th" and cell.colspan > 1 and cell.text
    )
    if spanning_headings:
        return spanning_headings[-1]
    populated = tuple(cell for cell in row if cell.text)
    if len(populated) == 1 and populated[0].tag == "th":
        return populated[0].text
    return None


def _is_capability_label(label: str) -> bool:
    if label == "project type":
        return True
    return any(term in label for term in ("process", "wafer", "technology", "capabilit", "timeline"))


def parse_detail_html(
    raw: str | bytes,
    *,
    page_url: str | None = None,
) -> NISTProjectDetail:
    """Return only source-visible disclosures; no amounts or sites are inferred."""

    parser = _DetailParser(page_url)
    parser.feed(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    parser.close()

    recipients: list[str] = []
    locations: list[str] = []
    direct_funding: list[FundingDisclosure] = []
    expected_capex: list[TextDisclosure] = []
    jobs: list[TextDisclosure] = []
    capabilities: list[TextDisclosure] = []
    award_stage: TextDisclosure | None = None
    project_description = parser.project_description

    rows_with_scopes: list[tuple[str | None, str, str]] = []
    for table in parser.tables:
        scope: str | None = None
        for row in table.rows:
            row_scope = _row_scope(row)
            if row_scope is not None:
                scope = row_scope
                continue
            field = _row_field(row)
            if field is not None:
                rows_with_scopes.append((scope, field[0], field[1]))

    for scope, label, source_text in rows_with_scopes:
        normalized = _normalized_label(label)
        if normalized in {"recipient", "recipients", "recipient(s)"}:
            for recipient in _split_explicit_lines(source_text):
                if recipient not in recipients:
                    recipients.append(recipient)
        elif normalized in {"location", "locations", "location(s)"}:
            for location in _split_explicit_lines(source_text):
                if location not in locations:
                    locations.append(location)
        elif normalized.startswith("direct funding"):
            direct_funding.append(
                FundingDisclosure(
                    label,
                    source_text,
                    scope,
                    _funding_qualifier(label, source_text),
                )
            )
        elif normalized.startswith("expected capital expenditure"):
            expected_capex.append(TextDisclosure(label, source_text, scope))
        elif "job" in normalized:
            jobs.append(TextDisclosure(label, source_text, scope))
        elif _is_capability_label(normalized):
            capabilities.append(TextDisclosure(label, source_text, scope))
        elif normalized in {"application stage", "award stage", "award status"} and award_stage is None:
            award_stage = TextDisclosure(label, source_text, scope)
        elif normalized in {"project summary", "project description"} and project_description is None:
            project_description = TextDisclosure(label, source_text, scope)

    for label, source_text in parser.field_pairs:
        normalized = _normalized_label(label)
        if normalized in {"application stage", "award stage", "award status"} and award_stage is None:
            award_stage = TextDisclosure(label, source_text)

    return NISTProjectDetail(
        canonical_url=parser.canonical_url,
        title=parser.title,
        recipients=tuple(recipients),
        locations=tuple(locations),
        award_stage=award_stage,
        direct_funding=tuple(direct_funding),
        expected_capex=tuple(expected_capex),
        jobs=tuple(jobs),
        project_description=project_description,
        published_at=parser.published_at,
        updated_at=parser.updated_at,
        capabilities=tuple(capabilities),
    )


def parse_detail_file(path: str | Path, *, page_url: str | None = None) -> NISTProjectDetail:
    return parse_detail_html(Path(path).read_bytes(), page_url=page_url)
