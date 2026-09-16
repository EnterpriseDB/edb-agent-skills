# AIDB Agent Hub & Tools Hub Reference

## Overview

The Agent Hub and Tools Hub are two cooperating systems within aidb:

- **Tools Hub** — registers and dispatches named tools (SQL, native, MCP) that can be invoked by agents or directly via SQL
- **Agent Hub** — runs a model in a reasoning loop, calling tools on the model's behalf, with full audit trail, budgets, memory, and delegation

---

## Agent Hub SQL Surface

### Creating an Agent

```sql
SELECT error FROM aidb.create_agent(
    name         => 'my_agent',
    instructions => 'You are a helpful database assistant. Answer concisely.',
    model        => 'my_llm_model'
    -- Optional: role, tools (TEXT[]), delegates (TEXT[]), budget config, output_type
);
```

### Conversing with an Agent

```sql
-- Basic conversation
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'What tables exist in the public schema?');

-- Continue an existing conversation
SELECT message, conversation_id, error
FROM aidb.agent_converse(
    'my_agent',
    'Now show me the columns in the orders table.',
    conversation_id => '<uuid-from-previous-turn>'
);

-- Read-only mode (safe for replicas, cannot write)
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'Summarize recent orders.', read_only => TRUE);

-- Debug mode (verbose reasoning trace in PostgreSQL logs)
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'Hello?', debug => TRUE);
```

**CRITICAL:** `agent_converse` NEVER raises a SQL exception. Always check `error IS NULL`.

### Session Management

```sql
-- Start a session explicitly (returns conversation_id for use in subsequent turns)
SELECT conversation_id FROM aidb.start_agent_session('my_agent');

-- View conversation history (RLS-filtered to current user)
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- Get full conversation detail
SELECT * FROM aidb.get_conversation('<uuid>');
```

### Managing Agents

```sql
-- List all agents
SELECT name, model FROM aidb.agents;

-- Update an agent
SELECT aidb.update_agent('my_agent', instructions => 'Updated instructions.');

-- Delete an agent (force => true also removes in-progress tasks)
SELECT aidb.delete_agent('my_agent', force => TRUE);
```

---

## Tools Hub SQL Surface

### Registering a SQL Tool

```sql
SELECT aidb.create_sql_tool(
    name            => 'list_recent_orders',
    description     => 'Returns orders placed within the last N days',
    sql_statement   => 'SELECT id, customer_id, total FROM orders WHERE created_at > NOW() - ($days || '' days'')::INTERVAL',
    params          => aidb.params(
                           aidb.param('days', 'integer', 'Number of days to look back')
                       ),
    read_only       => TRUE,
    return_type_hint => NULL
);
```

### Running a Tool Directly

```sql
-- Invoke any tool by name
SELECT aidb.run_tool('list_recent_orders', '{"days": 7}'::jsonb);

-- Invoke a native tool
SELECT aidb.run_tool('memory_search', '{"query": "upgrade plan"}'::jsonb);
```

### MCP Tool Integration

```sql
-- Import tools from an MCP server
SELECT aidb.import_mcp_tools(
    name      => 'my_mcp_server',
    url       => 'http://mcp-host:8080',
    transport => 'streamable_http',
    headers   => '{"Authorization": "Bearer token"}'::jsonb,
    tool_filter => ARRAY['tool_a', 'tool_b'],  -- NULL = import all
    headers_env => NULL
);

-- Refresh the tool cache (expires every 60 minutes automatically)
SELECT aidb.refresh_mcp_tools('my_mcp_server');

-- Delete a tool
SELECT aidb.delete_tool('list_recent_orders');
```

### Viewing Available Tools

```sql
-- All tools (native + SQL + MCP)
SELECT name, description, read_only FROM aidb.tools;

-- Native tools only
SELECT * FROM aidb.list_native_tools();

-- Cached MCP tools
SELECT * FROM aidb.list_cached_mcp_tools();
```

---

## Catalog Discovery Tools (Native)

These native tools are available to agents for schema introspection. Results are capped at `LISTING_ROW_CAP` rows (model context window safe).

```sql
SELECT aidb.run_tool('catalog_list_schemas');
SELECT aidb.run_tool('catalog_list_objects', '{"schema_name": "public"}'::jsonb);
SELECT aidb.run_tool('catalog_get_object_details', '{"schema_name":"public","object_name":"orders"}'::jsonb);
SELECT aidb.run_tool('catalog_list_relations', '{"schema_name": "public"}'::jsonb);
SELECT aidb.run_tool('catalog_list_extensions');
```

---

## Agent Memory

Agent memory persists conversation state across sessions. Controlled by two GUCs:

```sql
-- Use memory tier for history reads (default: action_log)
SET aidb.agent_session_source = 'memory';

-- Set which memory namespace to use (default: pg_agents)
SET aidb.agent_memory_namespace = 'my_namespace';
```

### Memory Namespace Provisioning

```sql
-- Initialize a namespace with a compaction model
SELECT aidb_memory.init(
    'my_namespace',
    'mock',
    '{"model_spec": {"llm": "my_summarizer_model"}}'::jsonb
);
```

**Compaction model resolution order:**
1. Namespace binding's `model_spec.llm` (set at `aidb_memory.init` time)
2. Agent's own model (fallback)
3. No compaction (truncation only, reported as `compaction_model_unavailable`)

### Memory Search (Native Tool)

```sql
-- Available to agents as a native tool
SELECT aidb.run_tool('memory_search', '{"query": "upgrade plan", "max_results": 10}'::jsonb);
```

- Namespace and actor come from GUC and `session_user` — agents cannot widen their own scope
- Capped at `MAX_MEMORY_SEARCH_RESULTS` (50) results

---

## Budget Strategies

| Strategy | Behavior on budget exceeded |
|---|---|
| `ignore` | Log warning, keep iterating |
| `error` | Halt, turn ends as error |
| `summarize` | Request closing summary from model, then finish |
| `attempt_complete` | Grant 3 grace iterations with "finalize now" instruction |

Default: `attempt_complete` with synthesized `max_iterations` of `MAX_REASONING_ITERATIONS - 3 - 1 = 21`.

---

## Safety Model

### Role Isolation
- `aidb.agents.role` sets the SQL execution identity for tool calls
- Requires `pg_has_role(current_user, role, 'MEMBER')` to assign
- Delegation CANNOT escalate privileges (inherits parent's role and read_only flag)
- All role switching goes through `execute_sql_as_role` — bare `SET ROLE` is never used

### Read-Only Enforcement (3 Layers)
1. **Static classification**: `READ_ONLY_COMMAND_TAGS` = {SELECT, EXPLAIN, COPY TO, PREPARE}
2. **Tool allow-list**: MCP tools always blocked in read-only mode (unclassified)
3. **PostgreSQL enforcement**: `SET LOCAL transaction_read_only = on` inside `with_discarded_subtransaction`

### Audit Trail
- `aidb_internal.action_log` is INSERT + SELECT only — agents cannot UPDATE or DELETE
- Every action recorded with `initiated_by = session_user` for RLS isolation
- `aidb.conversation_log` view applies same RLS predicate

---

## Reasoning Loop Limits

| Constant | Value | Protects Against |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Infinite loops |
| `MAX_UNKNOWN_ERROR_FAILURES` | 3 | Unclassifiable model errors |
| `MAX_RATE_LIMIT_RETRIES` | 5 | Retrying rate-limited services forever |
| `MAX_REPEATED_FAILED_TOOL_CALL_OCCURRENCES` | 3 | Same tool call failing identically |
| `MAX_REPEATED_SUCCESSFUL_TOOL_CALL_OCCURRENCES` | 2 | Same successful call repeating → compaction triggered |
| `MAX_DELEGATION_DEPTH_LIMIT_OCCURRENCES` | 1 | Exponential cost from deep delegate chains |
| `MAX_DELEGATION_DEPTH` | 11 | Total nesting depth |

---

## Not Yet Implemented (Scaffolding Only)

The following exist in schema but are NOT wired up — do not build on them:
- `aidb_internal.action_queue` (created, nothing writes to it)
- `agent_task_queue.agent_schedule_id` (no foreign key)
- `aidb.agents.preset` (stored, never resolved)
- Conversation forking (`parent_conversation_id`, `forked_from_message_id` always NULL)
- MCP `sse` transport (validated/stored, handled same as `streamable_http`)
- Async agent execution (`blocking` always true)
