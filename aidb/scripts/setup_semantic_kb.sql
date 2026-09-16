-- setup_semantic_kb.sql
-- Template: Create a Semantic Knowledge Base for natural-language schema search
-- Replace <KB_NAME>, <MODEL_NAME>, and <SCHEMAS> with your values.

-- Step 1: Register embedding model (skip if already registered)
SELECT aidb.create_model(
    '<MODEL_NAME>',    -- e.g. 'my_embed'
    '<PROVIDER>',      -- e.g. 'openai_embeddings', 'bert_local', 'dummy'
    config => '<CONFIG>'::JSONB
);

-- Step 2: Create the semantic KB
SELECT aidb.create_semantic_kb(
    name            => '<KB_NAME>',       -- e.g. 'schema_kb'
    model           => '<MODEL_NAME>',
    schemas         => ARRAY['<SCHEMAS>'],  -- e.g. ARRAY['public', 'app']
    auto_processing => 'Live',
    bypass_triggers => false,
    vector_index    => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
);

-- Step 3: Refresh if needed
-- SELECT aidb.refresh_semantic_kb('<KB_NAME>');

-- Step 4: Semantic search examples
-- Find tables matching a natural language query
SELECT schema_name, relation_name, similarity
FROM aidb.get_tables('<KB_NAME>', 'customer order history', 0.5, 10, 0)
ORDER BY similarity DESC;

-- Find columns matching a concept
SELECT schema_name, relation_name, column_name, similarity
FROM aidb.get_columns('<KB_NAME>', 'email address contact', 0.6, 10, 0)
ORDER BY similarity DESC;

-- Combined schema + alias search
SELECT source_type, entity_type, relation_name, column_name, object_ref, score
FROM aidb.semantic_kb_search('which tables track customer spending', '<KB_NAME>', 10)
ORDER BY score DESC;

-- Cleanup:
-- SELECT aidb.delete_semantic_kb('<KB_NAME>');
