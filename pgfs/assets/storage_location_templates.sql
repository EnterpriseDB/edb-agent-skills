-- ============================================================
-- pgfs Storage Location Templates
-- Copy and adapt these templates to configure pgfs for your backend.
-- All functions are in the pgfs schema.
-- ============================================================

-- PREREQUISITE: Install the extension (requires superuser)
CREATE EXTENSION IF NOT EXISTS pgfs CASCADE;


-- ============================================================
-- AWS S3
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_s3_bucket',                         -- storage location name (unique)
    's3://my-bucket-name',                  -- URL: s3://<bucket-name>
    '{"region": "us-east-1"}',             -- options JSON
    '{
        "access_key_id":     "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "session_token":     ""
    }'                                      -- credentials JSON (session_token optional)
);
-- Then create a foreign table:
SELECT pgfs.create_foreign_table('s3_files', 'my_s3_bucket');
-- Or create both at once (table named 'my_s3_bucket_ft'):
-- SELECT pgfs.create_storage_location_with_foreign_table('my_s3_bucket', 's3://my-bucket-name', '{"region": "us-east-1"}', '{"access_key_id": "...", "secret_access_key": "..."}');


-- ============================================================
-- Azure Blob Storage
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_azure_blob',
    'az://my-container',                    -- URL: az://<container-name>
    NULL,                                   -- no extra options typically needed
    '{
        "account_name": "mystorageaccount",
        "access_key":   "base64encodedkey=="
    }'
);
SELECT pgfs.create_foreign_table('azure_files', 'my_azure_blob');


-- ============================================================
-- Azure Data Lake Storage Gen2 (ADLS Gen2)
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_adls',
    'abfss://mycontainer@mystorageaccount.dfs.core.windows.net/mypath',
    NULL,
    '{
        "account_name": "mystorageaccount",
        "access_key":   "base64encodedkey=="
    }'
);
SELECT pgfs.create_foreign_table('adls_files', 'my_adls');


-- ============================================================
-- Google Cloud Storage
-- ============================================================
SELECT pgfs.create_storage_location(
    'my_gcs_bucket',
    'gs://my-gcs-bucket',                  -- URL: gs://<bucket-name>
    NULL,
    '{
        "service_account_key": "{\"type\":\"service_account\",...}"
    }'
);
SELECT pgfs.create_foreign_table('gcs_files', 'my_gcs_bucket');


-- ============================================================
-- Local Filesystem
-- ============================================================
-- First allow the path (session-level; add to postgresql.conf for persistence):
SET pgfs.allowed_local_fs_paths = '/data/shared';

SELECT pgfs.create_storage_location(
    'local_data',
    'file:///data/shared',                  -- URL: file://<absolute-path>
    NULL,
    NULL                                    -- no credentials for local filesystem
);
SELECT pgfs.create_foreign_table('local_files', 'local_data');


-- ============================================================
-- In-Memory (Testing Only)
-- ============================================================
SELECT pgfs.create_storage_location(
    'mem_test',
    'memory://',
    NULL,
    NULL
);
SELECT pgfs.create_foreign_table('mem_files', 'mem_test');


-- ============================================================
-- Foreign Table with Sub-path
-- ============================================================
-- Scope a foreign table to a sub-path within the storage location:
SELECT pgfs.create_foreign_table(
    'reports_2024',                         -- table name
    'my_s3_bucket',                         -- storage location
    '/reports/2024/',                       -- sub-path (default is '/')
    '{}'                                    -- extra options
);


-- ============================================================
-- Storage Location Management
-- ============================================================

-- List all storage locations (credentials masked)
SELECT name, url, options FROM pgfs.list_storage_locations();

-- Get a specific storage location with real credentials
SELECT * FROM pgfs.get_storage_location('my_s3_bucket');

-- Update a storage location (replaces all options and credentials)
SELECT pgfs.update_storage_location(
    'my_s3_bucket',
    's3://new-bucket-name',
    '{"region": "eu-west-1"}',
    '{"access_key_id": "NEWKEY", "secret_access_key": "NEWSECRET"}'
);

-- Delete a storage location (does NOT drop associated foreign tables)
-- Drop foreign tables first:
DROP FOREIGN TABLE IF EXISTS s3_files;
SELECT pgfs.delete_storage_location('my_s3_bucket');

-- Set / get default storage location
SELECT pgfs.set_default_storage_location('my_s3_bucket');
SELECT * FROM pgfs.get_default_storage_location();


-- ============================================================
-- Working With Foreign Tables
-- ============================================================

-- List objects (metadata only, body is NULL without key filter)
SELECT key, size, last_modified, e_tag FROM s3_files;
SELECT key, size FROM s3_files LIMIT 100;

-- Read a specific object's content
SELECT body FROM s3_files WHERE key = 'path/to/file.txt';
-- Cast body to text if the object is text:
SELECT convert_from(body, 'UTF8') AS content FROM s3_files WHERE key = 'readme.md';

-- Upload (INSERT) a new object
INSERT INTO s3_files (key, body)
VALUES ('uploads/hello.txt', 'Hello World!'::bytea);

-- Upload from a PostgreSQL value
INSERT INTO s3_files (key, body)
VALUES ('data/export.json', to_jsonb('{"a":1}')::text::bytea);

-- Update an object's content (key CANNOT be changed via UPDATE)
UPDATE s3_files SET body = 'Updated content'::bytea WHERE key = 'uploads/hello.txt';

-- Rename an object (DELETE + INSERT — not transactional!)
INSERT INTO s3_files (key, body)
    SELECT 'uploads/renamed.txt', body FROM s3_files WHERE key = 'uploads/hello.txt';
DELETE FROM s3_files WHERE key = 'uploads/hello.txt';

-- Delete an object
DELETE FROM s3_files WHERE key = 'uploads/hello.txt';
