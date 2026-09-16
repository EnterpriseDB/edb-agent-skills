# AIDB Agent Hub & Tools Hub — Operational Guide

Source: `agent_docs/agent_hub.md`, `static-sql/agent-hub.sql`, `src/api/agent_hub.rs`

---

## Agent Execution Flow

```
aidb.agent_converse(agent, prompt)
        ↓
load agent config, tools, delegates
        ↓
reasoning loop (max 25 iterations, max delegation depth 11)
        ↓
model asks for tool calls  →  aidb.run_tool dispatch  →  native / sql / mcp
        ↓
model returns an answer, coerced to declared output type
```

## Key Safety Limits (Confirmed from Source)

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REASONING_ITERATIONS` | 25 | Prevents infinite loops |
| `MAX_DELEGATION_DEPTH` | 11 | Caps recursive delegate chains |
| `MAX_UNKNOWN_ERROR_FAILURES` | 3 | Stops on repeated unclassifiable errors |
| `MAX_RATE_LIMIT_RETRIES` | 5 | Stops retrying rate-limited services |
| `MAX_REPEATED_FAILED_TOOL_CALL_OCCURRENCES` | 3 | Stops identical failing tool calls |
| `MAX_REPEATED_SUCCESSFUL_TOOL_CALL_OCCURRENCES` | 2 | Triggers summarize on repeated success |

## Error Convention

`aidb.agent_converse` **never raises on a failed turn**. It returns an `error` column. A bad turn does NOT abort the caller's transaction. This is intentionally different from Tools Hub functions (`aidb.run_tool`, `aidb.create_sql_tool`, etc.) which raise normally on error.

## Creating an Agent

```sql
SELECT error FROM aidb.create_agent(
    'my_agent',
    'You are a helpful database assistant.',  -- instructions
    'my_llm_model',                           -- model name
    tools => ARRAY['list_tables', 'my_sql_tool'],
    delegates => NULL,
    output_type => NULL,
    max_iterations => 10,
    max_input_tokens => 100000,
    max_output_tokens => 4096,
    budget_strategy => 'attempt_complete',    -- 'ignore' | 'error' | 'summarize' | 'attempt_complete'
    purpose => NULL
);
```

**Budget strategies:**
- `ignore` — keep going, push a warning
- `error` — halt with error
- `summarize` — ask model for closing summary, finish
- `attempt_complete` — give 3 grace iterations with "finalize now" instruction, then halt

## Conversing with an Agent

```sql
-- Simple call
SELECT message, conversation_id, error
FROM aidb.agent_converse('my_agent', 'What tables are in the public schema?');

-- Continue a conversation
SELECT message, conversation_id, error
FROM aidb.agent_converse(
    'my_agent',
    'Now list the columns in the orders table.',
    conversation_id => '<uuid from previous call>'
);

-- Read-only mode (also auto-enabled on read replicas)
SELECT message, error
FROM aidb.agent_converse('my_agent', 'Describe schema', read_only => true);

-- Structured output
SELECT message, error
FROM aidb.agent_converse(
    'my_agent',
    'Get the top 3 customers',
    output_type => aidb.output_type(
        aidb.output_field('customer_id', 'integer'),
        aidb.output_field('name', 'text'),
        aidb.output_field('revenue', 'numeric')
    )
);
```

## Creating SQL Tools

```sql
SELECT aidb.create_sql_tool(
    'list_user_orders',
    'List orders for a specific customer by email',
    'SELECT id, total, created_at FROM orders WHERE customer_email = ${email}',
    aidb.tool_params(
        aidb.tool_param('email', 'text', 'Customer email address')
    ),
    read_only => true
);
```

## Tool Dispatch

```sql
-- All tool calls go through a single entry point
SELECT aidb.run_tool('list_user_orders', '{"email": "user@example.com"}'::jsonb);

-- Catalog discovery tools (row-capped for model context)
SELECT aidb.catalog_list_schemas();
SELECT aidb.catalog_list_objects('public');
SELECT aidb.catalog_get_object_details('public', 'orders');
SELECT aidb.catalog_list_relations('public');
SELECT aidb.analyze_db_health();
```

## Importing MCP Tools

```sql
SELECT aidb.import_mcp_tools(
    'my_mcp_server',
    'http://mcp-server:8080/mcp',
    transport => 'streamable_http',
    tool_filter => ARRAY['search', 'fetch']   -- optional filter
);

-- Refresh tool cache (cache expires after 60 minutes)
SELECT aidb.refresh_mcp_tools('my_mcp_server');
```

## Querying Agent State

```sql
-- View all registered agents
SELECT * FROM aidb.agents;

-- View active tools (union of native, SQL, and MCP tools)
SELECT * FROM aidb.tools;

-- View conversation history
SELECT * FROM aidb.conversation_log WHERE conversation_id = '<uuid>';

-- Get a specific conversation
SELECT * FROM aidb.get_conversation('<uuid>');
```

## Read-Only Enforcement (Three Layers)

1. **Static classification** — SQL command tags are classified; unknowns treated as writes (fail-closed)
2. **Tool allow-list** — `is_tool_allowed_in_read_only_mode` blocks all MCP tools outright
3. **PostgreSQL level** — `SET LOCAL transaction_read_only = on` inside a savepoint

## Governance: Purpose Registry

```sql
-- Create a purpose (maps a name to a PostgreSQL role)
SELECT aidb.create_purpose('finance_agent', 'finance_readonly_role', 'Finance data access');

-- Assign to an agent
SELECT error FROM aidb.create_agent(
    'finance_assistant',
    '...',
    'my_model',
    purpose => 'finance_agent'
);

-- View audit records
SELECT * FROM aidb.purpose_registry;
```

Purposes are soft-deleted only — `delete_purpose` sets `deleted_at` but never physically removes the row.

## Native Tools (Always Available)

The `sleep` tool is always registered. Catalog discovery tools (`catalog_list_schemas`, `catalog_list_objects`, `catalog_get_object_details`, `catalog_list_relations`, `catalog_list_sequences`, `catalog_list_extensions`) and `analyze_db_health` are available and each capped in row count since consumers are model context windows. `memory_search` is available when Agent Memory is configured.
