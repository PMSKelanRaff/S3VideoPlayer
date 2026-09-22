"""Tests for d6433_adapter.py -- InspectionObservation -> D6433CalculationInput."""
import unittest
from datetime import datetime

from inspection_observation import InspectionObservation
from d6433_types import NormalizedD6433Observation, UnsupportedObservation
from d6433_adapter import (
    D6433_IN_SCOPE_DISTRESS_IDS, compute_density, normalize_observation,
    build_calculation_input,
)


def _make(**overrides) -> InspectionObservation:
    defaults = dict(
        observation_id="obs-1", section_id="EK26RA569B", sample_unit_id="EK26RA569B-0",
        distress_id="raveling", severity="", quantity=5.0, quantity_unit="percent",
        evidence=("s3://bucket/frame1.jpg",), recorded_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return InspectionObservation(**defaults)


class TestComputeDensity(unittest.TestCase):
    def test_known_fixture(self):
        # 50 (units) of distress over a 1000 sq m sample unit -> 5% density.
        self.assertEqual(compute_density(quantity=50, sample_unit_area_sq_m=1000), 5.0)

    def test_zero_quantity_gives_zero_density(self):
        self.assertEqual(compute_density(quantity=0, sample_unit_area_sq_m=250), 0.0)

    def test_non_positive_area_rejected(self):
        with self.assertRaises(ValueError):
            compute_density(quantity=1, sample_unit_area_sq_m=0)
        with self.assertRaises(ValueError):
            compute_density(quantity=1, sample_unit_area_sq_m=-10)

    def test_negative_quantity_rejected(self):
        with self.assertRaises(ValueError):
            compute_density(quantity=-1, sample_unit_area_sq_m=100)

    def test_no_rounding_applied(self):
        self.assertAlmostEqual(compute_density(quantity=1, sample_unit_area_sq_m=3), 33.333333333333336)


class TestInScopeDistressSet(unittest.TestCase):
    def test_exactly_the_four_priority_distresses(self):
        self.assertEqual(D6433_IN_SCOPE_DISTRESS_IDS,
                          {"potholes", "alligator_cracking", "raveling", "bleeding"})


class TestNormalizeObservation(unittest.TestCase):
    def test_area_pct_in_scope_distress_normalizes(self):
        obs = _make(distress_id="raveling", quantity=25, quantity_unit="percent")
        result = normalize_observation(obs, sample_unit_area_sq_m=1000)
        self.assertIsInstance(result, NormalizedD6433Observation)
        self.assertEqual(result.density_pct, 2.5)
        self.assertEqual(result.evidence, obs.evidence)

    def test_out_of_scope_distress_is_unsupported(self):
        obs = _make(distress_id="depression", quantity=10, quantity_unit="percent")
        result = normalize_observation(obs, sample_unit_area_sq_m=1000)
        self.assertIsInstance(result, UnsupportedObservation)
        self.assertIn("not in the current D6433 scope", result.reason)

    def test_count_based_in_scope_distress_is_unsupported(self):
        # Potholes IS in D6433_IN_SCOPE_DISTRESS_IDS but its quantity_kind is "count",
        # not "area_pct" -- must not be silently forced through the density formula.
        obs = _make(distress_id="potholes", severity="Medium", quantity=3, quantity_unit="count")
        result = normalize_observation(obs, sample_unit_area_sq_m=1000)
        self.assertIsInstance(result, UnsupportedObservation)
        self.assertIn("quantity_kind", result.reason)

    def test_mixed_unit_in_scope_distress_is_unsupported(self):
        # Alligator Cracking is in scope but its catalog quantity_kind is None
        # (mixed sq.m/% buckets in the source data, per Phase 1) -- must not guess.
        obs = _make(distress_id="alligator_cracking", severity="Low", quantity=2, quantity_unit="anything")
        result = normalize_observation(obs, sample_unit_area_sq_m=1000)
        self.assertIsInstance(result, UnsupportedObservation)

    def test_malformed_observation_raises_not_returns_unsupported(self):
        obs = _make(distress_id="not_a_real_distress")
        with self.assertRaises(Exception):
            normalize_observation(obs, sample_unit_area_sq_m=1000)


class TestBuildCalculationInput(unittest.TestCase):
    def test_mixed_batch_splits_normalized_and_unsupported(self):
        observations = [
            _make(observation_id="o1", distress_id="raveling", quantity=10, quantity_unit="percent"),
            _make(observation_id="o2", distress_id="depression", quantity=10, quantity_unit="percent"),
        ]
        result = build_calculation_input(
            section_id="EK26RA569B", sample_unit_id="EK26RA569B-0",
            sample_unit_area_sq_m=1000, observations=observations,
        )
        self.assertEqual(len(result.normalized_observations), 1)
        self.assertEqual(result.normalized_observations[0].observation_id, "o1")
        self.assertEqual(len(result.unsupported_observations), 1)
        self.assertEqual(result.unsupported_observations[0].observation_id, "o2")
        self.assertEqual(result.sample_unit_area_sq_m, 1000)

    def test_mismatched_sample_unit_id_raises(self):
        observations = [_make(sample_unit_id="some-other-unit")]
        with self.assertRaises(ValueError):
            build_calculation_input(
                section_id="EK26RA569B", sample_unit_id="EK26RA569B-0",
                sample_unit_area_sq_m=1000, observations=observations,
            )

    def test_empty_observation_list_produces_empty_input(self):
        result = build_calculation_input(
            section_id="EK26RA569B", sample_unit_id="EK26RA569B-0",
            sample_unit_area_sq_m=1000, observations=[],
        )
        self.assertEqual(result.normalized_observations, [])
        self.assertEqual(result.unsupported_observations, [])


if __name__ == "__main__":
    unittest.main()
