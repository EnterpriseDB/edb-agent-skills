---
name: pgfs
description: >
  Skill for operating pgfs, an EDB PostgreSQL extension (Rust/pgrx) that provides
  SQL access to filesystem-like cloud and local storage backends — AWS S3, Azure Blob
  Storage, Azure Data Lake Storage Gen2, Google Cloud Storage, local filesystem, and
  in-memory — through foreign data wrappers. Use this skill when a user wants to:
  install pgfs; create, list, update, or delete storage locations; create foreign
  tables over object storage; read object content (body); upload, update, or delete
  objects via SQL DML; configure the local filesystem allowlist GUC; troubleshoot
  body-null behavior, non-transactional DML, LIMIT pushdown, or UPDATE key-rename
  rejections; or understand how pgfs underpins pgaa (EDB Postgres Analytics Accelerator).
metadata:
  product: pgfs
  aliases: [pgfs]
  requires_pg: ">=14"
  language: sql
  extension_schema: pgfs
  superuser_required: true
---

# pgfs Agent Skill

pgfs is a PostgreSQL extension that exposes cloud and local object storage as SQL foreign tables. It uses the FDW (Foreign Data Wrapper) mechanism. All SQL functions live in the `pgfs` schema.

> **References:**
> - Full function signatures: [references/function-reference.md](references/function-reference.md)
> - Troubleshooting & edge cases: [references/troubleshooting.md](references/troubleshooting.md)
> - Copy-paste SQL templates: [assets/storage_location_templates.sql](assets/storage_location_templates.sql)
> - Diagnostic SQL generator: Run `scripts/pgfs_check.py` (or `python3 scripts/pgfs_check.py --storage-location NAME`)

---

## 1. Installation

Requires a **superuser** connection. PostgreSQL **14 or later** is required for full FDW functionality (scan + DML). PG12/PG13 installs a noop handler (0 rows, no DML).

```sql
CREATE EXTENSION pgfs CASCADE;
```

`CASCADE` automatically installs any missing dependencies.

---

## 2. Core Concepts

| Concept | PostgreSQL Object | Created By |
|---|---|---|
| **Storage location** | `FOREIGN SERVER` + `USER MAPPING` | `pgfs.create_storage_location(...)` |
| **Foreign table** | `FOREIGN TABLE` | `pgfs.create_foreign_table(...)` |
| **Combined setup** | Both at once | `pgfs.create_storage_location_with_foreign_table(...)` |

A **storage location** is a named pointer to a backend URL (bucket, container, path). A **foreign table** exposes objects at that location as rows with the fixed schema:

| Column | Type | Notes |
|---|---|---|
| `key` | `TEXT` | Object path relative to the location |
| `size` | `BIGINT` | Size in bytes |
| `last_modified` | `TIMESTAMPTZ` | Last modification time |
| `e_tag` | `TEXT` | ETag/version hash (may be NULL) |
| `body` | `BYTEA` | Content — **only fetched when `WHERE key = '...'` is present** |

---

## 3. Supported Backends

| Backend | URL Scheme |
|---|---|
| AWS S3 | `s3://bucket-name` |
| Azure Blob Storage | `az://container-name` |
| Azure Data Lake Storage Gen2 | `abfss://<container>@<account>.dfs.core.windows.net/<path>` |
| Google Cloud Storage | `gs://bucket-name` |
| Local filesystem | `file:///absolute/path` |
| In-memory (testing) | `memory://` |

---

## 4. Creating a Storage Location

```sql
SELECT pgfs.create_storage_location(
    'my_s3_bucket',                    -- name (unique, used to reference this location)
    's3://my-actual-bucket-name',      -- backend URL
    '{"region": "us-east-1"}',        -- options JSON (backend-specific)
    '{"access_key_id": "AKIA...",
      "secret_access_key": "..."}'     -- credentials JSON
);
```

**Then create a foreign table:**
```sql
SELECT pgfs.create_foreign_table('my_files', 'my_s3_bucket');
-- Optional: scope to a sub-path
SELECT pgfs.create_foreign_table('reports', 'my_s3_bucket', '/reports/2024/');
```

**Or do both in one call** (creates table named `<location>_ft`):
```sql
SELECT pgfs.create_storage_location_with_foreign_table(
    'my_s3_bucket', 's3://my-actual-bucket-name',
    '{"region": "us-east-1"}',
    '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);
-- Creates storage location + foreign table named 'my_s3_bucket_ft'
```

See [assets/storage_location_templates.sql](assets/storage_location_templates.sql) for per-backend templates (Azure, GCS, local, in-memory).

---

## 5. Querying Foreign Tables

```sql
-- List objects (body is NULL without key filter — intentional)
SELECT key, size, last_modified FROM my_files;
SELECT key, size FROM my_files LIMIT 100;

-- Read object content (MUST include WHERE key = '...')
SELECT body FROM my_files WHERE key = 'path/to/file.txt';

-- Cast body to text
SELECT convert_from(body, 'UTF8') AS content
FROM my_files WHERE key = 'readme.md';
```

### ⚠️ body Column Rule
`body` is **NULL** unless the query has **both**: (1) a reference to `body` and (2) a `WHERE key = 'exact-key'` equality filter. No error is raised — it silently returns NULL. This prevents accidental bulk downloads. See [references/troubleshooting.md](references/troubleshooting.md) for detailed examples.

---

## 6. DML on Foreign Tables

```sql
-- Upload (INSERT)
INSERT INTO my_files (key, body)
VALUES ('hello.txt', 'Hello World!'::bytea);

-- Update content (key column CANNOT be changed via UPDATE)
UPDATE my_files SET body = 'New content'::bytea WHERE key = 'hello.txt';

-- Rename = DELETE + INSERT (object store ops are NOT transactional)
INSERT INTO my_files (key, body)
    SELECT 'renamed.txt', body FROM my_files WHERE key = 'hello.txt';
DELETE FROM my_files WHERE key = 'hello.txt';

-- Delete
DELETE FROM my_files WHERE key = 'hello.txt';
```

### ⚠️ Non-Transactional Warning
Object store operations (INSERT/UPDATE/DELETE) **cannot be rolled back** if the surrounding PostgreSQL transaction aborts. Design workflows to be idempotent.

### ⚠️ UPDATE Cannot Rename
`UPDATE my_files SET key = 'new.txt' WHERE key = 'old.txt'` is rejected with an error. Use DELETE + INSERT instead.

---

## 7. Managing Storage Locations

```sql
-- List all (credentials masked — values shown as <redacted>)
SELECT name, url, options FROM pgfs.list_storage_locations();

-- Get one with real credentials
SELECT * FROM pgfs.get_storage_location('my_s3_bucket');

-- Update (replaces all options and credentials atomically via delete+recreate)
SELECT pgfs.update_storage_location(
    'my_s3_bucket', 's3://new-bucket', '{"region": "eu-west-1"}',
    '{"access_key_id": "...", "secret_access_key": "..."}'
);

-- Delete (does NOT drop foreign tables — drop them first)
DROP FOREIGN TABLE IF EXISTS my_files;
SELECT pgfs.delete_storage_location('my_s3_bucket');

-- Default storage location
SELECT pgfs.set_default_storage_location('my_s3_bucket');
SELECT * FROM pgfs.get_default_storage_location();
```

---

## 8. Local Filesystem Configuration

Local filesystem storage locations are restricted by the `pgfs.allowed_local_fs_paths` GUC (colon-separated path prefixes, default: `/tmp/pgfs`).

```sql
-- Session-level (resets after disconnect)
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';

-- Then create storage location
SELECT pgfs.create_storage_location('local_data', 'file:///data/shared');
SELECT pgfs.create_foreign_table('local_files', 'local_data');
```

Add to `postgresql.conf` for persistence:
```
pgfs.allowed_local_fs_paths = '/data/shared'
```

---

## 9. Diagnostic Queries

```sql
-- Check extension is installed
SELECT installed_version FROM pg_available_extensions WHERE name = 'pgfs';

-- List all storage locations
SELECT name, url FROM pgfs.list_storage_locations();

-- List all pgfs foreign tables
SELECT schema_name, server_name, ft_name FROM pgfs.v_foreign_tables;

-- Check GUC
SHOW pgfs.allowed_local_fs_paths;

-- pgfs version
SELECT pgfs.pgfs_version();
```

Run `python3 scripts/pgfs_check.py` to generate a full diagnostic SQL report, or `python3 scripts/pgfs_check.py --storage-location NAME` for a specific location.

---

## 10. LIMIT Pushdown Behavior

`LIMIT` is pushed down to stop listing early **only for simple queries**:

| Query type | LIMIT pushed down? |
|---|---|
| `SELECT key FROM t LIMIT 10` | ✓ Yes |
| `SELECT key FROM t ORDER BY last_modified LIMIT 10` | ✗ No (full scan) |
| `SELECT DISTINCT key FROM t LIMIT 10` | ✗ No |
| `SELECT COUNT(*) FROM t` | ✗ No (full scan) |
| Queries with aggregates, window functions, UNION, INTERSECT | ✗ No |

---

## 11. Relationship to pgaa

pgfs is the storage-location layer beneath **pgaa** (EDB Postgres Analytics Accelerator). pgaa declares `requires = 'pgfs'`, so pgfs must be installed first. When helping users configure pgaa analytics tables, use pgfs functions to set up the storage locations that pgaa reads from. Do not re-explain pgfs's SQL surface when documenting pgaa — refer back to this skill.

---

## 12. Common Workflow (End-to-End)

```sql
-- Step 1: Install (superuser)
CREATE EXTENSION pgfs CASCADE;

-- Step 2: Create storage location
SELECT pgfs.create_storage_location(
    'prod_bucket', 's3://my-prod-bucket',
    '{"region": "us-east-1"}',
    '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);

-- Step 3: Create foreign table
SELECT pgfs.create_foreign_table('prod_files', 'prod_bucket');

-- Step 4: Browse objects
SELECT key, size, last_modified FROM prod_files LIMIT 50;

-- Step 5: Read a specific file
SELECT convert_from(body, 'UTF8') FROM prod_files WHERE key = 'config/settings.json';

-- Step 6: Upload a file
INSERT INTO prod_files (key, body) VALUES ('output/result.csv', 'col1,col2\n1,2'::bytea);
```