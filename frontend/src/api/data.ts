/**
 * Data operations API — wraps /api/stats, /api/sync, /api/import, /api/reindex,
 * /api/sync-logs, and /api/import/scanned endpoints.
 */
import { apiFetch, apiStream, type SseEvent } from "./client";

export interface Stats {
  message_count: number;
  chunk_count: number;
  chat_count: number;
  included_chat_count: number;
  excluded_chat_count: number;
  embedded_count: number;
  last_sync: string | null;
  last_sync_added: number;
}

export interface ProgressEvent extends SseEvent {
  type: "progress";
  stage: string;
  message: string;
}

export interface DoneEvent extends SseEvent {
  type: "done";
  messages_added: number;
  chunks_created: number;
  chunks_embedded: number;
}

export interface SyncLogEntry {
  id: number;
  operation: string;
  started_at: number;
  finished_at: number | null;
  status: string | null;
  messages_added: number;
  chunks_created: number;
  skipped_duplicate: number;
  skipped_empty: number;
  detail: string | null;
}

export interface ScannedFile {
  path: string;
  name: string;
  size_mb: number;
  modified: number;
}

export interface ScannedImportsResponse {
  files: ScannedFile[];
  directory: string;
}

export async function fetchStats(): Promise<Stats> {
  return apiFetch<Stats>("/stats");
}

export async function* syncTelegram(
  signal?: AbortSignal,
): AsyncGenerator<ProgressEvent | DoneEvent> {
  for await (const event of apiStream("/sync", {}, signal)) {
    yield event as ProgressEvent | DoneEvent;
  }
}

export async function cancelSync(): Promise<void> {
  await fetch("/api/sync/cancel", { method: "POST" });
}

export async function* importJson(
  file: File,
  username?: string,
  signal?: AbortSignal,
): AsyncGenerator<ProgressEvent | DoneEvent> {
  const formData = new FormData();
  formData.append("file", file);
  if (username) {
    formData.append("username", username);
  }

  const res = await fetch("/api/import", {
    method: "POST",
    body: formData,
    signal,
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      const data = trimmed.slice(6);
      if (data === "[DONE]") return;
      try {
        const event = JSON.parse(data) as SseEvent;
        yield event as ProgressEvent | DoneEvent;
      } catch {
        // ignore malformed lines
      }
    }
  }
}

export async function* importJsonPath(
  path: string,
  username?: string,
  signal?: AbortSignal,
): AsyncGenerator<ProgressEvent | DoneEvent> {
  for await (const event of apiStream(
    "/import/path",
    { path, username },
    signal,
  )) {
    yield event as ProgressEvent | DoneEvent;
  }
}

export async function* reindexDatabase(
  signal?: AbortSignal,
): AsyncGenerator<ProgressEvent | DoneEvent> {
  for await (const event of apiStream("/reindex", { confirm: true }, signal)) {
    yield event as ProgressEvent | DoneEvent;
  }
}

export async function fetchSyncLogs(limit = 50): Promise<SyncLogEntry[]> {
  const res = await apiFetch<{ logs: SyncLogEntry[] }>(`/sync/logs?limit=${limit}`);
  return res.logs;
}

export async function fetchScannedImports(): Promise<ScannedImportsResponse> {
  return apiFetch<ScannedImportsResponse>("/import/scanned");
}

export interface PendingStats {
  unchunked_messages: number;
  unembedded_chunks: number;
  has_pending: boolean;
}

export async function fetchPendingStats(): Promise<PendingStats> {
  return apiFetch<PendingStats>("/pending-stats");
}

export async function* processPendingData(
  signal?: AbortSignal,
): AsyncGenerator<ProgressEvent | DoneEvent> {
  for await (const event of apiStream("/process", {}, signal)) {
    yield event as ProgressEvent | DoneEvent;
  }
}
