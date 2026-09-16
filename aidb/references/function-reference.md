# AIDB Function Reference

All functions live in the `aidb` schema (Agent Memory functions live in `aidb_memory`). Source: `static-sql/config_helpers.sql`, `static-sql/semantic_knowledge_base.sql`, `static-sql/aidb_model_registry.sql`, `static-sql/pipeline_common.sql`, `static-sql/agent-hub.sql`, `static-sql/tools-hub.sql`, `static-sql/purpose-registry.sql`, `static-sql/aidb_memory.sql`, `src/api/agent_hub.rs`, `src/api/tools_hub.rs`, `src/api/pg_catalog_tools.rs`, `src/api/purpose_registry.rs`, `src/memory/sql_memory_facade.rs`, `src/model_accessors.rs`.

---

## Pipeline Management

```sql
aidb.create_pipeline(
    name                TEXT,
    source              TEXT,
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

aidb.update_pipeline(
    name            TEXT,
    auto_processing TEXT     DEFAULT NULL,
    destination     TEXT     DEFAULT NULL,
    batch_size      INTEGER  DEFAULT NULL,
    sync_interval   INTERVAL DEFAULT NULL
)

aidb.run_pipeline(
    name        TEXT,
    force_sync  BOOLEAN DEFAULT FALSE
)

aidb.delete_pipeline(name TEXT, cascade BOOLEAN DEFAULT FALSE)

aidb.list_pipelines()  -- returns all pipeline metadata

aidb.get_pipeline(name TEXT)
```

**Constraints:**
- Max pipeline name length: 46 characters
- Max steps per pipeline: 10
- Destination table must not exist at creation time

---

## Model Management

```sql
aidb.create_model(
    name                    TEXT,
    provider                TEXT,
    config                  JSONB   DEFAULT NULL,
    credentials             JSONB   DEFAULT NULL,
    credentials_env         TEXT    DEFAULT NULL,  -- read credentials from this allow-listed env var instead
    credentials_k8s_secret  TEXT    DEFAULT NULL,  -- read credentials from this allow-listed mounted secret path instead
    validate                BOOLEAN DEFAULT NULL   -- test-connect the model at registration time
)

aidb.list_models()

aidb.get_model(name TEXT)

aidb.create_model_server(
    name        TEXT,
    provider    TEXT,
    config      JSONB DEFAULT NULL,
    credentials JSONB DEFAULT NULL
)

aidb.remove_cached_model(name TEXT)

aidb.get_model_description(name TEXT)

aidb.get_adapter_embedding_dimensions(name TEXT) -- returns INTEGER
```

---

## Standalone AI Operations

### Embeddings & Encoding

```sql
aidb.encode_text(input TEXT, model TEXT)                    -- returns VECTOR
aidb.encode_text_batch(inputs TEXT[], model TEXT)           -- returns VECTOR[]
aidb.encode_text_query(input TEXT, model TEXT)              -- returns VECTOR (query-side for bi-encoders)
aidb.encode_image(image BYTEA, model TEXT)                  -- returns VECTOR
aidb.rerank_text(query TEXT, candidates TEXT[], model TEXT) -- returns TABLE(index INT, score FLOAT)
```

### Text Generation

```sql
aidb.generate_text(prompt TEXT, model TEXT, options JSONB DEFAULT '{}')
    -- returns TEXT — preferred over the deprecated aidb.decode_text

aidb.generate_text_batch(prompts TEXT[], model TEXT, options JSONB DEFAULT '{}')
    -- returns TEXT[] — preferred over the deprecated aidb.decode_text_batch
```

**Deprecated:** `aidb.decode_text` / `aidb.decode_text_batch` still work but emit a runtime warning ("deprecated and will be removed in a future version") pointing at the two functions above. Always document `generate_text`/`generate_text_batch` as the current names.

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

These return JSONB used as `step_N_options` in `create_pipeline`.

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
    method                TEXT,
    allow_partial_parsing BOOLEAN DEFAULT NULL
) -- returns JSONB

aidb.ocr_config(model TEXT) -- returns JSONB

aidb.knowledge_base_config(
    model             TEXT,
    data_format       TEXT,    -- 'Text' | 'Image'
    distance_operator TEXT     DEFAULT NULL,
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

-- Search functions (all return similarity-ranked rows)
aidb.get_metadata(
    kb_name        TEXT,
    query          TEXT,
    min_similarity NUMERIC,
    limit          INTEGER,
    offset         INTEGER
) -- returns TABLE(schema_name, relation_name, column_name, entity_type, definition, comment, similarity)

aidb.get_tables(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)

aidb.get_columns(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)

aidb.get_column_definitions(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)

aidb.get_entity_definitions(kb_name TEXT, query TEXT, min_similarity NUMERIC, types TEXT[], limit INTEGER, offset INTEGER)

aidb.search_by_comment(kb_name TEXT, query TEXT, min_similarity NUMERIC, limit INTEGER, offset INTEGER)
```

**Similarity threshold guidance:**
- 0.9+ — near-exact matches only
- 0.8 — good default for semantic search
- 0.5–0.7 — broad exploration

### Semantic KB Relationships & Join Routing (newer, evolving)

Not covered by `docs/semantic-knowledge-base.md` — sourced from doc-comments under `src/pipeline_common/semantic_kb/*.rs`, so verify current behavior against source before documenting exact signatures. Adds a join-graph layer on top of Semantic KB: relationship management (`aidb.add_relationship`, `add_relationship_as_agent`, `list_relationships`, `delete_relationship`, `resolve_relationship`), join routing (`aidb.suggest_joins`, `aidb.find_join_path`, `aidb.semantic_kb_subgraph`), comment authoring (`aidb.add_comment_to_object`, `remove_comment`, `list_proposed_comments`, `resolve_object_comment`), and query-history mining from `pg_stat_statements` (`aidb.import_query_history`, `list_query_history`). **Important:** a relationship is only ever turned into executable SQL by the join-routing functions once it has been explicitly approved/curated — never present an unreviewed or candidate relationship as safe to route through.

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

-- Parameter builders
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

aidb.is_volume(schema TEXT, identifier TEXT) -- returns BOOLEAN
```

---

## Agent Hub

Source: `static-sql/agent-hub.sql`, `src/api/agent_hub.rs`. Full design writeup: `agent_docs/agent_hub.md`.

```sql
aidb.agent_converse(
    agent_name       TEXT,
    prompt           TEXT,
    conversation_id  UUID    DEFAULT NULL,
    read_only        BOOLEAN DEFAULT NULL,
    output_type      JSONB   DEFAULT NULL,  -- via aidb.output_type()/output_field()
    debug            BOOLEAN DEFAULT NULL
) -- returns a row with message/conversation_id/error columns; NEVER raises on a failed turn

aidb.create_agent(...)   -- instructions, model, delegates, tools, output_type, budgets, purpose
aidb.update_agent(...)
aidb.delete_agent(name TEXT, force BOOLEAN DEFAULT NULL)

aidb.start_agent_session(agent_name TEXT)
aidb.get_conversation(id UUID)
aidb.get_message(id UUID)

aidb.sleep(seconds NUMERIC)  -- backs the always-available 'sleep' native tool

-- Structured output schema builders
aidb.output_field(name TEXT, field_type TEXT, description TEXT DEFAULT NULL) -- returns JSONB
aidb.output_type(VARIADIC fields JSONB[])                                    -- returns JSONB
```

Caller-facing views: `aidb.agent_tasks`, `aidb.conversations`, `aidb.conversation_log`.

**Error convention:** `agent_converse` and the agent management functions never raise — a failed turn is reported via an `error` column so it doesn't abort the caller's transaction. This is different from Tools Hub functions below, which raise normally.

---

## Tools Hub

Source: `static-sql/tools-hub.sql`, `src/api/tools_hub.rs`, catalog tools in `src/api/pg_catalog_tools.rs`.

```sql
aidb.run_tool(name TEXT, arguments JSONB DEFAULT '{}') -- returns JSONB; the single dispatch entry point for all tool calls

aidb.create_sql_tool(
    name              TEXT,
    description       TEXT,
    sql_statement     TEXT,   -- may use ${param_name} placeholders
    params            JSONB,  -- via aidb.tool_params()/tool_param()
    read_only         BOOLEAN DEFAULT TRUE,
    return_type_hint  JSONB   DEFAULT NULL
)

aidb.delete_tool(name TEXT)
aidb.get_mcp_tools()
aidb.run_sql_query(query TEXT)
aidb.explain_query(sql TEXT, analyze BOOLEAN DEFAULT NULL)
aidb.list_native_tools()       -- backs the aidb.tools view
aidb.list_cached_mcp_tools()   -- backs the aidb.tools view

-- MCP server registration
aidb.import_mcp_tools(
    name         TEXT,
    url          TEXT,
    transport    TEXT DEFAULT 'streamable_http',  -- 'streamable_http' | 'sse'
    headers      JSONB DEFAULT NULL,
    tool_filter  TEXT[] DEFAULT NULL,
    headers_env  TEXT DEFAULT NULL  -- read headers from this env var instead of `headers`
)
aidb.refresh_mcp_tools(name TEXT) -- returns INT; re-fetches the tool cache for one server

-- Catalog discovery tools (each capped in row count — built for a model's context window, not a person)
aidb.catalog_list_schemas()
aidb.catalog_list_objects(schema TEXT)
aidb.catalog_get_object_details(schema TEXT, object_name TEXT)
aidb.catalog_list_relations(schema TEXT)
aidb.catalog_list_sequences(schema TEXT)
aidb.catalog_list_extensions()
aidb.analyze_db_health()

-- Parameter declaration helpers
aidb.param(name TEXT, param_type TEXT, description TEXT DEFAULT NULL, enum_values TEXT[] DEFAULT NULL) -- returns JSONB
aidb.params(VARIADIC params JSONB[])                                                                     -- returns JSONB
aidb.tool_param(name TEXT, type TEXT, description TEXT)  -- description is required, unlike aidb.param
aidb.tool_params(VARIADIC params JSONB[])
```

`aidb.tools` is the single governed registry (a view) unioning native tools, `aidb.sql_tool_registry` rows, and cached MCP tools — this is what an agent's `tools` list is validated/resolved against.

---

## Agent Memory

Schema: `aidb_memory`. Source: `src/memory/sql_memory_facade.rs`. Full design writeup: `agent_docs/agent_memory.md`. Only a `mock` provider is implemented today.

```sql
aidb_memory.init(name TEXT, provider TEXT, config JSONB) -- returns BIGINT; registers a namespace

aidb_memory.add(
    namespace         TEXT,
    actor_id          TEXT,
    turns             JSONB,
    session_id        TEXT DEFAULT NULL,
    agent_id          TEXT DEFAULT NULL,
    idempotency_key   TEXT DEFAULT NULL
) -- returns UUID; captures turns into memory

aidb_memory.search(
    namespace   TEXT,
    actor_id    TEXT,
    query       TEXT,
    limit       INTEGER DEFAULT 10,
    session_id  TEXT DEFAULT NULL,
    agent_id    TEXT DEFAULT NULL
) -- returns TABLE(id UUID, content TEXT, score REAL)

aidb_memory.delete(
    namespace   TEXT,
    actor_id    TEXT,
    selector    JSONB,
    reason      TEXT,
    session_id  TEXT DEFAULT NULL,
    agent_id    TEXT DEFAULT NULL
) -- returns BIGINT (count deleted)

-- Session tier: durable, resumable conversation state with automatic compaction.
-- Never exposed as a tool or over MCP — session boundaries are the calling harness's
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
) -- returns TABLE(items JSONB, overflow_chars BIGINT, compacted_entries INT, warnings TEXT[])

aidb_memory.session_end(...) -- closes a session, storing a deterministic handoff for the next session; idempotent
```

Integration with Agent Hub: GUCs `aidb.agent_session_source` (`action_log` | `memory`) and `aidb.agent_memory_namespace`.

---

## Governance: Purpose Registry

Source: `static-sql/purpose-registry.sql`, `src/api/purpose_registry.rs`.

```sql
aidb.create_purpose(name TEXT, role TEXT, description TEXT DEFAULT NULL)
    -- errors if name already exists (unless reviving a soft-deleted one)

aidb.update_purpose(name TEXT, role TEXT DEFAULT NULL, description TEXT DEFAULT NULL)
    -- only non-NULL arguments are changed

aidb.delete_purpose(name TEXT)
    -- soft delete: sets deleted_at, never physically removes the row (agents may still reference the name)
```

`aidb.purpose_registry` is a read-only view over the underlying registry (`name`, `role`, `description`, `created_at`, `updated_at`, `deleted_at`). A `purpose` can be assigned to an agent (`create_agent`/`update_agent`'s `purpose` argument); role switches performed under a purpose are recorded as audited decisions.

---

## GUC Parameters

The most commonly relevant parameters — the full surface is larger and spans several subsystems (egress/TLS/credential-source policy, OTel telemetry, MCP endpoint supervision, model download/cache behavior); confirm the complete, current list from source rather than assuming this table is exhaustive.

| Parameter | Type | Default | Restart Required | Description |
|---|---|---|---|---|
| `aidb.max_threads` | INTEGER | half of CPUs (min 1, max 1024) | Yes | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | BOOLEAN | true | No | Emit per-error WARNING to PostgreSQL log |
| `aidb.enable_memory_worker` | BOOLEAN | — | No | Enables the Agent Memory background dispatcher |
| `aidb.agent_session_source` | TEXT | `action_log` | No | `action_log` or `memory` — where Agent Hub reads session history from |
| `aidb.agent_memory_namespace` | TEXT | `pg_agents` | No | Default Agent Memory namespace |
| `aidb.egress_allowlist` | TEXT | — | No | Allow-listed hosts for outbound network calls (model providers, MCP servers, etc.) |
| `aidb.allow_insecure_egress` / `aidb.allow_insecure_tls` | BOOLEAN | false | No | Explicit opt-outs required to bypass egress/TLS restrictions |
| `aidb.env_var_allowed_prefix` / `aidb.k8s_secret_allowed_path_prefix` | TEXT | — | No | Allow-listed prefixes for `credentials_env` / `credentials_k8s_secret` |
| `aidb.otel_client` | TEXT | `noop` | No | `noop` \| `database` \| `stdout` \| `log` \| `grpc` — telemetry export mode |
| `edb.endpoints_mcp_enabled` | BOOLEAN | — | No | Enables the standalone MCP endpoint server (`edb-endpoints`) |

Errors always persist to the per-pipeline error log table regardless of `aidb.pipeline_error_warnings`.
