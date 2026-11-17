# AIDB Model Adapters Reference

Models are registered with `aidb.create_model(name, provider, config => ..., credentials => ..., replace_credentials => false, validate => true, credentials_env => ..., credentials_k8s_secret => ..., credential => ..., share => false)`. Pass everything after `provider` by name: the 5th positional argument is `replace_credentials` and the 6th is `validate`. The `provider` argument is one of the adapter names below. List every provider, its description and default host with `SELECT * FROM aidb.model_providers;`.

`create_model` test-connects the model by default. Pass `validate => false` to register it without the probe (for example when the endpoint is not reachable yet). The config shape is always checked, so unknown keys fail even with `validate => false`. `SELECT aidb.validate_model('my_model');` re-runs the registration probe later.

Pass at most one secret source:

- `credentials => '{"api_key": "..."}'` (or `{"basic_auth": "..."}`) stores the secret in a user mapping on a credential named `<schema>.<model>`, private to you unless `share => true`.
- `credentials_env` (the name of a server environment variable, default prefix `AIDB_`) and `credentials_k8s_secret` (a mounted file path, default prefix `/var/run/secrets/aidb`) store only the name or path and read the secret when the model loads. Only superusers and members of `aidb_admins` may use them.
- `credential => 'name'` binds the model to an existing credential made with `aidb.create_credential()`. It cannot be combined with the inline secret arguments.

Each provider family has a helper that builds its `config`: `aidb.embeddings_config`, `aidb.completions_config`, `aidb.openai_responses_config`, `aidb.anthropic_messages_config`, `aidb.gemini_config`, `aidb.openrouter_chat_config`, `aidb.openrouter_embeddings_config`, `aidb.nim_clip_config`, `aidb.nim_ocr_config`, `aidb.nim_reranking_config`, `aidb.bert_config`, `aidb.clip_config`, `aidb.t5_config`, `aidb.llama_config`. Do not use their `api_key` or `basic_auth` parameters: `create_model` rejects a config containing either. Pass secrets in `credentials`. See `function-reference.md` for their signatures.

---

## Local Models (In-Process)

Local providers run inference inside the Postgres backend. No API key is needed. Thread use is capped by `aidb.max_threads`.

The Hugging Face based providers (`bert_local`, `clip_local`, `t5_local`, `llama_instruct_local`) take `hf_model` (a Hugging Face repo id), optional `revision` (default `main`) and `cache_dir`. The older `model` key still works but logs a warning. With no `hf_model` each uses a pinned default: `bert_local` `sentence-transformers/all-MiniLM-L6-v2`, `clip_local` `openai/clip-vit-base-patch32`, `t5_local` `google-t5/t5-small`, `llama_instruct_local` `TinyLlama/TinyLlama-1.1B-Chat-v1.0`. On first use they download weights from Hugging Face (`https://huggingface.co`, or `HF_ENDPOINT`) into `cache_dir` (default `~/.cache/huggingface/hub` of the server user), so the server needs outbound HTTPS to Hugging Face unless the cache is pre-populated. Set `HF_TOKEN` in the server environment for gated repos.

| Provider | Capability | Notes |
|---|---|---|
| `bert_local` | Text embeddings | BERT-family sentence encoder from a Hugging Face repo (`hf_model`, default `sentence-transformers/all-MiniLM-L6-v2`); downloaded on first use |
| `clip_local` | Image + text embeddings | CLIP vision/text; supports cross-modal search |
| `t5_local` | Text embeddings + completions | T5 text-to-text architecture; not usable for Agent Hub agents |
| `llama_instruct_local` | Text completions | Llama instruction-tuned models (default TinyLlama-1.1B-Chat) |
| `llamacpp_generate` | Text completions (native tool calling) | GGUF model run in-process with llama.cpp |
| `llamacpp_embeddings` | Text embeddings | GGUF, in-process |
| `llamacpp_reranking` | Reranking | GGUF cross-encoder, in-process |
| `llamacpp_ocr` | OCR | Vision-language GGUF, in-process; also needs `mmproj_file` (or `mmproj_local_path`) for the multimodal projector |

The `llamacpp_*` providers are not servers and take no URL. Config: `hf_model` plus `model_file` (the `.gguf` name in the repo), or `local_path` (absolute path to a local `.gguf`), with optional `revision` (default `main`) and `cache_dir`. `n_ctx` defaults to 4096 for `llamacpp_generate` and 2048 for embeddings and reranking.

**Example:**
```sql
SELECT aidb.create_model(
    'my_bert',
    'bert_local',
    config => '{"hf_model": "sentence-transformers/all-MiniLM-L6-v2"}'::JSONB
);
```

---

## Remote APIs

`embeddings`, `completions`, `openai_embeddings`, `openai_completions`, `nim_embeddings`, `nim_completions` and `openrouter_*` speak the OpenAI embeddings or chat-completions protocol. `openai_responses*` use the OpenAI Responses API and `anthropic_messages*` the native Anthropic Messages API; both support native tool calling. `nim_clip`, `nim_reranking` and `nim_paddle_ocr` call NVIDIA NIM endpoints. Other completion providers fall back to prompt-injected tools when an agent uses them.

For OpenAI-compatible providers the `url` is the full endpoint (`.../v1/embeddings`, `.../v1/chat/completions`); aidb appends no path. Without a `url`, `embeddings` and `completions` default to the OpenAI URLs.

Pass API keys with `credentials => '{"api_key": "..."}'` (or `credentials_env`, `credentials_k8s_secret`, `credential`). The config helpers build non-secret settings only.

| Provider | Capability | Notes |
|---|---|---|
| `openai_embeddings` | Text embeddings | OpenAI `/v1/embeddings` endpoint |
| `openai_completions` | Text completions / chat | OpenAI `/v1/chat/completions` endpoint |
| `openai_responses` | Text completions | OpenAI Responses API |
| `openai_responses_azure` | Text completions | OpenAI Responses API via Azure; `url` is required (no default), e.g. `https://<resource>.openai.azure.com/openai/v1/responses` |
| `embeddings` | Text embeddings | Generic OpenAI-compatible; specify `url` for custom endpoint |
| `completions` | Text completions | Generic OpenAI-compatible; specify `url` for custom endpoint |
| `nim_embeddings` | Text embeddings | NVIDIA NIM embeddings |
| `nim_completions` | Text completions | NVIDIA NIM completions |
| `nim_clip` | Text + image embeddings | NVIDIA NIM CLIP (default `nvidia/nvclip`) |
| `nim_paddle_ocr` | OCR | NVIDIA NIM Paddle OCR |
| `nim_reranking` | Semantic reranking | NVIDIA NIM reranking |
| `anthropic_messages` | Text completions | Anthropic Messages API |
| `anthropic_messages_azure` | Text completions | Anthropic Messages API via Azure; `url` is required (no default), e.g. `https://<resource>.services.ai.azure.com/anthropic/v1/messages` |
| `anthropic_messages_bedrock` | Text completions | Anthropic Messages API via AWS Bedrock; `url` is required (no default), the regional base such as `https://bedrock-runtime.us-east-1.amazonaws.com` |

**Example (OpenAI):**
```sql
SELECT aidb.create_model(
    'my_openai_embed',
    'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => '{"api_key": "sk-..."}'::JSONB
);
```

**Example (generic OpenAI-compatible, e.g. vLLM):**
```sql
SELECT aidb.create_model(
    'my_local_llm',
    'completions',
    config => aidb.completions_config(
        model => 'mistral-7b',
        url   => 'https://vllm-host:8000/v1/chat/completions'
    )
);
```

Plain `http://` works only to `localhost` or a literal loopback or private IP (for example `http://10.0.0.5:8000/...`), and only after a superuser sets `aidb.allow_insecure_egress = on`. A hostname such as `http://vllm-host` is refused even then. For a self-signed HTTPS endpoint use `tls_config` or, as a last resort, `aidb.allow_insecure_tls` (applies to every outbound request).

---

## Other Remote Services

| Provider | Capability | Notes |
|---|---|---|
| `gemini` | Text completions | Google Gemini API (default model `gemini-2.5-flash`) |
| `hf_tei` | Text embeddings | Self-hosted Hugging Face Text Embeddings Inference server; `url` defaults to `http://localhost:8080`, which needs `aidb.allow_insecure_egress = on`; prefer an `https://` URL |
| `hf_tei_reranking` | Semantic reranking | Self-hosted TEI reranking server; `url` defaults to `http://localhost:8080`, which needs `aidb.allow_insecure_egress = on`; prefer an `https://` URL |
| `openrouter_chat` | Text completions | OpenRouter LLM routing |
| `openrouter_embeddings` | Text embeddings | OpenRouter embedding routing |

**Example (Hugging Face TEI):**
```sql
SELECT aidb.create_model(
    'my_hf_embed',
    'hf_tei',
    config => '{"url": "https://tei-host:8080"}'::JSONB
);
```

`hf_tei` has no `model` config key. `url` is the TEI base URL; aidb adds `/embed` itself, so do not include it. The model is whatever the TEI server was started with.

---

## Testing

| Provider | Capability | Notes |
|---|---|---|
| `dummy` | Embeddings + completions + OCR | Deterministic output; no external service; cannot rerank |

`dummy` returns 3-dimensional embeddings `[0.1, 0.2, 0.3]` for text and images (or `[x, x, x]` when the input text is a number `x`), `Hello world!` for completions, and `OCR dummy result...` for OCR. It needs no service. It cannot rerank; use a real reranking provider for `rerank_text` tests.

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
| OCR | `PerformOcr` step, `perform_ocr()` (`nim_paddle_ocr`, `llamacpp_ocr`, or `dummy` for tests) |
| Reranking | `rerank_text()` standalone function |

---

## Reusable Credentials

To share one secret across models, create it once, then bind models to it by name:

```sql
SELECT aidb.create_credential(
    'openai',
    credentials => '{"api_key": "sk-..."}'::JSONB,
    endpoint    => 'api.openai.com'
);

SELECT aidb.create_model(
    'my_openai_chat',
    'openai_completions',
    config     => aidb.completions_config(model => 'gpt-4o-mini'),
    credential => 'openai'
);
```

`endpoint` is required and lists the hosts the secret may be sent to (`'*'` allows any host, with a NOTICE). Inspect credentials with `SELECT * FROM aidb.credentials;`, and change them with `aidb.update_credential` or `aidb.delete_credential`.

---

## Credential Handling Notes

- API keys and passwords go only in `credentials` (or `credentials_env`, `credentials_k8s_secret`, `credential`) and are stored in a user mapping on the model's credential. `config` is stored in cleartext and must not contain `api_key` or `basic_auth`.
- `aidb.list_models()` (also the `aidb.models` view) and `aidb.get_model(model_name)` never return secrets. They do return the config in `options` and the bound credential's name in `credential`.
- Models are reused by name. A model's credential is private to the role that created it unless created with `share => true` (or shared later with `aidb.update_credential`), and its secret is only sent to the credential's endpoint host. Changing a model's `url` to another host needs an `aidb.update_credential(..., endpoint => ...)`.
