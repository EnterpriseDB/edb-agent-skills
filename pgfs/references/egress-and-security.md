# pgfs Security, Egress, and Configuration Reference

## Local Filesystem Security

The `file://` backend restricts access to a configurable allowlist of path prefixes.

### GUC: `pgfs.allowed_local_fs_paths`

- **Type:** String (colon-separated list of path prefixes)
- **Default:** `/tmp/pgfs`
- **Context:** `SUSET` (superuser can set at session or system level)
- **Flags:** `NO_RESET_ALL`

Any `file://` storage location whose path does not begin with one of the listed prefixes is rejected at query time. An empty string means nothing is allowed.

```sql
-- Allow a single path
SET pgfs.allowed_local_fs_paths = '/data/shared';

-- Allow multiple paths (colon-separated)
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs:/mnt/storage';

-- Disallow all local paths
SET pgfs.allowed_local_fs_paths = '';
```

> **Note:** This GUC is context `Suset`, meaning only superusers can change it. Setting it in `postgresql.conf` or via `ALTER SYSTEM SET` is the recommended approach for production.

## Egress Allowlist

pgfs supports two GUCs that control outbound network egress:

### GUC: `pgfs.egress_allowlist`

- **Type:** String (comma-separated hostnames or CIDR ranges)
- **Default:** unset (falls back to `edb.egress_allowlist`)
- **Context:** `SIGHUP`

A pgfs-only egress allowlist. A leading dot (`.example.com`) permits all subdomains. When set, this overrides `edb.egress_allowlist` for pgfs connections.

### GUC: `edb.egress_allowlist`

- **Type:** String (comma-separated hostnames or CIDR ranges)
- **Default:** unset (no restriction)
- **Context:** `SIGHUP`

Shared default across EDB extensions (pgfs, pgaa/AIDB, etc.). When both `pgfs.egress_allowlist` and `edb.egress_allowlist` are unset, no egress check is performed.

```ini
# postgresql.conf examples
pgfs.egress_allowlist = 's3.amazonaws.com,.s3.amazonaws.com,storage.googleapis.com'
edb.egress_allowlist = '.amazonaws.com,.googleapis.com,.core.windows.net'
```

## Non-Transactional Writes

**Critical:** Object store DML (INSERT, UPDATE, DELETE) in pgfs is **not transactional**.

- A successful write to S3/Azure/GCS cannot be rolled back if the PostgreSQL transaction subsequently aborts.
- If a transaction that includes a `pgfs` DML operation rolls back, the object store change persists.
- Design workflows that require atomicity around this limitation (e.g., write to a staging key, then rename via DELETE + INSERT in a compensating step).

## Superuser Requirement

The `pgfs` extension requires superuser to install (`superuser = true` in the control file). Storage location creation also requires superuser or appropriate privileges, as it creates foreign servers and user mappings in the PostgreSQL catalog.

## Credential Storage

- Credentials passed to `pgfs.create_storage_location()` are stored in a PostgreSQL user mapping (`pg_user_mappings`).
- `pgfs.list_storage_locations()` masks credential values (shows key names with `<redacted>` values).
- `pgfs.get_storage_location(name)` returns real credential values — use with care.

## Path Injection Prevention

When appending sub-paths to storage location URLs, pgfs uses segment-by-segment extension rather than `URL.join()`. This means:
- Inputs containing URL schemes (e.g., `s3://other-bucket`) are treated as literal path segments, not absolute URLs.
- This prevents a rogue `path` parameter from redirecting reads/writes to a different bucket.

## PostgreSQL Version Support

| PG Version | Full DML Support |
|---|---|
| PG 12/13 (incl. WHPG) | No — noop FDW handler (0 rows, no DML) |
| PG 14+ | Yes — full scan + INSERT/UPDATE/DELETE |
