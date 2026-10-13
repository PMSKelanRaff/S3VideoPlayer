"""
32-bit worker process for Access (.accdb) database I/O.

Why this exists: the main application runs as 64-bit Python, but only a 32-bit
"Microsoft Access Driver (*.mdb, *.accdb)" ODBC driver is installed (confirmed via
Get-OdbcDriver -- no 64-bit Access driver is available on these machines, and the
project has chosen NOT to require installing the 64-bit Access Database Engine
redistributable everywhere the app runs, since it's unclear how many machines/users
this will be deployed to). A 64-bit process cannot load a 32-bit ODBC driver, so all
Access reads/writes are delegated to this script, which is always invoked under a
32-bit Python interpreter (see access_db_bridge.py, the 64-bit-side caller).

Protocol: reads one JSON request object from stdin, performs exactly one SQL
statement against one .accdb file, writes one JSON response object to stdout, exits.
One process per call -- simplest and safest for a desktop app's occasional,
low-frequency DB access; no long-lived state to manage or leak.

Request shape:
    {"db_path": str, "sql": str, "params": [...], "many": bool, "readonly": bool}
  - "params": for a single execution, a flat list of bind values (or [] / omitted
    for none). For "many": true, a list of such lists -- executed via
    cursor.executemany(sql, params), for bulk inserts (e.g. populating 5mData).
  - "readonly": when true, opens the connection with ReadOnly=1 as a second,
    redundant safeguard on top of whatever SQL the caller sends (does not by
    itself block a write statement -- the caller is responsible for only sending
    SELECT statements when readonly=True; Access/ODBC does not reliably enforce
    this itself for all statement types).

Response shape (success):
    {"ok": true, "columns": [...] | null, "rows": [[...], ...] | null, "rowcount": int | null}
  SELECT statements return columns+rows (rowcount null). Everything else commits
  and returns rowcount (columns/rows null).

Response shape (failure):
    {"ok": false, "error": "<message>"}
  Never raises/crashes without emitting this -- the 64-bit caller always gets a
  well-formed JSON response to parse, even on a connection or SQL error.
"""
import json
import sys

import pyodbc


def _handle(request):
    db_path = request["db_path"]
    sql = request["sql"]
    params = request.get("params") or []
    many = request.get("many", False)
    readonly = request.get("readonly", False)

    conn_str = f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={db_path};"
    if readonly:
        conn_str += "ReadOnly=1;"

    conn = pyodbc.connect(conn_str, autocommit=False)
    try:
        cursor = conn.cursor()
        if many:
            cursor.executemany(sql, params)
        else:
            cursor.execute(sql, params)

        if cursor.description is not None:
            columns = [col[0] for col in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
            conn.rollback()  # a SELECT never needs a commit
            return {"ok": True, "columns": columns, "rows": rows, "rowcount": None}

        rowcount = cursor.rowcount
        conn.commit()
        return {"ok": True, "columns": None, "rows": None, "rowcount": rowcount}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()


def main():
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
        response = _handle(request)
    except Exception as exc:
        response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(response))


if __name__ == "__main__":
    main()
