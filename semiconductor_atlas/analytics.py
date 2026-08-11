"""Connect evidence-backed claim views to experimental forecasts and alert exports."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Iterator, Sequence, overload

from .forecast import (
    ASSUMPTION_STATUS,
    DEFAULT_MODEL_PARAMETERS,
    HORIZON_QUARTERS,
    CapacityInput,
    ForecastResult,
    ModelParameters,
    RampMilestone,
    forecast_capacity,
)
from .models import CapacityBasis
from .service import claim_records


PRODUCTION_MILESTONES = {
    "mass_production",
    "production_start",
    "ramp_start",
    "volume_production",
}

ACTIVE_MILESTONE_STATUSES = {"expected", "started", "completed", "delayed"}
RELATIVE_CAPACITY_UNITS = {
    "%",
    "pct",
    "percent",
    "percentage",
    "percentage point",
    "percentage points",
    "fraction",
    "ratio",
    "index",
    "x",
    "times",
}


@dataclass(frozen=True, slots=True)
class ForecastExclusion:
    entity_id: str
    capacity_claim_id: str
    reason: str
    detail: str
    related_claim_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ForecastPairing:
    capacity_claim_id: str
    milestone_claim_id: str
    method: str
    shared_source_document_ids: tuple[str, ...]
    assumption_status: str = ASSUMPTION_STATUS

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ForecastSelection(Sequence[ForecastResult]):
    results: tuple[ForecastResult, ...]
    exclusions: tuple[ForecastExclusion, ...]
    pairings: tuple[ForecastPairing, ...] = ()

    @overload
    def __getitem__(self, index: int) -> ForecastResult: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ForecastResult, ...]: ...

    def __getitem__(self, index: int | slice) -> ForecastResult | tuple[ForecastResult, ...]:
        return self.results[index]

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[ForecastResult]:
        return iter(self.results)


def _production_milestones(
    claims: list[dict[str, object]],
) -> tuple[dict[str, RampMilestone], tuple[str, ...]]:
    candidates: dict[str, RampMilestone] = {}
    cancelled: list[str] = []
    for claim in claims:
        if claim["value_kind"] != "milestone":
            continue
        value = claim["value"]
        assert isinstance(value, dict)
        if value.get("milestone_type") not in PRODUCTION_MILESTONES:
            continue
        claim_id = str(claim["id"])
        status = str(value.get("status"))
        if status == "cancelled":
            cancelled.append(claim_id)
            continue
        if status not in ACTIVE_MILESTONE_STATUSES:
            continue
        candidates[claim_id] = RampMilestone(
            date.fromisoformat(str(value["date_low"])),
            date.fromisoformat(str(value["date_base"])),
            date.fromisoformat(str(value["date_high"])),
            claim_id,
        )
    return candidates, tuple(sorted(set(cancelled)))


def _dependency_ids(claim: dict[str, object]) -> set[str]:
    dependencies = claim.get("dependencies")
    if not isinstance(dependencies, list):
        return set()
    return {
        str(item["depends_on_claim_version_id"])
        for item in dependencies
        if isinstance(item, dict) and item.get("depends_on_claim_version_id")
    }


def _linked_milestones(
    connection: sqlite3.Connection,
    capacity_claim: dict[str, object],
    milestone_claims: dict[str, dict[str, object]],
) -> dict[str, ForecastPairing]:
    capacity_id = str(capacity_claim["id"])
    capacity_dependencies = _dependency_ids(capacity_claim)
    capacity_documents = _lineage_document_ids(connection, capacity_id)
    linked: dict[str, ForecastPairing] = {}
    for milestone_id, milestone_claim in milestone_claims.items():
        milestone_dependencies = _dependency_ids(milestone_claim)
        direct_lineage = (
            milestone_id in capacity_dependencies
            or capacity_id in milestone_dependencies
            or bool(capacity_dependencies & milestone_dependencies)
        )
        shared_documents = tuple(
            sorted(capacity_documents & _lineage_document_ids(connection, milestone_id))
        )
        if direct_lineage or shared_documents:
            linked[milestone_id] = ForecastPairing(
                capacity_id,
                milestone_id,
                (
                    "claim_lineage"
                    if direct_lineage
                    else "same_entity_shared_transitive_source_document"
                ),
                shared_documents,
            )
    return linked


def _lineage_document_ids(
    connection: sqlite3.Connection,
    claim_id: str,
) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            WITH RECURSIVE lineage(id) AS (
                SELECT ?
                UNION
                SELECT dependencies.depends_on_claim_version_id
                FROM claim_dependencies AS dependencies
                JOIN lineage ON lineage.id = dependencies.claim_version_id
            )
            SELECT DISTINCT evidence.source_document_id
            FROM lineage
            JOIN claim_evidence AS evidence ON evidence.claim_version_id = lineage.id
            ORDER BY evidence.source_document_id
            """,
            (claim_id,),
        )
    }


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _add_quarters(value: date, quarters: int) -> date:
    month = value.year * 12 + value.month - 1 + 3 * quarters
    year, zero_based_month = divmod(month, 12)
    return date(year, zero_based_month + 1, 1)


def _relative_capacity(value: dict[str, object]) -> bool:
    unit = " ".join(str(value.get("unit") or "").casefold().split())
    metric = " ".join(str(value.get("metric") or "").casefold().split())
    return (
        unit in RELATIVE_CAPACITY_UNITS
        or unit.endswith(" percent")
        or unit.endswith(" percentage points")
        or "percentage" in metric
        or "percent" in metric
        or "relative" in metric
        or "multiple" in metric
        or "fold" in metric
        or ("increase" in metric and unit in {"", "%", "percent"})
    )


def _spreadsheet_safe_cell(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip()
    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _period_exclusion(
    value: dict[str, object],
    *,
    forecast_start: date,
    horizon_end: date,
    milestone: RampMilestone | None,
) -> tuple[str, str] | None:
    period_start = (
        date.fromisoformat(str(value["period_start"]))
        if value.get("period_start") is not None
        else None
    )
    period_end = (
        date.fromisoformat(str(value["period_end"]))
        if value.get("period_end") is not None
        else None
    )
    if period_end is not None and period_end <= forecast_start:
        return "expired_capacity_period", f"capacity period ended at {period_end.isoformat()}"
    if period_start is not None and period_start >= horizon_end:
        return (
            "capacity_period_outside_horizon",
            f"capacity period starts at {period_start.isoformat()}, outside the forecast horizon",
        )
    if period_end is not None and period_end < horizon_end:
        return (
            "capacity_period_ends_within_horizon",
            "baseline model cannot safely extend a time-bounded capacity beyond "
            f"{period_end.isoformat()}",
        )
    if period_start is not None and period_start > forecast_start:
        if milestone is None:
            return (
                "future_capacity_period_without_linked_milestone",
                f"capacity does not apply until {period_start.isoformat()}",
            )
        if milestone.production_start_low < period_start:
            return (
                "capacity_period_milestone_mismatch",
                "linked milestone can start before the capacity period begins",
            )
    return None


def forecast_current_capacity(
    connection: sqlite3.Connection,
    *,
    as_of: str,
    recorded_at: str,
    forecast_start: date,
    parameters: ModelParameters = DEFAULT_MODEL_PARAMETERS,
    claims: Sequence[dict[str, object]] | None = None,
) -> ForecastSelection:
    by_entity: dict[str, list[dict[str, object]]] = defaultdict(list)
    claim_view = (
        list(claims)
        if claims is not None
        else claim_records(connection, as_of=as_of, recorded_at=recorded_at)
    )
    for claim in claim_view:
        by_entity[str(claim["subject_entity_id"])].append(claim)
    results: list[ForecastResult] = []
    exclusions: list[ForecastExclusion] = []
    pairings: list[ForecastPairing] = []
    normalized_start = _quarter_start(forecast_start)
    horizon_end = _add_quarters(normalized_start, HORIZON_QUARTERS)
    for entity_id, claims in sorted(by_entity.items()):
        milestone_values, cancelled_milestones = _production_milestones(claims)
        milestone_claims = {
            str(claim["id"]): claim
            for claim in claims
            if str(claim["id"]) in milestone_values
        }
        for claim in claims:
            if claim["value_kind"] != "capacity":
                continue
            value = claim["value"]
            assert isinstance(value, dict)
            capacity_claim_id = str(claim["id"])
            basis = CapacityBasis(str(value["basis"]))
            if _relative_capacity(value):
                exclusions.append(
                    ForecastExclusion(
                        entity_id,
                        capacity_claim_id,
                        "relative_capacity_not_forecastable",
                        "relative or percentage capacity is not an absolute production quantity",
                    )
                )
                continue
            milestone: RampMilestone | None = None
            selected_pairing: ForecastPairing | None = None
            if basis is not CapacityBasis.ECONOMICALLY_USABLE:
                linked = _linked_milestones(connection, claim, milestone_claims)
                linked_ids = tuple(sorted(linked))
                if len(linked_ids) == 1:
                    milestone = milestone_values[linked_ids[0]]
                    selected_pairing = linked[linked_ids[0]]
                else:
                    if len(linked_ids) > 1:
                        reason = "ambiguous_linked_production_milestones"
                        detail = "capacity links to more than one active production milestone"
                        related = linked_ids
                    elif milestone_values:
                        reason = "unlinked_production_milestone"
                        detail = "active production milestones exist but none has explicit claim lineage to this capacity"
                        related = tuple(sorted(milestone_values))
                    elif cancelled_milestones:
                        reason = "cancelled_production_milestone"
                        detail = "only cancelled production milestones are available"
                        related = cancelled_milestones
                    else:
                        reason = "missing_production_milestone"
                        detail = "announced through qualified capacity requires one linked active production milestone"
                        related = ()
                    exclusions.append(
                        ForecastExclusion(
                            entity_id,
                            capacity_claim_id,
                            reason,
                            detail,
                            related,
                        )
                    )
                    continue
            period_problem = _period_exclusion(
                value,
                forecast_start=normalized_start,
                horizon_end=horizon_end,
                milestone=milestone,
            )
            if period_problem is not None:
                reason, detail = period_problem
                exclusions.append(
                    ForecastExclusion(
                        entity_id,
                        capacity_claim_id,
                        reason,
                        detail,
                        ((milestone.milestone_claim_id,) if milestone else ()),
                    )
                )
                continue
            if selected_pairing is not None:
                pairings.append(selected_pairing)
            capacity_input = CapacityInput(
                entity_id=entity_id,
                metric=str(value["metric"]),
                unit=str(value["unit"]),
                basis=basis,
                low=float(value["low"]),
                base=float(value["base"]),
                high=float(value["high"]),
                capacity_claim_id=capacity_claim_id,
                production_start=milestone,
            )
            results.append(
                forecast_capacity(
                    capacity_input,
                    forecast_start=forecast_start,
                    parameters=parameters,
                )
            )
    ordered_results = tuple(
        sorted(
            results,
            key=lambda item: (
                item.capacity_input.entity_id,
                item.capacity_input.metric,
                item.capacity_input.unit,
                item.capacity_input.capacity_claim_id,
            ),
        )
    )
    ordered_exclusions = tuple(
        sorted(
            exclusions,
            key=lambda item: (item.entity_id, item.capacity_claim_id, item.reason),
        )
    )
    ordered_pairings = tuple(
        sorted(pairings, key=lambda item: (item.capacity_claim_id, item.milestone_claim_id))
    )
    return ForecastSelection(ordered_results, ordered_exclusions, ordered_pairings)


def forecast_jsonl(results: Iterable[ForecastResult]) -> bytes:
    pairings = {
        item.capacity_claim_id: item
        for item in tuple(getattr(results, "pairings", ()))
    }
    return (
        "".join(
            json.dumps(
                {
                    **result.to_dict(),
                    "milestone_pairing": (
                        pairings[result.capacity_input.capacity_claim_id].as_dict()
                        if result.capacity_input.capacity_claim_id in pairings
                        else None
                    ),
                },
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for result in results
        )
    ).encode("utf-8")


def forecast_csv(results: Iterable[ForecastResult]) -> bytes:
    output = io.StringIO(newline="")
    fields = (
        "entity_id",
        "capacity_claim_id",
        "milestone_claim_id",
        "milestone_pairing_method",
        "shared_source_document_ids",
        "pairing_assumption_status",
        "input_basis",
        "output_basis",
        "metric",
        "unit",
        "quarter",
        "period_start",
        "period_end",
        "p10",
        "p50",
        "p90",
        "parameter_fingerprint",
        "assumption_status",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    pairings = {
        item.capacity_claim_id: item
        for item in tuple(getattr(results, "pairings", ()))
    }
    for result in results:
        milestone_id = (
            result.capacity_input.production_start.milestone_claim_id
            if result.capacity_input.production_start
            else ""
        )
        pairing = pairings.get(result.capacity_input.capacity_claim_id)
        for point in result.points:
            row = {
                    "entity_id": result.capacity_input.entity_id,
                    "capacity_claim_id": result.capacity_input.capacity_claim_id,
                    "milestone_claim_id": milestone_id,
                    "milestone_pairing_method": pairing.method if pairing else "",
                    "shared_source_document_ids": (
                        ";".join(pairing.shared_source_document_ids) if pairing else ""
                    ),
                    "pairing_assumption_status": pairing.assumption_status if pairing else "",
                    "input_basis": result.capacity_input.basis.value,
                    "output_basis": "economically_usable",
                    "metric": result.capacity_input.metric,
                    "unit": result.capacity_input.unit,
                    "quarter": point.quarter,
                    "period_start": point.period_start.isoformat(),
                    "period_end": point.period_end.isoformat(),
                    "p10": point.p10,
                    "p50": point.p50,
                    "p90": point.p90,
                    "parameter_fingerprint": result.parameters.fingerprint,
                    "assumption_status": ASSUMPTION_STATUS,
                }
            writer.writerow({key: _spreadsheet_safe_cell(value) for key, value in row.items()})
    return output.getvalue().encode("utf-8")


def forecast_summary(results: Iterable[ForecastResult]) -> bytes:
    rows = list(results)
    exclusions = tuple(getattr(results, "exclusions", ()))
    pairings = tuple(getattr(results, "pairings", ()))
    reason_counts: dict[str, int] = defaultdict(int)
    for exclusion in exclusions:
        reason_counts[exclusion.reason] += 1
    value = {
        "assumption_status": ASSUMPTION_STATUS,
        "capacity_series": len(rows),
        "forecast_points": sum(len(row.points) for row in rows),
        "excluded_capacity_inputs": len(exclusions),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "exclusions": [item.as_dict() for item in exclusions],
        "milestone_pairing_rule": (
            "For announced through qualified inputs, require exactly one active production "
            "milestone on the same entity linked by claim lineage or a shared transitive source "
            "document. Shared-document pairing is an analytical assumption, not a canonical fact."
        ),
        "milestone_pairings": [item.as_dict() for item in pairings],
        "model_parameter_fingerprints": sorted({row.parameters.fingerprint for row in rows}),
        "warning": "Transparent baseline assumptions only; not calibrated or approved for investment use.",
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def forecast_exclusions_jsonl(results: Iterable[ForecastResult]) -> bytes:
    exclusions = tuple(getattr(results, "exclusions", ()))
    return (
        "".join(
            json.dumps(
                item.as_dict(),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for item in exclusions
        )
    ).encode("utf-8")
