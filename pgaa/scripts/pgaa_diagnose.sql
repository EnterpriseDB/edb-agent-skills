-- pgaa_diagnose.sql
-- Run this script against a PostgreSQL instance to get a full pgaa health snapshot.
-- All statements are read-only. Safe to run in production.

\echo '=== pgaa Version ==='
SELECT pgaa.pgaa_version();

\echo ''
\echo '=== Engine Version (current executor_engine) ==='
SELECT pgaa.engine_version();

\echo ''
\echo '=== Active pgaa GUCs ==='
SELECT name, setting, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.%'
ORDER BY name;

\echo ''
\echo '=== Key pgaa GUCs to Watch ==='
SELECT name, setting
FROM pg_settings
WHERE name IN (
    'pgaa.executor_engine',
    'pgaa.autostart_seafowl',
    'pgaa.seafowl_url',
    'pgaa.enable_direct_scan',
    'pgaa.direct_scan_fail_behavior',
    'pgaa.enable_maintenance_worker',
    'pgaa.enable_metastore_sync_worker',
    'pgaa.enable_join_pushdown',
    'pgaa.enable_groupby_pushdown'
)
ORDER BY name;

\echo ''
\echo '=== Registered Catalogs ==='
SELECT name, type, status, created_at, refreshed_at
FROM pgaa.list_catalogs();

\echo ''
\echo '=== All pgaa Tables (with storage/replication info) ==='
SELECT
    schema_name,
    table_name,
    format,
    replication_status,
    catalog_name,
    catalog_namespace,
    catalog_table,
    storage_location_name,
    storage_location_path,
    pg_size_pretty(object_storage_snapshot_size_bytes) AS snapshot_size,
    pg_size_pretty(object_storage_total_size_bytes)    AS total_size
FROM pgaa.list_analytics_tables()
ORDER BY schema_name, table_name;

\echo ''
\echo '=== Tiered Tables ==='
SELECT
    schema_name,
    table_name,
    partition_increment,
    analytics_offload,
    retention,
    replication_enabled,
    pg_size_pretty(tiered_data_size)   AS cold_size,
    pg_size_pretty(untiered_data_size) AS hot_size
FROM pgaa.list_tiered_tables();

\echo ''
\echo '=== Background Task Summary (last 20) ==='
SELECT
    id,
    task_type,
    status,
    created_at,
    finished_at,
    EXTRACT(epoch FROM (finished_at - created_at))::int AS duration_s
FROM pgaa.background_task
ORDER BY created_at DESC
LIMIT 20;

\echo ''
\echo '=== DataFusion Settings (non-default only) ==='
SELECT name, setting, boot_val, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.datafusion%'
  AND setting <> boot_val
ORDER BY name;

\echo ''
\echo '=== pgfs Storage Locations ==='
SELECT name, url
FROM pgfs.list_storage_locations();

\echo ''
\echo '=== Diagnosis complete. ==='
