---
name: pgaa
description: >
  Skill for EDB Postgres Analytics Accelerator (pgaa) — a PostgreSQL extension
  (Rust/pgrx) that registers a custom Table Access Method to offload reads and
  writes for certain tables to an external analytical query engine (Seafowl or
  Spark Connect) over Apache Arrow Flight RPC. Use this skill when a user needs
  to: create or manage analytics tables backed by object storage (Delta/Iceberg/
  Parquet); register, attach, or sync external Iceberg REST or AWS S3 Tables
  catalogs; enable PGD change-data-capture replication to a lakehouse; run Spark
  SQL against Iceberg catalogs; diagnose performance issues with pushdown GUCs;
  or manage background maintenance tasks (compaction, vacuum, purge). Also
  applicable for alias queries about "beacon-analytics", "postgres-analytics-
  accelerator", or "analytics-accelerator". Requires the pgfs extension for
  storage-location management (separate pgfs skill covers that layer).
metadata:
  aliases:
    - pgaa
    - beacon-analytics
    - postgres-analytics-accelerator
    - analytics-accelerator
  source_repo: https://github.com/EnterpriseDB/beacon-analytics
  extension_schema: pgaa
  requires_extension: pgfs
  supported_pg_versions: [16, 17, 18]
  supported_flavors: [community, EPAS, PGE, WarehousePG]
---

# pgaa — EDB Postgres Analytics Accelerator

## What pgaa Is

**pgaa** (EDB **Postgres Analytics Accelerator**) is a PostgreSQL extension that registers a custom **Table Access Method**. Tables created `USING PGAA` store no data in ordinary Postgres heap pages; reads and writes are routed to an external analytical query engine over Apache Arrow Flight RPC.

Two backing engines are supported, selected by the `pgaa.executor_engine` GUC:
- **Seafowl** (default) — an in-house DataFusion-based engine, runs as a Postgres background worker or external process
- **Spark Connect** — for `pgaa.spark_sql()` and Spark-based query execution

pgaa ships from the `beacon-analytics` monorepo, which also contains Seafowl and metastore-agent. It formally **requires** the `pgfs` extension (`requires = 'pgfs'` in `pgaa.control`) — storage locations are defined via pgfs, not pgaa.

> **Naming rule:** Always say "Postgres Analytics Accelerator". Do NOT say "Postgres AI Analytics" or any other expansion.

---

## When to Trigger This Skill

| User intent | This skill covers |
|-------------|-------------------|
| Create a table in object storage | ✅ `CREATE TABLE ... USING PGAA` |
| Query external Iceberg/Delta/Parquet data | ✅ Catalog registration + attach |
| Sync AWS S3 Tables into Postgres | ✅ `iceberg-s3tables` catalog |
| Enable PGD replication to lakehouse | ✅ `enable_analytics_replication`, tiered tables |
| Run Spark SQL / Iceberg maintenance | ✅ `pgaa.spark_sql()` |
| Debug slow analytics queries | ✅ DirectScan / pushdown GUCs |
| Create/manage storage locations | ❌ → Use the **pgfs** skill instead |

---

## Prerequisites

1. pgfs extension installed and a storage location created (for non-catalog tables)
2. pgaa extension installed: `CREATE EXTENSION pgaa;` (this also installs the PGAA access method)
3. Seafowl running (embedded background worker or external) OR Spark Connect endpoint available
4. For PGD features: BDR ≥ 6.1 installed

---

## Core Workflows

### 1. Create a Standalone Analytics Table

```sql
-- Prerequisite: pgfs storage location already exists
SELECT pgaa.test_storage_location('my-s3-bucket', true);  -- NULL = OK

CREATE TABLE analytics.events (
    event_id    BIGINT,
    user_id     BIGINT,
    event_type  TEXT,
    occurred_at TIMESTAMPTZ
)
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-bucket',  -- name from pgfs.create_storage_location
    pgaa.path             = 'analytics/events'
);

-- Verify
SELECT schema_name, table_name, format, storage_location_name
FROM pgaa.list_analytics_tables();
```

### 2. Create an Analytics Table from a Query (CTAS)

```sql
CREATE TABLE analytics.user_summary
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-bucket',
    pgaa.path             = 'analytics/user_summary'
)
AS (
    SELECT user_id, count(*) AS cnt, max(occurred_at) AS last_seen
    FROM public.events
    GROUP BY user_id
);
```
> CTAS executes the query on Postgres, not in DirectScan mode. Empty result sets produce empty tables.

### 3. Register and Attach an Iceberg REST Catalog

```sql
-- Pre-validate (safe to call before registering)
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://my-catalog/api/catalog", "warehouse": "my_wh", "token": "tok"}'
);

-- Register
SELECT pgaa.add_catalog(
    'my-catalog', 'iceberg-rest',
    '{"url": "https://my-catalog/api/catalog", "warehouse": "my_wh", "token": "tok"}'
);

-- One-time import (all namespaces, or filter to one):
SELECT pgaa.import_catalog('my-catalog');
SELECT pgaa.import_catalog('my-catalog', 'production_data');

-- OR start continuous sync (tables refresh at pgaa.metastore_sync_poll_rate_s):
SELECT pgaa.attach_catalog('my-catalog');

-- Verify
SELECT name, status, refreshed_at FROM pgaa.list_catalogs();
```

### 4. Register an AWS S3 Tables Catalog

```sql
SELECT pgaa.add_catalog(
    'my-s3tables', 'iceberg-s3tables',
    '{"arn": "arn:aws:s3tables:us-east-1:123456789:bucket/my-bucket", "region": "us-east-1"}'
);
SELECT pgaa.import_catalog('my-s3tables');
```

### 5. Enable PGD Replication to Analytics (requires BDR ≥ 6.1)

```sql
CALL pgaa.enable_analytics_replication('public.orders');

-- Check status ('initial_offload' → 'enabled' once caught up)
SELECT table_name, replication_status FROM pgaa.list_analytics_tables();
```

### 6. Run Spark SQL (requires `executor_engine = 'spark_connect'`)

```sql
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://my-spark:15002';

-- Iceberg compaction via Spark procedure:
SELECT pgaa.spark_sql(
    'CALL system.rewrite_data_files(table => ''prod.events'')',
    'my-catalog'
);
```

---

## Diagnosing Issues

### Quick Health Check

```sql
SELECT pgaa.engine_version();           -- engine reachable?
SELECT pgaa.pgaa_version();             -- build info
SELECT name, status, refreshed_at FROM pgaa.list_catalogs();
SELECT schema_name, table_name, format, replication_status,
       pg_size_pretty(object_storage_total_size_bytes) AS total_size
FROM pgaa.list_analytics_tables();
SELECT id, type, status, target_table FROM pgaa.background_task
WHERE status IN ('pending', 'running');
```

### Run the Diagnostic Script

```bash
python3 scripts/pgaa_diagnostics.py --dsn "postgresql://user:pass@host/db"
# Add --test-storage to also probe each pgfs storage location
```

### Query Pushdown Debugging

```sql
-- Surface why DirectScan is not being used:
SET pgaa.direct_scan_fail_behavior = 'error';
-- Re-run the slow query; the error will explain the fallback reason.

-- Key pushdown GUCs (all default ON):
SET pgaa.enable_direct_scan = on;
SET pgaa.enable_join_pushdown = on;
SET pgaa.enable_groupby_pushdown = on;
SET pgaa.enable_orderby_pushdown = on;
SET pgaa.enable_distinct_pushdown = on;
SET pgaa.enable_window_pushdown = on;
```

---

## Key Constraints and Safety Rules

| Constraint | Detail |
|-----------|--------|
| `pgfs` required | `pgaa.storage_location` references a location created by `pgfs.create_storage_location`, not pgaa |
| Engine must be running | PGAA tables are unqueryable if Seafowl/Spark Connect is unreachable |
| `pgaa.spark_sql()` only with Spark Connect | Errors if `executor_engine ≠ 'spark_connect'` |
| `cascade` defaults to `false` | `detach_catalog` / `delete_catalog` will error if managed tables exist and `cascade` is not `true` |
| Catalog type is exactly two values | `'iceberg-rest'` or `'iceberg-s3tables'` — no others |
| `cascade := true` drops PG table defs only | Does **not** delete underlying data files in object storage |
| VACUUM on PGAA tables | Since 1.9.0: emits NOTICE and skips (data is in object storage, not heap) |
| Direct INSERT into PGAA tables | Not supported; use CTAS, replication, or catalog import |
| WarehousePG limitations | CTAS and metastore background workers unsupported on WarehousePG; Orca optimizer incompatible with PGAA table queries |

---

## Reference Files

| File | Contents |
|------|----------|
| [references/function-reference.md](references/function-reference.md) | Full SQL function/procedure signatures for all pgaa schema objects |
| [references/guc-reference.md](references/guc-reference.md) | All GUCs grouped by concern (engine, workers, pushdown, stats, replication) with tuning tips |
| [references/cookbook.md](references/cookbook.md) | End-to-end workflow examples (10 common workflows) |
| [references/troubleshooting.md](references/troubleshooting.md) | 12 common failure modes with diagnosis steps and resolutions |
| [references/schema-reference.md](references/schema-reference.md) | Internal catalog tables schema (`table_mapping`, `catalog`, `background_task`, etc.) |
| [scripts/pgaa_diagnostics.py](scripts/pgaa_diagnostics.py) | Self-contained Python diagnostic script; requires `psycopg2` |

---

## Ecosystem Context

| Component | Role | Scope of this skill |
|-----------|------|---------------------|
| **pgaa** | PostgreSQL extension (Table Access Method) | ✅ Primary subject |
| **Seafowl** | Default offload engine (DataFusion-based), ships in same package | Covered where it intersects pgaa config/GUCs |
| **metastore-agent** | Background worker for catalog schema sync | Covered via `attach_catalog` / `enable_metastore_sync_worker` |
| **pgfs** | Storage-location management extension | ❌ Use the `pgfs` skill — pgaa depends on it |

---

## Version Note

State versions cautiously: the `Cargo.toml` / `pgaa.control` version in the `beacon-analytics` source may be ahead of the last tagged release. The latest stable tagged release is **1.11.0** (2026-08-28). Prefer saying "as of the current `beacon-analytics` source" when exact release alignment is uncertain.