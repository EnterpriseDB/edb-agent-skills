---
name: aidb
description: >
  Skill for operating AIDB (EDB AI Accelerator Pipelines), the PostgreSQL extension that brings
  native AI capabilities into Postgres via SQL. Trigger this skill when a user wants to: register
  AI models; define data-transformation pipelines (embedding, OCR, PDF/HTML parsing, summarization);
  build a Semantic Knowledge Base for natural-language schema discovery; create text-to-SQL Semantic
  Aliases; define and converse with governed Agents backed by SQL and MCP tools; manage Agent Memory
  across sessions; configure governance via the Purpose Registry; expose AIDB's tool catalog to
  external MCP clients; or troubleshoot any of these subsystems. Also known as: ai-factory, EDB
  Postgres AI Accelerator Pipelines.
metadata:
  aliases:
    - aidb
    - ai-factory
  pg_versions: "14–18"
  install_sql: "CREATE EXTENSION aidb CASCADE;"
  schema: aidb
  deprecated_functions:
    - decode_text → generate_text
    - decode_text_batch → generate_text_batch
    - KnowledgeBase pipeline step → create_semantic_kb() or standalone encode_text()
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that delivers AI capabilities entirely through SQL. All AI processing
and storage stays inside the user's Postgres instance — no mandatory external dependency.

**Key principle:** Every AIDB operation is a SQL function call in the `aidb` schema (Agent Memory
functions are in `aidb_memory`). When in doubt, check the exact signature in
[references/function-reference.md](references/function-reference.md).

---

## 1. Installation & Prerequisites

```sql
-- Requires PostgreSQL 14–18; CASCADE installs pgvector and pgfs dependencies
CREATE EXTENSION aidb CASCADE;
```

For **Background** pipeline mode or the **MCP endpoint**, also add to `postgresql.conf` (restart required):
```
shared_preload_libraries = 'aidb,vchord'
```

**Versioning caution:** The version in `Cargo.toml`/`aidb.control` may be ahead of the last
finalized release. Say "as of the current aidb source" rather than asserting a specific version
number as a released version.

---

## 2. Feature Map

| User Goal | Primary Feature | Key Entry Point |
|---|---|---|
| Transform data with AI automatically | Pipeline | `aidb.create_pipeline()` |
| Natural-language schema search | Semantic KB | `aidb.create_semantic_kb()` |
| Discoverable parameterized queries | Semantic Aliases | `aidb.create_semantic_alias()` |
| AI assistant with tool use | Agent Hub | `aidb.create_agent()` + `aidb.agent_converse()` |
| One-off AI operations | Standalone functions | `aidb.encode_text()`, `aidb.generate_text()`, etc. |
| Register AI models | Model Management | `aidb.create_model()` |
| External MCP client access | MCP Endpoint | `edb.endpoints_mcp_*` GUCs + `edb-endpoints` |
| Role-scoped agent governance | Purpose Registry | `aidb.create_purpose()` |
| Cross-session agent memory | Agent Memory | `aidb_memory.init()` + GUC |

Use [references/decision-guide.md](references/decision-guide.md) to quickly identify the right
feature for any user request.

---

## 3. Model Registration (Always First)

Models are registered once and referenced by name everywhere else.

```sql
-- Testing (no external service, deterministic output)
SELECT aidb.create_model('test_model', 'dummy');

-- OpenAI embeddings
SELECT aidb.create_model(
    'my_embedder', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'),
    validate => true  -- test-connects at registration time
);

-- OpenAI completions
SELECT aidb.create_model(
    'my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...')
);

-- Generic OpenAI-compatible (vLLM, llama.cpp, etc.)
SELECT aidb.create_model(
    'local_llm', 'completions',
    config => aidb.completions_config(model => 'mistral-7b', url => 'http://host:8000/v1/chat/completions')
);

-- List registered models (credentials are NEVER returned)
SELECT name, provider FROM aidb.list_models();
```

**Credential options** (mutually exclusive):
- `credentials JSONB` — stored in `pg_user_mappings`, never returned by `list_models()`
- `credentials_env TEXT` — name of an allow-listed env var (prefix: `aidb.env_var_allowed_prefix`)
- `credentials_k8s_secret TEXT` — path of an allow-listed mounted K8s secret

**Egress:** Provider hostnames must be in the `aidb.egress_allowlist` GUC. TLS is enforced by
default (`aidb.allow_insecure_egress = false`).

See [references/model-adapters.md](references/model-adapters.md) for all 20+ providers.

---

## 4. Pipelines

### Constraints (enforced at creation time)
- **Max pipeline name: 46 characters**
- **Max steps per pipeline: 10**
- **Destination table must NOT already exist**
- **Steps must be type-compatible** — see [references/step-operations.md](references/step-operations.md)

### Create a Pipeline

```sql
SELECT aidb.create_pipeline(
    name               => 'doc_embeddings',       -- max 46 chars
    source             => 'my_documents',          -- table name or volume name
    source_key_column  => 'id',
    source_data_column => 'content',
    auto_processing    => 'Background',            -- 'Live' | 'Background' | 'Disabled'
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2             => 'KnowledgeBase',         -- deprecated; functional but discouraged for new work
    step_2_options     => aidb.knowledge_base_config(
        model             => 'my_embedder',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);
```

**⚠ KnowledgeBase step is deprecated.** For new embedding pipelines, use `ChunkText` in a pipeline
combined with standalone `aidb.encode_text()` calls or `aidb.create_semantic_kb()`.

### Auto-Processing Modes

| Mode | Behavior | Requirement |
|---|---|---|
| `Live` | Triggers run synchronously on INSERT/UPDATE | None (triggers added automatically) |
| `Background` | Async worker processes new rows in batches | `aidb` in `shared_preload_libraries` + restart |
| `Disabled` | Manual invocation only | None |

```sql
SELECT aidb.run_pipeline('doc_embeddings');                                    -- run manually
SELECT aidb.update_pipeline('doc_embeddings', auto_processing => 'Live');      -- change mode
SELECT aidb.delete_pipeline('doc_embeddings', cascade => true);                -- drop with state table
```

### Check Pipeline Errors

```sql
-- Find pipeline IDs
SELECT name, id FROM aidb.list_pipelines();

-- Read error log (substitute actual pipeline ID)
SELECT * FROM aidb_internal.pipeline_error_log_<id>
ORDER BY occurred_at DESC LIMIT 20;
```

See [references/step-operations.md](references/step-operations.md) for all step types and valid
step sequences. See [references/troubleshooting.md](references/troubleshooting.md) for common
failure modes.

---

## 5. Standalone AI Functions

```sql
-- Embeddings
SELECT aidb.encode_text('Hello world', 'my_embedder');           -- returns VECTOR
SELECT aidb.encode_text_query('search query', 'my_embedder');    -- query-side for bi-encoders
SELECT aidb.encode_text_batch(ARRAY['a','b','c'], 'my_embedder');-- returns VECTOR[]
SELECT aidb.encode_image(image_bytes, 'my_clip_model');          -- returns VECTOR

-- Text generation (PREFERRED names)
SELECT aidb.generate_text('Summarize: ...', 'my_llm');
SELECT aidb.generate_text_batch(ARRAY['prompt1','prompt2'], 'my_llm');
-- DEPRECATED (still work but emit warnings): decode_text, decode_text_batch

-- Text preparation
SELECT part_id, value FROM aidb.chunk_text('Long text...', aidb.chunk_text_config(desired_length => 512));
SELECT aidb.summarize_text('Long text...', aidb.summarize_text_config(model => 'my_llm'));
SELECT aidb.parse_pdf(pdf_bytes, aidb.pdf_parse_config(method => 'Structured'));
SELECT aidb.parse_html(html_bytes, aidb.html_parse_config(method => 'StructuredMarkdown'));
SELECT aidb.perform_ocr(image_bytes, 'my_ocr_model');  -- requires nim_paddle_ocr provider

-- Reranking
SELECT index, score
FROM aidb.rerank_text('my query', ARRAY['cand1','cand2','cand3'], 'my_reranker')
ORDER BY score DESC;
```

---

## 6. Semantic Knowledge Base

The Semantic KB vectorizes PostgreSQL schema metadata for natural-language discovery.
**This is the recommended path for new schema-embedding work** (not the deprecated KnowledgeBase step).

```sql
-- Create (Live mode re-embeds on DDL changes automatically)
SELECT aidb.create_semantic_kb('schema_kb', 'my_embedder', ARRAY['public', 'sales'], 'Live');

-- Search
SELECT schema_name, relation_name, entity_type, round(similarity::numeric, 3)
FROM aidb.get_tables('schema_kb', 'customer orders', 0.7, 10, 0)
ORDER BY similarity DESC;

SELECT schema_name, relation_name, column_name, round(similarity::numeric, 3)
FROM aidb.get_columns('schema_kb', 'email address', 0.7, 10, 0)
ORDER BY similarity DESC;

-- Refresh after schema changes (for Disabled mode)
SELECT aidb.refresh_semantic_kb('schema_kb');

-- Stats
SELECT * FROM aidb.semantic_kb_stats('schema_kb');
```

**Similarity thresholds:** 0.8 = good default; 0.9+ = near-exact only; 0.5–0.7 = broad exploration.

**Semantic Aliases** — discoverable parameterized queries:
```sql
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by total lifetime spending',
    'SELECT customer_id, SUM(total) AS ltv FROM orders GROUP BY customer_id ORDER BY ltv DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'Number of customers to return')),
    'schema_kb'
);
SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 10}'::JSONB);
```

**Join routing safety:** `aidb.suggest_joins` and `aidb.find_join_path` only route through
**explicitly approved** relationships. Never present an unreviewed relationship as safe to route.

---

## 7. Agent Hub

Agents are governed, tool-using conversational loops that run entirely in SQL.

```sql
-- Register a SQL tool
SELECT aidb.create_sql_tool(
    'recent_orders',
    'List recent orders for a customer',
    'SELECT id, total FROM orders WHERE customer_id = ${cid} ORDER BY created_at DESC LIMIT 5',
    aidb.tool_params(aidb.tool_param('cid', 'integer', 'Customer ID')),
    read_only => true
);

-- Create an agent
SELECT aidb.create_agent(
    'my_assistant',
    'You are a helpful database assistant.',
    'my_llm',
    tool_names => ARRAY['recent_orders']
);

-- Converse (NEVER raises — always check error column)
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_assistant', 'Show orders for customer 42');
```

**⚠ `agent_converse` never raises.** A failed turn is reported in the `error` column, not as a
PostgreSQL exception. The caller's transaction is NOT aborted. Always check `error`.

### Reasoning Loop Limits (from source)

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Hard cap on loop turns |
| `MAX_DELEGATION_DEPTH` | 11 | Max agent delegation depth (depth 1 = top level) |
| `MAX_UNKNOWN_ERROR_FAILURES` | 3 | Unclassifiable model errors before halt |
| `MAX_RATE_LIMIT_RETRIES` | 5 | Rate-limit retries before halt |

### Budget Strategies

| Strategy | On Budget Exceeded |
|---|---|
| `attempt_complete` (default) | Grants 3 extra rounds with "finalize now" instruction, then halts |
| `summarize` | Asks model for a closing summary, then finishes |
| `error` | Halts; turn ends as error |
| `ignore` | Pushes a warning, keeps going |

### Importing MCP Tools

```sql
SELECT aidb.import_mcp_tools(
    'weather_service', 'https://weather-mcp.example.com/mcp',
    transport => 'streamable_http',
    tool_filter => ARRAY['get_current_weather', 'get_forecast']
);
SELECT aidb.refresh_mcp_tools('weather_service');  -- refresh cache (ages out after 60 min)
```

**MCP tools are blocked entirely in read-only agent runs** (unclassified foreign tools).

See [references/agent-hub-deep-dive.md](references/agent-hub-deep-dive.md) for full execution flow,
delegation rules, and catalog discovery tools.

---

## 8. Governance: Purpose Registry

A purpose is a named policy scope that resolves to exactly one PostgreSQL role. Role switches
under a purpose are recorded as audited OTel `purpose_decision` spans.

```sql
SELECT aidb.create_purpose('analyst_purpose', 'analyst_role', 'Read-only analytics agents');

-- Assign to agent
SELECT aidb.create_agent('governed_agent', 'You are a read-only assistant.', 'my_llm',
    purpose => 'analyst_purpose');
```

**⚠ `delete_purpose` is a soft delete** (sets `deleted_at`, never physically removes the row).
Agents may still reference a deleted purpose name. View the registry:
```sql
SELECT name, role, deleted_at FROM aidb.purpose_registry;
```

See [references/governance-and-security.md](references/governance-and-security.md) for the full
security model including read-only enforcement layers, egress allowlists, and OTel telemetry.

---

## 9. Agent Memory

Agent Memory enables cross-session persistence. **Only the `mock` provider is implemented today** —
no real memory distillation occurs.

```sql
-- Initialize a namespace with compaction model
SELECT aidb.create_model('summarizer', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o-mini', api_key => 'sk-...'));
SELECT aidb_memory.init('my_ns', 'mock', '{"model_spec": {"llm": "summarizer"}}'::jsonb);

-- Point Agent Hub at it
SET aidb.agent_memory_namespace = 'my_ns';
SET aidb.agent_session_source = 'memory';  -- 'action_log' (default) | 'memory'
```

**Session tier:** `aidb_memory.session_start()` / `aidb_memory.session_get()` / `aidb_memory.session_end()`
are **never exposed as agent tools or over MCP** — session boundaries are the calling harness's
decision, not the model's.

**Bindings are immutable after `init`.** To change the compaction model, create a new namespace
and repoint the GUC.

---

## 10. MCP Endpoint (External Access)

```sql
-- Enable (requires postmaster restart)
ALTER SYSTEM SET edb.endpoints_mcp_enabled = on;
-- Default: http://127.0.0.1:8765 — non-loopback without TLS is rejected
```

Clients authenticate via HTTP Basic → SCRAM-SHA-256. They run SQL as their own PostgreSQL role.
See [references/mcp-endpoint-reference.md](references/mcp-endpoint-reference.md) for GUC surface,
type encoding, and troubleshooting.

---

## 11. Key GUCs

| GUC | Default | Restart? | Purpose |
|---|---|---|---|
| `aidb.max_threads` | half CPUs | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | true | No | Also emit pipeline errors as WARNINGs to PG log |
| `aidb.enable_memory_worker` | off | No | Enable Agent Memory background worker |
| `aidb.agent_session_source` | `action_log` | No | `action_log` or `memory` |
| `aidb.agent_memory_namespace` | `pg_agents` | No | Default memory namespace |
| `aidb.egress_allowlist` | — | No | Allow-listed provider/MCP hostnames |
| `aidb.allow_insecure_egress` | false | No | Bypass TLS — never enable in production |
| `aidb.otel_client` | `noop` | No | `noop`/`database`/`stdout`/`log`/`grpc` |
| `edb.endpoints_mcp_enabled` | off | **Yes** | Enable standalone MCP endpoint |

**`aidb.max_threads` changes require a database restart.** Inform the user before they attempt it.

---

## 12. Diagnostics & Troubleshooting

Run the diagnostic script to get a snapshot of the AIDB environment:
```bash
python3 scripts/diagnose_aidb.py --host localhost --port 5432 --dbname mydb --user postgres
```

### Common Failure Patterns

| Symptom | Cause | Fix |
|---|---|---|
| Pipeline creation fails: "name too long" | Name > 46 chars | Shorten the name |
| Pipeline creation fails: "destination table already exists" | Table exists | Drop it first |
| Pipeline creation fails: "incompatible step sequence" | Wrong step order | Check [references/step-operations.md](references/step-operations.md) |
| Background pipeline not processing | `aidb` not in `shared_preload_libraries` | Add and restart |
| `agent_converse` returns non-null `error` | Model/tool/budget issue | Check `error` column content |
| Model provider unreachable | Not in egress allowlist | Add to `aidb.egress_allowlist` |
| `decode_text` emits a warning | Deprecated function | Switch to `generate_text` |
| No similarity results from KB | Threshold too high | Try 0.7 or 0.5 |

See [references/troubleshooting.md](references/troubleshooting.md) for detailed procedures.
See [assets/cookbook.sql](assets/cookbook.sql) for runnable end-to-end examples.

---

## 13. Deprecated Items (Always Use New Names)

| Deprecated | Use Instead | Status |
|---|---|---|
| `aidb.decode_text` | `aidb.generate_text` | Still works; emits runtime warning |
| `aidb.decode_text_batch` | `aidb.generate_text_batch` | Still works; emits runtime warning |
| `KnowledgeBase` pipeline step | `aidb.create_semantic_kb()` or standalone `encode_text()` | Still works; emits warnings; marked "do not extend" in source |

When a user asks about or is already using a deprecated API:
1. Acknowledge it still works
2. Show the new preferred equivalent
3. Recommend migrating new code to the new API

---

## Reference Index

| File | Contents |
|---|---|
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures for all subsystems |
| [references/model-adapters.md](references/model-adapters.md) | All 20+ provider adapters with example SQL |
| [references/step-operations.md](references/step-operations.md) | Pipeline step types, config helpers, valid sequences |
| [references/agent-hub-deep-dive.md](references/agent-hub-deep-dive.md) | Reasoning loop constants, tool execution flow, delegation rules |
| [references/governance-and-security.md](references/governance-and-security.md) | Purpose Registry, egress, read-only enforcement, OTel |
| [references/troubleshooting.md](references/troubleshooting.md) | Failure patterns and diagnostic procedures |
| [references/mcp-endpoint-reference.md](references/mcp-endpoint-reference.md) | MCP endpoint GUCs, type encoding, client setup |
| [references/decision-guide.md](references/decision-guide.md) | Feature selection flowchart for ambiguous requests |
| [assets/cookbook.sql](assets/cookbook.sql) | 12 runnable SQL recipes covering all major features |
| [scripts/diagnose_aidb.py](scripts/diagnose_aidb.py) | Python diagnostic script: extension status, models, pipelines, agents, GUCs, errors |