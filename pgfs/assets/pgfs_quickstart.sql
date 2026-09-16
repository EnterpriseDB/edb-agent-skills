-- pgfs_quickstart.sql
-- Copy-paste ready SQL snippets for common pgfs tasks.
-- Replace all <placeholder> values before executing.

-- ============================================================
-- 0. INSTALL EXTENSION
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pgfs CASCADE;

-- ============================================================
-- 1. AWS S3 — Storage Location + Foreign Table
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_s3_location',                          -- unique name
    's3://my-bucket-name',                     -- bucket URL
    options      => '{"region": "us-east-1"}',
    credentials  => '{
        "access_key_id":     "<YOUR_ACCESS_KEY_ID>",
        "secret_access_key": "<YOUR_SECRET_ACCESS_KEY>"
    }'
);

-- Optional: add session token for temporary credentials
-- credentials => '{"access_key_id": "...", "secret_access_key": "...", "session_token": "..."}'

SELECT pgfs.create_foreign_table('s3_files', 'my_s3_location');

-- ============================================================
-- 2. Azure Blob Storage
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_azure_location',
    'az://my-container',
    options     => NULL,
    credentials => '{"account_name": "<ACCOUNT>", "account_key": "<KEY>"}'
);
SELECT pgfs.create_foreign_table('azure_files', 'my_azure_location');

-- ============================================================
-- 3. Azure Data Lake Storage Gen2
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_adls_location',
    'abfss://my-container@myaccount.dfs.core.windows.net/my-path',
    options     => NULL,
    credentials => '{"account_name": "<ACCOUNT>", "account_key": "<KEY>"}'
);
SELECT pgfs.create_foreign_table('adls_files', 'my_adls_location');

-- ============================================================
-- 4. Google Cloud Storage
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_gcs_location',
    'gs://my-gcs-bucket',
    options     => NULL,
    credentials => '{"service_account_key": "<JSON_KEY_CONTENT>"}'
);
SELECT pgfs.create_foreign_table('gcs_files', 'my_gcs_location');

-- ============================================================
-- 5. Local Filesystem (set GUC first!)
-- ============================================================
-- Allow access to the target directory (superuser required):
SET pgfs.allowed_local_fs_paths = '/data/shared:/tmp/pgfs';

SELECT pgfs.create_storage_location('local_data', 'file:///data/shared');
SELECT pgfs.create_foreign_table('local_files', 'local_data');

-- ============================================================
-- 6. In-Memory (testing only — data lost at session end)
-- ============================================================
SELECT pgfs.create_storage_location('mem', 'memory://');
SELECT pgfs.create_foreign_table('mem_files', 'mem');

-- ============================================================
-- 7. CONVENIENCE: Storage Location + Foreign Table in one call
--    Creates <name>_ft automatically
-- ============================================================
SELECT pgfs.create_storage_location_with_foreign_table(
    'quick_s3',
    's3://another-bucket',
    options     => '{"region": "eu-west-1"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);
-- Foreign table is now available as: quick_s3_ft

-- ============================================================
-- 8. LISTING OBJECTS
-- ============================================================
-- List all objects (body is NULL here):
SELECT key, size, last_modified, e_tag FROM s3_files;

-- List with early-exit (LIMIT pushdown; no ORDER BY or aggregates):
SELECT key, size FROM s3_files LIMIT 100;

-- Filter to a known prefix using LIKE (full scan, filter in PG):
SELECT key, size FROM s3_files WHERE key LIKE 'logs/2024/%';

-- ============================================================
-- 9. READING OBJECT CONTENT (body-on-demand)
-- ============================================================
-- WRONG — body is NULL without a key = filter:
SELECT key, body FROM s3_files;

-- CORRECT — single key, body is populated:
SELECT body FROM s3_files WHERE key = 'path/to/config.json';

-- Cast to text for text files:
SELECT convert_from(body, 'UTF8') FROM s3_files WHERE key = 'readme.txt';

-- Check size without downloading:
SELECT key, size FROM s3_files WHERE key = 'large-file.parquet';

-- ============================================================
-- 10. WRITING OBJECTS
-- ============================================================
-- Upload a new file:
INSERT INTO s3_files (key, body)
VALUES ('hello.txt', 'Hello, pgfs!'::bytea);

-- Upload from an existing table column:
INSERT INTO s3_files (key, body)
SELECT 'exports/' || id::text || '.json', data::bytea
FROM my_local_table;

-- Overwrite content (key must exist — UPDATE cannot rename):
UPDATE s3_files
SET body = 'Updated content'::bytea
WHERE key = 'hello.txt';

-- RENAME pattern (DELETE + INSERT):
INSERT INTO s3_files (key, body)
SELECT 'new-name.txt', body FROM s3_files WHERE key = 'old-name.txt';
DELETE FROM s3_files WHERE key = 'old-name.txt';

-- Delete an object:
DELETE FROM s3_files WHERE key = 'hello.txt';

-- ============================================================
-- 11. STORAGE LOCATION MANAGEMENT
-- ============================================================
-- List all (credentials masked):
SELECT * FROM pgfs.list_storage_locations();

-- Get one with real credentials (sensitive!):
SELECT * FROM pgfs.get_storage_location('my_s3_location');

-- Update (replaces entirely):
SELECT pgfs.update_storage_location(
    'my_s3_location',
    's3://new-bucket',
    options     => '{"region": "us-west-2"}',
    credentials => '{"access_key_id": "...", "secret_access_key": "..."}'
);

-- Delete (does NOT drop associated foreign tables):
SELECT pgfs.delete_storage_location('my_s3_location');

-- Set / get default:
SELECT pgfs.set_default_storage_location('my_s3_location');
SELECT * FROM pgfs.get_default_storage_location();

-- ============================================================
-- 12. DIAGNOSTICS
-- ============================================================
SELECT * FROM pgfs.v_server_options WHERE srvname = 'my_s3_location';
SELECT * FROM pgfs.v_foreign_tables;
SELECT pgfs.pgfs_version();
