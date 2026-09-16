#!/usr/bin/env python3
"""
pgfs_check.py — Diagnostic script for pgfs PostgreSQL extension.

Usage (run inside psql or via a DB connection tool):
  The SQL statements below can be run directly in psql.
  This script generates diagnostic SQL that the agent can execute.

Run with: python3 scripts/pgfs_check.py [--storage-location NAME]
Output: SQL commands to paste into psql / run via the agent's SQL execution tool.
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Generate diagnostic SQL for the pgfs extension."
    )
    parser.add_argument(
        "--storage-location",
        metavar="NAME",
        help="Specific storage location name to inspect (optional)",
    )
    args = parser.parse_args()

    lines = []

    lines.append("-- ============================================================")
    lines.append("-- pgfs Diagnostic SQL")
    lines.append("-- Run these statements in psql or your SQL execution tool.")
    lines.append("-- ============================================================")
    lines.append("")

    # 1. Check extension installed
    lines.append("-- 1. Verify pgfs extension is installed")
    lines.append(
        "SELECT name, default_version, installed_version "
        "FROM pg_available_extensions WHERE name = 'pgfs';"
    )
    lines.append("")

    # 2. Check PostgreSQL version
    lines.append("-- 2. Check PostgreSQL version (PG14+ required for full FDW)")
    lines.append("SELECT version();")
    lines.append("")

    # 3. List all storage locations
    lines.append("-- 3. List all storage locations (credentials masked)")
    lines.append("SELECT name, url, options FROM pgfs.list_storage_locations();")
    lines.append("")

    # 4. Optionally inspect a specific storage location
    if args.storage_location:
        sl = args.storage_location.replace("'", "''")
        lines.append(f"-- 4. Inspect storage location '{sl}' (credentials shown)")
        lines.append(f"SELECT * FROM pgfs.get_storage_location('{sl}');")
        lines.append("")
        lines.append(f"-- 4b. Foreign tables for storage location '{sl}'")
        lines.append(
            f"SELECT schema_name, ft_name FROM pgfs.v_foreign_tables WHERE server_name = '{sl}';"
        )
        lines.append("")

    # 5. List all foreign tables
    lines.append("-- 5. All pgfs foreign tables")
    lines.append("SELECT schema_name, server_name, ft_name FROM pgfs.v_foreign_tables;")
    lines.append("")

    # 6. Default storage location
    lines.append("-- 6. Default storage location")
    lines.append("SELECT * FROM pgfs.get_default_storage_location();")
    lines.append("")

    # 7. GUC settings
    lines.append("-- 7. pgfs GUC settings")
    lines.append("SHOW pgfs.allowed_local_fs_paths;")
    lines.append("")

    # 8. FDW registration
    lines.append("-- 8. Verify pgfs_fdw is registered")
    lines.append("SELECT fdwname FROM pg_foreign_data_wrapper WHERE fdwname = 'pgfs_fdw';")
    lines.append("")

    # 9. pgfs version
    lines.append("-- 9. pgfs version details")
    lines.append("SELECT pgfs.pgfs_version();")
    lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
