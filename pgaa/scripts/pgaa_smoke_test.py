#!/usr/bin/env python3
"""
pgaa_smoke_test.py

Runs a quick smoke test against a live PostgreSQL instance to verify that
pgaa and pgfs are installed and can query the public TPC-H SF1 sample data
(Delta format, public S3 bucket — no credentials required).

Usage:
    python3 pgaa_smoke_test.py --dsn "postgresql://user:pass@host:5432/dbname"
    python3 pgaa_smoke_test.py --dsn "postgresql://localhost/mydb" [--schema test_smoke]

Requires: psycopg2 (pip install psycopg2-binary)
Expected outcome: 150,000 rows from the 'customer' table.
"""

import argparse
import sys
import uuid

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


DEMO_BUCKET = "s3://beacon-analytics-demo-data-us-east-1-prod"
EXPECTED_COUNT = 150_000


def run_smoke_test(dsn: str, schema: str) -> bool:
    run_id = uuid.uuid4().hex[:8]
    location_name = f"{schema}-{run_id}-smoke-data"

    print(f"Connecting to: {dsn.split('@')[-1] if '@' in dsn else dsn}")
    print(f"Schema:        {schema}")
    print(f"Location name: {location_name}")
    print()

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()

    def execute(sql, label=""):
        if label:
            print(f"  [{label}] ", end="", flush=True)
        cur.execute(sql)
        if label:
            print("OK")

    try:
        # 1. Check extension is installed
        cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('pgaa','pgfs') ORDER BY extname")
        installed = {row[0] for row in cur.fetchall()}
        if "pgaa" not in installed:
            print("ERROR: pgaa extension is not installed. Run: CREATE EXTENSION pgaa CASCADE;")
            return False
        if "pgfs" not in installed:
            print("ERROR: pgfs extension is not installed. Run: CREATE EXTENSION pgfs CASCADE;")
            return False
        print("  Extensions: pgaa and pgfs are installed ✓")

        # 2. Check executor engine
        cur.execute("SHOW pgaa.executor_engine")
        engine = cur.fetchone()[0]
        print(f"  Executor engine: {engine}")

        # 3. Create schema
        execute(f"CREATE SCHEMA IF NOT EXISTS {schema}", "create schema")

        # 4. Create storage location (public bucket, no auth)
        execute(
            f"""
            SELECT pgfs.create_storage_location(
                '{location_name}',
                '{DEMO_BUCKET}',
                '{{"aws_skip_signature": "true"}}'
            )
            """,
            "create storage location",
        )

        # 5. Create PGAA table
        execute(
            f"""
            CREATE TABLE {schema}.customer () USING PGAA WITH (
                pgaa.storage_location = '{location_name}',
                pgaa.path             = 'tpch_sf_1/customer'
            )
            """,
            "create PGAA table",
        )

        # 6. Count rows
        print("  [count rows] ", end="", flush=True)
        cur.execute(f"SELECT COUNT(*) FROM {schema}.customer")
        count = cur.fetchone()[0]
        if count == EXPECTED_COUNT:
            print(f"{count} ✓ (expected {EXPECTED_COUNT})")
        else:
            print(f"{count} ✗ (expected {EXPECTED_COUNT})")
            return False

        # 7. Check EXPLAIN for scan type
        print("  [explain scan type] ", end="", flush=True)
        cur.execute(f"EXPLAIN SELECT COUNT(*) FROM {schema}.customer")
        plan_lines = [row[0] for row in cur.fetchall()]
        plan_text = "\n".join(plan_lines)
        if "SeafowlDirectScan" in plan_text:
            print("SeafowlDirectScan ✓ (full query offloaded to Seafowl)")
        elif "SeafowlCompatScan" in plan_text:
            print("SeafowlCompatScan ✓ (CompatScan — normal on WarehousePG/WHPG)")
        else:
            print(f"WARNING: neither DirectScan nor CompatScan found in plan:\n{plan_text}")

        print()
        print("Smoke test PASSED ✓")
        return True

    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return False

    finally:
        # Cleanup — always attempt regardless of success/failure
        print()
        print("Cleaning up...")
        for stmt, label in [
            (f"DROP TABLE IF EXISTS {schema}.customer CASCADE", "drop table"),
            (f"DROP SCHEMA IF EXISTS {schema} CASCADE", "drop schema"),
            (f"SELECT pgfs.delete_storage_location('{location_name}')", "delete storage location"),
        ]:
            try:
                execute(stmt, label)
            except Exception as cleanup_exc:
                print(f"  WARNING: cleanup step '{label}' failed: {cleanup_exc}")

        cur.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="pgaa smoke test using public TPC-H SF1 sample data")
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN, e.g. postgresql://user:pass@host/db")
    parser.add_argument("--schema", default="pgaa_smoke", help="Schema name to use (default: pgaa_smoke)")
    args = parser.parse_args()

    success = run_smoke_test(args.dsn, args.schema)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
