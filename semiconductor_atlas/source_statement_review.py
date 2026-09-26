"""Evidence-bound annotations across a frozen source census, not alert performance."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from . import curated_observation_population as population
from . import curated_capture as capture, discovery_handoff as binding
from .ai_critical_changes import _pretty_bytes, _strict_json
from .ai_critical import _normalized_evidence_text
from .source_checks import _read


LABEL_FORMAT = "semiconductor-atlas-source-statement-labels-v1"
REPORT_FORMAT = "semiconductor-atlas-source-statement-report-v1"
RULE_VERSION = "retained-source-native-statement-review-v1"
DISPOSITIONS = {"reviewed_targets", "no_relevant_scoped_target", "uncomparable", "unresolved"}
VERDICTS = {"no_revision", "revision", "unresolved"}
BOUNDARIES = {**population.BOUNDARIES, "canonical_identity_verified": False,
    "reviewer_identity_authenticated": False, "raw_redistribution": False}
_now, _instant, _hash = capture._now, capture._instant, population._hash


def _code_hashes() -> dict:
    root = Path(__file__).parent.parent
    paths = (Path(__file__), root / "scripts/review_source_statements.py")
    return {**population._code_hashes(), **{
        path.relative_to(root).as_posix(): _hash(_read(path)) for path in paths}}


def _array(value: object, context: str, *, minimum: int = 0, maximum: int = 10_000) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{context} must be a bounded array")
    return value


def _text(value: object) -> str:
    value = capture._text(value)
    if len(value) > 10_000:
        raise ValueError("review text exceeds 10000 characters")
    return value


def _cases(frozen: dict) -> dict:
    cases = {row["observation_id"]: {"case_id": row["observation_id"], "kind": "document_check",
        **row} for row in frozen["observations"] if row["window_membership"] == "in_window"}
    for row in frozen["incomplete_at_cutoff"]:
        for document in row["planned_documents"]:
            identity = {"capture_path": row["capture_path"], "document_id": document["id"],
                        "intent_started_at": row["intent_started_at"]}
            identifier = _hash({"incomplete_at_cutoff": identity})
            if identifier in cases:
                raise ValueError("duplicate incomplete document case")
            cases[identifier] = {"case_id": identifier, "kind": "incomplete_document", **identity,
                "url": document["url"], "scope": document["scope"],
                "status": "not_completed_by_cutoff", "comparison_eligible": False,
                "body_path": None, "body_sha256": None, "predecessor": None}
    return dict(sorted(cases.items()))


def _body(case: dict, side: str, root: Path, frozen: dict, inputs: dict) -> tuple[bytes, dict]:
    source = case if side == "after" else case["predecessor"]
    if not source or not source["body_path"] or not source["body_sha256"]:
        raise ValueError("evidence side has no retained successful body")
    relative = source["body_path"]
    expected = frozen["snapshot"]["files"].get(relative)
    path = population._path(root, relative)
    raw = _read(path)
    if (expected != {"bytes": len(raw), "sha256": _hash(raw)}
            or source["body_sha256"] != _hash(raw)):
        raise ValueError("evidence body differs from its frozen observation")
    if path in inputs and inputs[path] != raw:
        raise ValueError("evidence body changed during annotation")
    inputs[path] = raw
    return raw, {"body_path": relative, "body_sha256": source["body_sha256"]}


@lru_cache(maxsize=8)
def _visible_parts(raw: bytes) -> tuple:
    text = raw.decode("utf-8", errors="strict")
    # HTMLParser counts only LF as a line boundary.
    lines = text.split("\n")
    starts, position = [], 0
    for line in lines:
        starts.append(position)
        position += len(line.encode("utf-8")) + 1

    class VisibleRanges(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.hidden = 0
            self.parts = []
            self.entities = []

        def handle_starttag(self, tag, attrs):
            if tag.casefold() in {"script", "style"}:
                self.hidden += 1

        def handle_endtag(self, tag):
            if tag.casefold() in {"script", "style"} and self.hidden:
                self.hidden -= 1

        def handle_data(self, value):
            if not self.hidden:
                line, offset = self.getpos()
                start = starts[line - 1] + len(lines[line - 1][:offset].encode("utf-8"))
                self.parts.append((start, start + len(value.encode("utf-8"))))

        def _entity(self, prefix, name):
            line, offset = self.getpos()
            start = starts[line - 1] + len(lines[line - 1][:offset].encode("utf-8"))
            value = prefix + name
            if raw[start + len(value):start + len(value) + 1] == b";":
                value += ";"
            self.entities.append((start, start + len(value)))
            self.handle_data(value)

        def handle_entityref(self, name):
            self._entity("&", name)

        def handle_charref(self, name):
            self._entity("&#", name)

    parser = VisibleRanges()
    parser.feed(text)
    parser.close()
    contiguous = []
    for start, end in parser.parts:
        if contiguous and contiguous[-1][1] == start:
            contiguous[-1] = (contiguous[-1][0], end)
        else:
            contiguous.append((start, end))
    return tuple(contiguous), tuple(parser.entities)


def _span(value: object, raw: bytes) -> str:
    span = binding._keys(value, {"start", "end", "sha256", "locator"}, "UTF-8 byte span")
    _text(span["locator"])
    if (type(span["start"]) is not int or type(span["end"]) is not int
            or not 0 <= span["start"] < span["end"] <= len(raw)):
        raise ValueError("invalid end-exclusive UTF-8 byte offsets")
    fragment = raw[span["start"]:span["end"]]
    fragment.decode("utf-8", errors="strict")
    if binding._digest(span["sha256"]) != _hash(fragment):
        raise ValueError("evidence span hash mismatch")
    parts, entities = _visible_parts(raw)
    if any(start < boundary < end for start, end in entities for boundary in (span["start"], span["end"])):
        raise ValueError("evidence span cannot split an HTML character reference")
    visible = [raw[max(start, span["start"]):min(end, span["end"])].decode("utf-8")
               for start, end in parts if start < span["end"] and end > span["start"]]
    return _normalized_evidence_text(" ".join(visible))


def _side(value: object, raw: bytes) -> tuple[str, list[str]]:
    side = binding._keys(value, {"literal", "fragment", "context"}, "target evidence side")
    literal = " ".join(_text(side["literal"]).split())
    visible = _span(side["fragment"], raw)
    if literal not in visible:
        raise ValueError("literal is not in the bound normalized evidence fragment")
    context = sorted({_span(span, raw) for span in _array(side["context"], "scope context", minimum=1, maximum=50)})
    if any(not text for text in context):
        raise ValueError("scope context must contain source text under full-body HTML normalization")
    return visible, context


def _labels(raw: bytes, frozen: dict, frozen_raw: bytes, path: Path) -> tuple[dict, dict, dict]:
    labels = binding._keys(_strict_json(raw, "source statement labels"), {
        "format", "frozen_sha256", "reviewer", "reviewed_at", "prior_exposure", "coverage_statement",
        "target_scope", "supporting_reviews", "adjudications"}, "source statement labels")
    if labels["format"] != LABEL_FORMAT or labels["frozen_sha256"] != _hash(frozen_raw):
        raise ValueError("labels must bind the exact frozen source population")
    if not _instant(frozen["frozen_at"]) < _instant(labels["reviewed_at"]) <= _instant(_now()):
        raise ValueError("review clock must follow freeze and cannot be in the future")
    for key in ("reviewer", "prior_exposure", "coverage_statement", "target_scope"):
        _text(labels[key])
    inputs, reviews = {}, {}
    for ref in _array(labels["supporting_reviews"], "supporting reviews"):
        source, review = binding._binding(path.parent, ref)
        if source in inputs or ref["sha256"] in reviews:
            raise ValueError("duplicate supporting review")
        inputs[source] = review
        reviews[ref["sha256"]] = ref
    _array(labels["adjudications"], "adjudications")
    return labels, reviews, inputs


def _review_cases(labels: dict, cases: dict, reviews: dict, root: Path, frozen: dict, inputs: dict) -> tuple[dict, list]:
    judgments, targets = {}, []
    for row in labels["adjudications"]:
        binding._keys(row, {"case_id", "disposition", "reason", "review_sha256", "locator",
            "reviewed_regions", "targets"}, "observation adjudication")
        identifier = row["case_id"]
        if not isinstance(identifier, str) or identifier not in cases or identifier in judgments:
            raise ValueError("unknown, out-of-window or duplicate case label")
        for key in ("reason", "locator"):
            _text(row[key])
        if binding._digest(row["review_sha256"]) not in reviews:
            raise ValueError("adjudication must bind a supporting review")
        disposition = _text(row["disposition"])
        if disposition not in DISPOSITIONS:
            raise ValueError("unsupported observation disposition")
        case = cases[identifier]
        eligible = case["comparison_eligible"]
        if not eligible and disposition != "uncomparable":
            raise ValueError("first, failed, blocked or incomplete cases must remain uncomparable")
        region_sides, regions = set(), {"before": [], "after": []}
        for region in _array(row["reviewed_regions"], "reviewed regions", maximum=100):
            binding._keys(region, {"side", "span"}, "reviewed region")
            if region["side"] not in {"before", "after"}:
                raise ValueError("reviewed region must name before or after")
            body, _ = _body(case, region["side"], root, frozen, inputs)
            if not _span(region["span"], body):
                raise ValueError("reviewed region has no source text under full-body HTML normalization")
            region_sides.add(region["side"])
            regions[region["side"]].append(region["span"])
        values = _array(row["targets"], "reviewed targets", maximum=1000)
        if (disposition == "reviewed_targets") != bool(values):
            raise ValueError("only reviewed_targets has a nonempty target list")
        if disposition in {"reviewed_targets", "no_relevant_scoped_target"} and region_sides != {"before", "after"}:
            raise ValueError("target review needs reviewed project regions on both actual sides")
        groups = set()
        for target in values:
            binding._keys(target, {"source_native_subject", "milestone", "formulation", "scope",
                "verdict", "reason", "before", "after"}, "source-native target pair")
            for key in ("source_native_subject", "milestone", "formulation", "scope", "reason"):
                _text(target[key])
                if key != "reason" and target[key] != " ".join(target[key].split()):
                    raise ValueError("target grouping fields require canonical whitespace")
            if _text(target["verdict"]) not in VERDICTS:
                raise ValueError("unsupported target verdict")
            group = {"url": case["url"], **{key: target[key] for key in (
                "source_native_subject", "milestone", "formulation", "scope")}}
            group_id = _hash(group)
            if group_id in groups:
                raise ValueError("duplicate target formulation within a case")
            groups.add(group_id)
            bindings, normalized, full_text = {}, {}, {}
            for side in ("before", "after"):
                body, bindings[side] = _body(case, side, root, frozen, inputs)
                normalized[side] = _side(target[side], body)
                for span in [target[side]["fragment"], *target[side]["context"]]:
                    if not any(region["start"] <= span["start"] < span["end"] <= region["end"]
                               for region in regions[side]):
                        raise ValueError("target evidence must be contained in its side's reviewed regions")
                full_text[side] = capture.normalized_bytes(body, "html_visible_text_v1")
            if target["verdict"] == "revision" and (normalized["before"] == normalized["after"]
                    or full_text["before"] == full_text["after"]):
                raise ValueError("identical bound target and context text cannot establish a revision")
            evidence_id = _hash({"normalized_evidence": normalized})
            targets.append({"case_id": identifier, "group_id": group_id,
                "evidence_pair_id": evidence_id, **group, **target, "body_bindings": bindings})
        judgments[identifier] = row
    return judgments, sorted(targets, key=lambda row: (row["case_id"], row["group_id"]))


def review(frozen_path: str | Path, labels_path: str | Path, *, reference_root: str | Path) -> dict:
    """Replay frozen sources and bind reviewer assertions without accepting claims or scoring alerts."""
    codes = _code_hashes()
    frozen_path, labels_path = Path(frozen_path).absolute(), Path(labels_path).absolute()
    root = Path(reference_root).resolve()
    frozen_raw, raw = binding._bounded_read(frozen_path), binding._bounded_read(labels_path)
    verified = population.verify_population_sources(frozen_path, reference_root=root)
    if verified["frozen_sha256"] != _hash(frozen_raw):
        raise ValueError("frozen source population changed before review")
    frozen = _strict_json(frozen_raw, "frozen source population")
    labels, reviews, inputs = _labels(raw, frozen, frozen_raw, labels_path)
    cases = _cases(frozen)
    judgments, targets = _review_cases(labels, cases, reviews, root, frozen, inputs)
    dispositions = Counter(judgments[key]["disposition"] if key in judgments else "unlabelled" for key in cases)
    group_counts = Counter(row["group_id"] for row in targets)
    pair_counts = Counter(row["evidence_pair_id"] for row in targets)
    groups = []
    for identifier in sorted(group_counts):
        members = [row for row in targets if row["group_id"] == identifier]
        groups.append({"group_id": identifier, **{key: members[0][key] for key in (
            "url", "source_native_subject", "milestone", "formulation", "scope")},
            "case_ids": [row["case_id"] for row in members],
            "pair_count": len(members),
            "unique_normalized_evidence_pairs": len({row["evidence_pair_id"] for row in members})})
    case_rows = [{**case, "adjudication": judgments.get(key),
        "disposition": judgments[key]["disposition"] if key in judgments else "unlabelled"}
        for key, case in cases.items()]
    report = {"format": REPORT_FORMAT, "rule_version": RULE_VERSION,
        "frozen_sha256": _hash(frozen_raw), "labels_sha256": _hash(raw), "frozen_at": frozen["frozen_at"],
        **{key: labels[key] for key in ("reviewer", "reviewed_at", "prior_exposure", "coverage_statement", "target_scope")},
        "design": "retrospective_source_statement_annotations_not_independent_or_blind",
        "population_scope": frozen["scope"], "source_verification": verified,
        "counts": {"document_checks": sum(case["kind"] == "document_check" for case in cases.values()),
            "incomplete_document_cases": sum(case["kind"] == "incomplete_document" for case in cases.values()),
            "total_cases": len(cases), "labelled_cases": len(judgments),
            "unlabelled_cases": len(cases) - len(judgments),
            "collector_comparable_cases": sum(case["comparison_eligible"] for case in cases.values()),
            "dispositions": dict(sorted(dispositions.items())), "target_pairs": len(targets),
            "target_verdicts": dict(sorted(Counter(row["verdict"] for row in targets).items())),
            "unique_url_subject_milestone_formulation_scope_groups": len(group_counts),
            "repeated_group_observations": len(targets) - len(group_counts),
            "unique_normalized_evidence_pairs": len(pair_counts),
            "repeated_normalized_evidence_pairs": len(targets) - len(pair_counts)},
        "coverage": {"numerator": len(judgments), "denominator": len(cases),
                     "value": len(judgments) / len(cases) if cases else None},
        "metrics": dict.fromkeys(("alert_precision", "detection_recall", "false_positive_burden",
            "publication_detection_lag", "forecast_calibration")),
        "cases": case_rows, "targets": targets, "groups": groups,
        "supporting_reviews": labels["supporting_reviews"], "code_sha256": codes,
        "boundaries": BOUNDARIES.copy(),
        "limitations": [
            "Coverage is review accounting over declared retained sources, not publisher or target completeness.",
            "Source-native subjects are reviewer descriptions, never canonical facility identities.",
            "Exact bodies, UTF-8 spans and literals are verified; semantic judgments and reviewer identity are not authenticated.",
            "Locally recorded post-freeze review clocks and exposure disclosures are not independent attestations.",
            "Calendar wording is preserved literally; deadlines are not exact dates, intervals or forecast distributions.",
            "No-target findings apply only to the declared target scope and reviewed regions, not physical no-change.",
            "First, failed, blocked and incomplete checks cannot become no-revision negatives through later evidence.",
            "Repeated tests or textual formulations are not independent confirmations or distinct physical events.",
            "No accepted-claim or alert joins are made; earlier manually acquired comparisons are not collector detections or misses.",
            "Replay requires frozen inventory, labels, supporting reviews, retained original sources and pinned code."]}
    if len(_pretty_bytes(report)) > population.MAX_BYTES:
        raise ValueError("complete statement review exceeds 20 MB; never truncate")
    final_verification = population.verify_population_sources(frozen_path, reference_root=root)
    if final_verification != verified:
        raise ValueError("frozen source verification changed across annotation")
    for path, expected in [(frozen_path, frozen_raw), (labels_path, raw), *inputs.items()]:
        if _read(path) != expected:
            raise ValueError("statement review inputs changed across annotation")
    if codes != _code_hashes():
        raise ValueError("statement review code changed across annotation")
    return report
