# pgfs (Postgres File System) — Reference

pgfs is an EDB PostgreSQL extension that manages **storage locations**: named references to remote object stores (Amazon S3, Azure Blob Storage, Google Cloud Storage, or the local filesystem). The pgaa extension uses pgfs storage locations to access Delta Lake and Parquet tables without an Iceberg catalog. pgfs is auto-installed when you run `CREATE EXTENSION pgaa CASCADE`.

---

## Core Functions

### Create a Storage Location

```sql
SELECT pgfs.create_storage_location(
    name        TEXT,            -- unique identifier (e.g., 'my-s3-bucket')
    url         TEXT,            -- object store URL (e.g., 's3://bucket-name/prefix/')
    options     JSON DEFAULT NULL,
    credentials JSON DEFAULT NULL
);
-- Returns: the name of the created storage location
```

**Common `options` values:**

| JSON key | Purpose |
|---|---|
| `"aws_skip_signature": "true"` | Anonymous/public bucket access — no credentials needed |
| `"region": "us-east-1"` | AWS region |

**Common `credentials` values (private buckets):**

```json
{
  "access_key_id": "AKIA...",
  "secret_access_key": "..."
}
```

> Note: When using an Iceberg REST catalog (e.g. Lakekeeper), credentials are normally vended by the catalog. Pass pgfs credentials only for direct, catalog-free storage access.

### List / Get Storage Locations

```sql
SELECT * FROM pgfs.list_storage_locations();
-- Returns: name, url, msl_id (uuid), options (json), credentials (json)

SELECT * FROM pgfs.get_storage_location('my-s3-bucket');
-- Returns the same columns for a single location

SELECT * FROM pgfs.get_default_storage_location();
-- Returns: default_storage_location (text)
```

### Update / Delete

```sql
SELECT pgfs.update_storage_location(
    name        TEXT,
    url         TEXT,
    options     JSON DEFAULT NULL,
    credentials JSON DEFAULT NULL
);

SELECT pgfs.delete_storage_location('my-s3-bucket');
-- Returns: void. Does NOT delete any data in the bucket.
```

### Set Default (rarely needed)

```sql
SELECT pgfs.set_default_storage_location('my-s3-bucket');
```

> On PGD, prefer a default catalog over a default storage location for Iceberg workflows. Setting a default storage location is generally only useful for quick Delta/Parquet access.

### Test a Storage Location

```sql
SELECT pgaa.test_storage_location('my-s3-bucket', test_writes := false);
-- Returns NULL on success, error message on failure.
```

### Version / Metadata

```sql
SELECT pgfs.storage_fdw_meta();
-- Returns extension name, version, author, website.
```

---

## Smoke Test Pattern (Public Bucket)

This pattern validates that pgaa and pgfs are installed and can read from Delta Lake without any catalog or credentials. The public demo bucket contains TPC-H Scale Factor 1 data.

```sql
-- 1. Create an isolated schema
CREATE SCHEMA IF NOT EXISTS smoke_test;

-- 2. Register the public demo storage location
SELECT pgfs.create_storage_location(
    'smoke-test-data',
    's3://beacon-analytics-demo-data-us-east-1-prod',
    '{"aws_skip_signature": "true"}'
);

-- 3. Create a pgaa table pointing to the customer table (Delta format, default)
CREATE TABLE smoke_test.customer () USING PGAA WITH (
    pgaa.storage_location = 'smoke-test-data',
    pgaa.path             = 'tpch_sf_1/customer'
);

-- 4. Verify row count (expected: 150,000)
SELECT COUNT(*) FROM smoke_test.customer;

-- 5. Confirm pgaa is executing the query (not Postgres heap)
EXPLAIN SELECT COUNT(*) FROM smoke_test.customer;
-- On a Lakehouse/PGD cluster: look for "SeafowlDirectScan"
-- On WarehousePG:             look for "SeafowlCompatScan" (DirectScan falls back on Greenplum)

-- 6. Clean up (drop table BEFORE deleting storage location)
DROP TABLE smoke_test.customer CASCADE;
DROP SCHEMA smoke_test CASCADE;
SELECT pgfs.delete_storage_location('smoke-test-data');
```

**Available TPC-H SF1 tables** in `s3://beacon-analytics-demo-data-us-east-1-prod/tpch_sf_1/`:
- `customer` — 150,000 rows (best for smoke tests; small)
- `lineitem` — 6,001,215 rows (use sparingly; large)
- Other standard TPC-H tables: `nation`, `orders`, `part`, `partsupp`, `region`, `supplier`

---

## Cleanup Gotchas

- **Deleting a storage location does NOT delete data in the bucket** — it only removes the pgfs registration.
- **Drop pgaa tables BEFORE deleting their storage location.** Deleting the location first leaves the table as an orphan in `pg_class`; subsequent queries against it fail.
- **Storage location names are global** (not per-schema). For isolated test runs, prefix names with a unique run identifier.
- On PGD, dropping a replicated table does not remove the Iceberg data in object storage. Use `pgd.purge_analytics_target=true` on creation, or clean up via PyIceberg, for catalog-managed tables.
