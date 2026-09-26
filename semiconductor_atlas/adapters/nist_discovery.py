"""Pure, fail-closed extraction of links from bounded NIST CHIPS indexes.

These records describe the index, not the linked document or a facility claim.
No URL is fetched here. Visibility is limited to explicit HTML hiding markers;
this parser does not execute scripts or evaluate external stylesheets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import urljoin


_ORIGIN = "https://www.nist.gov"
_ROOTS = {
    "news": "/chips/chips-news-releases",
    "awards": "/chips/chips-program-office-awards",
}
_TITLES = {"news": "CHIPS News & Releases", "awards": "CHIPS Program Office Awards"}
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_HIDDEN_TAGS = frozenset({"script", "style", "template", "noscript"})
_HIDDEN_CLASSES = frozenset({"hidden", "visually-hidden", "sr-only", "display-none", "d-none"})


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)
    hidden: bool = False

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def descendants(self):
        for child in self.children:
            if isinstance(child, _Node) and not child.hidden:
                yield child
                yield from child.descendants()

    def text(self) -> str:
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif not child.hidden:
                parts.append(child.text())
        return " ".join(" ".join(parts).replace("\xa0", " ").split())


def _hidden(tag: str, attrs: dict[str, str | None]) -> bool:
    style = re.sub(r"\s+", "", attrs.get("style") or "").lower()
    return (
        tag in _HIDDEN_TAGS
        or "hidden" in attrs
        or (attrs.get("aria-hidden") or "").lower() == "true"
        or bool(set((attrs.get("class") or "").split()) & _HIDDEN_CLASSES)
        or bool(re.search(r"(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)", style))
    )


def _record(node: _Node) -> bool:
    return ((node.tag == "article" and node.has_class("nist-teaser"))
            or (node.tag == "div" and node.has_class("margin-top-3"))
            or (node.tag == "nav" and node.has_class("pager")))


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.opened_outer: set[str] = set()
        self.closed_outer: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"html", "body"}:
            parent = "document" if tag == "html" else "html"
            if tag in self.opened_outer or self.stack[-1].tag != parent:
                raise ValueError("malformed outer index document")
            self.opened_outer.add(tag)
        attributes = dict(attrs)
        node = _Node(tag, attributes, hidden=self.stack[-1].hidden or _hidden(tag, attributes))
        if not node.hidden and (_record(node) or any(_record(item) for item in self.stack)):
            if len(attributes) != len(attrs):
                raise ValueError("duplicate attribute in index structure")
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if tag in {"html", "body"}:
            raise ValueError("self-closing outer index document")
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        matching = next((i for i in range(len(self.stack) - 1, 0, -1)
                         if self.stack[i].tag == tag), None)
        if tag in {"html", "body"}:
            if (matching != len(self.stack) - 1 or tag in self.closed_outer
                    or (tag == "html" and "body" not in self.closed_outer)):
                raise ValueError("unclosed or malformed outer index document")
            self.closed_outer.add(tag)
        if matching is None:
            if any(_record(item) and not item.hidden for item in self.stack):
                raise ValueError("unmatched closing tag in index structure")
            return
        if matching != len(self.stack) - 1:
            if any(_record(item) and not item.hidden for item in self.stack):
                raise ValueError("malformed nesting in index structure")
        del self.stack[matching:]

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def close(self):
        super().close()
        if any(_record(item) and not item.hidden for item in self.stack):
            raise ValueError("unclosed index structure")
        if self.opened_outer != {"html", "body"} or self.closed_outer != {"html", "body"}:
            raise ValueError("incomplete outer index document")


def _one(nodes: list[_Node], label: str) -> _Node:
    if len(nodes) != 1:
        raise ValueError(f"expected exactly one {label}")
    return nodes[0]


def _field(node: _Node, class_name: str) -> _Node:
    return _one([x for x in node.descendants() if x.has_class(class_name)], class_name)


def _nonempty(value: str | None, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {label}")
    return value


def _page_number(url: str, kind: str, *, zero_alias: bool = False) -> int:
    root = _ORIGIN + _ROOTS[kind]
    if url == root:
        return 0
    match = re.fullmatch(re.escape(root) + r"\?page=([1-9][0-9]*|0)", url)
    if match and (zero_alias or match[1] != "0"):
        return int(match[1])
    raise ValueError("noncanonical NIST index URL")


def _document_url(value: str | None, kind: str) -> str:
    value = _nonempty(value, "document URL")
    prefix = r"/news-events/news/[0-9]{4}/(?:0[1-9]|1[0-2])/" if kind == "news" else r"/chips/"
    # Deliberately reject normalization ambiguity, credentials, ports, encoded
    # paths, fragments, scheme-relative URLs, queries and external origins.
    match = re.fullmatch(r"(?:https://www\.nist\.gov)?(" + prefix + r"[a-z0-9]+(?:-[a-z0-9]+)*)", value)
    if not match or match[1] in _ROOTS.values():
        raise ValueError("unsafe or unrecognized NIST document URL")
    return _ORIGIN + match[1]


def _news(node: _Node) -> dict:
    title = _field(node, "nist-teaser__title")
    if title.tag != "h3":
        raise ValueError("unrecognized news title structure")
    links = [x for x in title.descendants() if x.tag == "a" and not x.has_class("auto-anchor")]
    link = _one(links, "news title link")
    url = _document_url(link.attrs.get("href"), "news")
    if _document_url(node.attrs.get("about"), "news") != url:
        raise ValueError("news title/about URL disagreement")
    date_node = _field(node, "nist-teaser__date")
    time = _one([x for x in date_node.descendants() if x.tag == "time"], "news index date")
    index_date = _nonempty(time.attrs.get("datetime"), "news datetime")
    if index_date != index_date.strip():
        raise ValueError("noncanonical news datetime")
    try:
        datetime.fromisoformat(index_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid news datetime") from error
    _nonempty(time.text(), "visible news date")
    content = _field(node, "nist-teaser__content")
    summary = _one([x for x in content.descendants() if x.attrs.get("property") == "schema:text"], "news summary")
    return {"url": url, "title": _nonempty(link.text(), "news title"),
            "index_date": index_date, "summary": _nonempty(summary.text(), "news summary"),
            "source_native_scope": None}


def _award(node: _Node) -> tuple[dict, bool]:
    title = _field(node, "views-field-title")
    link = _one([x for x in title.descendants() if x.tag == "a"], "award title link")
    organization = _field(node, "views-field-field-chipfund-chips-org")
    office = _field(organization, "nist-field__item").text()
    _nonempty(office, "award organization")
    locality = _field(node, "views-field-field-chipfund-location-locality").text().lstrip("–- ").rstrip(", ")
    region = _field(node, "views-field-field-chipfund-location-administrative-area").text()
    record = {"url": _document_url(link.attrs.get("href"), "awards"),
              "title": _nonempty(link.text(), "award title"), "index_date": None,
              "summary": _nonempty(_field(node, "views-field-body").text(), "award summary"),
              "source_native_scope": {"locality": _nonempty(locality, "award locality"),
                                      "region": _nonempty(region, "award region")}}
    return record, office == "CHIPS Program Office"


def _next_url(nodes: list[_Node], page_url: str, kind: str, page: int) -> str | None:
    pagers = [x for x in nodes if x.tag == "nav" and x.has_class("pager")]
    if not pagers:
        if page:
            raise ValueError("missing pager for noninitial index page")
        return None
    pager = _one(pagers, "index pager")
    links = [x for x in pager.descendants() if x.tag == "a"]
    page_numbers = [
        _page_number(_pager_url(link.attrs.get("href"), page_url, kind), kind, zero_alias=True)
        for link in links
    ]
    current = _one([x for x in links if x.attrs.get("aria-current") == "page"], "current page marker")
    current_url = _pager_url(current.attrs.get("href"), page_url, kind)
    if _page_number(current_url, kind, zero_alias=True) != page:
        raise ValueError("pager current page disagrees with requested URL")
    following = [x for x in links if "next" in (x.attrs.get("rel") or "").lower().split()]
    if len(following) > 1:
        raise ValueError("multiple next-page links")
    if not following:
        if any(number > page for number in page_numbers):
            raise ValueError("future page links require a consistent next-page link")
        return None
    result = _pager_url(following[0].attrs.get("href"), page_url, kind)
    if _page_number(result, kind) != page + 1:
        raise ValueError("nonsequential next page URL")
    return result


def _pager_url(href: str | None, page_url: str, kind: str) -> str:
    href = _nonempty(href, "pager URL")
    allowed = (r"(?:https://www\.nist\.gov)?" + re.escape(_ROOTS[kind]))
    if not re.fullmatch(r"(?:" + allowed + r")?\?page=(?:0|[1-9][0-9]*)", href):
        raise ValueError("unsafe or noncanonical pager URL")
    return urljoin(page_url, href)


def parse_index(raw: bytes, *, page_url: str, kind: str) -> dict:
    """Extract index observations; raise ``ValueError`` on ambiguous structures.

    ``index_date`` preserves a news teaser's publisher datetime verbatim;
    awards have no per-card date. Exact duplicate observations are coalesced,
    while disagreement for a URL is rejected. ``excluded_count`` counts visible
    non-CPO award cards, not hidden content or unrecognized links.
    """
    if kind not in _ROOTS:
        raise ValueError("unknown NIST index kind")
    if not isinstance(raw, bytes):
        raise ValueError("index input must be bytes")
    if not isinstance(page_url, str):
        raise ValueError("index page URL must be a string")
    page = _page_number(page_url, kind)
    tree = _Tree()
    try:
        tree.feed(raw.decode("utf-8-sig", errors="strict"))
    except UnicodeDecodeError as error:
        raise ValueError("index input is not UTF-8") from error
    tree.close()
    nodes = list(tree.root.descendants())
    heading = _one([x for x in nodes if x.tag == "h1"], "index heading")
    if heading.text() != _TITLES[kind]:
        raise ValueError("index heading does not match requested kind")
    if kind == "news":
        records = [x for x in nodes if x.tag == "article" and x.has_class("nist-teaser")]
    else:
        records = [x for x in nodes if x.tag == "div" and x.has_class("margin-top-3")]
    if not records:
        raise ValueError("no recognized visible index records")
    entries = {}
    observations = {}
    excluded = 0
    for node in records:
        if any(_record(x) for x in node.descendants()):
            raise ValueError("nested index record")
        entry, included = (_news(node), True) if kind == "news" else _award(node)
        identity = entry["url"]
        observation = (entry, included)
        if identity in observations and observations[identity] != observation:
            raise ValueError("conflicting duplicate document URL")
        observations[identity] = observation
        if included:
            entries[identity] = entry
        else:
            excluded += 1
    return {"entries": list(entries.values()), "next_url": _next_url(nodes, page_url, kind, page),
            "entry_count": len(entries), "excluded_count": excluded, "record_count": len(records),
            "duplicate_count": len(records) - len(observations)}
