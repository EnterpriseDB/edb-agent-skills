# pgfs Troubleshooting & Edge Cases

## body Column Returning NULL

**Problem:** `SELECT body FROM my_table` returns NULL for all rows.

**Cause:** The `body` column is intentionally lazy. It is only fetched when:
1. The query references `body` (or an expression over `body`), AND
2. There is a `WHERE key = '...'` equality filter on the same scan.

**Fix:** Always pair `body` access with a key filter:
```sql
-- ✗ body is NULL
SELECT body FROM my_files;
SELECT key, body FROM my_files;
SELECT * FROM my_files;

-- ✓ body is populated
SELECT body FROM my_files WHERE key = 'path/to/file.txt';
SELECT key, body FROM my_files WHERE key = 'data/report.csv';
SELECT octet_length(body) FROM my_files WHERE key = 'archive.zip';
```

---

## UPDATE Cannot Rename Objects

**Problem:** `UPDATE my_files SET key = 'new_name.txt' WHERE key = 'old_name.txt'` raises an error.

**Cause:** The FDW does not support renaming objects via UPDATE on the `key` column.

**Fix:** Use DELETE + INSERT:
```sql
BEGIN;
INSERT INTO my_files (key, body)
  SELECT 'new_name.txt', body FROM my_files WHERE key = 'old_name.txt';
DELETE FROM my_files WHERE key = 'old_name.txt';
COMMIT;  -- WARNING: object store ops are NOT rolled back if the transaction aborts
```

---

## Non-Transactional Behavior

**Problem:** A PostgreSQL transaction rollback does not undo object store operations.

**Cause:** Object store operations (INSERT → upload, DELETE → removal) execute immediately and cannot be rolled back if the PostgreSQL transaction later aborts.

**Implication:** If you run:
```sql
BEGIN;
INSERT INTO my_files (key, body) VALUES ('uploaded.txt', 'data'::bytea);
-- ... some other SQL that fails ...
ROLLBACK;  -- 'uploaded.txt' is still in the object store!
```

**Guidance:** Design workflows to be idempotent. Use `DELETE + INSERT` patterns carefully.

---

## Local Filesystem Access Denied

**Problem:** Operations on a `file://` storage location fail with a path-not-allowed error.

**Cause:** The `pgfs.allowed_local_fs_paths` GUC restricts which paths the `file://` backend can access. Default is `/tmp/pgfs` only.

**Fix:**
```sql
-- For the current session:
SET pgfs.allowed_local_fs_paths = '/data/my-data:/tmp/pgfs';

-- For all connections (add to postgresql.conf):
-- pgfs.allowed_local_fs_paths = '/data/shared'
```

The check is prefix-based. `/data/shared` allows access to `/data/shared/subdir/file.txt`.

---

## LIMIT Not Pushed Down

**Problem:** Queries with `LIMIT` still take a long time because they scan the entire bucket.

**Cause:** LIMIT pushdown only works for **simple** queries — no `ORDER BY`, `DISTINCT`, aggregates, window functions, or set operations (UNION, INTERSECT, EXCEPT).

**Examples:**
```sql
-- ✓ LIMIT is pushed down (stops listing early)
SELECT key FROM my_files LIMIT 10;

-- ✗ LIMIT NOT pushed down (full scan, then PostgreSQL applies LIMIT)
SELECT key FROM my_files ORDER BY last_modified DESC LIMIT 10;
SELECT DISTINCT key FROM my_files LIMIT 10;
SELECT COUNT(*) FROM my_files;
```

---

## delete_storage_location Leaves Orphan Foreign Tables

**Problem:** After `pgfs.delete_storage_location('name')`, foreign tables that referenced the now-deleted server are left orphaned and produce errors when queried.

**Cause:** `delete_storage_location` uses `DROP SERVER ... CASCADE`, which drops dependent user mappings but **does NOT drop foreign tables**.

**Fix:** Drop foreign tables before deleting the storage location:
```sql
DROP FOREIGN TABLE IF EXISTS my_files;
SELECT pgfs.delete_storage_location('my_bucket');
```

Or find all dependent foreign tables first:
```sql
SELECT schema_name, ft_name
FROM pgfs.v_foreign_tables
WHERE server_name = 'my_bucket';
```

---

## Large Bucket Scans / Memory Usage

**Behavior:** pgfs scans objects in pages of 1000 at a time, so memory usage is bounded regardless of bucket size.

**Guidance:**
- Always use `WHERE key = '...'` when you know the exact object you want.
- Use `LIMIT` for browsing/sampling (works best on simple queries without ORDER BY).
- Avoid `SELECT *` or `SELECT body` over entire large buckets without a key filter.

---

## list_storage_locations() vs get_storage_location()

`list_storage_locations()` masks credential values (returns `<redacted>` for all credential field values). This is intentional to avoid printing secrets in query results.

To see real credential values for a specific location:
```sql
SELECT * FROM pgfs.get_storage_location('my_bucket');
```

---

## Extension Requires Superuser

`pgfs.control` sets `superuser = true`. The extension must be created by a superuser:
```sql
CREATE EXTENSION pgfs CASCADE;  -- must be superuser
```

After creation, `GRANT USAGE ON SCHEMA pgfs TO public` and `GRANT USAGE ON FOREIGN DATA WRAPPER pgfs_fdw TO public` are applied automatically, so regular users can use storage locations and foreign tables they have been granted access to.

---

## PostgreSQL Version Compatibility

- **PG14+**: Full FDW with scan, DML (INSERT/UPDATE/DELETE), LIMIT pushdown, key filter pushdown.
- **PG12/13**: Noop FDW handler only — queries return 0 rows, DML is not functional.

Always use PG14 or later for production pgfs usage.
