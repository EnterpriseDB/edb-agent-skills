# pgfs Credentials Guide

All credential configuration is passed at storage location creation time. No secrets are
stored in plain text in the SQL layer — credentials are stored in PostgreSQL foreign-server
user mappings.

---

## Credential Options by Backend

### AWS S3 (`s3://bucket`)

| Credential key | Description |
|---|---|
| `access_key_id` | AWS access key ID |
| `secret_access_key` | AWS secret access key |
| `session_token` | (Optional) STS session token for temporary credentials |

```sql
SELECT pgfs.create_storage_location(
    'my_s3',
    's3://my-bucket',
    options => '{"region": "us-east-1"}',
    credentials => '{"access_key_id": "AKIA...", "secret_access_key": "...", "session_token": "..."}'
);
```

If no credentials are supplied at all, the backend falls back to the ambient provider chain
(IAM instance role, ECS task role, environment variables, etc.).

---

### Azure Blob Storage (`az://container`)

| Credential key | Description |
|---|---|
| `access_key` | Storage account access key |

```sql
SELECT pgfs.create_storage_location(
    'my_azure',
    'az://mycontainer',
    options => '{"account": "mystorageaccount"}',
    credentials => '{"access_key": "..."}'
);
```

---

### Azure Data Lake Storage Gen2 (`abfss://...`)

```sql
SELECT pgfs.create_storage_location(
    'my_adls',
    'abfss://mycontainer@myaccount.dfs.core.windows.net/mypath',
    options => '{}',
    credentials => '{"access_key": "..."}'
);
```

---

### Google Cloud Storage (`gs://bucket`)

| Credential key | Description |
|---|---|
| `google_service_account_key` | Base64-encoded JSON service-account key |

```sql
SELECT pgfs.create_storage_location(
    'my_gcs',
    'gs://my-bucket',
    options => '{}',
    credentials => '{"google_service_account_key": "<base64-json>"}'
);
```

---

### Local Filesystem (`file:///path`)

No credentials are needed. Access is restricted to paths allowed by
`pgfs.allowed_local_fs_paths`.

```sql
-- Allow an additional directory
SET pgfs.allowed_local_fs_paths = '/tmp/pgfs:/data/shared';

SELECT pgfs.create_storage_location('local_data', 'file:///data/shared');
```

---

### In-Memory (`memory://`)

No credentials needed. Used only for testing.

---

## credentials_env: Injecting Secrets at Runtime

Instead of persisting credentials directly, you can point pgfs at an environment variable
whose JSON value will be resolved fresh at each use. Only the variable **name** is stored
in the catalog; the secret never touches persistent storage.

```sql
-- Only the variable name 'PGFS_S3_CREDS' is stored; the secret is never persisted.
SELECT pgfs.create_storage_location(
    'sl_env',
    's3://my-bucket',
    options => '{"region": "us-east-1"}',
    credentials => NULL,
    credentials_env => 'PGFS_S3_CREDS'
);
```

The environment variable must hold a JSON object with the same keys as the `credentials`
parameter would accept for the given backend. Example value:

```json
{"access_key_id": "AKIA...", "secret_access_key": "..."}
```

### Security constraints for credentials_env

| GUC | Default | Description |
|---|---|---|
| `pgfs.env_var_allowed_prefix` | `PGFS_` | Colon-separated list of allowed env var name prefixes |

- Setting `pgfs.env_var_allowed_prefix = ''` disables the restriction entirely.
- Multiple prefixes: `'PGFS_:AIDB_'` allows both `PGFS_*` and `AIDB_*` names.
- `credentials_env` and `credentials` are mutually exclusive — passing both raises an error.
- An empty JSON object (`'{}'`) for `credentials` is **not** NULL; it still conflicts with `credentials_env`.

---

## Credential Resolution Order (User Mappings)

pgfs stores credentials as PostgreSQL **user mappings** on the underlying foreign server.
Resolution order per session:

1. The current user's own user mapping (if one exists).
2. The `PUBLIC` user mapping (if one exists and the user has no personal mapping).
3. No credentials — the object store receives no auth (relies on ambient provider chain).

A superuser sees all mappings but only the user's own (or PUBLIC, as fallback) is used —
credentials from other users' mappings are never mixed in.

---

## Masking Behavior

| Function | Credential values |
|---|---|
| `pgfs.list_storage_locations()` | **Masked** — values replaced with `<redacted>` (except `credentials_env`, whose value is the env var name, not a secret) |
| `pgfs.get_storage_location(name)` | **Unmasked** — returns real credential values for the named location |
