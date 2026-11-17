#!/usr/bin/env python3
"""
pgaa_smoke_test.py — Smoke-test pgaa and pgfs installation.

Connects to a PostgreSQL instance, creates a temporary schema and pgfs
storage location pointing at the public EDB TPC-H demo bucket, creates
a pgaa table backed by Delta Lake data, verifies the row count, and
cleans up after itself.

Usage:
    python3 scripts/pgaa_smoke_test.py [--dsn DSN]

Arguments:
    --dsn   libpq connection string (default: reads PGCONN env var,
            falls back to "host=localhost dbname=postgres")

Requirements:
    pip install psycopg2-binary

Exit codes:
    0  — all checks passed
    1  — connection or setup error
    2  — row count mismatch (pgaa returned unexpected data)
    3  — cleanup error (data was validated but cleanup failed — inspect manually)
"""

import argparse
import os
import sys
import uuid

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

DEMO_BUCKET_URL   = "s3://beacon-analytics-demo-data-us-east-1-prod"
DEMO_BUCKET_OPTS  = '{"aws_skip_signature": "true"}'
DEMO_TABLE_PATH   = "tpch_sf_1/customer"
EXPECTED_ROW_COUNT = 150_000


def run_smoke_test(dsn: str) -> None:
    run_id      = uuid.uuid4().hex[:12]
    schema_name = f"pgaa_smoke_{run_id}"
    loc_name    = f"pgaa-smoke-{run_id}-data"
    table_name  = f"{schema_name}.customer"

    print(f"[pgaa smoke test] run_id={run_id}")
    print(f"  schema          : {schema_name}")
    print(f"  storage location: {loc_name}")
    print(f"  table           : {table_name}")
    print()

    conn = None
    created_schema = False
    created_location = False
    created_table = False

    try:
        print("[1/6] Connecting to PostgreSQL…")
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()

        # --- Check extension ---
        print("[2/6] Verifying pgaa and pgfs extensions are installed…")
        cur.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('pgaa', 'pgfs') ORDER BY extname"
        )
        installed = {row[0] for row in cur.fetchall()}
        for ext in ("pgaa", "pgfs"):
            if ext not in installed:
                print(f"  ERROR: extension '{ext}' is not installed. Run: CREATE EXTENSION pgaa CASCADE;")
                sys.exit(1)
        print("  pgaa and pgfs are installed. ✓")

        # --- Create schema ---
        print("[3/6] Creating test schema and storage location…")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        created_schema = True

        cur.execute(
            "SELECT pgfs.create_storage_location(%s, %s, %s::json)",
            (loc_name, DEMO_BUCKET_URL, DEMO_BUCKET_OPTS),
        )
        created_location = True
        print(f"  Schema '{schema_name}' and storage location '{loc_name}' created. ✓")

        # --- Create pgaa table ---
        print("[4/6] Creating pgaa table backed by Delta Lake demo data…")
        cur.execute(
            f"""
            CREATE TABLE {table_name} () USING PGAA WITH (
                pgaa.storage_location = %s,
                pgaa.path             = %s
            )
            """,
            (loc_name, DEMO_TABLE_PATH),
        )
        created_table = True
        print(f"  Table '{table_name}' created. ✓")

        # --- Check EXPLAIN plan ---
        print("[5/6] Checking query plan (expecting Seafowl scan node)…")
        cur.execute(f"EXPLAIN SELECT COUNT(*) FROM {table_name}")
        plan_lines = [row[0] for row in cur.fetchall()]
        plan_text  = "\n".join(plan_lines)
        if "Seafowl" in plan_text:
            print("  Seafowl scan node found in EXPLAIN. ✓")
        else:
            print("  WARNING: No Seafowl node in EXPLAIN — pgaa may not be routing to the analytics engine.")
            print(f"  Full plan:\n{plan_text}")

        # --- Validate row count ---
        print("[6/6] Verifying row count…")
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        actual = cur.fetchone()[0]
        if actual == EXPECTED_ROW_COUNT:
            print(f"  Row count: {actual} ✓  (expected {EXPECTED_ROW_COUNT})")
        else:
            print(f"  ERROR: Row count mismatch — got {actual}, expected {EXPECTED_ROW_COUNT}")
            _cleanup(cur, schema_name, loc_name, table_name)
            sys.exit(2)

    except psycopg2.Error as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if conn:
            try:
                _cleanup(conn.cursor(), schema_name, loc_name, table_name,
                         has_table=created_table, has_schema=created_schema,
                         has_location=created_location)
            except Exception:
                pass
        sys.exit(1)
    finally:
        if conn:
            try:
                cur2 = conn.cursor()
                _cleanup(cur2, schema_name, loc_name, table_name,
                         has_table=created_table, has_schema=created_schema,
                         has_location=created_location)
            except psycopg2.Error as exc:
                print(f"\nWARNING: Cleanup failed — {exc}", file=sys.stderr)
                print("  Please clean up manually:", file=sys.stderr)
                print(f"    DROP TABLE IF EXISTS {table_name} CASCADE;", file=sys.stderr)
                print(f"    DROP SCHEMA IF EXISTS {schema_name} CASCADE;", file=sys.stderr)
                print(f"    SELECT pgfs.delete_storage_location('{loc_name}');", file=sys.stderr)
                sys.exit(3)
            finally:
                conn.close()

    print("\n✅  pgaa smoke test PASSED — extension is installed and querying analytics storage correctly.")


def _cleanup(cur, schema_name, loc_name, table_name,
             has_table=True, has_schema=True, has_location=True):
    print("\n[cleanup] Removing test objects…")
    if has_table:
        cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
    if has_schema:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
    if has_location:
        cur.execute(
            "SELECT pgfs.delete_storage_location(%s)",
            (loc_name,),
        )
    print("[cleanup] Done.")


def main():
    parser = argparse.ArgumentParser(description="pgaa / pgfs smoke test")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PGCONN", "host=localhost dbname=postgres"),
        help="libpq connection string (default: PGCONN env var or 'host=localhost dbname=postgres')",
    )
    args = parser.parse_args()
    run_smoke_test(args.dsn)


if __name__ == "__main__":
    main()
