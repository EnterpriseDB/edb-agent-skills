# AIDB Quick-Decision Flowchart

Use this reference when a user's request maps to more than one AIDB feature. Follow the decision tree to pick the right approach, then see the linked references for exact SQL.

---

## Decision 1: What is the user trying to do?

```
User request
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
│   ├─► Instant reaction to INSERT/UPDATE → 'Live'
│   │       (adds triggers; synchronous; blocks writer until done)
│   ├─► Non-blocking batch pickup → 'Background'
│   │       (requires aidb in shared_preload_libraries; restart needed)
│   └─► Manual control → 'Disabled'
│           (call SELECT aidb.run_pipeline('name') when ready)
│
└─► What steps?
    ├─► Text → Embeddings: ChunkText → KnowledgeBase (deprecated but functional)
    │   Preferred alternative: ChunkText in pipeline + aidb.encode_text() standalone
    ├─► PDF → Text: ParsePdf → ChunkText → (embed with standalone functions)
    ├─► PDF → Image → OCR text: PdfToImage → PerformOcr
    ├─► HTML → Text: ParseHtml → ChunkText
    └─► Text → Summary: SummarizeText (uses an LLM model)
```

**IMPORTANT — KnowledgeBase step is deprecated.** For new embedding pipelines, prefer `ChunkText` pipeline steps combined with standalone `aidb.encode_text()` / `aidb.encode_text_batch()` calls. The `KnowledgeBase` step still works but emits deprecation warnings.

---

## Decision 3: Semantic KB vs. Standalone Embedding

```
User wants natural language schema discovery
│
├─► NEW use case → aidb.create_semantic_kb()
│   ├─► Searches tables/columns/comments by natural language
│   ├─► Returns similarity-ranked results via get_tables(), get_columns(), etc.
│   └─► Enables Semantic Aliases for agent-discoverable parameterized queries
│
└─► Legacy KnowledgeBase pipeline step (already exists) → warn about deprecation
    and offer migration path to create_semantic_kb()
```

**Similarity threshold guidance (applies to all KB search functions):**
- `0.9+` → near-exact match only
- `0.8` → good default for production semantic search
- `0.5–0.7` → broad exploration

**Relationship / join routing (newer):**
- Only routes through **explicitly approved** relationships
- Never use a candidate/unreviewed relationship for routing
- Functions: `aidb.suggest_joins`, `aidb.find_join_path`, `aidb.semantic_kb_subgraph`

---

## Decision 4: Agent Hub Components

```
User wants an agent
│
├─► What tools should the agent have?
│   ├─► Database queries → aidb.create_sql_tool()
│   ├─► External services → aidb.import_mcp_tools()
│   └─► Built-in catalog discovery → always available as native tools
│
├─► Should the agent be read-only?
│   └─► Pass read_only => true to agent_converse()
│       (3-layer enforcement: command tags, tool allowlist, SET LOCAL transaction_read_only)
│
├─► Does the agent need persistent memory?
│   ├─► In-conversation: default action_log (no config needed)
│   └─► Cross-session semantic memory: aidb_memory.init() + SET aidb.agent_session_source = 'memory'
│       NOTE: Only 'mock' provider is implemented today — no real memory distillation yet
│
├─► Should the agent be constrained to a specific database role?
│   └─► aidb.create_purpose() + assign purpose to agent
│       (role switches are audited via OTel purpose_decision spans)
│
└─► Structured output from agent?
    └─► Use aidb.output_type(aidb.output_field(...), ...) as output_type argument
```

**Error convention:** `aidb.agent_converse()` NEVER raises. Always check the `error` column:
```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'hello');
```

---

## Decision 5: Standalone Function Selection

| Task | Function | Notes |
|---|---|---|
| Convert text to vector | `aidb.encode_text(text, model)` | Use for storage-side embedding |
| Convert query text to vector | `aidb.encode_text_query(text, model)` | Use on the query side for bi-encoders |
| Batch embed texts | `aidb.encode_text_batch(texts[], model)` | More efficient than looping encode_text |
| Embed an image | `aidb.encode_image(bytea, model)` | Returns VECTOR |
| Generate/complete text | `aidb.generate_text(prompt, model)` | Preferred over deprecated decode_text |
| Generate text (batch) | `aidb.generate_text_batch(prompts[], model)` | Preferred over deprecated decode_text_batch |
| Rerank candidates | `aidb.rerank_text(query, candidates[], model)` | Returns TABLE(index INT, score FLOAT) |
| Split text into chunks | `aidb.chunk_text(text, options)` | Returns TABLE(part_id INT, value TEXT) |
| Summarize text | `aidb.summarize_text(text, options)` | Uses a configured LLM |
| Extract text from PDF | `aidb.parse_pdf(bytea, options)` | Returns TEXT |
| Extract text from HTML | `aidb.parse_html(bytea, options)` | Returns TEXT |
| OCR an image | `aidb.perform_ocr(image, model)` | Requires nim_paddle_ocr model |

**Deprecated (avoid in new code):**
- `aidb.decode_text` → use `aidb.generate_text`
- `aidb.decode_text_batch` → use `aidb.generate_text_batch`

---

## Decision 6: Which Model Provider?

```
Choosing a model provider
│
├─► Testing / CI / no external dependency → 'dummy'
│   (deterministic, no server needed, safe for all environments)
│
├─► Local inference, no API key needed
│   ├─► BERT-style embeddings → 'bert_local'
│   ├─► CLIP image+text → 'clip_local'
│   ├─► T5 → 't5_local'
│   └─► Llama instruction-tuned → 'llama_instruct_local'
│
├─► OpenAI API → 'openai_embeddings' / 'openai_completions' / 'openai_responses'
├─► NVIDIA NIM → 'nim_embeddings' / 'nim_completions' / 'nim_clip' / 'nim_paddle_ocr'
├─► Anthropic → 'anthropic_messages' (also: _azure, _bedrock variants)
├─► Google Gemini → 'gemini'
├─► HuggingFace TEI → 'hf_tei' / 'hf_tei_reranking'
├─► llama.cpp server → 'llamacpp_generate' / 'llamacpp_embeddings'
├─► Any OpenAI-compatible (vLLM, etc.) → 'completions' / 'embeddings'
├─► OpenRouter → 'openrouter_chat' / 'openrouter_embeddings'
└─► EDB Hybrid Control Plane auto-discovery → aidb.sync_hcp_models()
```

See `references/model-adapters.md` for complete provider list with example SQL.

---

## Key Safety Constraints (Always Enforce)

1. **Pipeline name ≤ 46 characters** — creation fails if exceeded
2. **Max 10 steps per pipeline** — creation fails if exceeded
3. **Destination table must NOT already exist** — creation fails if it does
4. **Step sequence must be type-compatible** — rejected at creation time (see `references/step-operations.md`)
5. **`aidb.max_threads` change requires DB restart** — inform user before they try
6. **`agent_converse` never raises** — always check `error` column
7. **KnowledgeBase step is deprecated** — steer new work to `create_semantic_kb()` or standalone `encode_text()`
8. **`decode_text`/`decode_text_batch` are deprecated** — always use `generate_text`/`generate_text_batch`
9. **Unreviewed KB relationships are never safe for join routing** — only approved relationships route
10. **Agent delegation cannot escalate privileges** — delegate always inherits parent's role and read_only flag
