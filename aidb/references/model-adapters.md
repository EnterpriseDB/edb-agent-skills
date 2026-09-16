# AIDB Model Adapters Reference

Models are registered with `aidb.create_model(name, provider, config, credentials, credentials_env, credentials_k8s_secret, validate)`. The `provider` argument is one of the adapter names below. Credentials can be passed inline as `credentials` JSONB, sourced from an allow-listed environment variable via `credentials_env`, or from an allow-listed mounted Kubernetes secret path via `credentials_k8s_secret`; whichever is passed is stored securely in PostgreSQL's user mapping system (`pg_user_mappings`) and is never returned by `aidb.list_models()` or `aidb.get_model()`. Pass `validate => true` to test-connect the model at registration time instead of waiting until first use.

Use `aidb.embeddings_config()` and `aidb.completions_config()` helpers to construct the `config` JSONB argument. See `function-reference.md` for their signatures.

---

## Local Models (No External API)

These run entirely on the database host using downloaded model weights. No API key required. Controlled by `aidb.max_threads`.

| Provider | Capability | Notes |
|---|---|---|
| `bert_local` | Text embeddings | BERT-family models; weights loaded from disk |
| `clip_local` | Image + text embeddings | CLIP vision/text; supports cross-modal search |
| `t5_local` | Text embeddings + completions | T5 text-to-text architecture |
| `llama_instruct_local` | Text completions | Llama instruction-tuned models |
| `smollm2_local` | Text completions | SmolLM2 lightweight local completions |

**Example:**
```sql
SELECT aidb.create_model(
    'my_bert',
    'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB
);
```

---

## OpenAI-Compatible Remote APIs

These use the OpenAI API protocol and can point to any compatible endpoint (OpenAI, NVIDIA NIM, self-hosted vLLM, etc.). Credentials are passed via `aidb.embeddings_config()` or `aidb.completions_config()`.

| Provider | Capability | Notes |
|---|---|---|
| `openai_embeddings` | Text embeddings | OpenAI `/v1/embeddings` endpoint |
| `openai_completions` | Text completions / chat | OpenAI `/v1/chat/completions` endpoint |
| `openai_responses` | Text completions | OpenAI Responses API |
| `openai_responses_azure` | Text completions | OpenAI Responses API via Azure |
| `embeddings` | Text embeddings | Generic OpenAI-compatible; specify `url` for custom endpoint |
| `completions` | Text completions | Generic OpenAI-compatible; specify `url` for custom endpoint |
| `nim_embeddings` | Text embeddings | NVIDIA NIM embeddings |
| `nim_completions` | Text completions | NVIDIA NIM completions |
| `nim_clip` | Image embeddings | NVIDIA NIM vision embeddings |
| `nim_paddle_ocr` | OCR | NVIDIA NIM Paddle OCR; required for the OCR pipeline step |
| `nim_reranking` | Semantic reranking | NVIDIA NIM reranking |
| `llamacpp_generate` | Text completions | llama.cpp server, OpenAI-compatible completions |
| `llamacpp_embeddings` | Text embeddings | llama.cpp server, OpenAI-compatible embeddings |
| `llamacpp_reranking` | Semantic reranking | llama.cpp server reranking |
| `llamacpp_ocr` | OCR | llama.cpp server, NIM-OCR-compatible |
| `anthropic_messages` | Text completions | Anthropic Messages API |
| `anthropic_messages_azure` | Text completions | Anthropic Messages API via Azure |
| `anthropic_messages_bedrock` | Text completions | Anthropic Messages API via AWS Bedrock |

**Example (OpenAI):**
```sql
SELECT aidb.create_model(
    'my_openai_embed',
    'openai_embeddings',
    config => aidb.embeddings_config(
        model   => 'text-embedding-3-small',
        api_key => 'sk-...'
    )
);
```

**Example (generic OpenAI-compatible, e.g. vLLM):**
```sql
SELECT aidb.create_model(
    'my_local_llm',
    'completions',
    config => aidb.completions_config(
        model => 'mistral-7b',
        url   => 'http://vllm-host:8000/v1/chat/completions'
    )
);
```

---

## Hosted Third-Party Services

| Provider | Capability | Notes |
|---|---|---|
| `gemini` | Text completions | Google Gemini API |
| `hf_tei` | Text embeddings | HuggingFace Text Embeddings Inference |
| `hf_tei_reranking` | Semantic reranking | HuggingFace TEI reranking endpoint |
| `openrouter_chat` | Text completions | OpenRouter LLM routing |
| `openrouter_embeddings` | Text embeddings | OpenRouter embedding routing |

**Example (HuggingFace TEI):**
```sql
SELECT aidb.create_model(
    'my_hf_embed',
    'hf_tei',
    config => aidb.embeddings_config(
        model => 'BAAI/bge-base-en-v1.5',
        url   => 'http://tei-host:8080/embed'
    )
);
```

---

## Testing

| Provider | Capability | Notes |
|---|---|---|
| `dummy` | Embeddings + completions | Deterministic output; no external service; safe for all test environments |

The `dummy` provider produces fixed-dimension zero vectors for embeddings and canned text for completions. It is the correct choice for all pipeline and semantic KB tests.

**Example:**
```sql
SELECT aidb.create_model('test_model', 'dummy');
```

---

## Capability Types

| Capability | Used By |
|---|---|
| Text embeddings | `KnowledgeBase` step, `encode_text()`, `encode_text_batch()`, `encode_text_query()`, Semantic KB |
| Image embeddings | `KnowledgeBase` step with `data_format => 'Image'`, `encode_image()` |
| Text completions | `SummarizeText` step, `generate_text()` (`decode_text()` is deprecated) |
| OCR | `PerformOcr` step (`nim_paddle_ocr` only) |
| Reranking | `rerank_text()` standalone function |

---

## Credential Handling Notes

- API keys and passwords passed in `config` or `credentials` are stored in `pg_user_mappings`, not in plain-text catalog tables
- `aidb.list_models()` and `aidb.get_model()` do not return credential fields
- Models can be shared across pipeline steps by name without re-specifying credentials
