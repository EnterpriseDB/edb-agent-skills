# AIDB Troubleshooting & Edge Case Reference

## Installation

```sql
-- Requires PostgreSQL 14–18; CASCADE installs pgvector and pgfs dependencies
CREATE EXTENSION aidb CASCADE;

-- Also add to shared_preload_libraries if using Background pipelines or MCP endpoint:
-- shared_preload_libraries = 'aidb,vchord'   (restart required)
```

### Common install failures
| Symptom | Cause | Fix |
|---|---|---|
| `extension "pgvector" is not available` | pgvector not installed | Install pgvector for this PG version |
| Background workers not starting | `shared_preload_libraries` missing `aidb` | Add and restart |
| `aidb.max_threads` change has no effect | Thread pool GUC requires DB restart | `pg_ctl restart` |

---

## Pipeline Troubleshooting

### Pipeline creation failures
| Error | Cause |
|---|---|
| `pipeline name too long` | Name exceeds 46 characters |
| `destination table already exists` | Drop the table first or choose a different destination name |
| `incompatible step sequence` | Step N+1 cannot consume step N's output type — check `references/step-operations.md` for valid sequences |
| `step limit exceeded` | Maximum 10 steps per pipeline |
| Model validation error at creation | Model config invalid; use `validate => true` on `create_model` to pre-check |

### Pipeline not processing rows
1. Check `auto_processing` mode: `SELECT name, auto_processing FROM aidb.list_pipelines()`
2. For `Background` mode: confirm `aidb` is in `shared_preload_libraries` and background workers are running (`SELECT * FROM pg_stat_activity WHERE backend_type LIKE '%aidb%'`)
3. For `Live` mode: confirm triggers exist on the source table (`\d <source_table>` in psql)
4. Run manually: `SELECT aidb.run_pipeline('<name>')`
5. Check error log: `SELECT name, id FROM aidb.list_pipelines()` → then `SELECT * FROM aidb_internal.pipeline_error_log_<id> ORDER BY occurred_at DESC LIMIT 20`

### Similarity search returning no results
- Check destination table has rows: `SELECT COUNT(*) FROM <destination_table>`
- Lower the similarity threshold (0.7 is a good default; try 0.5 for broader search)
- Confirm you're using `encode_text_query` (not `encode_text`) on the query side for bi-encoders
- Verify the distance operator in the `KnowledgeBase` config matches the pgvector operator you're using (`Cosine` → `<=>`, `L2` → `<->`, `InnerProduct` → `<#>`)

---

## Model Troubleshooting

### Model registration failures
- Use `validate => true` to test-connect immediately: `SELECT aidb.create_model('m', 'openai_embeddings', config => ..., validate => true)`
- Check egress allowlist: model provider hostname must be in `aidb.egress_allowlist` GUC
- Check TLS: `aidb.allow_insecure_egress = false` by default — do not disable in production
- Credentials are stored in `pg_user_mappings` and never returned by `list_models()`

### `decode_text`/`decode_text_batch` warning
These are deprecated. Use `generate_text`/`generate_text_batch` instead. The old names still work but emit a runtime warning.

### Local model performance
- `aidb.max_threads` controls the thread pool for local inference (default: half CPUs)
- Changes require a database restart
- Use `dummy` provider for tests — deterministic, no server needed

---

## Semantic Knowledge Base Troubleshooting

### KB not returning results after schema changes
- In `Disabled` mode: `SELECT aidb.refresh_semantic_kb('<name>')`
- In `Live` mode: triggers re-embed on DDL changes automatically, but verify trigger exists
- Check stats: `SELECT * FROM aidb.semantic_kb_stats('<name>')`

### Similarity threshold guidance
- `0.9+` — near-exact matches only
- `0.8` — good default for typical semantic search
- `0.5–0.7` — broad exploration / lower confidence

### Relationships & join routing
- `aidb.suggest_joins` and `aidb.find_join_path` only route through **explicitly approved** relationships
- A candidate/unreviewed relationship is never used for routing — always curate before relying on it

---

## Agent Hub Troubleshooting

### Agent converse returns an error in the `error` column
`agent_converse` never raises — check the `error` column:
```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'hello');
```

Common error causes:
| Error pattern | Cause |
|---|---|
| `model not found` | Model name in agent definition doesn't match `aidb.list_models()` |
| `tool not found` | Tool in agent's `tool_names` array doesn't exist in `aidb.tools` |
| `budget exceeded` | Max iterations or token budget hit; check `budget_strategy` |
| `delegation depth limit` | Too many nested delegate agents (max depth 11) |
| `role not found / not_role_member` | Purpose registry role doesn't exist or calling user isn't a member |

### Agent stuck in a loop
- Check `MAX_REASONING_ITERATIONS` = 25 (hard cap regardless of budget)
- Review `aidb.conversation_log` for the conversation to see which tool calls repeat
- Enable debug: `SELECT message, error FROM aidb.agent_converse('my_agent', 'hello', debug => true)`

### Read-only agent creating writes
Three enforcement layers:
1. Command-tag classification (`SELECT`, `EXPLAIN`, `COPY TO`, `PREPARE` = read-only)
2. MCP tools blocked entirely in read-only mode
3. `SET LOCAL transaction_read_only = on` inside a discarded subtransaction (authoritative)

Residual gap: VOLATILE functions writing via dblink or file I/O are not caught.

### Delegation privilege escalation
Impossible by design: a delegate always inherits the **parent's** `read_only` flag and role, overriding its own definition. A delegate cannot self-upgrade to a more privileged role.

---

## MCP Endpoint Troubleshooting

### Endpoint not starting
1. Check `edb.endpoints_mcp_enabled = on` (requires postmaster restart)
2. Check `pg_stat_activity` for `aidb mcp supervisor` or `edb-endpoints` process
3. Check TLS config: cert and key must be both-or-neither, and both files must exist
4. Non-loopback listen host without TLS is rejected (supervisor idles rather than burning restart budget)

### Restart budget exhausted
- Default: 5 restarts in 60 seconds (`edb.endpoints_mcp_restart_max`, `edb.endpoints_mcp_restart_window_secs`)
- After exhaustion: supervisor idles; `SELECT pg_reload_conf()` re-arms once window has room
- A `NotFound`/`PermissionDenied` spawn failure refunds its budget slot and idles (retrying cannot heal a missing binary)

### Tool cache stale
- MCP tool cache ages out after 60 minutes
- Refresh manually: `SELECT aidb.refresh_mcp_tools('<server_name>')`
- Invoking a tool does not refresh its cache entry

---

## Agent Memory Troubleshooting

### Memory worker not processing
- Enable worker: `SET aidb.enable_memory_worker = on` (or `ALTER SYSTEM SET`)
- Verify OS process: `SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type ILIKE '%memory%'`
- Worker wakes every 5 seconds
- Only `mock` provider is currently implemented — no real memory distillation

### Session compaction warnings
| Warning code | Meaning |
|---|---|
| `compaction_budget_too_small` | Budget's quarter share below minimum target — no provider contacted |
| `compaction_model_unavailable` | No compaction model configured and no fallback |
| `compaction_call_failed` | Model call for compaction failed |
| `compaction_summary_overlength` | Summary still over target after retries |

These warnings are returned in the `warnings` column of `aidb_memory.session_get` — a read is never failed by compaction.

### Configuring compaction model
```sql
SELECT aidb.create_model('my_summarizer', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o-mini', api_key => 'sk-...'));
SELECT aidb_memory.init('my_ns', 'mock', '{"model_spec": {"llm": "my_summarizer"}}'::jsonb);
SET aidb.agent_memory_namespace = 'my_ns';
```
Bindings are immutable after `init`. To change the compaction model, create a new namespace and repoint the GUC.

---

## Security Checklist

- [ ] `aidb.egress_allowlist` contains only intended provider hosts
- [ ] `aidb.allow_insecure_egress = false` (default) — do not override in production
- [ ] `aidb.allow_insecure_tls = false` (default) — do not override in production
- [ ] Credentials use `credentials_env` or `credentials_k8s_secret` rather than inline JSONB where possible
- [ ] Purpose registry roles are reviewed — `delete_purpose` is a soft delete, old agents may still reference the name
- [ ] SQL tools with `read_only => false` are reviewed carefully — they can write to the database
- [ ] MCP servers in `aidb.mcp_registry` are trusted and on the egress allowlist
- [ ] `aidb_governance` role is the only one with access to `aidb_otel.*` tables

## PGD (PostgreSQL Distributed) Notes

- Pipeline state tables gain a `_<pgd_node_group>` suffix under PGD
- `action_log` is `SELECT` and `INSERT` only — no UPDATE/DELETE — which is also why agents cannot rewrite audit history
- `mcp_registry.headers` has table-level `SELECT` revoked with column allow-list (use `headers_env` for headers in PGD)
