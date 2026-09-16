---
name: pgfs
description: >
  Skill for operating pgfs — the EDB PostgreSQL extension that exposes cloud and local
  object storage (AWS S3, Azure Blob, Azure Data Lake Gen2, Google Cloud Storage, local
  filesystem, in-memory) as foreign tables via SQL. Use this skill when a user needs to:
  install pgfs; create, inspect, update, or delete storage locations; create foreign tables
  over those locations; read, write, update, or delete objects using SQL DML; configure GUC
  parameters (allowed local paths, egress allowlists); diagnose listing or body-fetch issues;
  or understand pgfs's role as the storage layer underneath pgaa (EDB Postgres Analytics
  Accelerator). Triggers on any request involving pgfs storage locations, pgfs foreign tables,
  object store SQL access, or pgfs configuration.
metadata:
  aliases: [pgfs]
  version: "3.x"
  pg_versions: "14+ (full FDW); 12-13 (noop, no DML)"
  requires_superuser: true
  extension_schema: pgfs
---

# pgfs Agent Skill

pgfs is a PostgreSQL extension (built with Rust/pgrx) that provides SQL access to
filesystem-like storage backends through foreign data wrappers. It introduces two concepts:

- **Storage Location** — a named pointer to a storage backend (bucket, container, local path).
  Implemented as a PostgreSQL foreign server + user mapping under the hood.
- **Foreign Table** — a SQL table over a storage location. Fixed schema: `key`, `size`,
  `last_modified`, `e_tag`, `body`. Supports full DML.

See [references/function-reference.md](references/function-reference.md) for complete API signatures,
[references/troubleshooting.md](references/troubleshooting.md) for error resolution,
and [assets/pgfs_quickstart.sql](assets/pgfs_quickstart.sql) for copy-paste SQL snippets.
Run [scripts/pgfs_diagnose.py](scripts/pgfs_diagnose.py) to generate a JSON health report.

---

## Step 0 — Install the Extension

```sql
-- Requires superuser. CASCADE installs pgfs's own dependencies.
CREATE EXTENSION IF NOT EXISTS pgfs CASCADE;

-- Verify installation:
SELECT pgfs.pgfs_version();
```

pgfs must already be available on the PostgreSQL instance at the OS level. If
`CREATE EXTENSION` fails with a missing control file, the package has not been installed.

---

## Step 1 — Create a Storage Location

A storage location must exist before any foreign table can reference it.

### AWS S3

```sql
SELECT pgfs.create_storage_location(
    'my_s3',                              -- unique name (no spaces)
    's3://my-bucket-name',
    options     => '{"region": "us-east-1"}',
    credentials => '{
        "access_key_id":     "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    }'
);
-- With temporary credentials, also include: "session_token": "..."
```

### Azure Blob Storage

```sql
SELECT pgfs.create_storage_location(
    'my_azure',
    'az://my-container',
    credentials => '{"account_name": "myaccount", "account_key": "BASE64KEY=="}'
);
```

### Azure Data Lake Storage Gen2

```sql
SELECT pgfs.create_storage_location(
    'my_adls',
    'abfss://my-container@myaccount.dfs.core.windows.net/my-path'
);
```

### Google Cloud Storage

```sql
SELECT pgfs.create_storage_location(
    'my_gcs',
    'gs://my-gcs-bucket'
);
```

### Local Filesystem

```sql
-- Step 1: Allow the path (superuser required; persists only for session unless set in postgresql.conf)
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';

-- Step 2: Create the location
SELECT pgfs.create_storage_location('local_data', 'file:///data/shared');
```

> **Security:** Local paths outside the allowlist are always rejected regardless of credentials.

### In-Memory (Testing Only)

```sql
SELECT pgfs.create_storage_location('mem', 'memory://');
-- Data is lost when the session ends. Use only for development/testing.
```

### Convenience: Location + Foreign Table in One Call

```sql
-- Creates storage location AND a foreign table named '<name>_ft':
SELECT pgfs.create_storage_location_with_foreign_table(
    'quick_s3',
    's3://another-bucket',
    options     => '{"region": "eu-west-1"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);
-- Foreign table is now: quick_s3_ft
```

---

## Step 2 — Create a Foreign Table

```sql
SELECT pgfs.create_foreign_table(
    'my_files',        -- table name (may be schema-qualified: 'myschema.my_files')
    'my_s3',           -- storage location name
    path         => '/',           -- sub-path within the bucket (default '/')
    extra_options => '{}'          -- additional FDW options (e.g. for pgaa)
);
```

Every foreign table always has this schema:

| Column | Type | Notes |
|--------|------|-------|
| `key` | TEXT | Object path relative to storage root |
| `size` | BIGINT | Bytes |
| `last_modified` | TIMESTAMPTZ | Last write time |
| `e_tag` | TEXT | Version hash (may be NULL) |
| `body` | BYTEA | Content — **lazy** (see below) |

---

## Step 3 — Query Data

### List Objects

```sql
-- List all objects (body is NULL — no data downloaded):
SELECT key, size, last_modified FROM my_files;

-- Early-exit with LIMIT (pushdown only works without ORDER BY/aggregates):
SELECT key, size FROM my_files LIMIT 100;

-- Filter by prefix (full scan with PostgreSQL-side filter):
SELECT key FROM my_files WHERE key LIKE 'logs/2024/%';
```

### Read Object Content (body-on-demand)

> **Critical rule:** `body` is only populated when the query includes `WHERE key = '<exact-key>'`.
> Without this filter, `body` is NULL for every row — this prevents downloading entire buckets.

```sql
-- CORRECT — single object GET:
SELECT body FROM my_files WHERE key = 'config/settings.json';

-- Decode to text:
SELECT convert_from(body, 'UTF8') FROM my_files WHERE key = 'readme.txt';

-- WRONG — body will be NULL (no key filter):
SELECT key, body FROM my_files;
```

### Write Objects

```sql
-- Upload (INSERT):
INSERT INTO my_files (key, body)
VALUES ('hello.txt', 'Hello, pgfs!'::bytea);

-- Overwrite content (key cannot be changed via UPDATE):
UPDATE my_files SET body = 'New content'::bytea WHERE key = 'hello.txt';

-- Rename = DELETE + INSERT:
INSERT INTO my_files (key, body)
    SELECT 'new-name.txt', body FROM my_files WHERE key = 'old-name.txt';
DELETE FROM my_files WHERE key = 'old-name.txt';

-- Delete:
DELETE FROM my_files WHERE key = 'hello.txt';
```

> **Not transactional:** Object store DML that succeeds is permanent even if the
> PostgreSQL transaction is rolled back. Never rely on ROLLBACK to undo object writes.

---

## Step 4 — Manage Storage Locations

```sql
-- List all (credentials shown as <redacted> — safe to display):
SELECT * FROM pgfs.list_storage_locations();

-- Inspect one location — returns REAL credential values (treat as sensitive):
SELECT * FROM pgfs.get_storage_location('my_s3');

-- Replace a storage location entirely:
SELECT pgfs.update_storage_location(
    'my_s3',
    's3://new-bucket',
    options     => '{"region": "us-west-2"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);

-- Delete (does NOT drop associated foreign tables):
SELECT pgfs.delete_storage_location('my_s3');

-- Default storage location:
SELECT pgfs.set_default_storage_location('my_s3');
SELECT * FROM pgfs.get_default_storage_location();
```

---

## Configuration (GUC Parameters)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pgfs.allowed_local_fs_paths` | `/tmp/pgfs` | Colon-separated allowed `file://` path prefixes |
| `pgfs.egress_allowlist` | (none) | pgfs-specific network egress allowlist (comma-separated hostnames/CIDRs) |
| `edb.egress_allowlist` | (none) | Shared EDB extension egress allowlist (fallback) |

```sql
-- Session-scope (superuser required for SUSET GUCs):
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';

-- To persist: add to postgresql.conf:
-- pgfs.allowed_local_fs_paths = '/data/shared'
```

---

## Diagnostics

```sql
-- Extension version:
SELECT pgfs.pgfs_version();

-- All server options for a location:
SELECT * FROM pgfs.v_server_options WHERE srvname = 'my_s3';

-- All pgfs foreign tables:
SELECT * FROM pgfs.v_foreign_tables;

-- All option key-values on foreign tables:
SELECT * FROM pgfs.v_foreign_table_options;
```

Run the Python diagnostic script for a full JSON health report:

```bash
python3 scripts/pgfs_diagnose.py --dsn "host=localhost dbname=mydb user=postgres"
```

---

## Key Constraints Summary

| Constraint | Detail |
|------------|--------|
| **Not transactional** | INSERT/UPDATE/DELETE on foreign tables cannot be rolled back |
| **UPDATE cannot rename** | Setting `key` in UPDATE is rejected; use DELETE + INSERT |
| **body requires key filter** | `WHERE key = '...'` is mandatory to populate the `body` column |
| **LIMIT pushdown** | Only for queries with no ORDER BY, DISTINCT, aggregates, window functions, or set ops |
| **Local path allowlist** | `file://` paths must be under `pgfs.allowed_local_fs_paths` |
| **PG 14+ required** | Full FDW (scan + DML); PG12/13 installs a noop handler (0 rows, no DML) |
| **Superuser to install** | `CREATE EXTENSION pgfs` requires superuser |

---

## Ecosystem: pgfs and pgaa

pgfs is the storage-location layer for **pgaa** (EDB Postgres Analytics Accelerator).
`pgaa` declares `requires = 'pgfs'` in its control file. When helping users with pgaa:

- Storage locations and their credentials are configured via **pgfs** functions.
- Do not re-explain pgfs's SQL surface inside pgaa documentation — treat pgfs as the
  underlying layer that pgaa depends on.

---

## Quick Decision Tree

```
User wants to access object storage via SQL?
  → Create extension → create_storage_location → create_foreign_table → SELECT/INSERT/UPDATE/DELETE

User sees body = NULL?
  → Add WHERE key = '<exact-key>' to the query

User gets "path not allowed" for file://?
  → SET pgfs.allowed_local_fs_paths = '<path>'

User wants to rename an object?
  → INSERT with new key + DELETE old key (UPDATE on key column is rejected)

User's INSERT was committed but PostgreSQL rolled back the transaction?
  → Object store change is permanent; pgfs DML is not transactional

User wants one call for location + table?
  → pgfs.create_storage_location_with_foreign_table(...) → creates <name>_ft

User needs to see masked credential field names?
  → pgfs.list_storage_locations() (values are <redacted>)

User needs actual credential values?
  → pgfs.get_storage_location('<name>') — treat output as sensitive
```

---

## References

- [references/function-reference.md](references/function-reference.md) — Full API signatures, DML semantics, GUCs, backend URLs
- [references/troubleshooting.md](references/troubleshooting.md) — Error messages, causes, and resolutions
- [assets/pgfs_quickstart.sql](assets/pgfs_quickstart.sql) — Copy-paste SQL for all backends and operations
- [scripts/pgfs_diagnose.py](scripts/pgfs_diagnose.py) — Python diagnostic script (requires psycopg2 or psycopg)