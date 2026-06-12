# Agent Core Workspace Context

You are working on the LifeQuery workspace.

Use Agent Core for durable workspace memory, activity tracking, handoffs, and credential references. If this is a handoff, resume, or review of prior work, also inspect the recent activity trail and any generated briefing before making changes. Use `activity_list` and `briefing_list` when you need that trail from MCP.
Core MCP tools include `memory_search`, `memory_get`, `memory_write`, `memory_retract`, `credential_get`, `credential_list`, `activity_update`, `activity_list`, `get_briefing`, `briefing_list`, `connectors_list`, `connectors_summary`, `connectors_bindings_list`, `connectors_bindings_test`, `connectors_actions_list`, and `connectors_run`.

## Connection

- **Agent Core URL:** http://core.veditz.com
- **Workspace scope:** workspace:lifequery

The active Agent Core user and agent identities are determined by the MCP/API key configured in your tool, not by this file. Do not add API keys to this file.

## Memory Scopes

Use `workspace:lifequery` for default memory in this setup.
Read the authenticated/default user scope from your Agent Core connection for stable personal preferences and owner-context details when you have user-scope read access.
Use your authenticated Agent Core private scope, usually `agent:<your-agent-id>`, only for tool-specific scratch context.
Use full prefixed scope names exactly as shown. Do not use plain workspace IDs like `LifeQuery` or agent IDs like `Claude Code` as memory scopes.

## Memory Workflow

At the start of a meaningful task:

1. Start or refresh the activity record using the Activity Tracking workflow below.
2. Search `workspace:lifequery` with 2-3 focused queries for relevant architecture, decisions, prior bugs, and current project state. If the search returns little or nothing, retry with exact topic values, exact keywords from prior records, or a known record id. When embeddings are unavailable, broad conceptual queries can miss; exact tokens and known ids are more reliable.
3. Search the authenticated/default user scope only when you have user-scope read access and user preferences or personal workflow may matter.
4. Use `memory_get` with a scope to list or read records; use `memory_search` to find records by query, topic, or class (there is no fetch-by-id).

Write memory only when it will help a future session:

- `decision` in `workspace:lifequery` for durable choices, tradeoffs, rejected options, and why they were chosen.
- `fact` in `workspace:lifequery` for stable implementation facts, integration details, constraints, and verified behavior.
- `preference` in the authenticated/default user scope only if your key has user-scope write; otherwise treat the user scope as read-only owner context and write the preference to `workspace:lifequery` instead.
- `scratchpad` in the authenticated private agent scope for temporary private notes, or in `workspace:lifequery` only for short-lived workspace handoff notes.

Do not write memory for routine progress, command output, facts already obvious from files, secrets, raw credentials, or noisy transient debugging notes.

Keep memory content concise. Add domain/topic when useful for exact filtering. Set confidence to match certainty. Set importance higher only for information likely to matter later.

## Credentials

Use `credential_get` to retrieve `AC_SECRET_*` references. The Credential Broker resolves them at execution time.
Never ask users for raw credential values.

## Connectors

When a task may require an external service, credential, API token, repository host, chat service, browser service, or Composio-style connector, check Agent Core before asking the user for setup details.

1. Use `credential_list` to discover available credential references in authorized scopes.
2. Use `credential_get` only when you need a specific `AC_SECRET_*` reference for a local tool or command. Never ask the user for raw secrets and never print raw secrets.
3. Use `connectors_summary` for a compact capability overview, or `connectors_list` and `connectors_bindings_list` when you need raw connector and binding lists.
4. Use `connectors_actions_list` before running an unfamiliar connector action.
5. Use `connectors_bindings_test` when a binding may be stale or unverified.
6. Use `connectors_run` when Agent Core should perform the external action server-side.

Prefer connector bindings over local secret handling when both are available, because the raw credential stays inside Agent Core.

## Activity Tracking

Activity records are operational task tracking, not durable memory.

At the start of every non-trivial user task, call `activity_update` immediately with:

- `task_description`: a concise description of the current task
- `memory_scope`: `workspace:lifequery`
- `status`: `active`

When the task is a handoff, resume, or review of prior work, inspect the recent activity trail and any generated briefing before making changes. Use workspace memory, not agent-private scratch notes, as the durable source of truth for prior work.
Use `activity_list` and `briefing_list` when you need to inspect that trail from MCP instead of the dashboard.

While actively working, call `activity_update` again every 1-2 minutes as a heartbeat. Use `task_note` for interim progress updates and update `task_description` if the task changes materially.

If the session reloads, a handoff begins, or no active activity exists yet, open a fresh activity first with `status: active` before attempting to close it. Before your final response, call `activity_update` with `status: completed` and a short `task_result` summary when the task is complete. Use `task_note` for in-flight updates. Use `status: blocked` if you cannot proceed and need user input. Do not create activity records for trivial one-shot answers that do not inspect or modify project state.
If the session has to stop early or hits a token limit, leave the activity current and write durable decisions or handoff notes to memory so another agent can continue from the saved state.
If work needs to move across users or workspaces, make that explicit in the activity scope and handoff notes rather than assuming a hidden policy layer.
If the client supports hooks or plugins, use them to automate these calls. If it does not, keep using this file as the manual operating contract.


## Claude Code Notes

- Claude Code automatically reads this `CLAUDE.md` file when present in the workspace root.
- Claude Code uses the configured MCP connection or your shell environment's `AGENT_CORE_API_KEY`. That key determines which Agent Core user and agent are active.
- Do not add your API key to this file.
- **Tool availability:** Claude Code defers MCP tool schemas at startup to save context. Before calling any Agent Core tool in a new session, load the schemas with `ToolSearch("select:mcp__agent-core__memory_search,mcp__agent-core__activity_update,mcp__agent-core__memory_write")`. Skipping this causes `InputValidationError`. Add other tool names to the select list as needed.
- If Claude Code can't reach Agent Core, run the Verification Prompt output to verify the full end-to-end setup.
