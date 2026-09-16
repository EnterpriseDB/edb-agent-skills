#!/usr/bin/env python3
"""
aidb-diagnose.py — AIDB environment diagnostic script for the end-agent.

Connects to a PostgreSQL database (via libpq env vars or explicit args),
checks that the aidb extension is installed, reports its version cautiously,
lists registered models and pipelines, and emits a summary.

Usage:
    python3 scripts/aidb-diagnose.py
    python3 scripts/aidb-diagnose.py --host localhost --port 5432 --dbname mydb

Environment variables (standard libpq):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD

Requires: psycopg2 or psycopg2-binary
    pip install psycopg2-binary
"""

import argparse
import os
import sys

def get_connection(args):
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 is not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    conn_params = {}
    if args.host:
        conn_params['host'] = args.host
    if args.port:
        conn_params['port'] = args.port
    if args.dbname:
        conn_params['dbname'] = args.dbname
    if args.user:
        conn_params['user'] = args.user
    if args.password:
        conn_params['password'] = args.password

    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}")
        sys.exit(1)


def run_query(cur, sql, params=None):
    try:
        cur.execute(sql, params)
        return cur.fetchall(), [desc[0] for desc in cur.description]
    except Exception as e:
        return None, str(e)


def print_table(rows, headers, indent="  "):
    if not rows:
        print(f"{indent}(none)")
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val) if val is not None else "NULL"))
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    print(f"{indent}{header_line}")
    print(f"{indent}{'-' * len(header_line)}")
    for row in rows:
        row_line = "  ".join(str(val if val is not None else "NULL").ljust(col_widths[i])
                             for i, val in enumerate(row))
        print(f"{indent}{row_line}")


def main():
    parser = argparse.ArgumentParser(description="AIDB environment diagnostic")
    parser.add_argument("--host", default=os.environ.get("PGHOST"))
    parser.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE"))
    parser.add_argument("--user", default=os.environ.get("PGUSER"))
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD"))
    args = parser.parse_args()

    print("=" * 60)
    print("AIDB Environment Diagnostic")
    print("=" * 60)

    conn = get_connection(args)
    cur = conn.cursor()

    # 1. Check PostgreSQL version
    print("\n[1] PostgreSQL Version")
    rows, _ = run_query(cur, "SELECT version()")
    if rows:
        print(f"  {rows[0][0][:80]}")

    # 2. Check aidb extension
    print("\n[2] AIDB Extension")
    rows, headers = run_query(cur,
        "SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb'")
    if rows:
        ext_name, ext_version = rows[0]
        print(f"  Installed: YES")
        print(f"  Reported version in catalog: {ext_version}")
        print(f"  NOTE: This version may be ahead of the last released version.")
        print(f"        Treat it cautiously — the repo may be pre-release.")
    else:
        print("  Installed: NO")
        print("  Run: CREATE EXTENSION aidb CASCADE;")
        conn.close()
        return

    # 3. Check aidb schema exists
    print("\n[3] AIDB Schema Objects")
    rows, _ = run_query(cur,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'aidb'")
    if rows:
        print(f"  Functions in aidb schema: {rows[0][0]}")

    # 4. List registered models
    print("\n[4] Registered Models")
    rows, headers = run_query(cur,
        "SELECT name, provider FROM aidb.list_models()")
    if rows is None:
        print(f"  ERROR querying aidb.list_models(): {headers}")
    else:
        print_table(rows, ["name", "provider"])

    # 5. List pipelines
    print("\n[5] Pipelines")
    rows, headers = run_query(cur,
        "SELECT name, auto_processing FROM aidb.list_pipelines()")
    if rows is None:
        print(f"  ERROR querying aidb.list_pipelines(): {headers}")
    else:
        print_table(rows, ["name", "auto_processing"])

    # 6. List semantic KBs
    print("\n[6] Semantic Knowledge Bases")
    rows, headers = run_query(cur, "SELECT * FROM aidb.list_semantic_kbs()")
    if rows is None:
        print(f"  ERROR querying aidb.list_semantic_kbs(): {headers}")
    elif not rows:
        print("  (none)")
    else:
        print_table(rows, [h for h in headers])

    # 7. List agents
    print("\n[7] Registered Agents")
    rows, headers = run_query(cur,
        "SELECT name, model FROM aidb.agents")
    if rows is None:
        print(f"  ERROR querying aidb.agents: {headers}")
    else:
        print_table(rows, ["name", "model"])

    # 8. List tools
    print("\n[8] Registered Tools")
    rows, headers = run_query(cur,
        "SELECT name, type FROM aidb.tools ORDER BY type, name")
    if rows is None:
        print(f"  ERROR querying aidb.tools: {headers}")
    else:
        print_table(rows, ["name", "type"])

    # 9. GUC settings
    print("\n[9] Key AIDB GUC Settings")
    gucs = [
        'aidb.max_threads',
        'aidb.pipeline_error_warnings',
        'aidb.otel_client',
        'aidb.agent_session_source',
        'aidb.agent_memory_namespace',
    ]
    for guc in gucs:
        rows, _ = run_query(cur, "SELECT current_setting(%s, true)", (guc,))
        val = rows[0][0] if rows and rows[0][0] is not None else "(not set)"
        print(f"  {guc} = {val}")

    # 10. MCP endpoint
    print("\n[10] MCP Endpoint Status")
    rows, _ = run_query(cur, "SELECT current_setting('edb.endpoints_mcp_enabled', true)")
    enabled = rows[0][0] if rows else "(not set)"
    print(f"  edb.endpoints_mcp_enabled = {enabled}")

    print("\n" + "=" * 60)
    print("Diagnostic complete.")
    print("=" * 60)
    conn.close()


if __name__ == "__main__":
    main()
