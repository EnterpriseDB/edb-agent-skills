# pgfs Function Reference

All functions live in the `pgfs` schema. Source of truth: `sql/static.sql` in the pgfs repository.

---

## Storage Location Management

### Create

```sql
-- Standard form (recommended for users)
pgfs.create_storage_location(
    name         TEXT,
    url          TEXT,          -- backend URL, e.g. 's3://bucket', 'az://container', 'gs://bucket',
                                --   'abfss://<container>@<account>.dfs.core.windows.net/<path>',
                                --   'file:///path', 'memory://'
    options      JSON DEFAULT NULL,   -- e.g. '{"region": "us-east-1"}'
    credentials  JSON DEFAULT NULL    -- e.g. '{"access_key_id": "...", "secret_access_key": "..."}'
) RETURNS TEXT  -- returns the storage location name

-- Convenience: create storage location AND a foreign table named '<name>_ft' in one call
pgfs.create_storage_location_with_foreign_table(
    storage_location_name TEXT,
    url                   TEXT,
    options               JSON DEFAULT NULL,
    credentials           JSON DEFAULT NULL
) RETURNS TEXT  -- returns the storage location name
```

> **Internal 5-argument forms** (`msl_id UUID` parameter) exist for EDB managed-service replication
> from `upm-beacon`. Users should always use the 4-argument public forms above.

### Read / List

```sql
-- List all storage locations (credentials masked — values shown as '<redacted>')
pgfs.list_storage_locations()
    RETURNS TABLE(name TEXT, url TEXT, msl_id UUID, options JSON, credentials JSON)

-- Get a specific storage location, including REAL credential values — treat output as sensitive
pgfs.get_storage_location(storage_location_name TEXT)
    RETURNS TABLE(name TEXT, url TEXT, msl_id UUID, options JSON, credentials JSON)

-- Get the current default storage location name
pgfs.get_default_storage_location()
    RETURNS TABLE(default_storage_location TEXT)
```

### Update / Delete

```sql
-- Replace a storage location (internally: delete + recreate; not transactional)
pgfs.update_storage_location(
    name        TEXT,
    url         TEXT,
    options     JSON DEFAULT NULL,
    credentials JSON DEFAULT NULL
) RETURNS TEXT

-- Delete a storage location (drops server + user mapping; does NOT drop associated foreign tables)
pgfs.delete_storage_location(storage_location_name TEXT) RETURNS VOID

-- Set the default storage location (must already exist)
pgfs.set_default_storage_location(storage_location_name TEXT) RETURNS TEXT
```

---

## Foreign Tables

```sql
-- Create a foreign table over an existing storage location
pgfs.create_foreign_table(
    table_name    TEXT,           -- may be schema-qualified: 'myschema.mytable'
    server_name   TEXT,           -- a pgfs storage location name
    path          TEXT  DEFAULT '/',
    extra_options JSONB DEFAULT '{}'  -- e.g. '{"data_format": "Text", "table_type": "aidb-volume"}'
) RETURNS TEXT  -- returns the table name
```

### Fixed Foreign Table Schema

Every foreign table created by pgfs has these exact columns:

| Column         | Type          | Description |
|----------------|---------------|-------------|
| `key`          | `TEXT`        | Object path/name, relative to the storage location root |
| `size`         | `BIGINT`      | Object size in bytes |
| `last_modified`| `TIMESTAMPTZ` | Last modification timestamp |
| `e_tag`        | `TEXT`        | Object ETag (version hash); may be NULL for some backends |
| `body`         | `BYTEA`       | Object content — **lazy**: only populated when `WHERE key = '...'` is present |

---

## Diagnostic / Utility Views

```sql
-- Key-value breakdown of all pgfs foreign server options
pgfs.v_server_options    -- columns: srvname TEXT, key TEXT, value TEXT

-- All foreign tables backed by pgfs storage locations
pgfs.v_foreign_tables    -- columns: schema_name, server_name, ft_name, ftoptions

-- Key-value breakdown of all foreign table options
pgfs.v_foreign_table_options  -- columns: schema_name, server_name, ft_name, key, value
```

---

## GUC Parameters

| Parameter | Default | Context | Description |
|-----------|---------|---------|-------------|
| `pgfs.allowed_local_fs_paths` | `/tmp/pgfs` | `SUSET` | Colon-separated list of allowed local filesystem path prefixes for the `file://` backend. Empty string = no local paths allowed. |
| `pgfs.egress_allowlist` | (none) | `SIGHUP` | pgfs-specific egress allowlist (comma-separated hostnames/CIDR). Overrides `edb.egress_allowlist` when set. |
| `edb.egress_allowlist` | (none) | `SIGHUP` | Shared EDB extension egress allowlist. Falls back to no restriction check when both this and `pgfs.egress_allowlist` are unset. |

---

## Supported Backends

| Backend | URL scheme | Notes |
|---------|------------|-------|
| AWS S3 | `s3://bucket-name` | Use `options: {"region": "..."}` and credentials with `access_key_id`, `secret_access_key`, optionally `session_token` |
| Azure Blob Storage | `az://container-name` | Use credentials for account auth |
| Azure Data Lake Gen2 | `abfss://<container>@<account>.dfs.core.windows.net/<path>` | |
| Google Cloud Storage | `gs://bucket-name` | |
| Local filesystem | `file:///absolute/path` | Path must be under `pgfs.allowed_local_fs_paths` |
| In-memory (testing) | `memory://` | Data lost when session ends; for testing only |

---

## DML Semantics

| Operation | Behavior |
|-----------|----------|
| `SELECT` (no `WHERE key = '...'`) | Lists all objects; `body` is NULL for all rows |
| `SELECT` with `WHERE key = '...'` | Single HEAD/GET; `body` is populated |
| `INSERT (key, body)` | Uploads object to storage |
| `UPDATE SET body = ...` | Overwrites object content |
| `UPDATE SET key = ...` | **Rejected** — renaming via UPDATE is not supported; use DELETE + INSERT |
| `DELETE WHERE key = '...'` | Deletes the object from storage |

**Critical:** Object store operations are **not transactional**. A successful INSERT/UPDATE/DELETE
cannot be rolled back if the surrounding PostgreSQL transaction aborts.

---

## LIMIT Pushdown Rules

LIMIT is pushed down (stops listing early) **only** for simple queries. Pushdown is **disabled** when
any of the following are present:

- `ORDER BY`
- `DISTINCT`
- Aggregates (`COUNT`, `SUM`, etc.)
- Window functions
- Set operations (`UNION`, `INTERSECT`, `EXCEPT`)

Scans page through objects 1,000 at a time, so memory usage is bounded regardless of bucket size.

---

## Ecosystem Notes

- pgfs is a prerequisite for **pgaa** (EDB Postgres Analytics Accelerator): `pgaa.control` declares `requires = 'pgfs'`.
- The `pgfs_lib` crate provides a reusable FDW utility library for downstream extensions.
- Full FDW (scan + DML) requires PostgreSQL 14+. On PG12/PG13 a noop handler is installed (0 rows, no DML).
