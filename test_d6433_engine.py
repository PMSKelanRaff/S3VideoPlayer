"""
Tests for d6433_engine.py -- calculation mechanics only, using FakeDeductCurveProvider.
None of the numbers here are ASTM D6433 data; they exist purely to verify the
engine's routing/arithmetic/wiring, independently of the still-unresolved real
curve and correction-procedure data (see d6433_engine.py's module docstring).
"""
import unittest
from datetime import datetime

from d6433_types import (
    D6433CalculationInput, NormalizedD6433Observation, UnsupportedObservation,
    CDVIteration, CDVComputation,
)
from d6433_engine import (
    DeductCurveDataUnavailable, UnavailableDeductCurveProvider,
    compute_deduct_values, compute_tdv, compute_q, calculate_sample_unit_pci,
)
from fake_deduct_curve_provider import FakeDeductCurveProvider
from inspection_observation import InspectionObservation
from d6433_adapter import build_calculation_input


def _normalized(obs_id, distress_id, severity, density_pct):
    return NormalizedD6433Observation(
        observation_id=obs_id, distress_id=distress_id, severity=severity,
        density_pct=density_pct, evidence=("s3://bucket/frame.jpg",),
    )


class TestComputeTdvAndQ(unittest.TestCase):
    def test_tdv_is_the_sum(self):
        self.assertEqual(compute_tdv([10, 8, 3]), 21)
        self.assertEqual(compute_tdv([]), 0)

    def test_q_counts_values_greater_than_two(self):
        self.assertEqual(compute_q([10, 8, 3]), 3)
        self.assertEqual(compute_q([2, 2, 1]), 0)   # exactly 2 does not count
        self.assertEqual(compute_q([2.01, 1, 0]), 1)


class TestComputeDeductValuesRouting(unittest.TestCase):
    def test_provider_called_with_exact_distress_severity_density(self):
        provider = FakeDeductCurveProvider(deduct_values={
            ("raveling", "", 5.0): 12.0,
            ("potholes", "Medium", 1.5): 30.0,
        })
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[
                _normalized("o1", "raveling", "", 5.0),
                _normalized("o2", "potholes", "Medium", 1.5),
            ],
            unsupported_observations=[],
        )
        results = compute_deduct_values(calc_input, provider)
        self.assertEqual([r.deduct_value for r in results], [12.0, 30.0])
        self.assertEqual([r.observation_id for r in results], ["o1", "o2"])

    def test_unconfigured_combination_raises_immediately(self):
        provider = FakeDeductCurveProvider()  # nothing configured
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[_normalized("o1", "raveling", "", 5.0)],
            unsupported_observations=[],
        )
        with self.assertRaises(KeyError):
            compute_deduct_values(calc_input, provider)


class TestUnavailableDeductCurveProviderFailsClearly(unittest.TestCase):
    def test_deduct_value_raises(self):
        provider = UnavailableDeductCurveProvider()
        with self.assertRaises(DeductCurveDataUnavailable):
            provider.deduct_value("raveling", "", 5.0)

    def test_corrected_deduct_value_raises(self):
        provider = UnavailableDeductCurveProvider()
        with self.assertRaises(DeductCurveDataUnavailable):
            provider.compute_corrected_deduct_value([10.0])

    def test_is_verified_reference_data_is_false(self):
        self.assertFalse(UnavailableDeductCurveProvider.is_verified_reference_data)


class TestCalculateSampleUnitPciEndToEnd(unittest.TestCase):
    def test_full_fake_pipeline(self):
        provider = FakeDeductCurveProvider(
            deduct_values={
                ("raveling", "", 2.5): 12.0,
                ("bleeding", "", 1.0): 8.0,
            },
            cdv_computations={
                (12.0, 8.0): CDVComputation(
                    iterations=[
                        CDVIteration(deduct_values_used=(12.0, 8.0), total_deduct_value=20.0,
                                     q=2, corrected_deduct_value=18.0),
                    ],
                ),
            },
        )
        calc_input = D6433CalculationInput(
            section_id="EK26RA569B", sample_unit_id="EK26RA569B-0", sample_unit_area_sq_m=1000,
            normalized_observations=[
                _normalized("o1", "raveling", "", 2.5),
                _normalized("o2", "bleeding", "", 1.0),
            ],
            unsupported_observations=[
                UnsupportedObservation(observation_id="o3", distress_id="depression", reason="out of scope"),
            ],
        )

        result = calculate_sample_unit_pci(calc_input, provider)

        self.assertEqual(result.total_deduct_value, 20.0)
        self.assertEqual(result.max_corrected_deduct_value, 18.0)
        self.assertEqual(result.pci, 82.0)
        self.assertEqual(len(result.deduct_value_results), 2)
        self.assertEqual(len(result.cdv_iterations), 1)
        self.assertFalse(result.is_verified_reference_data)   # fake provider -> always False
        self.assertFalse(result.all_observations_scored)      # one unsupported observation present
        self.assertEqual(result.unsupported_observations[0].observation_id, "o3")

    def test_no_normalized_observations_gives_pci_100_without_calling_provider(self):
        provider = UnavailableDeductCurveProvider()  # would raise if ever called
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[], unsupported_observations=[],
        )
        result = calculate_sample_unit_pci(calc_input, provider)
        self.assertEqual(result.pci, 100.0)
        self.assertEqual(result.total_deduct_value, 0.0)
        self.assertEqual(result.cdv_iterations, [])
        self.assertTrue(result.all_observations_scored)

    def test_unavailable_provider_raises_when_observations_exist(self):
        provider = UnavailableDeductCurveProvider()
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[_normalized("o1", "raveling", "", 5.0)],
            unsupported_observations=[],
        )
        with self.assertRaises(DeductCurveDataUnavailable):
            calculate_sample_unit_pci(calc_input, provider)


class TestMaxCdvIsEngineDerivedNotProviderReported(unittest.TestCase):
    """CDVComputation carries no self-reported maximum (see d6433_types.py) --
    the engine must derive it from `iterations` itself. These prove that
    derivation is correct even when iterations aren't in any particular order."""

    def test_max_selected_from_non_monotonic_iterations(self):
        iterations = [
            CDVIteration(deduct_values_used=(10.0,), total_deduct_value=10.0, q=1,
                         corrected_deduct_value=9.0),
            CDVIteration(deduct_values_used=(10.0,), total_deduct_value=10.0, q=1,
                         corrected_deduct_value=15.0),   # the true max, not first or last
            CDVIteration(deduct_values_used=(10.0,), total_deduct_value=10.0, q=1,
                         corrected_deduct_value=11.0),
        ]
        provider = FakeDeductCurveProvider(
            deduct_values={("raveling", "", 5.0): 10.0},
            cdv_computations={(10.0,): CDVComputation(iterations=iterations)},
        )
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[_normalized("o1", "raveling", "", 5.0)],
            unsupported_observations=[],
        )
        result = calculate_sample_unit_pci(calc_input, provider)
        self.assertEqual(result.max_corrected_deduct_value, 15.0)
        self.assertEqual(result.pci, 85.0)
        # every iteration the provider produced is preserved for audit, not just the max
        self.assertEqual(len(result.cdv_iterations), 3)

    def test_provider_returning_no_iterations_for_nonempty_input_raises(self):
        provider = FakeDeductCurveProvider(
            deduct_values={("raveling", "", 5.0): 10.0},
            cdv_computations={(10.0,): CDVComputation(iterations=[])},
        )
        calc_input = D6433CalculationInput(
            section_id="S1", sample_unit_id="U1", sample_unit_area_sq_m=1000,
            normalized_observations=[_normalized("o1", "raveling", "", 5.0)],
            unsupported_observations=[],
        )
        with self.assertRaises(ValueError):
            calculate_sample_unit_pci(calc_input, provider)


class TestMultiFrameEvidenceDoesNotDuplicateObservation(unittest.TestCase):
    """One physical defect seen across several frames must produce exactly one
    scored observation, not one per frame -- see the Phase 3 audit's frame/evidence
    design and inspection_observation.py's module docstring."""

    def test_one_observation_with_three_frames_scores_once(self):
        obs = InspectionObservation(
            observation_id="obs-1", section_id="EK26RA569B", sample_unit_id="U1",
            distress_id="raveling", severity="", quantity=10.0, quantity_unit="percent",
            evidence=("s3://bucket/frame1.jpg", "s3://bucket/frame2.jpg", "s3://bucket/frame3.jpg"),
            recorded_at=datetime(2026, 1, 1),
        )
        calc_input = build_calculation_input(
            section_id="EK26RA569B", sample_unit_id="U1",
            sample_unit_area_sq_m=1000, observations=[obs],
        )
        # exactly one normalized observation, carrying all three frames as evidence --
        # NOT three separate observations, one per frame
        self.assertEqual(len(calc_input.normalized_observations), 1)
        self.assertEqual(len(calc_input.normalized_observations[0].evidence), 3)

        provider = FakeDeductCurveProvider(
            deduct_values={("raveling", "", 1.0): 7.0},
            cdv_computations={(7.0,): CDVComputation(iterations=[
                CDVIteration(deduct_values_used=(7.0,), total_deduct_value=7.0, q=0,
                             corrected_deduct_value=7.0),
            ])},
        )
        result = calculate_sample_unit_pci(calc_input, provider)
        # one deduct value contributed, not three -- the defect isn't triple-counted
        # merely because three frames document it
        self.assertEqual(len(result.deduct_value_results), 1)
        self.assertEqual(result.total_deduct_value, 7.0)


if __name__ == "__main__":
    unittest.main()
