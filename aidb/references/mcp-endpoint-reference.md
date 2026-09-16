# AIDB MCP Endpoint Reference

Source: `agent_docs/mcp_endpoint.md`, `edb-endpoints/src/`, `src/bgworker/worker_mcp_supervisor.rs`.

The MCP endpoint is the `edb-endpoints` binary — a standalone streamable-HTTP MCP server supervised as an AIDB background worker. It exposes the Tools Hub catalog and `execute_sql` to **external** MCP clients (e.g., Claude Desktop, other AI agents). This is separate from the in-database Agent Hub; it targets external callers, not in-database agents.

---

## Architecture

```text
External MCP client (HTTP Basic auth)
        ↓
edb-endpoints server (port 8765 by default)
        ↓  (per-call fresh Postgres connection, authenticated as caller's role)
aidb.get_mcp_tools()  /  aidb.run_tool()  /  execute_sql
```

Key properties:
- Runs SQL as the **calling client's own authenticated PostgreSQL role** (HTTP Basic → SCRAM-SHA-256)
- No role synthesis; no allowlist — the role is whatever the client authenticated as
- Tool calls dispatch through `aidb.run_tool()`, so Purpose Registry governance applies
- `execute_sql` returns results with a fixed type encoding independent of session GUCs

---

## Enabling the Endpoint

The MCP endpoint requires a postmaster restart to enable:

```sql
ALTER SYSTEM SET edb.endpoints_mcp_enabled = on;
-- Then restart PostgreSQL
```

Verify the supervisor is running:
```sql
SELECT pid, backend_type FROM pg_stat_activity
WHERE backend_type LIKE '%mcp%' OR backend_type LIKE '%endpoint%';
```

---

## GUC Configuration

| GUC | Type | Default | Reload | Description |
|---|---|---|---|---|
| `edb.endpoints_mcp_enabled` | bool | off | Postmaster | Master switch; requires restart |
| `edb.endpoints_mcp_host` | string | `127.0.0.1` | Sighup | Listen host |
| `edb.endpoints_mcp_port` | int | `8765` | Sighup | Listen port (1024–65535) |
| `edb.endpoints_mcp_database` | string | `postgres` | Sighup | Target database |
| `edb.endpoints_mcp_restart_max` | int | `5` | Sighup | Max restarts in window |
| `edb.endpoints_mcp_restart_window_secs` | int | `60` | Sighup | Restart budget window (seconds) |
| `edb.endpoints_mcp_tls_cert` | string | — | Sighup | TLS cert path (must pair with key) |
| `edb.endpoints_mcp_tls_key` | string | — | Sighup | TLS key path (must pair with cert) |
| `edb.endpoints_mcp_command_path` | string | — | Sighup | Custom binary path (defaults to `<bindir>/edb-endpoints`) |

**Reload**: Changes to Sighup GUCs take effect on `SELECT pg_reload_conf()`. Changed `ChildSpec` values (host/port/database/TLS) cause the child to be restarted.

---

## Security Rules

- **Non-loopback host without TLS is rejected**: supervisor idles rather than burning restart budget
- **TLS cert and key must both be present or both absent** — one without the other is rejected
- **Restart budget exhaustion**: after `restart_max` restarts within `restart_window_secs`, supervisor idles until the window clears; `SELECT pg_reload_conf()` re-arms
- **Orphan safety**: child watches its stdin pipe (`--supervisor-stdin-lifeline`) and exits on EOF — a SIGKILLed supervisor cannot leak an endpoint process

---

## Type Encoding for `execute_sql`

Results are encoded in Rust independent of session GUCs (`DateStyle`, `IntervalStyle`, etc.):

| PostgreSQL Type | JSON Encoding |
|---|---|
| `bool`, `int2`, `int4`, `float4`, `float8` | Native scalar |
| `int8` | Number if within ±(2^53-1), decimal string otherwise |
| `json`, `jsonb` | The document itself |
| `text`, `varchar`, `bpchar`, enums | String |
| `timestamptz` | RFC 3339 UTC with `Z` suffix |
| `timestamp`, `date`, `time` | ISO 8601 string |
| `interval` | ISO 8601 duration (e.g., `P1Y2M3DT4H5M6S`) |
| `numeric` | Full-precision decimal string |
| `bytea` | Padded base64 |
| arrays | JSON array (nested per dimension) |
| `money`, composites, PostGIS | Error with `cast it to ::TEXT` hint |

**Note:** Duplicate column names in a result (`SELECT 1 AS id, 2 AS id`) error rather than silently dropping a column.

---

## Connecting an External MCP Client

Example configuration for Claude Desktop or similar (connect to the endpoint as a PostgreSQL user with `aidb_users` membership):

```json
{
  "mcpServers": {
    "edb-aidb": {
      "url": "http://localhost:8765/mcp",
      "transport": "streamable_http",
      "auth": {
        "type": "basic",
        "username": "agent_alice",
        "password": "..."
      }
    }
  }
}
```

The connecting role must be a member of `aidb_users` to access the Tools Hub catalog. A non-member gets the PostgreSQL denial passed through as an MCP `isError` response.

---

## Troubleshooting

**Endpoint not starting:**
1. Check `edb.endpoints_mcp_enabled = on` and that a postmaster restart was done
2. Non-loopback host without TLS is silently rejected — check GUC values
3. TLS: both cert and key must exist; one without the other is rejected
4. Binary not found: `NotFound`/`PermissionDenied` spawn failures refund their budget slot and idle (retrying cannot heal a missing binary)

**Restart budget exhausted:**
- Default: 5 restarts in 60 seconds
- After exhaustion: supervisor idles; clear the window, then `SELECT pg_reload_conf()` to re-arm

**Tool cache stale:**
- MCP tool cache ages out after 60 minutes
- Refresh: `SELECT aidb.refresh_mcp_tools('<server_name>')`
- Invoking a tool does **not** refresh its cache entry

**Client auth failure:**
- HTTP Basic credentials are forwarded verbatim to PostgreSQL over SCRAM-SHA-256
- Check that `pg_hba.conf` allows SCRAM for the connecting host and database
