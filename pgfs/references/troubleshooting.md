# pgfs Troubleshooting Guide

Common errors, their causes, and resolutions.

---

## Installation Issues

### `ERROR: could not open extension control file ".../pgfs.control": No such file or directory`

**Cause:** pgfs is not installed on this PostgreSQL instance.  
**Resolution:** pgfs must be installed at the OS level before `CREATE EXTENSION` is called. For EDB-managed environments, install via the EDB package repository. For development, build with `cargo pgrx install --features pg18`.

### `ERROR: required extension "pgfs" is not installed`

**Cause:** Another extension (e.g., `pgaa`) requires pgfs but it isn't installed.  
**Resolution:** `CREATE EXTENSION pgfs CASCADE;` — the `CASCADE` flag installs any missing dependencies.

---

## Storage Location Issues

### `ERROR: server "<name>" already exists`

**Cause:** A storage location with that name was already created.  
**Options:**
- Use `pgfs.get_storage_location('<name>')` to inspect the existing one.
- Use `pgfs.update_storage_location(...)` to replace it.
- Use `pgfs.delete_storage_location('<name>')` then recreate.

### `ERROR: Storage location "<name>" does not exist`

**Cause:** Passing an unknown name to `set_default_storage_location` or `get_storage_location`.  
**Resolution:** Run `SELECT * FROM pgfs.list_storage_locations()` to see valid names.

### `ERROR: The 'url' must be present in the storage location options`

**Cause:** A foreign server was created manually without a `url` option, bypassing `pgfs.create_storage_location`.  
**Resolution:** Always use `pgfs.create_storage_location(...)` to create pgfs-managed servers.

### `ERROR: key contains '=' character`

**Cause:** A JSON key in the `options` or `credentials` argument contains an `=` sign (e.g., `"a=b": "value"`). pgfs stores options as PostgreSQL server options which use `key=value` format internally.  
**Resolution:** Ensure option/credential key names never contain `=` characters.

---

## Foreign Table Issues

### `ERROR: relation "<table>" already exists`

**Cause:** `pgfs.create_foreign_table(...)` was called but the table already exists.  
**Resolution:** Drop the existing table first: `DROP FOREIGN TABLE IF EXISTS <table>;` then recreate.

### Body column is always NULL

**Cause:** The query selects `body` without a `WHERE key = '...'` equality filter.  
**Resolution:** Always pair `body` with a key equality predicate:
```sql
-- WRONG:
SELECT key, body FROM my_ft;

-- CORRECT:
SELECT key, body FROM my_ft WHERE key = 'path/to/object.txt';
```

### `SELECT *` returns 0 rows for non-empty bucket

**Cause 1:** Wrong `path` argument when the foreign table was created. The `path` (default `/`) must match the prefix of objects in the bucket.  
**Cause 2:** Credentials are invalid or expired — the listing call fails silently.  
**Resolution:** Check `pgfs.v_server_options` to verify the URL and path. Test credentials independently with the cloud CLI.

---

## DML Issues

### `ERROR: UPDATE on column "key" is not supported`

**Cause:** Attempted to rename an object by setting `key` in an `UPDATE`.  
**Resolution:** Use the rename pattern — DELETE + INSERT:
```sql
INSERT INTO my_ft (key, body)
    SELECT 'new-name.txt', body FROM my_ft WHERE key = 'old-name.txt';
DELETE FROM my_ft WHERE key = 'old-name.txt';
```

### INSERT/UPDATE/DELETE succeeded but changes appear rolled back

**Cause:** Object store operations are **not transactional**. If a PostgreSQL transaction containing a successful DML statement on a foreign table is later aborted (`ROLLBACK`), the object store change is **not** undone.  
**Resolution:** This is by design. Do not rely on PostgreSQL transaction atomicity for object store DML. Structure workflows to treat object store writes as permanent on execution.

### `ERROR: permission denied for foreign table`

**Cause:** The current user does not have access to the foreign table.  
**Resolution:** `GRANT SELECT, INSERT, UPDATE, DELETE ON <table> TO <user>;`

---

## Local Filesystem Issues

### `ERROR: path is not allowed: /some/path`

**Cause:** The `file://` storage location points to a path outside `pgfs.allowed_local_fs_paths`.  
**Resolution (superuser required):**
```sql
-- Add the target prefix (colon-separated list):
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';
-- Or to persist across sessions, set in postgresql.conf:
-- pgfs.allowed_local_fs_paths = '/data/shared'
```

### Local files are accessible but body is NULL

**Cause:** Same as cloud: `body` requires `WHERE key = 'filename'`.

---

## LIMIT Pushdown Not Working

**Symptom:** A `LIMIT N` query on a large bucket takes a long time even though you expect it to stop early.  
**Cause:** LIMIT pushdown is disabled when any of these appear in the query:
- `ORDER BY`
- `DISTINCT`
- Aggregate functions (`COUNT`, `SUM`, etc.)
- Window functions
- Set operations (`UNION`, `INTERSECT`, `EXCEPT`)

**Resolution:** Remove the offending clause if you only need a sample, or accept the full scan cost.

---

## Credential / Authentication Issues

### Cloud provider returns 401/403 when SELECTing from a foreign table

**Cause:** Credentials stored in the user mapping are wrong or expired.  
**Resolution:**
1. Check current credentials (returns real values — handle carefully):
   ```sql
   SELECT credentials FROM pgfs.get_storage_location('my_location');
   ```
2. Update with fresh credentials:
   ```sql
   SELECT pgfs.update_storage_location(
       'my_location', '<url>',
       options     => '<options_json>',
       credentials => '{"access_key_id": "NEW_KEY", "secret_access_key": "NEW_SECRET"}'
   );
   ```

### `list_storage_locations` shows `<redacted>` for credential values

**Expected behavior.** `list_storage_locations()` intentionally masks credential values for safety. Use `get_storage_location('<name>')` to retrieve real values for a specific location.

---

## PG Version Compatibility

| PG Version | FDW Status |
|------------|-----------|
| PG 14+ | Full FDW: SELECT, INSERT, UPDATE, DELETE, LIMIT pushdown |
| PG 12/13 | Noop handler: 0 rows returned, no DML supported |

If queries return 0 rows unexpectedly and no errors are shown, check `SELECT version()` — you may be on PG12/13.
