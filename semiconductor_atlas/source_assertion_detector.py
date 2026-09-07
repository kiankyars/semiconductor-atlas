"""Pure, bounded source-statement extraction from three exposed development pages.

This is separate from the registered calendar detector. It describes wording in
retained HTML, not attained manufacturing state, event dates, or claim acceptance.
"""

from __future__ import annotations

import re

from .source_target_detector import _Abstain, _Document, _hash, _one


FORMAT = "semiconductor-atlas-source-assertion-detector-v1"
RULE_VERSION = "bounded-source-native-assertions-v1"
AMKOR_GROUNDBREAKING = "https://amkor.com/blog/amkor-semiconductor-packaging-facility-peoria-arizona/"
CHANDLER = "https://www.chandleraz.gov/business/economic-development/key-industries-and-employers/advanced-manufacturing"
MICRON_MTI = "https://www.mti.gov.sg/newsroom/speech-by-dpm-and-minister-for-trade-and-industry-gan-kim-yong-at-the-groundbreaking-ceremony-of-micron-s-hbm-manufacturing-facility/"
SUPPORTED_URLS = (AMKOR_GROUNDBREAKING, CHANDLER, MICRON_MTI)
BOUNDARIES = {key: False for key in (
    "physical_change", "canonical_identity", "event_date_inferred",
    "calendar_target_inferred", "production_means_hvm", "capacity_inferred",
    "performance_established", "delivery_eligible", "claim_acceptance",
    "whole_document_recall", "browser_rendered_visibility_verified",
    "prospective_registration", "independent_evaluation")}

_NEGATION = re.compile(r"\b(?:not(?! (?:only|just|least)\b)|never|no|cannot|unable|failed|cancel(?:led|ed)|delay(?:ed|s)?|suspend(?:ed|s)?|postpon(?:ed|es)|withdrawn|abandoned|halt(?:ed|s)?|shutter(?:ed|s|ing)?|shutdown|shut down|ceas(?:ed|es)|stopp(?:ed|ing))\b", re.I)
_MANUFACTURING = re.compile(r"\b(?:production|construction|groundbreaking|completion|operational|open(?:ed|ing)?|clos(?:ed|ing)|facilit(?:y|ies)|fabs?|plants?|campus|capacity|qualification|qualified|ramps?|ramping|manufacturing|targets?|projects?)\b", re.I)
_CALENDAR = re.compile(r"\b(?:20\d{2}|Q[1-4]|H[12]|tomorrow|yesterday|next (?:month|year|quarter)|end of the decade|first half|second half|January|February|March|April|June|July|August|September|October|November|December|(?:by|in|before|after|during) May|May [0-9])\b", re.I)
_MILESTONE_VOCABULARY = re.compile(
    r"\b(?:production|construction|groundbreaking|complet(?:e|es|ed|ing|ion)|operational|operating|"
    r"open(?:s|ed|ing)?|clos(?:ed|ing)|qualification|qualified|ramps?|ramping|"
    r"inaugurat(?:e|ed|ion)|commission(?:ed|ing)|timelines?|timetables?|targets?)\b", re.I)
_UNPARSED_PREDICATE = re.compile(
    r"\b(?:production|construction|groundbreaking|qualification|ramping)\s+(?:is |was |has |will |begins?|starts?|commences?|continues?|progresses?|ended|stopped)"
    r"|\b(?:begin|began|start|started|commence|commenced|cease|ceased|resume|resumed|reach|reached)\b.{0,90}\b(?:production|construction|qualification|capacity)\b"
    r"|\b(?:is|was|are|were|be|become|became|has been|will be|remains?)\s+(?:now |fully |already |newly )?(?:open|closed|operational|complete|completed|qualified)\b"
    r"|\b(?:will|would|expects? to|expected to|plans? to|planned to|scheduled to|targeted to|intends? to)\s+(?:begin|start|open|complete|finish|produce|manufacture|operate|qualify|ramp)\b"
    r"|\b(?:high[- ]volume|mass)\s+production\b"
    r"|\b(?:produces?|producing|manufactures?|(?:is|are|was|were) manufacturing)\b.{0,60}\b(?:chips?|wafers?)\b", re.I)
_SCOPE_QUALIFICATION = re.compile(r"\b(?:(?<!not )only|excluding|except|instead|another|additional|fourth|phase (?:one|two|1|2))\b", re.I)
_DATE_TEXT = r"(?:[1-9]|[12][0-9]|3[01]) (?:January|February|March|April|May|June|July|August|September|October|November|December) 20\d{2}"


def _class(node, value):
    return value in (node.attrs.get("class") or "").split()


def _ancestors(node):
    while node is not None:
        yield node
        node = node.parent


def _paragraphs(document, container):
    if any(document.find(tag, within=container) for tag in ("table", "h1", "h2", "h3", "h4", "h5", "h6")):
        raise _Abstain("unrecognized_assertion_container_sections")
    return document.find("p", within=container)


def _match(paragraphs, pattern, reason):
    values = []
    for node in paragraphs:
        matches = list(re.finditer(pattern, node.text()))
        values.extend((node, match) for match in matches)
    return _one(values, reason)


def _assertion(document, url, subject, predicate, formulation, node, literal, context):
    if not literal or node.text().count(literal) != 1:
        raise _Abstain("ambiguous_assertion_literal")
    return {
        "assertion_key": _hash({"url": url, "subject": subject, "predicate": predicate, "formulation": formulation}),
        "source_native_subject": subject,
        "predicate": predicate,
        "formulation": formulation,
        "literal": literal,
        "event_date": None,
        "calendar_target": None,
        "evidence": {
            "fragment": document.span(node, "Complete source paragraph containing normalized visible assertion literal"),
            "context": [document.span(item, "Source-native identity and surrounding context") for item in context],
        },
    }


def _residual(node, substitutions):
    if isinstance(node, str):
        return node
    if node.hidden:
        return ""
    if id(node) in substitutions:
        value = node.text()
        for literal, replacement in substitutions[id(node)]:
            if value.count(literal) != 1:
                raise _Abstain("ambiguous_residual_substitution")
            value = value.replace(literal, replacement, 1)
        return "\n" + value + "\n"
    value = "".join(_residual(child, substitutions) for child in node.children)
    return "\n" + value + "\n" if node.tag in {"p", "div", "section", "article", "li", "ol", "ul", "br"} else value


def _coverage(document, containers, assertions, substitutions, exclusions, nominal_context=()):
    texts = [_residual(node, substitutions) for node in containers]
    for value in texts:
        for line in value.splitlines():
            line = " ".join(line.split())
            if not line:
                continue
            for literal in nominal_context:
                line = line.replace(literal, "[bound non-milestone context]")
            if (_NEGATION.search(line)
                    or _CALENDAR.search(line)
                    or _MILESTONE_VOCABULARY.search(line)
                    or _UNPARSED_PREDICATE.search(line)
                    or (_MANUFACTURING.search(line) and _SCOPE_QUALIFICATION.search(line))):
                raise _Abstain("unparsed_manufacturing_target_or_qualification")
    return {
        "status": "recognized_bounded_assertions_with_bound_unparsed_context",
        "assertion_count": len(assertions),
        "context_sha256": _hash([" ".join(value.split()) for value in texts]),
        "containers": [document.span(node, "Complete bounded source context") for node in containers],
        "source_unparsed": True,
        "whole_document_complete": False,
        "exclusions": exclusions,
    }


def _substitute(substitutions, node, literal, token="[recognized source assertion]"):
    substitutions.setdefault(id(node), []).append((literal, token))


def _amkor(document):
    title = _one(document.find("h2", text="Amkor Technology Breaks Ground on Landmark U.S. Semiconductor Packaging Facility"), "missing_or_ambiguous_amkor_article_identity")
    if _one(document.find("h1"), "missing_or_ambiguous_page_identity").text() != "Blog":
        raise _Abstain("source_identity_mismatch")
    title_container = _one([node for node in _ancestors(title) if _class(node, "blog_title_tier")], "unrecognized_amkor_title_container")
    metadata = _one([node for node in document.find("div", within=title_container) if _class(node, "blog_title_meta")], "missing_or_ambiguous_article_metadata")
    if re.fullmatch(r"(?:January|February|March|April|May|June|July|August|September|October|November|December) [1-9][0-9]?, 20\d{2} in Company News by Amkor Marcom", metadata.text()) is None:
        raise _Abstain("unrecognized_amkor_article_metadata")
    container = _one([node for node in document.find("div") if _class(node, "blog_content_tier")], "unrecognized_amkor_article_container")
    paragraphs = _paragraphs(document, container)
    ceremony, match = _match(paragraphs,
        r"On (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (?:Jan\.|Feb\.|Mar\.|Apr\.|May|Jun\.|Jul\.|Aug\.|Sep\.|Sept\.|Oct\.|Nov\.|Dec\.) [1-9][0-9]?, Amkor Technology, Inc\. celebrated a major advancement in American semiconductor manufacturing with the groundbreaking of its cutting-edge semiconductor packaging and test facility in Peoria, Arizona\.",
        "missing_or_ambiguous_amkor_groundbreaking_grammar")
    construction, progress = _match(paragraphs,
        r"As construction (?:progresses|continues), the focus will remain on expanding cutting-edge manufacturing capacity, creating career opportunities for a skilled workforce, and enhancing supply chain resilience\.",
        "missing_or_ambiguous_amkor_construction_grammar")
    subject = "Amkor source-described Peoria semiconductor packaging and test facility"
    assertions = [
        _assertion(document, AMKOR_GROUNDBREAKING, subject, "reported_groundbreaking_ceremony", "opening_narrative", ceremony, match[0], [title, metadata, ceremony]),
        _assertion(document, AMKOR_GROUNDBREAKING, subject, "undated_construction_wording", "construction_narrative", construction, progress[0], [title, ceremony, construction]),
    ]
    substitutions = {}
    for node, literal in ((ceremony, match[0]), (construction, progress[0])):
        _substitute(substitutions, node, literal)
    coverage = _coverage(document, [title, metadata, container], assertions, substitutions,
        ["Navigation, sharing controls, related articles and footer outside the selected article body/title/metadata",
         "Jobs, cleanroom area, investment and campus scale wording are bound context, not capacity assertions",
         "Article metadata and incomplete ceremony date wording do not establish a normalized manufacturing event date"],
        [title.text(), metadata.text(), "boosting domestic production capabilities",
         "The groundbreaking event brought together a broad coalition of government officials, customers, suppliers, academic partners, employees, and community leaders."])
    return assertions, coverage, "amkor_groundbreaking_article_v1"


def _chandler(document):
    title = _one(document.find("h1"), "missing_or_ambiguous_page_identity")
    if title.text() != "Advanced Manufacturing":
        raise _Abstain("source_identity_mismatch")
    heading = _one(document.find("h2", text="A Few of the Leading Companies Operating in Chandler"), "missing_or_ambiguous_employer_section")
    link = _one([node for node in document.find("a", text="Intel") if node.attrs.get("role") == "button"], "missing_or_ambiguous_intel_section_identity")
    region_id = link.attrs.get("aria-controls")
    if not region_id or link.attrs.get("href") != "#" + region_id:
        raise _Abstain("unrecognized_intel_section_control")
    header = _one([node for node in _ancestors(link) if _class(node, "card-header")], "unrecognized_intel_section_header")
    container = _one([node for node in document.find("div") if node.attrs.get("role") == "region" and node.attrs.get("id") == region_id], "missing_or_ambiguous_intel_region")
    if not header.attrs.get("id") or container.attrs.get("aria-labelledby") != header.attrs["id"]:
        raise _Abstain("unrecognized_intel_section_binding")
    accordion = _one([node for node in _ancestors(container) if _class(node, "paragraph--type--accordion")], "unrecognized_employer_accordion")
    if not accordion.contains(header) or heading.end > accordion.start:
        raise _Abstain("unrecognized_intel_section_layout")
    paragraphs = _paragraphs(document, container)
    paragraph, match = _match(paragraphs,
        r"One of those fabs, Fab 52, (?P<opening>is now open|is open) and (?P<production>produces|is producing) the most advanced logic chips in the U\.S\.",
        "missing_or_ambiguous_fab52_assertion_grammar")
    prefix = paragraph.text()[:match.start()]
    if (not prefix.endswith("Intel announced an investment of more than $32 billion to build two new leading-edge chip factories and modernize an existing fab at its Ocotillo Campus in Chandler. ")
            or paragraph.text()[match.end():]):
        raise _Abstain("unrecognized_fab52_inherited_project_scope")
    subject = "Intel Fab 52, one of the source-described Ocotillo Campus fabs in Chandler"
    assertions = [
        _assertion(document, CHANDLER, subject, "reported_opening", "fab52_opening", paragraph, match["opening"], [title, heading, header, paragraph]),
        _assertion(document, CHANDLER, subject, "reported_production_not_hvm", "fab52_production", paragraph, match["production"] + " the most advanced logic chips in the U.S.", [title, heading, header, paragraph]),
    ]
    substitutions = {}
    for assertion in assertions:
        _substitute(substitutions, paragraph, assertion["literal"])
    coverage = _coverage(document, [title, heading, header, container], assertions, substitutions,
        ["Other employer accordion regions, news cards, navigation and footer",
         "Intel two-campus employment and multi-fab investment remain unallocated context",
         "Collapsed accordion markup is parsed statically; browser visibility and current production are unverified"], [title.text(), heading.text()])
    return assertions, coverage, "chandler_intel_accordion_v1"


def _micron(document):
    main = _one([node for node in document.find("main") if node.attrs.get("id") == "main-content"], "missing_or_ambiguous_mti_main")
    title = _one(document.find("h1", within=main), "missing_or_ambiguous_mti_identity")
    if title.text() != "Speech by DPM and Minister for Trade and Industry Gan Kim Yong at the groundbreaking ceremony of Micron's HBM Manufacturing Facility":
        raise _Abstain("source_identity_mismatch")
    date = _one([node for node in document.find("p", within=main) if _class(node, "prose-label-sm-medium")], "missing_or_ambiguous_mti_article_date")
    if not re.fullmatch(_DATE_TEXT, date.text()):
        raise _Abstain("unrecognized_mti_article_date")
    paragraphs = document.find("p", within=main)
    ceremony, opening = _match(paragraphs,
        r"It is my (?:great pleasure|pleasure) to join you here today at the groundbreaking ceremony of Micron’s HBM Advanced Packaging facility\.",
        "missing_or_ambiguous_micron_groundbreaking_grammar")
    conclusion, closing = _match(paragraphs,
        r"Let me congratulate Micron (?:once again|again) on the groundbreaking of this new HBM facility in Singapore\.",
        "missing_or_ambiguous_micron_groundbreaking_reprise")
    completion, aspiration = _match(paragraphs,
        r"I look forward to (?:the successful completion|completion) of this new facility, and to forge a deeper and stronger partnership with Micron in the years ahead\.",
        "missing_or_ambiguous_micron_completion_aspiration")
    container = _one([node for node in _ancestors(ceremony) if _class(node, "overflow-x-auto") and node.contains(conclusion) and node.contains(completion)], "unrecognized_mti_speech_container")
    _paragraphs(document, container)
    if not main.contains(container) or date.end > container.start or title.end > date.start:
        raise _Abstain("unrecognized_mti_speech_layout")
    subject = "Micron source-described new HBM advanced packaging facility in Singapore"
    assertions = [
        _assertion(document, MICRON_MTI, subject, "reported_groundbreaking_ceremony", "opening_narrative", ceremony, opening[0], [title, date, ceremony]),
        _assertion(document, MICRON_MTI, subject, "reported_groundbreaking_ceremony", "closing_narrative", conclusion, closing[0], [title, date, ceremony, conclusion]),
        _assertion(document, MICRON_MTI, subject, "undated_completion_aspiration", "completion_narrative", completion, aspiration[0], [title, ceremony, conclusion, completion]),
    ]
    substitutions = {}
    for node, literal in ((ceremony, opening[0]), (conclusion, closing[0]), (completion, aspiration[0])):
        _substitute(substitutions, node, literal)
    _substitute(substitutions, title, title.text(), "[bound article title]")
    _substitute(substitutions, date, date.text(), "[bound article date]")
    # This counterfactual thanks the existing team, not a negated project milestone.
    for node in paragraphs:
        literal = "I would also like to take this opportunity to recognise the Micron Singapore team for their dedication and operational excellence all these years, without which today’s expansion will not be possible. Well done!"
        if node.text() == literal:
            _substitute(substitutions, node, literal, "[bound counterfactual acknowledgement]")
    nominal_context = [title.text(),
        "existing production and supply chains for the semiconductor industry",
        "The latest investment in HBM production further deepens our partnership with Micron.",
        "open up more career opportunities for our students across all levels, especially those in STEM disciplines"]
    for node in paragraphs:
        if re.fullmatch(r"First, the market for semiconductors across various applications, such as automotives, communications and computing, is projected to almost double over the course of this decade, to about US\$1 trillion by 20\d{2}\.", node.text()):
            nominal_context.append(node.text())
    coverage = _coverage(document, [main], assertions, substitutions,
        ["Navigation outside main, site-wide footer and linked resources",
         "Existing NAND operations, company investment, jobs and national projections remain unallocated context",
         "Article date and relative today wording are retained as context without normalized event-date inference"], nominal_context)
    # Bound non-assertion identities and the acknowledgement even though their text
    # was removed from the predicate hazard scan.
    coverage["context_sha256"] = _hash([coverage["context_sha256"], title.text(), date.text(),
        [node.text() for node in paragraphs if id(node) in substitutions and node not in (ceremony, conclusion, completion, date)]])
    return assertions, coverage, "mti_hbm_speech_v1"


def extract(url: str, raw: bytes) -> dict:
    """Extract source wording independently; no date or manufacturing state is inferred."""
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "url": url,
              "status": "abstain", "reason": "unsupported_exact_url", "parser_route": None,
              "body_sha256": _hash(raw) if isinstance(raw, bytes) else None,
              "assertions": [], "coverage": {"status": "unparsed", "assertion_count": None,
                  "context_sha256": None, "containers": [], "source_unparsed": True,
                  "whole_document_complete": False, "exclusions": []},
              "boundaries": BOUNDARIES.copy()}
    if not isinstance(url, str) or url not in SUPPORTED_URLS:
        return result
    try:
        document = _Document(raw)
        # The shared parser is frozen. Tighten static disclosure handling only on
        # this new parser's private tree, without asserting browser visibility.
        for node in document.nodes:
            if (node.parent.hidden
                    or (node.tag in {"details", "dialog"} and "open" not in node.attrs)
                    or "popover" in node.attrs):
                node.hidden = True
        parser = {AMKOR_GROUNDBREAKING: _amkor, CHANDLER: _chandler, MICRON_MTI: _micron}[url]
        assertions, coverage, route = parser(document)
    except (_Abstain, UnicodeError, RecursionError) as error:
        result["reason"] = str(error) if isinstance(error, _Abstain) else "invalid_or_excessive_html"
        return result
    result.update(status="extracted", reason="recognized_bounded_source_assertions",
                  parser_route=route, assertions=assertions, coverage=coverage)
    return result


def analyze(url: str, before: bytes | None, after: bytes | None) -> dict:
    """Compare extracted assertions only when alignment and all bound context agree."""
    extractions = {side: extract(url, raw) for side, raw in (("before", before), ("after", after))}
    result = {"format": FORMAT, "rule_version": RULE_VERSION, "url": url,
              "result": "abstain", "reason": "incomplete_parser_coverage", "parser_route": None,
              "body_sha256": {side: value["body_sha256"] for side, value in extractions.items()},
              "coverage": {side: value["coverage"] for side, value in extractions.items()},
              "extractions": extractions, "assertions": [], "boundaries": BOUNDARIES.copy()}
    left, right = extractions["before"], extractions["after"]
    if before is None and right["status"] == "extracted":
        result.update(result="extraction_only", reason="first_observation_not_a_revision", parser_route=right["parser_route"])
        return result
    if left["status"] != "extracted" or right["status"] != "extracted":
        return result
    result["parser_route"] = right["parser_route"]
    if left["parser_route"] != right["parser_route"] or left["coverage"]["context_sha256"] != right["coverage"]["context_sha256"]:
        result["reason"] = "source_scope_or_unparsed_context_changed"
        return result
    identity = lambda value: [(row["assertion_key"], row["source_native_subject"], row["predicate"], row["formulation"]) for row in value["assertions"]]
    if identity(left) != identity(right):
        result["reason"] = "added_removed_or_ambiguous_assertion_alignment"
        return result
    for old, new in zip(left["assertions"], right["assertions"]):
        result["assertions"].append({
            "assertion_key": old["assertion_key"], "source_native_subject": old["source_native_subject"],
            "predicate": old["predicate"], "formulation": old["formulation"],
            "status": "source_assertion_literal_changed" if old["literal"] != new["literal"] else "unchanged",
            "before": old, "after": new})
    changed = any(row["status"] == "source_assertion_literal_changed" for row in result["assertions"])
    result.update(result="revision_candidate" if changed else "no_candidate",
                  reason="aligned_source_assertion_literal_changed" if changed else "recognized_source_assertions_unchanged")
    return result
