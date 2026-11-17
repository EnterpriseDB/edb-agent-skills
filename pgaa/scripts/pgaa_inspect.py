#!/usr/bin/env python3
"""
pgaa_inspect.py — Inspect pgaa tables, catalogs, tasks, and configuration.

Connects to a PostgreSQL instance and prints a summary of:
  - Installed pgaa / pgfs version
  - Registered pgfs storage locations
  - Registered pgaa catalogs (name, type, status)
  - pgaa-managed analytics tables (schema, table, format, replication status, size)
  - In-progress or recent background maintenance tasks
  - Key GUC settings

Usage:
    python3 scripts/pgaa_inspect.py [--dsn DSN] [--section SECTION]

Arguments:
    --dsn      libpq connection string (default: PGCONN env var or
               "host=localhost dbname=postgres")
    --section  One of: all, version, locations, catalogs, tables, tasks, gucs
               (default: all)

Requirements:
    pip install psycopg2-binary

Exit codes:
    0  — success
    1  — connection or query error
"""

import argparse
import os
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

SECTIONS = ("version", "locations", "catalogs", "tables", "tasks", "gucs")

KEY_GUCS = [
    "pgaa.executor_engine",
    "pgaa.autostart_seafowl",
    "pgaa.enable_direct_scan",
    "pgaa.direct_scan_fail_behavior",
    "pgaa.enable_maintenance_worker",
    "pgaa.enable_metastore_sync_worker",
    "pgaa.max_in_memory_mb",
    "pgaa.max_replication_lag_s",
]


def header(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_table(rows, columns):
    if not rows:
        print("  (no rows)")
        return
    col_widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val) if val is not None else "NULL"))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_widths)
    separator = "  " + "  ".join("-" * w for w in col_widths)
    print(fmt.format(*columns))
    print(separator)
    for row in rows:
        print(fmt.format(*[str(v) if v is not None else "NULL" for v in row]))


def inspect(dsn: str, sections: set):
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
    except psycopg2.Error as exc:
        print(f"ERROR: Could not connect — {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        if "version" in sections:
            header("Extension Versions")
            try:
                cur.execute("SELECT pgaa.pgaa_version()")
                print("  pgaa:")
                for line in cur.fetchone()[0].splitlines():
                    print(f"    {line}")
                cur.execute("SELECT pgaa.engine_version()")
                row = cur.fetchone()
                print(f"  active engine: {row[0]}")
                cur.execute("SELECT pgfs.storage_fdw_meta()")
                row = cur.fetchone()
                print(f"  pgfs: {row}")
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

        if "locations" in sections:
            header("pgfs Storage Locations")
            try:
                cur.execute("SELECT name, url, msl_id FROM pgfs.list_storage_locations() ORDER BY name")
                print_table(cur.fetchall(), ["name", "url", "msl_id"])
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

        if "catalogs" in sections:
            header("pgaa Catalogs")
            try:
                cur.execute(
                    "SELECT name, type, status, refreshed_at FROM pgaa.list_catalogs() ORDER BY name"
                )
                print_table(cur.fetchall(), ["name", "type", "status", "refreshed_at"])
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

        if "tables" in sections:
            header("pgaa Analytics Tables")
            try:
                cur.execute(
                    """
                    SELECT
                        schema_name,
                        table_name,
                        format,
                        replication_status,
                        COALESCE(catalog_name, storage_location_name) AS source,
                        pg_size_pretty(object_storage_snapshot_size_bytes) AS snapshot_size,
                        pg_size_pretty(object_storage_total_size_bytes)    AS total_size
                    FROM pgaa.list_analytics_tables()
                    ORDER BY schema_name, table_name
                    """
                )
                print_table(
                    cur.fetchall(),
                    ["schema", "table", "format", "replication", "source", "snapshot", "total"],
                )
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

        if "tasks" in sections:
            header("Background Maintenance Tasks (last 20)")
            try:
                cur.execute(
                    """
                    SELECT id::text, table_name::text, task_type, status, scheduled_at, error
                    FROM pgaa.background_task
                    ORDER BY scheduled_at DESC NULLS LAST
                    LIMIT 20
                    """
                )
                print_table(
                    cur.fetchall(),
                    ["id", "table", "type", "status", "scheduled_at", "error"],
                )
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

        if "gucs" in sections:
            header("Key pgaa GUC Settings")
            placeholders = ",".join(["%s"] * len(KEY_GUCS))
            try:
                cur.execute(
                    f"SELECT name, setting, unit FROM pg_settings WHERE name IN ({placeholders}) ORDER BY name",
                    KEY_GUCS,
                )
                print_table(cur.fetchall(), ["parameter", "value", "unit"])

                # Also list any DataFusion overrides that differ from defaults
                cur.execute(
                    """
                    SELECT name, setting
                    FROM pg_settings
                    WHERE name LIKE 'pgaa.datafusion%'
                      AND setting IS DISTINCT FROM boot_val
                    ORDER BY name
                    """
                )
                rows = cur.fetchall()
                if rows:
                    print("\n  DataFusion overrides (non-default):")
                    print_table(rows, ["parameter", "value"])
            except psycopg2.Error as exc:
                print(f"  WARNING: {exc}")

    finally:
        conn.close()

    print()


def main():
    parser = argparse.ArgumentParser(description="Inspect a pgaa-enabled PostgreSQL instance")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PGCONN", "host=localhost dbname=postgres"),
        help="libpq connection string",
    )
    parser.add_argument(
        "--section",
        choices=list(SECTIONS) + ["all"],
        default="all",
        help="Which section to display (default: all)",
    )
    args = parser.parse_args()

    sections = set(SECTIONS) if args.section == "all" else {args.section}
    inspect(args.dsn, sections)


if __name__ == "__main__":
    main()
