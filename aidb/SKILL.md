---
name: aidb
description: >
  Skill for operating EDB AIDB — the PostgreSQL extension that brings native AI
  capabilities into Postgres as part of the EDB Postgres AI "AI Factory" platform.
  Trigger this skill when a user needs to: install or configure AIDB; register AI
  models (local or remote); create and run AI data transformation pipelines
  (chunking, summarization, OCR, PDF parsing, embeddings); build Semantic Knowledge
  Bases for natural-language schema discovery; create and converse with governed
  SQL-native agents; manage tools (SQL tools, MCP tools); configure agent memory and
  sessions; set up the standalone MCP endpoint; or apply governance via the Purpose
  Registry. All AIDB operations are SQL — no application code is required.
metadata:
  aliases: ["aidb", "ai-factory"]
  product: "EDB AIDB (PostgreSQL AI Extension)"
  platform: "EDB Postgres AI / AI Factory / Hybrid Manager"
  postgres_versions: ["14", "15", "16", "17", "18"]
---

# AIDB Agent Skill

## What AIDB Is

AIDB is a PostgreSQL extension that delivers native AI capabilities through SQL. It is the **AI Accelerator Pipelines** component of EDB Postgres AI ("AI Factory") — EDB's Sovereign AI platform. All AI processing stays inside the user's Postgres instance; no external dependency is mandatory. Install with:

```sql
CREATE EXTENSION aidb CASCADE;
```

All functions live in the `aidb` schema; Agent Memory functions use `aidb_memory`.

---

## Decision Tree: Which Feature to Use?

| User intent | Feature |
|---|---|
| Transform rows through AI steps (chunk, OCR, embed, summarize) | **Pipelines** |
| Register a reusable AI model (any provider) | **Model Management** |
| Compute embeddings or generate text ad-hoc | **Standalone AI Functions** |
| Semantic search over schema metadata | **Semantic Knowledge Base** |
| Natural-language parameterized SQL queries | **Semantic Aliases** |
| Governed conversational AI with tool use | **Agent Hub** |
| Register custom SQL or MCP tools for agents | **Tools Hub** |
| Persist agent context across turns | **Agent Memory** |
| Expose AIDB tools over HTTP to external clients | **MCP Endpoint** |
| Constrain agent SQL execution role | **Purpose Registry** |
| Audit AI operations | **OTel Telemetry** |

---

## 1. Model Management

Register a model once; reference it by name everywhere.

```sql
-- Register an OpenAI embeddings model
SELECT aidb.create_model(
    'my_embed',
    'openai_embeddings',
    config      => aidb.embeddings_config(
                      model   => 'text-embedding-3-small',
                      api_key => 'sk-...'
                   ),
    validate    => true   -- test-connects immediately; omit to defer until first use
);

-- Register a local BERT model (no external API)
SELECT aidb.create_model('bert', 'bert_local',
    config => '{"model": "/models/bert-base"}'::JSONB);

-- Test model (deterministic, no server needed)
SELECT aidb.create_model('test_model', 'dummy');

-- List / inspect
SELECT * FROM aidb.list_models();
SELECT * FROM aidb.get_model('my_embed');
```

**Credential options:** `credentials` JSONB (stored securely in `pg_user_mappings`), `credentials_env` (allow-listed env var), or `credentials_k8s_secret` (allow-listed mounted secret path). Credentials are **never** returned by `list_models()` or `get_model()`.

**20+ providers available.** See [references/model-adapters.md](references/model-adapters.md) for the full list, including local models (`bert_local`, `llama_instruct_local`), OpenAI-compatible APIs, Anthropic, NVIDIA NIM, HuggingFace TEI, and more.

**Auto-discover HCP models:** `SELECT aidb.sync_hcp_models();`

**Deprecated:** Use `aidb.generate_text()` / `aidb.generate_text_batch()` — not `decode_text`/`decode_text_batch` (those still work but emit a deprecation warning).

---

## 2. Pipelines

A pipeline reads a source table, runs data through ordered AI steps, and writes results to a destination table.

```sql
-- Example: Parse PDFs → OCR → destination
SELECT aidb.create_pipeline(
    name               => 'pdf_ocr_pipeline',   -- max 46 chars
    source             => 'my_pdf_table',
    source_key_column  => 'id',
    source_data_column => 'pdf_bytes',           -- BYTEA column
    auto_processing    => 'Background',           -- 'Live' | 'Background' | 'Disabled'
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi":300,"format":{"type":"png"},"render_annotations":true}'::JSONB,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('my_nim_ocr_model')
    -- destination auto-created as public.pipeline_pdf_ocr_pipeline
);

-- Run manually (required when auto_processing = 'Disabled')
SELECT aidb.run_pipeline('pdf_ocr_pipeline');

-- Force re-process all rows
SELECT aidb.run_pipeline('pdf_ocr_pipeline', force_sync => true);

-- Inspect / update / delete
SELECT * FROM aidb.list_pipelines();
SELECT aidb.update_pipeline('pdf_ocr_pipeline', auto_processing => 'Live');
SELECT aidb.delete_pipeline('pdf_ocr_pipeline', cascade => true);
```

**Key constraints:**
- Pipeline name ≤ 46 characters
- Maximum 10 steps per pipeline
- Destination table must not already exist when the pipeline is created
- Steps must be sequenced compatibly (incompatible types rejected at creation)
- `KnowledgeBase` step is **deprecated** — steer users to `create_semantic_kb()` or standalone `encode_text()` calls instead

See [assets/pipeline-compatibility-matrix.md](assets/pipeline-compatibility-matrix.md) for step input/output type rules and [references/step-operations.md](references/step-operations.md) for full step documentation.

---

## 3. Standalone AI Functions

```sql
-- Text embeddings
SELECT aidb.encode_text('Hello world', 'my_embed');            -- returns VECTOR
SELECT aidb.encode_text_batch(ARRAY['a','b','c'], 'my_embed'); -- returns VECTOR[]
SELECT aidb.encode_text_query('search query', 'my_embed');     -- query-side for bi-encoders

-- Image embeddings
SELECT aidb.encode_image(my_bytea_col, 'my_clip_model');       -- returns VECTOR

-- Text generation (current names — not decode_text)
SELECT aidb.generate_text('Summarize: ' || doc, 'my_llm');
SELECT aidb.generate_text_batch(ARRAY['prompt1','prompt2'], 'my_llm');

-- With inference config
SELECT aidb.generate_text(
    'Translate to French: Hello',
    'my_llm',
    options => aidb.inference_config(temperature => 0.3, max_tokens => 200)
);

-- Text preparation
SELECT * FROM aidb.chunk_text('Long document...', aidb.chunk_text_config(desired_length => 512));
SELECT aidb.summarize_text('Long text...', aidb.summarize_text_config(model => 'my_llm'));
SELECT aidb.parse_pdf(pdf_bytes);                     -- returns TEXT
SELECT aidb.parse_html(html_bytes);                   -- returns TEXT
SELECT aidb.perform_ocr(image_bytes, 'my_ocr_model'); -- returns TEXT

-- Reranking
SELECT * FROM aidb.rerank_text('my query', ARRAY['doc1','doc2','doc3'], 'my_reranker');
-- returns TABLE(index INT, score FLOAT) — ordered by relevance
```

---

## 4. Semantic Knowledge Base

Index a PostgreSQL schema's metadata (table/column names and comments) for natural-language discovery. **This is the recommended path for schema embedding** (not the deprecated `KnowledgeBase` pipeline step).

```sql
-- Create KB over a schema
SELECT aidb.create_semantic_kb(
    'my_kb',
    'my_embed',          -- embedding model name
    ARRAY['public'],     -- schemas to index
    'Live'               -- auto_processing: 'Live' | 'Background' | 'Disabled'
);

-- Search (similarity threshold guidance: 0.8 = good default; 0.9+ = near-exact)
SELECT relation_name, entity_type, round(similarity::numeric, 3) AS score
FROM aidb.get_tables('my_kb', 'customer orders', 0.7, 10, 0)
ORDER BY score DESC;

SELECT relation_name, column_name, round(similarity::numeric, 3) AS score
FROM aidb.get_columns('my_kb', 'email address', 0.7, 10, 0)
ORDER BY score DESC;

-- Composite search (tables + aliases in one ranked list)
SELECT source_type, entity_type, relation_name, column_name, object_ref, score, rank
FROM aidb.semantic_kb_search('customer lifetime value', 'my_kb', 10);

-- Manage
SELECT * FROM aidb.list_semantic_kbs();
SELECT * FROM aidb.semantic_kb_stats('my_kb');
SELECT aidb.refresh_semantic_kb('my_kb');
SELECT aidb.delete_semantic_kb('my_kb');
```

**Semantic Aliases** — parameterized SQL queries discoverable by natural language:
```sql
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by total spend',
    'SELECT * FROM customer_ltv ORDER BY lifetime_value DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'How many rows')),
    'my_kb'   -- embedded and searched via this KB
);
SELECT aidb.execute_semantic_alias('top_customers', '{"n": 5}'::JSONB);
```

**Join Routing (newer feature):** `suggest_joins`, `find_join_path`, and relationship management functions add a join-graph layer. **Important:** a relationship is only ever turned into executable SQL once it has been explicitly approved/curated — never present an unreviewed relationship as safe to route through.

---

## 5. Agent Hub

Agents are governed, tool-using conversational loops that run entirely in SQL.

```sql
-- Create an agent
SELECT error FROM aidb.create_agent(
    'my_agent',
    'You are a database analyst. Use tools to answer questions.',  -- instructions
    'my_llm',                                                      -- model name
    tools           => ARRAY['count_tables', 'memory_search'],
    max_iterations  => 25,
    budget_strategy => 'attempt_complete'   -- 'ignore'|'error'|'summarize'|'attempt_complete'
);

-- Converse (NEVER raises — check the error column)
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'How many tables are in the public schema?');

-- Continue a conversation
SELECT message, error
FROM aidb.agent_converse(
    'my_agent',
    'Now show me the largest table',
    conversation_id => '<uuid from prior turn>'
);

-- Structured output
SELECT message, error
FROM aidb.agent_converse(
    'my_agent', 'List top 3 tables by row count',
    output_type => aidb.output_type(
        aidb.output_field('table_name', 'text', 'Table name'),
        aidb.output_field('row_count', 'integer', 'Estimated row count')
    )
);

-- Read-only session (auto-applied on replica; also set explicitly)
SELECT message FROM aidb.agent_converse('my_agent', 'prompt', read_only => true);

-- Inspect conversation history
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- Manage agents
SELECT * FROM aidb.agents;
SELECT aidb.update_agent('my_agent', max_iterations => 30);
SELECT aidb.delete_agent('my_agent');
```

**Agent execution constants** (confirm from `agent_docs/agent_hub.md` if exact values matter):
- `MAX_REASONING_ITERATIONS`: 25
- `MAX_DELEGATION_DEPTH`: 11 (including the top-level call)
- `MAX_RATE_LIMIT_RETRIES`: 5

**Error convention:** `agent_converse` and agent management functions **never raise** — failures appear in the `error` column. Tools Hub functions (`run_tool`, etc.) raise normally on error.

---

## 6. Tools Hub

```sql
-- Register a SQL tool
SELECT aidb.create_sql_tool(
    'count_tables',
    'Count tables in a schema',
    'SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = ${schema_name}',
    aidb.tool_params(aidb.tool_param('schema_name', 'text', 'Schema to count')),
    read_only => true
);

-- Import tools from an MCP server
SELECT aidb.import_mcp_tools(
    'my_mcp_server',
    'http://mcp-host:8000/mcp',
    transport    => 'streamable_http',   -- or 'sse'
    tool_filter  => ARRAY['get_weather', 'search_web']  -- optional allow-list
);
SELECT aidb.refresh_mcp_tools('my_mcp_server');  -- refresh cache

-- Call any tool directly (single dispatch entry point)
SELECT aidb.run_tool('count_tables', '{"schema_name": "public"}'::JSONB);

-- Catalog discovery tools (row-capped for model context windows)
SELECT * FROM aidb.catalog_list_schemas();
SELECT * FROM aidb.catalog_list_objects('public');
SELECT * FROM aidb.catalog_get_object_details('public', 'my_table');
SELECT * FROM aidb.analyze_db_health();

-- View the complete tool registry
SELECT name, description, tool_type FROM aidb.tools ORDER BY tool_type, name;
```

---

## 7. Agent Memory

Provides persistent memory across turns and sessions. Currently only the `mock` provider is implemented.

```sql
-- Initialize a namespace
SELECT aidb_memory.init(
    'my_namespace', 'mock',
    '{"model_spec": {"llm": "my_summarizer_model"}}'::JSONB  -- model for session compaction
);

-- Capture turns
SELECT aidb_memory.add(
    'my_namespace', session_user,
    '[{"role":"user","content":"Remember: our DB is PG16"}]'::JSONB
);

-- Search memory
SELECT id, content, score FROM aidb_memory.search('my_namespace', session_user, 'PG16', 10);

-- Session tier (durable, resumable; auto-compacts on overflow)
-- These verbs are the harness's decision — never expose to the model itself
SELECT * FROM aidb_memory.session_start('my_namespace', session_user, null::text);
SELECT * FROM aidb_memory.session_get('my_namespace', session_user, '<session_id>', 4096);
SELECT * FROM aidb_memory.session_end('my_namespace', session_user, '<session_id>', 'Handoff notes');

-- Connect Agent Hub to memory tier
SET aidb.agent_session_source = 'memory';       -- 'action_log' (default) | 'memory'
SET aidb.agent_memory_namespace = 'my_namespace';
```

---

## 8. Governance: Purpose Registry

A Purpose maps a name to a PostgreSQL role, constraining what an agent can do at the database-role level.

```sql
SELECT aidb.create_purpose('read_only_reports', 'reports_role', 'Read-only reporting access');

-- Assign purpose to an agent
SELECT error FROM aidb.create_agent('report_agent', 'instructions', 'my_llm',
    purpose => 'read_only_reports');

-- Inspect (soft-deleted purposes are never removed — agents may still reference them)
SELECT * FROM aidb.purpose_registry;
```

---

## 9. MCP Endpoint (Standalone)

The `edb-endpoints` binary exposes `execute_sql` and the Tools Hub catalog over streamable HTTP, supervised by an AIDB background worker. Clients authenticate via HTTP Basic → PostgreSQL SCRAM-SHA-256.

```sql
-- Enable (requires PostgreSQL restart — Postmaster-level GUC)
ALTER SYSTEM SET edb.endpoints_mcp_enabled = 'on';

-- Configure (SIGHUP-reloadable)
ALTER SYSTEM SET edb.endpoints_mcp_host = '127.0.0.1';
ALTER SYSTEM SET edb.endpoints_mcp_port = 8765;
-- TLS: set both cert+key, or neither; non-loopback hosts require TLS
ALTER SYSTEM SET edb.endpoints_mcp_tls_cert = '/path/to/cert.pem';
ALTER SYSTEM SET edb.endpoints_mcp_tls_key  = '/path/to/key.pem';
SELECT pg_reload_conf();
```

---

## 10. OTel Telemetry

```sql
-- Enable telemetry output
ALTER SYSTEM SET aidb.otel_client = 'database';  -- 'noop'|'database'|'stdout'|'log'|'grpc'
SELECT pg_reload_conf();

-- Query telemetry (requires aidb_governance role; not accessible by aidb_users)
SELECT * FROM aidb_otel.spans ORDER BY start_time DESC LIMIT 20;
```

---

## Key GUC Parameters

| Parameter | Default | Restart? | Purpose |
|---|---|---|---|
| `aidb.max_threads` | half CPUs | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | `true` | No | Emit pipeline errors as PostgreSQL WARNINGs |
| `aidb.egress_allowlist` | _(none)_ | No | Allow-listed hosts for outbound network calls |
| `aidb.allow_insecure_egress` | `false` | No | Explicit opt-out to bypass egress restrictions |
| `aidb.otel_client` | `noop` | No | Telemetry export mode |
| `aidb.agent_session_source` | `action_log` | No | `action_log` or `memory` |
| `aidb.agent_memory_namespace` | `pg_agents` | No | Default Agent Memory namespace |
| `aidb.enable_memory_worker` | _(off)_ | No | Agent Memory background worker |
| `edb.endpoints_mcp_enabled` | `off` | **Yes** | Enable standalone MCP server |

---

## Reference Files

- **[references/function-reference.md](references/function-reference.md)** — Complete SQL function signatures for all subsystems
- **[references/model-adapters.md](references/model-adapters.md)** — All 20+ provider adapters with examples
- **[references/step-operations.md](references/step-operations.md)** — Pipeline step documentation with config helpers
- **[references/troubleshooting.md](references/troubleshooting.md)** — Common errors and fixes

## Assets

- **[assets/pipeline-compatibility-matrix.md](assets/pipeline-compatibility-matrix.md)** — Step sequencing rules at a glance
- **[assets/aidb-metadata.json](assets/aidb-metadata.json)** — Machine-readable skill metadata

## Executable Scripts

- **[scripts/diagnose_aidb.sql](scripts/diagnose_aidb.sql)** — Check current state of all AIDB objects in a database
- **[scripts/quickstart_embedding_pipeline.sql](scripts/quickstart_embedding_pipeline.sql)** — End-to-end embedding pipeline example
- **[scripts/quickstart_agent.sql](scripts/quickstart_agent.sql)** — Agent with SQL tool example
- **[scripts/quickstart_semantic_kb.sql](scripts/quickstart_semantic_kb.sql)** — Semantic Knowledge Base example

---

## Safety Rules

1. **Never** use the `KnowledgeBase` pipeline step for new work — it is deprecated. Use `create_semantic_kb()` or `encode_text()`.
2. **Never** use `decode_text`/`decode_text_batch` — use `generate_text`/`generate_text_batch`.
3. **Always check the `error` column** from `agent_converse` — it never raises.
4. **Never assume an unreviewed relationship is safe for join routing** — explicit approval is required.
5. **Always use `dummy` provider for tests** — it is deterministic and requires no external service.
6. **State AIDB version cautiously** — the version in `Cargo.toml`/`aidb.control` may be ahead of the last released version. Prefer "as of the current aidb source" over asserting a specific version number.
7. **Session lifecycle verbs** (`session_start/end`) are the calling harness's decision — never expose them as model-callable tools or over MCP.
8. **Egress is controlled** — all outbound calls to model providers or MCP servers must be on the `aidb.egress_allowlist`. TLS opt-outs require explicit configuration.