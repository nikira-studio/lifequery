# LifeQuery Code Review — Fix Plan

Generated from backend code review (~10k LOC). Architecture is solid; below are
actionable fixes ranked by impact. Check off as completed.

---

## Priority batch (fix first — high value, low risk)

These five directly affect answer quality and data correctness.

### [ ] 1. RAG assembly throws away relevance ranking
**File:** `backend/rag/assemble.py:37` (`build_context`)
**Problem:** `retrieve()` fetches `top_k * 3` chunks sorted by similarity, then
`build_context` does `chunks.sort(key=lambda x: x.timestamp_start, reverse=True)`
and greedily fills `context_cap`. The budget gets consumed by the most *recent*
chunks; the most *relevant* (older) chunks are dropped before reaching the LLM.
Also `.sort()` mutates the caller's list in place.
**Fix:** Truncate to `top_k` by relevance FIRST, then sort the survivors by date
for presentation only. Don't mutate the input list (copy or sort a slice).

### [ ] 2. "may" false-positive in date filter
**File:** `backend/rag/retrieve.py:91` (`parse_date_range`)
**Problem:** Month map includes `"may": 5`. Any query containing the word "may"
("I may have", "may I ask") matches `\bmay\b`, applies a hard timestamp filter to
May of current/last year. Combined with the strict
`timestamp_start >= … AND timestamp_end <= …` where-clause, retrieval is silently
scoped to the wrong month → wrong/empty answers.
**Fix:** Only apply a month filter when an explicit year is also present, OR
remove bare "may" from the abbreviations.

### [ ] 3. Incremental embed never deletes stale vectors
**File:** `backend/embedding/__init__.py:136`
**Problem:** `deleted_chunk_ids = chroma_chunk_ids - sqlite_chunk_ids` is computed
and reported in `counts["deleted"]`, but there is NO `collection.delete(...)` call
in the incremental path. Chunks removed from SQLite stay in ChromaDB and keep
getting retrieved. Only a full reindex (temp→swap) clears them.
**Fix:** Call `collection.delete(ids=list(deleted_chunk_ids))` (wrapped in
`asyncio.to_thread`) before finishing the generator.

### [ ] 4. Chunk overlap is dead and broken
**File:** `backend/chunker/chunker.py:210-218`
**Problem:** In the `chunk_max` split branch, `overlap_content` / `overlap_lines`
are computed then never used — the new chunk is just the second half with NO
overlap. So `chunk_overlap` (250) does nothing. Dead code also slices
`chunk_overlap` as a *line* count when it's documented as a *token* count.
**Fix:** Either implement real token-based overlap (carry the last
`chunk_overlap` tokens of messages into the next chunk) or remove the dead code
and the setting.

### [ ] 5. `chunk_target` setting is never used
**Files:** `backend/config.py:29,98`, `backend/schemas.py:44,77`,
`backend/routers/settings.py:37`, frontend settings types.
**Problem:** Exposed in config/schema/UI but no code reads it. Only `chunk_max`
affects chunking. Users tuning "chunk target" get no effect.
**Fix:** Wire it into `chunk_chat` (soft target before `chunk_max` hard limit) OR
remove it from config, schema, router, and frontend.

---

## Medium

### [ ] 6. Duplicate chunking implementation
**File:** `backend/chunker/chunker.py`
`chunk_messages()` (non-streaming, ~80 lines) is never called; only
`chunk_messages_streaming()` is used. Two copies of insert/dedup/`last_chunked_at`
logic. **Fix:** Delete the dead function or have it delegate to the generator.

### [ ] 7. Non-constant-time API key compare
**File:** `backend/utils/auth.py:28`
`provided_key != expected_key` is vulnerable to timing attacks.
**Fix:** Use `secrets.compare_digest(provided_key, expected_key)`.

### [ ] 8. Blocking ChromaDB calls inside async
**File:** `backend/embedding/__init__.py`
`collection.count()`, `collection.get()`, and `chroma_upsert()` are called
synchronously inside async functions (`get_embedded_versions`,
`embed_chunks_incremental`, `reindex_all`), blocking the event loop during
reindex. The `chroma.query()` path correctly uses `asyncio.to_thread`; the
embedding pipeline does not.
**Fix:** Wrap blocking Chroma calls in `asyncio.to_thread`.

### [ ] 9. No guard against concurrent syncs
**File:** `backend/utils/scheduler.py:32`
`auto_sync_worker` runs the full `sync_generator()`. A manual `POST /api/sync` or
`/api/process` can run simultaneously; `_write_lock` only serializes individual
writes and `cancel_sync()` is global. Two pipelines interleave on the same data.
**Fix:** Add a single "sync in progress" lock/flag guarding the whole pipeline.

### [ ] 10. Settings can't be cleared to empty
**Files:** `backend/config.py:122` (`_convert_value`),
`backend/routers/settings.py:76`
Both drop empty strings (empty → None → skipped on load; `if v != ""` filter on
save). Once set, `system_prompt`, `noise_filter_keywords`, and `api_key` can't be
blanked via the UI — so you can't disable auth by clearing the key.
**Fix:** Distinguish "field omitted" from "field explicitly set to empty" (e.g.
allow empty-string updates for clearable fields; keep the `****` mask sentinel for
sensitive fields).

---

## Minor / cleanup

- [ ] **Deprecated `datetime.utcfromtimestamp`** (removed in future 3.x) —
  `backend/rag/format.py:56`, `backend/rag/assemble.py:43`,
  `backend/chunker/chunker.py:44`. Use
  `datetime.fromtimestamp(ts, tz=timezone.utc)`.
- [ ] **Redundant branch** — `backend/config.py:194`:
  `str_value = str(value) if value != "" else ""` — both branches yield `""`.
- [ ] **Unused `import uuid`** in `backend/chunker/chunker.py:5`;
  `compute_content_hash` defined twice (chunker + embedding), used only in tests —
  consolidate.
- [ ] **Deprecated executor call** — `backend/routers/data.py:187` uses
  `asyncio.get_event_loop().run_in_executor`; prefer `asyncio.to_thread`.
- [ ] **Brittle model sentinel** — `backend/llm/client.py`: `model != "qwen3:8b"`
  used to swap in a default; a user genuinely naming a non-Ollama model `qwen3:8b`
  gets it silently replaced.
- [ ] **Overcounted `skipped`** — `backend/embedding/__init__.py:186`:
  `len(chroma_chunk_ids) - len(changed)` overcounts when Chroma holds stale ids
  (reporting only).
- [ ] **Inconsistent settings model** — global frozen-dataclass mutated via
  `object.__setattr__` (`backend/config.py:159`) vs per-request
  `replace(settings, …)` in the OpenAI router. Consider standardizing.

---

## Notes
- Backend lives in `backend/`; restart requires
  `docker compose build backend && docker compose up -d` (see CLAUDE.md / memory).
- After fixing #3 (stale-vector delete), a one-time full reindex via
  `POST /api/reindex {"confirm":true}` will clean any existing stale vectors.
- Tests: `backend/tests/` — run them after chunker/embedding changes
  (`test_chunker.py`, `test_embedder.py`, `test_rag.py`).
