# AIDB Quick Reference Card

## Installation
```sql
-- postgresql.conf must have: shared_preload_libraries = 'aidb'
-- Then restart PostgreSQL, then:
CREATE EXTENSION IF NOT EXISTS aidb CASCADE;
```

## Key Constraints

| Constraint | Value |
|---|---|
| Max pipeline name length | **46 characters** |
| Max steps per pipeline | **10** |
| Max reasoning iterations (agent) | **25** |
| Max delegation depth (agent) | **11** |
| `aidb.max_threads` change | **Requires PostgreSQL restart** |
| Destination table at creation | **Must NOT already exist** |
| Supported PostgreSQL versions | **14–18** |

## Pipeline Modes

| Mode | Behavior | Trigger |
|---|---|---|
| `Live` | Trigger-based | Runs on INSERT/UPDATE immediately |
| `Background` | Worker batches | Async worker polls state table |
| `Disabled` | Manual only | `aidb.run_pipeline()` only |

## Step Type Quick Reference

| Step | Input | Output | Config Helper |
|---|---|---|---|
| `ChunkText` | Text | Text (multiple) | `aidb.chunk_text_config()` |
| `SummarizeText` | Text | Text | `aidb.summarize_text_config()` |
| `ParseHtml` | Bytes | Text | `aidb.html_parse_config()` |
| `ParsePdf` | Bytes | Text | `aidb.pdf_parse_config()` |
| `PdfToImage` | Bytes | Bytes (per page) | Raw JSONB |
| `PerformOcr` | Bytes | Text | `aidb.ocr_config()` |
| `KnowledgeBase` | Text or Bytes | Vector | `aidb.knowledge_base_config()` |

## Compatible Step Sequences (Common)

| Pipeline Pattern | Steps |
|---|---|
| Text RAG | `ChunkText → KnowledgeBase` |
| PDF RAG | `ParsePdf → ChunkText → KnowledgeBase` |
| HTML RAG | `ParseHtml → ChunkText → KnowledgeBase` |
| Scanned PDF OCR | `PdfToImage → PerformOcr` |
| Scanned PDF RAG | `PdfToImage → PerformOcr → ChunkText → KnowledgeBase` |
| Summarize + Embed | `SummarizeText → KnowledgeBase` |

> `KnowledgeBase` is always **terminal**. No step can follow it.

## Provider Quick Reference

| Provider | Type | Network |
|---|---|---|
| `openai_embeddings` | Embeddings | Remote (OpenAI) |
| `openai_completions` | Completions | Remote (OpenAI) |
| `embeddings` | Embeddings | Remote (any OpenAI-compatible) |
| `completions` | Completions | Remote (any OpenAI-compatible) |
| `bert_local` | Embeddings | Local (on-disk) |
| `clip_local` | Image+Text Embeddings | Local (on-disk) |
| `llamacpp_embeddings` | Embeddings | Local (GGUF) |
| `llamacpp_generate` | Completions | Local (GGUF) |
| `llama_instruct_local` | Completions | Local (on-disk) |
| `hf_tei` | Embeddings | Remote (HuggingFace) |
| `hf_tei_reranking` | Reranking | Remote (HuggingFace) |
| `nim_embeddings` | Embeddings | Remote (NVIDIA NIM) |
| `nim_completions` | Completions | Remote (NVIDIA NIM) |
| `nim_paddle_ocr` | OCR | Remote (NVIDIA NIM) |
| `nim_reranking` | Reranking | Remote (NVIDIA NIM) |
| `nim_clip` | Image Embeddings | Remote (NVIDIA NIM) |
| `gemini` | Completions | Remote (Google) |
| `anthropic_messages` | Completions+Tools | Remote (Anthropic) |
| `openai_responses` | Completions+Tools | Remote (OpenAI) |
| `openrouter_chat` | Completions | Remote (OpenRouter) |
| `dummy` | Both | None (**test only**) |

## Agent-Capable Providers (support tool-calling)

| Provider | Notes |
|---|---|
| `anthropic_messages` | Claude API |
| `anthropic_messages_azure` | Claude via Azure |
| `anthropic_messages_bedrock` | Claude via AWS Bedrock |
| `openai_responses` | OpenAI Responses API |
| `openai_responses_azure` | OpenAI via Azure |

## Similarity Threshold Guide

| Threshold | Use |
|---|---|
| `0.9+` | Near-exact matches only |
| `0.8` | Good default for semantic search |
| `0.7` | Broader matching |
| `0.5–0.6` | Exploratory / broad sweep |

## Distance Operators (KnowledgeBase step)

| Value | SQL Operator | Use Case |
|---|---|---|
| `L2` (default) | `<->` | General-purpose Euclidean |
| `Cosine` | `<=>` | Normalized embeddings, direction |
| `InnerProduct` | `<#>` | Unit-normalized, max inner product |
| `L1` | `<+>` | Manhattan distance |
| `Hamming` | `<~>` | Binary vectors |
| `Jaccard` | `<%>` | Set-based similarity |

## Similarity Search Pattern

```sql
-- Destination table is named: pipeline_<pipeline_name>
-- or whatever you set as `destination` in create_pipeline
SELECT source_id
FROM pipeline_<name>
ORDER BY embedding <=> aidb.encode_text_query('query', 'model_name')
LIMIT 10;
```

## Error Handling

```sql
-- Check pipeline errors:
SELECT * FROM aidb.get_error_logs('<pipeline_name>') LIMIT 10;

-- Re-queue failed rows after fixing root cause:
SELECT aidb.requeue_pipeline_errors('<pipeline_name>');

-- Agent errors (never raises, always check error column):
SELECT conversation_id, message, error
FROM aidb.agent_converse('agent_name', 'prompt');
```

## GUC Parameters

| Parameter | Default | Restart Required |
|---|---|---|
| `aidb.max_threads` | half of CPUs (min 1, max 1024) | **Yes** |
| `aidb.pipeline_error_warnings` | true | No |

```sql
-- View:
SELECT name, setting FROM pg_settings WHERE name LIKE 'aidb.%';
-- Set (no restart needed):
SET aidb.pipeline_error_warnings = false;
```

## Diagnostics

```sql
-- Check workers:
SELECT backend_type, state FROM pg_stat_activity WHERE backend_type ILIKE '%aidb%';
-- Check GUCs:
SELECT name, setting FROM pg_settings WHERE name LIKE 'aidb.%';
-- Models:
SELECT * FROM aidb.list_models();
-- Pipelines:
SELECT name, auto_processing FROM aidb.list_pipelines();
-- Semantic KBs:
SELECT * FROM aidb.list_semantic_kbs();
```
