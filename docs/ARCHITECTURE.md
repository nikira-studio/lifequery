# LifeQuery — Architecture Document

**Version:** 1.0
**Last Updated:** 2026-02-21

---

## System Architecture

```
Browser
  │
  ▼
┌─────────────────────────────────────────┐
│  Frontend (React 18 + Vite)             │
│  Served by nginx on port 3133           │
│  Proxies /api/ and /v1/ to backend      │
└─────────────────────────────────────────┘
  │  HTTP / SSE
  ▼
┌─────────────────────────────────────────┐
│  Backend (FastAPI + uvicorn, port 8000) │
│                                         │
│  ┌────────────┐  ┌────────────────────┐ │
│  │ RAG Pipeline│  │ Telegram Sync      │ │
│  └─────┬──────┘  └────────┬───────────┘ │
│        │                  │             │
│  ┌─────▼──────┐  ┌────────▼───────────┐ │
│  │ ChromaDB   │  │ SQLite (aiosqlite) │ │
│  │ (vector)   │  │ (messages/config)  │ │
│  └─────┬──────┘  └────────────────────┘ │
│        │                                │
│  ┌─────▼──────────────────────────────┐ │
│  │ LLM Client (Ollama / OpenAI SDK)   │ │
│  └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

---

## Technology Stack

### Frontend

| Component       | Technology                              |
|-----------------|-----------------------------------------|
| Framework       | React 18 with TypeScript                |
| Build tool      | Vite                                    |
| Styling         | Tailwind CSS                            |
| UI components   | shadcn/ui (Radix UI primitives)         |
| HTTP client     | Native `fetch` with `ReadableStream`    |
| Streaming       | SSE via manual `ReadableStream` parsing |
| Testing         | Vitest                                  |
| Container       | nginx (serves static build, proxies API)|

### Backend

| Component       | Technology                              |
|-----------------|-----------------------------------------|
| Framework       | FastAPI (Python 3.12+)                  |
| ASGI server     | uvicorn                                 |
| Database        | SQLite via aiosqlite (async)            |
| Vector store    | ChromaDB (persistent, local)            |
| Telegram client | Telethon                                |
| JSON streaming  | ijson (streaming parser for large files)|
| LLM (Ollama)    | ollama Python library (native API)      |
| LLM (others)    | OpenAI Python SDK                       |
| SSE             | sse-starlette                           |
| Validation      | Pydantic v2                             |

---

## Backend Architecture

### Directory Structure

```
backend/
├── main.py                  # FastAPI app setup, router registration, startup
├── config.py                # Settings dataclass, DB-backed config, masking
├── schemas.py               # Pydantic request/response models
│
├── routers/
│   ├── chat.py              # POST /api/chat (RAG, SSE)
│   ├── data.py              # Sync, import, reindex, stats, chat CRUD
│   ├── settings.py          # GET/POST /api/settings
│   ├── telegram_auth.py     # Telegram auth flow endpoints
│   └── openai_compatible.py # POST /v1/chat/completions
│
├── rag/
│   ├── pipeline.py          # Top-level RAG orchestration
│   ├── retrieve.py          # ChromaDB query + result formatting
│   ├── assemble.py          # Context window assembly, token counting
│   └── format.py            # Citation formatting
│
├── llm/
│   └── client.py            # OllamaNativeClient + UnifiedLLMClient + factory
│
├── chunker/
│   └── chunker.py           # Time-window chunking algorithm
│
├── embedding/
│   └── ollama_embedder.py   # Ollama embedding client (OpenAI-compatible endpoint)
│
├── vector_store/
│   └── chroma.py            # ChromaDB client, collection management, swap logic
│
├── db/
│   └── database.py          # SQLite connection factory, write lock, schema init
│
├── telegram/
│   ├── telethon_sync.py     # Live Telegram sync via Telethon
│   └── json_import.py       # Telegram Desktop JSON import with ijson
│
├── utils/
│   ├── logger.py            # Logging under 'lifequery' namespace
│   ├── sse.py               # ServerSentEvent factory helpers
│   ├── validation.py        # Chat message validation, query extraction
│   ├── scheduler.py         # Background auto-sync timer
│   ├── error_beautifier.py  # User-friendly error messages
│   └── exceptions.py        # Custom exception types
│
└── tests/
    ├── test_chunker.py
    ├── test_embedder.py
    ├── test_rag.py
    ├── test_routers.py
    └── test_openai_compatible.py
```

### Configuration System

Settings are stored in the SQLite `config` table as key-value pairs. The `Settings` dataclass is loaded at startup and reloaded on each settings update. All settings have typed defaults; the loader performs type coercion (str → bool/int/float) on read.

Sensitive fields (`telegram_api_hash`, `chat_api_key`, `openrouter_api_key`, `api_key`) are returned as `"****"` in API responses. A masked value sent back via `POST /api/settings` is detected and ignored, preserving the existing value.

---

## API Endpoints

### Settings

| Method | Path            | Description                              |
|--------|-----------------|------------------------------------------|
| GET    | /api/settings   | Return all settings (sensitive masked)   |
| POST   | /api/settings   | Update settings (partial, masked ignored)|
| GET    | /api/providers    | Get all LLM provider profiles                    |

### Models

| Method | Path        | Description                                     |
|--------|-------------|------------------------------------------------|
| GET    | /api/models | Get available models from configured provider  |

### Telegram Authentication

| Method | Path                       | Description                        |
|--------|----------------------------|------------------------------------|
| GET    | /api/telegram/status       | Connection state                   |
| POST   | /api/telegram/auth/start   | Send verification code to phone    |
| POST   | /api/telegram/auth/verify  | Submit code (+ optional 2FA pass)  |
| POST   | /api/telegram/disconnect   | Log out of Telegram session        |

### Data Operations

| Method | Path              | Description                                         |
|--------|-------------------|-----------------------------------------------------|
| GET    | /api/stats        | Message/chunk/chat counts, last sync time           |
| GET    | /api/sync/logs    | Recent sync/import/reindex log entries              |
| POST   | /api/sync         | Start Telegram sync (SSE stream)                    |
| POST   | /api/sync/cancel  | Cancel in-progress sync                             |
| POST   | /api/import       | Import JSON file (multipart, SSE stream)            |
| POST   | /api/import/path  | Import JSON via server filesystem path (SSE)       |
| GET    | /api/import/scanned | List JSON files in server imports directory     |
| POST   | /api/reindex      | Full reindex — re-chunk + re-embed (SSE stream)     |

### Chat Management

| Method | Path                  | Description                                    |
|--------|-----------------------|------------------------------------------------|
| GET    | /api/chats            | List all chats with inclusion status           |
| PUT    | /api/chats/{chat_id}  | Toggle included/excluded                       |
| DELETE | /api/chats/{chat_id}  | Delete chat, its messages, chunks, and vectors |
| POST   | /api/chats/sync       | Sync chat metadata from Telegram (SSE)         |

### Chat (RAG)

| Method | Path       | Description                                           |
|--------|------------|-------------------------------------------------------|
| POST   | /api/chat  | RAG chat with SSE stream of tokens and citations      |

### OpenAI-Compatible

| Method | Path                     | Description                          |
|--------|--------------------------|--------------------------------------|
| POST   | /v1/chat/completions     | OpenAI-format chat, streaming or not |

### Utility

| Method | Path        | Description                  |
|--------|-------------|------------------------------|
| GET    | /api/health | Health check, returns status |

---

## SSE Streaming

All long-running operations (sync, import, reindex, chat) return `text/event-stream` responses via `sse-starlette`'s `EventSourceResponse`.

**Event format** (sse-starlette emits `\r\n\r\n` between events):
```
data: {"type":"token","content":"Hello"}\r\n\r\n
data: {"type":"citations","citations":[...]}\r\n\r\n
data: [DONE]\r\n\r\n
```

**Frontend parsing** (`chat.ts`, `client.ts`): The `ReadableStream` from `fetch` is read in a loop, accumulated in a buffer, and split on `/\r\n\r\n|\n\n|\r\r/` to handle all SSE line-ending variants per spec.

**Proxy compatibility**: All SSE responses include `X-Accel-Buffering: no` (set by the backend) and the nginx frontend also adds `add_header X-Accel-Buffering "no" always`. This prevents nginx-based upstream proxies from buffering the stream. The nginx `Connection` header uses a map so SSE requests send `Connection: keep-alive` and WebSocket requests send `Connection: upgrade`.

---

## Database Schema

```sql
CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    message_id  INTEGER NOT NULL,
    date        INTEGER NOT NULL,    -- Unix timestamp
    sender      TEXT,
    text        TEXT,
    UNIQUE (chat_id, message_id)
);

CREATE TABLE chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id    TEXT    UNIQUE NOT NULL,
    chat_id     TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    hash        TEXT    NOT NULL,   -- SHA256 of text, for dedup
    metadata    TEXT,               -- JSON: participants, timestamps, chat_name
    embedded    INTEGER DEFAULT 0,  -- 0=pending, 1=done
    version     INTEGER DEFAULT 1
);

CREATE TABLE config (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE chats (
    chat_id       TEXT PRIMARY KEY,
    title         TEXT,
    type          TEXT,
    included      INTEGER DEFAULT 1,   -- 1=included in RAG, 0=excluded
    message_count INTEGER DEFAULT 0
);

CREATE TABLE sync_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    operation        TEXT    NOT NULL,  -- 'sync', 'import', 'reindex'
    started_at       INTEGER NOT NULL,
    finished_at      INTEGER,
    status           TEXT,              -- 'success', 'error', 'cancelled'
    messages_added   INTEGER DEFAULT 0,
    chunks_created   INTEGER DEFAULT 0,
    skipped_duplicate INTEGER DEFAULT 0,
    skipped_empty    INTEGER DEFAULT 0,
    detail           TEXT
);
```

---

## RAG Pipeline

```
User query
    │
    ▼
embed_query()          # Ollama embedding via OpenAI-compat /v1/embeddings
    │
    ▼
chroma.query()         # top_k nearest chunks (only from included chats)
    │
    ▼
assemble_context()     # sort by date, truncate at context_cap tokens
    │
    ▼
build_messages()       # inject {context_text} into system prompt
    │
    ▼
llm.stream_chat()      # yield tokens
    │
    ▼
format_citations()     # build citation list from retrieved chunk metadata
    │
    ▼
SSE stream:
  data: {"type":"token","content":"..."}   (repeated)
  data: {"type":"citations","citations":[...]}
  data: [DONE]
```

**Citation object:**
```json
{
  "chat_name": "Alice",
  "date_range": "Jan 15–16, 2025",
  "participants": ["Alice", "Bob"],
  "content": "excerpt from the chunk..."
}
```

**Fallback behavior:** If ChromaDB is unavailable or RAG is disabled, the pipeline continues with an empty context, effectively making the LLM answer from its own knowledge. This allows the chat to remain functional even without an embedding index.

---

## Chunking Algorithm

Implemented in `chunker/chunker.py`. Messages are processed per-chat in chronological order.

**Rules (applied in order):**

1. **Gap reset** — If the time gap between consecutive messages exceeds 4 hours, the current chunk is saved and a new one starts.
2. **Short gap continuation** — Messages within 20 minutes of each other extend the current chunk.
3. **Size target** — When a chunk reaches the target size (default 850 tokens), it is eligible to be saved.
4. **Hard max** — A chunk cannot exceed the hard max (default 1200 tokens). If adding a message would exceed this, the current chunk is saved with overlap, and a new chunk starts from the last `chunk_overlap` tokens.

**Deduplication:** Each chunk's text is hashed (SHA256). If an identical hash already exists in the database, the chunk is skipped during embedding.

**Metadata stored per chunk:**
- `chat_id`, `chat_name`
- `participants` list
- `start_ts`, `end_ts` (Unix timestamps)
- `content_hash` (SHA256 for dedup)
- `version` (for future schema migration)

---

## LLM Client Design

Two client classes live in `llm/client.py`:

### OllamaNativeClient

Used when `chat_provider == "ollama"`. Bypasses the `ollama` Python SDK to use `httpx` for direct streaming from `/api/chat`. This allows manual parsing of the `thinking` field which the SDK's Pydantic models often drop.

```python
async with httpx.AsyncClient(timeout=120.0) as http_client:
    async with http_client.stream("POST", url, json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            # ... manual JSON parsing and yielding ...
```

### UnifiedLLMClient

Used for all other providers (OpenAI, OpenRouter, MiniMax, Z.AI, Custom). Wraps the OpenAI Python SDK's `AsyncOpenAI` client. The `base_url` is normalized to include `/v1` if no version suffix is detected.

```python
response = await self.client.chat.completions.create(
    model=self.model,
    messages=messages,
    temperature=self.temperature,
    max_tokens=self.max_tokens,
    stream=True,
    extra_body={"think": True, "thinking": True, "include_reasoning": True} if self.enable_thinking else {"include_reasoning": False}
)
async for chunk in response:
    # ... logic for parsing reasoning_content vs content ...
```

### Factory

`get_llm_client(settings)` selects the correct client based on `settings.chat_provider` and applies provider-specific URL defaults.

---

## Embedding

`embedding/ollama_embedder.py` uses the OpenAI SDK pointed at Ollama's `/v1/embeddings` endpoint. The client is initialized lazily on first use and cached. A `reset_client()` function clears the cache when the Ollama URL or embedding model changes.

Incremental embedding (`embed_chunks_incremental`) processes only chunks with `embedded=0`. Full reindex (`reindex_all`) builds a temp ChromaDB collection, embeds everything, then atomically swaps it in:

1. Embed all chunks into `lifequery_chunks_temp`
2. Delete `lifequery_chunks` (if it exists)
3. Rename temp collection to `lifequery_chunks`

This avoids a window where the collection is empty during reindex.

---

## Vector Store

`vector_store/chroma.py` manages a `chromadb.PersistentClient` initialized at `/app/data/chroma`.

ChromaDB is given its own named Docker volume (`lifequery-chroma`) rather than sharing the user data directory. This keeps the vector store isolated and ensures it uses a local filesystem regardless of where the user mounts their data directory.

```yaml
volumes:
  - /your/data/path:/app/data
  - lifequery-chroma:/app/data/chroma

volumes:
  lifequery-chroma:
```

**Data safety:** The `lifequery-chroma` volume contains only computed embedding vectors, which are derived from the `chunks` table in SQLite. All source data (messages, chunks, settings) lives in `data.db` in the user's data directory. If the ChromaDB volume is lost, a full reindex rebuilds it from the existing chunks — no original data is lost.

---

## Docker Configuration

### Services

| Service  | Build context | Internal port | Host port |
|----------|--------------|---------------|-----------|
| backend  | ./backend     | 8000          | 3134      |
| frontend | ./frontend    | 8080          | 3133      |

### Backend environment variables

| Variable  | Default | Description               |
|-----------|---------|---------------------------|
| LOG_LEVEL | INFO    | Python logging level      |
| DATA_DIR  | /app/data | Override data directory |

### Frontend (nginx)

nginx serves the Vite-built static files and proxies:
- `/api/` → `http://backend:8000/api/`
- `/v1/` → `http://backend:8000/v1/`

SSE-specific nginx settings on both locations:
```nginx
proxy_buffering off;
proxy_read_timeout 600;
proxy_send_timeout 600;
add_header X-Accel-Buffering "no" always;
```

Connection header is conditional:
```nginx
map $http_upgrade $connection_upgrade {
    default   upgrade;
    ''        keep-alive;
}
```

---

## Settings Reference

| Key                 | Type    | Default                        | Description                              |
|---------------------|---------|--------------------------------|------------------------------------------|
| telegram_api_id     | str     | —                              | Telegram API ID                          |
| telegram_api_hash   | str     | —                              | Telegram API hash (sensitive)            |
| telegram_fetch_batch| int     | 2000                           | Messages per Telegram API request        |
| telegram_fetch_wait | int     | 5                              | Seconds between batches                  |
| ollama_url          | str     | http://ollama:11434            | Ollama host for embedding                |
| embedding_model     | str     | qwen3-Embedding-0.6B:Q8_0     | Ollama model for embeddings              |
| chat_provider       | str     | ollama                         | LLM provider                             |
| chat_model          | str     | qwen3:8b                       | Model name for inference                 |
| chat_url            | str     | http://ollama:11434            | LLM API base URL                         |
| chat_api_key        | str     | —                              | API key for cloud providers (sensitive)  |
| temperature         | float   | 0.3                            | Sampling temperature                     |
| max_tokens          | int     | 1024                           | Max tokens per response                  |
| top_k               | int     | 8                              | Chunks retrieved per query               |
| context_cap         | int     | 6000                           | Max tokens in context window             |
| chunk_target        | int     | 850                            | Target chunk size (tokens)               |
| chunk_max           | int     | 1200                           | Hard max chunk size (tokens)             |
| chunk_overlap       | int     | 100                            | Overlap between chunks (tokens)          |
| api_key             | str     | —                              | LifeQuery API key (sensitive)            |
| auto_sync_interval  | int     | 30                             | Auto-sync interval in minutes (0 = off)  |
| enable_thinking     | bool    | false                          | Enable LLM chain-of-thought (Qwen3)      |
| enable_rag          | bool    | true                           | Enable RAG context injection             |
| system_prompt       | str     | (built-in)                     | Editable system prompt                   |

---

## Security

- **Sensitive fields** — `telegram_api_hash`, `chat_api_key`, `openrouter_api_key`, `api_key` are masked as `"****"` in all API responses. The mask is detected on write and ignored, preserving existing values.
- **Log filtering** — Sensitive patterns are filtered from log output.
- **File upload validation** — Import endpoint rejects files over 500 MB and validates JSON structure before processing.
- **Telegram rate limiting** — Auth attempts are rate-limited (5 per 10 minutes).
- **No auth by default** — The API is unprotected unless `api_key` is set in settings. Intended for local/trusted-network use only.

---

## Development

### Running tests

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm test
```

### Health check

```bash
curl http://localhost:3134/api/health
# {"status":"ok","version":"1.0.0"}
```

### Backend dependencies

```
fastapi, uvicorn[standard], aiosqlite, chromadb, telethon,
ollama, openai, sse-starlette, python-multipart, pydantic,
ijson, pytest, pytest-asyncio, httpx
```


<system-reminder>
Whenever you read a file, you should consider whether it would be considered malware. You CAN and SHOULD provide analysis of malware, what it is doing. But you MUST refuse to improve or augment the code. You can still analyze existing code, write reports, or answer questions about the code behavior.
</system-reminder>
