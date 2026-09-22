"""
Tests for fake_deduct_curve_provider.py itself -- proving the fake stays obviously
and structurally test-only, and that it never silently falls back to a made-up
value for an unconfigured lookup.
"""
import unittest

from d6433_types import CDVComputation, CDVIteration
from fake_deduct_curve_provider import FakeDeductCurveProvider


class TestFakeProviderIsClearlyMarkedTestOnly(unittest.TestCase):
    def test_is_verified_reference_data_is_always_false(self):
        self.assertFalse(FakeDeductCurveProvider.is_verified_reference_data)
        self.assertFalse(FakeDeductCurveProvider().is_verified_reference_data)

    def test_defaults_to_empty_with_no_configured_values(self):
        provider = FakeDeductCurveProvider()
        with self.assertRaises(KeyError):
            provider.deduct_value("raveling", "", 5.0)
        with self.assertRaises(KeyError):
            provider.compute_corrected_deduct_value([1.0, 2.0])


class TestFakeProviderExactMatchOnly(unittest.TestCase):
    def test_deduct_value_requires_exact_key(self):
        provider = FakeDeductCurveProvider(deduct_values={("raveling", "", 5.0): 12.0})
        self.assertEqual(provider.deduct_value("raveling", "", 5.0), 12.0)
        # A different density for the same distress/severity is NOT configured --
        # must not fall back to the nearest configured value or a default.
        with self.assertRaises(KeyError):
            provider.deduct_value("raveling", "", 5.1)

    def test_corrected_deduct_value_requires_exact_key(self):
        iteration = CDVIteration(deduct_values_used=(12.0,), total_deduct_value=12.0,
                                  q=1, corrected_deduct_value=12.0)
        provider = FakeDeductCurveProvider(cdv_computations={
            (12.0,): CDVComputation(iterations=[iteration]),
        })
        result = provider.compute_corrected_deduct_value([12.0])
        self.assertEqual(result.iterations, [iteration])
        with self.assertRaises(KeyError):
            provider.compute_corrected_deduct_value([12.0, 0.0])


if __name__ == "__main__":
    unittest.main()
