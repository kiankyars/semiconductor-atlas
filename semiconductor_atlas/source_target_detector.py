"""Pure, narrow source-native calendar-target parsing for prospective shadow use.

The grammar was developed against previously exposed retained pages. Results are
parser-scoped candidates, never manufacturing attainment or measured performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re


FORMAT = "semiconductor-atlas-source-target-detector-v1"
RULE_VERSION = "structured-source-calendar-targets-v1"
MAX_BYTES = 2_000_000
MAX_NODES = 30_000
MAX_DEPTH = 200
TSMC = "https://www.nist.gov/chips/tsmc-arizona-phoenix"
SAMSUNG = "https://www.nist.gov/chips/samsung-electronics-texas-austin"
AMKOR = "https://www.nist.gov/chips/amkor-technology-inc-arizona-peoria"
AMKOR_COMPANY = "https://amkor.com/blog/amkor-peoria-number-one-deal-north-america/"
SUPPORTED_URLS = (TSMC, SAMSUNG, AMKOR, AMKOR_COMPANY)
BOUNDARIES = {"physical_change": False, "canonical_identity": False,
              "performance_established": False, "delivery_eligible": False,
              "browser_rendered_visibility_verified": False}
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = set("p div section article h1 h2 h3 h4 h5 h6 li ul ol table tr td th br hr".split())
_CALENDAR = r"(?:(?:by|in|at|before|after|during) )?(?:the )?(?:(?:first|second) half (?:of|in) 20\d{2}|end of (?:20\d{2}|the decade)|20\d{2})"
_DATED = re.compile(r"\b20\d{2}\b|\bend of the decade\b", re.I)
_MANUFACTURING = re.compile(r"\b(?:production|operational|opening|open|construction|fabs?|facility|facilities|campus)\b", re.I)
_NEGATION = re.compile(r"\b(?:not(?! only\b)|never|no longer|cancelled|canceled|delayed|suspended|postponed|withdrawn|abandoned)\b", re.I)
_SCOPE_CHANGE = re.compile(r"\b(?:fourth|additional|another|instead|only|excluding|except|phase (?:one|two|1|2))\b", re.I)
_QUALIFIED_SUBJECT = re.compile(r"\b(?:production|operational|opening|open|construction|fabs?|facility|facilities|campus|projects?|timelines?|targets?|phases?)\b", re.I)


def _hash(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha256(raw).hexdigest()


class _Abstain(ValueError):
    pass


@dataclass
class _Node:
    tag: str
    start: int
    end: int
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    hidden: bool = False
    parent: "_Node | None" = None

    def text(self) -> str:
        def render(node):
            if isinstance(node, str):
                return node
            if node.hidden:
                return ""
            content = "".join(render(child) for child in node.children)
            return f" {content} " if node.tag in _BLOCK else content
        return " ".join(render(self).split())

    def contains(self, other) -> bool:
        return self.start <= other.start and other.end <= self.end


class _Document(HTMLParser):
    def __init__(self, raw: bytes):
        super().__init__(convert_charrefs=False)
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
            raise _Abstain("missing_or_oversized_html")
        self.raw = raw
        self.text_source = raw.decode("utf-8", errors="strict")
        if "\x00" in self.text_source:
            raise _Abstain("malformed_html")
        self.offsets, offset = [0], 0
        for character in self.text_source:
            offset += len(character.encode("utf-8"))
            self.offsets.append(offset)
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", self.text_source))
        self.root = _Node("root", 0, len(raw))
        self.stack, self.nodes = [self.root], []
        self.doctype_seen = False
        self.feed(self.text_source)
        self.close()
        if len(self.stack) != 1 or len(self.find("html")) != 1 or len(self.find("body")) != 1:
            raise _Abstain("malformed_or_unrecognized_html_document")

    def position(self) -> int:
        line, column = self.getpos()
        return self.offsets[self.lines[line - 1] + column]

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise _Abstain("html_structure_limit")
        if len({name for name, _ in attrs}) != len(attrs):
            raise _Abstain("duplicate_html_attribute")
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style", "") or "").casefold()
        hidden = (self.stack[-1].hidden or tag in {"script", "style", "template", "noscript"}
                  or "hidden" in attributes or "inert" in attributes
                  or (attributes.get("aria-hidden") or "").casefold() == "true"
                  or "display:none" in style or "visibility:hidden" in style)
        start = self.position()
        node = _Node(tag, start, start + len(self.get_starttag_text().encode()), attributes,
                     hidden=hidden, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise _Abstain("malformed_html_nesting")
        node = self.stack.pop()
        start = self.position()
        finish = self.raw.find(b">", start)
        if finish == -1 or re.fullmatch(rb"</" + tag.encode() + rb"\s*>", self.raw[start:finish + 1], re.I) is None:
            raise _Abstain("malformed_html_end_tag")
        node.end = finish + 1

    def handle_decl(self, declaration):
        if declaration.casefold() != "doctype html" or self.doctype_seen or self.nodes:
            raise _Abstain("unsupported_html_declaration")
        self.doctype_seen = True

    def unknown_decl(self, declaration):
        raise _Abstain("unsupported_html_declaration")

    def handle_pi(self, data):
        raise _Abstain("unsupported_html_processing_instruction")

    def handle_comment(self, data):
        expected = ("<!--" + data + "-->").encode()
        start = self.position()
        if self.raw[start:start + len(expected)] != expected:
            raise _Abstain("malformed_html_comment")

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_entityref(self, name):
        self.stack[-1].children.append(unescape("&" + name + ";"))

    def handle_charref(self, name):
        self.stack[-1].children.append(unescape("&#" + name + ";"))

    def find(self, tag, *, within=None, text=None):
        return [node for node in self.nodes if node.tag == tag and not node.hidden
                and (within is None or within.contains(node)) and (text is None or node.text() == text)]

    def span(self, node, locator):
        return {"start": node.start, "end": node.end,
                "sha256": _hash(self.raw[node.start:node.end]), "locator": locator}


def _one(values, reason):
    if len(values) != 1:
        raise _Abstain(reason)
    return values[0]


def _cell(document, table, label):
    row = _one([row for row in document.find("tr", within=table)
                if any(cell.text() == label for cell in document.find("td", within=row))], "missing_or_ambiguous_" + label.lower().replace(" ", "_"))
    cells = document.find("td", within=row)
    if len(cells) < 2 or cells[-2].text() != label:
        raise _Abstain("unrecognized_table_row")
    return cells[-1]


def _evidence(document, node, literal, predicate, scope, context):
    if literal not in node.text() or not re.fullmatch(_CALENDAR, literal):
        raise _Abstain("unsupported_calendar_literal")
    return {"literal": literal, "predicate": predicate, "scope": scope,
            "fragment": document.span(node, "Parsed target formulation"),
            "context": [document.span(item, "Source-native subject and inherited predicate context") for item in context]}


def _unparsed(document, container, consumed):
    def residual(node):
        if isinstance(node, str):
            return node
        if node.hidden:
            return ""
        if any(region is node for region in consumed):
            return "\n[parsed target formulation]\n"
        text = "".join(residual(child) for child in node.children)
        return "\n" + text + "\n" if node.tag in _BLOCK else text

    remaining = residual(container)
    for line in remaining.splitlines():
        text = " ".join(line.split())
        qualifications = any(_QUALIFIED_SUBJECT.search(sentence) and (_NEGATION.search(sentence) or _SCOPE_CHANGE.search(sentence))
                             for sentence in re.split(r"[.;] +", text))
        if (_MANUFACTURING.search(text) and _DATED.search(text)) or qualifications:
            raise _Abstain("unparsed_manufacturing_calendar_or_qualification")
    return " ".join(" ".join(line.split()).removesuffix(".") for line in remaining.splitlines() if line.strip())


def _nist(document, url):
    titles = {TSMC: "TSMC Arizona", SAMSUNG: "Samsung Electronics (Texas)", AMKOR: "Amkor Technology, Inc. (Arizona)"}
    title = _one(document.find("h1"), "missing_or_ambiguous_page_identity")
    if title.text() != titles[url]:
        raise _Abstain("source_identity_mismatch")
    summary_heading = _one(document.find("h2", text="Project Summary"), "missing_or_ambiguous_project_summary")
    containers = [node for node in document.find("div") if "text-with-summary" in (node.attrs.get("class") or "").split() and node.contains(summary_heading)]
    container = _one(containers, "unrecognized_project_container")
    headings = document.find("h2", within=container)
    expected = ["Project Summary", "Economic and National Security Impact"]
    if url == AMKOR:
        expected += ["Workforce and Community Impact", "Environmental and Worker Safety Commitments"]
    expected += ["Financial and Commercial Terms"]
    if [node.text() for node in headings] != expected:
        raise _Abstain("unrecognized_project_sections")
    summary = _one([node for node in document.find("p", within=container)
                    if summary_heading.end <= node.start < headings[1].start], "ambiguous_project_scope")
    table_titles = {TSMC: ["Project Statistics: TSMC Arizona Corporation"], SAMSUNG: ["Samsung Project Overview"],
                    AMKOR: ["Amkor Technology Project Overview", "Project Statistics: Peoria, Arizona"]}[url]
    tables = document.find("table", within=container)
    if len(tables) != len(table_titles):
        raise _Abstain("unrecognized_project_tables")
    for table, expected_title in zip(tables, table_titles):
        if [node.text() for node in document.find("h3", within=table)] != [expected_title]:
            raise _Abstain("unrecognized_project_table_identity")
    if [node.text() for node in document.find("h3", within=container)] not in (table_titles, [*table_titles, "Related Links"]):
        raise _Abstain("unrecognized_project_subsections")
    timeline = _cell(document, tables[-1], "Project Timeline")
    project_type = _cell(document, tables[-1], "Project Type")
    location = _cell(document, tables[0], "Location(s)")
    scope_nodes = [summary, project_type, location]
    subjects, consumed = [], [timeline]
    if url == TSMC:
        if ("three greenfield leading-edge fabs in Phoenix, Arizona" not in summary.text()
                or project_type.text() != "Construction of three greenfield leading-edge fabs" or location.text() != "Phoenix, Arizona"):
            raise _Abstain("unsupported_source_project_scope")
        narrative = _one([node for node in document.find("p", within=container)
                          if re.search(r"\b(?:first fab|second fab|third fab|in the third)\b", node.text())], "missing_or_ambiguous_fab_narrative")
        match = re.fullmatch(r"TSMC Arizona is on track to begin high-volume production in the first fab (?P<first>" + _CALENDAR
            + r"), with (?:production beginning in the second fab (?P<second_a>" + _CALENDAR
            + r")|production in the second fab targeted for (?P<second_b>" + _CALENDAR
            + r")) and in the third (?P<third>" + _CALENDAR + r")\.", narrative.text())
        timeline_match = re.fullmatch(r"Fab 1: On track to start production (?P<first>" + _CALENDAR
            + r") Fab 2: Expected to begin production (?P<second>" + _CALENDAR
            + r") Fab 3: Expected to begin production (?P<third>" + _CALENDAR + r")", timeline.text())
        if match is None or timeline_match is None:
            raise _Abstain("unrecognized_fab_target_grammar")
        dates = [match["first"], match["second_a"] or match["second_b"], match["third"]]
        for number, ordinal in enumerate(("first", "second", "third")):
            scope = f"Source-described {ordinal} of three greenfield Arizona fabs; not a canonical facility"
            subject = {"source_native_subject": f"TSMC Arizona {ordinal} fab", "formulations": []}
            for name, node, literal, predicate in (("narrative", narrative, dates[number], "planned high-volume production" if number == 0 else "planned production"),
                                                  ("timeline", timeline, timeline_match[ordinal], "planned production start")):
                subject["formulations"].append({"formulation": name, "milestone": predicate,
                    "evidence": _evidence(document, node, literal, predicate, scope, [*scope_nodes, node])})
            subjects.append(subject)
        consumed.append(narrative)
    elif url == SAMSUNG:
        scope_paragraph = _one([node for node in document.find("p", within=container)
            if "two new leading-edge logic fabs and an R&D fab in Taylor" in node.text()
            and "expansion to the company’s existing Austin facility" in node.text()], "unsupported_samsung_award_scope")
        if (location.text() != "Taylor, Texas Austin, Texas" or project_type.text() != "2 leading-edge logic fabs, and an R&D fab Expansion of existing facility"):
            raise _Abstain("unsupported_source_project_scope")
        scope_nodes.append(scope_paragraph)
        match = re.fullmatch(r"All facilities are expected to be operational (?P<date>" + _CALENDAR + r")", timeline.text())
        if match is None:
            raise _Abstain("unrecognized_award_target_grammar")
        subjects = [{"source_native_subject": "Samsung Texas award-project all facilities", "formulations": [
            {"formulation": "timeline", "milestone": "planned operational state", "evidence": _evidence(document, timeline, match["date"], "planned operational state",
             "Two Taylor logic fabs, a separate Taylor R&D fab and Austin expansion; award-wide, not Taylor-only", [*scope_nodes, timeline])}]}]
    else:
        if ("new advanced packaging and test facility" not in summary.text() or "Peoria, Arizona" not in summary.text()
                or location.text() != "Peoria, Arizona"
                or project_type.text() != "Construction of a new manufacturing facility for advanced production lines to interconnect, protect and test integrated circuits"):
            raise _Abstain("unsupported_source_project_scope")
        match = re.fullmatch(r"Mass production expected to begin (?P<date>" + _CALENDAR + r")", timeline.text())
        if match is None:
            raise _Abstain("unrecognized_mass_production_target_grammar")
        subjects = [{"source_native_subject": "Amkor Peoria NIST award project", "formulations": [
            {"formulation": "timeline", "milestone": "planned mass-production start", "evidence": _evidence(document, timeline, match["date"], "planned mass-production start",
             "NIST Peoria advanced packaging/test project; phase unspecified", [*scope_nodes, timeline])}]}]
    if any(_NEGATION.search(node.text()) for node in scope_nodes):
        raise _Abstain("qualified_or_negated_project_scope")
    remainder = _unparsed(document, container, consumed)
    return subjects, _hash([[node.text() for node in scope_nodes], remainder]), "nist_project_sections_v1"


def _amkor_company(document):
    container = _one([node for node in document.find("div") if "blog_content_tier" in (node.attrs.get("class") or "").split()], "unrecognized_company_article_container")
    if any(document.find(tag, within=container) for tag in ("table", "h1", "h2", "h3", "h4")):
        raise _Abstain("unrecognized_company_article_sections")
    paragraphs = document.find("p", within=container)
    scope = _one([node for node in paragraphs if "advanced semiconductor packaging and test campus in Peoria, Arizona" in node.text()], "missing_or_ambiguous_peoria_scope")
    target = _one([node for node in paragraphs if "Phase One" in node.text()], "missing_or_ambiguous_phase_one")
    match = re.fullmatch(r"Supporting the future of U.S. semiconductor manufacturing Phase One of the Amkor campus is expected to open (?P<date>" + _CALENDAR
        + r"), and the facility is anticipated to become one of the largest advanced semiconductor packaging facilities in the world\. (?P<tail>.+)", target.text())
    if match is None or _NEGATION.search(scope.text()) or _NEGATION.search(target.text()):
        raise _Abstain("unrecognized_phase_opening_target_grammar")
    if _DATED.search(match["tail"]) or (_QUALIFIED_SUBJECT.search(match["tail"]) and _SCOPE_CHANGE.search(match["tail"])):
        raise _Abstain("additional_unparsed_phase_target")
    remainder = _unparsed(document, container, [target])
    subject = {"source_native_subject": "Amkor Peoria campus Phase One", "formulations": [
        {"formulation": "narrative", "milestone": "planned opening", "evidence": _evidence(document, target, match["date"], "planned opening",
         "Phase One of the source-described Peoria advanced packaging/test campus; opening, not production start", [scope, target])}]}
    return [subject], _hash([scope.text(), match["tail"], remainder]), "amkor_phase_one_article_v1"


def _parse(url, raw):
    document = _Document(raw)
    return _amkor_company(document) if url == AMKOR_COMPANY else _nist(document, url)


def analyze(url: str, before: bytes, after: bytes) -> dict:
    """Compare independently extracted targets; missing or ambiguous coverage abstains."""
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "url": url, "result": "abstain",
              "reason": "unsupported_exact_url", "parser_route": None, "coverage": {}, "subjects": [],
              "body_sha256": {side: _hash(raw) if isinstance(raw, bytes) else None for side, raw in (("before", before), ("after", after))},
              "boundaries": BOUNDARIES.copy()}
    if not isinstance(url, str) or url not in SUPPORTED_URLS:
        return result
    parsed = {}
    for side, raw in (("before", before), ("after", after)):
        try:
            parsed[side] = _parse(url, raw)
            subjects, _, route = parsed[side]
            result["coverage"][side] = {"status": "parsed", "reason": "recognized_project_target_structure", "subject_count": len(subjects),
                "target_count": sum(len(subject["formulations"]) for subject in subjects)}
            result["parser_route"] = route
        except (_Abstain, UnicodeError, RecursionError) as error:
            result["coverage"][side] = {"status": "abstain", "reason": str(error) if isinstance(error, _Abstain) else "invalid_or_excessive_html",
                                         "subject_count": None, "target_count": None}
    if len(parsed) != 2:
        result["reason"] = "incomplete_parser_coverage"
        return result
    if parsed["before"][1:] != parsed["after"][1:]:
        result["reason"] = "source_scope_or_context_changed"
        return result
    left, right = parsed["before"][0], parsed["after"][0]
    identity = lambda subjects: [(subject["source_native_subject"], [(row["formulation"], row["milestone"]) for row in subject["formulations"]]) for subject in subjects]
    if identity(left) != identity(right):
        result["reason"] = "added_removed_or_ambiguous_target_alignment"
        return result
    for old, new in zip(left, right):
        subject = {"subject_key": _hash({"url": url, "subject": old["source_native_subject"]}),
                   "source_native_subject": old["source_native_subject"], "status": "unchanged", "formulations": []}
        for prior, current in zip(old["formulations"], new["formulations"]):
            status = "target_literal_changed" if prior["evidence"]["literal"] != current["evidence"]["literal"] else "unchanged"
            subject["formulations"].append({"formulation": prior["formulation"], "milestone": prior["milestone"], "status": status,
                                            "before": prior["evidence"], "after": current["evidence"]})
            if status == "target_literal_changed":
                subject["status"] = "revision_candidate"
        result["subjects"].append(subject)
    changed = any(subject["status"] == "revision_candidate" for subject in result["subjects"])
    result["result"] = "revision_candidate" if changed else "no_candidate"
    result["reason"] = "aligned_source_target_literal_changed" if changed else "recognized_source_targets_unchanged"
    return result
