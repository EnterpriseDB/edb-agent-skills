-- =============================================================================
-- AIDB SQL Cookbook
-- Ready-to-use SQL patterns for common AIDB tasks.
-- Adapt variable names (marked with <angle_brackets>) before running.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1: Extension Setup
-- ---------------------------------------------------------------------------

-- Install AIDB (run once per database)
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;

-- Check version
SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';


-- ---------------------------------------------------------------------------
-- SECTION 2: Model Registration
-- ---------------------------------------------------------------------------

-- 2a. Local BERT embeddings (no API key)
SELECT aidb.create_model(
    '<model_name>',
    'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB
);

-- 2b. OpenAI embeddings
SELECT aidb.create_model(
    '<model_name>',
    'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => '<YOUR_OPENAI_KEY>'
    )
);

-- 2c. Generic OpenAI-compatible endpoint (e.g., vLLM, Ollama)
SELECT aidb.create_model(
    '<model_name>',
    'embeddings',
    config => aidb.embeddings_config(
        model => '<model_id>',
        url   => 'http://<host>:<port>/v1/embeddings'
    )
);

-- 2d. OpenAI completions (for agents / summarization)
SELECT aidb.create_model(
    '<llm_name>',
    'openai_completions',
    config => aidb.completions_config(
        model   => 'gpt-4o',
        api_key => '<YOUR_OPENAI_KEY>'
    )
);

-- 2e. NVIDIA NIM OCR (required for PerformOcr step)
SELECT aidb.create_model(
    '<ocr_model_name>',
    'nim_paddle_ocr',
    credentials => '{"api_key": "<YOUR_NIM_KEY>"}'::JSONB
);

-- 2f. Dummy model (for testing — no external service)
SELECT aidb.create_model('<test_model>', 'dummy');

-- 2g. List all models (credentials NOT returned)
SELECT * FROM aidb.list_models();

-- 2h. Delete a model
SELECT aidb.delete_model('<model_name>');


-- ---------------------------------------------------------------------------
-- SECTION 3: Pipelines
-- ---------------------------------------------------------------------------

-- 3a. Simple text-to-embedding pipeline
SELECT aidb.create_pipeline(
    name                => '<pipeline_name>',       -- max 46 chars
    source              => '<source_table>',
    source_key_column   => 'id',
    source_data_column  => 'body',
    auto_processing     => 'Background',            -- 'Live' | 'Background' | 'Disabled'
    step_1              => 'KnowledgeBase',
    step_1_options      => aidb.knowledge_base_config(
        model             => '<embed_model>',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- 3b. PDF → chunks → embeddings pipeline
SELECT aidb.create_pipeline(
    name                => '<pipeline_name>',
    source              => '<source_table>',
    source_key_column   => 'id',
    source_data_column  => 'pdf_bytes',             -- BYTEA column
    auto_processing     => 'Disabled',
    step_1              => 'ParsePdf',
    step_1_options      => aidb.pdf_parse_config(method => 'Structured'),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model             => '<embed_model>',
        data_format       => 'Text',
        distance_operator => 'Cosine'
    )
);

-- 3c. HTML → text pipeline
SELECT aidb.create_pipeline(
    name                => '<pipeline_name>',
    source              => '<source_table>',
    source_key_column   => 'id',
    source_data_column  => 'html_bytes',
    auto_processing     => 'Background',
    step_1              => 'ParseHtml',
    step_1_options      => aidb.html_parse_config(method => 'StructuredMarkdown'),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(desired_length => 512),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model       => '<embed_model>',
        data_format => 'Text'
    )
);

-- 3d. PDF → image → OCR pipeline
SELECT aidb.create_pipeline(
    name                => '<pipeline_name>',
    source              => '<source_table>',
    source_key_column   => 'id',
    source_data_column  => 'pdf_bytes',
    auto_processing     => 'Disabled',
    step_1              => 'PdfToImage',
    step_1_options      => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB,
    step_2              => 'PerformOcr',
    step_2_options      => aidb.ocr_config('<ocr_model_name>')
);

-- 3e. Summarization pipeline
SELECT aidb.create_pipeline(
    name                => '<pipeline_name>',
    source              => '<source_table>',
    source_key_column   => 'id',
    source_data_column  => 'content',
    auto_processing     => 'Background',
    step_1              => 'SummarizeText',
    step_1_options      => aidb.summarize_text_config(
        model    => '<llm_name>',
        strategy => 'reduce',
        reduction_factor => 4
    )
);

-- 3f. Run a pipeline manually
SELECT aidb.run_pipeline('<pipeline_name>');

-- Force re-process all rows (even previously processed)
SELECT aidb.run_pipeline('<pipeline_name>', force_sync => TRUE);

-- 3g. Update pipeline mode without recreating
SELECT aidb.update_pipeline(
    name            => '<pipeline_name>',
    auto_processing => 'Background'
);

-- 3h. List all pipelines
SELECT * FROM aidb.list_pipelines();

-- 3i. Delete a pipeline
-- cascade => TRUE also drops the destination table
SELECT aidb.delete_pipeline('<pipeline_name>', cascade => FALSE);


-- ---------------------------------------------------------------------------
-- SECTION 4: Similarity Search (after KnowledgeBase pipeline runs)
-- ---------------------------------------------------------------------------

-- 4a. Cosine similarity search
SELECT source_id, embedding <=> aidb.encode_text_query('<query>', '<embed_model>') AS distance
FROM <destination_table>
ORDER BY distance
LIMIT 10;

-- 4b. L2 distance search
SELECT source_id, embedding <-> aidb.encode_text_query('<query>', '<embed_model>') AS distance
FROM <destination_table>
ORDER BY distance
LIMIT 10;

-- 4c. Reranking results
SELECT index, score
FROM aidb.rerank_text(
    '<query>',
    ARRAY['<candidate1>', '<candidate2>', '<candidate3>'],
    '<rerank_model>'
)
ORDER BY score DESC;


-- ---------------------------------------------------------------------------
-- SECTION 5: Standalone AI Functions
-- ---------------------------------------------------------------------------

-- 5a. Compute embedding for a single text
SELECT aidb.encode_text('Hello, world!', '<embed_model>');

-- 5b. Batch embeddings
SELECT aidb.encode_text_batch(ARRAY['text1', 'text2', 'text3'], '<embed_model>');

-- 5c. Query-side embedding (for asymmetric bi-encoders)
SELECT aidb.encode_text_query('my search query', '<embed_model>');

-- 5d. Chunk text
SELECT part_id, value
FROM aidb.chunk_text(
    'Long text to chunk...',
    aidb.chunk_text_config(desired_length => 256, overlap_length => 32)
);

-- 5e. Summarize text
SELECT aidb.summarize_text('Long document...', aidb.summarize_text_config(model => '<llm>'));

-- 5f. Parse PDF to text
SELECT aidb.parse_pdf(pg_read_binary_file('/path/to/doc.pdf'), aidb.pdf_parse_config(method => 'Structured'));

-- 5g. Parse HTML to text
SELECT aidb.parse_html(convert_to('<html>...</html>', 'UTF8'), aidb.html_parse_config());

-- 5h. OCR an image
SELECT aidb.perform_ocr(pg_read_binary_file('/path/to/image.png'), '<ocr_model>');


-- ---------------------------------------------------------------------------
-- SECTION 6: Semantic Knowledge Base
-- ---------------------------------------------------------------------------

-- 6a. Create a semantic KB over multiple schemas
SELECT aidb.create_semantic_kb(
    '<kb_name>',
    '<embed_model>',
    ARRAY['public', 'analytics'],   -- schemas to index
    'Live',                          -- auto_processing
    false,                           -- bypass_triggers
    aidb.vector_index_hnsw_config()  -- optional vector index
);

-- 6b. Manually refresh KB index
SELECT aidb.refresh_semantic_kb('<kb_name>');

-- 6c. Search for tables
SELECT * FROM aidb.get_tables('<kb_name>', 'customer orders', min_similarity => 0.7, limit => 5, offset => 0);

-- 6d. Search for columns
SELECT * FROM aidb.get_columns('<kb_name>', 'email address', min_similarity => 0.7, limit => 5, offset => 0);

-- 6e. Full metadata search
SELECT * FROM aidb.get_metadata('<kb_name>', 'purchase history', min_similarity => 0.7, limit => 10, offset => 0);

-- 6f. Combined search (schema + aliases, v7.5.0+)
SELECT * FROM aidb.semantic_kb_search('customers with highest spend', '<kb_name>', top_k => 10);

-- 6g. KB stats
SELECT * FROM aidb.semantic_kb_stats('<kb_name>');


-- ---------------------------------------------------------------------------
-- SECTION 7: Semantic Aliases
-- ---------------------------------------------------------------------------

-- 7a. Create a semantic alias
SELECT aidb.create_semantic_alias(
    '<alias_name>',
    'Natural language description of what this query does',
    'SELECT * FROM <table> WHERE status = ${status} LIMIT ${n}',
    aidb.alias_params(
        aidb.alias_param('status', 'text', 'Filter by status', ARRAY['active', 'inactive']),
        aidb.alias_param('n', 'integer', 'Maximum results to return')
    ),
    '<kb_or_embed_model>'
);

-- 7b. Execute a semantic alias
SELECT aidb.execute_semantic_alias(
    '<alias_name>',
    '{"status": "active", "n": 10}'::JSONB
);

-- 7c. Search for relevant aliases
SELECT * FROM aidb.search_semantic_aliases(
    '<embed_model>',
    'show active customers',
    min_similarity => 0.7,
    limit => 5,
    offset => 0
);

-- 7d. List all aliases
SELECT * FROM aidb.get_semantic_aliases();


-- ---------------------------------------------------------------------------
-- SECTION 8: Agent Hub
-- ---------------------------------------------------------------------------

-- 8a. Create an agent
SELECT error FROM aidb.create_agent(
    '<agent_name>',
    'You are a helpful database assistant. Answer questions about the database schema.',
    '<llm_model>',
    role => NULL,        -- optional PostgreSQL role for tool execution
    tools => ARRAY['list_native_tools', 'catalog_list_schemas'],  -- specific tools
    max_iterations => 10,
    budget_strategy => 'attempt_complete'
);

-- 8b. Simple one-shot conversation
SELECT message, error, conversation_id
FROM aidb.agent_converse('<agent_name>', 'What tables exist in the public schema?');

-- 8c. Multi-turn conversation
SELECT conversation_id FROM aidb.start_agent_session('<agent_name>') \gset

SELECT message, error
FROM aidb.agent_converse(
    '<agent_name>',
    'Tell me about the orders table',
    conversation_id => :'conversation_id'
);

SELECT message, error
FROM aidb.agent_converse(
    '<agent_name>',
    'How many orders were placed last month?',
    conversation_id => :'conversation_id'
);

-- 8d. View conversation history
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- 8e. Delete an agent
SELECT aidb.delete_agent('<agent_name>', force => FALSE);


-- ---------------------------------------------------------------------------
-- SECTION 9: Tools Hub
-- ---------------------------------------------------------------------------

-- 9a. Create a SQL tool
SELECT aidb.create_sql_tool(
    '<tool_name>',
    'List orders for a specific customer',
    'SELECT id, total, status FROM orders WHERE customer_id = ${customer_id} LIMIT ${n}',
    aidb.params(
        aidb.param('customer_id', 'integer', 'Customer identifier'),
        aidb.param('n', 'integer', 'Maximum number of orders')
    ),
    read_only => true
);

-- 9b. Run a tool directly
SELECT aidb.run_tool('<tool_name>', '{"customer_id": 42, "n": 5}'::JSONB);

-- 9c. Import MCP server tools
SELECT aidb.import_mcp_tools(
    '<server_name>',
    'http://<mcp-server-host>:<port>',
    'streamable_http'
);

-- 9d. List all available tools
SELECT * FROM aidb.tools;

-- 9e. Delete a tool
SELECT aidb.delete_tool('<tool_name>');


-- ---------------------------------------------------------------------------
-- SECTION 10: Volume Management (Object Storage)
-- ---------------------------------------------------------------------------

-- 10a. Create a volume
SELECT aidb.create_volume(
    '<volume_name>',
    's3',                           -- storage_location
    's3://<bucket>/<prefix>/',      -- path
    'Bytes'                         -- 'Text' | 'Bytes' | 'Image'
);

-- 10b. List volumes
SELECT * FROM aidb.list_volumes();

-- 10c. List files in a volume
SELECT * FROM aidb.list_volume_content('<volume_name>');

-- 10d. Read a file from a volume
SELECT aidb.read_volume_file('<volume_name>', 'path/to/file.pdf');

-- 10e. Delete a volume
SELECT aidb.delete_volume('<volume_name>');


-- ---------------------------------------------------------------------------
-- SECTION 11: Configuration & Monitoring
-- ---------------------------------------------------------------------------

-- 11a. Check AIDB GUC settings
SHOW aidb.max_threads;
SHOW aidb.pipeline_error_warnings;

-- 11b. Set GUCs (session-level where possible)
SET aidb.pipeline_error_warnings = false;

-- 11c. For max_threads (requires restart):
ALTER SYSTEM SET aidb.max_threads = 8;
-- Then restart PostgreSQL

-- 11d. Get model embedding dimensions
SELECT aidb.get_adapter_embedding_dimensions('<model_name>');

-- 11e. Remove a cached model (forces reload on next use)
SELECT aidb.remove_cached_model('<model_name>');
