---
name: pgaa
description: >
  Skill for EDB Postgres Analytics Accelerator (pgaa) — a PostgreSQL extension that registers a
  custom Table Access Method routing reads/writes on analytics tables to an external offload engine
  (Seafowl by default, or Spark Connect) over Apache Arrow Flight RPC. Trigger this skill when a
  user needs to: create or query USING PGAA analytics tables; register or sync external Iceberg
  catalogs (Iceberg REST, AWS S3 Tables); configure or troubleshoot the Seafowl or Spark Connect
  executor engine; manage PGD logical-replication or tiered-table offload pipelines; run Spark SQL
  via pgaa.spark_sql(); inspect lakehouse table statistics or background maintenance tasks; or
  install/configure the pgaa extension on EDB Hybrid Manager, PGD clusters, or on-prem
  Debian/RPM deployments. pgaa depends on the pgfs extension for storage-location definitions;
  use the pgfs skill for storage-location creation.
metadata:
  aliases:
    - pgaa
    - beacon-analytics
    - postgres-analytics-accelerator
    - analytics-accelerator
  source_repo: https://github.com/EnterpriseDB/beacon-analytics.git
  subdirectory: pgaa/
  requires_extension: pgfs
  pg_versions: ["16", "17", "18"]
  flavors: ["community", "EPAS", "PGE", "WarehousePG"]
---

# EDB Postgres Analytics Accelerator (pgaa) — Agent Skill

## What pgaa Is

**pgaa** ("Postgres Analytics Accelerator") is a PostgreSQL extension (Rust/pgrx) that ships from
the `beacon-analytics` monorepo. It registers a custom **Table Access Method** so that tables
created `USING PGAA` store no data in ordinary Postgres heap pages — all reads and writes are
offloaded to an external analytical query engine over **Apache Arrow Flight RPC**.

> **Name note:** The full name is "EDB Postgres Analytics Accelerator". Do **not** call it "Postgres
> AI Analytics" or any other variation. The source package name is `pgaa`; the monorepo codename
> is `beacon-analytics`.

### Two Executor Engines (selected by `pgaa.executor_engine`)

| Engine | GUC value | Notes |
|--------|-----------|-------|
| **Seafowl** | `'seafowl'` (default) | In-house DataFusion-based fork; runs as a Postgres background worker or external process |
| **Spark Connect** | `'spark_connect'` | External Spark cluster; enables `pgaa.spark_sql()` |

### Hard Dependencies

- **pgfs extension** — `pgaa.storage_location` in `CREATE TABLE ... USING PGAA WITH (...)` refers
  to a storage location created via `pgfs.create_storage_location`. pgaa does not create storage
  locations itself. Use the `pgfs` skill for that surface.
- The offload engine must be running and reachable for PGAA tables to be queryable.

---

## Core Capabilities (Quick Map)

| User intent | Primary SQL entry point |
|-------------|------------------------|
| Create an analytics table | `CREATE TABLE ... USING PGAA WITH (...)` |
| Register an Iceberg catalog | `pgaa.add_catalog()` |
| One-time catalog import | `pgaa.import_catalog()` |
| Continuous catalog sync | `pgaa.attach_catalog()` / `pgaa.detach_catalog()` |
| Run Spark SQL directly | `pgaa.spark_sql()` (requires `executor_engine = 'spark_connect'`) |
| Enable PGD replication offload | `pgaa.enable_analytics_replication()` |
| Convert table to tiered | `pgaa.convert_to_tiered_table()` |
| Inspect analytics tables | `pgaa.list_analytics_tables()` |
| Storage / catalog connectivity test | `pgaa.test_storage_location()` / `pgaa.test_catalog()` |
| Schedule maintenance task | `pgaa.launch_task()` |
| Check engine/build version | `pgaa.engine_version()` / `pgaa.pgaa_version()` |

---

## Step-by-Step Usage Patterns

### 1 — Create an Analytics Table

```sql
-- Prerequisite: a pgfs storage location must already exist (see pgfs skill)
CREATE TABLE my_schema.sales_analytics (
    sale_id    BIGINT,
    sale_date  DATE,
    amount     NUMERIC(12,2),
    region     TEXT
)
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-location',   -- name of a pgfs storage location
    pgaa.path             = 'analytics/sales'
);
```

**CTAS variant:**
```sql
CREATE TABLE my_schema.sales_snap
USING PGAA
WITH (pgaa.storage_location = 'my-s3-location', pgaa.path = 'analytics/sales_snap')
AS (SELECT * FROM my_schema.sales WHERE sale_date >= '2024-01-01');
```

> Direct `INSERT` into PGAA tables is not supported and returns a clear error.
> Data enters PGAA tables via CTAS, PGD replication, or catalog sync.

---

### 2 — Register and Use an External Catalog

#### Pre-validate before registering
```sql
-- Returns NULL on success, error text on failure
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://my-catalog.example.com", "token": "my-token"}'
);
```

#### Register an Iceberg REST catalog
```sql
SELECT pgaa.add_catalog(
    'my-catalog',
    'iceberg-rest',
    '{"url": "https://my-catalog.example.com", "warehouse": "my_wh", "token": "my-token"}'
);
```

#### Register an AWS S3 Tables catalog
```sql
SELECT pgaa.add_catalog(
    'my-s3tables',
    'iceberg-s3tables',
    '{"arn": "arn:aws:s3tables:us-east-1:123456789012:bucket/my-bucket", "region": "us-east-1"}'
);
```

#### One-time import
```sql
SELECT pgaa.import_catalog('my-catalog');
-- With namespace filter:
SELECT pgaa.import_catalog('my-catalog', 'my_namespace');
```

#### Continuous sync (background worker must be enabled)
```sql
SELECT pgaa.attach_catalog('my-catalog');

-- Check sync status:
SELECT name, status, refreshed_at FROM pgaa.list_catalogs();

-- Stop continuous sync (tables remain unless cascade := true):
SELECT pgaa.detach_catalog('my-catalog');
SELECT pgaa.detach_catalog('my-catalog', cascade := true);  -- also drops managed tables
```

#### List tables in a catalog (without importing)
```sql
SELECT * FROM pgaa.list_catalog_tables('my-catalog');
SELECT * FROM pgaa.list_catalog_tables('my-catalog', 'my_namespace');
```

#### Delete a catalog
```sql
-- Fails if tables exist — intentional safety guard:
SELECT pgaa.delete_catalog('my-catalog');

-- Drop managed tables and delete:
SELECT pgaa.delete_catalog('my-catalog', cascade := true);
```

> **Safety:** `cascade` defaults to `false` on both `detach_catalog` and `delete_catalog`.
> Always confirm before passing `cascade := true`.

---

### 3 — Run Spark SQL

```sql
-- Requires: pgaa.executor_engine = 'spark_connect'
SELECT pgaa.spark_sql(
    'SELECT count(*) FROM my_catalog.my_ns.my_table',
    'my-catalog'
);

-- Multi-catalog:
SELECT pgaa.spark_sql(
    'SELECT ...',
    ARRAY['catalog-a', 'catalog-b']
);
```

---

### 4 — PGD Replication Offload

```sql
-- Enable analytics replication for a heap table:
CALL pgaa.enable_analytics_replication('my_schema.my_table');

-- Convert to a tiered (partitioned + offloaded) table:
CALL pgaa.convert_to_tiered_table(
    relation                => 'my_schema.my_table',
    range_partition_column  => 'created_at',
    partition_increment     => '1 month',
    analytics_offload_period => '3 months',
    initial_lower_bound     => '2024-01-01',
    retention_period        => '1 year',
    enable_replication      => true
);

-- Inspect tiered tables:
SELECT * FROM pgaa.list_tiered_tables();
```

---

### 5 — Inspect Tables and Engine

```sql
-- All analytics tables with sizes and replication status:
SELECT * FROM pgaa.list_analytics_tables();

-- Storage statistics for a single table (cached):
SELECT * FROM pgaa.lakehouse_table_stats('my_schema.my_table'::regclass);

-- Uncached:
SELECT * FROM pgaa.lakehouse_table_stats_uncached('my_schema.my_table'::regclass);

-- Engine version:
SELECT pgaa.engine_version();
SELECT pgaa.pgaa_version();
```

---

### 6 — Schedule Maintenance Tasks (Delta tables)

```sql
-- Compaction:
SELECT pgaa.launch_task(
    'my_schema.my_table'::regclass,
    'compaction',
    '{"target_size": 268435456}'::jsonb
);

-- Z-order:
SELECT pgaa.launch_task(
    'my_schema.my_table'::regclass,
    'zorder',
    '{"columns": ["col_a", "col_b"]}'::jsonb
);

-- Check status:
SELECT id, type, status, output FROM pgaa.background_task
WHERE target_table = 'my_schema.my_table'::regclass
ORDER BY scheduled_at DESC;

-- Wait for task:
SELECT pgaa._wait_for_task('<uuid>');
```

For Iceberg compaction (requires Spark Connect):
```sql
CALL pgaa.execute_compaction('my_schema.my_table'::regclass, '{"target_size": 268435456}');
```

---

## Key Constraints and Safety Rules

1. **pgfs required.** `pgaa.storage_location` in `WITH (...)` must name an existing pgfs location.
2. **Engine must be running.** PGAA tables are unqueryable if Seafowl/Spark Connect is down.
3. **`spark_sql()` only with `spark_connect` engine.** Calling it against Seafowl will error.
4. **`cascade` defaults to `false`** on `detach_catalog` and `delete_catalog`. This is intentional.
5. **Catalog types are exactly:** `'iceberg-rest'` or `'iceberg-s3tables'`. No others.
6. **No direct INSERT.** Data enters PGAA tables via CTAS, PGD replication, or catalog sync only.
7. **VACUUM** on PGAA tables emits a NOTICE and skips — this is correct behavior, not an error.
8. **Version caution.** The source workspace version may be ahead of the latest tagged release.
   Prefer "as of the current beacon-analytics source" over asserting a specific release number.
9. **WarehousePG limitations.** CTAS and maintenance/metastore background workers (including
   `attach_catalog`) are not supported on WarehousePG. Queries with PGAA tables cannot use
   the Orca optimizer on WarehousePG.

---

## Troubleshooting Quick Reference

| Symptom | First check |
|---------|-------------|
| Slow analytical queries | `pgaa.enable_direct_scan`, pushdown GUCs, `pgaa.direct_scan_fail_behavior` |
| DirectScan not activating | All tables same catalog? Schema-qualified? Set `direct_scan_fail_behavior = 'error'` |
| Engine unreachable | `SELECT pgaa.engine_version();` — check Seafowl/Spark process, logs |
| Catalog not refreshing | `SELECT name, status FROM pgaa.list_catalogs();` — `refresh_failed` = sync error |
| Storage location not found | `SELECT pgaa.test_storage_location('name', true);` |
| `delete_catalog` fails | Use `cascade := true` or drop tables first with `pgaa.drop_catalog_tables()` |
| `spark_sql` errors | Verify `pgaa.executor_engine = 'spark_connect'` and `pgaa.spark_connect_url` |
| INSERT into PGAA table fails | Expected — use CTAS, replication, or catalog sync instead |

See [references/troubleshooting.md](references/troubleshooting.md) for detailed diagnosis steps.

---

## Configuration (GUC) Summary

Key GUC groups (confirm full current list from source):

- **Engine:** `pgaa.executor_engine`, `pgaa.seafowl_url`, `pgaa.spark_connect_url`
- **Embedded Seafowl:** `pgaa.autostart_seafowl_*` family (port, path, memory, metrics)
- **Workers:** `pgaa.enable_maintenance_worker`, `pgaa.enable_metastore_sync_worker`, `pgaa.metastore_sync_poll_rate_s`
- **Pushdown:** `pgaa.enable_direct_scan`, `enable_join_pushdown`, `enable_groupby_pushdown`, `enable_orderby_pushdown`, `enable_distinct_pushdown`, `enable_window_pushdown`
- **Cost:** `pgaa.scan_startup_cost`, `pgaa.scan_per_tuple_cost`, `pgaa.use_seafowl_cost_estimates`
- **Stats/cache:** `pgaa.enable_metadata_stats`, `pgaa.lakehouse_table_stats_cache_ttl_s`
- **Replication:** `pgaa.max_replication_lag_s`, `pgaa.flush_task_interval_s`

See [references/guc-reference.md](references/guc-reference.md) for the full parameter table.

---

## Key Internal Tables

| Table | Purpose |
|-------|---------|
| `pgaa.catalog` | Registered external catalogs |
| `pgaa.table_mapping` | Maps Postgres tables to their lakehouse storage location |
| `pgaa.tiered_table` | Tables under tiering/retention management |
| `pgaa.background_task` | Task queue for `pgaa.launch_task()` |
| `pgaa.lakehouse_table_stats_cache` | Cached table statistics (TTL: `pgaa.lakehouse_table_stats_cache_ttl_s`) |

---

## Ecosystem Context

`pgaa` ships from the `beacon-analytics` monorepo alongside:
- **Seafowl** — the default offload engine (DataFusion-based); included in the pgaa Debian/RPM package since 1.3.0
- **metastore-agent** — background worker for external catalog/schema sync
- **pgfs** — separate extension (required dependency) for object-storage location definitions

Deployment targets: **EDB Hybrid Manager** (Lakehouse/PGD clusters), **on-prem** (Debian/RPM for PG/EPAS/PGE 16–18, WarehousePG 7 preview).

---

## Reference Files

| File | Content |
|------|---------|
| [references/function-reference.md](references/function-reference.md) | Full SQL function signatures with parameters and return types |
| [references/guc-reference.md](references/guc-reference.md) | Complete GUC parameter table with descriptions |
| [references/troubleshooting.md](references/troubleshooting.md) | Detailed diagnosis steps for common failure scenarios |
| [assets/catalog-options-reference.md](assets/catalog-options-reference.md) | JSON option schemas for catalogs and task types |
| [scripts/pgaa_diagnostics.py](scripts/pgaa_diagnostics.py) | Runnable Python script — collects a full diagnostic snapshot from a live database |