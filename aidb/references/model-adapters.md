# AIDB Model Adapters Reference

Models are registered with `aidb.create_model(name, provider, config, credentials)`.
Credentials are stored in `pg_user_mappings` and **never returned** by `aidb.list_models()` or `aidb.get_model()`.

Use `aidb.embeddings_config()` and `aidb.completions_config()` helpers to build the `config` JSONB.
See [function-reference.md](function-reference.md) for complete signatures.

---

## Local Models (No External API)

Run entirely on the database host using downloaded model weights. No API key required.
Controlled by `aidb.max_threads` GUC.

| Provider | Capability | Notes |
|---|---|---|
| `bert_local` | Text embeddings | BERT-family; weights loaded from disk |
| `clip_local` | Image + text embeddings | CLIP vision/text; supports cross-modal search |
| `t5_local` | Text embeddings + completions | T5 text-to-text architecture |
| `llama_instruct_local` | Text completions | Llama instruction-tuned models |
| `smollm2_local` | Text completions | SmolLM2 lightweight local completions |
| `llamacpp_embeddings` | Text embeddings | llama.cpp embeddings (GGUF format) |
| `llamacpp_generate` | Text completions | llama.cpp generation (GGUF format) |
| `llamacpp_reranking` | Semantic reranking | llama.cpp reranking |
| `llamacpp_ocr` | OCR | llama.cpp-based OCR |

**Example (BERT local):**
```sql
SELECT aidb.create_model(
    'my_bert',
    'bert_local',
    config => '{"model": "/path/to/bert-weights"}'::JSONB
);
```

**Example (llamacpp embeddings from HuggingFace GGUF):**
```sql
SELECT aidb.create_model(
    'bge-small',
    'llamacpp_embeddings',
    config => '{"model": "unsloth/bge-small-en-v1.5-GGUF",
                "model_file": "bge-small-en-v1.5-f16.gguf",
                "n_ctx": 512}'::JSONB,
    validate => false  -- set false to skip download on registration
);
```

---

## OpenAI-Compatible Remote APIs

Use the OpenAI API protocol. Can point to OpenAI, NVIDIA NIM, self-hosted vLLM, etc.

| Provider | Capability | Notes |
|---|---|---|
| `openai_embeddings` | Text embeddings | OpenAI `/v1/embeddings` endpoint |
| `openai_completions` | Text completions / chat | OpenAI `/v1/chat/completions` endpoint |
| `embeddings` | Text embeddings | Generic OpenAI-compatible; set `url` for custom endpoint |
| `completions` | Text completions | Generic OpenAI-compatible; set `url` for custom endpoint |
| `nim_embeddings` | Text embeddings | NVIDIA NIM embeddings |
| `nim_completions` | Text completions | NVIDIA NIM completions |
| `nim_clip` | Image embeddings | NVIDIA NIM vision embeddings |
| `nim_paddle_ocr` | OCR | NVIDIA NIM Paddle OCR; **required for `PerformOcr` step** |
| `nim_reranking` | Semantic reranking | NVIDIA NIM reranking |

**Example (OpenAI embeddings):**
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

**Example (NVIDIA NIM OCR — required for PDF OCR pipelines):**
```sql
SELECT aidb.create_model(
    'my_nim_ocr',
    'nim_paddle_ocr',
    credentials => '{"api_key": "nvapi-..."}'::JSONB
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

## Agent-Capable Models

These support tool-calling and structured output required by Agent Hub:

| Provider | Capability | Notes |
|---|---|---|
| `anthropic_messages` | Text completions with tools | Anthropic Claude API |
| `anthropic_messages_azure` | Text completions with tools | Anthropic via Azure |
| `anthropic_messages_bedrock` | Text completions with tools | Anthropic via AWS Bedrock |
| `openai_responses` | Text completions with tools | OpenAI Responses API |
| `openai_responses_azure` | Text completions with tools | OpenAI via Azure |

---

## Testing

| Provider | Capability | Notes |
|---|---|---|
| `dummy` | Embeddings + completions | **Deterministic output; no external service; safe for all tests** |

The `dummy` provider produces fixed-dimension zero vectors for embeddings and scripted text for completions.

```sql
-- Basic test model:
SELECT aidb.create_model('test_model', 'dummy');

-- Test model with scripted responses:
SELECT aidb.create_model(
    'test_llm',
    'dummy',
    config => '{"responses": [{"response": "I am the test assistant."}]}'::JSONB,
    validate => false
);
```

---

## Capability Summary

| Capability | Providers |
|---|---|
| Text embeddings | `openai_embeddings`, `embeddings`, `bert_local`, `clip_local`, `t5_local`, `hf_tei`, `nim_embeddings`, `llamacpp_embeddings`, `openrouter_embeddings`, `dummy` |
| Image embeddings | `clip_local`, `nim_clip` |
| Text completions | `openai_completions`, `completions`, `llama_instruct_local`, `smollm2_local`, `t5_local`, `gemini`, `openrouter_chat`, `nim_completions`, `llamacpp_generate`, `anthropic_messages`, `openai_responses`, `dummy` |
| OCR | `nim_paddle_ocr`, `llamacpp_ocr` |
| Reranking | `hf_tei_reranking`, `nim_reranking`, `llamacpp_reranking` |
| Agent tools | `anthropic_messages`, `anthropic_messages_azure`, `anthropic_messages_bedrock`, `openai_responses`, `openai_responses_azure` |

---

## Credential Handling

- API keys and passwords in `config` or `credentials` are stored in `pg_user_mappings`, not plain-text catalog tables
- `aidb.list_models()` and `aidb.get_model()` **never return credential fields**
- Models are shared across pipeline steps by name without re-specifying credentials
- To rotate credentials, use `aidb.create_model` with the same name (updates existing) or drop and recreate

---

## Model Validation

By default, `aidb.create_model(validate => TRUE)` contacts the provider to validate configuration. Set `validate => false` for:
- Local models not yet downloaded
- Providers not reachable from the DB host during setup
- CI/test environments
