# AIDB Models Reference

Signature reference for model registration, inference, and config helpers. AIDB 7.6.0.

A model is registered once by name, then referenced by that name everywhere else (pipeline steps, standalone functions, knowledge bases, agents).

## Discovery queries — run these before advising

The installed extension is the authority; the lists further down are the stock defaults only.

```sql
-- Which providers exist here? Only offer providers returned by this query.
SELECT server_name, server_description FROM aidb.model_providers ORDER BY server_name;

-- Is one specific provider available? (e.g. before offering HuggingFace TEI)
SELECT EXISTS (SELECT 1 FROM aidb.model_providers WHERE server_name = 'hf_tei') AS available;

-- What models are already registered, and what can each of them do?
SELECT name, provider, functions FROM aidb.models ORDER BY provider, name;

-- Does a model registration embed credentials in `config`? (security audit)
SELECT * FROM aidb.audit_leaked_credentials();
```

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
| `credentials_env` | TEXT | NULL | Name of an environment variable to read credentials from |

`credentials`, `credentials_env`, and (where present, see below) `credentials_k8s_secret` are **mutually exclusive** — supplying more than one raises.

**Credentials are per provider, not per model.** They are stored as a `PUBLIC` user mapping on the provider's foreign server, so a second model on the same provider reuses them. Registering a second model with *different* credentials for the same provider fails unless you pass `replace_credentials => true`, which overwrites them for every model on that provider.

**Validation.** With `validate => true`, a minimal probe inference runs before the registration commits — local models are downloaded and loaded; external models get a small test request. On failure nothing is registered. Use `validate => false` to defer a large download or register ahead of credentials being available, then `aidb.validate_model()` later.

**`credentials_env`.** Only the variable *name* is stored; the value is read from the Postgres backend process environment at each use and never persisted. The variable may hold a bare secret (used as `api_key`) or a JSON object (e.g. `{"basic_auth": "..."}`). Its name must start with the prefix in the `aidb.env_var_allowed_prefix` GUC (`AIDB_` by default) — this prevents a model config from naming an arbitrary host environment variable and exfiltrating it. The same mechanism backs MCP `headers_env`.

**`credentials_k8s_secret`.** Newer builds add a third, equally non-persisting option: an absolute path into a mounted Kubernetes Secret volume, read fresh at each use (a single file becomes `api_key`; a directory with `username`/`password` files becomes base64 `basic_auth`; a JSON object is used as-is). It is gated by an allowed-path-prefix GUC. **Confirm it exists on the target before offering it** — `\df aidb.create_model` or `SELECT pg_get_function_arguments(oid) FROM pg_proc WHERE proname = 'create_model';`.

### Credentials must never live in `config`

`config` is stored in cleartext as a foreign-table option; `credentials` lives behind a user mapping. `aidb.create_model()` therefore **rejects any `config` containing an `api_key` or `basic_auth` key at any nesting depth**, for every provider without exception:

```
ERROR: config must not contain "api_key" or "basic_auth" -- pass them via the
credentials, credentials_env, or credentials_k8s_secret argument instead
```

Consequences to know:

- Every config helper that has an `api_key` / `basic_auth` parameter (`embeddings_config`, `completions_config`, `openai_responses_config`, `anthropic_messages_config`, `nim_*_config`, `openrouter_*_config`, `gemini_config`, …) still *accepts* one, but the resulting JSONB is then unusable as `create_model`'s `config`. **Always leave those parameters NULL** (`jsonb_strip_nulls` drops them) and pass the secret through `credentials_env`.
- **Provider quirk — `gemini`:** `aidb.gemini_config()` declares `api_key` as its first, *required* parameter, a leftover from when Gemini kept its key in `config`. Pass `api_key => NULL` explicitly and supply the key via `credentials_env` (or `credentials`) like every other provider.
- `aidb.audit_leaked_credentials()` reports models registered *before* the check existed, whose `config` still embeds a secret. Treat a non-empty result as a finding: re-register those models with `credentials_env` and rotate the key.

### `aidb.audit_leaked_credentials`

No arguments. Returns `TABLE(model_name TEXT, detected_key TEXT, detected_at TIMESTAMPTZ)`.

**TLS.** Add a `tls_config` object inside `config`: `{"insecure_skip_verify": bool, "ca_path": "/path/to/ca.pem"}`.

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

**Always confirm against `SELECT server_name FROM aidb.model_providers` on the target installation before offering any of these.** The set of registered foreign servers is a property of the installed build; do not assume a count or that a given name is present.

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

### Remote

| Family | Providers |
|---|---|
| Generic OpenAI-compatible | `embeddings`, `completions` |
| OpenAI | `openai_embeddings`, `openai_completions`, `openai_responses`, `openai_responses_azure` |
| Anthropic | `anthropic_messages`, `anthropic_messages_azure`, `anthropic_messages_bedrock` |
| NVIDIA NIM | `nim_embeddings`, `nim_completions`, `nim_clip`, `nim_reranking`, `nim_paddle_ocr` |
| Google | `gemini` |
| OpenRouter | `openrouter_chat`, `openrouter_embeddings` |
| HuggingFace TEI | `hf_tei` (embeddings), `hf_tei_reranking` (reranking) — **build-dependent, see below** |

`embeddings` and `completions` are the generic OpenAI-compatible adapters; `openai_*` and `nim_*` are preconfigured specializations of the same adapters.

#### HuggingFace TEI (`hf_tei`, `hf_tei_reranking`)

For models served by HuggingFace's Text Embeddings Inference server. The adapters ship in the extension, and `hf_tei_reranking` is referenced from the hybrid-search documentation, **but on some builds no foreign server is registered for them**, in which case `aidb.create_model()` fails with `Model provider with name "hf_tei" not found`.

Rule: run the availability check above and only offer TEI if the server is present. If it is absent, say so plainly and offer `llamacpp_embeddings` / `embeddings` (a TEI endpoint that speaks the OpenAI embeddings API) or `nim_reranking` / `llamacpp_reranking` as alternatives.

There is no config helper for TEI — build `config` with `jsonb_build_object()`. Recognized keys and their defaults: `url` (`http://localhost:8080`; the adapter appends `/embed`), `normalize` (`true`), `truncate` (`true`), `truncation_direction` (`Right` or `Left`), `max_concurrent_requests` (`25`), `max_batch_size` (`32`), `tls_config`. Authentication is sent as a bearer/basic header from `credentials`/`credentials_env` — never from `config`.

#### Choosing between overlapping providers

- `openai_responses` for agentic/tool-calling work (native tool calls); `openai_completions` for plain generation (simulates tool calls via prompt injection when backing an agent).
- `openrouter_chat` is a gateway to many vendors using OpenRouter slugs, also with simulated tool calling.
- The OCR NIM provider is registered as `nim_paddle_ocr`; its config helper is `aidb.nim_ocr_config()`.

### Default models

Registered automatically at `CREATE EXTENSION`, usable with no configuration. Model files are downloaded on first use (or at `validate` time), so the first call can take minutes.

| Model | Provider | Notes |
|---|---|---|
| `bert` | `bert_local` | Text embeddings |
| `clip` | `clip_local` | Text + image embeddings — the default choice for image KBs |
| `t5` | `t5_local` | Text-to-text; **cannot back an agent** |
| `llama` | `llama_instruct_local` | Instruction following |
| `bge-small-en-v1.5-f16` | `llamacpp_embeddings` | Small/fast; context 512 |
| `nomic-embed-text-v1.5-Q8_0` | `llamacpp_embeddings` | Context 2048 |
| `bge-m3-f16` | `llamacpp_embeddings` | Multilingual; context 8192 |
| `qwen3-embedding-0.6b-Q8_0` | `llamacpp_embeddings` | Context 16384 |
| `qwen3-embedding-4b-Q8_0` | `llamacpp_embeddings` | Context 20480; largest, slowest |
| `qwen3.5-0.8b-Q8_0` | `llamacpp_generate` | Generation; supports native tool calls |
| `llama-3.2-1b-instruct-Q8_0` | `llamacpp_generate` | Generation; supports native tool calls |
| `lightonocr-2-1b-Q8_0` | `llamacpp_ocr` | Local OCR |
| `dummy` | `dummy` | Deterministic fake output for testing a pipeline's plumbing |

Default-model selection guidance: `bge-small-en-v1.5-f16` for short English text (fastest), `bge-m3-f16` for multilingual or longer chunks, `qwen3-embedding-*` when chunks are large. Chunk sizes larger than the model's context window are silently truncated by the server — size `chunk_text` to fit.

## Config helpers

Each returns `JSONB` for the `config` argument of `aidb.create_model()`. Leave every `api_key`/`basic_auth` parameter NULL (see the credentials rule above).

### `aidb.embeddings_config` — `openai_embeddings` and OpenAI-compatible embeddings

`model` (TEXT, required), `api_key`, `url`, `basic_auth`, `max_concurrent_requests` (INTEGER), `max_batch_size` (INTEGER), `input_type`, `input_type_query`, `tls_config` (JSONB), `is_hcp_model` (BOOLEAN).

### `aidb.completions_config` — `openai_completions` and OpenAI-compatible completions

`model` (TEXT, required), `api_key`, `url`, `basic_auth`, `system_prompt`, `temperature` (DOUBLE PRECISION), `top_p` (DOUBLE PRECISION), `seed` (BIGINT), `thinking` (BOOLEAN), `max_tokens` (JSONB — use `aidb.max_tokens_config()`), `max_concurrent_requests` (INTEGER), `extra_args` (JSONB), `is_hcp_model` (BOOLEAN).

### `aidb.max_tokens_config`

`size` (INTEGER, required), `format` (TEXT — `'default'`, `'legacy'`, or `'both'`).

### `aidb.openai_responses_config` — `openai_responses`, `openai_responses_azure`

`model` (TEXT, required), `api_key`, `basic_auth`, `url`, `max_concurrent_requests` (INTEGER, default `25`), `system_prompt` (sent as the Responses API's native `instructions`), `temperature`, `max_output_tokens` (INTEGER), `top_p`, `extra_args` (JSONB).

`url` defaults to OpenAI's Responses endpoint; it is **required** for `openai_responses_azure`.

### `aidb.anthropic_messages_config` — `anthropic_messages`, `_azure`, `_bedrock`

`model` (TEXT, required), `api_key`, `basic_auth`, `url`, `max_concurrent_requests` (INTEGER, default `25`), `system_prompt` (sent as native `system`), `temperature`, `max_tokens` (INTEGER, default `4096` — Anthropic requires it), `top_p`, `extra_args` (JSONB).

`url` defaults to `api.anthropic.com`; **required** for `_azure` (resource Messages endpoint) and `_bedrock` (regional `bedrock-runtime` base URL — the model ID is appended automatically). For `_bedrock`, the credential is a Bedrock bearer token, not an AWS SigV4 credential.

### `aidb.bert_config` — `bert_local`

`model` (TEXT, required), `revision`, `cache_dir`.

### `aidb.clip_config` — `clip_local`

`model` (TEXT, required), `revision`, `cache_dir`, `image_size` (INTEGER).

### `aidb.llama_config` — `llama_instruct_local`

`model` (TEXT, required), `revision`, `cache_dir`, `model_path`, `system_prompt`, `temperature`, `top_p`, `seed` (BIGINT), `sample_len` (INTEGER), `repeat_penalty` (REAL), `repeat_last_n` (INTEGER), `use_flash_attention` (BOOLEAN), `use_kv_cache` (BOOLEAN).

### `aidb.t5_config` — `t5_local`

`model` (TEXT, required), `revision`, `model_path`, `cache_dir`, `temperature`, `top_p`, `seed` (BIGINT), `max_tokens` (INTEGER), `repeat_penalty` (REAL), `repeat_last_n` (INTEGER).

### `aidb.gemini_config` — `gemini`

`api_key` (TEXT, **required parameter — pass NULL**), `model`, `url`, `max_concurrent_requests` (INTEGER), `thinking_budget` (INTEGER, Gemini 2.x only).

```sql
SELECT aidb.create_model(
    'gemini_flash',
    'gemini',
    config          => aidb.gemini_config(api_key => NULL, model => 'gemini-2.0-flash'),
    credentials_env => 'AIDB_GEMINI_API_KEY');
```

### `aidb.nim_clip_config`, `aidb.nim_ocr_config`, `aidb.nim_reranking_config`

All take the same shape: `api_key`, `model`, `url`, `basic_auth`, `is_hcp_model` (BOOLEAN). All optional.

### `aidb.openrouter_chat_config` — `openrouter_chat`

`model` (TEXT, required), `api_key`, `url`, `max_concurrent_requests` (INTEGER), `max_tokens` (JSONB — use `aidb.max_tokens_config()`).

### `aidb.openrouter_embeddings_config` — `openrouter_embeddings`

`model` (TEXT, required), `api_key`, `url`, `max_concurrent_requests` (INTEGER), `max_batch_size` (INTEGER).

The llama.cpp and TEI providers have no dedicated helper — build their `config` with `jsonb_build_object()`. Keys used by the pre-registered llama.cpp models: `hf_model`, `model_file`, `mmproj_file` (OCR), `revision`, `n_ctx`, `query_prefix`, `document_prefix`, `temperature`, `top_p`.

## Worked examples

```sql
-- Remote generation model, secret never stored:
--   export AIDB_OPENAI_API_KEY=sk-...   (in the Postgres server environment)
SELECT aidb.create_model(
    'gpt_4o',
    'openai_responses',
    config          => aidb.openai_responses_config(model => 'gpt-4o'),
    credentials_env => 'AIDB_OPENAI_API_KEY');

-- Fully local embedding model, nothing leaves the database:
SELECT aidb.create_model(
    'local_embed',
    'llamacpp_embeddings',
    config => jsonb_build_object(
        'hf_model',   'CompendiumLabs/bge-m3-gguf',
        'model_file', 'bge-m3-f16.gguf',
        'n_ctx',      8192),
    validate => false);   -- defer the model download
SELECT aidb.validate_model('local_embed');
```

## Common errors

| Message | Cause / fix |
|---|---|
| `Model provider with name "X" not found` | Not registered on this build. Re-check `aidb.model_providers` |
| `config must not contain "api_key" or "basic_auth"` | Move the secret to `credentials_env` / `credentials` |
| `Credentials for model provider "X" already exist` | Credentials are per provider; re-use them (omit the argument) or pass `replace_credentials => true` |
| `Only one of "credentials", "credentials_env", ... may be provided` | They are mutually exclusive |
| `... does not start with the required prefix` | `credentials_env` name must start with `aidb.env_var_allowed_prefix` (`AIDB_`) |
| `Failed to create model: ...` with a `validate => false` hint | The probe inference failed — bad URL, key, or model name |
