## pgaa Catalog Options — Quick-Reference Card

### Iceberg REST Catalog (`'iceberg-rest'`)

```json
{
  "url":                         "<required>  Iceberg REST API base URL",
  "warehouse":                   "<optional>  Warehouse ID (UUID)",
  "warehouse_name":              "<optional>  Warehouse name (string)",
  "token":                       "<optional>  Bearer token",
  "danger_accept_invalid_certs": "<optional>  'true' to accept self-signed TLS (dev only)",
  "oauth2.grant_type":           "<optional>  e.g. 'client_credentials'",
  "oauth2.client_id":            "<optional>  OAuth2 client ID",
  "oauth2.client_secret":        "<optional>  OAuth2 client secret",
  "oauth2.token_uri":            "<optional>  OAuth2 token endpoint URL",
  "oauth2.scope":                "<optional>  OAuth2 scopes"
}
```

### Iceberg S3 Tables Catalog (`'iceberg-s3tables'`)

```json
{
  "arn":    "<required>  S3 Tables bucket ARN (e.g. arn:aws:s3tables:us-east-1:123456:bucket/name)",
  "region": "<required>  AWS region (e.g. us-east-1)"
}
```

> **S3 Tables limitations:** No PGD replication, no tiered tables, no analytics offload. Use Iceberg REST for those features.

---

## pgaa Table WITH Options — Quick-Reference Card

| Option | Type | Pattern | Description |
|---|---|---|---|
| `pgaa.managed_by` | text | Catalog | Catalog name |
| `pgaa.catalog_namespace` | text | Catalog | Namespace/schema in catalog |
| `pgaa.catalog_table` | text | Catalog | Table name in catalog |
| `pgaa.storage_location` | text | Direct | pgfs storage location name |
| `pgaa.path` | text | Direct | Path within storage location |
| `pgaa.format` | enum | Both | `iceberg` \| `delta` \| `parquet` (read-only) |
| `pgaa.auto_truncate` | boolean | PGD | Flush heap on access method switch |
| `pgaa.purge_data_if_exists` | boolean | CTAS | Overwrite existing data at target |
| `pgaa.tiered_table` | boolean | PGD | Mark as tiered table |
| `pgd.replicate_to_analytics` | boolean | PGD | Enable PGD → Iceberg replication |
| `pgd.purge_analytics_target` | boolean | PGD | Purge before enabling replication (PGD 6.3+) |

---

## pgaa GUC Defaults — Quick-Reference Card

| GUC | Default | Notes |
|---|---|---|
| `pgaa.executor_engine` | `seafowl` | `seafowl` or `spark_connect` |
| `pgaa.seafowl_url` | `http://localhost:47470` | |
| `pgaa.spark_connect_url` | _(none)_ | Set to `sc://host:15002` |
| `pgaa.autostart_seafowl` | `on` | |
| `pgaa.autostart_seafowl_port` | `47470` | |
| `pgaa.enable_direct_scan` | `on` | |
| `pgaa.direct_scan_fail_behavior` | `warn` | `ignore` \| `warn` \| `error` |
| `pgaa.enable_join_pushdown` | `on` | |
| `pgaa.enable_groupby_pushdown` | `on` | |
| `pgaa.use_seafowl_cost_estimates` | `on` | |
| `pgaa.enable_maintenance_worker` | **`off`** | **Must enable for launch_task to run** |
| `pgaa.maintenance_worker_sleep_interval` | `30` | seconds |
| `pgaa.enable_metastore_sync_worker` | `on` | |
| `pgaa.metastore_sync_poll_rate_s` | `60` | seconds |
| `pgaa.max_replication_lag_s` | `5` | seconds |
| `pgaa.max_in_memory_mb` | `3072` | MB |
| `pgaa.lakehouse_table_stats_cache_ttl_s` | `300` | seconds |

---

## Administrative Enums

| Type | Values |
|---|---|
| `pgaa.table_format` | `delta`, `iceberg`, `parquet` |
| `pgaa.catalog_type` | `iceberg-rest`, `iceberg-s3tables` |
| `pgaa.catalog_status` | `detached`, `attached`, `refresh_retry`, `refresh_failed` |
| `pgaa.replication_status` | `disabled`, `initial_offload`, `enabled` |
| `pgaa.task_status` | `pending`, `running`, `success`, `failure` |

---

## Background Task Options — Quick-Reference Card

### compaction (delta + iceberg)
```json
{
  "target_size": 536870912,
  "preserve_insertion_order": true,
  "max_concurrent_tasks": 10,
  "max_spill_size": 2147483648,
  "min_commit_interval": 60,
  "dry_run": false
}
```

### zorder (delta only)
```json
{
  "columns": ["col1", "col2"],
  "target_size": 1073741824,
  "preserve_insertion_order": false,
  "max_concurrent_tasks": 4,
  "max_spill_size": 2147483648,
  "min_commit_interval": 30
}
```

### vacuum (delta only)
```json
{
  "retention_period": "168 hours",
  "dry_run": false,
  "enforce_retention_duration": true
}
```

### purge (delta only)
```json
{
  "storage_location": "my-location",
  "path": "archive/temp/"
}
```
