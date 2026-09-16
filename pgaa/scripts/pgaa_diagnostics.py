#!/usr/bin/env python3
"""
pgaa_diagnostics.py — Diagnostic helper for EDB Postgres Analytics Accelerator (pgaa).

Run against a live PostgreSQL connection to collect pgaa health information:
  - Extension version
  - Engine version and connectivity
  - Active GUCs (pushdown, cost, worker flags)
  - Analytics table listing (name, format, storage, replication status, sizes)
  - Registered catalogs and their status
  - Pending/running background tasks
  - Storage location reachability (optional)

Usage:
    python3 pgaa_diagnostics.py --dsn "postgresql://user:pass@host:5432/dbname"
    python3 pgaa_diagnostics.py --dsn "..." --test-storage  # also tests storage locations

Requirements: psycopg2 (pip install psycopg2-binary)
"""

import argparse
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(1)


SECTION = "=" * 70


def header(title):
    print(f"\n{SECTION}")
    print(f"  {title}")
    print(SECTION)


def run_query(cur, sql, params=None):
    try:
        cur.execute(sql, params)
        return cur.fetchall(), [d[0] for d in cur.description]
    except Exception as e:
        return None, str(e)


def fmt_table(rows, cols, max_col_width=40):
    if not rows:
        print("  (no rows)")
        return
    widths = [len(c) for c in cols]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = min(max(widths[i], len(str(val) if val is not None else "NULL")), max_col_width)
    header_row = " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    separator = "-+-".join("-" * w for w in widths)
    print(f"  {header_row}")
    print(f"  {separator}")
    for row in rows:
        cells = []
        for i, val in enumerate(row):
            s = str(val) if val is not None else "NULL"
            if len(s) > max_col_width:
                s = s[: max_col_width - 3] + "..."
            cells.append(s.ljust(widths[i]))
        print(f"  {' | '.join(cells)}")


def main():
    parser = argparse.ArgumentParser(description="pgaa diagnostic helper")
    parser.add_argument("--dsn", required=True, help="PostgreSQL connection string")
    parser.add_argument(
        "--test-storage",
        action="store_true",
        help="Call pgaa.test_storage_location() for each registered pgfs location",
    )
    args = parser.parse_args()

    try:
        conn = psycopg2.connect(args.dsn)
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 1. Extension version
    # ------------------------------------------------------------------ #
    header("1. pgaa Extension Version")
    rows, cols = run_query(
        cur,
        "SELECT extversion FROM pg_extension WHERE extname = 'pgaa'",
    )
    if isinstance(cols, str):
        print(f"  ERROR: {cols}")
    elif not rows:
        print("  WARNING: pgaa extension not installed in this database!")
    else:
        print(f"  Installed extension version: {rows[0]['extversion']}")

    # pgaa_version() for build details
    rows, cols = run_query(cur, "SELECT pgaa.pgaa_version()")
    if isinstance(cols, str):
        print(f"  pgaa_version() ERROR: {cols}")
    elif rows:
        print(f"  pgaa.pgaa_version(): {rows[0]['pgaa_version']}")

    # ------------------------------------------------------------------ #
    # 2. Engine version & executor engine
    # ------------------------------------------------------------------ #
    header("2. Executor Engine")
    rows, cols = run_query(cur, "SHOW pgaa.executor_engine")
    engine = None
    if isinstance(cols, str):
        print(f"  pgaa.executor_engine ERROR: {cols}")
    elif rows:
        engine = list(rows[0].values())[0]
        print(f"  pgaa.executor_engine = {engine}")

    rows, cols = run_query(cur, "SELECT pgaa.engine_version()")
    if isinstance(cols, str):
        print(f"  engine_version() ERROR: {cols}")
    elif rows:
        print(f"  pgaa.engine_version():\n    {list(rows[0].values())[0]}")

    # ------------------------------------------------------------------ #
    # 3. Key GUCs
    # ------------------------------------------------------------------ #
    header("3. Key pgaa GUCs")
    gucs = [
        "pgaa.executor_engine",
        "pgaa.enable_direct_scan",
        "pgaa.direct_scan_fail_behavior",
        "pgaa.enable_join_pushdown",
        "pgaa.enable_groupby_pushdown",
        "pgaa.enable_orderby_pushdown",
        "pgaa.enable_distinct_pushdown",
        "pgaa.enable_window_pushdown",
        "pgaa.use_seafowl_cost_estimates",
        "pgaa.enable_metadata_stats",
        "pgaa.enable_maintenance_worker",
        "pgaa.enable_metastore_sync_worker",
        "pgaa.metastore_sync_poll_rate_s",
        "pgaa.max_replication_lag_s",
        "pgaa.lakehouse_table_stats_cache_ttl_s",
    ]
    for guc in gucs:
        rows, cols = run_query(cur, f"SHOW {guc}")
        if isinstance(cols, str):
            print(f"  {guc}: (not available)")
        elif rows:
            val = list(rows[0].values())[0]
            print(f"  {guc} = {val}")

    # ------------------------------------------------------------------ #
    # 4. Analytics tables
    # ------------------------------------------------------------------ #
    header("4. Analytics Tables (pgaa.list_analytics_tables)")
    rows, cols = run_query(
        cur,
        """
        SELECT schema_name, table_name, format,
               object_storage_snapshot_size_bytes,
               object_storage_total_size_bytes,
               replication_status,
               storage_location_name, catalog_name
        FROM pgaa.list_analytics_tables()
        ORDER BY schema_name, table_name
        """,
    )
    if isinstance(cols, str):
        print(f"  ERROR: {cols}")
    else:
        fmt_table(
            [list(r.values()) for r in rows],
            cols,
        )

    # ------------------------------------------------------------------ #
    # 5. Registered catalogs
    # ------------------------------------------------------------------ #
    header("5. Registered Catalogs (pgaa.list_catalogs)")
    rows, cols = run_query(
        cur,
        "SELECT name, type, status, refreshed_at FROM pgaa.list_catalogs() ORDER BY name",
    )
    if isinstance(cols, str):
        print(f"  ERROR: {cols}")
    else:
        fmt_table([list(r.values()) for r in rows], cols)

    # ------------------------------------------------------------------ #
    # 6. Background tasks
    # ------------------------------------------------------------------ #
    header("6. Background Tasks (pgaa.background_task — pending/running)")
    rows, cols = run_query(
        cur,
        """
        SELECT id, type, target_table, status, scheduled_at, started_at
        FROM pgaa.background_task
        WHERE status IN ('pending', 'running')
        ORDER BY scheduled_at
        """,
    )
    if isinstance(cols, str):
        print(f"  ERROR: {cols}")
    else:
        fmt_table([list(r.values()) for r in rows], cols)

    # ------------------------------------------------------------------ #
    # 7. Recent failed tasks
    # ------------------------------------------------------------------ #
    header("7. Recent Failed Tasks (last 10)")
    rows, cols = run_query(
        cur,
        """
        SELECT id, type, target_table, status, finished_at, output
        FROM pgaa.background_task
        WHERE status = 'failure'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT 10
        """,
    )
    if isinstance(cols, str):
        print(f"  ERROR: {cols}")
    else:
        fmt_table([list(r.values()) for r in rows], cols)

    # ------------------------------------------------------------------ #
    # 8. Storage location test (optional)
    # ------------------------------------------------------------------ #
    if args.test_storage:
        header("8. Storage Location Connectivity Tests")
        rows, cols = run_query(
            cur,
            "SELECT name FROM pgfs.storage_location ORDER BY name",
        )
        if isinstance(cols, str):
            print(f"  Could not list pgfs storage locations: {cols}")
        elif not rows:
            print("  No pgfs storage locations registered.")
        else:
            for row in rows:
                loc_name = row["name"]
                test_rows, test_cols = run_query(
                    cur,
                    "SELECT pgaa.test_storage_location(%s, false)",
                    (loc_name,),
                )
                if isinstance(test_cols, str):
                    print(f"  {loc_name}: ERROR — {test_cols}")
                else:
                    result = list(test_rows[0].values())[0] if test_rows else None
                    status = "OK" if result is None else f"FAIL — {result}"
                    print(f"  {loc_name}: {status}")

    cur.close()
    conn.close()
    print(f"\n{SECTION}")
    print("  Diagnostics complete.")
    print(SECTION)


if __name__ == "__main__":
    main()
