---
name: aidb
description: >
  Skill for operating AIDB — a PostgreSQL extension (EDB Postgres AI / AI Factory) that brings
  native AI capabilities into Postgres entirely through SQL. Use this skill when a user needs to:
  register AI models; create, run, or manage data-transformation pipelines (chunking, OCR, PDF
  parsing, HTML parsing, summarization, or vector embedding); build a Semantic Knowledge Base
  for natural-language schema discovery (text-to-SQL); define and execute Semantic Aliases
  (parameterized agent-callable SQL queries); manage object-storage volumes; or call standalone
  AI functions (encode_text, rerank_text, parse_pdf, etc.). Supports PostgreSQL 14–18. All
  operations are SQL-only. No Python SDK or REST API exists; the agent must connect to PostgreSQL
  and issue SQL statements.
metadata:
  aliases:
    - aidb
    - ai-factory
  version: "7.4.0"
  edb_product: EDB Postgres AI — AI Accelerator Pipelines
  postgres_versions: "14, 15, 16, 17, 18"
  schema: aidb
  sovereign_ai: true
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that adds native AI capabilities — embeddings, vector search,
document parsing, OCR, summarization, and schema-aware intelligence — all via SQL in the
`aidb` schema. This skill teaches you exactly how to use it.

**Key principle**: Every operation is a SQL function call. Connect to PostgreSQL and run SQL.

---

## 1. Quick-Start Checklist

Before operating AIDB, verify the environment:

```sql
-- 1. Confirm extension is installed
SELECT name, installed_version FROM pg_available_extensions WHERE name = 'aidb';

-- 2. Install if missing (needs shared_preload_libraries='aidb' in postgresql.conf + restart first)
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;

-- 3. Verify models, pipelines, KBs
SELECT * FROM aidb.list_models();
SELECT * FROM aidb.list_pipelines();
SELECT * FROM aidb.list_semantic_kbs();
```

If `aidb` is not available in `pg_available_extensions`, the extension package is not installed on
this PostgreSQL instance. Escalate to the database administrator.

---

## 2. Core Concepts

| Concept | Description |
|---|---|
| **Model** | A named AI model registered with a provider and optional credentials. Referenced by name everywhere. |
| **Pipeline** | Source table → ordered transformation steps → destination table. Runs on data change or manually. |
| **Step** | One transformation unit within a pipeline (e.g., `ChunkText`, `KnowledgeBase`, `ParsePdf`). |
| **Semantic KB** | Vectorized index of PostgreSQL schema metadata. Enables natural language schema discovery. |
| **Semantic Alias** | Named SQL query with NL description and parameters, discoverable by agents via semantic search. |
| **Volume** | Object-storage mount (S3/GCS/Azure/local) for file-based pipeline sources. |

---

## 3. Model Registration

Models must be registered once and are then referenced by name.

```sql
-- Local model (no external API)
SELECT aidb.create_model('my_bert', 'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB);

-- OpenAI embeddings
SELECT aidb.create_model('my_embed', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- OpenAI completions
SELECT aidb.create_model('my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o-mini', api_key => 'sk-...'));

-- Generic OpenAI-compatible endpoint (e.g. vLLM, Ollama)
SELECT aidb.create_model('local_llm', 'completions',
    config => aidb.completions_config(
        model => 'mistral-7b',
        url   => 'http://vllm-host:8000/v1/chat/completions'));

-- Testing (no external service — deterministic output)
SELECT aidb.create_model('test_model', 'dummy');

-- List models (credentials never returned)
SELECT * FROM aidb.list_models();
```

> **Security**: Credentials are stored in `pg_user_mappings`, never in plain-text catalog tables.
> `aidb.list_models()` and `aidb.get_model()` never return credential fields.

See [references/model-adapters.md](references/model-adapters.md) for all supported providers.

---

## 4. Pipeline Creation

### 4.1 Basic text-to-embeddings pipeline

```sql
-- Source table must already exist; destination must NOT exist
CREATE TABLE documents (id SERIAL PRIMARY KEY, content TEXT NOT NULL);

SELECT aidb.create_pipeline(
    name               => 'doc_embeddings',        -- max 46 chars
    source             => 'documents',
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Background',            -- 'Live' | 'Background' | 'Disabled'
    step_1             => 'KnowledgeBase',
    step_1_options     => aidb.knowledge_base_config(
        model             => 'my_embed',
        data_format       => 'Text',
        distance_operator => 'Cosine',             -- 'L2' | 'Cosine' | 'InnerProduct'
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);
```

### 4.2 Multi-step: PDF → images → OCR → chunks → embeddings

```sql
SELECT aidb.create_pipeline(
    name               => 'pdf_ocr_embed',
    source             => 'pdf_docs',
    source_key_column  => 'id',
    source_data_column => 'content',               -- BYTEA column
    auto_processing    => 'Background',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('my_ocr_model'),
    step_3             => 'ChunkText',
    step_3_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_4             => 'KnowledgeBase',
    step_4_options     => aidb.knowledge_base_config(model => 'my_embed', data_format => 'Text')
);
```

### 4.3 Auto-processing modes

| Mode | Behavior |
|---|---|
| `Live` | Triggers synchronously on every INSERT/UPDATE (low-latency, use for small volumes) |
| `Background` | Async worker batches new rows (high-volume, non-blocking) |
| `Disabled` | Manual trigger only via `aidb.run_pipeline()` |

### 4.4 Run, update, delete

```sql
-- Run manually (works for any mode; force_sync reruns all rows)
SELECT aidb.run_pipeline('doc_embeddings');
SELECT aidb.run_pipeline('doc_embeddings', force_sync => TRUE);

-- Change auto_processing mode
SELECT aidb.update_pipeline('doc_embeddings', auto_processing => 'Live');

-- Delete pipeline (cascade => TRUE also drops destination table)
SELECT aidb.delete_pipeline('doc_embeddings', cascade => FALSE);
```

See [references/step-operations.md](references/step-operations.md) for all step types, their
input/output types, config helpers, and valid sequencing rules.

---

## 5. Semantic Search (After Pipeline Runs)

```sql
-- Destination table is named: pipeline_<pipeline_name>
-- Use pgvector distance operators based on distance_operator configured:
--   Cosine  →  <=>
--   L2      →  <->
--   InnerProduct → <#>

SELECT source_id
FROM pipeline_doc_embeddings
ORDER BY embedding <=> aidb.encode_text_query('find payment policies', 'my_embed')
LIMIT 10;
```

---

## 6. Semantic Knowledge Base

```sql
-- Create a KB over one or more schemas
SELECT aidb.create_semantic_kb(
    name            => 'app_schema_kb',
    model           => 'my_embed',
    schemas         => ARRAY['public', 'analytics'],
    auto_processing => 'Background'
);

-- Refresh after schema changes
SELECT aidb.refresh_semantic_kb('app_schema_kb');

-- Search: find relevant tables
SELECT schema_name, relation_name, similarity
FROM aidb.get_tables('app_schema_kb', 'customer order history', 0.7, 5, 0);

-- Search: find relevant columns
SELECT schema_name, relation_name, column_name, similarity
FROM aidb.get_columns('app_schema_kb', 'user email address', 0.7, 10, 0);

-- Full metadata search
SELECT schema_name, relation_name, column_name, entity_type, definition, similarity
FROM aidb.get_metadata('app_schema_kb', 'invoice total amount', 0.6, 10, 0);
```

**Similarity threshold guidance**: 0.9+ = near-exact; 0.8 = good default; 0.5–0.7 = exploration.

---

## 7. Semantic Aliases

```sql
-- Register a named, agent-callable query
SELECT aidb.create_semantic_alias(
    name        => 'monthly_sales_by_region',
    description => 'Total sales by region for a given calendar month',
    query_text  => $$
        SELECT region, SUM(amount) AS total_sales
        FROM sales
        WHERE date_trunc('month', sale_date) = date_trunc('month', ${target_month}::DATE)
        GROUP BY region ORDER BY total_sales DESC
    $$,
    params      => aidb.alias_params(
        aidb.alias_param('target_month', 'TEXT', 'Month to aggregate, e.g. 2024-01-01')
    ),
    model       => 'my_embed'
);

-- Discover aliases via natural language
SELECT name, description, similarity
FROM aidb.search_semantic_aliases('my_embed', 'sales by geography last month', 0.6, 5, 0);

-- Execute an alias
SELECT * FROM aidb.execute_semantic_alias(
    'monthly_sales_by_region',
    '{"target_month": "2024-01-01"}'::JSONB
);
```

---

## 8. Standalone AI Functions

These do NOT require a pipeline — call them directly in any SQL query.

```sql
-- Text embedding (single and batch)
SELECT aidb.encode_text('Hello world', 'my_embed');
SELECT aidb.encode_text_batch(ARRAY['doc 1', 'doc 2'], 'my_embed');
SELECT aidb.encode_text_query('search query', 'my_embed');  -- query-side for bi-encoders

-- Image embedding
SELECT aidb.encode_image(image_bytes_col, 'my_clip_model');

-- Semantic reranking
SELECT index, score FROM aidb.rerank_text('query', ARRAY['cand1','cand2'], 'my_rerank_model')
ORDER BY score DESC;

-- Text chunking
SELECT part_id, value FROM aidb.chunk_text(
    'Long text...', aidb.chunk_text_config(desired_length => 512, overlap_length => 64));

-- Summarization
SELECT aidb.summarize_text('Long doc...', aidb.summarize_text_config(model => 'my_llm'));

-- Document parsing
SELECT aidb.parse_html(html_bytes, aidb.html_parse_config());
SELECT aidb.parse_pdf(pdf_bytes, aidb.pdf_parse_config(method => 'Structured'));

-- OCR
SELECT aidb.perform_ocr(image_bytes, 'my_ocr_model');
```

---

## 9. Volume Management

```sql
-- Create a local-disk volume
SELECT aidb.create_volume(
    name             => 'my_local_vol',
    storage_location => 'local',
    path             => '/data/documents',
    data_type        => 'Text'          -- 'Text' | 'Bytes' | 'Image'
);

-- List, read, write, delete files
SELECT * FROM aidb.list_volumes();
SELECT aidb.list_volume_content('my_local_vol');
SELECT aidb.read_volume_file('my_local_vol', 'report.txt');
SELECT aidb.write_volume_data('my_local_vol', 'output.txt', 'content'::BYTEA);
SELECT aidb.delete_volume_file('my_local_vol', 'old.txt');
SELECT aidb.delete_volume('my_local_vol');
```

---

## 10. Runtime Configuration

```sql
-- View current GUC settings
SHOW aidb.max_threads;               -- CPU thread pool for local inference
SHOW aidb.pipeline_error_warnings;   -- Emit per-error WARNING to PG log

-- Change settings
ALTER SYSTEM SET aidb.max_threads = 8;          -- REQUIRES PG RESTART
ALTER SYSTEM SET aidb.pipeline_error_warnings = true;
SELECT pg_reload_conf();                         -- Only needed for pipeline_error_warnings
```

> **Important**: `aidb.max_threads` changes require a full PostgreSQL restart, not just `pg_reload_conf()`.

---

## 11. Key Constraints to Remember

- **Pipeline name max length**: 46 characters
- **Max steps per pipeline**: 10
- **Destination table must not exist** at pipeline creation time
- **Step sequencing**: output type of step N must match input type of step N+1; validated at creation
- **`KnowledgeBase` and `SemanticKB` must be the last step** in a pipeline (output type is Vector)
- **Model validation** happens at pipeline creation time, not at execution time
- **`aidb.max_threads` changes require a DB restart**

---

## 12. Troubleshooting Decision Tree

1. **`aidb` not found in `pg_available_extensions`** → Extension package not installed; contact DBA
2. **`CREATE EXTENSION aidb` fails** → Ensure `shared_preload_libraries = 'aidb'` in `postgresql.conf` and PG was restarted; use `CASCADE`
3. **Pipeline not processing automatically** → Check `auto_processing` mode; verify background workers are running (`SELECT * FROM pg_stat_activity WHERE backend_type LIKE '%background worker%'`)
4. **Pipeline creation error about destination** → Drop the destination table first
5. **Pipeline creation error about steps** → Review compatible step sequencing in [references/step-operations.md](references/step-operations.md)
6. **Model encode/completion fails** → Verify model registered (`aidb.list_models()`); test with `aidb.encode_text('test', 'model_name')`; check credentials via re-registration
7. **Similarity search returns nothing** → Check destination table row count; lower the similarity threshold; confirm embeddings are non-null
8. **Semantic KB returns no results** → Run `aidb.refresh_semantic_kb('name')` after schema changes; lower similarity threshold

---

## 13. Reference Files

| File | When to Read |
|---|---|
| [references/model-adapters.md](references/model-adapters.md) | All supported providers, capabilities, config helpers, credential handling |
| [references/step-operations.md](references/step-operations.md) | All pipeline step types, config helpers, compatible sequencing rules |
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures for all `aidb` schema functions |

---

## 14. Complete Workflow Example: Document Q&A Setup

```sql
-- 1. Install extension
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;

-- 2. Register embedding model
SELECT aidb.create_model('embed', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- 3. Create source table and insert data
CREATE TABLE docs (id SERIAL PRIMARY KEY, body TEXT NOT NULL);
INSERT INTO docs (body) VALUES ('Refunds must be requested within 30 days of purchase.');
INSERT INTO docs (body) VALUES ('Shipping is free for orders over $50.');

-- 4. Create pipeline
SELECT aidb.create_pipeline(
    name => 'docs_kb', source => 'docs',
    source_key_column => 'id', source_data_column => 'body',
    auto_processing => 'Background',
    step_1 => 'KnowledgeBase',
    step_1_options => aidb.knowledge_base_config(
        model => 'embed', data_format => 'Text', distance_operator => 'Cosine')
);

-- 5. Run pipeline to process existing rows
SELECT aidb.run_pipeline('docs_kb');

-- 6. Search
SELECT d.body, p.source_id
FROM pipeline_docs_kb p
JOIN docs d ON d.id = p.source_id
ORDER BY p.embedding <=> aidb.encode_text_query('What is the refund policy?', 'embed')
LIMIT 3;
```

New rows inserted into `docs` will be automatically embedded by the background worker.