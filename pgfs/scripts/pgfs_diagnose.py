#!/usr/bin/env python3
"""
pgfs_diagnose.py — Diagnostic helper for pgfs agent tasks.

Connects to a PostgreSQL database (using libpq environment variables or
explicit DSN) and prints a structured JSON report covering:
  - Extension presence and version
  - All storage locations (credentials masked)
  - All pgfs foreign tables
  - Current GUC values (pgfs.allowed_local_fs_paths, egress allowlists)
  - Default storage location

Usage:
  python3 scripts/pgfs_diagnose.py
  python3 scripts/pgfs_diagnose.py --dsn "host=localhost dbname=mydb user=postgres"
  python3 scripts/pgfs_diagnose.py --json-only    # suppress banner, pure JSON output

Environment: standard libpq vars (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, etc.)
are respected when --dsn is not provided.

Requirements: psycopg2 or psycopg (v3). Falls back gracefully if neither is available.
"""

import argparse
import json
import sys

def get_connection(dsn=None):
    """Try psycopg2 then psycopg3."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(dsn or "")
        conn.autocommit = True
        return conn, "psycopg2"
    except ImportError:
        pass
    try:
        import psycopg
        conn = psycopg.connect(dsn or "", autocommit=True)
        return conn, "psycopg3"
    except ImportError:
        pass
    print("ERROR: Neither psycopg2 nor psycopg (v3) is installed.", file=sys.stderr)
    print("Install with:  pip install psycopg2-binary  or  pip install psycopg", file=sys.stderr)
    sys.exit(1)


def fetchall_dict(cursor, sql, params=None):
    """Execute SQL and return list of dicts."""
    cursor.execute(sql, params or [])
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def scalar(cursor, sql, default=None):
    """Return first column of first row, or default."""
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
        return row[0] if row else default
    except Exception:
        return default


def run_diagnostics(conn, driver):
    report = {"driver": driver, "sections": {}}

    with conn.cursor() as cur:
        # 1. Extension check
        ext_sql = """
            SELECT name, default_version, installed_version, comment
            FROM pg_available_extensions
            WHERE name = 'pgfs'
        """
        exts = fetchall_dict(cur, ext_sql)
        report["sections"]["extension"] = exts[0] if exts else {"error": "pgfs extension not found in pg_available_extensions"}

        if not exts or not exts[0].get("installed_version"):
            report["sections"]["warning"] = "pgfs is not installed. Run: CREATE EXTENSION pgfs CASCADE;"
            return report

        # 2. pgfs version
        try:
            report["sections"]["pgfs_version"] = scalar(cur, "SELECT pgfs.pgfs_version()", "unavailable")
        except Exception as e:
            report["sections"]["pgfs_version"] = f"error: {e}"

        # 3. Storage locations
        try:
            sls = fetchall_dict(cur, "SELECT name, url, msl_id::text, options::text, credentials::text FROM pgfs.list_storage_locations()")
            for sl in sls:
                # Parse JSON fields for prettier output
                for field in ("options", "credentials"):
                    if sl.get(field):
                        try:
                            sl[field] = json.loads(sl[field])
                        except Exception:
                            pass
            report["sections"]["storage_locations"] = sls
        except Exception as e:
            report["sections"]["storage_locations"] = f"error: {e}"

        # 4. Foreign tables
        try:
            fts = fetchall_dict(cur, "SELECT schema_name, server_name, ft_name FROM pgfs.v_foreign_tables ORDER BY schema_name, ft_name")
            report["sections"]["foreign_tables"] = fts
        except Exception as e:
            report["sections"]["foreign_tables"] = f"error: {e}"

        # 5. Default storage location
        try:
            default_sl = scalar(cur, "SELECT default_storage_location FROM pgfs.get_default_storage_location()")
            report["sections"]["default_storage_location"] = default_sl
        except Exception as e:
            report["sections"]["default_storage_location"] = f"error: {e}"

        # 6. GUC values
        gucs = {}
        for guc in ("pgfs.allowed_local_fs_paths", "pgfs.egress_allowlist", "edb.egress_allowlist"):
            try:
                val = scalar(cur, f"SHOW {guc}")
                gucs[guc] = val
            except Exception as e:
                gucs[guc] = f"error: {e}"
        report["sections"]["gucs"] = gucs

        # 7. PostgreSQL version
        try:
            report["sections"]["pg_version"] = scalar(cur, "SELECT version()")
        except Exception:
            pass

    return report


def main():
    parser = argparse.ArgumentParser(description="pgfs diagnostic tool")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN string")
    parser.add_argument("--json-only", action="store_true", help="Output raw JSON only")
    args = parser.parse_args()

    conn, driver = get_connection(args.dsn)
    report = run_diagnostics(conn, driver)
    conn.close()

    if args.json_only:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("=" * 60)
        print("pgfs Diagnostic Report")
        print("=" * 60)
        print(json.dumps(report, indent=2, default=str))
        print("=" * 60)
        ext = report["sections"].get("extension", {})
        if isinstance(ext, dict) and ext.get("installed_version"):
            print(f"✓ pgfs {ext['installed_version']} is installed")
        else:
            print("✗ pgfs is NOT installed")
        sls = report["sections"].get("storage_locations", [])
        if isinstance(sls, list):
            print(f"✓ {len(sls)} storage location(s) registered")
        fts = report["sections"].get("foreign_tables", [])
        if isinstance(fts, list):
            print(f"✓ {len(fts)} foreign table(s) defined")


if __name__ == "__main__":
    main()
