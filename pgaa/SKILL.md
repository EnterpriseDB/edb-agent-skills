---
name: pgaa
description: >
  Skill for EDB Postgres Analytics Accelerator (pgaa) — a PostgreSQL extension
  (Rust/pgrx) that registers a custom Table Access Method to offload reads and
  writes against analytics tables to an external query engine (Seafowl or Spark
  Connect) over Apache Arrow Flight RPC. Trigger this skill when a user asks
  about: creating PGAA analytics tables, registering or syncing external Iceberg
  catalogs (iceberg-rest, iceberg-s3tables), replicating Postgres row changes to
  a lakehouse, running Spark SQL, diagnosing slow or failing PGAA queries,
  managing background maintenance tasks, or configuring pgaa GUCs. Also known as
  "EDB Postgres Analytics Accelerator", "beacon-analytics", or
  "postgres-analytics-accelerator". pgaa formally requires the pgfs extension for
  storage-location management.
metadata:
  aliases:
    - pgaa
    - beacon-analytics
    - postgres-analytics-accelerator
    - analytics-accelerator
  source_repo: https://github.com/EnterpriseDB/beacon-analytics.git
  skill_subdirectory: pgaa/
  depends_on: pgfs
---

# pgaa — EDB Postgres Analytics Accelerator

## What pgaa Is

**pgaa** ("EDB Postgres Analytics Accelerator") is a PostgreSQL extension that registers a custom **Table Access Method (TAM)**. Tables created `USING PGAA` store no data in ordinary Postgres heap pages; instead all reads and writes are routed over **Apache Arrow Flight RPC** to an external analytical query engine:

- **Seafowl** (default) — EDB's in-house DataFusion-based analytical engine; runs as a Postgres background worker or standalone process.
- **Spark Connect** — alternate engine for Spark clusters; selected via `pgaa.executor_engine = 'spark_connect'`.

pgaa ships from the `beacon-analytics` monorepo (alongside Seafowl and metastore-agent) and is installed via Debian/RPM packages for PG/EPAS/PGE 16, 17, 18, or via EDB Hybrid Manager (Lakehouse/PGD clusters).

> **Naming rule:** Always call it "EDB Postgres Analytics Accelerator" or "pgaa". Never "Postgres AI Analytics" or similar.

---

## Prerequisites

1. `pgfs` extension must be installed first — PGAA's `pgaa.storage_location` option references storage locations created via `pgfs.create_storage_location`. See the `pgfs` skill.
2. The offload engine (Seafowl or Spark Connect) must be running and reachable for PGAA tables to be queryable.

```sql
-- Install in order
CREATE EXTENSION IF NOT EXISTS pgfs;
CREATE EXTENSION IF NOT EXISTS pgaa;
```

---

## Core Concepts

| Concept | Description |
|---------|-------------|
| **Analytics Table** | A Postgres table `USING PGAA` whose data lives in object storage (Delta, Iceberg, or Parquet). |
| **DirectScan** | The entire SQL query is pushed to the engine; fastest path. |
| **CompatScan** | Postgres-compatible scan mode; partial pushdown (filters, joins, groupby, etc.). |
| **External Catalog** | An Iceberg REST or S3 Tables catalog whose schemas are synced into PGAA tables. |
| **Tiered Table** | A PGD-managed partitioned table whose older partitions are offloaded to analytics storage. |
| **Storage Location** | Object-store bucket/prefix defined via `pgfs`; referenced by name in PGAA table options. |

---

## 1. Creating Analytics Tables

```sql
-- Manual: create a PGAA table pointing at object storage
CREATE TABLE my_schema.my_analytics (
    event_id   BIGINT,
    event_ts   TIMESTAMPTZ,
    payload    JSONB
)
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-loc',   -- created via pgfs.create_storage_location
    pgaa.path             = 'analytics/my_analytics'
);

-- CTAS: create and populate from a query (runs on Postgres, then writes to engine)
CREATE TABLE my_schema.my_analytics
USING PGAA
WITH (pgaa.storage_location = 'my-s3-loc', pgaa.path = 'analytics/my_analytics')
AS (SELECT * FROM source_heap_table);
```

> **Note:** `pgaa.storage_location` must reference an existing pgfs storage location. Run `SELECT * FROM pgfs.list_storage_locations();` to see available locations.

---

## 2. Catalog Management (Iceberg REST & S3 Tables)

For the exact JSON options shape, see [assets/catalog-options-schema.md](assets/catalog-options-schema.md).

```sql
-- Step 1: Test BEFORE registering (safe, no side effects)
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://catalog.example.com", "warehouse": "prod", "token": "tok"}'::json
);
-- Returns NULL on success, error message on failure

-- Step 2: Register the catalog
SELECT pgaa.add_catalog(
    'my_catalog',
    'iceberg-rest',
    '{"url": "https://catalog.example.com", "warehouse": "prod", "token": "tok"}'::json
);

-- Step 3a: One-time import (no background worker needed)
SELECT pgaa.import_catalog('my_catalog');
-- With namespace filter:
SELECT pgaa.import_catalog('my_catalog', 'my_namespace');

-- Step 3b: Continuous sync (requires pgaa.enable_metastore_sync_worker = true)
SELECT pgaa.attach_catalog('my_catalog');

-- Inspect catalog status and managed tables
SELECT * FROM pgaa.list_catalogs();
SELECT * FROM pgaa.list_catalog_tables('my_catalog');
SELECT * FROM pgaa.list_analytics_tables();

-- Test an already-registered catalog
SELECT pgaa.test_catalog('my_catalog', false);  -- false = skip write test

-- Detach (stop sync, keep tables) — cascade drops tables first if true
SELECT pgaa.detach_catalog('my_catalog', cascade => false);

-- Delete (cascade required if tables exist)
SELECT pgaa.delete_catalog('my_catalog', cascade => true);
```

**Catalog types:**
- `'iceberg-rest'` — Lakekeeper, MinIO AIStor, Databricks Unity, AWS Glue, Snowflake Polaris
- `'iceberg-s3tables'` — AWS S3 Tables (native Iceberg catalog)

---

## 3. PGD Replication & Tiered Tables

These operations require the `bdr` extension (PGD cluster).

```sql
-- Enable analytics replication for a Postgres heap table
CALL pgaa.enable_analytics_replication('public.orders');

-- Convert to full PGAA access method (after replication is confirmed complete)
CALL pgaa.convert_to_analytics('public.orders');

-- Revert back to heap (non-destructive)
CALL pgaa.restore_from_analytics('public.orders');

-- Disable replication
CALL pgaa.disable_analytics_replication('public.orders');

-- Convert a plain table to a tiered table (requires PGD + bdr.autopartition)
CALL pgaa.convert_to_tiered_table(
    relation               => 'public.events',
    range_partition_column => 'event_ts',
    partition_increment    => '1 month',
    analytics_offload_period => '3 months',
    initial_lower_bound    => '2024-01-01',
    retention_period       => '12 months',
    enable_replication     => true
);

-- Inspect tiered tables
SELECT * FROM pgaa.list_tiered_tables();
```

---

## 4. Spark SQL

Only available when `pgaa.executor_engine = 'spark_connect'`.

```sql
-- Run arbitrary Spark SQL against a catalog
SELECT pgaa.spark_sql(
    'SELECT count(*) FROM my_catalog.my_namespace.my_table',
    'my_catalog'
);

-- Iceberg compaction via Spark (multi-catalog form)
SELECT pgaa.spark_sql(
    'CALL my_catalog.system.rewrite_data_files(table => ''my_namespace.my_table'', options => map(''rewrite-all'', ''true''))',
    ARRAY['my_catalog']
);
```

---

## 5. Introspection & Statistics

```sql
-- List all PGAA analytics tables + storage sizes + replication status
SELECT * FROM pgaa.list_analytics_tables();

-- Storage stats for a specific table (uses cache)
SELECT * FROM pgaa.lakehouse_table_stats('my_schema.my_analytics');

-- Storage stats bypassing cache
SELECT * FROM pgaa.lakehouse_table_stats_uncached('my_schema.my_analytics');

-- Version information
SELECT pgaa.pgaa_version();    -- PGAA build + git commit
SELECT pgaa.engine_version();  -- Current engine version (Seafowl or Spark)

-- Table-level settings
SELECT * FROM pgaa.get_all_analytics_table_settings();
```

---

## 6. Background Maintenance Tasks

```sql
-- Launch a compaction task
SELECT pgaa.launch_task('my_schema.my_analytics', 'compaction',
    '{"target_size": 134217728}'::jsonb  -- 128 MB target file size
);

-- Launch a vacuum task (Delta tables)
SELECT pgaa.launch_task('my_schema.my_analytics', 'vacuum',
    '{"retention_period": "7 days", "dry_run": false}'::jsonb
);

-- Check task status
SELECT id, type, target_table::text, status, started_at, finished_at
FROM pgaa.background_task
ORDER BY scheduled_at DESC LIMIT 20;

-- Iceberg compaction via Spark (convenience wrapper)
CALL pgaa.execute_compaction('my_schema.my_iceberg_table');
```

---

## 7. Key GUCs (Configuration)

See [references/guc-reference.md](references/guc-reference.md) for the full GUC reference.

**When troubleshooting performance**, check these first:

```sql
-- See all pgaa GUCs
SELECT name, setting FROM pg_settings WHERE name LIKE 'pgaa.%' ORDER BY name;

-- Engine selection
SHOW pgaa.executor_engine;          -- 'seafowl' (default) or 'spark_connect'

-- Pushdown controls (disable specific ones to isolate wrong-result bugs)
SHOW pgaa.enable_direct_scan;
SHOW pgaa.enable_join_pushdown;
SHOW pgaa.enable_groupby_pushdown;

-- DirectScan fail behavior (set to 'error' to diagnose why DirectScan is not used)
SET pgaa.direct_scan_fail_behavior = 'error';
```

---

## 8. Diagnostic Script

Run the automated diagnostics script to get a full status report:

```bash
python3 scripts/pgaa-diagnostics.py --connstr "host=localhost dbname=mydb user=postgres"
```

See [scripts/pgaa-diagnostics.py](scripts/pgaa-diagnostics.py). Requires `psycopg2-binary`.

---

## 9. Common Errors & Quick Fixes

| Symptom | First Check | Fix |
|---------|-------------|-----|
| Query error on PGAA table | `SELECT pgaa.engine_version()` | Start/configure Seafowl or Spark Connect |
| `spark_sql()` fails | `SHOW pgaa.executor_engine` | Set to `'spark_connect'` + configure `pgaa.spark_connect_url` |
| Slow queries | `SET pgaa.direct_scan_fail_behavior = 'error'` then retry | Fix DirectScan blocker or tune cost GUCs |
| Catalog tables not syncing | `SHOW pgaa.enable_metastore_sync_worker` | Enable worker or run `import_catalog()` manually |
| `delete_catalog` blocked | Tables still exist | Use `cascade => true` or call `drop_catalog_tables()` first |
| `VACUUM` warning on PGAA table | Expected behavior | Use `launch_task(..., 'vacuum')` or `execute_compaction()` instead |
| Missing `pgfs` dependency | `\dx pgfs` | `CREATE EXTENSION pgfs;` first |

For detailed troubleshooting steps, see [references/troubleshooting.md](references/troubleshooting.md).

---

## 10. Storage Authentication (AWS / GCP)

Object storage credentials are consumed from environment variables by both Seafowl and metastore-agent:

```bash
# AWS (EKS IRSA)
AWS_DEFAULT_REGION=us-east-1
AWS_REGION=us-east-1
AWS_ROLE_ARN=arn:aws:iam::123456789012:role/my-role
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token

# Temporary storage (performance critical — use NVMe-backed path)
SEAFOWL__RUNTIME__TEMP_DIR=/mnt/seafowl-temp
SEAFOWL__MISC__OBJECT_STORE_CACHE__CAPACITY=268435456  # 256 MB in bytes
```

---

## Reference Files

| File | Contents |
|------|----------|
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures and key tables |
| [references/guc-reference.md](references/guc-reference.md) | All pgaa GUC parameters, grouped by concern |
| [references/troubleshooting.md](references/troubleshooting.md) | Symptom-driven troubleshooting guide |
| [assets/catalog-options-schema.md](assets/catalog-options-schema.md) | Exact JSON shapes for catalog options + lifecycle diagram |
| [scripts/pgaa-diagnostics.py](scripts/pgaa-diagnostics.py) | Automated Python diagnostic script |

---

## Constraints & Safety Rules

1. **Never describe pgaa as "Postgres AI Analytics"** — the name is "EDB Postgres Analytics Accelerator".
2. **Storage locations belong to pgfs** — do not invent `pgaa.create_storage_location`; use `pgfs.create_storage_location` instead.
3. **`cascade => false` is the safe default** — `delete_catalog`/`detach_catalog` without `cascade` will not drop any tables. Always confirm with the user before using `cascade => true`.
4. **`pgaa.spark_sql()` requires `executor_engine = 'spark_connect'`** — it will fail on the default Seafowl engine.
5. **The engine must be running** — PGAA tables are not queryable if Seafowl/Spark is down. Check with `SELECT pgaa.engine_version()`.
6. **Catalog type values are exactly two strings**: `'iceberg-rest'` and `'iceberg-s3tables'`.
7. **Minimum upgrade path**: Users on versions before 1.3 must upgrade to 1.3.1 before upgrading to 1.6.0+.
8. **Version caution**: State versions as "as of the current beacon-analytics source" rather than asserting a specific release, since workspace versions may be pre-release.