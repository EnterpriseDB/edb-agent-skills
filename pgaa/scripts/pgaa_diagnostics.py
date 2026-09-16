#!/usr/bin/env python3
"""
pgaa_diagnostics.py — Agent diagnostic helper for EDB Postgres Analytics Accelerator.

Connects to a PostgreSQL database with the pgaa extension installed and
collects a structured diagnostics snapshot. Run this when a user reports
an issue with pgaa tables, catalog sync, replication, or engine connectivity.

Usage:
    python3 scripts/pgaa_diagnostics.py --dsn "postgresql://user:pass@host:5432/dbname"

Requirements: psycopg2 (pip install psycopg2-binary)
"""

import argparse
import json
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


def run_query(conn, sql, description, fatal=False):
    """Run a diagnostic query and return results as a list of dicts."""
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return {"status": "ok", "description": description, "rows": [dict(r) for r in rows]}
    except Exception as e:
        result = {"status": "error", "description": description, "error": str(e)}
        if fatal:
            raise
        return result


def collect_diagnostics(dsn):
    report = {}

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
    except Exception as e:
        print(f"ERROR: Could not connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    # 1. pgaa version
    report["pgaa_version"] = run_query(
        conn,
        "SELECT pgaa.pgaa_version() AS pgaa_version",
        "pgaa build version"
    )

    # 2. Engine version
    report["engine_version"] = run_query(
        conn,
        "SELECT pgaa.engine_version() AS engine_version",
        "Offload engine version (Seafowl or Spark Connect)"
    )

    # 3. Key GUCs
    report["key_gucs"] = run_query(
        conn,
        """
        SELECT name, setting, unit, category
        FROM pg_settings
        WHERE name LIKE 'pgaa.%'
        ORDER BY name
        """,
        "All pgaa GUC settings"
    )

    # 4. Analytics tables
    report["analytics_tables"] = run_query(
        conn,
        """
        SELECT
            schema_name,
            table_name,
            format,
            replication_status,
            object_storage_snapshot_size_bytes,
            object_storage_total_size_bytes,
            storage_location_name,
            catalog_name,
            catalog_namespace,
            catalog_table
        FROM pgaa.list_analytics_tables()
        ORDER BY schema_name, table_name
        """,
        "All PGAA analytics tables"
    )

    # 5. Catalogs
    report["catalogs"] = run_query(
        conn,
        """
        SELECT name, type, status, created_at, refreshed_at
        FROM pgaa.list_catalogs()
        ORDER BY name
        """,
        "Registered external catalogs"
    )

    # 6. Background tasks (last 20)
    report["background_tasks_recent"] = run_query(
        conn,
        """
        SELECT id, type, target_table::text, status, scheduled_at, started_at, finished_at, output
        FROM pgaa.background_task
        ORDER BY scheduled_at DESC
        LIMIT 20
        """,
        "Recent background tasks (last 20)"
    )

    # 7. Pending/running tasks
    report["background_tasks_active"] = run_query(
        conn,
        """
        SELECT id, type, target_table::text, status, scheduled_at, started_at
        FROM pgaa.background_task
        WHERE status IN ('pending', 'running')
        ORDER BY scheduled_at
        """,
        "Active (pending or running) background tasks"
    )

    # 8. pgfs storage locations (if pgfs is installed)
    report["pgfs_storage_locations"] = run_query(
        conn,
        """
        SELECT *
        FROM pgfs.list_storage_locations()
        """,
        "pgfs storage locations (requires pgfs extension)"
    )

    conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description="pgaa diagnostic snapshot collector")
    parser.add_argument(
        "--dsn",
        required=True,
        help='PostgreSQL DSN, e.g. "postgresql://user:pass@host:5432/dbname"'
    )
    parser.add_argument(
        "--output",
        default="-",
        help='Output file path (default: stdout)'
    )
    args = parser.parse_args()

    report = collect_diagnostics(args.dsn)

    output = json.dumps(report, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Diagnostics written to {args.output}", file=sys.stderr)

    # Print a human-readable summary to stderr
    print("\n--- SUMMARY ---", file=sys.stderr)
    for key, val in report.items():
        status = val.get("status", "?")
        desc = val.get("description", key)
        count = len(val.get("rows", []))
        err = val.get("error", "")
        if status == "ok":
            print(f"  ✓ {desc}: {count} row(s)", file=sys.stderr)
        else:
            print(f"  ✗ {desc}: ERROR — {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
