# pgaa Deployment Contexts — Reference Guide

EDB Postgres Analytics Accelerator (`pgaa`) operates in three distinct deployment contexts. Configuration requirements and behavioral differences vary significantly between them. This guide covers what is different in each context.

---

## 1. Lakehouse Cluster (Standalone)

**What it is:** A single-node or replicated EDB Postgres instance (PostgreSQL 16–18 or EDB Postgres Extended/Advanced Server 16–18) with `pgaa` installed. Used for direct analytics queries against Iceberg, Delta, and Parquet data in object storage.

**Setup requirements:**
```sql
CREATE EXTENSION pgaa CASCADE;  -- auto-installs pgfs
```

**Engine:** Seafowl starts automatically as a background process (`pgaa.autostart_seafowl = on`). No extra configuration is needed for the embedded engine.

**Query plan verification:**
```
EXPLAIN SELECT ... FROM pgaa_table;
-- Look for: SeafowlDirectScan (fast — entire query offloaded to Seafowl)
-- Fallback:  SeafowlCompatScan (query runs through PostgreSQL executor)
```

**DirectScan vs. CompatScan:**
- DirectScan uses the native Arrow Flight path for maximum performance.
- CompatScan is the fallback when the query can't be fully pushed down (e.g., CTAS source queries always use CompatScan).
- Control DirectScan with: `SET pgaa.enable_direct_scan = on/off;`
- Control failure behavior: `SET pgaa.direct_scan_fail_behavior = 'warn';` (`ignore` | `warn` | `error`)

---

## 2. WarehousePG (WHPG) — Distributed MPP

**What it is:** `pgaa` embedded within EDB's WarehousePG — a Greenplum-fork distributed MPP database running as a cluster of a coordinator node (`cdw`) and one or more segment hosts (`sdw1`, `sdw2`, …).

**Critical requirement — `shared_preload_libraries`:**
```
# postgresql.conf on coordinator AND all segment hosts:
shared_preload_libraries = 'pgaa'
```
Without this, `pgaa` will not function on WHPG. This is a common setup error.

**Creating PGAA tables on WHPG:**
```sql
-- Same CREATE TABLE syntax as Lakehouse, but the cluster distributes query execution
CREATE TABLE analytics.sales () USING PGAA WITH (
    pgaa.managed_by        = 'my_catalog',
    pgaa.catalog_namespace = 'production',
    pgaa.catalog_table     = 'sales',
    pgaa.format            = 'iceberg'
);
```

**Scan mode behavior difference:**
- On WHPG, DirectScan falls back automatically to **SeafowlCompatScan** because the Greenplum distributed executor intercepts the query. This is expected — `SeafowlCompatScan` in an EXPLAIN plan confirms PGAA is active on WHPG.

**Engine selection:**
```sql
SET pgaa.executor_engine = 'seafowl';       -- default (embedded DataFusion)
SET pgaa.executor_engine = 'spark_connect'; -- external Spark cluster
SET pgaa.spark_connect_url = 'sc://spark-connect:15002';
```

---

## 3. PGD Cluster (HTAP — Transactional + Analytics)

**What it is:** A PGD (EDB Postgres Distributed) multi-master cluster where `pgaa` enables Hybrid Transactional/Analytical Processing (HTAP) by replicating transactional writes from Postgres heap tables to Iceberg via the `bdr` extension.

**How replication works:**
1. Tables opt-in with `pgd.replicate_to_analytics = true`.
2. The BDR replication slot (`bdr.local_analytics_slot_name()`) streams WAL changes to the Iceberg catalog.
3. Replication is **eventually consistent** — reads from the analytics engine may lag behind the transactional table.
4. To read from the Iceberg copy instead of heap: `SET bdr.prefer_analytics_engine = true;`

### Setting Up Replication

```sql
-- Option A: At table creation
CREATE TABLE app.orders (
    order_id   SERIAL PRIMARY KEY,
    customer   TEXT,
    amount     DECIMAL(10,2),
    created_at TIMESTAMPTZ DEFAULT now()
) WITH (pgd.replicate_to_analytics = true);

-- Option B: On an existing table (triggers a FULL RELOAD)
CALL pgaa.enable_analytics_replication('app.orders'::regclass);
-- OR:
ALTER TABLE app.orders SET (pgd.replicate_to_analytics = true);

-- Disable replication
CALL pgaa.disable_analytics_replication('app.orders'::regclass);
-- OR:
ALTER TABLE app.orders SET (pgd.replicate_to_analytics = false);
```

**⚠ Full-reload gotcha:** Re-enabling replication on a table that previously had it **removes all existing analytics data and re-uploads the current local table**. This can be slow for large tables. Inform users before triggering this operation.

### Waiting for Replication

```sql
-- ONLY call this when all writes to the table have stopped.
-- This call BLOCKS INDEFINITELY if writes are still ongoing.
SELECT bdr.wait_slot_confirm_lsn(bdr.local_analytics_slot_name(), NULL);
```

Never use `pg_sleep()` as a substitute — the only correct synchronization primitive is `bdr.wait_slot_confirm_lsn`.

### Reading from the Analytics Engine

```sql
SET bdr.prefer_analytics_engine = true;
SELECT * FROM app.orders WHERE created_at > '2024-01-01';
-- Reads from Iceberg, not the local heap
```

### Monitoring Replication State

```sql
-- View all analytics table mappings
SELECT * FROM bdr.analytics_table;

-- Check per-table replication status
SELECT schema_name, table_name, replication_status
FROM pgaa.list_analytics_tables();
-- replication_status values: 'disabled', 'initial_offload', 'enabled'
```

### PGD Catalog Note

Replication uses the **default write catalog** configured on the cluster. Iceberg S3 Tables catalogs do **not** support PGD replication, tiered tables, or offload operations — use an Iceberg REST catalog (e.g., Lakekeeper) for all PGD integration scenarios.

---

## Catalog Type Compatibility Matrix

| Feature | Iceberg REST | Iceberg S3 Tables |
|---|---|---|
| Catalog-managed tables | ✅ | ✅ |
| PGD replication | ✅ | ❌ |
| Tiered tables | ✅ | ❌ |
| Analytics offload | ✅ | ❌ |
| `attach_catalog` (continuous sync) | ✅ | ✅ |

---

## Tiered Tables (PGD Context)

Tiered tables are a PGD feature that automatically moves partitions from hot local heap storage into cold pgaa/Iceberg storage as they age.

### Requirements
- The partition column must be `DATE` or `TIMESTAMP` and **must be part of the primary key**.
- The table cannot have sequences or foreign key constraints.
- The target Iceberg catalog must be an Iceberg REST catalog.

### Setup

```sql
-- 1. Start with a table that has an appropriate primary key
CREATE TABLE app.events (
    event_id   BIGSERIAL,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL,
    payload    JSONB,
    PRIMARY KEY (event_id, event_date)  -- event_date must be in PK
);

-- 2. Convert to tiered
CALL pgaa.convert_to_tiered_table(
    relation                 := 'app.events'::regclass,
    range_partition_column   := 'event_date',
    partition_increment      := '1 month',
    analytics_offload_period := '3 months',   -- data older than 3 months → cold
    initial_lower_bound      := '2024-01-01',
    retention_period         := '2 years',    -- purge after 2 years (optional)
    enable_replication       := true          -- HTAP for hot partitions
);
```

### Monitoring

```sql
SELECT schema_name, table_name, analytics_offload, tiered_data_size, untiered_data_size
FROM pgaa.list_tiered_tables();
```

### Converting Tiers Manually

```sql
-- HTAP table → cold-only analytics table (removes local disk data)
CALL pgaa.convert_to_analytics('app.events'::regclass);

-- Restore from analytics back to local heap
CALL pgaa.restore_from_analytics('app.events'::regclass);
-- WARNING: does NOT clean up files in object storage — manual cleanup required.
```

---

## Ecosystem Components (Background)

| Component | Role |
|---|---|
| **Seafowl** | Default analytics engine; embedded DataFusion-based process auto-started by pgaa |
| **metastore-agent** | Background worker that syncs external catalog and schema metadata into PostgreSQL |
| **pgfs** | Extension managing storage locations (required dependency, auto-installed with pgaa) |
| **Lakekeeper** | Common Iceberg REST catalog implementation used in EDB deployments |
| **BDR / PGD** | Provides the replication slot and WAL streaming for PGD → Iceberg replication |
