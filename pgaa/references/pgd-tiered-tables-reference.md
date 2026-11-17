# pgaa PGD Integration & Tiered Tables Reference

This document covers PGD (Postgres Distributed) HTAP replication to Iceberg and pgaa's tiered-table lifecycle management.

---

## PGD → Iceberg Replication

### How It Works

On a PGD cluster, pgaa can replicate writes from any transactional (heap) table to Iceberg automatically. This creates an HTAP architecture: transactions land in PGD for consistency and low-latency writes, while analytics queries read from the Iceberg copy via the analytics engine.

Replication uses the `bdr` extension's logical replication slot. It is **eventually consistent**: there is a small lag between a write to the heap table and its appearance in Iceberg.

### Create a Replicated Table

```sql
CREATE TABLE app.orders (
    order_id    SERIAL PRIMARY KEY,
    customer_id INT NOT NULL,
    amount      DECIMAL(10,2) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
) WITH (pgd.replicate_to_analytics = true);
```

Writes to this table automatically replicate to Iceberg via the cluster's configured default write catalog.

### Enable / Disable Replication on Existing Tables

```sql
-- Enable replication (performs a full reload — see warning below)
CALL pgaa.enable_analytics_replication(
    relation              := 'app.orders'::regclass,
    purge_analytics_target := false   -- set true to clear existing analytics data first (PGD 6.3+)
);

-- Disable replication
CALL pgaa.disable_analytics_replication('app.orders'::regclass);

-- Equivalent ALTER TABLE forms:
ALTER TABLE app.orders SET (pgd.replicate_to_analytics = true);
ALTER TABLE app.orders SET (pgd.replicate_to_analytics = false);
```

> ⚠️ **Re-enabling replication triggers a FULL RELOAD.** All existing Iceberg data for the table is removed and the current heap content is re-uploaded. This can be slow for large tables and causes a gap in analytics availability during the reload.

### Query via the Analytics Engine

```sql
-- Route this session's reads to Iceberg instead of the heap:
SET bdr.prefer_analytics_engine = true;
SELECT * FROM app.orders WHERE created_at > '2024-01-01';

-- Revert to normal heap reads:
SET bdr.prefer_analytics_engine = false;
```

### Wait for Replication to Catch Up

```sql
-- Use ONLY after all writes to the table have stopped.
-- Blocks indefinitely if writes are still occurring.
SELECT bdr.wait_slot_confirm_lsn(bdr.local_analytics_slot_name(), NULL);
```

> ⚠️ Never use `pg_sleep()` or a time-based loop as a substitute — this function is the only reliable way to confirm replication is complete. If called while writes are ongoing it will block forever; ensure writes are quiesced first.

### Monitor Replication State

```sql
-- View all analytics table mappings
SELECT * FROM bdr.analytics_table;

-- Check replication status per table (disabled | initial_offload | enabled)
SELECT schema_name, table_name, replication_status
FROM pgaa.list_analytics_tables();
```

### PGD → Convert/Restore

```sql
-- Promote an HTAP (replicated) table to cold analytics-only storage
-- (removes local heap data):
CALL pgaa.convert_to_analytics('app.orders'::regclass);

-- Restore from cold Iceberg storage back to local heap:
CALL pgaa.restore_from_analytics('app.orders'::regclass);
-- ⚠️ Does NOT delete files from object storage. Manual cleanup is required.
```

---

## Tiered Tables

### Concept

A tiered table is a partitioned PostgreSQL table where:
- **Hot partitions** (recent data) remain in local heap storage (or replicate via HTAP if `enable_replication = true`)
- **Cold partitions** (older than `analytics_offload_period`) are automatically converted to pgaa/Iceberg
- **Expired partitions** (older than `retention_period`, if set) are dropped or purged entirely

Partition advancement (creating new future partitions) and cold-tier conversion happen automatically in the background.

### Create a Tiered Table

```sql
-- Step 1: Create the base table first (must have no sequences or foreign keys)
CREATE TABLE app.events (
    event_id   BIGINT,
    event_date DATE        NOT NULL,
    event_type TEXT        NOT NULL,
    payload    JSONB,
    PRIMARY KEY (event_id, event_date)   -- partition column MUST be in the PK
);

-- Step 2: Convert to tiered
CALL pgaa.convert_to_tiered_table(
    relation               := 'app.events'::regclass,
    range_partition_column := 'event_date',       -- must be DATE or TIMESTAMP
    partition_increment    := '1 month',
    analytics_offload_period := '3 months',       -- data older than 3 months → cold
    initial_lower_bound    := '2024-01-01',
    retention_period       := '2 years',          -- data older than 2 years → purged
    enable_replication     := true,               -- HTAP for hot partitions
    minimum_advance_partitions := 2,
    maximum_advance_partitions := 5,
    drop_after_retention_period := true,
    purge_analytics_target := false               -- PGD 6.3+: purge analytics before setup
);
```

**Constraints (enforced; will error if violated):**
- `range_partition_column` must be `DATE` or `TIMESTAMP`
- The partition column must be part of the primary key
- The table must have no sequences and no foreign key constraints

### Inspect Tiered Tables

```sql
SELECT schema_name, table_name, partition_increment, analytics_offload,
       retention, replication_enabled, tiered_data_size, untiered_data_size
FROM pgaa.list_tiered_tables();
```

### Query a Tiered Table

```sql
-- Automatically reads from the correct tier:
SET bdr.prefer_analytics_engine = true;
SELECT event_type, COUNT(*)
FROM app.events
WHERE event_date >= '2024-01-01'
GROUP BY event_type;
```

---

## S3 Tables Catalog Limitation

Iceberg S3 Tables catalogs (`catalog_type = 'iceberg-s3tables'`) do **not** support any PGD features:
- PGD → Iceberg replication
- Tiered tables
- `pgaa.convert_to_tiered_table()`
- Partition offload

For any PGD-integrated workflow, use an Iceberg REST catalog (`iceberg-rest`), such as Lakekeeper.
