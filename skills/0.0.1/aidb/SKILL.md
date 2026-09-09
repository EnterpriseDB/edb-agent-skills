---
name: aidb
description: Operate EDB AIDB (Postgres extension, target 7.6.0) entirely from SQL — register local/remote AI models, build pipelines that parse/chunk/OCR/summarize/embed data into pgvector knowledge bases, run semantic and hybrid search, index schema metadata for text-to-SQL, register SQL/MCP tools, and build in-database agents. Use this skill whenever the user mentions aidb, ai-factory, AI Factory, aidb.create_model/create_pipeline/create_agent, aidb.retrieve_text, knowledge bases, semantic knowledge bases, semantic aliases, aidb.tools/run_tool, MCP tools in Postgres, or RAG/embeddings/agents inside EDB Postgres.
metadata:
  version: "1.0.0"
  target_product: "EDB AIDB 7.6.0"
  aliases: "aidb, ai-factory"
  postgres_support: "PostgreSQL 14-18, EDB Postgres Advanced Server, EDB Postgres Extended"
---

# AIDB — AI workflows inside Postgres

AIDB is an EDB-maintained Postgres extension. **Everything is SQL** — there is no separate
service, API, or SDK. It runs inside Postgres, has no runtime dependency on Agent Factory or
Hybrid Manager (though it ships with them), and with a local model no data leaves the database.

Installed with `shared_preload_libraries = 'aidb'` → restart → `CREATE EXTENSION aidb CASCADE;`
(`CASCADE` pulls in `vector`). `CREATE EXTENSION pgfs;` adds S3/GCS/Azure/local volumes. Access is
granted through the **`aidb_users`** role (`GRANT aidb_users TO alice;`) — which grants the AIDB API,
*not* the user's own tables; grant table privileges separately.

## The four building blocks

| Block | What it is |
|---|---|
| **Pipelines** | Declarative ≤10-step workflows: parse, chunk, OCR, summarize, embed. Run manually, on a background worker, or on trigger. |
| **Knowledge bases** | The output of a pipeline's `KnowledgeBase` step: a pgvector table queried with `aidb.retrieve_text()` / `aidb.retrieve_key()` or hybrid search. |
| **Agents** *(7.6.0)* | Named config of instructions + model + allowed tools, run in a ReAct loop. Can delegate to other agents. |
| **Tools** *(7.6.0)* | What an agent calls: native tools, your parameterized SQL, or tools imported from an MCP server. All unified in `aidb.tools`. |

Every pipeline step operation is **also** a standalone SQL function — no pipeline required.

### Two different things are called "knowledge base" — never conflate them

| | Vector knowledge base | Semantic knowledge base |
|---|---|---|
| Indexes | Your **data** | Your **schema** (tables, views, columns, comments) |
| Created by | `aidb.create_pipeline()` + `KnowledgeBase` step | `aidb.create_semantic_kb()` |
| Answers | "What content is about X?" | "Which table holds X?" — the basis for text-to-SQL |

## Binding rules — follow these on every task

1. **Never write without confirmation.** Creating a model, pipeline, agent, tool, volume, or
   deleting anything: summarize the action, print the SQL, wait for the user to approve.
2. **Print the exact SQL verbatim before executing it.** Not a paraphrase.
3. **Emit SQL that runs on any driver.** No psql-only syntax (`:'var'`, `\set`, `\if`) in SQL you
   intend to execute — use `$1` placeholders and state the binding, or an explicit quoted literal.
4. **Never invent** a function, parameter, provider, or model name. If it is not in
   `references/`, say so rather than guessing.
5. **Verify the installation first** (`aidb.model_providers`, `aidb.models`, `aidb.pipelines`,
   `aidb.knowledge_bases`, `aidb.tools`, `aidb.agents`) instead of assuming defaults.
6. **Never put a secret in a model's `config`.** Use `credentials`, or better `credentials_env`
   (only the variable *name* is stored). 7.6.0 rejects credentials embedded in `config`.
7. **`aidb.decode_text()` / `decode_text_batch()` are deprecated** — always use
   `aidb.generate_text()` / `aidb.generate_text_batch()`.
8. Offer **options** on complex creates (`create_pipeline`: which embedding model, chunk size,
   index type, auto-processing mode) rather than silently choosing.

---

## Step 0 — Preflight (read-only, always run first)

```sql
SELECT extname, extversion FROM pg_extension WHERE extname IN ('aidb','vector','pgfs') ORDER BY 1;
SELECT name, setting, context FROM pg_settings
 WHERE name LIKE 'aidb.%' OR name = 'edb.egress_allowlist' ORDER BY 1;
SELECT server_name, server_description FROM aidb.model_providers ORDER BY 1;
SELECT name, provider FROM aidb.models ORDER BY 1;
SELECT * FROM aidb.audit_leaked_credentials();
SELECT name, source_type, source, destination, auto_processing FROM aidb.pipelines ORDER BY 1;
SELECT pipeline, "Status", "count(record errors)", "count(blocking errors)", "last run completed"
  FROM aidb.pipeline_metrics ORDER BY 1;
SELECT name, model_name, distance_operator, pipeline_names FROM aidb.knowledge_bases ORDER BY 1;
SELECT * FROM aidb.list_semantic_kbs();
SELECT tool_type, count(*) FROM aidb.tools GROUP BY 1 ORDER BY 1;
SELECT name, model, tools, delegates FROM aidb.agents ORDER BY 1;
```

Interpretation: any row from `audit_leaked_credentials()` is a real leaked secret — report it and
propose re-registering that model with `credentials_env`. A pipeline in `Failed`,
`BlockingErrors`, or `PartialErrors` gets triaged (Workflow F) before anything new is built.
`aidb.agents` / `aidb.tools` missing ⇒ the installation predates 7.6.0; say so, don't guess.

### Runtime configuration (all nine `aidb.*` GUCs)

| GUC | Purpose |
|---|---|
| `aidb.max_threads` | Max CPU threads for **local** model inference (Candle, llama.cpp). `0` = half the CPUs. `SUSET`. |
| `aidb.max_io_threads` | Max threads for AIDB's async **I/O** runtimes (remote model HTTP, volume/object-store I/O). `0` = `TOKIO_WORKER_THREADS` or one per CPU. `SUSET`. |
| `aidb.download_log_level` | Channel for HuggingFace download/model-load progress messages: `notice` (default) / `log` / `off`. |
| `aidb.download_max_attempts` | Cap on total download attempts per model file (default `30`, range 1–500). Lower for fail-fast CI. |
| `aidb.enable_llamacpp_logs` | Whether llama.cpp's own verbose diagnostics reach the server log. Default `false`. `SUSET`, **read once at extension init — needs a Postgres restart to change**. |
| `aidb.egress_allowlist` | Outbound host/CIDR allowlist covering model provider calls, MCP servers, and model downloads. Unset ⇒ no restriction. `SUSET`. |
| `aidb.env_var_allowed_prefix` | Required prefix (default `AIDB_`) for env-var names usable by `credentials_env` / `headers_env`. Superuser-only. |
| `aidb.pipeline_error_warnings` | When on (default), each logged pipeline error also raises a Postgres `WARNING`. Errors persist to the log either way. `SUSET`. |
| `aidb.allow_insecure_egress` / `aidb.allow_insecure_tls` | **Dangerous, off by default**: permit plaintext HTTP to loopback/private hosts / skip TLS verification. Never enable in production. |

`edb.egress_allowlist` is the shared cross-extension fallback used when `aidb.egress_allowlist` is
unset. Thread GUCs apply live (session `SET` immediately; `ALTER SYSTEM` + `pg_reload_conf()`
propagates); older EDB material describes a restart — a restart is always sufficient.

---

## Workflow A — Register a model

Confirm the provider exists first (`SELECT server_name FROM aidb.model_providers ORDER BY 1;`).

```sql
-- Remote: the secret is never persisted; only the variable name is stored.
SELECT aidb.create_model(
    name            => 'my-embeddings',
    provider        => 'openai_embeddings',
    config          => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials_env => 'AIDB_OPENAI_API_KEY');
```

```sql
-- Local llama.cpp providers have no config helper: build config with jsonb_build_object().
SELECT aidb.create_model(
    name     => 'local-embed',
    provider => 'llamacpp_embeddings',
    config   => jsonb_build_object('model', 'unsloth/bge-small-en-v1.5-GGUF',
                                   'model_file', 'bge-small-en-v1.5-f16.gguf',
                                   'n_ctx', 512),
    validate => false);            -- defer the large download
SELECT aidb.validate_model('local-embed');
```

Notes: `validate => true` (default) runs a probe inference and registers nothing on failure. The
env var name must start with `aidb.env_var_allowed_prefix` (`AIDB_`). Default models exist out of
the box (`bert`, `clip`, `t5`, `llama`, `bge-m3-f16`, `qwen3-embedding-*`, `qwen3.5-0.8b-Q8_0`,
`lightonocr-2-1b-Q8_0`, `dummy`, …) — confirm with `SELECT name, provider FROM aidb.models`.
Full provider list, every config helper, `inference_config`, tool calling / structured output,
and embedding context windows: **[references/models.md](references/models.md)**.

## Workflow B — RAG: pipeline → knowledge base → retrieval

### B1. Create the pipeline (offer the user options before running this)

```sql
SELECT aidb.create_pipeline(
    name                     => 'docs_kb',
    source                   => 'public.documents',
    source_key_column        => 'id',
    source_data_column       => 'body',
    auto_processing          => 'Background',
    batch_size               => 100,
    background_sync_interval => INTERVAL '5 minutes',
    step_1                   => 'ChunkText',
    step_1_options           => aidb.chunk_text_config(desired_length => 800,
                                                       overlap_length => 100),
    step_2                   => 'KnowledgeBase',
    step_2_options           => aidb.knowledge_base_config(
                                    model             => 'bge-m3-f16',
                                    data_format       => 'Text',
                                    distance_operator => 'Cosine',
                                    vector_index      => aidb.vector_index_hnsw_config(
                                                             m => 16, ef_construction => 64)));

SELECT aidb.run_pipeline('docs_kb');                  -- Disabled/manual or first fill
SELECT * FROM aidb.get_pipeline_metrics('docs_kb');
```

Choices to surface: embedding model (match chunk size to its context window), `desired_length` /
`overlap_length`, `distance_operator` (`Cosine` for normalized embeddings, `L2` default),
`auto_processing` (`Live` = synchronous triggers, `Background` = worker, `Disabled` = manual),
and vector index (`aidb.vector_index_disabled_config()` above 2000 dimensions — HNSW's ceiling).

Destination defaults to `pipeline_<name>`, so the KB above is `public.pipeline_docs_kb`.

### B2. Retrieve

```sql
SELECT key, value, distance, part_ids
FROM aidb.retrieve_text('public.pipeline_docs_kb', 'how do I rotate credentials?', topk => 5);
```

`value` is NULL when the source content is non-text (PDF/Image) — the other columns still fill.
Both retrieval functions embed the query with the KB's own model via the query-side path, so you
never pass a model name.

### B3. Hybrid search (vector + full-text)

Bind the user's query text to `$1`; every occurrence is the same parameter. If your client cannot
bind parameters, substitute one properly quoted literal for each `$1`.

```sql
-- $1 := the user's natural-language query (text)
WITH vector_hits AS (
    SELECT key, value
    FROM aidb.retrieve_text('public.pipeline_docs_kb', $1, topk => 20)
),
lexical_hits AS (
    SELECT d.id::text AS key, d.body AS value
    FROM documents d
    WHERE to_tsvector('english', d.body) @@ plainto_tsquery('english', $1)
    ORDER BY ts_rank(to_tsvector('english', d.body), plainto_tsquery('english', $1)) DESC
    LIMIT 20
),
candidates AS (
    SELECT key, value FROM vector_hits
    UNION
    SELECT key, value FROM lexical_hits
),
agg AS (
    SELECT array_agg(value ORDER BY key) AS vals FROM candidates
)
SELECT c.key, r.logit_score, r.text
FROM agg a
CROSS JOIN LATERAL aidb.rerank_text('my-reranker', $1, a.vals) AS r
JOIN candidates c ON c.value = r.text
ORDER BY r.logit_score DESC
LIMIT 10;
```

No reranking model registered? Fuse with Reciprocal Rank Fusion in plain SQL instead:

```sql
-- $1 := the user's natural-language query (text)
WITH v AS (
    SELECT key, row_number() OVER (ORDER BY distance) AS vrank
    FROM aidb.retrieve_text('public.pipeline_docs_kb', $1, topk => 20)
),
l_scored AS (
    SELECT d.id::text AS key,
           ts_rank(to_tsvector('english', d.body), plainto_tsquery('english', $1)) AS score
    FROM documents d
    WHERE to_tsvector('english', d.body) @@ plainto_tsquery('english', $1)
    ORDER BY score DESC
    LIMIT 20
),
l AS (SELECT key, row_number() OVER (ORDER BY score DESC) AS lrank FROM l_scored)
SELECT COALESCE(v.key, l.key) AS key,
       COALESCE(1.0 / (60 + v.vrank), 0) + COALESCE(1.0 / (60 + l.lrank), 0) AS rrf_score
FROM v FULL JOIN l ON l.key = v.key
ORDER BY rrf_score DESC
LIMIT 10;
```

Edge case: if `candidates` is empty, `a.vals` is NULL and `rerank_text` gets no input — guard in
the application. To filter or join before ranking, encode with `aidb.kb_query_encode(kb, $1)::vector`
and query the vector table directly (read its shape from `aidb.knowledge_bases`); see
**[references/knowledge-bases.md](references/knowledge-bases.md)**.

### B4. Documents, PDFs, images, object storage

```sql
-- S3/GCS/Azure/local via PGFS (create the PGFS storage location first)
SELECT aidb.create_volume('docs_vol', 'my_s3_store', 'reports/', 'Pdf');
```

```sql
-- Scanned PDFs with no text layer: render pages, OCR them, chunk, embed.
SELECT aidb.create_pipeline(
    name               => 'scanned_pdfs',
    source             => 'public.pdf_docs',
    source_key_column  => 'id',
    source_data_column => 'content',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi":300,"max_pages":20,"format":{"type":"png"}}'::jsonb,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config(model => 'my-ocr'),
    step_3             => 'ChunkText',
    step_3_options     => aidb.chunk_text_config(desired_length => 800),
    step_4             => 'KnowledgeBase',
    step_4_options     => aidb.knowledge_base_config(model => 'bge-m3-f16', data_format => 'Text'));
```

For digitally produced PDFs use `ParsePdf` instead — faster and needs no OCR model. All step
operations, enums, config helpers, intermediate storage, and index helpers:
**[references/pipelines.md](references/pipelines.md)**.

## Workflow C — Semantic KB and text-to-SQL

```sql
SELECT aidb.create_semantic_kb('sales_kb', 'bge-m3-f16', ARRAY['public','sales'], 'Background');
SELECT * FROM aidb.semantic_kb_stats('sales_kb');

-- Composite search: schema metadata + semantic aliases, fused with RRF (7.6.0).
SELECT source_type, entity_type, object_ref, definition, score, rank
FROM aidb.semantic_kb_search('which table holds invoice totals', 'sales_kb', 10);

-- Leanest grounding for SQL generation:
SELECT * FROM aidb.get_column_definitions('sales_kb', 'invoice total amount');
```

Reusable parameterized query ("semantic alias") — findable by meaning, always read-only:

```sql
SELECT aidb.create_semantic_alias(
    name        => 'top_customers',
    description => 'Rank customers by lifetime value / total spend',
    query_text  => 'SELECT * FROM sales.customer_ltv ORDER BY lifetime_value DESC LIMIT ${n}',
    params      => aidb.alias_params(aidb.alias_param('n', 'integer', 'How many customers')));

SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 10}'::jsonb);
```

`kb_name` may be omitted on most semantic-KB functions when exactly one KB exists (7.6.0).
A well-commented schema produces a far better KB — comments are embedded alongside definitions.
Alias functions are deliberately **not** agent tools. Details:
**[references/knowledge-bases.md](references/knowledge-bases.md)**.

## Workflow D — Tools and agents (7.6.0)

### D1. Custom SQL tool

```sql
SELECT aidb.create_sql_tool(
    name          => 'orders_for_customer',
    description   => 'Recent orders for a customer, by email address.',
    sql_statement => 'SELECT o.id, o.total, o.created_at FROM orders o '
                     'JOIN customers c ON c.id = o.customer_id '
                     'WHERE c.email = ${email} ORDER BY o.created_at DESC LIMIT ${n}',
    params        => aidb.tool_params(
                         aidb.tool_param('email', 'text', 'Customer email address'),
                         aidb.tool_param('n', 'integer', 'How many orders to return')),
    read_only     => true);

SELECT aidb.run_tool('orders_for_customer', '{"email":"a@example.com","n":5}'::jsonb);
```

`${name}` values are bound as real query parameters, never interpolated. `read_only => true` is
enforced: registration fails if the statement matches a write pattern.

### D2. Import tools from an external MCP server (AIDB as MCP *client*)

```sql
SELECT aidb.import_mcp_tools(
    name        => 'weather',
    url         => 'https://weather.example.com/mcp',
    transport   => 'streamable_http',
    tool_filter => ARRAY['get_forecast'],
    headers_env => 'AIDB_WEATHER_HEADERS');   -- JSON headers, read fresh, never stored

SELECT aidb.refresh_mcp_tools('weather');     -- cache is 60 minutes
```

Only `streamable_http` works end-to-end today. Reaching the server is outbound egress, subject to
`aidb.egress_allowlist`. `aidb.delete_tool('weather')` removes the server **and every tool it
advertised**.

### D3. Expose AIDB's whole tool catalog over MCP (AIDB as MCP *server* side)

```sql
SELECT name, description, input_schema FROM aidb.get_mcp_tools();
```

This converts **every** entry in `aidb.tools` — native, SQL, and imported MCP tools alike — into
MCP `tools/list`-shaped descriptors, one row per tool, so an external agent platform can consume
AIDB's catalog in MCP's own format. AIDB does not itself listen on an MCP endpoint: publish these
descriptors from a thin external server and route each invocation back to
`aidb.run_tool(name, arguments)`. Full catalog by category and the `aidb.tools` /
`aidb.sql_tool_registry` / `aidb.mcp_registry` views: **[references/tools.md](references/tools.md)**.

### D4. Create and converse with an agent

```sql
SELECT * FROM aidb.create_agent(
    name            => 'support_bot',
    instructions    => 'Answer support questions. Search the docs knowledge base before answering; '
                       'cite the source key. Say you do not know rather than guessing.',
    model           => 'my-gpt',
    tools           => ARRAY['run_sql_query','semantic_kb_search','orders_for_customer'],
    max_iterations  => 8,
    timeout         => 120,
    budget_strategy => 'summarize');

SELECT message, conversation_id, error
FROM aidb.agent_converse('support_bot', 'Where do I rotate my API key?');

-- Continue that conversation:
SELECT message, error
FROM aidb.agent_converse('support_bot', 'And for Azure?', conversation_id => '<uuid-from-above>');

-- Safe exploration: nothing is persisted, non-read-only and all MCP tools are excluded.
SELECT message, error
FROM aidb.agent_converse('support_bot', 'Summarise our top accounts', read_only => true);
```

`create_agent` returns `TABLE(error TEXT)` and most agent functions **return an `error` column
instead of raising** — always select it. Model must be instruction-following: embedding,
reranking, OCR, and multimodal-embedding providers are rejected, and `t5_local` outright.
Prefer `openai_responses`, `anthropic_messages`, or `llamacpp_generate` (native tool calls).
Budgets, delegation, roles, debug mode, structured output:
**[references/agents.md](references/agents.md)**.

## Workflow E — One-off AI operations, no pipeline

Every step operation is a standalone function. Set-returning ones (`chunk_text`, `parse_pdf`,
`pdf_to_image`, `perform_ocr`) must be joined **laterally** against the source table:

```sql
SELECT a.id, c.part_id, c.chunk
FROM articles a,
     LATERAL aidb.chunk_text(a.body, aidb.chunk_text_config(desired_length => 800)) AS c
ORDER BY a.id, c.part_id
LIMIT 20;
```

```sql
-- Scalar functions can be called inline.
SELECT aidb.summarize_text(a.body,
         aidb.summarize_text_config(model => 'qwen3.5-0.8b-Q8_0', strategy => 'reduce')) AS summary
FROM articles a WHERE a.id = 1;

SELECT aidb.generate_text('my-gpt', 'Summarise AIDB in one sentence.',
         aidb.inference_config(temperature => 0.0, max_tokens => 200)::json) AS answer;

SELECT aidb.encode_text('bge-m3-f16', 'hello world')::vector AS embedding;
```

```sql
-- One summary per group; options is REQUIRED here and must be ::json.
SELECT topic,
       aidb.summarize_text_aggregate(body,
           aidb.summarize_text_config(model => 'qwen3.5-0.8b-Q8_0')::json
           ORDER BY published_at) AS topic_summary
FROM articles GROUP BY topic;
```

Signatures and option keys: **[references/sql-functions.md](references/sql-functions.md)**.

## Workflow F — Triage a failing pipeline

```sql
SELECT * FROM aidb.get_pipeline_metrics('docs_kb');
SELECT * FROM aidb.get_error_log_summary('docs_kb');
SELECT id, source_id, pipeline_step, step_operation, error_category,
       left(error_message, 200) AS error_message, retry_count, last_seen_at
FROM aidb.get_error_logs(p_pipeline_name => 'docs_kb', p_limit => 20)
ORDER BY last_seen_at DESC;
```

If a call errors with "function … does not exist", pass every parameter explicitly, using `NULL`
for the filters you don't need. Then act on `error_category`:

| Category | Meaning | Action |
|---|---|---|
| `RecordTemporary` | Transient, one record (today: model rate limits) | `SELECT * FROM aidb.requeue_pipeline_errors(p_pipeline_name => 'docs_kb');` then re-run |
| `RecordPermanent` | Bad input for that record | Fix the source row or accept the skip |
| `PipelineTemporary` | Network/outage; blocks the step | Retry later; check `aidb.egress_allowlist` |
| `PipelinePermanent` | Deleted model, invalid config | Fix the cause, re-run, then `aidb.clear_error_logs('docs_kb')` |

`requeue_pipeline_errors` only affects **record-level** entries; it deletes them and marks sources
dirty — it performs no processing itself. Error-log table lives at
`{source_schema}.pipeline_{name}_errors`.

---

## Constraints and gotchas (state these before proposing a design)

- Max **10 steps** per pipeline; name capped at **46 characters** by `create_pipeline`.
- `KnowledgeBase` **must be the last step** — its output is a `VECTOR` no later step can consume.
- Step sequences must be compatible; incompatible ones are rejected at creation time.
- Models are validated at **pipeline creation** time, not execution time.
- `aidb.update_pipeline()` only changes auto-processing settings — steps, source, and destination
  are immutable. Recreate the pipeline to change them.
- HNSW supports **≤2000 dimensions**; above that use `aidb.vector_index_disabled_config()`.
- `aidb.delete_knowledge_base()` **cascades**: every attached pipeline is deleted and the vector
  table dropped. To detach one pipeline, use `aidb.delete_pipeline()` instead.
- Agents: hard ceiling of **25 reasoning iterations** and **10 delegation levels** regardless of
  config; default timeout 300 s. Read-only mode always excludes MCP tools.
- Tool names must be unique across all three tool types; **native tools cannot be deleted**.
- **Pipeline authoring has no native agent tool** — an agent cannot build multi-step pipelines.
- `aidb.tools` serves MCP entries from a ≤60-minute cache; a `SELECT` never makes a network call.

## Reference map (load on demand)

| File | Contents |
|---|---|
| [references/models.md](references/models.md) | Providers, `create_model`, `credentials_env`, inference functions, `inference_config`, tool calling / structured output, every config helper, default models and their context windows |
| [references/pipelines.md](references/pipelines.md) | Pipeline types and enums, views, CRUD, all step operations + config helpers, intermediate storage, vector index helpers, the error log |
| [references/knowledge-bases.md](references/knowledge-bases.md) | KB views, `retrieve_text`/`retrieve_key`, hybrid-search helpers, volumes, and the whole semantic KB + semantic alias surface |
| [references/agents.md](references/agents.md) | Agent CRUD, `agent_converse`, conversations/sessions, read-only and debug modes, budgets, roles, structured output, delegation |
| [references/tools.md](references/tools.md) | `aidb.tools`, `run_tool`, custom SQL tools, MCP import, `get_mcp_tools`, and the full native tool catalog by category |
| [references/sql-functions.md](references/sql-functions.md) | Standalone transformations: chunk, parse HTML/PDF, PDF→image, OCR, summarize, query-side embedding |

## What changed in 7.6.0 (supersedes older behavior)

Agents and tools are entirely new · native tool-calling adapters `openai_responses` and
`anthropic_messages` (+ Azure/Bedrock variants) · `credentials_env`, `create_model` now rejects
credentials in `config`, new `aidb.audit_leaked_credentials()` · local reranking
(`llamacpp_reranking`) and local OCR (`llamacpp_ocr`) · `decode_text` → `generate_text` ·
`aidb.semantic_kb_search()` with RRF fusion · `kb_name` optional when a single semantic KB exists ·
semantic aliases dropped their `model` argument and may belong to multiple KBs ·
`last_run_completed` added to pipeline metrics.