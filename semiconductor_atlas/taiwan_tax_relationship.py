"""Reviewed facility-to-tax-unit references for Taiwan source records.

This module deliberately models a narrow registration reference.  An accepted
claim means only that, during the reviewed interval, a facility record and a
Taiwan MOF tax-unit record stated the same exact UBN.  It does not assert entity
identity, legal-person identity, ownership, parentage, operation, or operator
status.

Candidate generation is read-only.  Acceptance is atomic, evidence-bound, and
requires one explicit human decision for every candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .database import connect
from .models import (
    ClaimKind,
    ClaimSeries,
    ClaimVersion,
    DependencyKind,
    DependencyLink,
    EvidenceLink,
    EvidenceRole,
    IngestionRun,
    IngestionStatus,
    RelationshipValue,
    Source,
    SourceFamily,
    ValueKind,
)
from .moenv_snapshot import VerifiedMOENVSnapshot, verify_moenv_snapshot
from .repository import (
    add_claim_series,
    add_ingestion_run,
    add_source,
    add_source_family,
    insert_claim,
    stable_id,
    validate_database,
)
from .taiwan_factory_snapshot import (
    VerifiedTaiwanFactorySnapshot,
    verify_taiwan_factory_snapshot,
)
from .taiwan_facility_identity import (
    _begin_atomic,
    _canonical_date,
    _canonical_json_bytes,
    _canonical_timestamp,
    _clean_text,
    _commit_atomic,
    _expected_times,
    _json_object_from_database,
    _object,
    _query,
    _read_regular_file,
    _rollback_atomic,
    _schema_identity,
    _sha256,
    _strict_json,
    _timestamp,
    _write_canonical_artifact,
)
from .taiwan_mof_snapshot import (
    VerifiedTaiwanMOFSnapshot,
    verify_taiwan_mof_allowlist_sources,
    verify_taiwan_mof_snapshot,
)


TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT = (
    "semiconductor-atlas-taiwan-tax-relationship-candidates-v1"
)
TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT = (
    "semiconductor-atlas-taiwan-tax-relationship-review-v1"
)
TAIWAN_TAX_RELATIONSHIP_METHOD = (
    "taiwan_registered_tax_unit_reference_review_v1"
)
TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION = (
    "taiwan-facility-tax-unit-reviewed-v1"
)
TAIWAN_TAX_RELATIONSHIP_TYPE = "registered_tax_unit_reference"
TAIWAN_TAX_RELATIONSHIP_PREDICATE = "registered_tax_unit_reference"

MOENV_SOURCE_KEY = "taiwan-moenv-ems:ems_s_01"
FACTORY_SOURCE_KEY = "taiwan-ida-factory:registered-factories"
MOF_SOURCE_KEY = "taiwan-mof-tax:bgmopen1"
MOENV_UBN_PREDICATE = "moenv.uniformno"
FACTORY_UBN_PREDICATE = (
    "taiwan_factory_registry.unified_business_number"
)
MOF_UBN_PREDICATE = "organization.identifier.tw_ubn"

REVIEW_SOURCE_FAMILY_KEY = "semiconductor-atlas-reviewed-relationships"
REVIEW_SOURCE_KEY = (
    "semiconductor-atlas-reviewed-relationships:taiwan-tax-unit"
)
REVIEW_SOURCE_URL = "https://github.com/kiankyars/semiconductor-atlas"

TAIWAN_TAX_RELATIONSHIP_MIN_SCHEMA_VERSION = 4
TAIWAN_TAX_RELATIONSHIP_MAX_CANDIDATES = 20_000
_UBN_RE = re.compile(r"^[0-9]{8}$")
_OUTCOMES = frozenset({"match", "reject", "defer"})
_BINDING_FIELDS = {
    "source_key",
    "ingestion_run_id",
    "ingestion_run_sha256",
    "input_document_id",
    "snapshot_manifest_sha256",
    "snapshot_data_sha256",
    "snapshot_raw_sha256",
    "evidence_producer_ingestion_runs",
}
_RUN_IDENTITY_FIELDS = {"ingestion_run_id", "ingestion_run_sha256"}
_EVIDENCE_FIELDS = {
    "source_key",
    "entity_id",
    "entity_stable_key",
    "entity_kind",
    "predicate",
    "claim_version_id",
    "raw_value",
    "source_document_id",
    "source_record_id",
    "source_record_key",
    "claim_valid_from",
    "claim_valid_to",
    "producer_ingestion_run_id",
    "evidence_record_ingestion_run_id",
}
_ASSIGNMENT_FIELDS = {
    "assignment_id",
    "decision_id",
    "observed_entity_id",
    "canonical_entity_id",
    "source_record_id",
    "valid_from",
    "valid_to",
    "recorded_at",
}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "subject_entity_id",
    "subject_entity_stable_key",
    "subject_entity_kind",
    "tax_unit_entity_id",
    "tax_unit_entity_stable_key",
    "tax_unit_entity_kind",
    "unified_business_number",
    "relationship_type",
    "canonical_subject_rule",
    "canonical_assignments",
    "facility_evidence",
    "tax_unit_evidence",
    "evidence_valid_from",
    "evidence_valid_to",
    "score",
    "candidate_rank",
}
_CANDIDATE_TOP_LEVEL_FIELDS = {
    "format",
    "database_schema_version",
    "database_schema_sha256",
    "knowledge_cutoff_at",
    "moenv",
    "factory_registry",
    "mof_tax_registry",
    "candidates",
}
_REVIEW_TOP_LEVEL_FIELDS = {
    "format",
    "candidate_artifact_canonical_sha256",
    "reviewed_by",
    "reviewed_at",
    "decisions",
}
_REVIEW_DECISION_FIELDS = {
    "candidate_id",
    "outcome",
    "reason",
    "relationship_valid_from",
    "relationship_valid_to",
}


@dataclass(frozen=True, slots=True, order=True)
class TaiwanTaxIngestionIdentity:
    """Immutable identity of one succeeded evidence-producing run."""

    ingestion_run_id: str
    ingestion_run_sha256: str


@dataclass(frozen=True, slots=True)
class TaiwanTaxSourceBinding:
    """Selected snapshot run and all immutable evidence-producer identities."""

    source_key: str
    ingestion_run_id: str
    ingestion_run_sha256: str
    input_document_id: str
    snapshot_manifest_sha256: str
    snapshot_data_sha256: str
    snapshot_raw_sha256: str
    evidence_producer_ingestion_runs: tuple[TaiwanTaxIngestionIdentity, ...]


@dataclass(frozen=True, slots=True, order=True)
class TaiwanTaxEvidence:
    """One exact scalar claim and one exact source-record evidence link."""

    source_key: str
    entity_id: str
    entity_stable_key: str
    entity_kind: str
    predicate: str
    claim_version_id: str
    raw_value: str
    source_document_id: str
    source_record_id: str
    source_record_key: str
    claim_valid_from: str
    claim_valid_to: str | None
    producer_ingestion_run_id: str
    evidence_record_ingestion_run_id: str


@dataclass(frozen=True, slots=True, order=True)
class TaiwanTaxCanonicalAssignment:
    """Exact accepted facility assignment used to choose a canonical subject."""

    assignment_id: str
    decision_id: str
    observed_entity_id: str
    canonical_entity_id: str
    source_record_id: str
    valid_from: str
    valid_to: str | None
    recorded_at: str


@dataclass(frozen=True, slots=True)
class TaiwanTaxRelationshipCandidate:
    """One explicit facility-to-tax-unit registration-reference proposal."""

    candidate_id: str
    subject_entity_id: str
    subject_entity_stable_key: str
    tax_unit_entity_id: str
    tax_unit_entity_stable_key: str
    unified_business_number: str
    canonical_subject_rule: str
    canonical_assignments: tuple[TaiwanTaxCanonicalAssignment, ...]
    facility_evidence: tuple[TaiwanTaxEvidence, ...]
    tax_unit_evidence: tuple[TaiwanTaxEvidence, ...]
    evidence_valid_from: str
    evidence_valid_to: str | None
    score: float = 1.0
    candidate_rank: int = 1
    subject_entity_kind: str = "facility"
    tax_unit_entity_kind: str = "organization"
    relationship_type: str = TAIWAN_TAX_RELATIONSHIP_TYPE


@dataclass(frozen=True, slots=True)
class TaiwanTaxRelationshipCandidateArtifact:
    format: str
    database_schema_version: int
    database_schema_sha256: str
    knowledge_cutoff_at: str
    moenv: TaiwanTaxSourceBinding
    factory_registry: TaiwanTaxSourceBinding
    mof_tax_registry: TaiwanTaxSourceBinding
    candidates: tuple[TaiwanTaxRelationshipCandidate, ...]
    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TaiwanTaxRelationshipReviewDecision:
    candidate_id: str
    outcome: str
    reason: str
    relationship_valid_from: str | None
    relationship_valid_to: str | None = None


@dataclass(frozen=True, slots=True)
class TaiwanTaxRelationshipReviewArtifact:
    format: str
    candidate_artifact_canonical_sha256: str
    reviewed_by: str
    reviewed_at: str
    decisions: tuple[TaiwanTaxRelationshipReviewDecision, ...]
    raw_bytes: bytes
    raw_sha256: str
    canonical_bytes: bytes
    canonical_sha256: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class TaiwanTaxRelationshipAcceptanceResult:
    review_ingestion_run_id: str
    relationship_claim_ids: tuple[str, ...]
    superseded_claim_ids: tuple[str, ...]
    reaffirmed_claim_ids: tuple[str, ...]
    replayed: bool
    rows_written: int


def _ids_array(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    values = tuple(_clean_text(item, f"{context}[]") for item in value)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _optional_date(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _canonical_date(value, context)


def _raw_text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be text")
    return value


def _run_identity_payload(value: TaiwanTaxIngestionIdentity) -> dict[str, str]:
    return {
        "ingestion_run_id": value.ingestion_run_id,
        "ingestion_run_sha256": value.ingestion_run_sha256,
    }


def _parse_run_identity(value: object, context: str) -> TaiwanTaxIngestionIdentity:
    payload = _object(value, context)
    if set(payload) != _RUN_IDENTITY_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    return TaiwanTaxIngestionIdentity(
        _clean_text(payload["ingestion_run_id"], f"{context}.ingestion_run_id"),
        _sha256(
            payload["ingestion_run_sha256"],
            f"{context}.ingestion_run_sha256",
        ),
    )


def _binding_payload(value: TaiwanTaxSourceBinding) -> dict[str, object]:
    return {
        "source_key": value.source_key,
        "ingestion_run_id": value.ingestion_run_id,
        "ingestion_run_sha256": value.ingestion_run_sha256,
        "input_document_id": value.input_document_id,
        "snapshot_manifest_sha256": value.snapshot_manifest_sha256,
        "snapshot_data_sha256": value.snapshot_data_sha256,
        "snapshot_raw_sha256": value.snapshot_raw_sha256,
        "evidence_producer_ingestion_runs": [
            _run_identity_payload(item)
            for item in value.evidence_producer_ingestion_runs
        ],
    }


def _parse_binding(value: object, context: str) -> TaiwanTaxSourceBinding:
    payload = _object(value, context)
    if set(payload) != _BINDING_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    raw_producers = payload["evidence_producer_ingestion_runs"]
    if not isinstance(raw_producers, list) or not raw_producers:
        raise ValueError(f"{context}.evidence_producer_ingestion_runs must be nonempty")
    producers = tuple(
        _parse_run_identity(item, f"{context}.evidence_producer_ingestion_runs[{index}]")
        for index, item in enumerate(raw_producers)
    )
    producer_ids = tuple(item.ingestion_run_id for item in producers)
    if producer_ids != tuple(sorted(producer_ids)) or len(producer_ids) != len(
        set(producer_ids)
    ):
        raise ValueError(f"{context} producer identities must be sorted and unique")
    selected_run_id = _clean_text(
        payload["ingestion_run_id"], f"{context}.ingestion_run_id"
    )
    selected_digest = _sha256(
        payload["ingestion_run_sha256"], f"{context}.ingestion_run_sha256"
    )
    if TaiwanTaxIngestionIdentity(selected_run_id, selected_digest) not in producers:
        raise ValueError(f"{context} must include the selected run as a producer")
    return TaiwanTaxSourceBinding(
        source_key=_clean_text(payload["source_key"], f"{context}.source_key"),
        ingestion_run_id=selected_run_id,
        ingestion_run_sha256=selected_digest,
        input_document_id=_clean_text(
            payload["input_document_id"], f"{context}.input_document_id"
        ),
        snapshot_manifest_sha256=_sha256(
            payload["snapshot_manifest_sha256"],
            f"{context}.snapshot_manifest_sha256",
        ),
        snapshot_data_sha256=_sha256(
            payload["snapshot_data_sha256"], f"{context}.snapshot_data_sha256"
        ),
        snapshot_raw_sha256=_sha256(
            payload["snapshot_raw_sha256"], f"{context}.snapshot_raw_sha256"
        ),
        evidence_producer_ingestion_runs=producers,
    )


def _evidence_payload(value: TaiwanTaxEvidence) -> dict[str, object]:
    return {
        "source_key": value.source_key,
        "entity_id": value.entity_id,
        "entity_stable_key": value.entity_stable_key,
        "entity_kind": value.entity_kind,
        "predicate": value.predicate,
        "claim_version_id": value.claim_version_id,
        "raw_value": value.raw_value,
        "source_document_id": value.source_document_id,
        "source_record_id": value.source_record_id,
        "source_record_key": value.source_record_key,
        "claim_valid_from": value.claim_valid_from,
        "claim_valid_to": value.claim_valid_to,
        "producer_ingestion_run_id": value.producer_ingestion_run_id,
        "evidence_record_ingestion_run_id": (
            value.evidence_record_ingestion_run_id
        ),
    }


def _parse_evidence(value: object, context: str) -> TaiwanTaxEvidence:
    payload = _object(value, context)
    if set(payload) != _EVIDENCE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    kind = _clean_text(payload["entity_kind"], f"{context}.entity_kind")
    if kind not in {"facility", "organization"}:
        raise ValueError(f"{context}.entity_kind is invalid")
    return TaiwanTaxEvidence(
        source_key=_clean_text(payload["source_key"], f"{context}.source_key"),
        entity_id=_clean_text(payload["entity_id"], f"{context}.entity_id"),
        entity_stable_key=_clean_text(
            payload["entity_stable_key"], f"{context}.entity_stable_key"
        ),
        entity_kind=kind,
        predicate=_clean_text(payload["predicate"], f"{context}.predicate"),
        claim_version_id=_clean_text(
            payload["claim_version_id"], f"{context}.claim_version_id"
        ),
        raw_value=_raw_text(payload["raw_value"], f"{context}.raw_value"),
        source_document_id=_clean_text(
            payload["source_document_id"], f"{context}.source_document_id"
        ),
        source_record_id=_clean_text(
            payload["source_record_id"], f"{context}.source_record_id"
        ),
        source_record_key=_clean_text(
            payload["source_record_key"], f"{context}.source_record_key"
        ),
        claim_valid_from=_canonical_date(
            payload["claim_valid_from"], f"{context}.claim_valid_from"
        ),
        claim_valid_to=_optional_date(
            payload["claim_valid_to"], f"{context}.claim_valid_to"
        ),
        producer_ingestion_run_id=_clean_text(
            payload["producer_ingestion_run_id"],
            f"{context}.producer_ingestion_run_id",
        ),
        evidence_record_ingestion_run_id=_clean_text(
            payload["evidence_record_ingestion_run_id"],
            f"{context}.evidence_record_ingestion_run_id",
        ),
    )


def _assignment_payload(value: TaiwanTaxCanonicalAssignment) -> dict[str, object]:
    return {
        "assignment_id": value.assignment_id,
        "decision_id": value.decision_id,
        "observed_entity_id": value.observed_entity_id,
        "canonical_entity_id": value.canonical_entity_id,
        "source_record_id": value.source_record_id,
        "valid_from": value.valid_from,
        "valid_to": value.valid_to,
        "recorded_at": value.recorded_at,
    }


def _parse_assignment(value: object, context: str) -> TaiwanTaxCanonicalAssignment:
    payload = _object(value, context)
    if set(payload) != _ASSIGNMENT_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    assignment = TaiwanTaxCanonicalAssignment(
        assignment_id=_clean_text(
            payload["assignment_id"], f"{context}.assignment_id"
        ),
        decision_id=_clean_text(payload["decision_id"], f"{context}.decision_id"),
        observed_entity_id=_clean_text(
            payload["observed_entity_id"], f"{context}.observed_entity_id"
        ),
        canonical_entity_id=_clean_text(
            payload["canonical_entity_id"], f"{context}.canonical_entity_id"
        ),
        source_record_id=_clean_text(
            payload["source_record_id"], f"{context}.source_record_id"
        ),
        valid_from=_canonical_date(payload["valid_from"], f"{context}.valid_from"),
        valid_to=_optional_date(payload["valid_to"], f"{context}.valid_to"),
        recorded_at=_canonical_timestamp(
            payload["recorded_at"], f"{context}.recorded_at"
        ),
    )
    if assignment.valid_to is not None and assignment.valid_to <= assignment.valid_from:
        raise ValueError(f"{context}.valid_to must be later than valid_from")
    return assignment


def _evidence_array_payload(
    values: Sequence[TaiwanTaxEvidence],
) -> list[dict[str, object]]:
    return [_evidence_payload(item) for item in values]


def _parse_evidence_array(
    value: object,
    context: str,
    *,
    expected_kind: str,
    expected_predicates: frozenset[str],
) -> tuple[TaiwanTaxEvidence, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} must be a nonempty JSON array")
    items = tuple(
        _parse_evidence(item, f"{context}[{index}]")
        for index, item in enumerate(value)
    )
    if items != tuple(sorted(items)) or len(items) != len(set(items)):
        raise ValueError(f"{context} must be sorted and unique")
    if any(
        item.entity_kind != expected_kind
        or item.predicate not in expected_predicates
        for item in items
    ):
        raise ValueError(f"{context} has an invalid entity kind or predicate")
    return items


def _candidate_semantic_payload(
    *,
    subject_entity_id: str,
    subject_entity_stable_key: str,
    tax_unit_entity_id: str,
    tax_unit_entity_stable_key: str,
    unified_business_number: str,
    canonical_subject_rule: str,
    canonical_assignments: Sequence[TaiwanTaxCanonicalAssignment],
    facility_evidence: Sequence[TaiwanTaxEvidence],
    tax_unit_evidence: Sequence[TaiwanTaxEvidence],
    evidence_valid_from: str,
    evidence_valid_to: str | None,
) -> dict[str, object]:
    return {
        "subject_entity_id": subject_entity_id,
        "subject_entity_stable_key": subject_entity_stable_key,
        "subject_entity_kind": "facility",
        "tax_unit_entity_id": tax_unit_entity_id,
        "tax_unit_entity_stable_key": tax_unit_entity_stable_key,
        "tax_unit_entity_kind": "organization",
        "unified_business_number": unified_business_number,
        "relationship_type": TAIWAN_TAX_RELATIONSHIP_TYPE,
        "canonical_subject_rule": canonical_subject_rule,
        "canonical_assignments": [
            _assignment_payload(item) for item in canonical_assignments
        ],
        "facility_evidence": _evidence_array_payload(facility_evidence),
        "tax_unit_evidence": _evidence_array_payload(tax_unit_evidence),
        "evidence_valid_from": evidence_valid_from,
        "evidence_valid_to": evidence_valid_to,
    }


def _candidate_id(semantic_payload: Mapping[str, object]) -> str:
    return stable_id(
        "taiwan-tax-relationship-candidate",
        TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
        hashlib.sha256(_canonical_json_bytes(semantic_payload)).hexdigest(),
    )


def _candidate_payload(
    candidate: TaiwanTaxRelationshipCandidate,
) -> dict[str, object]:
    payload = _candidate_semantic_payload(
        subject_entity_id=candidate.subject_entity_id,
        subject_entity_stable_key=candidate.subject_entity_stable_key,
        tax_unit_entity_id=candidate.tax_unit_entity_id,
        tax_unit_entity_stable_key=candidate.tax_unit_entity_stable_key,
        unified_business_number=candidate.unified_business_number,
        canonical_subject_rule=candidate.canonical_subject_rule,
        canonical_assignments=candidate.canonical_assignments,
        facility_evidence=candidate.facility_evidence,
        tax_unit_evidence=candidate.tax_unit_evidence,
        evidence_valid_from=candidate.evidence_valid_from,
        evidence_valid_to=candidate.evidence_valid_to,
    )
    return {
        "candidate_id": candidate.candidate_id,
        **payload,
        "score": candidate.score,
        "candidate_rank": candidate.candidate_rank,
    }


def _parse_candidate(
    value: object, index: int
) -> TaiwanTaxRelationshipCandidate:
    context = f"candidates[{index}]"
    payload = _object(value, context)
    if set(payload) != _CANDIDATE_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    if payload["relationship_type"] != TAIWAN_TAX_RELATIONSHIP_TYPE:
        raise ValueError(f"{context}.relationship_type is invalid")
    if payload["subject_entity_kind"] != "facility":
        raise ValueError(f"{context}.subject_entity_kind must be facility")
    if payload["tax_unit_entity_kind"] != "organization":
        raise ValueError(f"{context}.tax_unit_entity_kind must be organization")
    ubn = _clean_text(
        payload["unified_business_number"],
        f"{context}.unified_business_number",
    )
    if not _UBN_RE.fullmatch(ubn):
        raise ValueError(f"{context}.unified_business_number must be eight digits")
    rule = _clean_text(
        payload["canonical_subject_rule"],
        f"{context}.canonical_subject_rule",
    )
    if rule not in {
        "source_native_facility",
        "accepted_factory_native_assignment",
        "factory_native_with_assigned_moenv_provenance",
    }:
        raise ValueError(f"{context}.canonical_subject_rule is invalid")
    raw_assignments = payload["canonical_assignments"]
    if not isinstance(raw_assignments, list):
        raise ValueError(f"{context}.canonical_assignments must be a JSON array")
    assignments = tuple(
        _parse_assignment(item, f"{context}.canonical_assignments[{item_index}]")
        for item_index, item in enumerate(raw_assignments)
    )
    if assignments != tuple(sorted(assignments)) or len(assignments) != len(
        set(assignments)
    ):
        raise ValueError(f"{context}.canonical_assignments must be sorted and unique")
    if rule == "source_native_facility" and assignments:
        raise ValueError(f"{context} source-native candidates cannot have assignments")
    if rule != "source_native_facility" and not assignments:
        raise ValueError(f"{context} assigned candidates require assignment lineage")

    facility_evidence = _parse_evidence_array(
        payload["facility_evidence"],
        f"{context}.facility_evidence",
        expected_kind="facility",
        expected_predicates=frozenset(
            {MOENV_UBN_PREDICATE, FACTORY_UBN_PREDICATE}
        ),
    )
    tax_evidence = _parse_evidence_array(
        payload["tax_unit_evidence"],
        f"{context}.tax_unit_evidence",
        expected_kind="organization",
        expected_predicates=frozenset({MOF_UBN_PREDICATE}),
    )
    if any(item.raw_value != ubn for item in (*facility_evidence, *tax_evidence)):
        raise ValueError(f"{context} evidence must state the exact candidate UBN")
    subject_id = _clean_text(
        payload["subject_entity_id"], f"{context}.subject_entity_id"
    )
    tax_id = _clean_text(
        payload["tax_unit_entity_id"], f"{context}.tax_unit_entity_id"
    )
    if subject_id == tax_id:
        raise ValueError(f"{context} subject and tax unit must be distinct")
    if any(item.entity_id != tax_id for item in tax_evidence):
        raise ValueError(f"{context} tax evidence belongs to another entity")
    if any(item.canonical_entity_id != subject_id for item in assignments):
        raise ValueError(f"{context} assignment target conflicts with subject")
    facility_entity_ids = {item.entity_id for item in facility_evidence}
    allowed_facility_ids = {subject_id} | {
        item.observed_entity_id for item in assignments
    }
    if not facility_entity_ids <= allowed_facility_ids:
        raise ValueError(f"{context} facility evidence is unrelated to the subject")
    if rule == "source_native_facility" and facility_entity_ids != {subject_id}:
        raise ValueError(f"{context} source-native evidence must belong to subject")
    valid_from = _canonical_date(
        payload["evidence_valid_from"], f"{context}.evidence_valid_from"
    )
    valid_to = _optional_date(
        payload["evidence_valid_to"], f"{context}.evidence_valid_to"
    )
    if valid_to is not None and valid_to <= valid_from:
        raise ValueError(f"{context}.evidence_valid_to must be later than valid_from")
    starts = [item.claim_valid_from for item in (*facility_evidence, *tax_evidence)]
    ends = [
        item.claim_valid_to
        for item in (*facility_evidence, *tax_evidence)
        if item.claim_valid_to is not None
    ]
    starts.extend(item.valid_from for item in assignments)
    ends.extend(item.valid_to for item in assignments if item.valid_to is not None)
    expected_from = max(starts)
    expected_to = min(ends) if ends else None
    if expected_to is not None and expected_to <= expected_from:
        raise ValueError(f"{context} evidence has no same-time overlap")
    if (valid_from, valid_to) != (expected_from, expected_to):
        raise ValueError(f"{context} evidence interval is not the exact intersection")
    score = payload["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or float(score) != 1.0:
        raise ValueError(f"{context}.score must be 1.0")
    rank = payload["candidate_rank"]
    if isinstance(rank, bool) or rank != 1:
        raise ValueError(f"{context}.candidate_rank must be 1")
    semantic = _candidate_semantic_payload(
        subject_entity_id=subject_id,
        subject_entity_stable_key=_clean_text(
            payload["subject_entity_stable_key"],
            f"{context}.subject_entity_stable_key",
        ),
        tax_unit_entity_id=tax_id,
        tax_unit_entity_stable_key=_clean_text(
            payload["tax_unit_entity_stable_key"],
            f"{context}.tax_unit_entity_stable_key",
        ),
        unified_business_number=ubn,
        canonical_subject_rule=rule,
        canonical_assignments=assignments,
        facility_evidence=facility_evidence,
        tax_unit_evidence=tax_evidence,
        evidence_valid_from=valid_from,
        evidence_valid_to=valid_to,
    )
    expected_id = _candidate_id(semantic)
    candidate_id = _clean_text(
        payload["candidate_id"], f"{context}.candidate_id"
    )
    if candidate_id != expected_id:
        raise ValueError(f"{context}.candidate_id is not content-derived")
    return TaiwanTaxRelationshipCandidate(
        candidate_id=candidate_id,
        subject_entity_id=subject_id,
        subject_entity_stable_key=str(semantic["subject_entity_stable_key"]),
        tax_unit_entity_id=tax_id,
        tax_unit_entity_stable_key=str(semantic["tax_unit_entity_stable_key"]),
        unified_business_number=ubn,
        canonical_subject_rule=rule,
        canonical_assignments=assignments,
        facility_evidence=facility_evidence,
        tax_unit_evidence=tax_evidence,
        evidence_valid_from=valid_from,
        evidence_valid_to=valid_to,
    )


def _candidate_artifact_payload(
    *,
    database_schema_version: int,
    database_schema_sha256: str,
    knowledge_cutoff_at: str,
    moenv: TaiwanTaxSourceBinding,
    factory_registry: TaiwanTaxSourceBinding,
    mof_tax_registry: TaiwanTaxSourceBinding,
    candidates: Sequence[TaiwanTaxRelationshipCandidate],
) -> dict[str, object]:
    return {
        "format": TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT,
        "database_schema_version": database_schema_version,
        "database_schema_sha256": database_schema_sha256,
        "knowledge_cutoff_at": knowledge_cutoff_at,
        "moenv": _binding_payload(moenv),
        "factory_registry": _binding_payload(factory_registry),
        "mof_tax_registry": _binding_payload(mof_tax_registry),
        "candidates": [_candidate_payload(item) for item in candidates],
    }


def parse_taiwan_tax_relationship_candidate_bytes(
    raw: bytes,
) -> TaiwanTaxRelationshipCandidateArtifact:
    """Parse a bounded strict candidate artifact and preserve raw identity."""

    value = _strict_json(raw, "Taiwan tax-relationship candidate artifact")
    payload = _object(value, "Taiwan tax-relationship candidate artifact")
    if set(payload) != _CANDIDATE_TOP_LEVEL_FIELDS:
        raise ValueError("candidate artifact must contain exactly the required fields")
    if payload["format"] != TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT:
        raise ValueError(
            f"format must be {TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT!r}"
        )
    version = payload["database_schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < TAIWAN_TAX_RELATIONSHIP_MIN_SCHEMA_VERSION
    ):
        raise ValueError("database_schema_version must be an integer at least 4")
    schema_sha = _sha256(
        payload["database_schema_sha256"], "database_schema_sha256"
    )
    cutoff = _canonical_timestamp(payload["knowledge_cutoff_at"], "knowledge_cutoff_at")
    moenv = _parse_binding(payload["moenv"], "moenv")
    factory = _parse_binding(payload["factory_registry"], "factory_registry")
    mof = _parse_binding(payload["mof_tax_registry"], "mof_tax_registry")
    if (
        moenv.source_key != MOENV_SOURCE_KEY
        or factory.source_key != FACTORY_SOURCE_KEY
        or mof.source_key != MOF_SOURCE_KEY
    ):
        raise ValueError("candidate artifact has an incorrect source binding")
    selected_run_ids = {
        moenv.ingestion_run_id,
        factory.ingestion_run_id,
        mof.ingestion_run_id,
    }
    if len(selected_run_ids) != 3:
        raise ValueError("candidate sources must use distinct ingestion runs")
    raw_candidates = payload["candidates"]
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a JSON array")
    if len(raw_candidates) > TAIWAN_TAX_RELATIONSHIP_MAX_CANDIDATES:
        raise ValueError("candidate artifact exceeds the candidate-count limit")
    candidates = tuple(
        _parse_candidate(item, index) for index, item in enumerate(raw_candidates)
    )
    candidate_ids = tuple(item.candidate_id for item in candidates)
    if candidate_ids != tuple(sorted(candidate_ids)) or len(candidate_ids) != len(
        set(candidate_ids)
    ):
        raise ValueError("candidates must be sorted by unique candidate_id")
    keys = tuple(
        (item.subject_entity_id, item.tax_unit_entity_id, item.evidence_valid_from)
        for item in candidates
    )
    if len(keys) != len(set(keys)):
        raise ValueError("candidate artifact contains a duplicate relationship interval")
    canonical = _canonical_json_bytes(
        _candidate_artifact_payload(
            database_schema_version=version,
            database_schema_sha256=schema_sha,
            knowledge_cutoff_at=cutoff,
            moenv=moenv,
            factory_registry=factory,
            mof_tax_registry=mof,
            candidates=candidates,
        )
    )
    return TaiwanTaxRelationshipCandidateArtifact(
        format=TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT,
        database_schema_version=version,
        database_schema_sha256=schema_sha,
        knowledge_cutoff_at=cutoff,
        moenv=moenv,
        factory_registry=factory,
        mof_tax_registry=mof,
        candidates=candidates,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def read_taiwan_tax_relationship_candidate_file(
    path: str | Path,
) -> TaiwanTaxRelationshipCandidateArtifact:
    candidate_path, raw = _read_regular_file(
        path, "Taiwan tax-relationship candidate artifact"
    )
    return replace(
        parse_taiwan_tax_relationship_candidate_bytes(raw), path=candidate_path
    )


def _decision_payload(
    decision: TaiwanTaxRelationshipReviewDecision,
) -> dict[str, object]:
    return {
        "candidate_id": decision.candidate_id,
        "outcome": decision.outcome,
        "reason": decision.reason,
        "relationship_valid_from": decision.relationship_valid_from,
        "relationship_valid_to": decision.relationship_valid_to,
    }


def _parse_decision(
    value: object,
    index: int,
    candidate: TaiwanTaxRelationshipCandidate,
) -> TaiwanTaxRelationshipReviewDecision:
    context = f"decisions[{index}]"
    payload = _object(value, context)
    if set(payload) != _REVIEW_DECISION_FIELDS:
        raise ValueError(f"{context} must contain exactly the required fields")
    candidate_id = _clean_text(payload["candidate_id"], f"{context}.candidate_id")
    if candidate_id != candidate.candidate_id:
        raise ValueError(f"{context} is not aligned with the candidate artifact")
    outcome = _clean_text(payload["outcome"], f"{context}.outcome")
    if outcome not in _OUTCOMES:
        raise ValueError(f"{context}.outcome must be match, reject, or defer")
    valid_from = _optional_date(
        payload["relationship_valid_from"], f"{context}.relationship_valid_from"
    )
    valid_to = _optional_date(
        payload["relationship_valid_to"], f"{context}.relationship_valid_to"
    )
    if outcome != "match":
        if valid_from is not None or valid_to is not None:
            raise ValueError(f"{context} dates must be null unless outcome is match")
    else:
        if valid_from is None:
            raise ValueError(f"{context}.relationship_valid_from is required for match")
        if valid_from < candidate.evidence_valid_from:
            raise ValueError(f"{context} predates the common evidence interval")
        if candidate.evidence_valid_to is not None and (
            valid_from >= candidate.evidence_valid_to
            or valid_to is None
            or valid_to > candidate.evidence_valid_to
        ):
            raise ValueError(f"{context} exceeds the common evidence interval")
        if valid_to is not None and valid_to <= valid_from:
            raise ValueError(f"{context}.relationship_valid_to must be later")
    return TaiwanTaxRelationshipReviewDecision(
        candidate_id=candidate_id,
        outcome=outcome,
        reason=_clean_text(payload["reason"], f"{context}.reason"),
        relationship_valid_from=valid_from,
        relationship_valid_to=valid_to,
    )


def _review_payload(
    *,
    candidate_artifact_canonical_sha256: str,
    reviewed_by: str,
    reviewed_at: str,
    decisions: Sequence[TaiwanTaxRelationshipReviewDecision],
) -> dict[str, object]:
    return {
        "format": TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT,
        "candidate_artifact_canonical_sha256": (
            candidate_artifact_canonical_sha256
        ),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "decisions": [_decision_payload(item) for item in decisions],
    }


def _reparse_candidate(
    artifact: TaiwanTaxRelationshipCandidateArtifact,
) -> TaiwanTaxRelationshipCandidateArtifact:
    parsed = parse_taiwan_tax_relationship_candidate_bytes(artifact.raw_bytes)
    if (
        parsed.raw_sha256 != artifact.raw_sha256
        or parsed.canonical_sha256 != artifact.canonical_sha256
        or parsed.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("candidate artifact object conflicts with its raw bytes")
    return replace(parsed, path=artifact.path)


def parse_taiwan_tax_relationship_review_bytes(
    raw: bytes,
    *,
    candidate_artifact: TaiwanTaxRelationshipCandidateArtifact,
) -> TaiwanTaxRelationshipReviewArtifact:
    candidate_artifact = _reparse_candidate(candidate_artifact)
    value = _strict_json(raw, "Taiwan tax-relationship review artifact")
    payload = _object(value, "Taiwan tax-relationship review artifact")
    if set(payload) != _REVIEW_TOP_LEVEL_FIELDS:
        raise ValueError("review artifact must contain exactly the required fields")
    if payload["format"] != TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT:
        raise ValueError(
            f"format must be {TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT!r}"
        )
    candidate_sha = _sha256(
        payload["candidate_artifact_canonical_sha256"],
        "candidate_artifact_canonical_sha256",
    )
    if candidate_sha != candidate_artifact.canonical_sha256:
        raise ValueError("review is bound to a different candidate artifact")
    reviewed_by = _clean_text(payload["reviewed_by"], "reviewed_by")
    reviewed_at = _canonical_timestamp(payload["reviewed_at"], "reviewed_at")
    if _timestamp(reviewed_at) < _timestamp(candidate_artifact.knowledge_cutoff_at):
        raise ValueError("reviewed_at must not predate the candidate cutoff")
    raw_decisions = payload["decisions"]
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions must be a JSON array")
    if len(raw_decisions) != len(candidate_artifact.candidates):
        raise ValueError("review must decide every candidate exactly once")
    decisions = tuple(
        _parse_decision(item, index, candidate)
        for index, (item, candidate) in enumerate(
            zip(raw_decisions, candidate_artifact.candidates, strict=True)
        )
    )
    ids = tuple(item.candidate_id for item in decisions)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ValueError("review decisions must be sorted and unique")
    canonical = _canonical_json_bytes(
        _review_payload(
            candidate_artifact_canonical_sha256=candidate_sha,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            decisions=decisions,
        )
    )
    return TaiwanTaxRelationshipReviewArtifact(
        format=TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT,
        candidate_artifact_canonical_sha256=candidate_sha,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        decisions=decisions,
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def build_taiwan_tax_relationship_review(
    candidate_artifact: TaiwanTaxRelationshipCandidateArtifact,
    *,
    reviewed_by: str,
    reviewed_at: str,
    decisions: Sequence[TaiwanTaxRelationshipReviewDecision],
) -> TaiwanTaxRelationshipReviewArtifact:
    candidate_artifact = _reparse_candidate(candidate_artifact)
    raw = _canonical_json_bytes(
        _review_payload(
            candidate_artifact_canonical_sha256=candidate_artifact.canonical_sha256,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            decisions=tuple(decisions),
        )
    )
    return parse_taiwan_tax_relationship_review_bytes(
        raw, candidate_artifact=candidate_artifact
    )


def read_taiwan_tax_relationship_review_file(
    path: str | Path,
    *,
    candidate_artifact: TaiwanTaxRelationshipCandidateArtifact,
) -> TaiwanTaxRelationshipReviewArtifact:
    review_path, raw = _read_regular_file(
        path, "Taiwan tax-relationship review artifact"
    )
    return replace(
        parse_taiwan_tax_relationship_review_bytes(
            raw, candidate_artifact=candidate_artifact
        ),
        path=review_path,
    )


def _verified_moenv_snapshot(
    value: str | Path | VerifiedMOENVSnapshot,
) -> VerifiedMOENVSnapshot:
    root = getattr(value, "root", value)
    return verify_moenv_snapshot(root)


def _verified_factory_snapshot(
    value: str | Path | VerifiedTaiwanFactorySnapshot,
) -> VerifiedTaiwanFactorySnapshot:
    root = getattr(value, "root", value)
    return verify_taiwan_factory_snapshot(root)


def _verified_mof_snapshot(
    value: str | Path | VerifiedTaiwanMOFSnapshot,
    *,
    moenv_snapshot: object | None = None,
    factory_snapshot: object | None = None,
) -> VerifiedTaiwanMOFSnapshot:
    root = getattr(value, "root", value)
    if moenv_snapshot is not None and factory_snapshot is not None:
        return verify_taiwan_mof_allowlist_sources(
            root,
            getattr(moenv_snapshot, "root", moenv_snapshot),
            getattr(factory_snapshot, "root", factory_snapshot),
        )
    return verify_taiwan_mof_snapshot(root)


def _run_identity(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    expected_source_key: str,
    cutoff: str,
) -> TaiwanTaxIngestionIdentity:
    rows = _query(
        connection,
        """
        SELECT runs.id, runs.source_id, runs.input_document_id,
               runs.started_at, runs.completed_at, runs.status,
               runs.code_version, runs.parameters_json, runs.error,
               sources.stable_key AS source_key
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        WHERE runs.id = ?
        """,
        (ingestion_run_id,),
    )
    if len(rows) != 1:
        raise ValueError(f"unknown evidence producer run: {ingestion_run_id}")
    row = rows[0]
    if row["source_key"] != expected_source_key:
        raise ValueError(f"evidence producer {ingestion_run_id} has the wrong source")
    if row["status"] != "succeeded" or row["completed_at"] is None:
        raise ValueError(f"evidence producer {ingestion_run_id} did not succeed")
    completed = _canonical_timestamp(
        row["completed_at"], f"evidence producer {ingestion_run_id}.completed_at"
    )
    if _timestamp(completed) > _timestamp(cutoff):
        raise ValueError(f"evidence producer {ingestion_run_id} postdates cutoff")
    parameters = _json_object_from_database(
        row["parameters_json"],
        f"evidence producer {ingestion_run_id}.parameters_json",
    )
    payload = {
        "id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "input_document_id": row["input_document_id"],
        "started_at": _canonical_timestamp(
            row["started_at"], f"evidence producer {ingestion_run_id}.started_at"
        ),
        "completed_at": completed,
        "status": "succeeded",
        "code_version": row["code_version"],
        "parameters": parameters,
        "error": row["error"],
    }
    return TaiwanTaxIngestionIdentity(
        ingestion_run_id=ingestion_run_id,
        ingestion_run_sha256=hashlib.sha256(
            _canonical_json_bytes(payload)
        ).hexdigest(),
    )


def _nested_sha(parameters: Mapping[str, Any], key: str) -> str | None:
    nested = parameters.get(key)
    if not isinstance(nested, dict):
        return None
    digest = nested.get("sha256")
    return digest if isinstance(digest, str) else None


def _selected_binding(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    expected_source_key: str,
    snapshot_manifest_sha256: str,
    snapshot_data_sha256: str,
    snapshot_raw_sha256: str,
    cutoff: str,
) -> TaiwanTaxSourceBinding:
    rows = _query(
        connection,
        """
        SELECT runs.input_document_id, runs.parameters_json,
               documents.content_sha256 AS input_document_sha256
        FROM ingestion_runs AS runs
        LEFT JOIN source_documents AS documents
          ON documents.id = runs.input_document_id
        WHERE runs.id = ?
        """,
        (ingestion_run_id,),
    )
    identity = _run_identity(
        connection,
        ingestion_run_id=ingestion_run_id,
        expected_source_key=expected_source_key,
        cutoff=cutoff,
    )
    if len(rows) != 1:
        raise ValueError(f"unknown selected ingestion run: {ingestion_run_id}")
    row = rows[0]
    input_document_id = _clean_text(
        row["input_document_id"], f"ingestion run {ingestion_run_id}.input_document_id"
    )
    if row["input_document_sha256"] != snapshot_data_sha256:
        raise ValueError(f"ingestion run {ingestion_run_id} input document conflicts")
    parameters = _json_object_from_database(
        row["parameters_json"], f"ingestion run {ingestion_run_id}.parameters_json"
    )
    if parameters.get("manifest_sha256") != snapshot_manifest_sha256:
        raise ValueError(f"ingestion run {ingestion_run_id} manifest conflicts")
    derivative_key = (
        "matched_derivative"
        if expected_source_key == MOF_SOURCE_KEY
        else "candidate_derivative"
    )
    if _nested_sha(parameters, derivative_key) != snapshot_data_sha256:
        raise ValueError(f"ingestion run {ingestion_run_id} derivative conflicts")
    if _nested_sha(parameters, "raw_archive") != snapshot_raw_sha256:
        raise ValueError(f"ingestion run {ingestion_run_id} raw archive conflicts")
    return TaiwanTaxSourceBinding(
        source_key=expected_source_key,
        ingestion_run_id=ingestion_run_id,
        ingestion_run_sha256=identity.ingestion_run_sha256,
        input_document_id=input_document_id,
        snapshot_manifest_sha256=snapshot_manifest_sha256,
        snapshot_data_sha256=snapshot_data_sha256,
        snapshot_raw_sha256=snapshot_raw_sha256,
        evidence_producer_ingestion_runs=(identity,),
    )


def _with_producers(
    connection: sqlite3.Connection,
    *,
    binding: TaiwanTaxSourceBinding,
    producer_ids: Iterable[str],
    cutoff: str,
) -> TaiwanTaxSourceBinding:
    identities = tuple(
        _run_identity(
            connection,
            ingestion_run_id=run_id,
            expected_source_key=binding.source_key,
            cutoff=cutoff,
        )
        for run_id in sorted(set(producer_ids) | {binding.ingestion_run_id})
    )
    return replace(binding, evidence_producer_ingestion_runs=identities)


def _selected_ubn_groups(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    cutoff: str,
    predicate: str,
    expected_kind: str,
    source_key: str,
) -> dict[str, dict[str, Any]]:
    """Freeze selected-run membership and exact current UBN evidence.

    The selected run's start timestamp is the state boundary.  Claims and
    evidence records may come from that run or an earlier succeeded run of the
    same source, which preserves unchanged-claim reuse without admitting a later
    refresh.
    """

    rows = _query(
        connection,
        """
        WITH selected_state AS (
            SELECT id, source_id, started_at AS state_at
            FROM ingestion_runs
            WHERE id = ?
              AND status = 'succeeded'
              AND julianday(completed_at) <= julianday(?)
        ),
        selected_entities AS (
            SELECT records.id AS selected_source_record_id,
                   records.source_record_key AS selected_source_record_key,
                   records.observed_at AS selected_observed_at,
                   entities.id AS entity_id,
                   entities.stable_key AS entity_stable_key,
                   entities.kind AS entity_kind
            FROM selected_state
            JOIN source_records AS records
              ON records.ingestion_run_id = selected_state.id
            JOIN entities ON entities.stable_key = records.source_record_key
            WHERE entities.kind = ?
        )
        SELECT DISTINCT selected_entities.selected_source_record_id,
               selected_entities.selected_source_record_key,
               selected_entities.entity_id,
               selected_entities.entity_stable_key,
               selected_entities.entity_kind,
               versions.id AS claim_version_id,
               versions.valid_from, versions.valid_to,
               versions.created_by_run_id AS producer_ingestion_run_id,
               scalar_values.text_value AS raw_value,
               evidence.source_document_id,
               evidence_records.id AS evidence_source_record_id,
               evidence_records.source_record_key AS evidence_source_record_key,
               evidence_records.ingestion_run_id AS evidence_record_ingestion_run_id
        FROM selected_state
        JOIN selected_entities ON 1 = 1
        JOIN claim_series AS series
          ON series.subject_entity_id = selected_entities.entity_id
         AND series.predicate = ?
         AND series.value_kind = 'scalar'
        JOIN claim_versions AS versions ON versions.series_id = series.id
        JOIN scalar_values ON scalar_values.claim_version_id = versions.id
                           AND scalar_values.scalar_type = 'text'
        JOIN claim_evidence AS evidence
          ON evidence.claim_version_id = versions.id
         AND evidence.source_record_id IS NOT NULL
        JOIN source_records AS evidence_records
          ON evidence_records.id = evidence.source_record_id
         AND evidence_records.source_document_id = evidence.source_document_id
        JOIN ingestion_runs AS producer ON producer.id = versions.created_by_run_id
        JOIN ingestion_runs AS evidence_run
          ON evidence_run.id = evidence_records.ingestion_run_id
        WHERE producer.source_id = selected_state.source_id
          AND evidence_run.source_id = selected_state.source_id
          AND producer.status = 'succeeded'
          AND evidence_run.status = 'succeeded'
          AND (
              producer.id = selected_state.id
              OR julianday(producer.completed_at) <= julianday(selected_state.state_at)
          )
          AND (
              evidence_run.id = selected_state.id
              OR julianday(evidence_run.completed_at) <= julianday(selected_state.state_at)
          )
          AND julianday(selected_entities.selected_observed_at)
              <= julianday(selected_state.state_at)
          AND julianday(evidence_records.observed_at)
              <= julianday(selected_state.state_at)
          AND julianday(versions.recorded_at) <= julianday(selected_state.state_at)
          AND (
              versions.superseded_at IS NULL
              OR julianday(versions.superseded_at) > julianday(selected_state.state_at)
          )
        ORDER BY selected_entities.entity_id, versions.id,
                 evidence.source_document_id, evidence_records.id
        """,
        (ingestion_run_id, cutoff, expected_kind, predicate),
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row["entity_id"])
        group = grouped.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "entity_stable_key": str(row["entity_stable_key"]),
                "entity_kind": str(row["entity_kind"]),
                "selected_source_record_id": str(
                    row["selected_source_record_id"]
                ),
                "selected_source_record_key": str(
                    row["selected_source_record_key"]
                ),
                "evidence": [],
                "producer_ids": set(),
            },
        )
        evidence = TaiwanTaxEvidence(
            source_key=source_key,
            entity_id=entity_id,
            entity_stable_key=str(row["entity_stable_key"]),
            entity_kind=str(row["entity_kind"]),
            predicate=predicate,
            claim_version_id=str(row["claim_version_id"]),
            raw_value=(row["raw_value"] if isinstance(row["raw_value"], str) else ""),
            source_document_id=str(row["source_document_id"]),
            source_record_id=str(row["evidence_source_record_id"]),
            source_record_key=str(row["evidence_source_record_key"]),
            claim_valid_from=_canonical_date(
                row["valid_from"], f"claim {row['claim_version_id']}.valid_from"
            ),
            claim_valid_to=_optional_date(
                row["valid_to"], f"claim {row['claim_version_id']}.valid_to"
            ),
            producer_ingestion_run_id=str(row["producer_ingestion_run_id"]),
            evidence_record_ingestion_run_id=str(
                row["evidence_record_ingestion_run_id"]
            ),
        )
        if evidence not in group["evidence"]:
            group["evidence"].append(evidence)
        group["producer_ids"].add(evidence.producer_ingestion_run_id)
        group["producer_ids"].add(evidence.evidence_record_ingestion_run_id)
    for group in grouped.values():
        if not str(group["entity_stable_key"]).startswith(f"{source_key}:"):
            raise ValueError(f"{source_key} selected entity is not source-native")
        group["evidence"] = tuple(sorted(group["evidence"]))
        group["producer_ids"] = tuple(sorted(group["producer_ids"]))
    return grouped


def _exact_group_ubn(group: Mapping[str, Any]) -> str | None:
    evidence = tuple(group["evidence"])
    raw_values = {item.raw_value for item in evidence}
    if len(raw_values) != 1:
        return None
    raw = next(iter(raw_values))
    if not _UBN_RE.fullmatch(raw):
        return None
    return raw


def _interval_intersection(
    evidence: Sequence[TaiwanTaxEvidence],
    assignments: Sequence[TaiwanTaxCanonicalAssignment] = (),
) -> tuple[str, str | None] | None:
    starts = [item.claim_valid_from for item in evidence]
    starts.extend(item.valid_from for item in assignments)
    ends = [item.claim_valid_to for item in evidence if item.claim_valid_to is not None]
    ends.extend(item.valid_to for item in assignments if item.valid_to is not None)
    valid_from = max(starts)
    valid_to = min(ends) if ends else None
    if valid_to is not None and valid_to <= valid_from:
        return None
    return valid_from, valid_to


def _current_assignment(
    connection: sqlite3.Connection,
    *,
    observed_entity_id: str,
    cutoff: str,
    facility_evidence: Sequence[TaiwanTaxEvidence],
) -> tuple[TaiwanTaxCanonicalAssignment, str, str] | None:
    rows = _query(
        connection,
        """
        SELECT assignments.id, assignments.decision_id,
               assignments.observed_entity_id, assignments.canonical_entity_id,
               assignments.source_record_id, assignments.valid_from,
               assignments.valid_to, assignments.recorded_at,
               canonical.kind AS canonical_kind,
               canonical.stable_key AS canonical_stable_key,
               observed.kind AS observed_kind,
               decisions.outcome,
               resolution.status AS resolution_status
        FROM source_entity_assignments AS assignments
        JOIN entities AS canonical ON canonical.id = assignments.canonical_entity_id
        JOIN entities AS observed ON observed.id = assignments.observed_entity_id
        JOIN entity_resolution_decisions AS decisions
          ON decisions.id = assignments.decision_id
        JOIN entity_resolution_candidates AS candidates
          ON candidates.id = decisions.candidate_id
        JOIN entity_resolution_runs AS resolution
          ON resolution.id = candidates.resolution_run_id
        WHERE assignments.observed_entity_id = ?
          AND julianday(assignments.recorded_at) <= julianday(?)
          AND (
              assignments.superseded_at IS NULL
              OR julianday(assignments.superseded_at) > julianday(?)
          )
        ORDER BY assignments.recorded_at DESC, assignments.id DESC
        """,
        (observed_entity_id, cutoff, cutoff),
    )
    applicable: list[tuple[TaiwanTaxCanonicalAssignment, str, str]] = []
    for row in rows:
        if (
            row["observed_kind"] != "facility"
            or row["canonical_kind"] != "facility"
            or row["outcome"] != "match"
            or row["resolution_status"] != "succeeded"
        ):
            raise ValueError("facility assignment lineage is not a succeeded match")
        stable_key = str(row["canonical_stable_key"])
        if not stable_key.startswith(f"{FACTORY_SOURCE_KEY}:"):
            raise ValueError("MOENV assignment target is not factory-native")
        assignment = TaiwanTaxCanonicalAssignment(
            assignment_id=str(row["id"]),
            decision_id=str(row["decision_id"]),
            observed_entity_id=str(row["observed_entity_id"]),
            canonical_entity_id=str(row["canonical_entity_id"]),
            source_record_id=str(row["source_record_id"]),
            valid_from=_canonical_date(
                row["valid_from"], f"assignment {row['id']}.valid_from"
            ),
            valid_to=_optional_date(
                row["valid_to"], f"assignment {row['id']}.valid_to"
            ),
            recorded_at=_canonical_timestamp(
                row["recorded_at"], f"assignment {row['id']}.recorded_at"
            ),
        )
        if _interval_intersection(facility_evidence, (assignment,)) is not None:
            applicable.append((assignment, str(row["canonical_entity_id"]), stable_key))
    if len(applicable) > 1:
        raise ValueError("MOENV facility has multiple applicable current assignments")
    return applicable[0] if applicable else None


def _make_candidate(
    *,
    subject_entity_id: str,
    subject_entity_stable_key: str,
    tax_unit_entity_id: str,
    tax_unit_entity_stable_key: str,
    ubn: str,
    canonical_subject_rule: str,
    assignments: Sequence[TaiwanTaxCanonicalAssignment],
    facility_evidence: Sequence[TaiwanTaxEvidence],
    tax_evidence: Sequence[TaiwanTaxEvidence],
) -> TaiwanTaxRelationshipCandidate | None:
    sorted_assignments = tuple(sorted(set(assignments)))
    sorted_facility = tuple(sorted(set(facility_evidence)))
    sorted_tax = tuple(sorted(set(tax_evidence)))
    interval = _interval_intersection(
        (*sorted_facility, *sorted_tax), sorted_assignments
    )
    if interval is None:
        return None
    valid_from, valid_to = interval
    semantic = _candidate_semantic_payload(
        subject_entity_id=subject_entity_id,
        subject_entity_stable_key=subject_entity_stable_key,
        tax_unit_entity_id=tax_unit_entity_id,
        tax_unit_entity_stable_key=tax_unit_entity_stable_key,
        unified_business_number=ubn,
        canonical_subject_rule=canonical_subject_rule,
        canonical_assignments=sorted_assignments,
        facility_evidence=sorted_facility,
        tax_unit_evidence=sorted_tax,
        evidence_valid_from=valid_from,
        evidence_valid_to=valid_to,
    )
    return TaiwanTaxRelationshipCandidate(
        candidate_id=_candidate_id(semantic),
        subject_entity_id=subject_entity_id,
        subject_entity_stable_key=subject_entity_stable_key,
        tax_unit_entity_id=tax_unit_entity_id,
        tax_unit_entity_stable_key=tax_unit_entity_stable_key,
        unified_business_number=ubn,
        canonical_subject_rule=canonical_subject_rule,
        canonical_assignments=sorted_assignments,
        facility_evidence=sorted_facility,
        tax_unit_evidence=sorted_tax,
        evidence_valid_from=valid_from,
        evidence_valid_to=valid_to,
    )


def propose_taiwan_tax_relationship_candidates(
    connection: sqlite3.Connection,
    *,
    knowledge_cutoff_at: str,
    moenv_ingestion_run_id: str,
    factory_ingestion_run_id: str,
    mof_ingestion_run_id: str,
    moenv_snapshot: str | Path | VerifiedMOENVSnapshot,
    factory_snapshot: str | Path | VerifiedTaiwanFactorySnapshot,
    mof_snapshot: str | Path | VerifiedTaiwanMOFSnapshot,
) -> TaiwanTaxRelationshipCandidateArtifact:
    """Propose exact-UBN registration references without writing any row."""

    cutoff = _canonical_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
    moenv_verified = _verified_moenv_snapshot(moenv_snapshot)
    factory_verified = _verified_factory_snapshot(factory_snapshot)
    mof_verified = _verified_mof_snapshot(
        mof_snapshot,
        moenv_snapshot=moenv_verified,
        factory_snapshot=factory_verified,
    )
    version, schema_sha = _schema_identity(connection)
    moenv_binding = _selected_binding(
        connection,
        ingestion_run_id=_clean_text(
            moenv_ingestion_run_id, "moenv_ingestion_run_id"
        ),
        expected_source_key=MOENV_SOURCE_KEY,
        snapshot_manifest_sha256=moenv_verified.manifest_sha256,
        snapshot_data_sha256=moenv_verified.candidate_sha256,
        snapshot_raw_sha256=moenv_verified.raw_sha256,
        cutoff=cutoff,
    )
    factory_binding = _selected_binding(
        connection,
        ingestion_run_id=_clean_text(
            factory_ingestion_run_id, "factory_ingestion_run_id"
        ),
        expected_source_key=FACTORY_SOURCE_KEY,
        snapshot_manifest_sha256=factory_verified.manifest_sha256,
        snapshot_data_sha256=factory_verified.candidate_sha256,
        snapshot_raw_sha256=factory_verified.raw_sha256,
        cutoff=cutoff,
    )
    mof_binding = _selected_binding(
        connection,
        ingestion_run_id=_clean_text(mof_ingestion_run_id, "mof_ingestion_run_id"),
        expected_source_key=MOF_SOURCE_KEY,
        snapshot_manifest_sha256=mof_verified.manifest_sha256,
        snapshot_data_sha256=mof_verified.matched_sha256,
        snapshot_raw_sha256=mof_verified.raw_sha256,
        cutoff=cutoff,
    )
    if len(
        {
            moenv_binding.ingestion_run_id,
            factory_binding.ingestion_run_id,
            mof_binding.ingestion_run_id,
        }
    ) != 3:
        raise ValueError("selected source runs must be distinct")

    moenv_groups = _selected_ubn_groups(
        connection,
        ingestion_run_id=moenv_binding.ingestion_run_id,
        cutoff=cutoff,
        predicate=MOENV_UBN_PREDICATE,
        expected_kind="facility",
        source_key=MOENV_SOURCE_KEY,
    )
    factory_groups = _selected_ubn_groups(
        connection,
        ingestion_run_id=factory_binding.ingestion_run_id,
        cutoff=cutoff,
        predicate=FACTORY_UBN_PREDICATE,
        expected_kind="facility",
        source_key=FACTORY_SOURCE_KEY,
    )
    mof_groups = _selected_ubn_groups(
        connection,
        ingestion_run_id=mof_binding.ingestion_run_id,
        cutoff=cutoff,
        predicate=MOF_UBN_PREDICATE,
        expected_kind="organization",
        source_key=MOF_SOURCE_KEY,
    )

    tax_targets: dict[str, Mapping[str, Any]] = {}
    for group in mof_groups.values():
        ubn = _exact_group_ubn(group)
        if ubn is None:
            continue
        if ubn in tax_targets and tax_targets[ubn]["entity_id"] != group["entity_id"]:
            raise ValueError(f"MOF tax-unit UBN {ubn} is not unique")
        tax_targets[ubn] = group

    observations: list[dict[str, Any]] = []
    for source_key, groups in (
        (FACTORY_SOURCE_KEY, factory_groups),
        (MOENV_SOURCE_KEY, moenv_groups),
    ):
        for group in groups.values():
            ubn = _exact_group_ubn(group)
            if ubn is None or ubn not in tax_targets:
                continue
            if source_key == FACTORY_SOURCE_KEY:
                observations.append(
                    {
                        "subject_entity_id": group["entity_id"],
                        "subject_entity_stable_key": group["entity_stable_key"],
                        "ubn": ubn,
                        "rule": "source_native_facility",
                        "assignments": (),
                        "evidence": group["evidence"],
                    }
                )
                continue
            assignment_result = _current_assignment(
                connection,
                observed_entity_id=str(group["entity_id"]),
                cutoff=cutoff,
                facility_evidence=group["evidence"],
            )
            if assignment_result is None:
                observations.append(
                    {
                        "subject_entity_id": group["entity_id"],
                        "subject_entity_stable_key": group["entity_stable_key"],
                        "ubn": ubn,
                        "rule": "source_native_facility",
                        "assignments": (),
                        "evidence": group["evidence"],
                    }
                )
            else:
                assignment, subject_id, subject_key = assignment_result
                observations.append(
                    {
                        "subject_entity_id": subject_id,
                        "subject_entity_stable_key": subject_key,
                        "ubn": ubn,
                        "rule": "accepted_factory_native_assignment",
                        "assignments": (assignment,),
                        "evidence": group["evidence"],
                    }
                )

    grouped_observations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for observation in observations:
        target = tax_targets[observation["ubn"]]
        key = (
            str(observation["subject_entity_id"]),
            str(target["entity_id"]),
            str(observation["ubn"]),
        )
        grouped = grouped_observations.setdefault(
            key,
            {
                "subject_entity_id": observation["subject_entity_id"],
                "subject_entity_stable_key": observation[
                    "subject_entity_stable_key"
                ],
                "tax_unit_entity_id": target["entity_id"],
                "tax_unit_entity_stable_key": target["entity_stable_key"],
                "ubn": observation["ubn"],
                "rules": set(),
                "assignments": [],
                "facility_evidence": [],
                "tax_evidence": list(target["evidence"]),
            },
        )
        grouped["rules"].add(observation["rule"])
        grouped["assignments"].extend(observation["assignments"])
        grouped["facility_evidence"].extend(observation["evidence"])

    candidates: list[TaiwanTaxRelationshipCandidate] = []
    for grouped in grouped_observations.values():
        rules = grouped["rules"]
        if "accepted_factory_native_assignment" in rules and "source_native_facility" in rules:
            rule = "factory_native_with_assigned_moenv_provenance"
        elif "accepted_factory_native_assignment" in rules:
            rule = "accepted_factory_native_assignment"
        else:
            rule = "source_native_facility"
        candidate = _make_candidate(
            subject_entity_id=grouped["subject_entity_id"],
            subject_entity_stable_key=grouped["subject_entity_stable_key"],
            tax_unit_entity_id=grouped["tax_unit_entity_id"],
            tax_unit_entity_stable_key=grouped["tax_unit_entity_stable_key"],
            ubn=grouped["ubn"],
            canonical_subject_rule=rule,
            assignments=grouped["assignments"],
            facility_evidence=grouped["facility_evidence"],
            tax_evidence=grouped["tax_evidence"],
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.candidate_id)
    if len(candidates) > TAIWAN_TAX_RELATIONSHIP_MAX_CANDIDATES:
        raise ValueError("proposal exceeds the candidate-count limit")

    moenv_producers = {moenv_binding.ingestion_run_id}
    factory_producers = {factory_binding.ingestion_run_id}
    mof_producers = {mof_binding.ingestion_run_id}
    for candidate in candidates:
        for item in candidate.facility_evidence:
            target_set = (
                moenv_producers
                if item.source_key == MOENV_SOURCE_KEY
                else factory_producers
            )
            target_set.add(item.producer_ingestion_run_id)
            target_set.add(item.evidence_record_ingestion_run_id)
        for item in candidate.tax_unit_evidence:
            mof_producers.add(item.producer_ingestion_run_id)
            mof_producers.add(item.evidence_record_ingestion_run_id)
    moenv_binding = _with_producers(
        connection, binding=moenv_binding, producer_ids=moenv_producers, cutoff=cutoff
    )
    factory_binding = _with_producers(
        connection,
        binding=factory_binding,
        producer_ids=factory_producers,
        cutoff=cutoff,
    )
    mof_binding = _with_producers(
        connection, binding=mof_binding, producer_ids=mof_producers, cutoff=cutoff
    )
    canonical = _canonical_json_bytes(
        _candidate_artifact_payload(
            database_schema_version=version,
            database_schema_sha256=schema_sha,
            knowledge_cutoff_at=cutoff,
            moenv=moenv_binding,
            factory_registry=factory_binding,
            mof_tax_registry=mof_binding,
            candidates=candidates,
        )
    )
    return parse_taiwan_tax_relationship_candidate_bytes(canonical)


def _load_candidate_artifact(
    value: TaiwanTaxRelationshipCandidateArtifact | bytes | str | Path,
) -> TaiwanTaxRelationshipCandidateArtifact:
    if isinstance(value, TaiwanTaxRelationshipCandidateArtifact):
        return _reparse_candidate(value)
    if isinstance(value, bytes):
        return parse_taiwan_tax_relationship_candidate_bytes(value)
    if isinstance(value, (str, Path)):
        return read_taiwan_tax_relationship_candidate_file(value)
    raise TypeError("candidate_artifact must be an artifact, bytes, or file path")


def _reparse_review(
    artifact: TaiwanTaxRelationshipReviewArtifact,
    candidate: TaiwanTaxRelationshipCandidateArtifact,
) -> TaiwanTaxRelationshipReviewArtifact:
    parsed = parse_taiwan_tax_relationship_review_bytes(
        artifact.raw_bytes, candidate_artifact=candidate
    )
    if (
        parsed.raw_sha256 != artifact.raw_sha256
        or parsed.canonical_sha256 != artifact.canonical_sha256
        or parsed.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("review artifact object conflicts with its raw bytes")
    return replace(parsed, path=artifact.path)


def _load_review_artifact(
    value: TaiwanTaxRelationshipReviewArtifact | bytes | str | Path,
    *,
    candidate: TaiwanTaxRelationshipCandidateArtifact,
) -> TaiwanTaxRelationshipReviewArtifact:
    if isinstance(value, TaiwanTaxRelationshipReviewArtifact):
        return _reparse_review(value, candidate)
    if isinstance(value, bytes):
        return parse_taiwan_tax_relationship_review_bytes(
            value, candidate_artifact=candidate
        )
    if isinstance(value, (str, Path)):
        return read_taiwan_tax_relationship_review_file(
            value, candidate_artifact=candidate
        )
    raise TypeError("review_artifact must be an artifact, bytes, or file path")


def _reverify_artifact_files(
    candidate: TaiwanTaxRelationshipCandidateArtifact,
    review: TaiwanTaxRelationshipReviewArtifact,
) -> tuple[
    TaiwanTaxRelationshipCandidateArtifact,
    TaiwanTaxRelationshipReviewArtifact,
]:
    refreshed_candidate = (
        read_taiwan_tax_relationship_candidate_file(candidate.path)
        if candidate.path is not None
        else _reparse_candidate(candidate)
    )
    if (
        refreshed_candidate.raw_sha256 != candidate.raw_sha256
        or refreshed_candidate.canonical_sha256 != candidate.canonical_sha256
    ):
        raise ValueError("candidate artifact changed during acceptance")
    refreshed_review = (
        read_taiwan_tax_relationship_review_file(
            review.path, candidate_artifact=refreshed_candidate
        )
        if review.path is not None
        else _reparse_review(review, refreshed_candidate)
    )
    if (
        refreshed_review.raw_sha256 != review.raw_sha256
        or refreshed_review.canonical_sha256 != review.canonical_sha256
    ):
        raise ValueError("review artifact changed during acceptance")
    return refreshed_candidate, refreshed_review


def _snapshot_identities(
    moenv: object, factory: object, mof: object
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    return (
        (
            getattr(moenv, "manifest_sha256"),
            getattr(moenv, "candidate_sha256"),
            getattr(moenv, "raw_sha256"),
            getattr(moenv, "retrieved_at"),
        ),
        (
            getattr(factory, "manifest_sha256"),
            getattr(factory, "candidate_sha256"),
            getattr(factory, "raw_sha256"),
            getattr(factory, "retrieved_at"),
        ),
        (
            getattr(mof, "manifest_sha256"),
            getattr(mof, "matched_sha256"),
            getattr(mof, "raw_sha256"),
            getattr(mof, "retrieved_at"),
        ),
    )


def _assert_recomputed(
    connection: sqlite3.Connection,
    *,
    artifact: TaiwanTaxRelationshipCandidateArtifact,
    moenv_snapshot: object,
    factory_snapshot: object,
    mof_snapshot: object,
) -> None:
    actual = propose_taiwan_tax_relationship_candidates(
        connection,
        knowledge_cutoff_at=artifact.knowledge_cutoff_at,
        moenv_ingestion_run_id=artifact.moenv.ingestion_run_id,
        factory_ingestion_run_id=artifact.factory_registry.ingestion_run_id,
        mof_ingestion_run_id=artifact.mof_tax_registry.ingestion_run_id,
        moenv_snapshot=moenv_snapshot,
        factory_snapshot=factory_snapshot,
        mof_snapshot=mof_snapshot,
    )
    if (
        actual.canonical_sha256 != artifact.canonical_sha256
        or actual.canonical_bytes != artifact.canonical_bytes
    ):
        raise ValueError("candidate artifact is stale relative to the database")


def _binding_producer_ids(binding: TaiwanTaxSourceBinding) -> frozenset[str]:
    return frozenset(item.ingestion_run_id for item in binding.evidence_producer_ingestion_runs)


def _assert_evidence_current(
    connection: sqlite3.Connection,
    *,
    artifact: TaiwanTaxRelationshipCandidateArtifact,
    recorded_at: str,
) -> None:
    binding_by_source = {
        artifact.moenv.source_key: artifact.moenv,
        artifact.factory_registry.source_key: artifact.factory_registry,
        artifact.mof_tax_registry.source_key: artifact.mof_tax_registry,
    }
    for binding in binding_by_source.values():
        for expected in binding.evidence_producer_ingestion_runs:
            actual = _run_identity(
                connection,
                ingestion_run_id=expected.ingestion_run_id,
                expected_source_key=binding.source_key,
                cutoff=recorded_at,
            )
            if actual != expected:
                raise ValueError(
                    f"evidence producer {expected.ingestion_run_id} changed"
                )
    for candidate in artifact.candidates:
        for expected in (*candidate.facility_evidence, *candidate.tax_unit_evidence):
            binding = binding_by_source.get(expected.source_key)
            if binding is None:
                raise ValueError("evidence has an unbound source")
            producer_ids = _binding_producer_ids(binding)
            rows = _query(
                connection,
                """
                SELECT versions.id, versions.valid_from, versions.valid_to,
                       versions.recorded_at, versions.superseded_at,
                       versions.created_by_run_id,
                       series.subject_entity_id, series.predicate,
                       series.value_kind, scalar.text_value,
                       entities.kind AS entity_kind,
                       entities.stable_key AS entity_stable_key,
                       sources.stable_key AS source_key,
                       producer.status AS producer_status,
                       producer.completed_at AS producer_completed_at,
                       evidence.source_document_id,
                       records.id AS source_record_id,
                       records.source_record_key,
                       records.ingestion_run_id AS evidence_record_ingestion_run_id,
                       evidence_run.status AS evidence_run_status,
                       evidence_run.completed_at AS evidence_run_completed_at
                FROM claim_versions AS versions
                JOIN claim_series AS series ON series.id = versions.series_id
                JOIN entities ON entities.id = series.subject_entity_id
                JOIN scalar_values AS scalar ON scalar.claim_version_id = versions.id
                JOIN claim_evidence AS evidence
                  ON evidence.claim_version_id = versions.id
                 AND evidence.source_record_id = ?
                 AND evidence.source_document_id = ?
                JOIN source_records AS records
                  ON records.id = evidence.source_record_id
                 AND records.source_document_id = evidence.source_document_id
                JOIN ingestion_runs AS producer ON producer.id = versions.created_by_run_id
                JOIN sources ON sources.id = producer.source_id
                JOIN ingestion_runs AS evidence_run
                  ON evidence_run.id = records.ingestion_run_id
                WHERE versions.id = ?
                """,
                (
                    expected.source_record_id,
                    expected.source_document_id,
                    expected.claim_version_id,
                ),
            )
            if not rows:
                raise ValueError(
                    f"missing exact evidence claim {expected.claim_version_id}"
                )
            expected_tuple = (
                expected.source_key,
                expected.entity_id,
                expected.entity_stable_key,
                expected.entity_kind,
                expected.predicate,
                expected.claim_version_id,
                expected.raw_value,
                expected.source_document_id,
                expected.source_record_id,
                expected.source_record_key,
                expected.claim_valid_from,
                expected.claim_valid_to,
                expected.producer_ingestion_run_id,
                expected.evidence_record_ingestion_run_id,
            )
            for row in rows:
                actual_tuple = (
                    row["source_key"],
                    row["subject_entity_id"],
                    row["entity_stable_key"],
                    row["entity_kind"],
                    row["predicate"],
                    row["id"],
                    row["text_value"],
                    row["source_document_id"],
                    row["source_record_id"],
                    row["source_record_key"],
                    row["valid_from"],
                    row["valid_to"],
                    row["created_by_run_id"],
                    row["evidence_record_ingestion_run_id"],
                )
                superseded_at = row["superseded_at"]
                if (
                    actual_tuple != expected_tuple
                    or row["value_kind"] != "scalar"
                    or row["producer_status"] != "succeeded"
                    or row["evidence_run_status"] != "succeeded"
                    or row["created_by_run_id"] not in producer_ids
                    or row["evidence_record_ingestion_run_id"] not in producer_ids
                    or row["producer_completed_at"] is None
                    or row["evidence_run_completed_at"] is None
                    or _timestamp(
                        _canonical_timestamp(
                            row["producer_completed_at"], "producer completed_at"
                        )
                    )
                    > _timestamp(recorded_at)
                    or _timestamp(
                        _canonical_timestamp(
                            row["evidence_run_completed_at"],
                            "evidence run completed_at",
                        )
                    )
                    > _timestamp(recorded_at)
                    or (
                        superseded_at is not None
                        and _timestamp(
                            _canonical_timestamp(
                                superseded_at, "evidence superseded_at"
                            )
                        )
                        <= _timestamp(recorded_at)
                    )
                ):
                    raise ValueError(
                        f"evidence claim {expected.claim_version_id} "
                        "is stale or conflicts"
                    )
        for expected in candidate.canonical_assignments:
            rows = _query(
                connection,
                """
                SELECT assignments.*, canonical.kind AS canonical_kind,
                       canonical.stable_key AS canonical_stable_key,
                       observed.kind AS observed_kind,
                       decisions.outcome, resolution.status AS resolution_status
                FROM source_entity_assignments AS assignments
                JOIN entities AS canonical
                  ON canonical.id = assignments.canonical_entity_id
                JOIN entities AS observed ON observed.id = assignments.observed_entity_id
                JOIN entity_resolution_decisions AS decisions
                  ON decisions.id = assignments.decision_id
                JOIN entity_resolution_candidates AS candidates
                  ON candidates.id = decisions.candidate_id
                JOIN entity_resolution_runs AS resolution
                  ON resolution.id = candidates.resolution_run_id
                WHERE assignments.id = ?
                """,
                (expected.assignment_id,),
            )
            if len(rows) != 1:
                raise ValueError(f"missing canonical assignment {expected.assignment_id}")
            row = rows[0]
            if (
                tuple(
                    row[key]
                    for key in (
                        "id",
                        "decision_id",
                        "observed_entity_id",
                        "canonical_entity_id",
                        "source_record_id",
                        "valid_from",
                        "valid_to",
                        "recorded_at",
                    )
                )
                != (
                    expected.assignment_id,
                    expected.decision_id,
                    expected.observed_entity_id,
                    expected.canonical_entity_id,
                    expected.source_record_id,
                    expected.valid_from,
                    expected.valid_to,
                    expected.recorded_at,
                )
                or row["canonical_kind"] != "facility"
                or row["observed_kind"] != "facility"
                or not str(row["canonical_stable_key"]).startswith(
                    f"{FACTORY_SOURCE_KEY}:"
                )
                or row["outcome"] != "match"
                or row["resolution_status"] != "succeeded"
                or (
                    row["superseded_at"] is not None
                    and _timestamp(
                        _canonical_timestamp(
                            row["superseded_at"], "assignment superseded_at"
                        )
                    )
                    <= _timestamp(recorded_at)
                )
            ):
                raise ValueError(
                    f"canonical assignment {expected.assignment_id} is stale or conflicts"
                )


def _series_stable_key(subject_entity_id: str, tax_unit_entity_id: str) -> str:
    return "taiwan-tax-reference:" + stable_id(
        "relationship-series",
        TAIWAN_TAX_RELATIONSHIP_METHOD,
        subject_entity_id,
        tax_unit_entity_id,
    )


def _relationship_attributes(ubn: str) -> dict[str, object]:
    return {
        "jurisdiction": "TW",
        "reference_kind": "tax_unit",
        "unified_business_number": ubn,
        "asserts_identity": False,
        "asserts_ownership": False,
        "asserts_parentage": False,
        "asserts_operator_or_operation": False,
    }


def _claim_evidence_payload(
    candidate: TaiwanTaxRelationshipCandidate,
) -> list[dict[str, object]]:
    links = {
        (
            item.source_document_id,
            item.source_record_id,
            item.predicate,
            item.raw_value,
        )
        for item in (*candidate.facility_evidence, *candidate.tax_unit_evidence)
    }
    return [
        {
            "source_document_id": document_id,
            "source_record_id": record_id,
            "role": "support",
            "locator": predicate,
            "excerpt": raw_value,
        }
        for document_id, record_id, predicate, raw_value in sorted(links)
    ]


def _claim_dependency_payload(
    candidate: TaiwanTaxRelationshipCandidate,
) -> list[dict[str, str]]:
    claim_ids = sorted(
        {
            item.claim_version_id
            for item in (*candidate.facility_evidence, *candidate.tax_unit_evidence)
        }
    )
    return [
        {
            "depends_on_claim_version_id": claim_id,
            "dependency_kind": "derived_from",
        }
        for claim_id in claim_ids
    ]


def _desired_semantics(
    candidate: TaiwanTaxRelationshipCandidate,
    decision: TaiwanTaxRelationshipReviewDecision,
) -> dict[str, object]:
    assert decision.relationship_valid_from is not None
    return {
        "series_id": stable_id(
            "claim-series",
            TAIWAN_TAX_RELATIONSHIP_METHOD,
            candidate.subject_entity_id,
            candidate.tax_unit_entity_id,
        ),
        "series_stable_key": _series_stable_key(
            candidate.subject_entity_id, candidate.tax_unit_entity_id
        ),
        "subject_entity_id": candidate.subject_entity_id,
        "predicate": TAIWAN_TAX_RELATIONSHIP_PREDICATE,
        "value_kind": "relationship",
        "object_entity_id": candidate.tax_unit_entity_id,
        "relationship_type": TAIWAN_TAX_RELATIONSHIP_TYPE,
        "attributes": _relationship_attributes(candidate.unified_business_number),
        "valid_from": decision.relationship_valid_from,
        "valid_to": decision.relationship_valid_to,
        "claim_kind": "reconciled_fact",
        "method": TAIWAN_TAX_RELATIONSHIP_METHOD,
        "confidence": 1.0,
        "notes": (
            "Reviewed same-time registration reference only. This does not assert "
            "identity, legal-person identity, ownership, parentage, operator "
            "status, or facility operation."
        ),
        "evidence": _claim_evidence_payload(candidate),
        "dependencies": _claim_dependency_payload(candidate),
    }


def _open_relationship_claims(
    connection: sqlite3.Connection,
    *,
    candidate: TaiwanTaxRelationshipCandidate,
) -> list[sqlite3.Row]:
    rows = _query(
        connection,
        """
        SELECT versions.*, series.stable_key AS series_stable_key,
               series.subject_entity_id, series.predicate,
               relationships.object_entity_id, relationships.relationship_type,
               relationships.attributes_json,
               sources.stable_key AS creator_source_key
        FROM claim_series AS series
        JOIN claim_versions AS versions ON versions.series_id = series.id
        JOIN relationship_values AS relationships
          ON relationships.claim_version_id = versions.id
        LEFT JOIN ingestion_runs AS runs ON runs.id = versions.created_by_run_id
        LEFT JOIN sources ON sources.id = runs.source_id
        WHERE series.stable_key = ? AND versions.superseded_at IS NULL
        ORDER BY versions.recorded_at, versions.id
        """,
        (_series_stable_key(candidate.subject_entity_id, candidate.tax_unit_entity_id),),
    )
    for row in rows:
        if (
            row["series_id"]
            != stable_id(
                "claim-series",
                TAIWAN_TAX_RELATIONSHIP_METHOD,
                candidate.subject_entity_id,
                candidate.tax_unit_entity_id,
            )
            or row["subject_entity_id"] != candidate.subject_entity_id
            or row["predicate"] != TAIWAN_TAX_RELATIONSHIP_PREDICATE
            or row["value_kind"] != "relationship"
            or row["creator_source_key"] != REVIEW_SOURCE_KEY
        ):
            raise ValueError("relationship series conflicts with reviewed workflow ownership")
    return rows


def _stored_lineage(
    connection: sqlite3.Connection, claim_id: str
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    evidence = [
        {
            "source_document_id": str(row["source_document_id"]),
            "source_record_id": str(row["source_record_id"]),
            "role": str(row["role"]),
            "locator": str(row["locator"]),
            "excerpt": str(row["excerpt"]),
        }
        for row in _query(
            connection,
            """
            SELECT source_document_id, source_record_id, role, locator, excerpt
            FROM claim_evidence WHERE claim_version_id = ?
            ORDER BY source_document_id, source_record_id, role, locator, excerpt
            """,
            (claim_id,),
        )
    ]
    dependencies = [
        {
            "depends_on_claim_version_id": str(row["depends_on_claim_version_id"]),
            "dependency_kind": str(row["dependency_kind"]),
        }
        for row in _query(
            connection,
            """
            SELECT depends_on_claim_version_id, dependency_kind
            FROM claim_dependencies WHERE claim_version_id = ?
            ORDER BY depends_on_claim_version_id, dependency_kind
            """,
            (claim_id,),
        )
    ]
    return evidence, dependencies


def _row_matches_semantics(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    desired: Mapping[str, object],
) -> bool:
    try:
        attributes = _json_object_from_database(
            row["attributes_json"], f"claim {row['id']}.attributes_json"
        )
    except ValueError:
        return False
    evidence, dependencies = _stored_lineage(connection, str(row["id"]))
    return (
        row["series_id"] == desired["series_id"]
        and row["series_stable_key"] == desired["series_stable_key"]
        and row["subject_entity_id"] == desired["subject_entity_id"]
        and row["predicate"] == desired["predicate"]
        and row["value_kind"] == desired["value_kind"]
        and row["object_entity_id"] == desired["object_entity_id"]
        and row["relationship_type"] == desired["relationship_type"]
        and attributes == desired["attributes"]
        and row["valid_from"] == desired["valid_from"]
        and row["valid_to"] == desired["valid_to"]
        and row["claim_kind"] == desired["claim_kind"]
        and row["method"] == desired["method"]
        and float(row["confidence"]) == desired["confidence"]
        and row["notes"] == desired["notes"]
        and evidence == desired["evidence"]
        and dependencies == desired["dependencies"]
    )


def _claim_descriptor(
    *,
    claim_id: str,
    candidate_id: str,
    created_by_run_id: str,
    recorded_at: str,
    semantics: Mapping[str, object],
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "candidate_id": candidate_id,
        "created_by_run_id": created_by_run_id,
        "recorded_at": recorded_at,
        **dict(semantics),
    }


def _review_run_id(
    candidate: TaiwanTaxRelationshipCandidateArtifact,
    review: TaiwanTaxRelationshipReviewArtifact,
    accepted_at: str,
) -> str:
    return stable_id(
        "ingestion-run",
        TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
        candidate.raw_sha256,
        candidate.canonical_sha256,
        review.raw_sha256,
        review.canonical_sha256,
        accepted_at,
    )


def _parameter_prefix(
    *,
    candidate: TaiwanTaxRelationshipCandidateArtifact,
    review: TaiwanTaxRelationshipReviewArtifact,
    accepted_at: str,
) -> dict[str, object]:
    return {
        "workflow": TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
        "relationship_type": TAIWAN_TAX_RELATIONSHIP_TYPE,
        "accepted_at": accepted_at,
        "candidate_artifact": {
            "raw_sha256": candidate.raw_sha256,
            "raw_bytes": len(candidate.raw_bytes),
            "canonical_sha256": candidate.canonical_sha256,
            "canonical_bytes": len(candidate.canonical_bytes),
        },
        "review_artifact": {
            "raw_sha256": review.raw_sha256,
            "raw_bytes": len(review.raw_bytes),
            "canonical_sha256": review.canonical_sha256,
            "canonical_bytes": len(review.canonical_bytes),
        },
        "database_schema_version": candidate.database_schema_version,
        "database_schema_sha256": candidate.database_schema_sha256,
        "knowledge_cutoff_at": candidate.knowledge_cutoff_at,
        "reviewed_by": review.reviewed_by,
        "reviewed_at": review.reviewed_at,
        "source_bindings": {
            "moenv": _binding_payload(candidate.moenv),
            "factory_registry": _binding_payload(candidate.factory_registry),
            "mof_tax_registry": _binding_payload(candidate.mof_tax_registry),
        },
        "decisions": [_decision_payload(item) for item in review.decisions],
    }


def _existing_claim_descriptor(
    connection: sqlite3.Connection,
    *,
    row: Mapping[str, Any],
    candidate_id: str,
    semantics: Mapping[str, object],
) -> dict[str, object]:
    return _claim_descriptor(
        claim_id=str(row["id"]),
        candidate_id=candidate_id,
        created_by_run_id=str(row["created_by_run_id"]),
        recorded_at=_canonical_timestamp(
            row["recorded_at"], f"claim {row['id']}.recorded_at"
        ),
        semantics=semantics,
    )


def _acceptance_plan(
    connection: sqlite3.Connection,
    *,
    candidate_artifact: TaiwanTaxRelationshipCandidateArtifact,
    review_artifact: TaiwanTaxRelationshipReviewArtifact,
    run_id: str,
    recorded_at: str,
) -> tuple[
    list[dict[str, Any]],
    tuple[str, ...],
    tuple[dict[str, object], ...],
]:
    created: list[dict[str, Any]] = []
    superseded: set[str] = set()
    reaffirmed: list[dict[str, object]] = []
    for candidate, decision in zip(
        candidate_artifact.candidates, review_artifact.decisions, strict=True
    ):
        open_rows = _open_relationship_claims(connection, candidate=candidate)
        for row in open_rows:
            _verify_open_prior_claim(connection, row)
            if _timestamp(
                _canonical_timestamp(row["recorded_at"], "prior claim recorded_at")
            ) >= _timestamp(recorded_at):
                raise ValueError("prior relationship is not earlier than this review")
        if decision.outcome != "match":
            superseded.update(str(row["id"]) for row in open_rows)
            continue
        semantics = _desired_semantics(candidate, decision)
        if len(open_rows) == 1 and _row_matches_semantics(
            connection, open_rows[0], semantics
        ):
            reaffirmed.append(
                _existing_claim_descriptor(
                    connection,
                    row=open_rows[0],
                    candidate_id=candidate.candidate_id,
                    semantics=semantics,
                )
            )
            continue
        superseded.update(str(row["id"]) for row in open_rows)
        claim_id = stable_id(
            "claim-version",
            TAIWAN_TAX_RELATIONSHIP_METHOD,
            run_id,
            candidate.candidate_id,
            decision.relationship_valid_from,
            decision.relationship_valid_to or "",
        )
        descriptor = _claim_descriptor(
            claim_id=claim_id,
            candidate_id=candidate.candidate_id,
            created_by_run_id=run_id,
            recorded_at=recorded_at,
            semantics=semantics,
        )
        created.append(
            {
                "candidate": candidate,
                "decision": decision,
                "descriptor": descriptor,
            }
        )
    created.sort(key=lambda item: str(item["descriptor"]["claim_id"]))
    reaffirmed.sort(key=lambda item: str(item["claim_id"]))
    return created, tuple(sorted(superseded)), tuple(reaffirmed)


def _verify_open_prior_claim(
    connection: sqlite3.Connection, row: Mapping[str, Any]
) -> None:
    run_id = row["created_by_run_id"]
    if not isinstance(run_id, str):
        raise ValueError("reviewed relationship lacks a creator run")
    run = connection.execute(
        "SELECT parameters_json FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if run is None:
        raise ValueError("reviewed relationship creator run is missing")
    parameters = _json_object_from_database(
        run["parameters_json"], f"review run {run_id}.parameters_json"
    )
    created = parameters.get("created_claims")
    if not isinstance(created, list):
        raise ValueError("reviewed relationship creator lineage is malformed")
    descriptors = [
        item
        for item in created
        if isinstance(item, dict) and item.get("claim_id") == row["id"]
    ]
    if len(descriptors) != 1:
        raise ValueError("reviewed relationship is absent from creator lineage")
    _verify_claim_descriptor(
        connection, descriptors[0], allow_later_supersession=False
    )


def _parameters(
    *,
    candidate: TaiwanTaxRelationshipCandidateArtifact,
    review: TaiwanTaxRelationshipReviewArtifact,
    accepted_at: str,
    created: Sequence[Mapping[str, Any]],
    superseded_claim_ids: Sequence[str],
    reaffirmed: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        **_parameter_prefix(
            candidate=candidate, review=review, accepted_at=accepted_at
        ),
        "created_claims": [dict(item["descriptor"]) for item in created],
        "superseded_claim_ids": list(superseded_claim_ids),
        "reaffirmed_claims": [dict(item) for item in reaffirmed],
    }


def _source_created_at(
    connection: sqlite3.Connection, table: str, identifier: str, fallback: str
) -> str:
    row = connection.execute(
        f"SELECT created_at FROM {table} WHERE id = ?", (identifier,)
    ).fetchone()
    return fallback if row is None else str(row["created_at"])


def _ensure_review_source(
    connection: sqlite3.Connection, *, created_at: str
) -> tuple[str, int]:
    family_id = stable_id("source-family", REVIEW_SOURCE_FAMILY_KEY)
    source_id = stable_id("source", REVIEW_SOURCE_KEY)
    rows = 0
    rows += int(
        add_source_family(
            connection,
            SourceFamily(
                family_id,
                REVIEW_SOURCE_FAMILY_KEY,
                "Semiconductor Atlas reviewed relationships",
                _source_created_at(
                    connection, "source_families", family_id, created_at
                ),
                description=(
                    "Internal immutable ledger of explicit human relationship "
                    "reviews; not an external source statement."
                ),
            ),
        )
    )
    rows += int(
        add_source(
            connection,
            Source(
                source_id,
                family_id,
                REVIEW_SOURCE_KEY,
                "Reviewed Taiwan facility tax-unit references",
                "Semiconductor Atlas",
                REVIEW_SOURCE_URL,
                _source_created_at(connection, "sources", source_id, created_at),
            ),
        )
    )
    return source_id, rows


def _strict_parameter_list(
    parameters: Mapping[str, Any], key: str
) -> list[dict[str, Any]]:
    value = parameters.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"review ingestion parameter {key} must be an object array")
    return list(value)


def _later_supersession_is_proven(
    connection: sqlite3.Connection,
    *,
    claim_id: str,
    original_recorded_at: str,
    superseded_at: str,
) -> bool:
    for row in _query(
        connection,
        """
        SELECT runs.id, runs.started_at, runs.completed_at, runs.status,
               runs.parameters_json, sources.stable_key AS source_key
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        WHERE sources.stable_key = ?
          AND runs.status = 'succeeded'
          AND julianday(runs.started_at) > julianday(?)
          AND julianday(runs.completed_at) < julianday(?)
        ORDER BY runs.started_at, runs.id
        """,
        (REVIEW_SOURCE_KEY, original_recorded_at, superseded_at),
    ):
        try:
            parameters = _json_object_from_database(
                row["parameters_json"], f"review run {row['id']}.parameters_json"
            )
            declared = parameters.get("superseded_claim_ids")
            accepted = _canonical_timestamp(
                parameters.get("accepted_at"), f"review run {row['id']}.accepted_at"
            )
            expected_recorded = _expected_times(accepted)[3]
        except (TypeError, ValueError):
            continue
        if (
            isinstance(declared, list)
            and claim_id in declared
            and expected_recorded == superseded_at
        ):
            return True
    return False


def _verify_claim_descriptor(
    connection: sqlite3.Connection,
    descriptor: Mapping[str, Any],
    *,
    allow_later_supersession: bool,
) -> None:
    required = {
        "claim_id",
        "candidate_id",
        "created_by_run_id",
        "recorded_at",
        "series_id",
        "series_stable_key",
        "subject_entity_id",
        "predicate",
        "value_kind",
        "object_entity_id",
        "relationship_type",
        "attributes",
        "valid_from",
        "valid_to",
        "claim_kind",
        "method",
        "confidence",
        "notes",
        "evidence",
        "dependencies",
    }
    if set(descriptor) != required:
        raise ValueError("review ingestion contains a malformed claim descriptor")
    claim_id = _clean_text(descriptor["claim_id"], "claim descriptor.claim_id")
    rows = _query(
        connection,
        """
        SELECT versions.*, series.stable_key AS series_stable_key,
               series.subject_entity_id, series.predicate,
               relationships.object_entity_id, relationships.relationship_type,
               relationships.attributes_json
        FROM claim_versions AS versions
        JOIN claim_series AS series ON series.id = versions.series_id
        JOIN relationship_values AS relationships
          ON relationships.claim_version_id = versions.id
        WHERE versions.id = ?
        """,
        (claim_id,),
    )
    if len(rows) != 1:
        raise ValueError(f"review relationship claim {claim_id} is missing")
    row = rows[0]
    desired_semantics = {
        key: descriptor[key]
        for key in (
            "series_id",
            "series_stable_key",
            "subject_entity_id",
            "predicate",
            "value_kind",
            "object_entity_id",
            "relationship_type",
            "attributes",
            "valid_from",
            "valid_to",
            "claim_kind",
            "method",
            "confidence",
            "notes",
            "evidence",
            "dependencies",
        )
    }
    if (
        row["created_by_run_id"] != descriptor["created_by_run_id"]
        or row["recorded_at"] != descriptor["recorded_at"]
        or not _row_matches_semantics(connection, row, desired_semantics)
    ):
        raise ValueError(f"review relationship claim {claim_id} conflicts")
    superseded_at = row["superseded_at"]
    if superseded_at is not None and (
        not allow_later_supersession
        or not _later_supersession_is_proven(
            connection,
            claim_id=claim_id,
            original_recorded_at=str(row["recorded_at"]),
            superseded_at=str(superseded_at),
        )
    ):
        raise ValueError(
            f"review relationship claim {claim_id} has an unproven supersession"
        )


def _verify_exact_replay(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    candidate: TaiwanTaxRelationshipCandidateArtifact,
    review: TaiwanTaxRelationshipReviewArtifact,
    accepted_at: str,
) -> TaiwanTaxRelationshipAcceptanceResult:
    rows = _query(
        connection,
        """
        SELECT runs.*, sources.stable_key AS source_key
        FROM ingestion_runs AS runs
        JOIN sources ON sources.id = runs.source_id
        WHERE runs.id = ?
        """,
        (run_id,),
    )
    if len(rows) != 1:
        raise ValueError("review replay run disappeared")
    row = rows[0]
    started, completed, _decided, recorded = _expected_times(accepted_at)
    if (
        row["source_key"] != REVIEW_SOURCE_KEY
        or row["input_document_id"] is not None
        or row["started_at"] != started
        or row["completed_at"] != completed
        or row["status"] != "succeeded"
        or row["code_version"] != TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION
        or row["error"] is not None
    ):
        raise ValueError("review replay ingestion run conflicts")
    parameters = _json_object_from_database(
        row["parameters_json"], f"review run {run_id}.parameters_json"
    )
    prefix = _parameter_prefix(
        candidate=candidate, review=review, accepted_at=accepted_at
    )
    if any(parameters.get(key) != value for key, value in prefix.items()):
        raise ValueError("review replay lineage conflicts with supplied artifacts")
    if set(parameters) != set(prefix) | {
        "created_claims",
        "superseded_claim_ids",
        "reaffirmed_claims",
    }:
        raise ValueError("review replay parameters contain unexpected fields")
    created = _strict_parameter_list(parameters, "created_claims")
    reaffirmed = _strict_parameter_list(parameters, "reaffirmed_claims")
    superseded_ids = _ids_array(
        parameters.get("superseded_claim_ids"), "superseded_claim_ids"
    )
    match_ids = {
        decision.candidate_id
        for decision in review.decisions
        if decision.outcome == "match"
    }
    described_match_ids = {
        _clean_text(item.get("candidate_id"), "claim descriptor.candidate_id")
        for item in (*created, *reaffirmed)
    }
    if described_match_ids != match_ids or len(described_match_ids) != len(
        created
    ) + len(reaffirmed):
        raise ValueError("review replay match-claim coverage conflicts")
    for descriptor in (*created, *reaffirmed):
        _verify_claim_descriptor(
            connection, descriptor, allow_later_supersession=True
        )
    for claim_id in superseded_ids:
        old = connection.execute(
            "SELECT superseded_at FROM claim_versions WHERE id = ?", (claim_id,)
        ).fetchone()
        if old is None or old["superseded_at"] != recorded:
            raise ValueError("review replay prior-claim supersession conflicts")
    errors = validate_database(connection)
    if errors:
        raise RuntimeError(
            "Taiwan tax-relationship replay failed database validation: "
            + "; ".join(errors)
        )
    return TaiwanTaxRelationshipAcceptanceResult(
        review_ingestion_run_id=run_id,
        relationship_claim_ids=tuple(
            sorted(str(item["claim_id"]) for item in created)
        ),
        superseded_claim_ids=superseded_ids,
        reaffirmed_claim_ids=tuple(
            sorted(str(item["claim_id"]) for item in reaffirmed)
        ),
        replayed=True,
        rows_written=0,
    )


def accept_taiwan_tax_relationship_review(
    connection: sqlite3.Connection,
    *,
    candidate_artifact: TaiwanTaxRelationshipCandidateArtifact | bytes | str | Path,
    review_artifact: TaiwanTaxRelationshipReviewArtifact | bytes | str | Path,
    moenv_snapshot: str | Path | VerifiedMOENVSnapshot,
    factory_snapshot: str | Path | VerifiedTaiwanFactorySnapshot,
    mof_snapshot: str | Path | VerifiedTaiwanMOFSnapshot,
    accepted_at: str,
) -> TaiwanTaxRelationshipAcceptanceResult:
    """Atomically accept one complete reviewed registration-reference artifact.

    Reject and defer decisions create no relationship claim.  They can close a
    prior claim in the exact same subject-to-tax-unit series.  Exact replays are
    verified and write zero rows.
    """

    candidates = _load_candidate_artifact(candidate_artifact)
    review = _load_review_artifact(review_artifact, candidate=candidates)
    acceptance = _canonical_timestamp(accepted_at, "accepted_at")
    if _timestamp(acceptance) < _timestamp(review.reviewed_at):
        raise ValueError("accepted_at must not predate reviewed_at")
    if _timestamp(acceptance) < _timestamp(candidates.knowledge_cutoff_at):
        raise ValueError("accepted_at must not predate the candidate cutoff")

    moenv_verified = _verified_moenv_snapshot(moenv_snapshot)
    factory_verified = _verified_factory_snapshot(factory_snapshot)
    mof_verified = _verified_mof_snapshot(
        mof_snapshot,
        moenv_snapshot=moenv_verified,
        factory_snapshot=factory_verified,
    )
    expected_snapshot_ids = (
        (
            candidates.moenv.snapshot_manifest_sha256,
            candidates.moenv.snapshot_data_sha256,
            candidates.moenv.snapshot_raw_sha256,
            getattr(moenv_verified, "retrieved_at"),
        ),
        (
            candidates.factory_registry.snapshot_manifest_sha256,
            candidates.factory_registry.snapshot_data_sha256,
            candidates.factory_registry.snapshot_raw_sha256,
            getattr(factory_verified, "retrieved_at"),
        ),
        (
            candidates.mof_tax_registry.snapshot_manifest_sha256,
            candidates.mof_tax_registry.snapshot_data_sha256,
            candidates.mof_tax_registry.snapshot_raw_sha256,
            getattr(mof_verified, "retrieved_at"),
        ),
    )
    if _snapshot_identities(
        moenv_verified, factory_verified, mof_verified
    ) != expected_snapshot_ids:
        raise ValueError("a selected snapshot conflicts with the candidate artifact")
    version, schema_sha = _schema_identity(connection)
    if (
        version != candidates.database_schema_version
        or schema_sha != candidates.database_schema_sha256
    ):
        raise ValueError("database schema identity changed after candidate generation")
    _assert_recomputed(
        connection,
        artifact=candidates,
        moenv_snapshot=moenv_verified,
        factory_snapshot=factory_verified,
        mof_snapshot=mof_verified,
    )

    run_id = _review_run_id(candidates, review, acceptance)
    if connection.execute(
        "SELECT 1 FROM ingestion_runs WHERE id = ?", (run_id,)
    ).fetchone():
        result = _verify_exact_replay(
            connection,
            run_id=run_id,
            candidate=candidates,
            review=review,
            accepted_at=acceptance,
        )
        refreshed_candidate, refreshed_review = _reverify_artifact_files(
            candidates, review
        )
        if (
            refreshed_candidate.raw_sha256 != candidates.raw_sha256
            or refreshed_review.raw_sha256 != review.raw_sha256
            or _snapshot_identities(
                _verified_moenv_snapshot(moenv_verified),
                _verified_factory_snapshot(factory_verified),
                _verified_mof_snapshot(
                    mof_verified,
                    moenv_snapshot=moenv_verified,
                    factory_snapshot=factory_verified,
                ),
            )
            != expected_snapshot_ids
        ):
            raise ValueError("an acceptance input changed during replay verification")
        return result

    started, completed, _decided, recorded = _expected_times(acceptance)
    for binding in (
        candidates.moenv,
        candidates.factory_registry,
        candidates.mof_tax_registry,
    ):
        for expected in binding.evidence_producer_ingestion_runs:
            if (
                _run_identity(
                    connection,
                    ingestion_run_id=expected.ingestion_run_id,
                    expected_source_key=binding.source_key,
                    cutoff=started,
                )
                != expected
            ):
                raise ValueError("review start does not follow immutable input runs")
    _assert_evidence_current(
        connection, artifact=candidates, recorded_at=recorded
    )
    created, superseded_ids, reaffirmed = _acceptance_plan(
        connection,
        candidate_artifact=candidates,
        review_artifact=review,
        run_id=run_id,
        recorded_at=recorded,
    )
    parameters = _parameters(
        candidate=candidates,
        review=review,
        accepted_at=acceptance,
        created=created,
        superseded_claim_ids=superseded_ids,
        reaffirmed=reaffirmed,
    )

    mode, savepoint = _begin_atomic(connection)
    rows_written = 0
    try:
        source_id, source_rows = _ensure_review_source(
            connection, created_at=started
        )
        rows_written += source_rows
        rows_written += int(
            add_ingestion_run(
                connection,
                IngestionRun(
                    run_id,
                    source_id,
                    started,
                    status=IngestionStatus.SUCCEEDED,
                    completed_at=completed,
                    code_version=TAIWAN_TAX_RELATIONSHIP_REVIEW_VERSION,
                    parameters=parameters,
                ),
            )
        )
        for claim_id in superseded_ids:
            cursor = connection.execute(
                """
                UPDATE claim_versions SET superseded_at = ?
                WHERE id = ? AND superseded_at IS NULL
                """,
                (recorded, claim_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"prior relationship {claim_id} changed during acceptance")
            rows_written += 1
        for item in created:
            candidate = item["candidate"]
            descriptor = item["descriptor"]
            assert isinstance(candidate, TaiwanTaxRelationshipCandidate)
            series_id = str(descriptor["series_id"])
            series_row = connection.execute(
                "SELECT created_at FROM claim_series WHERE id = ?", (series_id,)
            ).fetchone()
            series_created_at = (
                recorded if series_row is None else str(series_row["created_at"])
            )
            rows_written += int(
                add_claim_series(
                    connection,
                    ClaimSeries(
                        series_id,
                        str(descriptor["subject_entity_id"]),
                        str(descriptor["series_stable_key"]),
                        TAIWAN_TAX_RELATIONSHIP_PREDICATE,
                        ValueKind.RELATIONSHIP,
                        series_created_at,
                    ),
                )
            )
            evidence_links = tuple(
                EvidenceLink(
                    str(link["source_document_id"]),
                    EvidenceRole.SUPPORT,
                    source_record_id=str(link["source_record_id"]),
                    locator=str(link["locator"]),
                    excerpt=str(link["excerpt"]),
                )
                for link in descriptor["evidence"]
            )
            dependency_links = tuple(
                DependencyLink(
                    str(link["depends_on_claim_version_id"]),
                    DependencyKind.DERIVED_FROM,
                )
                for link in descriptor["dependencies"]
            )
            rows_written += int(
                insert_claim(
                    connection,
                    ClaimVersion(
                        str(descriptor["claim_id"]),
                        series_id,
                        str(descriptor["valid_from"]),
                        recorded,
                        ClaimKind.RECONCILED_FACT,
                        TAIWAN_TAX_RELATIONSHIP_METHOD,
                        1.0,
                        valid_to=(
                            None
                            if descriptor["valid_to"] is None
                            else str(descriptor["valid_to"])
                        ),
                        created_by_run_id=run_id,
                        notes=str(descriptor["notes"]),
                    ),
                    RelationshipValue(
                        candidate.tax_unit_entity_id,
                        TAIWAN_TAX_RELATIONSHIP_TYPE,
                        attributes=_relationship_attributes(
                            candidate.unified_business_number
                        ),
                    ),
                    evidence=evidence_links,
                    dependencies=dependency_links,
                )
            )

        refreshed_candidates, refreshed_review = _reverify_artifact_files(
            candidates, review
        )
        refreshed_moenv = _verified_moenv_snapshot(moenv_verified)
        refreshed_factory = _verified_factory_snapshot(factory_verified)
        refreshed_mof = _verified_mof_snapshot(
            mof_verified,
            moenv_snapshot=refreshed_moenv,
            factory_snapshot=refreshed_factory,
        )
        if _snapshot_identities(
            refreshed_moenv, refreshed_factory, refreshed_mof
        ) != expected_snapshot_ids:
            raise ValueError("a selected snapshot changed during acceptance")
        _assert_recomputed(
            connection,
            artifact=refreshed_candidates,
            moenv_snapshot=refreshed_moenv,
            factory_snapshot=refreshed_factory,
            mof_snapshot=refreshed_mof,
        )
        _assert_evidence_current(
            connection, artifact=refreshed_candidates, recorded_at=recorded
        )
        if refreshed_review.canonical_sha256 != review.canonical_sha256:
            raise ValueError("review artifact changed during acceptance")
        for item in created:
            _verify_claim_descriptor(
                connection,
                item["descriptor"],
                allow_later_supersession=False,
            )
        errors = validate_database(connection)
        if errors:
            raise RuntimeError(
                "Taiwan tax-relationship acceptance failed database validation: "
                + "; ".join(errors)
            )
        _commit_atomic(connection, mode, savepoint)
    except BaseException:
        _rollback_atomic(connection, mode, savepoint)
        raise

    return TaiwanTaxRelationshipAcceptanceResult(
        review_ingestion_run_id=run_id,
        relationship_claim_ids=tuple(
            sorted(str(item["descriptor"]["claim_id"]) for item in created)
        ),
        superseded_claim_ids=superseded_ids,
        reaffirmed_claim_ids=tuple(
            sorted(str(item["claim_id"]) for item in reaffirmed)
        ),
        replayed=False,
        rows_written=rows_written,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m semiconductor_atlas.taiwan_tax_relationship",
        description=(
            "Propose or accept reviewed Taiwan facility-to-tax-unit references."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser(
        "propose", help="write a canonical read-only candidate artifact"
    )
    propose.add_argument("--database", required=True)
    propose.add_argument("--moenv-snapshot", required=True)
    propose.add_argument("--factory-snapshot", required=True)
    propose.add_argument("--mof-snapshot", required=True)
    propose.add_argument("--moenv-ingestion-run-id", required=True)
    propose.add_argument("--factory-ingestion-run-id", required=True)
    propose.add_argument("--mof-ingestion-run-id", required=True)
    propose.add_argument("--knowledge-cutoff-at", required=True)
    propose.add_argument("--output", required=True)

    accept = commands.add_parser(
        "accept", help="atomically persist one complete reviewed artifact"
    )
    accept.add_argument("--database", required=True)
    accept.add_argument("--moenv-snapshot", required=True)
    accept.add_argument("--factory-snapshot", required=True)
    accept.add_argument("--mof-snapshot", required=True)
    accept.add_argument("--candidates", required=True)
    accept.add_argument("--review", required=True)
    accept.add_argument("--accepted-at", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    connection = connect(arguments.database)
    try:
        if arguments.command == "propose":
            artifact = propose_taiwan_tax_relationship_candidates(
                connection,
                knowledge_cutoff_at=arguments.knowledge_cutoff_at,
                moenv_ingestion_run_id=arguments.moenv_ingestion_run_id,
                factory_ingestion_run_id=arguments.factory_ingestion_run_id,
                mof_ingestion_run_id=arguments.mof_ingestion_run_id,
                moenv_snapshot=arguments.moenv_snapshot,
                factory_snapshot=arguments.factory_snapshot,
                mof_snapshot=arguments.mof_snapshot,
            )
            created = _write_canonical_artifact(
                arguments.output, artifact.canonical_bytes
            )
            summary: dict[str, object] = {
                "candidate_artifact_canonical_sha256": artifact.canonical_sha256,
                "candidate_count": len(artifact.candidates),
                "created": created,
                "output": str(Path(arguments.output).absolute()),
            }
        else:
            result = accept_taiwan_tax_relationship_review(
                connection,
                candidate_artifact=arguments.candidates,
                review_artifact=arguments.review,
                moenv_snapshot=arguments.moenv_snapshot,
                factory_snapshot=arguments.factory_snapshot,
                mof_snapshot=arguments.mof_snapshot,
                accepted_at=arguments.accepted_at,
            )
            summary = {
                "relationship_claim_ids": list(result.relationship_claim_ids),
                "reaffirmed_claim_ids": list(result.reaffirmed_claim_ids),
                "replayed": result.replayed,
                "review_ingestion_run_id": result.review_ingestion_run_id,
                "rows_written": result.rows_written,
                "superseded_claim_ids": list(result.superseded_claim_ids),
            }
    finally:
        connection.close()
    sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0


__all__ = [
    "FACTORY_UBN_PREDICATE",
    "MOENV_UBN_PREDICATE",
    "MOF_UBN_PREDICATE",
    "TAIWAN_TAX_RELATIONSHIP_CANDIDATE_FORMAT",
    "TAIWAN_TAX_RELATIONSHIP_PREDICATE",
    "TAIWAN_TAX_RELATIONSHIP_REVIEW_FORMAT",
    "TAIWAN_TAX_RELATIONSHIP_TYPE",
    "TaiwanTaxRelationshipAcceptanceResult",
    "TaiwanTaxRelationshipCandidate",
    "TaiwanTaxRelationshipCandidateArtifact",
    "TaiwanTaxRelationshipReviewArtifact",
    "TaiwanTaxRelationshipReviewDecision",
    "accept_taiwan_tax_relationship_review",
    "build_taiwan_tax_relationship_review",
    "main",
    "parse_taiwan_tax_relationship_candidate_bytes",
    "parse_taiwan_tax_relationship_review_bytes",
    "propose_taiwan_tax_relationship_candidates",
    "read_taiwan_tax_relationship_candidate_file",
    "read_taiwan_tax_relationship_review_file",
]


if __name__ == "__main__":
    raise SystemExit(main())
