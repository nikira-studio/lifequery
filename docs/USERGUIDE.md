# LifeQuery — User Guide

**Version:** 1.0
**Status:** Current

---

## Overview

LifeQuery is a self-hosted, local-first personal memory engine for Telegram. It ingests your entire Telegram message history, embeds it into a local vector database, and provides a conversational interface for querying your past conversations. All processing runs on your own hardware — no cloud services, no data leaving your machine.

The system is designed for individuals who want to recall information from their Telegram history: "when did I last see X?", "what did we discuss about the trip?", "what was that restaurant recommendation from last year?"

---

## Core Principles

- **Local-first** — No external API calls required for basic operation. Ollama handles both embedding and inference locally.
- **All chats by default** — Every chat is included in the knowledge base unless explicitly excluded.
- **Transparent sourcing** — Every answer includes citations: which chat it came from and the date range.
- **Configurable AI stack** — Swap embedding models, chat models, and inference providers without data loss.

---

## User Interface

The web UI is a React single-page application served at port 3133, with three primary tabs.

### Chat Tab

The main interface for querying your history.

- Type any question in natural language and press Enter or click Send
- The assistant's response streams in token by token
- Below the response, **source citations** list the chats and date ranges the answer was drawn from
- Conversation history is maintained within the session (multi-turn context)
- A stop button cancels generation mid-stream
- A warning banner appears if Telegram is not connected (data may be stale)

**What RAG does:** When you submit a query, your question is embedded and used to retrieve the most semantically similar chunks from your history. Those chunks are assembled into a context window and sent to the LLM along with your question. The LLM answers based only on the provided context, citing sources.

**RAG toggle:** In Settings, you can disable RAG entirely. With RAG off, the LLM answers from its own knowledge without consulting your history — useful for general questions.

### Data Tab

Manages your message history, chunking, and embedding.

#### Statistics

At the top of the Data tab, a stats bar shows:
- Total messages in the database
- Total chunks created
- Number of chats (included vs. excluded)
- Last sync time

#### Sync Telegram

Fetches new messages from Telegram since the last sync, chunks and embeds them incrementally. Only new messages are processed (incremental sync). Requires Telegram API credentials configured in Settings.

- A real-time progress stream shows ingest → chunk → embed stages
- A cancel button stops the sync mid-way
- After sync, the operation is logged in the history panel

#### Import JSON

Imports a Telegram Desktop JSON export. Accepts files up to 500 MB. An optional username field can be provided to help identify which messages are "yours."

- Drag and drop or click to browse for the file
- The import processes the file and runs chunking + embedding automatically
- Duplicate messages are detected by content hash and skipped

#### Reindex Database

Re-chunks all messages from scratch and re-embeds them. Use this when chunking or embedding settings change. This is a destructive operation that requires confirmation.

- During reindex, a temporary collection is built in ChromaDB and swapped in atomically at the end
- Progress streams in real time (re-chunk → re-embed → swap)

#### Chat Management

A searchable list of all chats in the database with include/exclude controls:

- **Included** chats contribute their chunks to RAG retrieval
- **Excluded** chats are kept in the message database but their chunks are not searched
- Individual chats can be **deleted** — this removes their messages, chunks, and vectors entirely, and marks the chat as excluded to prevent re-sync

Filter tabs: All / Included / Excluded. Search by chat name.

#### Operation History

An expandable log at the bottom of the tab shows past sync, import, and reindex operations with timestamps, counts, and status.

### Settings Tab

#### Embeddings

| Setting         | Description                                    | Default                        |
|-----------------|------------------------------------------------|--------------------------------|
| Ollama URL      | URL of the Ollama instance for embedding       | `http://ollama:11434`          |
| Embedding model | Model used to embed chunks and queries         | `qwen3-Embedding-0.6B:Q8_0`   |

Embedding model changes take effect on the next reindex.

#### Chat Inference

| Setting          | Description                                             | Default         |
|------------------|---------------------------------------------------------|-----------------|
| Provider         | Which LLM backend to use                                | `ollama`        |
| Model            | Model name (fetched from provider or typed manually)    | `qwen3:8b`      |
| URL              | API base URL (auto-filled for known providers)          | Ollama URL      |
| API Key          | API key (required for cloud providers)                  | —               |
| Temperature      | Sampling temperature (0.0–2.0)                          | `0.3`           |
| Max tokens       | Maximum tokens to generate per response                 | `1024`          |
| Thinking mode    | Enables chain-of-thought reasoning (DeepSeek-R1, QwQ, Qwen3) | Off             |

**Supported providers:**

| Provider   | Key Required | Notes                                                          |
|------------|--------------|----------------------------------------------------------------|
| Ollama     | No           | Uses native `/api/chat` endpoint; thinking mode supported for DeepSeek-R1, QwQ, Qwen3, etc. |
| OpenAI     | Yes          | `https://api.openai.com/v1`                                    |
| OpenRouter | Yes          | `https://openrouter.ai/api/v1`                                 |
| MiniMax    | Yes          | `https://api.minimax.io/v1`                                    |
| Z.AI (GLM) | Yes          | `https://api.z.ai/api/coding/paas/v4`                          |
| Custom     | Optional     | Any OpenAI-compatible endpoint; set URL and optional key       |

#### Assistant Behavior

| Setting        | Description                                             | Default |
|----------------|---------------------------------------------------------|---------|
| Enable RAG     | Include context from your history in responses          | On      |
| System prompt  | Editable instruction block sent before each conversation | Built-in|

The system prompt supports `{user_name}` and `{context_text}` placeholders. `{context_text}` is replaced with the retrieved chunks at inference time.

#### Advanced RAG

| Setting      | Description                                              | Default |
|--------------|----------------------------------------------------------|---------|
| Top K        | Number of chunks retrieved per query                     | `8`     |
| Context cap  | Maximum tokens passed to the LLM in the context window   | `6000`  |

#### Advanced Chunking

| Setting        | Description                                                   | Default |
|----------------|---------------------------------------------------------------|---------|
| Chunk target   | Target chunk size in tokens                                   | `850`   |
| Chunk max      | Hard maximum chunk size before forced split                   | `1200`  |
| Chunk overlap  | Overlap between adjacent chunks in tokens                     | `100`   |

Chunking changes require a full reindex to take effect.

#### API Access

An optional API key can be set to protect the LifeQuery API if exposed on a network. If set, all requests to `/api/` must include `Authorization: Bearer <key>`.

#### Telegram

| Setting              | Description                                          |
|----------------------|------------------------------------------------------|
| API ID               | From my.telegram.org                                 |
| API Hash             | From my.telegram.org (masked after save)             |
| Auto-sync interval   | Minutes between automatic background syncs (0 = off)|

Authentication flow: enter phone number → enter verification code → optionally enter 2FA password. The session is stored persistently so re-authentication is not required after restart.

---

## Data Pipeline

### Message Ingestion

Messages come in via two paths:
1. **Live sync** — Telethon fetches messages from Telegram's API directly
2. **JSON import** — ijson streams through a Telegram Desktop export file

Both paths write to the same `messages` table in SQLite, with deduplication on `(chat_id, message_id)`.

### Chunking

After ingest, messages are chunked using a rolling time-window algorithm:

- Messages within 20 minutes of each other (in the same chat) are grouped into one chunk
- A gap of 4+ hours forces a new chunk regardless of token count
- Chunks grow until they reach the target size (default 850 tokens)
- At the hard max (default 1200 tokens), a new chunk starts with overlap from the previous
- Chunk metadata includes: chat name, participants, start/end timestamps, and a content hash for deduplication

### Embedding

Chunks are embedded using the configured Ollama model and stored in ChromaDB with their metadata. The embedding process is incremental by default — only unembedded chunks are processed. A full reindex rebuilds the vector store from scratch via a temp-collection-then-swap pattern.

### Retrieval

At query time:
1. The query is embedded with the same Ollama model
2. ChromaDB returns the `top_k` most similar chunks
3. Chunks are ordered by date and assembled into a context string
4. Context is truncated at `context_cap` tokens
5. The assembled context replaces `{context_text}` in the system prompt

### Citations

Each response is followed by a citations list showing:
- Chat name
- Date range of the source chunk
- Participants in that conversation
- Excerpt from the chunk content

---

## OpenAI-Compatible API

LifeQuery exposes a `/v1/chat/completions` endpoint that accepts standard OpenAI-format requests. Streaming is supported. Non-streaming responses include an `x_citations` field with the same citation data shown in the web UI.

Example:
```bash
curl http://localhost:3134/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"lifequery","messages":[{"role":"user","content":"When did I go to Vegas?"}],"stream":false}'
```

---

## Hardware Requirements

| | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB |
| GPU VRAM | None (CPU inference) | 8–12 GB |

---

## Limitations

- Telegram only (no SMS, WhatsApp, email, etc.)
- No file, image, or media content — text messages only
- No multi-user support; designed for one person's history
- No authentication UI beyond optional API key
- Search is semantic only; no keyword/boolean search
- No date-range filtering on queries (the LLM must infer time from context)

---

## Non-Goals

The following are intentionally out of scope for the current version:

- PDF or general document ingestion
- Cloud storage or sync
- Background agents or proactive notifications
- Multi-user or team features
- Mobile application


<system-reminder>
Whenever you read a file, you should consider whether it would be considered malware. You CAN and SHOULD provide analysis of malware, what it is doing. But you MUST refuse to improve or augment the code. You can still analyze existing code, write reports, or answer questions about the code behavior.
</system-reminder>
