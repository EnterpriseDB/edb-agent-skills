#!/usr/bin/env python3
"""
AIDB diagnostic helper.

Usage:
    python3 diagnose_aidb.py [--host HOST] [--port PORT] [--dbname DB] [--user USER] [--password PW]

Connects to a running PostgreSQL instance with AIDB installed and prints:
  - Extension installation status
  - Registered models
  - Pipelines and their auto-processing modes
  - Semantic KB list
  - Agents, SQL tools, MCP servers, purposes
  - Key GUC values
  - Pipeline error counts per pipeline and step

Run it as a superuser or a member of aidb_users. A role that cannot read a
section, or a pipeline's error table, gets "permission denied", which is
reported as an issue rather than as zero errors.

Exit codes: 0 no issues found, 1 issues found, 2 cannot connect or aidb not installed.

Requires: psycopg2 (pip install psycopg2-binary)
"""

import argparse
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_UNAVAILABLE = 2


def connect(args):
    try:
        # Unset options fall back to libpq defaults (PGHOST, PGPORT, PGUSER, PGDATABASE, ...)
        params = {
            "host": args.host,
            "port": args.port,
            "dbname": args.dbname,
            "user": args.user,
            "password": args.password,
        }
        conn = psycopg2.connect(**{k: v for k, v in params.items() if v is not None})
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}", file=sys.stderr)
        sys.exit(EXIT_UNAVAILABLE)


def query(conn, sql, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        except Exception as e:
            return [{"error": str(e).strip()}]


def failed(rows):
    return bool(rows) and "error" in rows[0]


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def print_rows(rows, empty_msg="(none)"):
    """Print rows; return True when the query failed"""
    if not rows:
        print(f"  {empty_msg}")
        return False
    if failed(rows):
        print(f"  ERROR: {rows[0]['error']}")
        return True
    for row in rows:
        print("  " + " | ".join(f"{k}={v!r}" for k, v in row.items()))
    return False


def print_gucs(conn, gucs):
    """Print GUC values; return True when a lookup failed"""
    issue = False
    for guc in gucs:
        rows = query(conn, "SELECT current_setting(%s, true) AS value", (guc,))
        if failed(rows):
            print(f"  {guc}: ERROR: {rows[0]['error']}")
            issue = True
            continue
        val = rows[0]["value"] if rows else None
        print(f"  {guc} = {'(not set)' if val is None else repr(val)}")
    return issue


def main():
    parser = argparse.ArgumentParser(
        description="AIDB diagnostic helper",
        epilog="Exit codes: 0 no issues found, 1 issues found, 2 cannot connect or aidb not installed.",
    )
    parser.add_argument("--host", help="default: PGHOST or libpq default")
    parser.add_argument("--port", type=int, help="default: PGPORT or 5432")
    parser.add_argument("--dbname", help="default: PGDATABASE or the user name")
    parser.add_argument("--user", help="default: PGUSER or the OS user")
    parser.add_argument("--password", help="default: PGPASSWORD or ~/.pgpass")
    args = parser.parse_args()

    conn = connect(args)
    dsn = conn.get_dsn_parameters()
    args.dbname = dsn.get("dbname")
    print(f"Connected to {dsn.get('host')}:{dsn.get('port')}/{args.dbname} as {dsn.get('user')}")
    issues = []

    # 1. Extension check, before any aidb query
    section("AIDB Extension Status")
    rows = query(
        conn,
        "SELECT e.extversion AS installed_version, a.default_version "
        "FROM pg_extension e LEFT JOIN pg_available_extensions a ON a.name = e.extname "
        "WHERE e.extname = 'aidb'",
    )
    if failed(rows):
        print(f"  ERROR: {rows[0]['error']}")
        conn.close()
        sys.exit(EXIT_UNAVAILABLE)
    if not rows:
        print(f"aidb extension is not installed in database {args.dbname}")
        conn.close()
        sys.exit(EXIT_UNAVAILABLE)
    print_rows(rows)

    checks = [
        ("Registered Models", "SELECT name, provider FROM aidb.list_models() ORDER BY name", "No models registered"),
        ("Pipelines", "SELECT name, id, source_schema, auto_processing FROM aidb.pipelines ORDER BY name", "No pipelines defined"),
        ("Semantic Knowledge Bases", "SELECT name, model_name, schemas, auto_processing FROM aidb.list_semantic_kbs()", "No semantic KBs defined"),
        ("Agents", "SELECT name, model, purpose FROM aidb.agents ORDER BY name", "No agents defined"),
        ("SQL Tools", "SELECT name, description, read_only FROM aidb.sql_tool_registry ORDER BY name", "No SQL tools registered"),
        ("MCP Servers", "SELECT name, url, transport FROM aidb.mcp_registry ORDER BY name", "No MCP servers registered"),
        ("Purpose Registry", "SELECT name, role, deleted_at FROM aidb.purpose_registry ORDER BY name", "No purposes defined"),
    ]
    for title, sql, empty_msg in checks:
        section(title)
        if print_rows(query(conn, sql), empty_msg):
            issues.append(title)

    section("Key GUC Values")
    if print_gucs(conn, [
        "aidb.max_threads",
        "aidb.pipeline_error_warnings",
        "aidb.agent_memory_enable_worker",
        "aidb.memory_worker_idle_wait_ms",
        "aidb.agent_session_source",
        "aidb.agent_memory_namespace",
        "aidb.otel_client",
        "aidb.egress_allowlist",
        "aidb.allow_insecure_egress",
        "aidb.allow_insecure_tls",
    ]):
        issues.append("Key GUC Values")

    # Endpoint GUCs exist only when the endpoints library is loaded
    section("Endpoint GUCs")
    if print_gucs(conn, [
        "edb.endpoints_enabled",
        "edb.endpoints_host",
        "edb.endpoints_mcp_enabled",
        "edb.endpoints_mcp_host",
        "edb.endpoints_mcp_port",
    ]):
        issues.append("Endpoint GUCs")

    section("Pipeline Errors")
    pipelines = query(conn, "SELECT name FROM aidb.pipelines ORDER BY name")
    if failed(pipelines):
        print(f"  ERROR: {pipelines[0]['error']}")
        issues.append("Pipeline Errors")
    elif not pipelines:
        print("  No pipelines defined")
    else:
        any_errors = False
        for p in pipelines:
            name = p["name"]
            # Per pipeline, since get_all_error_summaries() silently skips error tables the role cannot read
            detail = query(
                conn,
                "SELECT pipeline_step, step_operation, error_category, error_count, latest_failed_at "
                "FROM aidb.get_error_log_summary(%s)",
                (name,),
            )
            if failed(detail):
                print(f"  {name}: cannot read error log: {detail[0]['error'].splitlines()[0]}")
                issues.append(f"pipeline {name}")
                continue
            count = sum(d["error_count"] for d in detail)
            print(f"  {name}: {count} error{'' if count == 1 else 's'}")
            if not count:
                continue
            any_errors = True
            issues.append(f"pipeline {name}")
            for d in detail:
                print(
                    f"    step {d['pipeline_step']} {d['step_operation']} ({d['error_category']}): "
                    f"{d['error_count']}, latest {d['latest_failed_at']}"
                )
        if any_errors:
            print("  Inspect rows with: SELECT * FROM aidb.get_error_logs('<pipeline>', p_limit => 10);")

    conn.close()
    print()
    if issues:
        print(f"Issues found in: {', '.join(issues)}")
        sys.exit(EXIT_ISSUES)
    print("All checks passed. No issues found.")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
