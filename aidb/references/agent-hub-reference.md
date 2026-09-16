# AIDB Agent Hub & Tools Hub — Operational Reference

> Read this document when managing agents, tools, or multi-turn conversations in AIDB.

---

## Agent Lifecycle

### Create an Agent

```sql
SELECT aidb.create_agent(
    'my_agent',                          -- unique name
    'You are a helpful DB assistant.',   -- instructions / system prompt
    'my_llm_model',                      -- registered model name (must support chat)
    role => NULL,                        -- optional: PostgreSQL role for tool execution
    tools => NULL,                       -- NULL = all tools; ARRAY['tool1','tool2'] to restrict
    delegates => NULL,                   -- ARRAY of sub-agent names
    max_iterations => 15,                -- default: 25 max
    budget_strategy => 'attempt_complete', -- ignore | error | summarize | attempt_complete
    read_only => false
);
```

### Converse (single-turn)

```sql
SELECT message, error, conversation_id
FROM aidb.agent_converse('my_agent', 'What tables are in the public schema?');
```

`agent_converse` **never raises**. Always check the `error` column. A non-NULL error means the turn failed.

### Multi-Turn Conversation

```sql
-- Start a session and capture the conversation_id
SELECT conversation_id FROM aidb.start_agent_session('my_agent') \gset

-- Turn 1
SELECT message, error
FROM aidb.agent_converse('my_agent', 'List all tables', conversation_id => :'conversation_id');

-- Turn 2
SELECT message, error
FROM aidb.agent_converse('my_agent', 'Show me the row count for each', conversation_id => :'conversation_id');
```

### View Conversation History

```sql
-- Full log for a conversation
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- Running tasks
SELECT * FROM aidb.agent_tasks;

-- Get a specific conversation (structured)
SELECT * FROM aidb.get_conversation('<uuid>');
```

### Delete an Agent

```sql
SELECT aidb.delete_agent('my_agent', force => FALSE);
-- force => TRUE terminates running tasks first
```

---

## Safety Limits (Hard-Coded)

| Limit | Value | Effect when exceeded |
|---|---|---|
| Max reasoning iterations | 25 | Turn ends; budget strategy applies |
| Max unknown error failures | 3 | Turn halts with error |
| Max rate limit retries | 5 | Turn halts with error |
| Max repeated failed tool calls | 3 | Turn halts with error |
| Max repeated successful tool calls | 2 | Triggers proactive summarize |
| Max delegation depth | 11 | Hard halt; delegate call returns error |

---

## Budget Strategies

| Strategy | Behavior when budget exceeded |
|---|---|
| `ignore` | Log warning, keep iterating |
| `error` | Halt immediately, return error |
| `summarize` | Ask model for closing summary, then finish |
| `attempt_complete` | Grant 3 extra "finalize now" iterations, then halt |

**Note:** When `max_iterations` is unset and strategy is `attempt_complete`, the system synthesizes a limit of `MAX_REASONING_ITERATIONS - ATTEMPT_COMPLETE_GRACE_ITERATIONS - 1` (= 21) automatically.

---

## Tools Hub

### Tool Resolution Priority
1. **Native tools** — built-in Rust functions (catalog discovery, memory search, sleep)
2. **SQL tools** — user-defined via `create_sql_tool()`
3. **MCP tools** — imported from external MCP servers

### Create a SQL Tool

```sql
SELECT aidb.create_sql_tool(
    'get_recent_orders',
    'List recent orders for a customer by ID',
    'SELECT id, total, status FROM orders WHERE customer_id = ${customer_id} LIMIT ${n}',
    aidb.params(
        aidb.param('customer_id', 'integer', 'Customer identifier'),
        aidb.param('n', 'integer', 'Max number of rows to return')
    ),
    read_only => true,           -- enforced via SET LOCAL transaction_read_only
    return_type_hint => 'table'  -- hints to the model how to format result
);
```

### Run a Tool Directly

```sql
SELECT aidb.run_tool('get_recent_orders', '{"customer_id": 42, "n": 5}'::JSONB);
```

`run_tool()` raises on failure (unlike `agent_converse`).

### Import MCP Server Tools

```sql
SELECT aidb.import_mcp_tools(
    'my_mcp_server',                  -- name
    'http://mcp-host:8080',           -- URL
    'streamable_http'                 -- transport
);
```

MCP tool cache expires after **60 minutes**. Refresh manually:
```sql
SELECT aidb.refresh_mcp_tools('my_mcp_server');
```

### List All Available Tools

```sql
SELECT * FROM aidb.tools;             -- union of native, SQL, and MCP tools
SELECT * FROM aidb.list_native_tools();
```

### Key Native Tools

| Tool Name | Description |
|---|---|
| `aidb.catalog_list_schemas` | List all schemas in the database |
| `aidb.catalog_list_objects` | List tables/views/sequences in a schema |
| `aidb.catalog_get_object_details` | Get DDL and comments for a specific object |
| `aidb.catalog_list_relations` | List foreign key relationships |
| `aidb.catalog_list_extensions` | List installed PostgreSQL extensions |
| `aidb.memory_search` | Semantic search over agent memory |
| `aidb.sleep` | Pause agent execution (seconds) |

---

## Read-Only Agents

- Pass `read_only => true` to `create_agent()` or `agent_converse()`
- Agents on PostgreSQL replicas automatically run read-only
- MCP tools are always blocked in read-only mode
- Read-only tool calls use `SET LOCAL transaction_read_only = on` inside a discarded subtransaction
- Read-only agent turns do not create a task row and return `NULL` for `conversation_id`

---

## Role & Security Model

- `aidb.agents.role` sets the PostgreSQL role used for tool execution
- Credential for the role must satisfy `pg_has_role(current_user, role, 'MEMBER')`
- A delegate agent inherits the parent's role and `read_only` flag — it cannot escalate privileges
- `initiated_by` (= `session_user`) is the RLS key — agents only see their own conversation history
- The `action_log` table is INSERT+SELECT only; agents cannot rewrite their own audit trail

---

## GUC Parameters Relevant to Agents

```sql
-- Switch conversation history source (action_log = default, memory = vector-based)
SET aidb.agent_session_source = 'action_log';  -- 'action_log' | 'memory'

-- Set memory namespace for agent sessions
SET aidb.agent_memory_namespace = 'pg_agents';
```

---

## Troubleshooting Agent Issues

**Turn returns error column non-NULL:**
```sql
SELECT message, error FROM aidb.agent_converse('my_agent', 'test prompt');
-- If error contains "model" -> check the model is registered and reachable
-- If error contains "tool" -> inspect aidb.tools for the tool
-- If error contains "budget" -> lower max_iterations or change budget_strategy
```

**Agent not using expected tools:**
```sql
-- Check what tools are loaded for an agent
SELECT tools FROM aidb.agents WHERE name = 'my_agent';
-- Verify tool exists
SELECT name, description FROM aidb.tools WHERE name = 'my_tool';
```

**MCP tools not found:**
```sql
-- Refresh the cache
SELECT aidb.refresh_mcp_tools('my_mcp_server');
-- Verify tools cached
SELECT * FROM aidb.list_cached_mcp_tools();
```
