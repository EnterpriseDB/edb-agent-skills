# pgaa Table Creation — Quick-Reference Templates

Paste-ready SQL patterns for every common pgaa table creation scenario.
Replace `<PLACEHOLDER>` values with your actual names before running.

---

## 1. Install the Extension

```sql
-- Installs pgaa and its dependency (pgfs) in one step.
CREATE EXTENSION pgaa CASCADE;
```

For WarehousePG, add to `postgresql.conf` before the first start:
```
shared_preload_libraries = 'pgaa'
```

---

## 2. Register an Iceberg REST Catalog

```sql
-- Bearer token authentication (e.g. Lakekeeper with static token)
SELECT pgaa.add_catalog(
    'my_catalog',
    'iceberg-rest',
    '{
        "url":            "<CATALOG_REST_URL>",
        "warehouse":      "<WAREHOUSE_ID>",
        "warehouse_name": "<WAREHOUSE_NAME>",
        "token":          "<BEARER_TOKEN>"
    }'::json
);

-- OAuth2 client credentials authentication
SELECT pgaa.add_catalog(
    'my_catalog',
    'iceberg-rest',
    '{
        "url":                   "<CATALOG_REST_URL>",
        "warehouse":             "<WAREHOUSE_ID>",
        "oauth2.grant_type":     "client_credentials",
        "oauth2.client_id":      "<CLIENT_ID>",
        "oauth2.client_secret":  "<CLIENT_SECRET>",
        "oauth2.token_uri":      "<TOKEN_ENDPOINT>",
        "oauth2.scope":          "<SCOPE>"
    }'::json
);

-- AWS S3 Tables catalog
SELECT pgaa.add_catalog(
    'my_s3tables',
    'iceberg-s3tables',
    '{
        "arn":    "arn:aws:s3tables:<REGION>:<ACCOUNT_ID>:bucket/<BUCKET_NAME>",
        "region": "<REGION>"
    }'::json
);

-- Validate connection before registering (returns NULL on success)
SELECT pgaa.validate_catalog_connection(
    'iceberg-rest',
    '{"url": "<CATALOG_REST_URL>", "token": "<BEARER_TOKEN>"}'::json
);
```

---

## 3. Create a pgaa Table

### Pattern A: Catalog-Managed (Recommended for Iceberg)

```sql
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE analytics.<TABLE_NAME> ()   -- empty column list = auto-discover schema
USING PGAA WITH (
    pgaa.managed_by        = 'my_catalog',
    pgaa.catalog_namespace = '<NAMESPACE>',
    pgaa.catalog_table     = '<CATALOG_TABLE_NAME>',
    pgaa.format            = 'iceberg'   -- 'iceberg' | 'delta' | 'parquet'
);
```

### Pattern B: Direct Storage Location (for Delta / Parquet)

```sql
-- Step 1: Create a pgfs storage location
SELECT pgfs.create_storage_location(
    '<LOCATION_NAME>',
    's3://<BUCKET>/<PREFIX>/',
    '{"region": "<REGION>"}'::json,
    '{"access_key_id": "<KEY>", "secret_access_key": "<SECRET>"}'::json
);

-- Step 2: Create the pgaa table
CREATE TABLE analytics.<TABLE_NAME> ()
USING PGAA WITH (
    pgaa.storage_location = '<LOCATION_NAME>',
    pgaa.path             = '<PATH_WITHIN_LOCATION>',
    pgaa.format           = 'delta'   -- 'delta' | 'parquet' (parquet is read-only)
);
```

### Pattern C: CTAS (Write Query Results to Lakehouse)

```sql
-- Write to catalog-managed Iceberg
CREATE TABLE analytics.<NEW_TABLE_NAME>
USING PGAA WITH (
    pgaa.managed_by           = 'my_catalog',
    pgaa.catalog_namespace    = '<NAMESPACE>',
    pgaa.catalog_table        = '<NEW_CATALOG_TABLE>',
    pgaa.format               = 'iceberg',
    pgaa.purge_data_if_exists = true   -- set true to overwrite if data already exists
) AS
SELECT <COLUMNS> FROM <SOURCE_TABLE> WHERE <CONDITIONS>;
-- Note: source query uses CompatScan (not DirectScan)
```

### Pattern D: Convert an Existing Heap Table In Place

```sql
-- Convert to pgaa (catalog-managed)
ALTER TABLE public.<TABLE_NAME>
    SET ACCESS METHOD pgaa,
    SET (
        pgaa.auto_truncate     = 'true',
        pgaa.format            = 'iceberg',
        pgaa.managed_by        = 'my_catalog',
        pgaa.catalog_namespace = '<NAMESPACE>',
        pgaa.catalog_table     = '<CATALOG_TABLE_NAME>'
    );

-- Convert to pgaa (storage location)
ALTER TABLE public.<TABLE_NAME>
    SET ACCESS METHOD pgaa,
    SET (
        pgaa.auto_truncate    = 'true',
        pgaa.format           = 'delta',
        pgaa.storage_location = '<LOCATION_NAME>',
        pgaa.path             = 'public.<TABLE_NAME>'
    );

-- Revert back to normal heap storage
ALTER TABLE public.<TABLE_NAME> SET ACCESS METHOD heap;
```

---

## 4. Catalog Management Operations

```sql
-- List all registered catalogs
SELECT name, type, status, refreshed_at FROM pgaa.list_catalogs();

-- List tables in a catalog namespace
SELECT * FROM pgaa.list_catalog_tables('my_catalog', '<NAMESPACE_FILTER>');

-- One-time import of all tables from a namespace into PostgreSQL
SELECT pgaa.import_catalog('my_catalog', '<NAMESPACE_FILTER>');

-- Start continuous background sync (live metadata updates)
SELECT pgaa.attach_catalog('my_catalog');

-- Stop continuous sync (table definitions remain)
SELECT * FROM pgaa.detach_catalog('my_catalog', cascade := false);

-- Update catalog credentials/options
SELECT * FROM pgaa.update_catalog(
    'my_catalog',
    '{"url": "<NEW_URL>", "token": "<NEW_TOKEN>"}'::json
);

-- Delete a catalog (cascade := true also drops dependent pgaa tables)
SELECT * FROM pgaa.delete_catalog('my_catalog', cascade := false);

-- Test that catalog connectivity is working (NULL = success)
SELECT pgaa.test_catalog('my_catalog', test_writes := false);
```

---

## 5. Background Maintenance Tasks

```sql
-- Enable the maintenance worker (must be set for tasks to run)
ALTER SYSTEM SET pgaa.enable_maintenance_worker = on;
SELECT pg_reload_conf();

-- Schedule compaction (delta and iceberg)
SELECT pgaa.launch_task(
    'analytics.<TABLE_NAME>'::regclass,
    'compaction',
    '{"target_size": 536870912, "dry_run": false}'::jsonb
);

-- Schedule Z-order optimization (delta only)
SELECT pgaa.launch_task(
    'analytics.<TABLE_NAME>'::regclass,
    'zorder',
    '{"columns": ["<COL1>", "<COL2>"], "target_size": 1073741824}'::jsonb
);

-- Schedule vacuum (delta only)
SELECT pgaa.launch_task(
    'analytics.<TABLE_NAME>'::regclass,
    'vacuum',
    '{"retention_period": "168 hours", "dry_run": false}'::jsonb
);

-- Schedule purge (delta only)
SELECT pgaa.launch_task(
    'analytics.<TABLE_NAME>'::regclass,
    'purge',
    '{"storage_location": "<LOCATION_NAME>", "path": "<PATH>"}'::jsonb
);

-- Check task status
SELECT id, task_type, status, scheduled_at, error
FROM pgaa.background_task
ORDER BY scheduled_at DESC
LIMIT 20;

-- For Iceberg zorder/vacuum/purge, use spark_sql instead:
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://<SPARK_HOST>:15002';

SELECT pgaa.spark_sql(
    'CALL system.rewrite_data_files(table => ''<NAMESPACE>.<TABLE>'')',
    'my_catalog'
);
```

---

## 6. Inspect Tables and Storage

```sql
-- All pgaa tables: format, replication status, storage size
SELECT schema_name, table_name, format, replication_status,
       pg_size_pretty(object_storage_snapshot_size_bytes) AS snapshot,
       pg_size_pretty(object_storage_total_size_bytes) AS total
FROM pgaa.list_analytics_tables();

-- Storage sizes for a specific table
SELECT * FROM pgaa.lakehouse_table_stats('analytics.<TABLE_NAME>'::regclass);

-- Test a storage location (NULL = success)
SELECT pgaa.test_storage_location('<LOCATION_NAME>', test_writes := false);

-- All pgfs storage locations
SELECT name, url FROM pgfs.list_storage_locations();
```

---

## 7. Spark SQL (Spark Connect Engine)

```sql
-- Switch to Spark Connect engine
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://<SPARK_HOST>:15002';

-- Query a single catalog
SELECT pgaa.spark_sql(
    'SELECT <COLS> FROM <NAMESPACE>.<TABLE> WHERE <CONDITIONS>',
    'my_catalog'
);

-- Query across multiple catalogs
SELECT pgaa.spark_sql(
    'SELECT a.*, b.* FROM cat1.<NS1>.<T1> a JOIN cat2.<NS2>.<T2> b ON a.id = b.id',
    ARRAY['my_catalog_1', 'my_catalog_2']
);
-- Returns: JSON object with query results.
-- Note: Spark Connect is READ-ONLY. All writes must go through PostgreSQL.
```
