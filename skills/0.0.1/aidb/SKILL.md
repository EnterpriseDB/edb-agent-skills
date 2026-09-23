---
name: aidb
description: >-
  Operate EDB AIDB (aidb, also shipped as part of EDB AI Factory / Hybrid Manager) — the
  in-database AI extension for PostgreSQL, driven entirely from SQL. Trigger when the user
  mentions aidb, ai-factory, aidb.* SQL functions, or asks to: build RAG or semantic search
  inside Postgres; register or debug AI models (OpenAI, Anthropic, NVIDIA NIM, Gemini,
  OpenRouter, HuggingFace TEI, llama.cpp/Candle local models); create pipelines that chunk,
  parse HTML/PDF, render PDFs to images, OCR, summarize or embed; query vector knowledge
  bases with aidb.retrieve_text/aidb.retrieve_key or hybrid vector+full-text search; ingest
  from S3/GCS/Azure/local files via PGFS volumes; build semantic knowledge bases for schema
  discovery and text-to-SQL; create in-database agents, custom SQL tools, import tools from
  an external MCP server, or expose AIDB's own tool catalog to external agents over MCP.
  Supplies discovery queries, exact signatures, safety rules (always confirm before acting;
  never store an API key in a model's config) and troubleshooting.
metadata:
  aliases: "aidb, ai-factory"
  target_version: "7.6.0"
  surface: "SQL only - no separate service, API or SDK"
---

# AIDB — in-database AI for PostgreSQL

## §1 Scope

AIDB is an EDB-maintained PostgreSQL extension. **Every capability is a SQL call in the `aidb`
schema** — there is no CLI, REST API or SDK to drive. It runs inside Postgres (14–18: community,
EDB Postgres Advanced Server, EDB Postgres Extended) and has no runtime dependency on Agent
Factory or Hybrid Manager, even though it ships with them. With a local model, neither the data
nor the inference leaves the database.

Building blocks, in the order users usually meet them:

| Block | What it is | Reference |
|---|---|---|
| Standalone SQL functions | `chunk_text`, `parse_html`, `parse_pdf`, `pdf_to_image`, `perform_ocr`, `summarize_text`, plus inference | [sql-functions.md](references/sql-functions.md) |
| Models | Registered once by name, reused everywhere; local or remote | [models.md](references/models.md) |
| Pipelines | Up to 10 declarative steps: parse → chunk → OCR → summarize → embed | [pipelines.md](references/pipelines.md) |
| Knowledge bases | A pipeline's embedding output, pgvector-indexed and queryable | [knowledge-bases.md](references/knowledge-bases.md) |
| Tools | Native tools, your parameterized SQL queries, imported MCP tools — one catalog | [tools.md](references/tools.md) |
| Agents | Instructions + model + allowed tools, run in a ReAct loop; can delegate | [agents.md](references/agents.md) |

Every pipeline step operation is *also* a standalone function, so a user can try a transformation
in one query before committing to a pipeline.

## §2 Get a connection, run SQL, then verify

The skill's whole output is SQL. Before promising any action, establish **how** you will run it.

**2.1 Find an execution channel, in this order:**

1. A Postgres MCP tool / database tool already available to you (e.g. one that executes queries
   against a configured database) — prefer it, and confirm which database it points at.
2. A `psql` binary plus a connection: an explicit connection string from the user, `DATABASE_URL`,
   `PGSERVICE` (`psql "service=$PGSERVICE"`), or standard libpq env vars
   (`PGHOST`/`PGPORT`/`PGUSER`/`PGDATABASE`). Run non-interactively and fail fast:
   `psql "<conn>" -v ON_ERROR_STOP=1 -P pager=off -f -`.
3. Ask the user. Request: host/port or connection string, database, and the **role** to run as
   (AIDB access is granted through the `aidb_users` role). Ask before, not after.

**2.2 If no channel exists**, say so plainly and switch to advisory mode: print the exact SQL in a
copy-pasteable block for the user to run, and ask them to paste back the output. **Never report
that an object was created, a pipeline ran, or a model was registered unless you saw the result of
the statement.** Do not narrate success you did not observe.

**2.3 Preflight (read-only) — run on any unfamiliar database before advising:**

```sql
-- Is it installed, at what version, with what companions?
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('aidb','vector','pgfs','vchord') ORDER BY extname;

-- Is the library loaded, and how is it configured? Empty result => aidb is not in
-- shared_preload_libraries (add it and restart), so nothing below will work.
SELECT name, setting, context FROM pg_settings WHERE name LIKE 'aidb.%' ORDER BY name;

-- Can this role use AIDB? Is this a replica (agents auto-run read-only there)?
SELECT current_user, current_database(),
       pg_has_role(current_user,'aidb_users','MEMBER') AS in_aidb_users,
       pg_is_in_recovery() AS is_read_replica;
```

**2.4 Version drift — mandatory rule.** This skill and every file in `references/` document
**AIDB 7.6.0**. Compare `extversion` to `7.6.0`:

* **Equal** → the references apply as written.
* **Different (older or newer)** → tell the user explicitly: *"This installation is AIDB
  `<extversion>`; the signatures, providers, native tools and config helpers in my reference set
  are for 7.6.0 and may not match."* Then **verify before proposing any call**:

```sql
-- Real signatures on THIS installation (never propose a call you have not confirmed here)
SELECT p.proname, pg_get_function_arguments(p.oid) AS arguments,
       pg_get_function_result(p.oid) AS returns
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'aidb' AND p.proname = 'create_model';   -- swap in the function you need

SELECT server_name FROM aidb.model_providers ORDER BY server_name;  -- real providers
SELECT name, provider, functions FROM aidb.models ORDER BY provider, name;  -- real models
SELECT name, tool_type, read_only FROM aidb.tools ORDER BY tool_type, name; -- real tools
```

Anything you cannot confirm in `pg_proc` or the live catalogs must be described as "documented for
7.6.0, not present here" rather than offered.

**2.5 Inventory of what exists** (run once per session before recommending anything; each view is
documented in the reference named beside it):

```sql
SELECT server_name, server_description FROM aidb.model_providers ORDER BY server_name; -- models.md
SELECT name, provider, functions FROM aidb.models ORDER BY provider, name;             -- models.md
SELECT * FROM aidb.audit_leaked_credentials();                                         -- models.md
SELECT name, source_type, source, destination_type, destination, auto_processing, steps
  FROM aidb.pipelines ORDER BY name;                                                   -- pipelines.md
SELECT pipeline, "auto processing", "Status", "count(record errors)",
       "count(blocking errors)", "last run completed"
  FROM aidb.pipeline_metrics ORDER BY pipeline;                                        -- pipelines.md
SELECT * FROM aidb.get_all_error_summaries();                                          -- pipelines.md
SELECT name, vector_schema, vector_table, model_name, distance_operator,
       distance_operator_sql, pipeline_names FROM aidb.knowledge_bases ORDER BY name;  -- knowledge-bases.md
SELECT name, pipelines, embeddings, status FROM aidb.knowledge_base_metrics ORDER BY name;
SELECT schema, volume, storage_location, path FROM aidb.volumes ORDER BY schema, volume;
SELECT * FROM aidb.list_semantic_kbs();                                                -- knowledge-bases.md
SELECT name, description, param_count FROM aidb.get_semantic_aliases() ORDER BY name;
SELECT name, tool_type, read_only, description FROM aidb.tools ORDER BY tool_type, name;-- tools.md
SELECT name, model, role, tools, delegates, max_iterations, timeout_seconds,
       budget_strategy FROM aidb.agents ORDER BY name;                                 -- agents.md
SELECT * FROM aidb.agent_tasks ORDER BY created_at DESC LIMIT 20;                      -- agents.md
```

The installed catalogs are the authority. The tables in `references/` are 7.6.0 stock defaults.

## §3 Routing table — intent to reference

Load **one** reference file on demand; do not preload them all.

| User intent | Go to | Key entry points |
|---|---|---|
| Install / check / configure AIDB, GUCs, `aidb_users` | [sql-functions.md](references/sql-functions.md) | `CREATE EXTENSION aidb CASCADE`, `aidb.egress_allowlist`, `aidb.max_threads` |
| One-off AI in a query: chunk, parse HTML/PDF, PDF→image, OCR, summarize | [sql-functions.md](references/sql-functions.md) | `aidb.chunk_text`, `aidb.parse_pdf`, `aidb.pdf_to_image`, `aidb.perform_ocr`, `aidb.summarize_text`, `aidb.summarize_text_aggregate` |
| Register a model, pick a provider, handle credentials | [models.md](references/models.md) | `aidb.create_model`, `credentials_env`, `aidb.validate_model`, config helpers |
| Embed / generate / rerank directly | [models.md](references/models.md) | `aidb.encode_text`, `aidb.generate_text`, `aidb.rerank_text`, `aidb.inference_config` |
| Audit stored credentials for leaked secrets | [models.md](references/models.md) | `aidb.audit_leaked_credentials()` |
| Build ingestion / RAG indexing, auto-processing modes | [pipelines.md](references/pipelines.md) | `aidb.create_pipeline`, step configs, `aidb.run_pipeline` |
| Pipeline health, errors, retries | [pipelines.md](references/pipelines.md) | `aidb.pipeline_metrics`, `aidb.get_error_logs`, `aidb.get_error_log_summary`, `aidb.get_all_error_summaries`, `aidb.requeue_pipeline_errors`, `aidb.clear_error_logs` |
| Query a vector KB; hybrid vector + full-text search | [knowledge-bases.md](references/knowledge-bases.md) | `aidb.retrieve_text`, `aidb.retrieve_key`, `aidb.kb_query_encode`, `aidb.rerank_text` |
| Ingest files from S3/GCS/Azure/local (volumes) | [knowledge-bases.md](references/knowledge-bases.md) | `aidb.create_volume`, `aidb.volumes`, `aidb.delete_volume` |
| Schema discovery / text-to-SQL / reusable parameterized queries | [knowledge-bases.md](references/knowledge-bases.md) | `aidb.create_semantic_kb`, `aidb.semantic_kb_search`, `aidb.get_column_definitions`, `aidb.create_semantic_alias`, `aidb.execute_semantic_alias` |
| Create / run / debug an agent, budgets, delegation, roles | [agents.md](references/agents.md) | `aidb.create_agent`, `aidb.agent_converse`, `aidb.agents`, `aidb.agent_tasks`, `aidb.conversation_log` |
| Custom SQL tool; native tool catalog; test a tool | [tools.md](references/tools.md) | `aidb.create_sql_tool`, `aidb.tool_param(s)`, `aidb.run_tool`, `aidb.tools` |
| **Import** tools from an external MCP server into AIDB | [tools.md](references/tools.md) | `aidb.import_mcp_tools`, `aidb.refresh_mcp_tools`, `aidb.mcp_registry`, `headers_env` |
| **Expose / serve** AIDB's whole tool catalog to an external agent over MCP | [tools.md](references/tools.md) | `aidb.get_mcp_tools()` — the MCP `tools/list`-shaped descriptor of every native, SQL and MCP tool; `aidb.run_tool()` backs the dispatch |
| Delete things safely (model, pipeline, KB, volume, tool, agent) | matching reference | `aidb.delete_model`, `aidb.delete_pipeline`, `aidb.delete_knowledge_base`, `aidb.delete_volume`, `aidb.delete_tool`, `aidb.delete_agent` |

**Serving note.** `aidb.get_mcp_tools()` (see [tools.md](references/tools.md)) is the in-database
half of MCP serving: it renders every row of `aidb.tools` as an MCP tool descriptor, and a call is
dispatched as `aidb.run_tool(name, args)` under the caller's own Postgres role, so `aidb_users`
membership and table grants remain the security boundary. The HTTP listener itself is a
product-level component configured outside the `aidb` schema (GUCs beginning `edb.endpoints_mcp`).
That listener config is **not** covered by `references/`; before asserting anything about it,
verify on the target and report only what you see:
`SELECT name, setting, context FROM pg_settings WHERE name LIKE 'edb.endpoints_mcp%' ORDER BY name;`
If the result is empty, tell the user this build exposes no such endpoint and that
`aidb.get_mcp_tools()` / `aidb.run_tool()` are still available to any MCP bridge they run themselves.

## §4 Binding rules

1. **Never act immediately.** Summarize the intended action, show the SQL, get explicit
   confirmation, then run it. This applies to every `create_*`, `update_*`, `delete_*`,
   `run_pipeline`, `import_mcp_tools` and `agent_converse` call.
2. **Print the SQL verbatim before executing it. And let the user confirm.** One fenced block, exactly what will run. Ask the user to confirm before executing it.
3. **Never invent** a function, parameter, provider, model, tool or view name. If it is not in
   `references/` *and* not confirmed on the target, say "I can't confirm that exists here."
4. **Verify against the installation, not memory** — §2.5. Offer only providers in
   `aidb.model_providers`, models in `aidb.models`, tools in `aidb.tools`, agents in `aidb.agents`.
5. **Secrets never go in a model's `config`.** Use `credentials`, or better `credentials_env` (the
   variable name is stored, never the value; it must start with `aidb.env_var_allowed_prefix`,
   `AIDB_` by default). 7.6.0 rejects `api_key`/`basic_auth` anywhere in `config`. A third,
   build-dependent option, `credentials_k8s_secret`, exists on some builds — [models.md](references/models.md)
   requires you to confirm it in `pg_proc` before offering it. Quirk: `aidb.gemini_config()` has a
   *required* `api_key` parameter — pass `api_key => NULL` and supply the key via `credentials_env`.
6. **Destructive calls get an explicit warning** naming what cascades — above all
   `aidb.delete_knowledge_base()` (deletes every attached pipeline and drops the vector table) and
   `aidb.delete_tool()` on an MCP server registration (removes every tool that server advertised).
7. **Read-only first.** Prefer `read_only => true` when demonstrating an agent; prefer
   `auto_processing => 'Disabled'` plus a manual `aidb.run_pipeline()` for a first build.

## §5 Guided create flows (the core of this skill)

`create_pipeline`, `create_model`, `create_agent`, `create_sql_tool`, `create_semantic_kb`,
`create_volume` and `import_mcp_tools` all take many arguments. Compile them *with* the user:

1. **Capture hints** from the prompt (table name, file location, "PDFs", "OpenAI", …). Treat them
   as hints, never as accepted input.
2. **Fill gaps with informed guesses**, and present each as a numbered multiple choice grounded in
   what exists here — e.g. embedding model options taken from `aidb.models`, `auto_processing`
   from `Live` / `Background` / `Disabled`, `distance_operator` from `L2` / `Cosine` /
   `InnerProduct`, tools from `aidb.tools`.
3. **Show a filled-in parameter table**, flagging every value you guessed.
4. **Print the final SQL** and ask for a yes/no.
5. **Execute** through the §2 channel, then **verify**: `aidb.pipelines` /
   `aidb.get_pipeline_metrics()` for pipelines, `aidb.models` for models, `aidb.tools` for tools,
   `aidb.agents` for agents, `aidb.knowledge_bases` for KBs.

Intake checklist per object type:

* **Model** — name; provider (from `aidb.model_providers`); `config` via that provider's helper;
  credential path (`credentials_env` preferred); `validate` true/false (false to defer a large
  local download, then `aidb.validate_model()`). Credentials are **per provider**, not per model.
* **Pipeline** — source (table/view or volume) and, for tables, `source_key_column` +
  `source_data_column`; the step sequence and its type compatibility; the embedding model and
  `data_format`; `auto_processing`; name ≤ 46 chars.
* **Vector KB** — created *by* a pipeline's `KnowledgeBase` step (`aidb.knowledge_base_config`), or
  attached to an existing KB with `aidb.knowledge_base_config_from_kb`.
* **Semantic KB** — name, one embedding model, the schemas to crawl, auto-processing.
* **Agent** — name; instructions (they double as the tool description when it is a delegate);
  model from the allowed tier; explicit `tools` array (every native tool must be listed except
  `sleep`); least-privilege `role`; `max_iterations` (≤25), `timeout`, token budgets,
  `budget_strategy`; optional `output_type`; optional `delegates`.
* **SQL tool** — unique name across *all* tool types; description; single read-only `SELECT` with
  `${name}` placeholders; `params` from `aidb.tool_params(aidb.tool_param(...))`; test with
  `aidb.run_tool()` before granting it to an agent.
* **Volume** — name (valid unquoted identifier), PGFS server name, path, `data_format`
  (`Text`/`Image`/`Pdf`). The PGFS storage location must exist first.

Canonical valid step sequences (details and invalid combinations in [pipelines.md](references/pipelines.md)):

```
text column   -> ChunkText -> KnowledgeBase
HTML column   -> ParseHtml -> ChunkText -> KnowledgeBase
digital PDFs  -> ParsePdf -> ChunkText -> KnowledgeBase
scanned PDFs  -> PdfToImage -> PerformOcr -> ChunkText -> KnowledgeBase
long text     -> SummarizeText -> KnowledgeBase
images        -> KnowledgeBase (data_format => 'Image', image-capable model)
```

## §6 The two "knowledge bases" — never conflate them

| | Vector knowledge base | Semantic knowledge base |
|---|---|---|
| Indexes | Your **data** | Your **schema** (tables, views, columns, comments) |
| Created by | `aidb.create_pipeline()` + a `KnowledgeBase` step | `aidb.create_semantic_kb()` |
| Queried with | `aidb.retrieve_text()`, `aidb.retrieve_key()` | `aidb.semantic_kb_search()`, `aidb.get_metadata()`, `aidb.get_column_definitions()`, `aidb.get_entity_definitions()`, `aidb.search_by_comment()` |
| Answers | "What content is about X?" | "Which table holds X?" — the basis for text-to-SQL |

Ask which one the user means whenever the phrase is ambiguous. Both live in
[knowledge-bases.md](references/knowledge-bases.md).

## §7 Constraints to state up front

* A pipeline has at most **10 steps**; `create_pipeline` caps the name at **46 characters**.
* A `KnowledgeBase` step **must be last** — it emits a `VECTOR`, which no step can consume.
* Step sequences are type-checked (Text / Bytes / Vector) and rejected at creation time.
* Models are validated at **pipeline creation**, not at execution.
* `aidb.update_pipeline()` changes only auto-processing settings; steps, source and destination are
  immutable — recreate the pipeline instead.
* HNSW supports at most **2000 dimensions**; above that use `aidb.vector_index_disabled_config()`.
* `aidb.delete_knowledge_base()` cascades to every attached pipeline and drops the vector table;
  use `aidb.delete_pipeline()` to detach just one.
* Agents: hard ceilings of **25 reasoning iterations** and **10 delegation levels** always apply;
  default timeout 300s. Most agent functions return an `error` column instead of raising — always
  select it.
* Embedding, reranking, OCR and multimodal-embedding models cannot back an agent; `t5_local` is
  rejected outright.
* **MCP tools are always excluded from read-only agent runs** (AIDB cannot verify an external
  server), and `read_only` runs persist nothing — `conversation_id` comes back NULL.
* Tool names are unique across native, SQL and MCP tools; **native tools cannot be deleted**.
* **No native tool authors pipelines** — an agent cannot build a multi-step pipeline itself. Author
  pipelines from SQL with user confirmation.
* Outbound calls (model endpoints, MCP servers, model downloads) are gated by
  `aidb.egress_allowlist`; a blocked host looks like a plain network error.
* `aidb.max_threads` changes require a **database restart**.

## §8 Reference map

Load on demand; each file is a signature-level reference for AIDB 7.6.0.

* [references/models.md](references/models.md) — providers (local and remote), `aidb.create_model`,
  the credentials rules (`credentials`, `credentials_env`, build-dependent `credentials_k8s_secret`,
  `aidb.audit_leaked_credentials()`), inference functions, `inference_config`, tool calling and
  structured output, every config helper, the stock default models.
* [references/pipelines.md](references/pipelines.md) — step/type compatibility, enums,
  `aidb.pipelines` and `aidb.pipeline_metrics`, CRUD, every step option helper, intermediate
  storage, vector-index helpers, and the error log
  (`get_error_logs`, `get_error_log_summary`, `get_all_error_summaries`, `clear_error_logs`,
  `requeue_pipeline_errors`).
* [references/knowledge-bases.md](references/knowledge-bases.md) — `aidb.knowledge_bases` and
  `aidb.knowledge_base_metrics`, `retrieve_text`/`retrieve_key`, hybrid-search building blocks
  (`aidb.kb_query_encode`, RRF pattern), volumes (`create_volume`, `aidb.volumes`, `delete_volume`),
  and the whole semantic KB + semantic alias surface.
* [references/agents.md](references/agents.md) — agent CRUD, `agent_converse`, `aidb.agents`,
  `aidb.agent_tasks`, conversations and sessions, read-only and debug modes, budgets, roles,
  structured output, delegation.
* [references/tools.md](references/tools.md) — `aidb.tools`, `aidb.run_tool`, custom SQL tools,
  MCP **import** (`import_mcp_tools`, `refresh_mcp_tools`, `mcp_registry`), MCP **serving/exposure**
  of AIDB's own catalog via `aidb.get_mcp_tools()`, and the full native tool catalog by category.
* [references/sql-functions.md](references/sql-functions.md) — installation and runtime checks,
  standalone transformations (chunk, parse HTML/PDF, PDF→image, OCR, summarize) and query-side
  embedding.

## §9 Troubleshooting triage

| Symptom | First move |
|---|---|
| `aidb.*` function/view does not exist | Extension not installed, or version drift — §2.3 / §2.4, then `pg_proc` |
| No `aidb.%` rows in `pg_settings` | Library not preloaded: add `aidb` to `shared_preload_libraries`, restart |
| `permission denied` on an `aidb` object | Role is not in `aidb_users` (§2.3) |
| `Model provider with name "X" not found` | Not registered on this build — re-check `aidb.model_providers`; see the HuggingFace TEI note in models.md |
| `config must not contain "api_key" or "basic_auth"` | Move the secret to `credentials_env` (rule §4.5) |
| `Credentials for model provider "X" already exist` | Credentials are per provider: omit them, or `replace_credentials => true` (overwrites for all models on that provider) |
| Pipeline stuck `Stale` | `auto_processing => 'Disabled'` — call `aidb.run_pipeline()` |
| Pipeline `NoResults` | Empty source, or `source_data_column` points at the wrong column |
| Pipeline `BlockingErrors` | `aidb.get_error_log_summary()` → fix cause → re-run → `aidb.clear_error_logs()`; record-level failures can be re-driven with `aidb.requeue_pipeline_errors()` |
| `retrieve_text` returns NULL `value` | Source is binary (PDF/Image) — use `key` and join the source table yourself |
| Retrieval returns nothing | Pipeline never ran (`aidb.pipeline_metrics`), or query/model mismatch; for semantic KBs lower `min_similarity` |
| Agent returns a populated `error` | Model missing or not generation-capable; unknown tool name; budget/iteration/timeout exceeded — check `aidb.agent_tasks.status` |
| Agent never calls its tools | Provider only simulates tool calls — shorten the tool list or move to `openai_responses`/`anthropic_messages`/`llamacpp_generate` |
| Agent `conversation_id` is NULL | The run was read-only (explicitly, or automatically on a replica) |
| Tool not found, hint mentions refresh | MCP cache expired — `aidb.refresh_mcp_tools('<server>')` |
| MCP import fails | URL unreachable, `tool_filter` matched nothing, or the host is blocked by `aidb.egress_allowlist` |