---
name: pgfs
description: >
  Skill for operating pgfs, an EDB PostgreSQL extension (Rust/pgrx) that exposes
  filesystem-like object storage — AWS S3, Azure Blob Storage, Azure Data Lake Storage Gen2,
  Google Cloud Storage, local filesystem, and in-memory — via PostgreSQL foreign data wrappers.
  Trigger this skill whenever a user needs to: install pgfs; create, update, list, or delete
  storage locations (named foreign servers) or their associated foreign tables; read, upload,
  update, or delete objects stored in cloud/local buckets through SQL DML; configure
  pgfs GUC parameters; or understand the body-on-demand, LIMIT pushdown, and
  non-transactional semantics of pgfs foreign tables. Also applies when configuring
  storage locations consumed by dependent extensions such as pgaa (EDB Postgres Analytics
  Accelerator), which requires pgfs as its storage layer.
metadata:
  aliases: [pgfs]
  extension_schema: pgfs
  install_command: "CREATE EXTENSION pgfs CASCADE"
  superuser_required: true
  source_repo: https://github.com/EnterpriseDB/pgfs.git
  docs: https://www.enterprisedb.com/docs/pg_extensions/pgfs/
---

# pgfs Agent Skill

pgfs gives PostgreSQL SQL-level access to object storage through foreign data wrappers.
Two primitives cover all usage: **storage locations** (named foreign servers, configured once)
and **foreign tables** (SQL tables that list and access objects in a location).

## Quick Reference

- Full SQL templates → [assets/pgfs_sql_templates.sql](assets/pgfs_sql_templates.sql)
- All function signatures → [references/function-reference.md](references/function-reference.md)
- Credential setup per backend → [references/credentials-guide.md](references/credentials-guide.md)
- Diagnostic script (Python) → [scripts/pgfs_diagnostics.py](scripts/pgfs_diagnostics.py)

---

## 1. Installation

```sql
CREATE EXTENSION pgfs CASCADE;
```

Requires superuser. `CASCADE` installs any prerequisite extensions automatically.
Verify: `SELECT pgfs.pgfs_version();`

---

## 2. Supported Backends

| Backend | URL scheme |
|---|---|
| AWS S3 | `s3://bucket` |
| Azure Blob Storage | `az://container` |
| Azure Data Lake Storage Gen2 | `abfss://<container>@<account>.dfs.core.windows.net/<path>` |
| Google Cloud Storage | `gs://bucket` |
| Local filesystem | `file:///absolute/path` |
| In-memory (testing only) | `memory://` |

---

## 3. Creating a Storage Location

A storage location is a named pointer to a bucket/container. Create it once; reference it everywhere.

```sql
-- Minimum form (name + URL)
SELECT pgfs.create_storage_location('my_s3', 's3://my-bucket-name');

-- With region option and static credentials
SELECT pgfs.create_storage_location(
    'my_s3',
    's3://my-bucket-name',
    options     => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);

-- One-shot: create storage location AND a foreign table named my_s3_ft
SELECT pgfs.create_storage_location_with_foreign_table(
    'my_s3',
    's3://my-bucket-name',
    options     => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);
```

For secrets that must not be persisted in the catalog, use `credentials_env`:

```sql
-- Only the env var NAME is stored; the secret is resolved at use time
SELECT pgfs.create_storage_location(
    'my_s3',
    's3://my-bucket-name',
    options         => '{"region": "us-east-1"}',
    credentials     => NULL,
    credentials_env => 'PGFS_S3_CREDS'   -- must start with PGFS_ by default
);
```

See [references/credentials-guide.md](references/credentials-guide.md) for per-backend
credential keys and `credentials_env` security rules.

---

## 4. Creating a Foreign Table

```sql
-- Simple (path defaults to '/')
SELECT pgfs.create_foreign_table('my_files', 'my_s3');

-- Scoped to a sub-path
SELECT pgfs.create_foreign_table('my_files', 'my_s3', '/data/2024/');

-- Schema-qualified table name
SELECT pgfs.create_foreign_table('analytics.raw_files', 'my_s3', '/raw/');
```

Every foreign table created by pgfs has this **fixed schema**:

| Column | Type | Description |
|---|---|---|
| `key` | `TEXT` | Object path/name, relative to the storage location |
| `size` | `BIGINT` | Object size in bytes |
| `last_modified` | `TIMESTAMPTZ` | Last modification timestamp |
| `e_tag` | `TEXT` | Object ETag (version hash), if available |
| `body` | `BYTEA` | Object content — **lazy; only populated with a `WHERE key = '...'` filter** |

---

## 5. Using a Foreign Table

### Listing objects

```sql
-- body is NULL for all rows without a key filter — this is intentional
SELECT key, size, last_modified, e_tag FROM my_files;

-- Bounded listing for large buckets (LIMIT pushdown applies to simple queries)
SELECT key, size FROM my_files LIMIT 100;
```

### Reading object content

```sql
-- body is populated ONLY when both: (a) selected, (b) WHERE key = '...' filter present
SELECT key, convert_from(body, 'UTF8') AS content
FROM my_files
WHERE key = 'readme.txt';

-- Raw bytes
SELECT body FROM my_files WHERE key = 'data/file.bin';
```

### Uploading (INSERT)

```sql
INSERT INTO my_files (key, body)
VALUES ('hello.txt', 'Hello, world!'::bytea);

-- Empty object
INSERT INTO my_files (key) VALUES ('empty.txt');
```

### Updating object content

```sql
-- Update content — OK
UPDATE my_files SET body = 'Updated content'::bytea WHERE key = 'hello.txt';

-- ⚠️ Renaming via UPDATE is REJECTED — use DELETE + INSERT instead
DELETE FROM my_files WHERE key = 'old-name.txt';
INSERT INTO my_files (key, body) VALUES ('new-name.txt', <body_value>);
```

### Deleting objects

```sql
DELETE FROM my_files WHERE key = 'hello.txt';
```

---

## 6. Managing Storage Locations

```sql
-- List all (credential values are masked — use get_storage_location for real values)
SELECT * FROM pgfs.list_storage_locations();

-- Inspect one location with real credential values
SELECT * FROM pgfs.get_storage_location('my_s3');

-- Update (delete-and-recreate; supply full new configuration)
SELECT pgfs.update_storage_location(
    'my_s3', 's3://new-bucket',
    options     => '{"region": "eu-west-1"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);

-- Delete (does NOT auto-drop associated foreign tables)
SELECT pgfs.delete_storage_location('my_s3');

-- Default storage location (used by dependent tools like pgaa)
SELECT pgfs.set_default_storage_location('my_s3');
SELECT * FROM pgfs.get_default_storage_location();
```

---

## 7. Introspection Views

```sql
SELECT * FROM pgfs.v_server_options;         -- all server-level options per storage location
SELECT * FROM pgfs.v_foreign_tables;         -- all pgfs foreign tables
SELECT * FROM pgfs.v_foreign_table_options;  -- all foreign table options
```

---

## 8. GUC Configuration

| GUC | Default | Description |
|---|---|---|
| `pgfs.allowed_local_fs_paths` | `/tmp/pgfs` | Colon-separated allowed local path prefixes for `file://` backend |
| `pgfs.env_var_allowed_prefix` | `PGFS_` | Colon-separated required prefixes for `credentials_env` env var names |
| `pgfs.egress_allowlist` | (unset) | pgfs-only egress allowlist (overrides `edb.egress_allowlist` when set) |
| `edb.egress_allowlist` | (unset) | Shared EDB egress allowlist default for pgfs and other EDB extensions |

```sql
-- Allow multiple local paths
SET pgfs.allowed_local_fs_paths = '/tmp/pgfs:/data/shared';

-- Persist (requires superuser + reload)
ALTER SYSTEM SET pgfs.allowed_local_fs_paths = '/tmp/pgfs:/data/shared';
SELECT pg_reload_conf();
```

---

## 9. Key Constraints and Gotchas

| Rule | Detail |
|---|---|
| **Non-transactional DML** | INSERT/UPDATE/DELETE that succeed on the object store **cannot be rolled back** if the surrounding PostgreSQL transaction aborts. |
| **UPDATE cannot rename** | Setting `key` in an UPDATE is rejected. Rename = DELETE old key + INSERT new key. |
| **body is lazy** | `body` is NULL unless the query both selects it *and* filters `WHERE key = '...'`. A plain `SELECT *` leaves `body` NULL for every row. |
| **LIMIT pushdown is conditional** | LIMIT is pushed down only for simple scans — no ORDER BY, DISTINCT, aggregates, window functions, set operations, joins, non-constant OFFSET, or SRFs in the target list. |
| **Local path allowlist** | `file://` backend rejects any path not under a prefix listed in `pgfs.allowed_local_fs_paths`. Default is `/tmp/pgfs`. |
| **credentials_env mutual exclusion** | `credentials` and `credentials_env` cannot both be provided. An explicit `'{}'` for `credentials` still counts — it must be `NULL` to use `credentials_env`. |
| **credentials_env env var prefix** | The env var name must start with a prefix from `pgfs.env_var_allowed_prefix` (default `PGFS_`). Set to `''` to disable. |
| **delete_storage_location is not CASCADE for FTs** | Deleting a storage location does NOT drop its foreign tables. Drop them manually first if needed. |
| **Pagination** | Full scans fetch objects 1000 at a time for bounded memory use. |

---

## 10. Common Patterns

### Check if pgfs is installed

```sql
SELECT extversion FROM pg_extension WHERE extname = 'pgfs';
```

### Full setup: S3 bucket with foreign table

```sql
CREATE EXTENSION pgfs CASCADE;

SELECT pgfs.create_storage_location_with_foreign_table(
    'prod_data',
    's3://my-prod-bucket',
    options     => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "AKIA...", "secret_access_key": "..."}'
);

-- prod_data_ft is now available
SELECT key, size FROM prod_data_ft LIMIT 50;
SELECT convert_from(body, 'UTF8') FROM prod_data_ft WHERE key = 'config/settings.json';
```

### Copy-write a transformed object

```sql
-- Read, transform in SQL, write back
INSERT INTO my_files (key, body)
SELECT
    'processed/' || key,
    convert_to(upper(convert_from(body, 'UTF8')), 'UTF8')
FROM my_files
WHERE key = 'raw/input.txt';
```

### Local filesystem testing

```sql
SET pgfs.allowed_local_fs_paths = '/tmp/pgfs';

SELECT pgfs.create_storage_location('local_test', 'file:///tmp/pgfs');
SELECT pgfs.create_foreign_table('local_ft', 'local_test');

INSERT INTO local_ft (key, body) VALUES ('test.txt', 'hello'::bytea);
SELECT key, convert_from(body, 'UTF8') FROM local_ft WHERE key = 'test.txt';
```

### pgaa integration note

pgfs is a required dependency of pgaa (EDB Postgres Analytics Accelerator). When helping a
user configure pgaa, the storage location must be created via pgfs first. Do not re-explain
pgfs's SQL surface from within pgaa context — refer here instead.

---

## 11. Diagnostics

Run the diagnostic script to verify extension install, list all storage locations, and show
all foreign tables:

```bash
python3 scripts/pgfs_diagnostics.py --dsn "host=localhost dbname=mydb user=postgres"
```

Or run inline SQL:
```sql
SELECT pgfs.pgfs_version();
SELECT name, url, options::text FROM pgfs.list_storage_locations();
SELECT schema_name, server_name, ft_name FROM pgfs.v_foreign_tables;
```