---
name: aidb
description: Operate EDB Postgres AI's AIDB extension (AI Accelerator Pipelines, part of AI Factory) entirely through SQL. Use when a user asks to build RAG or embedding pipelines in PostgreSQL, register AI models (local, OpenAI-compatible, NIM, Gemini, OpenRouter), chunk/summarize/parse/OCR text, run vector retrieval, build a Semantic Knowledge Base or Semantic Aliases for text-to-SQL, ingest from object-storage volumes, or troubleshoot pipeline errors, background workers, model credentials and auto-processing modes. Triggers on "aidb", "ai-factory", "EDB Postgres AI", "aidb.create_pipeline", "knowledge base", "retrieve_text", "semantic kb".
metadata:
  aliases: [aidb, ai-factory]
  product: EDB Postgres AI — AI Accelerator (AIDB)
  interface: SQL (schema `aidb`)
  supported_postgres: "14–18"
  docs: https://www.enterprisedb.com/docs/aidb/latest/
---

# AIDB — AI Accelerator Pipelines inside PostgreSQL

AIDB is a PostgreSQL extension. **Everything is SQL functions in the `aidb` schema.** There is
no CLI, no REST API to call, no Python SDK required. You help the user by writing and running
SQL against their database.

Five capability areas (this is the whole supported surface):

1. **Pipelines** — source (table or volume) → ordered AI steps → destination table.
2. **Models** — register once by name, reuse everywhere.
3. **Standalone AI functions** — embed, generate, rerank, chunk, summarize, parse HTML/PDF, OCR.
4. **Semantic Knowledge Base** — embeds a schema's metadata for natural-language schema search.
5. **Semantic Aliases** — named parameterized SQL, discoverable by semantic search.

---

## Safety rules — read before running anything

1. **Probe before you propose.** Run Step 0. Provider lists, view names, GUCs and installed
   versions differ per build.
2. **Never echo secrets.** Use `credentials => jsonb_build_object('api_key', current_setting('my.key'))`
   or `credentials_env => 'MY_API_KEY'`. Never paste a key into the transcript or into `config`.
3. **Never invent a function, provider, option key, view name or numeric limit.** If you are not
   sure it exists in this build, discover it (`\df aidb.*`, `\dv aidb.*`, `SELECT * FROM aidb.model_providers;`)
   or read `references/function-reference.md`. If the product rejects something, **quote its error
   message verbatim** — AIDB errors state the exact limit and the remedy.
4. **Confirm destructive actions explicitly** (see "Destructive operations" below).
5. **Backfill before automating.** Create pipelines with `auto_processing => 'Disabled'`, run once,
   verify, then switch to `Background`.
6. **One statement at a time when it matters.** `create_model` and `create_pipeline` roll back on
   failure; do not bundle them with unrelated DDL.

---

## Step 0 — environment probe (READ-ONLY, run first)

Save as a file and run `psql -X -v ON_ERROR_STOP=0 -P pager=off -f probe.sql`, or paste into psql.
A section that errors *is* the answer (e.g. `schema "aidb" does not exist` ⇒ not installed).

```sql
-- 0. server version: AIDB supports PostgreSQL 14–18
SELECT current_database(), current_user, version(),
       current_setting('server_version_num')::int AS version_num;

-- 1. extensions
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('aidb','vector','pgfs','vchord') ORDER BY extname;

-- 2. background workers need 'aidb' preloaded (restart to change)
SHOW shared_preload_libraries;

-- 3. runtime config (pending_restart = t means "set but not active")
SELECT name, setting, unit, context, pending_restart
FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'pgfs.%' ORDER BY name;

-- 4. providers compiled into THIS build — never assume
SELECT server_name, server_description FROM aidb.model_providers ORDER BY server_name;

-- 5. what objects/functions this build actually exposes (views are version-suffixed)
SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'aidb' AND c.relkind IN ('r','v','m') ORDER BY 2,1;     -- psql: \dv aidb.*
SELECT p.proname, pg_get_function_identity_arguments(p.oid)
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'aidb' ORDER BY 1,2;                                    -- psql: \df aidb.*

-- 6. inventory
SELECT name, provider FROM aidb.models ORDER BY name;
SELECT name, source, destination, auto_processing FROM aidb.pipelines_v7 ORDER BY name;
SELECT * FROM aidb.get_all_error_summaries();
SELECT * FROM aidb.list_semantic_kbs();
SELECT * FROM aidb.list_volumes();

-- 7. may the current role use AIDB?
SELECT current_user, pg_has_role(current_user, 'aidb_users', 'MEMBER') AS in_aidb_users;
```

**Interpretation** (full table: `references/step-operations.md`, "Operations runbook"):

| Observation | Meaning / next action |
|---|---|
| `version_num` < 140000 or ≥ 190000 | AIDB documents support for **PostgreSQL 14–18**. Warn the user before doing anything else. |
| no `aidb` row in §1 | Ask, then `CREATE EXTENSION IF NOT EXISTS aidb CASCADE;` (`CASCADE` pulls in pgvector) |
| `aidb` present, `vector` absent | CASCADE was skipped — `KnowledgeBase` steps will fail |
| `aidb` not in `shared_preload_libraries` | `Background` mode never advances; use `aidb.run_pipeline()` until an admin adds it **and restarts** |
| `pending_restart = t` on `aidb.max_threads` | new value not in effect yet |
| desired provider missing from §4 | pick another; `create_model` would fail with `Model provider with name "X" not found` |
| §5 has no `pipeline_metrics_v7` | this build names its views differently — use the §5 list, and `SELECT * FROM aidb.get_pipeline_metrics('<pipeline>');` |
| `in_aidb_users = f` and not superuser | ask an admin for `GRANT aidb_users TO <role>;` |

---

## Mental model

* A **pipeline** = `source` → `step_1..step_N` → `destination` table.
* Destination columns are always `id`, `source_id`, `part_ids`, and the payload column named
  **`value`** (TEXT, BYTEA, or `VECTOR(n)` depending on the terminal step).
* **Auto-processing modes:**

| Mode | Mechanism | Blocks writers? | Use when |
|---|---|---|---|
| `Live` | row triggers on the source table | yes — inference inside the write txn | low write volume, freshness matters |
| `Background` | worker drains in `batch_size` batches every `background_sync_interval` | no | production ingest, bulk loads |
| `Disabled` (default) | nothing until `aidb.run_pipeline()` | no | ETL / manual control |

* **Argument-order rule:** model-invoking functions take the **model name first**
  (`aidb.encode_text('m','text')`); data-prep functions take the **payload first** with an
  `options` bag second (`aidb.chunk_text('text', '{...}')`). When unsure, use named arguments.
* **JSON typing rule:** `step_N_options` is **jsonb** (config helpers return jsonb → no cast).
  Standalone data-prep functions take **json** — pass a quoted JSON literal, or cast a helper:
  `aidb.summarize_text_config('llm')::json`. Do **not** write `::jsonb` there.

---

## Workflow A — install / verify

```sql
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;   -- requires pgvector; CASCADE installs it
SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';
SELECT * FROM aidb.model_providers ORDER BY server_name;
```

Ask before creating an extension. If the user needs `Background` pipelines, `aidb` must be in
`shared_preload_libraries` (DBA + restart).

## Workflow B — register a model

```sql
-- local, no network, no credentials (Sovereign-AI default)
SELECT aidb.create_model('embed_model', 'bert_local');

-- remote, OpenAI-compatible
SELECT aidb.create_model(
    'openai_embed', 'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => jsonb_build_object('api_key', current_setting('my.openai_key')));

SELECT aidb.validate_model('openai_embed');
SELECT * FROM aidb.list_models();
```

* `validate => true` (the default) **probes the endpoint at model-registration time**; on failure
  the whole transaction rolls back and the error hints at `validate => false`.
* `config` is cleartext: `create_model` raises if it contains `api_key`/`basic_auth` at any depth.
* Credentials attach to the **provider server**, shared by all models on it → a second model with
  new credentials needs `replace_credentials => true`, and `delete_model` drops that shared mapping.
* Provider catalogue and capability→consumer map: `references/model-adapters.md`.

## Workflow C — standalone AI functions

```sql
-- embeddings (returns real[]; cast to vector for pgvector operators)
SELECT array_length(aidb.encode_text('embed_model', 'hello world'), 1);

-- completions
SELECT aidb.generate_text('my_llm', 'Summarize: ...',
         aidb.inference_config(temperature => 0.2, max_tokens => 64)::json);

-- reranking (returns text, logit_score, id)
SELECT text, logit_score
FROM aidb.rerank_text('my_reranker', 'capital of France',
                      ARRAY['Paris is the capital of France.','Bananas are yellow.'])
ORDER BY logit_score DESC;

-- chunking one string: set-returning, columns (part_id, chunk)
SELECT * FROM aidb.chunk_text('long text...', '{"desired_length":512,"overlap_length":64}');

-- chunking a whole table: LATERAL, one row per chunk
SELECT d.id, c.part_id, c.chunk
FROM docs d,
     LATERAL aidb.chunk_text(d.body, '{"desired_length":512,"overlap_length":64}') AS c;

-- summarize a column (scalar function; options must contain "model")
SELECT d.id, aidb.summarize_text(d.body, aidb.summarize_text_config('my_llm')::json)
FROM docs d;

-- parsing / OCR
SELECT aidb.parse_html('<h1>Hi</h1>', '{"method":"StructuredMarkdown"}');
SELECT * FROM aidb.parse_pdf(pg_read_binary_file('/tmp/doc.pdf')::bytea);
SELECT * FROM aidb.perform_ocr(img_bytes, '{"model":"my_ocr"}');
```

For a durable, incremental version of any of these, use a pipeline instead (Workflow D).

## Workflow D — build a RAG pipeline

```sql
-- 1. create (destination MUST NOT already exist)
SELECT aidb.create_pipeline(
    name               => 'docs_kb',
    source             => 'public.documents',
    source_key_column  => 'id',
    source_data_column => 'body',
    destination        => 'documents_vectors',
    auto_processing    => 'Disabled',              -- backfill first
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 600, overlap_length => 80),
    step_2             => 'KnowledgeBase',
    step_2_options     => aidb.knowledge_base_config(
                              model             => 'embed_model',
                              data_format       => 'Text',
                              distance_operator => 'Cosine'));

-- 2. backfill
SELECT aidb.run_pipeline('docs_kb');

-- 3. verify: progress, then quality
SELECT * FROM aidb.get_pipeline_metrics('docs_kb');
SELECT * FROM aidb.get_error_log_summary('docs_kb');
SELECT key, value, distance FROM aidb.retrieve_text('docs_kb', 'your question here', 5);

-- 4. automate only after a clean backfill
SELECT aidb.update_pipeline('docs_kb', auto_processing => 'Background',
                            batch_size => 100, background_sync_interval => '1 minute');
```

Manual similarity search (payload column is `value`):

```sql
SELECT source_id
FROM documents_vectors
ORDER BY value <=> aidb.kb_query_encode('docs_kb', 'search text')::vector
LIMIT 10;
```

Canonical step chains (details + templates: `references/step-operations.md`):

* HTML → RAG: `ParseHtml` → `ChunkText` → `KnowledgeBase`
* Digital PDF → RAG: `ParsePdf` → `ChunkText` → `KnowledgeBase`
* Scanned PDF → RAG: `PdfToImage` → `PerformOcr` → `ChunkText` → `KnowledgeBase`
* Long docs → short index: `ChunkText` → `SummarizeText` → `KnowledgeBase`

`KnowledgeBase` must be the **terminal** step. Envelope types (`Text`/`Bytes`/`Vector`) must match
between consecutive steps; mismatches are rejected at creation time.

## Workflow E — diagnose a pipeline (READ-ONLY)

Set the name once, run top to bottom, **stop at the first section that explains the symptom**.

```sql
\set p 'my_pipeline'

-- 1. definition: source, destination, steps, mode, owner_role
SELECT * FROM aidb.pipelines_v7 WHERE name = :'p';

-- 2. progress + error counters
SELECT * FROM aidb.get_pipeline_metrics(:'p');

-- 3. errors by step / operation / category
SELECT * FROM aidb.get_error_log_summary(:'p');

-- 4. the 25 most recent errors  (QUOTE error_message BACK VERBATIM)
SELECT id, source_id, pipeline_step, step_operation, error_category,
       left(error_message, 300) AS error_message, retry_count, last_seen_at
FROM aidb.get_error_logs(:'p', p_limit => 25);

-- 5. do the models referenced by the steps still exist?
SELECT s.step_order, s.operation, s.options->>'model' AS step_model,
       (m.name IS NOT NULL) AS model_exists
FROM aidb.pipelines_v7 p
CROSS JOIN LATERAL jsonb_to_recordset(p.steps)
     AS s(step_order int, operation text, options jsonb)
LEFT JOIN aidb.models m ON m.name = s.options->>'model'
WHERE p.name = :'p' ORDER BY s.step_order;

-- 6. background worker alive? (only matters in Background mode)
SHOW shared_preload_libraries;
SELECT pid, backend_type, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE backend_type ILIKE '%aidb%' OR application_name ILIKE '%aidb%';

-- 7. runtime state
SELECT * FROM aidb.pipeline_runtime_state WHERE name = :'p';
```

Reading it:

| Reading | Conclusion | Action |
|---|---|---|
| blocking-error count > 0 | pipeline-level failure halts everything | §4, fix the cause, re-run |
| unprocessed > 0, destination = 0 | nothing has run | `Disabled` ⇒ `run_pipeline()`; `Background` ⇒ §6 |
| unprocessed = 0, destination = 0 | no eligible rows, or every row errored | §4 |
| destination growing | healthy | check quality with `aidb.retrieve_text(...)` |
| `last_run_completed` NULL (§7) | never completed a run | as above |

`source_id IS NULL` ⇒ **blocking** error (model / credentials / permission) — never requeued.
`source_id NOT NULL` ⇒ record-level, eligible for requeue:

```sql
SELECT * FROM aidb.requeue_pipeline_errors('my_pipeline', ARRAY[1,2,3]::bigint[]);
SELECT aidb.run_pipeline('my_pipeline');       -- Background drains by itself
SELECT * FROM aidb.get_error_log_summary('my_pipeline');
```

Symptom→cause→fix matrix: `references/step-operations.md`, "Operations runbook".

## Workflow F — Semantic KB and text-to-SQL

```sql
SELECT aidb.create_semantic_kb('shop_kb', 'embed_model', ARRAY['shop'],
                               auto_processing => 'Background');
SELECT aidb.semantic_kb_stats('shop_kb');
SELECT * FROM aidb.semantic_kb_search('which table has customer spend?', 'shop_kb', top_k => 10);
```

* A Semantic KB embeds table/column names **and `COMMENT ON` text** — an uncommented schema
  embeds only identifiers. Advise the user to comment their schema.
* Loop: `semantic_kb_search` → if a high-scoring **alias** hit, `aidb.execute_semantic_alias(...)`
  and stop (curated beats generated) → else `get_entity_definitions` + `get_column_definitions`
  → generate SQL from those definitions only → show it to the user → run it read-only.
* Semantic-KB pipelines have an empty source and therefore **no error table**; use PostgreSQL
  logs and `aidb.semantic_kb_stats()`.
* Alias `${param}` placeholders bind as positional parameters — argument values cannot inject SQL.

## Workflow G — object-storage sources (volumes)

```sql
SELECT pgfs.create_storage_location('docs_bucket', 's3://my-bucket',
                                    options => '{"region":"eu-central-1"}');
SELECT aidb.create_volume('docs_volume', 'docs_bucket', 'corpus/', 'Pdf');
SELECT * FROM aidb.list_volume_content('docs_volume');   -- prove connectivity FIRST
```

Volume-sourced pipelines omit `source_key_column`/`source_data_column`; `source_id` becomes the
file path. Volume names must be valid unquoted identifiers (no hyphens). For `file://` locations,
`pgfs.allowed_local_fs_paths` must include the path.

---

## Constraints and gotchas (state these qualitatively; let the product supply exact values)

* **Pipeline names are length-limited** and **the number of steps is capped**, because AIDB derives
  helper object names from the pipeline name. Keep names short `snake_case`. If rejected, quote the
  error — it states the exact maximum for this build. Build-specific values:
  `references/function-reference.md`.
* Steps must be **consecutive from `step_1`** (no gaps) and **type-compatible**; both are validated
  at `create_pipeline()` time.
* **Two different validation moments — do not conflate them:**
  (a) `aidb.create_model(..., validate => true)` probes the provider endpoint **when the model is
  registered**; (b) `aidb.create_pipeline()` re-resolves each referenced model and validates that it
  has the capability the step needs (embedding / language / OCR) **before any data is touched**.
  Neither happens per-row at execution time; a model deleted after creation fails at run time with
  `Model not found: X`.
* The **destination table must not already exist**.
* `background_sync_interval` is range-checked by a SQL domain; out-of-range values are rejected with
  a message stating the accepted range.
* **Embedding dimensions are baked into the destination `value VECTOR(n)`** — never swap a KB's
  model; build a new KB.
* `topk` for retrieval must be ≥ 1.
* `aidb.max_threads` changes require a **PostgreSQL restart**.
* Pipeline errors always persist to the per-pipeline error table; `aidb.pipeline_error_warnings`
  only controls whether they are also logged as WARNINGs.
* Capabilities outside the five areas above (agents, tools, memory, MCP endpoints) exist in the
  source tree but are **build-dependent** — do not volunteer them; see Appendix A of
  `references/function-reference.md`.

## Common errors → action

| Error text (quote it to the user) | Action |
|---|---|
| `The destination table 'X' already exists.` | choose another `destination`, or drop it with consent |
| `... does not support text embedding / language / OCR operations` | model lacks that adapter — pick another provider (`references/model-adapters.md`) |
| `config must not contain "api_key" or "basic_auth"` | move the secret to `credentials` / `credentials_env` |
| `Credentials for model provider "X" already exist` | `replace_credentials => true`, or omit credentials to reuse |
| `Model provider with name "X" not found` | `SELECT * FROM aidb.model_providers;` |
| `Steps must be defined in a consecutive order. Missing steps: [...]` | renumber steps 1..N |
| `Output type '...' does not match input type '...'` | fix the chain (sequencing matrix in `references/step-operations.md`) |
| `The source 'X' cannot be found.` | schema-qualify; check the table/volume exists and is readable |
| `Table public.X is missing one or both columns` | wrong `source_key_column`/`source_data_column` |
| Background pipeline never advances | `aidb` missing from `shared_preload_libraries`; use `run_pipeline()` meanwhile |
| `Live` pipeline makes INSERTs slow | switch to `Background` with a sensible `batch_size` |

## Destructive operations — always describe the blast radius and get explicit confirmation

* `aidb.delete_pipeline` — also **drops the destination table** and intermediate step tables
  (a shared KB vector table survives only if other pipelines are attached to it)
* `aidb.delete_model` — drops the **provider-level** user mapping, which can break other models
* `aidb.delete_knowledge_base` / `aidb.delete_semantic_kb`
* `aidb.delete_volume_file` / `aidb.delete_volume` — touches real object storage
* `aidb.clear_error_logs` — destroys the audit trail; prefer `requeue_pipeline_errors`
* `DROP EXTENSION aidb CASCADE` — destroys every AIDB object in the database
* switching a busy pipeline to `Live`; any GUC change needing a restart; `CREATE EXTENSION`

---

## Reference index (load on demand)

| File | Use it for |
|---|---|
| `references/function-reference.md` | Full `aidb.*` signatures, config-helper catalogue, error-log API, retrieval helpers, Semantic KB & alias API, GUCs, permissions, build-specific enforced limits, and Appendix A (build-dependent surfaces) |
| `references/step-operations.md` | Per-step semantics (accepts/produces/explodes rows), sequencing matrix, copy-paste pipeline templates (T1–T5, incl. a zero-dependency `dummy`-provider smoke test), operations runbook, symptom→cause→fix matrix, retry workflow, performance tuning |
| `references/model-adapters.md` | Provider families (local, OpenAI-compatible, NIM, hosted, `dummy`), credential handling rules, capability→consumer map, model-choice decision guide |

Product documentation: <https://www.enterprisedb.com/docs/aidb/latest/> — use it to confirm whether
a capability is supported in the user's release before promising it.