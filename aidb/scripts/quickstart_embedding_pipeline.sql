-- quickstart_embedding_pipeline.sql
-- End-to-end example: register a model, create a source table, run a chunking +
-- embedding pipeline, and perform a similarity search.
--
-- Replace 'dummy' with your actual provider (e.g., 'openai_embeddings') and
-- supply real credentials to get real embeddings.
--
-- Usage:
--   psql -d <your_database> -f quickstart_embedding_pipeline.sql

\echo '=== Quickstart: Embedding Pipeline ==='

-- Step 1: Install the extension (if not already installed)
-- CREATE EXTENSION IF NOT EXISTS aidb CASCADE;

-- Step 2: Register an embedding model (using 'dummy' for testing)
SELECT aidb.create_model(
    'my_embed_model',
    'dummy'  -- Replace with 'openai_embeddings' or another provider for real embeddings
    -- For OpenAI, use:
    -- config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...')
) AS model_created;

-- Step 3: Create a source table with text content
DROP TABLE IF EXISTS my_documents;
CREATE TABLE my_documents (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL
);

INSERT INTO my_documents (content) VALUES
    ('PostgreSQL is a powerful open-source relational database system.'),
    ('AIDB brings AI capabilities natively into PostgreSQL via SQL.'),
    ('Vector embeddings enable semantic similarity search over text data.'),
    ('EDB Postgres AI is a Sovereign AI platform for enterprise databases.'),
    ('Pipelines transform data through AI steps like chunking and embedding.');

-- Step 4: Create a pipeline (ChunkText -> (destination table with embeddings))
-- NOTE: For embedding pipelines, use standalone encode_text() calls or the
-- deprecated KnowledgeBase step. For new embedding work, prefer standalone functions.
-- This example shows standalone batch embedding:

DROP TABLE IF EXISTS my_documents_embeddings;
CREATE TABLE my_documents_embeddings (
    source_id INT REFERENCES my_documents(id),
    chunk_id  INT,
    chunk_text TEXT,
    embedding VECTOR  -- requires pgvector extension
);

-- Embed documents using standalone function
INSERT INTO my_documents_embeddings (source_id, chunk_id, chunk_text, embedding)
SELECT
    d.id AS source_id,
    c.part_id AS chunk_id,
    c.value AS chunk_text,
    aidb.encode_text(c.value, 'my_embed_model') AS embedding
FROM my_documents d,
     aidb.chunk_text(d.content, aidb.chunk_text_config(desired_length => 200)) c;

\echo 'Embeddings created. Row count:'
SELECT COUNT(*) FROM my_documents_embeddings;

-- Step 5: Perform similarity search (requires pgvector)
\echo ''
\echo 'Similarity search for "AI capabilities in databases":'
SELECT
    e.source_id,
    e.chunk_text,
    e.embedding <=> aidb.encode_text_query('AI capabilities in databases', 'my_embed_model') AS distance
FROM my_documents_embeddings e
ORDER BY distance
LIMIT 3;

\echo '=== Quickstart Complete ==='
