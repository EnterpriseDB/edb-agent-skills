# AIDB Pipelines Reference

Signature reference for pipeline types, views, CRUD, step operations, and the error log. AIDB 7.6.0.

A pipeline reads from a source, applies up to 10 ordered steps, and writes to a destination.

## Types

### `aidb.PipelineAutoProcessingMode`

`Live` (Postgres triggers, synchronous on write) | `Background` (background worker, batched) | `Disabled` (manual via `aidb.run_pipeline()`).

### `aidb.PipelineDataFormat`

`Text` | `Image` | `Pdf`.

### `aidb.PipelineSourceType`

`Table` (table or view) | `Volume` (PGFS) | `Empty` (pipeline generates its own data).

### `aidb.PipelineDestinationType`

`Table` | `Volume` | `Empty` (output discarded).

### `aidb.PipelineStepOperation`

`ChunkText` | `SummarizeText` | `ParseHtml` | `ParsePdf` | `PerformOcr` | `KnowledgeBase` | `PdfToImage` | `SemanticKB`.

### `aidb.PipelineStatus`

| Value | Meaning |
|---|---|
| `Stale` | Source changed; pipeline needs to run |
| `Processing` | Currently executing |
| `UpToDate` | All source data processed |
| `NoResults` | Completed but produced no output |
| `Failed` | Last execution failed |
| `Unknown` | Status indeterminable |
| `PartialErrors` | Some records failed; pipeline otherwise operational |
| `BlockingErrors` | Pipeline-level error stopped processing; resolve, re-run, then clear the entry |

### `aidb.ErrorBlocking`

Two dimensions — scope (record vs pipeline) and temporality.

| Value | Meaning |
|---|---|
| `RecordTemporary` | Transient record failure; others keep processing. Currently only model rate-limit errors |
| `RecordPermanent` | Permanent record failure (bad input); others keep processing |
| `PipelineTemporary` | Transient pipeline failure (network timeout, outage); blocks the step |
| `PipelinePermanent` | Permanent pipeline failure (deleted model, invalid config); blocks the step |

### `aidb.DistanceOperator`

`L2` (default) | `InnerProduct` | `Cosine` | `L1` | `Hamming` | `Jaccard`.

## Domains

- `aidb.pipeline_name_50` — TEXT, max 50 characters.
- `aidb.background_sync_interval` — INTERVAL, between `1 second` and `2 days` inclusive.

## Views

### `aidb.pipelines` (alias `aidb.pipes`)

`id` (integer), `name` (text), `source_type`, `source_schema`, `source`, `source_key_column`, `source_data_column`, `destination_type`, `destination_schema`, `destination`, `destination_key_column`, `destination_data_column`, `steps` (jsonb — ordered step definitions), `auto_processing`, `batch_size` (integer), `background_sync_interval` (interval), `owner_role` (text).

### `aidb.pipeline_metrics` (alias `aidb.pipem`)

Column names contain spaces and must be quoted: `pipeline`, `"auto processing"`, `"table: unprocessed rows"`, `"volume: scans completed"`, `"count(source records)"`, `"count(destination records)"`, `"Status"`, `"count(record errors)"`, `"count(blocking errors)"`, `"last run completed"` (timestamptz, added 7.6.0).

## Functions

### `aidb.create_pipeline`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | TEXT | required | Pipeline name. Max 46 characters |
| `source` | TEXT | required | Source table or volume |
| `step_1` | PipelineStepOperation | required | First step's operation |
| `source_key_column` | TEXT | NULL | Unique key column in the source |
| `source_data_column` | TEXT | NULL | Column holding the data to process |
| `destination` | TEXT | NULL | Destination table or volume |
| `auto_processing` | PipelineAutoProcessingMode | NULL | Processing mode |
| `batch_size` | INT | NULL | Records per batch |
| `background_sync_interval` | INTERVAL | NULL | Between background runs; 1 second – 2 days |
| `owner_role` | TEXT | NULL | Role that owns and executes the pipeline |
| `step_1_options` | JSONB | NULL | Config for step 1 |
| `step_2` … `step_10` | PipelineStepOperation | NULL | Operations for steps 2–10 |
| `step_2_options` … `step_10_options` | JSONB | NULL | Configs for steps 2–10 |

Returns `name`, `destination_type`, `destination_schema`, `destination`, `destination_key_column`, `destination_data_column`.

### Other functions

| Function | Parameters | Notes |
|---|---|---|
| `aidb.update_pipeline` | `name TEXT`, `auto_processing`, `batch_size INT`, `background_sync_interval INTERVAL` | Only updates auto-processing settings — steps, source and destination are immutable |
| `aidb.delete_pipeline` | `name TEXT` | Does not drop source or destination tables |
| `aidb.run_pipeline` | `pipeline_name TEXT` | Runs immediately regardless of `auto_processing` |
| `aidb.get_pipeline_metrics` | `name TEXT` | See below |

`aidb.get_pipeline_metrics` returns `name`, `auto_processing`, `count_events` (unprocessed change events, table sources), `count_source`, `count_known_objects` (volume sources), `last_full_run_id`, `count_destination`, `status`, `count_errors_record`, `count_errors_pipeline`, `last_run_completed` (added 7.6.0).

## Step operations

Each step takes `step_N` (operation name) and `step_N_options` (JSONB, usually from a helper).

### Intermediate storage

Any step can persist its own output to a table as well as passing it on. Off by default. No helper has a parameter for it — merge it in as raw JSON:

```
step_1_options => aidb.chunk_text_config(desired_length => 100) || jsonb_build_object(
    'intermediate_destination', jsonb_build_object('enabled', true))
```

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | BOOLEAN | required | Turn on intermediate storage for this step |
| `destination` | TEXT | NULL | Table name; defaults to `pipeline_<name>_step_<N>` |

Must be an object with an `enabled` key — any other JSON type errors at pipeline creation. The table has the standard shape: `source_id`, `part_ids`, `value`. On the **last** step the table is still written but `aidb.retrieve_text()`'s `intermediate_steps` column never surfaces it; query the table directly. Since `KnowledgeBase` must be last, enabling it there is rarely useful.

### `ChunkText` — `aidb.chunk_text_config()`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `desired_length` | INTEGER | required | Target chunk size; unit depends on `strategy` |
| `max_length` | INTEGER | NULL | Max chunk size. If omitted, `desired_length` is a strict upper limit |
| `overlap_length` | INTEGER | NULL | Overlap between consecutive chunks. Defaults to 0 |
| `strategy` | TEXT | NULL | `'chars'` (default) or `'words'` |

Introduces a `part_id` column; one source row may produce many output rows.

### `ParseHtml` — `aidb.html_parse_config()`

`method` (TEXT, NULL) — `'StructuredPlaintext'` (default) or `'StructuredMarkdown'` to retain hierarchy.

### `ParsePdf` — `aidb.pdf_parse_config()`

`method` (TEXT, NULL) — `'Structured'` (default). `allow_partial_parsing` (BOOLEAN, NULL) — `true` (default) continues past unparseable pages.

Unnests: one row per page, `part_id` = page index.

### `PdfToImage` — no helper; pass a JSON literal

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dpi` | INTEGER | `300` | Render resolution |
| `first_page` | INTEGER | NULL | First page, 1-based |
| `last_page` | INTEGER | NULL | Last page inclusive, 1-based |
| `max_pages` | INTEGER | NULL | Safety cap regardless of first/last |
| `render_annotations` | BOOLEAN | `true` | Include annotations and form fields |
| `format` | JSONB | `{"type":"png"}` | `{"type":"png"}` or `{"type":"jpeg"}` |

Unnests: one row per page, `part_id` = zero-based page index. Use for scanned or image-heavy PDFs with no reliable text layer, then feed `PerformOcr`. For digitally produced PDFs use `ParsePdf` — faster and needs no OCR model. Keep `png` when feeding NIM PaddleOCR.

### `PerformOcr` — `aidb.ocr_config()`

`model` (TEXT, required) — a registered OCR-capable model (e.g. provider `nim_paddle_ocr`, `nim_ocr`, or local `llamacpp_ocr`).

### `SummarizeText` — `aidb.summarize_text_config()`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | TEXT | required | Summarization model |
| `chunk_config` | JSONB | NULL | From `aidb.chunk_text_config()`; applied before summarizing |
| `prompt` | TEXT | NULL | Custom prompt |
| `strategy` | TEXT | NULL | `'append'` (default) or `'reduce'` |
| `reduction_factor` | INTEGER | NULL | With `'reduce'`: aggressiveness per pass (default 3) |
| `inference_config` | JSONB | NULL | From `aidb.inference_config()` |

### `KnowledgeBase` — `aidb.knowledge_base_config()`

**Must always be the last step** — its output is a `VECTOR`, which no later step can consume.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | TEXT | required | Embedding model |
| `data_format` | PipelineDataFormat | required | `'Text'` or `'Image'` |
| `distance_operator` | DistanceOperator | NULL | Defaults to `L2` |
| `vector_index` | JSONB | NULL | From a vector index helper |

`aidb.knowledge_base_config_from_kb(data_format)` attaches the pipeline to an **existing** knowledge base instead of creating one, inheriting its model, distance operator, and vector index. This is how several pipelines share one vector table.

Destination table defaults to `pipeline_<pipeline_name>`. Shape: `id` (BIGSERIAL), `pipeline_id` (INT), `source_id` (TEXT), `part_ids` (BIGINT[]), `value` (VECTOR).

### `SemanticKB`

Indexes schema metadata into a semantic knowledge base. See `knowledge-bases.md`.

### `aidb.inference_config` in steps

Same signature as in `models.md`; used for the `inference_config` field of `aidb.summarize_text_config()`.

## Vector index config helpers

For the `vector_index` parameter of `aidb.knowledge_base_config()`.

| Helper | Parameters |
|---|---|
| `aidb.vector_index_hnsw_config` | `vector_data_type` TEXT, `m` INTEGER (default 16), `ef_construction` INTEGER (default 64), `ef_search` INTEGER |
| `aidb.vector_index_ivfflat_config` | `vector_data_type` TEXT, `lists` INTEGER, `probes` INTEGER |
| `aidb.vector_index_chord_hnsw_config` | `vector_data_type` TEXT, `m` INTEGER, `ef_construction` INTEGER, `max_connections` INTEGER, `ml` DOUBLE PRECISION |
| `aidb.vector_index_chord_vchordq_config` | `vector_data_type` TEXT, `lists` TEXT, `spherical_centroids` BOOLEAN |
| `aidb.vector_index_hsphere_optimized_config` | `clusters` INTEGER (required), `precision_val` DOUBLE PRECISION (required), `vector_data_type` TEXT |
| `aidb.vector_index_disabled_config` | none |

**HNSW supports at most 2000 dimensions.** For higher-dimensional vectors use `aidb.vector_index_disabled_config()` and manage indexes manually.

HNSW ops class by distance operator: `L2` → `vector_l2_ops`, `InnerProduct` → `vector_ip_ops`, `Cosine` → `vector_cosine_ops`, `L1` → `vector_l1_ops`.

## Error log

Each pipeline has an error log table at `{source_schema}.pipeline_{pipeline_name}_errors`.

| Function | Parameters | Returns |
|---|---|---|
| `aidb.get_error_logs` | `p_pipeline_name TEXT`, `p_source_id TEXT`, `p_pipeline_step SMALLINT`, `p_error_category ErrorBlocking`, `p_limit INTEGER`, `p_offset INTEGER` | See below |
| `aidb.get_error_log_summary` | `p_pipeline_name TEXT` | `pipeline_step`, `step_operation`, `error_category`, `error_count`, `latest_failed_at` |
| `aidb.get_all_error_summaries` | none | Same plus `pipeline_name` |
| `aidb.clear_error_logs` | `p_pipeline_name TEXT`, `p_error_ids BIGINT[]` | `BIGINT` — rows deleted |
| `aidb.requeue_pipeline_errors` | `p_pipeline_name TEXT`, `p_error_ids BIGINT[]` | `TABLE(error_id bigint, action text)` |

`aidb.get_error_logs` returns `id`, `source_id`, `part_ids`, `pipeline_step`, `step_operation`, `error_message`, `error_category`, `failed_at`, `retry_count`, `last_retry_at`, `last_seen_at`. `failed_at` is fixed at first occurrence; `last_seen_at` updates on each repeat.

`aidb.requeue_pipeline_errors` (7.5.0+) deletes the targeted **record-level** entries and marks their sources dirty; the next normal run reprocesses them — the call itself does no processing. `action` is `requeued`, `skipped_pipeline_level`, or `not_found`, one row per input ID ordered by ascending `error_id`.

## GUC

`aidb.pipeline_error_warnings` — boolean, default `true`, context `SUSET`. When on, each logged error also emits a Postgres `WARNING`. Errors persist to the log either way. Non-superusers need `GRANT SET ON PARAMETER aidb.pipeline_error_warnings` before they can `SET` it.
