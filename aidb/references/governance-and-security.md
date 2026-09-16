# AIDB Governance, Security & Observability Reference

## Purpose Registry

A **purpose** is a named policy scope that resolves to exactly one PostgreSQL role, providing role-level governance over agents.

```sql
-- Create a purpose (errors if name already exists and is not soft-deleted)
aidb.create_purpose(name TEXT, role TEXT, description TEXT DEFAULT NULL)

-- Update a purpose (only non-NULL arguments are changed)
aidb.update_purpose(name TEXT, role TEXT DEFAULT NULL, description TEXT DEFAULT NULL)

-- Soft-delete a purpose (sets deleted_at; NEVER physically removes the row
-- because agents may still reference the name)
aidb.delete_purpose(name TEXT)
```

`aidb.purpose_registry` is a read-only view: `name`, `role`, `description`, `created_at`, `updated_at`, `deleted_at`.

A `purpose` is assigned to agents via `create_agent`/`update_agent`'s `purpose` argument. All role switches performed under a purpose are recorded as audited decisions (OTel span `purpose_decision`, attributes `decision_id`, `action_status`, `interaction_point`, `principal`, `agent_role`, `denial_reason`).

**Denial reasons:** `role_not_found`, `not_role_member`, `engine_privilege_denied`.

> An action that is *allowed* but whose SQL then fails for an unrelated reason (syntax error, undefined object) still records as `allowed`.

---

## Egress & Credential Security

### Egress Allowlist

All outbound network calls (model providers, MCP servers) are governed by:

```
aidb.egress_allowlist     — GUC: allow-listed hosts for outbound calls
aidb.allow_insecure_egress — GUC (BOOLEAN, default false): must opt-in to bypass TLS
aidb.allow_insecure_tls    — GUC (BOOLEAN, default false): must opt-in to skip TLS verification
```

### Credential Sources

Models accept credentials through three mutually exclusive mechanisms:

| Parameter | Description |
|---|---|
| `credentials JSONB` | Inline JSON; stored in `pg_user_mappings`, never returned by `list_models()` |
| `credentials_env TEXT` | Name of an allow-listed environment variable (prefix controlled by `aidb.env_var_allowed_prefix`) |
| `credentials_k8s_secret TEXT` | Path of an allow-listed mounted Kubernetes secret (`aidb.k8s_secret_allowed_path_prefix`) |

MCP `import_mcp_tools` similarly accepts `headers_env` as an alternative to inline `headers`.

**Credentials are stored in `pg_user_mappings` and are never returned by `aidb.list_models()` or `aidb.get_model()`.**

---

## Read-Only Enforcement (Agent Hub)

Three enforcement layers:

1. **Static classification** — `READ_ONLY_COMMAND_TAGS` (`SELECT`, `EXPLAIN`, `COPY TO`, `PREPARE`); anything unrecognized treats as a write (fail-closed).
2. **Tool allow-list** — `is_tool_allowed_in_read_only_mode` blocks all MCP tools (unclassified foreign tools) regardless of declared flag.
3. **PostgreSQL level** — `SET LOCAL transaction_read_only = on` inside a discarded subtransaction. This is the authoritative layer.

A read-only run creates no task row and returns `NULL` for `conversation_id`.

> Residual gap: a VOLATILE function writing outside the database (dblink, file I/O) is not caught by any layer.

---

## Agent Role Model

Three distinct identities:

| Identity | Description |
|---|---|
| `aidb.agents.role` | SQL execution identity for agent tool calls. Setting requires `pg_has_role(current_user, role, 'MEMBER')`. |
| Per-task `role` | Stored on task queue and action log rows. |
| `initiated_by` | Set to `session_user`; the RLS key for all action tables. |

Role switching always uses `execute_sql_as_role` — never bare `SET LOCAL ROLE` — to prevent SQL inside the tool from issuing `RESET ROLE` to regain caller privileges.

**Delegation cannot escalate privileges.** A delegate inherits the parent's `read_only` flag and role, overriding its own definition.

---

## OpenTelemetry (OTel) Telemetry

AIDB exports OTel traces, logs, and metrics via `aidb_otel.*` tables.

### GUC: `aidb.otel_client`

| Value | Behavior |
|---|---|
| `noop` (default) | Nothing is exported; no-op instruments |
| `database` | Written to `aidb_otel.*` tables (readable by `aidb_governance` role only) |
| `stdout` | Written to process stdout |
| `log` | Written to PostgreSQL server log |
| `grpc` | Exported via OTLP/gRPC (requires `otel_client_grpc` build feature) |

### Background Workers

| Worker | Purpose |
|---|---|
| `worker_otel_exporter` | Delivers `aidb_otel.*` rows to the OTLP/HTTP endpoint |
| `worker_otel_cleaner` | Retention-based deletion of old `aidb_otel.*` rows |

### Purpose Decision Auditing

Every `execute_sql_as_role` call records a `purpose_decision` OTel span. A denied caller receives the `decision_id` inside the tool error message.

---

## HCP Model Sync

```sql
aidb.sync_hcp_models()
```

Auto-discovers and registers models hosted on EDB's Hybrid Control Plane. Returns a list of registered model names.

---

## PostgreSQL Distributed (PGD) Considerations

- Pipeline state tables gain a `_<pgd_node_group>` suffix under PGD.
- `action_log` is `SELECT` and `INSERT` only (no UPDATE/DELETE), so an agent cannot rewrite its own audit history.
- `mcp_registry.headers` and `headers_env` have table-level `SELECT` revoked with column allow-list granted.

---

## GUC Quick Reference (Full Surface)

The full GUC surface spans several subsystems. Consult source (`GucRegistry::define_*` call sites) for the exhaustive list. The most operationally relevant:

| GUC | Type | Restart? | Description |
|---|---|---|---|
| `aidb.max_threads` | INTEGER | Yes | Thread pool size for local model inference (default: half CPUs, min 1, max 1024) |
| `aidb.pipeline_error_warnings` | BOOLEAN | No | Emit per-error WARNING to PostgreSQL log (default: true). Errors always persist to the error log table. |
| `aidb.enable_memory_worker` | BOOLEAN | No | Enables the Agent Memory background dispatcher |
| `aidb.agent_session_source` | TEXT | No | `action_log` (default) or `memory` — where Agent Hub reads session history |
| `aidb.agent_memory_namespace` | TEXT | No | Default Agent Memory namespace (default: `pg_agents`) |
| `aidb.egress_allowlist` | TEXT | No | Allow-listed hosts for outbound network calls |
| `aidb.allow_insecure_egress` | BOOLEAN | No | Bypass egress restrictions (default: false) |
| `aidb.allow_insecure_tls` | BOOLEAN | No | Skip TLS verification (default: false) |
| `aidb.env_var_allowed_prefix` | TEXT | No | Prefix allow-list for `credentials_env` / `headers_env` |
| `aidb.k8s_secret_allowed_path_prefix` | TEXT | No | Path allow-list for `credentials_k8s_secret` |
| `aidb.otel_client` | TEXT | No | Telemetry export mode |
| `edb.endpoints_mcp_enabled` | BOOLEAN | Postmaster | Enable standalone MCP endpoint server |
| `edb.endpoints_mcp_host` | TEXT | Sighup | MCP server listen host (default: `127.0.0.1`) |
| `edb.endpoints_mcp_port` | INTEGER | Sighup | MCP server listen port (default: `8765`, range 1024–65535) |
| `edb.endpoints_mcp_restart_max` | INTEGER | Sighup | Max restarts in window (default: 5) |
| `edb.endpoints_mcp_restart_window_secs` | INTEGER | Sighup | Restart budget window in seconds (default: 60) |
| `edb.endpoints_mcp_database` | TEXT | Sighup | Database the endpoint connects to (default: `postgres`) |
| `edb.endpoints_mcp_tls_cert` | TEXT | Sighup | TLS certificate path (must be paired with key) |
| `edb.endpoints_mcp_tls_key` | TEXT | Sighup | TLS key path (must be paired with cert) |
