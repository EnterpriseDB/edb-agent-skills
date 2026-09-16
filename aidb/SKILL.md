---
name: aidb
description: >
  Skill for operating AIDB (AI Accelerator Pipelines), the EDB PostgreSQL extension that brings
  native AI capabilities into Postgres via SQL. Trigger this skill when a user wants to: install
  or configure AIDB; register AI models; create, run, or manage data transformation pipelines
  (chunking, embedding, OCR, summarization, PDF/HTML parsing); build or query a Semantic
  Knowledge Base over PostgreSQL schema metadata; define or discover Semantic Aliases (parameterized
  SQL queries with natural language descriptions); create and converse with AI agents using the
  Agent Hub; define SQL or MCP tools via the Tools Hub; manage object storage volumes; or
  troubleshoot any of the above. Also known as: ai-factory, EDB Postgres AI Accelerator Pipelines.
metadata:
  aliases: [aidb, ai-factory]
  version: "7.7.0"
  postgres_versions: ["14", "15", "16", "17", "18"]
  language: sql
  platform: EDB Postgres AI (Sovereign AI / Hybrid Manager)
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension (Rust/pgrx) that provides native AI operations entirely through
SQL. **All AI processing stays inside the user's Postgres instance** — no mandatory external
dependency. The entire API is SQL functions in the `aidb` schema.

## Quick-Start Checklist

Before any AIDB task, confirm:
1. `shared_preload_libraries` includes `'aidb'` (and `'vchord'` for VectorChord indexes)
2. Extension is installed: `CREATE EXTENSION aidb CASCADE;`
3. At least one model is registered (see §2 below)

Run the pre-flight checker:
```bash
python3 scripts/aidb_check_env.py --dsn "postgresql://user:pass@host/db"
```

Or verify manually:
```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';
SHOW shared_preload_libraries;
```

---

## §1 — Installation

```sql
-- PostgreSQL 14–18; CASCADE installs pgvector and pgfs automatically
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;
```

**`postgresql.conf` (set before starting Postgres):**
```
shared_preload_libraries = 'aidb,vchord'
```

**GUC parameters:**

| Parameter | Type | Default | Restart? | Notes |
|---|---|---|---|---|
| `aidb.max_threads` | INT | CPUs/2 (1–1024) | **Yes** | Local model inference thread pool |
| `aidb.pipeline_error_warnings` | BOOL | true | No | Also emit errors to PostgreSQL log |

```sql
-- Change max_threads (restart required):
ALTER SYSTEM SET aidb.max_threads = 8;

-- Toggle warnings (session-safe):
SET aidb.pipeline_error_warnings = false;
```

---

## §2 — Model Registration

Models are registered once and referenced everywhere by name. Credentials are stored in
`pg_user_mappings` — **never returned** by `aidb.list_models()` or `aidb.get_model()`.

```sql
-- Local model (no API key, weights on disk)
SELECT aidb.create_model('my_bert', 'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB);

-- OpenAI embeddings
SELECT aidb.create_model('my_embed', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- OpenAI completions (for agents / summarization)
SELECT aidb.create_model('my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...'));

-- Generic OpenAI-compatible endpoint (vLLM, Ollama, etc.)
SELECT aidb.create_model('my_local_llm', 'completions',
    config => aidb.completions_config(model => 'mistral-7b',
        url => 'http://vllm-host:8000/v1/chat/completions'));

-- Testing — deterministic, no external service
SELECT aidb.create_model('test_model', 'dummy');

-- List models (no credentials shown)
SELECT * FROM aidb.list_models();
```

**→ Full provider table:** [See references/model-adapters.md](references/model-adapters.md)

---

## §3 — Pipelines

A pipeline reads from a source (table or volume), transforms data through ordered steps, and
writes embeddings or text to a destination table.

**Key constraints (enforced at create time):**
- Pipeline name: **max 46 characters**
- Steps per pipeline: **max 10**
- Destination table: **must not exist** (created automatically)
- Step sequence: validated for type compatibility at create time

### Generate Pipeline SQL (Agent Tool)

```bash
python3 scripts/aidb_pipeline_builder.py \
    --name my_pipeline \
    --source documents \
    --source-key-column id \
    --source-data-column content \
    --steps ParsePdf ChunkText KnowledgeBase \
    --model my_embed \
    --mode Background
```

### Pipeline Templates

**Embed text directly:**
```sql
SELECT aidb.create_pipeline(
    name => 'embed_articles', source => 'articles',
    source_key_column => 'id', source_data_column => 'body',
    auto_processing => 'Background',
    step_1 => 'KnowledgeBase',
    step_1_options => aidb.knowledge_base_config(
        model => 'my_embed', data_format => 'Text',
        distance_operator => 'Cosine',
        vector_index => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);
```

**PDF → chunks → embeddings:**
```sql
SELECT aidb.create_pipeline(
    name => 'index_pdfs', source => 'pdf_docs',
    source_key_column => 'id', source_data_column => 'pdf_bytes',
    auto_processing => 'Disabled',
    step_1 => 'ParsePdf',
    step_1_options => aidb.pdf_parse_config(method => 'Structured'),
    step_2 => 'ChunkText',
    step_2_options => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_3 => 'KnowledgeBase',
    step_3_options => aidb.knowledge_base_config(model => 'my_embed', data_format => 'Text',
        distance_operator => 'Cosine')
);
```

**→ More templates:** [See assets/sql_cookbook.sql](assets/sql_cookbook.sql)  
**→ Step sequencing rules:** [See references/step-operations.md](references/step-operations.md)

### Run / Manage Pipelines

```sql
SELECT aidb.run_pipeline('my_pipeline');                      -- manual trigger
SELECT aidb.run_pipeline('my_pipeline', force_sync => TRUE);  -- re-process all rows
SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Background');
SELECT aidb.list_pipelines();
SELECT aidb.delete_pipeline('my_pipeline', cascade => FALSE); -- cascade => TRUE drops dest table
```

### Auto-Processing Mode Decision

| Mode | When to use |
|---|---|
| `Live` | Real-time; blocks writer until step completes |
| `Background` | High throughput, async batch processing |
| `Disabled` | Bulk backfills, testing, on-demand only |

**→ Decision guidance:** [See references/decision-trees.md](references/decision-trees.md)

### Similarity Search (after KnowledgeBase pipeline runs)

```sql
SELECT source_id, embedding <=> aidb.encode_text_query('my query', 'my_embed') AS dist
FROM my_destination_table
ORDER BY dist
LIMIT 10;
```

---

## §4 — Semantic Knowledge Base

Indexes PostgreSQL schema metadata (table names, column names, comments) for natural language
discovery. Used as the foundation for text-to-SQL and schema-aware agents.

```sql
-- Create KB over one or more schemas
SELECT aidb.create_semantic_kb('my_kb', 'my_embed',
    ARRAY['public', 'analytics'], 'Live');

-- Refresh manually (Disabled mode)
SELECT aidb.refresh_semantic_kb('my_kb');

-- Search: tables, columns, metadata
SELECT * FROM aidb.get_tables('my_kb', 'customer orders', min_similarity => 0.8, limit => 5, offset => 0);
SELECT * FROM aidb.get_columns('my_kb', 'email address', min_similarity => 0.8, limit => 5, offset => 0);
SELECT * FROM aidb.get_metadata('my_kb', 'purchase history', min_similarity => 0.7, limit => 10, offset => 0);

-- Combined schema + alias search (v7.5.0+)
SELECT * FROM aidb.semantic_kb_search('show revenue by customer', 'my_kb',
    top_k => 10, sources => ARRAY['schema', 'alias'], min_similarity => 0.7);
```

**Similarity threshold guidance:** 0.9 = exact | **0.8 = default** | 0.5–0.7 = broad

---

## §5 — Semantic Aliases

Parameterized SQL queries discoverable by natural language search. Agents and applications find
and execute them without knowing query names in advance.

```sql
-- Define an alias
SELECT aidb.create_semantic_alias(
    'top_customers_by_revenue',
    'Show top customers ranked by total revenue',
    'SELECT customer_id, sum(total) AS revenue FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'Number of customers to return')),
    'my_embed'
);

-- Discover aliases by natural language
SELECT * FROM aidb.search_semantic_aliases('my_embed', 'show top customers by revenue',
    min_similarity => 0.7, limit => 5, offset => 0);

-- Execute by name
SELECT aidb.execute_semantic_alias('top_customers_by_revenue', '{"n": 10}'::JSONB);
```

---

## §6 — Standalone AI Functions

Use these without building a pipeline:

```sql
-- Embeddings
SELECT aidb.encode_text('Hello world', 'my_embed');
SELECT aidb.encode_text_batch(ARRAY['text1', 'text2'], 'my_embed');
SELECT aidb.encode_text_query('search query', 'my_embed');  -- query-side for bi-encoders

-- Reranking
SELECT index, score FROM aidb.rerank_text('my query',
    ARRAY['candidate1', 'candidate2', 'candidate3'], 'my_rerank_model') ORDER BY score DESC;

-- Text chunking
SELECT part_id, value FROM aidb.chunk_text('Long text...',
    aidb.chunk_text_config(desired_length => 256, overlap_length => 32));

-- Summarization
SELECT aidb.summarize_text('Long document...', aidb.summarize_text_config(model => 'my_llm'));

-- Document parsing
SELECT aidb.parse_pdf(pg_read_binary_file('/path/to/doc.pdf'),
    aidb.pdf_parse_config(method => 'Structured'));
SELECT aidb.parse_html(convert_to('<html>...</html>', 'UTF8'), aidb.html_parse_config());
SELECT aidb.perform_ocr(pg_read_binary_file('/path/to/image.png'), 'my_ocr_model');
```

---

## §7 — Agent Hub

AI agents that use tools (catalog discovery, SQL queries, MCP calls) in a reasoning loop.

```sql
-- Create an agent
SELECT aidb.create_agent('db_assistant',
    'You are a helpful database assistant.',
    'my_llm',
    tools => NULL,               -- NULL = all tools available
    max_iterations => 15,
    budget_strategy => 'attempt_complete',
    read_only => false
);

-- Single-turn conversation (NEVER raises — always check error column)
SELECT message, error, conversation_id
FROM aidb.agent_converse('db_assistant', 'What tables exist in the public schema?');

-- Multi-turn
SELECT conversation_id FROM aidb.start_agent_session('db_assistant') \gset
SELECT message, error FROM aidb.agent_converse('db_assistant',
    'How many rows in the orders table?', conversation_id => :'conversation_id');

-- View conversation log
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';
```

**→ Full agent reference:** [See references/agent-hub-reference.md](references/agent-hub-reference.md)

### SQL Tools for Agents

```sql
SELECT aidb.create_sql_tool('list_orders', 'List recent orders for a customer',
    'SELECT id, total FROM orders WHERE customer_id = ${customer_id} LIMIT ${n}',
    aidb.params(aidb.param('customer_id', 'integer', 'Customer ID'),
                aidb.param('n', 'integer', 'Max rows')),
    read_only => true);

SELECT aidb.run_tool('list_orders', '{"customer_id": 42, "n": 5}'::JSONB);
SELECT * FROM aidb.tools;  -- view all available tools
```

---

## §8 — Volume Management (Object Storage)

```sql
SELECT aidb.create_volume('my_bucket', 's3', 's3://my-bucket/prefix/', 'Bytes');
SELECT * FROM aidb.list_volumes();
SELECT * FROM aidb.list_volume_content('my_bucket');
SELECT aidb.delete_volume('my_bucket');

-- Use volume as pipeline source (instead of table name)
SELECT aidb.create_pipeline(name => 'process_s3_docs', source => 'my_bucket', ...);
```

---

## §9 — Diagnostics & Troubleshooting

### Run Full Diagnostic

```bash
python3 scripts/aidb_diagnose.py --dsn "postgresql://user:pass@host/db"
```

### Pipeline Error Inspection

```sql
-- Find error log table name
SELECT name, error_log_table FROM aidb.list_pipelines();
-- Query it (replace <error_log_table> with actual name):
SELECT * FROM aidb.<error_log_table> ORDER BY last_seen_at DESC LIMIT 20;
-- Re-queue failed rows
SELECT aidb.requeue_pipeline_errors('my_pipeline');
```

### Background Worker Not Running

```sql
SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type LIKE '%background worker%';
```
If empty: verify `shared_preload_libraries = 'aidb'` and restart PostgreSQL.

### Agent Errors

```sql
-- agent_converse never raises; check error column
SELECT message, error FROM aidb.agent_converse('my_agent', 'test');
-- Enable debug output
SELECT message FROM aidb.agent_converse('my_agent', 'test', debug => true);
```

**→ Decision trees for common issues:** [See references/decision-trees.md](references/decision-trees.md)

---

## §10 — Hard Constraints (Never Violate)

| Constraint | Value |
|---|---|
| Pipeline name max length | **46 characters** |
| Steps per pipeline | **max 10** |
| Destination table at create time | **must not exist** (auto-created) |
| Step sequence validation | at `create_pipeline()` — incompatible sequences rejected immediately |
| Model validation | at `create_model()` by default; use `validate => false` to defer |
| `aidb.max_threads` change | **requires PostgreSQL restart** |
| Credentials visibility | **never** returned by `list_models()` / `get_model()` |
| Agent reasoning iterations | max **25** per turn |
| Agent delegation depth | max **11** levels |
| Semantic KB `top_k` | must be **≥ 1** |
| Do NOT version-bump | Never change `Cargo.toml` version unless explicitly instructed |
| Do NOT use deprecated modules | `knowledge_base_registry.rs`, `knowledge_base_pipeline*` are deprecated |

---

## Reference Index

| File | When to read |
|---|---|
| [references/REFERENCE.md](references/REFERENCE.md) | Full architecture, GUCs, pipeline lifecycle, step sequencing, Agent Hub safety limits |
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures for all `aidb` schema functions |
| [references/model-adapters.md](references/model-adapters.md) | All 19+ model providers, their capabilities, and configuration examples |
| [references/step-operations.md](references/step-operations.md) | Pipeline step types, input/output type matrix, valid sequencing patterns |
| [references/agent-hub-reference.md](references/agent-hub-reference.md) | Agent lifecycle, tools hub, read-only enforcement, role model, troubleshooting |
| [references/decision-trees.md](references/decision-trees.md) | Decision trees for modes, distances, providers, sequences; safety checklist |
| [assets/sql_cookbook.sql](assets/sql_cookbook.sql) | 11-section ready-to-run SQL cookbook covering all AIDB functional areas |
| [assets/quick_reference.md](assets/quick_reference.md) | One-page cheatsheet: step chains, provider picker, constraints, operators |
| [scripts/aidb_pipeline_builder.py](scripts/aidb_pipeline_builder.py) | CLI tool: validates step sequences and generates `create_pipeline()` SQL |
| [scripts/aidb_diagnose.py](scripts/aidb_diagnose.py) | Full diagnostic: extension health, models, pipelines, KBs, smoke test |
| [scripts/aidb_check_env.py](scripts/aidb_check_env.py) | Pre-flight: PostgreSQL version, shared_preload_libraries, extensions, GUCs |