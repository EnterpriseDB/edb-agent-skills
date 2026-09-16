-- setup_embedding_pipeline.sql
-- Template: End-to-end text embedding pipeline
-- Replace <TABLE>, <COL>, <KEY_COL>, <MODEL_NAME>, <PROVIDER>, <MODEL_CONFIG>
-- with your actual values before running.

-- Step 1: Register your embedding model (skip if already registered)
SELECT aidb.create_model(
    '<MODEL_NAME>',           -- e.g. 'my_embed'
    '<PROVIDER>',             -- e.g. 'openai_embeddings', 'bert_local', 'dummy'
    config => '<MODEL_CONFIG>'::JSONB  -- e.g. aidb.embeddings_config(model=>'text-embedding-3-small', api_key=>'sk-...')
);

-- Step 2: Create the pipeline
-- Source table must exist. Destination table must NOT exist yet.
SELECT aidb.create_pipeline(
    name                => 'my_embed_pipeline',
    source              => '<TABLE>',            -- e.g. 'public.documents'
    source_data_column  => '<COL>',              -- e.g. 'body'
    source_key_column   => '<KEY_COL>',          -- e.g. 'id'
    auto_processing     => 'Background',         -- 'Live' | 'Background' | 'Disabled'
    step_1              => 'KnowledgeBase',
    step_1_options      => aidb.knowledge_base_config(
        model             => '<MODEL_NAME>',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- Step 3: Trigger initial processing (for existing rows)
SELECT aidb.run_pipeline('my_embed_pipeline');

-- Step 4: Similarity search
-- SELECT source_id
-- FROM my_embed_pipeline_destination_table   -- adjust table name to actual destination
-- ORDER BY embedding <=> aidb.encode_text_query('your search query', '<MODEL_NAME>')
-- LIMIT 10;

-- Cleanup (if needed):
-- SELECT aidb.delete_pipeline('my_embed_pipeline', cascade => true);
