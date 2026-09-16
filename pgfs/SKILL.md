---
name: pgfs
description: >
  Skill for operating the pgfs PostgreSQL extension, which provides SQL-level access to
  object storage backends (AWS S3, Azure Blob Storage, Azure Data Lake Storage Gen2,
  Google Cloud Storage, local filesystem, and in-memory) via foreign data wrappers.
  Trigger this skill when a user asks to install pgfs, create or manage storage locations,
  create or query foreign tables over object stores, read or write object content, configure
  pgfs GUC parameters, or troubleshoot pgfs operations. Also applies when configuring storage
  locations for downstream EDB extensions such as pgaa (EDB Postgres Analytics Accelerator)
  that require pgfs as a dependency.
metadata:
  version: "3.2.1"
  language: SQL / Rust (pgrx)
  postgres_versions: "PG14+ (full DML); PG12/13 noop handler only"
  backends: "s3://, az://, abfss://, gs://, file://, memory://"
  references:
    - references/function-reference.md
    - references/egress-and-security.md
    - assets/storage-location-templates.md
  scripts:
    - scripts/pgfs_diagnose.py
---

# pgfs Agent Skill

## Overview

pgfs is a PostgreSQL extension (built in Rust/pgrx) that exposes object storage as
**foreign tables** with full DML support. The agent can use this skill to:

1. Install the extension
2. Create and manage **storage locations** (named foreign servers pointing at buckets/containers)
3. Create **foreign tables** over those locations
4. Read object metadata and content via `SELECT`
5. Upload, overwrite, and delete objects via `INSERT`, `UPDATE`, `DELETE`
6. Configure GUC parameters (egress, local filesystem allowlists)
7. Diagnose problems

See [references/function-reference.md](references/function-reference.md) for full function signatures.
See [assets/storage-location-templates.md](assets/storage-location-templates.md) for copy-paste SQL templates per backend.
See [references/egress-and-security.md](references/egress-and-security.md) for security configuration details.

---

## Step 1 — Install the Extension

```sql
CREATE EXTENSION pgfs CASCADE;
```

- Requires **superuser**.
- `CASCADE` installs the `pgfs_fdw` foreign data wrapper automatically.
- The extension schema is `pgfs`; all functions are in that schema.

---

## Step 2 — Create a Storage Location

A storage location is a named foreign server. Create it once; reference it everywhere.

```sql
SELECT pgfs.create_storage_location(
    name        => 'my_bucket',
    url         => 's3://my-bucket-name',
    options     => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);
```

**Or create location + foreign table in one call** (auto-names table `<name>_ft`):

```sql
SELECT pgfs.create_storage_location_with_foreign_table(
    'my_bucket', 's3://my-bucket-name',
    options     => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);
-- Creates foreign table: my_bucket_ft
```

### URL Schemes by Backend

| Backend | URL Format |
|---|---|
| AWS S3 | `s3://bucket-name` |
| Azure Blob Storage | `az://container-name` |
| Azure Data Lake Gen2 | `abfss://container@account.dfs.core.windows.net/path` |
| Google Cloud Storage | `gs://bucket-name` |
| Local filesystem | `file:///absolute/path` |
| In-memory (testing) | `memory://` |

For backend-specific credential fields, see [assets/storage-location-templates.md](assets/storage-location-templates.md).

---

## Step 3 — Create a Foreign Table

```sql
-- Over the whole bucket (root path):
SELECT pgfs.create_foreign_table('my_files', 'my_bucket');

-- Over a sub-path prefix:
SELECT pgfs.create_foreign_table('my_reports', 'my_bucket', '/reports/2024/');

-- Schema-qualified:
SELECT pgfs.create_foreign_table('myschema.my_files', 'my_bucket', '/data/');
```

Every foreign table has this fixed schema:

| Column | Type | Description |
|---|---|---|
| `key` | `TEXT` | Object path/name, relative to the storage location |
| `size` | `BIGINT` | Object size in bytes |
| `last_modified` | `TIMESTAMPTZ` | Last modification timestamp |
| `e_tag` | `TEXT` | ETag (version hash), if available |
| `body` | `BYTEA` | Object content — **lazy loaded** (see below) |

---

## Step 4 — Query and Modify Objects

### Listing objects (no body download)

```sql
SELECT key, size, last_modified, e_tag FROM my_files;
SELECT key, size FROM my_files LIMIT 50;
```

### Reading a single object's content

```sql
-- body is only populated when a WHERE key = '...' filter is present:
SELECT body FROM my_files WHERE key = 'path/to/file.txt';

-- Decode as text:
SELECT convert_from(body, 'UTF8') FROM my_files WHERE key = 'readme.md';
```

### Uploading (INSERT)

```sql
INSERT INTO my_files (key, body) VALUES ('hello.txt', 'Hello, World!'::bytea);
```

### Overwriting content (UPDATE)

```sql
UPDATE my_files SET body = 'Updated'::bytea WHERE key = 'hello.txt';
-- Cannot change key via UPDATE — use DELETE + INSERT to rename.
```

### Deleting (DELETE)

```sql
DELETE FROM my_files WHERE key = 'hello.txt';
```

### Renaming an object

```sql
-- No atomic rename — use DELETE + INSERT:
INSERT INTO my_files (key, body)
  SELECT 'new/path.txt', body FROM my_files WHERE key = 'old/path.txt';
DELETE FROM my_files WHERE key = 'old/path.txt';
```

---

## Step 5 — Manage Storage Locations

```sql
-- List all (credentials masked):
SELECT * FROM pgfs.list_storage_locations();

-- Get one with real credentials:
SELECT * FROM pgfs.get_storage_location('my_bucket');

-- Update (replaces server + user mapping):
SELECT pgfs.update_storage_location('my_bucket', 's3://new-bucket',
    options => '{"region": "us-west-2"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);

-- Delete (does NOT drop associated foreign tables):
SELECT pgfs.delete_storage_location('my_bucket');

-- Default location management:
SELECT pgfs.set_default_storage_location('my_bucket');
SELECT * FROM pgfs.get_default_storage_location();
```

---

## Step 6 — Configure GUC Parameters

### Local filesystem allowlist (required before using `file://`)

```sql
-- Set in session (superuser only):
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';

-- Or permanently in postgresql.conf:
-- pgfs.allowed_local_fs_paths = '/data/shared'
```

Default: `/tmp/pgfs`. Empty string = deny all local paths.

### Egress allowlist (optional, limits outbound connections)

```ini
# postgresql.conf
pgfs.egress_allowlist = '.s3.amazonaws.com,.storage.googleapis.com'
edb.egress_allowlist  = '.amazonaws.com,.googleapis.com,.core.windows.net'
```

See [references/egress-and-security.md](references/egress-and-security.md) for full details.

---

## Critical Constraints the Agent Must Enforce

1. **Non-transactional writes**: INSERT/UPDATE/DELETE on foreign tables **cannot be rolled back**. Successful object store writes persist even if the PostgreSQL transaction aborts. Warn users before DML in transaction blocks where rollback might be expected.

2. **UPDATE cannot rename objects**: Changing the `key` column in an UPDATE is rejected with an error. Always guide users to DELETE + INSERT for renames.

3. **`body` is lazy**: `SELECT *` or `SELECT body` without `WHERE key = '...'` returns NULL for `body`. Only a plain equality filter on `key` triggers a content fetch.

4. **LIMIT pushdown only for simple queries**: LIMIT is pushed down to the object store scan only for queries without `ORDER BY`, `GROUP BY`, `DISTINCT`, aggregates, window functions, or set operations. For complex queries, all objects may be fetched before LIMIT applies.

5. **Local filesystem path restriction**: `file://` locations outside the `pgfs.allowed_local_fs_paths` allowlist are rejected. The GUC must be set (superuser) before creating such locations.

6. **PG12/PG13 limitation**: On PG12 and PG13 (including WHPG), pgfs installs a noop FDW handler that returns 0 rows and does not support DML. Full functionality requires PG14+.

7. **`delete_storage_location` does not drop foreign tables**: Foreign tables built on a deleted storage location will error at query time. Drop them manually with `DROP FOREIGN TABLE`.

8. **`options` and `credentials` keys must not contain `=`**: Option keys containing `=` are rejected by the storage location creation functions (catalog storage format conflict).

---

## Diagnostic Workflow

When a user reports issues, run the diagnostic script or execute these queries manually:

```sql
-- 1. Confirm extension is installed:
SELECT extversion FROM pg_extension WHERE extname = 'pgfs';

-- 2. List storage locations:
SELECT * FROM pgfs.list_storage_locations();

-- 3. List foreign tables:
SELECT * FROM pgfs.v_foreign_tables;

-- 4. Check GUCs:
SHOW pgfs.allowed_local_fs_paths;

-- 5. Test a storage location (should return rows if bucket is non-empty):
SELECT key, size FROM <foreign_table_name> LIMIT 5;
```

Or run the diagnostic script (requires `psycopg2`):

```bash
PGFS_DSN="host=localhost dbname=mydb user=postgres" python3 scripts/pgfs_diagnose.py
```

See [scripts/pgfs_diagnose.py](scripts/pgfs_diagnose.py).

---

## Common Error Patterns

| Error | Likely Cause | Resolution |
|---|---|---|
| `extension "pgfs" does not exist` | Not installed | `CREATE EXTENSION pgfs CASCADE;` |
| `permission denied` on `create_storage_location` | Not superuser | Requires superuser role |
| `body` column is NULL | No `WHERE key = '...'` filter | Add exact key filter to query |
| `Cannot rename objects via UPDATE` | Tried to change `key` in UPDATE | Use DELETE + INSERT |
| `path ... not allowed` (file backend) | Path outside allowlist | Set `pgfs.allowed_local_fs_paths` |
| `server "x" already exists` | Duplicate location name | Use unique name or `delete_storage_location` first |
| `storage location "x" does not exist` | Bad name or already deleted | Verify with `list_storage_locations()` |
| Credential error from cloud provider | Wrong credentials or permissions | Check `get_storage_location()` for real credential values |
| `0 rows` on PG12/PG13 | Noop FDW on unsupported PG version | Upgrade to PG14+ for full support |

---

## Relationship to pgaa (EDB Postgres Analytics Accelerator)

pgfs is a **required dependency** of pgaa (`pgaa.control: requires = 'pgfs'`). When setting up pgaa analytics tables, pgfs storage locations define where raw data lives. Configure pgfs first, then reference the storage location name when creating pgaa tables. Do not re-explain pgfs's SQL surface when documenting pgaa — treat pgfs as the storage layer underneath it.

---

## Views Available for Inspection

```sql
-- All server-level options for pgfs storage locations:
SELECT * FROM pgfs.v_server_options;

-- All foreign tables backed by pgfs:
SELECT * FROM pgfs.v_foreign_tables;

-- Per-option breakdown of foreign table OPTIONS:
SELECT * FROM pgfs.v_foreign_table_options;
```