---
name: pgaa
description: >
  Skill for operating EDB Postgres Analytics Accelerator (pgaa) — a PostgreSQL
  extension that registers a custom Table Access Method (TAM) to offload reads
  and writes to an external analytical engine, enabling direct querying of
  Apache Iceberg, Delta Lake, and Parquet tables from PostgreSQL. Trigger this
  skill whenever a user needs to install, configure, create, query, maintain,
  monitor, or troubleshoot pgaa tables or catalogs; manage pgfs storage
  locations; set up PGD-to-Iceberg replication; configure tiered tables; run
  background maintenance tasks (compaction, vacuum, zorder, purge); diagnose
  scan-mode or performance issues; or work with pgaa in any of its three
  deployment contexts: standalone Lakehouse Cluster, WarehousePG (WHPG)
  distributed MPP, or PGD (HTAP) cluster.
metadata:
  aliases:
    - pgaa
    - beacon-analytics
    - postgres-analytics-accelerator
    - analytics-accelerator
  requires:
    - PostgreSQL 16, 17, or 18
    - pgfs extension (auto-installed via CASCADE)
  deployment_contexts:
    - Lakehouse Cluster (standalone)
    - WarehousePG (WHPG) — distributed MPP
    - PGD Cluster — HTAP transactional+analytics
---

# pgaa — EDB Postgres Analytics Accelerator

`pgaa` is a PostgreSQL extension that implements a **Table Access Method (TAM)**,
routing reads and writes for designated tables to an external analytical engine
rather than Postgres heap storage. It enables querying Apache Iceberg, Delta
Lake, and Parquet data directly from SQL. It requires PostgreSQL 16–18 and
depends on `pgfs` (auto-installed).

---

## 1. Installation

```sql
-- Auto-installs the required pgfs dependency
CREATE EXTENSION pgaa CASCADE;
```

**WarehousePG (WHPG) only:** `pgaa` must also appear in `postgresql.conf` on
the coordinator *and every segment host* before the cluster starts:

```
shared_preload_libraries = 'pgaa'
```

Restart the cluster after editing this file.

---

## 2. Verify the Installation (Smoke Test)

Use the public TPC-H SF1 sample bucket (no credentials needed):

```sql
CREATE SCHEMA IF NOT EXISTS smoke_test;

SELECT pgfs.create_storage_location(
    'smoke-test-data',
    's3://beacon-analytics-demo-data-us-east-1-prod',
    '{"aws_skip_signature": "true"}'
);

-- Empty column list () → pgaa auto-discovers schema
CREATE TABLE smoke_test.customer () USING PGAA WITH (
    pgaa.storage_location = 'smoke-test-data',
    pgaa.path             = 'tpch_sf_1/customer'
);

SELECT COUNT(*) FROM smoke_test.customer;  -- expected: 150000

-- Confirm query offload
EXPLAIN SELECT COUNT(*) FROM smoke_test.customer;
-- Lakehouse/PGD: "SeafowlDirectScan"
-- WarehousePG:   "SeafowlCompatScan" (expected — Greenplum intercepts)

-- Cleanup (table before storage location)
DROP TABLE smoke_test.customer CASCADE;
DROP SCHEMA smoke_test CASCADE;
SELECT pgfs.delete_storage_location('smoke-test-data');
```

Run `scripts/pgaa_smoke_test.py` for an automated version of this test.

---

## 3. Creating pgaa Tables

Two patterns exist. Choose based on your data format and infrastructure.

### Pattern 1 — Catalog-Managed (Recommended for Iceberg)

Register a catalog once, then point tables at it. Schema is auto-discovered
from the catalog when the column list is empty.

```sql
-- Step 1: Register an Iceberg REST catalog
SELECT pgaa.add_catalog(
    'my_catalog',
    'iceberg-rest',
    '{
        "url":       "https://your-catalog-host/catalog",
        "warehouse": "your-warehouse-uuid",
        "token":     "your-bearer-token"
    }'
);

-- Step 2: Create table (empty columns = auto-discover schema)
CREATE TABLE analytics.events () USING PGAA WITH (
    pgaa.managed_by        = 'my_catalog',
    pgaa.catalog_namespace = 'production',
    pgaa.catalog_table     = 'events',
    pgaa.format            = 'iceberg'
);

-- Query normally
SELECT event_type, COUNT(*)
FROM analytics.events
WHERE event_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY event_type;
```

See [assets/quick-reference.md](assets/quick-reference.md) for full catalog
option JSON schemas (REST and S3 Tables).

### Pattern 2 — Direct Storage Location (Delta / Parquet / Quick Tests)

```sql
-- Step 1: Create a pgfs storage location
SELECT pgfs.create_storage_location(
    'my-delta-store',
    's3://my-analytics-bucket/data/',
    NULL,
    '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);

-- Step 2: Create pgaa table
CREATE TABLE analytics.orders () USING PGAA WITH (
    pgaa.storage_location = 'my-delta-store',
    pgaa.path             = 'orders/',
    pgaa.format           = 'delta'       -- or 'parquet' (read-only)
);
```

See [references/pgfs-guide.md](references/pgfs-guide.md) for full pgfs
storage location management.

### CTAS — Write Query Results to a pgaa Table

```sql
CREATE TABLE analytics.summary USING PGAA WITH (
    pgaa.managed_by        = 'my_catalog',
    pgaa.catalog_namespace = 'production',
    pgaa.catalog_table     = 'summary',
    pgaa.format            = 'iceberg',
    pgaa.purge_data_if_exists = true    -- required if data already exists
) AS SELECT region, SUM(amount) FROM orders GROUP BY region;
```

⚠ CTAS source queries **always use CompatScan** (never DirectScan) — this is
expected behavior, not an error.

### Convert an Existing Heap Table In-Place

```sql
-- To pgaa (catalog-managed)
ALTER TABLE public.my_table
    SET ACCESS METHOD pgaa,
    SET (
        pgaa.auto_truncate     = 'true',
        pgaa.format            = 'iceberg',
        pgaa.managed_by        = 'my_catalog',
        pgaa.catalog_namespace = 'public',
        pgaa.catalog_table     = 'my_table'
    );

-- Back to heap
ALTER TABLE public.my_table SET ACCESS METHOD heap;
```

---

## 4. Execution Engines

### Seafowl (Default)

Embedded DataFusion-based engine. Starts automatically as a background process.

```sql
SET pgaa.executor_engine = 'seafowl';      -- default; usually no change needed
SHOW pgaa.autostart_seafowl;               -- should be 'on'
SHOW pgaa.seafowl_url;                     -- default: http://localhost:47470
```

**Scan modes:**
- **DirectScan** — full query offloaded to Seafowl via Arrow Flight (fast).
- **CompatScan** — falls back to PostgreSQL executor (slower; always used for
  CTAS source; normal on WarehousePG).

```sql
SET pgaa.enable_direct_scan = on;              -- default
SET pgaa.direct_scan_fail_behavior = 'warn';   -- 'ignore' | 'warn' | 'error'
SET pgaa.enable_join_pushdown   = on;          -- default
SET pgaa.enable_groupby_pushdown = on;         -- default
```

### Spark Connect (Heavy Workloads)

External Spark cluster. **Read-only** — all writes must go through PostgreSQL.

```sql
SET pgaa.executor_engine    = 'spark_connect';
SET pgaa.spark_connect_url  = 'sc://spark-host:15002';

-- Run raw Spark SQL (single catalog)
SELECT pgaa.spark_sql('SELECT COUNT(*) FROM ns.events', 'my_catalog');

-- Run Spark SQL against multiple catalogs
SELECT pgaa.spark_sql('SELECT * FROM c1.ns.a JOIN c2.ns.b ...', ARRAY['c1', 'c2']);
```

---

## 5. Catalog Management

```sql
-- Add a catalog (validates connection before registering)
SELECT pgaa.add_catalog('name', 'iceberg-rest', '{...}'::json);
SELECT pgaa.add_catalog('name', 'iceberg-s3tables', '{"arn":"...","region":"..."}'::json);

-- Update credentials/options
SELECT pgaa.update_catalog('name', '{"token":"new-token"}'::json);

-- List and inspect
SELECT * FROM pgaa.list_catalogs();
SELECT * FROM pgaa.list_catalog_tables('my_catalog');

-- One-time import of all tables from a catalog namespace
SELECT pgaa.import_catalog('my_catalog', 'production');

-- Continuous background sync (attach/detach)
SELECT pgaa.attach_catalog('my_catalog');
SELECT pgaa.detach_catalog('my_catalog', cascade := false);

-- Delete (cascade := true also drops managed tables)
SELECT pgaa.delete_catalog('my_catalog', cascade := false);

-- Test connectivity (returns NULL on success, error string on failure)
SELECT pgaa.test_catalog('my_catalog', false);
SELECT pgaa.validate_catalog_connection('iceberg-rest', '{...}'::json);
```

**S3 Tables limitation:** Iceberg S3 Tables catalogs do **not** support PGD
replication, tiered tables, or analytics offload. Use an Iceberg REST catalog
(e.g., Lakekeeper) for those features.

---

## 6. PGD Integration (HTAP)

On a PGD cluster, pgaa enables automatic replication of transactional writes
from heap tables to Iceberg.

```sql
-- At table creation
CREATE TABLE app.orders (
    order_id   SERIAL PRIMARY KEY,
    amount     DECIMAL(10,2),
    created_at TIMESTAMPTZ DEFAULT now()
) WITH (pgd.replicate_to_analytics = true);

-- Enable on an existing table (triggers FULL RELOAD — see gotcha below)
CALL pgaa.enable_analytics_replication('app.orders'::regclass);
-- OR: ALTER TABLE app.orders SET (pgd.replicate_to_analytics = true);

-- Disable
CALL pgaa.disable_analytics_replication('app.orders'::regclass);

-- Wait for replication to catch up (ONLY after all writes have stopped)
SELECT bdr.wait_slot_confirm_lsn(bdr.local_analytics_slot_name(), NULL);

-- Query via the analytics engine instead of heap
SET bdr.prefer_analytics_engine = true;
SELECT * FROM app.orders WHERE created_at > '2024-01-01';

-- Monitor replication state
SELECT schema_name, table_name, replication_status
FROM pgaa.list_analytics_tables();
-- replication_status: 'disabled' | 'initial_offload' | 'enabled'

SELECT * FROM bdr.analytics_table;
```

⚠ **Full-reload gotcha:** Re-enabling replication removes all existing
analytics data and re-uploads the current local table. This is intentional
but destructive — warn users before triggering on large tables.

⚠ **`bdr.wait_slot_confirm_lsn()` blocks indefinitely** if writes are still
ongoing. Only call it after all writes to the table have stopped.

---

## 7. Tiered Tables

Tiered tables automatically age partitions from hot local heap storage into cold
pgaa/Iceberg storage based on a configurable time threshold.

**Prerequisites:** partition column must be DATE/TIMESTAMP and part of the
primary key; no sequences or foreign keys on the table.

```sql
-- Create a suitable base table first
CREATE TABLE app.events (
    event_id   BIGSERIAL,
    event_date DATE NOT NULL,
    event_type TEXT,
    PRIMARY KEY (event_id, event_date)   -- date must be in PK
);

-- Convert to tiered
CALL pgaa.convert_to_tiered_table(
    relation                 := 'app.events'::regclass,
    range_partition_column   := 'event_date',
    partition_increment      := '1 month',
    analytics_offload_period := '3 months',   -- data older than 3mo → cold
    initial_lower_bound      := '2024-01-01',
    retention_period         := '2 years',    -- purge after 2 years (optional)
    enable_replication       := true          -- HTAP for hot partitions
);

-- Monitor
SELECT schema_name, table_name, analytics_offload, tiered_data_size, untiered_data_size
FROM pgaa.list_tiered_tables();

-- Manually convert HTAP → cold
CALL pgaa.convert_to_analytics('app.events'::regclass);

-- Restore cold → local heap (does NOT clean up object storage)
CALL pgaa.restore_from_analytics('app.events'::regclass);
```

---

## 8. Background Maintenance Tasks

**Critical:** `pgaa.enable_maintenance_worker` defaults to `off`. Tasks will
queue in `pgaa.background_task` but never execute unless this is enabled.

```sql
-- Enable the maintenance worker
ALTER SYSTEM SET pgaa.enable_maintenance_worker = on;
SELECT pg_reload_conf();

-- Schedule tasks
SELECT pgaa.launch_task('analytics.events'::regclass, 'compaction', '{}');
SELECT pgaa.launch_task('analytics.events'::regclass, 'zorder',
    '{"columns":["region","date"]}'::jsonb);  -- Delta only
SELECT pgaa.launch_task('analytics.events'::regclass, 'vacuum',
    '{"retention_period":"168 hours"}'::jsonb);  -- Delta only
SELECT pgaa.launch_task('analytics.events'::regclass, 'purge',
    '{"storage_location":"my-store","path":"tmp/"}'::jsonb);  -- Delta only

-- Check task status
SELECT id, task_type, status, created_at, finished_at
FROM pgaa.background_task ORDER BY created_at DESC LIMIT 20;
-- status: 'pending' | 'running' | 'success' | 'failure'
```

**Iceberg note:** `launch_task` only supports `compaction` for Iceberg tables.
For zorder/vacuum/purge on Iceberg, use `pgaa.spark_sql()` with the Spark
Connect engine.

`pgaa.execute_compaction()` is deprecated — use `launch_task` instead.

See [assets/quick-reference.md](assets/quick-reference.md) for full task
option JSON schemas.

---

## 9. Inspecting Tables and Storage

```sql
-- Version info
SELECT pgaa.pgaa_version();
SELECT pgaa.engine_version();

-- All pgaa tables
SELECT schema_name, table_name, format, replication_status,
       pg_size_pretty(object_storage_snapshot_size_bytes) AS snap_size
FROM pgaa.list_analytics_tables();

-- Storage stats for a specific table
SELECT * FROM pgaa.lakehouse_table_stats('analytics.events'::regclass);

-- Test connectivity
SELECT pgaa.test_catalog('my_catalog', false);         -- NULL = OK
SELECT pgaa.test_storage_location('my-store', false);  -- NULL = OK
```

Run `scripts/pgaa_diagnose.sql` with `\i scripts/pgaa_diagnose.sql` for a
full health snapshot.

---

## 10. Key Gotchas — Read Before Acting

| # | Gotcha | Action |
|---|--------|--------|
| 1 | `pgaa.enable_maintenance_worker` defaults to **`off`** | Set to `on` for `launch_task` to execute |
| 2 | Re-enabling PGD replication triggers a **full reload** (analytics data wiped) | Warn user before calling; expect slowness on large tables |
| 3 | `bdr.wait_slot_confirm_lsn()` **blocks indefinitely** if writes are ongoing | Only call after all writes are stopped |
| 4 | `pgaa.restore_from_analytics()` does **not** clean up object storage files | Manual cloud-side cleanup required afterward |
| 5 | S3 Tables catalogs do **not** support PGD, tiered tables, or offload | Use Iceberg REST catalog for those features |
| 6 | CTAS source queries **always use CompatScan**, never DirectScan | Expected — not an error |
| 7 | `launch_task` supports **only `compaction`** for Iceberg tables | Use `pgaa.spark_sql()` for other Iceberg maintenance |
| 8 | Empty column list `()` in `CREATE TABLE` triggers **schema auto-discovery** | Intentional — not an error |
| 9 | Schema evolution in the catalog is **not auto-applied** to Postgres | Manually `ALTER TABLE` to match upstream schema changes |
| 10 | WarehousePG requires `pgaa` in **`shared_preload_libraries`** | Add on coordinator + all segment hosts, then restart |
| 11 | CTAS fails if data exists at the target path | Add `pgaa.purge_data_if_exists = true` to `WITH` clause |
| 12 | Deleting a pgfs storage location **does not delete bucket data** | Drop dependent PGAA tables first; clean bucket separately |

---

## 11. Reference Files

| File | When to read |
|------|-------------|
| [references/function-reference.md](references/function-reference.md) | Full function signatures, GUC table, data-type mapping |
| [references/troubleshooting.md](references/troubleshooting.md) | Symptom-based diagnosis for common errors and misconfigurations |
| [references/deployment-contexts.md](references/deployment-contexts.md) | Per-context setup differences: Lakehouse, WarehousePG, PGD |
| [references/pgfs-guide.md](references/pgfs-guide.md) | pgfs storage location management, credentials, cleanup rules |
| [assets/quick-reference.md](assets/quick-reference.md) | Catalog option JSON schemas, table WITH options, GUC defaults, task option JSON |
| [scripts/pgaa_diagnose.sql](scripts/pgaa_diagnose.sql) | Run against a live instance for a full health snapshot |
| [scripts/pgaa_smoke_test.py](scripts/pgaa_smoke_test.py) | Automated smoke test using the public TPC-H SF1 Delta sample data |