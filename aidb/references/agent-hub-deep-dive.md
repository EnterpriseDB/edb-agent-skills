# AIDB Agent Hub & Tools Hub Deep-Dive Reference

How `aidb.agent_converse` and `aidb.run_tool` behave internally: limits, execution order, role resolution, and the native tool catalog.

---

## Defining an Agent

```sql
aidb.create_agent(
    name                TEXT,
    instructions        TEXT,
    model               TEXT,
    tools               TEXT[]  DEFAULT NULL,  -- names from the aidb.tools view
    delegates           TEXT[]  DEFAULT NULL,  -- other agents this agent may hand work to
    purpose             TEXT    DEFAULT NULL,  -- a name from aidb.purpose_registry
    output_type         JSONB   DEFAULT NULL,  -- via aidb.output_type()/output_field()
    input_token_budget  INTEGER DEFAULT NULL,
    output_token_budget INTEGER DEFAULT NULL,
    max_iterations      INTEGER DEFAULT NULL,
    timeout             INTEGER DEFAULT NULL,
    budget_strategy     TEXT    DEFAULT 'attempt_complete',  -- 'ignore' | 'error' | 'summarize' | 'attempt_complete'
    preset              TEXT    DEFAULT NULL,
    plugins             TEXT[]  DEFAULT NULL   -- names from aidb.agent_plugins
) -- returns TABLE(error TEXT)
```

The agent's SQL execution role is not stored on the agent. It comes from its `purpose`: the matching row in `aidb.purpose_registry` names the role, looked up at call time. An agent with no purpose runs as the caller.

---

## Reasoning Loop Constants

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Max loop turns before giving up |
| `MAX_DELEGATION_DEPTH` | 11 (depth 1 = top-level call, so 10 delegate hops) | Prevents exponential delegate cost |
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

When `max_iterations` is unset and strategy is `attempt_complete` (the default), the config synthesizes `MAX_REASONING_ITERATIONS - ATTEMPT_COMPLETE_GRACE_ITERATIONS - 1` (21) as the effective limit so the strategy has a real limit to grace against.

## Tool Execution Flow (in order)

1. `run_tool` normalizes NULL `arguments` to `{}`
2. `lookup_tool_declaration` queries `aidb.tools` ordered: native, then sql, then mcp (dispatch precedence)
3. `validate_tool_arguments` checks argument keys, not types: unknown keys are refused, every declared SQL-tool parameter is required, and so is anything in an MCP schema's `required` list. MCP treats absent `additionalProperties` as closed
4. `invoke_tool` dispatches:
   - **native**: builds SQL plus bindings and runs it via `execute_sql_as_role`
   - **sql**: rewrites `${name}` to positional `$n`; wraps in `WITH t AS (...) SELECT coalesce(jsonb_agg(row_to_json(t)), '[]')`. Read-only SQL tools run in a discarded subtransaction with `SET LOCAL transaction_read_only = on`.
   - **mcp**: joins cache to registry, posts through the egress-checked HTTP client. MCP `isError: true` responses become SQL errors for `run_tool` callers.

## `agent_converse` Execution Order

1. `DelegationDepthGuard::enter()`: top-level turn is depth 1
2. `load_agent_config`, then the execution role: a delegate called with a parent grant (the parent ran with both a role and a purpose) uses the parent's role and purpose; otherwise `resolve_purpose_role(agent.purpose)` reads the role from `aidb.purpose_registry` at that moment. A retired purpose (`deleted_at` set) refuses the turn. No purpose means run as the caller
3. Resolve output fields: call-time `output_type` overrides stored one
4. `effective_read_only(arg, pg_is_in_recovery())`: read replica auto-enables read-only
5. `load_plugins`: resolves `agents.plugins` in order; plugin tools are appended after the agent's own tools (de-duplicated), and each plugin's name, description and `prompt_content` is appended to the system prompt under `# Additional capabilities`
6. `load_tools`: errors on a missing tool name, filters by `is_tool_allowed_in_read_only_mode`, appends `sleep` if under `max_tools`. Plugin tools count toward `max_tools` too
7. `load_delegates`: each delegate appears as a tool to the model
8. `run_reasoning_loop`: max `MAX_REASONING_ITERATIONS` iterations; each: deadline, budget, thought assembly, optional compaction, model call, tool calls or answer
9. `finalize_task`: writes terminal status, notifies `aidb_agent_conversation` channel

## Error Conventions

- **Never raise**: `create_agent`, `update_agent`, `delete_agent`, the agent plugin functions, `agent_converse` and `get_message`. A failure is reported in the `error` column, so the caller's transaction is not aborted
- **Raise normally**: `start_agent_session`, `get_conversation`, and the Tools Hub functions
- A **read-only** run: uses `NullActionRecorder`, no task row, returns `NULL` `conversation_id`

## Native Tools Catalog

The native catalog holds about 85 tools: SQL and catalog discovery (`run_sql_query`, `explain_query`, `catalog_list_schemas`, `catalog_list_objects`, `catalog_get_object_details`, ...), `analyze_db_health`, model and embedding functions, pipeline and error-log tools, semantic KB tools, volume and credential tools, agent management (`list_agents`, `create_agent`, `update_agent`, `delete_agent`), `memory_search`, `memory_add` and `sleep`. List them with:

```sql
SELECT name, read_only FROM aidb.tools WHERE tool_type = 'native_tool' ORDER BY name;
```

- An agent is offered only the tools named in its `tools` (plus its plugins' tools). `sleep` is the only tool appended automatically, and only if it fits under `max_tools`
- Write-classified tools (`read_only = false`) are hidden on read-only runs
- `memory_search(namespace, actor_id, query, max_results DEFAULT 10, session_id, agent_id)`: `namespace`, `actor_id` and `query` are required; `max_results` is capped at 50. The application supplies the scope; it is not derived from the database login. Available on read-only runs
- `memory_add` is its write companion and is not offered on read-only runs

## Delegation Rules

- A delegate appears as a tool to the parent model
- A delegate inherits the parent's `read_only` flag. When the parent runs with both a role and a purpose, the delegate also inherits both, overriding its own purpose. A parent running as the caller with no purpose hands nothing down, and the delegate resolves its own purpose
- Every recursive delegate call gets a fresh budget
- `MAX_DELEGATION_DEPTH` (11) = total depth including top level

## Role Switching Safety

- Always via `execute_sql_as_role`, never bare `SET LOCAL ROLE`
- Prevents SQL inside the tool from issuing `RESET ROLE` to regain caller privileges
- A `purpose_decision` OTel span is recorded only when both a role and a purpose are resolved for an agent tool call. An agent without a purpose, a direct `aidb.run_tool` call, and the MCP endpoint record none
- `action_log` is `SELECT` + `INSERT` only: an agent cannot rewrite its own audit history

## MCP Tool Caveats

- Cache ages out after 60 minutes; only `aidb.refresh_mcp_tools(name)` refreshes it
- MCP `sse` transport is stored but handled identically to `streamable_http`; the `initialize` handshake is unimplemented
- All MCP tools blocked in read-only mode (unclassified foreign tools)

## Views for Inspection

| Object | Kind | Contents |
|---|---|---|
| `aidb.tools` | view | Union of native tools, SQL tools, MCP cached tools |
| `aidb.agents` | table | Agent definitions (`purpose`, `tools`, `delegates`, budgets, `plugins`) |
| `aidb.agent_plugins` | view | Agent plugins: name, description, prompt_content, tools |
| `aidb.agent_tasks` | view | Per-turn task queue (caller-facing) |
| `aidb.conversations` | view | Conversation metadata |
| `aidb.conversation_log` | view | User prompts and answers for conversations the current session user started. Tool calls are not here: read `tool_call` / `tool_response` rows from `aidb_internal.action_log` (see troubleshooting.md) |
| `aidb.purpose_registry` | view | Purpose to role governance mappings; written only through `aidb.create_purpose`, `aidb.update_purpose`, `aidb.delete_purpose` by `aidb_governance` members |

Plugins are managed with `aidb.create_agent_plugin`, `aidb.update_agent_plugin` and `aidb.delete_agent_plugin` (signatures in function-reference.md).

## Not Yet Wired (scaffolding only, do not rely on)

- `aidb_internal.action_queue`: exists but nothing writes to it (future async phase)
- `agent_task_queue.agent_schedule_id`: no FK, nothing references it
- `action_*.decision_id` and `decision`: action gating, later phase
- `aidb.agents.preset`: stored but never resolved
- Conversation forking (`parent_conversation_id`, `forked_from_message_id`): always NULL
- `agent_task_queue.blocking`: always true; only synchronous phase exists
