---
name: pgaa
description: >
  Skill for the EDB Postgres Analytics Accelerator (pgaa) — a PostgreSQL
  extension that registers a Table Access Method (TAM) allowing PostgreSQL to
  read and write Apache Iceberg, Delta Lake, and Parquet tables stored in object
  storage. Use this skill whenever a user needs to: install or configure pgaa;
  create analytics tables (catalog-managed or via direct storage location);
  register and manage Iceberg catalogs; query lakehouse data from PostgreSQL;
  set up PGD-to-Iceberg replication or tiered tables; schedule background
  maintenance tasks (compaction, vacuum, zorder, purge); smoke-test a pgaa
  deployment; or troubleshoot pgaa query routing, catalog sync, or replication
  issues. Aliases: beacon-analytics, postgres-analytics-accelerator,
  analytics-accelerator.
metadata:
  aliases:
    - pgaa
    - beacon-analytics
    - postgres-analytics-accelerator
    - analytics-accelerator
  postgresql_versions: ["16", "17", "18"]
  formats: ["iceberg", "delta", "parquet"]
  engines: ["seafowl", "spark_connect"]
  deployment_contexts: ["lakehouse-cluster", "warehousepg", "pgd"]
---

# pgaa — EDB Postgres Analytics Accelerator

pgaa is a PostgreSQL extension that registers a custom **Table Access Method (TAM)**
so that reads and writes against designated tables are offloaded to an external
analytical engine rather than executed against Postgres heap storage. It lets
PostgreSQL query Apache Iceberg, Delta Lake, and Parquet tables in object storage
directly using standard SQL.

**Requires:** PostgreSQL 16–18 and the `pgfs` extension (auto-installed via CASCADE).

---

## Deployment Contexts

| Context | Description | Key Requirement |
|---|---|---|
| **Lakehouse Cluster** | Standalone EDB Postgres for direct analytics queries | None beyond `CREATE EXTENSION pgaa CASCADE` |
| **WarehousePG (WHPG)** | pgaa inside a Greenplum-fork MPP cluster | `pgaa` in `shared_preload_libraries` before server start; DirectScan falls back to CompatScan |
| **PGD** | HTAP — transactional tables replicate from PGD heap to Iceberg | `bdr` extension; configure a default write catalog |

---

## Step 1 — Install the Extension

```sql
CREATE EXTENSION pgaa CASCADE;   -- also installs pgfs
```

**WarehousePG only** — add to `postgresql.conf` before the first start:
```
shared_preload_libraries = 'pgaa'
```

---

## Step 2 — Set Up Storage Access

pgaa tables are backed by either an **Iceberg catalog** (recommended) or a
**pgfs storage location** (direct, catalog-free access to Delta/Parquet).

### Register an Iceberg REST Catalog

```sql
-- Bearer-token auth (e.g., Lakekeeper)
SELECT pgaa.add_catalog(
  'my_catalog',
  'iceberg-rest',
  '{"url": "<CATALOG_URL>", "warehouse": "<WAREHOUSE_ID>", "token": "<TOKEN>"}'::json
);

-- OAuth2 client credentials
SELECT pgaa.add_catalog(
  'my_catalog',
  'iceberg-rest',
  '{"url": "<CATALOG_URL>", "oauth2.grant_type": "client_credentials",
    "oauth2.client_id": "<ID>", "oauth2.client_secret": "<SECRET>",
    "oauth2.token_uri": "<TOKEN_URI>"}'::json
);

-- AWS S3 Tables catalog
SELECT pgaa.add_catalog(
  'my_s3tables',
  'iceberg-s3tables',
  '{"arn": "arn:aws:s3tables:<REGION>:<ACCOUNT>:bucket/<BUCKET>", "region": "<REGION>"}'::json
);

-- Validate before registering (returns NULL on success)
SELECT pgaa.validate_catalog_connection('iceberg-rest', '{"url": "...", "token": "..."}'::json);
```

> **S3 Tables limitation:** `iceberg-s3tables` catalogs do **not** support PGD
> features (replication, tiered tables, partition offload). Use `iceberg-rest` for
> any PGD-integrated workflow.

### Create a pgfs Storage Location (for Delta/Parquet or smoke tests)

```sql
SELECT pgfs.create_storage_location(
  'my_location',
  's3://<BUCKET>/<PREFIX>/',
  '{"region": "<REGION>"}'::json,
  '{"access_key_id": "<KEY>", "secret_access_key": "<SECRET>"}'::json
);

-- Public/anonymous bucket (no credentials needed):
SELECT pgfs.create_storage_location(
  'public_data',
  's3://public-bucket/',
  '{"aws_skip_signature": "true"}'::json
);
```

For full pgfs documentation: [references/pgfs-reference.md](references/pgfs-reference.md)

---

## Step 3 — Create pgaa Tables

### Pattern 1: Catalog-Managed (recommended for Iceberg)

```sql
CREATE SCHEMA IF NOT EXISTS analytics;

-- Empty column list = auto-discover schema from the catalog table
CREATE TABLE analytics.events () USING PGAA WITH (
  pgaa.managed_by        = 'my_catalog',
  pgaa.catalog_namespace = 'production',
  pgaa.catalog_table     = 'events',
  pgaa.format            = 'iceberg'
);
```

### Pattern 2: Direct Storage Location (for Delta/Parquet)

```sql
CREATE TABLE analytics.logs () USING PGAA WITH (
  pgaa.storage_location = 'my_location',
  pgaa.path             = 'logs/2024/',
  pgaa.format           = 'delta'    -- 'delta' | 'parquet' (parquet is read-only)
);
```

### Pattern 3: CTAS — Write Query Results to Lakehouse

```sql
CREATE TABLE analytics.summary USING PGAA WITH (
  pgaa.managed_by           = 'my_catalog',
  pgaa.catalog_namespace    = 'production',
  pgaa.catalog_table        = 'summary',
  pgaa.format               = 'iceberg',
  pgaa.purge_data_if_exists = true      -- required if data exists at the target
) AS SELECT region, SUM(amount) FROM orders GROUP BY region;
```
> CTAS source queries always use CompatScan (not DirectScan). Fails if data exists
> at the target path unless `pgaa.purge_data_if_exists = true`.

### Pattern 4: Convert an Existing Heap Table In Place

```sql
-- Heap → pgaa (catalog-managed)
ALTER TABLE public.my_table
  SET ACCESS METHOD pgaa,
  SET (pgaa.auto_truncate = 'true', pgaa.format = 'iceberg',
       pgaa.managed_by = 'my_catalog', pgaa.catalog_namespace = 'ns',
       pgaa.catalog_table = 'my_table');

-- pgaa → heap (revert)
ALTER TABLE public.my_table SET ACCESS METHOD heap;
```

For complete paste-ready SQL templates: [assets/sql-templates.md](assets/sql-templates.md)

---

## Querying pgaa Tables

```sql
-- Standard SQL — pgaa routes to the analytics engine transparently
SELECT event_type, COUNT(*)
FROM analytics.events
WHERE event_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY event_type;

-- Verify routing with EXPLAIN
EXPLAIN SELECT COUNT(*) FROM analytics.events;
-- Lakehouse/PGD:   SeafowlDirectScan  (fast, full pushdown)
-- WarehousePG:     SeafowlCompatScan  (expected fallback on Greenplum)
-- If no Seafowl node: pgaa is not active — check installation
```

---

## Execution Engines

pgaa supports two engines, controlled by `pgaa.executor_engine` (default: `seafowl`).

| Engine | Type | Use Case | Writes? |
|---|---|---|---|
| `seafowl` | Embedded DataFusion | Default; no external dependencies; auto-started | Yes |
| `spark_connect` | External Spark cluster | Heavy/complex workloads | No (read-only) |

```sql
-- Switch to Spark Connect
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://spark-host:15002';

-- Raw Spark SQL (single or multi-catalog)
SELECT pgaa.spark_sql('SELECT * FROM ns.table', 'my_catalog');
SELECT pgaa.spark_sql('SELECT a.*, b.* FROM cat1.ns.t1 a JOIN cat2.ns.t2 b ON a.id = b.id',
                      ARRAY['cat1', 'cat2']);
-- Returns JSON. Spark Connect is READ-ONLY; all writes go through PostgreSQL.
```

---

## Catalog Management Operations

```sql
SELECT * FROM pgaa.list_catalogs();                            -- list all catalogs
SELECT * FROM pgaa.list_catalog_tables('my_catalog', NULL);   -- list tables in catalog

SELECT pgaa.import_catalog('my_catalog', 'namespace');        -- one-time import
SELECT pgaa.attach_catalog('my_catalog');                     -- start continuous sync
SELECT * FROM pgaa.detach_catalog('my_catalog', cascade := false);  -- stop sync

SELECT * FROM pgaa.update_catalog('my_catalog', '{"token": "<NEW>"}'::json);
SELECT * FROM pgaa.delete_catalog('my_catalog', cascade := false);  -- cascade := true drops tables
SELECT pgaa.test_catalog('my_catalog', test_writes := false);       -- NULL = success
```

---

## PGD → Iceberg Replication

For full details: [references/pgd-tiered-tables-reference.md](references/pgd-tiered-tables-reference.md)

```sql
-- Create table with replication enabled
CREATE TABLE app.orders (
  order_id    SERIAL PRIMARY KEY,
  customer_id INT NOT NULL,
  amount      DECIMAL(10,2) NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
) WITH (pgd.replicate_to_analytics = true);

-- Enable/disable on existing tables
CALL pgaa.enable_analytics_replication('app.orders'::regclass);
CALL pgaa.disable_analytics_replication('app.orders'::regclass);
```

> ⚠️ **Re-enabling replication triggers a full reload** — existing analytics data is
> removed and current local data is re-uploaded. Plan for downtime on large tables.

```sql
-- Query via analytics engine
SET bdr.prefer_analytics_engine = true;
SELECT * FROM app.orders WHERE created_at > '2024-01-01';

-- Wait for replication (ONLY after all writes have stopped — blocks indefinitely otherwise)
SELECT bdr.wait_slot_confirm_lsn(bdr.local_analytics_slot_name(), NULL);

-- Monitor replication state
SELECT schema_name, table_name, replication_status FROM pgaa.list_analytics_tables();
```

---

## Tiered Tables

Tiered tables partition data with hot (heap) and cold (pgaa/Iceberg) tiers, with
automatic partition advancement and offload.

**Constraints:** partition column must be `DATE`/`TIMESTAMP` and part of the primary key; no sequences or foreign key constraints on the table.

```sql
-- Step 1: Create base table (no BIGSERIAL, no FK)
CREATE TABLE app.events (
  event_id   BIGINT,
  event_date DATE NOT NULL,
  event_type TEXT NOT NULL,
  PRIMARY KEY (event_id, event_date)   -- partition column in PK
);

-- Step 2: Convert to tiered
CALL pgaa.convert_to_tiered_table(
  relation               := 'app.events'::regclass,
  range_partition_column := 'event_date',
  partition_increment    := '1 month',
  analytics_offload_period := '3 months',
  initial_lower_bound    := '2024-01-01',
  retention_period       := '2 years',
  enable_replication     := true       -- HTAP for hot partitions
);

-- Monitor
SELECT schema_name, table_name, analytics_offload, tiered_data_size, untiered_data_size
FROM pgaa.list_tiered_tables();
```

---

## Background Maintenance Tasks

> ⚠️ **`pgaa.enable_maintenance_worker` defaults to `off`** — tasks will queue but
> not execute until this is enabled.

```sql
ALTER SYSTEM SET pgaa.enable_maintenance_worker = on;
SELECT pg_reload_conf();

-- Schedule compaction (delta and iceberg)
SELECT pgaa.launch_task('analytics.events'::regclass, 'compaction',
  '{"target_size": 536870912, "dry_run": false}'::jsonb);

-- Schedule Z-order (delta only; use pgaa.spark_sql() for Iceberg)
SELECT pgaa.launch_task('analytics.events'::regclass, 'zorder',
  '{"columns": ["customer_id", "event_date"]}'::jsonb);

-- Schedule vacuum (delta only)
SELECT pgaa.launch_task('analytics.events'::regclass, 'vacuum',
  '{"retention_period": "168 hours", "dry_run": false}'::jsonb);

-- Check task status
SELECT id, task_type, status, scheduled_at, error
FROM pgaa.background_task ORDER BY scheduled_at DESC LIMIT 20;
```

**Iceberg maintenance note:** `launch_task` only supports `compaction` for Iceberg.
Use `pgaa.spark_sql()` for `zorder`, `vacuum`, and `purge` on Iceberg tables.
`pgaa.execute_compaction()` is deprecated; use `launch_task` instead.

For task scheduling and monitoring via Python: `Run scripts/pgaa_maintenance.py`

---

## Inspection & Diagnostics

```sql
-- All pgaa tables: format, replication status, sizes
SELECT schema_name, table_name, format, replication_status,
       pg_size_pretty(object_storage_snapshot_size_bytes) AS snapshot,
       pg_size_pretty(object_storage_total_size_bytes) AS total
FROM pgaa.list_analytics_tables();

-- Storage sizes for a specific table
SELECT * FROM pgaa.lakehouse_table_stats('analytics.events'::regclass);

-- Version info
SELECT pgaa.pgaa_version();
SELECT pgaa.engine_version();

-- All pgaa GUC settings
SELECT name, setting, unit FROM pg_settings WHERE name LIKE 'pgaa%' ORDER BY name;

-- DataFusion overrides (non-default only)
SELECT name, setting FROM pg_settings
WHERE name LIKE 'pgaa.datafusion%' AND setting IS DISTINCT FROM boot_val;
```

For a full diagnostic report across versions, catalogs, tables, tasks, and GUCs,
run: `Run scripts/pgaa_inspect.py`

---

## Smoke Test

Validate installation against the EDB TPC-H public demo bucket (no credentials needed):

```sql
CREATE SCHEMA IF NOT EXISTS smoke_test;
SELECT pgfs.create_storage_location('smoke-data',
  's3://beacon-analytics-demo-data-us-east-1-prod', '{"aws_skip_signature": "true"}');
CREATE TABLE smoke_test.customer () USING PGAA WITH (
  pgaa.storage_location = 'smoke-data', pgaa.path = 'tpch_sf_1/customer');
SELECT COUNT(*) FROM smoke_test.customer;   -- expected: 150,000
EXPLAIN SELECT COUNT(*) FROM smoke_test.customer;  -- expect SeafowlDirectScan / SeafowlCompatScan
-- Cleanup (order: table → schema → storage location)
DROP TABLE smoke_test.customer CASCADE;
DROP SCHEMA smoke_test CASCADE;
SELECT pgfs.delete_storage_location('smoke-data');
```

Or run the automated smoke test: `Run scripts/pgaa_smoke_test.py`

---

## Key Gotchas Summary

| Situation | Gotcha |
|---|---|
| WarehousePG install | `pgaa` must be in `shared_preload_libraries` — extension creation alone is not enough |
| Maintenance tasks | `pgaa.enable_maintenance_worker` defaults to `off` — tasks queue silently |
| Re-enabling PGD replication | Triggers a full reload — existing analytics data is wiped and re-uploaded |
| `bdr.wait_slot_confirm_lsn()` | Blocks indefinitely if writes are ongoing — only call when writes are stopped |
| CTAS overwrite | Fails unless `pgaa.purge_data_if_exists = true` |
| Iceberg maintenance | `launch_task` only supports `compaction` for Iceberg; use `spark_sql()` for the rest |
| Catalog cascade | `delete_catalog(cascade := false)` leaves dependent tables intact; use `cascade := true` to drop them |
| `restore_from_analytics()` | Restores data to heap but does **not** delete files from object storage |
| Schema evolution | Catalog-managed table schema changes require manual `ALTER TABLE` in Postgres |
| S3 Tables + PGD | `iceberg-s3tables` catalogs do not support PGD replication, tiered tables, or offload |
| Storage location cleanup | Delete pgaa tables **before** deleting their storage location; reverse order orphans the table |
| Spark Connect | Read-only — all writes must go through PostgreSQL |

---

## Reference Files

| File | Contents |
|---|---|
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures, return columns, GUC tables, and enum types |
| [references/pgfs-reference.md](references/pgfs-reference.md) | pgfs storage location management and smoke-test patterns |
| [references/pgd-tiered-tables-reference.md](references/pgd-tiered-tables-reference.md) | PGD replication, tiered tables, convert/restore procedures |
| [references/deployment-config-reference.md](references/deployment-config-reference.md) | Deployment contexts, all GUC parameters with defaults, data type mapping |
| [references/troubleshooting-reference.md](references/troubleshooting-reference.md) | Error messages, debugging steps, common failure patterns |
| [assets/sql-templates.md](assets/sql-templates.md) | Paste-ready SQL for every table creation, catalog, and maintenance pattern |
| [scripts/pgaa_smoke_test.py](scripts/pgaa_smoke_test.py) | Automated smoke test against the public TPC-H demo bucket |
| [scripts/pgaa_inspect.py](scripts/pgaa_inspect.py) | Diagnostic report: versions, catalogs, tables, tasks, GUCs |
| [scripts/pgaa_maintenance.py](scripts/pgaa_maintenance.py) | Schedule, enable, and monitor background maintenance tasks |