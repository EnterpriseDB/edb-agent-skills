# AIDB Troubleshooting & Edge Case Reference

Error text below is quoted as aidb prints it. `<...>` marks a value aidb fills in.

## Installation

```sql
-- Requires PostgreSQL 14-18; CASCADE installs the pgvector extension (vector)
CREATE EXTENSION aidb CASCADE;

-- Add aidb to shared_preload_libraries (restart required):
-- shared_preload_libraries = 'aidb'
```

Supported deployments always preload `aidb`. Background pipelines, the memory worker, the OTel workers and the MCP endpoint need it.

### Common install failures
| Symptom | Cause | Fix |
|---|---|---|
| `extension "vector" is not available` (PostgreSQL message) | pgvector not installed | Install pgvector for this PG version |
| Background workers not starting | `shared_preload_libraries` missing `aidb` | Add and restart |
| `aidb.max_threads` change not seen by workers | Set only in one session | `SET aidb.max_threads = N` (superuser) applies at once in that backend. For all backends and workers use `ALTER SYSTEM SET aidb.max_threads = N; SELECT pg_reload_conf();` |

---

## Pipeline Troubleshooting

### Pipeline creation failures
| Error | Cause / fix |
|---|---|
| `The pipeline name is too long. Maximum length is 46 characters to accommodate intermediate step table names.` | Use a name of 46 characters or fewer |
| `The destination table '<schema>.<table>' already exists. Please choose a different name or delete the existing table.` | Drop the table or choose a different `destination`. A `KnowledgeBase` pipeline pointed at an existing knowledge base attaches to it instead of failing |
| `The state table '<name>' already exists. If this is a leftover from a previous installation, you can drop it with: DROP TABLE <name>;` | Leftover state table; drop it as the message says |
| `Output type '<type>' of the <step> step (#<n>) does not match input type '<type>' of the next <step> step (#<n+1>)` | Step N+1 cannot consume step N's output type. Check `references/step-operations.md` for valid sequences. Raised by `create_pipeline` and `run_pipeline`; it is never written to the error log |
| `Steps must be defined in a consecutive order. Missing steps: [<n>]` | A step number was skipped (for example `step_1` and `step_3`) |
| `function aidb.create_pipeline(... step_11 => unknown) does not exist` | Maximum 10 steps; there is no `step_11` parameter |
| `Failed to create model: <reason>` with `HINT:  Use the argument "validate => false" if you want to create the model without running checks.` | `create_model` probes the model by default (`validate => true`). Fix the config or endpoint, or pass `validate => false` to register without probing |

`create_pipeline` wraps some failures as `An error occurred creating pipeline '<name>': <message>`.

### Pipeline not processing rows
1. Check `auto_processing` mode: `SELECT name, auto_processing FROM aidb.pipelines WHERE name = '<name>';`
2. For `Background` mode: confirm `aidb` is in `shared_preload_libraries` and the per-database dispatcher is running: `SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type LIKE 'aidb%';` (expect `aidb root bgworker` and `aidb dispatcher db_oid(<oid>)`)
3. For `Live` mode: confirm triggers exist on the source table (`\d <source_table>` in psql)
4. Run manually: `SELECT aidb.run_pipeline('<name>');`
5. Check metrics: `SELECT * FROM aidb.get_pipeline_metrics('<name>');` (`count_errors_record`, `count_errors_pipeline`)
6. Check the error log: `SELECT * FROM aidb.get_error_logs('<name>', p_limit => 20);` (newest first by `last_seen_at`). Per-step counts: `SELECT * FROM aidb.get_error_log_summary('<name>');`. All pipelines: `SELECT * FROM aidb.get_all_error_summaries();`

Errors are stored in the table `<source_schema>.pipeline_<name>_errors` with columns `id, source_id, part_ids, pipeline_step, step_operation, error_message, error_category, failed_at, retry_count, last_retry_at, last_seen_at`. Read it through `aidb.get_error_logs` rather than querying it directly. A pipeline with no source table (a Semantic KB) has no error table.

### Similarity search returning no results
- Check destination table has rows: `SELECT COUNT(*) FROM <destination_table>`
- Search functions default `min_similarity` to NULL (no cutoff). If you set a threshold, lower it; any value is a heuristic to tune on your data
- Confirm you're using `encode_text_query` (not `encode_text`) on the query side for bi-encoders
- Verify the distance operator in the `KnowledgeBase` config matches the pgvector operator you're using (`Cosine` → `<=>`, `L2` → `<->`, `InnerProduct` → `<#>`)

### `retrieve_text` / `retrieve_key` lookups
The first argument is the schema-qualified destination table of a `KnowledgeBase` pipeline: `SELECT key, value, distance FROM aidb.retrieve_text('public.pipeline_docs_kb', 'refund policy', topk => 5);`

| Message | Cause / fix |
|---|---|
| `WARNING:  Knowledge base looked up by pipeline name '<pipeline>'. This is deprecated. Use the KB identifier '<schema>.<table>' instead.` | You passed the pipeline name. The call still returns rows; pass `'<schema>.<table>'` as the warning says |
| `Knowledge base not found: <name>` | Unqualified table name, or a name that is neither a pipeline nor a table. Use `'<schema>.<table>'` (find it with `SELECT destination_schema, destination FROM aidb.pipelines WHERE name = '<name>';`) |

### Standalone function call errors
| Error | Cause / fix |
|---|---|
| `function aidb.chunk_text(text, jsonb) does not exist` | You passed a `*_config()` helper result. `chunk_text`, `summarize_text`, `parse_html`, `parse_pdf`, `perform_ocr`, `pdf_to_image`, `generate_text` and `generate_text_batch` take `json`, but every `aidb.*_config()` helper returns `jsonb`. Append `::json`: `SELECT part_id, chunk FROM aidb.chunk_text(body, aidb.chunk_text_config(desired_length => 200)::json);` (a string literal works without a cast) |
| `Model not found: <text you passed>` | Arguments swapped. Model functions take the model name first: `aidb.encode_text('my_model', 'hello')`, `aidb.generate_text('my_llm', 'prompt')`. If the reported name is your input text, swap the arguments; otherwise check `SELECT * FROM aidb.list_models();` |

---

## Model Troubleshooting

### Model registration failures
- `create_model` validates by default (`validate => true`) and prints the `validate => false` hint on a failed probe
- `Failed to create model: config must not contain "api_key" or "basic_auth" -- pass them via the credentials, credentials_env, or credentials_k8s_secret argument instead`: move the secret out of `config`, for example `credentials => '{"api_key": "sk-..."}'::jsonb`
- `egress to host "<host>" is not permitted by <guc>`: when `aidb.egress_allowlist` (or the shared `edb.egress_allowlist`) is set, the provider host must match it. With both unset there is no host check
- `egress to "<url>" (host "<host>") was blocked: scheme "http" is not permitted -- HTTPS is required`: plain `http://` endpoints (even `localhost`) are refused unless a superuser sets `aidb.allow_insecure_egress = on`, which only allows loopback and private hosts. A self-signed HTTPS endpoint needs `aidb.allow_insecure_tls = on`, which applies to every host. Leave both off in production
- Secrets live in a user mapping and are never returned by `list_models()`; its `credential` column names the credential only

### `decode_text`/`decode_text_batch` warning
These are deprecated. Use `generate_text`/`generate_text_batch` instead. The old names still work but emit ``WARNING:  `decode_text` is deprecated and will be removed in a future version. Please use `generate_text` instead`` (and the matching `decode_text_batch` / `generate_text_batch` text).

### Local model performance
- `aidb.max_threads` controls the thread pool for local inference (default `0` = half the CPUs)
- Superuser setting: session `SET` applies immediately; `ALTER SYSTEM SET` plus `SELECT pg_reload_conf()` applies everywhere
- Use `dummy` provider for tests: deterministic, no server needed

---

## Semantic Knowledge Base Troubleshooting

### KB not returning results after schema changes
- `create_semantic_kb` installs DDL event triggers unless `bypass_triggers => true`; they update entities, columns and relationships as the schema changes
- A `Disabled` or trigger-bypassed KB needs an explicit refresh: `SELECT aidb.refresh_semantic_kb('<name>');`
- Check stats: `SELECT * FROM aidb.semantic_kb_stats('<name>');`

### Similarity threshold guidance
Search functions apply no `min_similarity` cutoff by default. There is no built-in recommended value; start without one and add a cutoff only after looking at the scores your data produces.

### Relationships & join routing
- `aidb.find_join_path` routes only through approved relationships
- `aidb.suggest_joins` returns approved relationships by default, and proposed ones too with `include_proposed => true`. Curate proposed relationships before relying on them

---

## Agent Hub Troubleshooting

### Agent converse returns an error in the `error` column
`agent_converse` never raises; check the `error` column:
```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'hello');
```

Common error text:
| Error | Cause |
|---|---|
| `Model not found: <model>` | Returned by `create_agent` when the model is not in `aidb.list_models()` |
| `Invalid parameters: agent references unknown tool '<tool>'` | A name in the agent's `tools` array does not exist in `aidb.tools` |
| `agent '<name>' not found` | `agent_converse` called with an agent name that was never created |
| `<Iterations\|InputTokens\|OutputTokens> budget exceeded` | A budget ran out. With the default `budget_strategy` (`attempt_complete`) this surfaces after a short grace period; `error` surfaces it at once; `ignore` keeps going |
| `gave up after 25 reasoning iterations without reaching an answer` | Hard cap on reasoning rounds |
| `delegation depth limit (11) exceeded -- this usually means agents are delegating in a cycle <detail>` | Too many nested delegate calls, usually an agent delegating to itself or two agents delegating back and forth |
| `current user is not a member of role '<role>' with the right to SET ROLE to it; refusing to switch` | The caller must be granted the purpose's role (PG16+: with SET) |

### Agent stuck in a loop
- Reasoning is capped at 25 iterations regardless of budget
- Tool calls are `tool_call` / `tool_response` rows in `aidb_internal.action_log` (row-level security shows only your own): `SELECT created_at, action_type, request_payload, response_payload FROM aidb_internal.action_log WHERE conversation_id = '<id>' AND action_type IN ('tool_call', 'tool_response') ORDER BY created_at;`. `aidb.conversation_log` shows only prompts and answers
- Enable debug: `SELECT message, error FROM aidb.agent_converse('my_agent', 'hello', debug => true)`

### Read-only agent creating writes
Three enforcement layers:
1. Command-tag classification (`SELECT`, `EXPLAIN`, `COPY TO`, `PREPARE` = read-only; anything unrecognized counts as a write)
2. MCP tools blocked entirely in read-only mode
3. `SET LOCAL transaction_read_only = on` inside a discarded subtransaction (authoritative)

Residual gap: VOLATILE functions writing outside the database via dblink or file I/O are not caught.

### Delegation privilege escalation
A delegate always inherits the parent's `read_only` flag. When the parent runs under a purpose, the delegate runs under the parent's role and purpose, overriding its own. A parent with no purpose passes nothing down, and the delegate uses its own purpose.

---

## MCP Endpoint Troubleshooting

### Endpoint not starting
1. Check `edb.endpoints_mcp_enabled = on`. The supervisor is registered only if `edb.endpoints_mcp_enabled` or `edb.endpoints_otlp_enabled` is on at PostgreSQL startup, so the first enable needs a restart; after that, toggling applies at `pg_reload_conf()`. `edb.endpoints_enabled = off` (postmaster setting, default on) disables everything
2. Check the supervisor: `SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type = 'aidb endpoints supervisor';`. The `edb-endpoints` server is a child OS process, so look for it in the OS process list, not `pg_stat_activity`
3. Check TLS config: cert and key must be both-or-neither, and both files must exist
4. Non-loopback listen host without TLS is rejected (supervisor idles rather than burning restart budget)

### Restart budget exhausted
- Default: 5 spawns in 60 seconds (`edb.endpoints_restart_max`, `edb.endpoints_restart_window_secs`). The `edb.endpoints_mcp_restart_max` and `edb.endpoints_mcp_restart_window_secs` names are deprecated and warn when set
- After exhaustion: supervisor idles; `SELECT pg_reload_conf()` retries once the window has room
- A `NotFound`/`PermissionDenied` spawn failure refunds its budget slot and idles (retrying cannot heal a missing binary)

### Tool cache stale
- MCP tool cache ages out after 60 minutes
- Refresh manually: `SELECT aidb.refresh_mcp_tools('<server_name>')`
- Invoking a tool does not refresh its cache entry

---

## Agent Memory Troubleshooting

### Memory worker not processing
- Enable the worker (superuser): `ALTER SYSTEM SET aidb.agent_memory_enable_worker = on; SELECT pg_reload_conf();`. It is off by default, and a session `SET` does not reach the background worker
- Verify the worker: `SELECT pid, backend_type FROM pg_stat_activity WHERE backend_type LIKE 'aidb memory dispatcher%';`
- Idle poll every 2 seconds by default (`aidb.memory_worker_idle_wait_ms`); 5 second cooldown after a provider failure
- Runtimes: `mock` (deterministic test double) and `agent_memory_container` (needs a running Agent Memory Server at `config.base_url`)

### Session compaction warnings
| Warning code | Meaning |
|---|---|
| `compaction_budget_too_small` | Budget's quarter share below minimum target; no provider contacted |
| `compaction_model_unavailable` | No `sessionmemory_specs.llm` in the binding and no `fallback_model` from the caller, or the named model is not registered |
| `compaction_call_failed` | Model call for compaction failed |
| `compaction_summary_overlength` | Summary still over target after retries |

Each code starts an entry in the `warnings` column of `aidb_memory.session_get`; a read never fails because of compaction.

### Configuring compaction model
```sql
SELECT aidb.create_model('my_summarizer', 'openai_completions',
    config => aidb.completions_config(model => 'gpt-4o-mini'),
    credentials => '{"api_key": "sk-..."}'::jsonb);
SELECT * FROM aidb_memory.init('my_ns', '{"sessionmemory_specs": {"llm": "my_summarizer"}}'::jsonb);
SET aidb.agent_memory_namespace = 'my_ns';
```
Bindings are immutable after `init`. To change the compaction model, create a new namespace and repoint the GUC.

---

## Security Checklist

- [ ] `aidb.egress_allowlist` (or `edb.egress_allowlist`) is set and contains only intended provider hosts; with both unset there is no host check
- [ ] `aidb.allow_insecure_egress = off` (default); do not override in production
- [ ] `aidb.allow_insecure_tls = off` (default); do not override in production
- [ ] Secrets are passed in `credentials` / `credential` / `credentials_env` / `credentials_k8s_secret`, never in `config`. `credentials_env` and `credentials_k8s_secret` are for superusers and `aidb_admins` only
- [ ] Purpose registry roles are reviewed; `delete_purpose` is a soft delete, old agents may still reference the name
- [ ] SQL tools with `read_only => false` are reviewed carefully; they can write to the database
- [ ] MCP servers in `aidb.mcp_registry` are trusted and on the egress allowlist
- [ ] `aidb_governance` is the only non-superuser role with `SELECT` on the `aidb_otel` tables (`metrics`, `logs`, `traces`)

### Grants worth knowing
- `aidb_internal.action_log` is `SELECT` and `INSERT` only (no UPDATE/DELETE), which is why agents cannot rewrite audit history
- `aidb_users` can read only `name, url, transport, tool_filter, created_at, updated_at, owner` of `aidb.mcp_registry`; header columns are hidden

## PGD (PostgreSQL Distributed) Notes

- Pipeline state tables gain a `_<pgd_node_group>` suffix under PGD
