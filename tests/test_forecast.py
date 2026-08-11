from __future__ import annotations

import json
import unittest
from datetime import date, datetime

from semiconductor_atlas.forecast import (
    ASSUMPTION_STATUS,
    HORIZON_QUARTERS,
    AggregatedForecastResult,
    BasisRealization,
    CapacityInput,
    DEFAULT_BASIS_REALIZATION_FACTORS,
    DEFAULT_MODEL_PARAMETERS,
    DEFAULT_RAMP_FRACTIONS,
    ForecastGrouping,
    ForecastPoint,
    ForecastResult,
    MissingMilestonePolicy,
    ModelParameters,
    QuantileValues,
    RampFraction,
    RampMilestone,
    aggregate_forecasts,
    forecast_capacity,
)
from semiconductor_atlas.models import CapacityBasis


class ForecastTests(unittest.TestCase):
    def capacity(self, **overrides: object) -> CapacityInput:
        values: dict[str, object] = {
            "entity_id": "production-unit:1",
            "metric": "wafer_starts_per_month",
            "unit": "300mm wafers/month",
            "basis": CapacityBasis.ANNOUNCED,
            "low": 80.0,
            "base": 100.0,
            "high": 120.0,
            "capacity_claim_id": "claim:capacity:1",
        }
        values.update(overrides)
        return CapacityInput(**values)  # type: ignore[arg-type]

    def milestone(self, **overrides: object) -> RampMilestone:
        values: dict[str, object] = {
            "production_start_low": date(2026, 10, 15),
            "production_start_base": date(2027, 1, 1),
            "production_start_high": date(2027, 4, 30),
            "milestone_claim_id": "claim:milestone:1",
        }
        values.update(overrides)
        return RampMilestone(**values)  # type: ignore[arg-type]

    def forecast(
        self, capacity: CapacityInput | None = None, **overrides: object
    ) -> ForecastResult:
        arguments: dict[str, object] = {
            "capacity_input": capacity or self.capacity(),
            "forecast_start": date(2026, 1, 15),
        }
        arguments.update(overrides)
        return forecast_capacity(**arguments)  # type: ignore[arg-type]

    def test_default_parameters_are_explicit_monotone_assumptions(self) -> None:
        parameters = DEFAULT_MODEL_PARAMETERS

        self.assertEqual(ASSUMPTION_STATUS, parameters.to_dict()["assumption_status"])
        self.assertEqual(
            set(CapacityBasis),
            {item.basis for item in parameters.basis_realization_factors},
        )
        self.assertEqual(5, len(parameters.basis_realization_factors))
        self.assertIn("assumption:", parameters.basis_factor_label)
        self.assertIn("not a fact", parameters.basis_factor_label)
        self.assertIn("assumption:", parameters.ramp_curve_label)
        self.assertIn("not a fact", parameters.ramp_curve_label)
        self.assertEqual(
            MissingMilestonePolicy.AUTO_BY_BASIS,
            parameters.missing_milestone_policy,
        )
        self.assertIn("announced through qualified", parameters.missing_milestone_policy_label)
        self.assertIn("economically usable", parameters.missing_milestone_policy_label)

        prior = None
        for basis in CapacityBasis:
            factors = parameters.factor_for(basis)
            self.assertLessEqual(factors.p10, factors.p50)
            self.assertLessEqual(factors.p50, factors.p90)
            if prior is not None:
                self.assertGreaterEqual(factors.p10, prior.p10)
                self.assertGreaterEqual(factors.p50, prior.p50)
                self.assertGreaterEqual(factors.p90, prior.p90)
            prior = factors

        prior = None
        for expected_offset, item in enumerate(parameters.ramp_fractions):
            self.assertEqual(expected_offset, item.quarter_offset)
            if prior is not None:
                self.assertGreaterEqual(item.fractions.p10, prior.p10)
                self.assertGreaterEqual(item.fractions.p50, prior.p50)
                self.assertGreaterEqual(item.fractions.p90, prior.p90)
            prior = item.fractions

    def test_capacity_input_preserves_exact_basis_range_and_claim_ids(self) -> None:
        milestone = self.milestone()
        capacity = self.capacity(
            basis=CapacityBasis.TOOL_INSTALLED,
            production_start=milestone,
        )

        self.assertEqual(CapacityBasis.TOOL_INSTALLED, capacity.basis)
        self.assertEqual(("claim:capacity:1", "claim:milestone:1"), capacity.input_claim_ids)
        self.assertEqual("tool_installed", capacity.to_dict()["basis"])
        with self.assertRaisesRegex(ValueError, "five CapacityBasis"):
            self.capacity(basis="announced")
        with self.assertRaisesRegex(ValueError, "0 <= low <= base <= high"):
            self.capacity(low=101, base=100)
        with self.assertRaisesRegex(ValueError, "finite"):
            self.capacity(high=float("inf"))
        with self.assertRaisesRegex(ValueError, "capacity_claim_id"):
            self.capacity(capacity_claim_id=" ")
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            self.capacity(
                capacity_claim_id="claim:same",
                production_start=self.milestone(milestone_claim_id="claim:same"),
            )

    def test_milestone_requires_ordered_date_objects_and_claim_id(self) -> None:
        self.milestone()
        with self.assertRaisesRegex(ValueError, "low <= base <= high"):
            self.milestone(
                production_start_low=date(2027, 3, 1),
                production_start_base=date(2027, 2, 1),
            )
        with self.assertRaisesRegex(ValueError, "must be a date"):
            self.milestone(production_start_low="2026-10-15")
        with self.assertRaisesRegex(ValueError, "must be a date"):
            self.milestone(production_start_low=datetime(2026, 10, 15, 0, 0))
        with self.assertRaisesRegex(ValueError, "milestone_claim_id"):
            self.milestone(milestone_claim_id="")

    def test_forecast_normalizes_start_and_returns_exact_twenty_quarters(self) -> None:
        result = forecast_capacity(
            self.capacity(),
            forecast_start=date(2026, 7, 17),
        )

        self.assertEqual(date(2026, 7, 1), result.forecast_start)
        self.assertEqual(HORIZON_QUARTERS, len(result.points))
        self.assertEqual("2026-Q3", result.points[0].quarter)
        self.assertEqual(date(2026, 10, 1), result.points[0].period_end)
        self.assertEqual("2031-Q2", result.points[-1].quarter)
        self.assertTrue(all(point.period_end > point.period_start for point in result.points))
        with self.assertRaisesRegex(ValueError, "must be a date"):
            forecast_capacity(self.capacity(), forecast_start=datetime(2026, 7, 17, 12, 0))

    def test_no_quantile_leaks_into_a_quarter_before_its_start_bound(self) -> None:
        result = self.forecast(self.capacity(production_start=self.milestone()))
        by_quarter = {point.quarter: point for point in result.points}

        for quarter in ("2026-Q1", "2026-Q2", "2026-Q3"):
            self.assertEqual((0.0, 0.0, 0.0), self.values(by_quarter[quarter]))
        self.assertEqual(0.0, by_quarter["2026-Q4"].p10)
        self.assertEqual(0.0, by_quarter["2026-Q4"].p50)
        self.assertGreater(by_quarter["2026-Q4"].p90, 0)
        self.assertEqual(0.0, by_quarter["2027-Q1"].p10)
        self.assertGreater(by_quarter["2027-Q1"].p50, 0)
        self.assertGreater(by_quarter["2027-Q1"].p90, 0)
        self.assertGreater(by_quarter["2027-Q2"].p10, 0)
        self.assertIn("claim:milestone:1", result.input_claim_ids)

    def test_output_is_range_safe_and_monotone_for_all_five_bases(self) -> None:
        prior_final = None
        for basis in CapacityBasis:
            result = self.forecast(
                self.capacity(basis=basis, low=100, base=100, high=100)
            )
            factors = DEFAULT_MODEL_PARAMETERS.factor_for(basis)
            first_ramp = DEFAULT_MODEL_PARAMETERS.ramp_for(0)
            expected_first = (
                round(100 * factors.p10 * first_ramp.p10, 9),
                round(100 * factors.p50 * first_ramp.p50, 9),
                round(100 * factors.p90 * first_ramp.p90, 9),
            )
            if basis is CapacityBasis.ECONOMICALLY_USABLE:
                expected_first = (
                    round(100 * factors.p10, 9),
                    round(100 * factors.p50, 9),
                    round(100 * factors.p90, 9),
                )
            expected_final = (
                round(100 * factors.p10, 9),
                round(100 * factors.p50, 9),
                round(100 * factors.p90, 9),
            )
            self.assertEqual(expected_first, self.values(result.points[0]))
            self.assertEqual(expected_final, self.values(result.points[-1]))
            for point in result.points:
                self.assertLessEqual(0, point.p10)
                self.assertLessEqual(point.p10, point.p50)
                self.assertLessEqual(point.p50, point.p90)
            if prior_final is not None:
                self.assertGreaterEqual(result.points[-1].p10, prior_final.p10)
                self.assertGreaterEqual(result.points[-1].p50, prior_final.p50)
                self.assertGreaterEqual(result.points[-1].p90, prior_final.p90)
            prior_final = result.points[-1]

        economically_usable = self.forecast(
            self.capacity(
                basis=CapacityBasis.ECONOMICALLY_USABLE,
                low=100,
                base=100,
                high=100,
            )
        )
        self.assertEqual((100.0, 100.0, 100.0), self.values(economically_usable.points[0]))
        self.assertEqual((100.0, 100.0, 100.0), self.values(economically_usable.points[-1]))

    def test_basis_aware_missing_milestone_default_does_not_deramp_usable_capacity(self) -> None:
        usable = self.forecast(
            self.capacity(
                basis=CapacityBasis.ECONOMICALLY_USABLE,
                low=80,
                base=100,
                high=120,
            )
        )
        announced = self.forecast(
            self.capacity(
                basis=CapacityBasis.ANNOUNCED,
                capacity_claim_id="claim:capacity:announced",
            )
        )
        payload = usable.to_dict()

        self.assertTrue(
            all(self.values(point) == (80.0, 100.0, 120.0) for point in usable.points)
        )
        self.assertLess(announced.points[0].p50, announced.points[-1].p50)
        self.assertIn("basis-aware default", " ".join(usable.reasoning))
        self.assertEqual(
            "model_assumption_steady_state",
            payload["calculation"]["production_start_source"],  # type: ignore[index]
        )
        self.assertEqual(
            "assume_start_at_horizon_except_economically_usable_steady_state",
            payload["parameters"]["missing_milestone_policy"],  # type: ignore[index]
        )

    def test_steady_state_missing_milestone_policy_is_explicit_and_overridable(self) -> None:
        parameters = ModelParameters(
            missing_milestone_policy=MissingMilestonePolicy.STEADY_STATE
        )
        result = self.forecast(
            self.capacity(basis=CapacityBasis.ECONOMICALLY_USABLE),
            parameters=parameters,
        )

        self.assertEqual((80.0, 100.0, 120.0), self.values(result.points[0]))
        self.assertTrue(
            all(self.values(point) == self.values(result.points[0]) for point in result.points)
        )
        self.assertIn("steady-state", " ".join(result.reasoning))
        self.assertEqual(
            "assume_steady_state_at_horizon",
            result.to_dict()["parameters"]["missing_milestone_policy"],  # type: ignore[index]
        )

    def test_valid_overrides_are_canonicalized_for_deterministic_output(self) -> None:
        reversed_parameters = ModelParameters(
            basis_realization_factors=tuple(reversed(DEFAULT_BASIS_REALIZATION_FACTORS)),
            ramp_fractions=tuple(reversed(DEFAULT_RAMP_FRACTIONS)),
        )
        normal = self.forecast(parameters=DEFAULT_MODEL_PARAMETERS)
        reversed_result = self.forecast(parameters=reversed_parameters)

        self.assertEqual(DEFAULT_MODEL_PARAMETERS.to_json(), reversed_parameters.to_json())
        self.assertEqual(DEFAULT_MODEL_PARAMETERS.fingerprint, reversed_parameters.fingerprint)
        self.assertEqual(normal.to_json(), reversed_result.to_json())

    def test_parameter_validation_rejects_incomplete_or_nonmonotone_assumptions(self) -> None:
        with self.assertRaisesRegex(ValueError, "each of the five bases"):
            ModelParameters(
                basis_realization_factors=DEFAULT_BASIS_REALIZATION_FACTORS[:-1]
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            ModelParameters(
                basis_realization_factors=(
                    *DEFAULT_BASIS_REALIZATION_FACTORS[:-1],
                    DEFAULT_BASIS_REALIZATION_FACTORS[0],
                )
            )
        nonmonotone_factors = (
            BasisRealization(CapacityBasis.ANNOUNCED, QuantileValues(0.6, 0.7, 0.8)),
            BasisRealization(CapacityBasis.PHYSICAL_CONSTRUCTION, QuantileValues(0.5, 0.7, 0.9)),
            *DEFAULT_BASIS_REALIZATION_FACTORS[2:],
        )
        with self.assertRaisesRegex(ValueError, "monotone by capacity maturity"):
            ModelParameters(basis_realization_factors=nonmonotone_factors)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            ModelParameters(
                ramp_fractions=(
                    RampFraction(0, QuantileValues(0.1, 0.2, 0.3)),
                    RampFraction(2, QuantileValues(0.2, 0.3, 0.4)),
                )
            )
        with self.assertRaisesRegex(ValueError, "monotone over time"):
            ModelParameters(
                ramp_fractions=(
                    RampFraction(0, QuantileValues(0.1, 0.2, 0.3)),
                    RampFraction(1, QuantileValues(0.05, 0.3, 0.4)),
                )
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            QuantileValues(0.5, 0.9, 1.1)
        with self.assertRaisesRegex(ValueError, "assumptions, not facts"):
            ModelParameters(basis_factor_label="reported factors")

    def test_result_serialization_is_stable_complete_and_auditable(self) -> None:
        capacity = self.capacity(
            basis=CapacityBasis.PHYSICAL_CONSTRUCTION,
            production_start=self.milestone(),
        )
        first = self.forecast(capacity)
        second = self.forecast(capacity)
        payload = json.loads(first.to_json())

        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(
            ["claim:capacity:1", "claim:milestone:1"], payload["input_claim_ids"]
        )
        self.assertEqual("physical_construction", payload["input"]["basis"])
        self.assertEqual("economically_usable", payload["output"]["basis"])
        self.assertEqual(HORIZON_QUARTERS, len(payload["output"]["points"]))
        self.assertEqual(ASSUMPTION_STATUS, payload["parameters"]["assumption_status"])
        self.assertEqual(DEFAULT_MODEL_PARAMETERS.fingerprint, payload["parameter_fingerprint"])
        self.assertEqual(
            "input.low * basis_factor.p10 * ramp_fraction.p10",
            payload["calculation"]["formula"]["p10"],
        )
        self.assertEqual(
            "2027-04-30",
            payload["calculation"]["production_start_dates_used"]["p10"],
        )
        self.assertEqual(
            "2026-10-15",
            payload["calculation"]["production_start_dates_used"]["p90"],
        )
        self.assertEqual(
            "physical_construction",
            payload["calculation"]["basis_realization_factors_used"]["basis"],
        )
        self.assertNotIn("NaN", first.to_json())

    def test_result_structure_rejects_incomplete_or_misaligned_points(self) -> None:
        result = self.forecast()
        with self.assertRaisesRegex(ValueError, "exactly 20"):
            ForecastResult(
                capacity_input=result.capacity_input,
                forecast_start=result.forecast_start,
                points=result.points[:-1],
                parameters=result.parameters,
                reasoning=result.reasoning,
            )
        with self.assertRaisesRegex(ValueError, "quarter must match"):
            ForecastPoint(
                quarter="wrong",
                period_start=date(2026, 1, 1),
                period_end=date(2026, 4, 1),
                p10=1,
                p50=2,
                p90=3,
            )

    def test_aggregation_is_explicit_order_independent_and_preserves_lineage(self) -> None:
        first = self.forecast(
            self.capacity(
                entity_id="production-unit:b",
                capacity_claim_id="claim:capacity:b",
                production_start=self.milestone(milestone_claim_id="claim:milestone:b"),
            )
        )
        second = self.forecast(
            self.capacity(
                entity_id="production-unit:a",
                capacity_claim_id="claim:capacity:a",
                low=40,
                base=50,
                high=60,
            )
        )
        grouping = ForecastGrouping("country", "US")
        forward = aggregate_forecasts([first, second], grouping=grouping)
        reverse = aggregate_forecasts([second, first], grouping=grouping)

        self.assertIsInstance(forward, AggregatedForecastResult)
        self.assertEqual(forward.to_json(), reverse.to_json())
        self.assertEqual(("production-unit:a", "production-unit:b"), forward.member_entity_ids)
        self.assertEqual(("claim:capacity:a", "claim:capacity:b"), forward.capacity_claim_ids)
        self.assertEqual(("claim:milestone:b",), forward.milestone_claim_ids)
        self.assertEqual(
            "country",
            forward.to_dict()["grouping"]["dimension"],  # type: ignore[index]
        )
        for index, point in enumerate(forward.points):
            self.assertAlmostEqual(first.points[index].p10 + second.points[index].p10, point.p10)
            self.assertAlmostEqual(first.points[index].p50 + second.points[index].p50, point.p50)
            self.assertAlmostEqual(first.points[index].p90 + second.points[index].p90, point.p90)
        self.assertIn("without_diversification", forward.to_json())

    def test_aggregation_rejects_incomparable_or_duplicate_members(self) -> None:
        first = self.forecast()
        grouping = ForecastGrouping("country", "US")

        with self.assertRaisesRegex(ValueError, "at least one"):
            aggregate_forecasts([], grouping=grouping)
        with self.assertRaisesRegex(ValueError, "explicit ForecastGrouping"):
            aggregate_forecasts([first], grouping="US")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "grouping dimension"):
            ForecastGrouping("", "US")
        with self.assertRaisesRegex(ValueError, "different metrics"):
            aggregate_forecasts(
                [
                    first,
                    self.forecast(
                        self.capacity(
                            metric="good_die_per_month", capacity_claim_id="c2"
                        )
                    ),
                ],
                grouping=grouping,
            )
        with self.assertRaisesRegex(ValueError, "different units"):
            aggregate_forecasts(
                [
                    first,
                    self.forecast(
                        self.capacity(
                            unit="200mm wafers/month", capacity_claim_id="c2"
                        )
                    ),
                ],
                grouping=grouping,
            )
        with self.assertRaisesRegex(ValueError, "different quarter horizons"):
            aggregate_forecasts(
                [
                    first,
                    self.forecast(
                        self.capacity(capacity_claim_id="c2"),
                        forecast_start=date(2026, 4, 1),
                    ),
                ],
                grouping=grouping,
            )
        with self.assertRaisesRegex(ValueError, "different model parameters"):
            aggregate_forecasts(
                [
                    first,
                    self.forecast(
                        self.capacity(capacity_claim_id="c2"),
                        parameters=ModelParameters(round_digits=6),
                    ),
                ],
                grouping=grouping,
            )
        duplicate_claim = self.forecast(
            self.capacity(entity_id="production-unit:2", capacity_claim_id="claim:capacity:1")
        )
        with self.assertRaisesRegex(ValueError, "double count"):
            aggregate_forecasts([first, duplicate_claim], grouping=grouping)

    @staticmethod
    def values(point: ForecastPoint) -> tuple[float, float, float]:
        return point.p10, point.p50, point.p90


if __name__ == "__main__":
    unittest.main()
