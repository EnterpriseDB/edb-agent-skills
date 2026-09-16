-- quickstart_agent.sql
-- Example: create a tool, register an agent, and run a conversation.
-- Uses the 'dummy' provider so no external model credentials are required.
--
-- Usage:
--   psql -d <your_database> -f quickstart_agent.sql

\echo '=== Quickstart: Agent Hub ==='

-- Step 1: Create a model for the agent (dummy = no external service needed)
SELECT aidb.create_model(
    'my_agent_llm',
    'dummy',
    '{"responses": [{"response": "The database has 5 tables in the public schema."}]}'::jsonb,
    validate => false
) AS agent_model;

-- Step 2: Register a SQL tool the agent can invoke
SELECT aidb.create_sql_tool(
    'count_tables',
    'Count the number of tables in a given schema',
    'SELECT count(*) AS table_count FROM information_schema.tables WHERE table_schema = ${schema_name}',
    aidb.tool_params(
        aidb.tool_param('schema_name', 'text', 'The schema to count tables in')
    ),
    read_only => true
);

-- Step 3: Create the agent
SELECT error FROM aidb.create_agent(
    'my_demo_agent',
    'You are a database analyst. Use the count_tables tool when asked about table counts.',
    'my_agent_llm',
    tools => ARRAY['count_tables']
);

-- Step 4: Have a conversation
\echo ''
\echo 'Conversing with the agent...'
SELECT message, error
FROM aidb.agent_converse(
    'my_demo_agent',
    'How many tables are in the public schema?'
);

-- Step 5: Inspect available tools
\echo ''
\echo 'Available tools:'
SELECT name, description, tool_type FROM aidb.tools ORDER BY name;

-- Cleanup
-- SELECT aidb.delete_agent('my_demo_agent');
-- SELECT aidb.delete_tool('count_tables');
-- SELECT aidb.delete_model('my_agent_llm');

\echo '=== Agent Quickstart Complete ==='
