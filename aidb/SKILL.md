---
name: aidb
description: >
  Skill for operating AIDB (also known as AI Accelerator Pipelines / AI Factory), the EDB PostgreSQL
  extension that brings native AI capabilities into Postgres entirely through SQL. Use this skill when
  a user asks about: registering AI models in Postgres; building data pipelines that chunk, embed,
  summarize, OCR, or parse documents; performing semantic search over documents or over database
  schema metadata; building in-database AI agents with governed tool use; exposing AIDB tools to
  external MCP clients; managing agent memory across sessions; or configuring any aidb.*/edb.* GUC.
  Aliases: aidb, ai-factory. Requires PostgreSQL 14–18 with CREATE EXTENSION aidb CASCADE.
metadata:
  aliases:
    - aidb
    - ai-factory
  supports:
    - postgresql: "14-18"
    - extension: "aidb"
  references:
    - references/function-reference.md
    - references/step-operations.md
    - references/model-adapters.md
    - references/decision-guide.md
    - references/agent-hub-deep-dive.md
    - references/governance-and-security.md
    - references/mcp-endpoint-reference.md
    - references/troubleshooting.md
    - references/naming-and-types-cheatsheet.md
    - assets/cookbook.sql
  scripts:
    - scripts/diagnose_aidb.py
    - scripts/setup_aidb_model.py
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that brings AI processing natively into Postgres. All interaction
is through SQL. It is the AI Accelerator Pipelines component of EDB Postgres AI ("AI Factory").
Data never leaves the user's database unless a remote model API is configured.

**Install:** `CREATE EXTENSION aidb CASCADE;`  
**Preload** (required for background workers, agents, and MCP): add `aidb` to `shared_preload_libraries`, then restart PostgreSQL.

---

## Calling Conventions — Read Before Writing Any SQL

These rules prevent the most common errors. Apply them to every query you generate.

1. **Model functions take the model name first.**  
   `aidb.encode_text('model', input)`, `generate_text('model', input)`, `rerank_text('model', query, inputs)`.  
   Swapped arguments silently look up the input text as a model name and fail with "Model not found".

2. **Embeddings return `real[]`, not `vector`.** Cast with `::vector` before pgvector operators or storing into a `vector` column.

3. **Standalone functions take `options JSON`, not `jsonb`.**  
   `chunk_text`, `summarize_text`, `parse_html`, `parse_pdf`, `perform_ocr`, `pdf_to_image`,  
   `generate_text`, `generate_text_batch` all take `JSON`.  
   Every `aidb.*_config()` helper returns `jsonb` — append `::json`:  
   ```sql
   SELECT * FROM aidb.chunk_text(body, aidb.chunk_text_config(200, 250)::json);
   ```
   Pipeline `step_N_options` take `jsonb` directly — no cast needed there.

4. **Embed a query once, never inside `ORDER BY`.**
   ```sql
   WITH q AS (SELECT aidb.encode_text_query('my_model', 'refund policy')::vector AS v)
   SELECT d.id FROM docs d, q ORDER BY d.embedding <=> q.v LIMIT 5;
   ```

5. **`create_pipeline` and `create_model`: always use named arguments.** Positional order is tricky; named args are unambiguous. See [references/function-reference.md](references/function-reference.md).

6. **Secrets go in `credentials`, never in `config`.** `create_model` rejects `api_key` or `basic_auth` in `config`:
   ```sql
   SELECT aidb.create_model('m', 'openai_embeddings',
       config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
       credentials => '{"api_key": "sk-..."}'::jsonb);
   ```

7. **`retrieve_text` / `retrieve_key` first argument is the schema-qualified destination table**, not the pipeline name:
   ```sql
   SELECT key, value, distance FROM aidb.retrieve_text('public.pipeline_docs_kb', 'query', 5);
   ```

8. **`agent_converse` never raises.** It returns an `error` column. Always check it.

9. **`decode_text`/`decode_text_batch` are deprecated.** Use `generate_text`/`generate_text_batch`.

For a complete list of name errors and type gotchas, see [references/naming-and-types-cheatsheet.md](references/naming-and-types-cheatsheet.md).

---

## Quick-Decision Guide

> For the full flowchart, see [references/decision-guide.md](references/decision-guide.md).

| User's goal | Feature to use |
|---|---|
| Embed documents and search them (RAG/semantic search) | `ChunkText → KnowledgeBase` pipeline + `aidb.retrieve_text` |
| Transform data automatically (chunk, OCR, summarize, embed) | Pipeline (`create_pipeline`) |
| Search schema by natural language (find tables/columns) | Semantic KB (`create_semantic_kb`) |
| Build an AI agent with database tools | Agent Hub (`create_agent`, `agent_converse`) |
| Run a one-off AI operation (embed, summarize, OCR) | Standalone AI functions |
| Expose AIDB to an external MCP client (Claude, etc.) | MCP Endpoint |
| Constrain an agent to a database role | Purpose Registry |

---

## 1. Installation & Verification

```sql
CREATE EXTENSION aidb CASCADE;
-- Verify
SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';
-- List all aidb/edb GUCs
SELECT name, setting, short_desc FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'edb.%' ORDER BY name;
```

Run the diagnostic script for a full status check:
```bash
python3 scripts/diagnose_aidb.py [--host H] [--port P] [--dbname DB] [--user U]
```

---

## 2. Model Management

### Register a model

```sql
-- OpenAI embeddings
SELECT aidb.create_model(
    name        => 'my_embedder',
    provider    => 'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-..."}'::jsonb
);

-- Local BERT (no API key, downloads from HuggingFace on first use)
SELECT aidb.create_model('my_bert', 'bert_local',
    config => '{"hf_model": "sentence-transformers/all-MiniLM-L6-v2"}'::jsonb);

-- Test/CI model (deterministic, no server)
SELECT aidb.create_model('test_model', 'dummy');

-- Register without probing the endpoint (useful when endpoint is not reachable yet)
SELECT aidb.create_model('m', 'openai_completions',
    config      => aidb.completions_config(model => 'gpt-4o'),
    credentials => '{"api_key": "sk-..."}'::jsonb,
    validate    => false);
```

**Use the helper script** to generate registration SQL for any provider:
```bash
python3 scripts/setup_aidb_model.py --model-name my_embedder --provider openai_embeddings \
    --model-id text-embedding-3-small --api-key sk-... [--dry-run]
```

### Inspect and manage models

```sql
SELECT name, provider, functions FROM aidb.models;         -- list registered models
SELECT * FROM aidb.model_providers;                        -- list all available providers
SELECT aidb.validate_model('my_embedder');                 -- re-probe a registered model
SELECT aidb.delete_model('my_embedder');
```

For the full provider catalogue and configuration options, see [references/model-adapters.md](references/model-adapters.md).

### Reusable credentials

```sql
SELECT aidb.create_credential('openai_cred',
    credentials => '{"api_key": "sk-..."}'::jsonb,
    endpoint    => 'api.openai.com');
SELECT aidb.create_model('my_chat', 'openai_completions',
    config     => aidb.completions_config(model => 'gpt-4o-mini'),
    credential => 'openai_cred');
```

---

## 3. Pipelines

**Limits:** max 46-character name, max 10 steps.  
**Destination table** must not already exist (exception: a `KnowledgeBase` pipeline may attach to an existing KB table).

### Create a document search pipeline (most common pattern)

```sql
SELECT * FROM aidb.create_pipeline(
    name               => 'docs_kb',
    source             => 'public.documents',
    source_key_column  => 'id',
    source_data_column => 'body',
    auto_processing    => 'Disabled',   -- 'Live' | 'Background' | 'Disabled'
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2             => 'KnowledgeBase',
    step_2_options     => aidb.knowledge_base_config(
        model             => 'my_embedder',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

SELECT aidb.run_pipeline('docs_kb');   -- manual trigger when auto_processing = 'Disabled'

-- Search: pass the schema-qualified destination table
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_docs_kb', 'refund policy', topk => 5);
```

### Pipeline auto-processing modes

| Mode | Behavior | Notes |
|---|---|---|
| `'Live'` | Trigger on each INSERT/UPDATE/DELETE | Table source+destination only; not on WarehousePG |
| `'Background'` | Worker processes rows in batches | Needs `aidb` in `shared_preload_libraries` |
| `'Disabled'` | Manual: `SELECT aidb.run_pipeline('name')` | Always available |

### Inspect pipelines

```sql
SELECT name, auto_processing, source, destination FROM aidb.pipelines;
SELECT * FROM aidb.get_pipeline_metrics('docs_kb');
-- List all pipelines with status:
SELECT pipeline, "auto processing", "Status", "count(record errors)" FROM aidb.pipeline_metrics;
```

### Pipeline error logs

```sql
SELECT * FROM aidb.get_error_logs('docs_kb', p_limit => 20);
SELECT * FROM aidb.get_error_log_summary('docs_kb');
SELECT * FROM aidb.get_all_error_summaries();
```

### Other step types

For step-by-step configuration of `SummarizeText`, `ParseHtml`, `ParsePdf`, `PdfToImage`, `PerformOcr`, and `KnowledgeBase` (including type-compatibility rules), see [references/step-operations.md](references/step-operations.md).

**Common chains:**

| Chain | Source column |
|---|---|
| `ChunkText → KnowledgeBase (Text)` | TEXT |
| `ParsePdf → ChunkText → KnowledgeBase (Text)` | BYTEA or volume |
| `PdfToImage → PerformOcr → ChunkText → KnowledgeBase` | BYTEA or volume |
| `ParseHtml → ChunkText → KnowledgeBase (Text)` | TEXT (HTML) |

---

## 4. Standalone AI Functions

```sql
-- Embed text (returns real[])
SELECT aidb.encode_text('my_embedder', 'Hello, world!');
SELECT aidb.encode_text('my_embedder', 'Hello')::vector;  -- cast for pgvector

-- Batch embed
SELECT * FROM aidb.encode_text_batch('my_embedder', ARRAY['text 1', 'text 2']);

-- Generate text (use generate_text, not deprecated decode_text)
SELECT aidb.generate_text('my_chat', 'Summarize: ...', aidb.inference_config(temperature => 0.2)::json);

-- Chunk text (note ::json cast)
SELECT part_id, chunk FROM aidb.chunk_text(body, aidb.chunk_text_config(200, 50)::json);

-- Rerank candidates
SELECT id, text, logit_score FROM aidb.rerank_text('my_reranker', 'query', ARRAY['a','b','c'])
ORDER BY logit_score DESC;

-- Parse a PDF (returns TABLE(part_id, text), one row per page)
SELECT part_id, text FROM aidb.parse_pdf(pdf_bytes, aidb.pdf_parse_config()::json);

-- OCR an image
SELECT part_id, text FROM aidb.perform_ocr(img_bytes, aidb.ocr_config('my_ocr_model')::json);
```

---

## 5. Semantic Knowledge Base (Schema Discovery)

A Semantic KB indexes **schema metadata** (tables, views, columns, comments). It does not embed document rows — use a `KnowledgeBase` pipeline for that.

```sql
SELECT aidb.create_semantic_kb(
    name            => 'schema_kb',
    model           => 'my_embedder',
    schemas         => ARRAY['public', 'sales'],
    auto_processing => 'Live'
);

-- Hybrid search (best first call)
SELECT entity_type, schema_name, relation_name, column_name, score
FROM aidb.semantic_kb_search('customer orders', kb_name => 'schema_kb', top_k => 10);

-- Tables and views by meaning
SELECT schema_name, relation_name, entity_type, similarity
FROM aidb.get_entity_definitions('schema_kb', 'customer orders', top_k => 10);

-- Columns by meaning
SELECT relation_name, column_name, similarity
FROM aidb.get_metadata('schema_kb', 'email address', top_k => 10)
WHERE entity_type = 'Column';

-- Refresh manually (needed when auto_processing = 'Disabled')
SELECT aidb.refresh_semantic_kb('schema_kb');
```

> **Join routing safety:** Only routes through **explicitly approved** relationships. Never treat a candidate/unreviewed relationship as safe to route through.

---

## 6. Agent Hub

```sql
-- Create a SQL tool
SELECT aidb.create_sql_tool(
    'list_orders',
    'List recent orders for a customer',
    'SELECT id, total FROM orders WHERE customer_id = ${customer_id}::int ORDER BY created_at DESC LIMIT 5',
    aidb.tool_params(aidb.tool_param('customer_id', 'integer', 'The customer ID')),
    read_only => true
);

-- Create an agent
SELECT * FROM aidb.create_agent(
    name         => 'my_assistant',
    instructions => 'You are a helpful database assistant.',
    model        => 'my_chat',
    tools        => ARRAY['list_orders']
);

-- Converse — ALWAYS check the error column
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_assistant', 'Show orders for customer 42');

-- Continue a conversation
SELECT message, error
FROM aidb.agent_converse('my_assistant', 'Which order was largest?',
    conversation_id => '<prior_conversation_id>');

-- Inspect registered agents and tools
SELECT name, model, tools, purpose FROM aidb.agents;
SELECT name, tool_type, read_only FROM aidb.tools WHERE tool_type = 'native_tool' ORDER BY name;
```

**Budget strategies:** `'ignore'` | `'error'` | `'summarize'` | `'attempt_complete'` (default).  
**Hard limits:** 25 reasoning iterations, 11 delegation depth.  
For the full execution model, loop constants, and native tool catalog, see [references/agent-hub-deep-dive.md](references/agent-hub-deep-dive.md).

---

## 7. Governance — Purpose Registry

A purpose binds an agent to a PostgreSQL role. Requires `aidb_governance` role or superuser.

```sql
SELECT aidb.create_purpose('analyst_purpose', 'analyst_role', 'Read-only analytics access');

SELECT * FROM aidb.create_agent(
    name         => 'governed_agent',
    instructions => 'Read-only analytics assistant.',
    model        => 'my_chat',
    tools        => ARRAY['list_orders'],
    purpose      => 'analyst_purpose'
);

SELECT name, role, description FROM aidb.purpose_registry;
```

`delete_purpose` is a **soft delete** — the row is never physically removed because agents may still reference the name. For the complete security model, egress policy, OTel telemetry, and role model, see [references/governance-and-security.md](references/governance-and-security.md).

---

## 8. MCP Endpoint

The MCP endpoint exposes AIDB's Tools Hub to external clients (Claude, other agents) over streamable HTTP.

```sql
-- Enable (requires a PostgreSQL restart on first enable)
ALTER SYSTEM SET edb.endpoints_mcp_enabled = on;
-- After restart, further changes apply with:
SELECT pg_reload_conf();

-- Verify supervisor
SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type LIKE '%endpoint%';
```

Clients connect to `http://127.0.0.1:8765/mcp` with HTTP Basic credentials (pg_hba must use `scram-sha-256`, not `trust`/`peer`). For full GUC reference, OAuth setup, TLS, and troubleshooting, see [references/mcp-endpoint-reference.md](references/mcp-endpoint-reference.md).

---

## 9. Key GUCs

```sql
-- View all aidb/edb settings
SELECT name, setting, context, short_desc FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'edb.%' ORDER BY name;
```

| GUC | Default | Description |
|---|---|---|
| `aidb.max_threads` | `0` (half CPUs) | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | `on` | Emit per-error WARNING to PostgreSQL log |
| `aidb.agent_memory_enable_worker` | `off` | Enable Agent Memory background worker |
| `aidb.agent_session_source` | `action_log` | `action_log` or `memory` for Agent Hub session history |
| `aidb.egress_allowlist` | empty | Hostnames/CIDRs allowed for outbound calls |
| `aidb.allow_insecure_egress` | `off` | Allow plaintext HTTP to localhost/private IPs only |
| `aidb.otel_client` | `noop` | Telemetry: `noop`/`database`/`stdout`/`log`/`grpc` |
| `edb.endpoints_mcp_enabled` | `off` | Enable the MCP endpoint server |
| `edb.endpoints_mcp_port` | `8765` | MCP listen port |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `function does not exist` on `chunk_text(text, jsonb)` | Passed `jsonb` to a `JSON` parameter | Append `::json` to the config helper |
| `Model not found: <text_you_typed>` | Arguments swapped — text came first | Model name must be first argument |
| `Knowledge base not found: <name>` | Unqualified or pipeline-name passed to `retrieve_text` | Use `'schema.table'` |
| `The pipeline name is too long` | Name > 46 characters | Shorten the name |
| `Output type ... does not match input type` | Incompatible step sequence | Check [references/step-operations.md](references/step-operations.md) |
| `config must not contain "api_key"` | Secret placed in `config` | Move to `credentials => '{"api_key":"..."}'::jsonb` |
| Background pipeline not processing | Worker not running | Check `aidb` in `shared_preload_libraries`; `SELECT pid FROM pg_stat_activity WHERE backend_type LIKE 'aidb%'` |
| `agent_converse` returns no `message` but `error` is set | Agent error (bad model, unknown tool, budget) | Read the `error` column; fix the named issue |
| `egress to host ... is not permitted` | Egress allowlist set, host not included | Add host to `aidb.egress_allowlist` |

For comprehensive troubleshooting including agent loops, MCP, and Agent Memory, see [references/troubleshooting.md](references/troubleshooting.md).

---

## 11. Working Examples

The [assets/cookbook.sql](assets/cookbook.sql) file contains 13 runnable recipes covering:
- Setting up models (OpenAI, local, dummy)
- Standalone AI functions
- Document search pipeline (ChunkText + KnowledgeBase)
- PDF-to-image-to-OCR pipeline
- Semantic KB and semantic aliases
- SQL tools and agents
- Purpose-governed agents
- MCP tool import
- Pipeline error reading

---

## 12. What Not to Use

These names do not exist in AIDB. Never generate them:

- `aidb.list_pipelines()` → use `aidb.pipelines` view
- `aidb.get_pipeline()` → use `aidb.pipelines` view or `aidb.get_pipeline_metrics(name)`
- `aidb.create_model_server` → use `aidb.create_credential` + `aidb.create_model(..., credential =>)`
- `aidb.get_tables()` / `aidb.get_columns()` → use `aidb.get_entity_definitions` / `aidb.get_metadata`
- `smollm2_local` provider → use `llama_instruct_local` or `llamacpp_generate`
- `aidb.enable_memory_worker` → GUC is `aidb.agent_memory_enable_worker`
- `tool_names` parameter on `create_agent` → parameter is `tools`

See [references/naming-and-types-cheatsheet.md](references/naming-and-types-cheatsheet.md) for the full list.

---

## 13. AI Factory Platform Context

AIDB is one component of EDB Postgres AI:
- **pgvector** — vector search within Postgres
- **AIDB** — AI pipelines, agents, semantic KB, MCP endpoint (this skill)
- **Gen AI Builder** — Assistants, Tools, Rulesets, Threads
- **Model Serving** — GPU-accelerated KServe inference via EDB Hybrid Manager

AIDB runs standalone on any EDB Postgres AI distribution, or inside Hybrid Manager (Kubernetes) for full governance. Version: state cautiously — the checked-in source may be ahead of the last released version. Prefer "as of the current `aidb` source" over asserting a specific version number.