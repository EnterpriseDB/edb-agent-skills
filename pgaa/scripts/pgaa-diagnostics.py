#!/usr/bin/env python3
"""
pgaa-diagnostics.py — Agent diagnostic script for EDB Postgres Analytics Accelerator (pgaa)

Usage:
    python3 scripts/pgaa-diagnostics.py --connstr "host=localhost dbname=mydb user=postgres"

Requirements:
    pip install psycopg2-binary

This script connects to a PostgreSQL instance with pgaa installed, runs a
suite of diagnostic queries, and prints a structured report. It is designed
to be run by an AI agent when investigating pgaa issues.
"""

import argparse
import sys

def main():
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("ERROR: psycopg2 not available. Install with: pip install psycopg2-binary")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="pgaa diagnostic tool")
    parser.add_argument(
        "--connstr",
        default="host=localhost dbname=postgres user=postgres",
        help="PostgreSQL connection string (libpq format)",
    )
    args = parser.parse_args()

    try:
        conn = psycopg2.connect(args.connstr)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}")
        sys.exit(1)

    sections = []

    # ------------------------------------------------------------------ #
    def run(label, sql, critical=False):
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            sections.append((label, rows, None))
        except Exception as e:
            sections.append((label, None, str(e)))
            if critical:
                print(f"CRITICAL: {label} failed — {e}")
                sys.exit(1)

    # ------------------------------------------------------------------ #
    print("=== pgaa Diagnostic Report ===\n")

    # 1. Extension presence
    run(
        "Installed Extensions (pgfs, pgaa)",
        "SELECT extname, extversion FROM pg_extension WHERE extname IN ('pgfs', 'pgaa') ORDER BY extname;",
        critical=False,
    )

    # 2. pgaa + engine version
    run("pgaa Version", "SELECT pgaa.pgaa_version() AS pgaa_version;")
    run("Engine Version", "SELECT pgaa.engine_version() AS engine_version;")

    # 3. Key GUCs
    run(
        "Key pgaa GUCs",
        """
        SELECT name, setting, unit, short_desc
        FROM pg_settings
        WHERE name LIKE 'pgaa.%'
          AND name IN (
              'pgaa.executor_engine',
              'pgaa.seafowl_url',
              'pgaa.spark_connect_url',
              'pgaa.enable_direct_scan',
              'pgaa.direct_scan_fail_behavior',
              'pgaa.enable_metastore_sync_worker',
              'pgaa.enable_maintenance_worker',
              'pgaa.metastore_sync_poll_rate_s',
              'pgaa.max_replication_lag_s',
              'pgaa.enable_metadata_stats',
              'pgaa.lakehouse_table_stats_cache_ttl_s',
              'pgaa.autostart_seafowl',
              'pgaa.autostart_seafowl_enable_metrics',
              'pgaa.enable_join_pushdown',
              'pgaa.enable_groupby_pushdown',
              'pgaa.enable_orderby_pushdown',
              'pgaa.use_seafowl_cost_estimates'
          )
        ORDER BY name;
        """,
    )

    # 4. Registered catalogs
    run(
        "Registered Catalogs",
        "SELECT name, type, status, created_at, refreshed_at FROM pgaa.list_catalogs();",
    )

    # 5. Analytics tables
    run(
        "Analytics Tables",
        """
        SELECT schema_name, table_name, format,
               object_storage_snapshot_size_bytes,
               object_storage_total_size_bytes,
               replication_status,
               storage_location_name,
               catalog_name
        FROM pgaa.list_analytics_tables()
        ORDER BY schema_name, table_name;
        """,
    )

    # 6. Tiered tables (if any)
    run(
        "Tiered Tables",
        """
        SELECT schema_name, table_name,
               partition_increment, retention, analytics_offload,
               replication_enabled,
               tiered_data_size, untiered_data_size
        FROM pgaa.list_tiered_tables()
        ORDER BY schema_name, table_name;
        """,
    )

    # 7. Background tasks
    run(
        "Background Tasks (recent 20)",
        """
        SELECT id, type, target_table::text, status,
               scheduled_at, started_at, finished_at
        FROM pgaa.background_task
        ORDER BY scheduled_at DESC
        LIMIT 20;
        """,
    )

    # 8. Table mapping
    run(
        "Table Mapping (pgaa.table_mapping)",
        """
        SELECT rel_namespace, rel_name, storage_location,
               storage_path, managed_by, format,
               catalog_namespace, catalog_table
        FROM pgaa.table_mapping
        ORDER BY rel_namespace, rel_name;
        """,
    )

    # ------------------------------------------------------------------ #
    for label, rows, err in sections:
        print(f"\n--- {label} ---")
        if err:
            print(f"  ERROR: {err}")
        elif not rows:
            print("  (no rows)")
        else:
            # Print column headers
            headers = list(rows[0].keys())
            col_widths = {h: max(len(h), max((len(str(r.get(h, ''))) for r in rows), default=0)) for h in headers}
            header_line = "  " + "  ".join(h.ljust(col_widths[h]) for h in headers)
            print(header_line)
            print("  " + "-" * (len(header_line) - 2))
            for row in rows:
                print("  " + "  ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers))

    print("\n=== End of Report ===")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
