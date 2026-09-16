# pgaa Catalog Options Reference

This asset documents the exact JSON shape for `catalog_options` used with `pgaa.add_catalog()`, `pgaa.update_catalog()`, and `pgaa.validate_catalog_connection()`.

---

## catalog_type: `'iceberg-rest'`

### Required Fields
| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Base URL of the Iceberg REST catalog endpoint. |

### Optional Fields
| Field | Type | Description |
|-------|------|-------------|
| `warehouse` | string | Warehouse identifier passed to the catalog on every request. |
| `token` | string | Static bearer token for authorization. |
| `oauth2.client_id` | string | OAuth2 client ID for dynamic token exchange. |
| `oauth2.client_secret` | string | OAuth2 client secret. |
| `oauth2.token_uri` | string | OAuth2 token endpoint URI. |
| `oauth2.scope` | string | OAuth2 scope(s), space-separated. |
| `oauth2.grant_type` | string | OAuth2 grant type (e.g. `"client_credentials"`). |
| `danger_accept_invalid_certs` | `"true"` / `"false"` | **Testing only.** Disables TLS certificate verification. Never use in production. |

### Example (Lakekeeper / generic Iceberg REST)
```json
{
  "url": "https://catalog.example.com",
  "warehouse": "my-warehouse",
  "token": "my-static-token"
}
```

### Example (OAuth2)
```json
{
  "url": "https://catalog.example.com",
  "warehouse": "prod-warehouse",
  "oauth2.client_id": "my-client-id",
  "oauth2.client_secret": "my-client-secret",
  "oauth2.token_uri": "https://auth.example.com/oauth/token",
  "oauth2.scope": "catalog:read catalog:write",
  "oauth2.grant_type": "client_credentials"
}
```

### Example (MinIO AIStor)
```json
{
  "url": "http://minio-host:9000/iceberg",
  "warehouse": "s3://my-bucket/warehouse"
}
```

---

## catalog_type: `'iceberg-s3tables'`

### Required Fields
| Field | Type | Description |
|-------|------|-------------|
| `arn` | string | ARN of the AWS S3 Tables bucket. Example: `arn:aws:s3tables:us-east-1:123456789012:bucket/my-bucket`. |

### Optional Fields
| Field | Type | Description |
|-------|------|-------------|
| `region` | string | AWS region override. If omitted, inferred from environment (`AWS_REGION` / `AWS_DEFAULT_REGION`). |

### Credential Acquisition
Credentials are picked up from standard AWS environment variables:
- `AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE` (EKS IRSA)
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (static credentials)
- Instance metadata (EC2/ECS)

### Example
```json
{
  "arn": "arn:aws:s3tables:us-east-1:123456789012:bucket/my-analytics-bucket",
  "region": "us-east-1"
}
```

---

## Catalog Lifecycle Quick Reference

```
pgaa.validate_catalog_connection(type, options)  -- test BEFORE registering
         |
         v
pgaa.add_catalog(name, type, options)            -- register (validates first)
         |
         +-- pgaa.attach_catalog(name)           -- continuous sync (background worker)
         |       pgaa.detach_catalog(name)        -- stop sync, keep tables
         |
         +-- pgaa.import_catalog(name, ns)        -- one-time import
         |
         +-- pgaa.test_catalog(name, test_writes) -- test registered catalog
         |
         +-- pgaa.update_catalog(name, options)   -- update credentials/options
         |
         +-- pgaa.list_catalog_tables(name, ns)   -- browse catalog contents
         |
         +-- pgaa.drop_catalog_tables(name, cascade)
         |
         v
pgaa.delete_catalog(name, cascade)              -- remove registration
```

---

## Databricks Unity Catalog (iceberg-rest via Unity)

Databricks Unity Catalog exposes an Iceberg REST-compatible endpoint. Configure as `iceberg-rest` with the Unity Catalog URL and an OAuth2 personal access token:

```json
{
  "url": "https://<workspace>.azuredatabricks.net/api/2.1/unity-catalog/iceberg",
  "warehouse": "<catalog-name>",
  "token": "<databricks-pat>"
}
```
