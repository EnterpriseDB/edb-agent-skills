# AIDB Knowledge Bases Reference

Signature reference for vector knowledge bases, retrieval, volumes, hybrid search, and semantic knowledge bases. AIDB 7.6.0.

Two distinct things share the name "knowledge base":

| | Vector knowledge base | Semantic knowledge base |
|---|---|---|
| Embeds | Your **data** (rows, documents, images) | Your **schema** (table/view/column metadata) |
| Created by | `aidb.create_pipeline()` with a `KnowledgeBase` step | `aidb.create_semantic_kb()` |
| Searched with | `aidb.retrieve_text()`, `aidb.retrieve_key()` | `aidb.semantic_kb_search()` and friends |
| Used for | RAG, semantic search over content | Schema discovery, agent tools, text-to-SQL |

Always confirm which one the user means before acting.

## Discovery queries — run these before advising

```sql
-- Vector KBs: name is 'schema.vector_table' and is what retrieve_* takes
SELECT name, vector_schema, vector_table, model_name, distance_operator,
       distance_operator_sql, pipeline_names
FROM aidb.knowledge_bases ORDER BY name;

SELECT name, pipelines, embeddings, status FROM aidb.knowledge_base_metrics ORDER BY name;

-- Volumes usable as pipeline sources
SELECT schema, volume, storage_location, path FROM aidb.volumes ORDER BY schema, volume;

-- Semantic KBs and reusable parameterized queries
SELECT * FROM aidb.list_semantic_kbs();
SELECT name, description, param_count FROM aidb.get_semantic_aliases() ORDER BY name;
```

## Vector knowledge bases

A KB is identified by `schema.vector_table`.

### `aidb.knowledge_bases` (alias `aidb.kbs`)

`id` (integer), `name` (text), `vector_schema`, `vector_table`, `model_name`, `distance_operator` (DistanceOperator), `distance_operator_sql` (text — e.g. `<->`, `<=>`), `vector_data_column`, `vector_key_column`, `vector_index` (jsonb), `pipeline_ids` (integer[]), `pipeline_names` (text[]).

### `aidb.knowledge_base_metrics` (alias `aidb.kbm`)

`name` (text — `schema.vector_table`), `pipelines` (integer), `embeddings` (bigint — vector table row count), `status` (text — worst status across attached pipelines).

Legacy aliases `aidb.knowledge_base_stats` and `aidb.kbstat` still resolve to this view.

### `aidb.retrieve_key`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `knowledge_base_name` | TEXT | required | `schema.vector_table` |
| `query` | TEXT or BYTEA | required | Must match the embedding model's data format |
| `topk` | INTEGER | `1` | Must be positive; `0` or negative raises an error |
| `deduplicate` | BOOLEAN | `true` | Return each source record once even if several parts match |

Returns `key` (text), `distance` (double precision), `part_ids` (bigint[]), `pipeline_name` (text).

### `aidb.retrieve_text`

Same parameters as `aidb.retrieve_key`. Joins the embeddings table back to the source table.

Returns `key` (text), `value` (text — **NULL when the source content is non-text**, e.g. PDF or Image; other columns still populate), `distance` (double precision), `part_ids` (bigint[]), `pipeline_name` (text), `intermediate_steps` (jsonb).

`intermediate_steps` is `[]` unless some step has intermediate storage enabled. Each entry:

```json
{"value": "...", "operation": "ChunkText", "step_order": 1, "destination_table": "public.pipeline_my_pipeline_step_1"}
```

Only steps *before* the last one are considered, so a last-step `intermediate_destination` never appears here.

```sql
-- Typical RAG retrieval
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_docs_rag', 'how do I rotate credentials?', topk => 5);

-- Image KB: pass BYTEA, and read `key` (value is NULL for non-text sources)
SELECT key, distance
FROM aidb.retrieve_key('public.pipeline_images', (SELECT img FROM samples WHERE id = 1), topk => 3);
```

Distance is lower-is-closer for `L2`/`Cosine`; interpret it with the KB's `distance_operator`.

### `aidb.delete_knowledge_base`

`aidb.delete_knowledge_base(knowledge_base_name TEXT)` — **cascades**: deletes every attached pipeline, drops the vector table, removes the registry entry. Does not delete source tables.

To detach one pipeline from a multi-pipeline KB while keeping the rest, use `aidb.delete_pipeline()` instead. Deleting the last attached pipeline also drops the KB.

## Hybrid search helpers

Hybrid search combines vector similarity with Postgres full-text search; AIDB supplies the building blocks rather than a single hybrid function.

| Function | Signature | Purpose |
|---|---|---|
| `aidb.kb_query_encode` | `(knowledge_base_name TEXT, query TEXT)` → `real[]` | Encode a query with the KB's own configured model — no need to look up the model name |
| `aidb.rerank_text` | `(model_name TEXT, query TEXT, input TEXT[])` | Post-retrieval reranking; see `models.md` |

Use `aidb.knowledge_bases.distance_operator_sql`, `vector_schema`, `vector_table`, `vector_data_column`, and `vector_key_column` to build custom queries without hardcoding paths.

Pattern — reciprocal rank fusion of vector and full-text results (adapt table/column names to the KB row you read from `aidb.knowledge_bases`; the `<=>` operator here assumes a `Cosine` KB):

```sql
WITH q AS (
    SELECT aidb.kb_query_encode('public.pipeline_docs_rag', 'credential rotation')::vector AS v,
           plainto_tsquery('english', 'credential rotation')                             AS ts
),
vec AS (
    SELECT e.source_id,
           row_number() OVER (ORDER BY e.value <=> (SELECT v FROM q)) AS rank
    FROM public.pipeline_docs_rag e
    ORDER BY e.value <=> (SELECT v FROM q)
    LIMIT 50
),
fts AS (
    SELECT d.id::text AS source_id,
           row_number() OVER (ORDER BY ts_rank_cd(to_tsvector('english', d.body),
                                                  (SELECT ts FROM q)) DESC) AS rank
    FROM public.documents d
    WHERE to_tsvector('english', d.body) @@ (SELECT ts FROM q)
    LIMIT 50
)
SELECT COALESCE(vec.source_id, fts.source_id) AS source_id,
       COALESCE(1.0 / (60 + vec.rank), 0) + COALESCE(1.0 / (60 + fts.rank), 0) AS score
FROM vec FULL OUTER JOIN fts USING (source_id)
ORDER BY score DESC
LIMIT 10;
```

Then optionally rerank the shortlist with `aidb.rerank_text()` using a reranking model that exists on this installation.

## Volumes

A volume exposes a PGFS storage location as a foreign table usable as a pipeline source. Set up the PGFS storage location first (`pgfs.create_storage_location(...)` — a separate extension).

| Function / view | Signature | Notes |
|---|---|---|
| `aidb.create_volume` | `(name TEXT, server_name TEXT, path TEXT, data_format TEXT)` | `name` must be a valid unquoted Postgres identifier. `data_format` is `Text`, `Image`, or `Pdf` |
| `aidb.volumes` | view | `schema`, `volume`, `storage_location`, `path` |
| `aidb.delete_volume` | `(volume_name TEXT)` | Deleting the underlying PGFS storage location deletes all volumes built on it |

`path` is canonicalized to a single trailing slash — `foo`, `/foo`, `foo/`, `/foo/` all store as `foo/`; `NULL` or `/` store as `/`.

Outbound access to object storage and to model endpoints is subject to the `aidb.egress_allowlist` GUC.

## Semantic knowledge bases

Indexes tables, views, and columns — each with its definition and any `COMMENT ON` text. Every entry has an `entity_type` of `Table`, `View`, or `Column`. A well-commented schema produces a substantially better semantic KB, because comment text is embedded alongside the structural definition.

A semantic KB uses a **single embedding model** for all its metadata and for any member aliases, so all vectors stay comparable.

### Management

| Function | Purpose |
|---|---|
| `aidb.create_semantic_kb(name, model, schemas, auto_processing, bypass_triggers, vector_index)` | Create over one or more schemas; embeds and crawls immediately. Returns the KB name. `model` is always required |
| `aidb.list_semantic_kbs()` | Every KB with model, schemas, processing mode |
| `aidb.refresh_semantic_kb(name)` | Re-crawl from scratch |
| `aidb.update_semantic_kb_auto_processing(name, auto_processing)` | Change refresh behaviour |
| `aidb.semantic_kb_stats(kb_name)` | Entity counts (`total`, `tables`, `views`, `columns`) and `pending` schema changes |
| `aidb.delete_semantic_kb(name)` | Delete the KB and its indexed metadata |

**Single-KB shortcut (7.6.0).** When exactly one semantic KB exists, `name`/`kb_name` may be omitted from every function and resolves to that KB. Omitting the name at creation registers it as `default_semkb`. Once a second KB exists, name-omitting calls fail with an ambiguity error.

**Auto-processing:** `Disabled` (default — call `refresh_semantic_kb()` yourself), `Live` (DDL triggers re-crawl affected relations), `Background` (a worker drains queued DDL events via an internal `SemanticKB` pipeline step you never manage directly).

### Search

`aidb.semantic_kb_search()` is the composite entry point — it runs the per-source ranked searches (schema metadata and semantic aliases) and fuses them with Reciprocal Rank Fusion.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query_text` | text | required | Natural-language query |
| `kb_name` | text | NULL | Omit to resolve the single KB |
| `top_k` | int | `10` | Ranked rows to return |
| `sources` | text[] | NULL | Active: `schema`, `alias`. (`history`, `relationship` reserved.) Omit for all |
| `entity_types` | text[] | NULL | Active: `Table`, `View`, `Column`, `Alias`. Omit for all |
| `rrf_k` | int | `60` | Rank-fusion damping constant |
| `min_similarity` | double precision | NULL | Similarity floor |

Returns `source_type` (`schema` or `alias`), `entity_type`, `schema_name`, `relation_name`, `column_name` (empty for table/view matches), `object_ref`, `definition`, `comment`, `score` (fused RRF), `rank` (1-based), `components` (jsonb — per-source scores).

Four narrower functions search schema metadata only. All share `kb_name`, `query_text`, `min_similarity`, `top_k`, `offset`, and return a cosine `similarity` (higher is closer).

| Function | Matches | Returns |
|---|---|---|
| `aidb.get_metadata()` | Columns, tables and views | `schema_name`, `relation_name`, `column_name`, `entity_type`, `definition`, `comment`, `definition_vector`, `similarity`. `top_k` default `10`, `offset` default `0` |
| `aidb.get_entity_definitions()` | Tables and views only | `schema_name`, `relation_name`, `entity_type`, `definition`, `comment`, `similarity`. Extra param `entity_types` defaults to `ARRAY['Table','View']` |
| `aidb.get_column_definitions()` | Columns | A single `definition` column — leanest grounding for SQL generation |
| `aidb.search_by_comment()` | `COMMENT ON` text only | `schema_name`, `relation_name`, `column_name`, `entity_type`, `definition`, `comment`, `similarity` |

Tuning: lower `min_similarity` (e.g. `0.3`) when a query returns nothing, raise it when results are too broad; page with `top_k`/`offset`; narrow with `entity_types`.

### Semantic aliases

A named, parameterized SQL query paired with a natural-language description that is embedded and therefore findable by meaning.

An alias belongs to **every** semantic KB that owns the schema its SQL reads — AIDB infers the owning KB(s) from the query and embeds the description once per KB with that KB's model. Creation never fails on ambiguity. A schema owned by no KB leaves the alias unembedded until a covering KB appears.

> Changed in 7.6.0: alias functions no longer take a `model` argument.

| Function | Parameters |
|---|---|
| `aidb.create_semantic_alias` | `name` text, `description` text, `query_text` text, `params` jsonb (NULL), `kb_name` text (NULL — narrows to one KB) |
| `aidb.search_semantic_aliases` | `query_text` text, `min_similarity` double precision (NULL), `top_k` int (`10`), `offset` int (`0`), `kb_name` text (NULL). Returns `name`, `query_text`, `similarity` |
| `aidb.execute_semantic_alias` | `alias_name` text, `args` jsonb (NULL), `execute_role` text (NULL). Returns a set of `result` JSONB rows |
| `aidb.get_semantic_aliases()` | Lists `name`, `description`, `query_text`, `param_count` |
| `aidb.get_semantic_alias(alias_name)` | One alias: `name`, `description`, `query_text`, `params` |
| `aidb.update_semantic_alias` | `name`, `description`, `query_text`, `params`, `kb_name` |
| `aidb.delete_semantic_alias(name)` | Delete by name |

`query_text` must be a **single read-only `SELECT`**, using `${name}` placeholders. This is validated at creation and re-validated at execution, so an alias can never perform writes. `execute_role` runs the query as a least-privilege role (needs the appropriate `SET ROLE` grants).

Each `params` entry: `name` (required, matches a `${name}` placeholder), `param_type` (required, a Postgres type), `description` (optional), `enum_values` (optional, constrains to a set).

Build `params` with the dedicated helpers rather than hand-written JSON:

| Helper | Signature |
|---|---|
| `aidb.alias_param` | `(name TEXT, param_type TEXT, description TEXT = NULL, enum_values TEXT[] = NULL)` → JSONB. NULL fields are stripped |
| `aidb.alias_params` | `(VARIADIC params JSONB[])` → JSONB |

These are distinct from `aidb.tool_param()`/`aidb.tool_params()`, which build parameters for SQL *tools* and use a `type` key rather than `param_type`.

`update_semantic_alias` reconciles embeddings: changing `query_text` or `kb_name` re-infers owning KBs, adding and dropping embeddings; changing `description` re-embeds in every KB it already belongs to. Deleting a KB removes only that KB's embedding — the alias and its other embeddings survive, and a later KB covering the same schema adopts it.

**Alias functions are deliberately not exposed as agent tools.** An agent can discover and read schema, but cannot create or execute aliases itself.

```sql
-- Create a semantic KB over two schemas, then find the right table by meaning
SELECT aidb.create_semantic_kb(
    name  => 'app_schema_kb',
    model => 'bge-m3-f16',
    schemas => ARRAY['public', 'sales']);

SELECT relation_name, column_name, entity_type, score
FROM aidb.semantic_kb_search('where are customer email addresses stored?', top_k => 5);

-- A reusable parameterized query, findable by description
SELECT aidb.create_semantic_alias(
    name        => 'orders_by_status',
    description => 'List orders filtered by their current status',
    query_text  => 'SELECT id, total FROM sales.orders WHERE status = ${status}',
    params      => aidb.alias_params(
                       aidb.alias_param('status', 'text', 'Order status',
                                        ARRAY['open','shipped','cancelled'])));

SELECT * FROM aidb.execute_semantic_alias('orders_by_status', '{"status": "open"}'::jsonb);
```

## Common errors

| Symptom | Cause / fix |
|---|---|
| `retrieve_text` returns rows with NULL `value` | Source content is binary (PDF/Image) — use `key` and join the source yourself |
| `topk` error | Must be positive |
| Empty results | Pipeline never ran (`aidb.pipeline_metrics`), or the query language/model mismatches the KB's model |
| Ambiguity error from a semantic KB function | More than one semantic KB exists — pass `kb_name` explicitly |
| Deleting a KB removed pipelines too | `delete_knowledge_base` cascades by design; use `delete_pipeline` to detach one pipeline |
