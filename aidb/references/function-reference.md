# AIDB Function Reference

All functions live in the `aidb` schema (Agent Memory functions live in `aidb_memory`). Signatures below match the installed extension (7.10.0): argument names, order, types, defaults and return types. Prefer named arguments (`=>`) for anything with more than two parameters.

Two type rules catch most failed calls:

- Functions that take `options JSON` or `inference_config JSON` (`chunk_text`, `summarize_text`, `parse_html`, `parse_pdf`, `perform_ocr`, `pdf_to_image`, `generate_text`, `generate_text_batch`) reject a `jsonb` value with "function does not exist". Every `aidb.*_config()` helper returns `jsonb`, so append `::json` when passing a helper result to one of these functions. String literals (`'{"desired_length": 200}'`) work as is. Pipeline `step_N_options` are `jsonb`, so helpers go there without a cast.
- Agent Hub ids (`conversation_id`, `message_id`) are `TEXT`. There is no implicit uuid to text cast, so pass a `uuid` value as `id::text`.

---

## Pipeline Management

```sql
aidb.create_pipeline(
    name                     TEXT,
    source                   TEXT,
    step_1                   aidb.PipelineStepOperation,  -- 'ChunkText' | 'SummarizeText' | 'ParseHtml' | 'ParsePdf' | 'PerformOcr' | 'KnowledgeBase' | 'PdfToImage' | 'SemanticKB'
    source_key_column        TEXT     DEFAULT NULL,
    source_data_column       TEXT     DEFAULT NULL,
    destination              TEXT     DEFAULT NULL,
    auto_processing          aidb.PipelineAutoProcessingMode DEFAULT NULL,  -- 'Live' | 'Background' | 'Disabled' (NULL means 'Disabled')
    batch_size               INTEGER  DEFAULT NULL,
    background_sync_interval INTERVAL DEFAULT NULL,
    owner_role               TEXT     DEFAULT NULL,
    step_1_options           JSONB    DEFAULT NULL,
    step_2                   aidb.PipelineStepOperation DEFAULT NULL,
    step_2_options           JSONB    DEFAULT NULL,
    -- ... same pair repeats up to step_10 / step_10_options
) -- returns TABLE(name TEXT, destination_type TEXT, destination_schema TEXT, destination TEXT, destination_key_column TEXT, destination_data_column TEXT)
  -- step_1 is the 3rd argument and the key column comes before the data column: always call with named arguments

aidb.update_pipeline(
    name                     TEXT,
    auto_processing          aidb.PipelineAutoProcessingMode DEFAULT NULL,
    batch_size               INTEGER  DEFAULT NULL,
    background_sync_interval INTERVAL DEFAULT NULL
) -- returns VOID; the destination and steps cannot be changed after creation

aidb.run_pipeline(pipeline_name TEXT) -- returns VOID; processes the pipeline now, in the calling session

aidb.delete_pipeline(name TEXT) -- returns VOID

aidb.get_pipeline_metrics(name TEXT)
    -- returns TABLE(name TEXT, auto_processing TEXT, count_events BIGINT, count_source BIGINT, count_known_objects BIGINT,
    --               last_full_run_id BIGINT, count_destination BIGINT, status TEXT, count_errors_record BIGINT,
    --               count_errors_pipeline BIGINT, last_run_completed TIMESTAMPTZ)
```

Inspect pipelines through views:

- `aidb.pipelines` (alias `aidb.pipes`): one row per pipeline with `id, name, source_type, source_schema, source, source_key_column, source_data_column, destination_type, destination_schema, destination, destination_key_column, destination_data_column, steps JSONB, auto_processing, batch_size, background_sync_interval, owner_role`. Filter with `WHERE name = '...'`.
- `aidb.pipeline_metrics`: the same metrics as `get_pipeline_metrics`, for all pipelines, under human-readable column names that need double quotes: `pipeline, "auto processing", "table: unprocessed rows", "volume: scans completed", "count(source records)", "count(destination records)", "Status", "count(record errors)", "count(blocking errors)", "last run completed"`. It has no `name` or `status` column, and `aidb.pipelines` has no status column at all.

List every pipeline with its status:

```sql
SELECT pipeline, "auto processing", "Status", "count(record errors)" FROM aidb.pipeline_metrics;

-- Or with plain column names:
SELECT p.name, m.auto_processing, m.status, m.count_errors_record
FROM aidb.pipelines p CROSS JOIN LATERAL aidb.get_pipeline_metrics(p.name) m;
```

**Constraints:**
- Max pipeline name length: 46 characters
- Max steps per pipeline: 10
- Destination table must not exist at creation time, except that a pipeline whose last step is `KnowledgeBase` may name an existing knowledge base table and attaches to it

### Knowledge base retrieval

A pipeline whose last step is `KnowledgeBase` is queried with `retrieve_text` / `retrieve_key`. Both are current and supported. The first argument is the schema-qualified destination table, `'<schema>.<table>'`. An unqualified table name is not found. Passing the pipeline name still works but emits a WARNING asking for the `'<schema>.<table>'` identifier.

```sql
aidb.retrieve_text(knowledge_base_name TEXT, query TEXT,        topk INTEGER DEFAULT 1, deduplicate BOOLEAN DEFAULT true)
aidb.retrieve_text(knowledge_base_name TEXT, query_image BYTEA, topk INTEGER DEFAULT 1, deduplicate BOOLEAN DEFAULT true)
    -- returns TABLE(key TEXT, value TEXT, distance DOUBLE PRECISION, part_ids BIGINT[], pipeline_name TEXT, intermediate_steps JSONB)

aidb.retrieve_key(knowledge_base_name TEXT, query TEXT,        topk INTEGER DEFAULT 1, deduplicate BOOLEAN DEFAULT true)
aidb.retrieve_key(knowledge_base_name TEXT, query_image BYTEA, topk INTEGER DEFAULT 1, deduplicate BOOLEAN DEFAULT true)
    -- returns TABLE(key TEXT, distance DOUBLE PRECISION, part_ids BIGINT[], pipeline_name TEXT)
```

Example: `SELECT key, value, distance FROM aidb.retrieve_text('public.docs_kb', 'refund policy', topk => 5);`

### Pipeline error log

Record and pipeline errors are written to `<source_schema>.pipeline_<name>_errors`. They are always stored there, whatever `aidb.pipeline_error_warnings` says. Read them through these functions rather than querying the table directly.

```sql
aidb.get_error_logs(
    p_pipeline_name  TEXT,
    p_source_id      TEXT              DEFAULT NULL,
    p_pipeline_step  SMALLINT          DEFAULT NULL,
    p_error_category aidb.ErrorBlocking DEFAULT NULL,  -- 'RecordTemporary' | 'RecordPermanent' | 'PipelineTemporary' | 'PipelinePermanent'
    p_limit          INTEGER           DEFAULT NULL,
    p_offset         INTEGER           DEFAULT 0
) -- returns TABLE(id BIGINT, source_id TEXT, part_ids BIGINT[], pipeline_step SMALLINT, step_operation aidb.PipelineStepOperation,
  --               error_message TEXT, error_category aidb.ErrorBlocking, failed_at TIMESTAMPTZ, retry_count INTEGER,
  --               last_retry_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ)

aidb.get_error_log_summary(p_pipeline_name TEXT)
    -- returns TABLE(pipeline_step SMALLINT, step_operation aidb.PipelineStepOperation, error_category aidb.ErrorBlocking,
    --               error_count BIGINT, latest_failed_at TIMESTAMPTZ)

aidb.get_all_error_summaries()
    -- returns TABLE(pipeline_name TEXT, pipeline_step SMALLINT, step_operation aidb.PipelineStepOperation,
    --               error_category aidb.ErrorBlocking, error_count BIGINT, latest_failed_at TIMESTAMPTZ)

aidb.clear_error_logs(p_pipeline_name TEXT, p_error_ids BIGINT[]) -- returns BIGINT (rows cleared)

aidb.requeue_pipeline_errors(p_pipeline_name TEXT, p_error_ids BIGINT[]) -- returns TABLE(error_id BIGINT, action TEXT)
```

---

## Model Management

```sql
aidb.create_model(
    name                   TEXT,
    provider               VARCHAR,
    config                 JSONB   DEFAULT '{}',   -- must not contain api_key or basic_auth (rejected)
    credentials            JSONB   DEFAULT '{}',   -- inline secret, e.g. '{"api_key": "..."}'
    replace_credentials    BOOLEAN DEFAULT FALSE,
    validate               BOOLEAN DEFAULT TRUE,   -- test-connects the model at registration
    credentials_env        TEXT    DEFAULT NULL,   -- read the secret from this allow-listed env var instead
    credentials_k8s_secret TEXT    DEFAULT NULL,   -- read the secret from this allow-listed mounted secret path instead
    credential             TEXT    DEFAULT NULL,   -- bind to an existing aidb.create_credential() by name
    share                  BOOLEAN DEFAULT FALSE
) -- returns TEXT
  -- the 5th positional argument is replace_credentials: always call with named arguments

aidb.list_models()             -- returns TABLE(name TEXT, provider TEXT, options TEXT[], functions TEXT[], credential TEXT)
aidb.get_model(model_name TEXT) -- returns TABLE(name TEXT, provider TEXT, options TEXT[], credential TEXT)
aidb.delete_model(model_name TEXT) -- returns TABLE(name TEXT, provider TEXT, options TEXT[])
aidb.validate_model(model_name TEXT) -- returns TEXT; test-connects a registered model

aidb.remove_cached_model(model_name TEXT) -- returns VOID; evicts a model from this backend's cache

aidb.list_providers() -- returns TABLE(name TEXT, description TEXT, functions TEXT[], default_host TEXT)
aidb.get_model_description(model_name TEXT) -- returns TEXT
    -- despite the parameter name, pass a PROVIDER name (e.g. 'embeddings'); a model name returns ''

aidb.get_adapter_embedding_dimensions(model_name TEXT, data_format aidb.PipelineDataFormat) -- 'Text' | 'Image' | 'Pdf'; returns INTEGER
```

Views:

- `aidb.models`: `name, provider, options TEXT[], functions TEXT[], credential`. Same rows as `list_models()`.
- `aidb.model_providers`: `name, description, functions TEXT[], default_host`. Same rows as `list_providers()`.
- `aidb.credentials`: `name, owner, endpoint, shared, has_secret, can_use`.

### Credentials

A credential holds one secret and the hosts it may be sent to. Bind a model to one with `create_model(..., credential => 'name')` instead of passing `credentials` inline. Secrets go only in `credentials`, `credential`, `credentials_env` or `credentials_k8s_secret`, never in `config`.

```sql
aidb.create_credential(
    name                   TEXT,
    credentials            JSONB   DEFAULT NULL,
    credentials_env        TEXT    DEFAULT NULL,
    credentials_k8s_secret TEXT    DEFAULT NULL,
    endpoint               TEXT    DEFAULT NULL,
    share                  BOOLEAN DEFAULT FALSE
) -- returns TEXT

aidb.update_credential(
    name                   TEXT,
    credentials            JSONB   DEFAULT NULL,
    credentials_env        TEXT    DEFAULT NULL,
    credentials_k8s_secret TEXT    DEFAULT NULL,
    endpoint               TEXT    DEFAULT NULL,
    share                  BOOLEAN DEFAULT NULL
) -- returns TEXT

aidb.delete_credential(name TEXT, cascade BOOLEAN DEFAULT FALSE) -- returns TEXT
```

### HCP models

```sql
aidb.create_hcp_model(name TEXT, hcp_model_name TEXT) -- returns TEXT
aidb.list_hcp_models()  -- returns TABLE(name TEXT, url TEXT, model TEXT); also the aidb.hcp_models view
aidb.sync_hcp_models()  -- returns TABLE(status TEXT, model TEXT, reason TEXT)
```

**Deprecated:** `aidb.get_hcp_models()` still works; use `aidb.list_hcp_models()`.

---

## Standalone AI Operations

### Embeddings & Encoding

Every model function takes the model name first.

```sql
aidb.encode_text(model_name TEXT, input TEXT)               -- returns REAL[]
aidb.encode_text_batch(model_name TEXT, input TEXT[])       -- returns SETOF REAL[] (one row per input)
aidb.encode_text_query(model_name TEXT, input TEXT)         -- returns REAL[] (query-side encoding for bi-encoders)
aidb.encode_text_query_batch(model_name TEXT, input TEXT[]) -- returns SETOF REAL[]
aidb.encode_image(model_name TEXT, input BYTEA)             -- returns REAL[]
aidb.rerank_text(model_name TEXT, query TEXT, input TEXT[]) -- returns TABLE(text TEXT, logit_score DOUBLE PRECISION, id INTEGER)
```

Embeddings are `real[]`. Cast with `::vector` before using pgvector operators. Embed a search query once, in a CTE or scalar subquery, and compare against that; never call an encode function inside `ORDER BY`, where it can run once per row.

```sql
WITH q AS (SELECT aidb.encode_text_query('my_embedder', 'refund policy')::vector AS v)
SELECT d.id, d.body
FROM docs d, q
ORDER BY d.embedding <=> q.v
LIMIT 5;
```

### Text Generation

```sql
aidb.generate_text(model_name TEXT, input TEXT, inference_config JSON DEFAULT NULL)         -- returns TEXT
aidb.generate_text_batch(model_name TEXT, input TEXT[], inference_config JSON DEFAULT NULL) -- returns SETOF TEXT
    -- inference_config is JSON, not JSONB: pass aidb.inference_config(...)::json
```

Example: `SELECT aidb.generate_text('my_llm', 'Summarize: ...', aidb.inference_config(temperature => 0.2)::json);`

**Deprecated:** `aidb.decode_text` / `aidb.decode_text_batch` take the same arguments and still work, but emit a warning ("is deprecated and will be removed in a future version") pointing at `generate_text` / `generate_text_batch`. Always use the `generate_text*` names.

### Text Preparation

```sql
aidb.chunk_text(input TEXT, options JSON DEFAULT '{}')
    -- returns TABLE(part_id BIGINT, chunk TEXT); options: aidb.chunk_text_config(...)::json

aidb.summarize_text(input TEXT, options JSON DEFAULT '{}')
    -- returns TEXT; options: aidb.summarize_text_config(...)::json

aidb.parse_html(html TEXT, options JSON DEFAULT '{}')
    -- returns TEXT; options: aidb.html_parse_config(...)::json

aidb.parse_pdf(bytes BYTEA, options JSON DEFAULT '{}')
    -- returns TABLE(part_id BIGINT, text TEXT); options: aidb.pdf_parse_config(...)::json

aidb.perform_ocr(input BYTEA, options JSON DEFAULT '{}')
    -- returns TABLE(part_id BIGINT, text TEXT); the model goes in options: aidb.ocr_config('my_ocr_model')::json

aidb.pdf_to_image(bytes BYTEA, options JSON DEFAULT '{}')
    -- returns TABLE(part_id BIGINT, image BYTEA)
```

Example: `SELECT part_id, chunk FROM aidb.chunk_text(body, aidb.chunk_text_config(desired_length => 200)::json);`

---

## Step Config Helpers

These return JSONB. Use them directly as `step_N_options` in `create_pipeline`, or append `::json` when passing them to a standalone function above.

```sql
aidb.chunk_text_config(
    desired_length   INTEGER,
    max_length       INTEGER  DEFAULT NULL,
    overlap_length   INTEGER  DEFAULT NULL,
    strategy         TEXT     DEFAULT NULL
) -- returns JSONB

aidb.summarize_text_config(
    model              TEXT,
    chunk_config       JSONB   DEFAULT NULL,
    prompt             TEXT    DEFAULT NULL,
    strategy           TEXT    DEFAULT NULL,  -- 'append' | 'reduce'
    reduction_factor   INTEGER DEFAULT NULL,
    inference_config   JSONB   DEFAULT NULL
) -- returns JSONB

aidb.html_parse_config(
    method TEXT DEFAULT NULL  -- 'StructuredPlaintext' | 'StructuredMarkdown'
) -- returns JSONB

aidb.pdf_parse_config(
    method                TEXT    DEFAULT NULL,
    allow_partial_parsing BOOLEAN DEFAULT NULL
) -- returns JSONB

aidb.ocr_config(model TEXT) -- returns JSONB

aidb.knowledge_base_config(
    model             TEXT,
    data_format       aidb.PipelineDataFormat,                -- 'Text' | 'Image' | 'Pdf'
    distance_operator aidb.DistanceOperator DEFAULT NULL,     -- 'L2' (default) | 'InnerProduct' | 'Cosine' | 'L1' | 'Hamming' | 'Jaccard'
    vector_index      JSONB                 DEFAULT NULL
) -- returns JSONB

aidb.knowledge_base_config_from_kb(data_format aidb.PipelineDataFormat) -- returns JSONB
```

---

## Inference Config Builder

```sql
aidb.inference_config(
    system_prompt  TEXT              DEFAULT NULL,
    temperature    DOUBLE PRECISION  DEFAULT NULL,
    max_tokens     INTEGER           DEFAULT NULL,
    top_p          DOUBLE PRECISION  DEFAULT NULL,
    seed           BIGINT            DEFAULT NULL,
    repeat_penalty REAL              DEFAULT NULL,
    repeat_last_n  INTEGER           DEFAULT NULL,
    thinking       BOOLEAN           DEFAULT NULL,
    extra_args     JSONB             DEFAULT NULL
) -- returns JSONB; pass as inference_config => aidb.inference_config(...)::json to generate_text*
```

---

## Model Config Builders

These build the `config` JSONB for `create_model`. Several of them still declare `api_key` / `basic_auth` parameters, but `create_model` rejects a config that contains either key. Leave those parameters unset and pass the secret through `credentials`, `credential`, `credentials_env` or `credentials_k8s_secret`.

```sql
aidb.embeddings_config(
    model                   TEXT,
    api_key                 TEXT    DEFAULT NULL,  -- leave unset for create_model
    basic_auth              TEXT    DEFAULT NULL,  -- leave unset for create_model
    url                     TEXT    DEFAULT NULL,
    max_concurrent_requests INTEGER DEFAULT NULL,
    max_batch_size          INTEGER DEFAULT NULL,
    input_type              TEXT    DEFAULT NULL,
    input_type_query        TEXT    DEFAULT NULL,
    tls_config              JSONB   DEFAULT NULL,
    is_hcp_model            BOOLEAN DEFAULT NULL
) -- returns JSONB

aidb.completions_config(
    model                   TEXT,
    api_key                 TEXT              DEFAULT NULL,  -- leave unset for create_model
    basic_auth              TEXT              DEFAULT NULL,  -- leave unset for create_model
    url                     TEXT              DEFAULT NULL,
    max_concurrent_requests INTEGER           DEFAULT NULL,
    max_tokens              JSONB             DEFAULT NULL,  -- build with aidb.max_tokens_config()
    is_hcp_model            BOOLEAN           DEFAULT NULL,
    system_prompt           TEXT              DEFAULT NULL,
    temperature             DOUBLE PRECISION  DEFAULT NULL,
    top_p                   DOUBLE PRECISION  DEFAULT NULL,
    seed                    BIGINT            DEFAULT NULL,
    thinking                BOOLEAN           DEFAULT NULL,
    extra_args              JSONB             DEFAULT NULL
) -- returns JSONB

aidb.max_tokens_config(size INTEGER, format TEXT DEFAULT NULL) -- returns JSONB

aidb.openai_responses_config(
    model                   TEXT,
    api_key                 TEXT             DEFAULT NULL,
    basic_auth              TEXT             DEFAULT NULL,
    url                     TEXT             DEFAULT NULL,
    max_concurrent_requests INTEGER          DEFAULT NULL,
    system_prompt           TEXT             DEFAULT NULL,
    temperature             DOUBLE PRECISION DEFAULT NULL,
    max_output_tokens       INTEGER          DEFAULT NULL,
    top_p                   DOUBLE PRECISION DEFAULT NULL,
    extra_args              JSONB            DEFAULT NULL
) -- returns JSONB

aidb.anthropic_messages_config(
    model                   TEXT,
    api_key                 TEXT             DEFAULT NULL,
    basic_auth              TEXT             DEFAULT NULL,
    url                     TEXT             DEFAULT NULL,
    max_concurrent_requests INTEGER          DEFAULT NULL,
    system_prompt           TEXT             DEFAULT NULL,
    temperature             DOUBLE PRECISION DEFAULT NULL,
    max_tokens              INTEGER          DEFAULT NULL,
    top_p                   DOUBLE PRECISION DEFAULT NULL,
    extra_args              JSONB            DEFAULT NULL
) -- returns JSONB

aidb.openrouter_chat_config(model TEXT, api_key TEXT DEFAULT NULL, url TEXT DEFAULT NULL, max_concurrent_requests INTEGER DEFAULT NULL, max_tokens JSONB DEFAULT NULL) -- returns JSONB
aidb.openrouter_embeddings_config(model TEXT, api_key TEXT DEFAULT NULL, url TEXT DEFAULT NULL, max_concurrent_requests INTEGER DEFAULT NULL, max_batch_size INTEGER DEFAULT NULL) -- returns JSONB
aidb.gemini_config(api_key TEXT, model TEXT DEFAULT NULL, url TEXT DEFAULT NULL, max_concurrent_requests INTEGER DEFAULT NULL, thinking_budget INTEGER DEFAULT NULL) -- returns JSONB; api_key comes first

aidb.nim_clip_config(api_key TEXT DEFAULT NULL, basic_auth TEXT DEFAULT NULL, model TEXT DEFAULT NULL, url TEXT DEFAULT NULL, is_hcp_model BOOLEAN DEFAULT NULL) -- returns JSONB
aidb.nim_ocr_config(api_key TEXT DEFAULT NULL, basic_auth TEXT DEFAULT NULL, url TEXT DEFAULT NULL, model TEXT DEFAULT NULL, is_hcp_model BOOLEAN DEFAULT NULL) -- returns JSONB; url comes before model
aidb.nim_reranking_config(api_key TEXT DEFAULT NULL, basic_auth TEXT DEFAULT NULL, model TEXT DEFAULT NULL, url TEXT DEFAULT NULL, is_hcp_model BOOLEAN DEFAULT NULL) -- returns JSONB

-- In-process local models
aidb.bert_config(model TEXT, revision TEXT DEFAULT NULL, cache_dir TEXT DEFAULT NULL) -- returns JSONB
aidb.clip_config(model TEXT, revision TEXT DEFAULT NULL, cache_dir TEXT DEFAULT NULL, image_size INTEGER DEFAULT NULL) -- returns JSONB
aidb.llama_config(
    model               TEXT,
    revision            TEXT             DEFAULT NULL,
    cache_dir           TEXT             DEFAULT NULL,
    system_prompt       TEXT             DEFAULT NULL,
    use_flash_attention BOOLEAN          DEFAULT NULL,
    model_path          TEXT             DEFAULT NULL,
    seed                BIGINT           DEFAULT NULL,
    temperature         DOUBLE PRECISION DEFAULT NULL,
    top_p               DOUBLE PRECISION DEFAULT NULL,
    sample_len          INTEGER          DEFAULT NULL,
    use_kv_cache        BOOLEAN          DEFAULT NULL,
    repeat_penalty      REAL             DEFAULT NULL,
    repeat_last_n       INTEGER          DEFAULT NULL
) -- returns JSONB
aidb.t5_config(
    model          TEXT,
    revision       TEXT             DEFAULT NULL,
    temperature    DOUBLE PRECISION DEFAULT NULL,
    seed           BIGINT           DEFAULT NULL,
    max_tokens     INTEGER          DEFAULT NULL,
    repeat_penalty REAL             DEFAULT NULL,
    repeat_last_n  INTEGER          DEFAULT NULL,
    model_path     TEXT             DEFAULT NULL,
    cache_dir      TEXT             DEFAULT NULL,
    top_p          DOUBLE PRECISION DEFAULT NULL
) -- returns JSONB
```

---

## Vector Index Config Builders

```sql
aidb.vector_index_hnsw_config(
    vector_data_type TEXT    DEFAULT NULL,
    m                INTEGER DEFAULT NULL,
    ef_construction  INTEGER DEFAULT NULL,
    ef_search        INTEGER DEFAULT NULL
) -- returns JSONB

aidb.vector_index_ivfflat_config(
    vector_data_type TEXT    DEFAULT NULL,
    lists            INTEGER DEFAULT NULL,
    probes           INTEGER DEFAULT NULL
) -- returns JSONB

aidb.vector_index_chord_hnsw_config(
    vector_data_type TEXT              DEFAULT NULL,
    m                INTEGER           DEFAULT NULL,
    ef_construction  INTEGER           DEFAULT NULL,
    max_connections  INTEGER           DEFAULT NULL,
    ml               DOUBLE PRECISION  DEFAULT NULL
) -- returns JSONB

aidb.vector_index_chord_vchordq_config(
    vector_data_type    TEXT    DEFAULT NULL,
    lists               TEXT    DEFAULT NULL,
    spherical_centroids BOOLEAN DEFAULT NULL
) -- returns JSONB

aidb.vector_index_hsphere_optimized_config(
    clusters         INTEGER,
    precision_val    DOUBLE PRECISION,
    vector_data_type TEXT DEFAULT NULL
) -- returns JSONB

aidb.vector_index_disabled_config() -- returns JSONB
```

---

## Semantic Knowledge Base

A semantic KB indexes schema metadata (tables, views, columns, comments) so an agent can find the right objects to query. It does not embed document contents; use a `KnowledgeBase` pipeline for that.

```sql
aidb.create_semantic_kb(
    name                 TEXT    DEFAULT NULL,
    model                TEXT    DEFAULT NULL,
    schemas              TEXT[]  DEFAULT ARRAY['public'],
    auto_processing      aidb.PipelineAutoProcessingMode DEFAULT 'Disabled',
    bypass_triggers      BOOLEAN DEFAULT FALSE,
    vector_index         JSONB   DEFAULT NULL,
    curation             BOOLEAN DEFAULT FALSE,
    user_defined_context TEXT    DEFAULT NULL
) -- returns TEXT

aidb.delete_semantic_kb(name TEXT DEFAULT NULL) -- returns VOID

aidb.list_semantic_kbs()
    -- returns TABLE(name TEXT, model_name TEXT, schemas TEXT[], auto_processing TEXT, bypass_triggers BOOLEAN, curation BOOLEAN,
    --               user_defined_context TEXT, context_updated_at TIMESTAMP, context_updated_by TEXT, plugin_name TEXT,
    --               context_processed_at TIMESTAMP, context_processed_model TEXT, context_attempted_at TIMESTAMP)

aidb.refresh_semantic_kb(name TEXT DEFAULT NULL) -- returns VOID

aidb.update_semantic_kb_auto_processing(
    name            TEXT DEFAULT NULL,
    auto_processing aidb.PipelineAutoProcessingMode DEFAULT NULL
) -- returns VOID

aidb.update_semantic_kb_context(user_defined_context TEXT, kb_name TEXT DEFAULT NULL)       -- returns VOID
aidb.update_semantic_kb_curation(kb_name TEXT DEFAULT NULL, enabled BOOLEAN DEFAULT NULL)    -- returns JSONB
aidb.update_semantic_kb_history_filter(kb_name TEXT DEFAULT NULL, exclude_roles TEXT[] DEFAULT NULL) -- returns JSONB

aidb.semantic_kb_stats(kb_name TEXT DEFAULT NULL)
    -- returns TABLE(total BIGINT, tables BIGINT, views BIGINT, columns BIGINT, pending BIGINT, relationships BIGINT, drifted BIGINT, queries BIGINT)

-- Search functions (similarity-ranked; kb_name NULL picks the default KB)
aidb.semantic_kb_search(
    query_text       TEXT,
    kb_name          TEXT             DEFAULT NULL,
    top_k            INTEGER          DEFAULT 10,
    sources          TEXT[]           DEFAULT NULL,
    entity_types     TEXT[]           DEFAULT NULL,
    rrf_k            INTEGER          DEFAULT 60,
    min_similarity   DOUBLE PRECISION DEFAULT NULL,
    include_proposed BOOLEAN          DEFAULT FALSE
) -- returns TABLE(source_type TEXT, entity_type TEXT, schema_name TEXT, relation_name TEXT, column_name TEXT, object_ref TEXT,
  --               definition TEXT, comment TEXT, score DOUBLE PRECISION, rank INTEGER, components JSONB)
  -- hybrid search that fuses several sources with reciprocal rank fusion; a good first call

aidb.get_metadata(
    kb_name        TEXT             DEFAULT NULL,
    query_text     TEXT             DEFAULT NULL,
    min_similarity DOUBLE PRECISION DEFAULT NULL,
    top_k          INTEGER          DEFAULT 10,
    "offset"       INTEGER          DEFAULT 0
) -- returns TABLE(schema_name TEXT, relation_name TEXT, column_name TEXT, entity_type TEXT, definition TEXT, comment TEXT,
  --               definition_vector TEXT, similarity DOUBLE PRECISION)

aidb.get_column_definitions(kb_name TEXT DEFAULT NULL, query_text TEXT DEFAULT NULL, min_similarity DOUBLE PRECISION DEFAULT NULL, top_k INTEGER DEFAULT 10, "offset" INTEGER DEFAULT 0)
    -- returns TABLE(definition TEXT)

aidb.get_entity_definitions(
    kb_name        TEXT             DEFAULT NULL,
    query_text     TEXT             DEFAULT NULL,
    min_similarity DOUBLE PRECISION DEFAULT NULL,
    entity_types   TEXT[]           DEFAULT ARRAY['Table', 'View'],
    top_k          INTEGER          DEFAULT 10,
    "offset"       INTEGER          DEFAULT 0
) -- returns TABLE(schema_name TEXT, relation_name TEXT, entity_type TEXT, definition TEXT, comment TEXT, similarity DOUBLE PRECISION)
  -- use entity_types => ARRAY['Table'] to find tables

aidb.search_by_comment(kb_name TEXT DEFAULT NULL, query_text TEXT DEFAULT NULL, min_similarity DOUBLE PRECISION DEFAULT NULL, top_k INTEGER DEFAULT 10, "offset" INTEGER DEFAULT 0)
    -- returns TABLE(schema_name TEXT, relation_name TEXT, column_name TEXT, entity_type TEXT, definition TEXT, comment TEXT, similarity DOUBLE PRECISION)
```

The search parameters are `query_text` and `top_k` (not `query` / `limit`). `"offset"` is a reserved word, so quote it in named calls: `"offset" => 10`.

**Similarity thresholds:** `min_similarity` defaults to NULL (no cutoff). Useful values depend on the embedding model, so run a search without a threshold first, look at the `similarity` values returned, and pick a cutoff from those.

### Semantic KB Relationships & Join Routing

A join-graph layer on top of the semantic KB. Check each function's signature with `\df+ aidb.<name>` before calling it, since this area has many optional parameters.

- Relationship management: `aidb.add_relationship`, `aidb.add_relationship_as_agent`, `aidb.list_relationships`, `aidb.delete_relationship` (two overloads: by `relationship_id`, or by endpoints and columns).
- Review of derived candidates: `aidb.relationship_candidates`, `aidb.accept_relationship_candidates(candidate_ids BIGINT[], kb_name TEXT DEFAULT NULL, cardinality TEXT DEFAULT NULL, is_nullable BOOLEAN DEFAULT NULL)`, `aidb.relationship_drift(kb_name TEXT DEFAULT NULL)`.
- Join routing: `aidb.suggest_joins(kb_name TEXT DEFAULT NULL, relations TEXT[] DEFAULT NULL, include_proposed BOOLEAN DEFAULT FALSE)`, `aidb.find_join_path(kb_name TEXT DEFAULT NULL, from_object TEXT DEFAULT NULL, to_object TEXT DEFAULT NULL, max_hops INTEGER DEFAULT 4, max_paths INTEGER DEFAULT 10)`, `aidb.semantic_kb_subgraph(kb_name TEXT DEFAULT NULL, query_text TEXT DEFAULT NULL, top_k INTEGER DEFAULT 10, max_hops INTEGER DEFAULT 3)`.
- Comment authoring: `aidb.add_comment_to_object`, `aidb.remove_comment`, `aidb.list_proposed_comments(kb_name TEXT DEFAULT NULL)`, `aidb.resolve_object_comment`.
- Query-history mining from `pg_stat_statements`: `aidb.import_query_history(kb_name TEXT DEFAULT NULL, min_calls BIGINT DEFAULT 5, max_statements INTEGER DEFAULT 1000, derive_candidates BOOLEAN DEFAULT true)`, `aidb.list_query_history`.
- Audit: `aidb.semantic_kb_audit`.

**Important:** join routing only turns a relationship into executable SQL when it is approved (`status = 'approved'`, `level = 'data'`, and kind `key_join`, `junction` or `conditional`). Never present an unreviewed or candidate relationship as safe to route through.

---

## Semantic Aliases

```sql
aidb.create_semantic_alias(
    name        TEXT,
    description TEXT,
    query_text  TEXT,                -- SQL with ${param_name} placeholders
    params      JSONB DEFAULT NULL,  -- from aidb.alias_params()
    kb_name     TEXT  DEFAULT NULL   -- the KB supplies the embedding model
) -- returns TEXT

aidb.update_semantic_alias(
    name        TEXT,
    description TEXT  DEFAULT NULL,
    query_text  TEXT  DEFAULT NULL,
    params      JSONB DEFAULT NULL,
    kb_name     TEXT  DEFAULT NULL
) -- returns VOID

aidb.execute_semantic_alias(alias_name TEXT, args JSONB DEFAULT NULL, execute_role TEXT DEFAULT NULL)
    -- returns TABLE(result JSONB)

aidb.search_semantic_aliases(
    query_text     TEXT,
    min_similarity DOUBLE PRECISION DEFAULT NULL,
    top_k          INTEGER          DEFAULT 10,
    "offset"       INTEGER          DEFAULT 0,
    kb_name        TEXT             DEFAULT NULL
) -- returns TABLE(name TEXT, query_text TEXT, similarity DOUBLE PRECISION)

aidb.get_semantic_aliases()           -- returns TABLE(name TEXT, description TEXT, query_text TEXT, param_count INTEGER)

aidb.get_semantic_alias(alias_name TEXT) -- returns TABLE(name TEXT, description TEXT, query_text TEXT, params JSONB)

aidb.delete_semantic_alias(name TEXT) -- returns VOID

-- Parameter builders
aidb.alias_param(
    name        TEXT,
    param_type  TEXT,
    description TEXT   DEFAULT NULL,
    enum_values TEXT[] DEFAULT NULL
) -- returns JSONB

aidb.alias_params(VARIADIC params JSONB[]) -- returns JSONB
```

---

## Volume Management

```sql
aidb.create_volume(
    name        TEXT,
    server_name TEXT,  -- a PGFS storage location
    path        TEXT,
    data_format TEXT   -- 'Text' | 'Image' | 'Pdf'
) -- returns VOID

aidb.list_volumes() -- returns TABLE(schema TEXT, volume TEXT, storage_location TEXT, path TEXT)

aidb.delete_volume(name TEXT) -- returns VOID

aidb.read_volume_file(volume_name TEXT, name TEXT) -- returns BYTEA

aidb.write_volume_data(volume_name TEXT, name TEXT, data BYTEA) -- returns VOID

aidb.delete_volume_file(volume_name TEXT, name TEXT) -- returns VOID

aidb.list_volume_content(name TEXT) -- returns TABLE(file_name TEXT, size BIGINT, last_modified TEXT)

aidb.is_volume(schema_name TEXT, name TEXT) -- returns BOOLEAN
```

---

## Agent Hub

```sql
aidb.create_agent(
    name                TEXT,
    instructions        TEXT,
    model               TEXT,
    tools               TEXT[]  DEFAULT NULL,  -- names from the aidb.tools view
    delegates           TEXT[]  DEFAULT NULL,  -- other agents this agent may hand work to
    purpose             TEXT    DEFAULT NULL,  -- a name from aidb.purpose_registry
    output_type         JSONB   DEFAULT NULL,  -- via aidb.output_type()/output_field()
    input_token_budget  INTEGER DEFAULT NULL,
    output_token_budget INTEGER DEFAULT NULL,
    max_iterations      INTEGER DEFAULT NULL,
    timeout             INTEGER DEFAULT NULL,
    budget_strategy     TEXT    DEFAULT 'attempt_complete',  -- 'ignore' | 'error' | 'summarize' | 'attempt_complete'
    preset              TEXT    DEFAULT NULL,
    plugins             TEXT[]  DEFAULT NULL   -- names from aidb.agent_plugins
) -- returns TABLE(error TEXT)

aidb.update_agent(
    name                TEXT,
    instructions        TEXT    DEFAULT NULL,
    model               TEXT    DEFAULT NULL,
    tools               TEXT[]  DEFAULT NULL,
    delegates           TEXT[]  DEFAULT NULL,
    purpose             TEXT    DEFAULT NULL,
    output_type         JSONB   DEFAULT NULL,
    input_token_budget  INTEGER DEFAULT NULL,
    output_token_budget INTEGER DEFAULT NULL,
    max_iterations      INTEGER DEFAULT NULL,
    timeout             INTEGER DEFAULT NULL,
    budget_strategy     TEXT    DEFAULT NULL,
    preset              TEXT    DEFAULT NULL,
    plugins             TEXT[]  DEFAULT NULL
) -- returns TABLE(error TEXT)

aidb.delete_agent(name TEXT, force BOOLEAN DEFAULT NULL) -- returns TABLE(error TEXT)

aidb.agent_converse(
    agent_name       TEXT,
    prompt           TEXT,
    conversation_id  TEXT    DEFAULT NULL,  -- TEXT, not UUID: cast uuid values with ::text
    read_only        BOOLEAN DEFAULT NULL,
    output_type      JSONB   DEFAULT NULL,
    debug            BOOLEAN DEFAULT NULL
) -- returns TABLE(message TEXT, conversation_id TEXT, error TEXT)

aidb.start_agent_session(agent_name TEXT)    -- returns TABLE(conversation_id TEXT)
aidb.get_conversation(conversation_id TEXT)  -- returns TABLE(message_id TEXT, task_id TEXT, action_type TEXT, role TEXT, sender_id TEXT, contents TEXT)
aidb.get_message(message_id TEXT)            -- returns TABLE(message TEXT, error TEXT)

aidb.sleep(seconds DOUBLE PRECISION) -- returns JSONB; backs the built-in sleep tool, which every agent is offered

-- Structured output schema builders
aidb.output_field(name TEXT, field_type TEXT, description TEXT DEFAULT NULL) -- returns JSONB
aidb.output_type(VARIADIC fields JSONB[])                                    -- returns JSONB
```

The `aidb.agents` table lists registered agents: `id UUID, name, instructions, model, delegates TEXT[], tools TEXT[], output_type JSONB, input_token_budget, output_token_budget, max_iterations, timeout_seconds, budget_strategy, preset, created_at, updated_at, purpose, plugins TEXT[]`. Query it with `SELECT name, model, tools, purpose FROM aidb.agents;`.

Caller-facing views: `aidb.agent_tasks`, `aidb.conversations`, `aidb.conversation_log`. Their id columns are `uuid`; cast with `::text` before passing one to `agent_converse` or `get_conversation`.

**Error convention:** `create_agent`, `update_agent`, `delete_agent`, `agent_converse`, `get_message` and the agent plugin functions never raise. A failure is reported in the `error` column so it does not abort the caller's transaction; always check it. `start_agent_session` and `get_conversation` raise normally, as do the Tools Hub functions below.

### Agent plugins

A plugin bundles a prompt fragment and a tool list that several agents can share through `create_agent(..., plugins => ARRAY['name'])`. Every tool a plugin lists must exist in `aidb.tools`.

```sql
aidb.create_agent_plugin(name TEXT, description TEXT, prompt_content TEXT, tools TEXT[] DEFAULT NULL) -- returns TABLE(error TEXT)

aidb.update_agent_plugin(name TEXT, description TEXT DEFAULT NULL, prompt_content TEXT DEFAULT NULL, tools TEXT[] DEFAULT NULL) -- returns TABLE(error TEXT)

aidb.delete_agent_plugin(name TEXT, force BOOLEAN DEFAULT NULL) -- returns TABLE(error TEXT)
    -- refused while an agent uses the plugin, unless force => true, which detaches it from every agent first
```

The `aidb.agent_plugins` view lists plugins: `id, name, description, prompt_content, tools TEXT[], created_at, updated_at, managed_by`.

---

## Tools Hub

```sql
aidb.run_tool(name TEXT, arguments JSONB DEFAULT '{}') -- returns JSONB; the single dispatch entry point for all tool calls

aidb.create_sql_tool(
    name              TEXT,
    description       TEXT,
    sql_statement     TEXT,   -- may use ${param_name} placeholders
    params            JSONB,  -- required: via aidb.tool_params()/tool_param(); use aidb.tool_params() for no parameters
    read_only         BOOLEAN DEFAULT TRUE,
    return_type_hint  JSONB   DEFAULT NULL
) -- returns TEXT

aidb.delete_tool(name TEXT) -- returns TEXT
aidb.get_mcp_tools()        -- returns TABLE(name TEXT, description TEXT, input_schema JSONB)
aidb.run_sql_query(query TEXT) -- returns SETOF JSONB
aidb.explain_query(sql TEXT, "analyze" BOOLEAN DEFAULT FALSE) -- returns JSONB
aidb.list_native_tools()       -- returns TABLE(name TEXT, description TEXT, tool_type TEXT, params JSONB, read_only BOOLEAN)
aidb.list_cached_mcp_tools()   -- same columns

-- MCP server registration
aidb.import_mcp_tools(
    name               TEXT,
    url                TEXT,
    transport          TEXT   DEFAULT 'streamable_http',  -- 'streamable_http' | 'sse'
    headers            JSONB  DEFAULT NULL,
    tool_filter        TEXT[] DEFAULT NULL,
    headers_env        TEXT   DEFAULT NULL,  -- read headers from this allow-listed env var instead of `headers`
    headers_k8s_secret TEXT   DEFAULT NULL   -- read headers from this allow-listed mounted secret path instead
) -- returns TEXT
aidb.refresh_mcp_tools(name TEXT) -- returns INTEGER; re-fetches the tool cache for one server

-- Catalog discovery tools (each capped in row count, with a truncated column, built for a model's context window)
aidb.catalog_list_schemas(include_system BOOLEAN DEFAULT FALSE)
    -- returns TABLE(schema_name TEXT, owner TEXT, comment TEXT, truncated BOOLEAN)
aidb.catalog_list_objects(schema TEXT DEFAULT NULL, object_type TEXT DEFAULT 'all', name_pattern TEXT DEFAULT NULL)
    -- returns TABLE(schema_name TEXT, object_name TEXT, object_type TEXT, comment TEXT, truncated BOOLEAN)
aidb.catalog_get_object_details(name TEXT, schema TEXT DEFAULT 'public')
    -- returns TABLE(kind TEXT, details JSONB); object name first, schema second
aidb.catalog_list_relations(schema TEXT DEFAULT NULL, name TEXT DEFAULT NULL)
    -- returns foreign keys: TABLE(constraint_name TEXT, schema_name TEXT, table_name TEXT, columns TEXT[], referenced_schema TEXT,
    --                             referenced_table TEXT, referenced_columns TEXT[], truncated BOOLEAN)
aidb.catalog_list_sequences(schema TEXT DEFAULT NULL)
    -- returns TABLE(schema_name TEXT, sequence_name TEXT, comment TEXT, truncated BOOLEAN)
aidb.catalog_list_extensions()
    -- returns TABLE(extension_name TEXT, version TEXT, relocatable BOOLEAN, comment TEXT, truncated BOOLEAN)
aidb.analyze_db_health(checks TEXT[] DEFAULT NULL) -- returns JSONB

-- Parameter declaration helpers
aidb.param(name TEXT, param_type TEXT, description TEXT DEFAULT NULL, enum_values TEXT[] DEFAULT NULL) -- returns JSONB
aidb.params(VARIADIC params JSONB[])                                                                     -- returns JSONB
aidb.tool_param(name TEXT, type TEXT, description TEXT)  -- returns JSONB; description is required, unlike aidb.param
aidb.tool_params(VARIADIC params JSONB[])                -- returns JSONB
aidb.tool_params()                                       -- returns JSONB; no-argument overload for tools without parameters
```

`aidb.tools` is the single governed registry (a view: `name, description, tool_type, params, read_only`) unioning native tools, `aidb.sql_tool_registry` rows, and cached MCP tools. An agent's `tools` list is validated and resolved against it.

---

## Agent Memory

Schema: `aidb_memory`. A namespace binds a memory runtime. Providers: `mock` (testing) and `agent_memory_container` (an external Agent Memory container reached at `base_url`). The background dispatcher runs only when `aidb.agent_memory_enable_worker` is on.

```sql
aidb_memory.init(name TEXT, config JSONB)
    -- config holds model_spec, agentmemory_spec {provider, config}, storage_spec, sessionmemory_specs
    -- returns TABLE(namespace TEXT, status TEXT, created_at TIMESTAMPTZ, model_spec JSONB, agentmemory_spec JSONB,
    --               storage_spec JSONB, sessionmemory_specs JSONB)

aidb_memory.add(
    turns             JSONB,
    namespace         TEXT,
    actor_id          TEXT,
    agent_id          TEXT  DEFAULT NULL,
    session_id        TEXT  DEFAULT NULL,
    idempotency_key   TEXT  DEFAULT NULL,
    metadata          JSONB DEFAULT '{}'
) -- returns UUID; turns comes first

aidb_memory.status(
    namespace   TEXT,
    id          UUID    DEFAULT NULL,
    actor_id    TEXT    DEFAULT NULL,
    session_id  TEXT    DEFAULT NULL,
    agent_id    TEXT    DEFAULT NULL,
    "limit"     INTEGER DEFAULT 100,
    "offset"    INTEGER DEFAULT 0
) -- returns TABLE(id UUID, namespace TEXT, status TEXT, actor_id TEXT, session_id TEXT, agent_id TEXT, error_detail TEXT)
  -- ingestion status of earlier add() calls

aidb_memory.search(
    namespace   TEXT,
    actor_id    TEXT,
    query       TEXT,
    "limit"     INTEGER DEFAULT 10,
    session_id  TEXT    DEFAULT NULL,
    agent_id    TEXT    DEFAULT NULL
) -- returns TABLE(id UUID, content TEXT, score REAL, metadata JSONB)

aidb_memory.list_memories(
    namespace   TEXT,
    actor_id    TEXT,
    session_id  TEXT    DEFAULT NULL,
    agent_id    TEXT    DEFAULT NULL,
    "limit"     INTEGER DEFAULT 100
) -- returns TABLE(id UUID, content TEXT, agent_id TEXT, session_id TEXT)

aidb_memory.delete(
    namespace   TEXT,
    actor_id    TEXT,
    selector    JSONB,
    reason      TEXT,
    session_id  TEXT DEFAULT NULL,
    agent_id    TEXT DEFAULT NULL
) -- returns BIGINT (count deleted)

-- Session tier: durable, resumable conversation state with automatic compaction.
-- Never exposed as a tool or over MCP: session boundaries are the calling harness's
-- decision, not the model's.
aidb_memory.session_start(
    namespace   TEXT,
    actor_id    TEXT,
    agent_id    TEXT,
    session_id  TEXT DEFAULT NULL
) -- returns TABLE(session_id TEXT, prev_handoff JSONB, started_at TIMESTAMPTZ, closed_at TIMESTAMPTZ)
  -- idempotent: resuming an existing session returns its original started_at

aidb_memory.session_get(
    namespace       TEXT,
    actor_id        TEXT,
    session_id      TEXT,
    limit_chars     BIGINT DEFAULT NULL,
    fallback_model  TEXT   DEFAULT NULL  -- model to compact with if the namespace binding names none
) -- returns TABLE(items JSONB, overflow_chars BIGINT, compacted_entries INTEGER, warnings TEXT[])

aidb_memory.session_end(
    namespace   TEXT,
    actor_id    TEXT,
    session_id  TEXT,
    notes       TEXT DEFAULT NULL
) -- returns TABLE(session_id TEXT, ended_at TIMESTAMPTZ, handoff JSONB)
  -- closes a session, storing a deterministic handoff for the next session
```

Agent-facing native tools (in `aidb.tools`, callable directly too):

```sql
aidb.memory_add(namespace TEXT, actor_id TEXT, content TEXT, role TEXT DEFAULT 'user', session_id TEXT DEFAULT NULL, agent_id TEXT DEFAULT NULL, idempotency_key TEXT DEFAULT NULL)
    -- returns UUID
aidb.memory_search(namespace TEXT, actor_id TEXT, query TEXT, max_results INTEGER DEFAULT 10, session_id TEXT DEFAULT NULL, agent_id TEXT DEFAULT NULL)
    -- returns TABLE(content TEXT, score REAL, metadata JSONB)
```

Integration with Agent Hub: GUCs `aidb.agent_session_source` (`action_log` | `memory`) and `aidb.agent_memory_namespace`.

---

## Governance: Purpose Registry

Only the `aidb_governance` role may execute these three functions.

```sql
aidb.create_purpose(name TEXT, role TEXT, description TEXT DEFAULT NULL) -- returns VOID
    -- errors if name already exists (unless reviving a soft-deleted one)

aidb.update_purpose(name TEXT, role TEXT DEFAULT NULL, description TEXT DEFAULT NULL) -- returns VOID
    -- only non-NULL arguments are changed

aidb.delete_purpose(name TEXT) -- returns VOID
    -- soft delete: sets deleted_at, never physically removes the row (agents may still reference the name)
```

`aidb.purpose_registry` is a read-only view over the underlying registry (`name`, `role`, `description`, `created_at`, `updated_at`, `deleted_at`). A `purpose` can be assigned to an agent (`create_agent`/`update_agent`'s `purpose` argument); role switches performed under a purpose are recorded as audited decisions.

---

## GUC Parameters

The most commonly relevant parameters. The full surface is larger (OTel exporter and cleaner, model download and cache, semantic KB context); list everything with `SELECT name, setting, short_desc FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'edb.%';`.

| Parameter | Type | Default | Takes effect | Description |
|---|---|---|---|---|
| `aidb.max_threads` | INTEGER | 0 (half of CPUs) | Superuser `SET` immediately, or `pg_reload_conf()` | Thread pool for local model inference, 0 to 1024 |
| `aidb.pipeline_error_warnings` | BOOLEAN | on | Superuser `SET` | Emit per-error WARNING to the PostgreSQL log |
| `aidb.agent_memory_enable_worker` | BOOLEAN | off | Superuser `SET` (use `ALTER DATABASE ... SET` to scope per database) | Enables the Agent Memory background dispatcher |
| `aidb.agent_session_source` | ENUM | `action_log` | Superuser `SET` | `action_log` or `memory`: where Agent Hub reads session history from |
| `aidb.agent_memory_namespace` | TEXT | `pg_agents` | Superuser `SET` | Default Agent Memory namespace |
| `aidb.egress_allowlist` | TEXT | empty | Superuser `SET` | Comma-separated hosts or CIDR ranges for outbound calls (model providers, MCP servers). Overrides `edb.egress_allowlist` when set; if both are empty, egress is not restricted |
| `edb.egress_allowlist` | TEXT | empty | Superuser `SET` | Shared allowlist used by AIDB and other EDB extensions when `aidb.egress_allowlist` is empty |
| `aidb.allow_insecure_egress` | BOOLEAN | off | Superuser `SET` | Permits plaintext HTTP egress, to loopback and private destinations only |
| `aidb.allow_insecure_tls` | BOOLEAN | off | Superuser `SET` | Skips TLS certificate validation on outbound HTTPS |
| `aidb.env_var_allowed_prefix` | TEXT | `AIDB_` | Superuser `SET` | Required prefix for `credentials_env` / `headers_env` variable names |
| `aidb.k8s_secret_allowed_path_prefix` | TEXT | `/var/run/secrets/aidb` | Superuser `SET` | Required path prefix for `credentials_k8s_secret` / `headers_k8s_secret` |
| `aidb.otel_client` | ENUM | `noop` | Superuser `SET` | `noop` \| `stdout` \| `log` \| `database`, plus `grpc` in builds compiled with gRPC export (check `SELECT enumvals FROM pg_settings WHERE name = 'aidb.otel_client'`): telemetry export mode |

### Endpoint server GUCs

The endpoint server (`edb-endpoints`) runs the MCP server and the OTLP/HTTP receiver. All settings below are Sighup context (apply at `pg_reload_conf()`) except `edb.endpoints_enabled`, which needs a PostgreSQL restart. The supervisor only starts if `edb.endpoints_mcp_enabled` or `edb.endpoints_otlp_enabled` is on when PostgreSQL starts.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `edb.endpoints_enabled` | BOOLEAN | on | Master switch (Postmaster context). Off disables every endpoint server |
| `edb.endpoints_mcp_enabled` | BOOLEAN | off | Serves the MCP server |
| `edb.endpoints_otlp_enabled` | BOOLEAN | off | Serves the OTLP/HTTP receiver for external agents' telemetry |
| `edb.endpoints_host` | TEXT | `127.0.0.1` | Listen host of every endpoint server |
| `edb.endpoints_database` | TEXT | `postgres` | Comma-separated databases served; the first is the default at `/mcp`, each is also served at `/mcp/<name>` and `/otlp/<name>` |
| `edb.endpoints_mcp_port` | INTEGER | 8765 | MCP listen port |
| `edb.endpoints_otlp_port` | INTEGER | 8766 | OTLP receiver listen port; must differ from the MCP port |
| `edb.endpoints_mcp_host` / `edb.endpoints_mcp_database` | TEXT | empty | MCP-only overrides of `edb.endpoints_host` / `edb.endpoints_database` |
| `edb.endpoints_tls_cert` / `edb.endpoints_tls_key` | TEXT | empty | PEM certificate and key for every endpoint server. Empty serves plaintext HTTP, allowed on loopback hosts only |
| `edb.endpoints_mcp_tls_cert` / `edb.endpoints_mcp_tls_key` | TEXT | empty | MCP-only TLS pair, used instead of the shared pair when set |
| `edb.endpoints_oauth_issuer` | TEXT | empty | OAuth issuer (https URL) for bearer tokens. Empty disables OAuth. Requires `edb.endpoints_oauth_audience`, PostgreSQL 18, an `oauth` pg_hba line and a validator module; MCP also needs `edb.endpoints_oauth_scopes` and `edb.endpoints_mcp_oauth_resource_url` |
| `edb.endpoints_oauth_audience` | TEXT | empty | Expected JWT `aud`; a mismatch gets HTTP 401 |
| `edb.endpoints_oauth_scopes` | TEXT | empty | Space-separated scopes advertised to MCP clients; match the pg_hba `scope=` option |
| `edb.endpoints_oauth_role_claim` | TEXT | `preferred_username` | JWT claim that names the Postgres role |
| `edb.endpoints_mcp_oauth_resource_url` | TEXT | empty | Protected resource URL of the MCP endpoint (https, path `/mcp`); required whenever MCP has OAuth on |
| `edb.endpoints_mcp_oauth_issuer`, `edb.endpoints_mcp_oauth_audience`, `edb.endpoints_mcp_oauth_scopes`, `edb.endpoints_mcp_oauth_role_claim` | TEXT | empty (role claim `preferred_username`) | MCP-only OAuth set. When `edb.endpoints_mcp_oauth_issuer` is set, MCP takes its whole OAuth configuration from these instead of the shared `edb.endpoints_oauth_issuer` group |
| `edb.endpoints_restart_max` | INTEGER | 5 | Maximum spawns (including the first) within the restart window |
| `edb.endpoints_restart_window_secs` | INTEGER | 60 | Restart-intensity window in seconds |
| `edb.endpoints_command_path` | TEXT | empty | Path to the endpoint binary; empty derives it from the postmaster's bin directory |

**Deprecated:** `edb.endpoints_mcp_command_path`, `edb.endpoints_mcp_restart_max` and `edb.endpoints_mcp_restart_window_secs` still take precedence when set, but use `edb.endpoints_command_path`, `edb.endpoints_restart_max` and `edb.endpoints_restart_window_secs`.
