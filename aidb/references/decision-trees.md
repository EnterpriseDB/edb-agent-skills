# AIDB Decision Trees — Operational Playbook

> This document provides decision-tree style guidance for common AIDB tasks.
> Read the relevant section only when needed.

---

## Decision Tree 1: How to Choose an Auto-Processing Mode

```
Is data arriving continuously and latency matters?
  YES → Use Live mode (synchronous trigger, blocks writer until done)
  NO  → Is throughput more important than per-write latency?
          YES → Use Background mode (async batch worker)
          NO  → Use Disabled mode, call aidb.run_pipeline() manually
                (best for: bulk backfills, testing, scheduled jobs)
```

**Switching modes without recreating:**
```sql
SELECT aidb.update_pipeline('my_pipeline', auto_processing => 'Background');
```

---

## Decision Tree 2: Choosing a Vector Distance Operator

```
Are your embeddings L2-normalized (unit vectors)?
  YES → Use Cosine (<=>)  — most common; works for text embeddings
  NO  → Do you need Euclidean geometric distance?
          YES → Use L2 (<->)  — general-purpose default
          NO  → Use InnerProduct (<#>) for max inner product search
```

**In knowledge_base_config:**
```sql
aidb.knowledge_base_config(model => 'my_model', data_format => 'Text', distance_operator => 'Cosine')
```

**In search queries:**
```sql
-- Cosine similarity
ORDER BY embedding <=> aidb.encode_text_query('my query', 'my_model')
-- L2 distance
ORDER BY embedding <-> aidb.encode_text_query('my query', 'my_model')
```

---

## Decision Tree 3: Choosing a Model Provider

```
Is external API access acceptable?
  NO  → Use local providers: bert_local, clip_local, llama_instruct_local, smollm2_local
  YES → Which service?
          OpenAI           → openai_embeddings, openai_completions
          NVIDIA NIM       → nim_embeddings, nim_completions, nim_clip, nim_paddle_ocr
          Google Gemini    → gemini
          HuggingFace TEI  → hf_tei (embeddings), hf_tei_reranking
          OpenRouter       → openrouter_embeddings, openrouter_chat
          Custom endpoint  → embeddings (with url=) or completions (with url=)
          Testing only     → dummy (no external service, deterministic output)
```

---

## Decision Tree 4: What Step Sequence Do I Need?

```
Source column type?
  TEXT column:
    → Just embed?                            Text → KnowledgeBase
    → Chunk first, then embed?               Text → ChunkText → KnowledgeBase
    → Summarize first, then embed?           Text → SummarizeText → KnowledgeBase
    → Summarize only (no embedding)?         Text → SummarizeText

  BYTEA column:
    → It's a PDF?
        → Extract text only?                 BYTEA → ParsePdf
        → Extract, chunk, embed?             BYTEA → ParsePdf → ChunkText → KnowledgeBase
        → OCR (image-based PDF)?             BYTEA → PdfToImage → PerformOcr
        → OCR then embed?                    BYTEA → PdfToImage → PerformOcr → ChunkText → KnowledgeBase
    → It's HTML?
        → Extract text only?                 BYTEA → ParseHtml
        → Extract, chunk, embed?             BYTEA → ParseHtml → ChunkText → KnowledgeBase
    → It's an image?
        → Embed image directly?              BYTEA → KnowledgeBase (data_format='Image')
```

---

## Decision Tree 5: Diagnosing Pipeline Errors

```
Pipeline not processing rows?
  1. Check mode:
     SELECT auto_processing FROM aidb.list_pipelines() WHERE name = 'my_pipeline';
     → Disabled? Call: SELECT aidb.run_pipeline('my_pipeline');
     → Live/Background? Check pg_stat_activity for background workers

  2. Check for errors:
     SELECT error_log_table FROM aidb.list_pipelines() WHERE name = 'my_pipeline';
     -- Then: SELECT * FROM aidb.<error_log_table>;

  3. Check GUC:
     SHOW aidb.pipeline_error_warnings;
     -- Set to 'on' to see warnings in PostgreSQL logs

  4. Re-queue failed rows (Background mode):
     SELECT aidb.requeue_pipeline_errors('my_pipeline');
```

---

## Decision Tree 6: Semantic KB — Choosing Search Parameters

```
What are you looking for?
  Exact table/column name match?
    → Use min_similarity => 0.9, limit => 5

  General schema exploration?
    → Use min_similarity => 0.8, limit => 10 (recommended default)

  Broad discovery / uncertain query?
    → Use min_similarity => 0.5, limit => 20

Need both schema entities AND semantic aliases?
    → Use aidb.semantic_kb_search() with sources => ARRAY['schema', 'alias']
```

---

## Decision Tree 7: Agent Won't Complete / Errors

```
agent_converse() returns non-NULL error column?
  "model" in error message?
    → Test model: SELECT aidb.encode_text('hello', 'my_model');
    → Check model registered: SELECT * FROM aidb.list_models();

  "budget" or "iterations" in error?
    → Increase max_iterations or change budget_strategy to 'attempt_complete'
    → SELECT aidb.update_agent('my_agent', max_iterations => 20);

  "tool" in error message?
    → Check tool exists: SELECT * FROM aidb.tools WHERE name = 'my_tool';
    → If MCP: SELECT aidb.refresh_mcp_tools('my_server');

  "delegation depth" in error?
    → Reduce nested delegates; max is 11 levels

  No error but wrong answer?
    → Review conversation log: SELECT * FROM aidb.conversation_log WHERE conversation_id = '<id>';
    → Enable debug: SELECT message FROM aidb.agent_converse('my_agent', 'prompt', debug => true);
```

---

## Safety Checklist Before Production Deployments

- [ ] Pipeline name ≤ 46 characters
- [ ] Destination table does NOT exist (`create_pipeline` creates it)
- [ ] Step sequence is valid (tested with a Disabled pipeline first)
- [ ] Model registered and validated (`SELECT * FROM aidb.list_models()`)
- [ ] `aidb.max_threads` tuned for available CPUs (requires restart to take effect)
- [ ] `shared_preload_libraries` includes `'aidb'` (and `'vchord'` if using VectorChord)
- [ ] Credentials confirmed NOT visible via `aidb.list_models()` (stored in `pg_user_mappings`)
- [ ] Error log monitoring configured (`aidb.pipeline_error_warnings = on` for dev)
- [ ] For agents: `read_only => true` set on agents that should not write
- [ ] For agents: reviewed tool list — agents only need the tools they require

---

## Common Errors and Quick Fixes

| Error Message (partial) | Likely Cause | Fix |
|---|---|---|
| `destination table already exists` | Pipeline create failed previously, table left behind | `DROP TABLE <destination>;` then recreate pipeline |
| `pipeline name too long` | Name exceeds 46 chars | Shorten the pipeline name |
| `incompatible step sequence` | e.g., ChunkText after KnowledgeBase | Reorder steps; see step sequencing rules |
| `model not found` | Model name typo or not registered | `SELECT * FROM aidb.list_models()` to verify |
| `max_threads` change has no effect | Requires restart | `ALTER SYSTEM SET aidb.max_threads = N;` + restart |
| `top_k must be >= 1` | Passed `top_k => 0` to KB search | Use `top_k => 1` minimum |
| Agent turn hangs | Infinite reasoning loop | Set `max_iterations`; check tool availability |
| Background worker missing | `shared_preload_libraries` not updated | Add `aidb` to `shared_preload_libraries`, restart PostgreSQL |
