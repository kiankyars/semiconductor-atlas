"""Transparent deterministic baseline forecasts for economically usable capacity.

This module deliberately contains no fitted coefficients, clock reads, randomness, or I/O. The
default realization factors and ramp curves are labelled assumptions. Callers can replace them with
validated parameter sets, but must not present either the defaults or overrides as observed facts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from math import fsum, isfinite
from typing import Iterable

from .models import CapacityBasis


HORIZON_QUARTERS = 20
OUTPUT_BASIS = CapacityBasis.ECONOMICALLY_USABLE
ASSUMPTION_STATUS = "assumption_not_fact"


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")


def _date(value: date, field_name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a date")


def _ordered_nonnegative(low: float, base: float, high: float, field_name: str) -> None:
    values = (low, base, high)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError(f"{field_name} values must be numbers")
    if not all(isfinite(float(value)) for value in values):
        raise ValueError(f"{field_name} values must be finite")
    if not 0 <= float(low) <= float(base) <= float(high):
        raise ValueError(f"{field_name} must satisfy 0 <= low <= base <= high")


def _quarter_start(value: date) -> date:
    month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, month, 1)


def _add_quarters(value: date, quarters: int) -> date:
    absolute_month = value.year * 12 + value.month - 1 + 3 * quarters
    year, zero_based_month = divmod(absolute_month, 12)
    return date(year, zero_based_month + 1, 1)


def _quarter_label(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _quarter_offset(start: date, target: date) -> int:
    return (target.year - start.year) * 4 + ((target.month - 1) // 3) - (
        (start.month - 1) // 3
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class QuantileValues:
    """An ordered probability range used only for model assumptions."""

    p10: float
    p50: float
    p90: float

    def __post_init__(self) -> None:
        _ordered_nonnegative(self.p10, self.p50, self.p90, "quantiles")
        if self.p90 > 1:
            raise ValueError("assumption quantiles must be between 0 and 1")

    def to_dict(self) -> dict[str, float]:
        return {"p10": float(self.p10), "p50": float(self.p50), "p90": float(self.p90)}


@dataclass(frozen=True, slots=True)
class BasisRealization:
    """Assumed eventual usable share of one input capacity basis."""

    basis: CapacityBasis
    factors: QuantileValues

    def __post_init__(self) -> None:
        if not isinstance(self.basis, CapacityBasis):
            raise ValueError("basis must be one of the five CapacityBasis values")
        if not isinstance(self.factors, QuantileValues):
            raise ValueError("factors must be QuantileValues")

    def to_dict(self) -> dict[str, object]:
        return {"basis": self.basis.value, "factors": self.factors.to_dict()}


@dataclass(frozen=True, slots=True)
class RampFraction:
    """Assumed usable fraction at a zero-based quarter offset from production start."""

    quarter_offset: int
    fractions: QuantileValues

    def __post_init__(self) -> None:
        if (
            isinstance(self.quarter_offset, bool)
            or not isinstance(self.quarter_offset, int)
            or self.quarter_offset < 0
        ):
            raise ValueError("quarter_offset must be a nonnegative integer")
        if not isinstance(self.fractions, QuantileValues):
            raise ValueError("fractions must be QuantileValues")

    def to_dict(self) -> dict[str, object]:
        return {"quarter_offset": self.quarter_offset, "fractions": self.fractions.to_dict()}


class MissingMilestonePolicy(StrEnum):
    """Explicit assumption for a capacity claim without a production-start milestone."""

    AUTO_BY_BASIS = "assume_start_at_horizon_except_economically_usable_steady_state"
    START_AT_HORIZON = "assume_start_at_horizon"
    STEADY_STATE = "assume_steady_state_at_horizon"


DEFAULT_BASIS_REALIZATION_FACTORS = (
    BasisRealization(CapacityBasis.ANNOUNCED, QuantileValues(0.45, 0.70, 0.90)),
    BasisRealization(CapacityBasis.PHYSICAL_CONSTRUCTION, QuantileValues(0.65, 0.82, 0.95)),
    BasisRealization(CapacityBasis.TOOL_INSTALLED, QuantileValues(0.80, 0.92, 1.00)),
    BasisRealization(CapacityBasis.QUALIFIED, QuantileValues(0.90, 0.97, 1.00)),
    BasisRealization(CapacityBasis.ECONOMICALLY_USABLE, QuantileValues(1.00, 1.00, 1.00)),
)


DEFAULT_RAMP_FRACTIONS = (
    RampFraction(0, QuantileValues(0.05, 0.15, 0.30)),
    RampFraction(1, QuantileValues(0.15, 0.35, 0.60)),
    RampFraction(2, QuantileValues(0.30, 0.60, 0.82)),
    RampFraction(3, QuantileValues(0.50, 0.80, 0.95)),
    RampFraction(4, QuantileValues(0.70, 0.92, 1.00)),
    RampFraction(5, QuantileValues(0.85, 1.00, 1.00)),
    RampFraction(6, QuantileValues(0.95, 1.00, 1.00)),
    RampFraction(7, QuantileValues(1.00, 1.00, 1.00)),
)


@dataclass(frozen=True, slots=True)
class ModelParameters:
    """Versioned assumptions for the transparent baseline model."""

    model_name: str = "transparent_baseline_ramp"
    model_version: str = "1.0"
    basis_realization_factors: tuple[BasisRealization, ...] = DEFAULT_BASIS_REALIZATION_FACTORS
    ramp_fractions: tuple[RampFraction, ...] = DEFAULT_RAMP_FRACTIONS
    missing_milestone_policy: MissingMilestonePolicy = MissingMilestonePolicy.AUTO_BY_BASIS
    round_digits: int = 9
    basis_factor_label: str = (
        "assumption: eventual economically usable share of each input capacity basis; not a fact"
    )
    ramp_curve_label: str = (
        "assumption: quarterly usable fraction after production start; not a fact"
    )

    def __post_init__(self) -> None:
        _required(self.model_name, "model_name")
        _required(self.model_version, "model_version")
        for field_name in ("basis_factor_label", "ramp_curve_label"):
            value = getattr(self, field_name)
            _required(value, field_name)
            if not value.startswith("assumption:") or "not a fact" not in value:
                raise ValueError(f"{field_name} must label the values as assumptions, not facts")
        if not isinstance(self.basis_realization_factors, tuple) or not all(
            isinstance(item, BasisRealization) for item in self.basis_realization_factors
        ):
            raise ValueError("basis_realization_factors must be a tuple of BasisRealization values")
        if not isinstance(self.ramp_fractions, tuple) or not all(
            isinstance(item, RampFraction) for item in self.ramp_fractions
        ):
            raise ValueError("ramp_fractions must be a tuple of RampFraction values")
        if not isinstance(self.missing_milestone_policy, MissingMilestonePolicy):
            raise ValueError("missing_milestone_policy is invalid")
        if (
            isinstance(self.round_digits, bool)
            or not isinstance(self.round_digits, int)
            or not 0 <= self.round_digits <= 12
        ):
            raise ValueError("round_digits must be an integer between 0 and 12")

        factors = {item.basis: item.factors for item in self.basis_realization_factors}
        if len(factors) != len(self.basis_realization_factors):
            raise ValueError("basis_realization_factors cannot contain duplicate bases")
        if set(factors) != set(CapacityBasis):
            raise ValueError(
                "basis_realization_factors must contain each of the five bases exactly once"
            )
        previous = None
        for basis in CapacityBasis:
            current = factors[basis]
            if previous is not None and (
                current.p10 < previous.p10
                or current.p50 < previous.p50
                or current.p90 < previous.p90
            ):
                raise ValueError("basis realization factors must be monotone by capacity maturity")
            previous = current

        if not self.ramp_fractions:
            raise ValueError("ramp_fractions cannot be empty")
        ramp = sorted(self.ramp_fractions, key=lambda item: item.quarter_offset)
        if [item.quarter_offset for item in ramp] != list(range(len(ramp))):
            raise ValueError("ramp quarter offsets must be contiguous and start at zero")
        previous = None
        for item in ramp:
            current = item.fractions
            if previous is not None and (
                current.p10 < previous.p10
                or current.p50 < previous.p50
                or current.p90 < previous.p90
            ):
                raise ValueError("ramp fractions must be monotone over time")
            previous = current

    def factor_for(self, basis: CapacityBasis) -> QuantileValues:
        if not isinstance(basis, CapacityBasis):
            raise ValueError("basis must be one of the five CapacityBasis values")
        return next(item.factors for item in self.basis_realization_factors if item.basis is basis)

    def ramp_for(self, quarter_offset: int) -> QuantileValues:
        if quarter_offset < 0:
            return QuantileValues(0.0, 0.0, 0.0)
        ramp = sorted(self.ramp_fractions, key=lambda item: item.quarter_offset)
        return ramp[min(quarter_offset, len(ramp) - 1)].fractions

    def starts_at_horizon_without_milestone(self, basis: CapacityBasis) -> bool:
        """Apply the documented missing-timing assumption for one input basis."""

        if not isinstance(basis, CapacityBasis):
            raise ValueError("basis must be one of the five CapacityBasis values")
        if self.missing_milestone_policy is MissingMilestonePolicy.START_AT_HORIZON:
            return True
        if self.missing_milestone_policy is MissingMilestonePolicy.STEADY_STATE:
            return False
        return basis is not CapacityBasis.ECONOMICALLY_USABLE

    @property
    def missing_milestone_policy_label(self) -> str:
        if self.missing_milestone_policy is MissingMilestonePolicy.AUTO_BY_BASIS:
            return (
                "assumption: without a milestone, announced through qualified capacity starts "
                "ramping at the forecast horizon, while economically usable capacity is already "
                "steady-state; not a fact"
            )
        if self.missing_milestone_policy is MissingMilestonePolicy.START_AT_HORIZON:
            return (
                "assumption: without a milestone, every capacity basis starts ramping at the "
                "forecast horizon; not a fact"
            )
        return (
            "assumption: without a milestone, every capacity basis is already steady-state at "
            "the forecast horizon; not a fact"
        )

    def to_dict(self) -> dict[str, object]:
        factors = {item.basis: item for item in self.basis_realization_factors}
        return {
            "assumption_status": ASSUMPTION_STATUS,
            "basis_factor_label": self.basis_factor_label,
            "basis_realization_factors": [factors[basis].to_dict() for basis in CapacityBasis],
            "horizon_quarters": HORIZON_QUARTERS,
            "missing_milestone_policy": self.missing_milestone_policy.value,
            "missing_milestone_policy_label": self.missing_milestone_policy_label,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "ramp_curve_label": self.ramp_curve_label,
            "ramp_fractions": [
                item.to_dict()
                for item in sorted(
                    self.ramp_fractions, key=lambda item: item.quarter_offset
                )
            ],
            "round_digits": self.round_digits,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


DEFAULT_MODEL_PARAMETERS = ModelParameters()


@dataclass(frozen=True, slots=True)
class RampMilestone:
    """Evidence-backed interval for the first production quarter."""

    production_start_low: date
    production_start_base: date
    production_start_high: date
    milestone_claim_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "production_start_low",
            "production_start_base",
            "production_start_high",
        ):
            _date(getattr(self, field_name), field_name)
        if not (
            self.production_start_low
            <= self.production_start_base
            <= self.production_start_high
        ):
            raise ValueError("production start dates must satisfy low <= base <= high")
        _required(self.milestone_claim_id, "milestone_claim_id")

    def to_dict(self) -> dict[str, str]:
        return {
            "milestone_claim_id": self.milestone_claim_id,
            "production_start_base": self.production_start_base.isoformat(),
            "production_start_high": self.production_start_high.isoformat(),
            "production_start_low": self.production_start_low.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CapacityInput:
    """One evidence-backed capacity interval used by the baseline model."""

    entity_id: str
    metric: str
    unit: str
    basis: CapacityBasis
    low: float
    base: float
    high: float
    capacity_claim_id: str
    production_start: RampMilestone | None = None

    def __post_init__(self) -> None:
        for field_name in ("entity_id", "metric", "unit", "capacity_claim_id"):
            _required(getattr(self, field_name), field_name)
        if not isinstance(self.basis, CapacityBasis):
            raise ValueError("basis must be exactly one of the five CapacityBasis values")
        _ordered_nonnegative(self.low, self.base, self.high, "capacity input")
        if self.production_start is not None and not isinstance(
            self.production_start, RampMilestone
        ):
            raise ValueError("production_start must be a RampMilestone")
        if (
            self.production_start is not None
            and self.production_start.milestone_claim_id == self.capacity_claim_id
        ):
            raise ValueError("capacity and milestone claim IDs must be distinct")

    @property
    def input_claim_ids(self) -> tuple[str, ...]:
        if self.production_start is None:
            return (self.capacity_claim_id,)
        return (self.capacity_claim_id, self.production_start.milestone_claim_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "base": float(self.base),
            "basis": self.basis.value,
            "capacity_claim_id": self.capacity_claim_id,
            "entity_id": self.entity_id,
            "high": float(self.high),
            "input_claim_ids": list(self.input_claim_ids),
            "low": float(self.low),
            "metric": self.metric,
            "production_start": self.production_start.to_dict() if self.production_start else None,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Economically usable capacity available by the end of one calendar quarter."""

    quarter: str
    period_start: date
    period_end: date
    p10: float
    p50: float
    p90: float

    def __post_init__(self) -> None:
        _date(self.period_start, "period_start")
        _date(self.period_end, "period_end")
        if self.period_start != _quarter_start(self.period_start):
            raise ValueError("period_start must be the first day of a calendar quarter")
        if self.period_end != _add_quarters(self.period_start, 1):
            raise ValueError("period_end must be the exclusive start of the next quarter")
        if self.quarter != _quarter_label(self.period_start):
            raise ValueError("quarter must match period_start")
        _ordered_nonnegative(self.p10, self.p50, self.p90, "forecast point")

    def to_dict(self) -> dict[str, object]:
        return {
            "p10": float(self.p10),
            "p50": float(self.p50),
            "p90": float(self.p90),
            "period_end": self.period_end.isoformat(),
            "period_start": self.period_start.isoformat(),
            "quarter": self.quarter,
        }


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """One complete, reproducible 20-quarter baseline forecast."""

    capacity_input: CapacityInput
    forecast_start: date
    points: tuple[ForecastPoint, ...]
    parameters: ModelParameters
    reasoning: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.capacity_input, CapacityInput):
            raise ValueError("capacity_input must be a CapacityInput")
        _date(self.forecast_start, "forecast_start")
        if self.forecast_start != _quarter_start(self.forecast_start):
            raise ValueError("forecast_start must be the first day of a calendar quarter")
        if not isinstance(self.points, tuple) or len(self.points) != HORIZON_QUARTERS:
            raise ValueError(f"forecast must contain exactly {HORIZON_QUARTERS} quarterly points")
        if not all(isinstance(point, ForecastPoint) for point in self.points):
            raise ValueError("points must be ForecastPoint values")
        expected_starts = tuple(
            _add_quarters(self.forecast_start, index) for index in range(HORIZON_QUARTERS)
        )
        if tuple(point.period_start for point in self.points) != expected_starts:
            raise ValueError("forecast points must be contiguous and aligned to forecast_start")
        for previous, current in zip(self.points, self.points[1:]):
            if (
                current.p10 < previous.p10
                or current.p50 < previous.p50
                or current.p90 < previous.p90
            ):
                raise ValueError("baseline forecast points must be monotone over time")
        if not isinstance(self.parameters, ModelParameters):
            raise ValueError("parameters must be ModelParameters")
        if not isinstance(self.reasoning, tuple) or not self.reasoning:
            raise ValueError("reasoning must be a nonempty tuple")
        for step in self.reasoning:
            _required(step, "reasoning step")

    @property
    def input_claim_ids(self) -> tuple[str, ...]:
        return self.capacity_input.input_claim_ids

    def to_dict(self) -> dict[str, object]:
        factor = self.parameters.factor_for(self.capacity_input.basis)
        milestone = self.capacity_input.production_start
        if milestone is not None:
            starts = {
                "p10": milestone.production_start_high.isoformat(),
                "p50": milestone.production_start_base.isoformat(),
                "p90": milestone.production_start_low.isoformat(),
            }
            start_source = "milestone_claim"
        elif self.parameters.starts_at_horizon_without_milestone(
            self.capacity_input.basis
        ):
            starts = {
                quantile: self.forecast_start.isoformat()
                for quantile in ("p10", "p50", "p90")
            }
            start_source = "model_assumption_forecast_horizon"
        else:
            starts = {quantile: None for quantile in ("p10", "p50", "p90")}
            start_source = "model_assumption_steady_state"
        return {
            "calculation": {
                "basis_realization_factors_used": {
                    "basis": self.capacity_input.basis.value,
                    "factors": factor.to_dict(),
                },
                "formula": {
                    "p10": "input.low * basis_factor.p10 * ramp_fraction.p10",
                    "p50": "input.base * basis_factor.p50 * ramp_fraction.p50",
                    "p90": "input.high * basis_factor.p90 * ramp_fraction.p90",
                },
                "production_start_dates_used": starts,
                "production_start_source": start_source,
                "ramp_lookup": (
                    "zero before mapped start quarter; indexed from the quarter containing the "
                    "mapped date; final configured fraction is held thereafter"
                ),
            },
            "forecast_start": self.forecast_start.isoformat(),
            "horizon_quarters": HORIZON_QUARTERS,
            "input": self.capacity_input.to_dict(),
            "input_claim_ids": list(self.input_claim_ids),
            "output": {
                "basis": OUTPUT_BASIS.value,
                "metric": self.capacity_input.metric,
                "points": [point.to_dict() for point in self.points],
                "quarter_value_semantics": "capacity available by quarter end",
                "unit": self.capacity_input.unit,
            },
            "parameter_fingerprint": self.parameters.fingerprint,
            "parameters": self.parameters.to_dict(),
            "reasoning": list(self.reasoning),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ForecastGrouping:
    """An explicit aggregate dimension and value, such as country=US."""

    dimension: str
    value: str

    def __post_init__(self) -> None:
        _required(self.dimension, "grouping dimension")
        _required(self.value, "grouping value")

    def to_dict(self) -> dict[str, str]:
        return {"dimension": self.dimension, "value": self.value}


@dataclass(frozen=True, slots=True)
class AggregatedForecastResult:
    """Pointwise sum of comparable member forecasts under an explicit grouping."""

    grouping: ForecastGrouping
    metric: str
    unit: str
    forecast_start: date
    points: tuple[ForecastPoint, ...]
    member_entity_ids: tuple[str, ...]
    capacity_claim_ids: tuple[str, ...]
    milestone_claim_ids: tuple[str, ...]
    parameters: ModelParameters
    reasoning: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.grouping, ForecastGrouping):
            raise ValueError("grouping must be an explicit ForecastGrouping")
        for field_name in ("metric", "unit"):
            _required(getattr(self, field_name), field_name)
        _date(self.forecast_start, "forecast_start")
        if self.forecast_start != _quarter_start(self.forecast_start):
            raise ValueError("forecast_start must be the first day of a calendar quarter")
        if not isinstance(self.points, tuple) or len(self.points) != HORIZON_QUARTERS:
            raise ValueError(f"aggregate must contain exactly {HORIZON_QUARTERS} points")
        if not all(isinstance(point, ForecastPoint) for point in self.points):
            raise ValueError("points must be ForecastPoint values")
        expected_starts = tuple(
            _add_quarters(self.forecast_start, index) for index in range(HORIZON_QUARTERS)
        )
        if tuple(point.period_start for point in self.points) != expected_starts:
            raise ValueError("aggregate points must be contiguous and aligned to forecast_start")
        for previous, current in zip(self.points, self.points[1:]):
            if (
                current.p10 < previous.p10
                or current.p50 < previous.p50
                or current.p90 < previous.p90
            ):
                raise ValueError("aggregate forecast points must be monotone over time")
        for field_name in ("member_entity_ids", "capacity_claim_ids"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{field_name} must be a nonempty tuple")
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{field_name} must be unique and sorted")
        if tuple(sorted(set(self.milestone_claim_ids))) != self.milestone_claim_ids:
            raise ValueError("milestone_claim_ids must be unique and sorted")
        if set(self.capacity_claim_ids) & set(self.milestone_claim_ids):
            raise ValueError("capacity and milestone claim IDs must be distinct")
        if not isinstance(self.parameters, ModelParameters):
            raise ValueError("parameters must be ModelParameters")
        if not isinstance(self.reasoning, tuple) or not self.reasoning:
            raise ValueError("reasoning must be a nonempty tuple")
        for step in self.reasoning:
            _required(step, "reasoning step")

    @property
    def input_claim_ids(self) -> tuple[str, ...]:
        return tuple(sorted((*self.capacity_claim_ids, *self.milestone_claim_ids)))

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregation_assumption": (
                "sum_corresponding_quantiles_without_diversification_or_dependence_model"
            ),
            "capacity_claim_ids": list(self.capacity_claim_ids),
            "forecast_start": self.forecast_start.isoformat(),
            "grouping": self.grouping.to_dict(),
            "horizon_quarters": HORIZON_QUARTERS,
            "input_claim_ids": list(self.input_claim_ids),
            "member_entity_ids": list(self.member_entity_ids),
            "milestone_claim_ids": list(self.milestone_claim_ids),
            "output": {
                "basis": OUTPUT_BASIS.value,
                "metric": self.metric,
                "points": [point.to_dict() for point in self.points],
                "quarter_value_semantics": "capacity available by quarter end",
                "unit": self.unit,
            },
            "parameter_fingerprint": self.parameters.fingerprint,
            "parameters": self.parameters.to_dict(),
            "reasoning": list(self.reasoning),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def _ramp_for_quantile(
    parameters: ModelParameters,
    target_quarter: date,
    start_date: date | None,
    quantile: str,
) -> float:
    if start_date is None:
        return 1.0
    offset = _quarter_offset(_quarter_start(start_date), target_quarter)
    if offset < 0:
        return 0.0
    return float(getattr(parameters.ramp_for(offset), quantile))


def forecast_capacity(
    capacity_input: CapacityInput,
    *,
    forecast_start: date,
    parameters: ModelParameters = DEFAULT_MODEL_PARAMETERS,
) -> ForecastResult:
    """Forecast the quarter containing ``forecast_start`` and the 19 quarters after it."""

    if not isinstance(capacity_input, CapacityInput):
        raise ValueError("capacity_input must be a CapacityInput")
    _date(forecast_start, "forecast_start")
    if not isinstance(parameters, ModelParameters):
        raise ValueError("parameters must be ModelParameters")

    horizon_start = _quarter_start(forecast_start)
    factor = parameters.factor_for(capacity_input.basis)
    milestone = capacity_input.production_start
    if milestone is not None:
        start_p10 = milestone.production_start_high
        start_p50 = milestone.production_start_base
        start_p90 = milestone.production_start_low
        start_reason = (
            "start-date uncertainty maps p10 to the latest, p50 to the base, and p90 to "
            "the earliest evidence-backed production-start date"
        )
    elif parameters.starts_at_horizon_without_milestone(capacity_input.basis):
        start_p10 = start_p50 = start_p90 = horizon_start
        if parameters.missing_milestone_policy is MissingMilestonePolicy.AUTO_BY_BASIS:
            start_reason = (
                "no milestone claim was supplied; the basis-aware default explicitly assumes "
                "announced through qualified capacity starts in the first forecast quarter"
            )
        else:
            start_reason = (
                "no milestone claim was supplied; the model explicitly assumes production starts "
                "in the first forecast quarter"
            )
    else:
        start_p10 = start_p50 = start_p90 = None
        if parameters.missing_milestone_policy is MissingMilestonePolicy.AUTO_BY_BASIS:
            start_reason = (
                "no milestone claim was supplied; the basis-aware default explicitly treats "
                "economically usable capacity as steady-state in the first forecast quarter"
            )
        else:
            start_reason = (
                "no milestone claim was supplied; the model explicitly assumes the input is at "
                "steady-state ramp fraction in the first forecast quarter"
            )

    points = []
    for index in range(HORIZON_QUARTERS):
        period_start = _add_quarters(horizon_start, index)
        ramp_p10 = _ramp_for_quantile(parameters, period_start, start_p10, "p10")
        ramp_p50 = _ramp_for_quantile(parameters, period_start, start_p50, "p50")
        ramp_p90 = _ramp_for_quantile(parameters, period_start, start_p90, "p90")
        values = (
            round(float(capacity_input.low) * factor.p10 * ramp_p10, parameters.round_digits),
            round(float(capacity_input.base) * factor.p50 * ramp_p50, parameters.round_digits),
            round(float(capacity_input.high) * factor.p90 * ramp_p90, parameters.round_digits),
        )
        points.append(
            ForecastPoint(
                quarter=_quarter_label(period_start),
                period_start=period_start,
                period_end=_add_quarters(period_start, 1),
                p10=values[0],
                p50=values[1],
                p90=values[2],
            )
        )

    reasoning = (
        "output is economically usable capacity available by each calendar quarter end",
        "p10/p50/p90 map respectively to the input low/base/high capacity values",
        "each output equals input capacity multiplied by a basis-realization assumption "
        "and a ramp assumption",
        start_reason,
        "basis-realization factors and ramp fractions are labelled assumptions, not observed facts",
    )
    return ForecastResult(
        capacity_input=capacity_input,
        forecast_start=horizon_start,
        points=tuple(points),
        parameters=parameters,
        reasoning=reasoning,
    )


def aggregate_forecasts(
    forecasts: Iterable[ForecastResult],
    *,
    grouping: ForecastGrouping,
) -> AggregatedForecastResult:
    """Sum comparable forecasts pointwise under an explicit group dimension and value."""

    if not isinstance(grouping, ForecastGrouping):
        raise ValueError("grouping must be an explicit ForecastGrouping")
    members = list(forecasts)
    if not members:
        raise ValueError("at least one forecast is required")
    if not all(isinstance(member, ForecastResult) for member in members):
        raise ValueError("all members must be ForecastResult values")
    members.sort(
        key=lambda member: (
            member.capacity_input.capacity_claim_id,
            member.capacity_input.entity_id,
        )
    )
    first = members[0]
    metric = first.capacity_input.metric
    unit = first.capacity_input.unit
    horizon = tuple(point.period_start for point in first.points)
    parameter_fingerprint = first.parameters.fingerprint
    capacity_claim_ids = [member.capacity_input.capacity_claim_id for member in members]
    if len(set(capacity_claim_ids)) != len(capacity_claim_ids):
        raise ValueError("duplicate capacity claim IDs would double count the aggregate")

    for member in members[1:]:
        if member.capacity_input.metric != metric:
            raise ValueError("cannot aggregate forecasts with different metrics")
        if member.capacity_input.unit != unit:
            raise ValueError("cannot aggregate forecasts with different units")
        if tuple(point.period_start for point in member.points) != horizon:
            raise ValueError("cannot aggregate forecasts with different quarter horizons")
        if member.parameters.fingerprint != parameter_fingerprint:
            raise ValueError("cannot aggregate forecasts with different model parameters")

    points = []
    for index, period_start in enumerate(horizon):
        p10 = round(
            fsum(member.points[index].p10 for member in members),
            first.parameters.round_digits,
        )
        p50 = round(
            fsum(member.points[index].p50 for member in members),
            first.parameters.round_digits,
        )
        p90 = round(
            fsum(member.points[index].p90 for member in members),
            first.parameters.round_digits,
        )
        points.append(
            ForecastPoint(
                quarter=_quarter_label(period_start),
                period_start=period_start,
                period_end=_add_quarters(period_start, 1),
                p10=p10,
                p50=p50,
                p90=p90,
            )
        )

    milestone_claim_ids = sorted(
        {
            member.capacity_input.production_start.milestone_claim_id
            for member in members
            if member.capacity_input.production_start is not None
        }
    )
    return AggregatedForecastResult(
        grouping=grouping,
        metric=metric,
        unit=unit,
        forecast_start=first.forecast_start,
        points=tuple(points),
        member_entity_ids=tuple(sorted({member.capacity_input.entity_id for member in members})),
        capacity_claim_ids=tuple(sorted(capacity_claim_ids)),
        milestone_claim_ids=tuple(milestone_claim_ids),
        parameters=first.parameters,
        reasoning=(
            "only forecasts with identical metric, unit, quarter horizon, and model parameters "
            "are combined",
            "the caller supplied an explicit grouping dimension and value",
            "corresponding quantiles are summed pointwise without a dependence or "
            "diversification model; this is an aggregation assumption, not a fitted joint "
            "distribution",
        ),
    )


__all__ = [
    "ASSUMPTION_STATUS",
    "AggregatedForecastResult",
    "BasisRealization",
    "CapacityInput",
    "DEFAULT_BASIS_REALIZATION_FACTORS",
    "DEFAULT_MODEL_PARAMETERS",
    "DEFAULT_RAMP_FRACTIONS",
    "ForecastGrouping",
    "ForecastPoint",
    "ForecastResult",
    "HORIZON_QUARTERS",
    "MissingMilestonePolicy",
    "ModelParameters",
    "OUTPUT_BASIS",
    "QuantileValues",
    "RampFraction",
    "RampMilestone",
    "aggregate_forecasts",
    "forecast_capacity",
]
