# AIDB Quick-Reference Card

## Pipeline Step Sequences (Valid Chains)

| Goal | Steps |
|---|---|
| Embed text | `Text column → KnowledgeBase` |
| Chunk then embed | `Text column → ChunkText → KnowledgeBase` |
| Summarize then embed | `Text column → SummarizeText → KnowledgeBase` |
| PDF text extraction | `BYTEA → ParsePdf` |
| PDF → embed | `BYTEA → ParsePdf → ChunkText → KnowledgeBase` |
| HTML → embed | `BYTEA → ParseHtml → ChunkText → KnowledgeBase` |
| PDF OCR | `BYTEA → PdfToImage → PerformOcr` |
| PDF OCR + embed | `BYTEA → PdfToImage → PerformOcr → ChunkText → KnowledgeBase` |
| Image embed | `BYTEA → KnowledgeBase (data_format='Image')` |

## Model Provider Quick Pick

| Need | Provider | Requires |
|---|---|---|
| Text embedding (local) | `bert_local` | Model weights on disk |
| Text embedding (API) | `openai_embeddings` | API key |
| Text embedding (custom URL) | `embeddings` | URL + optional key |
| Image embedding | `clip_local` or `nim_clip` | Weights or NIM API key |
| Text completion | `openai_completions` | API key |
| Text completion (local) | `llama_instruct_local` | Model weights |
| OCR | `nim_paddle_ocr` | NIM API key |
| Reranking | `hf_tei_reranking` or `nim_reranking` | URL or NIM API key |
| Testing | `dummy` | Nothing |

## Key Constraints

```
Pipeline name: max 46 characters
Pipeline steps: max 10 per pipeline
Destination table: must NOT exist at creation
Model validation: happens at create_model() time by default
max_threads change: requires PostgreSQL restart
Agent reasoning: max 25 iterations per turn
Agent delegation: max 11 levels deep
Semantic KB search top_k: minimum 1 (0 returns error)
```

## Distance Operators

| aidb value | pgvector operator | Best for |
|---|---|---|
| `L2` (default) | `<->` | General-purpose Euclidean |
| `Cosine` | `<=>` | Normalized embeddings |
| `InnerProduct` | `<#>` | Unit-normalized vectors |

## Auto-Processing Mode Decision

```
Need real-time? → Live
High throughput, async? → Background
Bulk backfill / testing? → Disabled + run_pipeline()
```

## Similarity Threshold Guidance

```
≥ 0.9  — Near-exact matches only
  0.8  — Good default for semantic search
0.5–0.7 — Broad exploration
```

## Budget Strategy for Agents

```
ignore          → Log warning, keep going
error           → Halt, return error
summarize       → Ask model for closing summary
attempt_complete → Grant 3 extra iterations (default when max_iterations set)
```
