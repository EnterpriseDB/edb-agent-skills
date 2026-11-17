# AIDB Quick-Decision Flowchart

Use this reference when a user's request maps to more than one AIDB feature. Follow the decision tree to pick the right approach, then see the linked references for exact SQL.

---

## Decision 1: What is the user trying to do?

```
User request
│
├─► "Search my documents / rows by meaning (RAG, semantic search)"
│       → KNOWLEDGE BASE PIPELINE: ChunkText → KnowledgeBase, queried with aidb.retrieve_text  (see Decision 2)
│
├─► "Process/transform data automatically as it arrives or in bulk"
│       → PIPELINE  (see Decision 2)
│
├─► "Search my schema by natural language / find relevant tables & columns"
│       → SEMANTIC KNOWLEDGE BASE  (see Decision 3)
│
├─► "Build an AI assistant that uses tools / talks to my database"
│       → AGENT HUB  (see Decision 4)
│
├─► "Run a one-off AI operation (embed text, summarize, OCR, chunk)"
│       → STANDALONE FUNCTIONS  (see Decision 5)
│
├─► "Expose AIDB tools to an external AI client (Claude, etc.)"
│       → MCP ENDPOINT  (see references/mcp-endpoint-reference.md)
│
└─► "Control what an agent is allowed to do at the database role level"
        → PURPOSE REGISTRY  (see references/governance-and-security.md)
```

---

## Decision 2: Pipeline Flavors

```
User wants a pipeline
│
├─► Source is a database table → use table name as `source`
│
├─► Source is object storage (S3/Azure/GCS/local) → create a Volume first:
│       SELECT aidb.create_volume(...); then use volume name as `source`
│
├─► What auto-processing mode?
│   ├─► Instant reaction to INSERT/UPDATE/DELETE → 'Live'
│   │       (adds row triggers; runs synchronously in the writer's transaction)
│   │       Table sources and table destinations only: rejected for a volume source,
│   │       a volume destination, and on WarehousePG (WHPG)
│   ├─► Non-blocking batch pickup → 'Background'
│   │       (requires aidb in shared_preload_libraries; restart needed)
│   │       On a table source it adds a change-capture trigger; that combination is
│   │       rejected on WarehousePG (WHPG). Volume sources need no trigger.
│   └─► Manual control → 'Disabled'
│           (call SELECT aidb.run_pipeline('name') when ready)
│   Live and Background both need the pipeline's owner role to still exist.
│
└─► What steps?
    ├─► Text → searchable embeddings: ChunkText → KnowledgeBase
    ├─► PDF → searchable text: ParsePdf → ChunkText → KnowledgeBase
    ├─► PDF → Image → OCR text: PdfToImage → PerformOcr (→ ChunkText → KnowledgeBase)
    ├─► PDF pages as images → embeddings: PdfToImage → KnowledgeBase (data_format 'Image')
    ├─► HTML (TEXT column) → Text: ParseHtml → ChunkText
    └─► Text → Summary: SummarizeText (uses an LLM model)
```

**Document search:** build a `ChunkText` + `KnowledgeBase` pipeline. `KnowledgeBase` is the supported step for embedding content and keeping it in sync: as the last step it registers a knowledge base, creates the `VECTOR(<dims>)` destination table (default `<source_schema>.pipeline_<name>`) and builds a vector index. Search it with `aidb.retrieve_text('<schema>.<table>', query, topk)`, where the first argument is the schema-qualified destination table:

```sql
SELECT key, value, distance
FROM aidb.retrieve_text('public.pipeline_docs_kb', 'refund policy', 5);
```

An unqualified table name is not found. Passing the pipeline name still works but emits a WARNING asking for the `'<schema>.<table>'` identifier. See `references/step-operations.md` for the full pipeline SQL and destination table columns.

---

## Decision 3: Document Search vs. Schema Search

```
User wants natural language search
│
├─► Over document or row CONTENTS (manuals, tickets, product text)
│   └─► ChunkText → KnowledgeBase pipeline + aidb.retrieve_text('<schema>.<table>', ...)
│
└─► Over SCHEMA METADATA (which tables and columns hold what)
    └─► aidb.create_semantic_kb()
        ├─► Indexes tables, views, columns and comments; it does not embed row contents
        ├─► Search with aidb.semantic_kb_search(query_text, ...) (hybrid, a good first call),
        │   aidb.get_entity_definitions(...) for tables and views, aidb.get_metadata(...),
        │   aidb.get_column_definitions(...), aidb.search_by_comment(...)
        └─► Enables Semantic Aliases for agent-discoverable parameterized queries
```

**Similarity thresholds:** the semantic KB search functions take `min_similarity`, which defaults to NULL (no cutoff) and is compared against the returned `similarity` column. Good cutoffs depend on the embedding model, so run a search without a threshold first, look at the `similarity` values, and pick a cutoff from those.

**Relationship / join routing:**
- Only routes through **explicitly approved** relationships
- Never use a candidate/unreviewed relationship for routing
- Functions: `aidb.suggest_joins`, `aidb.find_join_path`, `aidb.semantic_kb_subgraph`

---

## Decision 4: Agent Hub Components

```
User wants an agent
│
├─► What tools should the agent have? (pass names in create_agent(..., tools => ARRAY[...]))
│   ├─► Database queries → aidb.create_sql_tool()
│   ├─► External services → aidb.import_mcp_tools()
│   └─► Built-in native tools (catalog discovery and more) → listed in the aidb.tools view;
│       name the ones you want in `tools`. Only the `sleep` tool is added automatically
│
├─► Should the agent be read-only?
│   └─► Pass read_only => true to agent_converse()
│       (3-layer enforcement: SQL tools classified by command tag, tool allowlist
│        filtered to read-only tools, SET LOCAL transaction_read_only around SQL tool calls)
│
├─► Does the agent need persistent memory?
│   ├─► In-conversation: default action_log (no config needed)
│   └─► Cross-session memory: SET aidb.agent_session_source = 'memory' (superuser)
│       The default namespace pg_agents exists after install; create others with
│       aidb_memory.init(name, config). Providers: 'mock' (testing) and
│       'agent_memory_container' (external Agent Memory container). The background
│       dispatcher runs only with aidb.agent_memory_enable_worker = on (default off).
│       Not available on WarehousePG (WHPG) builds.
│
├─► Should the agent be constrained to a specific database role?
│   └─► aidb.create_purpose() (needs the aidb_governance role), then
│       create_agent(..., purpose => 'name')
│       (role switches are audited via OTel purpose_decision spans)
│
└─► Structured output from agent?
    └─► Use aidb.output_type(aidb.output_field(...), ...) as output_type argument
```

**Error convention:** `aidb.agent_converse()` reports failures in its `error` column instead of raising, so it does not abort the caller's transaction. Always check the `error` column:
```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'hello');
```

---

## Decision 5: Standalone Function Selection

Every model function takes the model name first. Embeddings come back as `real[]`; cast with `::vector` before using pgvector operators or storing into a `vector` column, and embed a search query once in a CTE rather than inside `ORDER BY`. Functions marked `options JSON` take `json`: the `aidb.*_config()` helpers return `jsonb`, so append `::json` when passing one.

| Task | Function | Notes |
|---|---|---|
| Convert text to vector | `aidb.encode_text(model_name, input)` | Returns `real[]`. Storage-side embedding |
| Convert query text to vector | `aidb.encode_text_query(model_name, input)` | Returns `real[]`. Query side for bi-encoders |
| Batch embed texts | `aidb.encode_text_batch(model_name, input text[])` | Returns `SETOF real[]`, one row per input |
| Embed an image | `aidb.encode_image(model_name, input bytea)` | Returns `real[]` |
| Generate/complete text | `aidb.generate_text(model_name, input, inference_config json DEFAULT NULL)` | Preferred over deprecated decode_text |
| Generate text (batch) | `aidb.generate_text_batch(model_name, input text[], inference_config json DEFAULT NULL)` | Returns `SETOF text`. Preferred over deprecated decode_text_batch |
| Rerank candidates | `aidb.rerank_text(model_name, query, input text[])` | Returns `TABLE(text, logit_score, id)` in input order; add `ORDER BY logit_score DESC` to rank |
| Split text into chunks | `aidb.chunk_text(input, options json)` | Returns `TABLE(part_id BIGINT, chunk TEXT)` |
| Summarize text | `aidb.summarize_text(input, options json)` | Returns `TEXT`. Model goes in `aidb.summarize_text_config(...)::json` |
| Extract text from PDF | `aidb.parse_pdf(bytes bytea, options json)` | Returns `TABLE(part_id BIGINT, text TEXT)`, one row per page |
| Render PDF pages | `aidb.pdf_to_image(bytes bytea, options json)` | Returns `TABLE(part_id BIGINT, image BYTEA)` |
| Extract text from HTML | `aidb.parse_html(html text, options json)` | Takes TEXT, returns `TEXT` |
| OCR an image | `aidb.perform_ocr(input bytea, options json)` | Returns `TABLE(part_id BIGINT, text TEXT)`. Model goes in options: `aidb.ocr_config('my_ocr_model')::json`; needs an OCR-capable model such as `nim_paddle_ocr` or `llamacpp_ocr` |

```sql
WITH q AS (SELECT aidb.encode_text_query('my_embedder', 'refund policy')::vector AS v)
SELECT d.id, d.body
FROM docs d, q
ORDER BY d.embedding <=> q.v
LIMIT 5;
```

**Deprecated (avoid in new code):**
- `aidb.decode_text` → use `aidb.generate_text`
- `aidb.decode_text_batch` → use `aidb.generate_text_batch`
- `aidb.get_hcp_models` → use `aidb.list_hcp_models`

---

## Decision 6: Which Model Provider?

```
Choosing a model provider
│
├─► Testing / CI / no external dependency → 'dummy'
│   (returns fake data, no server needed)
│
├─► Local inference in the database process, no API key needed
│   ├─► BERT-style embeddings → 'bert_local'
│   ├─► CLIP image+text → 'clip_local'
│   ├─► T5 → 't5_local'
│   ├─► Llama instruction-tuned → 'llama_instruct_local'
│   └─► GGUF models via llama.cpp → 'llamacpp_generate' / 'llamacpp_embeddings' /
│       'llamacpp_reranking' / 'llamacpp_ocr'
│
├─► OpenAI API → 'openai_embeddings' / 'openai_completions' / 'openai_responses' (also: openai_responses_azure)
├─► NVIDIA NIM → 'nim_embeddings' / 'nim_completions' / 'nim_clip' / 'nim_reranking' / 'nim_paddle_ocr'
├─► Anthropic → 'anthropic_messages' (also: anthropic_messages_azure, anthropic_messages_bedrock)
├─► Google Gemini → 'gemini'
├─► HuggingFace TEI → 'hf_tei' / 'hf_tei_reranking'
├─► Any OpenAI-compatible (vLLM, etc.) → 'completions' / 'embeddings'
├─► OpenRouter → 'openrouter_chat' / 'openrouter_embeddings'
└─► EDB Hybrid Manager auto-discovery → aidb.sync_hcp_models()
```

See `references/model-adapters.md` for complete provider list with example SQL.

---

## Key Safety Constraints (Always Enforce)

1. **Pipeline name ≤ 46 characters**: creation fails if exceeded
2. **Max 10 steps per pipeline**: creation fails if exceeded
3. **Destination table must not already exist**: creation fails if it does. The one exception is a pipeline whose last step is KnowledgeBase, which may attach to an existing knowledge base table
4. **Step sequence must be type-compatible**: rejected at creation time (see `references/step-operations.md`)
5. **'Live' auto-processing needs a table source and table destination**: rejected for volume sources, volume destinations and on WarehousePG (WHPG)
6. **`agent_converse` reports failures in its `error` column**: always check it
7. **Document search uses a KnowledgeBase pipeline**: query it with `aidb.retrieve_text('<schema>.<table>', ...)`; `create_semantic_kb()` is for schema-metadata search only
8. **`decode_text`/`decode_text_batch` are deprecated**: always use `generate_text`/`generate_text_batch`
9. **Unreviewed KB relationships are never safe for join routing**: only approved relationships route
10. **Agent delegation cannot escalate privileges**: a delegate always inherits the parent's read_only flag, and inherits the parent's role and purpose when the parent runs under a purpose; otherwise it resolves its own purpose
