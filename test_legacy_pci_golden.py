"""
Real C# production differential tests -- compares legacy_pci_engine.py against
actual stored results from the live VPCI_Application.exe, via
legacy_pci_production_fixture.py (see that module's docstring for exact
provenance: a local, read-only extraction of
"Adman L3001 Rathangan Jan23 PCI.accdb", a production backup database; the
production file itself was never opened directly during extraction -- see the
project's production-validation deliverable report for the full safety chain).

This file does NOT connect to any database at test-collection or test-run time --
it only imports the already-extracted, committed fixture module, so these tests
run offline/reproducibly like any other test in this suite.

Every expected value here came directly from a real VPCIData row; none were
hand-derived, guessed, or back-computed from this engine.
"""
import unittest

from legacy_pci_adapter import build_cells_from_labels
from legacy_pci_engine import calculate_sample_unit_pci
from legacy_pci_production_fixture import PRODUCTION_GOLDEN_CASES
from legacy_pci_provider import StaticLegacyPCIProvider

_provider = StaticLegacyPCIProvider()


def _run_case(case):
    cells = build_cells_from_labels(case.labels)
    return calculate_sample_unit_pci(case.road, case.case_id, cells, _provider)


class TestExhaustiveProductionDataset(unittest.TestCase):
    """Every InputData row in the production database joined 1:1 with its VPCIData
    row -- the full rated, non-skipped population (1315 rows), not a cherry-picked
    sample. Checked as one data-driven test (with subTest per case, so a failure
    identifies exactly which case_id/field failed) rather than 1315 separate test
    methods."""

    def test_all_production_cases_match_exactly(self):
        self.assertEqual(len(PRODUCTION_GOLDEN_CASES), 1315)

        mismatches = []
        for case in PRODUCTION_GOLDEN_CASES:
            result = _run_case(case)
            with self.subTest(case_id=case.case_id, road=case.road):
                checks = {
                    "pci": (result.pci, case.expected_pci),
                    "tdv": (result.total_deduct_value, case.expected_tdv),
                    "q": (result.q, case.expected_q),
                    "structure_deduct_value": (
                        result.structure_deduct_value, case.expected_structure_deduct_value),
                    "structure_deduct_percentage": (
                        result.structure_deduct_percentage, case.expected_structure_deduct_percentage),
                    "surface_deduct_value": (
                        result.surface_deduct_value, case.expected_surface_deduct_value),
                    "surface_deduct_percentage": (
                        result.surface_deduct_percentage, case.expected_surface_deduct_percentage),
                    "other_deduct_value": (
                        result.other_deduct_value, case.expected_other_deduct_value),
                }
                for field_name, (actual, expected) in checks.items():
                    if isinstance(expected, float):
                        ok = abs(actual - expected) < 1e-6
                    else:
                        ok = (actual == expected)
                    if not ok:
                        mismatches.append((case.case_id, case.road, field_name, actual, expected))

        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)} field mismatch(es) against real C# production data "
            f"(case_id, road, field, python_value, c#_value): {mismatches[:20]}"
            + (" ... (truncated)" if len(mismatches) > 20 else ""),
        )


def _case_by_id(case_id):
    for case in PRODUCTION_GOLDEN_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(f"No production case with case_id={case_id!r}")


class TestNotableProductionCases(unittest.TestCase):
    """A few individually-named, illustrative real cases -- already covered by
    TestExhaustiveProductionDataset above, but called out here on their own because
    they exercise specific documented behaviors end to end against real data."""

    def test_case_1_q5_lookup_branch(self):
        # Road AM23RA113A0.85.67 @ 0.8km: q=5, TDV=106.8 lands in the lookup range;
        # real C# PCI=44. The first case hand-verified before building the full
        # fixture -- see the deliverable report.
        case = _case_by_id("1")
        result = _run_case(case)
        self.assertEqual(result.branch, "q==5/lookup")
        self.assertEqual(result.pci, 44)

    def test_case_41_q8_lookup_miss_floor_recovers_correct_pci(self):
        # q=8 (NOT clamped to 7): queries InterpolationCurve at (7, 175), which has
        # no row (the table only has rows through q=7 -- see legacy_pci_engine.py's
        # module docstring) -- final_deduct misses to 0.0, then the maxDeduct floor
        # brings it back up, and the result still matches the real C# PCI exactly.
        case = _case_by_id("41")
        result = _run_case(case)
        self.assertEqual(result.q, 8)
        self.assertEqual(result.branch, "q>=7/lookup")
        self.assertFalse(result.cdv_lookup.found)
        self.assertEqual(result.final_deduct_before_floor, 0.0)
        self.assertGreater(result.final_deduct, 0.0)  # the floor did its job
        self.assertEqual(result.pci, case.expected_pci)
        self.assertEqual(result.pci, 54)

    def test_case_551_q9_not_clamped(self):
        # q=9 -- even further past the table's q=7 ceiling than case 41, still
        # resolves to the correct real C# PCI via the same floor mechanism.
        case = _case_by_id("551")
        result = _run_case(case)
        self.assertEqual(result.q, 9)
        self.assertEqual(result.pci, case.expected_pci)
        self.assertEqual(result.pci, 67)


if __name__ == "__main__":
    unittest.main()
