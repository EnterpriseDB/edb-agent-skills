---
name: aidb
description: >
  Skill for operating AIDB (AI Accelerator Pipelines), the EDB Postgres AI extension
  that brings native AI capabilities into PostgreSQL entirely through SQL. Use this skill
  whenever a user asks about: installing or configuring AIDB; registering AI models
  (embeddings, completions, OCR, reranking); creating and running data transformation
  pipelines; building semantic knowledge bases over PostgreSQL schemas; using AI
  functions (encode_text, generate_text, chunk_text, parse_pdf, perform_ocr); creating
  and conversing with SQL-native agents (Agent Hub); managing tools (SQL tools, MCP
  tools, native catalog tools); agent memory and session management; the MCP endpoint
  server; governance via the Purpose Registry; and OpenTelemetry observability. Aliases:
  aidb, ai-factory, edb-aidb, pg-ai, postgres-ai-pipelines.
metadata:
  aliases: [aidb, ai-factory]
  pg_versions: ["14", "15", "16", "17", "18"]
  install: "CREATE EXTENSION aidb CASCADE;"
  schema: aidb
  internal_schema: aidb_internal
  memory_schema: aidb_memory
---

# AIDB Agent Skill

AIDB is a PostgreSQL extension that brings AI/ML capabilities natively into Postgres.
The entire surface is SQL — no external API calls are required for local models.
It is the AI Accelerator Pipelines component of EDB Postgres AI ("AI Factory").

> **Version note:** The version in `aidb.control`/`Cargo.toml` may be ahead of the
> last finalized release. Prefer "as of the current aidb source" over stating a specific
> version number. There is no CHANGELOG; reconstruct history from migration diffs.

---

## Core Concepts

| Concept | What It Is |
|---|---|
| **Pipeline** | Named data flow: source table/volume → AI steps → destination table |
| **Model** | Named AI adapter registered once, reused everywhere |
| **Semantic KB** | Vector index over PostgreSQL schema metadata for natural-language schema discovery |
| **Semantic Alias** | Parameterized SQL query with a natural language description, discoverable by agents |
| **Agent** | SQL-native conversational loop backed by tools and a model (Agent Hub) |
| **Tool** | Callable SQL function, native built-in, or imported MCP capability |
| **Purpose** | Named governance policy (name → PostgreSQL role) for audited agent role-switching |
| **Volume** | Object storage (S3/Azure/GCS/local) accessible as a pipeline source |

---

## Step 1: Install

```sql
CREATE EXTENSION aidb CASCADE;  -- installs aidb + pgvector + other deps
```

Supported: PostgreSQL 14–18. Requires `aidb` in `shared_preload_libraries` for
background pipeline workers and the MCP endpoint supervisor.

---

## Step 2: Register a Model

Models are registered by name and referenced everywhere else by that name.

```sql
-- For testing (no external service needed, deterministic output)
SELECT aidb.create_model('test_model', 'dummy');

-- OpenAI embeddings
SELECT aidb.create_model('my_embedder', 'openai_embeddings',
    config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...'));

-- OpenAI completions
SELECT aidb.create_model('my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...'));

-- Using env var instead of inline credentials (recommended for production)
SELECT aidb.create_model('claude', 'anthropic_messages',
    credentials_env => 'ANTHROPIC_API_KEY');

-- Validate connectivity at registration time
SELECT aidb.create_model('my_llm', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o', api_key => 'sk-...'),
    validate => true);
```

See [references/model-adapters.md](references/model-adapters.md) for the full provider list
(20+ adapters: local models, OpenAI-compatible, NVIDIA NIM, HuggingFace, Anthropic, Gemini, etc.).

**Security:** Credentials are stored in `pg_user_mappings`, never in plain-text catalog tables.
`aidb.list_models()` never returns credential fields.

---

## Step 3: Use Standalone AI Functions

```sql
-- Embeddings
SELECT aidb.encode_text('Hello world', 'my_embedder');           -- returns VECTOR
SELECT aidb.encode_text_batch(ARRAY['a','b'], 'my_embedder');    -- returns VECTOR[]

-- Text generation (ALWAYS use generate_text, NOT the deprecated decode_text)
SELECT aidb.generate_text('Summarize: ...', 'my_llm');
SELECT aidb.generate_text_batch(ARRAY['prompt1','prompt2'], 'my_llm');

-- Text preparation
SELECT * FROM aidb.chunk_text(long_text, aidb.chunk_text_config(desired_length => 512));
SELECT aidb.summarize_text(text, aidb.summarize_text_config(model => 'my_llm'));
SELECT aidb.parse_pdf(pdf_bytes);    -- returns TEXT
SELECT aidb.parse_html(html_bytes);  -- returns TEXT
SELECT aidb.perform_ocr(image_bytes, 'my_nim_ocr_model');  -- requires nim_paddle_ocr

-- Reranking
SELECT * FROM aidb.rerank_text('query', ARRAY['doc1','doc2'], 'my_rerank_model')
ORDER BY score DESC;
```

> ⚠️ `aidb.decode_text` / `aidb.decode_text_batch` are **deprecated**. They still work
> but emit runtime warnings. Always use `generate_text` / `generate_text_batch`.

---

## Step 4: Create a Pipeline

Pipelines transform source table rows through ordered AI steps and write to a destination.

```sql
-- Constraints:  name ≤ 46 chars  |  steps ≤ 10  |  destination must not exist
SELECT aidb.create_pipeline(
    name               => 'my_pipeline',
    source             => 'my_source_table',
    source_key_column  => 'id',
    source_data_column => 'body',
    auto_processing    => 'Background',   -- 'Live' | 'Background' | 'Disabled'
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
);

-- Run manually (Disabled mode or force re-run)
SELECT aidb.run_pipeline('my_pipeline');

-- List pipelines
SELECT * FROM aidb.list_pipelines();
```

**Auto-processing modes:**
- `Live` — database trigger fires on every INSERT/UPDATE (synchronous)
- `Background` — async worker processes new rows in batches (non-blocking)
- `Disabled` — manual `aidb.run_pipeline()` only

> ⚠️ The `KnowledgeBase` pipeline step is **deprecated**. Do not use it for new work.
> Use `aidb.create_semantic_kb()` for schema embedding, or `aidb.encode_text()` for
> general embedding pipelines.

See [references/step-operations.md](references/step-operations.md) for all step types
(ChunkText, SummarizeText, ParseHtml, ParsePdf, PdfToImage, PerformOcr) and
compatible sequencing rules.

---

## Step 5: Build a Semantic Knowledge Base

Semantic KBs vectorize PostgreSQL schema metadata for natural-language schema discovery.

```sql
-- Create KB over one or more schemas
SELECT aidb.create_semantic_kb(
    'my_kb', 'my_embedder', ARRAY['public', 'sales'], 'Live');

-- Search (similarity threshold 0.7–0.8 is a good default)
SELECT * FROM aidb.get_tables('my_kb', 'customer purchase history', 0.7, 5, 0);
SELECT * FROM aidb.get_columns('my_kb', 'email address', 0.75, 10, 0);
SELECT * FROM aidb.get_metadata('my_kb', 'order total', 0.6, 10, 0);

-- Composite search (tables + aliases, requires aidb 7.5.0+)
SELECT * FROM aidb.semantic_kb_search('top customers by spend', 'my_kb', 10);

-- Refresh after schema changes
SELECT aidb.refresh_semantic_kb('my_kb');
```

**Semantic Aliases** (parameterized SQL with natural language descriptions):
```sql
SELECT aidb.create_semantic_alias(
    'top_customers', 'Return top N customers by revenue',
    'SELECT id, name, SUM(total) revenue FROM orders GROUP BY id, name ORDER BY revenue DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'Number of results')),
    'my_kb');

SELECT aidb.execute_semantic_alias('top_customers', '{"n": 10}'::jsonb);
```

> ⚠️ KB relationships (add_relationship, suggest_joins, find_join_path) only become
> executable SQL **after explicit approval/curation**. Never route through an
> unreviewed relationship.

---

## Step 6: Use the Agent Hub

Agents are governed, tool-using conversational loops running entirely in SQL.

```sql
-- Create a SQL tool
SELECT aidb.create_sql_tool(
    'recent_orders', 'Get the N most recent orders',
    'SELECT id, total FROM orders ORDER BY created_at DESC LIMIT ${n}',
    aidb.tool_params(aidb.tool_param('n', 'integer', 'Count')),
    read_only => true);

-- Create an agent
SELECT error FROM aidb.create_agent(
    'db_assistant',
    'You are a helpful database assistant.',
    'my_llm',
    tools => ARRAY['recent_orders', 'catalog_list_schemas'],
    budget_strategy => 'attempt_complete');

-- Converse — NEVER raises; check the `error` column
SELECT message, conversation_id, error
FROM aidb.agent_converse('db_assistant', 'Show me the 5 most recent orders.');

-- Continue a conversation
SELECT message, error
FROM aidb.agent_converse('db_assistant', 'Now group by customer.',
    conversation_id => '<uuid>');
```

**Agent safety limits:** Max 25 reasoning iterations, max delegation depth 11.
`agent_converse` always returns an `error` column instead of raising on failure.

**Import external MCP tools:**
```sql
SELECT aidb.import_mcp_tools('my_server', 'http://mcp-host:8080/mcp');
SELECT aidb.refresh_mcp_tools('my_server');  -- cache expires after 60 min
```

See [references/agent-hub-guide.md](references/agent-hub-guide.md) for the full
Agent Hub & Tools Hub operational guide, including budget strategies, governance,
read-only enforcement, and delegation rules.

---

## Step 7: Agent Memory (Session Tier)

```sql
-- Initialize namespace (only mock provider ships today)
SELECT aidb_memory.init('my_ns', 'mock', '{}'::jsonb);

-- Configure session (session-local GUCs, safe to set per connection)
SET aidb.agent_memory_namespace = 'my_ns';
SET aidb.agent_session_source = 'memory';  -- or 'action_log' (default)

-- Session starts with conversation; memory captured automatically
SELECT conversation_id FROM aidb.start_agent_session('db_assistant') \gset s_

SELECT message FROM aidb.agent_converse('db_assistant', 'Hello',
    conversation_id => :'s_conversation_id');

-- Read session history (budgeted; compacts automatically on overflow)
SELECT item->>'kind', item->>'content'
FROM aidb_memory.session_get('my_ns', session_user, :'s_conversation_id', 8000) s,
     jsonb_array_elements(s.items) t(item);

-- Close session with handoff notes
SELECT aidb_memory.session_end('my_ns', session_user, :'s_conversation_id',
    'User asked about top customers.');
```

> Session verbs (`session_start/get/end`) are **never** exposed as agent tools or
> over MCP. They are the calling harness's responsibility.

---

## Key Constraints — Memorize These

| Constraint | Value |
|---|---|
| Max pipeline name length | 46 characters |
| Max steps per pipeline | 10 |
| Destination table at creation | Must NOT exist |
| Model validation | At pipeline creation time (not execution), unless `validate => true` passed to `create_model` |
| Thread pool change (`aidb.max_threads`) | Requires database restart |
| `KnowledgeBase` step | Deprecated — do not use for new work |
| `decode_text` / `decode_text_batch` | Deprecated — use `generate_text` / `generate_text_batch` |
| `purpose` deletions | Soft-delete only (never physically removed) |
| KB join relationships | Must be approved before routing — never assume unreviewed is safe |

---

## Egress & Security

Outbound network calls require explicit allow-listing:
```sql
ALTER SYSTEM SET aidb.egress_allowlist = 'api.openai.com,my-nim:8080';
SELECT pg_reload_conf();
```

For TLS: set `aidb.allow_insecure_tls = 'on'` ONLY for isolated dev environments.

---

## MCP Endpoint (External Agent Access)

The standalone `edb-endpoints` binary exposes Tools Hub and `execute_sql` over
streamable-HTTP MCP. Controlled via GUCs:

```sql
-- Requires postgresql.conf change + restart for enabled flag
ALTER SYSTEM SET edb.endpoints_mcp_enabled = 'on';
ALTER SYSTEM SET edb.endpoints_mcp_port = 8765;
-- TLS required for non-loopback hosts
ALTER SYSTEM SET edb.endpoints_mcp_tls_cert = '/path/to/cert.pem';
ALTER SYSTEM SET edb.endpoints_mcp_tls_key  = '/path/to/key.pem';
```

Clients authenticate with HTTP Basic → SCRAM. SQL runs as the client's own PG role.

---

## Diagnostic & Troubleshooting

Run the diagnostic script to check environment health:
```
python3 scripts/aidb-diagnose.py
```

Common issues and fixes: [references/troubleshooting.md](references/troubleshooting.md)

Decision checklists (pipeline modes, provider selection, checklist before creating
pipelines/agents): [assets/decision-checklists.md](assets/decision-checklists.md)

---

## AI Factory Context

AIDB is one component of EDB Postgres AI alongside:
- **pgvector** — vector search engine (installed as dependency)
- **Gen AI Builder** — Assistants, Tools, Rulesets, Threads
- **Model Serving** — GPU-accelerated KServe inference

All AI processing stays inside the user's infrastructure ("Sovereign AI"). No external
dependency is mandatory, and every operation is auditable.

---

## Reference Files

| File | Contents |
|---|---|
| [references/function-reference.md](references/function-reference.md) | Complete SQL function signatures for all AIDB features |
| [references/model-adapters.md](references/model-adapters.md) | All 20+ model providers with examples |
| [references/step-operations.md](references/step-operations.md) | Pipeline step types, options, and compatible sequencing |
| [references/agent-hub-guide.md](references/agent-hub-guide.md) | Agent Hub & Tools Hub operational guide |
| [references/quick-start-examples.md](references/quick-start-examples.md) | Runnable SQL examples for all major features |
| [references/troubleshooting.md](references/troubleshooting.md) | Common errors and fixes |
| [assets/decision-checklists.md](assets/decision-checklists.md) | Checklists: provider selection, pipeline creation, agent setup |
| [scripts/aidb-diagnose.py](scripts/aidb-diagnose.py) | Python diagnostic script for environment health check |