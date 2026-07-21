# LifeQuery Agent API

LifeQuery exposes a narrow agent-facing REST API for tools such as Agent Core,
Hermes-style agents, digital scribes, and other OpenAPI connector systems.

This API is separate from the UI/admin API. It is designed for controlled data
retrieval and summaries, not for sync, import, settings, reindexing, or Telegram
authentication.

## OpenAPI Spec

Import this URL into OpenAPI-capable connector systems:

```text
http://localhost:3134/api/agent/openapi.json
```

If LifeQuery is running inside Docker and Agent Core is on the same Docker
network, use the service URL instead:

```text
http://backend:8000/api/agent/openapi.json
```

The filtered spec contains only the agent-safe endpoints.

## Authentication

Agent endpoints use Bearer auth with the LifeQuery API key configured in
Settings. The same key is used by the OpenAI-compatible `/v1` API.

```http
Authorization: Bearer YOUR_LIFEQUERY_API_KEY
```

If no API key is configured, LifeQuery allows access. That is convenient for
local-only testing, but do not expose raw-message endpoints outside a trusted
network without setting an API key.

## Endpoints

| Method | Path | Operation ID | Purpose |
|--------|------|--------------|---------|
| GET | `/api/agent/chats` | `list_chats` | List chats, groups, and channels for filters |
| GET | `/api/agent/people` | `list_people` | List known senders for filters |
| POST | `/api/agent/messages/query` | `query_messages` | Pull raw messages by date range and filters |
| POST | `/api/agent/chunks/query` | `query_chunks` | Pull chunked context by date range and filters |
| POST | `/api/agent/summary` | `summarize_range` | Generate an LLM summary over filtered messages |

## Query Messages

```http
POST /api/agent/messages/query
Authorization: Bearer YOUR_LIFEQUERY_API_KEY
Content-Type: application/json
```

```json
{
  "start": "2026-06-01T00:00:00Z",
  "end": "2026-06-08T00:00:00Z",
  "chat_ids": ["12345"],
  "sender_names": ["Alex"],
  "chat_types": ["group"],
  "included_only": true,
  "text_query": "release",
  "limit": 500,
  "order": "asc"
}
```

`start` is inclusive. `end` is exclusive. The response includes `next_cursor`
when another page is available. Send that cursor back with the same filters to
fetch the next page.

Each message also includes forward provenance when Telegram supplied it:

- `is_forwarded` — whether the message was forwarded into the current chat
- `forward_sender_id` / `forward_sender_name` — original author, not the person who forwarded it
- `forward_date`, `forward_chat_id`, `forward_message_id` — available original-source references

For a forwarded message, `sender_name` identifies the person who forwarded it
into this chat. Treat `forward_sender_name` as the author of the forwarded
content. Older records imported before this schema existed have null forward
fields and cannot be retroactively attributed.

Supported filters:

- `chat_ids`
- `chat_names`
- `chat_types`
- `sender_ids`
- `sender_names`
- `sources`
- `included_only`
- `text_query`
- `limit`
- `cursor`
- `order`

## Query Chunks

Use chunks when an agent needs larger conversation context instead of individual
messages. Chunks support date, chat, chat type, participant-name, included-only,
and text filters.

```json
{
  "start": "2026-06-01T00:00:00Z",
  "end": "2026-06-08T00:00:00Z",
  "chat_types": ["group"],
  "sender_names": ["Alex"],
  "include_content": true,
  "limit": 100
}
```

## Summaries

`/api/agent/summary` first pulls matching raw messages, then asks the configured
LifeQuery LLM to summarize only those messages.

```json
{
  "start": "2026-06-01T00:00:00Z",
  "end": "2026-06-08T00:00:00Z",
  "chat_names": ["Work"],
  "prompt": "Focus on decisions, blockers, and follow-ups.",
  "include_messages": false,
  "limit": 1000
}
```

Use `include_messages: true` when the caller needs to audit the source material
used for the summary.

## Agent Core

Recommended Agent Core path:

1. Import the filtered OpenAPI spec from `/api/agent/openapi.json`.
2. Store the LifeQuery API key as a credential.
3. Bind the imported connector to that credential with Bearer auth.
4. Scope the binding to the workspace or agents that should be able to read
   LifeQuery data.

For a quick answer-only integration, Agent Core can also use a generic HTTP
connector against `/v1/chat/completions`. The Agent API is the better option
when agents need structured date/person/group retrieval.
