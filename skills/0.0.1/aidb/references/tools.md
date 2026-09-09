# AIDB Tools Reference

Signature reference for AIDB's unified tool catalog. Introduced in AIDB 7.6.0.

A tool is anything an agent can invoke by name. Three kinds, one catalog:

| `tool_type` | Source | Mutable |
|---|---|---|
| `native_tool` | AIDB's built-in catalog | No — can't be added, removed, or reconfigured |
| `sql_tool` | A parameterized SQL query you register | Yes |
| `mcp_tool` | Imported from an external MCP server | Via the server registration |

Grant an agent a tool by listing its name in `create_agent`/`update_agent`'s `tools` array. Tools can also be invoked directly from SQL (testing, development), and AIDB's whole catalog can be exposed over MCP to external agents.

## Catalog views

### `aidb.tools`

Read-only union of all three kinds.

| Column | Type | Description |
|---|---|---|
| `name` | text | Tool name |
| `description` | text | Shown to the model |
| `tool_type` | text | `'native_tool'`, `'sql_tool'`, or `'mcp_tool'` |
| `params` | jsonb | For native/SQL tools: array of `{name, type, description}`. For MCP tools: the server's raw JSON Schema `inputSchema` |
| `read_only` | boolean | Whether the tool is known never to write. **Always `false` for `mcp_tool`** |

A tool's exact parameters are always discoverable with `SELECT params FROM aidb.tools WHERE name = '<name>'`.

MCP entries are served from a cache — `aidb.tools` only shows entries cached within the last 60 minutes, so a `SELECT` never triggers a network call.

### `aidb.sql_tool_registry`

`name` (text, PK), `description` (text), `query_text` (text — with `${name}` placeholders), `read_only` (boolean), `return_type_hint` (`aidb.ToolParam[]`), `params` (`aidb.ToolParam[]`), `created_at`, `updated_at`.

Query this directly to see a SQL tool's stored query text; use `aidb.tools` for everything else.

### `aidb.mcp_registry`

`name` (text, PK), `url` (text), `transport` (text), `headers` (jsonb — **not readable by `aidb_users`**), `headers_env` (text), `tool_filter` (text[]), `created_at`, `updated_at`.

## Invoking a tool

`aidb.run_tool(name TEXT, arguments JSONB = '{}'::jsonb)` → `JSONB`

Arguments are validated against the tool's declared parameters. If a tool isn't found, the error suggests `aidb.refresh_mcp_tools()` in case an MCP cache expired.

## Custom SQL tools

### `aidb.create_sql_tool`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | TEXT | required | Unique across **every** tool type |
| `description` | TEXT | required | Shown to the model |
| `sql_statement` | TEXT | required | The query, with `${name}` placeholders |
| `params` | JSONB | required | From `aidb.tool_params()` |
| `read_only` | BOOLEAN | `true` | Enforced, not descriptive |
| `return_type_hint` | JSONB | NULL | Documents the return shape. Not currently surfaced to the model |

Returns `TEXT` — the tool's name. **Raises** if the name is taken by any tool type, or the statement isn't a single, invocable, read-only-compliant statement.

**Placeholders.** `${name}` values are bound as real query parameters, never string-interpolated. A parameter used several times need only be declared once. `params` is required — pass `aidb.tool_params()` with no arguments for a tool that takes none.

**Read-only enforcement works in both directions.** Left at the default `true`, registration fails immediately if `sql_statement` matches a recognized write pattern (`INSERT`, `UPDATE`, `DELETE`, …), so a mislabeled tool can't get through. A tool explicitly marked `read_only => false` is excluded from read-only agent runs, the same as an unclassified native or MCP tool. At invocation a read-only SQL tool runs under `SET LOCAL transaction_read_only = on`, scoped to the invocation by a discarded subtransaction, so the caller's transaction can still write afterwards.

`sql_statement` must be a single statement and cannot be a utility statement such as `EXPLAIN` or `COPY`. Trailing whitespace and a trailing `;` are stripped at registration.

### `aidb.delete_tool`

`(name TEXT)` → `TEXT`. Deletes a SQL tool, or an MCP server registration (**removing every tool that server advertised**). Raises if the name doesn't exist or is a native tool — native tools can't be deleted.

### Parameter helpers

| Function | Signature |
|---|---|
| `aidb.tool_param` | `(name TEXT, type TEXT, description TEXT)` → JSONB. All three required |
| `aidb.tool_params` | `(VARIADIC params JSONB[])` → JSONB. No arguments = empty parameter list |
| `aidb.param` / `aidb.params` | Generic constructors used by native tools that take an enum-constrained argument. `aidb.param()` also accepts `enum_values TEXT[]`. Not normally needed directly |

## MCP tools

### `aidb.import_mcp_tools`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | TEXT | required | Unique server registration name |
| `url` | TEXT | required | MCP endpoint URL |
| `transport` | TEXT | `'streamable_http'` | `'streamable_http'` or `'sse'` |
| `headers` | JSONB | NULL | Request headers. Mutually exclusive with `headers_env` |
| `tool_filter` | TEXT[] | NULL | Import only these tool names |
| `headers_env` | TEXT | NULL | Env var holding the headers JSON, read fresh per use. Mutually exclusive with `headers` |

Returns `TEXT` — the server name. **Raises** if the name is taken, the URL is unreachable, or nothing matches `tool_filter`.

The server is validated via its `tools/list` endpoint before anything is stored, and the returned tools are cached immediately so they appear in `aidb.tools` without a separate refresh.

- Only `streamable_http` works end-to-end today; `sse` is accepted but not fully implemented.
- Reaching an MCP server is an outbound network call, subject to the `aidb.egress_allowlist` GUC like model calls and HuggingFace downloads.
- `headers_env` must start with the `aidb.env_var_allowed_prefix` prefix (`AIDB_` by default) — the same mechanism as `credentials_env` for model credentials.

### `aidb.refresh_mcp_tools`

`(name TEXT)` → `INTEGER` — the number of tools now cached (after `tool_filter`). Changing a server's registration also invalidates its cache automatically.

### `aidb.get_mcp_tools`

No arguments. Converts every registered tool — native, SQL, and MCP alike — into an MCP `tools/list`-shaped descriptor. Returns `TABLE(name TEXT, description TEXT, input_schema JSONB)`, one row per `aidb.tools` entry. This is how AIDB exposes its own catalog to external agents over MCP.

## Native tool catalog

Mostly thin wrappers around existing AIDB functionality. Every native tool must be listed explicitly in an agent's `tools` — **except `sleep`**, which is always available (unless the model's tool-count cap is already filled by the agent's other tools) because it backs the reasoning loop's pause/resume.

### Ad hoc SQL

| Tool | Read-only |
|---|---|
| `run_sql_query` | Yes — run a read-only SQL query, rows returned as JSON |

### Catalog discovery

Live, exact lookups against the Postgres catalog. No setup, always current, matched by exact name or SQL `LIKE` pattern. Complementary to the semantic KB tools: use semantic search to find the right table from a vague description, then catalog discovery for its exact current columns and foreign keys.

| Tool | Read-only | Description |
|---|---|---|
| `catalog_list_schemas` | Yes | List schemas; hides internal/extension schemas unless `include_system` |
| `catalog_list_objects` | Yes | List tables, views, matviews, foreign tables and/or functions. Narrow with `schema`, `object_type`, `name_pattern` |
| `catalog_get_object_details` | Yes | Full detail on one object: columns, PK/unique/FK for relations; args and return type for functions |
| `catalog_list_relations` | Yes | List FK relationships across a schema, or edges touching one table in either direction |

All four are privilege-aware and only return objects the calling user can see. Listings hide Postgres/AIDB internal and extension schemas by default — `catalog_get_object_details` deliberately does not, since naming an extension object explicitly is asking for it. **Listings are capped at 200 rows**; every row carries a `truncated` flag that is `true` when more exist, signalling the caller to narrow with `schema` or `name_pattern`. Optional text parameters treat an empty string as omitted.

### Semantic knowledge bases

| Tool | Read-only |
|---|---|
| `create_semantic_kb` | No |
| `delete_semantic_kb` | No |
| `list_semantic_kbs` | Yes |
| `get_semantic_kb` | Yes |
| `refresh_semantic_kb` | No |
| `update_semantic_kb_auto_processing` | No |
| `get_column_definitions` | Yes |
| `get_metadata` | Yes |
| `get_entity_definitions` | Yes |
| `semantic_kb_search` | Yes |
| `search_by_comment` | Yes |
| `semantic_kb_stats` | Yes |

Semantic *alias* functions are deliberately **not** exposed as tools — an agent discovers and reads schema but does not create or execute aliases.

### Models

`list_models` (Y), `get_model` (Y), `create_model` (N), `delete_model` (N), `sync_hcp_models` (N), `list_hcp_models` (Y), `get_hcp_models` (Y), `create_hcp_model` (N).

### Volumes

`list_volumes` (Y), `create_volume` (N), `delete_volume` (N), `list_volume_content` (Y), `read_volume_file` (Y), `write_volume_data` (N), `delete_volume_file` (N).

### Embedding and document processing

All read-only: `encode_text`, `encode_text_query` (query-tuned rather than index-tuned), `encode_image`, `generate_text`, `get_model_description`, `list_model_providers`, `rerank_text`, `get_adapter_embedding_dimensions`, `chunk_text`, `parse_html`, `parse_pdf`, `pdf_to_image`, `perform_ocr`, `summarize_text`.

### Agent management

| Tool | Read-only |
|---|---|
| `list_agents` | Yes |
| `create_agent` | No |
| `update_agent` | No |
| `delete_agent` | No |
| `sleep` | Yes — pause reasoning for N seconds, then resume |

Letting an agent create or reconfigure *other* agents (including itself) is a self-modifying capability — grant these deliberately.

### Pipelines

`search_pipelines` (Y), `get_pipeline` (Y), `delete_pipeline` (N), `run_pipeline` (N), `get_pipeline_metrics` (Y), `get_error_logs` (Y), `clear_error_logs` (N), `requeue_pipeline_errors` (N), `get_error_log_summary` (Y), `get_all_error_summaries` (Y).

**Pipeline authoring has no native tool.** `aidb.create_pipeline`/`update_pipeline` take up to 30 parameters across step/step-options pairs — impractical for one generic tool call — so an agent cannot author multi-step pipelines on its own.
