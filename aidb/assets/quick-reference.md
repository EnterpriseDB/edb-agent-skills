# AIDB Quick-Reference Card

## Extension Lifecycle

```sql
-- Install (requires pgvector, pgfs, VectorChord available)
CREATE EXTENSION aidb CASCADE;

-- Upgrade (run from psql after updating packages)
ALTER EXTENSION aidb UPDATE;

-- Verify version
SELECT installed_version FROM pg_available_extensions WHERE name = 'aidb';
```

## GUC Parameters

| Parameter | Type | Default | Restart? | Description |
|---|---|---|---|---|
| `aidb.max_threads` | INTEGER | CPUs/2 | **Yes** | Thread pool for local model inference |
| `aidb.pipeline_error_warnings` | BOOLEAN | true | No | Emit pipeline errors to PostgreSQL log |
| `aidb.agent_session_source` | ENUM | `action_log` | No | `action_log` or `memory` for conversation history |
| `aidb.agent_memory_namespace` | TEXT | `pg_agents` | No | Memory namespace for Agent Hub |

## Model Providers Quick Reference

| Provider | Capability | Notes |
|---|---|---|
| `dummy` | All | Deterministic; no external service; for testing |
| `bert_local` | Embeddings | Local BERT weights |
| `clip_local` | Image+Text embeddings | Local CLIP model |
| `t5_local` | Embeddings+Completions | Local T5 |
| `llama_instruct_local` | Completions | Local Llama |
| `smollm2_local` | Completions | Local SmolLM2 |
| `openai_embeddings` | Embeddings | OpenAI API |
| `openai_completions` | Completions | OpenAI Chat API |
| `embeddings` | Embeddings | Generic OpenAI-compatible |
| `completions` | Completions | Generic OpenAI-compatible |
| `nim_embeddings` | Embeddings | NVIDIA NIM |
| `nim_completions` | Completions | NVIDIA NIM |
| `nim_clip` | Image embeddings | NVIDIA NIM |
| `nim_paddle_ocr` | OCR | Required for PerformOcr step |
| `nim_reranking` | Reranking | NVIDIA NIM |
| `gemini` | Completions | Google Gemini |
| `hf_tei` | Embeddings | HuggingFace TEI |
| `hf_tei_reranking` | Reranking | HuggingFace TEI |
| `openrouter_chat` | Completions | OpenRouter |
| `openrouter_embeddings` | Embeddings | OpenRouter |

## Pipeline Auto-Processing Modes

| Mode | Behavior | Trigger |
|---|---|---|
| `Live` | Synchronous, on every INSERT/UPDATE | DB trigger |
| `Background` | Async batch worker | Background worker |
| `Disabled` | Manual only | `aidb.run_pipeline()` |

## Pipeline Step Types & Sequencing

| Step | Input | Output | Key Config Helper |
|---|---|---|---|
| `ChunkText` | Text | Text (N rows) | `aidb.chunk_text_config(desired_length, ...)` |
| `SummarizeText` | Text | Text | `aidb.summarize_text_config(model, ...)` |
| `ParseHtml` | BYTEA | Text | `aidb.html_parse_config(method)` |
| `ParsePdf` | BYTEA | Text | `aidb.pdf_parse_config(method, ...)` |
| `PdfToImage` | BYTEA | BYTEA (pages) | raw JSONB `{"dpi":300,...}` |
| `PerformOcr` | BYTEA | Text | `aidb.ocr_config(model)` |
| `KnowledgeBase` | Text or Image | Vectors in dest table | `aidb.knowledge_base_config(model, data_format, ...)` |

**Common valid chains:**
- `Text → ChunkText → KnowledgeBase` (text embedding with chunking)
- `BYTEA → ParsePdf → ChunkText → KnowledgeBase` (PDF RAG)
- `BYTEA → ParseHtml → ChunkText → KnowledgeBase` (web page RAG)
- `BYTEA → PdfToImage → PerformOcr` (scanned PDF text extraction)
- `BYTEA → KnowledgeBase (Image)` (image embedding)

## Common Constraints

| Constraint | Limit |
|---|---|
| Max pipeline name length | 46 characters |
| Max steps per pipeline | 10 |
| Destination table | Must NOT exist at pipeline creation |
| Model validation | At pipeline creation, not at run time |
| Agent max reasoning iterations | 25 |
| Agent max delegation depth | 11 |

## Similarity Threshold Guidance (Semantic KB)

| Threshold | Use Case |
|---|---|
| 0.9+ | Near-exact match |
| 0.8 | Good default for semantic search |
| 0.5–0.7 | Broad exploration |

## Credential Security

- API keys are stored in `pg_user_mappings`, never in plain-text catalog tables
- `aidb.list_models()` and `aidb.get_model()` **do not** return credential fields
- Models can be shared across pipelines by name without re-specifying credentials

## Key Safety Rules

- `agent_converse` NEVER raises — always check `error` column
- Agents cannot escalate privileges via delegation
- `action_log` is INSERT + SELECT only — cannot be rewritten
- MCP tools always blocked in read-only agent mode
- `aidb.max_threads` changes require a PostgreSQL restart

## Vector Distance Operators

| Operator | SQL | Best For |
|---|---|---|
| L2 (default) | `<->` | General Euclidean distance |
| Cosine | `<=>` | Normalized embeddings |
| InnerProduct | `<#>` | Unit-normalized vectors |

## Scripts Available

| Script | Purpose |
|---|---|
| `scripts/check_aidb.sql` | SQL-based diagnostic check |
| `scripts/aidb_health_check.py` | Python diagnostic with pass/fail exit codes |
| `scripts/setup_embedding_pipeline.sql` | Text embedding pipeline template |
| `scripts/setup_document_pipeline.sql` | PDF/HTML ingestion pipeline templates |
| `scripts/setup_semantic_kb.sql` | Semantic Knowledge Base template |
| `scripts/setup_agent.sql` | Agent creation and conversation template |
