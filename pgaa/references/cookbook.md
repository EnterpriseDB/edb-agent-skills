# pgaa Workflow Cookbook

Common end-to-end workflows for EDB Postgres Analytics Accelerator (pgaa).
See [function-reference.md](function-reference.md) for full API signatures and [guc-reference.md](guc-reference.md) for GUC details.

---

## Workflow 1: Create a Standalone Analytics Table

**Prerequisite:** A pgfs storage location must already exist (managed by the `pgfs` extension).

```sql
-- Step 1: Verify the storage location exists (pgfs skill covers creation)
SELECT * FROM pgfs.storage_location WHERE name = 'my-s3-bucket';

-- Step 2: Test the storage location is reachable from pgaa
SELECT pgaa.test_storage_location('my-s3-bucket', true);  -- NULL = OK

-- Step 3: Create the PGAA table
CREATE TABLE analytics.events (
    event_id    BIGINT,
    user_id     BIGINT,
    event_type  TEXT,
    occurred_at TIMESTAMPTZ,
    payload     JSONB
)
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-bucket',
    pgaa.path             = 'analytics/events'
);

-- Step 4: Verify it appears in the analytics table list
SELECT schema_name, table_name, format, storage_location_name
FROM pgaa.list_analytics_tables()
WHERE schema_name = 'analytics';
```

---

## Workflow 2: Create an Analytics Table from a Query (CTAS)

```sql
-- CTAS: populate a PGAA table from an existing Postgres query
CREATE TABLE analytics.user_summary
USING PGAA
WITH (
    pgaa.storage_location = 'my-s3-bucket',
    pgaa.path             = 'analytics/user_summary'
)
AS (
    SELECT user_id, count(*) AS event_count, max(occurred_at) AS last_seen
    FROM public.events
    GROUP BY user_id
);

-- Note: CTAS executes the query on Postgres (not in DirectScan mode).
-- An empty result set produces an empty table (supported since 1.3.0).
```

---

## Workflow 3: Register and Attach an Iceberg REST Catalog

```sql
-- Step 1: Validate connectivity before registering
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "https://my-catalog.example.com/api/catalog",
      "warehouse": "my_warehouse",
      "token": "my_bearer_token"}'
);
-- NULL = success

-- Step 2: Register the catalog
SELECT pgaa.add_catalog(
    'my-iceberg-catalog',
    'iceberg-rest',
    '{"url": "https://my-catalog.example.com/api/catalog",
      "warehouse": "my_warehouse",
      "token": "my_bearer_token"}'
);

-- Step 3: One-time import (creates USING PGAA tables for all existing catalog tables)
SELECT pgaa.import_catalog('my-iceberg-catalog');
-- Or import only a specific namespace:
SELECT pgaa.import_catalog('my-iceberg-catalog', 'production_data');

-- Step 4: Alternatively, start continuous sync (tables auto-refresh at metastore_sync_poll_rate_s)
SELECT pgaa.attach_catalog('my-iceberg-catalog');

-- Step 5: Verify
SELECT name, status, refreshed_at FROM pgaa.list_catalogs();
SELECT schema_name, table_name FROM pgaa.list_catalog_tables('my-iceberg-catalog');
```

---

## Workflow 4: Register an AWS S3 Tables Catalog

```sql
-- S3Tables requires only the ARN (region is optional — auto-detected from env)
SELECT pgaa.add_catalog(
    'my-s3tables',
    'iceberg-s3tables',
    '{"arn": "arn:aws:s3tables:us-east-1:123456789012:bucket/my-table-bucket",
      "region": "us-east-1"}'
);

-- Then import or attach as in Workflow 3
SELECT pgaa.import_catalog('my-s3tables');
```

---

## Workflow 5: Enable PGD Replication to an Analytics Table

**Prerequisite:** BDR/PGD must be installed. Run on the PGD write leader.

```sql
-- Enable replication for a heap table into analytics storage
CALL pgaa.enable_analytics_replication('public.orders');

-- Verify replication status
SELECT table_name, replication_status FROM pgaa.list_analytics_tables()
WHERE table_name = 'orders';
-- replication_status = 'initial_offload' → 'enabled' once caught up
```

---

## Workflow 6: Convert a Table to a PGD Tiered Table

```sql
-- Convert an existing partitioned range table to a tiered table
-- (data older than analytics_offload_period is offloaded to the lakehouse)
CALL pgaa.convert_to_tiered_table(
    relation                  => 'public.orders',
    range_partition_column    => 'created_at',
    partition_increment       => '1 month',
    analytics_offload_period  => '3 months',
    initial_lower_bound       => '2024-01-01',
    retention_period          => '2 years',
    enable_replication        => true,
    minimum_advance_partitions => 2,
    maximum_advance_partitions => 5,
    drop_after_retention_period => true
);

-- Check tiered tables
SELECT * FROM pgaa.list_tiered_tables();
```

---

## Workflow 7: Run Compaction on an Iceberg Table

```sql
-- Launch a compaction background task
SELECT pgaa.execute_compaction('analytics.events');
-- The procedure commits, then waits for the background worker to complete.

-- Monitor progress manually:
SELECT id, type, status, started_at, output
FROM pgaa.background_task
WHERE target_table = 'analytics.events'::regclass
ORDER BY scheduled_at DESC
LIMIT 5;
```

---

## Workflow 8: Run Spark SQL (Iceberg Compaction via Spark)

**Prerequisite:** `pgaa.executor_engine = 'spark_connect'` and `pgaa.spark_connect_url` configured.

```sql
-- Run Iceberg compaction via Spark procedures
SELECT pgaa.spark_sql(
    'CALL system.rewrite_data_files(table => ''production_data.events'')',
    'my-iceberg-catalog'
);

-- Expire old Iceberg snapshots
SELECT pgaa.spark_sql(
    'CALL system.expire_snapshots(table => ''production_data.events'', older_than => TIMESTAMP ''2024-01-01 00:00:00'')',
    'my-iceberg-catalog'
);
```

---

## Workflow 9: Check Storage & Table Health

```sql
-- Table sizes and replication status
SELECT
    schema_name,
    table_name,
    format,
    pg_size_pretty(object_storage_snapshot_size_bytes) AS snapshot_size,
    pg_size_pretty(object_storage_total_size_bytes)    AS total_size,
    replication_status
FROM pgaa.list_analytics_tables()
ORDER BY object_storage_total_size_bytes DESC NULLS LAST;

-- Detailed stats (bypasses cache)
SELECT * FROM pgaa.lakehouse_table_stats_uncached('analytics.events'::regclass);

-- Engine health
SELECT pgaa.engine_version();

-- Check for stuck background tasks
SELECT id, type, target_table, status, scheduled_at
FROM pgaa.background_task
WHERE status IN ('pending', 'running');
```

---

## Workflow 10: Detach and Remove a Catalog

```sql
-- Detach (stop sync, keep tables):
SELECT pgaa.detach_catalog('my-iceberg-catalog');

-- Detach and drop all managed PGAA tables:
SELECT pgaa.detach_catalog('my-iceberg-catalog', cascade := true);

-- Delete the catalog entry (fails if tables exist and cascade is false):
SELECT pgaa.delete_catalog('my-iceberg-catalog');

-- Delete and drop all managed tables:
SELECT pgaa.delete_catalog('my-iceberg-catalog', cascade := true);
```

> **Note:** `cascade := true` drops the **PostgreSQL** table definitions only.
> It does **not** delete the underlying Iceberg/Delta/Parquet data files in object storage.
