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

pgfs.get_storage_location(storage_location_name TEXT)   -- returns real credential values (use with care)
pgfs.list_storage_locations()                            -- credential values masked (<redacted>)
pgfs.update_storage_location(name TEXT, url TEXT, options JSON DEFAULT NULL, credentials JSON DEFAULT NULL)
pgfs.delete_storage_location(storage_location_name TEXT)
  -- NOTE: does NOT drop any foreign tables that reference this server; those must be dropped separately

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

## Helper Views

```sql
-- All pgfs foreign server options as key/value rows
pgfs.v_server_options    -- columns: srvname TEXT, key TEXT, value TEXT

-- All pgfs foreign tables with their server and schema
pgfs.v_foreign_tables    -- columns: schema_name TEXT, server_name TEXT, ft_name TEXT, ftoptions TEXT[]

-- All pgfs foreign table options as key/value rows
pgfs.v_foreign_table_options  -- columns: schema_name, server_name, ft_name, key, value
```

---

## Utility Function

```sql
-- Returns the pgfs version string with build metadata
SELECT pgfs.pgfs_version();
```

---

## Supported Backends

| Backend | URL scheme | Notes |
|---|---|---|
| AWS S3 | `s3://bucket` | Use options: `{"region": "..."}` |
| Azure Blob Storage | `az://container` | |
| Azure Data Lake Storage Gen2 | `abfss://<container>@<accountname>.dfs.core.windows.net/<path>` | |
| Google Cloud Storage | `gs://bucket` | |
| Local filesystem | `file:///path` | Restricted to `pgfs.allowed_local_fs_paths` prefixes |
| In-memory (testing) | `memory://` | Data not persisted |

---

## GUC Parameters

| Parameter | Default | Description |
|---|---|---|
| `pgfs.allowed_local_fs_paths` | `/tmp/pgfs` | Colon-separated list of allowed local filesystem path prefixes for `file://` backend |

Set at session level: `SET pgfs.allowed_local_fs_paths = '/data/shared:/data/uploads';`
Set in `postgresql.conf` for persistence.

---

## Credential Fields by Backend

### AWS S3
```json
{
  "access_key_id": "AKIA...",
  "secret_access_key": "...",
  "session_token": "..."   // optional, for temporary credentials
}
```
Options: `{"region": "us-east-1"}`

### Azure Blob Storage / ADLS Gen2
```json
{
  "account_name": "...",
  "access_key": "...",
  "sas_token": "..."   // alternative to access_key
}
```

### Google Cloud Storage
```json
{
  "service_account_key": "..."   // JSON key file contents
}
```

### Local filesystem / In-memory
No credentials required. For `file://`, ensure the path is listed in `pgfs.allowed_local_fs_paths`.

---

## Error Conditions

| Situation | Error / Behavior |
|---|---|
| `UPDATE` with `key` column changed | Error: renaming via UPDATE is rejected |
| `file://` path outside allowed prefixes | Error: access rejected |
| `set_default_storage_location` on non-existent name | EXCEPTION raised |
| `delete_storage_location` on non-existent name | NOTICE logged, no error |
| Option key containing `=` character | Error: bad key formatting |
| `SELECT body` without `WHERE key = '...'` | `body` is NULL — no error, silent null |
| `SELECT *` or `SELECT body` with no key filter | `body` is NULL for all rows |
