# AIDB SQL Function Reference

Everything AIDB exposes lives in the `aidb` schema (internal objects in `aidb_internal`).

**Provenance.** Signatures and enforced limits below are read from the `EnterpriseDB/aidb`
repository source. Where a limit is quoted, the file that defines it is cited. Anything not
cited should be verified in the live database before you rely on it:

```sql
\df aidb.*                      -- psql: every AIDB function in this build
SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';
```

> **Argument-order rule:** model-invoking functions take the **model name first**
> (`aidb.encode_text('my_model', 'text')`). Data-preparation functions take the **payload
> first** and an `options` bag second (`aidb.chunk_text('text', '{...}')`).
> When unsure, use named arguments.

Contents: 1 install · 2 models · 3 pipelines · 4 error logs · 5 standalone AI ·
6 data prep · 7 retrieval · 8 config builders · 9 semantic KB · 10 semantic aliases ·
11 volumes · 12 GUCs · 13 permissions · Appendix A (build-dependent surfaces).

---

## 1. Install / verify

```sql
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;   -- pulls in vector (pgvector) and friends

SELECT extname, extversion FROM pg_extension WHERE extname = 'aidb';
SELECT * FROM aidb.model_providers ORDER BY server_name;   -- providers in THIS build
```

`aidb.control` declares `requires = 'vector'` and `schema = 'aidb'`, so `CASCADE` is
mandatory on a database that does not already have pgvector.

`shared_preload_libraries` must contain `aidb` for background workers to run (add `vchord`
if VectorChord indexes are used). Changing it requires a PostgreSQL restart.

---

## 2. Model management

```sql
aidb.create_model(
    name                TEXT,
    provider            VARCHAR(255),        -- see references/model-adapters.md
    config              JSONB   DEFAULT '{}',
    credentials         JSONB   DEFAULT '{}',
    replace_credentials BOOLEAN DEFAULT FALSE,
    validate            BOOLEAN DEFAULT TRUE,
    credentials_env     TEXT    DEFAULT NULL
) RETURNS TEXT
```

* `config` is stored in cleartext FDW options and **must not** contain `api_key` or
  `basic_auth` at any nesting depth — `create_model` raises if it does. Put secrets in
  `credentials` (stored in `pg_user_mappings`) or `credentials_env` (name of an env var read
  fresh at each model use). The two are mutually exclusive.
* Credentials attach to the **provider server**, not the model. A second model on the same
  provider with new credentials needs `replace_credentials => true`.
* `validate => true` (default) probes the model; on failure the transaction rolls back, so no
  broken model is left behind, and the error hints at `validate => false`.

```sql
aidb.validate_model(name TEXT)
aidb.list_models()                   -- TABLE(name, provider, options, functions)
aidb.get_model(model_name TEXT)
aidb.delete_model(model_name TEXT)   -- drops foreign table + user mapping + cache entry
aidb.remove_cached_model(name TEXT)  -- evict from the in-memory LRU model cache
aidb.get_model_description(provider TEXT)
aidb.audit_leaked_credentials()      -- models whose config was found to hold a secret

SELECT * FROM aidb.models;           -- same as list_models()
SELECT * FROM aidb.model_providers;  -- installed provider adapters + descriptions
```

---

## 3. Pipelines

### Create

```sql
aidb.create_pipeline(
    name                     => TEXT,      -- length-limited; see "Enforced limits" below
    source                   => TEXT,      -- 'table', 'schema.table', or a volume name
    step_1                   => TEXT,      -- required; the only mandatory step
    source_key_column        => TEXT,      -- required for table sources
    source_data_column       => TEXT,      -- required for table sources
    destination              => TEXT,      -- default: <source_schema>.pipeline_<name>
    auto_processing          => TEXT,      -- 'Live' | 'Background' | 'Disabled' (default)
    batch_size               => INTEGER,   -- default 100
    background_sync_interval => INTERVAL,  -- default 30 seconds
    owner_role               => TEXT,      -- default: current_role
    step_1_options           => JSONB,
    step_2 .. step_10        => TEXT,
    step_2_options .. step_10_options => JSONB
)
```

Returns one row: `name, destination_type, destination_schema, destination,
destination_key_column, destination_data_column`.

### Enforced limits (cited)

| Limit | Where it is defined | Behaviour |
|---|---|---|
| Maximum number of steps | `src/pipeline_common/registry.rs` — `Pipeline::MAX_NUM_STEPS = 10`, and the function only exposes `step_1` … `step_10` | An 11th step is not expressible |
| Maximum pipeline name length | `src/pipeline_common/registry.rs` — `MAX_NAME_LEN = 63 - DESTINATION_NAME_OVERHEAD`, because helper object names get suffixes such as `pipeline_<name>_step_99`; a `pipeline_name_50` SQL domain in `static-sql/pipeline_common.sql` adds a second ceiling | `create_pipeline` raises before doing any work |
| `background_sync_interval` range | `static-sql/pipeline_common.sql` — domain `background_sync_interval` with `CHECK (VALUE >= '1 second' AND VALUE <= '2 days')` | Out-of-range values are rejected by the domain constraint |

**Do not quote a specific character count to the user from memory.** Keep pipeline names
comfortably short (~30 chars, plain `snake_case`), and if the limit is hit, surface the
error message verbatim — it states the exact maximum for the running build.

Also enforced at creation time:

* Steps must be consecutive starting at `step_1`. A gap raises
  `Steps must be defined in a consecutive order. Missing steps: [2]`.
* The step sequence must be type-compatible (see `references/step-operations.md`). Mismatch:
  `Output type 'Text' of the ChunkText step (#2) does not match input type 'Bytes' of the
  next KnowledgeBase(Image) step (#3)`.
* The destination table **must not already exist**:
  `The destination table 'public.X' already exists. Please choose a different name or delete
  the existing table.`
* The source must exist and be readable:
  `The source 'X' cannot be found. Make sure the table/volume exists with appropriate
  permissions.`
* Source columns must exist: `Table public.X is missing one or both columns: id and content.`
* Models named in step options are resolved and validated **at creation time**, not at run
  time. Capability mismatches raise `... does not support text embedding operations`,
  `... does not support language operations`, or `... does not support OCR operations`.
* Volume sources take no `source_data_column` / `source_key_column`; supplying neither for a
  table source raises `'source_key_column' must be specified for table sources`.

### Manage

```sql
aidb.update_pipeline(
    name                     => TEXT,
    auto_processing          => TEXT     DEFAULT NULL,
    batch_size               => INTEGER  DEFAULT NULL,
    background_sync_interval => INTERVAL DEFAULT NULL
)

aidb.run_pipeline(name TEXT)     -- process everything pending, synchronously, now
aidb.delete_pipeline(name TEXT)
```

`aidb.run_pipeline()` works in every auto-processing mode and is the standard way to force a
catch-up or to drive a `Disabled` pipeline.

`aidb.delete_pipeline()` removes the registry rows, the state table, the error-log table, the
auto-processing triggers, **and drops the destination table and any intermediate step
tables** — except when the destination is a shared knowledge-base vector table that other
pipelines are still attached to, in which case only this pipeline's vectors are deleted.
Treat it as destructive; confirm with the user first.

### Auto-processing modes

| Mode | Mechanism | Blocks writers? | Use when |
|---|---|---|---|
| `Live` | row triggers on the source table | yes — inference runs inside the write txn | low write volume, freshness matters |
| `Background` | worker polls the state table in `batch_size` batches every `background_sync_interval` | no | production ingest, bulk loads |
| `Disabled` | nothing until `run_pipeline()` | no | batch/ETL, manual control |

### Inspect

```sql
SELECT * FROM aidb.pipelines_v7;              -- full definition incl. steps JSONB, owner_role
SELECT * FROM aidb.pipeline_metrics_v7;       -- per-pipeline progress + error counts + status
SELECT * FROM aidb.knowledge_bases_v7;        -- vector tables created by KnowledgeBase steps
SELECT * FROM aidb.knowledge_base_metrics_v7;
SELECT * FROM aidb.pipeline_registry;         -- raw registry table
SELECT * FROM aidb.pipeline_runtime_state;    -- current_run_id, last_run_completed
SELECT * FROM aidb.get_pipeline_metrics('my_pipeline');
```

`aidb.pipeline_metrics_v7` columns: `pipeline`, `auto processing`,
`table: unprocessed rows`, `volume: scans completed`, `count(source records)`,
`count(destination records)`, `Status`, `count(record errors)`, `count(blocking errors)`,
`last run completed`.

### Destination table shape

Created by `Pipeline::create_destination_table`:

```text
id          BIGSERIAL UNIQUE NOT NULL
pipeline_id INT                        -- KnowledgeBase destinations only
source_id   TEXT                       -- source key, or the file path for volume sources
part_ids    BIGINT[]                   -- chunk/page ordering from row-exploding steps
value       TEXT | BYTEA | VECTOR(n)   -- depends on the terminal step
```

The payload column is always named `value` — including for embeddings, where its type is
`VECTOR(n)` with `n` taken from the model's embedding dimensions.

---

## 4. Pipeline error logs

Each table/volume-sourced pipeline gets `<source_schema>.pipeline_<name>_errors`.

```sql
aidb.get_error_logs(
    p_pipeline_name  TEXT,
    p_source_id      TEXT               DEFAULT NULL,
    p_pipeline_step  SMALLINT           DEFAULT NULL,
    p_error_category aidb.ErrorBlocking DEFAULT NULL,
    p_limit          INTEGER            DEFAULT NULL,
    p_offset         INTEGER            DEFAULT 0
)
-- TABLE(id, source_id, part_ids, pipeline_step, step_operation, error_message,
--       error_category, failed_at, retry_count, last_retry_at, last_seen_at)
-- Ordered by last_seen_at DESC, id DESC.

aidb.get_error_log_summary(p_pipeline_name TEXT)
aidb.get_all_error_summaries()
aidb.clear_error_logs(p_pipeline_name TEXT, p_error_ids BIGINT[])        -- RETURNS BIGINT
aidb.requeue_pipeline_errors(p_pipeline_name TEXT, p_error_ids BIGINT[])
-- TABLE(error_id, action): 'requeued' | 'skipped_pipeline_level' | 'not_found'
```

Semantics that matter:

* Two partial unique indexes enforce **one row per `(source_id, part_ids, pipeline_step,
  step_operation)`** and one row per pipeline-level step failure. Repeat failures update
  `error_message`/`last_seen_at`; `failed_at`/`retry_count` are preserved unless the
  message+category changed.
* Rows with `source_id IS NULL` are **pipeline-level (blocking)** errors — a bad model,
  credential, or permission. They are never requeued; fix the cause and re-run.
* `requeue_pipeline_errors` only *marks* work dirty. `Background` pipelines drain it
  automatically; `Live`/`Disabled` need a following `aidb.run_pipeline()`.
* Pipelines with an empty source (Semantic KB) have **no error table**; these functions
  return empty for them. Use the PostgreSQL log and `aidb.semantic_kb_stats()` instead.

---

## 5. Standalone AI functions (model name FIRST)

```sql
aidb.encode_text(model TEXT, input TEXT)                     -- embedding vector
aidb.encode_text_batch(model TEXT, inputs TEXT[])
aidb.encode_image(model TEXT, image BYTEA)
aidb.generate_text(model TEXT, prompt TEXT, inference_config JSON DEFAULT NULL)
aidb.generate_text_batch(model TEXT, prompts TEXT[], inference_config JSON DEFAULT NULL)
aidb.rerank_text(model TEXT, query TEXT, documents TEXT[])
-- rerank returns TABLE(id INT, text TEXT, logit_score FLOAT); sort by logit_score DESC
```

```sql
SELECT cardinality(aidb.encode_text('my_bert', 'hello world'));
SELECT aidb.generate_text('my_llm', 'summarize: ...',
                          aidb.inference_config(temperature => 0.2, max_tokens => 64)::json);
SELECT text, logit_score
FROM aidb.rerank_text('my_reranker', 'capital of France',
                      ARRAY['Paris is the capital of France.', 'Bananas are yellow.'])
ORDER BY logit_score DESC;
```

---

## 6. Standalone data-prep functions (payload FIRST)

```sql
aidb.chunk_text(input TEXT, options JSONB DEFAULT '{}')   -- TABLE(part_id INT, value TEXT)
aidb.summarize_text(input TEXT, options JSON)             -- options must contain "model"
aidb.summarize_text_aggregate(input TEXT, options JSON)   -- aggregate; ORDER BY inside
aidb.parse_html(html TEXT, options JSONB DEFAULT '{}')    -- TEXT input, not BYTEA
aidb.parse_pdf(bytes BYTEA, options JSONB DEFAULT '{}')
aidb.perform_ocr(image BYTEA, options JSONB)              -- options must contain "model"
```

```sql
SELECT * FROM aidb.chunk_text('long text...', '{"desired_length": 512, "overlap_length": 64}');
SELECT aidb.summarize_text(content, aidb.summarize_text_config('my_llm')::json) FROM docs;
SELECT * FROM aidb.parse_html('<h1>Hi</h1>', '{"method":"StructuredMarkdown"}');
SELECT * FROM aidb.parse_pdf(pg_read_binary_file('/tmp/doc.pdf')::bytea);
SELECT * FROM aidb.perform_ocr(img_bytes, options => '{"model":"my_ocr"}');
```

---

## 7. Retrieval helpers (for `KnowledgeBase` pipelines)

Signatures from `src/knowledge_base_pipeline_pg_extern.rs`:

```sql
aidb.retrieve_key(
    knowledge_base_name TEXT,
    query               TEXT | BYTEA,
    topk                INTEGER DEFAULT 1,
    deduplicate         BOOLEAN DEFAULT TRUE)
-- TABLE(key TEXT, distance FLOAT8, part_ids BIGINT[], pipeline_name TEXT)

aidb.retrieve_text(
    knowledge_base_name TEXT,
    query               TEXT | BYTEA,
    topk                INTEGER DEFAULT 1,
    deduplicate         BOOLEAN DEFAULT TRUE)
-- TABLE(key TEXT, value TEXT, distance FLOAT8, part_ids BIGINT[],
--       pipeline_name TEXT, intermediate_steps JSONB)

aidb.kb_query_encode(knowledge_base_name TEXT, query TEXT)   -- query-side embedding
aidb.delete_knowledge_base(kb_name TEXT)
```

* `topk` **must be >= 1**; `topk => 0` raises
  `topk (the number of results to return) must be at least 1, got 0.`
* The first argument accepts the pipeline name or the fully-qualified KB name
  (`public.pipeline_<name>` when `destination` was omitted).
* A `BYTEA` query does cross-modal retrieval on CLIP-style models.
* Retrieval against an empty vector table returns zero rows, not an error.
* `pipeline_name` in the result identifies which pipeline produced the row — useful when
  several pipelines share one KB.

Manual similarity search always works too (payload column is `value`):

```sql
SELECT source_id
FROM my_destination
ORDER BY value <=> aidb.kb_query_encode('public.pipeline_my_kb', 'search text')::vector
LIMIT 10;
```

Join back to the source table on `key` (it is TEXT, so cast):

```sql
SELECT s.*
FROM aidb.retrieve_text('my_kb', 'orchid', 5) r
JOIN source_table s ON s.id::TEXT = r.key;
```

---

## 8. Config builder functions

All return JSONB and are `IMMUTABLE PARALLEL SAFE` (`static-sql/config_helpers.sql`).

**Step options**

```sql
aidb.knowledge_base_config(model, data_format, distance_operator DEFAULT NULL, vector_index DEFAULT NULL)
aidb.knowledge_base_config_from_kb(data_format)     -- attach a pipeline to an existing KB
aidb.chunk_text_config(desired_length, max_length DEFAULT NULL, overlap_length DEFAULT NULL, strategy DEFAULT NULL)
aidb.summarize_text_config(model, chunk_config DEFAULT NULL, prompt DEFAULT NULL,
                           strategy DEFAULT NULL, reduction_factor DEFAULT NULL,
                           inference_config DEFAULT NULL)
aidb.html_parse_config(method DEFAULT NULL)         -- 'StructuredPlaintext' | 'StructuredMarkdown'
aidb.pdf_parse_config(method DEFAULT NULL, allow_partial_parsing DEFAULT NULL)
aidb.ocr_config(model)
aidb.inference_config(system_prompt, temperature, max_tokens, top_p, seed,
                      repeat_penalty, repeat_last_n, thinking, extra_args)   -- all DEFAULT NULL
```

**Model config**

```sql
aidb.embeddings_config(model, api_key, basic_auth, url, max_concurrent_requests,
                       max_batch_size, input_type, input_type_query, tls_config, is_hcp_model)
aidb.completions_config(model, api_key, basic_auth, url, max_concurrent_requests, max_tokens,
                        is_hcp_model, system_prompt, temperature, top_p, seed, thinking, extra_args)
aidb.max_tokens_config(size, format DEFAULT NULL)
aidb.bert_config(...)  aidb.clip_config(...)  aidb.llama_config(...)  aidb.t5_config(...)
aidb.gemini_config(...)  aidb.nim_clip_config(...)  aidb.nim_ocr_config(...)
aidb.nim_reranking_config(...)  aidb.openrouter_chat_config(...)  aidb.openrouter_embeddings_config(...)
```

> The `api_key`/`basic_auth` parameters on these helpers are legacy. `create_model` **rejects**
> a config containing them — route secrets through `credentials` / `credentials_env`.

**Vector index config**

```sql
aidb.vector_index_hnsw_config(vector_data_type, m, ef_construction, ef_search)
aidb.vector_index_ivfflat_config(vector_data_type, lists, probes)
aidb.vector_index_chord_hnsw_config(vector_data_type, m, ef_construction, max_connections, ml)
aidb.vector_index_chord_vchordq_config(vector_data_type, lists, spherical_centroids)
aidb.vector_index_hsphere_optimized_config(clusters, precision_val, vector_data_type)
aidb.vector_index_disabled_config()
```

When a `KnowledgeBase` step is created **without** `vector_index`, the KB registry stores
`{"type": "hnsw", "m": 16, "ef_construction": 64}` (`register_new_kb` in
`src/pipeline_common/registry.rs`). Use `aidb.vector_index_disabled_config()` to opt out of
indexing entirely (exact search).

**Alias params**

```sql
aidb.alias_param(name, param_type, description DEFAULT NULL, enum_values TEXT[] DEFAULT NULL)
aidb.alias_params(VARIADIC params JSONB[])
```

---

## 9. Semantic Knowledge Base

A Semantic KB embeds a schema's *metadata* (table/view/column names, types, and
`COMMENT ON` text) so natural-language questions can find the right relations. It is the
foundation for text-to-SQL. **Comments are what make it useful** — an uncommented schema
embeds only identifiers.

```sql
aidb.create_semantic_kb(name, model, schemas TEXT[],
                        auto_processing DEFAULT 'Disabled',   -- Live | Background | Disabled
                        bypass_triggers DEFAULT FALSE,
                        vector_index    DEFAULT NULL)
aidb.delete_semantic_kb(name)
aidb.list_semantic_kbs()
aidb.refresh_semantic_kb(name)                      -- full re-crawl
aidb.update_semantic_kb_auto_processing(name, mode)
aidb.semantic_kb_stats(name)
```

| Mode | Behaviour |
|---|---|
| `Live` | DDL event triggers re-embed on CREATE/ALTER/DROP/COMMENT synchronously; initial crawl runs at creation |
| `Background` | DDL events queue into `aidb_internal.semantic_kb_state`; a worker drains them |
| `Disabled` | Nothing until `aidb.refresh_semantic_kb()` — schema drift is silent |

`bypass_triggers => TRUE` skips DDL trigger installation altogether.

### Search

All of these take `(kb_name, query, min_similarity, limit, offset)`. `query => NULL` returns
everything; `min_similarity => 0.0` disables the floor.

```sql
aidb.get_metadata(kb, query, min_similarity, limit, offset)
-- TABLE(schema_name, relation_name, column_name, entity_type, definition, comment, similarity)
aidb.get_tables(kb, query, min_similarity, limit, offset)
aidb.get_columns(kb, query, min_similarity, limit, offset)
aidb.get_column_definitions(kb, query, min_similarity, limit, offset)
aidb.get_entity_definitions(kb, query, min_similarity, types TEXT[], limit, offset)
aidb.search_by_comment(kb, query, min_similarity, limit, offset)
```

Composite search over schema **and** aliases in one ranked list:

```sql
aidb.semantic_kb_search(query, kb_name DEFAULT NULL, top_k DEFAULT 10,
                        sources TEXT[] DEFAULT NULL,        -- e.g. ARRAY['alias']
                        entity_types TEXT[] DEFAULT NULL,   -- e.g. ARRAY['Table','View']
                        min_similarity NUMERIC DEFAULT NULL)
-- TABLE(source_type, entity_type, relation_name, column_name, object_ref, definition, score, rank)
```

`kb_name` may be omitted when exactly one KB exists. `top_k < 1`, unknown `sources`, and
unknown `entity_types` are rejected.

**Similarity thresholds:** 0.9+ near-exact · 0.8 good default · 0.5–0.7 broad exploration ·
0.0 no floor.

Raw storage (debug only): `aidb_internal.schema_metadata_<kb_name>` with
`schema_name, relation_name, column_name, entity_type, definition, comment,
definition_vector, comment_vector, embedded_at`.

---

## 10. Semantic Aliases

A named, parameterized SQL query registered with a natural-language description, embedded with
its owning KB and discoverable by semantic search.

```sql
aidb.create_semantic_alias(name, description, query_text, params DEFAULT NULL, kb_name)
aidb.update_semantic_alias(name, description)        -- re-embeds
aidb.delete_semantic_alias(name)
aidb.get_semantic_aliases()
aidb.get_semantic_alias(name)
aidb.search_semantic_aliases(query, min_similarity, limit, offset, kb_name DEFAULT NULL)
aidb.execute_semantic_alias(name, args JSONB, role TEXT DEFAULT NULL)
```

```sql
SELECT aidb.create_semantic_alias(
    'top_customers',
    'Rank customers by lifetime value / total spend',
    'SELECT * FROM shop.customer_ltv ORDER BY lifetime_value DESC LIMIT ${n}',
    aidb.alias_params(aidb.alias_param('n', 'integer', 'How many customers')),
    'shop_kb');

SELECT * FROM aidb.execute_semantic_alias('top_customers', '{"n": 5}'::jsonb);
```

* `${param}` placeholders become **bound positional parameters** — injection through argument
  values is impossible; `'; DROP TABLE x; --` binds as a literal string.
* Every declared param is required; pass JSON `null` to bind SQL NULL.
* A trailing semicolon in `query_text` is stripped at registration.
* An alias is embedded once **per owning KB**, so one alias can serve several KBs.
* Deleting a KB removes only that KB's alias embeddings, not the alias itself.

### Recommended text-to-SQL loop

1. `aidb.semantic_kb_search(question, kb, top_k => 10)`.
2. High-scoring alias hit → `aidb.execute_semantic_alias(...)` and stop. Curated beats generated.
3. Otherwise `aidb.get_entity_definitions(...)` + `aidb.get_column_definitions(...)` for the
   top hits.
4. Generate SQL using only those definitions.
5. Show the SQL to the user and run it read-only.

---

## 11. Volumes (object storage sources)

Volumes wrap a `pgfs` storage location. **Create the pgfs storage location first.**

```sql
SELECT pgfs.create_storage_location('loc', 's3://bucket',
                                    options => '{"region":"eu-central-1"}');
SELECT pgfs.create_storage_location('local_loc', 'file:///srv/data', '{}', '{}');

aidb.create_volume(name TEXT, storage_location TEXT, path TEXT, data_type TEXT)
-- data_type: 'Text' | 'Bytes' | 'Image' | 'Pdf'
-- name must be a valid unquoted PostgreSQL identifier (no hyphens)

aidb.list_volumes()
aidb.list_volume_content(volume_name TEXT)     -- TABLE(file_name, size, last_modified)
aidb.read_volume_file(volume_name TEXT, file_path TEXT)
aidb.write_volume_data(volume_name TEXT, file_path TEXT, data BYTEA)
aidb.delete_volume_file(volume_name TEXT, file_path TEXT)
aidb.delete_volume(name TEXT)
```

For local `file://` locations set `pgfs.allowed_local_fs_paths` to include the target path.
A volume-sourced pipeline omits `source_key_column`/`source_data_column`; `source_id` in the
destination is the file path.

---

## 12. GUC parameters

The two documented, user-facing knobs:

| Parameter | Restart | Purpose |
|---|---|---|
| `aidb.max_threads` | yes | CPU thread pool size for local model inference |
| `aidb.pipeline_error_warnings` | no | also emit each pipeline error as a PostgreSQL WARNING |

Pipeline errors always persist to the per-pipeline error table regardless of
`aidb.pipeline_error_warnings`.

Related, outside the `aidb.` namespace: `pgfs.allowed_local_fs_paths` (allow-list for
`file://` volumes) and `shared_preload_libraries` (must contain `aidb`).

Additional `aidb.*` settings exist in some builds. Enumerate the truth for the running
instance rather than guessing:

```sql
SELECT name, setting, unit, context, pending_restart
FROM pg_settings WHERE name LIKE 'aidb.%' ORDER BY name;
```

---

## 13. Permissions

* `aidb_users` is the role that grants day-to-day AIDB usage:
  `GRANT aidb_users TO app_role;`
* Pipelines record an `owner_role` (defaults to `current_role`, overridable via the
  `owner_role` argument, which requires `pg_has_role(current_user, owner_role, 'USAGE')`).
  Triggers and background execution run with that identity.
* `aidb_internal` tables are not granted broadly — go through the documented `aidb.*`
  functions.

---

## Appendix A — build-dependent surfaces (verify before use)

The five capability areas above (pipelines, models, standalone AI functions, Semantic
Knowledge Base, Semantic Aliases) are the supported, documented AIDB surface. The
`EnterpriseDB/aidb` source tree additionally contains crates (`aidb-agents`, `aidb-tools`,
`aidb-memory`, `edb-endpoints`) whose SQL functions are **not part of that surface and may be
absent, renamed, or behave differently in the build you are connected to.**

**Rules for the agent:**

1. Do **not** volunteer these capabilities, and do not mention them when the user asks what
   AIDB can do.
2. Only if the user explicitly names one of them, first prove it exists:

   ```sql
   \df aidb.*
   -- or, portable:
   SELECT n.nspname, p.proname
   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname IN ('aidb','aidb_memory')
     AND (p.proname LIKE '%agent%' OR p.proname LIKE '%tool%' OR p.proname LIKE '%mcp%')
   ORDER BY 1,2;
   ```

3. If the query returns nothing, tell the user the capability is not available in this build
   and stop. If it returns functions, inspect the real signatures with `\df+` before calling
   anything — do not assume argument names or order from memory.
4. Point the user at <https://www.enterprisedb.com/docs/aidb/latest/> for whether a given
   capability is supported in their release.
