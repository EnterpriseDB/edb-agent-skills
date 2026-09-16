# AIDB Pipeline Step Operations Reference

Steps are specified as `step_N` arguments to `aidb.create_pipeline()`. Each step has an associated `step_N_options` JSONB argument, typically built with a config helper function. Steps must be sequenced in a compatible order based on what data type each step accepts and produces.

**Step count limit:** Maximum 10 steps per pipeline.

---

## ChunkText

Splits input text into smaller overlapping or non-overlapping chunks.

- **Accepts:** Text rows from source table or previous step
- **Produces:** Multiple text rows per input row (one per chunk), with a `part_id` ordering column

**Config helper:**
```sql
aidb.chunk_text_config(
    desired_length  INTEGER,           -- target chunk size in characters (required)
    max_length      INTEGER DEFAULT NULL,
    overlap_length  INTEGER DEFAULT NULL,  -- character overlap between consecutive chunks
    strategy        TEXT    DEFAULT NULL   -- chunking strategy; currently only default supported
)
```

**Example:**
```sql
step_1 => 'ChunkText',
step_1_options => aidb.chunk_text_config(desired_length => 512, overlap_length => 64)
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

Extracts plain text or Markdown from HTML bytes.

- **Accepts:** Binary (HTML content as BYTEA)
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

Extracts structured text from PDF bytes.

- **Accepts:** Binary (PDF content as BYTEA)
- **Produces:** Text

**Config helper:**
```sql
aidb.pdf_parse_config(
    method                TEXT,             -- currently only 'Structured' is supported
    allow_partial_parsing BOOLEAN DEFAULT NULL  -- continue on PDF errors (default: true)
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

- **Accepts:** Binary (PDF content as BYTEA)
- **Produces:** Binary (image bytes, one row per page)

**Config (raw JSONB — no helper function):**
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

**Note:** Typically chained with `PerformOcr` as step_2.

---

## PerformOcr

Runs optical character recognition on image bytes to extract text. Requires a registered OCR-capable model (currently `nim_paddle_ocr`).

- **Accepts:** Binary (image bytes as BYTEA)
- **Produces:** Text

**Config helper:**
```sql
aidb.ocr_config(model TEXT)  -- model must be registered with 'nim_paddle_ocr' provider
```

**Example (as step_2 after PdfToImage):**
```sql
step_2 => 'PerformOcr',
step_2_options => aidb.ocr_config('my_nim_ocr_model')
```

---

## KnowledgeBase

Generates vector embeddings for each row and stores them in the destination table. Enables similarity search via pgvector operators (`<->`, `<=>`, `<#>`, etc.).

- **Accepts:** Text or Image (set via `data_format`)
- **Produces:** Vector embeddings in destination table

**Config helper:**
```sql
aidb.knowledge_base_config(
    model             TEXT,
    data_format       TEXT,   -- 'Text' | 'Image'
    distance_operator TEXT    DEFAULT NULL,  -- 'L2' | 'InnerProduct' | 'Cosine' | 'L1' | 'Hamming' | 'Jaccard'
    vector_index      JSONB   DEFAULT NULL   -- via vector_index_*_config() helpers
)
```

**Distance operator → pgvector SQL operator mapping:**
| Value | Operator | Use case |
|---|---|---|
| `L2` (default) | `<->` | General-purpose Euclidean distance |
| `Cosine` | `<=>` | Normalized embeddings, direction-based similarity |
| `InnerProduct` | `<#>` | Unit-normalized vectors, max inner product search |

**Example:**
```sql
step_1 => 'KnowledgeBase',
step_1_options => aidb.knowledge_base_config(
    model             => 'my_embed_model',
    data_format       => 'Text',
    distance_operator => 'Cosine',
    vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
)
```

**Performing similarity search after pipeline runs:**
```sql
SELECT source_id FROM my_destination_table
ORDER BY embedding <=> aidb.encode_text_query('my search query', 'my_embed_model')
LIMIT 10;
```

---

## SemanticKB

Vectorizes PostgreSQL schema metadata (table names, column names, comments) and stores it for natural language discovery. The source is the database catalog, not a user table — `source_data_column` is not needed.

- **Accepts:** Schema metadata (automatic, from catalog)
- **Produces:** Vectorized schema index in the semantic KB

**Preferred interface:** Use `aidb.create_semantic_kb()` directly instead of a pipeline with SemanticKB step. It provides a higher-level API with dedicated search functions (`aidb.get_tables()`, `aidb.get_columns()`, `aidb.get_metadata()`).

---

## Compatible Sequencing

Steps must be sequenced so the output of step N matches the input of step N+1:

| Preceding Step | Following Step | Valid? |
|---|---|---|
| (source text column) | ChunkText | Yes |
| (source text column) | SummarizeText | Yes |
| (source BYTEA column) | ParseHtml | Yes |
| (source BYTEA column) | ParsePdf | Yes |
| (source BYTEA column) | PdfToImage | Yes |
| PdfToImage | PerformOcr | Yes |
| ParsePdf | ChunkText | Yes |
| ParseHtml | ChunkText | Yes |
| PerformOcr | ChunkText | Yes |
| ChunkText | KnowledgeBase | Yes |
| (source text column) | KnowledgeBase | Yes |
| (source BYTEA column) | KnowledgeBase (Image) | Yes |

Incompatible sequencing is caught and rejected at `create_pipeline()` time.
