# AIDB Step Operations Reference

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

---

## ParsePdf

Extracts structured text from PDF bytes.

- **Accepts:** Binary (PDF content as BYTEA)
- **Produces:** Text

**Config helper:**
```sql
aidb.pdf_parse_config(
    method                TEXT,             -- currently only 'Structured' is supported
    allow_partial_parsing BOOLEAN DEFAULT NULL
)
```

---

## PdfToImage

Renders PDF pages to image bytes (one row per page). Typically chained with `PerformOcr`.

- **Accepts:** Binary (PDF as BYTEA)
- **Produces:** Binary (image bytes per page)

**Config (raw JSONB):**
```json
{"dpi": 300, "format": {"type": "png"}, "render_annotations": true, "max_pages": null}
```

---

## PerformOcr

Runs OCR on image bytes. Requires `nim_paddle_ocr` provider.

- **Accepts:** Binary (image as BYTEA)
- **Produces:** Text

**Config helper:**
```sql
aidb.ocr_config(model TEXT)
```

---

## KnowledgeBase

Generates vector embeddings and stores them in the destination table for similarity search.

- **Accepts:** Text or Image
- **Produces:** Vectors in destination table (queryable via `<->`, `<=>`, `<#>`)

**Config helper:**
```sql
aidb.knowledge_base_config(
    model             TEXT,
    data_format       TEXT,   -- 'Text' | 'Image'
    distance_operator TEXT    DEFAULT NULL,  -- 'L2' | 'InnerProduct' | 'Cosine'
    vector_index      JSONB   DEFAULT NULL
)
```

**Distance operators:**
| Value | SQL Operator | Use Case |
|---|---|---|
| `L2` (default) | `<->` | General Euclidean |
| `Cosine` | `<=>` | Normalized embeddings |
| `InnerProduct` | `<#>` | Unit-normalized vectors |

---

## Compatible Sequencing

| Source → | Next Step | Valid? |
|---|---|---|
| Text column | ChunkText | ✅ |
| Text column | SummarizeText | ✅ |
| Text column | KnowledgeBase | ✅ |
| BYTEA column | ParseHtml | ✅ |
| BYTEA column | ParsePdf | ✅ |
| BYTEA column | PdfToImage | ✅ |
| BYTEA column | KnowledgeBase (Image) | ✅ |
| ParsePdf | ChunkText | ✅ |
| ParseHtml | ChunkText | ✅ |
| PdfToImage | PerformOcr | ✅ |
| PerformOcr | ChunkText | ✅ |
| ChunkText | KnowledgeBase | ✅ |

Incompatible sequences are rejected at `create_pipeline()` time.
