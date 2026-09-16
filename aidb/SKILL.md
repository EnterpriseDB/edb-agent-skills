---
name: aidb
description: >
  Skill for operating AIDB (EDB AI Accelerator Pipelines), the PostgreSQL extension that brings
  native AI capabilities into EDB Postgres AI ("AI Factory"). Use this skill when a user asks to:
  install or configure AIDB; register AI models (embeddings, completions, OCR, reranking);
  create, run, update, or delete AI processing pipelines; build vector knowledge bases for
  semantic/similarity search (RAG); process PDFs, HTML, or images through AI steps; create or
  query a Semantic Knowledge Base for natural-language schema discovery or text-to-SQL; define
  and execute Semantic Aliases (parameterized SQL with natural-language descriptions); create
  AI agents with tool-calling capabilities; debug pipeline failures or model errors; or tune
  AIDB runtime configuration. All operations are executed as SQL in the `aidb` schema.
metadata:
  version: "7.7.0"
  postgres_versions: "14-18"
  aliases:
    - aidb
    - ai-factory
  references:
    - references/function-reference.md
    - references/model-adapters.md
    - references/step-operations.md
    - references/examples.md
    - references/troubleshooting.md
  scripts:
    - scripts/register_model.py
    - scripts/setup_pipeline.py
    - scripts/diagnose_aidb.sql
  assets:
    - assets/quick-reference.md
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that exposes all AI capabilities as SQL functions in the `aidb`
schema. Every operation — model registration, pipeline management, embedding, summarization,
OCR, semantic search, agent conversations — is invoked via `SELECT aidb.<function>(...)`.

## Core Mental Model

```
Source Table / Volume
       ↓
  Pipeline (named, max 46-char name)
       ↓  (up to 10 ordered steps)
  Steps: ParsePdf | ParseHtml | PdfToImage | PerformOcr | ChunkText | SummarizeText | KnowledgeBase
       ↓
  Destination Table  (auto-created; must NOT exist at creation time)
```

Data flows through **envelope types**: `Bytes` → `Text` → `Vector`. Steps must be sequenced so
the output type of step N matches the input type of step N+1. Incompatible sequences are
rejected at creation time. `KnowledgeBase` is always the **terminal** step.

---

## Step 1: Install

> Prerequisites: `shared_preload_libraries = 'aidb'` in `postgresql.conf` and a PostgreSQL
> restart. `pgvector` must be installed for `KnowledgeBase` steps.

```sql
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;
```

Verify:
```sql
SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';
SHOW shared_preload_libraries;
```

---

## Step 2: Register a Model

All pipelines and standalone AI functions reference models **by name**. Register once, reuse
everywhere. Credentials are stored in `pg_user_mappings` and never returned by `list_models`.

**Pattern:**
```sql
SELECT aidb.create_model(
    name        => '<model_name>',          -- unique identifier
    provider    => '<provider_type>',       -- see references/model-adapters.md
    config      => <config_jsonb>,          -- use aidb.embeddings_config() or aidb.completions_config()
    credentials => <creds_jsonb>,           -- API keys, auth tokens
    validate    => TRUE                     -- set FALSE for local models not yet downloaded
);
```

**Quick examples:**
```sql
-- OpenAI embeddings
SELECT aidb.create_model('my_embed', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- Local BERT (no external call, no API key)
SELECT aidb.create_model('my_bert', 'bert_local',
    config => '{"model": "/models/bert-base-uncased"}'::JSONB, validate => false);

-- OpenAI completions
SELECT aidb.create_model('my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...'));

-- Self-hosted vLLM (any OpenAI-compatible endpoint)
SELECT aidb.create_model('my_vllm', 'completions',
    config => aidb.completions_config(model => 'mistral-7b',
              url => 'http://vllm:8000/v1/chat/completions'));

-- Anthropic Claude (agent-capable: supports tool-calling)
SELECT aidb.create_model('claude', 'anthropic_messages',
    config => '{"model": "claude-3-5-sonnet-20241022"}'::JSONB,
    credentials => '{"api_key": "sk-ant-..."}'::JSONB);

-- Test/dummy model (no external service, deterministic — safe for all testing)
SELECT aidb.create_model('test_model', 'dummy');

-- List all registered models:
SELECT * FROM aidb.list_models();
```

> Use `scripts/register_model.py` to generate correct SQL for any provider.
> See [references/model-adapters.md](references/model-adapters.md) for the full provider list,
> capability matrix, and credential handling details.

---

## Step 3: Create a Pipeline

```sql
SELECT aidb.create_pipeline(
    name                => '<name>',            -- max 46 characters
    source              => '<table_or_volume>', -- source table name
    source_key_column   => '<pk_column>',       -- primary key column
    source_data_column  => '<data_column>',     -- column with content (TEXT or BYTEA)
    destination         => NULL,                -- auto-named pipeline_<name> if NULL
    auto_processing     => 'Background',        -- 'Live' | 'Background' | 'Disabled'
    step_1              => '<StepType>',        -- e.g. 'ChunkText'
    step_1_options      => <jsonb>,             -- use aidb.*_config() helpers
    step_2              => '<StepType>',        -- optional, supported up to step_10
    step_2_options      => <jsonb>
);
```

**Common pipeline recipes:**

| Goal | Step sequence |
|------|--------------|
| Text → embeddings (RAG) | `ChunkText → KnowledgeBase` |
| PDF → embeddings (RAG) | `ParsePdf → ChunkText → KnowledgeBase` |
| HTML → embeddings (RAG) | `ParseHtml → ChunkText → KnowledgeBase` |
| Scanned PDF → text (OCR) | `PdfToImage → PerformOcr` |
| Scanned PDF → embeddings | `PdfToImage → PerformOcr → ChunkText → KnowledgeBase` |
| Summarize text | `SummarizeText` |
| Summarize + embed | `SummarizeText → KnowledgeBase` |

**Example — text RAG pipeline:**
```sql
SELECT aidb.create_pipeline(
    name                => 'article_embeddings',   -- must be ≤46 chars
    source              => 'articles',
    source_key_column   => 'id',
    source_data_column  => 'body',
    auto_processing     => 'Background',
    step_1              => 'ChunkText',
    step_1_options      => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2              => 'KnowledgeBase',
    step_2_options      => aidb.knowledge_base_config(
        model             => 'my_embed',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);
```

**Example — PDF RAG pipeline:**
```sql
SELECT aidb.create_pipeline(
    name                => 'doc_rag',
    source              => 'documents',
    source_key_column   => 'id',
    source_data_column  => 'content',         -- BYTEA column containing PDF bytes
    auto_processing     => 'Disabled',
    step_1              => 'ParsePdf',
    step_1_options      => aidb.pdf_parse_config(method => 'Structured'),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model => 'my_embed', data_format => 'Text', distance_operator => 'Cosine'
    )
);
```

> Use `scripts/setup_pipeline.py` to generate boilerplate SQL for any pipeline type.
> See [references/step-operations.md](references/step-operations.md) for all step types,
> type compatibility rules, and config helper signatures.

---

## Step 4: Run a Pipeline and Search

```sql
-- Trigger manually (always works; required for 'Disabled' mode and initial loads):
SELECT aidb.run_pipeline('article_embeddings');

-- Force re-process all rows regardless of state:
SELECT aidb.run_pipeline('article_embeddings', force_sync => TRUE);

-- Similarity search (destination table is pipeline_<name> by default):
SELECT source_id,
       embedding <=> aidb.encode_text_query('postgres performance tuning', 'my_embed') AS distance
FROM pipeline_article_embeddings
ORDER BY embedding <=> aidb.encode_text_query('postgres performance tuning', 'my_embed')
LIMIT 10;
```

**Distance operator → pgvector SQL operator mapping:**

| `distance_operator` | SQL op | Use case |
|---------------------|--------|----------|
| `Cosine` | `<=>` | Normalized embeddings (recommended default) |
| `L2` (default) | `<->` | General-purpose Euclidean |
| `InnerProduct` | `<#>` | Unit-normalized vectors |
| `L1` | `<+>` | Manhattan distance |

---

## Semantic Knowledge Base

A Semantic KB indexes your PostgreSQL schema metadata (tables, columns, views, comments) so AI
agents and applications can find relevant schema elements using natural language. It is the
foundation for text-to-SQL and schema-aware AI workflows.

```sql
-- Create a KB over the 'public' schema
SELECT aidb.create_semantic_kb(
    name            => 'main_kb',
    model           => 'my_embed',
    schemas         => ARRAY['public'],
    auto_processing => 'Live'   -- 'Live' | 'Background' | 'Disabled'
);

-- Search for tables related to a concept
SELECT schema_name, relation_name, entity_type, similarity
FROM aidb.get_tables('main_kb', 'customer purchase history', 0.7, 10, 0);

-- Search for columns
SELECT relation_name, column_name, definition, similarity
FROM aidb.get_columns('main_kb', 'email address', 0.7, 10, 0);

-- Combined ranked search (tables + columns + semantic aliases)
SELECT source_type, entity_type, relation_name, column_name, score
FROM aidb.semantic_kb_search('which table stores order totals', 'main_kb', 10)
ORDER BY rank;

-- Create a Semantic Alias (parameterized SQL discoverable via natural language)
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by total spend',
    'SELECT customer_id, SUM(total) AS spend FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'How many customers to return')),
    'main_kb'
);

-- Execute an alias by name
SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 5}'::JSONB);

-- Refresh manually (if using Disabled mode)
SELECT aidb.refresh_semantic_kb('main_kb');
```

**Similarity threshold guidance:** `0.9+` near-exact · `0.8` good default · `0.7` broader ·
`0.5–0.6` exploratory

---

## Standalone AI Functions

Invoke AI operations without a full pipeline:

```sql
-- Single embedding (returns VECTOR)
SELECT aidb.encode_text('Hello, PostgreSQL!', 'my_embed');

-- Batch embeddings
SELECT aidb.encode_text_batch(ARRAY['text one', 'text two', 'text three'], 'my_embed');

-- Rerank candidates
SELECT index, score FROM aidb.rerank_text(
    'postgres performance tuning',
    ARRAY['vacuum is important', 'indexing helps queries', 'unrelated content'],
    'my_rerank_model'
) ORDER BY score DESC;

-- Chunk text
SELECT part_id, value
FROM aidb.chunk_text('Long document text...', aidb.chunk_text_config(desired_length => 200))
ORDER BY part_id;

-- Summarize
SELECT aidb.summarize_text('Very long document...',
    aidb.summarize_text_config(model => 'my_llm', strategy => 'reduce', reduction_factor => 3));

-- Parse HTML (input BYTEA)
SELECT aidb.parse_html('<html><body><p>Content</p></body></html>'::BYTEA);

-- Parse PDF (input BYTEA)
SELECT aidb.parse_pdf(pg_read_binary_file('/path/to/doc.pdf'));
```

---

## Agent Hub

Agents require an **agent-capable model** that supports tool-calling:
`anthropic_messages`, `anthropic_messages_azure`, `anthropic_messages_bedrock`,
`openai_responses`, `openai_responses_azure`.

```sql
-- 1. Create a SQL tool
SELECT aidb.create_sql_tool(
    'get_top_products',
    'Returns the top N products by revenue',
    'SELECT product_id, name, SUM(revenue) AS total FROM sales GROUP BY 1, 2 ORDER BY 3 DESC LIMIT ${n}',
    aidb.params(aidb.param('n', 'integer', 'How many products')),
    read_only => TRUE
);

-- 2. Create an agent
SELECT aidb.create_agent(
    name         => 'sales_agent',
    instructions => 'You are a sales analytics assistant. Use available tools to answer questions.',
    model        => 'claude',
    tools        => ARRAY['get_top_products']
);

-- 3. Converse — NEVER raises; always inspect the error column
SELECT conversation_id, message, error
FROM aidb.agent_converse('sales_agent', 'What were our top 5 products last month?');

-- 4. Continue a conversation using conversation_id from previous call
SELECT conversation_id, message, error
FROM aidb.agent_converse('sales_agent', 'How does that compare to the month before?',
    conversation_id => '<uuid-from-above>');

-- Inspect conversation history
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';
```

> **Critical rule:** `aidb.agent_converse` **never raises an exception**. A failed turn returns
> an `error` string. Always `SELECT ... error FROM aidb.agent_converse(...)` and check it.

---

## Pipeline Management

```sql
-- List all pipelines with their mode
SELECT name, auto_processing FROM aidb.list_pipelines();

-- Switch processing mode
SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Live');

-- View errors (per pipeline)
SELECT * FROM aidb.get_error_logs('my_pipeline') ORDER BY last_seen_at DESC LIMIT 20;

-- Re-queue failed items after fixing root cause
SELECT aidb.requeue_pipeline_errors('my_pipeline');

-- Delete pipeline; cascade => TRUE also drops the destination table
SELECT aidb.delete_pipeline('my_pipeline', cascade => TRUE);
```

---

## Key Constraints (Never Violate These)

| Constraint | Value |
|---|---|
| Pipeline name max length | **46 characters** |
| Max steps per pipeline | **10** |
| Destination table at creation | **Must NOT already exist** |
| Step sequence validation | At **creation time** — incompatible sequences are rejected |
| `KnowledgeBase` step position | Always **terminal** — no step can follow it |
| `PerformOcr` provider requirement | Needs `nim_paddle_ocr` or `llamacpp_ocr` provider |
| `aidb.max_threads` changes | Require **PostgreSQL restart** |
| `agent_converse` error handling | **Never raises** — always check the `error` column |
| Max agent reasoning iterations | **25** per conversation turn |
| Max agent delegation depth | **11** levels |

---

## GUC Parameters

| Parameter | Default | Restart Required | Description |
|---|---|---|---|
| `aidb.max_threads` | half of CPUs (min 1, max 1024) | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | true | No | Emit per-error WARNING to PostgreSQL log |

```sql
-- View current settings
SELECT name, setting FROM pg_settings WHERE name LIKE 'aidb.%';

-- Change without restart
SET aidb.pipeline_error_warnings = false;

-- Change max_threads: edit postgresql.conf then restart
-- aidb.max_threads = 8
```

---

## Diagnostics & Troubleshooting

Run the full diagnostic script:
```bash
psql -d mydb -f scripts/diagnose_aidb.sql
```

Quick inline checks:
```sql
-- Extension installed?
SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';
-- pgvector available (required for KnowledgeBase)?
SELECT installed_version FROM pg_available_extensions WHERE name = 'vector';
-- Background workers running?
SELECT backend_type, state FROM pg_stat_activity WHERE backend_type ILIKE '%aidb%';
-- Models registered?
SELECT * FROM aidb.list_models();
-- Pipelines configured?
SELECT name, auto_processing FROM aidb.list_pipelines();
-- Recent pipeline errors?
SELECT * FROM aidb.get_error_logs('<pipeline_name>') LIMIT 10;
```

**Common failure → resolution:**

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `CREATE EXTENSION aidb` fails | `aidb` not in `shared_preload_libraries` | Edit `postgresql.conf`, restart Postgres |
| Background workers absent | Same | Same fix |
| "destination table already exists" | Table already present | `DROP TABLE IF EXISTS <dest>;` first |
| "name too long" on pipeline create | Name > 46 chars | Shorten the pipeline name |
| Step sequence rejected at creation | Type mismatch between steps | Check step I/O types in [step-operations.md](references/step-operations.md) |
| No similarity search results | `min_similarity` threshold too high | Lower to `0.5` and increase gradually |
| Model validation error during setup | Provider unreachable | Use `validate => false` |
| Local model inference slow | `aidb.max_threads` too low | Increase in `postgresql.conf` + restart |
| KB search returns nothing | KB not yet populated / refreshed | `SELECT aidb.refresh_semantic_kb('kb_name');` |

See [references/troubleshooting.md](references/troubleshooting.md) for detailed resolution steps
and advanced debug logging.

---

## Reference Files (Load On Demand)

| File | When to read |
|------|-------------|
| [references/function-reference.md](references/function-reference.md) | Complete SQL signatures for every `aidb.*` function |
| [references/model-adapters.md](references/model-adapters.md) | All providers, capabilities, credential handling |
| [references/step-operations.md](references/step-operations.md) | Step types, compatibility table, multi-step recipes |
| [references/examples.md](references/examples.md) | Runnable SQL for all major use cases |
| [references/troubleshooting.md](references/troubleshooting.md) | Failure patterns, diagnostics, resolution |
| [assets/quick-reference.md](assets/quick-reference.md) | Compact cheat sheet: constraints, operators, thresholds |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/register_model.py` | Generate model-registration SQL for any provider |
| `scripts/setup_pipeline.py` | Generate pipeline boilerplate SQL for any pattern |
| `scripts/diagnose_aidb.sql` | Full system diagnostic report (run with psql) |