#!/usr/bin/env python3
"""
AIDB diagnostic helper.

Usage:
    python3 diagnose_aidb.py [--host HOST] [--port PORT] [--dbname DB] [--user USER]

Connects to a running PostgreSQL instance with AIDB installed and prints:
  - Extension installation status
  - Registered models
  - Pipeline list and their auto-processing modes
  - Semantic KB list
  - Agent list
  - MCP registry
  - Key GUC values
  - Recent pipeline errors (last 10 per pipeline)

Requires: psycopg2 (pip install psycopg2-binary)
"""

import argparse
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


def connect(args):
    try:
        conn = psycopg2.connect(
            host=args.host,
            port=args.port,
            dbname=args.dbname,
            user=args.user,
            password=args.password,
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}", file=sys.stderr)
        sys.exit(1)


def query(conn, sql, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        except Exception as e:
            return [{"error": str(e)}]


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def print_rows(rows, empty_msg="(none)"):
    if not rows:
        print(f"  {empty_msg}")
        return
    for row in rows:
        if "error" in row:
            print(f"  ERROR: {row['error']}")
            return
        parts = []
        for k, v in row.items():
            parts.append(f"{k}={v!r}")
        print("  " + " | ".join(parts))


def main():
    parser = argparse.ArgumentParser(description="AIDB diagnostic helper")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", default="postgres")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    conn = connect(args)
    print(f"Connected to {args.host}:{args.port}/{args.dbname} as {args.user}")

    # 1. Extension check
    section("AIDB Extension Status")
    rows = query(conn, "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'aidb'")
    print_rows(rows, "AIDB extension not found in pg_available_extensions")

    # 2. Models
    section("Registered Models")
    rows = query(conn, "SELECT name, provider FROM aidb.list_models()")
    print_rows(rows, "No models registered")

    # 3. Pipelines
    section("Pipelines")
    rows = query(conn, "SELECT name, id, auto_processing FROM aidb.list_pipelines()")
    print_rows(rows, "No pipelines defined")

    # 4. Semantic KBs
    section("Semantic Knowledge Bases")
    rows = query(conn, "SELECT name, model, auto_processing FROM aidb.list_semantic_kbs()")
    print_rows(rows, "No semantic KBs defined")

    # 5. Agents
    section("Agents")
    rows = query(conn, "SELECT name, model, purpose FROM aidb.agents")
    print_rows(rows, "No agents defined")

    # 6. SQL Tools
    section("SQL Tools")
    rows = query(conn, "SELECT name, description, read_only FROM aidb.sql_tool_registry")
    print_rows(rows, "No SQL tools registered")

    # 7. MCP Registry
    section("MCP Servers")
    rows = query(conn, "SELECT name, url, transport FROM aidb.mcp_registry")
    print_rows(rows, "No MCP servers registered")

    # 8. Purpose Registry
    section("Purpose Registry")
    rows = query(conn, "SELECT name, role, deleted_at FROM aidb.purpose_registry")
    print_rows(rows, "No purposes defined")

    # 9. Key GUCs
    section("Key GUC Values")
    gucs = [
        "aidb.max_threads",
        "aidb.pipeline_error_warnings",
        "aidb.enable_memory_worker",
        "aidb.agent_session_source",
        "aidb.agent_memory_namespace",
        "aidb.otel_client",
        "aidb.egress_allowlist",
        "aidb.allow_insecure_egress",
        "aidb.allow_insecure_tls",
    ]
    for guc in gucs:
        rows = query(conn, "SELECT current_setting(%s, true) AS value", (guc,))
        val = rows[0].get("value", "N/A") if rows else "N/A"
        print(f"  {guc} = {val!r}")

    # 10. MCP endpoint GUCs
    section("MCP Endpoint GUCs")
    mcp_gucs = [
        "edb.endpoints_mcp_enabled",
        "edb.endpoints_mcp_host",
        "edb.endpoints_mcp_port",
    ]
    for guc in mcp_gucs:
        rows = query(conn, "SELECT current_setting(%s, true) AS value", (guc,))
        val = rows[0].get("value", "N/A") if rows else "N/A"
        print(f"  {guc} = {val!r}")

    # 11. Recent pipeline errors
    section("Recent Pipeline Errors (last 5 per pipeline)")
    pipeline_rows = query(conn, "SELECT name, id FROM aidb.list_pipelines()")
    if pipeline_rows and "error" not in pipeline_rows[0]:
        for p in pipeline_rows:
            pid = p.get("id")
            pname = p.get("name")
            if pid is None:
                continue
            err_table = f"aidb_internal.pipeline_error_log_{pid}"
            err_rows = query(
                conn,
                f"SELECT occurred_at, step_number, error_message FROM {err_table} ORDER BY occurred_at DESC LIMIT 5"
            )
            if err_rows and "error" not in err_rows[0]:
                print(f"\n  Pipeline '{pname}' (id={pid}) errors:")
                for er in err_rows:
                    print(f"    [{er.get('occurred_at')}] step={er.get('step_number')}: {er.get('error_message')}")
            elif err_rows and "error" in err_rows[0]:
                print(f"  Pipeline '{pname}': could not read error log — {err_rows[0]['error']}")
            else:
                print(f"  Pipeline '{pname}': no errors recorded")
    else:
        print("  No pipelines to check.")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
