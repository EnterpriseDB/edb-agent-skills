# pgaa Key Tables Schema Reference

Internal catalog tables used by the pgaa extension. All live in the `pgaa` schema.
Sources: `pgaa/sql/pgaa--0.0.0--1.3.0.sql` and subsequent migrations.

---

## `pgaa.table_mapping`

Maps PostgreSQL tables (identified by schema + name) to their backing lakehouse location.

| Column | Type | Description |
|--------|------|-------------|
| `rel_namespace` | `text` | Schema name of the PGAA-managed table |
| `rel_name` | `text` | Table name of the PGAA-managed table |
| `storage_location` | `text` | Name of the pgfs storage location backing this table |
| `storage_path` | `text` | Path within the storage location |
| `managed_by` | `text` | Name of the catalog managing this table (FK → `pgaa.catalog.name`); NULL for unmanaged tables |
| `format` | `text` | Table format: `'delta'`, `'iceberg'`, or `'parquet'` |
| `catalog_namespace` | `text` | Namespace within the catalog (for catalog-managed tables) |
| `catalog_table` | `text` | Table name within the catalog (for catalog-managed tables) |

**Primary Key:** `(rel_namespace, rel_name)`

**Constraints (since 1.10.0):**
- `format IN ('delta', 'iceberg', 'parquet')`

**Note:** Registered for `pg_extension_config_dump` — rows survive `pg_dump`/`pg_restore`.

---

## `pgaa.catalog`

Registered external catalogs (Iceberg REST or S3 Tables).

| Column | Type | Description |
|--------|------|-------------|
| `name` | `text` | Unique catalog name (user-defined) |
| `type` | `text` | Catalog type: `'iceberg-rest'` or `'iceberg-s3tables'` |
| `options` | `json` | Connection options (URL/ARN, credentials, warehouse, etc.) |
| `created_at` | `timestamp` | When the catalog was registered |
| `refreshed_at` | `timestamp` | Last successful metadata refresh timestamp |
| `status` | `text` | Sync status: `'detached'`, `'attached'`, `'refresh_retry'`, `'refresh_failed'` |

**Primary Key:** `name`

**Constraints (since 1.10.0):**
- `type IN ('iceberg-rest', 'iceberg-s3tables')`
- `status IN ('detached', 'attached', 'refresh_retry', 'refresh_failed')`

**Note:** Registered for `pg_extension_config_dump`.

---

## `pgaa.background_task`

Task queue for background maintenance operations (compaction, vacuum, purge, zorder).

| Column | Type | Description |
|--------|------|-------------|
| `id` | `uuid` | Task identifier (returned by `pgaa.launch_task()`) |
| `type` | `text` | Task type: `'compaction'`, `'zorder'`, `'vacuum'`, `'purge'` |
| `target_table` | `regclass` | Target PGAA table OID |
| `options` | `json` | Task-specific options (type-dependent JSON) |
| `status` | `text` | Current status: `'pending'`, `'running'`, `'success'`, `'failure'` |
| `scheduled_at` | `timestamp` | When the task was scheduled |
| `started_at` | `timestamp` | When the maintenance worker picked it up |
| `finished_at` | `timestamp` | When the task completed |
| `output` | `json` | Task output/metrics on completion, or error details on failure |

**Constraints:**
- `status IN ('pending', 'running', 'success', 'failure')`
- Unique index: only one `'pending'` task of each type per table
- Unique index: only one `'running'` task per table at a time

---

## `pgaa.tiered_table`

Tracks tables under PGD tiering/retention management.

(Schema varies across versions; consult `pgaa/sql/pgaa--*.sql` for current column list.)

Key fields include: `schema_oid`, `table_oid`, `schema_name`, `table_name`, `partition_increment`, `retention`, analytics offload configuration, and storage sizes.

---

## `pgaa.lakehouse_table_stats_cache`

Cached storage statistics for analytics tables (avoids repeated object-storage metadata reads).

| Column | Type | Description |
|--------|------|-------------|
| `relation` | `regclass` | Table OID |
| `latest_snapshot_size` | `bigint` | Size of the latest snapshot in bytes |
| `total_size` | `bigint` | Total size including all snapshots in bytes |
| `updated_at` | `timestamp` | When this cache entry was last refreshed |

**TTL:** Governed by `pgaa.lakehouse_table_stats_cache_ttl_s` GUC. Bypassed by `pgaa.lakehouse_table_stats_uncached()`.
