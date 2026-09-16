# AIDB Agent Reference

This document provides deep technical detail for agents operating the AIDB PostgreSQL extension. Read the section relevant to your current task; you do not need to read the entire document upfront.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Installation & Prerequisites](#installation--prerequisites)
3. [GUC Parameters](#guc-parameters)
4. [Pipeline Lifecycle In Depth](#pipeline-lifecycle-in-depth)
5. [Step Sequencing Rules](#step-sequencing-rules)
6. [Model Registry Detail](#model-registry-detail)
7. [Semantic Knowledge Base Detail](#semantic-knowledge-base-detail)
8. [Semantic Aliases Detail](#semantic-aliases-detail)
9. [Agent Hub Detail](#agent-hub-detail)
10. [Tools Hub Detail](#tools-hub-detail)
11. [Volume Management Detail](#volume-management-detail)
12. [Error Handling & Diagnostics](#error-handling--diagnostics)
13. [Safety Rules & Constraints Summary](#safety-rules--constraints-summary)

---

## Architecture Overview

AIDB (v7.7.0) is a Rust/pgrx PostgreSQL extension. It is the **AI Accelerator Pipelines** component of EDB Postgres AI ("AI Factory"). All AI processing stays inside the user's Postgres instance.

### Three Layers

| Layer | Crates | Role |
|---|---|---|
| Extension | `src/` (root pgrx crate) | PostgreSQL glue, GUC registration, `#[pg_extern]` functions |
| Pipeline | `src/pipeline_common/`, `src/bgworker/` | Data transformation engine, state tracking, background workers |
| Model | `aidb-model/` (pure Rust, no pgrx) | 19+ ML model adapters |

### Pure Rust Sub-crates (no PostgreSQL dependency)

- `aidb-model/` — ML model adapters
- `aidb-extract/` — HTML/PDF parsing
- `aidb-tools/` — Tools Hub business logic, builds invocation plans
- `aidb-agents/` — Agent Hub reasoning loop
- `aidb-memory/` — Agent memory subsystem

### Envelope Types (Data Container)

Data flowing through pipeline steps is typed as one of: `Text`, `Bytes`, `Vector`. Steps validate envelope compatibility at pipeline creation time (not at runtime).

---

## Installation & Prerequisites

```sql
-- Install on PostgreSQL 14–18
CREATE EXTENSION aidb CASCADE;

-- Required shared_preload_libraries (set before restart):
-- shared_preload_libraries = 'aidb,vchord'
-- vchord is required for VectorChord index support
```

**Dependencies loaded by CASCADE:** `pgvector` (for vector storage), `pgfs` (for object storage volumes).

---

## GUC Parameters

| Parameter | Type | Default | Restart | Notes |
|---|---|---|---|---|
| `aidb.max_threads` | INTEGER | CPUs/2 (min 1, max 1024) | **Yes** | Thread pool for local model inference |
| `aidb.max_io_threads` | INTEGER | — | Yes | Async I/O thread pool |
| `aidb.pipeline_error_warnings` | BOOLEAN | true | No | Emit per-error WARNING to PostgreSQL log; errors always persist to error log table |
| `aidb.agent_session_source` | ENUM | `action_log` | No | `action_log` or `memory` — where agent history is read from |
| `aidb.agent_memory_namespace` | TEXT | `pg_agents` | No | Memory namespace for agent session capture |

Set GUCs:
```sql
SET aidb.pipeline_error_warnings = false;
-- For max_threads, requires ALTER SYSTEM + restart:
ALTER SYSTEM SET aidb.max_threads = 8;
```

---

## Pipeline Lifecycle In Depth

### State Table

Each pipeline has a per-pipeline state table: `aidb.aidb_pipeline_state_<pipeline_id>`. Under PGD (BDR), the suffix becomes `_<pipeline_id>_<pgd_node_group>`. This table tracks: pending/in-progress/completed/failed rows. The background worker polls state tables for batch processing.

### Auto-Processing Modes

| Mode | Trigger | Use Case |
|---|---|---|
| `Live` | Database trigger fires synchronously on INSERT/UPDATE | Real-time, low-latency; blocks writer until complete |
| `Background` | Background worker polls state table in batches | High throughput, async; writer not blocked |
| `Disabled` | Manual `aidb.run_pipeline()` only | Bulk backfills, testing, on-demand |

### Listing Pipelines and Their Status

```sql
-- All pipeline metadata
SELECT * FROM aidb.list_pipelines();

-- Single pipeline detail
SELECT * FROM aidb.get_pipeline('my_pipeline');
```

### Updating Pipelines

`aidb.update_pipeline()` allows changing `auto_processing` mode, `destination`, `batch_size`, and `sync_interval`. You cannot change steps or source in place — delete and recreate the pipeline.

### Error Log

Each pipeline has a per-pipeline error log table. Query it by inspecting the pipeline metadata for the error log table name:
```sql
-- The error log table is referenced in the pipeline metadata
SELECT * FROM aidb.list_pipelines();
-- Then query the specific error table, e.g.:
-- SELECT * FROM aidb.pipeline_error_log_<pipeline_id>;
```

---

## Step Sequencing Rules

Steps are validated at `create_pipeline()` time. Incompatible sequences are rejected immediately.

### Input/Output Type Matrix

| Step | Input Envelope | Output Envelope |
|---|---|---|
| `ChunkText` | Text | Text (1:N — multiple rows per input) |
| `SummarizeText` | Text | Text (1:1) |
| `ParseHtml` | Bytes | Text |
| `ParsePdf` | Bytes | Text |
| `PdfToImage` | Bytes | Bytes (1:N — one row per page) |
| `PerformOcr` | Bytes (image) | Text |
| `KnowledgeBase` | Text or Bytes (image) | Vector (stored to destination) |

### Valid Pipeline Patterns (Examples)

```
Text column → KnowledgeBase                          (embed text directly)
Text column → ChunkText → KnowledgeBase              (chunk then embed)
Text column → SummarizeText → KnowledgeBase          (summarize then embed)
BYTEA column → ParsePdf → ChunkText → KnowledgeBase  (PDF → text → chunks → embeddings)
BYTEA column → ParseHtml → ChunkText → KnowledgeBase (HTML → text → chunks → embeddings)
BYTEA column → PdfToImage → PerformOcr               (PDF → images → OCR text)
BYTEA column → ParsePdf                              (just extract text from PDF)
```

---

## Model Registry Detail

### Credential Storage

API keys and secrets are stored in `pg_user_mappings` — not in plain-text catalog tables. `aidb.list_models()` and `aidb.get_model()` never return credential fields.

### Model Capabilities by Provider

**Local (no API key):**
- `bert_local`, `clip_local`, `t5_local` — text/image embeddings
- `llama_instruct_local`, `smollm2_local` — text completions
- `llamacpp_embeddings`, `llamacpp_generate`, `llamacpp_reranking`, `llamacpp_ocr` — via llama.cpp

**OpenAI-compatible:**
- `openai_embeddings`, `openai_completions` — OpenAI API
- `embeddings`, `completions` — generic, specify `url` for custom endpoint
- `nim_embeddings`, `nim_completions`, `nim_clip`, `nim_paddle_ocr`, `nim_reranking` — NVIDIA NIM

**Hosted third-party:**
- `gemini` — Google Gemini
- `hf_tei`, `hf_tei_reranking` — HuggingFace TEI
- `openrouter_chat`, `openrouter_embeddings` — OpenRouter

**Agentic (for Agent Hub):**
- `anthropic_messages`, `anthropic_messages_azure`, `anthropic_messages_bedrock`
- `openai_responses`, `openai_responses_azure`

**Testing:**
- `dummy` — deterministic, no external service required. Zero vectors for embeddings, canned text for completions.

### Validate vs. No-Validate

By default, `create_model()` validates the model at registration time. Pass `validate => false` to skip (useful in pipelines and tests):

```sql
SELECT aidb.create_model('my_model', 'dummy', validate => false);
```

---

## Semantic Knowledge Base Detail

### What Gets Embedded

The Semantic KB vectorizes PostgreSQL **schema metadata**: table names, column names, comments, and type definitions from the specified schemas. Users write `COMMENT ON TABLE/COLUMN` to enrich the index.

### Auto-Processing

- `Live` — triggers automatically crawl the catalog on DDL changes
- `Background` — worker periodically re-crawls
- `Disabled` — manual `aidb.refresh_semantic_kb()` only

### Search Functions

All search functions return `(schema_name, relation_name, column_name, entity_type, definition, comment, similarity)` or a compatible subset.

**Similarity guidance:**
- `min_similarity => 0.9` — near-exact matches only
- `min_similarity => 0.8` — good default (recommended)
- `min_similarity => 0.5` — broad exploration

### Combined Search (v7.5.0+)

```sql
-- Search both schema entities and semantic aliases in one ranked list
SELECT * FROM aidb.semantic_kb_search(
    'query text',
    'my_kb',          -- optional when only one KB exists
    top_k => 10,
    sources => ARRAY['schema', 'alias'],  -- filter sources
    entity_types => ARRAY['Table', 'View', 'Column'],  -- filter entity types
    min_similarity => 0.7
);
```

---

## Semantic Aliases Detail

Semantic Aliases are parameterized SQL queries with natural language descriptions. Agents and applications discover and execute them via semantic search — no hardcoded query names needed.

### Parameter Declaration

```sql
-- Build parameter list
SELECT aidb.alias_params(
    aidb.alias_param('limit_n', 'integer', 'Maximum number of results'),
    aidb.alias_param('status', 'text', 'Filter by status', ARRAY['active', 'inactive'])
);
```

### Execution

```sql
-- Execute by name
SELECT aidb.execute_semantic_alias(
    'alias_name',
    '{"limit_n": 10, "status": "active"}'::JSONB
);

-- Search for relevant aliases
SELECT * FROM aidb.search_semantic_aliases(
    'my_kb_model',
    'show top customers by revenue',
    min_similarity => 0.7,
    limit => 5,
    offset => 0
);
```

---

## Agent Hub Detail

### Core Concepts

- **Agent** — named configuration with instructions, model, tools, delegates, and budgets
- **Turn** — one `agent_converse()` call
- **Conversation** — a series of turns sharing a `conversation_id`
- **Task** — one conversation turn tracked in `aidb_internal.agent_task_queue`
- **Action** — individual reasoning steps recorded in `aidb_internal.action_log`

### Safety Limits

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Prevent infinite loops |
| `MAX_UNKNOWN_ERROR_FAILURES` | 3 | Stop on repeated unclassifiable errors |
| `MAX_RATE_LIMIT_RETRIES` | 5 | Don't retry rate-limited services forever |
| `MAX_REPEATED_FAILED_TOOL_CALL_OCCURRENCES` | 3 | Stop if same tool call keeps failing |
| `MAX_REPEATED_SUCCESSFUL_TOOL_CALL_OCCURRENCES` | 2 | Detect loops of successful calls |
| `MAX_DELEGATION_DEPTH` | 11 | Prevent recursive delegation explosion |

### Budget Strategies

| Strategy | On Exceeding Budget |
|---|---|
| `ignore` | Log warning, keep going |
| `error` | Halt, return error |
| `summarize` | Ask model for closing summary, then finish |
| `attempt_complete` | Grant 3 extra "finalize now" iterations |

### Read-Only Mode

Read-only agents (`read_only => true`) or agents on replica PostgreSQL instances use `SET LOCAL transaction_read_only = on` inside a discarded subtransaction. MCP tools are always blocked in read-only mode.

### Viewing Conversation History

```sql
-- View all turns in a conversation
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- Get a specific conversation
SELECT * FROM aidb.get_conversation('<uuid>');

-- View running tasks
SELECT * FROM aidb.agent_tasks;
```

---

## Tools Hub Detail

### Tool Resolution Order

When `run_tool()` is called, tools are resolved in this priority order:
1. **Native tools** — built-in Rust functions
2. **SQL tools** — user-defined via `create_sql_tool()`
3. **MCP tools** — imported from external MCP servers

### Native Tools

List built-in native tools:
```sql
SELECT * FROM aidb.list_native_tools();
```

Includes catalog discovery tools: `aidb.catalog_list_schemas`, `aidb.catalog_list_objects`, `aidb.catalog_get_object_details`, `aidb.catalog_list_relations`, `aidb.catalog_list_sequences`, `aidb.catalog_list_extensions`.

Memory search tool: `aidb.memory_search(query, max_results)` — uses current session's namespace and `session_user` as scope.

### MCP Tool Cache

MCP tool descriptors are cached and age out after 60 minutes. Refresh manually:
```sql
SELECT aidb.refresh_mcp_tools('my_mcp_server');
```

### Creating SQL Tools

```sql
SELECT aidb.create_sql_tool(
    'list_orders',
    'List recent orders for a customer',
    'SELECT id, total FROM orders WHERE customer_id = ${customer_id} LIMIT ${n}',
    aidb.params(
        aidb.param('customer_id', 'integer', 'Customer ID'),
        aidb.param('n', 'integer', 'Max results')
    ),
    read_only => true,
    return_type_hint => 'table'
);
```

---

## Volume Management Detail

Volumes connect AIDB pipelines to object storage (S3, Azure Blob, GCS, local filesystem via pgfs).

```sql
-- Create a volume pointing to S3
SELECT aidb.create_volume(
    'my_s3_bucket',
    's3',                         -- storage_location
    's3://my-bucket/prefix/',     -- path
    'Bytes'                       -- data_type: 'Text' | 'Bytes' | 'Image'
);

-- Use volume name as pipeline source
SELECT aidb.create_pipeline(
    name => 'process_docs',
    source => 'my_s3_bucket',    -- volume name instead of table name
    ...
);
```

---

## Error Handling & Diagnostics

### Pipeline Errors

Errors are always stored in the per-pipeline error log table regardless of `aidb.pipeline_error_warnings`. The error log table name is visible in `aidb.list_pipelines()`.

```sql
-- View pipeline errors (find table name first)
SELECT error_log_table FROM aidb.list_pipelines() WHERE name = 'my_pipeline';
-- Then: SELECT * FROM aidb.<error_log_table_name>;
```

### Agent Errors

`agent_converse()` never raises. Check the `error` column in the return value:
```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'user prompt');
```

Tool Hub functions (`run_tool()`, etc.) raise normally on failure.

### Background Worker Issues

From `agent_docs/debugging.md`: Check PostgreSQL logs for background worker messages. The dispatcher logs which databases and pipelines it's processing. Set `aidb.pipeline_error_warnings = true` to see per-error warnings in the log.

---

## Safety Rules & Constraints Summary

| Constraint | Value | Notes |
|---|---|---|
| Max pipeline name length | 46 characters | Enforced at create time |
| Max steps per pipeline | 10 | Enforced at create time |
| Destination table | Must not exist | Create pipeline creates the table |
| Step sequencing | Validated at create time | Incompatible types rejected immediately |
| Model validation | At create time (default) | Use `validate => false` to defer |
| `max_threads` change | Requires PostgreSQL restart | Cannot hot-reload |
| Credentials | Never returned by list/get | Stored in `pg_user_mappings` |
| Agent delegation depth | 11 levels | Hard limit |
| Agent reasoning iterations | 25 per turn | Hard limit |
| Semantic KB search `top_k` | Must be ≥ 1 | Validated; 0 returns error |

**Never do:**
- Drop the destination table while a pipeline is running
- Change `max_threads` without planning for a restart
- Assume credentials are visible via `list_models()`
- Use `knowledge_base_registry.rs` / `knowledge_base_pipeline*` (deprecated, use `pipeline_common`)
- Bump the version in `Cargo.toml` unless explicitly instructed
