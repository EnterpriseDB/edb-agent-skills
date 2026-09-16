# AIDB Decision Checklists

Use these when helping a user choose the right approach.

---

## Choosing a Pipeline Auto-Processing Mode

| User wants... | Mode |
|---|---|
| Embeddings computed immediately on every INSERT/UPDATE | `Live` |
| Batch processing in the background without blocking writes | `Background` |
| Manual control (trigger processing on demand) | `Disabled` |
| Testing / one-off runs | `Disabled` → `aidb.run_pipeline()` |

---

## Choosing How to Embed Text

| Use Case | Approach |
|---|---|
| One-off embedding of a string | `aidb.encode_text(text, model)` |
| Batch embedding of many strings | `aidb.encode_text_batch(texts[], model)` |
| Query-side encoding for bi-encoders | `aidb.encode_text_query(text, model)` |
| Embed rows from a table continuously | Pipeline with deprecated `KnowledgeBase` step (avoid for new work) or `encode_text` in triggers/functions |
| Embed schema metadata for semantic search | `aidb.create_semantic_kb()` |

---

## Choosing a Model Provider

| Situation | Provider |
|---|---|
| Testing, no external service | `dummy` |
| Local on-disk BERT weights | `bert_local` |
| Local CLIP (image+text) | `clip_local` |
| Local Llama instruct model | `llama_instruct_local` |
| Local llama.cpp server | `llamacpp_embeddings` / `llamacpp_generate` |
| OpenAI API | `openai_embeddings` / `openai_completions` |
| OpenAI via Azure | `openai_responses_azure` |
| Anthropic Claude | `anthropic_messages` |
| Anthropic via AWS Bedrock | `anthropic_messages_bedrock` |
| NVIDIA NIM | `nim_embeddings` / `nim_completions` / `nim_clip` / `nim_paddle_ocr` |
| HuggingFace TEI | `hf_tei` / `hf_tei_reranking` |
| Google Gemini | `gemini` |
| Any OpenAI-compatible endpoint | `embeddings` / `completions` (set `url` in config) |
| Multiple LLM providers via routing | `openrouter_chat` / `openrouter_embeddings` |
| OCR (NIM-based) | `nim_paddle_ocr` (required for `PerformOcr` step) |
| EDB Hybrid Control Plane models | Use `aidb.sync_hcp_models()` to auto-discover |

---

## Choosing a Credential Source

| Situation | Approach |
|---|---|
| Development / quick testing | Inline `credentials => '{"api_key": "..."}'::jsonb` |
| Production (env variable) | `credentials_env => 'MY_API_KEY'` (requires `aidb.env_var_allowed_prefix`) |
| Kubernetes deployment | `credentials_k8s_secret => '/secrets/api-key'` (requires `aidb.k8s_secret_allowed_path_prefix`) |

---

## Choosing a Vector Index

| Use Case | Index |
|---|---|
| Default / general-purpose ANN | `aidb.vector_index_hnsw_config()` |
| IVFFlat (good for large datasets, faster build) | `aidb.vector_index_ivfflat_config()` |
| VectorChord HNSW | `aidb.vector_index_chord_hnsw_config()` |
| VectorChord VCHORDQ (quantized) | `aidb.vector_index_chord_vchordq_config()` |
| No index (small dataset) | `aidb.vector_index_disabled_config()` |

---

## Checklist: Creating a New Pipeline

Before calling `aidb.create_pipeline()`:

- [ ] Model is registered: `SELECT * FROM aidb.list_models();`
- [ ] Source table exists with the correct column names
- [ ] Pipeline name is ≤ 46 characters
- [ ] Destination table does NOT exist yet
- [ ] Step sequence is compatible (see `references/step-operations.md`)
- [ ] Number of steps ≤ 10
- [ ] `auto_processing` mode chosen appropriately
- [ ] For `Background` mode: `aidb` is in `shared_preload_libraries`

---

## Checklist: Creating an Agent

Before calling `aidb.create_agent()`:

- [ ] Model is registered and capable of tool use (chat/completions adapter)
- [ ] All tool names in the `tools` array are registered in `aidb.tools`
- [ ] If using a `purpose`, the purpose exists in `aidb.purpose_registry`
- [ ] Budget strategy chosen: `ignore` | `error` | `summarize` | `attempt_complete`
- [ ] Token and iteration budgets set appropriately for the expected use case
- [ ] For agent memory: `aidb_memory.init()` called with appropriate namespace

---

## Semantic KB Similarity Thresholds

| Threshold | Meaning |
|---|---|
| `0.9+` | Near-exact match only |
| `0.8` | Good default for production semantic search |
| `0.7` | Balanced — recommended starting point |
| `0.5–0.6` | Broad exploration, higher noise |
| `< 0.5` | Very noisy, rarely useful |

---

## When NOT to Use Certain Features

| Feature | When to avoid |
|---|---|
| `KnowledgeBase` pipeline step | All new work — it is deprecated. Use `create_semantic_kb()` or `encode_text()` instead |
| `aidb.decode_text()` | Always — it is deprecated. Use `aidb.generate_text()` |
| Inline credentials in `config` | Production deployments — use `credentials_env` or `credentials_k8s_secret` |
| `allow_insecure_egress = on` | Production — only for isolated development |
| `allow_insecure_tls = on` | Production — only for isolated development |
| Unreviewed KB relationships | Never route through `suggest_joins`/`find_join_path` until relationship is approved |
| Session memory verbs as agent tools | Never — `session_start/get/end` are intentionally excluded from the tool catalog |
