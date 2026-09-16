# pgfs Function Reference

All functions live in the `pgfs` schema. Source: `sql/static.sql` (authoritative current signatures), `sql/pgfs--*.sql` (upgrade history).

---

## Storage Location Management

```sql
pgfs.create_storage_location(
    name         TEXT,
    url          TEXT,          -- e.g. 's3://bucket', 'az://container', 'gs://bucket', 'file:///path', 'memory://'
    options      JSON DEFAULT NULL,  -- e.g. '{"region": "us-east-1"}'
    credentials  JSON DEFAULT NULL   -- e.g. '{"access_key_id": "...", "secret_access_key": "..."}'
) -- returns TEXT (the storage location name); creates a foreign server + user mapping under the hood

pgfs.create_storage_location_with_foreign_table(
    storage_location_name  TEXT,
    url                    TEXT,
    options                JSON DEFAULT NULL,
    credentials            JSON DEFAULT NULL
) -- returns TEXT; convenience wrapper that also creates a foreign table named '<storage_location_name>_ft'

pgfs.get_storage_location(storage_location_name TEXT)   -- returns real credential values
pgfs.list_storage_locations()                            -- credential values masked
pgfs.update_storage_location(name TEXT, url TEXT, options JSON DEFAULT NULL, credentials JSON DEFAULT NULL)
pgfs.delete_storage_location(storage_location_name TEXT)

pgfs.get_default_storage_location()
pgfs.set_default_storage_location(storage_location_name TEXT)
```

---

## Foreign Tables

```sql
pgfs.create_foreign_table(
    table_name     TEXT,           -- may be schema-qualified, e.g. 'myschema.mytable'
    server_name    TEXT,           -- a pgfs storage location name
    path           TEXT  DEFAULT '/',
    extra_options  JSONB DEFAULT '{}'
) -- returns TEXT; creates a foreign table with the fixed schema below
```

**Foreign table schema** (fixed, applies to every table `create_foreign_table` creates):

| Column | Type | Description |
|---|---|---|
| `key` | `TEXT` | Object path/name, relative to the storage location |
| `size` | `BIGINT` | Object size in bytes |
| `last_modified` | `TIMESTAMPTZ` | Last modification timestamp |
| `e_tag` | `TEXT` | Object ETag (version hash), if available |
| `body` | `BYTEA` | Object content — only populated when selected together with a `key = '...'` filter |

Full DML is supported: `SELECT`, `INSERT` (upload), `UPDATE` (except the `key` column — renaming via UPDATE is rejected), `DELETE`.

---

## Supported Backends

| Backend | URL scheme |
|---|---|
| AWS S3 | `s3://bucket` |
| Azure Blob Storage | `az://container` |
| Azure Data Lake Storage Gen2 | `abfss://<container>@<accountname>.dfs.core.windows.net/<path>` |
| Google Cloud Storage | `gs://bucket` |
| Local filesystem | `file:///path` |
| In-memory (testing) | `memory://` |

---

## GUC Parameters

| Parameter | Default | Description |
|---|---|---|
| `pgfs.allowed_local_fs_paths` | `/tmp/pgfs` | Colon-separated list of allowed local filesystem path prefixes |
