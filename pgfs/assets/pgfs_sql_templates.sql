-- pgfs Quick Reference: Copy-paste SQL templates for common operations.
-- Replace placeholder values (<...>) with real values before executing.

-- ============================================================
-- 0. INSTALL
-- ============================================================
CREATE EXTENSION pgfs CASCADE;

-- ============================================================
-- 1. CREATE STORAGE LOCATION
-- ============================================================

-- AWS S3 (static credentials)
SELECT pgfs.create_storage_location(
    '<location_name>',
    's3://<bucket_name>',
    options      => '{"region": "<aws_region>"}',
    credentials  => '{"access_key_id": "<key_id>", "secret_access_key": "<secret>"}'
);

-- AWS S3 (IAM / instance profile — no credentials block)
SELECT pgfs.create_storage_location(
    '<location_name>',
    's3://<bucket_name>',
    options => '{"region": "<aws_region>"}'
);

-- AWS S3 with temporary STS credentials
SELECT pgfs.create_storage_location(
    '<location_name>',
    's3://<bucket_name>',
    options     => '{"region": "<aws_region>"}',
    credentials => '{"access_key_id": "<key_id>", "secret_access_key": "<secret>", "session_token": "<token>"}'
);

-- Azure Blob Storage
SELECT pgfs.create_storage_location(
    '<location_name>',
    'az://<container_name>',
    options     => '{"account": "<storage_account>"}',
    credentials => '{"access_key": "<storage_key>"}'
);

-- Azure Data Lake Storage Gen2
SELECT pgfs.create_storage_location(
    '<location_name>',
    'abfss://<container>@<account>.dfs.core.windows.net/<path>',
    options     => '{}',
    credentials => '{"access_key": "<storage_key>"}'
);

-- Google Cloud Storage
SELECT pgfs.create_storage_location(
    '<location_name>',
    'gs://<bucket_name>',
    options     => '{}',
    credentials => '{"google_service_account_key": "<base64_service_account_json>"}'
);

-- Local filesystem (path must be under pgfs.allowed_local_fs_paths)
SELECT pgfs.create_storage_location(
    '<location_name>',
    'file:///<absolute_path>'
);

-- In-memory (testing only)
SELECT pgfs.create_storage_location('<location_name>', 'memory://');

-- credentials_env: store env var name, not the secret itself
SELECT pgfs.create_storage_location(
    '<location_name>',
    's3://<bucket_name>',
    options          => '{"region": "<aws_region>"}',
    credentials      => NULL,
    credentials_env  => 'PGFS_<MY_SECRET_ENV_VAR>'
);

-- ============================================================
-- 2. CREATE STORAGE LOCATION + FOREIGN TABLE IN ONE CALL
--    (creates <location_name>_ft automatically)
-- ============================================================
SELECT pgfs.create_storage_location_with_foreign_table(
    '<location_name>',
    's3://<bucket_name>',
    options     => '{"region": "<aws_region>"}',
    credentials => '{"access_key_id": "<key_id>", "secret_access_key": "<secret>"}'
);

-- ============================================================
-- 3. CREATE FOREIGN TABLE MANUALLY
-- ============================================================
SELECT pgfs.create_foreign_table(
    '<table_name>',          -- may be schema-qualified: 'myschema.mytable'
    '<location_name>',       -- storage location name
    '/',                     -- optional sub-path (default '/')
    '{}'                     -- optional extra JSONB options
);

-- ============================================================
-- 4. USE A FOREIGN TABLE
-- ============================================================

-- List objects (body is NULL without key filter)
SELECT key, size, last_modified, e_tag FROM <table_name>;

-- Read a specific object's content (body only populated with WHERE key = '...')
SELECT key, convert_from(body, 'UTF8') AS content
FROM <table_name>
WHERE key = '<object_key>';

-- Read raw bytes of a specific object
SELECT body FROM <table_name> WHERE key = '<object_key>';

-- Upload a new object
INSERT INTO <table_name> (key, body)
VALUES ('<object_key>', '<content>'::bytea);

-- Update object content (key column cannot be changed via UPDATE)
UPDATE <table_name>
SET body = '<new_content>'::bytea
WHERE key = '<object_key>';

-- Rename an object: DELETE + INSERT (UPDATE on key is rejected)
DELETE FROM <table_name> WHERE key = '<old_key>';
INSERT INTO <table_name> (key, body) VALUES ('<new_key>', <body_value>);

-- Delete an object
DELETE FROM <table_name> WHERE key = '<object_key>';

-- ============================================================
-- 5. MANAGE STORAGE LOCATIONS
-- ============================================================

-- List all (credentials masked)
SELECT * FROM pgfs.list_storage_locations();

-- Get one (credentials unmasked)
SELECT * FROM pgfs.get_storage_location('<location_name>');

-- Update (replaces the location; use same params as create)
SELECT pgfs.update_storage_location(
    '<location_name>',
    's3://<new_bucket>',
    options     => '{"region": "<new_region>"}',
    credentials => '{"access_key_id": "<new_key>", "secret_access_key": "<new_secret>"}'
);

-- Delete (does NOT auto-drop associated foreign tables)
SELECT pgfs.delete_storage_location('<location_name>');

-- ============================================================
-- 6. DEFAULT STORAGE LOCATION
-- ============================================================
SELECT pgfs.set_default_storage_location('<location_name>');
SELECT * FROM pgfs.get_default_storage_location();

-- ============================================================
-- 7. CONFIGURATION (local filesystem path allowlist)
-- ============================================================

-- View current setting
SHOW pgfs.allowed_local_fs_paths;

-- Temporarily allow a path (session-scoped)
SET pgfs.allowed_local_fs_paths = '/tmp/pgfs:/data/shared';

-- Persist in postgresql.conf or via ALTER SYSTEM (requires superuser + reload)
ALTER SYSTEM SET pgfs.allowed_local_fs_paths = '/tmp/pgfs:/data/shared';
SELECT pg_reload_conf();

-- ============================================================
-- 8. INTROSPECTION VIEWS
-- ============================================================

-- All server-level options for every pgfs storage location
SELECT * FROM pgfs.v_server_options WHERE srvname = '<location_name>';

-- All pgfs foreign tables
SELECT * FROM pgfs.v_foreign_tables;

-- All foreign table options
SELECT * FROM pgfs.v_foreign_table_options WHERE ft_name = '<table_name>';

-- Extension version
SELECT pgfs.pgfs_version();
