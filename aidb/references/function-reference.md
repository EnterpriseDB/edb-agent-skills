# AIDB Function Reference

> **Complete SQL function signatures for the `aidb` schema.**
> Source: `static-sql/` directory in the aidb repository, v7.7.0.

All functions live in the `aidb` schema. Call them as `aidb.<function_name>(...)`.

---

## Pipeline Management

### `aidb.create_pipeline`

Creates a new AI processing pipeline. The destination table must **not** already exist.

```sql
aidb.create_pipeline(
    name                TEXT,               -- max 46 characters
    source              TEXT,               -- table name or volume name
    source_data_column  TEXT     DEFAULT NULL,
    source_key_column   TEXT     DEFAULT NULL,
    destination         TEXT     DEFAULT NULL,
    auto_processing     TEXT     DEFAULT 'Disabled',  -- 'Live' | 'Background' | 'Disabled'
    step_1              TEXT,
    step_1_options      JSONB,
    step_2              TEXT     DEFAULT NULL,
    step_2_options      JSONB    DEFAULT NULL,
    -- ... up to step_10 / step_10_options
)
```

**Constraints:**
- Max pipeline name: **46 characters**
- Max steps: **10**
- Destination table must not exist at creation time
- Steps are validated for compatible sequencing at creation time
- Models are validated at creation time

### `aidb.update_pipeline`

Updates pipeline configuration. Only provided parameters are changed.

```sql
aidb.update_pipeline(
    name            TEXT,
    auto_processing TEXT     DEFAULT NULL,
    destination     TEXT     DEFAULT NULL,
    batch_size      INTEGER  DEFAULT NULL,
    sync_interval   INTERVAL DEFAULT NULL
)
```

### `aidb.run_pipeline`

Manually triggers pipeline execution. Works in all modes.

```sql
aidb.run_pipeline(
    name        TEXT,
    force_sync  BOOLEAN DEFAULT FALSE  -- if TRUE, processes all rows regardless of state
)
```

### `aidb.delete_pipeline`

```sql
aidb.delete_pipeline(name TEXT, cascade BOOLEAN DEFAULT FALSE)
```

`cascade => TRUE` drops the destination table as well.

### `aidb.list_pipelines` / `aidb.get_pipeline`

```sql
aidb.list_pipelines()           -- returns all pipeline metadata rows
aidb.get_pipeline(name TEXT)    -- returns one pipeline row
```

---

## Model Management

### `aidb.create_model`

Registers an AI model for use in pipelines and standalone functions.

```sql
aidb.create_model(
    name        TEXT,
    provider    TEXT,
    config      JSONB DEFAULT NULL,
    credentials JSONB DEFAULT NULL,
    validate    BOOLEAN DEFAULT TRUE  -- set FALSE to skip provider validation
)
```

Credentials are stored in `pg_user_mappings` (never returned by `list_models`).

### `aidb.list_models` / `aidb.get_model`

```sql
aidb.list_models()
aidb.get_model(name TEXT)
aidb.get_model_description(name TEXT)
aidb.get_adapter_embedding_dimensions(name TEXT)  -- returns INTEGER
```

### `aidb.create_model_server` / `aidb.remove_cached_model`

```sql
aidb.create_model_server(name TEXT, provider TEXT, config JSONB, credentials JSONB)
aidb.remove_cached_model(name TEXT)   -- evicts from LRU cache, forcing reload
```

---

## Standalone AI Operations

### Embeddings

```sql
aidb.encode_text(input TEXT, model TEXT)                         -- returns VECTOR
aidb.encode_text_batch(inputs TEXT[], model TEXT)                -- returns VECTOR[]
aidb.encode_text_query(input TEXT, model TEXT)                   -- returns VECTOR (query-side for bi-encoders)
aidb.encode_image(image BYTEA, model TEXT)                       -- returns VECTOR
aidb.rerank_text(query TEXT, candidates TEXT[], model TEXT)      -- returns TABLE(index INT, score FLOAT)
```

### Text Preparation

```sql
aidb.chunk_text(input TEXT, options JSONB DEFAULT '{}')
    -- returns TABLE(part_id INT, value TEXT)

aidb.summarize_text(input TEXT, options JSONB DEFAULT '{}')
    -- returns TEXT

aidb.parse_html(input BYTEA, options JSONB DEFAULT '{}')
    -- returns TEXT

aidb.parse_pdf(input BYTEA, options JSONB DEFAULT '{}')
    -- returns TEXT

aidb.perform_ocr(image BYTEA, model TEXT)
    -- returns TEXT
```

---

## Step Config Helpers

These return JSONB for use as `step_N_options` in `create_pipeline`.

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

aidb.html_parse_config(method TEXT DEFAULT NULL)
    -- method: 'StructuredPlaintext' | 'StructuredMarkdown'

aidb.pdf_parse_config(method TEXT, allow_partial_parsing BOOLEAN DEFAULT NULL)
    -- method: currently only 'Structured'

aidb.ocr_config(model TEXT)

aidb.knowledge_base_config(
    model             TEXT,
    data_format       TEXT,    -- 'Text' | 'Image'
    distance_operator TEXT     DEFAULT NULL,   -- 'L2' | 'Cosine' | 'InnerProduct' | 'L1' | 'Hamming' | 'Jaccard'
    vector_index      JSONB    DEFAULT NULL
) -- returns JSONB

aidb.knowledge_base_config_from_kb(data_format TEXT) -- returns JSONB
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
) -- returns JSONB
```

---

## Model Config Builders

```sql
aidb.embeddings_config(
    model                   TEXT,
    api_key                 TEXT    DEFAULT NULL,
    basic_auth              TEXT    DEFAULT NULL,
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
    api_key                 TEXT              DEFAULT NULL,
    basic_auth              TEXT              DEFAULT NULL,
    url                     TEXT              DEFAULT NULL,
    max_concurrent_requests INTEGER           DEFAULT NULL,
    max_tokens              JSONB             DEFAULT NULL,
    is_hcp_model            BOOLEAN           DEFAULT NULL,
    system_prompt           TEXT              DEFAULT NULL,
    temperature             DOUBLE PRECISION  DEFAULT NULL,
    top_p                   DOUBLE PRECISION  DEFAULT NULL,
    seed                    BIGINT            DEFAULT NULL,
    thinking                BOOLEAN           DEFAULT NULL,
    extra_args              JSONB             DEFAULT NULL
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

aidb.vector_index_disabled_config() -- returns JSONB
```

---

## Semantic Knowledge Base

### Management

```sql
aidb.create_semantic_kb(
    name            TEXT,
    model           TEXT,
    schemas         TEXT[],
    auto_processing TEXT    DEFAULT 'Disabled',
    bypass_triggers BOOLEAN DEFAULT FALSE,
    vector_index    JSONB   DEFAULT NULL
)

aidb.delete_semantic_kb(name TEXT)
aidb.list_semantic_kbs()
aidb.refresh_semantic_kb(name TEXT)
aidb.update_semantic_kb_auto_processing(name TEXT, mode TEXT)
aidb.semantic_kb_stats(name TEXT)
```

### Search Functions (all return similarity-ranked rows)

```sql
aidb.get_metadata(
    kb_name TEXT, query TEXT, min_similarity NUMERIC,
    limit INTEGER, offset INTEGER
) -- returns TABLE(schema_name, relation_name, column_name, entity_type, definition, comment, similarity)

aidb.get_tables(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)
aidb.get_columns(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)
aidb.get_column_definitions(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)
aidb.get_entity_definitions(kb_name TEXT, query TEXT, min_similarity NUMERIC, types TEXT[], limit INTEGER, offset INTEGER)
aidb.search_by_comment(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)

-- Combined search (schema entities + semantic aliases in one ranked list):
aidb.semantic_kb_search(
    query          TEXT,
    kb_name        TEXT    DEFAULT NULL,  -- optional if only one KB exists
    top_k          INTEGER DEFAULT 10,
    min_similarity NUMERIC DEFAULT NULL,
    sources        TEXT[]  DEFAULT NULL,  -- e.g., ARRAY['schema', 'alias']
    entity_types   TEXT[]  DEFAULT NULL   -- e.g., ARRAY['Table', 'View', 'Column']
) -- returns TABLE(source_type, entity_type, relation_name, column_name, object_ref, definition, score, rank)
```

**Similarity threshold guidance:**
- `0.9+` — near-exact matches only
- `0.8` — good default for semantic search
- `0.5–0.7` — broad exploration

---

## Semantic Aliases

```sql
aidb.create_semantic_alias(
    name        TEXT,
    description TEXT,
    query_text  TEXT,   -- SQL with ${param_name} placeholders
    params      JSONB,  -- from aidb.alias_params()
    model       TEXT
)

aidb.execute_semantic_alias(name TEXT, args JSONB, role TEXT DEFAULT NULL)
aidb.search_semantic_aliases(model TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)
aidb.get_semantic_aliases()
aidb.get_semantic_alias(name TEXT)
aidb.delete_semantic_alias(name TEXT)

-- Parameter builders:
aidb.alias_param(
    name        TEXT,
    param_type  TEXT,
    description TEXT,
    enum_values TEXT[] DEFAULT NULL
)
aidb.alias_params(VARIADIC params RECORD[])
```

---

## Volume Management

```sql
aidb.create_volume(
    name             TEXT,
    storage_location TEXT,
    path             TEXT,
    data_type        TEXT   -- 'Text' | 'Bytes' | 'Image'
)

aidb.list_volumes()
aidb.delete_volume(name TEXT)
aidb.read_volume_file(volume_name TEXT, file_path TEXT)
aidb.write_volume_data(volume_name TEXT, file_path TEXT, data BYTEA)
aidb.delete_volume_file(volume_name TEXT, file_path TEXT)
aidb.list_volume_content(volume_name TEXT)
aidb.is_volume(schema TEXT, identifier TEXT)  -- returns BOOLEAN
```

---

## Agent Hub

```sql
aidb.create_agent(
    name         TEXT,
    instructions TEXT,
    model        TEXT,
    -- optional: tools TEXT[], delegates TEXT[], role TEXT, budgets JSONB
)

aidb.agent_converse(
    agent_name      TEXT,
    prompt          TEXT,
    conversation_id UUID    DEFAULT NULL,
    read_only       BOOLEAN DEFAULT FALSE,
    output_type     JSONB   DEFAULT NULL,
    debug           BOOLEAN DEFAULT FALSE
) -- returns TABLE(conversation_id UUID, message TEXT, error TEXT)

aidb.update_agent(name TEXT, ...)
aidb.delete_agent(name TEXT, force BOOLEAN DEFAULT FALSE)
aidb.start_agent_session(agent_name TEXT)  -- returns TABLE(conversation_id UUID)
aidb.get_conversation(id UUID)
aidb.get_message(id UUID)
```

**Agent error convention:** `agent_converse` never raises. Check the `error` column.

---

## Tools Hub

```sql
aidb.run_tool(name TEXT, arguments JSONB DEFAULT '{}')  -- returns JSONB

aidb.create_sql_tool(
    name            TEXT,
    description     TEXT,
    sql_statement   TEXT,
    params          JSONB,
    read_only       BOOLEAN DEFAULT TRUE,
    return_type_hint TEXT   DEFAULT NULL
)

aidb.import_mcp_tools(name TEXT, url TEXT, transport TEXT, headers JSONB,
                       tool_filter TEXT[], headers_env JSONB)
aidb.refresh_mcp_tools(name TEXT)   -- returns INT (count refreshed)
aidb.delete_tool(name TEXT)
aidb.list_native_tools()
aidb.list_cached_mcp_tools()

-- Parameter helpers:
aidb.param(name TEXT, param_type TEXT, description TEXT)
aidb.params(VARIADIC ...)
```

---

## Pipeline Error Log

```sql
-- Get errors for a specific pipeline:
aidb.get_error_logs(pipeline_name TEXT)
    -- returns TABLE(id, source_id, part_ids, pipeline_step, step_operation,
    --               error_message, error_category, failed_at, last_seen_at, retry_count)

-- Re-queue failed items for retry:
aidb.requeue_pipeline_errors(pipeline_name TEXT)
```

---

## GUC Parameters

| Parameter | Type | Default | Restart Required | Description |
|---|---|---|---|---|
| `aidb.max_threads` | INTEGER | half of CPUs (min 1, max 1024) | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | BOOLEAN | true | No | Emit per-error WARNING to PostgreSQL log |

```sql
-- View current values:
SELECT name, setting FROM pg_settings WHERE name LIKE 'aidb.%';

-- Change (no restart needed for pipeline_error_warnings):
SET aidb.pipeline_error_warnings = false;

-- For max_threads, requires postgresql.conf + restart:
-- aidb.max_threads = 8
```
