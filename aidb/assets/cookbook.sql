-- AIDB Quick-Start Cookbook
-- Each recipe is self-contained and runnable against a live AIDB install.
-- Prerequisites: CREATE EXTENSION aidb CASCADE; (PostgreSQL 14-18)

-- ============================================================
-- RECIPE 1: Register a model (dummy, for testing)
-- ============================================================
SELECT aidb.create_model('test_model', 'dummy');
-- Verify:
SELECT name, provider FROM aidb.list_models() WHERE name = 'test_model';

-- ============================================================
-- RECIPE 2: Register an OpenAI embedding model
-- ============================================================
SELECT aidb.create_model(
    'my_embedder',
    'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => 'sk-...'
    ),
    validate => true  -- test-connects immediately
);

-- ============================================================
-- RECIPE 3: Register a local llama.cpp completions model
-- ============================================================
SELECT aidb.create_model(
    'local_llm',
    'completions',
    config => aidb.completions_config(
        model => 'mistral-7b-instruct',
        url   => 'http://localhost:8000/v1/chat/completions'
    )
);

-- ============================================================
-- RECIPE 4: Run standalone AI operations
-- ============================================================
-- Encode text to vector
SELECT aidb.encode_text('Hello, world!', 'my_embedder');

-- Chunk text for downstream embedding
SELECT part_id, value
FROM aidb.chunk_text('Long document text...', aidb.chunk_text_config(desired_length => 512, overlap_length => 64));

-- Generate text (preferred over deprecated decode_text)
SELECT aidb.generate_text('Summarize: The quick brown fox', 'local_llm');

-- Rerank candidates
SELECT index, score
FROM aidb.rerank_text('search query', ARRAY['candidate 1', 'candidate 2', 'candidate 3'], 'my_reranker')
ORDER BY score DESC;

-- ============================================================
-- RECIPE 5: Create an embedding pipeline (Background mode)
-- ============================================================
CREATE TABLE my_documents (
    id      SERIAL PRIMARY KEY,
    content TEXT NOT NULL
);

SELECT aidb.create_pipeline(
    name              => 'doc_embeddings',
    source            => 'my_documents',
    source_key_column => 'id',
    source_data_column=> 'content',
    auto_processing   => 'Background',
    step_1            => 'ChunkText',
    step_1_options    => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2            => 'KnowledgeBase',
    step_2_options    => aidb.knowledge_base_config(
        model             => 'my_embedder',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- Insert data; Background worker picks it up automatically
INSERT INTO my_documents (content) VALUES ('Sample document text');

-- Or run manually:
SELECT aidb.run_pipeline('doc_embeddings');

-- Similarity search after pipeline runs:
SELECT source_id
FROM pipeline_doc_embeddings
ORDER BY embedding <=> aidb.encode_text_query('my search query', 'my_embedder')
LIMIT 10;

-- ============================================================
-- RECIPE 6: PDF → Image → OCR pipeline (2 steps)
-- ============================================================
SELECT aidb.create_model(
    'my_ocr_model',
    'nim_paddle_ocr',
    credentials => '{"api_key":"<YOUR_KEY>"}'::JSONB
);

CREATE TABLE pdf_files (id SERIAL PRIMARY KEY, content BYTEA NOT NULL);

SELECT aidb.create_pipeline(
    name               => 'pdf_ocr',
    source             => 'pdf_files',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Disabled',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('my_ocr_model')
);

SELECT aidb.run_pipeline('pdf_ocr');

-- ============================================================
-- RECIPE 7: Semantic Knowledge Base for natural language schema discovery
-- ============================================================
-- Create KB over a schema (Live mode re-embeds on DDL changes via triggers)
SELECT aidb.create_semantic_kb(
    'schema_kb',
    'my_embedder',
    ARRAY['public', 'sales'],
    'Live'
);

-- Search: find tables related to "customer orders"
SELECT schema_name, relation_name, entity_type, round(similarity::numeric, 3) AS score
FROM aidb.get_tables('schema_kb', 'customer orders', 0.7, 10, 0)
ORDER BY similarity DESC;

-- Search: find columns related to "email address"
SELECT schema_name, relation_name, column_name, round(similarity::numeric, 3) AS score
FROM aidb.get_columns('schema_kb', 'email address', 0.7, 10, 0)
ORDER BY similarity DESC;

-- Refresh manually (useful after schema changes in Disabled mode)
SELECT aidb.refresh_semantic_kb('schema_kb');

-- ============================================================
-- RECIPE 8: Semantic Aliases — discoverable parameterized queries
-- ============================================================
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by total lifetime spending',
    'SELECT customer_id, SUM(total) AS ltv FROM orders GROUP BY customer_id ORDER BY ltv DESC LIMIT ${n}',
    aidb.alias_params(
        aidb.alias_param('n', 'integer', 'Number of customers to return')
    ),
    'schema_kb'
);

-- Execute a semantic alias by name
SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 10}'::JSONB);

-- Discover aliases by natural language
SELECT name, description
FROM aidb.search_semantic_aliases('schema_kb', 'customer lifetime value', 0.7, 5, 0);

-- ============================================================
-- RECIPE 9: Create and converse with an Agent
-- ============================================================
SELECT aidb.create_model('agent_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...')
);

-- Register a SQL tool the agent can call
SELECT aidb.create_sql_tool(
    'list_recent_orders',
    'List the most recent orders for a given customer',
    'SELECT id, total, created_at FROM orders WHERE customer_id = ${customer_id} ORDER BY created_at DESC LIMIT 5',
    aidb.tool_params(aidb.tool_param('customer_id', 'integer', 'The customer ID')),
    read_only => true
);

-- Create the agent
SELECT aidb.create_agent(
    'my_assistant',
    'You are a helpful database assistant. Use available tools to answer questions.',
    'agent_llm',
    tool_names => ARRAY['list_recent_orders']
);

-- Converse (never raises; check error column)
SELECT message, error
FROM aidb.agent_converse('my_assistant', 'What are the recent orders for customer 42?');

-- ============================================================
-- RECIPE 10: Governance — Purpose Registry
-- ============================================================
-- Create a purpose that constrains an agent to a read-only role
CREATE ROLE analyst_role LOGIN;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_role;

SELECT aidb.create_purpose(
    'analyst_purpose',
    'analyst_role',
    'Read-only access for analyst agents'
);

-- Create an agent governed by this purpose
SELECT aidb.create_agent(
    'governed_agent',
    'You are a read-only analytics assistant.',
    'agent_llm',
    purpose => 'analyst_purpose'
);

-- ============================================================
-- RECIPE 11: Import external MCP tools
-- ============================================================
SELECT aidb.import_mcp_tools(
    'weather_service',
    'https://weather-mcp.example.com/mcp',
    transport => 'streamable_http',
    tool_filter => ARRAY['get_current_weather', 'get_forecast']
);

-- Refresh tool cache when the remote server updates
SELECT aidb.refresh_mcp_tools('weather_service');

-- ============================================================
-- RECIPE 12: View pipeline error log
-- ============================================================
-- Pipeline errors are stored in a per-pipeline table:
-- SELECT * FROM aidb_internal.pipeline_error_log_<pipeline_id>;
-- Use list_pipelines() to find the pipeline ID:
SELECT name, id FROM aidb.list_pipelines();
