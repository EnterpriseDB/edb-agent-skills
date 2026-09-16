# AIDB Troubleshooting Guide

Common errors, their causes, and their fixes.

---

## Extension & Installation

### `ERROR: extension "aidb" already exists`
Normal — it is installed. Use `SELECT extversion FROM pg_extension WHERE extname = 'aidb';` to confirm.

### `ERROR: could not open extension control file`
AIDB is not installed in the PostgreSQL extension directory. Install via your EDB Postgres AI distribution package.

### Extension version is higher than expected
The repo version in `Cargo.toml`/`aidb.control` may be ahead of the last released version. Do not assume the catalog version equals a released version.

---

## Pipeline Errors

### `ERROR: pipeline name too long`
Pipeline names are capped at **46 characters**. Shorten the name.

### `ERROR: pipeline already has maximum steps`
Each pipeline supports at most **10 steps**. Break into multiple pipelines or reduce steps.

### `ERROR: destination table already exists`
The destination table specified (or auto-generated) already exists. Drop it first or choose a different destination name.

### `ERROR: incompatible step sequence`
Steps must be compatible: a `ParsePdf` step produces Text and cannot feed into `PerformOcr` (which takes Bytes/Image). Review the compatibility table in `references/step-operations.md`.

### Pipeline runs but destination table is empty
- In `Disabled` mode, you must call `aidb.run_pipeline('name')` explicitly.
- In `Background` mode, the background worker may not have run yet. Check `aidb.list_pipelines()` for the pipeline state.
- Check the pipeline error log: `SELECT * FROM aidb_pipeline_error_log_<pipeline_id>;`

### `ERROR: model not found`
The model name referenced in a step does not exist. Run `SELECT * FROM aidb.list_models();` and verify the name.

---

## Model Errors

### `ERROR: egress blocked`
The model provider's endpoint is not in the egress allowlist. Add it:
```sql
ALTER SYSTEM SET aidb.egress_allowlist = 'api.openai.com';
SELECT pg_reload_conf();
```

### `ERROR: TLS verification failed`
Either the endpoint is using self-signed certificates or TLS is misconfigured. For development:
```sql
ALTER SYSTEM SET aidb.allow_insecure_tls = 'on';
SELECT pg_reload_conf();
```
**Do not use `allow_insecure_tls` in production.**

### `ERROR: credentials not found` / auth failure
- If using `credentials_env`, ensure the env var prefix is allowed: `ALTER SYSTEM SET aidb.env_var_allowed_prefix = 'AIDB_';`
- For K8s secrets: ensure `aidb.k8s_secret_allowed_path_prefix` includes the path.

### Credential leakage concern
`aidb.list_models()` and `aidb.get_model()` never return credential fields. Credentials are stored in `pg_user_mappings`.

### `WARNING: decode_text is deprecated`
Replace all calls to `aidb.decode_text()` / `aidb.decode_text_batch()` with `aidb.generate_text()` / `aidb.generate_text_batch()`. The old functions still work but emit a runtime warning.

---

## Semantic Knowledge Base

### KB shows no results after creation
- Check `aidb.semantic_kb_stats('kb_name')` to see if embedding has run.
- If auto_processing is `Disabled`, call `aidb.refresh_semantic_kb('kb_name')`.
- Ensure the schema has `COMMENT ON TABLE/COLUMN` statements — those are what gets embedded.

### Similarity threshold too high / no results
Try a lower `min_similarity`. Recommended starting point: `0.7`. For exploration, try `0.5`.

### `ERROR: relationship not approved for routing`
When using `aidb.suggest_joins()` or `aidb.find_join_path()`, relationships must be explicitly approved/curated before they can be routed. Never assume an unreviewed relationship is safe.

---

## Agent Hub

### Agent never returns / hangs
- Check `aidb.conversation_log` for the conversation — it should show intermediate actions.
- The agent will stop at `MAX_REASONING_ITERATIONS` (25). If it is close, the prompt may be ambiguous.
- Check tool error patterns: `MAX_REPEATED_FAILED_TOOL_CALL_OCCURRENCES` (3) stops repeated identical failures.

### Agent converse returns `error` field non-null but no SQL error
This is expected behavior. `agent_converse` never raises — it returns an `error` column. Read the error field:
```sql
SELECT message, error FROM aidb.agent_converse('my_agent', 'hello');
```

### `ERROR: tool not found` / `ERROR: tool not available to this agent`
- Run `SELECT * FROM aidb.tools;` — confirm the tool is registered.
- If it's an MCP tool, run `SELECT aidb.refresh_mcp_tools('server_name');` — the cache expires after 60 minutes.
- Confirm the agent's `tools` array includes the tool name.
- In read-only mode, MCP tools are always blocked.

### Agent ignores a tool
- The model may not have selected it. Check `aidb.conversation_log` to see all tool calls.
- Ensure the tool description is clear enough for the model to identify it.

### Delegation depth exceeded
Delegation depth cap is 11 (top-level call is depth 1). Reduce nesting of delegates or flatten the agent hierarchy.

### `ERROR: role not found` / `ERROR: not role member`
The agent's assigned role does not exist or the current session user is not a member. Check `aidb.purpose_registry` and PostgreSQL role membership.

---

## Agent Memory

### Session history is empty / shorter than expected
- `aidb.agent_session_source` defaults to `action_log`. Set it to `memory` to use the session tier:
  ```sql
  SET aidb.agent_session_source = 'memory';
  ```
- If switching from `action_log` to `memory` mid-conversation, older turns captured under `action_log` mode are not in the memory tier.

### `compaction_model_unavailable` warning in session_get
- The namespace binding has no `model_spec.llm` AND no `fallback_model` was passed.
- On the `pg_agents` default namespace, the agent's own model is used as fallback automatically.
- To provision a dedicated summarizer:
  ```sql
  SELECT aidb.create_model('summarizer', 'openai_completions', aidb.completions_config(model => 'gpt-4o-mini', api_key => 'sk-...'));
  SELECT aidb_memory.init('my_ns', 'mock', '{"model_spec": {"llm": "summarizer"}}'::jsonb);
  ```

### Only `mock` provider available
Agent Memory currently ships only the `mock` provider, which is a deterministic test double — it does not perform real semantic memory distillation. Real providers are planned but not yet shipped.

### Session functions not available as tools
`session_start`, `session_get`, and `session_end` are deliberately NOT exposed as agent tools or over MCP. Session boundaries are the calling harness's decision, not the model's.

---

## MCP Endpoint

### `edb-endpoints` binary not found
The `edb-endpoints` binary is separate from the extension. Ensure your EDB Postgres AI distribution includes it and it is in `$PATH` (or set `edb.endpoints_mcp_command_path`).

### MCP endpoint restart loop / exhausted budget
- Check `edb.endpoints_mcp_restart_max` (default: 5) and `edb.endpoints_mcp_restart_window_secs` (default: 60).
- After exhaustion, run `SELECT pg_reload_conf();` to re-arm once the window expires.

### Non-loopback host without TLS rejected
The MCP supervisor rejects non-loopback listen addresses (`127.0.0.1`, `::1`, `localhost`) unless TLS cert+key are both configured:
```sql
ALTER SYSTEM SET edb.endpoints_mcp_tls_cert = '/path/to/cert.pem';
ALTER SYSTEM SET edb.endpoints_mcp_tls_key = '/path/to/key.pem';
```

### MCP tool cache stale
The MCP tool cache expires after 60 minutes and is only refreshed by explicit `aidb.refresh_mcp_tools('name')` calls.

---

## Thread Pool / Performance

### Thread pool change has no effect
`aidb.max_threads` requires a **database restart** to take effect. It controls the CPU thread pool for local model inference.

### Background pipeline not processing
- Ensure `shared_preload_libraries` includes `aidb`.
- Check `SELECT backend_type FROM pg_stat_activity WHERE backend_type LIKE '%aidb%';`
- `aidb.pipeline_error_warnings = on` (default) emits per-error WARNINGs to the PostgreSQL log.

---

## KnowledgeBase Step (Deprecated)

If you see `WARNING: KnowledgeBase step is deprecated`, this is expected. For new work:
- Use `aidb.create_semantic_kb()` for schema-embedding.
- Use `aidb.encode_text()` / `aidb.encode_text_batch()` for general embedding pipelines.
- The KnowledgeBase step still works but should not be used in new pipelines.
