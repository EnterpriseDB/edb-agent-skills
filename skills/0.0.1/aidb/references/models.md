# AIDB Models Reference

Signature reference for model registration, inference, and config helpers. AIDB 7.6.0.

A model is registered once by name, then referenced by that name everywhere else (pipeline steps, standalone functions, knowledge bases, agents).

## Configuration model

Two distinct layers, and it matters which one a setting belongs to:

- **Creation-time `config`** — persistent, set via a provider-specific config helper in `aidb.create_model()`.
- **Call-time `inference_config`** — per-call override built with `aidb.inference_config()`. Falls back to `config` for anything not overridden.

Only these accept a call-time override:

| Function | Call-time override |
|---|---|
| `aidb.generate_text()`, `aidb.generate_text_batch()` | Yes — `inference_config` argument |
| `aidb.summarize_text()`, `aidb.summarize_text_aggregate()` | Yes — via `aidb.summarize_text_config(inference_config => ...)` |
| `aidb.encode_text()`, `aidb.encode_text_batch()`, `aidb.encode_image()` | No — fixed at creation time |
| `aidb.rerank_text()` | No — fixed at creation time |
| `aidb.perform_ocr()` | No — its `options` only selects the model |

## Catalog views

### `aidb.model_providers`

| Column | Type | Description |
|---|---|---|
| `server_name` | name | Provider name |
| `server_description` | text | Provider description |
| `server_options` | text[] | Available configuration options |

### `aidb.models`

| Column | Type | Description |
|---|---|---|
| `name` | text | Model name |
| `provider` | text | Provider name |
| `options` | text[] | Configured options |
| `functions` | text[] | Internal capability identifiers (e.g. `aidb-openai-text-embeddings`, `aidb-openai-text-completion`) |

## Model management

### `aidb.create_model`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | TEXT | required | Unique model name |
| `provider` | TEXT | required | Provider name (see `aidb.model_providers`) |
| `config` | JSONB | `'{}'` | Provider-specific configuration; build with a config helper |
| `credentials` | JSONB | `'{}'` | e.g. `{"api_key": "..."}`. Stored in `pg_user_mappings` |
| `replace_credentials` | BOOLEAN | `false` | Update stored credentials without re-creating the model |
| `validate` | BOOLEAN | `true` | Probe the model at creation time to confirm it works |
| `credentials_env` | TEXT | NULL | Name of an environment variable to read credentials from. Mutually exclusive with `credentials` |

**Validation.** With `validate => true`, a minimal probe inference runs before the registration commits — local models are downloaded and loaded; external models get a small test request. On failure nothing is registered. Use `validate => false` to defer a large download or register ahead of credentials being available, then `aidb.validate_model()` later.

**`credentials_env`.** Only the variable *name* is stored; the value is read from the Postgres backend process environment at each use and never persisted. The variable may hold a bare secret (used as `api_key`) or a JSON object (e.g. `{"basic_auth": "..."}`). Its name must start with the prefix in the `aidb.env_var_allowed_prefix` GUC (`AIDB_` by default) — this prevents a model config from naming an arbitrary host environment variable and exfiltrating it. The same mechanism backs MCP `headers_env`.

**Leaked credentials (7.6.0 change).** `create_model()` now *rejects* a `config` that embeds `api_key` or `basic_auth` directly. Pass secrets via `credentials` or `credentials_env`.

> **Binding handling rule.** Never write a literal secret into a model's `config`, and never print one in the SQL you show the user. Route every credential through `credentials` or `credentials_env`; prefer `credentials_env` so nothing is persisted at all.
>
> One provider quirk to be aware of rather than to exploit: `aidb.gemini_config()` declares `api_key` as a config field (see the helper table below), so a Gemini config *can* carry a key and it lands in the less-restricted config store instead of the credentials store. Do not take that route. Register Gemini with `credentials_env` (or `credentials`) like every other remote provider, and if you meet an installation where that genuinely fails, report the failure to the user and let them decide — do not silently inline the key. Run `aidb.audit_leaked_credentials()` on any installation you inherit.

**TLS.** Add a `tls_config` object inside `config`: `{"insecure_skip_verify": bool, "ca_path": "/path/to/ca.pem"}`.

### `aidb.audit_leaked_credentials`

No arguments. Reports pre-existing models where credentials were detected embedded in `config`.

Returns `TABLE(model_name TEXT, detected_key TEXT, detected_at TIMESTAMPTZ)`.

### `aidb.get_model`

`aidb.get_model(model_name TEXT)` → `TABLE(name text, provider text, options text[])`

### `aidb.delete_model`

`aidb.delete_model(model_name TEXT)` → name, provider, options of the deleted model. Does not affect pipelines or knowledge bases referencing it until they next execute.

### `aidb.validate_model`

`aidb.validate_model(model_name TEXT)` → `TEXT` — the capability probed (`text embedding`, `language`, `reranking`, `image embedding`, `OCR`). Raises on failure.

### HCP model functions

For models running on EDB Hybrid Manager.

| Function | Signature / returns |
|---|---|
| `aidb.list_hcp_models()` | `TABLE(name text, url text, model text)` |
| `aidb.create_hcp_model(name TEXT, hcp_model_name TEXT)` | Registers an HCP-hosted model by its running instance name |
| `aidb.sync_hcp_models()` | `TABLE(status text, model text)` — `created`, `deleted`, `unchanged`, `skipped` |

## Inference functions

> `aidb.decode_text()` / `aidb.decode_text_batch()` were renamed to `aidb.generate_text()` / `aidb.generate_text_batch()` in 7.6.0. Old names still work but are deprecated and will be removed. Always use the new names.

| Function | Parameters | Returns |
|---|---|---|
| `aidb.encode_text` | `model_name TEXT, input TEXT` | `real[]` — cast to `vector` for pgvector operators |
| `aidb.encode_text_batch` | `model_name TEXT, input TEXT[]` | `SETOF real[]`, input order; column named `encode_text_batch` |
| `aidb.encode_image` | `model_name TEXT, input BYTEA` | `real[]` |
| `aidb.generate_text` | `model_name TEXT, input TEXT, inference_config JSON = NULL` | `TEXT` |
| `aidb.generate_text_batch` | `model_name TEXT, input TEXT[], inference_config JSON = NULL` | `SETOF TEXT`, input order; column named `generate_text_batch` |
| `aidb.rerank_text` | `model_name TEXT, query TEXT, input TEXT[] = '{}'` | `TABLE(text text, logit_score double precision, id int)` — `id` is the input index |

`inference_config` must be cast to `::json`.

## `aidb.inference_config`

All parameters optional. Returns `JSONB` — cast to `::json` before passing to an inference function.

| Parameter | Type | Description |
|---|---|---|
| `system_prompt` | TEXT | Prepended system prompt. Not supported by T5 |
| `temperature` | DOUBLE PRECISION | `0.0` is deterministic |
| `max_tokens` | INTEGER | Max tokens to generate |
| `top_p` | DOUBLE PRECISION | Nucleus sampling threshold |
| `seed` | BIGINT | Reproducible output |
| `repeat_penalty` | REAL | `1.0` = none. Not supported by NIM or OpenAI |
| `repeat_last_n` | INTEGER | Tokens considered for `repeat_penalty`. Not supported by NIM or OpenAI |
| `thinking` | BOOLEAN | `true`/NULL retains `<think>` tags; `false` strips them |
| `extra_args` | JSONB | Passed through to the provider API |

### Tool calling and structured output

Three further fields exist but are **not** named parameters of `aidb.inference_config()` — build them as raw JSON, merging with `||` if combining with the helper.

| Field | Type | Description |
|---|---|---|
| `tools` | JSONB array | OpenAI-compatible function definitions |
| `tool_choice` | JSONB | `"auto"`, `"required"`, `"none"`, or `{"type":"function","function":{"name":"..."}}` |
| `response_format` | JSONB | `{"type":"json_schema","json_schema":{"schema":{...}}}` |

Only `llamacpp_generate`, `openai_responses` (+ `_azure`), and `anthropic_messages` (+ `_azure`/`_bedrock`) honor these; every other provider rejects them outright on direct calls.

- On `openai_responses` / `anthropic_messages` these map to the API's native fields and real tool-call responses are parsed back.
- On those providers `response_format` is mutually exclusive with `tools`/`tool_choice`. Anthropic has no native structured-output field, so `response_format` is implemented as a forced call to a synthetic tool.
- The rejection applies only to *direct* calls. Any provider except `t5_local` can still back an agent — the agent framework falls back to simulating tool calls via prompt injection.

## Providers

### Local

| Provider | Runtime | Purpose |
|---|---|---|
| `bert_local` | Candle | Text embeddings |
| `clip_local` | Candle | Text + image embeddings |
| `t5_local` | Candle | Text-to-text generation; also encodes text |
| `llama_instruct_local` | Candle | Instruction-following generation |
| `llamacpp_generate` | llama.cpp | Generation from GGUF; supports `tools`/`response_format` |
| `llamacpp_embeddings` | llama.cpp | Embeddings from GGUF |
| `llamacpp_reranking` | llama.cpp | Reranking from GGUF (cross-encoder and Qwen3-Reranker decoder-only) |
| `llamacpp_ocr` | llama.cpp | OCR from a GGUF vision model |
| `dummy` | — | Deterministic fake output for testing |

### External

| Family | Providers |
|---|---|
| Generic OpenAI-compatible | `embeddings`, `completions` |
| OpenAI | `openai_embeddings`, `openai_completions`, `openai_responses`, `openai_responses_azure` |
| Anthropic | `anthropic_messages`, `anthropic_messages_azure`, `anthropic_messages_bedrock` |
| NVIDIA NIM | `nim_embeddings`, `nim_completions`, `nim_clip`, `nim_reranking`, `nim_paddle_ocr` |
| Google | `gemini` |
| OpenRouter | `openrouter_chat`, `openrouter_embeddings` |

Together with the nine local providers, these are the complete set of 26 foreign servers `CREATE EXTENSION` registers **in the build this reference was written against**. `embeddings` and `completions` are the generic OpenAI-compatible adapters; `openai_*` and `nim_*` are preconfigured specializations of the same adapters.

> **The installation is the authority, not this list.** Before offering any provider, run
> `SELECT server_name, server_description FROM aidb.model_providers ORDER BY 1;`
> and work from what it returns. Never claim a provider is unavailable without having run that query on the target installation.

**HuggingFace TEI.** EDB product material lists HuggingFace TEI among the usable remote providers. The names `hf_tei` and `hf_tei_reranking` appear in the codebase, and in the build this reference was written against they had no registered foreign server, so `aidb.create_model()` could not use them there. Resolve this the same way as anything else: query `aidb.model_providers` on the target installation. If a TEI server name is present, use it. If it is not, say so factually ("this installation registers no `hf_tei` server — here is what it does register") and offer the generic OpenAI-compatible `embeddings` adapter, which takes an arbitrary `url`, as the alternative.

The OCR NIM provider is registered as `nim_paddle_ocr`; the docs' config helper for it is `aidb.nim_ocr_config()`.

Choosing between OpenAI providers: `openai_responses` for agentic/tool-calling work (native tool calls); `openai_completions` for plain generation (simulates tool calls via prompt injection when backing an agent). `openrouter_chat` is a gateway to many vendors using OpenRouter slugs, also with simulated tool calling.

### Default models

Registered automatically at `CREATE EXTENSION`, usable with no configuration — again, confirm with `SELECT name, provider FROM aidb.models ORDER BY 1;` rather than assuming:

`bert` (bert_local), `clip` (clip_local), `t5` (t5_local), `llama` (llama_instruct_local), `bge-small-en-v1.5-f16`, `nomic-embed-text-v1.5-Q8_0`, `bge-m3-f16`, `qwen3-embedding-0.6b-Q8_0`, `qwen3-embedding-4b-Q8_0` (all llamacpp_embeddings), `qwen3.5-0.8b-Q8_0`, `llama-3.2-1b-instruct-Q8_0` (llamacpp_generate), `lightonocr-2-1b-Q8_0` (llamacpp_ocr), `dummy` (dummy).

Context windows differ meaningfully among the embedding defaults, which drives the chunk size you should pair with them:

| Default embedding model | Context window |
|---|---|
| `nomic-embed-text-v1.5-Q8_0` | 2048 |
| `bge-m3-f16` | 8192 |
| `qwen3-embedding-0.6b-Q8_0` | 16384 |
| `qwen3-embedding-4b-Q8_0` | 20480 |

## Config helpers

Each returns `JSONB` for the `config` argument of `aidb.create_model()`.

### `aidb.embeddings_config` — `openai_embeddings` and OpenAI-compatible embeddings

`model` (TEXT, required), `api_key`, `url`, `basic_auth`, `max_concurrent_requests` (INTEGER), `max_batch_size` (INTEGER), `input_type`, `input_type_query`, `tls_config` (JSONB), `is_hcp_model` (BOOLEAN).

### `aidb.completions_config` — `openai_completions` and OpenAI-compatible completions

`model` (TEXT, required), `api_key`, `url`, `basic_auth`, `system_prompt`, `temperature` (DOUBLE PRECISION), `top_p` (DOUBLE PRECISION), `seed` (BIGINT), `thinking` (BOOLEAN), `max_tokens` (JSONB — use `aidb.max_tokens_config()`), `max_concurrent_requests` (INTEGER), `extra_args` (JSONB), `is_hcp_model` (BOOLEAN).

> The `api_key` / `basic_auth` parameters exist on these helpers for backward compatibility. Leave them unset: `create_model()` rejects a config carrying them, and the handling rule above applies.

### `aidb.max_tokens_config`

`size` (INTEGER, required), `format` (TEXT — `'default'`, `'legacy'`, or `'both'`).

### `aidb.openai_responses_config` — `openai_responses`, `openai_responses_azure`

`model` (TEXT, required), `api_key`, `basic_auth`, `url`, `max_concurrent_requests` (INTEGER, default `25`), `system_prompt` (sent as the Responses API's native `instructions`), `temperature`, `max_output_tokens` (INTEGER), `top_p`, `extra_args` (JSONB).

`url` defaults to OpenAI's Responses endpoint; it is **required** for `openai_responses_azure`.

### `aidb.anthropic_messages_config` — `anthropic_messages`, `_azure`, `_bedrock`

`model` (TEXT, required), `api_key`, `basic_auth`, `url`, `max_concurrent_requests` (INTEGER, default `25`), `system_prompt` (sent as native `system`), `temperature`, `max_tokens` (INTEGER, default `4096` — Anthropic requires it), `top_p`, `extra_args` (JSONB).

`url` defaults to `api.anthropic.com`; **required** for `_azure` (resource Messages endpoint) and `_bedrock` (regional `bedrock-runtime` base URL — the model ID is appended automatically). For `_bedrock`, `api_key` is a Bedrock bearer token, not an AWS SigV4 credential.

### `aidb.bert_config` — `bert_local`

`model` (TEXT, required), `revision`, `cache_dir`.

### `aidb.clip_config` — `clip_local`

`model` (TEXT, required), `revision`, `cache_dir`, `image_size` (INTEGER).

### `aidb.llama_config` — `llama_instruct_local`

`model` (TEXT, required), `revision`, `cache_dir`, `model_path`, `system_prompt`, `temperature`, `top_p`, `seed` (BIGINT), `sample_len` (INTEGER), `repeat_penalty` (REAL), `repeat_last_n` (INTEGER), `use_flash_attention` (BOOLEAN), `use_kv_cache` (BOOLEAN).

### `aidb.t5_config` — `t5_local`

`model` (TEXT, required), `revision`, `model_path`, `cache_dir`, `temperature`, `top_p`, `seed` (BIGINT), `max_tokens` (INTEGER), `repeat_penalty` (REAL), `repeat_last_n` (INTEGER).

### `aidb.gemini_config` — `gemini`

`api_key` (TEXT, required), `model`, `url`, `max_concurrent_requests` (INTEGER), `thinking_budget` (INTEGER, Gemini 2.x only). See the handling rule under "Leaked credentials" before using `api_key`.

### `aidb.nim_clip_config`, `aidb.nim_ocr_config`, `aidb.nim_reranking_config`

All take the same shape: `api_key`, `model`, `url`, `basic_auth`, `is_hcp_model` (BOOLEAN). All optional.

### `aidb.openrouter_chat_config` — `openrouter_chat`

`model` (TEXT, required), `api_key`, `url`, `max_concurrent_requests` (INTEGER), `max_tokens` (JSONB — use `aidb.max_tokens_config()`).

### `aidb.openrouter_embeddings_config` — `openrouter_embeddings`

`model` (TEXT, required), `api_key`, `url`, `max_concurrent_requests` (INTEGER), `max_batch_size` (INTEGER).

The llama.cpp providers have no dedicated helper — build their `config` with `jsonb_build_object()`.
