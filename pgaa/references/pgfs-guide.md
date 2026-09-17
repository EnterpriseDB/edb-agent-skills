# PGFS (Postgres File System) — Reference Guide

PGFS is an EDB PostgreSQL extension that manages **storage locations** — named references to remote object stores (Amazon S3, Azure Blob Storage, Google Cloud Storage, or local filesystem). The `pgaa` extension depends on `pgfs` for direct-access table patterns (Parquet and Delta) and for smoke testing. When `CREATE EXTENSION pgaa CASCADE` is run, `pgfs` is automatically installed.

---

## Core Concepts

- A **storage location** is a named, reusable pointer to an object store prefix.  
- Storage location names are **global** (not per-schema); use prefixes or run IDs when creating multiple test locations to avoid collisions.
- Deleting a storage location **does not delete data in the bucket**; it only removes the PGFS registration.
- Drop all PGAA tables that reference a storage location *before* deleting it — otherwise the table becomes orphaned (visible in `pg_class` but queries will fail).

---

## Storage Location Management

```sql
-- Create a storage location (standard form)
SELECT pgfs.create_storage_location(
    name        text,           -- unique name, e.g. 'my-s3-data'
    url         text,           -- e.g. 's3://bucket-name/prefix/'
    options     json DEFAULT NULL,
    credentials json DEFAULT NULL
);
-- Returns: the name of the created storage location.

-- List all storage locations
SELECT * FROM pgfs.list_storage_locations();
-- Returns: name, url, msl_id (uuid), options (json), credentials (json)

-- Get a specific storage location
SELECT * FROM pgfs.get_storage_location('my-s3-data');

-- Get the configured default storage location
SELECT * FROM pgfs.get_default_storage_location();

-- Update a storage location
SELECT pgfs.update_storage_location(
    name        text,
    url         text,
    options     json DEFAULT NULL,
    credentials json DEFAULT NULL
);

-- Delete a storage location (does NOT delete bucket data)
SELECT pgfs.delete_storage_location('my-s3-data');
```

### Options JSON

Key fields for the `options` parameter:

| Key | Example | Purpose |
|---|---|---|
| `aws_skip_signature` | `"true"` | Anonymous access for public buckets |
| `region` | `"us-east-1"` | AWS region |

### Credentials JSON

For private buckets without an IAM role:

```json
{
  "access_key_id": "AKIA...",
  "secret_access_key": "..."
}
```

> **Note:** When using an Iceberg REST catalog (e.g., Lakekeeper), the catalog vends credentials automatically. Pass credentials via PGFS only for **direct storage access** that bypasses a catalog.

---

## Testing a Storage Location

```sql
SELECT pgaa.test_storage_location('my-s3-data', true);
-- Returns NULL on success, or an error message string on failure.
-- Set second argument to false to skip write tests.
```

---

## Smoke Test — Public S3 Bucket (Delta Format)

This pattern verifies that `pgaa` + `pgfs` are functional without requiring a catalog or private credentials. The public bucket contains TPC-H Scale Factor 1 sample data in Delta format.

```sql
-- 1. Create schema and storage location
CREATE SCHEMA IF NOT EXISTS smoke_test;

SELECT pgfs.create_storage_location(
    'smoke-test-data',
    's3://beacon-analytics-demo-data-us-east-1-prod',
    '{"aws_skip_signature": "true"}'
);

-- 2. Create a PGAA table pointing to the customer table (150,000 rows)
CREATE TABLE smoke_test.customer () USING PGAA WITH (
    pgaa.storage_location = 'smoke-test-data',
    pgaa.path             = 'tpch_sf_1/customer'
);
-- Note: empty column list () triggers automatic schema discovery

-- 3. Verify row count (expected: 150000)
SELECT COUNT(*) FROM smoke_test.customer;

-- 4. Inspect the query plan to confirm engine offload
EXPLAIN SELECT COUNT(*) FROM smoke_test.customer;
-- On a Lakehouse/PGD cluster: look for "SeafowlDirectScan"
-- On WarehousePG (WHPG):      look for "SeafowlCompatScan"

-- 5. Clean up (order matters: table before storage location)
DROP TABLE smoke_test.customer CASCADE;
DROP SCHEMA smoke_test CASCADE;
SELECT pgfs.delete_storage_location('smoke-test-data');
```

**Available TPC-H SF1 tables** at `s3://beacon-analytics-demo-data-us-east-1-prod/tpch_sf_1/`:

| Table | Approx. Rows |
|---|---|
| `customer` | 150,000 |
| `lineitem` | 6,001,215 |
| `orders`, `part`, `partsupp`, `nation`, `region`, `supplier` | Standard TPC-H sizes |

Use `customer` for quick smoke tests (smallest table). Avoid `lineitem` in loops.

---

## Using PGFS with PGAA (Direct Storage Pattern)

For Delta or Parquet files not managed by an Iceberg catalog, create a storage location and reference it in `CREATE TABLE`:

```sql
-- Create location
SELECT pgfs.create_storage_location(
    'my-delta-store',
    's3://my-analytics-bucket/data/',
    NULL,
    '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);

-- Create PGAA table (Delta format)
CREATE TABLE analytics.events () USING PGAA WITH (
    pgaa.storage_location = 'my-delta-store',
    pgaa.path             = 'events/',
    pgaa.format           = 'delta'
);

-- Create PGAA table (Parquet format — read-only)
CREATE TABLE analytics.reports () USING PGAA WITH (
    pgaa.storage_location = 'my-delta-store',
    pgaa.path             = 'reports/2024/',
    pgaa.format           = 'parquet'
);
```

> **Iceberg best practice:** For Iceberg tables, use `pgaa.add_catalog()` and the catalog-managed pattern instead of a storage location. PGFS direct access is the right choice for Delta and Parquet, or for quick smoke tests.

---

## Cleanup Gotchas

1. **Drop tables before deleting their storage location.** A storage location with dependent PGAA tables cannot be cleanly deleted; the tables become orphaned.
2. `pgfs.delete_storage_location()` removes only the PGFS registration — **data in the bucket is untouched**.
3. On PGD, dropping a replicated PGAA table does not remove the underlying Iceberg data. Clean up via `pgd.purge_analytics_target = true` at create time, or manually using PyIceberg after the fact.
4. Storage location names are **globally scoped** — use unique prefixes (e.g., a run ID) in test environments to prevent collisions.

---

## Internal / Rarely-Called Functions

| Function | Purpose |
|---|---|
| `pgfs.storage_fdw_handler()` | FDW handler (used internally by CREATE SERVER) |
| `pgfs.storage_fdw_validator()` | FDW option validator |
| `pgfs.storage_fdw_meta()` | Returns name, version, author, website |
| `pgfs.set_default_storage_location(name)` | Set a default storage location |
| `pgfs.create_foreign_table(table_name, server_name)` | Create a foreign table from a storage location |
| `pgfs.create_storage_location_with_foreign_table(...)` | Create storage location and foreign table together |
