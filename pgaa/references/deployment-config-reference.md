# pgaa Deployment & Configuration Reference

This document covers deployment contexts, runtime configuration (GUCs), and operational setup for the EDB Postgres Analytics Accelerator (pgaa).

---

## Deployment Contexts

pgaa runs in three distinct contexts. Choose the right setup path based on your deployment:

| Context | Description | Key Requirements |
|---|---|---|
| **Lakehouse Cluster** | Standalone EDB Postgres instance for direct analytics queries against Iceberg/Delta/Parquet | None beyond `CREATE EXTENSION pgaa CASCADE` |
| **WarehousePG (WHPG)** | pgaa inside EDB's Greenplum-fork distributed MPP database (coordinator + segment hosts) | `pgaa` must be listed in `shared_preload_libraries`; note that DirectScan falls back to CompatScan on WHPG |
| **PGD (Postgres Distributed)** | pgaa as the analytics side of an HTAP cluster; transactional tables replicate from PGD heap to Iceberg | Requires `bdr` extension; configure a default write catalog for replication |

### Installation

```sql
-- Installs pgaa and its dependency (pgfs) in one step.
-- Requires PostgreSQL 16, 17, or 18.
CREATE EXTENSION pgaa CASCADE;
```

For WarehousePG, add to `postgresql.conf` **before** creating the extension:

```
shared_preload_libraries = 'pgaa'
```

---

## Execution Engines

### Seafowl (default)

An embedded, in-process DataFusion-based engine. Starts automatically as a background worker (controlled by `pgaa.autostart_seafowl`). No external dependencies.

**Scan modes:**
- **DirectScan** — offloads the entire query to Seafowl over Apache Arrow Flight (fast, preferred path). Active when `pgaa.enable_direct_scan = on`.
- **CompatScan** — falls back to the PostgreSQL executor when DirectScan cannot handle the query or is disabled. CTAS source queries always use CompatScan.

> On WarehousePG, DirectScan falls back to CompatScan due to Greenplum's distributed execution model. EXPLAIN output shows `SeafowlCompatScan` instead of `SeafowlDirectScan`.

### Spark Connect

An external Apache Spark cluster for heavier workloads. **Read-only** — all writes must still go through PostgreSQL.

```sql
SET pgaa.executor_engine = 'spark_connect';
SET pgaa.spark_connect_url = 'sc://spark-host:15002';
-- Optional: pass extra Spark session config
SET pgaa.spark_connect_extra_config = '{"spark.sql.shuffle.partitions": "200"}';
```

---

## GUC Reference

All parameters can be set in `postgresql.conf` (server-wide), `ALTER SYSTEM`, or per-session via `SET`.

### Engine Selection

| Parameter | Default | Values | Description |
|---|---|---|---|
| `pgaa.executor_engine` | `seafowl` | `seafowl`, `spark_connect` | Active query engine |
| `pgaa.seafowl_url` | `http://localhost:47470` | URL | Seafowl HTTP endpoint (if not auto-started) |
| `pgaa.spark_connect_url` | _(none)_ | `sc://host:port` | Spark Connect endpoint |
| `pgaa.spark_connect_extra_config` | _(none)_ | JSON string | Additional Spark session properties |

### Query Behavior & Cost Estimation

| Parameter | Default | Description |
|---|---|---|
| `pgaa.enable_direct_scan` | `on` | Offload entire query to Seafowl (DirectScan) |
| `pgaa.direct_scan_fail_behavior` | `warn` | Action on DirectScan failure: `ignore`, `warn`, or `error` |
| `pgaa.enable_join_pushdown` | `on` | Push JOINs to the remote executor |
| `pgaa.enable_groupby_pushdown` | `on` | Push GROUP BY to the remote executor |
| `pgaa.use_seafowl_cost_estimates` | `on` | Use real-time Seafowl row-count estimates |
| `pgaa.scan_startup_cost` | `25` | Fixed startup cost multiplier |
| `pgaa.scan_per_tuple_cost` | `0.02` | Per-row scan cost multiplier |
| `pgaa.scan_aggregation_cost_factor` | `0.005` | Aggregation cost multiplier |

### Embedded Seafowl Autostart

| Parameter | Default | Description |
|---|---|---|
| `pgaa.autostart_seafowl` | `on` | Auto-start Seafowl background worker |
| `pgaa.autostart_seafowl_port` | `47470` | Seafowl listen port |
| `pgaa.autostart_seafowl_max_memory_mb` | _(system)_ | Memory limit for Seafowl |
| `pgaa.autostart_seafowl_log_level` | _(none)_ | Log level: `error`, `warn`, `info`, `debug`, `trace` |
| `pgaa.autostart_seafowl_path` | _(none)_ | Path to Seafowl executable (if not on PATH) |
| `pgaa.autostart_seafowl_tmp_dir` | _(none)_ | Temporary directory for Seafowl spills |
| `pgaa.autostart_seafowl_enable_metrics` | `on` | Expose Prometheus metrics endpoint |
| `pgaa.autostart_seafowl_metrics_host` | `0.0.0.0` | Metrics bind address |
| `pgaa.autostart_seafowl_metrics_port` | `9090` | Metrics port |
| `pgaa.autostart_seafowl_enable_object_store_cache` | `on` | Object store read cache |
| `pgaa.autostart_seafowl_object_store_cache_ttl_s` | `3600` | Cache TTL in seconds |
| `pgaa.autostart_seafowl_object_store_cache_capacity_mb` | `1024` | Cache size in MiB |
| `pgaa.autostart_seafowl_object_store_cache_min_fetch_size_kb` | `1024` | Minimum fetch unit in KiB |

### Writer & Replication Flush

| Parameter | Default | Description |
|---|---|---|
| `pgaa.max_replication_lag_s` | `5` | Maximum lag (seconds) before forcing a flush |
| `pgaa.max_in_memory_mb` | `3072` | In-memory write buffer size (MiB) |
| `pgaa.ctas_intermediate_flush_size` | `8192` | Batch row count for CTAS intermediate flushes |

### Maintenance Worker

| Parameter | Default | Description |
|---|---|---|
| `pgaa.enable_maintenance_worker` | **`off`** | **Must be `on` for `pgaa.launch_task` tasks to actually execute.** |
| `pgaa.maintenance_worker_sleep_interval` | `30` | Seconds between maintenance checks |

### Catalog Sync Worker

| Parameter | Default | Description |
|---|---|---|
| `pgaa.enable_metastore_sync_worker` | `on` | Background catalog metadata sync |
| `pgaa.metastore_sync_poll_rate_s` | `60` | Polling interval in seconds |
| `pgaa.metastore_sync_stale_duration_s` | `60` | Staleness threshold in seconds |

### Caching

| Parameter | Default | Description |
|---|---|---|
| `pgaa.lakehouse_table_stats_cache_ttl_s` | `300` | Table stats cache TTL in seconds |
| `pgaa.seafowl_client_retry_base_ms` | `100` | Initial retry delay (milliseconds) |

### DataFusion Pass-Through

DataFusion's own configuration is exposed under the `pgaa.datafusion.*` namespace. List available options:

```sql
SELECT name, setting, short_desc
FROM pg_settings
WHERE name LIKE 'pgaa.datafusion%';
```

Commonly tuned settings:
- `pgaa.datafusion.execution.target_partitions` — parallelism
- `pgaa.datafusion.execution.batch_size` — Arrow record batch size
- `pgaa.datafusion.execution.parquet.*` — Parquet-specific reader options

---

## Version & Diagnostics

```sql
-- pgaa version, git SHA, build info, platform, opt level, features
SELECT pgaa.pgaa_version();

-- Seafowl version (default engine)
SELECT pgaa.seafowl_version();

-- Active engine version (uses pgaa.executor_engine setting)
SELECT pgaa.engine_version();

-- pgfs extension metadata
SELECT pgfs.storage_fdw_meta();
```

---

## Administrative Enum Types

| Type | Values |
|---|---|
| `pgaa.table_format` | `delta`, `iceberg`, `parquet` |
| `pgaa.catalog_type` | `iceberg-rest`, `iceberg-s3tables` |
| `pgaa.catalog_status` | `detached`, `attached`, `refresh_retry`, `refresh_failed` |
| `pgaa.replication_status` | `disabled`, `initial_offload`, `enabled` |
| `pgaa.task_status` | `pending`, `running`, `success`, `failure` |

---

## Data Type Mapping

### Reading (Analytics → PostgreSQL)

**Binary (native):** `boolean`, `bytea`, `char`, `smallint`, `integer`, `bigint`, `oid`, `real`, `double precision`, `numeric`, `date`, `timestamp`, `timestamptz`, `interval`, plus arrays of the numeric/boolean types.

**Text-coerced:** `text`, `varchar`, `char(n)`, `json`, `jsonb`, `uuid`, enums, text/uuid arrays, and all other types.

### Writing (PostgreSQL → Analytics)

**Binary:** `boolean`, `bytea`, `char`, `smallint`, `integer`, `bigint`, `oid`, `real`, `double precision`, `timestamp`, `timestamptz`, `date`, `interval`, `numeric`, plus arrays of numeric/boolean types.

**UTF-8 text:** `text`, `varchar`, `char(n)`, `json`, `jsonb`, `uuid`, enums, text arrays, and all other types.
