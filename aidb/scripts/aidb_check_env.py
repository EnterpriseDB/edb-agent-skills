#!/usr/bin/env python3
"""
aidb_check_env.py — Pre-flight environment checker for AIDB.

Validates that the shared_preload_libraries, extension installation,
GUC settings, and key dependencies are correctly configured before
attempting to use AIDB pipelines or agents.

Usage:
    python3 scripts/aidb_check_env.py --dsn "postgresql://user:pass@host/db"

Requirements:
    pip install psycopg2-binary

Exit codes:
    0 — All required checks passed
    1 — One or more warnings (non-fatal)
    2 — Critical failure (AIDB cannot function)
"""

import sys
import argparse

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(2)

REQUIRED_EXTENSIONS = ["aidb", "pgvector"]
RECOMMENDED_EXTENSIONS = ["pgfs"]
REQUIRED_PRELOAD = ["aidb"]
RECOMMENDED_PRELOAD = ["vchord"]


def connect(dsn: str):
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"CRITICAL: Cannot connect — {e}")
        sys.exit(2)


def check_preload_libraries(conn) -> tuple[bool, bool]:
    """Check shared_preload_libraries for required and recommended entries."""
    with conn.cursor() as cur:
        cur.execute("SHOW shared_preload_libraries;")
        val = cur.fetchone()[0]
        libs = [lib.strip() for lib in val.split(",")]

    required_ok = True
    for lib in REQUIRED_PRELOAD:
        if lib in libs:
            print(f"OK:   shared_preload_libraries includes '{lib}'")
        else:
            print(f"FAIL: shared_preload_libraries missing '{lib}'")
            print(f"      Add '{lib}' to shared_preload_libraries and restart PostgreSQL")
            required_ok = False

    for lib in RECOMMENDED_PRELOAD:
        if lib in libs:
            print(f"OK:   shared_preload_libraries includes '{lib}' (recommended)")
        else:
            print(f"WARN: shared_preload_libraries missing '{lib}' (needed for VectorChord indexes)")

    return required_ok, True


def check_extensions(conn) -> bool:
    """Check that required extensions are installed."""
    with conn.cursor() as cur:
        cur.execute("SELECT extname, extversion FROM pg_extension;")
        installed = {row[0]: row[1] for row in cur.fetchall()}

    all_ok = True
    for ext in REQUIRED_EXTENSIONS:
        if ext in installed:
            print(f"OK:   Extension '{ext}' installed (version {installed[ext]})")
        else:
            print(f"FAIL: Extension '{ext}' not installed")
            print(f"      Run: CREATE EXTENSION {ext} CASCADE;")
            all_ok = False

    for ext in RECOMMENDED_EXTENSIONS:
        if ext in installed:
            print(f"OK:   Extension '{ext}' installed (version {installed[ext]}) (recommended)")
        else:
            print(f"WARN: Extension '{ext}' not installed (needed for volume/object storage support)")

    return all_ok


def check_pg_version(conn) -> bool:
    """Verify PostgreSQL version is supported (14–18)."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('server_version_num')::integer;")
        ver_num = cur.fetchone()[0]

    major = ver_num // 10000
    if 14 <= major <= 18:
        print(f"OK:   PostgreSQL major version {major} is supported (14–18)")
        return True
    else:
        print(f"FAIL: PostgreSQL major version {major} is not supported by AIDB (requires 14–18)")
        return False


def check_gucs(conn):
    """Report on AIDB GUC values."""
    gucs = {
        "aidb.max_threads": "Thread pool for local model inference (restart required to change)",
        "aidb.pipeline_error_warnings": "Whether pipeline errors are emitted as PostgreSQL WARNINGs",
    }
    print("\nAIDB GUC Parameters:")
    with conn.cursor() as cur:
        for guc, desc in gucs.items():
            try:
                cur.execute(f"SHOW {guc};")
                val = cur.fetchone()[0]
                print(f"  {guc} = {val}")
                print(f"    ({desc})")
            except Exception:
                print(f"  {guc} = (not available — is AIDB installed?)")


def check_bgworker(conn) -> bool:
    """Check if AIDB background worker is running."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pid, backend_type, state
            FROM pg_stat_activity
            WHERE backend_type LIKE '%aidb%'
               OR backend_type LIKE '%background worker%'
            LIMIT 10;
        """)
        workers = cur.fetchall()

    if workers:
        print(f"OK:   Found {len(workers)} background worker(s) in pg_stat_activity")
        for w in workers:
            print(f"      PID={w['pid']} type={w['backend_type']} state={w['state']}")
    else:
        print("WARN: No AIDB background workers found in pg_stat_activity")
        print("      Background pipelines will not process until a worker starts.")
        print("      Verify shared_preload_libraries includes 'aidb' and restart PostgreSQL.")
    return True


def check_aidb_schema(conn) -> bool:
    """Verify key aidb schema objects are present."""
    checks = [
        ("aidb", "aidb_pipeline_registry", "table"),
        ("aidb", "model_registry", "table"),
    ]
    all_ok = True
    with conn.cursor() as cur:
        for schema, name, kind in checks:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, name)
            )
            if cur.fetchone():
                print(f"OK:   {schema}.{name} ({kind}) exists")
            else:
                print(f"WARN: {schema}.{name} ({kind}) not found — may be expected on older installs")
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="AIDB pre-flight environment checker")
    parser.add_argument(
        "--dsn",
        default="postgresql://localhost/postgres",
        help="PostgreSQL DSN (default: postgresql://localhost/postgres)",
    )
    args = parser.parse_args()

    print("=" * 62)
    print("AIDB Pre-Flight Environment Check")
    print("=" * 62)
    print(f"DSN: {args.dsn}\n")

    conn = connect(args.dsn)
    critical_ok = True

    print("--- PostgreSQL Version ---")
    if not check_pg_version(conn):
        critical_ok = False

    print("\n--- Shared Preload Libraries ---")
    preload_ok, _ = check_preload_libraries(conn)
    if not preload_ok:
        critical_ok = False

    print("\n--- Installed Extensions ---")
    if not check_extensions(conn):
        critical_ok = False

    print("\n--- AIDB Schema Objects ---")
    check_aidb_schema(conn)

    print("\n--- Background Workers ---")
    check_bgworker(conn)

    check_gucs(conn)

    conn.close()

    print("\n" + "=" * 62)
    if critical_ok:
        print("Result: Pre-flight checks PASSED — AIDB environment looks healthy")
        sys.exit(0)
    else:
        print("Result: Pre-flight checks FAILED — fix the FAIL items above before using AIDB")
        sys.exit(2)


if __name__ == "__main__":
    main()
