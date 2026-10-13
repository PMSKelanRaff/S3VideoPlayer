"""
Tests for project_setup.py -- the StartEnds.xlsx -> per-project .accdb utility.

Pure-logic pieces (parse_start_ends, build_image0_uri, _nearest_index) are
tested against small, self-contained fixtures built on the fly -- no network
paths, no real client data. The full create_project_database() integration
test needs the real Default_VPCI.accdb template (see test_access_db_bridge.py's
identical reasoning) and a synthetic (not real-client) RSP file, and skips
gracefully if the template or a 32-bit Python interpreter aren't reachable.

Per project rule, every test file this suite creates lives under the user's
Desktop and is cleaned up afterwards -- never a production/network location.
"""
import os
import shutil
import unittest

import openpyxl

import access_db_bridge as bridge
import project_setup as ps

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


def _write_start_ends_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Filename", "Location", "Lane/Dir", "Start", "End", "Length", "Width", "Comments"])
    for row in rows:
        ws.append(row)
    wb.save(path)


_SAMPLE_5280_TEMPLATE = (
    "5280,  {chainage_km:.6f},  {chainage_km:.6f},0,134225.41, {lat:.8f},  {lng:.8f}, {alt:.2f}, 4,33,   0,  0, 0.9"
)


def _write_synthetic_rsp(path, start_km, end_km, step_km=0.001):
    """A minimal RSP file with just enough header + 5280 records for
    parse_rsp_file() to extract Chainage/Lat/Lng/Alt -- deliberately denser
    than the 5m grid (step_km=1m here) to exercise the downsampling logic."""
    lines = ['5003,"TEST    ","Synthetic","TESTROAD","RSP"']
    chainage = start_km
    lat, lng, alt = 53.0, -7.0, 50.0
    while chainage <= end_km + 1e-9:
        lines.append(_SAMPLE_5280_TEMPLATE.format(chainage_km=chainage, lat=lat, lng=lng, alt=alt))
        chainage += step_km
        lat += 0.00001
        lng -= 0.00001
        alt += 0.01
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class TestParseStartEnds(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TEST_DIR, exist_ok=True)
        self.xlsx_path = os.path.join(_TEST_DIR, f"StartEnds_{self._testMethodName}.xlsx")

    def tearDown(self):
        if os.path.exists(self.xlsx_path):
            os.remove(self.xlsx_path)

    def test_metres_converted_to_km(self):
        _write_start_ends_xlsx(self.xlsx_path, [
            ["TESTROADA", "Some Road", "SB", 230, 440, 210, "Full", None],
        ])
        segments = ps.parse_start_ends(self.xlsx_path)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].road, "TESTROADA")
        self.assertAlmostEqual(segments[0].chainage_from_km, 0.23)
        self.assertAlmostEqual(segments[0].chainage_to_km, 0.44)
        self.assertEqual(segments[0].lane_dir, "SB")
        self.assertEqual(segments[0].width, "Full")

    def test_multiple_rows_same_road_both_kept(self):
        _write_start_ends_xlsx(self.xlsx_path, [
            ["TESTROADB", "Loc A", "SB", 230, 440, 210, "Full", None],
            ["TESTROADB", "Loc B", "SB", 470, 3790, 3320, "Full", None],
        ])
        segments = ps.parse_start_ends(self.xlsx_path)
        self.assertEqual(len(segments), 2)
        self.assertEqual([s.location for s in segments], ["Loc A", "Loc B"])

    def test_blank_trailing_row_ignored(self):
        _write_start_ends_xlsx(self.xlsx_path, [
            ["TESTROADC", "Loc", "EB", 100, 200, 100, "Half", None],
            [None, None, None, None, None, None, None, None],
        ])
        segments = ps.parse_start_ends(self.xlsx_path)
        self.assertEqual(len(segments), 1)


class TestSegmentIdentifier(unittest.TestCase):
    def test_matches_confirmed_real_convention(self):
        segment = ps.RoadSegment(
            road="AO24RA286A", chainage_from_km=1.56, chainage_to_km=5.62,
            location="", lane_dir="", width="",
        )
        self.assertEqual(ps.segment_identifier(segment), "AO24RA286A1.565.62")

    def test_two_segments_of_the_same_road_get_different_identifiers(self):
        xlsx = os.path.join(_TEST_DIR, "StartEnds_collision_check.xlsx")
        os.makedirs(_TEST_DIR, exist_ok=True)
        try:
            _write_start_ends_xlsx(xlsx, [
                ["SAMEROAD", "Loc A", "SB", 230, 440, 210, "Full", None],
                ["SAMEROAD", "Loc B", "SB", 470, 3790, 3320, "Full", None],
            ])
            segments = ps.parse_start_ends(xlsx)
            identifiers = [ps.segment_identifier(s) for s in segments]
            self.assertEqual(len(identifiers), len(set(identifiers)), "identifiers must not collide")
        finally:
            if os.path.exists(xlsx):
                os.remove(xlsx)


class TestLocateJobFolder(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(_TEST_DIR, "fake_remote_pci_root_locate")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root, exist_ok=True)
        self.xlsx_path = os.path.join(_TEST_DIR, "LocateTest_StartEnds.xlsx")
        _write_start_ends_xlsx(self.xlsx_path, [
            ["ROADA", "Loc", "SB", 0, 100, 100, "Full", None],
            ["ROADB", "Loc", "SB", 0, 100, 100, "Full", None],
        ])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        if os.path.exists(self.xlsx_path):
            os.remove(self.xlsx_path)

    def _touch(self, *path_parts):
        path = os.path.join(*path_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

    def test_finds_the_one_folder_with_all_matching_rsp_files(self):
        self._touch(self.root, "Unrelated Job", "ROADA.RSP")  # missing ROADB -- not a match
        self._touch(self.root, "The Real Job", "ROADA.RSP")
        self._touch(self.root, "The Real Job", "ROADB.RSP")

        found = ps.locate_job_folder(self.xlsx_path, remote_pci_root=self.root)
        self.assertEqual(found, os.path.join(self.root, "The Real Job"))

    def test_no_matching_folder_raises(self):
        self._touch(self.root, "Unrelated Job", "ROADA.RSP")
        with self.assertRaises(ps.JobFolderLocationError):
            ps.locate_job_folder(self.xlsx_path, remote_pci_root=self.root)

    def test_multiple_matching_folders_raises_ambiguous(self):
        self._touch(self.root, "Job One", "ROADA.RSP")
        self._touch(self.root, "Job One", "ROADB.RSP")
        self._touch(self.root, "Job Two (duplicate)", "ROADA.RSP")
        self._touch(self.root, "Job Two (duplicate)", "ROADB.RSP")

        with self.assertRaises(ps.JobFolderLocationError):
            ps.locate_job_folder(self.xlsx_path, remote_pci_root=self.root)

    def test_missing_root_raises(self):
        with self.assertRaises(ps.JobFolderLocationError):
            ps.locate_job_folder(self.xlsx_path, remote_pci_root=os.path.join(self.root, "nope"))


class TestDeriveS3JobPrefix(unittest.TestCase):
    def test_matches_confirmed_real_example(self):
        xlsx = (
            r"S:\RSP\RSP2026\Project Level 2026\CR26RB919- Coir vPCI Dromcolliher, "
            r"Co. Limerick Sept 2026\Analysis\CR25RB919 StartEnds.xlsx"
        )
        self.assertEqual(
            ps.derive_s3_job_prefix(xlsx),
            "2026/RSP Project Level Imagery 2026/"
            "CR26RB919- Coir vPCI Dromcolliher, Co. Limerick Sept 2026",
        )

    def test_different_year_handled(self):
        xlsx = r"S:\RSP\RSP2023\Project Level 2023\Some Job 2023\Analysis\StartEnds.xlsx"
        self.assertEqual(
            ps.derive_s3_job_prefix(xlsx),
            "2023/RSP Project Level Imagery 2023/Some Job 2023",
        )

    def test_unrecognized_shape_raises(self):
        with self.assertRaises(ps.S3PrefixDerivationError):
            ps.derive_s3_job_prefix(r"C:\Somewhere\Unexpected\StartEnds.xlsx")


class TestBuildImage0Uri(unittest.TestCase):
    def test_matches_confirmed_s3_pattern(self):
        uri = ps.build_image0_uri("2026/RSP Project Level Imagery 2026/Some Job- 2026", "CR26RB920A")
        self.assertEqual(
            uri,
            "s3://pms-survey-imagery/2026/RSP Project Level Imagery 2026/Some Job- 2026/"
            "CR26RB920A/CR26RB920A_ROW/CR26RB920A    0.000 1.JPG",
        )

    def test_strips_leading_and_trailing_slashes_from_prefix(self):
        uri = ps.build_image0_uri("/2026/Job/", "ROAD1")
        self.assertIn("pms-survey-imagery/2026/Job/ROAD1/ROAD1_ROW/", uri)


class TestNearestIndex(unittest.TestCase):
    def test_finds_closest_value(self):
        chainages = [0.0, 0.001, 0.002, 0.003, 0.010, 0.011]
        self.assertEqual(ps._nearest_index(chainages, 0.0025, 0), 2)
        self.assertEqual(ps._nearest_index(chainages, 0.0103, 0), 4)
        self.assertEqual(ps._nearest_index(chainages, 0.0108, 0), 5)

    def test_resumes_search_from_given_start_not_from_zero(self):
        chainages = [0.0, 0.005, 0.010, 0.015, 0.020]
        # Starting the search at index 3 should never consider index 0/1, even
        # though they're closer in absolute value -- this is what lets
        # _gps_rows_for_segment avoid rescanning from the start every grid step.
        self.assertEqual(ps._nearest_index(chainages, 0.0, 3), 3)


@unittest.skipUnless(_template_available(), f"Default_VPCI.accdb template not reachable at {_TEMPLATE_PATH}")
@unittest.skipUnless(_python32_available(), "No 32-bit Python interpreter found for the Access bridge")
class TestCreateProjectDatabaseIntegration(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TEST_DIR, exist_ok=True)
        self.job_folder = os.path.join(_TEST_DIR, "synthetic_job")
        os.makedirs(self.job_folder, exist_ok=True)
        self.xlsx_path = os.path.join(_TEST_DIR, "synthetic_StartEnds.xlsx")
        self.output_db = os.path.join(_TEST_DIR, "synthetic_project.accdb")

        _write_start_ends_xlsx(self.xlsx_path, [
            ["TESTROADX", "Synthetic Road", "SB", 0, 50, 50, "Full", None],
        ])
        _write_synthetic_rsp(os.path.join(self.job_folder, "TESTROADX.RSP"), 0.0, 0.05)

    def tearDown(self):
        shutil.rmtree(self.job_folder, ignore_errors=True)
        for path in (self.xlsx_path, self.output_db):
            if os.path.exists(path):
                os.remove(path)

    def test_creates_db_with_one_projectdata_row_and_5m_spaced_gps(self):
        segments = ps.create_project_database(
            job_folder=self.job_folder,
            start_ends_xlsx=self.xlsx_path,
            s3_job_prefix="2026/RSP Project Level Imagery 2026/Synthetic Job 2026",
            output_db_path=self.output_db,
            file_description="Synthetic test project",
        )
        self.assertEqual(len(segments), 1)
        self.assertTrue(os.path.isfile(self.output_db))

        project_rows = bridge.query(self.output_db, "SELECT * FROM [ProjectData]")
        self.assertEqual(len(project_rows), 1)
        self.assertEqual(project_rows[0]["Identifier"], "TESTROADX0.00.05")
        self.assertEqual(project_rows[0]["FileName"], "TESTROADX")
        self.assertFalse(project_rows[0]["StartedRating"])
        self.assertFalse(project_rows[0]["FinishedRating"])

        gps_rows = bridge.query(
            self.output_db, "SELECT * FROM [5mData] ORDER BY Chainage"
        )
        # 0m to 50m in 5m steps = 11 rows (0, 5, 10, ..., 50)
        self.assertEqual(len(gps_rows), 11)
        chainages = [row["Chainage"] for row in gps_rows]
        for a, b in zip(chainages, chainages[1:]):
            self.assertAlmostEqual(b - a, 0.005, places=6)
        self.assertIsInstance(gps_rows[0]["X"], str)

    def test_setup_project_from_request_end_to_end(self):
        # Mirrors the real shape: a StartEnds.xlsx under a RSP<year>\Project
        # Level <year>\<job>\Analysis path (for S3 prefix derivation), and a
        # SEPARATE, differently-named "remote_pci_root/<job folder>" holding
        # the matching RSP files (for content-based job-folder location) --
        # the two job folder names are deliberately different, matching the
        # real Coir Dromcolliher case where they don't share a naming convention.
        s_drive_shaped_dir = os.path.join(
            _TEST_DIR, "RSP2026", "Project Level 2026", "S Drive Job Name 2026", "Analysis"
        )
        os.makedirs(s_drive_shaped_dir, exist_ok=True)
        shaped_xlsx = os.path.join(s_drive_shaped_dir, "StartEnds.xlsx")
        _write_start_ends_xlsx(shaped_xlsx, [["TESTROADY", "Loc", "SB", 0, 50, 50, "Full", None]])

        remote_pci_root = os.path.join(_TEST_DIR, "fake_remote_pci_root_e2e")
        differently_named_job_folder = os.path.join(remote_pci_root, "Totally Different Backup Name")
        os.makedirs(differently_named_job_folder, exist_ok=True)
        _write_synthetic_rsp(os.path.join(differently_named_job_folder, "TESTROADY.RSP"), 0.0, 0.05)

        output_db = os.path.join(_TEST_DIR, "e2e_project.accdb")
        try:
            segments = ps.setup_project_from_request(
                start_ends_xlsx=shaped_xlsx,
                output_db_path=output_db,
                file_description="End to end test project",
                remote_pci_root=remote_pci_root,
            )
            self.assertEqual(len(segments), 1)

            rows = bridge.query(output_db, "SELECT Identifier, JPEGstem FROM [ProjectData]")
            self.assertEqual(rows[0]["Identifier"], "TESTROADY0.00.05")
            self.assertIn("2026/RSP Project Level Imagery 2026/S Drive Job Name 2026", rows[0]["JPEGstem"])

            gps_rows = bridge.query(output_db, "SELECT COUNT(*) AS n FROM [5mData]")
            self.assertEqual(gps_rows[0]["n"], 11)  # 0m to 50m in 5m steps
        finally:
            shutil.rmtree(os.path.join(_TEST_DIR, "RSP2026"), ignore_errors=True)
            shutil.rmtree(remote_pci_root, ignore_errors=True)
            if os.path.exists(output_db):
                os.remove(output_db)

    def test_s3_job_prefix_auto_derived_when_omitted(self):
        # Re-locate the StartEnds.xlsx under a path shaped like the real
        # RSP<year>\Project Level <year>\<job> convention, so omitting
        # s3_job_prefix exercises derive_s3_job_prefix() for real.
        shaped_dir = os.path.join(_TEST_DIR, "RSP2026", "Project Level 2026", "Synthetic Job 2026", "Analysis")
        os.makedirs(shaped_dir, exist_ok=True)
        shaped_xlsx = os.path.join(shaped_dir, "StartEnds.xlsx")
        _write_start_ends_xlsx(shaped_xlsx, [["TESTROADX", "Loc", "SB", 0, 50, 50, "Full", None]])
        try:
            ps.create_project_database(
                job_folder=self.job_folder, start_ends_xlsx=shaped_xlsx,
                output_db_path=self.output_db, file_description="x",
            )
            rows = bridge.query(self.output_db, "SELECT JPEGstem FROM [ProjectData]")
            self.assertIn(
                "2026/RSP Project Level Imagery 2026/Synthetic Job 2026",
                rows[0]["JPEGstem"],
            )
        finally:
            shutil.rmtree(os.path.join(_TEST_DIR, "RSP2026"), ignore_errors=True)

    def test_refuses_to_overwrite_existing_output(self):
        ps.create_project_database(
            job_folder=self.job_folder, start_ends_xlsx=self.xlsx_path,
            s3_job_prefix="x", output_db_path=self.output_db, file_description="x",
        )
        with self.assertRaises(FileExistsError):
            ps.create_project_database(
                job_folder=self.job_folder, start_ends_xlsx=self.xlsx_path,
                s3_job_prefix="x", output_db_path=self.output_db, file_description="x",
            )


if __name__ == "__main__":
    unittest.main()
