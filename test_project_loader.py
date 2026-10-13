"""
Tests for project_loader.py -- loading a project .accdb's ProjectData into
pci_viewer.py's existing segment shape, and writing committed ratings back
into InputData/VPCIData.

All fixtures are built fresh under the user's Desktop (per project rule),
using project_setup.create_project_database() to produce a real project
.accdb the same way the actual setup utility does -- never against real
client data.
"""
import os
import shutil
import unittest

import access_db_bridge as bridge
import project_loader as pl
import project_setup as ps
from legacy_pci_adapter import build_cells_from_labels
from legacy_pci_engine import calculate_sample_unit_pci
from legacy_pci_provider import StaticLegacyPCIProvider

_TEMPLATE_PATH = r"P:\Remote PCI\VPCI Application\Resources\Default_VPCI.accdb"
_TEST_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "pci_app_test_dbs")


def _template_available():
    return os.path.isfile(_TEMPLATE_PATH)


def _python32_available():
    try:
        bridge._find_python32()
        return True
    except bridge.Access32BridgeError:
        return False


def _write_synthetic_rsp(path, start_km, end_km, step_km=0.001):
    lines = ['5003,"TEST    ","Synthetic","TESTROAD","RSP"']
    chainage = start_km
    lat, lng, alt = 53.0, -7.0, 50.0
    while chainage <= end_km + 1e-9:
        lines.append(
            f"5280,  {chainage:.6f},  {chainage:.6f},0,134225.41, {lat:.8f},  {lng:.8f}, {alt:.2f}, 4,33,   0,  0, 0.9"
        )
        chainage += step_km
        lat += 0.00001
        lng -= 0.00001
        alt += 0.01
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_start_ends_xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Filename", "Location", "Lane/Dir", "Start", "End", "Length", "Width", "Comments"])
    for row in rows:
        ws.append(row)
    wb.save(path)


@unittest.skipUnless(_template_available(), f"Default_VPCI.accdb template not reachable at {_TEMPLATE_PATH}")
@unittest.skipUnless(_python32_available(), "No 32-bit Python interpreter found for the Access bridge")
class TestLoadProjectSegments(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TEST_DIR, exist_ok=True)
        self.remote_pci_root = os.path.join(_TEST_DIR, "fake_remote_pci_loader")
        self.job_folder = os.path.join(self.remote_pci_root, "Some Backup Job Name")
        os.makedirs(self.job_folder, exist_ok=True)
        self.xlsx_path = os.path.join(_TEST_DIR, "loader_StartEnds.xlsx")
        self.output_db = os.path.join(_TEST_DIR, "loader_project.accdb")

        _write_start_ends_xlsx(self.xlsx_path, [
            ["TESTROADL", "Loc A", "SB", 0, 50, 50, "Full", None],
            ["TESTROADL", "Loc B", "SB", 60, 150, 90, "Full", None],
        ])
        _write_synthetic_rsp(os.path.join(self.job_folder, "TESTROADL.RSP"), 0.0, 0.15)

        ps.create_project_database(
            job_folder=self.job_folder, start_ends_xlsx=self.xlsx_path,
            output_db_path=self.output_db, file_description="Loader test project",
            s3_job_prefix="2026/RSP Project Level Imagery 2026/Fake Job 2026",
        )

    def tearDown(self):
        shutil.rmtree(self.remote_pci_root, ignore_errors=True)
        for path in (self.xlsx_path, self.output_db):
            if os.path.exists(path):
                os.remove(path)

    def test_returns_one_segment_per_projectdata_row_with_csv_shaped_keys(self):
        segments = pl.load_project_segments(self.output_db, remote_pci_root=self.remote_pci_root)
        self.assertEqual(len(segments), 2)
        for segment in segments:
            self.assertIn("RSPFile", segment)
            self.assertIn("ChainageFrom", segment)
            self.assertIn("ChainageTo", segment)
            self.assertIn("Image0", segment)
            self.assertEqual(segment["RSPFile"], os.path.join(self.job_folder, "TESTROADL.RSP"))
            self.assertTrue(os.path.isfile(segment["RSPFile"]))

        identifiers = {s["_identifier"] for s in segments}
        self.assertEqual(identifiers, {"TESTROADL0.00.05", "TESTROADL0.060.15"})

    def test_raises_project_load_error_when_rsp_folder_not_found(self):
        shutil.rmtree(self.job_folder)  # the RSP file this project needs no longer exists anywhere
        with self.assertRaises(pl.ProjectLoadError):
            pl.load_project_segments(self.output_db, remote_pci_root=self.remote_pci_root)

    def test_raises_project_load_error_for_db_with_no_projectdata_rows(self):
        empty_db = os.path.join(_TEST_DIR, "empty_project.accdb")
        shutil.copyfile(_TEMPLATE_PATH, empty_db)
        os.chmod(empty_db, 0o666)
        try:
            with self.assertRaises(pl.ProjectLoadError):
                pl.load_project_segments(empty_db, remote_pci_root=self.remote_pci_root)
        finally:
            os.remove(empty_db)


@unittest.skipUnless(_template_available(), f"Default_VPCI.accdb template not reachable at {_TEMPLATE_PATH}")
@unittest.skipUnless(_python32_available(), "No 32-bit Python interpreter found for the Access bridge")
class TestCommitRatedSampleUnit(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TEST_DIR, exist_ok=True)
        self.remote_pci_root = os.path.join(_TEST_DIR, "fake_remote_pci_commit")
        self.job_folder = os.path.join(self.remote_pci_root, "Commit Test Job")
        os.makedirs(self.job_folder, exist_ok=True)
        self.xlsx_path = os.path.join(_TEST_DIR, "commit_StartEnds.xlsx")
        self.output_db = os.path.join(_TEST_DIR, "commit_project.accdb")

        _write_start_ends_xlsx(self.xlsx_path, [["TESTROADC", "Loc", "SB", 0, 50, 50, "Full", None]])
        _write_synthetic_rsp(os.path.join(self.job_folder, "TESTROADC.RSP"), 0.0, 0.05)

        ps.create_project_database(
            job_folder=self.job_folder, start_ends_xlsx=self.xlsx_path,
            output_db_path=self.output_db, file_description="Commit test project",
            s3_job_prefix="2026/RSP Project Level Imagery 2026/Fake Job 2026",
        )
        self.provider = StaticLegacyPCIProvider()

    def tearDown(self):
        shutil.rmtree(self.remote_pci_root, ignore_errors=True)
        for path in (self.xlsx_path, self.output_db):
            if os.path.exists(path):
                os.remove(path)

    def _compute(self, selections):
        cells = build_cells_from_labels(selections)
        return calculate_sample_unit_pci("TestSection", "TestUnit", cells, self.provider)

    def test_writes_inputdata_and_vpcidata_and_marks_started(self):
        segments = pl.load_project_segments(self.output_db, remote_pci_root=self.remote_pci_root)
        project_data_id = segments[0]["_project_data_id"]
        identifier = segments[0]["_identifier"]

        selections = {"potholes_high": "> 5"}  # a real bucket label, not the "0" default
        result = self._compute(selections)

        pl.commit_rated_sample_unit(
            self.output_db, project_data_id, identifier,
            unit_chainage_from_km=0.0, unit_chainage_to_km=0.1,
            selections=selections, result=result,
        )

        input_rows = bridge.query(self.output_db, "SELECT * FROM [InputData]")
        self.assertEqual(len(input_rows), 1)
        self.assertEqual(input_rows[0]["Road"], identifier)
        self.assertEqual(input_rows[0]["PotholesHigh"], "> 5")
        self.assertEqual(input_rows[0]["PatchingLow"], "0")  # untouched cells default to "0"

        vpci_rows = bridge.query(self.output_db, "SELECT * FROM [VPCIData]")
        self.assertEqual(len(vpci_rows), 1)
        self.assertEqual(vpci_rows[0]["PCI"], float(result.pci))
        # The confirmed swap: the un-suffixed column holds DEDUCT VALUE, the
        # "...Quantity"-suffixed column holds QUANTITY -- the opposite of what
        # the names say, matching real production data exactly.
        pothole_high_cell = next(c for c in result.cell_results if c.distress_type == "PotholeHigh")
        self.assertEqual(vpci_rows[0]["PotholesHigh"], pothole_high_cell.deduct_value)
        self.assertEqual(vpci_rows[0]["PotholesHighQuantity"], pothole_high_cell.quantity)

        project_rows = bridge.query(self.output_db, "SELECT StartedRating FROM [ProjectData] WHERE ID = ?", [project_data_id])
        self.assertTrue(project_rows[0]["StartedRating"])

    def test_committing_twice_appends_two_rows_not_one(self):
        segments = pl.load_project_segments(self.output_db, remote_pci_root=self.remote_pci_root)
        project_data_id = segments[0]["_project_data_id"]
        identifier = segments[0]["_identifier"]

        for _ in range(2):
            result = self._compute({})
            pl.commit_rated_sample_unit(
                self.output_db, project_data_id, identifier,
                unit_chainage_from_km=0.0, unit_chainage_to_km=0.1,
                selections={}, result=result,
            )

        self.assertEqual(len(bridge.query(self.output_db, "SELECT * FROM [InputData]")), 2)
        self.assertEqual(len(bridge.query(self.output_db, "SELECT * FROM [VPCIData]")), 2)


if __name__ == "__main__":
    unittest.main()
