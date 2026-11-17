-- AIDB Quick-Start Cookbook
-- Run the recipes in order on a database with the aidb extension installed
-- (CREATE EXTENSION aidb CASCADE; PostgreSQL 14-18). Recipe 1 sets up a test
-- model and sample tables, and every later recipe removes the objects it creates
-- before creating them again, so the file can be rerun
--
-- Recipes marked "requires: external service" call a real model or MCP
-- server. Without that service they fail with a connection or authentication
-- error. Replace the placeholder URL and key with your own. All other recipes
-- use the built-in "dummy" model, which returns fixed 3-dimension embeddings
-- and the text "Hello world!", so they run anywhere
--
-- Call aidb functions with named arguments where the reference shows several
-- optional parameters: positional calls are easy to get wrong

-- Recipe 1: Setup (test model and sample tables)
-- A fresh install already registers "dummy", so register it only if missing
SELECT aidb.create_model('dummy', 'dummy', validate => false)
WHERE NOT EXISTS (SELECT 1 FROM aidb.models WHERE name = 'dummy');

SELECT name, provider FROM aidb.models WHERE name = 'dummy';

DROP SCHEMA IF EXISTS aidb_cookbook CASCADE;
CREATE SCHEMA aidb_cookbook;

CREATE TABLE aidb_cookbook.customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL
);
COMMENT ON TABLE aidb_cookbook.customers IS 'Customers who place orders';
COMMENT ON COLUMN aidb_cookbook.customers.email IS 'Customer email address';

CREATE TABLE aidb_cookbook.orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES aidb_cookbook.customers (customer_id),
    total       NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE aidb_cookbook.orders IS 'Customer orders with totals';

INSERT INTO aidb_cookbook.customers VALUES
    (42, 'Ada', 'ada@example.com'),
    (43, 'Grace', 'grace@example.com');
INSERT INTO aidb_cookbook.orders (id, customer_id, total) VALUES
    (1, 42, 120.00),
    (2, 42, 35.50),
    (3, 43, 80.25);

-- Recipe 2: Register OpenAI models
-- requires: external service (OpenAI API key)
-- Secrets go in credentials (or credential, credentials_env,
-- credentials_k8s_secret), never in config. create_model test-connects the
-- model by default. Pass validate => false to register it offline
SELECT aidb.delete_model(name) FROM aidb.models WHERE name IN ('my_embedder', 'my_chat');
-- Inline credentials are stored as a credential named <current_schema>.<model>,
-- which delete_model keeps, so delete it too
SELECT aidb.delete_credential(name) FROM aidb.credentials
WHERE name IN (current_schema() || '.my_embedder', current_schema() || '.my_chat');

SELECT aidb.create_model(
    'my_embedder',
    'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-..."}'::jsonb
);

SELECT aidb.create_model(
    'my_chat',
    'openai_completions',
    config      => aidb.completions_config(model => 'gpt-4o'),
    credentials => '{"api_key": "sk-..."}'::jsonb
);

-- Recipe 3: Register a local OpenAI-compatible completions server
-- requires: external service (OpenAI-compatible server on localhost:8000)
-- url is the full endpoint, and aidb appends no path. Plain http:// is refused
-- unless a superuser turns on aidb.allow_insecure_egress, and then only for
-- localhost or private and loopback addresses
SELECT aidb.delete_model(name) FROM aidb.models WHERE name = 'local_llm';

SET aidb.allow_insecure_egress = on;  -- superuser only

SELECT aidb.create_model(
    'local_llm',
    'completions',
    config => aidb.completions_config(
        model => 'mistral-7b-instruct',
        url   => 'http://localhost:8000/v1/chat/completions'
    )
);

RESET aidb.allow_insecure_egress;

-- Recipe 4: Standalone AI functions
-- Model functions take the model name first. Embeddings come back as real[]
SELECT aidb.encode_text('dummy', 'Hello, world!');

SELECT * FROM aidb.encode_text_batch('dummy', ARRAY['first text', 'second text']);

-- Standalone functions take json options, and *_config() helpers return jsonb,
-- so cast them with ::json
SELECT part_id, chunk
FROM aidb.chunk_text(
    'Long document text. It has several sentences. Each chunk stays short.',
    aidb.chunk_text_config(desired_length => 30, max_length => 50)::json
);

-- generate_text replaces the deprecated decode_text
SELECT aidb.generate_text('dummy', 'Summarize: The quick brown fox');

SELECT aidb.generate_text(
    'dummy',
    'Summarize: The quick brown fox',
    aidb.inference_config(temperature => 0.2, max_tokens => 100)::json
);

-- Recipe 5: Rerank candidates
-- requires: external service (NVIDIA NIM reranking API key)
-- The dummy provider does not rerank, so this needs a reranking model
SELECT aidb.delete_model(name) FROM aidb.models WHERE name = 'my_reranker';
SELECT aidb.delete_credential(name) FROM aidb.credentials WHERE name = current_schema() || '.my_reranker';

SELECT aidb.create_model(
    'my_reranker',
    'nim_reranking',
    credentials => '{"api_key": "nvapi-..."}'::jsonb
);

SELECT id, text, logit_score
FROM aidb.rerank_text('my_reranker', 'search query', ARRAY['candidate 1', 'candidate 2', 'candidate 3'])
ORDER BY logit_score DESC;

-- Recipe 6: Document search with a ChunkText + KnowledgeBase pipeline
-- delete_pipeline also drops the destination table once no pipeline uses it
-- create_pipeline refuses an existing destination table, so drop it too
SELECT aidb.delete_pipeline(name) FROM aidb.pipelines WHERE name = 'doc_embeddings';
DROP TABLE IF EXISTS public.pipeline_doc_embeddings;
DROP TABLE IF EXISTS public.my_documents;

CREATE TABLE public.my_documents (
    id      INTEGER PRIMARY KEY,
    content TEXT NOT NULL
);
INSERT INTO public.my_documents VALUES
    (1, 'Refunds are issued within 30 days of purchase.'),
    (2, 'Shipping is free on orders over 50 dollars.');

-- auto_processing: 'Live' processes rows as they change, 'Background' lets a
-- worker process them, 'Disabled' waits for run_pipeline. step_N_options are
-- jsonb, so helpers go in without a cast. Swap 'dummy' for a real embedding
-- model such as my_embedder from Recipe 2
SELECT * FROM aidb.create_pipeline(
    name               => 'doc_embeddings',
    source             => 'public.my_documents',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Disabled',
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2             => 'KnowledgeBase',
    step_2_options     => aidb.knowledge_base_config(
        model             => 'dummy',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

SELECT aidb.run_pipeline('doc_embeddings');

-- Search by the schema-qualified destination table
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_doc_embeddings', 'refund policy', topk => 5);

-- Or query the destination table with pgvector. Embed the query once, and
-- cast real[] to vector for the distance operator
WITH q AS (
    SELECT aidb.encode_text_query('dummy', 'refund policy')::vector AS v
)
SELECT d.source_id, d.part_ids, d.value <=> q.v AS distance
FROM pipeline_doc_embeddings d, q
ORDER BY d.value <=> q.v
LIMIT 10;

-- Inspect the pipeline
SELECT name, source, destination, auto_processing FROM aidb.pipelines WHERE name = 'doc_embeddings';
SELECT name, status, count_source, count_destination, count_errors_record
FROM aidb.get_pipeline_metrics('doc_embeddings');

-- Recipe 7: PDF to image to OCR pipeline
-- requires: external service (NVIDIA NIM OCR API key)
-- validate => false registers the model without calling it, so the pipeline
-- can be created offline, and run_pipeline calls the OCR service
SELECT aidb.delete_pipeline(name) FROM aidb.pipelines WHERE name = 'pdf_ocr';
DROP TABLE IF EXISTS pipeline_pdf_ocr;
DROP TABLE IF EXISTS pdf_files;
SELECT aidb.delete_model(name) FROM aidb.models WHERE name = 'my_ocr_model';
SELECT aidb.delete_credential(name) FROM aidb.credentials WHERE name = current_schema() || '.my_ocr_model';

SELECT aidb.create_model(
    'my_ocr_model',
    'nim_paddle_ocr',
    credentials => '{"api_key": "nvapi-..."}'::jsonb,
    validate    => false
);

CREATE TABLE pdf_files (id INTEGER PRIMARY KEY, content BYTEA NOT NULL);
-- Load real PDFs here, for example with pg_read_binary_file()

SELECT * FROM aidb.create_pipeline(
    name               => 'pdf_ocr',
    source             => 'pdf_files',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Disabled',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::jsonb,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('my_ocr_model')
);

SELECT aidb.run_pipeline('pdf_ocr');

-- Recipe 8: Semantic knowledge base for schema discovery
-- A semantic KB indexes schema metadata (tables, views, columns, comments) so
-- you can find objects by meaning. It does not index document contents. Use
-- a KnowledgeBase pipeline (Recipe 6) for that. Swap 'dummy' for a real
-- embedding model to get meaningful similarity scores
SELECT aidb.delete_semantic_kb(name) FROM aidb.list_semantic_kbs() WHERE name = 'schema_kb';

SELECT aidb.create_semantic_kb(
    name            => 'schema_kb',
    model           => 'dummy',
    schemas         => ARRAY['aidb_cookbook'],
    auto_processing => 'Live'  -- re-embeds when the schema changes
);

-- Hybrid search across tables, columns and other sources
SELECT entity_type, schema_name, relation_name, column_name, round(score::numeric, 3) AS score
FROM aidb.semantic_kb_search('customer orders', kb_name => 'schema_kb', top_k => 10);

-- Tables and views by similarity. The fourth argument is entity_types, so
-- pass top_k by name
SELECT schema_name, relation_name, entity_type, round(similarity::numeric, 3) AS similarity
FROM aidb.get_entity_definitions('schema_kb', 'customer orders', top_k => 10)
ORDER BY similarity DESC;

-- Columns by similarity
SELECT schema_name, relation_name, column_name, round(similarity::numeric, 3) AS similarity
FROM aidb.get_metadata('schema_kb', 'email address', top_k => 50)
WHERE entity_type = 'Column'
ORDER BY similarity DESC
LIMIT 10;

-- Refresh after schema changes when auto_processing is 'Disabled'
SELECT aidb.refresh_semantic_kb('schema_kb');

-- Recipe 9: Semantic aliases (named, parameterized queries)
-- Uses schema_kb from Recipe 8, which supplies the embedding model
-- Arguments are bound as text, so cast each ${placeholder} to its type
SELECT aidb.delete_semantic_alias(name) FROM aidb.get_semantic_aliases() WHERE name = 'top_customers';

SELECT aidb.create_semantic_alias(
    name        => 'top_customers',
    description => 'Rank customers by total lifetime spending',
    query_text  => 'SELECT customer_id, SUM(total) AS ltv FROM aidb_cookbook.orders GROUP BY customer_id ORDER BY ltv DESC LIMIT ${n}::int',
    params      => aidb.alias_params(
        aidb.alias_param('n', 'integer', 'Number of customers to return')
    ),
    kb_name     => 'schema_kb'
);

SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 10}'::jsonb);

-- Find aliases by meaning. query_text comes first and kb_name last
SELECT name, query_text, similarity
FROM aidb.search_semantic_aliases('customer lifetime value', top_k => 5, kb_name => 'schema_kb');

-- Recipe 10: SQL tool and agent
-- The agent uses dummy so the recipe runs anywhere. For real answers swap in
-- a chat model such as my_chat from Recipe 2. Agent functions never raise:
-- check the error column
-- Tool arguments are bound as text, so cast each ${placeholder} to its type
-- delete_agent refuses an agent with conversation history unless force => true
SELECT aidb.delete_agent(name, force => true) FROM aidb.agents WHERE name IN ('my_assistant', 'governed_agent');
SELECT aidb.delete_tool(name) FROM aidb.tools WHERE name = 'list_recent_orders';

SELECT aidb.create_sql_tool(
    'list_recent_orders',
    'List the most recent orders for a given customer',
    'SELECT id, total, created_at FROM aidb_cookbook.orders WHERE customer_id = ${customer_id}::int ORDER BY created_at DESC LIMIT 5',
    aidb.tool_params(aidb.tool_param('customer_id', 'integer', 'The customer ID')),
    read_only => true
);

SELECT * FROM aidb.create_agent(
    'my_assistant',
    'You are a helpful database assistant. Use the available tools to answer questions.',
    'dummy',
    tools => ARRAY['list_recent_orders']
);

SELECT name, model, tools, purpose FROM aidb.agents WHERE name = 'my_assistant';

SELECT message, conversation_id, error
FROM aidb.agent_converse('my_assistant', 'What are the recent orders for customer 42?');

-- Recipe 11: Governance with a purpose
-- A purpose binds an agent to a database role. create_purpose and
-- delete_purpose need superuser or the aidb_governance role. The user who
-- converses with the agent must be able to SET ROLE to the purpose's role
SELECT aidb.delete_agent(name, force => true) FROM aidb.agents WHERE name = 'governed_agent';
-- delete_purpose is a soft delete, and create_purpose revives a deleted name
SELECT aidb.delete_purpose(name) FROM aidb.purpose_registry
WHERE name = 'analyst_purpose' AND deleted_at IS NULL;

-- Roles are cluster-wide, so another database may already have created it
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_role') THEN
        CREATE ROLE analyst_role NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA aidb_cookbook TO analyst_role;
GRANT SELECT ON ALL TABLES IN SCHEMA aidb_cookbook TO analyst_role;
-- On PostgreSQL 16 and later the grant includes SET by default
GRANT analyst_role TO CURRENT_USER;

SELECT aidb.create_purpose('analyst_purpose', 'analyst_role', 'Read-only access for analyst agents');

SELECT * FROM aidb.create_agent(
    'governed_agent',
    'You are a read-only analytics assistant.',
    'dummy',
    tools   => ARRAY['list_recent_orders'],
    purpose => 'analyst_purpose'
);

SELECT name, role, description FROM aidb.purpose_registry WHERE name = 'analyst_purpose';

-- Recipe 12: Import tools from an MCP server
-- requires: external service (MCP server at your URL)
-- delete_tool also removes a registered MCP server and its cached tools
SELECT aidb.delete_tool(name) FROM aidb.mcp_registry WHERE name = 'weather_service';

SELECT aidb.import_mcp_tools(
    'weather_service',
    'https://weather-mcp.example.com/mcp',
    transport   => 'streamable_http',
    tool_filter => ARRAY['get_current_weather', 'get_forecast']
);

-- Re-fetch the tool list when the remote server changes
SELECT aidb.refresh_mcp_tools('weather_service');

-- Recipe 13: Read pipeline errors
-- Record errors go to <source_schema>.pipeline_<name>_errors. Read them with
-- the error log functions. This pipeline gets a file that is not a PDF, so
-- PdfToImage logs a record error and the run continues
SELECT aidb.delete_pipeline(name) FROM aidb.pipelines WHERE name = 'pdf_pages';
DROP TABLE IF EXISTS pipeline_pdf_pages;
DROP TABLE IF EXISTS bad_pdfs;

CREATE TABLE bad_pdfs (id INTEGER PRIMARY KEY, content BYTEA NOT NULL);
INSERT INTO bad_pdfs VALUES (1, 'not a pdf'::bytea);

SELECT * FROM aidb.create_pipeline(
    name               => 'pdf_pages',
    source             => 'bad_pdfs',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Disabled',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi": 150, "format": {"type": "png"}}'::jsonb
);

SELECT aidb.run_pipeline('pdf_pages');

-- Newest first
SELECT source_id, pipeline_step, step_operation, error_category, error_message, last_seen_at
FROM aidb.get_error_logs('pdf_pages', p_limit => 20);

SELECT * FROM aidb.get_error_log_summary('pdf_pages');

-- Error counts for every pipeline
SELECT * FROM aidb.get_all_error_summaries();
