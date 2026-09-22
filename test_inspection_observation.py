"""Tests for inspection_observation.py -- the methodology-agnostic observation model."""
import unittest
from datetime import datetime

from inspection_observation import (
    InspectionObservation, validate_observation,
    UnknownDistressError, InvalidSeverityError, InvalidQuantityError,
    InvalidQuantityUnitError, MissingEvidenceError, InvalidSampleUnitAssociationError,
)


def _make(**overrides) -> InspectionObservation:
    defaults = dict(
        observation_id="obs-1", section_id="EK26RA569B", sample_unit_id="EK26RA569B-0",
        distress_id="raveling", severity="", quantity=5.0, quantity_unit="percent",
        evidence=("s3://bucket/frame1.jpg",), recorded_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return InspectionObservation(**defaults)


class TestValidObservation(unittest.TestCase):
    def test_valid_no_severity_observation_passes(self):
        validate_observation(_make())  # must not raise

    def test_valid_severity_observation_passes(self):
        obs = _make(distress_id="potholes", severity="Medium", quantity=3, quantity_unit="count")
        validate_observation(obs)  # must not raise

    def test_zero_quantity_is_valid(self):
        validate_observation(_make(quantity=0))


class TestInvalidDistress(unittest.TestCase):
    def test_unknown_distress_id_rejected(self):
        obs = _make(distress_id="not_a_real_distress")
        with self.assertRaises(UnknownDistressError):
            validate_observation(obs)


class TestInvalidSeverity(unittest.TestCase):
    def test_severity_not_offered_by_distress_rejected(self):
        # "raveling" has no severities -- only NO_SEVERITY ("") is valid.
        obs = _make(distress_id="raveling", severity="Medium")
        with self.assertRaises(InvalidSeverityError):
            validate_observation(obs)

    def test_severity_required_when_distress_has_severities(self):
        obs = _make(distress_id="potholes", severity="", quantity_unit="count")
        with self.assertRaises(InvalidSeverityError):
            validate_observation(obs)

    def test_unknown_severity_label_rejected(self):
        obs = _make(distress_id="potholes", severity="Extreme", quantity_unit="count")
        with self.assertRaises(InvalidSeverityError):
            validate_observation(obs)

    def test_rutting_has_no_low_severity(self):
        obs = _make(distress_id="rutting", severity="Low", quantity_unit=None)
        with self.assertRaises(InvalidSeverityError):
            validate_observation(obs)


class TestInvalidQuantity(unittest.TestCase):
    def test_negative_quantity_rejected(self):
        with self.assertRaises(InvalidQuantityError):
            validate_observation(_make(quantity=-1))

    def test_none_quantity_rejected(self):
        with self.assertRaises(InvalidQuantityError):
            validate_observation(_make(quantity=None))


class TestInvalidQuantityUnit(unittest.TestCase):
    def test_missing_unit_rejected(self):
        with self.assertRaises(InvalidQuantityUnitError):
            validate_observation(_make(quantity_unit=""))

    def test_unit_mismatched_with_catalog_rejected(self):
        # "raveling"'s catalog quantity_unit is "percent" (Phase 1) -- anything else
        # must be rejected when the catalog has a definite unit for that distress.
        with self.assertRaises(InvalidQuantityUnitError):
            validate_observation(_make(quantity_unit="sq_m"))

    def test_unit_accepted_when_catalog_unit_is_undetermined(self):
        # "patching"'s catalog quantity_unit is None (mixed units in the source data,
        # per Phase 1) -- any non-empty unit string must be accepted at this
        # structural-validation layer; the D6433 adapter is what may reject it later.
        obs = _make(distress_id="patching", severity="Low", quantity_unit="whatever")
        validate_observation(obs)  # must not raise


class TestMissingEvidence(unittest.TestCase):
    def test_empty_evidence_tuple_rejected(self):
        with self.assertRaises(MissingEvidenceError):
            validate_observation(_make(evidence=()))


class TestInvalidSampleUnitAssociation(unittest.TestCase):
    def test_missing_sample_unit_id_rejected(self):
        with self.assertRaises(InvalidSampleUnitAssociationError):
            validate_observation(_make(sample_unit_id=""))

    def test_missing_section_id_rejected(self):
        with self.assertRaises(InvalidSampleUnitAssociationError):
            validate_observation(_make(section_id=""))


if __name__ == "__main__":
    unittest.main()
