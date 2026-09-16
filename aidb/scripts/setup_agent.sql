-- setup_agent.sql
-- Template: Create an AIDB Agent with tools and run a conversation.
-- Replace placeholder values before running.

-- Step 1: Register a completions model for the agent
-- Providers: openai_completions, completions (generic OpenAI-compatible),
--            gemini, llama_instruct_local, smollm2_local, dummy (testing)
SELECT aidb.create_model(
    '<AGENT_MODEL_NAME>',      -- e.g. 'my_gpt4o'
    '<PROVIDER>',              -- e.g. 'openai_completions'
    config => aidb.completions_config(
        model   => '<MODEL_ID>',   -- e.g. 'gpt-4o'
        api_key => '<API_KEY>'
    )
);

-- Step 2 (optional): Create a SQL tool the agent can call
SELECT aidb.create_sql_tool(
    name             => 'list_recent_records',
    description      => 'Returns the most recent N records from a table',
    sql_statement    => 'SELECT * FROM <YOUR_TABLE> ORDER BY created_at DESC LIMIT $n',
    params           => aidb.params(
                            aidb.param('n', 'integer', 'Number of records to return')
                        ),
    read_only        => TRUE,
    return_type_hint => NULL
);

-- Step 3: Create the agent
-- tools: list of tool names the agent can use (NULL = use all)
-- role: PostgreSQL role to run tool SQL under (NULL = session user)
SELECT error FROM aidb.create_agent(
    name         => '<AGENT_NAME>',          -- e.g. 'data_assistant'
    instructions => 'You are a helpful database assistant. Answer concisely and accurately.',
    model        => '<AGENT_MODEL_NAME>'
    -- Optionally add: , tools => ARRAY['list_recent_records']
);

-- Step 4: Start a session and converse
SELECT conversation_id FROM aidb.start_agent_session('<AGENT_NAME>') \gset

-- First turn
SELECT message, conversation_id, error
FROM aidb.agent_converse(
    '<AGENT_NAME>',
    'What tables are in the public schema?',
    conversation_id => :'conversation_id'
);

-- Follow-up turn (continue conversation)
SELECT message, conversation_id, error
FROM aidb.agent_converse(
    '<AGENT_NAME>',
    'Show me the 5 most recent records.',
    conversation_id => :'conversation_id'
);

-- View conversation history
SELECT * FROM aidb.conversation_log
WHERE conversation_id = :'conversation_id'
ORDER BY created_at;

-- Cleanup:
-- SELECT aidb.delete_agent('<AGENT_NAME>', force => TRUE);
-- SELECT aidb.delete_tool('list_recent_records');
-- SELECT aidb.delete_model('<AGENT_MODEL_NAME>');
