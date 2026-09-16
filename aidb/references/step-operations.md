# AIDB Pipeline Step Operations Reference

Steps are specified as `step_N` / `step_N_options` arguments to `aidb.create_pipeline()`.
Max **10 steps** per pipeline. Steps must sequence compatibly (output type of step N must match input type of step N+1). Incompatible sequences are **rejected at creation time**.

---

## Data Envelope Types

Data flows through steps as one of three envelope types:

| Type | SQL type | Description |
|------|----------|-------------|
| `Text` | `TEXT` | Plain or structured text |
| `Bytes` | `BYTEA` | Binary content (PDFs, images, HTML) |
| `Vector` | `VECTOR` | Embedding vectors |

---

## ChunkText

Splits input text into overlapping or non-overlapping chunks.

- **Accepts:** `Text`
- **Produces:** Multiple `Text` rows per input (one per chunk), with a `part_id` ordering column

```sql
step_N => 'ChunkText',
step_N_options => aidb.chunk_text_config(
    desired_length  => 512,      -- target chunk size in characters (required)
    max_length      => NULL,     -- hard cap
    overlap_length  => 64,       -- character overlap between consecutive chunks
    strategy        => NULL      -- chunking strategy; currently only default supported
)
```

---

## SummarizeText

Compresses text using a language model.

- **Accepts:** `Text`
- **Produces:** `Text` (one summary per input)

```sql
step_N => 'SummarizeText',
step_N_options => aidb.summarize_text_config(
    model            => 'my_llm',
    chunk_config     => NULL,           -- optional pre-chunking
    prompt           => NULL,           -- custom summarization prompt
    strategy         => 'reduce',       -- 'append' (default) | 'reduce'
    reduction_factor => 3,              -- compression ratio for 'reduce' strategy
    inference_config => aidb.inference_config(temperature => 0.3)
)
```

---

## ParseHtml

Extracts plain text or Markdown from HTML bytes.

- **Accepts:** `Bytes` (HTML content as BYTEA)
- **Produces:** `Text`

```sql
step_N => 'ParseHtml',
step_N_options => aidb.html_parse_config(
    method => 'StructuredMarkdown'  -- 'StructuredPlaintext' (default) | 'StructuredMarkdown'
)
```

---

## ParsePdf

Extracts structured text from PDF bytes.

- **Accepts:** `Bytes` (PDF content as BYTEA)
- **Produces:** `Text`

```sql
step_N => 'ParsePdf',
step_N_options => aidb.pdf_parse_config(
    method                => 'Structured',     -- currently only 'Structured' is supported
    allow_partial_parsing => TRUE              -- continue on PDF errors (default: true)
)
```

---

## PdfToImage

Renders PDF pages to image bytes. Produces **one output row per page**.

- **Accepts:** `Bytes` (PDF content as BYTEA)
- **Produces:** `Bytes` (image bytes, one row per page)

```sql
step_N => 'PdfToImage',
step_N_options => '{
    "dpi": 300,
    "format": {"type": "png"},
    "render_annotations": true,
    "first_page": 1,
    "last_page": null,
    "max_pages": null
}'::JSONB
```

**Typically chained with `PerformOcr` as the next step.**

---

## PerformOcr

Runs optical character recognition on image bytes. Requires `nim_paddle_ocr` provider.

- **Accepts:** `Bytes` (image bytes as BYTEA)
- **Produces:** `Text`

```sql
step_N => 'PerformOcr',
step_N_options => aidb.ocr_config('my_ocr_model')
-- model must be registered with 'nim_paddle_ocr' provider
```

---

## KnowledgeBase

Generates vector embeddings and stores them in the destination table. Enables pgvector similarity search.

- **Accepts:** `Text` or `Bytes` (image, set via `data_format`)
- **Produces:** `Vector` stored in destination table

```sql
step_N => 'KnowledgeBase',
step_N_options => aidb.knowledge_base_config(
    model             => 'my_embed_model',
    data_format       => 'Text',           -- 'Text' | 'Image'
    distance_operator => 'Cosine',
    vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
)
```

**Distance operator → pgvector SQL operator mapping:**

| Value | Operator | Use Case |
|-------|----------|----------|
| `L2` (default) | `<->` | General-purpose Euclidean distance |
| `Cosine` | `<=>` | Normalized embeddings, direction similarity |
| `InnerProduct` | `<#>` | Unit-normalized vectors, max inner product |
| `L1` | `<+>` | Manhattan distance |
| `Hamming` | `<~>` | Binary vectors |
| `Jaccard` | `<%>` | Set-based similarity |

**Performing similarity search after pipeline runs:**

```sql
SELECT source_id
FROM my_destination_table
ORDER BY embedding <=> aidb.encode_text_query('my search query', 'my_embed_model')
LIMIT 10;
```

---

## SemanticKB (Deprecated for direct use)

Vectorizes PostgreSQL catalog metadata for natural language discovery.

**Preferred interface:** Use `aidb.create_semantic_kb()` directly instead.

---

## Compatible Step Sequencing

| Source / Preceding Step | Following Step | Valid? |
|-------------------------|----------------|--------|
| Source text column | `ChunkText` | ✅ |
| Source text column | `SummarizeText` | ✅ |
| Source text column | `KnowledgeBase` (Text) | ✅ |
| Source BYTEA column | `ParseHtml` | ✅ |
| Source BYTEA column | `ParsePdf` | ✅ |
| Source BYTEA column | `PdfToImage` | ✅ |
| Source BYTEA column | `KnowledgeBase` (Image) | ✅ |
| `ParsePdf` | `ChunkText` | ✅ |
| `ParseHtml` | `ChunkText` | ✅ |
| `PerformOcr` | `ChunkText` | ✅ |
| `PdfToImage` | `PerformOcr` | ✅ |
| `ChunkText` | `KnowledgeBase` | ✅ |
| `KnowledgeBase` | Anything else | ❌ (terminal) |
| `Text` step | `ParsePdf` / `ParseHtml` / `PdfToImage` | ❌ (type mismatch) |

---

## Common Multi-Step Recipes

### Document RAG (PDF → Chunk → Embed)
```
ParsePdf → ChunkText → KnowledgeBase
```

### Web Content RAG (HTML → Chunk → Embed)
```
ParseHtml → ChunkText → KnowledgeBase
```

### PDF OCR (scanned documents)
```
PdfToImage → PerformOcr
```

### PDF OCR → Embed (for scanned doc search)
```
PdfToImage → PerformOcr → ChunkText → KnowledgeBase
(requires 4-step pipeline; PerformOcr produces Text)
```

### Text Summarization + Embed
```
SummarizeText → KnowledgeBase
```
