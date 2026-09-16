# AIDB Common Errors and Troubleshooting

## Pipeline Errors

### "Pipeline name too long"
**Cause:** Pipeline names are limited to 46 characters.
**Fix:** Shorten the name. Check `LENGTH('your_pipeline_name') <= 46`.

### "Destination table already exists"
**Cause:** `aidb.create_pipeline()` refuses to create a pipeline if the destination table already exists.
**Fix:** Drop the existing table first, or choose a different destination name.
```sql
DROP TABLE IF EXISTS my_destination_table;
```

### "Step sequence incompatible"
**Cause:** Steps must be ordered so the output type of step N matches the input type of step N+1 (Text → Text, Bytes → Bytes, etc.).
**Fix:** See [Step Operations Reference](step-operations.md) for the compatibility matrix. Common valid chains:
- `ParsePdf` → `ChunkText` → _(destination)_
- `PdfToImage` → `PerformOcr` → _(destination)_
- `ParseHtml` → `ChunkText` → _(destination)_

### "Maximum 10 steps per pipeline"
**Cause:** Hard limit of 10 steps per pipeline.
**Fix:** Split complex pipelines into two separate pipelines where the destination of the first feeds the source of the second.

### Pipeline runs but produces no output
**Possible causes:**
1. Source table is empty.
2. Auto-processing mode is `Disabled` — run manually: `SELECT aidb.run_pipeline('name');`
3. All rows are already in the `completed` state in the pipeline state table.

**Check pipeline errors:**
```sql
SELECT * FROM aidb_internal.pipeline_error_log WHERE pipeline_name = 'your_pipeline';
```
Or set `aidb.pipeline_error_warnings = true` to emit errors as PostgreSQL WARNINGs.

### "Model not found" during pipeline execution
**Cause:** Models are validated at pipeline creation time, not at execution time (unless `validate => true` was passed to `create_model`).
**Fix:** Verify the model exists: `SELECT * FROM aidb.list_models() WHERE name = 'model_name';`

---

## Model Registration Errors

### "Provider not recognized"
**Cause:** The `provider` argument does not match a known adapter.
**Fix:** Check [Model Adapters Reference](model-adapters.md) for the exact provider name (case-sensitive, lowercase, underscores).

### "Credential validation failed" (when `validate => true`)
**Cause:** The model endpoint rejected the test connection at registration time.
**Fix:**
- Verify API key, URL, and model name in the `config` JSONB.
- Check egress allowlist: `SELECT * FROM pg_settings WHERE name = 'aidb.egress_allowlist';`
- For self-hosted endpoints, verify the server is reachable from the database host.

### "Egress blocked"
**Cause:** The model's endpoint URL is not in the egress allowlist.
**Fix:**
```sql
ALTER SYSTEM SET aidb.egress_allowlist = 'api.openai.com,my-llm-host.internal';
SELECT pg_reload_conf();
```

### Credentials appearing in logs
**Cause:** Never pass credentials via inline `config` for production; use `credentials` (stored in `pg_user_mappings`), `credentials_env`, or `credentials_k8s_secret`.
**Note:** `aidb.list_models()` and `aidb.get_model()` never return credential fields — this is by design.

---

## Agent Hub Errors

### `agent_converse` returns an `error` column value instead of raising
**This is expected behavior.** `agent_converse` never raises — it returns errors via the `error` column so the caller's transaction is not aborted. Always check:
```sql
SELECT message, error FROM aidb.agent_converse('my_agent', 'prompt');
```

### "Tool not found" / tool call fails
**Possible causes:**
1. Tool name is misspelled in the agent's `tools` list.
2. MCP tool cache is stale — call `SELECT aidb.refresh_mcp_tools('server_name');`
3. Tool is only available for read-write sessions but the agent is in read-only mode.

### "Maximum delegation depth exceeded"
**Cause:** Agent delegation chains are bounded (up to 11 levels deep, including the top-level call). A delegate's delegate's delegate... hit the limit.
**Fix:** Flatten the delegation graph or reduce nesting depth.

### "Budget exceeded — strategy: error"
**Cause:** The agent hit its configured token or iteration budget and `budget_strategy = 'error'`.
**Fix:** Increase budget limits in `create_agent`/`update_agent`, or change `budget_strategy` to `attempt_complete` or `summarize`.

### Agent ignores read-only flag
**Note:** Read-only enforcement has three layers: static tag classification, a tool allow-list, and `SET LOCAL transaction_read_only = on`. MCP tools are always blocked in read-only mode regardless of their declared flag.

---

## Semantic Knowledge Base Errors

### KB shows no results (similarity = 0 for everything)
**Cause:** Using the `dummy` provider returns zero vectors; cosine similarity of zero vectors is undefined or 0.
**Fix:** Use a real embedding model for meaningful similarity scores.

### "Relationship not approved for routing"
**Cause:** The join-routing functions (`suggest_joins`, `find_join_path`) only route through relationships that have been explicitly approved/curated.
**Fix:** Review and approve the relationship first using the relationship management functions. Never assume an unreviewed relationship is safe to route through.

### KB not updating on data changes
**Cause:** Auto-processing mode may be `Disabled`.
**Fix:**
```sql
SELECT aidb.update_semantic_kb_auto_processing('my_kb', 'Live');
-- or
SELECT aidb.refresh_semantic_kb('my_kb');
```

### `KnowledgeBase` step in pipeline emits deprecation warnings
**Cause:** The `KnowledgeBase` pipeline step type is deprecated. It still works but emits runtime warnings.
**Fix:** For schema embedding, migrate to `aidb.create_semantic_kb()`. For general embedding, use `aidb.encode_text()` / `aidb.encode_text_batch()` directly.

---

## Deprecation Warnings

### `aidb.decode_text` / `aidb.decode_text_batch` warnings
These functions still work but emit a warning: *"deprecated and will be removed in a future version"*.
**Fix:** Replace with `aidb.generate_text()` / `aidb.generate_text_batch()`.

---

## Configuration / GUC Issues

### Thread pool changes don't take effect
**Cause:** `aidb.max_threads` requires a PostgreSQL restart.
**Fix:** `ALTER SYSTEM SET aidb.max_threads = N;` then restart the database server.

### MCP endpoint not starting
**Cause:** `edb.endpoints_mcp_enabled` defaults to `off`.
**Fix:**
```sql
ALTER SYSTEM SET edb.endpoints_mcp_enabled = 'on';
-- Requires postmaster restart (GUC context = Postmaster)
```
Also check: TLS cert/key must be both-or-neither, and non-loopback listen addresses require TLS.

### OTel traces not appearing
**Cause:** `aidb.otel_client` defaults to `noop` (no output).
**Fix:** Set to `database` to write to `aidb_otel.*` tables, `stdout` for console output, or `grpc` for an OTLP collector.
```sql
ALTER SYSTEM SET aidb.otel_client = 'database';
SELECT pg_reload_conf();
```
Note: Readable by `aidb_governance` role only (not `aidb_users`).

---

## Testing Tips

- Always use `provider => 'dummy'` for tests — it returns deterministic output without any external service.
- The `dummy` provider returns zero vectors for embeddings and canned responses for completions; do not use it to test similarity ranking quality.
- For pipeline regression tests, the `dummy` provider is the correct and only safe choice.
