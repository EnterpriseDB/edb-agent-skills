# AIDB Quick-Start Examples

Runnable SQL patterns for common AIDB tasks.

---

## 1. Install the Extension

```sql
CREATE EXTENSION aidb CASCADE;
```

Requires PostgreSQL 14–18. The `CASCADE` flag installs `pgvector` and other dependencies automatically.

---

## 2. Register Models

```sql
-- Test model (no external service needed)
SELECT aidb.create_model('test_model', 'dummy');

-- OpenAI embeddings
SELECT aidb.create_model(
    'my_embedder',
    'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => 'sk-...'
    )
);

-- OpenAI completions
SELECT aidb.create_model(
    'my_llm',
    'openai_completions',
    config => aidb.completions_config(
        model   => 'gpt-4o',
        api_key => 'sk-...'
    )
);

-- Local BERT model
SELECT aidb.create_model(
    'bert_model',
    'bert_local',
    config => '{"model": "/opt/models/bert-base-uncased"}'::jsonb
);

-- Anthropic via env var (no inline credentials)
SELECT aidb.create_model(
    'claude',
    'anthropic_messages',
    credentials_env => 'ANTHROPIC_API_KEY'
);

-- Validate model at registration time
SELECT aidb.create_model(
    'my_llm_validated',
    'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...'),
    validate => true
);
```

---

## 3. Standalone AI Functions

```sql
-- Compute a text embedding
SELECT aidb.encode_text('Hello world', 'my_embedder');

-- Batch embeddings
SELECT aidb.encode_text_batch(ARRAY['text one', 'text two'], 'my_embedder');

-- Generate text (preferred; NOT aidb.decode_text which is deprecated)
SELECT aidb.generate_text('Summarize: PostgreSQL is a powerful open-source database.', 'my_llm');

-- Chunk long text
SELECT * FROM aidb.chunk_text(
    'Very long document text here...',
    aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
);

-- Parse HTML
SELECT aidb.parse_html(convert_to('<html><body><p>Hello</p></body></html>', 'UTF8'));

-- Parse PDF
SELECT aidb.parse_pdf(pg_read_binary_file('/path/to/document.pdf'));

-- OCR an image
SELECT aidb.perform_ocr(pg_read_binary_file('/path/to/image.png'), 'my_nim_ocr_model');

-- Rerank results
SELECT * FROM aidb.rerank_text(
    'database performance tuning',
    ARRAY['PostgreSQL indexing', 'Python tutorial', 'Query optimization'],
    'my_rerank_model'
) ORDER BY score DESC;
```

---

## 4. Text Embedding Pipeline

```sql
-- Source table
CREATE TABLE documents (
    id   SERIAL PRIMARY KEY,
    body TEXT NOT NULL
);

-- Create pipeline (destination table must not exist)
SELECT aidb.create_pipeline(
    name              => 'doc_embeddings',
    source            => 'documents',
    source_key_column => 'id',
    source_data_column => 'body',
    auto_processing   => 'Background',
    step_1            => 'ChunkText',
    step_1_options    => aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
    -- KnowledgeBase step is deprecated; use encode_text for embeddings instead
);

-- Run manually
SELECT aidb.run_pipeline('doc_embeddings');

-- Check pipeline state
SELECT * FROM aidb.list_pipelines();
```

---

## 5. PDF → OCR Pipeline (Two Steps)

```sql
SELECT aidb.create_model(
    'ocr_model',
    'nim_paddle_ocr',
    credentials => '{"api_key": "your-nim-key"}'::jsonb
);

CREATE TABLE pdf_docs (
    id      SERIAL PRIMARY KEY,
    content BYTEA NOT NULL
);

SELECT aidb.create_pipeline(
    name               => 'pdf_ocr',
    source             => 'pdf_docs',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Disabled',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::jsonb,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('ocr_model')
);

SELECT aidb.run_pipeline('pdf_ocr');
```

---

## 6. Semantic Knowledge Base

```sql
-- Register embedding model
SELECT aidb.create_model('kb_embedder', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- Create KB over one or more schemas
SELECT aidb.create_semantic_kb(
    'schema_kb',
    'kb_embedder',
    ARRAY['public', 'sales'],
    auto_processing => 'Live'
);

-- Search for tables semantically
SELECT * FROM aidb.get_tables('schema_kb', 'customer purchase history', 0.7, 5, 0);

-- Search for columns
SELECT * FROM aidb.get_columns('schema_kb', 'email address', 0.75, 10, 0);

-- Broader metadata search
SELECT * FROM aidb.get_metadata('schema_kb', 'order total', 0.6, 10, 0);

-- Composite search (tables + aliases in one ranked list, requires aidb 7.5.0+)
SELECT * FROM aidb.semantic_kb_search('top customers by spend', 'schema_kb', 10);

-- Refresh after schema changes
SELECT aidb.refresh_semantic_kb('schema_kb');
```

---

## 7. Semantic Aliases

```sql
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Return the top N customers by total revenue',
    'SELECT customer_id, name, SUM(total) AS revenue FROM orders
     GROUP BY customer_id, name
     ORDER BY revenue DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'Number of customers to return')),
    'schema_kb'
);

-- Execute directly
SELECT aidb.execute_semantic_alias('top_customers', '{"n": 10}'::jsonb);

-- Or let an agent discover and call it
SELECT * FROM aidb.search_semantic_aliases('kb_embedder', 'best customers', 0.7, 3, 0);
```

---

## 8. Agent Hub

```sql
-- Register a SQL tool
SELECT aidb.create_sql_tool(
    'get_recent_orders',
    'Get the N most recent orders',
    'SELECT id, customer_id, total FROM orders ORDER BY created_at DESC LIMIT ${n}',
    aidb.tool_params(aidb.tool_param('n', 'integer', 'Number of orders')),
    read_only => true
);

-- Create an agent
SELECT error FROM aidb.create_agent(
    'db_assistant',
    'You are a database assistant. Use the available tools to answer questions.',
    'my_llm',
    tools => ARRAY['get_recent_orders', 'catalog_list_schemas', 'catalog_list_objects']
);

-- Converse with the agent
SELECT message, conversation_id, error
FROM aidb.agent_converse('db_assistant', 'Show me the 5 most recent orders.');

-- Delete when done
SELECT aidb.delete_agent('db_assistant');
```

---

## 9. Agent Memory Session

```sql
-- Initialize a memory namespace
SELECT aidb_memory.init('my_ns', 'mock', '{}'::jsonb);

-- Set session GUCs
SET aidb.agent_memory_namespace = 'my_ns';
SET aidb.agent_session_source = 'memory';

-- Start a session
SELECT conversation_id FROM aidb.start_agent_session('db_assistant') \gset session_

-- Converse (memory is captured automatically)
SELECT message FROM aidb.agent_converse(
    'db_assistant',
    'Tell me about our top customers.',
    conversation_id => :'session_conversation_id'
);

-- Read session history
SELECT item->>'kind', item->>'content'
FROM aidb_memory.session_get('my_ns', session_user, :'session_conversation_id') AS s,
     jsonb_array_elements(s.items) AS t(item);

-- Close session with a handoff note
SELECT aidb_memory.session_end('my_ns', session_user, :'session_conversation_id',
    'User was asking about top customers.');
```

---

## 10. MCP Endpoint Configuration

```sql
-- Enable the MCP endpoint (requires postgresql.conf restart)
ALTER SYSTEM SET edb.endpoints_mcp_enabled = 'on';
ALTER SYSTEM SET edb.endpoints_mcp_host = '127.0.0.1';
ALTER SYSTEM SET edb.endpoints_mcp_port = 8765;
-- SELECT pg_reload_conf();  -- for other non-postmaster GUCs

-- View currently available MCP tools
SELECT aidb.get_mcp_tools();
```

---

## 11. Telemetry / OTel

```sql
-- Configure OTel export mode
ALTER SYSTEM SET aidb.otel_client = 'database';  -- stores in aidb_otel.* tables
-- Other options: 'noop' (default), 'stdout', 'log', 'grpc'

-- After restart, view recent spans
SELECT * FROM aidb_otel.spans ORDER BY start_time DESC LIMIT 20;
```

---

## 12. Egress Allow-listing

```sql
-- Allow outbound calls to specific hosts
ALTER SYSTEM SET aidb.egress_allowlist = 'api.openai.com,my-nim-host:8080';

-- Allow insecure (non-TLS) connections (not recommended for production)
ALTER SYSTEM SET aidb.allow_insecure_egress = 'on';
```
