# AIDB Standalone SQL Functions Reference

Signature reference for transformation functions callable directly in a query, with no pipeline. AIDB 7.6.0.

Every pipeline step operation is also available as a standalone function. Inference functions (`encode_text`, `generate_text`, `rerank_text`) are in `models.md`.

## Installation and runtime check

Run this first on an unfamiliar database — it tells you whether AIDB is installed, at what version, and how it is configured.

```sql
-- Extension presence and version (pgvector and pgfs matter for KBs and volumes)
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('aidb', 'vector', 'pgfs', 'vchord') ORDER BY extname;

-- All AIDB GUCs. An empty result means the library is not loaded
-- (add `aidb` to shared_preload_libraries and restart).
SELECT name, setting, context, short_desc FROM pg_settings
WHERE name LIKE 'aidb.%' ORDER BY name;

-- Does the current role have AIDB access?
SELECT current_user, pg_has_role(current_user, 'aidb_users', 'MEMBER') AS in_aidb_users;
```

Runtime GUCs and what they gate:

| GUC | Effect |
|---|---|
| `aidb.max_threads` | Model thread pool. **Changing it requires a database restart** |
| `aidb.max_io_threads` | I/O thread pool |
| `aidb.egress_allowlist` | Outbound host/CIDR allowlist covering model endpoints, MCP servers, and model downloads. A blocked host looks like a network error |
| `aidb.env_var_allowed_prefix` | Prefix (default `AIDB_`) that `credentials_env` and `headers_env` names must start with |
| `aidb.download_log_level`, `aidb.download_max_attempts` | Model download logging/retries |
| `aidb.enable_llamacpp_logs` | llama.cpp logging |
| `aidb.pipeline_error_warnings` | Also emit a `WARNING` for each logged pipeline error (default on) |

Installation summary: add `aidb` to `shared_preload_libraries`, restart, then `CREATE EXTENSION aidb CASCADE;`. Supported on PostgreSQL 14–18 (community, EDB Postgres Advanced Server, EDB Postgres Extended). Access is managed through the `aidb_users` role.

## `aidb.chunk_text`

`aidb.chunk_text(input TEXT, options JSONB)` → `TABLE(part_id bigint, chunk text)`

`part_id` is a zero-based segment index.

| Option key | Type | Default | Description |
|---|---|---|---|
| `desired_length` | integer | required | Target segment size. A strict upper limit if `max_length` is omitted |
| `max_length` | integer | NULL | Upper bound. Chunks extend past `desired_length` only to preserve semantic boundaries |
| `overlap_length` | integer | `0` | Content repeated between consecutive chunks |
| `strategy` | text | `'chars'` | `'chars'` or `'words'` — sets the unit for all three lengths above |

Build with `aidb.chunk_text_config()` or pass raw JSONB.

## `aidb.parse_html`

`aidb.parse_html(html TEXT, options JSONB)` → `TEXT`

| Option key | Type | Default | Description |
|---|---|---|---|
| `method` | text | `'StructuredPlaintext'` | `'StructuredPlaintext'` or `'StructuredMarkdown'` (retains headers and lists) |

## `aidb.parse_pdf`

`aidb.parse_pdf(bytes BYTEA, options JSONB)` → `TABLE(part_id bigint, text text)`

One row per page; `part_id` is the zero-based page index.

| Option key | Type | Default | Description |
|---|---|---|---|
| `method` | text | `'Structured'` | Spec-based text block extraction |
| `allow_partial_parsing` | boolean | `true` | Continue past unparseable pages, returning as much as possible |

## `aidb.pdf_to_image`

`aidb.pdf_to_image(bytes BYTEA, options JSON = '{}')` → `TABLE(part_id bigint, image bytea)`

One row per rendered page; `part_id` is the zero-based page index. Takes the same options as the `PdfToImage` pipeline step: `dpi` (default 300), `first_page`, `last_page`, `max_pages`, `render_annotations` (default true), `format` (default `{"type":"png"}`, or `{"type":"jpeg"}`).

Use for scanned or image-heavy PDFs with no reliable text layer, then feed `aidb.perform_ocr()`. For digitally produced PDFs, `aidb.parse_pdf()` is faster and needs no model.

## `aidb.perform_ocr`

`aidb.perform_ocr(input BYTEA, options JSONB)` → `TABLE(part_id bigint, text text)`

`part_id` is a text block index — one image may yield several rows if the provider returns multiple segments.

| Option key | Type | Description |
|---|---|---|
| `model` | text | A registered OCR-capable model (e.g. `nim_paddle_ocr` or `llamacpp_ocr` provider) |

`options` selects the model only; it carries no inference settings.

## `aidb.summarize_text`

`aidb.summarize_text(input TEXT, options JSONB)` → `TEXT`

| Option key | Type | Default | Description |
|---|---|---|---|
| `model` | text | required | A registered model supporting `generate_text` |
| `prompt` | text | standard prompt | Custom instruction guiding style |
| `chunk_config` | JSONB | NULL | Chunking applied before summarizing when input exceeds the context window. Same keys as `chunk_text` options |
| `strategy` | text | `'append'` | `'append'` (summarize each chunk independently, concatenate) or `'reduce'` (iteratively summarize) |
| `reduction_factor` | integer | `3` | With `'reduce'`: how aggressively each pass reduces |
| `inference_config` | JSONB | NULL | From `aidb.inference_config()` — the one call-time override path for summarization |

## `aidb.summarize_text_aggregate`

`aidb.summarize_text_aggregate(input TEXT, options JSON)` → `TEXT`

Aggregate form. Accumulates text from every row in a group, then summarizes the combined result — one summary per `GROUP BY` group. Empty and NULL rows are skipped.

**`options` is required here**, unlike the scalar form, and must contain at least `model`. Cast `aidb.summarize_text_config(...)` to `::json`. Ordering within a group is controlled with `ORDER BY` inside the aggregate call.

## `aidb.summarize_text_config`

Builds the `options` object for both summarize functions.

`model` (TEXT, required), `chunk_config` (JSONB), `prompt` (TEXT), `strategy` (TEXT), `reduction_factor` (INTEGER), `inference_config` (JSONB).

## Query-side embedding

Not covered by the docs' reference pages, but present in the extension and exposed as native agent tools:

| Function | Signature | Notes |
|---|---|---|
| `aidb.encode_text_query` | `(model_name TEXT, input TEXT)` → `real[]` | Embedding tuned for **querying** rather than indexing. Some providers use a different input-type hint for queries vs. documents (`input_type_query` in the model config) |
| `aidb.encode_text_query_batch` | `(model_name TEXT, input TEXT[])` → `SETOF real[]` | Batch form, input order preserved |

When embedding a query against a specific knowledge base, prefer `aidb.kb_query_encode(kb_name, query)` — it resolves the KB's own model for you. See `knowledge-bases.md`.

## Worked examples

```sql
-- Chunk a column on the fly, no pipeline involved
SELECT d.id, c.part_id, c.chunk
FROM documents d,
     LATERAL aidb.chunk_text(d.body, aidb.chunk_text_config(desired_length => 300)) c
LIMIT 20;

-- Summarize per group
SELECT customer_id,
       aidb.summarize_text_aggregate(note ORDER BY created_at,
           aidb.summarize_text_config(model => 'llama-3.2-1b-instruct-Q8_0')::json) AS summary
FROM support_notes GROUP BY customer_id;

-- Generate with a per-call override
SELECT aidb.generate_text('llama-3.2-1b-instruct-Q8_0', 'Explain vector search in one sentence.',
         aidb.inference_config(temperature => 0.0, max_tokens => 120)::json);
```

Scale note: these run inference per row. Try them on a handful of rows (`LIMIT`) before running over a whole table, and use a pipeline when the results should be stored and kept up to date.

## Defaults quick reference

| Function | Parameter | SQL default | Runtime default |
|---|---|---|---|
| `summarize_text` | `options` | `'{}'` | Must include `model` |
| `summarize_text_aggregate` | `input`, `options` | required | — |
| `summarize_text_config` | `model` | required | — |
| `summarize_text_config` | `chunk_config` | NULL | No chunking |
| `summarize_text_config` | `prompt` | NULL | Standard summarize prompt |
| `summarize_text_config` | `strategy` | NULL | `'append'` |
| `summarize_text_config` | `reduction_factor` | NULL | `3` |
| `summarize_text_config` | `inference_config` | NULL | Provider defaults |
| `chunk_text_config` | `desired_length` | required | — |
| `chunk_text_config` | `max_length` | NULL | Same as `desired_length` |
| `chunk_text_config` | `overlap_length` | NULL | `0` |
| `chunk_text_config` | `strategy` | NULL | `'chars'` |
| `pdf_to_image` | `options` | `'{}'` | dpi 300, PNG, all pages |
