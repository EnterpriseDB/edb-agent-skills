# pgfs Storage Location Templates

## AWS S3

```sql
SELECT pgfs.create_storage_location(
    'my_s3_location',                        -- name (unique identifier)
    's3://YOUR_BUCKET_NAME',                 -- URL
    options      => '{"region": "us-east-1"}',
    credentials  => '{
        "access_key_id":     "YOUR_ACCESS_KEY_ID",
        "secret_access_key": "YOUR_SECRET_ACCESS_KEY"
    }'
    -- For temporary credentials, add:
    -- "session_token": "YOUR_SESSION_TOKEN"
);
```

## AWS S3 — IAM Role / Instance Profile (no explicit credentials)

```sql
SELECT pgfs.create_storage_location(
    'my_s3_iam',
    's3://YOUR_BUCKET_NAME',
    options => '{"region": "us-east-1"}'
    -- credentials omitted — will use the instance/pod IAM role
);
```

## Azure Blob Storage

```sql
SELECT pgfs.create_storage_location(
    'my_azure_location',
    'az://YOUR_CONTAINER_NAME',
    options     => '{}',
    credentials => '{
        "account_name": "YOUR_STORAGE_ACCOUNT",
        "account_key":  "YOUR_ACCOUNT_KEY"
    }'
);
```

## Azure Data Lake Storage Gen2 (ABFSS)

```sql
SELECT pgfs.create_storage_location(
    'my_adls_location',
    'abfss://YOUR_CONTAINER@YOUR_ACCOUNT.dfs.core.windows.net/optional/subpath',
    options     => '{}',
    credentials => '{
        "account_name": "YOUR_ACCOUNT",
        "account_key":  "YOUR_KEY"
    }'
);
```

## Google Cloud Storage

```sql
SELECT pgfs.create_storage_location(
    'my_gcs_location',
    'gs://YOUR_BUCKET_NAME',
    options     => '{}',
    credentials => '{
        "service_account_key": "YOUR_BASE64_ENCODED_JSON_KEY"
    }'
);
```

## Local Filesystem

```sql
-- First, ensure the path is allowed (superuser required):
SET pgfs.allowed_local_fs_paths = '/data/shared';

SELECT pgfs.create_storage_location(
    'my_local_location',
    'file:///data/shared'
    -- no credentials needed for local filesystem
);
```

## In-Memory (Testing Only)

```sql
SELECT pgfs.create_storage_location(
    'my_memory_location',
    'memory://'
);
```

---

## Creating a Foreign Table Over a Storage Location

```sql
-- Simple (whole bucket/container root):
SELECT pgfs.create_foreign_table('my_files', 'my_s3_location');

-- With a sub-path prefix:
SELECT pgfs.create_foreign_table('my_reports', 'my_s3_location', '/reports/2024/');

-- Schema-qualified table name:
SELECT pgfs.create_foreign_table('analytics.raw_files', 'my_s3_location', '/raw/');

-- Combined (create location + foreign table in one call):
SELECT pgfs.create_storage_location_with_foreign_table(
    'my_s3_location',
    's3://my-bucket',
    options     => '{"region": "eu-west-1"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);
-- Creates foreign table named: my_s3_location_ft
```

---

## Common DML Patterns

```sql
-- List objects (metadata only — body is NULL):
SELECT key, size, last_modified, e_tag
FROM my_files
ORDER BY last_modified DESC
LIMIT 100;

-- Read a single object's content:
SELECT body FROM my_files WHERE key = 'path/to/file.txt';

-- Read content as text:
SELECT convert_from(body, 'UTF8') FROM my_files WHERE key = 'readme.md';

-- Upload a new object:
INSERT INTO my_files (key, body)
VALUES ('uploads/hello.txt', 'Hello, World!'::bytea);

-- Overwrite an existing object:
UPDATE my_files SET body = 'Updated content'::bytea WHERE key = 'uploads/hello.txt';

-- Delete an object:
DELETE FROM my_files WHERE key = 'uploads/hello.txt';

-- Rename an object (DELETE + INSERT — UPDATE cannot rename keys):
BEGIN;
-- Note: object store ops are NOT transactional; plan for partial failure
INSERT INTO my_files (key, body)
  SELECT 'new/path/file.txt', body FROM my_files WHERE key = 'old/path/file.txt';
DELETE FROM my_files WHERE key = 'old/path/file.txt';
COMMIT;
```
