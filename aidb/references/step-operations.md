# AIDB Pipeline Step Operations Reference

Steps are specified as `step_N` arguments to `aidb.create_pipeline()`. Each step has an associated `step_N_options` JSONB argument, typically built with a config helper function. The helpers return `jsonb`, so they go into `step_N_options` without a cast. Steps must be sequenced so each step's output type matches the next step's input type (see Compatible Sequencing below).

**Step count limit:** Maximum 10 steps per pipeline. **Name limit:** pipeline names are at most 46 characters.

---

## Destination tables

Unless you pass `destination`, a pipeline writes to `<source_schema>.pipeline_<name>`. An unqualified `destination` also lands in the source schema, not `public`: with source `sales.docs`, `destination => 'docs_out'` creates `sales.docs_out`. Errors go to `<source_schema>.pipeline_<name>_errors`; read them with `aidb.get_error_logs('<name>')`.

The data column is always `value` and the key column is always `source_id`. `part_ids` is a `BIGINT[]` array with one element per step, not a single `part_id`. It records which part of the source row an output row came from: in a ChunkText → KnowledgeBase pipeline, the third chunk of a row has `part_ids = '{2,0}'`.

| Last step | Columns | Primary key |
|---|---|---|
| Any step except KnowledgeBase | `id BIGINT, source_id TEXT, part_ids BIGINT[], value <TEXT or BYTEA>` | `(source_id, part_ids)` |
| KnowledgeBase | `id BIGINT, pipeline_id INTEGER, source_id TEXT, part_ids BIGINT[], value VECTOR(<dims>)` | `(pipeline_id, source_id, part_ids)` |

The destination table must not exist when the pipeline is created, with one exception: a pipeline whose last step is KnowledgeBase may name an existing knowledge base table (one created by an earlier KnowledgeBase pipeline). The new pipeline is attached to that knowledge base, and `pipeline_id` tells their rows apart.

---

## ChunkText

Splits input text into smaller overlapping or non-overlapping chunks.

- **Accepts:** Text (a TEXT source column or a Text-producing previous step)
- **Produces:** Text, one output row per chunk

**Config helper:**
```sql
aidb.chunk_text_config(
    desired_length  INTEGER,               -- target chunk size (required, must be > 0)
    max_length      INTEGER DEFAULT NULL,  -- must be >= desired_length
    overlap_length  INTEGER DEFAULT NULL,  -- overlap between consecutive chunks, must be < desired_length
    strategy        TEXT    DEFAULT NULL   -- 'chars' (default) or 'words'
)
```

All three lengths are measured in the unit `strategy` picks: characters (grapheme clusters) for `chars`, whitespace-separated words for `words`. Any other strategy value is rejected with "unknown variant ..., expected `chars` or `words`". Breaking a length rule fails with an "Invalid parameters" error.

**Example:**
```sql
step_1 => 'ChunkText',
step_1_options => aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
```

Word-based chunks:
```sql
step_1 => 'ChunkText',
step_1_options => aidb.chunk_text_config(desired_length => 200, overlap_length => 20, strategy => 'words')
```

---

## SummarizeText

Compresses text using a language model. Supports single-pass summarization or iterative reduction.

- **Accepts:** Text
- **Produces:** Text (one summary per input)

**Config helper:**
```sql
aidb.summarize_text_config(
    model             TEXT,
    chunk_config      JSONB   DEFAULT NULL,   -- optional pre-chunking via chunk_text_config()
    prompt            TEXT    DEFAULT NULL,   -- custom summarization prompt
    strategy          TEXT    DEFAULT NULL,   -- 'append' (default) or 'reduce'
    reduction_factor  INTEGER DEFAULT NULL,   -- for 'reduce': target compression ratio (default 3)
    inference_config  JSONB   DEFAULT NULL    -- via aidb.inference_config()
)
```

**Example:**
```sql
step_1 => 'SummarizeText',
step_1_options => aidb.summarize_text_config(
    model            => 'my_llm',
    strategy         => 'reduce',
    reduction_factor => 4
)
```

---

## ParseHtml

Extracts plain text or Markdown from HTML.

- **Accepts:** Text (HTML stored in a TEXT column). A BYTEA source column is rejected at `create_pipeline()` time.
- **Produces:** Text

**Config helper:**
```sql
aidb.html_parse_config(
    method TEXT DEFAULT NULL  -- 'StructuredPlaintext' (default) | 'StructuredMarkdown'
)
```

**Example:**
```sql
step_1 => 'ParseHtml',
step_1_options => aidb.html_parse_config(method => 'StructuredMarkdown')
```

---

## ParsePdf

Extracts structured text from PDF bytes, one output row per page.

- **Accepts:** Bytes (PDF content in a BYTEA column, or a volume of PDF files)
- **Produces:** Text

**Config helper:**
```sql
aidb.pdf_parse_config(
    method                TEXT    DEFAULT NULL,  -- 'Structured' is the only method
    allow_partial_parsing BOOLEAN DEFAULT NULL   -- continue on PDF errors (default: true)
)
```

**Example:**
```sql
step_1 => 'ParsePdf',
step_1_options => aidb.pdf_parse_config(method => 'Structured')
```

---

## PdfToImage

Renders PDF pages to image bytes (PNG or JPEG). Produces one output row per page.

- **Accepts:** Bytes (PDF content in a BYTEA column, or a volume of PDF files)
- **Produces:** Bytes (image bytes, one row per page)

**Config (raw JSONB, no helper function):**
```json
{
  "dpi": 300,
  "format": {"type": "png"},
  "render_annotations": true,
  "first_page": 1,
  "last_page": null,
  "max_pages": null
}
```

**Example:**
```sql
step_1 => 'PdfToImage',
step_1_options => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB
```

**Note:** Typically chained with `PerformOcr` as step_2, or with `KnowledgeBase` (Image) to embed page images. It cannot feed `ChunkText` directly, because its output is Bytes.

---

## PerformOcr

Runs optical character recognition on image bytes to extract text. Requires a registered OCR-capable model, for example one with the `nim_paddle_ocr` or `llamacpp_ocr` provider.

- **Accepts:** Bytes (image bytes)
- **Produces:** Text

**Config helper:**
```sql
aidb.ocr_config(model TEXT)  -- name of a registered OCR-capable model
```

**Example (as step_2 after PdfToImage):**
```sql
step_2 => 'PerformOcr',
step_2_options => aidb.ocr_config('my_ocr_model')
```

---

## KnowledgeBase

The supported step for embedding content and keeping the embeddings in sync with the source. When it is the last step, `create_pipeline()` registers a knowledge base, creates the `VECTOR(<dims>)` destination table and builds a vector index on it. Use it for document search: chunk with `ChunkText`, embed with `KnowledgeBase`, then query with `aidb.retrieve_text`.

- **Accepts:** Text or Bytes (image), chosen with `data_format`
- **Produces:** Vector embeddings in the destination table. It must be the last step, since no step accepts its Vector output.

**Config helper:**
```sql
aidb.knowledge_base_config(
    model             TEXT,
    data_format       aidb.PipelineDataFormat,             -- 'Text' | 'Image'
    distance_operator aidb.DistanceOperator DEFAULT NULL,  -- 'L2' (default) | 'InnerProduct' | 'Cosine' | 'L1' | 'Hamming' | 'Jaccard'
    vector_index      JSONB                 DEFAULT NULL   -- via vector_index_*_config() helpers
)
```

`data_format` and `distance_operator` are enum types. String literals work as is; a TEXT variable or column needs a cast such as `v_format::aidb.PipelineDataFormat`. The `PipelineDataFormat` enum also has `Pdf`, but KnowledgeBase rejects it ("Embedding is not supported for the data format `Pdf`"). Parse the PDF first with ParsePdf, or render it with PdfToImage.

**Distance operator → pgvector SQL operator mapping:**
| Value | Operator | Use case |
|---|---|---|
| `L2` (default) | `<->` | General-purpose Euclidean distance |
| `Cosine` | `<=>` | Normalized embeddings, direction-based similarity |
| `InnerProduct` | `<#>` | Unit-normalized vectors, max inner product search. pgvector returns the negative inner product, so smaller is still closer |
| `L1` | `<+>` | Manhattan (taxicab) distance |
| `Hamming` | `<~>` | Binary vectors |
| `Jaccard` | `<%>` | Binary vectors |

**Example (document search pipeline):**
```sql
SELECT * FROM aidb.create_pipeline(
    name               => 'docs_kb',
    source             => 'public.docs',
    source_key_column  => 'id',
    source_data_column => 'body',
    step_1             => 'ChunkText',
    step_1_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
    step_2             => 'KnowledgeBase',
    step_2_options     => aidb.knowledge_base_config(
        model             => 'my_embed_model',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);
-- destination: public.pipeline_docs_kb
```

**Searching after the pipeline runs:** pass the schema-qualified destination table as the first argument. An unqualified table name is not found ("Knowledge base not found"). Passing the pipeline name still works but emits a WARNING asking for the `'<schema>.<table>'` identifier.
```sql
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_docs_kb', 'refund policy', 5);
```

`retrieve_text` returns `key, value, distance, part_ids, pipeline_name, intermediate_steps`. `aidb.retrieve_key` takes the same arguments and returns `key, distance, part_ids, pipeline_name`.

**Querying the vector table directly:** the embedding column is `value`. Encode the query once in a CTE and cast it to `vector`. The encode functions return `real[]`, and called inside `ORDER BY` they can run once per row.
```sql
WITH q AS (SELECT aidb.encode_text_query('my_embed_model', 'my search query')::vector AS v)
SELECT kb.source_id, kb.part_ids
FROM public.pipeline_docs_kb kb, q
ORDER BY kb.value <=> q.v
LIMIT 10;
```

---

## SemanticKB

Vectorizes PostgreSQL schema metadata (table names, column names, comments) and stores it for natural language discovery of tables and columns. The source is the database catalog, not a user table, so `source_data_column` is not needed. It does not embed document contents; use KnowledgeBase for that.

- **Accepts:** Schema metadata (automatic, from catalog)
- **Produces:** Vectorized schema index in the semantic KB

**Preferred interface:** Use `aidb.create_semantic_kb()` directly instead of a pipeline with a SemanticKB step. It provides a higher-level API with dedicated search functions: `aidb.semantic_kb_search(query_text, ...)` (hybrid search, a good first call), `aidb.get_entity_definitions(...)` (tables and views), `aidb.get_metadata(...)`, `aidb.get_column_definitions(...)` and `aidb.search_by_comment(...)`. See the function reference for their signatures.

---

## Compatible Sequencing

Each step reads one envelope type and writes one. A chain is valid exactly when each step's output type equals the next step's input type. Anything else is rejected at `create_pipeline()` time with "Output type '...' of the ... step (#N) does not match input type '...' of the next ... step (#N+1)". The first step must also match the source column: a TEXT column feeds a Text step and a BYTEA column feeds a Bytes step. A volume's files are read as text or as bytes, whichever the first step expects.

| Step | Input | Output |
|---|---|---|
| ChunkText | Text | Text |
| SummarizeText | Text | Text |
| ParseHtml | Text | Text |
| ParsePdf | Bytes | Text |
| PerformOcr | Bytes | Text |
| PdfToImage | Bytes | Bytes |
| KnowledgeBase (`data_format => 'Text'`) | Text | Vector |
| KnowledgeBase (`data_format => 'Image'`) | Bytes | Vector |

Common valid chains:

| Chain | Source column |
|---|---|
| ChunkText → KnowledgeBase (Text) | TEXT |
| KnowledgeBase (Text) | TEXT |
| ChunkText → SummarizeText | TEXT |
| SummarizeText → ChunkText → KnowledgeBase (Text) | TEXT |
| ParseHtml → ChunkText → KnowledgeBase (Text) | TEXT (HTML) |
| ParsePdf → ChunkText → KnowledgeBase (Text) | BYTEA or volume |
| ParsePdf → SummarizeText | BYTEA or volume |
| PdfToImage → PerformOcr → ChunkText | BYTEA or volume |
| PdfToImage → KnowledgeBase (Image) | BYTEA or volume |
| KnowledgeBase (Image) | BYTEA or volume |

Invalid: any step after KnowledgeBase (nothing accepts Vector), PdfToImage → ChunkText (Bytes into Text), and ParseHtml on a BYTEA column.
