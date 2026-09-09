# AIDB Pipelines: Steps, Recipes, and Operations Runbook

* **Part 1** — step operations reference (what each step accepts/produces, how to sequence).
* **Part 2** — copy-paste pipeline templates.
* **Part 3** — operations & troubleshooting runbook, including two ready-to-run SQL
  blocks: the **environment probe** and the **per-pipeline diagnostic**.

Steps are passed to `aidb.create_pipeline()` as `step_N` (operation name) plus
`step_N_options` (JSONB, usually built with a config helper). The function exposes
`step_1` … `step_10`, matching `Pipeline::MAX_NUM_STEPS = 10` in
`src/pipeline_common/registry.rs`. Steps must be consecutive from `step_1`, and the chain
must be type-compatible; both are validated at `create_pipeline()` time, not at run time.
Envelope types between steps: `Text`, `Bytes`, `Vector`.

---

# PART 1 — STEP OPERATIONS

## Operation summary

| Operation | Accepts | Produces | Explodes rows? | Options helper |
|---|---|---|---|---|
| `ChunkText` | Text | Text | yes (per chunk) | `aidb.chunk_text_config` |
| `SummarizeText` | Text | Text | no | `aidb.summarize_text_config` |
| `ParseHtml` | Text | Text | no | `aidb.html_parse_config` |
| `ParsePdf` | Bytes | Text | yes (per section) | `aidb.pdf_parse_config` |
| `PdfToImage` | Bytes | Bytes (image) | yes (per page) | raw JSONB |
| `PerformOcr` | Bytes (image) | Text | yes | `aidb.ocr_config` |
| `KnowledgeBase` | Text or Bytes (per `data_format`) | Vector | no | `aidb.knowledge_base_config` |
| `SemanticKB` | catalog metadata | vectorized schema index | n/a | use `aidb.create_semantic_kb` |

"Explodes rows" means one input row can yield many destination rows, distinguished by
`part_ids BIGINT[]`.

---

## ChunkText

```sql
aidb.chunk_text_config(
    desired_length  INTEGER,                 -- target chunk size (required)
    max_length      INTEGER DEFAULT NULL,    -- hard ceiling
    overlap_length  INTEGER DEFAULT NULL,    -- characters repeated between chunks
    strategy        TEXT    DEFAULT NULL     -- e.g. 'chars', 'words'
)
```

The splitter stays at the highest semantic level that still fits. Setting `max_length` well
above `desired_length` keeps whole sentences/paragraphs together. Setting
`desired_length = max_length` forces fixed-size chunks (useful to fill an embedding model's
context window exactly).

```sql
step_1 => 'ChunkText',
step_1_options => aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
```

Sensible starting point for RAG: `desired_length` 400–800 chars, `overlap_length` ~10–15% of
that.

---

## SummarizeText

```sql
aidb.summarize_text_config(
    model             TEXT,                    -- must support text completions
    chunk_config      JSONB   DEFAULT NULL,    -- optional pre-chunking
    prompt            TEXT    DEFAULT NULL,    -- custom instruction
    strategy          TEXT    DEFAULT NULL,    -- 'append' (default) | 'reduce'
    reduction_factor  INTEGER DEFAULT NULL,    -- for 'reduce'
    inference_config  JSONB   DEFAULT NULL     -- aidb.inference_config(...)
)
```

```sql
step_1 => 'SummarizeText',
step_1_options => aidb.summarize_text_config(
    model            => 'my_llm',
    strategy         => 'reduce',
    reduction_factor => 4,
    inference_config => aidb.inference_config(temperature => 0.2, max_tokens => 128))
```

If the model lacks the language adapter, `create_pipeline` fails immediately with a message
containing `does not support language operations`.

---

## ParseHtml

```sql
aidb.html_parse_config(method TEXT DEFAULT NULL)
-- 'StructuredPlaintext' (default) | 'StructuredMarkdown'
```

`StructuredMarkdown` preserves headings, lists and links — usually better ahead of
chunking + embedding for RAG. The step reads a **TEXT** column.

---

## ParsePdf

```sql
aidb.pdf_parse_config(
    method                TEXT    DEFAULT NULL,   -- 'Structured'
    allow_partial_parsing BOOLEAN DEFAULT NULL    -- continue past broken pages
)
```

Extracts the embedded text layer from a **BYTEA** column. Scans with no text layer produce
empty output — use `PdfToImage` + `PerformOcr` instead.

---

## PdfToImage

No config helper; pass raw JSONB.

```json
{
  "dpi": 300,
  "format": {"type": "png"},
  "render_annotations": true,
  "first_page": 1,
  "last_page": null,
  "max_pages": 3
}
```

One output row per rendered page. Almost always chained into `PerformOcr`.
Watch `dpi` × `max_pages`: 600 dpi on a 200-page PDF will blow up memory and storage.

---

## PerformOcr

```sql
aidb.ocr_config(model TEXT)
```

Requires a provider with an OCR adapter. Wrong model type raises
`does not support OCR operations` at create time.

```sql
step_1 => 'PdfToImage',
step_1_options => '{"dpi":300,"format":{"type":"png"},"render_annotations":true}'::jsonb,
step_2 => 'PerformOcr',
step_2_options => aidb.ocr_config('my_ocr_model')
```

---

## KnowledgeBase

Computes embeddings into the destination table and registers an entry in
`aidb.knowledge_bases_v7`.

```sql
aidb.knowledge_base_config(
    model             TEXT,
    data_format       TEXT,                   -- 'Text' | 'Image'
    distance_operator TEXT  DEFAULT NULL,     -- 'L2'|'Cosine'|'InnerProduct'|'L1'|'Hamming'|'Jaccard'
    vector_index      JSONB DEFAULT NULL      -- aidb.vector_index_*_config()
)
```

| distance_operator | pgvector operator | Use when |
|---|---|---|
| `L2` (default) | `<->` | general purpose Euclidean |
| `Cosine` | `<=>` | normalized embeddings / direction similarity |
| `InnerProduct` | `<#>` | unit-normalized vectors, MIPS |

Must be the **terminal** step (its output envelope is `Vector`, which no step accepts).
Wrong model type raises `does not support text embedding operations`.

Attaching a second pipeline to an existing KB (e.g. text and images into one vector table)
uses `aidb.knowledge_base_config_from_kb('Image')`, which inherits model, distance operator
and index config from the KB. Compatibility is re-checked: model name, distance operator,
**embedding dimensions**, and vector index config must all match, or creation fails with an
explicit "mismatch" message.

Index choice: `aidb.vector_index_disabled_config()` (exact search) is reasonable for small
sets; when no `vector_index` is given the KB registry defaults to
`{"type":"hnsw","m":16,"ef_construction":64}`. Use the `chord_*` variants only when
VectorChord is installed and preloaded.

---

## SemanticKB

Vectorizes catalog metadata rather than user rows. **Prefer `aidb.create_semantic_kb()`** —
it wraps this step with a proper lifecycle and dedicated search functions. See section 9 of
`references/function-reference.md`. Such pipelines have an *empty* source and therefore no
per-pipeline error table.

---

## Valid sequencing matrix

| Preceding | Following | Valid |
|---|---|---|
| source TEXT column | `ChunkText`, `SummarizeText`, `ParseHtml`, `KnowledgeBase` (Text) | yes |
| source BYTEA column | `ParsePdf`, `PdfToImage`, `PerformOcr`, `KnowledgeBase` (Image) | yes |
| `ParsePdf` / `ParseHtml` / `PerformOcr` / `SummarizeText` | `ChunkText` | yes |
| `PdfToImage` | `PerformOcr` | yes |
| `ChunkText` | `SummarizeText`, `KnowledgeBase` (Text) | yes |
| `KnowledgeBase` | anything | no — must be terminal |
| `PdfToImage` | `ChunkText` | no — Bytes into a Text step |
| `ChunkText` | `KnowledgeBase` (Image) | no — Text into a Bytes step |

Canonical recipes:

* HTML → RAG: `ParseHtml` → `ChunkText` → `KnowledgeBase`
* Digital PDF → RAG: `ParsePdf` → `ChunkText` → `KnowledgeBase`
* Scanned PDF → RAG: `PdfToImage` → `PerformOcr` → `ChunkText` → `KnowledgeBase`
* Long docs → short index: `ChunkText` → `SummarizeText` → `KnowledgeBase`

---

# PART 2 — COPY-PASTE TEMPLATES

Replace `__NAME__`-style placeholders. Every template ends with a verification query.

## T1. Minimal text RAG over an existing table

```sql
-- 0. prerequisites
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;

-- 1. model (confirm the provider exists: SELECT * FROM aidb.model_providers;)
SELECT aidb.create_model('embed_model', 'bert_local');

-- 2. pipeline: source table -> embeddings
SELECT aidb.create_pipeline(
    name               => 'docs_kb',
    source             => 'public.documents',
    source_key_column  => 'id',
    source_data_column => 'body',
    destination        => 'documents_vectors',
    auto_processing    => 'Disabled',        -- backfill first, automate later
    step_1             => 'KnowledgeBase',
    step_1_options     => aidb.knowledge_base_config(
                              model             => 'embed_model',
                              data_format       => 'Text',
                              distance_operator => 'Cosine')
);

-- 3. backfill
SELECT aidb.run_pipeline('docs_kb');

-- 4. verify
SELECT * FROM aidb.pipeline_metrics_v7 WHERE pipeline = 'docs_kb';
SELECT key, value, distance FROM aidb.retrieve_text('docs_kb', 'your query here', 5);

-- 5. keep it fresh (only after the backfill succeeded)
SELECT aidb.update_pipeline('docs_kb', auto_processing => 'Background',
                            batch_size => 100,
                            background_sync_interval => '1 minute');
```

## T2. Chunked RAG from HTML

```sql
SELECT aidb.create_pipeline(
    name               => 'web_kb',
    source             => 'public.web_pages',
    source_key_column  => 'id',
    source_data_column => 'html',
    destination        => 'web_pages_vectors',
    step_1             => 'ParseHtml',
    step_1_options     => aidb.html_parse_config(method => 'StructuredMarkdown'),
    step_2             => 'ChunkText',
    step_2_options     => aidb.chunk_text_config(desired_length => 600, overlap_length => 80),
    step_3             => 'KnowledgeBase',
    step_3_options     => aidb.knowledge_base_config(
                              model => 'embed_model', data_format => 'Text',
                              distance_operator => 'Cosine',
                              vector_index => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64))
);
SELECT aidb.run_pipeline('web_kb');
SELECT source_id, part_ids FROM web_pages_vectors ORDER BY source_id, part_ids LIMIT 20;
```

## T3. Scanned-PDF OCR pipeline

```sql
SELECT aidb.create_model(
    'ocr_model', '__OCR_PROVIDER__',                  -- must have an OCR adapter
    credentials => jsonb_build_object('api_key', current_setting('my.ocr_key')),
    replace_credentials => true);

SELECT aidb.create_pipeline(
    name               => 'scans_ocr',
    source             => 'public.scanned_docs',
    source_key_column  => 'id',
    source_data_column => 'pdf_bytes',
    destination        => 'scanned_docs_text',
    step_1             => 'PdfToImage',
    step_1_options     => '{"dpi":300,"max_pages":20,"render_annotations":true,
                            "format":{"type":"png"}}'::jsonb,
    step_2             => 'PerformOcr',
    step_2_options     => aidb.ocr_config('ocr_model')
);
SELECT aidb.run_pipeline('scans_ocr');
SELECT source_id, part_ids, left(value, 120) FROM scanned_docs_text ORDER BY source_id, part_ids;
```

## T4. Object-storage (volume) source

```sql
SELECT pgfs.create_storage_location('docs_bucket', 's3://my-bucket',
                                    options => '{"region":"eu-central-1"}');
SELECT aidb.create_volume('docs_volume', 'docs_bucket', 'corpus/', 'Pdf');
SELECT * FROM aidb.list_volume_content('docs_volume');   -- confirm connectivity FIRST

SELECT aidb.create_pipeline(
    name        => 'volume_kb',
    source      => 'docs_volume',           -- no key/data column for volumes
    destination => 'volume_vectors',
    step_1      => 'ParsePdf',
    step_1_options => aidb.pdf_parse_config(),
    step_2      => 'ChunkText',
    step_2_options => aidb.chunk_text_config(desired_length => 600, overlap_length => 60),
    step_3      => 'KnowledgeBase',
    step_3_options => aidb.knowledge_base_config(model => 'embed_model', data_format => 'Text')
);
SELECT aidb.run_pipeline('volume_kb');
```

## T5. Smoke test with zero external dependencies

Use the deterministic test provider (`dummy`, if present in
`aidb.model_providers`) to prove the plumbing without any network access.

```sql
SELECT aidb.create_model('smoke_model', 'dummy');
CREATE TABLE smoke_src (id int PRIMARY KEY, content text);
INSERT INTO smoke_src VALUES (1, 'hello'), (2, 'world');

SELECT aidb.create_pipeline(
    name => 'smoke_pipe', source => 'smoke_src',
    source_key_column => 'id', source_data_column => 'content',
    step_1 => 'KnowledgeBase',
    step_1_options => aidb.knowledge_base_config(model => 'smoke_model', data_format => 'Text'));

SELECT aidb.run_pipeline('smoke_pipe');
SELECT count(*) FROM public.pipeline_smoke_pipe;      -- default destination name
SELECT * FROM aidb.get_error_log_summary('smoke_pipe');

-- teardown (delete_pipeline also drops public.pipeline_smoke_pipe)
SELECT aidb.delete_pipeline('smoke_pipe');
DROP TABLE smoke_src;
SELECT aidb.delete_model('smoke_model');
```

Note: `dummy` embeddings tie at identical scores. It proves wiring, not ranking.

---

# PART 3 — OPERATIONS & TROUBLESHOOTING RUNBOOK

## 3.1 Environment probe (run this before proposing any work)

Read-only. Copy the whole block into psql, or save it as a `.sql` file and run with
`psql ... -v ON_ERROR_STOP=0 -P pager=off -f probe.sql`. A section that errors is itself the
answer (e.g. `schema "aidb" does not exist` ⇒ the extension is not installed).

```sql
-- 1. extensions
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('aidb','vector','pgfs','vchord') ORDER BY extname;

-- 2. background workers: must contain 'aidb', restart required to change
SHOW shared_preload_libraries;

-- 3. runtime configuration (pending_restart = t means "set but not active")
SELECT name, setting, unit, context, pending_restart
FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'pgfs.%' ORDER BY name;

-- 4. providers compiled into THIS build -- never assume
SELECT server_name, server_description FROM aidb.model_providers ORDER BY server_name;

-- 5. inventory
SELECT name, provider FROM aidb.models ORDER BY name;
SELECT name, source, destination, auto_processing FROM aidb.pipelines_v7 ORDER BY name;
SELECT * FROM aidb.pipeline_metrics_v7;
SELECT * FROM aidb.get_all_error_summaries();
SELECT * FROM aidb.knowledge_bases_v7;
SELECT * FROM aidb.list_semantic_kbs();
SELECT * FROM aidb.list_volumes();

-- 6. can the current role use AIDB?
SELECT current_user,
       pg_has_role(current_user, 'aidb_users', 'MEMBER') AS in_aidb_users;
```

How to read it:

| Observation | Meaning / next action |
|---|---|
| no `aidb` row in step 1 | `CREATE EXTENSION IF NOT EXISTS aidb CASCADE;` (ask first) |
| `aidb` present, `vector` absent | CASCADE was skipped; `KnowledgeBase` steps will fail |
| `aidb` missing from `shared_preload_libraries` | `Background` mode will never advance; use `aidb.run_pipeline()` until an admin adds it and restarts |
| `pending_restart = t` for `aidb.max_threads` | the new value is not in effect yet |
| desired provider absent from step 4 | pick another provider; `create_model` would fail |
| `in_aidb_users = f` and not superuser | ask an admin for `GRANT aidb_users TO <role>;` |

## 3.2 Per-pipeline diagnostic

Read-only. Set the name once, then run top to bottom and **stop at the first section that
explains the symptom**.

```sql
\set p 'my_pipeline'

-- 1. definition: source, destination, steps, mode, owner_role
SELECT * FROM aidb.pipelines_v7 WHERE name = :'p';

-- 2. progress + error counters
SELECT * FROM aidb.pipeline_metrics_v7 WHERE pipeline = :'p';

-- 3. error counts by category
SELECT * FROM aidb.get_error_log_summary(:'p');

-- 4. the 25 most recent errors
SELECT id, source_id, pipeline_step, step_operation, error_category,
       left(error_message, 300) AS error_message, retry_count, last_seen_at
FROM aidb.get_error_logs(:'p', p_limit => 25);

-- 5. do the models referenced by the steps still exist?
SELECT name, provider FROM aidb.models ORDER BY name;

-- 6. is the background worker alive? (only matters in Background mode)
SHOW shared_preload_libraries;
SELECT pid, backend_type, datname, state, wait_event_type, wait_event
FROM pg_stat_activity
WHERE backend_type ILIKE '%aidb%' OR application_name ILIKE '%aidb%';

-- 7. runtime state
SELECT * FROM aidb.pipeline_runtime_state WHERE name = :'p';
```

Decision table for section 2 (`pipeline_metrics_v7`):

| Reading | Conclusion | Action |
|---|---|---|
| `count(blocking errors)` > 0 | pipeline-level failure halts everything | go to section 4, fix the cause, re-run |
| unprocessed > 0, destination = 0 | nothing has run | `Disabled` ⇒ `aidb.run_pipeline()`; `Background` ⇒ check section 6 |
| unprocessed = 0, destination = 0 | no eligible source rows, or every row errored | section 4 |
| destination growing | healthy | verify quality with `aidb.retrieve_text(...)` |
| `last run completed` NULL (section 7) | never completed a run | as above |

For section 4: `source_id IS NULL` ⇒ **blocking** error (model/credential/permission), never
requeued — fix and re-run. `source_id NOT NULL` ⇒ record-level error, eligible for
`aidb.requeue_pipeline_errors`. **Always quote `error_message` back to the user verbatim**;
AIDB error text carries the remedy.

## 3.3 Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| `The destination table 'X' already exists.` | re-running a create script | drop the table or pick a different `destination` |
| `... does not support text embedding operations` / `language operations` / `OCR operations` | model lacks the adapter the step needs | pick a provider with that capability (`references/model-adapters.md`) |
| `The requested adapter is not supported by the model provider: <provider>` | same class of problem | as above |
| `config must not contain "api_key" or "basic_auth"` | secret placed in `config` | move it to `credentials` / `credentials_env` |
| `Credentials for model provider "X" already exist` | provider-level user mapping exists | `replace_credentials => true`, or omit credentials to reuse |
| `Model provider with name "X" not found` | provider not compiled into this build | `SELECT * FROM aidb.model_providers;` |
| `Steps must be defined in a consecutive order. Missing steps: [...]` | a `step_N` was skipped | renumber so steps run 1..N with no gaps |
| `Output type '...' of the X step (#n) does not match input type '...'` | incompatible chain | consult the sequencing matrix in Part 1 |
| pipeline name rejected as too long | name + generated suffixes exceed PostgreSQL's identifier limit | shorten the name; the error states the exact maximum |
| `Sync intervals for background workers must be >1s and <2d` | `background_sync_interval` outside the domain `CHECK (VALUE >= '1 second' AND VALUE <= '2 days')` (`static-sql/pipeline_common.sql`) | pass an interval inside that range |
| `The source 'X' cannot be found.` | typo, wrong schema, or no permission | qualify the name; check `\dt` and grants |
| `Table public.X is missing one or both columns: id and content.` | wrong `source_key_column` / `source_data_column` | check `\d source_table` |
| Destination stays empty after `run_pipeline` | all rows errored, or no eligible rows | `aidb.get_error_logs`; check `count(source records)` |
| Background pipeline never processes | worker not running | add `aidb` to `shared_preload_libraries` + restart; meanwhile use `run_pipeline()` |
| Live pipeline makes INSERTs very slow | inference on the write path | switch to `Background` with a suitable `batch_size` |
| Errors keep reappearing after requeue | blocking error (`source_id IS NULL`) | fix model/credentials/permissions, then re-run |
| `Volume name '...' is not a valid unquoted PostgreSQL identifier` | hyphen in volume name | use underscores |
| Volume lists no files | wrong pgfs location/prefix or missing credentials | `aidb.list_volume_content()` before building the pipeline; for `file://` set `pgfs.allowed_local_fs_paths` |
| `Embedding dimension mismatch: ...` | attaching to an existing KB whose vector column has different dimensions | use the original model, or build a new KB |
| Local model OOM / extremely slow | thread pool or host RAM | tune `aidb.max_threads` (**restart required**) |
| `topk ... must be at least 1, got 0` | `topk => 0` passed to a retrieve function | pass a positive integer |
| Retrieval returns nothing | empty vector table, or similarity floor too high | check destination row count; lower `min_similarity`; query with the same model that built the KB |
| Semantic KB stale | KB in `Disabled` mode | `aidb.refresh_semantic_kb()` or switch to `Background`/`Live` |
| `aidb.get_error_logs` empty for a Semantic KB pipeline | empty-source pipelines have no error table | use PostgreSQL logs + `aidb.semantic_kb_stats()` |

## 3.4 Retry workflow

```sql
-- 1. see what failed
SELECT id, source_id, error_category, left(error_message,150)
FROM aidb.get_error_logs('my_pipeline', p_limit => 50);

-- 2. fix the root cause (credentials, model, source data, permissions)

-- 3. requeue the record-level rows (blocking rows are skipped by design)
SELECT * FROM aidb.requeue_pipeline_errors('my_pipeline', ARRAY[1,2,3]::bigint[]);

-- 4. drain (Background workers do it automatically; otherwise force it)
SELECT aidb.run_pipeline('my_pipeline');

-- 5. confirm
SELECT * FROM aidb.get_error_log_summary('my_pipeline');
```

**Large volume + Disabled mode caveat:** requeue sets a retry marker that only the background
worker honours. A manual `run_pipeline` on a volume source re-reads the *whole* volume. For
large volumes: switch to `Background`, requeue, let the worker drain, switch back.

## 3.5 Performance tuning checklist

1. Prefer `Background` over `Live` for anything with sustained write volume.
2. Raise `batch_size` for remote embedding providers (fewer, larger HTTP calls); also raise
   the provider's `max_batch_size` / `max_concurrent_requests` in `embeddings_config`.
3. Local models are CPU-bound: `aidb.max_threads` (restart). One model load is cached per
   backend via an LRU; `aidb.remove_cached_model()` evicts.
4. Add a vector index only once the KB is large; HNSW build cost is real.
5. Chunk sizing dominates both cost and quality — fewer, larger chunks reduce embedding calls.

## 3.6 Safety rules for destructive actions

Always state what will be destroyed and get explicit user confirmation before:

* `aidb.delete_pipeline` — also **drops the destination table** and any intermediate step
  tables (the shared vector table survives only if other pipelines are attached to that KB)
* `aidb.delete_knowledge_base` / `aidb.delete_semantic_kb`
* `aidb.delete_model` — drops the user mapping, i.e. **credentials shared across that
  provider**, which can break other models
* `aidb.delete_volume_file` / `aidb.delete_volume` — touches real object storage
* `DROP EXTENSION aidb CASCADE` — destroys every AIDB object in the database
* `aidb.clear_error_logs` — loses the audit trail; prefer `requeue_pipeline_errors`
* switching a busy pipeline to `Live` — adds inference latency to every write
* `CREATE EXTENSION`, GUC changes, and anything requiring a PostgreSQL restart
