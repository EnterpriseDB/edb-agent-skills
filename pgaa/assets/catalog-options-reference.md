# pgaa Catalog Options Quick Reference

Use this asset when constructing JSON options for `pgaa.add_catalog()`,
`pgaa.update_catalog()`, or `pgaa.validate_catalog_connection()`.

---

## iceberg-rest

**Required:** `url`

```json
{
  "url": "https://my-iceberg-catalog.example.com",
  "warehouse": "my_warehouse",
  "token": "static-bearer-token",

  "oauth2.client_id": "my-client-id",
  "oauth2.client_secret": "my-client-secret",
  "oauth2.token_uri": "https://auth.example.com/oauth/token",
  "oauth2.scope": "catalog",
  "oauth2.grant_type": "client_credentials",

  "danger_accept_invalid_certs": "true"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `url` | **Yes** | REST catalog endpoint |
| `warehouse` | No | Catalog warehouse/location identifier |
| `token` | No | Static bearer token for auth |
| `oauth2.*` | No | OAuth2 fields for dynamic token retrieval |
| `danger_accept_invalid_certs` | No | `"true"` disables SSL cert validation (dev/test only) |

---

## iceberg-s3tables

**Required:** `arn`

```json
{
  "arn": "arn:aws:s3tables:us-east-1:123456789012:bucket/my-table-bucket",
  "region": "us-east-1"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `arn` | **Yes** | S3 Tables bucket ARN |
| `region` | No | AWS region; inferred from environment if omitted |

Credentials are picked up from standard AWS env variables:
`AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_DEFAULT_REGION`, etc.

---

## pgaa.launch_task Options by Task Type

Used with `pgaa.launch_task(table_name, task_type, task_options)`.

### compaction
```json
{
  "target_size": 268435456,
  "preserve_insertion_order": false,
  "max_concurrent_tasks": 4,
  "max_spill_size": 1073741824,
  "min_commit_interval": 60
}
```

### zorder
```json
{
  "columns": ["col_a", "col_b"],
  "target_size": 268435456,
  "max_concurrent_tasks": 4
}
```
`columns` is required for zorder.

### vacuum
```json
{
  "retention_period": "7 days",
  "dry_run": false,
  "enforce_retention_duration": true
}
```

### purge
```json
{
  "storage_location": "my-storage-location",
  "path": "path/to/table/data"
}
```
Both `storage_location` and `path` are required for purge.

---

## Catalog Status Values

| Status | Meaning |
|--------|---------|
| `detached` | Catalog registered but no continuous sync running |
| `attached` | Continuous sync active via `attach_catalog()` |
| `refresh_retry` | Sync worker encountered an error and is retrying |
| `refresh_failed` | Sync worker has given up retrying |

## Table Format Values

| Format | Description |
|--------|-------------|
| `iceberg` | Apache Iceberg table format |
| `delta` | Delta Lake table format |
| `parquet` | Raw Parquet files (read-only, no catalog) |

## Background Task Status Values

| Status | Meaning |
|--------|---------|
| `pending` | Task scheduled, not yet started |
| `running` | Task currently executing |
| `success` | Task completed successfully |
| `failure` | Task failed; see `output` JSON for error details |
