"""
Tests for access_db_bridge.py -- the 64-bit-side API that delegates all Access
(.accdb) reads/writes to the 32-bit helper (tools/access_bridge_worker.py).

These tests exercise the REAL 32-bit subprocess + a real .accdb file (there's no
meaningful way to fake "a 32-bit ODBC driver exists" from the 64-bit side without
just re-testing pyodbc's own mocks, which would prove nothing about the actual
bridging). Per project rule, all test database files are created on the user's
Desktop, never in a production/network location, and are cleaned up afterwards.

Skips gracefully (rather than failing) if the Default_VPCI.accdb template isn't
reachable (e.g. the network share isn't mounted on whatever machine runs this),
or if no 32-bit Python interpreter can be found -- both are environment
preconditions, not bugs in this module.
"""
import os
import shutil
import unittest

import access_db_bridge as bridge

_TEMPLATE_PATH = r"P:\Remote PCI\VPCI Application\Resources\Default_VPCI.accdb"
_TEST_DB_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "pci_app_test_dbs")


def _template_available():
    return os.path.isfile(_TEMPLATE_PATH)


def _python32_available():
    try:
        bridge._find_python32()
        return True
    except bridge.Access32BridgeError:
        return False


@unittest.skipUnless(_template_available(), f"Default_VPCI.accdb template not reachable at {_TEMPLATE_PATH}")
@unittest.skipUnless(_python32_available(), "No 32-bit Python interpreter found for the Access bridge")
class TestAccessDbBridge(unittest.TestCase):
    def setUp(self):
        os.makedirs(_TEST_DB_DIR, exist_ok=True)
        self.db_path = os.path.join(_TEST_DB_DIR, f"bridge_test_{self._testMethodName}.accdb")
        shutil.copyfile(_TEMPLATE_PATH, self.db_path)
        os.chmod(self.db_path, 0o666)  # the template is typically locked read-only

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_query_against_empty_template_returns_no_rows(self):
        rows = bridge.query(self.db_path, "SELECT * FROM [5mData]")
        self.assertEqual(rows, [])

    def test_execute_insert_then_query_sees_it(self):
        rowcount = bridge.execute(
            self.db_path,
            "INSERT INTO [5mData] (Identifier, Chainage, Frame, X, Y, Z, IRIL, IRIR, IRIC) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ["TESTROAD", 1.0, 1, "53.1", "-7.1", "50.0", 1.0, 2.0, 3.0],
        )
        self.assertEqual(rowcount, 1)

        rows = bridge.query(self.db_path, "SELECT * FROM [5mData]")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Identifier"], "TESTROAD")
        self.assertEqual(rows[0]["Frame"], 1)
        self.assertEqual(rows[0]["X"], "53.1")

    def test_execute_many_bulk_inserts_all_rows(self):
        param_rows = [
            ["TESTROAD", 1.0 + 0.005 * i, i + 1, "53.0", "-7.0", "50.0", 1.0, 1.0, 1.0]
            for i in range(5)
        ]
        bridge.execute_many(
            self.db_path,
            "INSERT INTO [5mData] (Identifier, Chainage, Frame, X, Y, Z, IRIL, IRIR, IRIC) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            param_rows,
        )
        rows = bridge.query(self.db_path, "SELECT COUNT(*) AS n FROM [5mData]")
        self.assertEqual(rows[0]["n"], 5)

    def test_bad_sql_raises_access_query_error_not_a_crash(self):
        with self.assertRaises(bridge.AccessQueryError):
            bridge.query(self.db_path, "SELECT * FROM [NotARealTable]")

    def test_select_with_readonly_false_still_works(self):
        # readonly is advisory metadata for the connection string, not a SQL
        # statement filter -- a SELECT works regardless of the flag.
        rows = bridge.query(self.db_path, "SELECT * FROM [5mData]", readonly=False)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
