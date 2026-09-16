# AIDB Pipeline Step Compatibility Matrix

## Data Types (Envelopes)
Steps pass data between them as typed envelopes. The output type of step N must match the input type of step N+1.

| Envelope Type | Typical Column Type |
|---|---|
| `Text` | `TEXT`, `VARCHAR` |
| `Bytes` | `BYTEA` |
| `Vector` | `VECTOR` (pgvector) |

## Step Input/Output Types

| Step | Input | Output | Notes |
|---|---|---|---|
| `ChunkText` | Text | Text (multiple rows per input) | Adds `part_id` ordering column |
| `SummarizeText` | Text | Text | Uses a completions model |
| `ParseHtml` | Bytes | Text | HTML → plain text or Markdown |
| `ParsePdf` | Bytes | Text | PDF → structured text |
| `PdfToImage` | Bytes | Bytes | PDF pages → PNG/JPEG images |
| `PerformOcr` | Bytes | Text | Image → text (requires `nim_paddle_ocr`) |
| `KnowledgeBase` *(deprecated)* | Text or Bytes (Image) | Vector | Produces embeddings |

## Valid Step Sequences

```
Source (TEXT column)   → ChunkText   → destination
Source (TEXT column)   → SummarizeText → destination
Source (TEXT column)   → KnowledgeBase (deprecated) → destination
Source (BYTEA column)  → ParseHtml   → ChunkText → destination
Source (BYTEA column)  → ParsePdf    → ChunkText → destination
Source (BYTEA column)  → PdfToImage  → PerformOcr → destination
Source (BYTEA column)  → PdfToImage  → PerformOcr → ChunkText → destination
Source (BYTEA column)  → KnowledgeBase (Image mode, deprecated) → destination
ParsePdf               → SummarizeText → destination
ParseHtml              → SummarizeText → destination
PerformOcr             → SummarizeText → destination
```

## Common Invalid Sequences (rejected at create_pipeline time)
- `ChunkText` → `ParsePdf` (Text → Bytes: incompatible)
- `PdfToImage` → `ChunkText` (Bytes → Text: incompatible, OCR step is required between them)
- `ParsePdf` → `PerformOcr` (Text → Bytes: incompatible, OCR requires image bytes)

## Auto-Processing Modes

| Mode | Trigger | Use Case |
|---|---|---|
| `Live` | Triggers on INSERT/UPDATE (synchronous) | Low-latency, low-volume |
| `Background` | Async worker polls in batches | High-throughput, non-blocking |
| `Disabled` | Manual `aidb.run_pipeline('name')` only | Ad-hoc or scheduled |

## Config Helper Quick Reference

```sql
-- Chunk text into 512-char segments with 64-char overlap
aidb.chunk_text_config(desired_length => 512, overlap_length => 64)

-- Summarize with reduce strategy (iterative compression, factor 4x)
aidb.summarize_text_config(model => 'my_llm', strategy => 'reduce', reduction_factor => 4)

-- Parse PDF (only 'Structured' method is currently supported)
aidb.pdf_parse_config(method => 'Structured')

-- Parse HTML as Markdown
aidb.html_parse_config(method => 'StructuredMarkdown')

-- Render PDF pages to PNG at 300 DPI (raw JSONB, no helper)
'{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB

-- OCR using NIM Paddle OCR model
aidb.ocr_config(model => 'my_nim_ocr_model')

-- HNSW vector index
aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)

-- IVFFLAT vector index
aidb.vector_index_ivfflat_config(lists => 100, probes => 10)
```
