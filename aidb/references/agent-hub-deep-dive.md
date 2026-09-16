# AIDB Agent Hub & Tools Hub — Deep-Dive Reference

Source: `agent_docs/agent_hub.md`, `src/api/agent_hub.rs`, `src/api/tools_hub.rs`.

---

## Reasoning Loop Constants (source: `aidb-agents/src/reasoning_loop.rs`)

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Max loop turns before giving up |
| `MAX_DELEGATION_DEPTH` | 11 (depth 1 = top-level call) | Prevents exponential delegate cost |
| `MAX_UNKNOWN_ERROR_FAILURES` | 3 | Unclassifiable model errors before halt |
| `MAX_RATE_LIMIT_RETRIES` | 5 | Rate-limit retries before halt |
| `MAX_REPEATED_FAILED_TOOL_CALL_OCCURRENCES` | 3 | Same tool call failing identically |
| `MAX_REPEATED_SUCCESSFUL_TOOL_CALL_OCCURRENCES` | 2 | Same successful call repeating (triggers proactive summarize) |
| `MAX_DELEGATION_DEPTH_LIMIT_OCCURRENCES` | 1 | Exponential depth chain guard |
| `ATTEMPT_COMPLETE_GRACE_ITERATIONS` | 3 | Extra rounds granted after budget exceeded with `attempt_complete` strategy |

## Budget Strategies

| Strategy | On budget exceeded |
|---|---|
| `ignore` | Push a warning onto metrics, keep going |
| `error` | Halt; turn ends as error |
| `summarize` | Ask model for a closing summary, then finish |
| `attempt_complete` | Grant 3 more rounds with "finalize now" instruction, then halt |

When `max_iterations` is unset and strategy is `attempt_complete` (the default), the config synthesizes `MAX_REASONING_ITERATIONS - ATTEMPT_COMPLETE_GRACE_ITERATIONS - 1` as the effective limit so the strategy has a real limit to grace against.

## Tool Execution Flow (in order)

1. `run_tool` normalizes NULL `arguments` → `{}`
2. `lookup_tool_declaration` queries `aidb.tools` ordered: native → sql → mcp (dispatch precedence)
3. `validate_tool_arguments` — checks argument keys (not types); MCP treats absent `additionalProperties` as closed
4. `invoke_tool` dispatches:
   - **native**: builds `NativeInvocationPlan { sql, bindings }`, runs via `execute_sql_as_role`
   - **sql**: rewrites `${name}` → positional `$n`; wraps in `WITH t AS (...) SELECT coalesce(jsonb_agg(row_to_json(t)), '[]')`. Read-only SQL tools run in a discarded subtransaction with `SET LOCAL transaction_read_only = on`.
   - **mcp**: joins cache to registry, posts through `EgressBlockingClient`. MCP `isError: true` responses become SQL errors for `run_tool` callers.

## `agent_converse` Execution Order

1. `DelegationDepthGuard::enter()` — top-level turn is depth 1
2. `load_agent_config` — read agent definition
3. Resolve role: `parent_role.or(agent.role)` — parent always wins
4. Resolve output fields — call-time `output_type` overrides stored one
5. `effective_read_only(arg, pg_is_in_recovery())` — read replica auto-enables read-only
6. `load_tools` — filters by `is_tool_allowed_in_read_only_mode`, appends `sleep` if under `max_tools`
7. `load_delegates` — each delegate appears as a tool to the model
8. `run_reasoning_loop` — max `MAX_REASONING_ITERATIONS` iterations; each: deadline → budget → thought assembly → optional compaction → model call → tool calls or answer
9. `finalize_task` — writes terminal status, notifies `aidb_agent_conversation` channel

## Error Conventions

- **`agent_converse`** and agent management functions: **never raise** — failed turn in `error` column, so the caller's transaction is not aborted
- **Tools Hub functions**: raise normally on error
- A **read-only** run: uses `NullActionRecorder`, no task row, returns `NULL` `conversation_id`

## Native Tools Catalog

The `NATIVE_TOOLS` registry (source: `aidb-tools/src/native_tools.rs`) always includes:
- `sleep` — always available, backs `aidb.sleep(seconds)`
- `memory_search` — available on read-only runs; uses `session_user` and GUC namespace; results capped at `MAX_MEMORY_SEARCH_RESULTS` (50)
- Catalog discovery tools (all capped at `LISTING_ROW_CAP`): `catalog_list_schemas`, `catalog_list_objects`, `catalog_get_object_details`, `catalog_list_relations`, `catalog_list_sequences`, `catalog_list_extensions`
- `analyze_db_health`

## Delegation Rules

- A delegate appears as a tool to the parent model
- Delegate inherits parent's `read_only` flag and role — cannot escalate
- Every recursive delegate call gets a fresh budget
- `MAX_DELEGATION_DEPTH` (11) = total depth including top level

## Role Switching Safety

- Always via `execute_sql_as_role` — never bare `SET LOCAL ROLE`
- Prevents SQL inside the tool from issuing `RESET ROLE` to regain caller privileges
- Every `execute_sql_as_role` for a purpose records a `purpose_decision` OTel span
- `action_log` is `SELECT` + `INSERT` only — an agent cannot rewrite its own audit history

## MCP Tool Caveats

- Cache ages out after 60 minutes; only `aidb.refresh_mcp_tools()` refreshes it
- MCP `sse` transport is stored but handled identically to `streamable_http` — `initialize` handshake unimplemented
- All MCP tools blocked in read-only mode (unclassified foreign tools)

## Views for Inspection

| View | Contents |
|---|---|
| `aidb.tools` | Union of native tools, SQL tools, MCP cached tools |
| `aidb.agents` | Agent definitions |
| `aidb.agent_tasks` | Per-turn task queue (caller-facing) |
| `aidb.conversations` | Conversation metadata |
| `aidb.conversation_log` | Append-only audit of every action |
| `aidb.purpose_registry` | Purpose → role governance mappings |

## Not Yet Wired (scaffolding only, do not rely on)

- `aidb_internal.action_queue` — exists but nothing writes to it (future async phase)
- `agent_task_queue.agent_schedule_id` — no FK, nothing references it
- `action_*.decision_id` and `decision` — action gating, later phase
- `aidb.agents.preset` — stored but never resolved
- Conversation forking (`parent_conversation_id`, `forked_from_message_id`) — always NULL
- `agent_task_queue.blocking` — always true; only synchronous phase exists
