---
name: aidb
description: >
  Skill for operating EDB aidb (AI Accelerator Pipelines), the PostgreSQL extension
  that brings native AI capabilities into EDB Postgres AI ("AI Factory"). Use this
  skill when a user needs to: install or configure the aidb extension; register AI
  models (local or remote); create, run, or debug AI pipelines that embed, chunk,
  summarize, OCR, or parse documents; build a Semantic Knowledge Base for
  natural-language schema search; create Semantic Aliases (parameterized SQL
  discoverable by AI); create and converse with Agents (Agent Hub) using registered
  tools (Tools Hub); or troubleshoot any of these capabilities. Also applies when the
  user references "AI Factory", "AI Accelerator", "pg_ai", "aidb", or "EDB Sovereign AI".
metadata:
  aliases:
    - aidb
    - ai-factory
  version: "7.7.0"
  postgres_versions: ["14", "15", "16", "17", "18"]
  primary_interface: SQL (all operations are SQL functions in the `aidb` schema)
  docs: https://www.enterprisedb.com/docs/pg_ai/latest/
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that brings AI natively into Postgres. Every operation
is a SQL function call in the `aidb` schema — there is no separate service to manage.
All data stays in the user's Postgres instance (Sovereign AI).

> **Quick orientation:** `[Function Reference](references/function-reference.md)` |
> `[Model Adapters](references/model-adapters.md)` |
> `[Pipeline Steps](references/step-operations.md)` |
> `[Agent Hub](references/agent-hub.md)` |
> `[Troubleshooting](references/troubleshooting.md)` |
> `[Quick-Reference Card](assets/quick-reference.md)`

---

## 1. Extension Setup

```sql
-- Install (CASCADE installs pgvector and pgfs dependencies)
CREATE EXTENSION aidb CASCADE;

-- Verify
SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';

-- Upgrade after package update
ALTER EXTENSION aidb UPDATE;
```

**Required in `postgresql.conf` (restart needed):**
```
shared_preload_libraries = 'aidb,vchord'
```

**Supported PostgreSQL versions:** 14, 15, 16, 17, 18.

---

## 2. Registering Models

Models are registered once and reused by name everywhere. Credentials are stored
securely in `pg_user_mappings` — never returned by `list_models()`.

```sql
-- Local model (no external API)
SELECT aidb.create_model('my_bert', 'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB);

-- OpenAI embeddings
SELECT aidb.create_model('my_embed', 'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => 'sk-...'
    ));

-- Generic OpenAI-compatible (vLLM, NIM, etc.)
SELECT aidb.create_model('my_llm', 'completions',
    config => aidb.completions_config(
        model => 'mistral-7b',
        url   => 'http://vllm-host:8000/v1/chat/completions'
    ));

-- Test/development (no external service needed, deterministic output)
SELECT aidb.create_model('test_model', 'dummy');

-- Inspect registered models
SELECT name, provider FROM aidb.list_models();
```

For all provider options and configuration: **[Model Adapters](references/model-adapters.md)**

---

## 3. AI Pipelines

A pipeline reads from a **source table**, runs rows through **up to 10 ordered steps**,
and writes results to a **destination table** (must not exist at creation time).

### 3.1 Pipeline Auto-Processing Modes

| Mode | Behavior |
|---|---|
| `Live` | Synchronous trigger on each INSERT/UPDATE |
| `Background` | Async batch worker (non-blocking) |
| `Disabled` | Manual-only via `aidb.run_pipeline()` |

### 3.2 Creating a Text Embedding Pipeline

```sql
-- Source table must already exist; destination must NOT exist
SELECT aidb.create_pipeline(
    name               => 'doc_embeddings',
    source             => 'public.documents',
    source_data_column => 'body',
    source_key_column  => 'id',
    auto_processing    => 'Background',
    step_1             => 'KnowledgeBase',
    step_1_options     => aidb.knowledge_base_config(
        model             => 'my_embed',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- Process existing rows immediately
SELECT aidb.run_pipeline('doc_embeddings');

-- Similarity search against destination table
SELECT source_id
FROM doc_embeddings_destination  -- actual name shown in aidb.list_pipelines()
ORDER BY embedding <=> aidb.encode_text_query('your query', 'my_embed')
LIMIT 10;
```

**Complete template:** `scripts/setup_embedding_pipeline.sql`

### 3.3 Multi-Step Pipelines (PDF → OCR, HTML → Embed)

For document ingestion patterns: **`scripts/setup_document_pipeline.sql`**

Common chains:
- `ParsePdf → ChunkText → KnowledgeBase` (text PDFs)
- `PdfToImage → PerformOcr` (scanned PDFs, requires `nim_paddle_ocr`)
- `ParseHtml → ChunkText → KnowledgeBase` (web pages)

Full step reference: **[Step Operations](references/step-operations.md)**

### 3.4 Pipeline Management

```sql
SELECT aidb.list_pipelines();                                      -- list all
SELECT aidb.update_pipeline('name', auto_processing => 'Live');    -- change mode
SELECT aidb.run_pipeline('name');                                   -- manual trigger
SELECT aidb.delete_pipeline('name', cascade => true);              -- delete + destination table

-- Check error log
SELECT source_id, error_message, last_seen_at
FROM pipeline_<name>_errors ORDER BY last_seen_at DESC;

-- Requeue failed rows
SELECT aidb.requeue_pipeline_errors('name');
```

**Name constraint:** ≤ 46 characters.

---

## 4. Standalone AI Functions

These work without any pipeline setup:

```sql
-- Text embedding
SELECT aidb.encode_text('hello world', 'my_embed');            -- single → VECTOR
SELECT aidb.encode_text_batch(ARRAY['a','b'], 'my_embed');     -- batch → VECTOR[]
SELECT aidb.encode_text_query('find similar docs', 'my_embed');-- query-side vector

-- Text chunking
SELECT part_id, value FROM aidb.chunk_text(
    'Long document text here...',
    aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
);

-- Summarization
SELECT aidb.summarize_text('Long text...', aidb.summarize_text_config(model => 'my_llm'));

-- Document parsing
SELECT aidb.parse_pdf(pdf_bytes_column, aidb.pdf_parse_config('Structured'));
SELECT aidb.parse_html(html_bytes_column, aidb.html_parse_config('StructuredMarkdown'));

-- Semantic reranking
SELECT index, score
FROM aidb.rerank_text('query', ARRAY['doc1', 'doc2', 'doc3'], 'my_rerank_model')
ORDER BY score DESC;
```

---

## 5. Semantic Knowledge Base

A Semantic KB embeds a PostgreSQL schema's table/column metadata so that natural
language queries can find relevant database objects.

```sql
-- Create KB over one or more schemas
SELECT aidb.create_semantic_kb(
    name            => 'schema_kb',
    model           => 'my_embed',
    schemas         => ARRAY['public', 'app'],
    auto_processing => 'Live',
    vector_index    => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
);

-- Combined search: schema entities + aliases ranked together (v7.5.0+)
SELECT source_type, entity_type, relation_name, column_name, score
FROM aidb.semantic_kb_search('customer order history', 'schema_kb', 10)
ORDER BY score DESC;

-- Targeted searches
SELECT schema_name, relation_name, similarity
FROM aidb.get_tables('schema_kb', 'customer orders', 0.5, 10, 0);

SELECT schema_name, relation_name, column_name, similarity
FROM aidb.get_columns('schema_kb', 'email address', 0.6, 10, 0);

-- Refresh after schema changes
SELECT aidb.refresh_semantic_kb('schema_kb');
```

**Similarity thresholds:** 0.9+ (near-exact), 0.8 (good default), 0.5–0.7 (broad).

**Template:** `scripts/setup_semantic_kb.sql`

### 5.1 Semantic Aliases

Register parameterized SQL queries discoverable by agents via semantic search:

```sql
SELECT aidb.create_semantic_alias(
    name        => 'top_customers_by_spend',
    description => 'Rank customers by total lifetime spending',
    query_text  => 'SELECT * FROM customer_ltv ORDER BY lifetime_value DESC LIMIT ${n}',
    params      => aidb.alias_params(
                       aidb.alias_param('n', 'integer', 'Number of customers to return')
                   ),
    model       => 'schema_kb'
);

-- Execute an alias
SELECT aidb.execute_semantic_alias('top_customers_by_spend', '{"n": 5}'::JSONB);
```

---

## 6. Agent Hub

Agents use a reasoning loop to answer prompts by calling registered tools.

```sql
-- Create an agent
SELECT error FROM aidb.create_agent(
    name         => 'data_assistant',
    instructions => 'You are a helpful database assistant. Answer concisely.',
    model        => 'my_llm'
);

-- Converse (NEVER raises — always check error column)
SELECT message, conversation_id, error
FROM aidb.agent_converse('data_assistant', 'What tables exist in public schema?');

-- Continue a conversation
SELECT message, conversation_id, error
FROM aidb.agent_converse(
    'data_assistant',
    'Show me the 5 most recent orders.',
    conversation_id => '<uuid-from-previous-turn>'
);
```

**Template:** `scripts/setup_agent.sql`
**Full reference:** `references/agent-hub.md`

### 6.1 Agent Safety Rules (MUST follow)

1. **`agent_converse` never raises** — check `error IS NULL` before using `message`
2. **Credentials never in plaintext** — use `aidb.create_model()` with config helpers
3. **Delegation cannot escalate privileges** — inherits parent's role and `read_only` flag
4. **Audit trail is immutable** — `action_log` is INSERT + SELECT only
5. **Read-only enforcement** is 3-layered; PostgreSQL enforces it at the transaction level

### 6.2 Registering Tools

```sql
-- SQL tool
SELECT aidb.create_sql_tool(
    name          => 'recent_orders',
    description   => 'Returns the N most recent orders',
    sql_statement => 'SELECT id, total FROM orders ORDER BY created_at DESC LIMIT $n',
    params        => aidb.params(aidb.param('n', 'integer', 'Number of orders')),
    read_only     => TRUE,
    return_type_hint => NULL
);

-- Test a tool directly
SELECT aidb.run_tool('recent_orders', '{"n": 5}'::jsonb);

-- View all tools (native + SQL + MCP)
SELECT name, description, read_only FROM aidb.tools;
```

---

## 7. Configuration Reference

| GUC | Default | Restart? | Notes |
|---|---|---|---|
| `aidb.max_threads` | CPUs/2 | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | `true` | No | Emit pipeline errors to PostgreSQL log |
| `aidb.agent_session_source` | `action_log` | No | `action_log` or `memory` |
| `aidb.agent_memory_namespace` | `pg_agents` | No | Memory namespace for Agent Hub |

---

## 8. Diagnostic & Troubleshooting

**Run the SQL diagnostic:**
```bash
psql -d mydb -f scripts/check_aidb.sql
```

**Run the Python health check:**
```bash
python3 scripts/aidb_health_check.py --host localhost --dbname mydb
```

**Common issues:**

| Symptom | Likely Cause | Fix |
|---|---|---|
| Background worker not processing | `aidb` not in `shared_preload_libraries` | Add to conf → restart PG |
| Pipeline creation fails | Destination table already exists | Drop it or choose a new name |
| `model not found` error | Model not registered in this DB | `SELECT aidb.create_model(...)` |
| Agent always returns error | Model unreachable or bad credentials | Test `aidb.encode_text()` or `aidb.run_tool()` directly |
| `semantic_kb_search` not found | aidb version < 7.5.0 | Use `get_tables`/`get_columns` instead |
| Local model slow | `aidb.max_threads` too low | Increase in `postgresql.conf` → restart |
| `encode_text` returns NULL | Wrong model capability type | Check provider in `references/model-adapters.md` |

Full troubleshooting guide: **[Troubleshooting](references/troubleshooting.md)**

---

## 9. Key Constraints Summary

| Constraint | Value |
|---|---|
| Pipeline name max length | 46 characters |
| Max steps per pipeline | 10 |
| Destination table at creation | Must NOT exist |
| Model validation timing | At `create_pipeline`, not at runtime |
| Agent max reasoning iterations | 25 |
| Agent max delegation depth | 11 |
| `aidb.max_threads` change | Requires PostgreSQL restart |

---

## 10. AI Factory Context

AIDB is the AI Accelerator Pipelines component of EDB Postgres AI. Related components:

- **pgvector** — vector similarity search (installed as CASCADE dependency)
- **Gen AI Builder** — Assistants, Tools, Rulesets, Threads (higher-level agent UI)
- **Model Serving** — GPU-accelerated KServe inference
- **Hybrid Manager** — Kubernetes governance layer for full audit and governance

AIDB runs standalone on any EDB Postgres AI distribution or inside Hybrid Manager.
No mandatory external dependency — local model providers need no API calls.