# AIDB Named-Function & Deprecation Quick Reference

This file is a compact lookup table for functions that are commonly confused,
deprecated, or non-existent. Load it on demand when a user asks about function
names, return types, or gets "function does not exist" errors.

---

## Deprecated Functions — Always Use the Replacement

| Deprecated Name | Replacement | Notes |
|---|---|---|
| `aidb.decode_text(model, input, config)` | `aidb.generate_text(model, input, config json)` | Still works but emits a WARNING |
| `aidb.decode_text_batch(model, inputs, config)` | `aidb.generate_text_batch(model, inputs, config json)` | Still works but emits a WARNING |
| `aidb.get_hcp_models()` | `aidb.list_hcp_models()` | Still works but emits a WARNING |

---

## Functions That Do Not Exist (Never Generate These)

| Invented Name | Correct Replacement |
|---|---|
| `aidb.list_pipelines()` | `SELECT * FROM aidb.pipelines;` or `SELECT * FROM aidb.pipeline_metrics;` |
| `aidb.get_pipeline(name)` | `SELECT * FROM aidb.pipelines WHERE name = '...';` or `aidb.get_pipeline_metrics(name)` |
| `aidb_internal.pipeline_error_log_<id>` | `<source_schema>.pipeline_<name>_errors` via `aidb.get_error_logs(name)` |
| `aidb.create_model_server(...)` | `aidb.create_credential(...)` + `aidb.create_model(..., credential => ...)` |
| `aidb.get_tables()` | `aidb.get_entity_definitions(...)` or `aidb.semantic_kb_search(...)` |
| `aidb.get_columns()` | `aidb.get_column_definitions(...)` or `aidb.get_metadata(...)` |
| `smollm2_local` provider | Does not exist. Use `llama_instruct_local` or `llamacpp_generate` |
| `aidb.enable_memory_worker` | The GUC is `aidb.agent_memory_enable_worker` |
| `agents.role` column | Was dropped. Agent SQL role comes from its `purpose` |
| `tool_names` parameter | The `create_agent`/`update_agent` argument is `tools` |

---

## Type Gotchas

### `real[]` vs `vector`

Embedding functions (`encode_text`, `encode_text_batch`, `encode_text_query`, `encode_image`)
return `real[]`, not `vector`. Cast before using pgvector operators or storing into a `vector`
column:

```sql
SELECT aidb.encode_text('my_model', 'hello')::vector;
```

### `JSON` vs `JSONB` for standalone functions

These standalone functions take `options JSON` (not `jsonb`):
`chunk_text`, `summarize_text`, `parse_html`, `parse_pdf`, `perform_ocr`, `pdf_to_image`,
`generate_text`, `generate_text_batch`.

Every `aidb.*_config()` helper returns `jsonb`. Append `::json` when passing a helper to these:

```sql
-- WRONG: type error
SELECT * FROM aidb.chunk_text(body, aidb.chunk_text_config(200, 250));

-- CORRECT:
SELECT * FROM aidb.chunk_text(body, aidb.chunk_text_config(200, 250)::json);

-- ALSO CORRECT: a string literal is fine without a cast
SELECT * FROM aidb.chunk_text(body, '{"desired_length": 200}');
```

Pipeline `step_N_options` accept `jsonb`, so no cast is needed there.

### `inference_config` for `generate_text`

`generate_text` and `generate_text_batch` take `inference_config JSON`. Pass the helper with a cast:

```sql
SELECT aidb.generate_text('my_llm', 'prompt', aidb.inference_config(temperature => 0.2)::json);
```

### Agent Hub IDs are `TEXT`

`conversation_id` and `message_id` in `agent_converse`, `get_conversation`, `get_message` are
`TEXT`. There is no implicit `uuid → text` cast. If you have a `uuid` from a view, cast it:

```sql
SELECT * FROM aidb.agent_converse('my_agent', 'hi', conversation_id => my_uuid::text);
```

---

## Argument Order Errors

### Model functions — model name always first

```sql
-- WRONG (model not found, because 'hello' is not a model name):
SELECT aidb.encode_text('hello', 'my_model');

-- CORRECT:
SELECT aidb.encode_text('my_model', 'hello');
```

### `create_pipeline` — always use named arguments

The positional order has `step_1` as argument 3 (before `source_key_column`). Positional calls
place key/data columns incorrectly. Always call with named arguments:

```sql
SELECT * FROM aidb.create_pipeline(
    name               => 'my_pipeline',
    source             => 'public.docs',
    source_key_column  => 'id',
    source_data_column => 'body',
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(512, 64),
    step_2             => 'KnowledgeBase',
    step_2_options     => aidb.knowledge_base_config('my_model', 'Text')
);
```

### `create_model` — always use named arguments after `provider`

The 5th positional argument is `replace_credentials`, not `validate`. Always use named args:

```sql
SELECT aidb.create_model(
    name        => 'my_model',
    provider    => 'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-..."}'::jsonb
);
```

---

## `retrieve_text` / `retrieve_key` — First Argument is the Destination Table

Always pass the **schema-qualified destination table**, not the pipeline name:

```sql
-- CORRECT:
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_docs_kb', 'refund policy', topk => 5);

-- WARNING (works but warns):
SELECT key, value, distance
FROM aidb.retrieve_text('docs_kb', 'refund policy', 5);
-- → WARNING: Knowledge base looked up by pipeline name 'docs_kb'. Use 'public.pipeline_docs_kb'.
```

Find the destination table: `SELECT destination_schema, destination FROM aidb.pipelines WHERE name = 'docs_kb';`

---

## Semantic KB vs Document KB — When to Use Which

| Goal | Use |
|---|---|
| Search document / row **contents** (manuals, support tickets, product text) | `ChunkText → KnowledgeBase` pipeline + `aidb.retrieve_text(...)` |
| Search **schema metadata** (which tables and columns hold what) | `aidb.create_semantic_kb()` + `aidb.semantic_kb_search(...)` |

`aidb.create_semantic_kb()` does **not** embed document rows. Using it for document search will
return schema metadata results, not document content.

---

## Secrets Never Go in `config`

`aidb.create_model` rejects a `config` JSONB that contains `api_key` or `basic_auth` at any
depth. Use `credentials`:

```sql
-- WRONG (rejected):
SELECT aidb.create_model('m', 'openai_embeddings',
    config => '{"model": "text-embedding-3-small", "api_key": "sk-..."}'::jsonb);

-- CORRECT:
SELECT aidb.create_model('m', 'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-..."}'::jsonb);
```

---

## Embedding a Search Query Safely

Never call an embedding function inside `ORDER BY`. It can run once per row (N model round-trips
for N rows). Embed the query once in a CTE or scalar subquery:

```sql
-- WRONG (N model calls for N rows):
SELECT id FROM docs ORDER BY embedding <=> aidb.encode_text_query('m', 'q')::vector LIMIT 5;

-- CORRECT (1 model call):
WITH q AS (SELECT aidb.encode_text_query('my_model', 'refund policy')::vector AS v)
SELECT d.id, d.body
FROM docs d, q
ORDER BY d.embedding <=> q.v
LIMIT 5;
```

---

## `agent_converse` Error Convention

`agent_converse` **never raises**. It returns a row with an `error` column. Always check it:

```sql
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'What is the weather?');
-- If error IS NOT NULL, something went wrong (bad model, unknown tool, budget exceeded, etc.)
```

---

## Pipeline Name and Step Limits

- **Maximum pipeline name length: 46 characters** — creation fails silently with a clear error message if exceeded
- **Maximum steps per pipeline: 10** — there is no `step_11` parameter

---

## `aidb.semantic_kb_search` Parameter Names

The search text parameter is `query_text`, the count parameter is `top_k` (not `query` or `limit`):

```sql
SELECT * FROM aidb.semantic_kb_search(
    query_text => 'customer orders',
    kb_name    => 'schema_kb',
    top_k      => 10
);
```

The `"offset"` parameter is a reserved word; quote it in named calls:
```sql
SELECT * FROM aidb.get_metadata('schema_kb', 'email', top_k => 10, "offset" => 20);
```
