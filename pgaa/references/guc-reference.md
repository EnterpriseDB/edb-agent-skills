# pgaa GUC (Configuration Parameter) Reference

All GUCs are set via `SET pgaa.<parameter> = value;` or in `postgresql.conf`.
Source: `pgaa/sql/pgaa--*.sql`, `SUMMARY.md`, repo `README.md`.

---

## Engine Selection & Connection

| GUC | Default | Description |
|-----|---------|-------------|
| `pgaa.executor_engine` | `seafowl` | Selects the offload engine. Valid values: `seafowl`, `spark_connect`. |
| `pgaa.seafowl_url` | — | Endpoint for an **external** (non-embedded) Seafowl process. Mutually exclusive with the `autostart_seafowl_*` family. |
| `pgaa.spark_connect_url` | — | URL for the Spark Connect endpoint. Only used when `executor_engine = 'spark_connect'`. |
| `pgaa.spark_connect_extra_config` | — | JSON dictionary of extra config key/value strings forwarded to Spark Connect. |

---

## Embedded Seafowl Process (`autostart_seafowl_*` family)

These GUCs control the Seafowl background worker that pgaa can auto-start inside PostgreSQL:

| GUC | Description |
|-----|-------------|
| `pgaa.autostart_seafowl_enable_metrics` | Enable Prometheus metrics endpoint for the embedded Seafowl process. |
| `pgaa.autostart_seafowl_metrics_host` | Host for the Seafowl Prometheus metrics endpoint (default `0.0.0.0`). |
| `pgaa.autostart_seafowl_metrics_port` | Port for the Seafowl Prometheus metrics endpoint (default `9092`). |

> **Tip**: Environment variables `SEAFOWL__FRONTEND__HTTP__BIND_PORT`, `SEAFOWL__MISC__METRICS__HOST`, `SEAFOWL__MISC__METRICS__PORT`, and `SEAFOWL__MISC__OBJECT_STORE_CACHE__CAPACITY` can also be used to configure the embedded Seafowl; see root `README.md`.

---

## Background Workers

| GUC | Description |
|-----|-------------|
| `pgaa.enable_maintenance_worker` | Enable the background maintenance worker for compaction/vacuum tasks. |
| `pgaa.enable_metastore_sync_worker` | Enable the background worker that continuously syncs attached catalog schemas. |
| `pgaa.metastore_sync_poll_rate_s` | Poll interval (seconds) for the continuous catalog sync started by `pgaa.attach_catalog()`. |
| `pgaa.maintenance_worker_sleep_interval` | Sleep interval between maintenance worker iterations. |

---

## Query Planning & Pushdown

| GUC | Default | Description |
|-----|---------|-------------|
| `pgaa.enable_direct_scan` | `true` | Enable DirectScan (push the full query to the engine, bypassing CompatScan). |
| `pgaa.direct_scan_fail_behavior` | — | What to do if DirectScan is unavailable: silently fall back, log reason, or error. |
| `pgaa.enable_join_pushdown` | `true` | Push JOIN operations to the engine in CompatScan. |
| `pgaa.enable_groupby_pushdown` | `true` | Push GROUP BY to the engine in CompatScan. |
| `pgaa.enable_orderby_pushdown` | `true` | Push ORDER BY / LIMIT / OFFSET to the engine in CompatScan. |
| `pgaa.enable_distinct_pushdown` | `true` | Push DISTINCT to the engine in CompatScan. |
| `pgaa.enable_window_pushdown` | `true` | Push window functions to the engine in CompatScan. |
| `pgaa.scan_startup_cost` | — | Cost estimate planner uses for starting a PGAA scan. |
| `pgaa.scan_per_tuple_cost` | — | Cost estimate per tuple for PGAA scans. |
| `pgaa.scan_aggregation_cost_factor` | — | Cost multiplier for aggregation pushdown. |
| `pgaa.scan_sort_cost_factor` | — | Cost multiplier for sort pushdown. |
| `pgaa.use_seafowl_cost_estimates` | — | Use cost estimates returned from Seafowl rather than static GUC values. |
| `pgaa.max_in_memory_mb` | — | Maximum in-memory buffer (MB) before the write executor flushes to storage. |

---

## Statistics & Caching

| GUC | Description |
|-----|-------------|
| `pgaa.enable_metadata_stats` | Inject Iceberg/Delta/Parquet metadata statistics (row counts, min/max, NDV) into the PG planner. Governed by 1.11.0+. |
| `pgaa.metadata_stats_ttl_s` | TTL (seconds) for cached metadata statistics. |
| `pgaa.lakehouse_table_stats_cache_ttl_s` | TTL (seconds) for `pgaa.lakehouse_table_stats_cache` entries. |

---

## Replication

| GUC | Description |
|-----|-------------|
| `pgaa.max_replication_lag_s` | Replication lag threshold (seconds). Default 5s. |
| `pgaa.flush_task_interval_s` | How often the replication writer flushes buffered data. Default 5s. |

---

## Storage Authentication (Environment Variables)

These are **environment variables** (not GUCs), set in the container/OS environment for Seafowl and metastore-agent:

```
AWS_DEFAULT_REGION
AWS_REGION
AWS_ROLE_ARN
AWS_WEB_IDENTITY_TOKEN_FILE
```

For GCP workload identity on GKE, use standard GCP application default credentials.

---

## Performance Tuning Tips

1. **Unexpected slow queries**: Check `pgaa.enable_direct_scan`. If DirectScan is off or falling back, set `pgaa.direct_scan_fail_behavior = 'error'` to surface the reason.
2. **JOIN/GROUP BY not pushed down**: Verify `pgaa.enable_join_pushdown` and `pgaa.enable_groupby_pushdown` are both `on`.
3. **Planner choosing Postgres execution over Seafowl**: Lower `pgaa.scan_startup_cost` and `pgaa.scan_per_tuple_cost`, or enable `pgaa.use_seafowl_cost_estimates`.
4. **Replication lag**: Check `pgaa.max_replication_lag_s` and monitor with `pgaa.list_analytics_tables()`.
5. **Memory pressure during replication**: Tune `pgaa.max_in_memory_mb` down to flush more frequently.
