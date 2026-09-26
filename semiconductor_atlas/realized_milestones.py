"""Reference-only, source-reported realized events; not canonical claims or scores.

The v1 semantic rule is deliberately limited to one issuer-table layout and one
source-native facility. Hashes bind supplied local evidence, not its authenticity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from . import milestone_benchmark as _benchmark


REVIEW_FORMAT = "semiconductor-atlas-realized-milestone-review-v1"
ARTIFACT_FORMAT = "semiconductor-atlas-realized-milestone-observation-v1"
RULE = "tsmc_operating_fab_commercial_production_year_v1"
EVENT_TYPES = {"commercial_production_commencement", "production_start", "high_volume_production"}
SOURCE_ID = "tsmc-2025-20f"
SOURCE_URL = "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm"
PROVENANCE_REFERENCE = "baselines/ai_critical_manufacturing_v1.json"
SUBJECT = {"source_native_id": SOURCE_ID + ":fab:21", "kind": "facility", "label": "Fab 21",
           "scope": "issuer_operating_fab_table", "canonical_entity_id": None, "location": None}
RIGHTS = {"use": "local_review_only", "redistribution_authorized": False,
          "source_bodies_embedded": False, "provenance_bodies_embedded": False, "excerpts_embedded": False}
BOUNDARIES = {"canonical_identity_assigned": False, "physical_operation_independently_verified": False,
              "reviewer_independence_certified": False, "forecast_vintage": False,
              "forecast_score": False, "capacity_inferred": False,
              "external_timestamp_attestation": False, "browser_rendered_visibility_verified": False}
_SOURCE_FIELDS = {"source_id", "url", "content_sha256", "bytes", "published_at",
                  "published_at_precision", "retrieved_at"}
_HEADERS = ["Fab(1)", "", "Year of commencement of commercial production", "", "Wafer size", "",
            "The most advanced technology for volume production(2)"]
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = set("div p table tr td th br section article h1 h2 h3 li".split())
_INTRO = re.compile(r"The following table lists our wafer fabs and those of our subsidiaries in operation as of "
                    r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})\s*, together with the year of commencement of commercial "
                    r"production, wafer size and the most advanced technology for volume production:")
canonical_bytes = _benchmark.canonical_bytes
write_new = _benchmark.write_new
_hash, _keys, _clock, _day, _digest = (_benchmark._hash, _benchmark._keys, _benchmark._clock,
                                    _benchmark._day, _benchmark._digest)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not raw or len(raw) > 2_000_000:
        raise ValueError("expected bounded JSON bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _review(value) -> dict:
    value = _json(value if isinstance(value, bytes) else canonical_bytes(value))
    _keys(value, {"format", "validation_rule", "subject", "event", "source", "provenance", "evidence", "review"}, "review")
    if value["format"] != REVIEW_FORMAT or value["validation_rule"] != RULE:
        raise ValueError("unsupported review format or semantic validation rule")
    if value["subject"] != SUBJECT:
        raise ValueError("unsupported source-native facility identity or scope")
    event = _keys(value["event"], {"event_type", "low", "base", "high", "precision", "literal"}, "event")
    if not isinstance(event["event_type"], str) or event["event_type"] not in EVENT_TYPES:
        raise ValueError("unknown event type")
    if event["event_type"] != "commercial_production_commencement" or event["precision"] != "year":
        raise ValueError("v1 table rule supports only commercial-production commencement at year precision")
    if not isinstance(event["literal"], str) or not re.fullmatch(r"[12]\d{3}", event["literal"]):
        raise ValueError("year must be a literal four-digit source year, not a vague date")
    year = event["literal"]
    if event["base"] is not None or event["low"] != year + "-01-01" or event["high"] != year + "-12-31":
        raise ValueError("year bounds must cover the entire calendar year with null base")
    _day(event["low"])
    _day(event["high"])
    source = _keys(value["source"], _SOURCE_FIELDS, "source")
    if source["source_id"] != SOURCE_ID or source["url"] != SOURCE_URL or source["published_at_precision"] != "day":
        raise ValueError("unsupported exact source identity or publication precision")
    _digest(source["content_sha256"])
    if type(source["bytes"]) is not int or not 0 < source["bytes"] <= 20_000_000:
        raise ValueError("body size outside supported bounds")
    if _day(source["published_at"]) > _clock(source["retrieved_at"]).date():
        raise ValueError("source publication follows retrieval")
    if _day(event["high"]) > _day(source["published_at"]):
        raise ValueError("realized year must have elapsed by publication")
    provenance = _keys(value["provenance"], {"sha256", "reference"}, "provenance")
    _digest(provenance["sha256"])
    if provenance["reference"] != PROVENANCE_REFERENCE:
        raise ValueError("unsupported provenance reference")
    _keys(value["evidence"], {"intro", "table", "header", "row"}, "evidence")
    for span in value["evidence"].values():
        _keys(span, {"start", "end", "sha256"}, "span")
        _digest(span["sha256"])
        if type(span["start"]) is not int or type(span["end"]) is not int or not 0 <= span["start"] < span["end"] <= source["bytes"]:
            raise ValueError("invalid UTF-8 byte span bounds")
    review = _keys(value["review"], {"reviewed_by", "reviewed_at", "prior_exposure", "rationale"}, "review declaration")
    for name, minimum in (("reviewed_by", 3), ("prior_exposure", 40), ("rationale", 80)):
        if not isinstance(review[name], str) or not minimum <= len(review[name].strip()) <= 4_000:
            raise ValueError("substantive bounded reviewer identity, exposure and rationale are required")
    if _clock(review["reviewed_at"]) < _clock(source["retrieved_at"]):
        raise ValueError("declared review precedes source retrieval")
    return value


@dataclass
class _Node:
    tag: str
    start: int
    end: int | None
    attrs: dict
    hidden: bool
    parent: _Node | None
    children: list = field(default_factory=list)

    def text(self) -> str:
        def render(node):
            if isinstance(node, str):
                return node
            content = "".join(render(child) for child in node.children)
            return " " + content + " " if node.tag in _BLOCK else content
        return " ".join(render(self).split())


class _EvidenceHTML(HTMLParser):
    """Parse the actual prefix/ancestors; retain only the bounded evidence region."""

    def __init__(self, body: bytes, begin: int, end: int):
        super().__init__(convert_charrefs=True)
        self.raw, self.begin, self.end = body, begin, end
        self.source = body[:end].decode("utf-8", errors="strict")
        if "\x00" in self.source:
            raise ValueError("NUL in HTML")
        self.lines = [0, *(match.end() for match in re.finditer("\n", self.source))]
        self.char_at = self.byte_at = 0
        self.stack, self.nodes, self.parts = [], [], []
        self.page_tags = {"html": 0, "body": 0}
        self.feed(self.source)
        self.close()
        if self.page_tags != {"html": 1, "body": 1}:
            raise ValueError("expected a retained HTML document, not detached evidence fragments")

    def position(self):
        line, column = self.getpos()
        current = self.lines[line - 1] + column
        self.byte_at += len(self.source[self.char_at:current].encode())
        self.char_at = current
        return self.byte_at

    def handle_starttag(self, tag, attrs):
        start = self.position()
        if tag in self.page_tags:
            self.page_tags[tag] += 1
        if len(self.stack) >= 200 or len(self.nodes) >= 20_000:
            raise ValueError("HTML evidence structure limit")
        if len(dict(attrs)) != len(attrs):
            raise ValueError("duplicate HTML attribute")
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style") or "").casefold()
        hidden = (bool(self.stack and self.stack[-1].hidden) or tag in {"script", "style", "template", "noscript", "ix:hidden"}
                  or "hidden" in attrs or "inert" in attrs or (attrs.get("aria-hidden") or "").casefold() == "true"
                  or "display:none" in style or "visibility:hidden" in style or "visibility:collapse" in style
                  or (tag in {"details", "dialog"} and "open" not in attrs))
        node = _Node(tag, start, start + len(self.get_starttag_text().encode()) if tag in _VOID else None,
                     attrs, hidden, self.stack[-1] if self.stack else None)
        if self.begin <= start < self.end:
            if hidden:
                raise ValueError("hidden or collapsed evidence context")
            self.nodes.append(node)
            if self.stack and self.stack[-1].start >= self.begin:
                self.stack[-1].children.append(node)
            if tag in _BLOCK:
                self.parts.append((start, " "))
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            node = self.stack.pop()
            node.end = node.start + len(self.get_starttag_text().encode())

    def handle_endtag(self, tag):
        start = self.position()
        if not self.stack or self.stack[-1].tag != tag:
            raise ValueError("unrecognized HTML nesting")
        node = self.stack.pop()
        node.end = self.raw.find(b">", start) + 1
        if node.end <= start:
            raise ValueError("incomplete HTML end tag")
        if self.begin <= start < self.end and tag in _BLOCK:
            self.parts.append((start, " "))

    def handle_data(self, data):
        start = self.position()
        if self.begin <= start < self.end:
            if not self.stack or self.stack[-1].hidden:
                raise ValueError("unbound or hidden evidence text")
            self.parts.append((start, data))
            if self.stack[-1].start >= self.begin:
                self.stack[-1].children.append(data)

    def text(self, span):
        return " ".join("".join(text for pos, text in self.parts if span["start"] <= pos < span["end"]).split())


def _one(values, field):
    if len(values) != 1:
        raise ValueError("missing or ambiguous " + field)
    return values[0]


def _table(review, body):
    spans = review["evidence"]
    for span in spans.values():
        fragment = body[span["start"]:span["end"]]
        fragment.decode("utf-8", errors="strict")
        if _hash(fragment) != span["sha256"]:
            raise ValueError("evidence span hash mismatch")
    intro, table, header, row = (spans[name] for name in ("intro", "table", "header", "row"))
    if intro["end"] != table["start"] or table["end"] - intro["start"] > 200_000:
        raise ValueError("intro must be immediately contiguous with the bounded full table")
    document = _EvidenceHTML(body, intro["start"], table["end"])
    if not any(n.start == intro["start"] and n.tag in {"p", "div"} for n in document.nodes):
        raise ValueError("introduction must begin at a complete source block")
    def node(span, tag):
        return _one([n for n in document.nodes if n.start == span["start"] and n.end == span["end"] and n.tag == tag], tag + " exact span")
    table_node, header_node, row_node = node(table, "table"), node(header, "tr"), node(row, "tr")
    if header_node.parent is not table_node and not (header_node.parent.tag in {"tbody", "thead"} and header_node.parent.parent is table_node):
        raise ValueError("header is not in the selected table")
    table_nodes = [n for n in document.nodes if n.tag == "table"]
    if table_nodes != [table_node]:
        raise ValueError("multiple or nested tables in evidence region")
    for current in document.nodes:
        if current.start < table["start"]:
            continue
        if current.tag not in {"table", "tbody", "thead", "tfoot", "tr", "td", "div", "span", "b", "i", "strong", "em", "sup", "sub", "br"}:
            raise ValueError("unsupported table annotation or layout")
        permitted_children = {"table": {"tbody", "thead", "tfoot", "tr"}, "tbody": {"tr"},
                              "thead": {"tr"}, "tfoot": {"tr"}, "tr": {"td"}}.get(current.tag)
        if permitted_children is not None and any(
                (child.strip() if isinstance(child, str) else child.tag not in permitted_children)
                for child in current.children):
            raise ValueError("unparsed table or row context outside cells")
    intro_text = document.text(intro)
    matched = _INTRO.fullmatch(intro_text)
    if matched is None:
        raise ValueError("unrecognized operating-fab table introduction")
    as_of = datetime.strptime(matched["date"], "%B %d, %Y").date()
    if as_of < _day(review["event"]["high"]) or as_of > _day(review["source"]["published_at"]):
        raise ValueError("table-state date is inconsistent with elapsed event year/publication")
    rows = [n for n in document.nodes if n.tag == "tr"]
    values = []
    for current in rows:
        parent = current.parent
        if parent is not table_node and not (parent.tag in {"tbody", "thead", "tfoot"} and parent.parent is table_node):
            raise ValueError("row is not directly bound to selected table")
        cells = [n for n in current.children if isinstance(n, _Node) and n.tag == "td"]
        if len(cells) != 7 or any(n.parent is not current for n in cells):
            raise ValueError("each table row must have seven direct td cells")
        if any(n.attrs.get(attr, "1") != "1" for n in cells for attr in ("rowspan", "colspan")):
            raise ValueError("merged cells are unsupported")
        texts = [n.text() for n in cells]
        if any(texts[i] for i in (1, 3, 5)):
            raise ValueError("nonempty spacer column")
        values.append(texts)
    index = values.index(_HEADERS) if values.count(_HEADERS) == 1 else -1
    if index < 0 or rows[index] is not header_node:
        raise ValueError("expected unique commercial-production header not selected")
    if any(any(cells) for cells in values[:index]):
        raise ValueError("nonempty rows precede table header")
    selected = [i for i, cells in enumerate(values) if cells[0] == "21"]
    chosen = _one(selected, "Fab 21 row")
    if rows[chosen] is not row_node or chosen <= index:
        raise ValueError("selected evidence row does not identify Fab 21")
    if values[chosen] != ["21", "", review["event"]["literal"], "", "12-inch", "", "5"]:
        raise ValueError("Fab 21 commercial-production year or table layout mismatch")
    for cells in values[index + 1:]:
        if not (re.fullmatch(r"\d+", cells[0]) and re.fullmatch(r"[12]\d{3}", cells[2])
                and re.fullmatch(r"\d+-inch", cells[4]) and re.fullmatch(r"\d+", cells[6])):
            raise ValueError("unparsed operating-fab data row")
    return {name: document.text(span) for name, span in spans.items()}


def validate_review(review, *, body: bytes, provenance: bytes) -> dict:
    """Validate all local bytes and semantics; returns LOCAL-ONLY evidence text.

    This is not an admission, reviewer-independence check, or source-authentication
    certificate. No filesystem or database is modified.
    """
    review = _review(review)
    if not isinstance(body, bytes) or len(body) != review["source"]["bytes"] or _hash(body) != review["source"]["content_sha256"]:
        raise ValueError("retained body hash or size mismatch")
    if not isinstance(provenance, bytes) or _hash(provenance) != review["provenance"]["sha256"]:
        raise ValueError("exact provenance bytes mismatch")
    metadata = _json(provenance)
    if metadata.get("format") != "semiconductor-atlas-ai-critical-input-v1":
        raise ValueError("unsupported baseline provenance format")
    sources = metadata.get("sources")
    if not isinstance(sources, list) or not all(isinstance(row, dict) for row in sources):
        raise ValueError("invalid baseline sources")
    source = _one([row for row in sources if row.get("source_id") == review["source"]["source_id"]], "baseline source identity")
    if any(source.get(key) != value for key, value in review["source"].items()):
        raise ValueError("review source fields disagree with baseline provenance")
    if source.get("media_type") != "text/html" or source.get("source_type") != "official_primary":
        raise ValueError("unsupported source media or provenance type")
    if not (_clock(source["retrieved_at"]) <= _clock(source.get("acquired_at")) <= _clock(review["review"]["reviewed_at"])):
        raise ValueError("acquired/retrieved/review clocks are inconsistent")
    runs = metadata.get("ingestion_runs")
    if not isinstance(runs, list) or not all(isinstance(row, dict) for row in runs):
        raise ValueError("invalid baseline ingestion lineage")
    run = _one([row for row in runs if row.get("run_id") == source.get("ingestion_run_id")], "source ingestion run")
    expected_input = {"source_id": SOURCE_ID, "content_sha256": source["content_sha256"], "document_role": "primary"}
    if run.get("source_id") != SOURCE_ID or run.get("outcome") != "succeeded" or run.get("inputs") != [expected_input]:
        raise ValueError("source ingestion lineage mismatch")
    if not (max(_clock(source["retrieved_at"]), _clock(source["acquired_at"])) <= _clock(run.get("started_at"))
            <= _clock(run.get("finished_at")) <= _clock(metadata.get("recorded_at"))
            <= _clock(review["review"]["reviewed_at"]) <= _clock(_now())):
        raise ValueError("baseline/review clocks are inconsistent or in the future")
    local_text = _table(review, body)
    return {"review_sha256": _hash(review), "source_row_sha256": _hash(source), "ingestion_run_sha256": _hash(run),
            "provenance_recorded_at": metadata["recorded_at"], "local_evidence_text": local_text, "rights": RIGHTS.copy()}


def _disk_codes():
    return {**_benchmark._codes(), Path(__file__).name: _hash(Path(__file__).read_bytes())}


_LOADED_CODES = _disk_codes()


def _codes():
    codes = _disk_codes()
    if codes != _LOADED_CODES:
        raise ValueError("producer implementation changed since module loading; restart the process")
    return codes


def admit(review, *, body: bytes, provenance: bytes) -> dict:
    """Construct a newly clocked immutable sidecar; call write_new to retain it."""
    started = _now()
    codes = _codes()
    review = _review(review)
    checked = validate_review(review, body=body, provenance=provenance)
    if _codes() != codes:
        raise ValueError("producer implementation changed during admission")
    admitted = _now()
    if not _clock(review["review"]["reviewed_at"]) <= _clock(started) <= _clock(admitted):
        raise ValueError("admission clocks precede review or moved backwards")
    return _benchmark._seal({"format": ARTIFACT_FORMAT, "review": review,
        "review_sha256": checked["review_sha256"], "source_row_sha256": checked["source_row_sha256"],
        "ingestion_run_sha256": checked["ingestion_run_sha256"], "provenance_recorded_at": checked["provenance_recorded_at"],
        "validation_started_at": started, "admitted_at": admitted, "code_sha256": codes,
        "rights": RIGHTS, "boundaries": BOUNDARIES})


def verify(artifact, *, body: bytes, provenance: bytes) -> dict:
    """Replay exact local evidence, semantic rule, lineage, clocks and code pins."""
    artifact = _benchmark._load(artifact, ARTIFACT_FORMAT)
    _keys(artifact, {"format", "review", "review_sha256", "source_row_sha256", "ingestion_run_sha256",
        "provenance_recorded_at", "validation_started_at", "admitted_at", "code_sha256", "rights", "boundaries", "sha256"}, "artifact")
    codes = _codes()
    if (canonical_bytes(artifact["rights"]) != canonical_bytes(RIGHTS)
            or canonical_bytes(artifact["boundaries"]) != canonical_bytes(BOUNDARIES) or artifact["code_sha256"] != codes):
        raise ValueError("rights, boundary or pinned implementation mismatch")
    checked = validate_review(artifact["review"], body=body, provenance=provenance)
    if _codes() != codes:
        raise ValueError("producer implementation changed during verification")
    if any(artifact[key] != checked[key] for key in ("review_sha256", "source_row_sha256", "ingestion_run_sha256", "provenance_recorded_at")):
        raise ValueError("review or provenance lineage hash mismatch")
    if not (_clock(artifact["review"]["review"]["reviewed_at"]) <= _clock(artifact["validation_started_at"])
            <= _clock(artifact["admitted_at"]) <= _clock(_now())):
        raise ValueError("invalid or future admission clocks")
    return {"sha256": artifact["sha256"], "verified": True, "admitted_at": artifact["admitted_at"],
            "subject": artifact["review"]["subject"], "event": artifact["review"]["event"],
            "rights": RIGHTS.copy(), "boundaries": BOUNDARIES.copy()}
