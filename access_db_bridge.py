"""
64-bit-side API for reading/writing per-project Access (.accdb) databases.

The app itself runs as 64-bit Python, but only a 32-bit Access ODBC driver is
installed on these machines (confirmed: no 64-bit "Microsoft Access Driver
(*.mdb, *.accdb)" exists via Get-OdbcDriver, and the project has deliberately
chosen not to require installing the 64-bit Access Database Engine
redistributable on every deployment machine, since it's unclear how many
machines/users this will run on). So every Access read/write is delegated, one
call at a time, to tools/access_bridge_worker.py running under a 32-bit Python
interpreter -- see that module's docstring for the request/response protocol.

This module is the ONLY thing in the app that should know a 32-bit subprocess is
involved; callers just get plain Python values back (list of dict rows, or a
rowcount for writes).
"""
import json
import os
import shutil
import subprocess

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "access_bridge_worker.py")

# Explicit override for machines where neither the `py` launcher nor the
# fallback paths below locate a working 32-bit interpreter.
_ENV_OVERRIDE = "PCI_APP_PYTHON32"

_FALLBACK_CANDIDATES = [
    r"C:\Users\{user}\AppData\Local\Programs\Python\Python313-32\python.exe",
    r"C:\Program Files (x86)\Python313\python.exe",
    r"C:\Program Files (x86)\Python312\python.exe",
    r"C:\Program Files (x86)\Python311\python.exe",
]


class Access32BridgeError(Exception):
    """Raised when the 32-bit helper process can't be found/run, or returns a
    malformed response -- distinct from AccessQueryError, which is a normal SQL/
    connection failure the helper reported cleanly."""


class AccessQueryError(Exception):
    """Raised when the 32-bit helper ran but the SQL/connection itself failed
    (e.g. bad SQL, locked file, missing table) -- carries the helper's own error
    message."""


def _find_python32():
    override = os.environ.get(_ENV_OVERRIDE)
    if override and os.path.isfile(override):
        return override

    py_launcher = shutil.which("py")
    if py_launcher:
        for tag in ("-3-32", "-32"):
            try:
                result = subprocess.run(
                    [py_launcher, tag, "-c", "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return None, [py_launcher, tag]  # tell caller to use the launcher directly
            except (OSError, subprocess.SubprocessError):
                pass

    for template in _FALLBACK_CANDIDATES:
        candidate = template.format(user=os.environ.get("USERNAME", ""))
        if os.path.isfile(candidate):
            return candidate

    raise Access32BridgeError(
        "No 32-bit Python interpreter found to run tools/access_bridge_worker.py. "
        f"Install one (e.g. python.org's 32-bit Windows installer) and either let "
        f"the 'py' launcher find it, or set the {_ENV_OVERRIDE} environment "
        f"variable to its python.exe path."
    )


def _invoke(db_path, sql, params=None, many=False, readonly=False):
    found = _find_python32()
    if isinstance(found, tuple):
        _, launcher_cmd = found
        cmd = launcher_cmd + [_WORKER_SCRIPT]
    else:
        cmd = [found, _WORKER_SCRIPT]

    request = {
        "db_path": db_path,
        "sql": sql,
        "params": params or [],
        "many": many,
        "readonly": readonly,
    }

    try:
        result = subprocess.run(
            cmd, input=json.dumps(request), capture_output=True, text=True, timeout=60,
        )
    except subprocess.SubprocessError as exc:
        raise Access32BridgeError(f"Failed to launch 32-bit Access helper: {exc}") from exc

    if not result.stdout.strip():
        raise Access32BridgeError(
            f"32-bit Access helper produced no output (exit code {result.returncode}). "
            f"stderr: {result.stderr.strip()}"
        )

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Access32BridgeError(f"32-bit Access helper returned invalid JSON: {result.stdout!r}") from exc

    if not response.get("ok"):
        raise AccessQueryError(response.get("error", "Unknown error from Access helper"))

    return response


def query(db_path, sql, params=None, readonly=True):
    """Runs a SELECT and returns rows as a list of dicts (column name -> value)."""
    response = _invoke(db_path, sql, params=params, many=False, readonly=readonly)
    columns = response["columns"] or []
    return [dict(zip(columns, row)) for row in response["rows"] or []]


def execute(db_path, sql, params=None):
    """Runs a single INSERT/UPDATE/DELETE (or DDL) statement; returns the
    affected row count. Never pass readonly=True SQL through here."""
    response = _invoke(db_path, sql, params=params, many=False, readonly=False)
    return response["rowcount"]


def execute_many(db_path, sql, param_rows):
    """Runs the same INSERT/UPDATE statement once per row in param_rows (bulk
    write, e.g. populating 5mData) -- one subprocess call total, not one per
    row. Returns the affected row count (may be -1; some ODBC drivers don't
    report an accurate count for executemany)."""
    response = _invoke(db_path, sql, params=param_rows, many=True, readonly=False)
    return response["rowcount"]
