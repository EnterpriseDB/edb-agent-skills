# pgaa GUC (Configuration Parameter) Reference

All GUCs are set in `postgresql.conf`, `ALTER SYSTEM`, or `SET` (session-level where applicable).
Source of truth: `pgaa/src/` and the `autostart_seafowl_*` family in the source. Confirm the
full current list from source; this reference captures the key surface.

---

## Engine Selection & Connection

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.executor_engine` | text | `'seafowl'` | Selects the offload engine. One of `'seafowl'` or `'spark_connect'`. |
| `pgaa.seafowl_url` | text | — | Endpoint for an **external** Seafowl process. Used when not relying on the embedded `autostart_seafowl_*` worker. |
| `pgaa.spark_connect_url` | text | — | Endpoint for the Spark Connect service. Required when `executor_engine = 'spark_connect'`. |
| `pgaa.spark_connect_extra_config` | text (JSON) | — | Extra JSON key/value config forwarded to Spark Connect. |

---

## Embedded Seafowl Autostart (`autostart_seafowl_*`)

These GUCs control the Seafowl background worker process that pgaa can launch automatically.

| GUC | Description |
|-----|-------------|
| `pgaa.autostart_seafowl` | Set `true` to enable embedded Seafowl startup (deprecated standalone Seafowl package removed as of 1.3.0). |
| `pgaa.autostart_seafowl_port` | Port for the embedded Seafowl process. |
| `pgaa.autostart_seafowl_path` | Path to the Seafowl binary. |
| `pgaa.autostart_seafowl_tmp_dir` | Temporary/spill directory for Seafowl. |
| `pgaa.autostart_seafowl_enable_metrics` | Enables Prometheus metrics endpoint on embedded Seafowl (default: true as of 1.3.1). |
| `pgaa.autostart_seafowl_metrics_host` | Host for Seafowl Prometheus metrics. |
| `pgaa.autostart_seafowl_metrics_port` | Port for Seafowl Prometheus metrics (default: 9092). |
| `pgaa.autostart_seafowl_memory_limit` | Memory cap (bytes) for the embedded Seafowl process. |
| `pgaa.autostart_seafowl_object_store_cache_capacity` | Object-store cache size (bytes) for Seafowl. |

---

## Background Workers

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_maintenance_worker` | bool | — | Enables the Delta table maintenance background worker (compaction, zorder, vacuum, purge). |
| `pgaa.enable_metastore_sync_worker` | bool | — | Enables the catalog sync background worker (used with `attach_catalog`). |
| `pgaa.metastore_sync_poll_rate_s` | integer | — | Poll interval (seconds) for the continuous catalog sync worker started by `attach_catalog`. |
| `pgaa.maintenance_worker_sleep_interval` | integer | — | Sleep interval (seconds) between maintenance worker cycles. |

---

## Query Planning & Pushdown

These GUCs govern which operations are offloaded to the engine vs. executed in Postgres. When
troubleshooting unexpected query performance, check these first.

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_direct_scan` | bool | true | Enables DirectScan mode — full query forwarded to engine. |
| `pgaa.direct_scan_fail_behavior` | text | — | What to do when DirectScan is unavailable: silently fall back, or raise an error/notice. |
| `pgaa.enable_join_pushdown` | bool | true | Enables join pushdown in CompatScan. |
| `pgaa.enable_groupby_pushdown` | bool | true | Enables GROUP BY pushdown in CompatScan. |
| `pgaa.enable_orderby_pushdown` | bool | true | Enables ORDER BY/LIMIT/OFFSET pushdown in CompatScan. |
| `pgaa.enable_distinct_pushdown` | bool | true | Enables DISTINCT pushdown in CompatScan. |
| `pgaa.enable_window_pushdown` | bool | true | Enables window-function pushdown in CompatScan (added 1.11.0). |
| `pgaa.scan_startup_cost` | float | — | Planner startup cost estimate for a PGAA scan. |
| `pgaa.scan_per_tuple_cost` | float | — | Planner per-tuple cost estimate for a PGAA scan. |
| `pgaa.scan_aggregation_cost_factor` | float | — | Cost scaling factor for aggregation pushdown estimates. |
| `pgaa.scan_sort_cost_factor` | float | — | Cost scaling factor for sort pushdown estimates. |
| `pgaa.use_seafowl_cost_estimates` | bool | — | Use Seafowl-reported cost estimates in the Postgres planner. |

---

## Statistics & Caching

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.enable_metadata_stats` | bool | — | Injects Iceberg/Delta/Parquet metadata statistics (row counts, min/max, NDV) into the PG planner (added 1.11.0). |
| `pgaa.metadata_stats_ttl_s` | integer | — | TTL (seconds) for cached metadata statistics. |
| `pgaa.lakehouse_table_stats_cache_ttl_s` | integer | — | TTL (seconds) for the `pgaa.lakehouse_table_stats_cache` entries. |
| `pgaa.max_in_memory_mb` | integer | — | Maximum in-memory buffer (MB) for the replication write executor before flushing. |

---

## Replication

| GUC | Type | Default | Description |
|-----|------|---------|-------------|
| `pgaa.max_replication_lag_s` | integer | 5 | Replication lag threshold (seconds) before a flush is forced. |
| `pgaa.flush_task_interval_s` | integer | 5 | Interval between replication flush task cycles. |

---

## Iceberg / Manifest Concurrency

| GUC | Description |
|-----|-------------|
| `pgaa.iceberg_manifest_concurrency` | Controls the concurrency level for reading Iceberg manifest files (added 1.11.0). |

---

## Storage Environment Variables (not GUCs)

Set in the environment for Seafowl and metastore-agent containers:

```
AWS_DEFAULT_REGION / AWS_REGION     — AWS region for S3 access
AWS_ROLE_ARN                        — IAM role ARN for IRSA
AWS_WEB_IDENTITY_TOKEN_FILE         — Service account token path
SEAFOWL__RUNTIME__TEMP_DIR          — Seafowl spill/temp directory
SEAFOWL__MISC__OBJECT_STORE_CACHE__CAPACITY  — Object store cache (bytes)
TMPDIR                              — Fallback temp dir
```

---

## DirectScan vs. CompatScan

**DirectScan** — pgaa serializes the entire SQL query text and forwards it verbatim to the engine.
Fastest path; requires that all tables in the query come from the same single catalog (or none are
schema-qualified), and the engine must support all functions used.

**CompatScan** — pgaa acts as a custom scan node: Postgres plans the query, pgaa pushes down
individual operations (filters, joins, GROUP BY, ORDER BY, DISTINCT, windows) to the engine as
capabilities allow, and reassembles results. More compatible; slower when pushdown is partial.

Set `pgaa.direct_scan_fail_behavior` to surface why DirectScan is being skipped for a query.
