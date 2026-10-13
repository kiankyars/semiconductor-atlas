"""Deterministic, fail-closed AI-critical manufacturing baseline releases."""

from __future__ import annotations

import csv
import ctypes
import errno
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from html.parser import HTMLParser
from typing import Any, BinaryIO, Iterable, Mapping, Sequence
from urllib.parse import urlparse


INPUT_FORMAT = "semiconductor-atlas-ai-critical-input-v1"
RELEASE_FORMAT = "semiconductor-atlas-ai-critical-release-v1"
SELECTION_VERSION = "ai-critical-cohort-selection-v1"
SCHEMA_VERSION = "ai-critical-manufacturing-schema-v1"
CODE_VERSION = "0.1.0"
MODEL_VERSION = "none"
ENTITY_RESOLUTION_VERSION = "ai-critical-facility-identity-review-v1"
EVIDENCE_DATE_FLOOR = date(2024, 1, 1)
SOURCE_ADAPTER_VERSION = "official-primary-exact-byte-capture-v1"

COMPANIES = (
    "TSMC",
    "Samsung",
    "Intel",
    "Micron",
    "SK hynix",
    "Amkor",
    "ASE",
)
CAPACITY_BASES = (
    "announced",
    "physical_construction",
    "tool_installed",
    "qualified",
    "economically_usable",
)
CAPACITY_METRIC_UNITS = {
    "units_throughput": frozenset({"units/month", "units/quarter"}),
    "wafers_throughput": frozenset({"wafers/month", "wafers/quarter"}),
}
CAPACITY_INPUT_OUTPUT_BASES = frozenset(
    {"source_stated_throughput", "source_stated_quarter_total"}
)
CAPACITY_QUANTITY_SEMANTICS = {
    ("units_throughput", "units/month"): "monthly_rate",
    ("units_throughput", "units/quarter"): "quarter_total",
    ("wafers_throughput", "wafers/month"): "monthly_rate",
    ("wafers_throughput", "wafers/quarter"): "quarter_total",
}
SCOPE_CATEGORIES = frozenset(
    {
        "leading_edge_logic",
        "hbm_fabrication",
        "hbm_packaging",
        "advanced_packaging",
        "advanced_test",
    }
)
LIFECYCLE_STATES = frozenset(
    {
        "lead",
        "announced",
        "site_control",
        "permitting",
        "permitted",
        "site_preparation",
        "civil_works",
        "shell",
        "cleanroom_fitout",
        "utilities_ready",
        "tools_installing",
        "commissioning",
        "customer_qualification",
        "ramping",
        "complete",
        "paused",
        "cancelled",
        "redesigned",
        "repurposed",
        "decommissioned",
        "unknown",
    }
)
READINESS_STATES = frozenset(
    {"planned", "equipment_installed", "qualified", "production", "unknown"}
)
IDENTITY_SCOPES = frozenset(
    {"named_facility", "campus_scope", "project_site_scope"}
)
CLAIM_KINDS = frozenset(
    {"source_statement", "direct_observation", "reconciled_fact", "derived_estimate"}
)
AI_CRITICAL_INPUT_CLAIM_KINDS = frozenset({"source_statement", "reconciled_fact"})
PUBLICATION_PRECISIONS = frozenset({"day", "month", "year"})
REDISTRIBUTION_CLASSES = frozenset(
    {"public_domain", "licensed_redistribution", "metadata_and_excerpt_only"}
)
EVIDENCE_ROLES = frozenset({"support", "refute", "context"})
INGESTION_DOCUMENT_ROLES = frozenset(
    {"primary", "index", "detail", "archive", "derivative"}
)
_ID_RE = re.compile(r"[a-z0-9][a-z0-9._:-]*\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_NAMESPACE = uuid.UUID("d21c9543-9720-43a1-935f-bc923d216e1f")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag.casefold() in {"script", "style"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


@dataclass(frozen=True, slots=True)
class ValidatedBaseline:
    spec: Mapping[str, Any]
    source_root: Path
    sources: Mapping[str, Mapping[str, Any]]
    ingestion_runs: tuple[Mapping[str, Any], ...]
    evidence: Mapping[str, Mapping[str, Any]]
    facilities: tuple[Mapping[str, Any], ...]
    input_bytes: bytes
    input_sha256: str
    source_bytes_verified: bool
    atlas_template_bytes: bytes
    code_bytes: bytes
    builder_script_bytes: bytes
    atlas_generator_bytes: bytes


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_bytes(raw: bytes, context: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{context} must be UTF-8") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{context} is not valid JSON: {error}") from error


def _require_object(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _require_list(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _require_keys(
    value: Mapping[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    context: str,
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ValueError(f"{context} is missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{context} has unknown fields: {', '.join(unknown)}")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{context} must not have leading or trailing whitespace")
    return value


def _identifier(value: object, context: str) -> str:
    result = _text(value, context)
    if not _ID_RE.fullmatch(result):
        raise ValueError(f"{context} must be a lowercase stable identifier")
    return result


def _iso_date(value: object, context: str) -> str:
    result = _text(value, context)
    try:
        parsed = date.fromisoformat(result)
    except ValueError as error:
        raise ValueError(f"{context} must use YYYY-MM-DD") from error
    if parsed.isoformat() != result:
        raise ValueError(f"{context} must use canonical YYYY-MM-DD")
    return result


def _iso_timestamp(value: object, context: str) -> str:
    result = _text(value, context)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{context} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if canonical != result:
        raise ValueError(f"{context} must be a canonical UTC timestamp")
    return result


def _https_url(value: object, context: str) -> str:
    result = _text(value, context)
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{context} must be an HTTPS URL without credentials")
    return result


def _sha256(value: object, context: str) -> str:
    result = _text(value, context)
    if not _SHA256_RE.fullmatch(result):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
    return result


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{context} must be an integer >= {minimum}")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ValueError(f"{context} must be a finite number")
    return result


def _decimal_number(value: object, context: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"{context} must be a finite number")
    return result


def _string_list(value: object, context: str, *, nonempty: bool = False) -> list[str]:
    rows = _require_list(value, context)
    result = [_text(item, f"{context}[{index}]") for index, item in enumerate(rows)]
    if nonempty and not result:
        raise ValueError(f"{context} must not be empty")
    if result != sorted(set(result)):
        raise ValueError(f"{context} must be sorted and unique")
    return result


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _source_record_sha256(source: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(source)).hexdigest()


def _normalized_evidence_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", normalized)


def _html_visible_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(value)
    parser.close()
    return _normalized_evidence_text(" ".join(parser.parts))


def _json_string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _json_string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_string_values(item)


def _automatic_source_text(raw: bytes, media_type: str) -> tuple[str, ...]:
    if media_type == "text/html":
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("archived HTML evidence must be UTF-8") from error
        return (_html_visible_text(decoded),)
    if media_type == "application/json":
        document = _strict_json_bytes(raw, "archived JSON evidence")
        return tuple(
            text
            for item in _json_string_values(document)
            if (text := _html_visible_text(item))
        )
    raise ValueError("automatic evidence-text verification requires HTML or JSON")


def _excerpt_segments(excerpt: str) -> list[str]:
    without_edges = re.sub(r"^(?:\.{3}|…)[ ]*", "", excerpt)
    without_edges = re.sub(r"[ ]*(?:\.{3}|…)$", "", without_edges)
    return [
        _normalized_evidence_text(part)
        for part in re.split(r"\s+(?:\.{3}|…)\s+", without_edges)
        if part.strip()
    ]


def _excerpt_is_substantive(segments: Sequence[str]) -> bool:
    combined = " ".join(segments)
    alphanumeric_count = sum(character.isalnum() for character in combined)
    word_count = len(re.findall(r"\b\w+\b", combined, flags=re.UNICODE))
    return (
        bool(segments)
        and all(
            len(segment) >= 8
            and any(character.isalnum() for character in segment)
            for segment in segments
        )
        and alphanumeric_count >= 24
        and word_count >= 4
    )


def _verify_excerpt_segments(
    excerpt: str,
    source_texts: Sequence[str],
    context: str,
) -> None:
    segments = _excerpt_segments(excerpt)
    if not _excerpt_is_substantive(segments):
        raise ValueError(f"{context}.excerpt must contain a substantive source segment")
    for source_text in source_texts:
        position = 0
        for segment in segments:
            found = source_text.find(segment, position)
            if found < 0:
                break
            position = found + len(segment)
        else:
            return
    raise ValueError(
        f"{context}.excerpt segments do not resolve in one archived source record"
    )


def _capacity_assertion_signature(value: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        value["metric"],
        value["basis"],
        value["unit"],
        _capacity_number_identity(value["low"]),
        _capacity_number_identity(value["base"]),
        _capacity_number_identity(value["high"]),
        value["period_start"],
        value["period_end"],
        value["scope_kind"],
        value["input_output_basis"],
        value["quantity_semantics"],
        tuple(value["technology_scope"]),
    )


def _capacity_number_identity(value: object) -> str:
    return str(Decimal(str(value)).normalize())


def _capacity_number_token(value: object) -> str:
    token = format(Decimal(str(value)), "f")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def _capacity_assertion_matches_excerpt(
    assertion: Mapping[str, Any], excerpt: str
) -> bool:
    text = _normalized_evidence_text(excerpt).casefold()
    statements = [
        clause.strip()
        for statement in re.split(r"(?:\s*(?:\.{3}|…)\s*|[.;]\s+)", text)
        for clause in re.split(
            r"(?:,\s+(?:and|while|whereas|but|versus|however)\b|"
            r"\s+(?:while|whereas|but|versus|however)\s+)",
            statement,
        )
        if clause.strip()
    ]
    noun = "units" if assertion["metric"] == "units_throughput" else "wafers"
    semantics = assertion["quantity_semantics"]
    if assertion["basis"] != "announced":
        return False
    basis_terms = {
        "announced": ("announced", "expected", "planned", "upon completion"),
        "physical_construction": ("under construction", "construction underway"),
        "tool_installed": ("tool installed", "equipment installed"),
        "qualified": ("qualified",),
        "economically_usable": ("economically usable",),
    }

    def basis_matches(statement: str, basis: str) -> list[re.Match[str]]:
        return [
            match
            for term in basis_terms[basis]
            if (
                match := re.search(
                    r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b",
                    statement,
                )
            )
            is not None
        ]

    def has_positive_basis(statement: str, capacity_start: int) -> bool:
        if (
            re.search(
                r"\b(?:no|not|never|without|cannot|unannounced|denied|denies|"
                r"denying|disputed|refuted|false)\b",
                statement,
            )
            or "n't" in statement
            or "n’t" in statement
        ):
            return False
        selected_basis = str(assertion["basis"])
        if any(
            basis_matches(statement, other_basis)
            for other_basis in basis_terms
            if other_basis != selected_basis
        ):
            return False
        for match in basis_matches(statement, selected_basis):
            if match.end() > capacity_start:
                continue
            between = statement[match.end():capacity_start]
            if len(between) > 200 or re.search(
                r"(?<!\d),(?!\d)|[;:.]|\b(?:while|whereas|but|versus|however|legacy|separate|"
                r"unrelated|different|other)\b",
                between,
            ):
                continue
            return True
        return False

    if semantics == "monthly_rate":
        def local_capacity_match(
            value: object, statement: str
        ) -> re.Match[str] | None:
            token = _capacity_number_token(value)
            return re.search(
                rf"(?<![\d.]){re.escape(token)}(?![\d.])\s+"
                rf"{noun}\s*(?:/|per\s+)month\b",
                statement.replace(",", ""),
            )
    elif semantics == "quarter_total":
        period_start = assertion["period_start"]
        period_end = assertion["period_end"]
        if period_start is None or period_end is None:
            return False
        start = date.fromisoformat(str(period_start))
        quarter = (start.month - 1) // 3 + 1
        period_labels = (f"{start.year} q{quarter}", f"q{quarter} {start.year}")
        statements = [
            statement
            for statement in statements
            if "quarter total" in statement
            and any(label in statement for label in period_labels)
            and re.search(
                r"(?:\b(?:day|daily|week|weekly|month|monthly|hour|hourly|rate|"
                r"annual|annually|year|yearly|annum|lifetime|cumulative)\b|"
                r"/(?:day|week|month|hour|year)\b)",
                statement,
            )
            is None
        ]

        def local_capacity_match(
            value: object, statement: str
        ) -> re.Match[str] | None:
            token = _capacity_number_token(value)
            labels = "|".join(re.escape(label) for label in period_labels)
            return re.search(
                rf"\bquarter\s+total\b\s*(?:of\s+|:\s*|(?:is|was)\s+)?"
                rf"(?<![\d.]){re.escape(token)}(?![\d.])\s+{noun}\b\s+"
                rf"(?:in|for|during)\s+(?:{labels})\b",
                statement.replace(",", ""),
            )
    else:
        return False
    values = {
        _capacity_number_identity(assertion[field]): assertion[field]
        for field in ("low", "base", "high")
    }
    if not statements or not all(
        any(
            match is not None and has_positive_basis(statement, match.start())
            for statement in statements
            if (match := local_capacity_match(value, statement)) is not None
        )
        for value in values.values()
    ):
        return False
    return True


def _validate_capacity_assertion(
    value: object,
    context: str,
    excerpt: str,
) -> Mapping[str, Any]:
    assertion = _require_object(value, context)
    _require_keys(
        assertion,
        required=(
            "metric",
            "basis",
            "unit",
            "low",
            "base",
            "high",
            "period_start",
            "period_end",
            "scope_kind",
            "input_output_basis",
            "quantity_semantics",
            "technology_scope",
        ),
        context=context,
    )
    metric = _identifier(assertion["metric"], f"{context}.metric")
    basis = _text(assertion["basis"], f"{context}.basis")
    if basis not in CAPACITY_BASES:
        raise ValueError(f"{context}.basis is not one of the five capacity bases")
    unit = _text(assertion["unit"], f"{context}.unit")
    if metric not in CAPACITY_METRIC_UNITS or unit not in CAPACITY_METRIC_UNITS[metric]:
        raise ValueError(f"{context} metric and unit are not in the v1 capacity registry")
    low = _decimal_number(assertion["low"], f"{context}.low")
    base = _decimal_number(assertion["base"], f"{context}.base")
    high = _decimal_number(assertion["high"], f"{context}.high")
    if not 0 <= low <= base <= high:
        raise ValueError(f"{context} must satisfy 0 <= low <= base <= high")
    period_start = assertion["period_start"]
    period_end = assertion["period_end"]
    if period_start is not None:
        period_start = _iso_date(period_start, f"{context}.period_start")
    if period_end is not None:
        period_end = _iso_date(period_end, f"{context}.period_end")
    if period_start and period_end and period_end <= period_start:
        raise ValueError(f"{context}.period_end must be later than period_start")
    if _text(assertion["scope_kind"], f"{context}.scope_kind") not in {
        "project_addition",
        "facility_total",
        "production_unit_total",
    }:
        raise ValueError(f"{context}.scope_kind is invalid")
    input_output_basis = _text(
        assertion["input_output_basis"], f"{context}.input_output_basis"
    )
    if input_output_basis not in CAPACITY_INPUT_OUTPUT_BASES:
        raise ValueError(f"{context}.input_output_basis is not in the v1 registry")
    quantity_semantics = _text(
        assertion["quantity_semantics"], f"{context}.quantity_semantics"
    )
    if CAPACITY_QUANTITY_SEMANTICS.get((metric, unit)) != quantity_semantics:
        raise ValueError(f"{context}.quantity_semantics is incompatible")
    technology_scope = _string_list(
        assertion["technology_scope"], f"{context}.technology_scope", nonempty=True
    )
    if not set(technology_scope) <= SCOPE_CATEGORIES:
        raise ValueError(f"{context}.technology_scope is outside the v1 scope")
    if not _capacity_assertion_matches_excerpt(assertion, excerpt):
        raise ValueError(f"{context} does not match the verified evidence excerpt")
    return assertion


def _jsonl_bytes(rows: Iterable[object]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _spreadsheet_safe(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def _csv_bytes(fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _spreadsheet_safe(row.get(key, "")) for key in fieldnames})
    return stream.getvalue().encode("utf-8")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _hash_open_stream(stream: BinaryIO) -> tuple[int, str]:
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def _open_real_directory_fd(
    path_value: str | Path,
    context: str,
    *,
    create: bool,
) -> tuple[Path, int]:
    """Open each directory component relative to its already-pinned parent."""

    absolute = Path(os.path.abspath(Path(path_value)))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags)
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except OSError as error:
                try:
                    mode = os.stat(
                        part, dir_fd=descriptor, follow_symlinks=False
                    ).st_mode
                except OSError:
                    raise ValueError(
                        f"{context} has a missing or unsafe component: {current}"
                    ) from error
                if stat.S_ISLNK(mode):
                    raise ValueError(
                        f"{context} must not traverse a symlink: {current}"
                    ) from error
                raise ValueError(
                    f"{context} has a non-directory component: {current}"
                ) from error
            os.close(descriptor)
            descriptor = next_descriptor
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _check_directory_identity(
    descriptor: int,
    expected: tuple[int, int] | None,
    context: str,
) -> None:
    if expected is None:
        return
    status = os.fstat(descriptor)
    if (status.st_dev, status.st_ino) != expected:
        raise OSError(f"{context} identity changed")


def _rename_exclusive(
    source: Path,
    destination: Path,
    *,
    expected_source_parent: tuple[int, int] | None = None,
    expected_destination_parent: tuple[int, int] | None = None,
) -> None:
    """Atomically rename without replacing a destination created by a race."""

    system = os.uname().sysname
    library = ctypes.CDLL(None, use_errno=True)
    _, source_parent = _open_real_directory_fd(
        source.parent, "source parent", create=False
    )
    try:
        _check_directory_identity(
            source_parent, expected_source_parent, "source parent"
        )
        _, destination_parent = _open_real_directory_fd(
            destination.parent, "destination parent", create=False
        )
        try:
            _check_directory_identity(
                destination_parent,
                expected_destination_parent,
                "destination parent",
            )
            source_raw = os.fsencode(source.name)
            destination_raw = os.fsencode(destination.name)
            if system == "Darwin":
                rename = getattr(library, "renameatx_np", None)
                if rename is None:
                    raise OSError(errno.ENOTSUP, "exclusive rename is unavailable")
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(
                    source_parent,
                    source_raw,
                    destination_parent,
                    destination_raw,
                    0x00000004,
                )
            elif system == "Linux":
                rename = getattr(library, "renameat2", None)
                if rename is None:
                    raise OSError(errno.ENOTSUP, "exclusive rename is unavailable")
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(
                    source_parent,
                    source_raw,
                    destination_parent,
                    destination_raw,
                    0x00000001,
                )
            else:
                raise OSError(
                    errno.ENOTSUP,
                    "exclusive rename is unsupported on this platform",
                )
        finally:
            os.close(destination_parent)
    finally:
        os.close(source_parent)
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValueError(f"destination already exists: {destination}")
    raise OSError(error_number, os.strerror(error_number), destination)


def _link_file_exclusive(
    source: Path,
    destination: Path,
    *,
    expected_source_parent: tuple[int, int] | None = None,
    expected_destination_parent: tuple[int, int] | None = None,
) -> None:
    _, source_parent = _open_real_directory_fd(
        source.parent, "source parent", create=False
    )
    try:
        _check_directory_identity(
            source_parent, expected_source_parent, "source parent"
        )
        _, destination_parent = _open_real_directory_fd(
            destination.parent, "destination parent", create=False
        )
        try:
            _check_directory_identity(
                destination_parent,
                expected_destination_parent,
                "destination parent",
            )
            try:
                os.link(
                    source.name,
                    destination.name,
                    src_dir_fd=source_parent,
                    dst_dir_fd=destination_parent,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise ValueError(f"destination already exists: {destination}") from error
            try:
                os.unlink(source.name, dir_fd=source_parent)
            except OSError:
                # The exclusive link is the commit point. Leaving the owned staging
                # link is safer than deleting a raced destination pathname.
                return
        finally:
            os.close(destination_parent)
    finally:
        os.close(source_parent)


def ensure_real_directory(path_value: str | Path, context: str) -> Path:
    absolute, descriptor = _open_real_directory_fd(path_value, context, create=True)
    os.close(descriptor)
    return absolute


def install_directory_exclusive(
    source: str | Path,
    destination: str | Path,
    *,
    expected_source_parent: tuple[int, int] | None = None,
    expected_destination_parent: tuple[int, int] | None = None,
) -> None:
    _rename_exclusive(
        Path(source),
        Path(destination),
        expected_source_parent=expected_source_parent,
        expected_destination_parent=expected_destination_parent,
    )


def install_file_exclusive(
    source: str | Path,
    destination: str | Path,
    *,
    expected_source_parent: tuple[int, int] | None = None,
    expected_destination_parent: tuple[int, int] | None = None,
) -> None:
    _link_file_exclusive(
        Path(source),
        Path(destination),
        expected_source_parent=expected_source_parent,
        expected_destination_parent=expected_destination_parent,
    )


def _read_pinned_artifact(
    path_value: str | Path,
    expected_sha256: str,
    context: str,
) -> bytes:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{context} must be a regular file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"{context} does not match the pinned SHA-256")
    return raw


def _verify_archived_sources(baseline: ValidatedBaseline) -> None:
    if not baseline.source_bytes_verified:
        raise ValueError("release writing requires verified archived source bytes")
    for source_id, source in baseline.sources.items():
        raw = _read_safe_source_bytes(
            baseline.source_root,
            str(source["archive_path"]),
            f"source {source_id} archive_path",
        )
        actual_bytes = len(raw)
        actual_hash = hashlib.sha256(raw).hexdigest()
        if (
            actual_bytes != source["bytes"]
            or actual_hash != source["content_sha256"]
        ):
            raise ValueError(
                f"source {source_id} archived bytes changed after baseline validation"
            )


def _read_safe_source_bytes(
    source_root: Path,
    relative: str,
    context: str,
) -> bytes:
    pure = _safe_relative_posix_path(relative, context)
    _, descriptor = _open_real_directory_fd(source_root, context, create=False)
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for part in pure.parts[:-1]:
            try:
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ValueError(
                        f"{context} must not traverse a symlink: {relative}"
                    ) from error
                raise ValueError(
                    f"{context} has a missing or unsafe component: {relative}"
                ) from error
            os.close(descriptor)
            descriptor = next_descriptor
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(pure.parts[-1], file_flags, dir_fd=descriptor)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError(
                    f"{context} must not traverse a symlink: {relative}"
                ) from error
            raise ValueError(
                f"{context} does not exist or is unsafe: {relative}"
            ) from error
        try:
            status = os.fstat(file_descriptor)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError(f"{context} must be a regular file: {relative}")
            chunks: list[bytes] = []
            while chunk := os.read(file_descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


def _safe_relative_posix_path(relative: str, context: str) -> PurePosixPath:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{context} must be a safe relative POSIX path")
    return pure


def _validate_rights(value: object, context: str) -> Mapping[str, Any]:
    rights = _require_object(value, context)
    _require_keys(
        rights,
        required=(
            "access",
            "license",
            "license_url",
            "redistribution",
            "attribution",
            "reviewed_at",
        ),
        context=context,
    )
    if _text(rights["access"], f"{context}.access") != "public":
        raise ValueError(f"{context}.access must be public")
    _text(rights["license"], f"{context}.license")
    _https_url(rights["license_url"], f"{context}.license_url")
    redistribution = _text(rights["redistribution"], f"{context}.redistribution")
    if redistribution not in REDISTRIBUTION_CLASSES:
        raise ValueError(f"{context}.redistribution is not supported")
    _text(rights["attribution"], f"{context}.attribution")
    _iso_date(rights["reviewed_at"], f"{context}.reviewed_at")
    return rights


def _validate_source(
    value: object,
    index: int,
    source_root: Path,
    *,
    verify_source_bytes: bool,
    source_bytes_cache: dict[str, bytes],
) -> Mapping[str, Any]:
    context = f"sources[{index}]"
    source = _require_object(value, context)
    _require_keys(
        source,
        required=(
            "source_id",
            "source_family",
            "source_type",
            "publisher",
            "title",
            "url",
            "published_at",
            "published_at_precision",
            "published_at_basis",
            "retrieved_at",
            "acquired_at",
            "archive_path",
            "content_sha256",
            "bytes",
            "media_type",
            "language",
            "adapter_version",
            "ingestion_run_id",
            "rights",
        ),
        context=context,
    )
    source_id = _identifier(source["source_id"], f"{context}.source_id")
    _identifier(source["source_family"], f"{context}.source_family")
    if _text(source["source_type"], f"{context}.source_type") != "official_primary":
        raise ValueError(f"{context}.source_type must be official_primary")
    _text(source["publisher"], f"{context}.publisher")
    _text(source["title"], f"{context}.title")
    _https_url(source["url"], f"{context}.url")
    published_at = _iso_date(source["published_at"], f"{context}.published_at")
    if date.fromisoformat(published_at) < EVIDENCE_DATE_FLOOR:
        raise ValueError(f"{context}.published_at predates 2024")
    publication_precision = _text(
        source["published_at_precision"], f"{context}.published_at_precision"
    )
    if publication_precision not in PUBLICATION_PRECISIONS:
        raise ValueError(f"{context}.published_at_precision is invalid")
    publication_date = date.fromisoformat(published_at)
    if publication_precision == "month" and publication_date.day != 1:
        raise ValueError(f"{context}.published_at must be the month start for month precision")
    if publication_precision == "year" and (
        publication_date.month != 1 or publication_date.day != 1
    ):
        raise ValueError(f"{context}.published_at must be the year start for year precision")
    _text(source["published_at_basis"], f"{context}.published_at_basis")
    retrieved_at = _iso_timestamp(source["retrieved_at"], f"{context}.retrieved_at")
    acquired_at = _iso_timestamp(source["acquired_at"], f"{context}.acquired_at")
    if acquired_at != retrieved_at:
        raise ValueError(
            f"{context}.acquired_at must equal retrieved_at for exact-byte capture"
        )
    relative = _text(source["archive_path"], f"{context}.archive_path")
    _safe_relative_posix_path(relative, f"{context}.archive_path")
    expected_hash = _sha256(source["content_sha256"], f"{context}.content_sha256")
    expected_bytes = _integer(source["bytes"], f"{context}.bytes", minimum=1)
    _text(source["media_type"], f"{context}.media_type")
    language = _text(source["language"], f"{context}.language")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", language):
        raise ValueError(f"{context}.language must be a supported language tag")
    if _text(source["adapter_version"], f"{context}.adapter_version") != SOURCE_ADAPTER_VERSION:
        raise ValueError(
            f"{context}.adapter_version must be {SOURCE_ADAPTER_VERSION}"
        )
    _identifier(source["ingestion_run_id"], f"{context}.ingestion_run_id")
    _validate_rights(source["rights"], f"{context}.rights")
    if verify_source_bytes:
        raw = _read_safe_source_bytes(
            source_root, relative, f"{context}.archive_path"
        )
        actual_bytes = len(raw)
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_bytes != expected_bytes or actual_hash != expected_hash:
            raise ValueError(
                f"{context} archived bytes do not match declared size and SHA-256"
            )
        source_bytes_cache[source_id] = raw
    return source


def _validate_ingestion_runs(
    value: object,
    sources: Mapping[str, Mapping[str, Any]],
    recorded_at: str,
) -> tuple[Mapping[str, Any], ...]:
    rows = _require_list(value, "ingestion_runs")
    if not rows:
        raise ValueError("ingestion_runs must not be empty")
    recorded = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    run_ids: set[str] = set()
    linked_sources: dict[str, str] = {}
    validated: list[Mapping[str, Any]] = []
    for index, row_value in enumerate(rows):
        context = f"ingestion_runs[{index}]"
        row = _require_object(row_value, context)
        _require_keys(
            row,
            required=(
                "format",
                "run_id",
                "source_id",
                "code_version",
                "environment",
                "configuration",
                "configuration_sha256",
                "inputs",
                "started_at",
                "finished_at",
                "outcome",
            ),
            context=context,
        )
        if row["format"] != "semiconductor-atlas-source-ingestion-run-v1":
            raise ValueError(f"{context}.format is invalid")
        run_id = _identifier(row["run_id"], f"{context}.run_id")
        if run_id in run_ids:
            raise ValueError(f"duplicate ingestion run_id: {run_id}")
        run_ids.add(run_id)
        run_source_id = _identifier(row["source_id"], f"{context}.source_id")
        if run_source_id not in sources:
            raise ValueError(f"{context}.source_id does not resolve")
        if _text(row["code_version"], f"{context}.code_version") != SOURCE_ADAPTER_VERSION:
            raise ValueError(
                f"{context}.code_version must be {SOURCE_ADAPTER_VERSION}"
            )
        _text(row["environment"], f"{context}.environment")
        configuration = _require_object(row["configuration"], f"{context}.configuration")
        _require_keys(
            configuration,
            required=("method", "selection_version", "source_count"),
            context=f"{context}.configuration",
        )
        if (
            _text(configuration["method"], f"{context}.configuration.method")
            != "official_primary_exact_byte_local_replay"
        ):
            raise ValueError(f"{context}.configuration.method is invalid")
        if configuration["selection_version"] != SELECTION_VERSION:
            raise ValueError(f"{context}.configuration.selection_version is invalid")
        configured_source_count = _integer(
            configuration["source_count"],
            f"{context}.configuration.source_count",
            minimum=1,
        )
        configuration_hash = _sha256(
            row["configuration_sha256"], f"{context}.configuration_sha256"
        )
        if configuration_hash != hashlib.sha256(
            _canonical_bytes(configuration)
        ).hexdigest():
            raise ValueError(f"{context}.configuration_sha256 is inconsistent")
        started_at = _iso_timestamp(row["started_at"], f"{context}.started_at")
        finished_at = _iso_timestamp(row["finished_at"], f"{context}.finished_at")
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        if finished <= started:
            raise ValueError(f"{context} must finish after it starts")
        if finished > recorded:
            raise ValueError(f"{context} finishes after the knowledge cutoff")
        if _text(row["outcome"], f"{context}.outcome") != "succeeded":
            raise ValueError(f"{context}.outcome must be succeeded")
        inputs = _require_list(row["inputs"], f"{context}.inputs")
        if not inputs:
            raise ValueError(f"{context}.inputs must not be empty")
        input_keys: list[tuple[str, str]] = []
        for input_index, input_value in enumerate(inputs):
            input_context = f"{context}.inputs[{input_index}]"
            input_row = _require_object(input_value, input_context)
            _require_keys(
                input_row,
                required=("source_id", "document_role", "content_sha256"),
                context=input_context,
            )
            source_id = _identifier(input_row["source_id"], f"{input_context}.source_id")
            if source_id not in sources:
                raise ValueError(f"{input_context}.source_id does not resolve")
            if source_id != run_source_id:
                raise ValueError(
                    f"{input_context}.source_id must match the ingestion run source_id"
                )
            role = _text(input_row["document_role"], f"{input_context}.document_role")
            if role not in INGESTION_DOCUMENT_ROLES:
                raise ValueError(f"{input_context}.document_role is invalid")
            content_hash = _sha256(
                input_row["content_sha256"], f"{input_context}.content_sha256"
            )
            if content_hash != sources[source_id]["content_sha256"]:
                raise ValueError(f"{input_context}.content_sha256 is inconsistent")
            if sources[source_id]["ingestion_run_id"] != run_id:
                raise ValueError(f"{input_context} conflicts with the source ingestion run")
            if source_id in linked_sources:
                raise ValueError(f"source {source_id} has multiple ingestion-run links")
            acquired = datetime.fromisoformat(
                str(sources[source_id]["acquired_at"]).replace("Z", "+00:00")
            )
            if acquired > started:
                raise ValueError(f"{input_context} was acquired after the run started")
            linked_sources[source_id] = run_id
            input_keys.append((source_id, role))
        if input_keys != sorted(set(input_keys)):
            raise ValueError(f"{context}.inputs must be sorted and unique")
        if configured_source_count != len(inputs):
            raise ValueError(f"{context}.configuration.source_count is inconsistent")
        if configured_source_count != 1:
            raise ValueError(f"{context} must be source-local")
        validated.append(row)
    missing = sorted(set(sources) - set(linked_sources))
    if missing:
        raise ValueError(
            "source records without ingestion-run document links: " + ", ".join(missing)
        )
    return tuple(validated)


def _validate_evidence(
    value: object,
    index: int,
    sources: Mapping[str, Mapping[str, Any]],
    source_root: Path,
    recorded_at: str,
    verify_source_bytes: bool,
    source_bytes_cache: Mapping[str, bytes],
    source_text_cache: dict[str, tuple[str, ...]],
) -> Mapping[str, Any]:
    context = f"evidence[{index}]"
    evidence = _require_object(value, context)
    _require_keys(
        evidence,
        required=(
            "evidence_id",
            "source_id",
            "role",
            "locator",
            "excerpt",
            "verification",
            "fragment_sha256",
        ),
        optional=("capacity_assertions",),
        context=context,
    )
    _identifier(evidence["evidence_id"], f"{context}.evidence_id")
    source_id = _identifier(evidence["source_id"], f"{context}.source_id")
    if source_id not in sources:
        raise ValueError(f"{context}.source_id does not resolve")
    role = _text(evidence["role"], f"{context}.role")
    if role != "support":
        raise ValueError(f"{context}.role must be support in the v1 direct-source slice")
    locator = _text(evidence["locator"], f"{context}.locator")
    excerpt = _text(evidence["excerpt"], f"{context}.excerpt")
    if len(excerpt) > 500:
        raise ValueError(f"{context}.excerpt exceeds the 500-character release limit")
    excerpt_segments = _excerpt_segments(excerpt)
    if not _excerpt_is_substantive(excerpt_segments):
        raise ValueError(f"{context}.excerpt must contain substantive source text")
    verification = _require_object(
        evidence["verification"], f"{context}.verification"
    )
    _require_keys(
        verification,
        required=("method", "reviewer", "reviewed_at", "result"),
        context=f"{context}.verification",
    )
    method = _text(verification["method"], f"{context}.verification.method")
    media_type = str(sources[source_id]["media_type"])
    expected_method = (
        "normalized_text_segments_v1"
        if media_type in {"text/html", "application/json"}
        else "manual_pdf_visual_review_v1"
        if media_type == "application/pdf"
        else None
    )
    if method != expected_method:
        raise ValueError(f"{context}.verification.method is invalid for the source media")
    _identifier(verification["reviewer"], f"{context}.verification.reviewer")
    reviewed_at = _iso_timestamp(
        verification["reviewed_at"], f"{context}.verification.reviewed_at"
    )
    reviewed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    acquired = datetime.fromisoformat(
        str(sources[source_id]["acquired_at"]).replace("Z", "+00:00")
    )
    release_recorded = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    if reviewed < acquired:
        raise ValueError(f"{context}.verification predates source acquisition")
    if reviewed > release_recorded:
        raise ValueError(f"{context}.verification exceeds the knowledge cutoff")
    if verification["result"] != "matched_archived_source":
        raise ValueError(f"{context}.verification.result is invalid")
    capacity_assertions = [
        _validate_capacity_assertion(
            assertion,
            f"{context}.capacity_assertions[{assertion_index}]",
            excerpt,
        )
        for assertion_index, assertion in enumerate(
            _require_list(
                evidence.get("capacity_assertions", []),
                f"{context}.capacity_assertions",
            )
        )
    ]
    assertion_keys = [_canonical_bytes(assertion) for assertion in capacity_assertions]
    if assertion_keys != sorted(set(assertion_keys)):
        raise ValueError(f"{context}.capacity_assertions must be sorted and unique")
    if verify_source_bytes and method == "normalized_text_segments_v1":
        if source_id not in source_text_cache:
            raw = source_bytes_cache.get(source_id)
            if raw is None:
                raise ValueError(f"{context}.source archived bytes were not verified")
            source_text_cache[source_id] = _automatic_source_text(
                raw, media_type
            )
        _verify_excerpt_segments(excerpt, source_text_cache[source_id], context)
    declared_hash = _sha256(
        evidence["fragment_sha256"], f"{context}.fragment_sha256"
    )
    fragment_payload = {
        "source_id": source_id,
        "source_record_sha256": _source_record_sha256(sources[source_id]),
        "role": role,
        "locator": locator,
        "excerpt": excerpt,
        "verification": verification,
    }
    if "capacity_assertions" in evidence:
        fragment_payload["capacity_assertions"] = capacity_assertions
    expected_hash = hashlib.sha256(_canonical_bytes(fragment_payload)).hexdigest()
    if declared_hash != expected_hash:
        raise ValueError(f"{context}.fragment_sha256 does not bind the evidence content")
    return evidence


def _validate_evidence_ids(
    value: object,
    context: str,
    evidence_ids: set[str],
) -> list[str]:
    result = _string_list(value, context, nonempty=True)
    missing = sorted(set(result) - evidence_ids)
    if missing:
        raise ValueError(f"{context} contains unresolved evidence IDs: {', '.join(missing)}")
    return result


def _validate_geography(
    value: object,
    context: str,
    evidence_ids: set[str],
) -> Mapping[str, Any]:
    geography = _require_object(value, context)
    _require_keys(
        geography,
        required=(
            "country_code",
            "country",
            "admin1",
            "city",
            "latitude",
            "longitude",
            "precision_m",
            "geometry_scope",
            "valid_from",
            "evidence_ids",
        ),
        context=context,
    )
    country_code = _text(geography["country_code"], f"{context}.country_code")
    if not re.fullmatch(r"[A-Z]{2}", country_code):
        raise ValueError(f"{context}.country_code must be ISO alpha-2")
    for field in ("country", "admin1", "city"):
        _text(geography[field], f"{context}.{field}")
    latitude = geography["latitude"]
    longitude = geography["longitude"]
    precision = geography["precision_m"]
    geometry_scope = _text(geography["geometry_scope"], f"{context}.geometry_scope")
    if latitude is None or longitude is None:
        if latitude is not None or longitude is not None or precision is not None:
            raise ValueError(f"{context} coordinate and precision values must be all null or all set")
        if geometry_scope != "unresolved":
            raise ValueError(f"{context}.geometry_scope must be unresolved without coordinates")
    else:
        lat = _number(latitude, f"{context}.latitude")
        lon = _number(longitude, f"{context}.longitude")
        accuracy = _number(precision, f"{context}.precision_m")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or accuracy < 0:
            raise ValueError(f"{context} has invalid coordinate bounds or precision")
        if geometry_scope != "source_reported_facility_point":
            raise ValueError(f"{context}.geometry_scope is not facility-safe")
    _iso_date(geography["valid_from"], f"{context}.valid_from")
    _validate_evidence_ids(geography["evidence_ids"], f"{context}.evidence_ids", evidence_ids)
    return geography


def _validate_lifecycle(
    value: object,
    context: str,
    evidence_ids: set[str],
) -> Mapping[str, Any]:
    lifecycle = _require_object(value, context)
    _require_keys(
        lifecycle,
        required=("state", "statement", "as_of", "claim_kind", "method", "evidence_ids"),
        context=context,
    )
    state_value = _text(lifecycle["state"], f"{context}.state")
    if state_value not in LIFECYCLE_STATES:
        raise ValueError(f"{context}.state is not in the lifecycle vocabulary")
    _text(lifecycle["statement"], f"{context}.statement")
    _iso_date(lifecycle["as_of"], f"{context}.as_of")
    claim_kind = _text(lifecycle["claim_kind"], f"{context}.claim_kind")
    if claim_kind not in AI_CRITICAL_INPUT_CLAIM_KINDS:
        raise ValueError(f"{context}.claim_kind is invalid")
    _identifier(lifecycle["method"], f"{context}.method")
    _validate_evidence_ids(lifecycle["evidence_ids"], f"{context}.evidence_ids", evidence_ids)
    return lifecycle


def _validate_capability(
    value: object,
    context: str,
    scope: set[str],
    evidence_ids: set[str],
) -> Mapping[str, Any]:
    capability = _require_object(value, context)
    _require_keys(
        capability,
        required=(
            "category",
            "technology",
            "readiness",
            "valid_from",
            "claim_kind",
            "method",
            "evidence_ids",
        ),
        context=context,
    )
    category = _text(capability["category"], f"{context}.category")
    if category not in SCOPE_CATEGORIES or category not in scope:
        raise ValueError(f"{context}.category is outside the declared facility scope")
    _text(capability["technology"], f"{context}.technology")
    readiness = _text(capability["readiness"], f"{context}.readiness")
    if readiness not in READINESS_STATES:
        raise ValueError(f"{context}.readiness is invalid")
    _iso_date(capability["valid_from"], f"{context}.valid_from")
    claim_kind = _text(capability["claim_kind"], f"{context}.claim_kind")
    if claim_kind not in AI_CRITICAL_INPUT_CLAIM_KINDS:
        raise ValueError(f"{context}.claim_kind is invalid")
    _identifier(capability["method"], f"{context}.method")
    _validate_evidence_ids(capability["evidence_ids"], f"{context}.evidence_ids", evidence_ids)
    return capability


def _validate_capacity(
    value: object,
    context: str,
    scope: set[str],
    evidence_ids: set[str],
) -> Mapping[str, Any]:
    capacity = _require_object(value, context)
    _require_keys(
        capacity,
        required=(
            "metric",
            "basis",
            "unit",
            "low",
            "base",
            "high",
            "period_start",
            "period_end",
            "scope_kind",
            "input_output_basis",
            "quantity_semantics",
            "technology_scope",
            "valid_from",
            "claim_kind",
            "method",
            "notes",
            "evidence_ids",
        ),
        context=context,
    )
    metric = _identifier(capacity["metric"], f"{context}.metric")
    basis = _text(capacity["basis"], f"{context}.basis")
    if basis not in CAPACITY_BASES:
        raise ValueError(f"{context}.basis is not one of the five capacity bases")
    unit = _text(capacity["unit"], f"{context}.unit")
    if metric not in CAPACITY_METRIC_UNITS or unit not in CAPACITY_METRIC_UNITS[metric]:
        raise ValueError(f"{context} metric and unit are not in the v1 capacity registry")
    low = _decimal_number(capacity["low"], f"{context}.low")
    base = _decimal_number(capacity["base"], f"{context}.base")
    high = _decimal_number(capacity["high"], f"{context}.high")
    if not 0 <= low <= base <= high:
        raise ValueError(f"{context} must satisfy 0 <= low <= base <= high")
    period_start = capacity["period_start"]
    period_end = capacity["period_end"]
    if period_start is not None:
        period_start = _iso_date(period_start, f"{context}.period_start")
    if period_end is not None:
        period_end = _iso_date(period_end, f"{context}.period_end")
    if period_start and period_end and period_end <= period_start:
        raise ValueError(f"{context}.period_end must be later than period_start")
    if _text(capacity["scope_kind"], f"{context}.scope_kind") not in {
        "project_addition",
        "facility_total",
        "production_unit_total",
    }:
        raise ValueError(f"{context}.scope_kind is invalid")
    input_output_basis = _text(
        capacity["input_output_basis"], f"{context}.input_output_basis"
    )
    if input_output_basis not in CAPACITY_INPUT_OUTPUT_BASES:
        raise ValueError(f"{context}.input_output_basis is not in the v1 registry")
    quantity_semantics = _text(
        capacity["quantity_semantics"], f"{context}.quantity_semantics"
    )
    if CAPACITY_QUANTITY_SEMANTICS.get((metric, unit)) != quantity_semantics:
        raise ValueError(f"{context}.quantity_semantics is incompatible with metric and unit")
    technology_scope = _string_list(
        capacity["technology_scope"], f"{context}.technology_scope", nonempty=True
    )
    if not set(technology_scope) <= scope:
        raise ValueError(f"{context}.technology_scope exceeds facility scope")
    _iso_date(capacity["valid_from"], f"{context}.valid_from")
    claim_kind = _text(capacity["claim_kind"], f"{context}.claim_kind")
    if claim_kind not in AI_CRITICAL_INPUT_CLAIM_KINDS:
        raise ValueError(f"{context}.claim_kind is invalid")
    _identifier(capacity["method"], f"{context}.method")
    _text(capacity["notes"], f"{context}.notes")
    _validate_evidence_ids(capacity["evidence_ids"], f"{context}.evidence_ids", evidence_ids)
    return capacity


def _validate_facility(
    value: object,
    index: int,
    evidence_ids: set[str],
) -> Mapping[str, Any]:
    context = f"facilities[{index}]"
    facility = _require_object(value, context)
    _require_keys(
        facility,
        required=(
            "facility_key",
            "company",
            "name",
            "identity_scope",
            "identity_valid_from",
            "identity_evidence_ids",
            "geography",
            "scope_categories",
            "lifecycle",
            "capabilities",
            "capacities",
            "unknowns",
        ),
        context=context,
    )
    _identifier(facility["facility_key"], f"{context}.facility_key")
    company = _text(facility["company"], f"{context}.company")
    if company not in COMPANIES:
        raise ValueError(f"{context}.company is outside the seven-company allowlist")
    _text(facility["name"], f"{context}.name")
    identity_scope = _text(facility["identity_scope"], f"{context}.identity_scope")
    if identity_scope not in IDENTITY_SCOPES:
        raise ValueError(f"{context}.identity_scope is invalid")
    _iso_date(facility["identity_valid_from"], f"{context}.identity_valid_from")
    _validate_evidence_ids(
        facility["identity_evidence_ids"],
        f"{context}.identity_evidence_ids",
        evidence_ids,
    )
    _validate_geography(facility["geography"], f"{context}.geography", evidence_ids)
    scope = set(
        _string_list(
            facility["scope_categories"],
            f"{context}.scope_categories",
            nonempty=True,
        )
    )
    if not scope <= SCOPE_CATEGORIES:
        raise ValueError(f"{context}.scope_categories contains out-of-scope activity")
    _validate_lifecycle(facility["lifecycle"], f"{context}.lifecycle", evidence_ids)
    capabilities = _require_list(facility["capabilities"], f"{context}.capabilities")
    if not capabilities:
        raise ValueError(f"{context}.capabilities must not be empty")
    capability_signatures: set[bytes] = set()
    for capability_index, capability in enumerate(capabilities):
        _validate_capability(
            capability,
            f"{context}.capabilities[{capability_index}]",
            scope,
            evidence_ids,
        )
        signature = _canonical_bytes(capability)
        if signature in capability_signatures:
            raise ValueError(f"{context}.capabilities contains a duplicate capability")
        capability_signatures.add(signature)
    capability_scope = {str(row["category"]) for row in capabilities}
    if capability_scope != scope:
        raise ValueError(
            f"{context}.scope_categories must exactly match capability categories"
        )
    lifecycle_state = str(facility["lifecycle"]["state"])
    if lifecycle_state in {"lead", "announced", "site_control", "permitting", "permitted"} and any(
        row["readiness"] in {"equipment_installed", "qualified", "production"}
        for row in capabilities
    ):
        raise ValueError(
            f"{context} cannot promote an early lifecycle statement to installed, qualified, or production readiness"
        )
    capacities = _require_list(facility["capacities"], f"{context}.capacities")
    capacity_keys: set[tuple[str, ...]] = set()
    supported_bases: set[str] = set()
    for capacity_index, capacity in enumerate(capacities):
        validated = _validate_capacity(
            capacity,
            f"{context}.capacities[{capacity_index}]",
            scope,
            evidence_ids,
        )
        key = (
            str(validated["metric"]),
            str(validated["basis"]),
            str(validated["unit"]),
            str(validated["scope_kind"]),
            str(validated["input_output_basis"]),
            str(validated["quantity_semantics"]),
            str(validated["period_start"]),
            str(validated["period_end"]),
            ";".join(str(item) for item in validated["technology_scope"]),
            str(validated["valid_from"]),
        )
        if key in capacity_keys:
            raise ValueError(f"{context}.capacities contains a duplicate capacity slice")
        capacity_keys.add(key)
        supported_bases.add(str(validated["basis"]))
    unknowns = _require_object(facility["unknowns"], f"{context}.unknowns")
    _require_keys(
        unknowns,
        required=("yield", "utilization", "qualification", "capacity_bases"),
        context=f"{context}.unknowns",
    )
    for field in ("yield", "utilization", "qualification"):
        if unknowns[field] != "unknown":
            raise ValueError(f"{context}.unknowns.{field} must remain unknown")
    if any(row["readiness"] == "qualified" for row in capabilities):
        raise ValueError(
            f"{context} cannot publish qualified readiness while qualification is unknown"
        )
    unknown_bases = _string_list(
        unknowns["capacity_bases"], f"{context}.unknowns.capacity_bases"
    )
    expected_unknown = sorted(set(CAPACITY_BASES) - supported_bases)
    if unknown_bases != expected_unknown:
        raise ValueError(
            f"{context}.unknowns.capacity_bases must exactly cover unsupported bases"
        )
    if lifecycle_state in {"lead", "announced", "site_control", "permitting", "permitted"} and any(
        row["basis"] != "announced" for row in capacities
    ):
        raise ValueError(
            f"{context} cannot promote an early lifecycle statement beyond announced capacity"
        )
    if any(row["basis"] == "qualified" for row in capacities):
        raise ValueError(
            f"{context} cannot publish qualified capacity while qualification is unknown"
        )
    if any(row["basis"] == "economically_usable" for row in capacities):
        raise ValueError(
            f"{context} cannot publish economically usable capacity while yield and utilization are unknown"
        )
    return facility


def load_baseline(
    input_path: str | Path,
    source_root: str | Path,
    *,
    verify_source_bytes: bool = True,
    atlas_template_path: str | Path | None = None,
    code_path: str | Path | None = None,
    builder_script_path: str | Path | None = None,
    atlas_generator_path: str | Path | None = None,
) -> ValidatedBaseline:
    path = Path(input_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("baseline input must be a regular file")
    raw = path.read_bytes()
    spec = _require_object(_strict_json_bytes(raw, "baseline input"), "baseline input")
    _require_keys(
        spec,
        required=(
            "format",
            "release_id",
            "as_of",
            "recorded_at",
            "selection_version",
            "atlas_template_sha256",
            "code_sha256",
            "builder_script_sha256",
            "atlas_generator_sha256",
            "companies",
            "sources",
            "ingestion_runs",
            "evidence",
            "facilities",
        ),
        context="baseline input",
    )
    if spec["format"] != INPUT_FORMAT:
        raise ValueError(f"baseline input format must be {INPUT_FORMAT}")
    _identifier(spec["release_id"], "release_id")
    as_of = _iso_date(spec["as_of"], "as_of")
    recorded_at = _iso_timestamp(spec["recorded_at"], "recorded_at")
    if datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).date() < date.fromisoformat(as_of):
        raise ValueError("recorded_at cannot precede the world-state cutoff")
    if spec["selection_version"] != SELECTION_VERSION:
        raise ValueError(f"selection_version must be {SELECTION_VERSION}")
    expected_template_hash = _sha256(
        spec["atlas_template_sha256"], "atlas_template_sha256"
    )
    expected_code_hash = _sha256(spec["code_sha256"], "code_sha256")
    expected_builder_hash = _sha256(
        spec["builder_script_sha256"], "builder_script_sha256"
    )
    expected_generator_hash = _sha256(
        spec["atlas_generator_sha256"], "atlas_generator_sha256"
    )
    repository_root = Path(__file__).resolve().parents[1]
    template_path = (
        Path(atlas_template_path)
        if atlas_template_path is not None
        else repository_root / "web" / "atlas-template.html"
    )
    atlas_template_bytes = _read_pinned_artifact(
        template_path, expected_template_hash, "standalone atlas template"
    )
    code_bytes = _read_pinned_artifact(
        code_path if code_path is not None else Path(__file__),
        expected_code_hash,
        "AI-critical release code",
    )
    builder_script_bytes = _read_pinned_artifact(
        builder_script_path
        if builder_script_path is not None
        else repository_root / "scripts" / "build_ai_critical_release.py",
        expected_builder_hash,
        "AI-critical builder script",
    )
    atlas_generator_bytes = _read_pinned_artifact(
        atlas_generator_path
        if atlas_generator_path is not None
        else repository_root / "web" / "generate_atlas.py",
        expected_generator_hash,
        "standalone atlas generator",
    )
    companies = _require_list(spec["companies"], "companies")
    if tuple(companies) != COMPANIES:
        raise ValueError("companies must exactly match the ordered seven-company cohort")

    root = Path(source_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("source_root must be a real directory")
    sources: dict[str, Mapping[str, Any]] = {}
    source_bytes_cache: dict[str, bytes] = {}
    for index, value in enumerate(_require_list(spec["sources"], "sources")):
        source = _validate_source(
            value,
            index,
            root,
            verify_source_bytes=verify_source_bytes,
            source_bytes_cache=source_bytes_cache,
        )
        source_id = str(source["source_id"])
        if source_id in sources:
            raise ValueError(f"duplicate source_id: {source_id}")
        published_at = date.fromisoformat(str(source["published_at"]))
        retrieved_at = datetime.fromisoformat(
            str(source["retrieved_at"]).replace("Z", "+00:00")
        )
        acquired_at = datetime.fromisoformat(
            str(source["acquired_at"]).replace("Z", "+00:00")
        )
        release_recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if published_at > retrieved_at.date():
            raise ValueError(f"sources[{index}] was retrieved before publication")
        if retrieved_at > release_recorded_at:
            raise ValueError(f"sources[{index}] was retrieved after the knowledge cutoff")
        if acquired_at > release_recorded_at:
            raise ValueError(f"sources[{index}] was acquired after the knowledge cutoff")
        rights_reviewed_at = date.fromisoformat(str(source["rights"]["reviewed_at"]))
        if rights_reviewed_at < retrieved_at.date():
            raise ValueError(f"sources[{index}] rights review predates retrieval")
        if rights_reviewed_at > release_recorded_at.date():
            raise ValueError(f"sources[{index}] rights review exceeds the knowledge cutoff")
        sources[source_id] = source

    ingestion_runs = _validate_ingestion_runs(
        spec["ingestion_runs"], sources, recorded_at
    )

    evidence: dict[str, Mapping[str, Any]] = {}
    source_text_cache: dict[str, tuple[str, ...]] = {}
    for index, value in enumerate(_require_list(spec["evidence"], "evidence")):
        row = _validate_evidence(
            value,
            index,
            sources,
            root,
            recorded_at,
            verify_source_bytes,
            source_bytes_cache,
            source_text_cache,
        )
        evidence_id = str(row["evidence_id"])
        if evidence_id in evidence:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        evidence[evidence_id] = row

    facilities: list[Mapping[str, Any]] = []
    facility_keys: set[str] = set()
    company_counts: dict[str, int] = defaultdict(int)
    for index, value in enumerate(_require_list(spec["facilities"], "facilities")):
        facility = _validate_facility(value, index, set(evidence))
        for capacity_index, capacity in enumerate(facility["capacities"]):
            capacity_signature = _capacity_assertion_signature(capacity)
            matching_assertions = [
                assertion
                for evidence_id in capacity["evidence_ids"]
                for assertion in evidence[evidence_id].get("capacity_assertions", [])
                if _capacity_assertion_signature(assertion) == capacity_signature
            ]
            if not matching_assertions:
                raise ValueError(
                    f"facilities[{index}].capacities[{capacity_index}] is not "
                    "bound to an exact reviewed capacity assertion"
                )
        facility_dates = [
            ("identity_valid_from", facility["identity_valid_from"]),
            ("geography.valid_from", facility["geography"]["valid_from"]),
            ("lifecycle.as_of", facility["lifecycle"]["as_of"]),
            *(
                (f"capabilities[{capability_index}].valid_from", capability["valid_from"])
                for capability_index, capability in enumerate(facility["capabilities"])
            ),
            *(
                (f"capacities[{capacity_index}].valid_from", capacity["valid_from"])
                for capacity_index, capacity in enumerate(facility["capacities"])
            ),
        ]
        for field, value_date in facility_dates:
            if date.fromisoformat(str(value_date)) > date.fromisoformat(as_of):
                raise ValueError(f"facilities[{index}].{field} exceeds the world-state cutoff")
        key = str(facility["facility_key"])
        if key in facility_keys:
            raise ValueError(f"duplicate facility_key: {key}")
        facility_keys.add(key)
        company_counts[str(facility["company"])] += 1
        facilities.append(facility)
    invalid_company_counts = [
        f"{company}={company_counts[company]}"
        for company in COMPANIES
        if company_counts[company] != 1
    ]
    if invalid_company_counts:
        raise ValueError(
            "the v1 cohort requires exactly one row per company: "
            + ", ".join(invalid_company_counts)
        )
    expected_order = sorted(
        facilities,
        key=lambda row: (COMPANIES.index(str(row["company"])), str(row["facility_key"])),
    )
    if facilities != expected_order:
        raise ValueError("facilities must use deterministic company and facility-key order")

    used_evidence = {
        evidence_id
        for facility in facilities
        for evidence_id in _facility_evidence_ids(facility)
    }
    unused_evidence = sorted(set(evidence) - used_evidence)
    if unused_evidence:
        raise ValueError("unused evidence records are not allowed: " + ", ".join(unused_evidence))
    used_sources = {str(evidence[evidence_id]["source_id"]) for evidence_id in used_evidence}
    unused_sources = sorted(set(sources) - used_sources)
    if unused_sources:
        raise ValueError("unused source records are not allowed: " + ", ".join(unused_sources))

    return ValidatedBaseline(
        spec=spec,
        source_root=root.resolve(),
        sources=sources,
        ingestion_runs=ingestion_runs,
        evidence=evidence,
        facilities=tuple(facilities),
        input_bytes=raw,
        input_sha256=hashlib.sha256(raw).hexdigest(),
        source_bytes_verified=verify_source_bytes,
        atlas_template_bytes=atlas_template_bytes,
        code_bytes=code_bytes,
        builder_script_bytes=builder_script_bytes,
        atlas_generator_bytes=atlas_generator_bytes,
    )


def _facility_evidence_ids(facility: Mapping[str, Any]) -> set[str]:
    result = set(facility["identity_evidence_ids"])
    result.update(facility["geography"]["evidence_ids"])
    result.update(facility["lifecycle"]["evidence_ids"])
    for capability in facility["capabilities"]:
        result.update(capability["evidence_ids"])
    for capacity in facility["capacities"]:
        result.update(capacity["evidence_ids"])
    return result


def _stable_uuid(*parts: object) -> str:
    return str(uuid.uuid5(_NAMESPACE, "\x1f".join(str(part) for part in parts)))


def _producing_run_descriptor(baseline: ValidatedBaseline) -> dict[str, Any]:
    return {
        "release_id": baseline.spec["release_id"],
        "schema_version": SCHEMA_VERSION,
        "code_version": CODE_VERSION,
        "configuration_sha256": baseline.input_sha256,
        "code_sha256": baseline.spec["code_sha256"],
        "builder_script_sha256": baseline.spec["builder_script_sha256"],
        "atlas_generator_sha256": baseline.spec["atlas_generator_sha256"],
        "atlas_template_sha256": baseline.spec["atlas_template_sha256"],
        "model_version": MODEL_VERSION,
        "selection_version": SELECTION_VERSION,
        "entity_resolution_version": ENTITY_RESOLUTION_VERSION,
        "source_documents": [
            {
                "source_id": source_id,
                "content_sha256": source["content_sha256"],
                "source_record_sha256": _source_record_sha256(source),
                "ingestion_run_id": source["ingestion_run_id"],
            }
            for source_id, source in sorted(baseline.sources.items())
        ],
    }


def _producing_run_id(baseline: ValidatedBaseline) -> str:
    descriptor_sha256 = hashlib.sha256(
        _canonical_bytes(_producing_run_descriptor(baseline))
    ).hexdigest()
    return _stable_uuid("ai-critical-producing-run", descriptor_sha256)


def _source_record(
    baseline: ValidatedBaseline,
    source_id: str,
) -> dict[str, Any]:
    payload = dict(baseline.sources[source_id])
    payload["source_record_sha256"] = _source_record_sha256(payload)
    return payload


def _evidence_links(
    baseline: ValidatedBaseline,
    evidence_ids: Sequence[str],
) -> list[dict[str, str]]:
    return [
        {
            "evidence_id": evidence_id,
            "role": str(baseline.evidence[evidence_id]["role"]),
            "fragment_sha256": str(
                baseline.evidence[evidence_id]["fragment_sha256"]
            ),
        }
        for evidence_id in evidence_ids
    ]


def _claim(
    baseline: ValidatedBaseline,
    facility_id: str,
    facility_key: str,
    *,
    predicate: str,
    value_kind: str,
    value: object,
    valid_from: str,
    claim_kind: str,
    method: str,
    evidence_ids: Sequence[str],
    notes: str | None = None,
    dependencies: Sequence[str] = (),
) -> dict[str, Any]:
    evidence_id_list = list(evidence_ids)
    evidence_links = _evidence_links(baseline, evidence_id_list)
    dependency_list = list(dependencies)
    signature = hashlib.sha256(
        _canonical_bytes(
            {
                "value": value,
                "valid_from": valid_from,
                "claim_kind": claim_kind,
                "method": method,
                "evidence_links": evidence_links,
                "dependencies": dependency_list,
                "notes": notes,
                "recorded_at": baseline.spec["recorded_at"],
                "producing_run_id": _producing_run_id(baseline),
            }
        )
    ).hexdigest()
    claim_id = _stable_uuid("claim", facility_key, predicate, signature)
    return {
        "claim_id": claim_id,
        "subject_entity_id": facility_id,
        "subject_stable_key": facility_key,
        "predicate": predicate,
        "value_kind": value_kind,
        "value": value,
        "valid_from": valid_from,
        "valid_to": None,
        "recorded_at": baseline.spec["recorded_at"],
        "superseded_at": None,
        "claim_kind": claim_kind,
        "method": method,
        "producing_run_id": _producing_run_id(baseline),
        "review_state": "reviewed",
        "claim_confidence": None,
        "confidence_scope": "unknown_not_calibrated",
        "extraction_confidence": None,
        "extraction_confidence_scope": "unknown_not_calibrated",
        "notes": notes,
        "evidence_ids": evidence_id_list,
        "evidence_links": evidence_links,
        "dependencies": dependency_list,
    }


def _facility_claims(
    baseline: ValidatedBaseline,
    facility: Mapping[str, Any],
) -> list[dict[str, Any]]:
    facility_key = str(facility["facility_key"])
    facility_id = _stable_uuid("facility", facility_key)
    identity_evidence = facility["identity_evidence_ids"]
    identity_valid_from = str(facility["identity_valid_from"])
    rows = [
        _claim(
            baseline,
            facility_id,
            facility_key,
            predicate="facility.name",
            value_kind="scalar",
            value={"scalar_type": "text", "value": facility["name"], "unit": None},
            valid_from=identity_valid_from,
            claim_kind="reconciled_fact",
            method="ai_critical_facility_identity_review_v1",
            evidence_ids=identity_evidence,
            notes="Reviewed canonical display name at the declared identity scope.",
        ),
        _claim(
            baseline,
            facility_id,
            facility_key,
            predicate="cohort.company",
            value_kind="scalar",
            value={"scalar_type": "text", "value": facility["company"], "unit": None},
            valid_from=identity_valid_from,
            claim_kind="reconciled_fact",
            method="ai_critical_company_scope_review_v1",
            evidence_ids=identity_evidence,
            notes="Cohort attribution only; it is not a general ownership or operator relationship.",
        ),
        _claim(
            baseline,
            facility_id,
            facility_key,
            predicate="facility.identity_scope",
            value_kind="scalar",
            value={
                "scalar_type": "controlled_concept",
                "value": facility["identity_scope"],
                "unit": None,
            },
            valid_from=identity_valid_from,
            claim_kind="reconciled_fact",
            method="ai_critical_facility_identity_review_v1",
            evidence_ids=identity_evidence,
        ),
    ]
    geography = facility["geography"]
    for field in ("country_code", "country", "admin1", "city"):
        rows.append(
            _claim(
                baseline,
                facility_id,
                facility_key,
                predicate=f"geography.{field}",
                value_kind="scalar",
                value={"scalar_type": "text", "value": geography[field], "unit": None},
                valid_from=str(geography["valid_from"]),
                claim_kind="reconciled_fact",
                method="ai_critical_geography_normalization_v1",
                evidence_ids=geography["evidence_ids"],
                notes="Reviewed canonical geography; normalized labels and codes are not asserted as verbatim source text.",
            )
        )
    if geography["latitude"] is not None:
        rows.append(
            _claim(
                baseline,
                facility_id,
                facility_key,
                predicate="geography.point",
                value_kind="geometry",
                value={
                    "type": "Point",
                    "coordinates": [geography["longitude"], geography["latitude"]],
                    "crs": "EPSG:4326",
                    "precision_m": geography["precision_m"],
                    "geometry_scope": geography["geometry_scope"],
                },
                valid_from=str(geography["valid_from"]),
                claim_kind="source_statement",
                method="ai_critical_source_geometry_capture_v1",
                evidence_ids=geography["evidence_ids"],
            )
        )
    lifecycle = facility["lifecycle"]
    lifecycle_summary_kind = str(lifecycle["claim_kind"])
    lifecycle_statement_claim = _claim(
        baseline,
        facility_id,
        facility_key,
        predicate=(
            "lifecycle.source_statement"
            if lifecycle_summary_kind == "source_statement"
            else "lifecycle.evidence_summary"
        ),
        value_kind="scalar",
        value={
            "scalar_type": "text",
            "value": lifecycle["statement"],
            "unit": None,
        },
        valid_from=str(lifecycle["as_of"]),
        claim_kind=lifecycle_summary_kind,
        method=(
            "ai_critical_lifecycle_statement_capture_v1"
            if lifecycle_summary_kind == "source_statement"
            else "ai_critical_lifecycle_summary_review_v1"
        ),
        evidence_ids=lifecycle["evidence_ids"],
        notes=(
            None
            if lifecycle_summary_kind == "source_statement"
            else "Reviewed evidence summary; not asserted as a verbatim source statement."
        ),
    )
    lifecycle_dependencies = (
        [str(lifecycle_statement_claim["claim_id"])]
        if lifecycle["claim_kind"] == "reconciled_fact"
        else []
    )
    lifecycle_stage_claim = _claim(
        baseline,
        facility_id,
        facility_key,
        predicate="lifecycle.stage",
        value_kind="scalar",
        value={
            "scalar_type": "controlled_concept",
            "value": lifecycle["state"],
            "unit": None,
        },
        valid_from=str(lifecycle["as_of"]),
        claim_kind=str(lifecycle["claim_kind"]),
        method=str(lifecycle["method"]),
        evidence_ids=lifecycle["evidence_ids"],
        dependencies=lifecycle_dependencies,
    )
    rows.extend([lifecycle_stage_claim, lifecycle_statement_claim])
    for capability in facility["capabilities"]:
        rows.append(
            _claim(
                baseline,
                facility_id,
                facility_key,
                predicate="facility.capability",
                value_kind="capability",
                value={
                    "capability_type": "ai_critical_manufacturing_activity",
                    "category": capability["category"],
                    "technology": capability["technology"],
                    "readiness": capability["readiness"],
                },
                valid_from=str(capability["valid_from"]),
                claim_kind=str(capability["claim_kind"]),
                method=str(capability["method"]),
                evidence_ids=capability["evidence_ids"],
            )
        )
    for capacity in facility["capacities"]:
        rows.append(
            _claim(
                baseline,
                facility_id,
                facility_key,
                predicate=f"capacity.{capacity['metric']}",
                value_kind="capacity",
                value={
                    "metric": capacity["metric"],
                    "basis": capacity["basis"],
                    "unit": capacity["unit"],
                    "low": capacity["low"],
                    "base": capacity["base"],
                    "high": capacity["high"],
                    "period_start": capacity["period_start"],
                    "period_end": capacity["period_end"],
                    "scope_kind": capacity["scope_kind"],
                    "input_output_basis": capacity["input_output_basis"],
                    "quantity_semantics": capacity["quantity_semantics"],
                    "technology_scope": capacity["technology_scope"],
                },
                valid_from=str(capacity["valid_from"]),
                claim_kind=str(capacity["claim_kind"]),
                method=str(capacity["method"]),
                evidence_ids=capacity["evidence_ids"],
                notes=str(capacity["notes"]),
            )
        )
    return sorted(rows, key=lambda row: (row["predicate"], row["claim_id"]))


def _is_exact_calendar_quarter(
    period_start: object,
    period_end: object,
) -> bool:
    if not isinstance(period_start, str) or not isinstance(period_end, str):
        return False
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError:
        return False
    if start.day != 1 or start.month not in {1, 4, 7, 10}:
        return False
    expected_end = (
        date(start.year + 1, 1, 1)
        if start.month == 10
        else date(start.year, start.month + 3, 1)
    )
    return end == expected_end


def _supply_capacity_ineligibility(value: Mapping[str, Any]) -> str | None:
    if value.get("quantity_semantics") != "quarter_total":
        return "quantity_semantics_not_quarter_total"
    if value.get("unit") not in {"units/quarter", "wafers/quarter"}:
        return "unit_not_quarter_total"
    if value.get("input_output_basis") != "source_stated_quarter_total":
        return "input_output_basis_not_quarter_total"
    if not _is_exact_calendar_quarter(
        value.get("period_start"), value.get("period_end")
    ):
        return "period_not_exact_calendar_quarter"
    return None


def materialize_baseline(baseline: ValidatedBaseline) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    facility_rows: list[dict[str, Any]] = []
    capacity_rows: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    supply_capacity_rows: list[dict[str, Any]] = []
    supply_facility_rows: list[dict[str, Any]] = []
    evidence_to_claims: dict[str, list[str]] = defaultdict(list)

    for facility in baseline.facilities:
        facility_claims = _facility_claims(baseline, facility)
        claims.extend(facility_claims)
        for claim in facility_claims:
            for evidence_id in claim["evidence_ids"]:
                evidence_to_claims[evidence_id].append(str(claim["claim_id"]))
        facility_id = _stable_uuid("facility", facility["facility_key"])
        supported_bases = {str(row["basis"]) for row in facility["capacities"]}
        capability_summary = "; ".join(
            f"{row['category']} | {row['technology']} | {row['readiness']}"
            for row in facility["capabilities"]
        )
        source_ids = sorted(
            {
                str(baseline.evidence[evidence_id]["source_id"])
                for evidence_id in _facility_evidence_ids(facility)
            }
        )
        geography = facility["geography"]
        facility_row: dict[str, Any] = {
            "facility_id": facility_id,
            "stable_key": facility["facility_key"],
            "company": facility["company"],
            "facility_name": facility["name"],
            "identity_scope": facility["identity_scope"],
            "country_code": geography["country_code"],
            "country": geography["country"],
            "admin1": geography["admin1"],
            "city": geography["city"],
            "latitude": "" if geography["latitude"] is None else geography["latitude"],
            "longitude": "" if geography["longitude"] is None else geography["longitude"],
            "geometry_scope": geography["geometry_scope"],
            "geometry_precision_m": (
                "" if geography["precision_m"] is None else geography["precision_m"]
            ),
            "scope_categories": ";".join(facility["scope_categories"]),
            "lifecycle_state": facility["lifecycle"]["state"],
            "lifecycle_as_of": facility["lifecycle"]["as_of"],
            "lifecycle_statement": facility["lifecycle"]["statement"],
            "capabilities": capability_summary,
            "yield": "unknown",
            "utilization": "unknown",
            "qualification": "unknown",
            "source_ids": ";".join(source_ids),
            "claim_count": len(facility_claims),
        }
        for basis in CAPACITY_BASES:
            facility_row[f"capacity_{basis}"] = (
                "supported_claim_present" if basis in supported_bases else "unknown"
            )
        facility_rows.append(facility_row)

        for claim in facility_claims:
            if claim["value_kind"] != "capacity":
                continue
            value = claim["value"]
            capacity_rows.append(
                {
                    "claim_id": claim["claim_id"],
                    "facility_id": facility_id,
                    "company": facility["company"],
                    "facility_name": facility["name"],
                    "metric": value["metric"],
                    "basis": value["basis"],
                    "unit": value["unit"],
                    "low": value["low"],
                    "base": value["base"],
                    "high": value["high"],
                    "period_start": value["period_start"] or "",
                    "period_end": value["period_end"] or "",
                    "scope_kind": value["scope_kind"],
                    "input_output_basis": value["input_output_basis"],
                    "quantity_semantics": value["quantity_semantics"],
                    "technology_scope": ";".join(value["technology_scope"]),
                    "valid_from": claim["valid_from"],
                    "claim_kind": claim["claim_kind"],
                    "method": claim["method"],
                    "notes": claim["notes"],
                    "evidence_ids": ";".join(claim["evidence_ids"]),
                }
            )

        geometry = None
        if geography["latitude"] is not None:
            geometry = {
                "type": "Point",
                "coordinates": [geography["longitude"], geography["latitude"]],
            }
        source_urls = sorted(
            {str(baseline.sources[source_id]["url"]) for source_id in source_ids}
        )
        features.append(
            {
                "type": "Feature",
                "id": facility_id,
                "geometry": geometry,
                "properties": {
                    "entity_id": facility_id,
                    "entity_kind": {
                        "named_facility": "facility",
                        "campus_scope": "campus",
                        "project_site_scope": "project_site",
                    }[str(facility["identity_scope"])],
                    "stable_key": facility["facility_key"],
                    "name": facility["name"],
                    "company": facility["company"],
                    "identity_scope": facility["identity_scope"],
                    "country_code": geography["country_code"],
                    "country": geography["country"],
                    "admin1": geography["admin1"],
                    "city": geography["city"],
                    "geometry_scope": geography["geometry_scope"],
                    "geometry_precision_m": geography["precision_m"],
                    "scope_categories": facility["scope_categories"],
                    "lifecycle_state": facility["lifecycle"]["state"],
                    "lifecycle_as_of": facility["lifecycle"]["as_of"],
                    "lifecycle_statement": facility["lifecycle"]["statement"],
                    "capabilities": [
                        {
                            "capability_type": "facility_activity",
                            "value": row["category"],
                            "technology": row["technology"],
                            "readiness": row["readiness"],
                            "valid_from": row["valid_from"],
                            "claim_kind": row["claim_kind"],
                            "method": row["method"],
                            "evidence_ids": row["evidence_ids"],
                        }
                        for row in facility["capabilities"]
                    ],
                    "capacities": [
                        {
                            "metric": row["metric"],
                            "basis": row["basis"],
                            "unit": row["unit"],
                            "low": row["low"],
                            "base": row["base"],
                            "high": row["high"],
                            "period_start": row["period_start"],
                            "period_end": row["period_end"],
                            "scope_kind": row["scope_kind"],
                            "input_output_basis": row["input_output_basis"],
                            "quantity_semantics": row["quantity_semantics"],
                            "technology_scope": row["technology_scope"],
                            "valid_from": row["valid_from"],
                            "claim_kind": row["claim_kind"],
                            "method": row["method"],
                            "notes": row["notes"],
                            "evidence_ids": row["evidence_ids"],
                        }
                        for row in facility["capacities"]
                    ],
                    "unknowns": facility["unknowns"],
                    "capacity_basis_status": {
                        basis: (
                            "supported_claim_present"
                            if basis in supported_bases
                            else "unknown"
                        )
                        for basis in CAPACITY_BASES
                    },
                    "scalar_fields": {
                        "lifecycle.stage": [
                            {"value": facility["lifecycle"]["state"]}
                        ],
                        "geography.city": [{"value": geography["city"]}],
                        "cohort.company": [{"value": facility["company"]}],
                    },
                    "source_urls": source_urls,
                    "claim_count": len(facility_claims),
                },
            }
        )
        supply_facility_rows.append(
            {
                "format": "semiconductor-atlas-supply-intelligence-facility-v1",
                "schema_version": "supply-intelligence-facility-scope-v1",
                "record_type": "facility_scope",
                "facility_id": facility_id,
                "company": facility["company"],
                "facility_name": facility["name"],
                "identity_scope": facility["identity_scope"],
                "geography": {
                    "country_code": geography["country_code"],
                    "admin1": geography["admin1"],
                    "city": geography["city"],
                },
                "scope_categories": facility["scope_categories"],
                "lifecycle": {
                    "state": facility["lifecycle"]["state"],
                    "as_of": facility["lifecycle"]["as_of"],
                },
                "unknowns": facility["unknowns"],
                "atlas_release_id": baseline.spec["release_id"],
            }
        )

    company_order = {company: index for index, company in enumerate(COMPANIES)}
    claims.sort(
        key=lambda row: (
            company_order[
                next(
                    str(facility["company"])
                    for facility in baseline.facilities
                    if facility["facility_key"] == row["subject_stable_key"]
                )
            ],
            row["subject_stable_key"],
            row["predicate"],
            row["claim_id"],
        )
    )
    for claim in claims:
        if claim["value_kind"] != "capacity":
            continue
        value = claim["value"]
        period_start = value["period_start"]
        period_end = value["period_end"]
        if _supply_capacity_ineligibility(value) is not None:
            continue
        supply_capacity_rows.append(
            {
                "format": "semiconductor-atlas-supply-intelligence-capacity-v1",
                "schema_version": "supply-intelligence-capacity-quarter-total-v1",
                "record_type": "capacity_claim",
                "claim_id": claim["claim_id"],
                "entity_id": claim["subject_entity_id"],
                "predicate": claim["predicate"],
                "claim_kind": claim["claim_kind"],
                "metric": value["metric"],
                "basis": value["basis"],
                "unit": value["unit"],
                "quantity_semantics": value["quantity_semantics"],
                "low": value["low"],
                "base": value["base"],
                "high": value["high"],
                "period_start": period_start,
                "period_end": period_end,
                "scope_kind": value["scope_kind"],
                "input_output_basis": value["input_output_basis"],
                "technology_scope": value["technology_scope"],
                "valid_from": claim["valid_from"],
                "recorded_at": claim["recorded_at"],
                "method": claim["method"],
                "confidence": claim["claim_confidence"],
                "notes": claim["notes"],
                "evidence_ids": claim["evidence_ids"],
                "evidence_link_count": len(claim["evidence_links"]),
                "dependencies": claim["dependencies"],
                "dependency_count": len(claim["dependencies"]),
                "atlas_release_id": baseline.spec["release_id"],
            }
        )
    evidence_rows = []
    for evidence_id, evidence in sorted(baseline.evidence.items()):
        source = _source_record(baseline, str(evidence["source_id"]))
        payload = {
            "evidence_id": evidence_id,
            "role": evidence["role"],
            "fragment_sha256": evidence["fragment_sha256"],
            "source_id": source["source_id"],
            "source_family": source["source_family"],
            "source_type": source["source_type"],
            "publisher": source["publisher"],
            "title": source["title"],
            "url": source["url"],
            "published_at": source["published_at"],
            "published_at_precision": source["published_at_precision"],
            "published_at_basis": source["published_at_basis"],
            "retrieved_at": source["retrieved_at"],
            "acquired_at": source["acquired_at"],
            "content_sha256": source["content_sha256"],
            "archive_path": source["archive_path"],
            "media_type": source["media_type"],
            "language": source["language"],
            "adapter_version": source["adapter_version"],
            "ingestion_run_id": source["ingestion_run_id"],
            "source_record_sha256": source["source_record_sha256"],
            "rights": source["rights"],
            "locator": evidence["locator"],
            "excerpt": evidence["excerpt"],
            "verification": evidence["verification"],
            "claim_ids": sorted(evidence_to_claims[evidence_id]),
            "claim_links": [
                {"claim_id": claim_id, "role": evidence["role"]}
                for claim_id in sorted(evidence_to_claims[evidence_id])
            ],
        }
        if "capacity_assertions" in evidence:
            payload["capacity_assertions"] = evidence["capacity_assertions"]
        payload["evidence_sha256"] = evidence["fragment_sha256"]
        evidence_rows.append(payload)
    capacity_counts = {
        basis: sum(1 for row in capacity_rows if row["basis"] == basis)
        for basis in CAPACITY_BASES
    }
    coverage = {
        "format": "semiconductor-atlas-ai-critical-coverage-v1",
        "release_id": baseline.spec["release_id"],
        "as_of": baseline.spec["as_of"],
        "recorded_at": baseline.spec["recorded_at"],
        "companies": list(COMPANIES),
        "company_facility_counts": {
            company: sum(1 for row in baseline.facilities if row["company"] == company)
            for company in COMPANIES
        },
        "facility_count": len(baseline.facilities),
        "claim_count": len(claims),
        "evidence_fragment_count": len(evidence_rows),
        "source_document_count": len(baseline.sources),
        "mapped_facility_count": sum(
            1 for row in baseline.facilities if row["geography"]["latitude"] is not None
        ),
        "scope_category_counts": {
            category: sum(
                1 for row in baseline.facilities if category in row["scope_categories"]
            )
            for category in sorted(SCOPE_CATEGORIES)
        },
        "capacity_claims_by_basis": capacity_counts,
        "unknown_counts": {
            "yield": len(baseline.facilities),
            "utilization": len(baseline.facilities),
            "qualification": len(baseline.facilities),
            "capacity_bases": {
                basis: sum(
                    1
                    for row in baseline.facilities
                    if basis in row["unknowns"]["capacity_bases"]
                )
                for basis in CAPACITY_BASES
            },
        },
        "known_limitations": [
            "This is a bounded seven-company baseline, not a global or company-complete facility census.",
            "Project-site-scope rows are not asserted to be individual operating fabs or lines.",
            "Null GeoJSON geometry means the evidence supports named geography but not a releasable facility point.",
            "Announcements and expected milestones are not operating, installed, qualified, or economically usable capacity.",
            "Yield, utilization, and qualification remain unknown for every facility in v1.",
            "Capacity bases with no direct claim remain unknown; zero claims do not mean zero physical capacity.",
        ],
    }
    return {
        "claims": claims,
        "evidence": evidence_rows,
        "facilities": facility_rows,
        "capacity": sorted(
            capacity_rows,
            key=lambda row: (
                company_order[str(row["company"])],
                str(row["facility_id"]),
                str(row["basis"]),
                str(row["metric"]),
            ),
        ),
        "geojson": {
            "type": "FeatureCollection",
            "atlas_release_id": baseline.spec["release_id"],
            "atlas_as_of": baseline.spec["as_of"],
            "atlas_recorded_at": baseline.spec["recorded_at"],
            "attribution": sorted(
                {
                    str(source["rights"]["attribution"])
                    for source in baseline.sources.values()
                }
            ),
            "features": features,
        },
        "supply_intelligence": supply_capacity_rows,
        "supply_intelligence_facilities": supply_facility_rows,
        "supply_intelligence_contract": {
            "format": "semiconductor-atlas-supply-intelligence-export-contract-v1",
            "schema_version": "supply-intelligence-capacity-quarter-total-v1",
            "atlas_release_id": baseline.spec["release_id"],
            "record_count": len(supply_capacity_rows),
            "consumer_boundary": {
                "intended_consumer": "Supply Intelligence handoff",
                "adapter_compatibility": "new_schema_not_legacy_atlas_adapter",
                "confidence_policy": "null_means_unknown_not_calibrated",
            },
            "eligibility": {
                "period": "exact_calendar_quarter_half_open",
                "quantity_semantics": "quarter_total",
                "required_fields": [
                    "format",
                    "schema_version",
                    "record_type",
                    "claim_id",
                    "entity_id",
                    "predicate",
                    "claim_kind",
                    "metric",
                    "basis",
                    "unit",
                    "quantity_semantics",
                    "low",
                    "base",
                    "high",
                    "period_start",
                    "period_end",
                    "scope_kind",
                    "input_output_basis",
                    "technology_scope",
                    "valid_from",
                    "recorded_at",
                    "method",
                    "confidence",
                    "notes",
                    "evidence_ids",
                    "evidence_link_count",
                    "dependencies",
                    "dependency_count",
                    "atlas_release_id",
                ],
            },
            "excluded_capacity_claims": [
                {
                    "claim_id": row["claim_id"],
                    "reason": _supply_capacity_ineligibility(row),
                }
                for row in capacity_rows
                if _supply_capacity_ineligibility(row) is not None
            ],
        },
        "coverage": coverage,
    }


_FACILITY_FIELDS = (
    "facility_id",
    "stable_key",
    "company",
    "facility_name",
    "identity_scope",
    "country_code",
    "country",
    "admin1",
    "city",
    "latitude",
    "longitude",
    "geometry_scope",
    "geometry_precision_m",
    "scope_categories",
    "lifecycle_state",
    "lifecycle_as_of",
    "lifecycle_statement",
    "capabilities",
    "capacity_announced",
    "capacity_physical_construction",
    "capacity_tool_installed",
    "capacity_qualified",
    "capacity_economically_usable",
    "yield",
    "utilization",
    "qualification",
    "source_ids",
    "claim_count",
)

_CAPACITY_FIELDS = (
    "claim_id",
    "facility_id",
    "company",
    "facility_name",
    "metric",
    "basis",
    "unit",
    "low",
    "base",
    "high",
    "period_start",
    "period_end",
    "scope_kind",
    "input_output_basis",
    "quantity_semantics",
    "technology_scope",
    "valid_from",
    "claim_kind",
    "method",
    "notes",
    "evidence_ids",
)


def _methodology_text(baseline: ValidatedBaseline) -> str:
    return f"""# AI-Critical Manufacturing Baseline v1 methodology

- Release ID: `{baseline.spec['release_id']}`
- World-state cutoff: `{baseline.spec['as_of']}`
- Knowledge cutoff: `{baseline.spec['recorded_at']}`
- Selection method: `{SELECTION_VERSION}`

This release is a bounded, evidence-first baseline for exactly TSMC, Samsung, Intel, Micron,
SK hynix, Amkor, and ASE. It includes only leading-edge logic, HBM fabrication, HBM packaging,
advanced packaging, and advanced test. Every source document is official, was published on or
after 2024-01-01, and is bound to locally archived bytes by size and SHA-256 before release.

Facility identity is deliberately scoped as `named_facility`, `campus_scope`, or
`project_site_scope`. The latter two labels prevent a company or government project description
from being promoted into a more precise fab, building, line, operator, or ownership assertion.
City, region, and country remain publishable when point geometry is unknown. Null GeoJSON geometry
is intentional and must not be backfilled with an inferred facility coordinate. Canonical display
names, normalized geography labels and codes, lifecycle summaries, and mapped lifecycle stages are
reviewed `reconciled_fact` claims, not purported verbatim source statements.

Lifecycle values are evidence-bounded. Announcement, funding, project-type, groundbreaking, and
expected-start statements do not establish operation, tool installation, customer qualification,
yield, utilization, or economically usable output. Capability readiness is stored separately from
lifecycle. All capacity uses exactly one of the five bases: `announced`,
`physical_construction`, `tool_installed`, `qualified`, or `economically_usable`. Missing bases are
reported as unknown, never zero. Differently scoped or differently unitized capacity rows are not
summed.

The fixed v1 numeric-claim grammar admits only the cohort's directly supported `announced` basis.
The other four bases remain present in every facility's five-basis unknown contract and release
coverage, but a later release must add a separately reviewed and tested evidence grammar before it
can publish a numeric claim on any of them. This prevents construction or equipment-installation
lifecycle language from being reused as numeric capacity support.

Every published claim names one or more evidence fragment IDs. `evidence.jsonl` binds each fragment
to publisher, official URL, publication and retrieval clocks, exact-byte hash, source-ingestion
run, rights decision, precise locator, and a short excerpt. V1 accepts support links only; the
local release gate resolves ordered HTML/JSON excerpt segments against normalized archived text and
requires dated manual visual review for PDF fragments. The fragment hash binds the source record,
locator, excerpt, and verification record. Numeric capacity also requires a typed assertion inside
the linked fragment that exactly matches its metric, basis, unit, range, period, scope,
input/output basis, quantity semantics, and technology scope. The loader reconciles the assertion's
number and time language to the verified excerpt, so a monthly rate cannot be relabeled as a
quarterly total. Each claim ID binds those fragment hashes, the claim's system-time acceptance, and
its producing run. Re-recording the same proposition
under a different accepted run therefore creates a new immutable claim version rather than silently
changing an existing ID. Claim and extraction confidence remain null because neither is calibrated. The release does
not redistribute source bytes classified as
metadata-and-excerpt-only; their local archive locators and hashes remain sufficient for authorized
replay. Approximate publisher dates retain their explicit precision and normalization basis rather
than masquerading as exact days. `producing_run.json` resolves every claim's producing run and pins
the schema, code, configuration, selection, identity-review, model, and template versions. The
deterministic manifest hashes every managed release file.

The Supply Intelligence capacity export is a deliberately smaller, fail-closed consumer surface.
Only capacity with an exact half-open calendar-quarter period and quarter-total semantics is
eligible; v1 has zero eligible rows because the two direct Amkor statements are monthly future
rates with no applicability period. A separately versioned facility-scope JSONL carries the seven
bounded rows and explicit unknowns without dangling capacity IDs. Consumers must resolve claim and
evidence detail from the full release. These are new handoff schemas, not a claim that the existing
sibling Supply Intelligence legacy adapter can ingest this release directly. Handoff confidence is
null and explicitly means unknown/not calibrated; no numeric value is invented for compatibility.
"""


def _attribution_text(baseline: ValidatedBaseline) -> str:
    lines = ["# Attribution", ""]
    attributions = sorted(
        {str(source["rights"]["attribution"]) for source in baseline.sources.values()}
    )
    lines.extend(f"- {attribution}" for attribution in attributions)
    lines.extend(["", "## Source documents", ""])
    for source_id, source in sorted(baseline.sources.items()):
        lines.append(
            f"- `{source_id}` — {source['publisher']}, [{source['title']}]({source['url']}), "
            f"published {source['published_at']} ({source['published_at_precision']} precision; "
            f"{source['published_at_basis']}), retrieved {source['retrieved_at']}, "
            f"SHA-256 `{source['content_sha256']}`; {source['rights']['license']}; "
            f"redistribution `{source['rights']['redistribution']}`."
        )
    lines.append("")
    return "\n".join(lines)


def _readme_text(baseline: ValidatedBaseline, materialized: Mapping[str, Any]) -> str:
    coverage = materialized["coverage"]
    return f"""# Semiconductor Atlas — AI-Critical Manufacturing Baseline v1

This deterministic release contains {coverage['facility_count']} bounded facility or project-site
records across all seven required companies, {coverage['claim_count']} atomic claims,
{coverage['evidence_fragment_count']} evidence fragments, and
{coverage['source_document_count']} official source documents dated 2024 or later.

The release is not a global census and is not proof that an announced project is operating.
Yield, utilization, and qualification remain unknown for every row. Capacity remains unknown unless
an exact claim appears in `capacity.csv`; the five bases are never collapsed.

Files:

- `facilities.csv`: facility-centric consumer table with explicit unknown states;
- `claims.jsonl` and `evidence.jsonl`: atomic claims and verified evidence lineage;
- `capacity.csv`: supported capacity claims only;
- `atlas.geojson` and `atlas.html`: GeoJSON plus a standalone dependency-free interface;
- `source_inputs.json`: archived-byte, clock, rights, and attribution metadata;
- `source_ingestion_runs.json`: immutable capture-run and adapter lineage;
- `producing_run.json`: succeeded run and version/configuration provenance;
- `coverage.json`: achieved coverage and known gaps;
- `supply_intelligence.jsonl`: eligible quarter-total capacity rows (zero in v1);
- `supply_intelligence_contract.json`: eligibility and excluded-claim reasons;
- `supply_intelligence_facilities.jsonl`: separately versioned facility-scope rows;
- `METHODOLOGY.md` and `ATTRIBUTION.md`: interpretation and reuse boundary; and
- `manifest.json`: deterministic file sizes and SHA-256 hashes.

- Release ID: `{baseline.spec['release_id']}`
- World-state cutoff: `{baseline.spec['as_of']}`
- Knowledge cutoff: `{baseline.spec['recorded_at']}`
"""


def _release_files(
    baseline: ValidatedBaseline,
    materialized: Mapping[str, Any],
) -> dict[str, bytes]:
    source_inputs = [
        _source_record(baseline, source_id) for source_id in sorted(baseline.sources)
    ]
    ingestion_runs = list(baseline.ingestion_runs)
    run_descriptor = _producing_run_descriptor(baseline)
    producing_run = {
        "format": "semiconductor-atlas-ai-critical-producing-run-v1",
        "run_id": _producing_run_id(baseline),
        "started_at": max(
            str(run["finished_at"]) for run in baseline.ingestion_runs
        ),
        "finished_at": baseline.spec["recorded_at"],
        "outcome": "succeeded",
        "descriptor_sha256": hashlib.sha256(
            _canonical_bytes(run_descriptor)
        ).hexdigest(),
        **run_descriptor,
        "environment": "python>=3.11",
        "review_method": "official_primary_source_facility_scope_review_v1",
    }
    return {
        "ATTRIBUTION.md": _attribution_text(baseline).encode("utf-8"),
        "BUILD_ai_critical.py": baseline.code_bytes,
        "BUILD_build_ai_critical_release.py": baseline.builder_script_bytes,
        "BUILD_generate_atlas.py": baseline.atlas_generator_bytes,
        "METHODOLOGY.md": _methodology_text(baseline).encode("utf-8"),
        "README.md": _readme_text(baseline, materialized).encode("utf-8"),
        "atlas.geojson": _pretty_bytes(materialized["geojson"]),
        "atlas-template.html": baseline.atlas_template_bytes,
        "capacity.csv": _csv_bytes(_CAPACITY_FIELDS, materialized["capacity"]),
        "claims.jsonl": _jsonl_bytes(materialized["claims"]),
        "cohort.json": baseline.input_bytes,
        "coverage.json": _pretty_bytes(materialized["coverage"]),
        "evidence.jsonl": _jsonl_bytes(materialized["evidence"]),
        "facilities.csv": _csv_bytes(_FACILITY_FIELDS, materialized["facilities"]),
        "producing_run.json": _pretty_bytes(producing_run),
        "source_inputs.json": _pretty_bytes(source_inputs),
        "source_ingestion_runs.json": _pretty_bytes(ingestion_runs),
        "supply_intelligence.jsonl": _jsonl_bytes(materialized["supply_intelligence"]),
        "supply_intelligence_contract.json": _pretty_bytes(
            materialized["supply_intelligence_contract"]
        ),
        "supply_intelligence_facilities.jsonl": _jsonl_bytes(
            materialized["supply_intelligence_facilities"]
        ),
    }


def _file_metadata(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _manifest_payload(
    baseline: ValidatedBaseline,
    materialized: Mapping[str, Any],
    files: Mapping[str, bytes],
    *,
    atlas_html: bytes | None = None,
) -> dict[str, Any]:
    file_manifest = {
        name: _file_metadata(raw) for name, raw in sorted(files.items())
    }
    if atlas_html is not None:
        file_manifest["atlas.html"] = {
            **_file_metadata(atlas_html),
            "role": "standalone_atlas_html",
            "source": "atlas.geojson",
        }
        file_manifest = dict(sorted(file_manifest.items()))
    return {
        "format": RELEASE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "code_version": CODE_VERSION,
        "code_sha256": baseline.spec["code_sha256"],
        "builder_script_sha256": baseline.spec["builder_script_sha256"],
        "atlas_generator_sha256": baseline.spec["atlas_generator_sha256"],
        "model_version": MODEL_VERSION,
        "entity_resolution_version": ENTITY_RESOLUTION_VERSION,
        "atlas_template_sha256": baseline.spec["atlas_template_sha256"],
        "release_id": baseline.spec["release_id"],
        "as_of": baseline.spec["as_of"],
        "recorded_at": baseline.spec["recorded_at"],
        "selection_version": baseline.spec["selection_version"],
        "input_sha256": baseline.input_sha256,
        "companies": list(COMPANIES),
        "facility_count": len(materialized["facilities"]),
        "claim_count": len(materialized["claims"]),
        "evidence_fragment_count": len(materialized["evidence"]),
        "source_document_count": len(baseline.sources),
        "producing_run_id": _producing_run_id(baseline),
        "capacity_claims_by_basis": materialized["coverage"][
            "capacity_claims_by_basis"
        ],
        "stable_sort_rules": {
            "claims": "company_allowlist_order,subject_stable_key,predicate,claim_id",
            "evidence": "evidence_id",
            "facilities": "company_allowlist_order,facility_key",
            "files": "filename_codepoint_order",
        },
        "source_input_hashes": {
            source_id: source["content_sha256"]
            for source_id, source in sorted(baseline.sources.items())
        },
        "source_record_hashes": {
            source_id: _source_record_sha256(source)
            for source_id, source in sorted(baseline.sources.items())
        },
        "rights_decisions": {
            redistribution: sum(
                1
                for source in baseline.sources.values()
                if source["rights"]["redistribution"] == redistribution
            )
            for redistribution in sorted(REDISTRIBUTION_CLASSES)
        },
        "source_checkpoint": {
            "published_at_min": min(
                str(source["published_at"]) for source in baseline.sources.values()
            ),
            "published_at_max": max(
                str(source["published_at"]) for source in baseline.sources.values()
            ),
            "retrieved_at_min": min(
                str(source["retrieved_at"]) for source in baseline.sources.values()
            ),
            "retrieved_at_max": max(
                str(source["retrieved_at"]) for source in baseline.sources.values()
            ),
        },
        "known_limitations": materialized["coverage"]["known_limitations"],
        "files": file_manifest,
    }


def write_release(
    baseline: ValidatedBaseline,
    output_dir: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir)
    if output.is_symlink() or output.exists():
        raise ValueError("release output must not already exist")
    if not output.name or output.name in {".", ".."}:
        raise ValueError("release output name is unsafe")
    output = ensure_real_directory(
        output.parent, "release output parent"
    ) / output.name
    _verify_archived_sources(baseline)
    materialized = materialize_baseline(baseline)
    files = _release_files(baseline, materialized)
    manifest = _manifest_payload(baseline, materialized, files)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent)
    )
    try:
        for name, raw in sorted(files.items()):
            (stage / name).write_bytes(raw)
        (stage / "manifest.json").write_bytes(_pretty_bytes(manifest))
        validate_release(stage)
        _rename_exclusive(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest


def _jsonl_rows(path: Path, context: str) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), start=1):
        if not raw:
            raise ValueError(f"{context}:{line_number} must not be blank")
        rows.append(
            _require_object(
                _strict_json_bytes(raw, f"{context}:{line_number}"),
                f"{context}:{line_number}",
            )
        )
    return rows


def validate_release(
    output_dir: str | Path,
    *,
    require_html: bool = False,
) -> dict[str, Any]:
    output = Path(output_dir)
    if output.is_symlink() or not output.is_dir():
        raise ValueError("release output must be a real directory")
    manifest_path = output / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("release manifest is missing or unsafe")
    manifest = _require_object(
        _strict_json_bytes(manifest_path.read_bytes(), "release manifest"),
        "release manifest",
    )
    if manifest.get("format") != RELEASE_FORMAT:
        raise ValueError("release manifest format is invalid")
    if tuple(manifest.get("companies", ())) != COMPANIES:
        raise ValueError("release manifest company cohort is invalid")
    files = _require_object(manifest.get("files"), "release manifest files")
    required = {
        "ATTRIBUTION.md",
        "BUILD_ai_critical.py",
        "BUILD_build_ai_critical_release.py",
        "BUILD_generate_atlas.py",
        "METHODOLOGY.md",
        "README.md",
        "atlas.geojson",
        "atlas-template.html",
        "capacity.csv",
        "claims.jsonl",
        "cohort.json",
        "coverage.json",
        "evidence.jsonl",
        "facilities.csv",
        "producing_run.json",
        "source_inputs.json",
        "source_ingestion_runs.json",
        "supply_intelligence.jsonl",
        "supply_intelligence_contract.json",
        "supply_intelligence_facilities.jsonl",
    }
    allowed = required | {"atlas.html"}
    extra = sorted(set(files) - allowed)
    if extra:
        raise ValueError("release manifest has unmanaged file entries: " + ", ".join(extra))
    if require_html:
        required.add("atlas.html")
    missing = sorted(required - set(files))
    if missing:
        raise ValueError("release manifest is missing required files: " + ", ".join(missing))
    actual_names = {path.name for path in output.iterdir()}
    expected_names = set(files) | {"manifest.json"}
    if actual_names != expected_names:
        raise ValueError("release directory contains missing or unmanaged files")
    for name, metadata_value in files.items():
        if Path(name).name != name or name in {".", "..", "manifest.json"}:
            raise ValueError(f"unsafe managed release filename: {name}")
        metadata = _require_object(metadata_value, f"manifest files.{name}")
        if name == "atlas.html":
            _require_keys(
                metadata,
                required=("bytes", "sha256", "role", "source"),
                context=f"manifest files.{name}",
            )
        else:
            _require_keys(
                metadata,
                required=("bytes", "sha256"),
                context=f"manifest files.{name}",
            )
        _integer(metadata["bytes"], f"manifest files.{name}.bytes")
        _sha256(metadata["sha256"], f"manifest files.{name}.sha256")
        path = output / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"managed release file is missing or unsafe: {name}")
        raw = path.read_bytes()
        if metadata.get("bytes") != len(raw) or metadata.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"managed release file does not match manifest: {name}")

    cohort = load_baseline(
        output / "cohort.json",
        output,
        verify_source_bytes=False,
        atlas_template_path=output / "atlas-template.html",
        code_path=output / "BUILD_ai_critical.py",
        builder_script_path=output / "BUILD_build_ai_critical_release.py",
        atlas_generator_path=output / "BUILD_generate_atlas.py",
    )
    materialized = materialize_baseline(cohort)
    expected_files = _release_files(cohort, materialized)
    for name, expected in expected_files.items():
        if (output / name).read_bytes() != expected:
            raise ValueError(f"managed release content is inconsistent with cohort: {name}")
    expected_html = None
    if "atlas.html" in files:
        from web.generate_atlas import load_geojson, render

        expected_html = render(
            load_geojson(output / "atlas.geojson"),
            (output / "atlas-template.html").read_text(encoding="utf-8"),
        ).encode("utf-8")
        if (output / "atlas.html").read_bytes() != expected_html:
            raise ValueError("standalone atlas content is inconsistent with atlas.geojson")
    expected_manifest = _manifest_payload(
        cohort,
        materialized,
        expected_files,
        atlas_html=expected_html,
    )
    if manifest_path.read_bytes() != _pretty_bytes(expected_manifest):
        raise ValueError("release manifest is not the exact deterministic manifest")

    claims = _jsonl_rows(output / "claims.jsonl", "claims.jsonl")
    evidence = _jsonl_rows(output / "evidence.jsonl", "evidence.jsonl")
    producing_run = _require_object(
        _strict_json_bytes(
            (output / "producing_run.json").read_bytes(),
            "producing_run.json",
        ),
        "producing_run.json",
    )
    producing_run_id = _text(producing_run.get("run_id"), "producing run ID")
    if producing_run_id != manifest.get("producing_run_id"):
        raise ValueError("producing run does not resolve to the release manifest")
    producing_started = datetime.fromisoformat(
        _iso_timestamp(
            producing_run.get("started_at"), "producing run started_at"
        ).replace("Z", "+00:00")
    )
    producing_finished = datetime.fromisoformat(
        _iso_timestamp(
            producing_run.get("finished_at"), "producing run finished_at"
        ).replace("Z", "+00:00")
    )
    if producing_finished <= producing_started:
        raise ValueError("producing run must finish after it starts")
    if producing_run.get("outcome") != "succeeded":
        raise ValueError("producing run outcome must be succeeded")
    evidence_by_id = {str(row.get("evidence_id")): row for row in evidence}
    evidence_ids = set(evidence_by_id)
    if len(evidence_by_id) != len(evidence):
        raise ValueError("evidence IDs must be unique")
    for row in evidence:
        published_at = _iso_date(row.get("published_at"), "evidence published_at")
        if date.fromisoformat(published_at) < EVIDENCE_DATE_FLOOR:
            raise ValueError("release evidence predates 2024")
        role = _text(row.get("role"), "evidence role")
        if role not in EVIDENCE_ROLES:
            raise ValueError("release evidence has an invalid role")
        fragment_hash = _sha256(
            row.get("fragment_sha256"), "evidence fragment_sha256"
        )
        if row.get("evidence_sha256") != fragment_hash:
            raise ValueError("release evidence fragment identity is inconsistent")
        _sha256(row.get("source_record_sha256"), "evidence source_record_sha256")
        _identifier(row.get("ingestion_run_id"), "evidence ingestion_run_id")
    claim_ids: set[str] = set()
    for row in claims:
        claim_id = _text(row.get("claim_id"), "claim_id")
        if claim_id in claim_ids:
            raise ValueError("claim IDs must be unique")
        claim_ids.add(claim_id)
        if row.get("producing_run_id") != producing_run_id:
            raise ValueError(f"claim {claim_id} has an unresolved producing run")
        if row.get("claim_confidence") is not None:
            raise ValueError(f"claim {claim_id} must not publish uncalibrated confidence")
        if row.get("extraction_confidence") is not None:
            raise ValueError(
                f"claim {claim_id} must not publish uncalibrated extraction confidence"
            )
        linked = _string_list(row.get("evidence_ids"), f"claim {claim_id} evidence_ids", nonempty=True)
        if not set(linked) <= evidence_ids:
            raise ValueError(f"claim {claim_id} has unresolved evidence")
        link_values = _require_list(
            row.get("evidence_links"), f"claim {claim_id} evidence_links"
        )
        links: list[Mapping[str, Any]] = []
        for link_index, link_value in enumerate(link_values):
            link = _require_object(
                link_value, f"claim {claim_id} evidence_links[{link_index}]"
            )
            _require_keys(
                link,
                required=("evidence_id", "role", "fragment_sha256"),
                context=f"claim {claim_id} evidence_links[{link_index}]",
            )
            links.append(link)
        if [link.get("evidence_id") for link in links] != linked:
            raise ValueError(f"claim {claim_id} evidence links do not match evidence IDs")
        for link in links:
            evidence_row = evidence_by_id[str(link["evidence_id"])]
            if (
                link.get("role") != evidence_row.get("role")
                or link.get("fragment_sha256")
                != evidence_row.get("fragment_sha256")
            ):
                raise ValueError(f"claim {claim_id} has a stale evidence binding")
        if not any(link.get("role") == "support" for link in links):
            raise ValueError(f"claim {claim_id} has no supporting evidence link")
        if row.get("value_kind") == "capacity":
            value = _require_object(row.get("value"), f"claim {claim_id} value")
            if value.get("basis") not in CAPACITY_BASES:
                raise ValueError(f"claim {claim_id} has an invalid capacity basis")
    for row in claims:
        claim_id = str(row["claim_id"])
        dependencies = _string_list(
            row.get("dependencies"), f"claim {claim_id} dependencies"
        )
        if claim_id in dependencies:
            raise ValueError(f"claim {claim_id} cannot depend on itself")
        if not set(dependencies) <= claim_ids:
            raise ValueError(f"claim {claim_id} has unresolved dependencies")
    if manifest.get("claim_count") != len(claims):
        raise ValueError("release manifest claim count is inconsistent")
    if manifest.get("evidence_fragment_count") != len(evidence):
        raise ValueError("release manifest evidence count is inconsistent")
    coverage = _require_object(
        _strict_json_bytes((output / "coverage.json").read_bytes(), "coverage.json"),
        "coverage.json",
    )
    if tuple(coverage.get("companies", ())) != COMPANIES:
        raise ValueError("coverage company cohort is invalid")
    capacity_counts = coverage.get("capacity_claims_by_basis")
    if not isinstance(capacity_counts, dict) or tuple(sorted(capacity_counts)) != tuple(sorted(CAPACITY_BASES)):
        raise ValueError("coverage must preserve exactly the five capacity bases")
    geojson = _require_object(
        _strict_json_bytes((output / "atlas.geojson").read_bytes(), "atlas.geojson"),
        "atlas.geojson",
    )
    if geojson.get("type") != "FeatureCollection" or not isinstance(geojson.get("features"), list):
        raise ValueError("atlas.geojson must be a FeatureCollection")
    if manifest.get("facility_count") != len(geojson["features"]):
        raise ValueError("release manifest facility count is inconsistent")
    return dict(manifest)


def write_deterministic_archive(
    output_dir: str | Path,
    archive_path: str | Path,
) -> dict[str, Any]:
    output = Path(output_dir)
    manifest = validate_release(output, require_html=True)
    manifest_raw = _pretty_bytes(manifest)
    archive = Path(archive_path)
    try:
        archive.resolve(strict=False).relative_to(output.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ValueError("archive output must be outside the release directory")
    if archive.is_symlink() or archive.exists():
        raise ValueError("archive output must not already exist")
    if archive.suffixes[-2:] != [".tar", ".gz"]:
        raise ValueError("archive output must end in .tar.gz")
    archive = ensure_real_directory(
        archive.parent, "archive output parent"
    ) / archive.name
    archive_parent_status = archive.parent.lstat()
    archive_parent_identity = (
        archive_parent_status.st_dev,
        archive_parent_status.st_ino,
    )
    descriptor, stage_name = tempfile.mkstemp(
        prefix=f".{archive.name}.stage-",
        dir=archive.parent,
    )
    stage = Path(stage_name)
    stage_status = os.fstat(descriptor)
    stage_identity = (stage_status.st_dev, stage_status.st_ino)
    try:
        with os.fdopen(descriptor, "w+b") as raw_stream:
            with gzip.GzipFile(fileobj=raw_stream, mode="wb", filename="", mtime=0) as gzip_stream:
                with tarfile.open(fileobj=gzip_stream, mode="w") as tar:
                    archive_names = sorted({*manifest["files"], "manifest.json"})
                    for name in archive_names:
                        path = output / name
                        if path.is_symlink() or not path.is_file():
                            raise ValueError(
                                f"release archive input is missing or unsafe: {name}"
                            )
                        data = path.read_bytes()
                        if path.name == "manifest.json":
                            if data != manifest_raw:
                                raise ValueError("release manifest changed during archive creation")
                        else:
                            metadata = _require_object(
                                manifest["files"].get(path.name),
                                f"manifest files.{path.name}",
                            )
                            if (
                                metadata.get("bytes") != len(data)
                                or metadata.get("sha256")
                                != hashlib.sha256(data).hexdigest()
                            ):
                                raise ValueError(
                                    f"managed release file changed during archive creation: {path.name}"
                                )
                        info = tarfile.TarInfo(
                            f"{manifest['release_id']}/{path.name}"
                        )
                        info.size = len(data)
                        info.mtime = 0
                        info.mode = 0o644
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        tar.addfile(info, io.BytesIO(data))
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
            staged_size, staged_digest = _hash_open_stream(raw_stream)
            staged_status_after_hash = os.fstat(raw_stream.fileno())
            if (
                staged_status_after_hash.st_dev,
                staged_status_after_hash.st_ino,
            ) != stage_identity:
                raise OSError("archive stage identity changed while hashing")
        if validate_release(output, require_html=True) != manifest:
            raise ValueError("release changed during archive creation")
        _link_file_exclusive(
            stage,
            archive,
            expected_source_parent=archive_parent_identity,
            expected_destination_parent=archive_parent_identity,
        )
    finally:
        try:
            current_stage = stage.lstat()
        except FileNotFoundError:
            current_stage = None
        if current_stage is not None and (
            current_stage.st_dev,
            current_stage.st_ino,
        ) == stage_identity:
            try:
                stage.unlink()
            except OSError:
                pass
    _, archive_parent = _open_real_directory_fd(
        archive.parent, "archive output parent", create=False
    )
    try:
        _check_directory_identity(
            archive_parent, archive_parent_identity, "archive output parent"
        )
        descriptor = os.open(
            archive.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=archive_parent,
        )
        try:
            installed = os.fstat(descriptor)
            if (installed.st_dev, installed.st_ino) != stage_identity:
                raise OSError("archive output identity changed")
            with os.fdopen(descriptor, "rb", closefd=False) as archive_stream:
                installed_size, installed_digest = _hash_open_stream(archive_stream)
            installed_after_hash = os.fstat(descriptor)
            if (
                installed_after_hash.st_dev,
                installed_after_hash.st_ino,
            ) != stage_identity:
                raise OSError("archive output identity changed while hashing")
        finally:
            os.close(descriptor)
        installed_name = os.stat(
            archive.name,
            dir_fd=archive_parent,
            follow_symlinks=False,
        )
        if (installed_name.st_dev, installed_name.st_ino) != stage_identity:
            raise OSError("archive output pathname changed while hashing")
    finally:
        os.close(archive_parent)
    if (installed_size, installed_digest) != (staged_size, staged_digest):
        raise OSError("archive output bytes changed during publication")
    return {
        "path": str(archive),
        "bytes": staged_size,
        "sha256": staged_digest,
    }


__all__ = [
    "CAPACITY_BASES",
    "COMPANIES",
    "EVIDENCE_DATE_FLOOR",
    "INPUT_FORMAT",
    "RELEASE_FORMAT",
    "SCOPE_CATEGORIES",
    "SELECTION_VERSION",
    "SOURCE_ADAPTER_VERSION",
    "ValidatedBaseline",
    "load_baseline",
    "materialize_baseline",
    "ensure_real_directory",
    "install_directory_exclusive",
    "install_file_exclusive",
    "validate_release",
    "write_deterministic_archive",
    "write_release",
]
