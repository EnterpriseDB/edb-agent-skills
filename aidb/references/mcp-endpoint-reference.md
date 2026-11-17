# AIDB MCP Endpoint Reference

The MCP endpoint is the `edb-endpoints` binary: a standalone streamable-HTTP MCP server supervised by an AIDB background worker. It exposes the Tools Hub catalog and `execute_sql` to **external** MCP clients (e.g., Claude Desktop, other AI agents). This is separate from the in-database Agent Hub; it targets external callers, not in-database agents. The same process can also run an OTLP/HTTP telemetry receiver.

---

## Architecture

```text
External MCP client (HTTP Basic or OAuth Bearer)
        ↓
edb-endpoints server (port 8765 by default)
        ↓  (per-call fresh Postgres connection over the local socket, as the caller's role)
aidb.get_mcp_tools()  /  aidb.run_tool()  /  execute_sql
```

Key properties:
- Runs SQL as the **calling client's own authenticated PostgreSQL role**. The client sends HTTP Basic credentials (checked by a pg_hba password method), or an OAuth Bearer token when OAuth is configured
- No role synthesis; no allowlist: the role is whatever the client authenticated as
- Catalog tools run via `aidb.run_tool()` as that role, under the role's own grants. There is no purpose decision: the Purpose Registry does not govern MCP calls and no `purpose_decision` span is recorded
- `tools/list` returns `execute_sql` plus every `aidb.tools` row the caller can see, including the native management tools such as `create_agent` and the credential tools. `execute_sql` is reserved: a catalog row with that name is dropped
- `execute_sql` returns results with a fixed type encoding independent of session GUCs
- Routes: `/mcp` (default database), `/mcp/<db>` (each listed database), plus `GET /health` and `GET /ready`

---

## Enabling the Endpoint

`shared_preload_libraries` must include `aidb`. The supervisor only starts if MCP or OTLP is on when PostgreSQL starts, so on a fresh install (both off) enable it and restart once:

```sql
ALTER SYSTEM SET edb.endpoints_mcp_enabled = on;
-- Then restart PostgreSQL (first time only)
```

After that, `edb.endpoints_mcp_enabled` reloads on SIGHUP: toggling it, and every other endpoint GUC except `edb.endpoints_enabled`, needs only `SELECT pg_reload_conf();`.

Verify the supervisor is running:
```sql
SELECT pid, backend_type FROM pg_stat_activity
WHERE backend_type LIKE '%endpoint%';
```

Then check `GET http://127.0.0.1:8765/health` and `/ready` return 200.

---

## pg_hba.conf Requirement

The endpoint connects to PostgreSQL over the local unix socket (TCP to `127.0.0.1` if `unix_socket_directories` is empty), so `local` lines apply. Every connection comes from the server host itself, wherever the MCP client is.

- Right after connecting, before any caller SQL runs, the endpoint checks which pg_hba method admitted the connection and refuses it unless that method checked the credential
- Accepted for Basic: `scram-sha-256` (recommended), `md5`, `password`, `ldap`, `radius`, `pam`, `bsd`
- Accepted for Bearer: `oauth` only
- `trust`, `peer` and `ident` connections are refused. With the common default `local all all trust` or `peer`, every call fails with `PostgreSQL admitted it without checking a password` until a `local` line for the agent roles uses a password method

```text
# pg_hba.conf: place before any trust/peer catch-all
local   all   +mcp_agents   scram-sha-256
```

Requires PostgreSQL 16 or later (or WarehousePG 7). OAuth Bearer requires PostgreSQL 18 and a validator module in `oauth_validator_libraries`.

---

## GUC Configuration

All endpoint GUCs are Sighup (apply at `pg_reload_conf()`) except `edb.endpoints_enabled`.

General settings (every server the process runs):

| GUC | Type | Default | Reload | Description |
|---|---|---|---|---|
| `edb.endpoints_enabled` | bool | on | Postmaster | Master off switch; `off` disables every server. Needs a restart |
| `edb.endpoints_host` | string | `127.0.0.1` | Sighup | Listen host |
| `edb.endpoints_database` | string | `postgres` | Sighup | Databases to serve, as an identifier list like `search_path`; the first is the default at `/mcp` |
| `edb.endpoints_tls_cert` | string | empty | Sighup | TLS certificate (PEM); both-or-neither with the key |
| `edb.endpoints_tls_key` | string | empty | Sighup | TLS private key (PEM) |
| `edb.endpoints_restart_max` | int | `5` | Sighup | Max spawns (including the first) within the window (1-100) |
| `edb.endpoints_restart_window_secs` | int | `60` | Sighup | Restart budget window in seconds (1-3600) |
| `edb.endpoints_command_path` | string | empty | Sighup | Binary path; empty derives `<bindir>/edb-endpoints` |

MCP settings (an empty one falls back to its general setting):

| GUC | Type | Default | Reload | Description |
|---|---|---|---|---|
| `edb.endpoints_mcp_enabled` | bool | off | Sighup | Serves MCP (see restart note above for a fresh install) |
| `edb.endpoints_mcp_host` | string | empty | Sighup | MCP listen host; empty uses `edb.endpoints_host` |
| `edb.endpoints_mcp_port` | int | `8765` | Sighup | Listen port (1024-65535) |
| `edb.endpoints_mcp_database` | string | empty | Sighup | MCP databases; empty uses `edb.endpoints_database` |
| `edb.endpoints_mcp_tls_cert` | string | empty | Sighup | MCP TLS cert; when it or the key is set, MCP uses this pair instead of the general pair (never mixed) |
| `edb.endpoints_mcp_tls_key` | string | empty | Sighup | MCP TLS key |

OTLP receiver: `edb.endpoints_otlp_enabled` (bool, off) and `edb.endpoints_otlp_port` (int, `8766`, must differ from the MCP port).

OAuth: general set `edb.endpoints_oauth_issuer`, `edb.endpoints_oauth_scopes`, `edb.endpoints_oauth_role_claim` (default `preferred_username`), `edb.endpoints_oauth_audience`; MCP-only set `edb.endpoints_mcp_oauth_issuer`, `edb.endpoints_mcp_oauth_resource_url`, `edb.endpoints_mcp_oauth_scopes`, `edb.endpoints_mcp_oauth_role_claim`, `edb.endpoints_mcp_oauth_audience`. MCP uses its own set when `edb.endpoints_mcp_oauth_issuer` is set, otherwise the general one. For MCP the issuer, resource URL (ending in `/mcp`), scopes and audience are required together. An empty issuer means OAuth is off.

Deprecated aliases (a set value still overrides its replacement and logs a warning):

| Deprecated GUC | Use instead | Unset value |
|---|---|---|
| `edb.endpoints_mcp_restart_max` | `edb.endpoints_restart_max` | `0` |
| `edb.endpoints_mcp_restart_window_secs` | `edb.endpoints_restart_window_secs` | `0` |
| `edb.endpoints_mcp_command_path` | `edb.endpoints_command_path` | empty |

**Reload**: Changes to Sighup GUCs take effect on `SELECT pg_reload_conf()`; a changed host, port, database list or TLS pair restarts the child.

---

## Serving Several Databases

List databases in `edb.endpoints_database` (or `edb.endpoints_mcp_database` for MCP only):

```sql
ALTER SYSTEM SET edb.endpoints_mcp_database = 'sales, hr';
SELECT pg_reload_conf();
```

- `/mcp` and `/mcp/` go to the first entry (`sales`)
- `/mcp/<name>` goes to that listed database (`/mcp/hr`)
- Any unlisted name gets a 404 with body `unknown database`
- Unquoted names fold to lowercase (`Sales` is served at `/mcp/sales`); double-quote to keep case: `'"Sales"'`
- Sessions are per database. Who may use a listed database is still decided by `CONNECT` privileges and `pg_hba.conf`

---

## Security Rules

- **Non-loopback host without TLS is rejected**: loopback means `127.0.0.1`, `::1` or `localhost`, each listener is checked against its own host and TLS pair, and the supervisor logs a warning and idles rather than burning restart budget
- **TLS cert and key must both be present or both absent**: one without the other is rejected
- **Restart budget exhaustion**: after `edb.endpoints_restart_max` spawns within `edb.endpoints_restart_window_secs`, the supervisor idles; `SELECT pg_reload_conf()` retries once the window has room
- **Orphan safety**: the child watches its stdin pipe and exits on EOF, so a SIGKILLed supervisor cannot leak an endpoint process

---

## Type Encoding for `execute_sql`

Results are encoded independent of session GUCs (`DateStyle`, `IntervalStyle`, `bytea_output`):

| PostgreSQL Type | JSON Encoding |
|---|---|
| `bool`, `int2`, `int4`, `float4`, `float8` | Native scalar; non-finite floats error |
| `int8` | Number if within ±(2^53-1), decimal string otherwise; if any value in the column overflows, the whole column is strings |
| `json`, `jsonb` | The document itself |
| `text`, `varchar`, `bpchar`, `name`, enums | String |
| `timestamptz` | RFC 3339 UTC with `Z` suffix |
| `timestamp`, `date`, `time`, `timetz` | ISO 8601 string |
| years outside 0000..9999 | ISO 8601 expanded form with a sign, such as `+10000-01-01` |
| years BC | Postgres spelling with a `BC` suffix |
| `interval` | ISO 8601 duration (e.g., `P1Y2M3DT4H5M6S`) |
| `numeric` | Full-precision decimal string |
| `uuid`, `inet`, `cidr`, `macaddr` | Postgres text spelling |
| `bytea` | Padded base64 |
| arrays | JSON array (nested per dimension) |
| ranges | Postgres literal such as `[1,10)` |
| `money`, composites, multiranges, PostGIS | Error with `cast it to ::TEXT` hint |

**Note:** Duplicate column names in a result (`SELECT 1 AS id, 2 AS id`) error rather than silently dropping a column.

---

## Connecting an External MCP Client

Each MCP client has its own configuration format; check the client's documentation. What every client needs:

- URL: `http://127.0.0.1:8765/mcp` (default database) or `http(s)://<host>:8765/mcp/<db>`. A non-loopback host requires TLS (`https://`)
- Transport: streamable HTTP
- Auth header: `Authorization: Basic <base64 of role:password>` for a PostgreSQL role, or `Authorization: Bearer <JWT>` when OAuth is configured

The connecting role must be a member of `aidb_users` to list the Tools Hub catalog. A non-member gets the PostgreSQL permission error as the `tools/list` JSON-RPC error, or as an `isError` result on `tools/call`.

---

## Troubleshooting

**Endpoint not starting:**
1. Check `edb.endpoints_mcp_enabled = on`, `edb.endpoints_enabled` is not off, and that PostgreSQL was restarted once if the supervisor was not already running
2. Non-loopback host without TLS is rejected with a warning in the server log; check GUC values
3. TLS: both cert and key must exist; one without the other is rejected
4. Binary not found: a missing or non-executable binary refunds its budget slot and idles (retrying cannot heal a missing binary); check `edb.endpoints_command_path`
5. PostgreSQL 14 or 15: the supervisor logs a warning and does not start the endpoint

**Restart budget exhausted:**
- Default: 5 spawns in 60 seconds (`edb.endpoints_restart_max`, `edb.endpoints_restart_window_secs`)
- After exhaustion: supervisor idles; once the window has room, `SELECT pg_reload_conf()` to retry

**Tool cache stale:**
- MCP tool cache ages out after 60 minutes
- Refresh: `SELECT aidb.refresh_mcp_tools('<server_name>')`
- Invoking a tool does **not** refresh its cache entry

**Client auth failure or every call refused:**
- `PostgreSQL admitted it without checking a password`: the pg_hba line matching the endpoint's local-socket connection is `trust`, `peer` or `ident`. Add a `local` line with `scram-sha-256` for the agent roles ahead of it, then `SELECT pg_reload_conf()`
- `PostgreSQL admitted it without checking the token`: a Bearer call matched a non-`oauth` pg_hba line
- Wrong password: check the role has a password set and `LOGIN`
- 404 `unknown database`: the path names a database not in `edb.endpoints_mcp_database` / `edb.endpoints_database`
