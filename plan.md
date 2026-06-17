# LifeQuery Code Review — Fix Plan

Generated from backend code review (~10k LOC). All items implemented 2026-06-17.

---

## Priority batch ✅

### [x] 1. RAG assembly throws away relevance ranking
**File:** `backend/rag/assemble.py` (`build_context`)
**Fix:** Fill context budget in relevance order (do not sort input). After
selecting chunks, sort only the selected set chronologically for presentation.
Store `(chunk, formatted_text)` pairs during selection to avoid double-rendering.

### [x] 2. "may" false-positive in date filter
**File:** `backend/rag/retrieve.py` (`parse_date_range`)
**Fix:** Removed `"may": 5` from the months dict. Common modal verb ("I may
have…") was triggering May month-scoping on unrelated queries. Users wanting
May-specific results should include the year ("May 2024").

### [x] 3. Incremental embed never deletes stale vectors
**File:** `backend/embedding/__init__.py`
**Fix:** Compute `deleted_chunk_ids = chroma_chunk_ids - sqlite_chunk_ids` and
call `collection.delete(ids=list(deleted_chunk_ids))` (via `asyncio.to_thread`)
before the batch embedding loop. Also corrected `skipped` count to use
`shared_chunk_ids - changed_chunk_ids` instead of over-counting stale ids.

### [x] 4. Chunk overlap is dead and broken
**File:** `backend/chunker/chunker.py` (`chunk_chat`)
**Fix:** Replaced dead `overlap_lines` code. Now carries messages from the END of
`first_half` into the next chunk until their cumulative tokens exceed
`settings.chunk_overlap`. Provides real token-based overlap.

### [x] 5. `chunk_target` setting is never used
**File:** `backend/chunker/chunker.py`
**Fix:** Soft-break threshold changed from hardcoded `CHUNK_MIN_TOKENS` (300) to
`settings.chunk_target` (default 1000). A 20-minute gap now finalizes a chunk
only when it has reached the target size. `CHUNK_MIN_TOKENS` constant kept for
test backward-compatibility.

---

## Medium ✅

### [x] 6. Duplicate chunking implementation
**File:** `backend/chunker/chunker.py`
**Fix:** Deleted dead `chunk_messages()` non-streaming function (~85 lines). Only
`chunk_messages_streaming()` is used in production.

### [x] 7. Non-constant-time API key compare
**File:** `backend/utils/auth.py`
**Fix:** Replaced `provided_key != expected_key` with
`not secrets.compare_digest(provided_key, expected_key)`.

### [x] 8. Blocking ChromaDB calls inside async
**File:** `backend/embedding/__init__.py`
**Fix:** Wrapped `collection.count()`, `collection.get()`, `collection.delete()`,
and `chroma_upsert()` calls in `asyncio.to_thread()` in both
`get_embedded_versions()`, `embed_chunks_incremental()`, and `reindex_all()`.

### [x] 9. No guard against concurrent syncs
**Files:** `backend/routers/data.py`, `backend/utils/scheduler.py`
**Fix:** Added module-level `_sync_lock = asyncio.Lock()` in `data.py`. Both
`sync_generator` and `process_generator` check/acquire the lock and return an
error event if already locked. `auto_sync_worker` checks the lock before
attempting a sync.

### [x] 10. Settings can't be cleared to empty
**Files:** `backend/config.py`, `backend/routers/settings.py`
**Fix:** `_convert_value` now returns `""` for string fields when `value == ""`
(instead of `None`/skipped). Settings router filter changed from `v != ""` to
`v is not None` so explicit empty-string updates reach the database.

---

## Minor / cleanup ✅

- [x] **`datetime.utcfromtimestamp` deprecated** — replaced with
  `datetime.fromtimestamp(ts, tz=timezone.utc)` in `rag/format.py`,
  `rag/assemble.py`, `chunker/chunker.py`.
- [x] **Redundant branch** — `config.py` `save_to_db`:
  `str(value) if value != "" else ""` → `str(value)`.
- [x] **Unused `import uuid`** removed from `chunker/chunker.py`.
- [x] **Deprecated `asyncio.get_event_loop().run_in_executor`** in
  `routers/data.py:delete_chat` → `asyncio.to_thread`.
- [x] **Brittle model sentinel** in `llm/client.py` — `model != "qwen3:8b"`
  guard replaced with simple `model if model else "default"` for OpenAI,
  MiniMax, and GLM providers.
- [x] **Overcounted `skipped`** in `embedding/__init__.py` — now uses
  `shared_chunk_ids` (present in both SQLite and Chroma) minus changed ones.

---

## Not changed (by design)

- **`compute_content_hash` duplication** — chunker's version returns a 64-char
  full SHA256 (required by `test_hash_length`); embedding's version truncates to
  16 chars for storage efficiency. Different purposes; kept separate.
- **Inconsistent settings model** (global frozen-dataclass mutated via
  `object.__setattr__`) — architectural change, deferred.

---

## Post-deploy notes

- After deploying, run `POST /api/reindex {"confirm":true}` once to purge any
  stale vectors that accumulated before fix #3.
- New `chunk_target=1000` soft-break threshold creates larger chunks than the
  old 300-token floor. Existing chunks are unaffected; only new messages get
  the new sizing. A full reindex applies it uniformly.
- Rebuild: `docker compose build backend && docker compose up -d`
