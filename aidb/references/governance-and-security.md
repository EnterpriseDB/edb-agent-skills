# AIDB Governance, Security & Observability Reference

## Roles

aidb creates these group roles. Grant them to login roles; they are not login roles themselves.

| Role | Grants |
|---|---|
| `aidb_users` | Use aidb: its functions and tables |
| `aidb_admins` | Member of `aidb_users`; may also reference server environment variables and mounted secret files (`credentials_env`, `credentials_k8s_secret`, `headers_env`, `headers_k8s_secret`) |
| `aidb_governance` | Manage purposes, read the `aidb_otel` telemetry tables, run retention cleanup |
| `aidb_otel_writer` | Send OTLP telemetry into `aidb_otel` |

---

## Purpose Registry

A **purpose** is a named policy scope that resolves to exactly one PostgreSQL role, providing role-level governance over agents.

```sql
-- Create a purpose (errors if the name is already active; a retired name is revived)
aidb.create_purpose(name TEXT, role TEXT, description TEXT DEFAULT NULL)

-- Update a purpose (only non-NULL arguments are changed)
aidb.update_purpose(name TEXT, role TEXT DEFAULT NULL, description TEXT DEFAULT NULL)

-- Soft-delete a purpose (sets deleted_at; NEVER physically removes the row
-- because agents may still reference the name)
aidb.delete_purpose(name TEXT)
```

Only members of `aidb_governance` (and superusers) can call `create_purpose`, `update_purpose` and `delete_purpose`; an `aidb_users` member gets "permission denied". Grant it with `GRANT aidb_governance TO <admin_role>;`. Any `aidb_users` member can read `aidb.purpose_registry`.

`aidb.purpose_registry` is a read-only view: `name`, `role`, `description`, `created_at`, `updated_at`, `deleted_at`.

`create_purpose` on a retired (soft-deleted) name revives it with the new role. `update_purpose` refuses a retired purpose. An agent whose purpose is retired fails its turn instead of running as the caller. `create_agent`/`update_agent` reject a purpose that was never registered.

A `purpose` is assigned to agents via `create_agent`/`update_agent`'s `purpose` argument. Every tool SQL statement an agent runs under a purpose-resolved role records one OTel span named `purpose_decision`. Its attributes are prefixed `aidb.agent.decision.`: `decision_id`, `action_status` (`allowed` or `denied`), `interaction_point` (`sql_exec`), `principal`, `agent_role`, `effective_purpose`, `denial_reason`, `denial_class`, `denial_funcname`, `denial_message`, `referenced_relations`, `referenced_relations_total`, plus `db.query.text`, `db.response.status_code`, `gen_ai.agent.name` and `gen_ai.conversation.id`. Agents with no purpose, direct `aidb.run_tool` calls, and MCP endpoint calls record no decision. With `aidb.otel_client = 'database'` the spans are stored in `aidb_otel.traces`, where `decision_id`, `decision_status` and `agent_name` are indexed columns.

**Denial reasons:** `role_not_found` (the purpose's role does not exist), `not_role_member` (the caller cannot SET ROLE to it), `escalation_blocked` (the SQL tried to change identity, such as `RESET ROLE`, `SET ROLE`, `SET SESSION AUTHORIZATION`, `RESET ALL`, `DISCARD ALL`, or a privilege-gated `set_config`), `engine_privilege_denied` (any other permission error, SQLSTATE 42501), `pre_execution_rejected` (rejected before execution: multiple statements, a non-invocable statement type, or a parse failure).

> An action whose SQL is valid but then fails for an unrelated reason (missing table or column, model error) still records as `allowed`. A statement that fails to parse is recorded as `denied` with `pre_execution_rejected`.

---

## Egress & Credential Security

### Egress Settings

All outbound network calls (model providers, MCP servers) are governed by these superuser-only settings:

- `aidb.egress_allowlist`: comma-separated hostnames or CIDR ranges; a leading dot allows subdomains. When empty, aidb uses the shared `edb.egress_allowlist`. When both are unset, outbound calls are not restricted. It is also the only destination limit for env or mounted-file secrets that have no `_HOST` companion.
- `aidb.allow_insecure_egress` (BOOLEAN, default off): permits plaintext `http://` requests, but only to `localhost` or a literal loopback or private (RFC 1918 / unique-local) IP address. Public hosts always require HTTPS, and a plaintext DNS name other than `localhost` is refused. Does not affect the egress allowlist or certificate checks. Logs a rate-limited WARNING per plaintext request.
- `aidb.allow_insecure_tls` (BOOLEAN, default off): skips certificate validation on every outbound HTTPS request, to any host, including ones carrying credentials. Still requires `https://`. For a private CA, prefer `tls_config.ca_path` in the model config; a model that sends a secret is refused if its config sets `tls_config.insecure_skip_verify`.

### Credential Sources

Models accept credentials through one of these mutually exclusive mechanisms:

| Parameter | Description |
|---|---|
| `credentials JSONB` | `{"api_key": "..."}` or `{"basic_auth": "..."}` only. Never put secrets in `config`: `create_model` rejects `api_key` or `basic_auth` at any depth there. |
| `credentials_env TEXT` | Name of a server environment variable |
| `credentials_k8s_secret TEXT` | Path of a mounted Kubernetes secret file |
| `credential TEXT` | Name of an existing credential made with `aidb.create_credential` |

Inline `credentials` create a private credential named after the model (`<schema>.<model>`), owned by the caller. The secret is stored as the options of that credential's PUBLIC user mapping. `aidb.list_models()`, `aidb.get_model()` and `aidb.credentials` never return it (`aidb.credentials` shows only `has_secret`), but the credential's owner role and superusers can read it from `pg_user_mappings`.

Only superusers and members of `aidb_admins` may use `credentials_env` or `credentials_k8s_secret`. The variable name must start with `aidb.env_var_allowed_prefix` (default `AIDB_`); the path must lie under `aidb.k8s_secret_allowed_path_prefix` (default `/var/run/secrets/aidb`). An optional companion `NAME_HOST` variable, or `<file>_HOST` file, limits which hosts the secret may be sent to.

To bind an existing credential, pass `credential => '<name>'`, created with `aidb.create_credential(name, credentials | credentials_env | credentials_k8s_secret, endpoint, share)`. `endpoint` is required and lists the hosts the secret may be sent to (`'*'` allows any, with a NOTICE). A credential is private to its owner unless `share => true`. Manage with `aidb.update_credential`, `aidb.delete_credential` and the `aidb.credentials` view.

`aidb.import_mcp_tools` takes one of `headers` (inline JSONB), `headers_env` (environment variable name) or `headers_k8s_secret` (mounted secret path). The last two are limited to members of `aidb_admins` and superusers.

Agent and tool SQL that reads user-mapping options is refused, so an agent cannot read stored secrets even when its role owns them.

`SELECT * FROM aidb.audit_leaked_credentials();` lists models whose config was found to hold a secret key (`model_name`, `detected_key`, `detected_at`).

---

## Read-Only Enforcement (Agent Hub)

`read_only` defaults to on when the server is a hot standby, and off otherwise. An explicit argument always wins.

Three enforcement layers:

1. **Static classification**: only `SELECT`, `EXPLAIN`, `COPY TO` and `PREPARE` count as reads; anything unrecognized is treated as a write (fail-closed).
2. **Tool allow-list**: all MCP tools (unclassified foreign tools) are blocked regardless of their declared flag.
3. **PostgreSQL level**: `SET LOCAL transaction_read_only = on` inside a discarded subtransaction. This is the authoritative layer.

A read-only run creates no task row and returns `NULL` for `conversation_id`.

> Residual gap: a VOLATILE function writing outside the database (dblink, file I/O) is not caught by any layer.

---

## Agent Role Model

Three distinct identities:

| Identity | Description |
|---|---|
| `aidb.agents.purpose` | Names a purpose in `aidb.purpose_registry`. That purpose's `role` is the SQL execution identity for the agent's tool calls, resolved at every run, so `aidb.update_purpose` repoints every agent holding it. An agent with no purpose runs tool SQL as the calling user. |
| Per-task `role` | Stored on task queue and action log rows. |
| `initiated_by` | Set to `session_user`; the RLS key for all action tables. |

Assigning a purpose checks nothing about the role. At each tool call aidb checks that the role exists and that the calling user may `SET ROLE` to it (`pg_has_role(current_user, role, 'SET')` on PostgreSQL 16+, `'MEMBER'` on 14 and 15). Failures are denied as `role_not_found` or `not_role_member`.

An `aidb_users` member can read only the agent action rows they initiated. Superusers and BYPASSRLS roles see every row.

aidb never switches role with a bare `SET LOCAL ROLE`. It switches identity inside a subtransaction under a security-restricted context, so SQL in a tool cannot `RESET ROLE`, `SET ROLE`, `SET SESSION AUTHORIZATION` or `set_config('role', ...)` back to the caller's privileges. Such attempts are denied as `escalation_blocked`.

**Delegation cannot escalate privileges.** A delegate always inherits the parent's `read_only` flag. When the parent runs under a purpose, the delegate also runs under the parent's role and purpose, ignoring its own. When the parent has no purpose (runs as the caller), the delegate resolves its own purpose.

---

## MCP Endpoint Authentication

The MCP endpoint authenticates each call with HTTP Basic (pg_hba must use a password method such as `scram-sha-256`) or an OAuth Bearer token (pg_hba `oauth`). Connections admitted by `trust`, `peer` or `ident` are refused. Tools run as the authenticated role, without a purpose, and record no `purpose_decision`.

---

## OpenTelemetry (OTel) Telemetry

AIDB exports OTel traces, logs, and metrics, optionally stored in the `aidb_otel.traces`, `aidb_otel.logs` and `aidb_otel.metrics` tables.

### GUC: `aidb.otel_client`

| Value | Behavior |
|---|---|
| `noop` (default) | Nothing is exported; no-op instruments |
| `database` | Written to the `aidb_otel` tables (readable by `aidb_governance` and superusers only) |
| `stdout` | Written to process stdout |
| `log` | Written to PostgreSQL server log |
| `grpc` | Exported via OTLP/gRPC (requires `otel_client_grpc` build feature) |

`aidb_users` can write telemetry but not read it. `aidb_otel_writer` is the role for external senders that call the ingest functions (`aidb_otel.ingest_traces_json`, `aidb_otel.ingest_traces_binary`, `aidb_otel.ingest_logs_json`, `aidb_otel.ingest_logs_binary`, `aidb_otel.ingest_metrics_json`, `aidb_otel.ingest_metrics_binary`).

### Background Workers

| Worker | Purpose |
|---|---|
| `worker_otel_exporter` | Delivers `aidb_otel` rows to the OTLP/HTTP endpoint |
| `worker_otel_cleaner` | Retention-based deletion of old `aidb_otel` rows |

Both workers are off by default. The exporter needs `aidb.otel_exporter_enabled = on` and `aidb.otel_exporter_endpoint` (an OTLP/HTTP collector URL). The cleaner needs `aidb.otel_cleaner_enabled = on` and keeps `aidb.otel_cleaner_retention_days` (default 365). `aidb_governance` can run `SELECT aidb.run_otel_retention_cleanup();` by hand. Each setting can be overridden per database with `ALTER DATABASE`.

### Purpose Decision Auditing

Every agent tool SQL call run under a purpose-resolved role records one `purpose_decision` span. A denied call's tool error ends with ` [decision_id=<uuid>]`, which matches `aidb_otel.traces.decision_id`.

---

## Hybrid Manager Model Sync

```sql
aidb.sync_hcp_models()
```

Syncs models served by EDB Hybrid Manager model serving into the registry. Returns one row per model: `status` (`created`, `unchanged`, `deleted`, `skipped`), `model`, `reason`. It deletes previously synced HCP models that are no longer served.

---

## Privileges

- `aidb_users` may only SELECT and INSERT the agent action log, never UPDATE or DELETE it, so an agent cannot rewrite its own audit history.
- `aidb_users` can read only `name, url, transport, tool_filter, created_at, updated_at, owner` of `aidb.mcp_registry`; `headers`, `headers_env` and `headers_k8s_secret` are hidden, and rows are written only through `aidb.import_mcp_tools`, `aidb.refresh_mcp_tools` and `aidb.delete_tool`.

---

## PostgreSQL Distributed (PGD) Considerations

- Pipeline state tables gain a `_<pgd_node_group>` suffix under PGD.

---

## GUC Quick Reference

List every setting with `SELECT name, setting, context, short_desc FROM pg_settings WHERE name LIKE 'aidb.%' OR name LIKE 'edb.%' ORDER BY name;`. Most `aidb.*` settings are superuser-only (`context = superuser`). The most operationally relevant:

| GUC | Type | Who can set / when it applies | Description |
|---|---|---|---|
| `aidb.max_threads` | INTEGER | Superuser; SET applies to the session, ALTER SYSTEM after `pg_reload_conf()` | Threads for local model inference (default 0 = half the CPUs; range 0-1024) |
| `aidb.pipeline_error_warnings` | BOOLEAN | Superuser | Emit per-error WARNING to PostgreSQL log (default: true). Errors always persist to the error log table. |
| `aidb.agent_memory_enable_worker` | BOOLEAN | Superuser; per database via ALTER DATABASE | Enables the Agent Memory background worker (default: off) |
| `aidb.agent_session_source` | TEXT | Superuser | `action_log` (default) or `memory`: where Agent Hub reads session history |
| `aidb.agent_memory_namespace` | TEXT | Superuser | Default Agent Memory namespace (default: `pg_agents`) |
| `aidb.egress_allowlist` | TEXT | Superuser | Hostnames or CIDR ranges allowed for outbound calls; empty falls back to `edb.egress_allowlist`; both unset = no check |
| `aidb.allow_insecure_egress` | BOOLEAN | Superuser | Permit plaintext `http://` to localhost or loopback/private IPs only (default: false) |
| `aidb.allow_insecure_tls` | BOOLEAN | Superuser | Skip certificate validation on every outbound HTTPS request (default: false) |
| `aidb.env_var_allowed_prefix` | TEXT | Superuser | Required prefix for `credentials_env` / `headers_env` (default `AIDB_`; empty disables the check) |
| `aidb.k8s_secret_allowed_path_prefix` | TEXT | Superuser | Required path prefix for `credentials_k8s_secret` / `headers_k8s_secret` (default `/var/run/secrets/aidb`; empty disables the check) |
| `aidb.otel_client` | ENUM (`noop`, `database`, `stdout`, `log`; `grpc` only in gRPC-enabled builds) | Superuser; read once per backend, so a change applies to new connections | Telemetry exporter (default `noop`) |
| `edb.endpoints_enabled` | BOOLEAN | Postmaster (restart) | Master switch for the endpoint supervisor (default: on) |
| `edb.endpoints_host` | TEXT | Sighup | Default listen host for endpoints (default `127.0.0.1`) |
| `edb.endpoints_database` | TEXT | Sighup | Comma-separated databases every endpoint serves, each at `/mcp/<name>` and `/otlp/<name>`; the first is also served at `/mcp` (default `postgres`) |
| `edb.endpoints_tls_cert` | TEXT | Sighup | Default TLS certificate path (pair with key) |
| `edb.endpoints_tls_key` | TEXT | Sighup | Default TLS key path (pair with cert) |
| `edb.endpoints_oauth_issuer` | TEXT | Sighup | OAuth issuer for Bearer auth (empty = OAuth off); see also `edb.endpoints_oauth_scopes`, `edb.endpoints_oauth_role_claim`, `edb.endpoints_oauth_audience` |
| `edb.endpoints_restart_max` | INTEGER | Sighup | Max endpoint restarts in the window (default 5, range 1-100) |
| `edb.endpoints_restart_window_secs` | INTEGER | Sighup | Restart budget window in seconds (default 60, range 1-3600) |
| `edb.endpoints_mcp_enabled` | BOOLEAN | Reload (SIGHUP); restart once if neither endpoint was on at startup | Serves the MCP endpoint (default: off). Requires `edb.endpoints_enabled`. |
| `edb.endpoints_mcp_host` | TEXT | Sighup | MCP listen host (default empty: uses `edb.endpoints_host`, default `127.0.0.1`) |
| `edb.endpoints_mcp_port` | INTEGER | Sighup | MCP server listen port (default: `8765`, range 1024-65535) |
| `edb.endpoints_mcp_database` | TEXT | Sighup | Comma-separated databases the MCP endpoint serves, each at `/mcp/<name>`; the first is also served at `/mcp` (default empty: uses `edb.endpoints_database`, default `postgres`) |
| `edb.endpoints_mcp_tls_cert` | TEXT | Sighup | MCP TLS certificate path (must be paired with key) |
| `edb.endpoints_mcp_tls_key` | TEXT | Sighup | MCP TLS key path (must be paired with cert) |
| `edb.endpoints_otlp_enabled` | BOOLEAN | Sighup | Serves the OTLP receiver endpoint (default: off) |
| `edb.endpoints_otlp_port` | INTEGER | Sighup | OTLP receiver port (default 8766) |

MCP-specific OAuth overrides are `edb.endpoints_mcp_oauth_issuer`, `edb.endpoints_mcp_oauth_resource_url`, `edb.endpoints_mcp_oauth_scopes`, `edb.endpoints_mcp_oauth_role_claim` and `edb.endpoints_mcp_oauth_audience`. The `edb.endpoints_mcp_restart_max` and `edb.endpoints_mcp_restart_window_secs` names are deprecated aliases that log a warning when set above 0, and still take precedence over the new names.
