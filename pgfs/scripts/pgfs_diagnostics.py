#!/usr/bin/env python3
"""
pgfs diagnostic helper.

Connects to a PostgreSQL database and reports:
  - Whether the pgfs extension is installed
  - All configured storage locations (credentials masked)
  - All pgfs foreign tables and their associated storage locations

Usage:
    python3 scripts/pgfs_diagnostics.py [--dsn DSN]

The DSN defaults to libpq environment variables (PGHOST, PGPORT, PGUSER, etc.).
Requires the `psycopg2` or `psycopg` package.
"""

import argparse
import sys


def get_connection(dsn: str | None):
    """Return a database connection, trying psycopg2 then psycopg."""
    try:
        import psycopg2
        return psycopg2.connect(dsn or "")
    except ImportError:
        pass
    try:
        import psycopg
        return psycopg.connect(dsn or "")
    except ImportError:
        pass
    print("ERROR: Neither psycopg2 nor psycopg is installed.", file=sys.stderr)
    sys.exit(1)


def check_extension(cur) -> bool:
    cur.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'pgfs'"
    )
    row = cur.fetchone()
    if row:
        print(f"[OK] pgfs extension installed, version: {row[0]}")
        return True
    else:
        print("[MISSING] pgfs extension is NOT installed.")
        print("          Run: CREATE EXTENSION pgfs CASCADE;")
        return False


def list_storage_locations(cur):
    print("\n--- Storage Locations (credentials masked) ---")
    try:
        cur.execute("SELECT name, url, options::text, credentials::text FROM pgfs.list_storage_locations()")
        rows = cur.fetchall()
        if not rows:
            print("  (none configured)")
            return
        for row in rows:
            name, url, options, credentials = row
            print(f"  name        : {name}")
            print(f"  url         : {url}")
            print(f"  options     : {options or '(none)'}")
            print(f"  credentials : {credentials or '(none)'}")
            print()
    except Exception as e:
        print(f"  ERROR reading storage locations: {e}")


def list_foreign_tables(cur):
    print("--- pgfs Foreign Tables ---")
    try:
        cur.execute(
            """
            SELECT schema_name, server_name, ft_name
            FROM pgfs.v_foreign_tables
            ORDER BY schema_name, ft_name
            """
        )
        rows = cur.fetchall()
        if not rows:
            print("  (none configured)")
            return
        for row in rows:
            schema_name, server_name, ft_name = row
            print(f"  {schema_name}.{ft_name}  ->  storage location: {server_name}")
    except Exception as e:
        print(f"  ERROR reading foreign tables: {e}")


def check_default_storage_location(cur):
    print("\n--- Default Storage Location ---")
    try:
        cur.execute("SELECT default_storage_location FROM pgfs.get_default_storage_location()")
        row = cur.fetchone()
        if row and row[0]:
            print(f"  {row[0]}")
        else:
            print("  (none set)")
    except Exception as e:
        print(f"  ERROR reading default storage location: {e}")


def main():
    parser = argparse.ArgumentParser(description="pgfs diagnostic helper")
    parser.add_argument("--dsn", default=None, help="libpq connection string")
    args = parser.parse_args()

    conn = get_connection(args.dsn)
    conn.autocommit = True
    cur = conn.cursor()

    print("=== pgfs Diagnostics ===\n")

    if not check_extension(cur):
        sys.exit(1)

    list_storage_locations(cur)
    list_foreign_tables(cur)
    check_default_storage_location(cur)

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
