#!/usr/bin/env python3
"""
pgfs diagnostic helper — connects to a PostgreSQL database and reports
the current state of pgfs storage locations and foreign tables.

Usage:
    python3 scripts/pgfs_diagnose.py [--dsn DSN]

Requirements:
    pip install psycopg2-binary

The DSN defaults to the PGFS_DSN environment variable, or
"host=localhost dbname=postgres user=postgres" if unset.
"""

import os
import sys
import argparse
import json

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


def get_connection(dsn: str):
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    except psycopg2.Error as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}", file=sys.stderr)
        sys.exit(1)


def check_extension(cur) -> bool:
    cur.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'pgfs';"
    )
    row = cur.fetchone()
    if row:
        print(f"[OK]  pgfs extension installed, version: {row[0]}")
        return True
    else:
        print("[FAIL] pgfs extension is NOT installed.")
        print("       Run: CREATE EXTENSION pgfs CASCADE;")
        return False


def list_storage_locations(cur):
    print("\n--- Storage Locations ---")
    try:
        cur.execute("SELECT name, url, msl_id, options, credentials FROM pgfs.list_storage_locations();")
        rows = cur.fetchall()
        if not rows:
            print("  (none)")
        for row in rows:
            name, url, msl_id, options, credentials = row
            print(f"  Name       : {name}")
            print(f"  URL        : {url}")
            print(f"  MSL ID     : {msl_id or '(none)'}")
            print(f"  Options    : {json.dumps(options, indent=4) if options else '(none)'}")
            print(f"  Credentials: {json.dumps(credentials, indent=4) if credentials else '(none)'} [keys only, values masked]")
            print()
    except psycopg2.Error as e:
        print(f"  ERROR listing storage locations: {e}")


def list_foreign_tables(cur):
    print("--- Foreign Tables ---")
    try:
        cur.execute("SELECT schema_name, server_name, ft_name FROM pgfs.v_foreign_tables ORDER BY schema_name, ft_name;")
        rows = cur.fetchall()
        if not rows:
            print("  (none)")
        for schema, server, ft_name in rows:
            print(f"  {schema}.{ft_name}  →  storage location: {server}")
    except psycopg2.Error as e:
        print(f"  ERROR listing foreign tables: {e}")


def check_default_storage_location(cur):
    print("\n--- Default Storage Location ---")
    try:
        cur.execute("SELECT default_storage_location FROM pgfs.get_default_storage_location();")
        row = cur.fetchone()
        val = row[0] if row else None
        print(f"  {val if val else '(none set)'}")
    except psycopg2.Error as e:
        print(f"  ERROR: {e}")


def check_gucs(cur):
    print("\n--- GUC Parameters ---")
    gucs = [
        "pgfs.allowed_local_fs_paths",
        "pgfs.egress_allowlist",
        "edb.egress_allowlist",
    ]
    for guc in gucs:
        try:
            cur.execute(f"SHOW \"{guc}\";")
            row = cur.fetchone()
            val = row[0] if row else "(unset)"
            print(f"  {guc} = {repr(val)}")
        except psycopg2.Error:
            print(f"  {guc} = (not defined)")


def main():
    parser = argparse.ArgumentParser(description="pgfs diagnostic helper")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PGFS_DSN", "host=localhost dbname=postgres user=postgres"),
        help="PostgreSQL DSN (default: PGFS_DSN env var or localhost/postgres)",
    )
    args = parser.parse_args()

    print(f"Connecting to: {args.dsn}\n")
    conn = get_connection(args.dsn)
    cur = conn.cursor()

    ok = check_extension(cur)
    if not ok:
        cur.close()
        conn.close()
        sys.exit(1)

    check_gucs(cur)
    list_storage_locations(cur)
    list_foreign_tables(cur)
    check_default_storage_location(cur)

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
