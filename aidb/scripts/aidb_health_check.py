#!/usr/bin/env python3
"""
aidb_health_check.py — Diagnostic script for AIDB extension health.

Usage:
    python3 scripts/aidb_health_check.py \
        --host localhost --port 5432 --dbname mydb --user myuser

Requires: psycopg2  (pip install psycopg2-binary)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed or extension not installed
    2 — could not connect to database
"""

import argparse
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)


CHECKS = []

def check(name):
    """Decorator to register a check function."""
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


@check("Extension installed")
def check_extension(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';"
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return False, "aidb extension not installed"
        return True, f"version {row[0]}"


@check("aidb schema accessible")
def check_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = 'aidb';"
        )
        count = cur.fetchone()[0]
        if count == 0:
            return False, "aidb schema not found"
        return True, "schema exists"


@check("GUC: aidb.max_threads")
def check_max_threads(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW aidb.max_threads;")
            val = cur.fetchone()[0]
            return True, f"value = {val}"
    except Exception as e:
        return False, str(e)


@check("GUC: aidb.pipeline_error_warnings")
def check_pipeline_warnings(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW aidb.pipeline_error_warnings;")
            val = cur.fetchone()[0]
            return True, f"value = {val}"
    except Exception as e:
        return False, str(e)


@check("Registered models")
def check_models(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, provider FROM aidb.list_models();")
            rows = cur.fetchall()
            if not rows:
                return True, "no models registered (OK for fresh install)"
            names = ", ".join(f"{r[0]} ({r[1]})" for r in rows)
            return True, f"{len(rows)} model(s): {names}"
    except Exception as e:
        return False, str(e)


@check("Registered pipelines")
def check_pipelines(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, auto_processing FROM aidb.list_pipelines();")
            rows = cur.fetchall()
            if not rows:
                return True, "no pipelines registered (OK for fresh install)"
            names = ", ".join(f"{r[0]} [{r[1]}]" for r in rows)
            return True, f"{len(rows)} pipeline(s): {names}"
    except Exception as e:
        return False, str(e)


@check("Registered semantic KBs")
def check_semantic_kbs(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM aidb.list_semantic_kbs();")
            rows = cur.fetchall()
            return True, f"{len(rows)} KB(s) registered"
    except Exception as e:
        return False, str(e)


@check("Dummy model smoke test (encode_text)")
def check_smoke_test(conn):
    model_name = "_healthcheck_dummy_model_"
    try:
        with conn.cursor() as cur:
            # Create temp dummy model if not present
            cur.execute(
                "SELECT COUNT(*) FROM aidb.list_models() WHERE name = %s;",
                (model_name,)
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "SELECT aidb.create_model(%s, 'dummy');",
                    (model_name,)
                )
            # Test encode_text
            cur.execute(
                "SELECT aidb.encode_text('hello world', %s);",
                (model_name,)
            )
            result = cur.fetchone()
            if result is None:
                return False, "encode_text returned NULL"
            return True, "encode_text OK"
    except Exception as e:
        return False, str(e)
    finally:
        # Cleanup (best-effort)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT aidb.delete_model(%s);", (model_name,))
        except Exception:
            pass


@check("Dummy model smoke test (chunk_text)")
def check_chunk_text(conn):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT part_id, value FROM aidb.chunk_text("
                "  'This is a test sentence for chunking purposes.',"
                "  aidb.chunk_text_config(10)"
                ") LIMIT 5;"
            )
            rows = cur.fetchall()
            if not rows:
                return False, "chunk_text returned no rows"
            return True, f"chunk_text OK ({len(rows)} chunk(s))"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="AIDB health check")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", default="postgres")
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--sslmode", default="prefer")
    args = parser.parse_args()

    # Connect
    try:
        conn_kwargs = dict(
            host=args.host,
            port=args.port,
            dbname=args.dbname,
            sslmode=args.sslmode,
        )
        if args.user:
            conn_kwargs["user"] = args.user
        if args.password:
            conn_kwargs["password"] = args.password

        conn = psycopg2.connect(**conn_kwargs)
        conn.autocommit = True
        print(f"Connected to {args.host}:{args.port}/{args.dbname}")
        print("=" * 60)
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        sys.exit(2)

    # Run checks
    all_passed = True
    for name, fn in CHECKS:
        try:
            passed, detail = fn(conn)
        except Exception as e:
            passed, detail = False, f"unexpected error: {e}"

        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}: {detail}")
        if not passed:
            all_passed = False

    conn.close()
    print("=" * 60)
    if all_passed:
        print("All checks PASSED.")
        sys.exit(0)
    else:
        print("One or more checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
