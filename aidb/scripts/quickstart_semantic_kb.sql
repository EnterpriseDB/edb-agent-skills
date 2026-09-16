-- quickstart_semantic_kb.sql
-- Build a Semantic Knowledge Base over a schema and run natural language queries.
-- Uses the 'dummy' provider for testing; replace with a real embedding model for
-- meaningful similarity scores.
--
-- Usage:
--   psql -d <your_database> -f quickstart_semantic_kb.sql

\echo '=== Quickstart: Semantic Knowledge Base ==='

-- Step 1: Ensure embedding model exists
SELECT aidb.create_model('kb_embed_model', 'dummy') AS model_created;

-- Step 2: Create a demo schema with commented tables (comments are what get embedded)
CREATE SCHEMA IF NOT EXISTS sales_demo;

CREATE TABLE IF NOT EXISTS sales_demo.customers (
    id SERIAL PRIMARY KEY,
    email TEXT,
    region TEXT
);
COMMENT ON TABLE sales_demo.customers IS 'Customer accounts and regional data';
COMMENT ON COLUMN sales_demo.customers.email IS 'Customer email address for notifications';

CREATE TABLE IF NOT EXISTS sales_demo.orders (
    id SERIAL PRIMARY KEY,
    customer_id INT,
    total NUMERIC(12,2),
    created_at TIMESTAMPTZ DEFAULT now()
);
COMMENT ON TABLE sales_demo.orders IS 'Purchase orders placed by customers';
COMMENT ON COLUMN sales_demo.orders.total IS 'Total order value in USD';

CREATE TABLE IF NOT EXISTS sales_demo.products (
    id SERIAL PRIMARY KEY,
    name TEXT,
    price NUMERIC(10,2),
    stock INT
);
COMMENT ON TABLE sales_demo.products IS 'Product catalog with pricing and inventory levels';

-- Step 3: Create the Semantic Knowledge Base (Live mode indexes immediately)
SELECT aidb.create_semantic_kb(
    'sales_kb',
    'kb_embed_model',
    ARRAY['sales_demo'],
    'Live'
) AS kb_created;

-- Step 4: Query tables and columns by natural language
\echo ''
\echo 'Finding tables related to "customer purchases":'
SELECT relation_name, entity_type, round(similarity::numeric, 3) AS score
FROM aidb.get_tables('sales_kb', 'customer purchases', 0.0, 5, 0)
ORDER BY score DESC;

\echo ''
\echo 'Finding columns related to "price or cost":'
SELECT relation_name, column_name, round(similarity::numeric, 3) AS score
FROM aidb.get_columns('sales_kb', 'price or cost', 0.0, 10, 0)
ORDER BY score DESC;

\echo ''
\echo 'KB statistics:'
SELECT * FROM aidb.semantic_kb_stats('sales_kb');

-- Cleanup
-- SELECT aidb.delete_semantic_kb('sales_kb');
-- DROP SCHEMA sales_demo CASCADE;

\echo '=== Semantic KB Quickstart Complete ==='
