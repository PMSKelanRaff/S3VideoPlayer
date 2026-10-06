"""
End-to-end golden-case tests using the REAL StaticLegacyPCIProvider (the verbatim
VPCI_Config.accdb extraction, see legacy_pci_data.py) -- not fakes.

IMPORTANT LIMITATION (see the project's PCI port deliverable report): this
environment has no .NET runtime and no way to drive the compiled C# VPCI_Application
GUI, so these are NOT a literal "ran the C# app, compared output" differential
test. Each case below is instead hand-verified arithmetic against (a) the real
extracted [Distresses] row values and (b) Form1.cs's documented branch literals --
deliberately choosing inputs that land in the q<=1 or a literal (non-lookup) q==2
sub-branch, so no InterpolationCurve curve-fit value needs to be independently
reproduced by hand to check these. True differential testing against a live C# run
remains a remaining blocker -- see the deliverable report.
"""
import unittest

from legacy_pci_adapter import build_cells
from legacy_pci_engine import calculate_sample_unit_pci
from legacy_pci_provider import StaticLegacyPCIProvider


class TestRealDataGoldenCases(unittest.TestCase):
    def setUp(self):
        self.provider = StaticLegacyPCIProvider()

    def test_everything_unobserved_gives_pci_100(self):
        cells = build_cells({})
        result = calculate_sample_unit_pci("RoadA", "SU0", cells, self.provider)
        self.assertEqual(result.total_deduct_value, 0.0)
        self.assertEqual(result.q, 0)
        self.assertEqual(result.pci, 100)

    def test_single_severe_pothole_q_le_1_branch(self):
        # PotholeHigh bucket 6 ("> 5") = deduct_value 90.0, quantity 7.0 -- real DB
        # row. Only one cell >5 -> q=1 (<=1 branch). TDV=90<=100 -> finalDeduct=90.
        # maxDeduct=90 -> floor is a no-op. PCI = 100 - 90 = 10.
        cells = build_cells({("potholes", "High"): 6})
        result = calculate_sample_unit_pci("RoadA", "SU1", cells, self.provider)
        self.assertEqual(result.total_deduct_value, 90.0)
        self.assertEqual(result.q, 1)
        self.assertEqual(result.max_deduct_value, 90.0)
        self.assertEqual(result.branch, "q<=1/TDV<=100")
        self.assertEqual(result.final_deduct, 90.0)
        self.assertEqual(result.pci, 10)

    def test_two_moderate_distresses_q2_literal_low_branch(self):
        # AlligatorMed bucket 1 ("< 1 sq.m") = 7.4, PatchMed bucket 1 ("1") = 6.6 --
        # both real DB deduct values, both >5 -> q=2. TDV=14.0, rounded=14, which is
        # < 15 -> literal finalDeduct=10 (no InterpolationCurve lookup involved).
        # maxDeduct=7.4 -> floor is a no-op (10 > 7.4). PCI = 100 - 10 = 90.
        cells = build_cells({
            ("alligator_cracking", "Medium"): 1,
            ("patching", "Medium"): 1,
        })
        result = calculate_sample_unit_pci("RoadA", "SU2", cells, self.provider)
        self.assertAlmostEqual(result.total_deduct_value, 14.0, places=9)
        self.assertEqual(result.total_deduct_value_rounded, 14)
        self.assertEqual(result.q, 2)
        self.assertEqual(result.max_deduct_value, 7.4)
        self.assertEqual(result.branch, "q==2/TDV<15")
        self.assertIsNone(result.cdv_lookup)
        self.assertEqual(result.final_deduct, 10.0)
        self.assertEqual(result.pci, 90)

    def test_cell_results_round_trip_the_real_descriptor_and_quantity(self):
        cells = build_cells({("potholes", "High"): 6})
        result = calculate_sample_unit_pci("RoadA", "SU3", cells, self.provider)
        pothole_high = next(c for c in result.cell_results if c.distress_type == "PotholeHigh")
        self.assertEqual(pothole_high.descriptor, "> 5")
        self.assertEqual(pothole_high.quantity, 7.0)
        self.assertTrue(pothole_high.found)


if __name__ == "__main__":
    unittest.main()
