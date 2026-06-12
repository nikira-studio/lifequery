# Agent Core Workspace Context

You are working on the LifeQuery workspace.

## Agent Core

Use Agent Core MCP for memory, credential references, and activity tracking. If this is a handoff, resume, or review of prior work, also inspect the recent activity trail and any generated briefing before making changes. Use `activity_list` and `briefing_list` when you need that trail from MCP.

- **Base URL:** http://core.veditz.com
- **Workspace scope:** workspace:lifequery

The active Agent Core user and agent identities are determined by the MCP/API key configured in your tool, not by this file. Do not commit credentials to this file.
If your host defers tool availability, run its tool discovery/load step first so the Agent Core MCP tools are available before you try to call them.

## Memory Scope Guidance

Default memory scope for this setup is `workspace:lifequery`.
Read the authenticated/default user scope from your Agent Core connection for stable personal preferences and owner-context details when you have user-scope read access.
Use your authenticated Agent Core private scope, usually `agent:<your-agent-id>`, for private scratch notes only.
Use full prefixed scope names exactly as shown. Do not use plain workspace IDs or agent IDs as memory scopes.

## Activity Workflow

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

## Memory Workflow

At the start of a meaningful task:

1. Confirm Agent Core is reachable at http://core.veditz.com/mcp if this is a new setup or connectivity is uncertain.
2. Start or refresh the activity record using the Activity Workflow above.
3. Search `workspace:lifequery` with 2-3 focused queries for relevant architecture, decisions, prior bugs, and current project state. If the search returns little or nothing, retry with exact topic values, exact keywords from prior records, or a known record id. When embeddings are unavailable, broad conceptual queries can miss; exact tokens and known ids are more reliable.
4. If this is a handoff, resume, or review of prior work, inspect the recent activity trail and any generated briefing before making changes.
5. Search the authenticated/default user scope only when you have user-scope read access and user preferences or personal workflow may matter.
6. Use `memory_get` with a scope to list or read records; use `memory_search` to find records by query, topic, or class (there is no fetch-by-id).

Write memory only when it will help a future session:

- `decision` in `workspace:lifequery` for durable choices, tradeoffs, rejected options, and why they were chosen.
- `fact` in `workspace:lifequery` for stable implementation facts, integration details, constraints, and verified behavior.
- `preference` in the authenticated/default user scope only if your key has user-scope write; otherwise treat the user scope as read-only owner context and write the preference to `workspace:lifequery` instead.
- `scratchpad` in the authenticated private agent scope for temporary private notes, or in `workspace:lifequery` only for short-lived workspace handoff notes.

Do not write memory for routine progress, command output, facts already obvious from files, secrets, raw credentials, or noisy transient debugging notes.

Keep memory content concise. Add domain/topic when useful for exact filtering. Set confidence to match certainty. Set importance higher only for information likely to matter later.

## Credentials And Connectors

When a task may require an external service, credential, API token, repository host, chat service, browser service, or Composio-style connector, check Agent Core before asking the user for setup details.

1. Use `credential_list` to discover available credential references in authorized scopes.
2. Use `credential_get` only when you need a specific `AC_SECRET_*` reference for a local tool or command. Never ask the user for raw secrets and never print raw secrets.
3. Use `connectors_summary` for a compact capability overview, or `connectors_list` and `connectors_bindings_list` when you need raw connector and binding lists.
4. Use `connectors_actions_list` before running an unfamiliar connector action.
5. Use `connectors_bindings_test` when a binding may be stale or unverified.
6. Use `connectors_run` when Agent Core should perform the external action server-side.

Prefer connector bindings over local secret handling when both are available, because the raw credential stays inside Agent Core.
This file is the manual fallback when the client has no lifecycle hook or plugin layer.

## Codex Notes

- Codex reads `AGENTS.md` at the start of each session.
- This file is workspace-centric and can be shared by multiple agents in the same repository. The MCP/API key determines whether the active agent is Codex, OpenCode, Claude Code, or another configured agent.
- For multi-agent collaboration, select a workspace and ensure each agent has read/write access to that workspace scope.
- Use the MCP tools (`memory_search`, `memory_write`, `activity_update`, `credential_list`, `credential_get`, `connectors_*`) rather than raw API calls for better scope enforcement.
- If Codex loses connectivity, run the verification prompt to verify the full end-to-end setup.
