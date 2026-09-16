# AIDB Example SQL Patterns

Curated, runnable SQL snippets for the most common AIDB tasks. Copy and adapt to your environment.

---

## 1. Install Extension

```sql
-- Requires 'aidb' in shared_preload_libraries and a PostgreSQL restart first.
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;
```

---

## 2. Register Models

```sql
-- OpenAI text embeddings
SELECT aidb.create_model(
    'openai_embed',
    'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => 'sk-...'
    )
);

-- OpenAI completions
SELECT aidb.create_model(
    'openai_gpt4o',
    'openai_completions',
    config => aidb.completions_config(
        model   => 'gpt-4o',
        api_key => 'sk-...'
    )
);

-- Local BERT embeddings
SELECT aidb.create_model(
    'local_bert',
    'bert_local',
    config => '{"model": "/models/bert-base-uncased"}'::JSONB
);

-- Self-hosted vLLM (OpenAI-compatible)
SELECT aidb.create_model(
    'vllm_mistral',
    'completions',
    config => aidb.completions_config(
        model => 'mistral-7b-instruct',
        url   => 'http://vllm:8000/v1/chat/completions'
    )
);

-- Test/dummy model (no external service needed)
SELECT aidb.create_model('dummy_model', 'dummy');

-- List all models:
SELECT * FROM aidb.list_models();
```

---

## 3. Text Embedding Pipeline

```sql
-- Source table setup
CREATE TABLE articles (
    id      SERIAL PRIMARY KEY,
    title   TEXT,
    body    TEXT
);

INSERT INTO articles (title, body) VALUES
    ('Postgres Performance', 'Vacuuming and indexing are key to performance...'),
    ('AIDB Overview', 'AIDB brings AI capabilities natively into Postgres...');

-- Create pipeline: ChunkText → KnowledgeBase
SELECT aidb.create_pipeline(
    name                => 'article_embeddings',
    source              => 'articles',
    source_key_column   => 'id',
    source_data_column  => 'body',
    auto_processing     => 'Background',   -- or 'Live', 'Disabled'
    step_1              => 'ChunkText',
    step_1_options      => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2              => 'KnowledgeBase',
    step_2_options      => aidb.knowledge_base_config(
        model             => 'openai_embed',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- Manually trigger (for Disabled mode or initial load):
SELECT aidb.run_pipeline('article_embeddings');

-- Similarity search:
SELECT source_id, part_ids,
       embedding <=> aidb.encode_text_query('how to improve Postgres speed', 'openai_embed') AS distance
FROM pipeline_article_embeddings
ORDER BY embedding <=> aidb.encode_text_query('how to improve Postgres speed', 'openai_embed')
LIMIT 5;
```

---

## 4. PDF Processing Pipeline

```sql
-- PDF text extraction + embedding
CREATE TABLE documents (
    id       SERIAL PRIMARY KEY,
    filename TEXT,
    content  BYTEA   -- raw PDF bytes
);

SELECT aidb.create_pipeline(
    name                => 'doc_rag',
    source              => 'documents',
    source_key_column   => 'id',
    source_data_column  => 'content',
    auto_processing     => 'Disabled',
    step_1              => 'ParsePdf',
    step_1_options      => aidb.pdf_parse_config(method => 'Structured'),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model             => 'openai_embed',
        data_format       => 'Text',
        distance_operator => 'Cosine'
    )
);

-- PDF OCR pipeline (for scanned PDFs)
SELECT aidb.create_model('nim_ocr', 'nim_paddle_ocr',
    credentials => '{"api_key": "nvapi-..."}'::JSONB);

SELECT aidb.create_pipeline(
    name                => 'pdf_ocr',
    source              => 'documents',
    source_key_column   => 'id',
    source_data_column  => 'content',
    auto_processing     => 'Disabled',
    step_1              => 'PdfToImage',
    step_1_options      => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB,
    step_2              => 'PerformOcr',
    step_2_options      => aidb.ocr_config('nim_ocr')
);
```

---

## 5. Semantic Knowledge Base

```sql
-- Create a KB over the 'public' schema
SELECT aidb.create_semantic_kb(
    name            => 'main_kb',
    model           => 'openai_embed',
    schemas         => ARRAY['public'],
    auto_processing => 'Live'
);

-- Search for tables related to a concept:
SELECT schema_name, relation_name, entity_type, similarity
FROM aidb.get_tables('main_kb', 'customer purchase history', 0.7, 10, 0);

-- Search for columns:
SELECT relation_name, column_name, definition, similarity
FROM aidb.get_columns('main_kb', 'email address', 0.7, 10, 0);

-- Combined search (tables + columns + aliases):
SELECT source_type, entity_type, relation_name, column_name, score
FROM aidb.semantic_kb_search('which table tracks orders', 'main_kb', 10)
ORDER BY rank;

-- Create a semantic alias (parameterized query):
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by total purchase amount',
    'SELECT customer_id, SUM(total) AS spend FROM orders GROUP BY customer_id ORDER BY spend DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'How many customers to return')),
    'main_kb'
);

-- Execute the alias:
SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 5}'::JSONB);

-- Search for aliases:
SELECT * FROM aidb.search_semantic_aliases('main_kb', 'openai_embed', 'best customers', 0.7, 5, 0);
```

---

## 6. Standalone AI Operations

```sql
-- Compute a single embedding:
SELECT aidb.encode_text('Hello, PostgreSQL!', 'openai_embed');

-- Batch embeddings:
SELECT aidb.encode_text_batch(ARRAY['text one', 'text two', 'text three'], 'openai_embed');

-- Rerank search results:
SELECT index, score
FROM aidb.rerank_text(
    'postgres performance tuning',
    ARRAY['vacuum is important', 'indexing helps queries', 'unrelated content'],
    'my_rerank_model'
)
ORDER BY score DESC;

-- Chunk text:
SELECT part_id, value
FROM aidb.chunk_text('Long document text goes here...', aidb.chunk_text_config(desired_length => 200))
ORDER BY part_id;

-- Summarize text:
SELECT aidb.summarize_text(
    'Very long document...',
    aidb.summarize_text_config(model => 'openai_gpt4o', strategy => 'reduce')
);

-- Parse HTML:
SELECT aidb.parse_html('<html><body><h1>Title</h1><p>Content</p></body></html>'::BYTEA);

-- Parse PDF:
SELECT aidb.parse_pdf(pg_read_binary_file('/path/to/doc.pdf'));
```

---

## 7. Agent Hub

```sql
-- Register an agent-capable model first:
SELECT aidb.create_model(
    'claude',
    'anthropic_messages',
    config => '{"model": "claude-3-5-sonnet-20241022"}'::JSONB,
    credentials => '{"api_key": "sk-ant-..."}'::JSONB
);

-- Create a SQL tool for the agent to use:
SELECT aidb.create_sql_tool(
    'get_top_products',
    'Returns the top N products by revenue',
    'SELECT product_id, name, SUM(revenue) AS total FROM sales GROUP BY product_id, name ORDER BY total DESC LIMIT ${n}',
    aidb.params(aidb.param('n', 'integer', 'How many products')),
    read_only => TRUE
);

-- Create an agent:
SELECT error FROM aidb.create_agent(
    name         => 'sales_analyst',
    instructions => 'You are a sales analytics assistant. Use the available tools to answer questions about sales data.',
    model        => 'claude',
    tools        => ARRAY['get_top_products']
);

-- Converse:
SELECT conversation_id, message, error
FROM aidb.agent_converse(
    'sales_analyst',
    'What were our top 5 products last month?'
);

-- Continue conversation:
SELECT conversation_id, message, error
FROM aidb.agent_converse(
    'sales_analyst',
    'How does this compare to the previous month?',
    conversation_id => '<uuid from above>'
);

-- Inspect conversation history:
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';
```

---

## 8. Pipeline Management Operations

```sql
-- List all pipelines with their status:
SELECT name, auto_processing FROM aidb.list_pipelines();

-- Switch a pipeline to Background mode:
SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Background');

-- View error log:
SELECT * FROM aidb.get_error_logs('my_pipeline') ORDER BY last_seen_at DESC;

-- Re-queue failed items:
SELECT aidb.requeue_pipeline_errors('my_pipeline');

-- Delete a pipeline (and its destination table):
SELECT aidb.delete_pipeline('my_pipeline', cascade => TRUE);
```
