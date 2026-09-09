# AIDB Agents Reference

Signature reference for in-database agents. Introduced in AIDB 7.6.0.

An agent is a named, reusable configuration — instructions, a model, and an allowed tool set — that AIDB runs through a ReAct-style loop: the model thinks, optionally calls tools, observes results, and repeats until it has an answer. Agents are ordinary Postgres objects driven entirely from SQL.

## Error convention

Most agent functions **never raise** for a logic failure. They return an `error` column instead, so the transaction (and anything already logged) still commits. The exceptions are `aidb.start_agent_session()` and `aidb.get_conversation()`, which do raise on an empty/unknown argument.

## Catalog views

### `aidb.agents`

`id` (uuid), `name` (text), `instructions` (text), `model` (text), `role` (text), `delegates` (text[]), `tools` (text[]), `output_type` (jsonb), `input_token_budget` (integer), `output_token_budget` (integer), `max_iterations` (integer), `timeout_seconds` (integer), `budget_strategy` (`aidb.budget_strategy`), `preset` (text), `created_at`, `updated_at` (timestamptz).

There is no "list agents" function — query this view.

### `aidb.agent_tasks`

One row per `agent_converse` call. `task_id` (uuid), `agent_id` (uuid), `conversation_id` (uuid), `request_message_id` (uuid), `response_message_id` (uuid), `caller_role` (text), `status` (`aidb.task_status`), `blocking` (boolean — always `true` today), `error` (text), `created_at`, `completed_at`.

### `aidb.conversation_log`

Every user prompt and agent answer across all conversations. `id` (uuid), `conversation_id` (uuid), `agent_id` (uuid), `task_id` (uuid), `action_type` (`aidb.action_type` — `user_prompt` or `answer`), `payload` (jsonb), `created_at`, `completed_at`.

Tool calls, model requests, and other internal activity are **not** here — query `aidb_internal.action_log` for the full activity history.

### `aidb.conversations`

Aggregated from `conversation_log`. `conversation_id` (uuid), `agent_id` (uuid), `parent_conversation_id` (uuid — always NULL, forking not implemented), `forked_from_message_id` (uuid — always NULL), `status` (`aidb.task_status` of the latest task), `message_count` (bigint), `last_message_id` (uuid), `created_at`, `updated_at`.

## Management functions

### `aidb.create_agent`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | TEXT | required | Unique agent name |
| `instructions` | TEXT | required | Behaviour definition. Also becomes the tool description when this agent is a delegate |
| `model` | TEXT | required | A registered text-generation model |
| `tools` | TEXT[] | NULL | Tool names from `aidb.tools` |
| `delegates` | TEXT[] | NULL | Names of agents this one may hand off to |
| `role` | TEXT | NULL | Postgres role tool calls execute as. Caller must be a member |
| `output_type` | JSONB | NULL | Structured output schema from `aidb.output_type()` |
| `input_token_budget` | INTEGER | NULL (no limit) | Input tokens per call |
| `output_token_budget` | INTEGER | NULL (no limit) | Output tokens per call |
| `max_iterations` | INTEGER | NULL | Reasoning rounds per call. **A hard ceiling of 25 always applies** |
| `timeout` | INTEGER | NULL (300 at runtime) | Wall-clock seconds per call |
| `budget_strategy` | TEXT | `'attempt_complete'` | `'ignore'`, `'error'`, `'summarize'`, `'attempt_complete'` |
| `preset` | TEXT | NULL | Stored but resolves to no behaviour today |

Returns `TABLE(error TEXT)` — no rows on success.

`tools` and `delegates` are validated against `aidb.tools` / `aidb.agents` at creation time. A delegate name must not collide with one of the agent's own tool names. The **model** need not exist yet at `create_agent` time — only `agent_converse` requires it to resolve.

### `aidb.update_agent`

Same parameters, all optional except `name`, all defaulting to `NULL` meaning "leave unchanged" — including `budget_strategy`, whose default here is `NULL`, **not** `'attempt_complete'`. Returns `TABLE(error TEXT)`.

### `aidb.delete_agent`

`(name TEXT, force BOOLEAN = false)` → `TABLE(error TEXT)`.

Without `force`, deletion fails if the agent has conversation history. `force => true` also deletes internal task/action-queue records. The transcript in `aidb.conversation_log` is preserved either way.

## Choosing a model

`model` must name a registered model capable of generating text and following instructions.

**Rejected outright:** embedding, reranking, OCR, and multimodal-embedding providers (`bert_local`, `openai_embeddings`, `nim_reranking`, `clip_local`, …), plus `t5_local` — it has no instruction-following or system-prompt support.

**Preferred** (real provider-native tool calls): `openai_responses` (+ `_azure`), `anthropic_messages` (+ `_azure`/`_bedrock`), `llamacpp_generate`.

**Supported** (AIDB simulates tool calling by describing tools in the prompt and parsing the reply): `openai_completions`, `completions`, `nim_completions`, `openrouter_chat`, `gemini`, `llama_instruct_local`.

## Conversation functions

### `aidb.agent_converse`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_name` | TEXT | required | Agent to converse with |
| `prompt` | TEXT | required | The prompt |
| `conversation_id` | TEXT | NULL | Continue an existing conversation; omit to start a new one |
| `read_only` | BOOLEAN | NULL | Force read-only on/off. Auto-enabled on a read replica when omitted |
| `output_type` | JSONB | NULL | Override the agent's structured output schema for this call |
| `debug` | BOOLEAN | NULL | Emit every action as a `NOTICE` |

Returns `TABLE(message TEXT, conversation_id TEXT, error TEXT)` — one row. On failure `error` is set and the others are NULL. `conversation_id` is also NULL for any read-only run, since nothing is persisted.

### Other conversation functions

| Function | Signature | Notes |
|---|---|---|
| `aidb.start_agent_session` | `(agent_name TEXT)` → `TABLE(conversation_id TEXT)` | Mints an id ahead of the first call. **Raises** on empty/unknown agent. The conversation doesn't exist until the first `agent_converse` uses the id |
| `aidb.get_conversation` | `(conversation_id TEXT)` → `TABLE(message_id TEXT, task_id TEXT, action_type TEXT, role TEXT, sender_id TEXT, contents TEXT)` | Prompts and answers only, chronological. `role` is `user` or `agent`. **Raises** if `conversation_id` is empty |
| `aidb.get_message` | `(message_id TEXT)` → `TABLE(message TEXT, error TEXT)` | Does not raise; `error` set when not found |

## Read-only mode

`read_only => true` persists nothing — no history is written and `conversation_id` returns NULL, so the run cannot be resumed. Automatic on a read replica.

Two enforcement layers at once: every tool call runs under `SET LOCAL transaction_read_only = on`, **and** any tool not provably read-only is excluded from what the agent can see or call. **MCP tools are always excluded in read-only mode** regardless of classification — AIDB can't verify an external server's behaviour.

## Debug mode

`debug => true` emits every action — model requests/responses, tool calls and results, reasoning steps — as a Postgres `NOTICE`, in addition to the normal `aidb_internal.action_log` history.

Payloads are truncated but **not redacted**, so avoid it where prompts, tool arguments, or returned data shouldn't be visible in client output or the server log.

## Budgets and limits

Four optional caps per `agent_converse` call:

| Parameter | Caps |
|---|---|
| `input_token_budget` | Input tokens |
| `output_token_budget` | Output tokens |
| `max_iterations` | Reasoning rounds — one model call plus the tools it calls in that round, not individual tool calls. Hard ceiling of 25 regardless of configuration |
| `timeout` | Wall-clock seconds; 300 if unset |

`budget_strategy` decides what happens on exceeding one:

| Strategy | Behaviour |
|---|---|
| `attempt_complete` (default) | Grants 3 extra reasoning rounds, telling the model to wrap up; errors if still unfinished |
| `error` | Halts immediately with an error |
| `summarize` | Stops and asks the model for a brief status report, returned as the answer instead of an error |
| `ignore` | Logs a one-time Postgres `WARNING` and continues with no limit |

## Context length

Bounded automatically. Once a task's assembled prompt would exceed an internally tracked size estimate, AIDB has the model summarize the older history and continues with that summary. Nothing to configure; a `compaction` entry appears in the activity log.

## Roles

By default tool calls run as whichever role called `agent_converse`. Pass `role` to pin the agent to a specific role — e.g. to give it narrower table permissions than the application user. The caller must already be a member of that role (`pg_has_role(..., 'MEMBER')`); otherwise `create_agent`/`update_agent` reject it.

## Structured output

| Function | Signature |
|---|---|
| `aidb.output_field` | `(name TEXT, field_type TEXT, description TEXT = NULL)` → JSONB. `field_type` e.g. `'TEXT'`, `'FLOAT'`, `'BOOLEAN'` |
| `aidb.output_type` | `(VARIADIC fields JSONB[])` → JSONB |

Pass the result to `create_agent`/`update_agent`'s `output_type`, or to `agent_converse`'s for a single call.

## Delegation

List another agent in `delegates`. Each delegate becomes an ordinary function-call tool named after the delegate agent, taking `prompt` (required) and `conversation_id` (optional). **The delegate's `instructions` become that tool's description**, so write instructions with that dual purpose in mind.

A delegate call returns an object with `answer` and `conversation_id`; the model may pass that id back later to continue that specific delegate conversation. Omitting it starts fresh each time.

What crosses a handoff:

| | Propagates? |
|---|---|
| Read-only mode | **Yes** — a read-only agent can't reach a write through a delegate. A read-only delegate call always returns `conversation_id: null` |
| Debug mode | **Yes** |
| Role | **No** — a delegate runs under its own configured `role`, or the current user |
| Budgets | **No** — each delegate call gets fresh budgets from its own configuration |

Delegation chains are capped at **10 levels**. This exists mainly to fail fast on a cycle. Hitting the limit stops the task immediately with an error and is not retried.

## Types

### `aidb.budget_strategy`

`'ignore'` | `'error'` | `'summarize'` | `'attempt_complete'`.

### `aidb.task_status`

`PENDING`, `IN_PROGRESS`, `RECOVERY`, `AWAIT_APPROVAL`, `DENIED`, `ERROR`, `TIMEOUT`, `CANCELED`, `SUCCESS`, `EVALUATING`, `COMPLETE`.

Every task runs synchronously to completion within one `agent_converse` call, so a terminal status is normally `SUCCESS` or `ERROR`. The rest are reserved for asynchronous/approval workflows not yet implemented.

### `aidb.action_type`

`user_prompt` | `answer` (as surfaced in `aidb.conversation_log`).
