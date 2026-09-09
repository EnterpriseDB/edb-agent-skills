# AIDB Model Adapters Reference

Models are registered once with `aidb.create_model()` and then referenced **by name**
everywhere else (pipeline step options, standalone functions, Semantic KBs).

```sql
aidb.create_model(
    name, provider,
    config              => '{}'::jsonb,   -- cleartext; NO api_key / basic_auth allowed
    credentials         => '{}'::jsonb,   -- stored in pg_user_mappings
    replace_credentials => false,
    validate            => true,
    credentials_env     => NULL           -- name of an env var, read at each use
);
```

> **Rule zero — never assume a provider exists.** The set of adapters compiled into a build
> varies. Always enumerate first; `create_model` fails with
> `Model provider with name "X" not found` otherwise.

```sql
SELECT server_name, server_description FROM aidb.model_providers ORDER BY 1;
```

The tables below name the adapter families present in the `EnterpriseDB/aidb` source tree
(`aidb-model/src/adapter/`). Treat them as *candidates to look for* in the output of the query
above, not as a guarantee.

---

## Credential handling (read this before wiring any remote provider)

* `config` is stored in cleartext FDW options. `create_model` **raises** if `config` contains
  `api_key` or `basic_auth` at any nesting depth. Pass those through `credentials` instead.
* Credentials attach to the **provider server**, shared by all models on that provider. To
  change them: `replace_credentials => true`. Note the corollary: `aidb.delete_model()` drops
  the user mapping, which can break *other* models on the same provider.
* `credentials_env => 'MY_API_KEY'` stores only the variable *name* and reads the value fresh
  at each model use — the right choice for rotated secrets. Mutually exclusive with
  `credentials`.
* `aidb.list_models()` / `aidb.get_model()` never return credential values.
* `aidb.audit_leaked_credentials()` lists models whose config was found to hold a secret.
* Never echo a user's API key back into the transcript. Prefer
  `current_setting('my.key')` or `credentials_env`.

---

## Local models (no external API, no key)

Weights are loaded on the database host. Throughput is bounded by `aidb.max_threads`
(restart required to change). First use may download weights unless a local path/cache is set.

| Provider family | Capability | Config helper |
|---|---|---|
| `bert_local` | text embeddings | `aidb.bert_config` |
| `clip_local` | text + image embeddings (cross-modal search) | `aidb.clip_config` |
| `t5_local` | text embeddings **and** completions | `aidb.t5_config` |
| `llama_instruct_local` | text completions | `aidb.llama_config` |
| `llamacpp_embeddings` | GGUF embeddings via llama.cpp | raw JSONB |
| `llamacpp_generate` | GGUF completions via llama.cpp | raw JSONB |
| `llamacpp_reranking` | GGUF cross-encoder reranking | raw JSONB |
| `llamacpp_ocr` | GGUF vision OCR | raw JSONB |

```sql
SELECT aidb.create_model('my_bert', 'bert_local');

SELECT aidb.create_model(
    'bge_small', 'llamacpp_embeddings',
    '{"model":"unsloth/bge-small-en-v1.5-GGUF",
      "model_file":"bge-small-en-v1.5-f16.gguf",
      "n_ctx":512}'::jsonb,
    validate => false);
```

Local providers are the Sovereign-AI default: no data leaves the database host.

---

## OpenAI-compatible remote APIs

Point at OpenAI, a self-hosted vLLM/TGI, NVIDIA NIM, or anything speaking the same protocol.

| Provider family | Capability |
|---|---|
| `openai_embeddings` | `/v1/embeddings` |
| `openai_completions` | `/v1/chat/completions` |
| `embeddings` | generic OpenAI-compatible embeddings (`url` required) |
| `completions` | generic OpenAI-compatible completions (`url` required) |
| `nim_embeddings` / `nim_completions` | NVIDIA NIM text |
| `nim_clip` | NVIDIA NIM image embeddings |
| `nim_paddle_ocr` | NVIDIA NIM OCR |
| `nim_reranking` | NVIDIA NIM reranking |

```sql
SELECT aidb.create_model(
    'openai_embed', 'openai_embeddings',
    config      => aidb.embeddings_config(model => 'text-embedding-3-small'),
    credentials => jsonb_build_object('api_key', current_setting('my.openai_key'))
);

SELECT aidb.create_model(
    'local_llm', 'completions',
    config => aidb.completions_config(
        model => 'mistral-7b',
        url   => 'http://vllm-host:8000/v1/chat/completions')
);
```

---

## Hosted third-party services

| Provider family | Capability | Config helper |
|---|---|---|
| `gemini` | text completions | `aidb.gemini_config` |
| `hf_tei` | HuggingFace TEI embeddings | `aidb.embeddings_config` |
| `hf_tei_reranking` | HuggingFace TEI reranking | `aidb.embeddings_config` |
| `openrouter_chat` | routed LLM completions | `aidb.openrouter_chat_config` |
| `openrouter_embeddings` | routed embeddings | `aidb.openrouter_embeddings_config` |

Additional tool-calling adapters (`openai_responses`, `anthropic_messages`, and their
Azure/Bedrock variants) exist in the source tree. They serve build-dependent surfaces outside
the five core AIDB capability areas — see Appendix A of `references/function-reference.md`
before offering them.

---

## Testing provider

| Provider | Capability |
|---|---|
| `dummy` | deterministic embeddings + completions; no network |

`dummy` is the right choice for regression/CI/demo scenarios and for proving a pipeline is
wired correctly without credentials. It can be scripted:

```sql
SELECT aidb.create_model('test_model', 'dummy');
SELECT aidb.create_model('scripted', 'dummy',
    '{"responses":[{"response":"hi"}]}'::jsonb, validate => false);
```

Note: with `dummy`, similarity scores tie — use it to prove wiring, not ranking.

---

## Capability → consumer map

| Capability | Consumed by |
|---|---|
| Text embeddings | `KnowledgeBase` step (`data_format => 'Text'`), `encode_text*`, Semantic KB, semantic aliases |
| Image embeddings | `KnowledgeBase` step (`'Image'`), `encode_image`, cross-modal `retrieve_text(kb, bytes)` |
| Text completions | `SummarizeText` step, `generate_text*` |
| OCR | `PerformOcr` step, `aidb.perform_ocr` |
| Reranking | `aidb.rerank_text` |

Using a model that lacks the required adapter fails **at pipeline creation time**, e.g.
`... does not support text embedding operations` or
`The requested adapter is not supported by the model provider: <provider>`.

---

## Choosing a model: decision guide

1. **Must data stay on-premises?** → local provider (`bert_local`, `clip_local`,
   `llamacpp_*`). No credentials needed; budget CPU via `aidb.max_threads`.
2. **Cross-modal (search images with text)?** → `clip_local` or `nim_clip`, and set
   `data_format => 'Image'` on the KB step.
3. **High throughput, remote API acceptable?** → an OpenAI-compatible provider plus a larger
   pipeline `batch_size` and `max_batch_size` / `max_concurrent_requests`.
4. **Just proving the wiring?** → `dummy`.
5. Once a KB exists, **do not swap its embedding model.** Dimensions are baked into the
   destination `value VECTOR(n)` column; attaching a differently-sized model raises
   `Embedding dimension mismatch: ...`. Build a new KB instead.

---

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `config must not contain "api_key" or "basic_auth"` | secret in `config` | move it to `credentials` |
| `Credentials for model provider "X" already exist` | server-level mapping already set | `replace_credentials => true`, or omit credentials to reuse |
| `Model provider with name "X" not found` | provider not in this build | `SELECT * FROM aidb.model_providers;` |
| `Failed to create model: ...` + hint about `validate` | probe failed (bad key/url/model id) | fix config, or `validate => false` to register anyway and `aidb.validate_model()` later |
| `Model not found: X` at run time | model deleted after the pipeline was created | recreate the model with the same name, or recreate the pipeline |
| Local model OOM / very slow | thread pool or RAM | tune `aidb.max_threads` (restart), check host memory |
