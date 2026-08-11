"""Import archived NIST CHIPS award pages into the provenance claim store."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import parse_qs, urlsplit

from .adapters.nist_awards import (
    NIST_AWARDS_URL,
    AwardRecord,
    classify_facility_activities,
    extract_capabilities,
    parse_awards_html,
)
from .adapters.nist_details import NISTProjectDetail, TextDisclosure, parse_detail_file
from .models import (
    CapacityBasis,
    CapacityValue,
    CapabilityValue,
    ClaimKind,
    ClaimSeries,
    ClaimValue,
    ClaimVersion,
    ConstraintSeverity,
    ConstraintStatus,
    ConstraintValue,
    DependencyKind,
    DependencyLink,
    Entity,
    EntityKind,
    EvidenceLink,
    IngestionRun,
    IngestionRunDocument,
    IngestionStatus,
    MilestoneStatus,
    MilestoneValue,
    RelationshipValue,
    ScalarType,
    ScalarValue,
    Source,
    SourceDocument,
    SourceFamily,
    SourceRecord,
)
from .repository import (
    add_claim_series,
    add_entity,
    add_ingestion_run,
    add_ingestion_run_document,
    add_source,
    add_source_document,
    add_source_family,
    add_source_record,
    insert_claim,
    stable_id,
    value_sha256,
)


NIST_SOURCE_CREATED_AT = "2026-07-17T00:00:00Z"
NIST_REUSE_NOTICE = (
    "NIST public information unless marked otherwise; credit requested; "
    "marked and third-party exceptions excluded"
)
NIST_COPYRIGHT_NOTICE_URL = "https://www.nist.gov/copyrights-disclaimers"
NIST_IMPORT_VERSION = "nist-awards-import-v3"

_SOURCE_FAMILY_KEY = "us-official-semiconductor-awards"
_SOURCE_KEY = "nist-chips-for-america-awards"
_IMPORT_SAVEPOINTS = itertools.count()
_INDEX_PAGE_RE = re.compile(r"page[-_=]?(\d+)", re.IGNORECASE)
_MONEY_RE = re.compile(
    r"(?:(nearly|approximately|about|more than|over|at least|up to)\s+)?"
    r"\$([\d,]+(?:\.\d+)?)(\+)?\s*(thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)
_SPLIT_ACROSS_SCOPE_RE = re.compile(
    r"\(\s*(split\s+across\b[^()]*)\s*\)",
    re.IGNORECASE,
)
_CLEANROOM_RE = re.compile(
    r"\b(?:(over|approximately|about|up to)\s+)?([\d,]+)\s+"
    r"square feet of cleanrooms?\b",
    re.IGNORECASE,
)
_CAPACITY_PERCENT_RE = re.compile(
    r"\b(?:expected to|would|will|target(?:ed)? to)\s+increase\b"
    r"[^.\n]{0,100}?\bproduction capacity by\s+([\d,]+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_CAPACITY_THROUGHPUT_RE = re.compile(
    r"\b(?:expected|anticipated|planned|would|will)\b[^.\n]{0,120}?"
    r"\bcapacity of\s+([\d,]+(?:\.\d+)?)\s*(thousand|million|billion)?\s+"
    r"(wafers|chips)\s+(?:per|each|every)\s+(day|month|year)\b",
    re.IGNORECASE,
)
_FORWARD_THROUGHPUT_RE = re.compile(
    r"\b(?:approximately|about|up to)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(thousand|million|billion)?\s+(wafers|chips|units)\s+"
    r"(?:per|each|every)\s+(day|month|year)\b",
    re.IGNORECASE,
)
_MULTIPLIERS = {
    None: Decimal(1),
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
    "trillion": Decimal(1_000_000_000_000),
}
_US_STATES = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "district of columbia": "dc",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}


@dataclass(frozen=True, slots=True)
class NISTImportResult:
    run_id: str
    source_id: str
    source_document_ids: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    organization_ids: tuple[str, ...]
    site_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    claim_version_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DocumentInput:
    id: str
    path: Path
    raw: bytes
    url: str
    title: str
    content_sha256: str
    record_type: str
    published_at: str | None = None
    updated_at: str | None = None
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class _IndexInput:
    document: _DocumentInput
    record: AwardRecord


@dataclass(frozen=True, slots=True)
class _DetailInput:
    document: _DocumentInput
    detail: NISTProjectDetail


@dataclass(frozen=True, slots=True)
class _MonetaryGroup:
    anchor_url: str
    canonical_url: str
    member_urls: tuple[str, ...]
    existing_entity_id: str | None = None


@dataclass(slots=True)
class _ImportContext:
    connection: sqlite3.Connection
    run_id: str
    retrieved_at: str
    as_of_date: str
    source_record_ids: set[str]
    organization_ids: set[str]
    site_ids: set[str]
    project_ids: set[str]
    claim_version_ids: set[str]
    recipient_display_names: dict[str, str]
    index_site_locations: dict[str, tuple[tuple[str, str], ...]]
    monetary_groups: dict[tuple[str, str, int, str], _MonetaryGroup]
    index_amounts_by_url: dict[str, int]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _normalized_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _entity_stable_key(kind: EntityKind, *identity: str) -> str:
    fingerprint = stable_id("nist-entity-key", kind.value, *identity)
    return f"nist:{kind.value}:{fingerprint}"


def _site_location_identity(location: str) -> str:
    pieces = [piece.strip() for piece in location.rsplit(",", 1)]
    if len(pieces) == 2:
        locality, region = pieces
        normalized_region = _normalized_identity(region)
        region_key = _US_STATES.get(normalized_region, normalized_region)
        return f"{_normalized_identity(locality)}|{region_key}"
    return _normalized_identity(location)


def _index_location_identity(locality: str, region: str) -> str:
    region_key = _US_STATES.get(_normalized_identity(region), _normalized_identity(region))
    return f"{_normalized_identity(locality)}|{region_key}"


def _site_display_name(locality: str, region: str) -> str:
    normalized_region = _normalized_identity(region)
    region_display = _US_STATES.get(normalized_region, region.strip())
    if len(region_display) == 2:
        region_display = region_display.upper()
    return f"{locality.strip()}, {region_display}"


def _location_display_name(location: str) -> str:
    pieces = [piece.strip() for piece in location.rsplit(",", 1)]
    if len(pieces) == 2:
        return _site_display_name(pieces[0], pieces[1])
    return location.strip()


def _canonical_detail_site(
    context: _ImportContext,
    recipient_identity: str,
    location: str,
) -> tuple[str, str]:
    pieces = [piece.strip() for piece in location.rsplit(",", 1)]
    if len(pieces) != 2:
        return _site_location_identity(location), _location_display_name(location)
    locality, region = pieces
    normalized_locality = _normalized_identity(locality)
    normalized_region = _US_STATES.get(
        _normalized_identity(region),
        _normalized_identity(region),
    )
    candidates = [
        candidate
        for candidate in context.index_site_locations.get(recipient_identity, ())
        if _US_STATES.get(
            _normalized_identity(candidate[1]),
            _normalized_identity(candidate[1]),
        )
        == normalized_region
    ]
    ranked = sorted(
        (
            SequenceMatcher(
                None,
                normalized_locality,
                _normalized_identity(candidate_locality),
            ).ratio(),
            candidate_locality,
            candidate_region,
        )
        for candidate_locality, candidate_region in candidates
    )
    if ranked:
        best_score, best_locality, best_region = ranked[-1]
        next_score = ranked[-2][0] if len(ranked) > 1 else 0.0
        if best_score >= 0.9 and best_score - next_score >= 0.05:
            return (
                _index_location_identity(best_locality, best_region),
                _site_display_name(best_locality, best_region),
            )
    return _site_location_identity(location), _location_display_name(location)


def _one_second_later(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    completed = (parsed + timedelta(seconds=1)).isoformat()
    if completed.endswith("+00:00"):
        return completed[:-6] + "Z"
    return completed


NISTSourceInput = str | Path | tuple[str | Path, str]


def _source_input(value: NISTSourceInput) -> tuple[Path, str | None]:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("a NIST source input tuple must contain path and URL")
        path, url = value
        if not isinstance(url, str) or not url:
            raise ValueError("a NIST source input URL must be a non-empty string")
        return Path(path), url
    return Path(value), None


def _nist_index_page(url: str) -> int:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "www.nist.gov"
        or parsed.path.rstrip("/") != "/chips/chips-america-awards"
        or parsed.fragment
    ):
        raise ValueError(f"unexpected NIST award index URL: {url}")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not query:
        return 0
    if set(query) != {"page"} or len(query["page"]) != 1:
        raise ValueError(f"unexpected NIST award index query: {url}")
    try:
        page = int(query["page"][0])
    except ValueError as error:
        raise ValueError(f"invalid NIST award index page URL: {url}") from error
    if page < 0 or str(page) != query["page"][0]:
        raise ValueError(f"invalid NIST award index page URL: {url}")
    return page


def _ordered_index_paths(paths: Iterable[NISTSourceInput]) -> list[tuple[int, Path, str]]:
    ordered: list[tuple[int, int, Path, str]] = []
    used_pages: set[int] = set()
    used_paths: set[Path] = set()
    next_fallback = 0
    for ordinal, value in enumerate(paths):
        path, authoritative_url = _source_input(value)
        resolved_path = path.resolve()
        if resolved_path in used_paths:
            raise ValueError(f"duplicate NIST index path: {path}")
        used_paths.add(resolved_path)
        if authoritative_url is not None:
            page_number = _nist_index_page(authoritative_url)
        else:
            match = _INDEX_PAGE_RE.search(path.stem)
            if match:
                page_number = int(match.group(1))
            else:
                while next_fallback in used_pages:
                    next_fallback += 1
                page_number = next_fallback
                next_fallback += 1
            authoritative_url = (
                NIST_AWARDS_URL
                if page_number == 0
                else f"{NIST_AWARDS_URL}?page={page_number}"
            )
        if page_number in used_pages:
            raise ValueError(f"duplicate NIST index page number: {page_number}")
        used_pages.add(page_number)
        ordered.append((page_number, ordinal, path, authoritative_url))
    return [(page, path, url) for page, _, path, url in sorted(ordered)]


def _reuse_metadata(record_type: str) -> dict[str, object]:
    return {
        "access_class": "public",
        "attribution": "National Institute of Standards and Technology",
        "copyright_notice_url": NIST_COPYRIGHT_NOTICE_URL,
        "record_type": record_type,
        "reuse_notice": NIST_REUSE_NOTICE,
        "reuse_status": "public_information_unless_marked_otherwise",
    }


def _prepare_inputs(
    index_paths: Iterable[NISTSourceInput],
    detail_paths: Iterable[NISTSourceInput],
    retrieved_at: str,
    source_id: str,
) -> tuple[list[_DocumentInput], list[_IndexInput], dict[str, _DetailInput]]:
    documents: list[_DocumentInput] = []
    index_records: list[_IndexInput] = []
    seen_index_urls: set[str] = set()

    for page_number, path, page_url in _ordered_index_paths(index_paths):
        raw = path.read_bytes()
        digest = _sha256(raw)
        document = _DocumentInput(
            id=stable_id("source-document", source_id, page_url, retrieved_at, digest),
            path=path,
            raw=raw,
            url=page_url,
            title=f"NIST CHIPS for America awards page {page_number}",
            content_sha256=digest,
            record_type="award_index_page",
            page_number=page_number,
        )
        documents.append(document)
        parsed_records = parse_awards_html(raw, page_url=page_url)
        for record in parsed_records:
            if record.chips_organization != "CHIPS Program Office":
                continue
            if record.source_url in seen_index_urls:
                continue
            seen_index_urls.add(record.source_url)
            index_records.append(_IndexInput(document, record))

    if not documents:
        raise ValueError("at least one NIST award index path is required")
    if not index_records:
        raise ValueError("NIST award index inputs contain no CHIPS Program Office records")

    details: dict[str, _DetailInput] = {}
    unpacked_details = [_source_input(value) for value in detail_paths]
    seen_detail_paths: set[Path] = set()
    for path, authoritative_url in sorted(unpacked_details, key=lambda item: str(item[0])):
        resolved_path = path.resolve()
        if resolved_path in seen_detail_paths:
            raise ValueError(f"duplicate NIST detail path: {path}")
        seen_detail_paths.add(resolved_path)
        detail = parse_detail_file(path)
        if not detail.canonical_url:
            raise ValueError(f"NIST detail page has no canonical URL: {path}")
        if authoritative_url is not None and detail.canonical_url != authoritative_url:
            raise ValueError(
                "NIST detail canonical URL does not match the snapshot manifest: "
                f"{path} ({detail.canonical_url!r} != {authoritative_url!r})"
            )
        if not any(
            (
                detail.recipients,
                detail.locations,
                detail.direct_funding,
                detail.expected_capex,
                detail.jobs,
                detail.project_description,
                detail.capabilities,
            )
        ):
            raise ValueError(f"NIST detail layout produced no substantive fields: {path}")
        if detail.canonical_url in details:
            raise ValueError(f"duplicate NIST detail canonical URL: {detail.canonical_url}")
        raw = path.read_bytes()
        digest = _sha256(raw)
        document = _DocumentInput(
            id=stable_id(
                "source-document",
                source_id,
                detail.canonical_url,
                retrieved_at,
                digest,
            ),
            path=path,
            raw=raw,
            url=detail.canonical_url,
            title=detail.title or "NIST CHIPS project detail",
            content_sha256=digest,
            record_type="award_detail_page",
            published_at=detail.published_at,
            updated_at=detail.updated_at,
        )
        documents.append(document)
        details[detail.canonical_url] = _DetailInput(document, detail)

    if unpacked_details:
        expected_detail_urls = {item.record.source_url for item in index_records}
        actual_detail_urls = set(details)
        missing = sorted(expected_detail_urls - actual_detail_urls)
        extra = sorted(actual_detail_urls - expected_detail_urls)
        if missing or extra:
            raise ValueError(
                "NIST detail inputs must exactly match parsed CHIPS Program Office records; "
                f"missing={missing!r}, extra={extra!r}"
            )

    index_records.sort(key=lambda item: item.record.source_url)
    documents.sort(key=lambda item: (item.url, item.content_sha256))
    return documents, index_records, details


def _source_family_and_source(connection: sqlite3.Connection) -> tuple[str, str]:
    family_id = stable_id("source-family", _SOURCE_FAMILY_KEY)
    source_id = stable_id("source", _SOURCE_KEY)
    add_source_family(
        connection,
        SourceFamily(
            family_id,
            _SOURCE_FAMILY_KEY,
            "United States official semiconductor award disclosures",
            NIST_SOURCE_CREATED_AT,
            description="Official subsidy and award statements; not proof of physical progress.",
        ),
    )
    add_source(
        connection,
        Source(
            source_id,
            family_id,
            _SOURCE_KEY,
            "NIST CHIPS for America awards",
            "National Institute of Standards and Technology",
            NIST_AWARDS_URL,
            NIST_SOURCE_CREATED_AT,
            license=NIST_REUSE_NOTICE,
        ),
    )
    return family_id, source_id


def _add_document(
    connection: sqlite3.Connection,
    source_id: str,
    document: _DocumentInput,
    retrieved_at: str,
) -> None:
    metadata = _reuse_metadata(document.record_type)
    if document.page_number is not None:
        metadata["page_number"] = document.page_number
    if document.updated_at is not None:
        metadata["updated_at"] = document.updated_at
    add_source_document(
        connection,
        SourceDocument(
            document.id,
            source_id,
            document.url,
            document.title,
            retrieved_at,
            document.content_sha256,
            published_at=document.published_at,
            media_type="text/html",
            license=NIST_REUSE_NOTICE,
            metadata=metadata,
        ),
    )


def _add_record(
    context: _ImportContext,
    document: _DocumentInput,
    source_record_key: str,
    payload: dict[str, object],
) -> str:
    record_id = stable_id("source-record", context.run_id, source_record_key)
    add_source_record(
        context.connection,
        SourceRecord(
            record_id,
            context.run_id,
            document.id,
            source_record_key,
            context.retrieved_at,
            _payload_sha256(payload),
            payload=payload,
        ),
    )
    context.source_record_ids.add(record_id)
    return record_id


def _add_entity(
    context: _ImportContext,
    kind: EntityKind,
    *identity: str,
    display_name: str | None = None,
) -> str:
    stable_key = _entity_stable_key(kind, *identity)
    entity_id = stable_id("entity", stable_key)
    existing = context.connection.execute(
        "SELECT kind, stable_key, display_name FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if existing is None:
        add_entity(
            context.connection,
            Entity(
                entity_id,
                kind,
                stable_key,
                context.retrieved_at,
                display_name=display_name,
                created_by_run_id=context.run_id,
            ),
        )
    else:
        expected = (kind.value, stable_key)
        actual = (existing["kind"], existing["stable_key"])
        if actual != expected:
            raise ValueError(
                f"NIST entity {entity_id} conflicts with its stable identity"
            )
    if kind is EntityKind.ORGANIZATION:
        context.organization_ids.add(entity_id)
    elif kind is EntityKind.SITE:
        context.site_ids.add(entity_id)
    elif kind is EntityKind.PROJECT:
        context.project_ids.add(entity_id)
    return entity_id


def _series(
    context: _ImportContext,
    subject_entity_id: str,
    predicate: str,
    source_record_key: str,
    field_key: str,
    value: ClaimValue,
) -> str:
    series_fingerprint = stable_id(
        "nist-claim-series-key",
        subject_entity_id,
        predicate,
        source_record_key,
        field_key,
    )
    stable_key = f"nist:claim:{series_fingerprint}"
    series_id = stable_id("claim-series", stable_key)
    add_claim_series(
        context.connection,
        ClaimSeries(
            series_id,
            subject_entity_id,
            stable_key,
            predicate,
            value.kind,
            NIST_SOURCE_CREATED_AT,
        ),
    )
    return series_id


def _source_claim(
    context: _ImportContext,
    subject_entity_id: str,
    predicate: str,
    value: ClaimValue,
    *,
    source_record_key: str,
    field_key: str,
    document_id: str,
    source_record_id: str,
    locator: str,
    excerpt: str,
) -> str:
    series_id = _series(
        context,
        subject_entity_id,
        predicate,
        source_record_key,
        field_key,
        value,
    )
    version_id = stable_id(
        "claim-version",
        series_id,
        context.run_id,
        context.as_of_date,
        value_sha256(value),
    )
    insert_claim(
        context.connection,
        ClaimVersion(
            version_id,
            series_id,
            context.as_of_date,
            context.retrieved_at,
            ClaimKind.SOURCE_STATEMENT,
            "nist_literal_v1",
            1.0,
            created_by_run_id=context.run_id,
        ),
        value,
        evidence=[
            EvidenceLink(
                document_id,
                source_record_id=source_record_id,
                locator=locator,
                excerpt=excerpt,
            )
        ],
    )
    context.claim_version_ids.add(version_id)
    return version_id


def _derived_claim(
    context: _ImportContext,
    subject_entity_id: str,
    predicate: str,
    value: ClaimValue,
    *,
    source_record_key: str,
    field_key: str,
    parents: Sequence[str],
    method: str,
    confidence: float = 1.0,
    notes: str | None = None,
) -> str:
    if not parents:
        raise ValueError("a derived NIST claim requires a parent source claim")
    series_id = _series(
        context,
        subject_entity_id,
        predicate,
        source_record_key,
        field_key,
        value,
    )
    version_id = stable_id(
        "claim-version",
        series_id,
        context.run_id,
        context.as_of_date,
        value_sha256(value),
    )
    insert_claim(
        context.connection,
        ClaimVersion(
            version_id,
            series_id,
            context.as_of_date,
            context.retrieved_at,
            ClaimKind.DERIVED_ESTIMATE,
            method,
            confidence,
            created_by_run_id=context.run_id,
            notes=notes,
        ),
        value,
        dependencies=[
            DependencyLink(parent, DependencyKind.TRANSFORMS)
            for parent in sorted(set(parents))
        ],
    )
    context.claim_version_ids.add(version_id)
    return version_id


def _literal_text(
    context: _ImportContext,
    subject_entity_id: str,
    predicate: str,
    source_text: str,
    *,
    source_record_key: str,
    field_key: str,
    document_id: str,
    source_record_id: str,
    locator: str,
) -> str:
    return _source_claim(
        context,
        subject_entity_id,
        predicate,
        ScalarValue(ScalarType.TEXT, source_text),
        source_record_key=source_record_key,
        field_key=field_key,
        document_id=document_id,
        source_record_id=source_record_id,
        locator=locator,
        excerpt=source_text,
    )


def _money_values(source_text: str) -> list[tuple[str, int, str]]:
    values: list[tuple[str, int, str]] = []
    for match in _MONEY_RE.finditer(source_text):
        amount = Decimal(match.group(2).replace(",", ""))
        multiplier = _MULTIPLIERS[match.group(4).casefold()]
        normalized = amount * multiplier
        if normalized == normalized.to_integral_value():
            prefix = (match.group(1) or "").casefold()
            if prefix == "up to":
                qualifier = "up_to"
            elif prefix in {"nearly", "approximately", "about"}:
                qualifier = "approximate"
            elif prefix in {"more than", "over", "at least"} or match.group(3):
                qualifier = "lower_bound"
            else:
                qualifier = "exact_reported"
            values.append((match.group(0), int(normalized), qualifier))
    return values


def _existing_award_program_entity(
    connection: sqlite3.Connection,
    member_urls: Sequence[str],
) -> str | None:
    page_entity_ids = [
        stable_id(
            "entity",
            _entity_stable_key(
                EntityKind.PROJECT,
                _normalized_identity(url),
            ),
        )
        for url in member_urls
    ]
    placeholders = ",".join("?" for _ in page_entity_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT relationships.object_entity_id
        FROM claim_series AS series
        JOIN claim_versions AS versions ON versions.series_id = series.id
        JOIN relationship_values AS relationships
          ON relationships.claim_version_id = versions.id
        WHERE series.subject_entity_id IN ({placeholders})
          AND relationships.relationship_type = 'part_of_award_program'
        ORDER BY relationships.object_entity_id
        """,
        page_entity_ids,
    ).fetchall()
    entity_ids = tuple(str(row[0]) for row in rows)
    if len(entity_ids) > 1:
        raise ValueError(
            "NIST award pages resolve to multiple historical program entities; "
            "manual award-group reconciliation is required"
        )
    return entity_ids[0] if entity_ids else None


def _shared_allocation_identity(source_text: str) -> str | None:
    match = _SPLIT_ACROSS_SCOPE_RE.search(source_text)
    if match is None:
        return None
    return _normalized_identity(match.group(1))


def _monetary_group_maps(
    connection: sqlite3.Connection,
    index_records: Sequence[_IndexInput],
    details: dict[str, _DetailInput],
) -> tuple[dict[tuple[str, str, int, str], _MonetaryGroup], dict[str, int]]:
    occurrences: dict[
        tuple[str, str, int, str, tuple[str, ...]], set[str]
    ] = {}
    shared_index_occurrences: dict[tuple[str, int, str], set[str]] = {}
    disclosure_urls: dict[tuple[str, str, int], set[str]] = {}
    overview_entries: list[tuple[str, str, int, str]] = []
    forced_entries: list[tuple[str, str, int, str]] = []
    forced_nodes: set[tuple[str, str]] = set()
    index_amounts: dict[str, int] = {}
    recipient_by_url = {
        item.record.source_url: _normalized_identity(item.record.recipient)
        for item in index_records
    }
    parents: dict[tuple[str, str], tuple[str, str]] = {}

    def register_disclosure(recipient: str, kind: str, amount: int, url: str) -> None:
        disclosure_urls.setdefault((recipient, kind, amount), set()).add(url)

    def find(node: tuple[str, str]) -> tuple[str, str]:
        parent = parents[node]
        if parent != node:
            parents[node] = find(parent)
        return parents[node]

    def union(left: tuple[str, str], right: tuple[str, str]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for item in index_records:
        amount = item.record.amount
        if amount is None:
            continue
        index_amounts[item.record.source_url] = amount.amount_usd
        if amount.allocation_scope == "multi_site_shared":
            recipient = _normalized_identity(item.record.recipient)
            node = (recipient, item.record.source_url)
            parents.setdefault(node, node)
            register_disclosure(
                recipient,
                "direct_funding",
                amount.amount_usd,
                item.record.source_url,
            )
            entry = (
                recipient,
                "direct_funding",
                amount.amount_usd,
                item.record.source_url,
            )
            forced_entries.append(entry)
            forced_nodes.add(node)
            shared_identity = _shared_allocation_identity(amount.source_text)
            if shared_identity is not None:
                shared_index_occurrences.setdefault(
                    (recipient, amount.amount_usd, shared_identity),
                    set(),
                ).add(item.record.source_url)

    for url, item in details.items():
        recipient = recipient_by_url.get(url)
        if recipient is None and item.detail.recipients:
            recipient = _normalized_identity(item.detail.recipients[0])
        if recipient is None:
            continue
        for disclosure_kind, disclosures in (
            ("direct_funding", item.detail.direct_funding),
            ("expected_capex", item.detail.expected_capex),
        ):
            for disclosure in disclosures:
                if "project overview" not in (disclosure.scope or "").casefold():
                    continue
                values = _money_values(disclosure.source_text)
                if values:
                    scope_identity = _normalized_identity(disclosure.scope or "")
                    location_identity = tuple(
                        sorted(
                            {
                                _site_location_identity(location)
                                for location in item.detail.locations
                            }
                        )
                    )
                    register_disclosure(recipient, disclosure_kind, values[0][1], url)
                    node = (recipient, url)
                    parents.setdefault(node, node)
                    if location_identity:
                        key = (
                            recipient,
                            disclosure_kind,
                            values[0][1],
                            scope_identity,
                            location_identity,
                        )
                        occurrences.setdefault(key, set()).add(url)
                    overview_entries.append((recipient, disclosure_kind, values[0][1], url))

    for (recipient, _amount, _shared_identity), urls in shared_index_occurrences.items():
        ordered_urls = sorted(urls)
        for url in ordered_urls[1:]:
            union((recipient, ordered_urls[0]), (recipient, url))

    for occurrence_key, urls in occurrences.items():
        recipient = occurrence_key[0]
        ordered_urls = sorted(urls)
        for url in ordered_urls[1:]:
            union((recipient, ordered_urls[0]), (recipient, url))

    components: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for node in parents:
        components.setdefault(find(node), set()).add(node)
    grouped_components = {
        root: nodes
        for root, nodes in components.items()
        if len(nodes) > 1 or bool(nodes & forced_nodes)
    }
    component_for_node = {
        node: root for root, nodes in grouped_components.items() for node in nodes
    }
    component_metadata: dict[
        tuple[str, str], tuple[str, tuple[str, ...], str | None]
    ] = {}
    reused_entity_ids: dict[str, tuple[str, str]] = {}
    for root, nodes in sorted(grouped_components.items()):
        member_urls = tuple(sorted(url for _, url in nodes))
        anchor_url = member_urls[0]
        existing_entity_id = _existing_award_program_entity(connection, member_urls)
        if existing_entity_id is not None:
            prior_component = reused_entity_ids.get(existing_entity_id)
            if prior_component is not None and prior_component != root:
                raise ValueError(
                    "one historical NIST award program maps to multiple current page groups; "
                    "manual award-group reconciliation is required"
                )
            reused_entity_ids[existing_entity_id] = root
        component_metadata[root] = (anchor_url, member_urls, existing_entity_id)

    result: dict[tuple[str, str, int, str], _MonetaryGroup] = {}
    for recipient, disclosure_kind, amount, url in (*forced_entries, *overview_entries):
        root = component_for_node.get((recipient, url))
        if root is None:
            continue
        anchor_url, member_urls, existing_entity_id = component_metadata[root]
        matching_urls = disclosure_urls[(recipient, disclosure_kind, amount)] & set(member_urls)
        if not matching_urls:
            raise AssertionError("monetary group has no matching disclosure URL")
        result[(recipient, disclosure_kind, amount, url)] = _MonetaryGroup(
            anchor_url=anchor_url,
            canonical_url=min(matching_urls),
            member_urls=member_urls,
            existing_entity_id=existing_entity_id,
        )
    return result, index_amounts


def _monetary_target(
    context: _ImportContext,
    *,
    page_project_id: str,
    recipient_identity: str,
    disclosure_kind: str,
    amount: int,
    source_url: str,
    source_record_key: str,
    document_id: str,
    source_record_id: str,
    locator: str,
    excerpt: str,
) -> tuple[str, bool, bool]:
    key = (recipient_identity, disclosure_kind, amount, source_url)
    group = context.monetary_groups.get(key)
    if group is None:
        return page_project_id, True, False
    display_name = (
        f"{context.recipient_display_names.get(recipient_identity, recipient_identity)} "
        "CHIPS award program (aggregate)"
    )
    if group.existing_entity_id is None:
        group_id = _add_entity(
            context,
            EntityKind.PROJECT,
            "award-program",
            recipient_identity,
            _normalized_identity(group.anchor_url),
            display_name=display_name,
        )
    else:
        entity = context.connection.execute(
            "SELECT kind, display_name FROM entities WHERE id = ?",
            (group.existing_entity_id,),
        ).fetchone()
        if entity is None or entity["kind"] != EntityKind.PROJECT.value:
            raise ValueError("historical NIST award program target is missing or not a project")
        group_id = group.existing_entity_id
        context.project_ids.add(group_id)
    _source_claim(
        context,
        page_project_id,
        "relationship.award_program",
        RelationshipValue(
            group_id,
            "part_of_award_program",
            {
                "award_group_anchor_url": group.anchor_url,
                "disclosure_kind": disclosure_kind,
                "amount_usd": amount,
                "member_urls": list(group.member_urls),
            },
        ),
        source_record_key=source_record_key,
        field_key=f"award-program:{disclosure_kind}",
        document_id=document_id,
        source_record_id=source_record_id,
        locator=locator,
        excerpt=excerpt,
    )
    return group_id, source_url == group.canonical_url, True


def _monetary_scope_claim(
    context: _ImportContext,
    subject_entity_id: str,
    disclosure_kind: str,
    classification: str,
    *,
    source_record_key: str,
    field_key: str,
    parents: Sequence[str],
) -> None:
    _derived_claim(
        context,
        subject_entity_id,
        f"{disclosure_kind}.scope_classification",
        ScalarValue(ScalarType.TEXT, classification),
        source_record_key=source_record_key,
        field_key=f"{field_key}:scope-classification",
        parents=parents,
        method="nist_monetary_scope_v1",
    )


def _monetary_amount_predicate(disclosure_kind: str, classification: str) -> str:
    suffix = {
        "program_shared": "program_amount_usd",
        "project_total": "project_amount_usd",
        "site_allocation": "site_amount_usd",
    }[classification]
    return f"{disclosure_kind}.{suffix}"


def _program_series_key(group_entity_id: str, disclosure_kind: str) -> str:
    return f"aggregate:chips-award-program:{group_entity_id}:{disclosure_kind}"


def _scope_phrase(source_text: str) -> str | None:
    match = re.search(r"\(([^()]*(?:split|shared) across[^()]*)\)", source_text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _capability_predicate(label: str) -> str:
    normalized = " ".join(label.casefold().split())
    if "timeline" in normalized:
        return "timeline.source_text"
    if "technology" in normalized:
        return "technology.source_text"
    if normalized == "project type":
        return "project_type.source_text"
    return "capability.source_text"


def _disclosure_field_key(prefix: str, disclosure: TextDisclosure) -> str:
    identity = stable_id(
        "nist-disclosure-field",
        _normalized_identity(disclosure.label),
        _normalized_identity(disclosure.scope or ""),
    )
    return f"{prefix}:{identity}"


def _throughput_values(source_text: str) -> list[tuple[str, float, str, str]]:
    values: list[tuple[str, float, str, str]] = []
    seen: set[tuple[float, str, str]] = set()

    def add(match: re.Match[str]) -> None:
        base = Decimal(match.group(1).replace(",", ""))
        multiplier = _MULTIPLIERS[match.group(2).casefold() if match.group(2) else None]
        value = float(base * multiplier)
        item = match.group(3).casefold()
        period = match.group(4).casefold()
        key = (value, item, period)
        if key not in seen:
            values.append((match.group(0).strip(), value, item, period))
            seen.add(key)

    for match in _CAPACITY_THROUGHPUT_RE.finditer(source_text):
        add(match)
    for sentence in re.split(r"[.\n]", source_text):
        if not re.search(
            r"\b(?:expected|anticipated|planned|will|would)\s+to\s+produce\b",
            sentence,
            re.IGNORECASE,
        ):
            continue
        for match in _FORWARD_THROUGHPUT_RE.finditer(sentence):
            add(match)
    return values


def _production_timeline_windows(
    source_text: str,
) -> list[tuple[str, str, str, str, str]]:
    windows: list[tuple[str, str, str, str, str]] = []
    for raw_sentence in re.split(r"[.\n]", source_text):
        sentence = " ".join(raw_sentence.split())
        if not sentence:
            continue
        eligible = (
            re.search(
                r"\b(?:mass\s+)?production\b.{0,50}\b(?:expected|will)\b"
                r".{0,50}\b(?:begin|start)\b",
                sentence,
                re.IGNORECASE,
            )
            or re.search(
                r"\bon track to start production\b",
                sentence,
                re.IGNORECASE,
            )
            or re.search(
                r"\bproduction expected\s+(?:in\s+|summer|first half|second half)",
                sentence,
                re.IGNORECASE,
            )
        )
        year_match = re.search(r"\b(20\d{2})\b", sentence)
        if not eligible or year_match is None:
            continue
        year = int(year_match.group(1))
        lowered = sentence.casefold()
        if "first half" in lowered:
            low, base, high = f"{year}-01-01", f"{year}-04-01", f"{year}-06-30"
        elif "second half" in lowered:
            low, base, high = f"{year}-07-01", f"{year}-10-01", f"{year}-12-31"
        elif "summer" in lowered:
            low, base, high = f"{year}-06-01", f"{year}-07-15", f"{year}-08-31"
        elif "end of" in lowered:
            low, base, high = f"{year}-10-01", f"{year}-12-01", f"{year}-12-31"
        else:
            low, base, high = f"{year}-01-01", f"{year}-07-01", f"{year}-12-31"
        milestone_type = "mass_production" if "mass production" in lowered else "production_start"
        windows.append((sentence, milestone_type, low, base, high))
    return windows


def _text_derivations(
    context: _ImportContext,
    project_id: str,
    source_text: str,
    *,
    source_record_key: str,
    field_key: str,
    parents: Sequence[str],
) -> None:
    wafer_sizes, process_nodes, _, technologies = extract_capabilities(source_text)
    activities = classify_facility_activities(source_text)

    for value in wafer_sizes:
        _derived_claim(
            context,
            project_id,
            "capability.wafer_size_mm",
            CapabilityValue("wafer_size", float(value), unit="mm", qualifier="source_label"),
            source_record_key=source_record_key,
            field_key=f"{field_key}:wafer:{float(value):g}",
            parents=parents,
            method="nist_exact_capability_extraction_v1",
        )
    for value in process_nodes:
        _derived_claim(
            context,
            project_id,
            "capability.process_node_nm",
            CapabilityValue("process_node_label", value, unit="nm", qualifier="source_label"),
            source_record_key=source_record_key,
            field_key=f"{field_key}:node:{_normalized_identity(str(value))}",
            parents=parents,
            method="nist_exact_capability_extraction_v1",
        )
    for value in technologies:
        _derived_claim(
            context,
            project_id,
            "capability.technology_taxonomy",
            CapabilityValue("technology_taxonomy", value, qualifier="normalized_taxonomy"),
            source_record_key=source_record_key,
            field_key=f"{field_key}:technology:{_normalized_identity(value)}",
            parents=parents,
            method="nist_technology_taxonomy_v1",
            confidence=0.9,
        )
    for value in activities:
        _derived_claim(
            context,
            project_id,
            "facility.activity_taxonomy",
            CapabilityValue("facility_activity", value, qualifier="normalized_taxonomy"),
            source_record_key=source_record_key,
            field_key=f"{field_key}:activity:{_normalized_identity(value)}",
            parents=parents,
            method="nist_facility_activity_taxonomy_v1",
            confidence=0.9,
        )

    for match in _CLEANROOM_RE.finditer(source_text):
        area = int(match.group(2).replace(",", ""))
        cleanroom_key = f"{area}:{_normalized_identity(match.group(1) or 'exact')}"
        _derived_claim(
            context,
            project_id,
            "cleanroom_area.value_ft2",
            ScalarValue(ScalarType.INTEGER, area, unit="ft2"),
            source_record_key=source_record_key,
            field_key=f"{field_key}:cleanroom:{cleanroom_key}:value",
            parents=parents,
            method="nist_exact_cleanroom_area_extraction_v1",
            notes=f"Extracted literal: {match.group(0)}",
        )
        if match.group(1):
            _derived_claim(
                context,
                project_id,
                "cleanroom_area.qualifier",
                ScalarValue(ScalarType.TEXT, match.group(1).casefold()),
                source_record_key=source_record_key,
                field_key=f"{field_key}:cleanroom:{cleanroom_key}:qualifier",
                parents=parents,
                method="nist_quantity_qualifier_v1",
            )

    for match in _CAPACITY_PERCENT_RE.finditer(source_text):
        value = float(Decimal(match.group(1).replace(",", "")))
        _derived_claim(
            context,
            project_id,
            "capacity.production_capacity_increase",
            CapacityValue(
                "production_capacity_increase",
                CapacityBasis.ANNOUNCED,
                "percent",
                value,
                value,
                value,
            ),
            source_record_key=source_record_key,
            field_key=f"{field_key}:capacity-percent:production-capacity-increase",
            parents=parents,
            method="nist_exact_announced_capacity_extraction_v1",
            notes=f"Extracted literal: {match.group(0)}",
        )

    for literal, value, item, period in _throughput_values(source_text):
        _derived_claim(
            context,
            project_id,
            "capacity.announced_throughput",
            CapacityValue(
                f"{item}_throughput",
                CapacityBasis.ANNOUNCED,
                f"{item}/{period}",
                value,
                value,
                value,
            ),
            source_record_key=source_record_key,
            field_key=f"{field_key}:capacity-throughput:{item}:{period}",
            parents=parents,
            method="nist_exact_announced_capacity_extraction_v1",
            notes=f"Extracted literal: {literal}",
        )

    for literal, milestone_type, low, base, high in _production_timeline_windows(source_text):
        _derived_claim(
            context,
            project_id,
            "milestone.production_start",
            MilestoneValue(
                milestone_type,
                MilestoneStatus.EXPECTED,
                low,
                base,
                high,
            ),
            source_record_key=source_record_key,
            field_key=f"{field_key}:production-milestone:{milestone_type}",
            parents=parents,
            method="nist_timeline_window_v1",
            confidence=0.75,
            notes=(
                f"Mapped source wording '{literal}' to a conservative calendar window; "
                "the interval is an estimate, not a source-stated exact date."
            ),
        )


def _disclosure_claims(
    context: _ImportContext,
    project_id: str,
    disclosure: TextDisclosure,
    *,
    predicate: str,
    source_record_key: str,
    field_key: str,
    document_id: str,
    source_record_id: str,
) -> tuple[str, ...]:
    locator = f"{disclosure.scope} / {disclosure.label}" if disclosure.scope else disclosure.label
    raw_id = _literal_text(
        context,
        project_id,
        predicate,
        disclosure.source_text,
        source_record_key=source_record_key,
        field_key=f"{field_key}:text",
        document_id=document_id,
        source_record_id=source_record_id,
        locator=locator,
    )
    parents = [raw_id]
    if disclosure.scope:
        scope_id = _literal_text(
            context,
            project_id,
            predicate.removesuffix("source_text") + "scope_text",
            disclosure.scope,
            source_record_key=source_record_key,
            field_key=f"{field_key}:scope",
            document_id=document_id,
            source_record_id=source_record_id,
            locator=disclosure.scope,
        )
        parents.append(scope_id)
    return tuple(parents)


def _money_derivations(
    context: _ImportContext,
    project_id: str,
    source_text: str,
    *,
    predicate: str,
    source_record_key: str,
    field_key: str,
    parents: Sequence[str],
    include_qualifier: bool = False,
) -> None:
    values = _money_values(source_text)
    if not values:
        return
    literal, amount, qualifier = values[0]
    if amount >= 250_000_000_000:
        _derived_claim(
            context,
            project_id,
            "data_quality.source_value_anomaly",
            ConstraintValue(
                "source_value_anomaly",
                ConstraintStatus.POTENTIAL,
                ConstraintSeverity.HIGH,
                f"Quarantined implausible monetary disclosure: {literal}",
                constrained_entity_id=project_id,
                attributes={"amount_usd": amount, "predicate": predicate, "literal": literal},
            ),
            source_record_key=source_record_key,
            field_key=f"{field_key}:amount-anomaly",
            parents=parents,
            method="nist_monetary_anomaly_guard_v1",
            confidence=0.95,
        )
        return
    _derived_claim(
        context,
        project_id,
        predicate,
        ScalarValue(ScalarType.INTEGER, amount, unit="USD"),
        source_record_key=source_record_key,
        field_key=f"{field_key}:amount",
        parents=parents,
        method="nist_primary_usd_extraction_v2",
        notes=(
            f"Extracted primary literal: {literal}; qualifier={qualifier}; "
            "contextual monetary tokens are retained only in the source text; nominal price year not stated."
        ),
    )
    if include_qualifier:
        _derived_claim(
            context,
            project_id,
            predicate.removesuffix("amount_usd") + "qualifier",
            ScalarValue(ScalarType.TEXT, qualifier),
            source_record_key=source_record_key,
            field_key=f"{field_key}:qualifier",
            parents=parents,
            method="nist_money_qualifier_v1",
        )


def _index_payload(record: AwardRecord) -> dict[str, object]:
    return {"record_type": "award_index_record", **asdict(record)}


def _detail_payload(detail: NISTProjectDetail) -> dict[str, object]:
    return {"record_type": "award_detail_record", **asdict(detail)}


def _ingest_index_record(
    context: _ImportContext,
    item: _IndexInput,
) -> tuple[str, str, str]:
    record = item.record
    source_record_key = f"index:{record.source_url}"
    source_record_id = _add_record(
        context,
        item.document,
        source_record_key,
        _index_payload(record),
    )
    project_id = _add_entity(
        context,
        EntityKind.PROJECT,
        _normalized_identity(record.source_url),
        display_name=record.title,
    )
    recipient_identity = _normalized_identity(record.recipient)
    organization_id = _add_entity(
        context,
        EntityKind.ORGANIZATION,
        recipient_identity,
        display_name=record.recipient,
    )
    site_id = _add_entity(
        context,
        EntityKind.SITE,
        recipient_identity,
        _index_location_identity(record.locality, record.region),
        display_name=_site_display_name(record.locality, record.region),
    )

    _literal_text(
        context,
        project_id,
        "canonical_url",
        record.source_url,
        source_record_key=source_record_key,
        field_key="canonical-url",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card link",
    )
    _literal_text(
        context,
        project_id,
        "name",
        record.title,
        source_record_key=source_record_key,
        field_key="project-name",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card title",
    )
    _literal_text(
        context,
        organization_id,
        "name",
        record.recipient,
        source_record_key=source_record_key,
        field_key="recipient-name",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card recipient",
    )
    _source_claim(
        context,
        project_id,
        "relationship.recipient",
        RelationshipValue(organization_id, "recipient", {"source_field": "recipient"}),
        source_record_key=source_record_key,
        field_key="recipient-relationship",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card recipient",
        excerpt=record.recipient,
    )
    _literal_text(
        context,
        site_id,
        "location.locality",
        record.locality,
        source_record_key=source_record_key,
        field_key="location-locality",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card locality",
    )
    _literal_text(
        context,
        site_id,
        "location.region",
        record.region,
        source_record_key=source_record_key,
        field_key="location-region",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card region",
    )
    _source_claim(
        context,
        project_id,
        "relationship.site",
        RelationshipValue(site_id, "applies_to_site", {"source_field": "location"}),
        source_record_key=source_record_key,
        field_key="site-relationship",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card location",
        excerpt=f"{record.locality}, {record.region}",
    )

    description_id = _literal_text(
        context,
        project_id,
        "description.source_text",
        record.description,
        source_record_key=source_record_key,
        field_key="description",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="award index card description",
    )
    _text_derivations(
        context,
        project_id,
        record.description,
        source_record_key=source_record_key,
        field_key="description",
        parents=[description_id],
    )

    if record.amount is not None:
        amount_target_id, is_canonical_amount, is_program_amount = _monetary_target(
            context,
            page_project_id=project_id,
            recipient_identity=recipient_identity,
            disclosure_kind="direct_funding",
            amount=record.amount.amount_usd,
            source_url=record.source_url,
            source_record_key=source_record_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="award index card amount",
            excerpt=record.amount.source_text,
        )
        amount_text_id = _literal_text(
            context,
            amount_target_id,
            "direct_funding.source_text",
            record.amount.source_text,
            source_record_key=source_record_key,
            field_key="direct-funding:text",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="award index card amount",
        )
        amount_parents = [amount_text_id]
        phrase = _scope_phrase(record.amount.source_text)
        if phrase:
            amount_parents.append(
                _literal_text(
                    context,
                    amount_target_id,
                    "direct_funding.scope_text",
                    phrase,
                    source_record_key=source_record_key,
                    field_key="direct-funding:scope-text",
                    document_id=item.document.id,
                    source_record_id=source_record_id,
                    locator="award index card amount parenthetical",
                )
            )
        qualifier = (
            "unqualified"
            if record.amount.qualifier == "reported_amount"
            else record.amount.qualifier
        )
        _derived_claim(
            context,
            amount_target_id,
            "direct_funding.qualifier",
            ScalarValue(ScalarType.TEXT, qualifier),
            source_record_key=source_record_key,
            field_key="direct-funding:qualifier",
            parents=amount_parents,
            method="nist_funding_qualifier_v1",
        )
        classification = "program_shared" if is_program_amount else "site_allocation"
        _monetary_scope_claim(
            context,
            amount_target_id,
            "direct_funding",
            classification,
            source_record_key=source_record_key,
            field_key="direct-funding",
            parents=amount_parents,
        )
        if is_canonical_amount:
            amount_series_key = (
                _program_series_key(amount_target_id, "direct_funding")
                if is_program_amount
                else source_record_key
            )
            _derived_claim(
                context,
                amount_target_id,
                _monetary_amount_predicate("direct_funding", classification),
                ScalarValue(ScalarType.INTEGER, record.amount.amount_usd, unit="USD"),
                source_record_key=amount_series_key,
                field_key=(
                    "direct-funding:program-aggregate"
                    if is_program_amount
                    else "direct-funding:amount"
                ),
                parents=amount_parents,
                method="nist_exact_usd_extraction_v1",
                notes=(
                    "Program totals and site allocations use distinct predicates and "
                    "must not be added without an explicit allocation model; nominal price year not stated."
                ),
            )

    return project_id, recipient_identity, source_record_id


def _detail_timestamp_claim(
    context: _ImportContext,
    project_id: str,
    value: str,
    *,
    predicate: str,
    field_key: str,
    source_record_key: str,
    document_id: str,
    source_record_id: str,
) -> None:
    scalar_type = ScalarType.TIMESTAMP if "T" in value else ScalarType.DATE
    _source_claim(
        context,
        project_id,
        predicate,
        ScalarValue(scalar_type, value),
        source_record_key=source_record_key,
        field_key=field_key,
        document_id=document_id,
        source_record_id=source_record_id,
        locator=f"metadata {predicate}",
        excerpt=value,
    )


def _ingest_detail_record(
    context: _ImportContext,
    project_id: str,
    recipient_identity: str,
    item: _DetailInput,
) -> None:
    detail = item.detail
    assert detail.canonical_url is not None
    source_record_key = f"detail:{detail.canonical_url}"
    source_record_id = _add_record(
        context,
        item.document,
        source_record_key,
        _detail_payload(detail),
    )

    _literal_text(
        context,
        project_id,
        "canonical_url",
        detail.canonical_url,
        source_record_key=source_record_key,
        field_key="canonical-url",
        document_id=item.document.id,
        source_record_id=source_record_id,
        locator="canonical link",
    )
    if detail.title:
        _literal_text(
            context,
            project_id,
            "name",
            detail.title,
            source_record_key=source_record_key,
            field_key="project-name",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="page title",
        )
    if detail.published_at:
        _detail_timestamp_claim(
            context,
            project_id,
            detail.published_at,
            predicate="source.published_at",
            field_key="published-at",
            source_record_key=source_record_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
        )
    if detail.updated_at:
        _detail_timestamp_claim(
            context,
            project_id,
            detail.updated_at,
            predicate="source.updated_at",
            field_key="updated-at",
            source_record_key=source_record_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
        )

    for recipient in detail.recipients:
        recipient_field_key = f"recipient:{_normalized_identity(recipient)}"
        organization_id = _add_entity(
            context,
            EntityKind.ORGANIZATION,
            _normalized_identity(recipient),
            display_name=recipient,
        )
        _literal_text(
            context,
            organization_id,
            "name",
            recipient,
            source_record_key=source_record_key,
            field_key=f"{recipient_field_key}:name",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="Recipient",
        )
        _source_claim(
            context,
            project_id,
            "relationship.recipient",
            RelationshipValue(organization_id, "recipient", {"source_field": "Recipient"}),
            source_record_key=source_record_key,
            field_key=f"{recipient_field_key}:relationship",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="Recipient",
            excerpt=recipient,
        )

    for location in detail.locations:
        site_identity, site_display_name = _canonical_detail_site(
            context,
            recipient_identity,
            location,
        )
        site_id = _add_entity(
            context,
            EntityKind.SITE,
            recipient_identity,
            site_identity,
            display_name=site_display_name,
        )
        location_field_key = f"location:{site_id}"
        _literal_text(
            context,
            site_id,
            "location.source_text",
            location,
            source_record_key=source_record_key,
            field_key=f"{location_field_key}:text",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="Location(s)",
        )
        _source_claim(
            context,
            project_id,
            "relationship.site",
            RelationshipValue(site_id, "applies_to_site", {"source_field": "Location(s)"}),
            source_record_key=source_record_key,
            field_key=f"{location_field_key}:relationship",
            document_id=item.document.id,
            source_record_id=source_record_id,
            locator="Location(s)",
            excerpt=location,
        )

    if detail.award_stage:
        _disclosure_claims(
            context,
            project_id,
            detail.award_stage,
            predicate="application_stage.source_text",
            source_record_key=source_record_key,
            field_key="application-stage",
            document_id=item.document.id,
            source_record_id=source_record_id,
        )

    if detail.project_description:
        parents = _disclosure_claims(
            context,
            project_id,
            detail.project_description,
            predicate="description.source_text",
            source_record_key=source_record_key,
            field_key="project-description",
            document_id=item.document.id,
            source_record_id=source_record_id,
        )
        _text_derivations(
            context,
            project_id,
            detail.project_description.source_text,
            source_record_key=source_record_key,
            field_key="project-description",
            parents=parents,
        )
    for funding in detail.direct_funding:
        disclosure = TextDisclosure(funding.label, funding.source_text, funding.scope)
        field_key = _disclosure_field_key("direct-funding", disclosure)
        values = _money_values(funding.source_text)
        amount = values[0][1] if values else None
        target_id = project_id
        is_canonical_amount = True
        is_program_amount = False
        if amount is not None and "project overview" in (funding.scope or "").casefold():
            target_id, is_canonical_amount, is_program_amount = _monetary_target(
                context,
                page_project_id=project_id,
                recipient_identity=recipient_identity,
                disclosure_kind="direct_funding",
                amount=amount,
                source_url=detail.canonical_url,
                source_record_key=source_record_key,
                document_id=item.document.id,
                source_record_id=source_record_id,
                locator=(
                    f"{funding.scope} / {funding.label}"
                    if funding.scope
                    else funding.label
                ),
                excerpt=funding.source_text,
            )
        parents = _disclosure_claims(
            context,
            target_id,
            disclosure,
            predicate="direct_funding.source_text",
            source_record_key=source_record_key,
            field_key=field_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
        )
        _derived_claim(
            context,
            target_id,
            "direct_funding.qualifier",
            ScalarValue(ScalarType.TEXT, funding.qualifier),
            source_record_key=source_record_key,
            field_key=f"{field_key}:qualifier",
            parents=parents,
            method="nist_funding_qualifier_v1",
        )
        classification = (
            "program_shared"
            if is_program_amount
            else (
                "site_allocation"
                if "project statistics" in (funding.scope or "").casefold()
                else "project_total"
            )
        )
        _monetary_scope_claim(
            context,
            target_id,
            "direct_funding",
            classification,
            source_record_key=source_record_key,
            field_key=field_key,
            parents=parents,
        )
        duplicates_index_amount = (
            amount is not None
            and context.index_amounts_by_url.get(detail.canonical_url) == amount
            and classification in {"site_allocation", "program_shared"}
        )
        if is_canonical_amount and not duplicates_index_amount:
            _money_derivations(
                context,
                target_id,
                funding.source_text,
                predicate=_monetary_amount_predicate("direct_funding", classification),
                source_record_key=(
                    _program_series_key(target_id, "direct_funding")
                    if is_program_amount
                    else source_record_key
                ),
                field_key=(
                    "direct-funding:program-aggregate"
                    if is_program_amount
                    else field_key
                ),
                parents=parents,
            )

    for capex in detail.expected_capex:
        field_key = _disclosure_field_key("expected-capex", capex)
        values = _money_values(capex.source_text)
        amount = values[0][1] if values else None
        target_id = project_id
        is_canonical_amount = True
        is_program_amount = False
        if amount is not None and "project overview" in (capex.scope or "").casefold():
            target_id, is_canonical_amount, is_program_amount = _monetary_target(
                context,
                page_project_id=project_id,
                recipient_identity=recipient_identity,
                disclosure_kind="expected_capex",
                amount=amount,
                source_url=detail.canonical_url,
                source_record_key=source_record_key,
                document_id=item.document.id,
                source_record_id=source_record_id,
                locator=(f"{capex.scope} / {capex.label}" if capex.scope else capex.label),
                excerpt=capex.source_text,
            )
        parents = _disclosure_claims(
            context,
            target_id,
            capex,
            predicate="expected_capex.source_text",
            source_record_key=source_record_key,
            field_key=field_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
        )
        classification = "program_shared" if is_program_amount else "project_total"
        _monetary_scope_claim(
            context,
            target_id,
            "expected_capex",
            classification,
            source_record_key=source_record_key,
            field_key=field_key,
            parents=parents,
        )
        if is_canonical_amount:
            _money_derivations(
                context,
                target_id,
                capex.source_text,
                predicate=_monetary_amount_predicate("expected_capex", classification),
                source_record_key=(
                    _program_series_key(target_id, "expected_capex")
                    if is_program_amount
                    else source_record_key
                ),
                field_key=(
                    "expected-capex:program-aggregate"
                    if is_program_amount
                    else field_key
                ),
                parents=parents,
                include_qualifier=True,
            )

    for jobs in detail.jobs:
        _disclosure_claims(
            context,
            project_id,
            jobs,
            predicate="jobs.source_text",
            source_record_key=source_record_key,
            field_key=_disclosure_field_key("jobs", jobs),
            document_id=item.document.id,
            source_record_id=source_record_id,
        )

    for capability in detail.capabilities:
        field_key = _disclosure_field_key("capability", capability)
        parents = _disclosure_claims(
            context,
            project_id,
            capability,
            predicate=_capability_predicate(capability.label),
            source_record_key=source_record_key,
            field_key=field_key,
            document_id=item.document.id,
            source_record_id=source_record_id,
        )
        _text_derivations(
            context,
            project_id,
            capability.source_text,
            source_record_key=source_record_key,
            field_key=field_key,
            parents=parents,
        )


def _retire_claims_absent_from_complete_snapshot(
    context: _ImportContext,
    source_id: str,
) -> None:
    active_prior = context.connection.execute(
        """
        SELECT versions.id, versions.recorded_at
        FROM claim_versions AS versions
        JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        WHERE runs.source_id = ?
          AND versions.created_by_run_id != ?
          AND versions.superseded_at IS NULL
        ORDER BY versions.id
        """,
        (source_id, context.run_id),
    ).fetchall()
    for row in active_prior:
        older = context.connection.execute(
            "SELECT julianday(?) < julianday(?)",
            (row["recorded_at"], context.retrieved_at),
        ).fetchone()[0]
        if older != 1:
            raise ValueError(
                "complete NIST snapshot is not later than an active prior claim; "
                "out-of-order absence retirement is not allowed"
            )
        context.connection.execute(
            "UPDATE claim_versions SET superseded_at = ? WHERE id = ?",
            (context.retrieved_at, row["id"]),
        )


def import_nist_awards(
    connection: sqlite3.Connection,
    index_paths: Iterable[NISTSourceInput],
    detail_paths: Iterable[NISTSourceInput],
    retrieved_at: str,
    as_of_date: str,
    *,
    snapshot_is_complete: bool = False,
) -> NISTImportResult:
    """Import one pinned NIST snapshot without promoting announcements to physical facts."""

    if not isinstance(snapshot_is_complete, bool):
        raise ValueError("snapshot_is_complete must be a boolean")

    parsed_retrieved_at = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    if parsed_retrieved_at.tzinfo is None or parsed_retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    retrieved_at = parsed_retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z")

    try:
        parsed_as_of_date = date.fromisoformat(as_of_date)
    except ValueError as error:
        raise ValueError("as_of_date must use YYYY-MM-DD") from error
    if parsed_as_of_date.isoformat() != as_of_date:
        raise ValueError("as_of_date must use YYYY-MM-DD")

    family_id = stable_id("source-family", _SOURCE_FAMILY_KEY)
    source_id = stable_id("source", _SOURCE_KEY)
    documents, index_records, details = _prepare_inputs(
        index_paths,
        detail_paths,
        retrieved_at,
        source_id,
    )
    monetary_groups, index_amounts = _monetary_group_maps(
        connection,
        index_records,
        details,
    )
    recipient_names: dict[str, str] = {}
    index_site_locations: dict[str, set[tuple[str, str]]] = {}
    for item in index_records:
        identity = _normalized_identity(item.record.recipient)
        index_site_locations.setdefault(identity, set()).add(
            (item.record.locality, item.record.region)
        )
        current = recipient_names.get(identity)
        if current is None or (len(item.record.recipient), item.record.recipient.casefold()) < (
            len(current),
            current.casefold(),
        ):
            recipient_names[identity] = item.record.recipient
    document_ids = tuple(sorted(document.id for document in documents))
    run_id = stable_id(
        "ingestion-run",
        NIST_IMPORT_VERSION,
        source_id,
        retrieved_at,
        as_of_date,
        str(snapshot_is_complete).casefold(),
        *document_ids,
    )
    context = _ImportContext(
        connection,
        run_id,
        retrieved_at,
        as_of_date,
        set(),
        set(),
        set(),
        set(),
        set(),
        recipient_names,
        {
            identity: tuple(sorted(locations))
            for identity, locations in sorted(index_site_locations.items())
        },
        monetary_groups,
        index_amounts,
    )
    savepoint = f"nist_import_{next(_IMPORT_SAVEPOINTS)}"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        created_family_id, created_source_id = _source_family_and_source(connection)
        assert created_family_id == family_id
        assert created_source_id == source_id
        for document in documents:
            _add_document(connection, source_id, document, retrieved_at)
        add_ingestion_run(
            connection,
            IngestionRun(
                run_id,
                source_id,
                retrieved_at,
                status=IngestionStatus.SUCCEEDED,
                completed_at=_one_second_later(retrieved_at),
                code_version=NIST_IMPORT_VERSION,
                input_document_id=min(
                    (
                        document
                        for document in documents
                        if document.record_type == "award_index_page"
                    ),
                    key=lambda document: document.page_number or 0,
                ).id,
                parameters={
                    "as_of_date": as_of_date,
                    "detail_document_ids": sorted(
                        item.document.id for item in details.values()
                    ),
                    "index_document_ids": sorted(
                        document.id
                        for document in documents
                        if document.record_type == "award_index_page"
                    ),
                    "import_version": NIST_IMPORT_VERSION,
                    "snapshot_is_complete": snapshot_is_complete,
                },
            ),
        )
        document_roles = {
            "award_index_page": "parameter:index_document_ids",
            "award_detail_page": "parameter:detail_document_ids",
        }
        for document in documents:
            add_ingestion_run_document(
                connection,
                IngestionRunDocument(
                    run_id,
                    document.id,
                    document_roles[document.record_type],
                ),
            )
        for item in index_records:
            project_id, recipient_identity, _ = _ingest_index_record(context, item)
            detail_input = details.get(item.record.source_url)
            if detail_input is not None:
                _ingest_detail_record(
                    context,
                    project_id,
                    recipient_identity,
                    detail_input,
                )
        if snapshot_is_complete:
            _retire_claims_absent_from_complete_snapshot(context, source_id)
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")

    return NISTImportResult(
        run_id=run_id,
        source_id=source_id,
        source_document_ids=document_ids,
        source_record_ids=tuple(sorted(context.source_record_ids)),
        organization_ids=tuple(sorted(context.organization_ids)),
        site_ids=tuple(sorted(context.site_ids)),
        project_ids=tuple(sorted(context.project_ids)),
        claim_version_ids=tuple(sorted(context.claim_version_ids)),
    )
