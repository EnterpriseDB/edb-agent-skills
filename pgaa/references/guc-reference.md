# pgaa GUC (Configuration Parameter) Reference

All parameters are set in `postgresql.conf` or via `SET pgaa.<parameter> = ...` in SQL (unless noted as read-only/startup). This reference is grouped by concern.

---

## Engine Selection & Connection

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.executor_engine` | enum | `'seafowl'` | Selects the offload engine: `'seafowl'` (default) or `'spark_connect'`. Must be changed before starting background workers that depend on the engine. |
| `pgaa.seafowl_url` | string | — | Endpoint for an **external** Seafowl process (when not using the embedded `autostart_seafowl_*` worker). Example: `http://localhost:8082`. |
| `pgaa.spark_connect_url` | string | — | Spark Connect endpoint, e.g. `sc://spark-host:15002`. Required when `executor_engine = 'spark_connect'`. |
| `pgaa.spark_connect_extra_config` | JSON string | — | Extra key-value config forwarded to Spark Connect session. Example: `'{"spark.executor.memory": "4g"}'`. |

---

## Embedded Seafowl Auto-Start (`autostart_seafowl_*` family)

These GUCs control PGAA's embedded Seafowl background worker. When `pgaa.autostart_seafowl = true`, PGAA starts Seafowl automatically as a Postgres background worker.

| GUC | Type | Description |
|-----|------|-------------|
| `pgaa.autostart_seafowl` | bool | Enable/disable Seafowl auto-start as a background worker. |
| `pgaa.autostart_seafowl_port` | int | Port the embedded Seafowl listens on for Arrow Flight gRPC. |
| `pgaa.autostart_seafowl_path` | string | Path to the Seafowl binary. |
| `pgaa.autostart_seafowl_tmp_dir` | string | Temporary directory for Seafowl spill/cache. Recommend NVMe-backed path. |
| `pgaa.autostart_seafowl_memory_limit_mb` | int | Memory limit for the embedded Seafowl process (megabytes). |
| `pgaa.autostart_seafowl_enable_metrics` | bool | Expose Seafowl Prometheus metrics endpoint (default: true since 1.4.0). |
| `pgaa.autostart_seafowl_metrics_host` | string | Host for Seafowl metrics endpoint. Default `0.0.0.0`. |
| `pgaa.autostart_seafowl_metrics_port` | int | Port for Seafowl Prometheus metrics. Default `9092`. |
| `pgaa.autostart_seafowl_object_store_cache_capacity` | int | Object store cache capacity in bytes. Recommend setting to available disk space on temp drive. |

---

## Background Workers

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_maintenance_worker` | bool | `true` | Enable the background maintenance worker (compaction, vacuum, purge for Delta tables). |
| `pgaa.enable_metastore_sync_worker` | bool | `false` | Enable the background metastore sync worker for continuous catalog sync (`attach_catalog`). |
| `pgaa.metastore_sync_poll_rate_s` | int | — | How often (seconds) the metastore sync worker polls external catalogs for schema changes. |
| `pgaa.metastore_sync_stale_duration_s` | int | — | Duration after which a catalog sync is considered stale and triggers a re-sync. |
| `pgaa.maintenance_worker_sleep_interval` | interval | — | Sleep interval between maintenance worker task checks. |

---

## Query Planning & Pushdown

These are the **first place to look** when troubleshooting unexpected query performance.

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_direct_scan` | bool | `true` | Enables DirectScan: the entire SQL query is pushed down to Seafowl/Spark and executed there. Fastest path. |
| `pgaa.direct_scan_fail_behavior` | enum | — | Controls what happens when DirectScan is unavailable: `'fallback'` (use CompatScan) or `'error'` (raise an error to the user, useful for debugging). |
| `pgaa.enable_join_pushdown` | bool | `true` | Allow JOIN pushdown in CompatScan. Disable if wrong results observed on joined queries. |
| `pgaa.enable_groupby_pushdown` | bool | `true` | Allow GROUP BY pushdown in CompatScan. |
| `pgaa.enable_orderby_pushdown` | bool | `true` | Allow ORDER BY / LIMIT / OFFSET pushdown in CompatScan. |
| `pgaa.enable_distinct_pushdown` | bool | `true` | Allow DISTINCT pushdown in CompatScan. |
| `pgaa.enable_window_pushdown` | bool | `true` | Allow window function pushdown in CompatScan. |
| `pgaa.scan_startup_cost` | float | — | Planner startup cost estimate for PGAA table scans. Tune to affect plan selection. |
| `pgaa.scan_per_tuple_cost` | float | — | Planner per-tuple cost estimate for PGAA table scans. |
| `pgaa.scan_aggregation_cost_factor` | float | — | Cost multiplier applied when aggregation is pushed down. |
| `pgaa.scan_sort_cost_factor` | float | — | Cost multiplier applied when sort is pushed down. |
| `pgaa.use_seafowl_cost_estimates` | bool | — | Use Seafowl-provided row-count estimates to guide the Postgres planner (requires engine round-trip per plan). |

---

## Replication & Memory

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.max_replication_lag_s` | int | `5` | Maximum replication lag threshold in seconds before PGAA flushes buffered changes to the engine. |
| `pgaa.flush_task_interval_s` | int | `5` | Interval (seconds) between flush task invocations in the replication writer. |
| `pgaa.max_in_memory_mb` | int | — | Controls the size-based flush criterion for the replication/CTAS write path (megabytes held in memory before forcing a flush to object storage). |

---

## Statistics & Caching

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_metadata_stats` | bool | — | Inject Iceberg/Delta/Parquet metadata statistics (row counts, min/max, NDV) into the PG planner for better query plans. |
| `pgaa.metadata_stats_ttl_s` | int | — | TTL (seconds) for cached metadata statistics. |
| `pgaa.lakehouse_table_stats_cache_ttl_s` | int | — | TTL (seconds) for the `pgaa.lakehouse_table_stats_cache` entries. |

---

## Iceberg Concurrency

| GUC | Type | Description |
|-----|------|-------------|
| `pgaa.iceberg_manifest_concurrency` | int | Controls the number of concurrent Iceberg manifest fetches during scan planning. |

---

## DataFusion Runtime (Advanced)

GUCs auto-generated from DataFusion's own settings are exposed as `pgaa.*` parameters. Examples include memory pool type selection and `EXPLAIN` option forwarding. Consult `pg_settings WHERE name LIKE 'pgaa.%'` for the full live list on a running instance.

---

## Useful Queries

```sql
-- List all current pgaa GUC values
SELECT name, setting, unit, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.%'
ORDER BY name;
```
