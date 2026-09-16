#!/usr/bin/env python3
"""
aidb_diagnose.py — AIDB environment diagnostic script.

Run this against a PostgreSQL database with AIDB installed to check
extension health, list models, pipelines, and semantic KBs.

Usage:
    python3 scripts/aidb_diagnose.py [--dsn "postgresql://user:pass@host/db"]

Requirements:
    pip install psycopg2-binary
    (or psycopg2 from system packages)

Exit codes:
    0 — All checks passed
    1 — One or more checks failed
    2 — Could not connect or AIDB not installed
"""

import sys
import argparse
import json

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(2)


def connect(dsn: str):
    """Return a psycopg2 connection or exit with error."""
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}")
        sys.exit(2)


def check_extension(conn) -> bool:
    """Verify AIDB is installed and get its version."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';"
        )
        row = cur.fetchone()
        if not row:
            print("FAIL: AIDB extension is not installed in this database.")
            print("      Run: CREATE EXTENSION aidb CASCADE;")
            return False
        print(f"OK:   AIDB extension installed, version {row[1]}")
        return True


def check_schema(conn) -> bool:
    """Verify the aidb schema and key tables exist."""
    ok = True
    expected_tables = [
        ("aidb", "aidb_pipeline_registry"),
        ("aidb_internal", "mcp_tool_cache"),
    ]
    with conn.cursor() as cur:
        for schema, table in expected_tables:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s;
                """,
                (schema, table),
            )
            if cur.fetchone():
                print(f"OK:   Table {schema}.{table} exists")
            else:
                print(f"WARN: Table {schema}.{table} not found (may be expected on older versions)")
    return ok


def list_models(conn):
    """List all registered models."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT * FROM aidb.list_models();")
            rows = cur.fetchall()
            if not rows:
                print("INFO: No models registered.")
            else:
                print(f"\nModels ({len(rows)} registered):")
                for row in rows:
                    name = row.get("name", "?")
                    provider = row.get("provider", "?")
                    print(f"  - {name} [{provider}]")
        except Exception as e:
            print(f"WARN: Could not list models: {e}")


def list_pipelines(conn):
    """List all pipelines and their status."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT * FROM aidb.list_pipelines();")
            rows = cur.fetchall()
            if not rows:
                print("INFO: No pipelines defined.")
            else:
                print(f"\nPipelines ({len(rows)} defined):")
                for row in rows:
                    name = row.get("name", "?")
                    mode = row.get("auto_processing", "?")
                    src = row.get("source", "?")
                    print(f"  - {name} [mode={mode}, source={src}]")
        except Exception as e:
            print(f"WARN: Could not list pipelines: {e}")


def list_semantic_kbs(conn):
    """List all semantic knowledge bases."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT * FROM aidb.list_semantic_kbs();")
            rows = cur.fetchall()
            if not rows:
                print("INFO: No semantic knowledge bases defined.")
            else:
                print(f"\nSemantic KBs ({len(rows)} defined):")
                for row in rows:
                    name = row.get("name", "?")
                    model = row.get("model", "?")
                    mode = row.get("auto_processing", "?")
                    print(f"  - {name} [model={model}, mode={mode}]")
        except Exception as e:
            print(f"WARN: Could not list semantic KBs: {e}")


def list_agents(conn):
    """List all registered agents."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute("SELECT name, model, role FROM aidb.agents ORDER BY name;")
            rows = cur.fetchall()
            if not rows:
                print("INFO: No agents defined.")
            else:
                print(f"\nAgents ({len(rows)} defined):")
                for row in rows:
                    name = row.get("name", "?")
                    model = row.get("model", "?")
                    role = row.get("role") or "(default)"
                    print(f"  - {name} [model={model}, role={role}]")
        except Exception as e:
            print(f"WARN: Could not list agents (may not exist in this version): {e}")


def check_gucs(conn):
    """Check AIDB GUC parameters."""
    gucs = [
        "aidb.max_threads",
        "aidb.pipeline_error_warnings",
    ]
    print("\nGUC Parameters:")
    with conn.cursor() as cur:
        for guc in gucs:
            try:
                cur.execute(f"SHOW {guc};")
                val = cur.fetchone()[0]
                print(f"  {guc} = {val}")
            except Exception:
                print(f"  {guc} = (not set or not available)")


def run_smoke_test(conn) -> bool:
    """Run a quick smoke test using the dummy model."""
    print("\nSmoke test (dummy model):")
    with conn.cursor() as cur:
        # Check if dummy model is registered; if not, create a temp one
        try:
            cur.execute("SELECT name FROM aidb.list_models() WHERE name = '__diag_dummy__';")
            already = cur.fetchone()
            if not already:
                cur.execute(
                    "SELECT aidb.create_model('__diag_dummy__', 'dummy', validate => false);"
                )

            cur.execute("SELECT aidb.encode_text('hello world', '__diag_dummy__') IS NOT NULL AS ok;")
            row = cur.fetchone()
            if row and row[0]:
                print("  OK: encode_text() with dummy model works")
            else:
                print("  FAIL: encode_text() returned unexpected result")
                return False
        except Exception as e:
            print(f"  FAIL: Smoke test error: {e}")
            return False
        finally:
            # Clean up temp model
            try:
                cur.execute(
                    """
                    DO $$ BEGIN
                        PERFORM aidb.delete_model('__diag_dummy__');
                    EXCEPTION WHEN OTHERS THEN NULL;
                    END $$;
                    """
                )
            except Exception:
                pass
    return True


def main():
    parser = argparse.ArgumentParser(description="AIDB diagnostic script")
    parser.add_argument(
        "--dsn",
        default="postgresql://localhost/aidb",
        help="PostgreSQL DSN (default: postgresql://localhost/aidb)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("AIDB Diagnostic Report")
    print("=" * 60)
    print(f"DSN: {args.dsn}\n")

    conn = connect(args.dsn)

    all_ok = True

    if not check_extension(conn):
        sys.exit(2)

    check_schema(conn)
    list_models(conn)
    list_pipelines(conn)
    list_semantic_kbs(conn)
    list_agents(conn)
    check_gucs(conn)

    smoke_ok = run_smoke_test(conn)
    if not smoke_ok:
        all_ok = False

    conn.close()

    print("\n" + "=" * 60)
    if all_ok:
        print("Result: All checks PASSED")
        sys.exit(0)
    else:
        print("Result: Some checks FAILED — see output above")
        sys.exit(1)


if __name__ == "__main__":
    main()
