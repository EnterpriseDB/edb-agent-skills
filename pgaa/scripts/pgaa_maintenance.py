#!/usr/bin/env python3
"""
pgaa_maintenance.py — Schedule and monitor background maintenance tasks for pgaa tables.

This script connects to a PostgreSQL instance with pgaa installed and can:
  - Verify the maintenance worker is enabled
  - Schedule compaction, zorder, vacuum, or purge tasks
  - Monitor task status (live-poll until completion)
  - List recent task history

Usage:
    python3 scripts/pgaa_maintenance.py [--dsn DSN] <command> [options]

Commands:
    check                     Check whether the maintenance worker is enabled
    enable                    Enable the maintenance worker via ALTER SYSTEM
    schedule --table SCHEMA.TABLE --type TYPE [--options JSON]
                              Schedule a maintenance task
    watch --task-id UUID      Poll task status until complete
    history [--limit N]       List recent background tasks

Arguments:
    --dsn       libpq connection string (default: PGCONN env var or
                "host=localhost dbname=postgres")
    --table     Qualified table name (e.g., analytics.events)
    --type      Task type: compaction | zorder | vacuum | purge
                compaction  — delta and iceberg tables
                zorder      — delta tables only
                vacuum      — delta tables only
                purge       — delta tables only
                (For Iceberg zorder/vacuum/purge, use pgaa.spark_sql() instead)
    --options   JSON string of task options (optional; defaults to '{}')
    --task-id   UUID of a previously scheduled task to monitor
    --limit     Number of rows for history command (default: 20)
    --interval  Polling interval in seconds for watch command (default: 5)

Requirements:
    pip install psycopg2-binary

Exit codes:
    0  — success (or task completed successfully)
    1  — connection / query error
    2  — task failed
    3  — maintenance worker is disabled (use 'enable' command first)
"""

import argparse
import json
import os
import sys
import time

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

TASK_TYPES = ("compaction", "zorder", "vacuum", "purge")

DEFAULT_OPTIONS: dict[str, dict] = {
    "compaction": {
        "target_size": 536870912,
        "preserve_insertion_order": True,
        "dry_run": False,
    },
    "zorder": {
        "target_size": 1073741824,
        "preserve_insertion_order": False,
    },
    "vacuum": {
        "retention_period": "168 hours",
        "dry_run": False,
        "enforce_retention_duration": True,
    },
    "purge": {},
}


def connect(dsn: str):
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    except psycopg2.Error as exc:
        print(f"ERROR: Could not connect — {exc}", file=sys.stderr)
        sys.exit(1)


def check_maintenance_worker(cur) -> bool:
    cur.execute(
        "SELECT setting FROM pg_settings WHERE name = 'pgaa.enable_maintenance_worker'"
    )
    row = cur.fetchone()
    if row is None:
        print("WARNING: pgaa.enable_maintenance_worker GUC not found — is pgaa installed?")
        return False
    enabled = row[0].lower() == "on"
    print(f"  pgaa.enable_maintenance_worker = {row[0]}")
    return enabled


def cmd_check(dsn: str):
    conn = connect(dsn)
    cur = conn.cursor()
    print("Checking maintenance worker status…")
    enabled = check_maintenance_worker(cur)
    if enabled:
        print("  ✓ Maintenance worker is ENABLED — tasks will execute.")
    else:
        print("  ✗ Maintenance worker is DISABLED — tasks will be queued but not executed.")
        print("    Run:  python3 scripts/pgaa_maintenance.py enable")
        print("    Or:   ALTER SYSTEM SET pgaa.enable_maintenance_worker = on; SELECT pg_reload_conf();")
        sys.exit(3)
    conn.close()


def cmd_enable(dsn: str):
    conn = connect(dsn)
    cur = conn.cursor()
    print("Enabling maintenance worker…")
    try:
        cur.execute("ALTER SYSTEM SET pgaa.enable_maintenance_worker = on")
        cur.execute("SELECT pg_reload_conf()")
        print("  ✓ pgaa.enable_maintenance_worker set to on and config reloaded.")
        print("  Note: Existing queued tasks will begin executing on the next worker cycle.")
    except psycopg2.Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_schedule(dsn: str, table: str, task_type: str, options_str: str | None):
    if task_type not in TASK_TYPES:
        print(f"ERROR: Invalid task type '{task_type}'. Choose from: {', '.join(TASK_TYPES)}", file=sys.stderr)
        sys.exit(1)

    # Parse / merge options
    options: dict = dict(DEFAULT_OPTIONS.get(task_type, {}))
    if options_str:
        try:
            overrides = json.loads(options_str)
            options.update(overrides)
        except json.JSONDecodeError as exc:
            print(f"ERROR: Invalid JSON in --options: {exc}", file=sys.stderr)
            sys.exit(1)

    conn = connect(dsn)
    cur = conn.cursor()

    # Warn if maintenance worker is off
    if not check_maintenance_worker(cur):
        print("WARNING: Maintenance worker is disabled. Task will be queued but won't execute.")
        print("         Run 'enable' command or SET pgaa.enable_maintenance_worker = on.")

    print(f"\nScheduling '{task_type}' task on {table}…")
    print(f"  options: {json.dumps(options)}")

    try:
        cur.execute(
            "SELECT pgaa.launch_task(%s::regclass, %s, %s::jsonb)",
            (table, task_type, json.dumps(options)),
        )
        task_id = cur.fetchone()[0]
        print(f"  ✓ Task scheduled — ID: {task_id}")
        print(f"\nMonitor with:")
        print(f"  python3 scripts/pgaa_maintenance.py --dsn '...' watch --task-id {task_id}")
        print(f"  SELECT * FROM pgaa.background_task WHERE id = '{task_id}';")
    except psycopg2.Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_watch(dsn: str, task_id: str, interval: int):
    conn = connect(dsn)
    cur = conn.cursor()

    print(f"Watching task {task_id} (polling every {interval}s)…")
    last_status = None

    try:
        while True:
            cur.execute(
                "SELECT status, error FROM pgaa.background_task WHERE id = %s::uuid",
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                print(f"ERROR: Task {task_id} not found.", file=sys.stderr)
                sys.exit(1)

            status, error = row
            if status != last_status:
                print(f"  [{time.strftime('%H:%M:%S')}] status: {status}" +
                      (f"  error: {error}" if error else ""))
                last_status = status

            if status == "success":
                print(f"\n✓ Task {task_id} completed successfully.")
                break
            elif status == "failure":
                print(f"\n✗ Task {task_id} FAILED. Error: {error}", file=sys.stderr)
                sys.exit(2)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        conn.close()


def cmd_history(dsn: str, limit: int):
    conn = connect(dsn)
    cur = conn.cursor()

    print(f"Recent background tasks (last {limit}):")
    try:
        cur.execute(
            """
            SELECT
                id::text,
                table_name::text,
                task_type,
                status,
                scheduled_at,
                COALESCE(SUBSTRING(error FROM 1 FOR 60), '') AS error_snippet
            FROM pgaa.background_task
            ORDER BY scheduled_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        if not rows:
            print("  (no tasks found)")
        else:
            cols = ["id", "table", "type", "status", "scheduled_at", "error"]
            col_widths = [len(c) for c in cols]
            for row in rows:
                for i, val in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(val) if val is not None else ""))
            fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_widths)
            sep = "  " + "  ".join("-" * w for w in col_widths)
            print(fmt.format(*cols))
            print(sep)
            for row in rows:
                print(fmt.format(*[str(v) if v is not None else "" for v in row]))
    except psycopg2.Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Schedule and monitor pgaa background maintenance tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PGCONN", "host=localhost dbname=postgres"),
        help="libpq connection string",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="Check maintenance worker status")
    subparsers.add_parser("enable", help="Enable maintenance worker via ALTER SYSTEM")

    p_sched = subparsers.add_parser("schedule", help="Schedule a maintenance task")
    p_sched.add_argument("--table", required=True, help="Qualified table name (schema.table)")
    p_sched.add_argument(
        "--type", dest="task_type", required=True, choices=TASK_TYPES, help="Task type"
    )
    p_sched.add_argument("--options", default=None, help="Task options as JSON string")

    p_watch = subparsers.add_parser("watch", help="Poll task status until complete")
    p_watch.add_argument("--task-id", required=True, help="Task UUID to monitor")
    p_watch.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")

    p_hist = subparsers.add_parser("history", help="List recent background tasks")
    p_hist.add_argument("--limit", type=int, default=20, help="Number of rows to show")

    args = parser.parse_args()

    if args.command == "check":
        cmd_check(args.dsn)
    elif args.command == "enable":
        cmd_enable(args.dsn)
    elif args.command == "schedule":
        cmd_schedule(args.dsn, args.table, args.task_type, args.options)
    elif args.command == "watch":
        cmd_watch(args.dsn, args.task_id, args.interval)
    elif args.command == "history":
        cmd_history(args.dsn, args.limit)


if __name__ == "__main__":
    main()
