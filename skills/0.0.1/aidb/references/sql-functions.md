# AIDB Standalone SQL Functions Reference

Signature reference for transformation functions callable directly in a query, with no pipeline. AIDB 7.6.0.

Every pipeline step operation is also available as a standalone function. Inference functions (`encode_text`, `generate_text`, `rerank_text`) are in `models.md`.

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
