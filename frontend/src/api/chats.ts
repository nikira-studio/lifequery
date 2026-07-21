/**
 * Chats API — wraps /api/chats endpoints.
 */
import { apiFetch, apiStream, type SseEvent } from "./client";

export interface Chat {
  chat_id: string;
  chat_name: string;
  chat_type: string;
  message_count: number;
  last_message_at: string | null;
  created_at: string | null;
  included: boolean;
}

export interface ChatProgressEvent extends SseEvent {
  type: "progress";
  stage: string;
  message: string;
}

export interface ChatDoneEvent extends SseEvent {
  type: "done";
  messages_added: number;
  chunks_created: number;
  chunks_embedded: number;
}

export async function fetchChats(): Promise<{ chats: Chat[] }> {
  return apiFetch<{ chats: Chat[] }>("/chats");
}

export async function updateChat(
  chatId: string,
  included: boolean,
): Promise<void> {
  await apiFetch(`/chats/${chatId}`, {
    method: "PUT",
    body: JSON.stringify({ included }),
  });
}

export async function renameChat(
  chatId: string,
  chatName: string,
): Promise<void> {
  await apiFetch(`/chats/${chatId}`, {
    method: "PUT",
    body: JSON.stringify({ chat_name: chatName }),
  });
}

export async function deleteChat(chatId: string): Promise<{
  ok: boolean;
  messages_deleted: number;
  chunks_deleted: number;
}> {
  return apiFetch<{
    ok: boolean;
    messages_deleted: number;
    chunks_deleted: number;
  }>(`/chats/${chatId}`, { method: "DELETE" });
}

export async function* syncChats(
  signal?: AbortSignal,
): AsyncGenerator<ChatProgressEvent | ChatDoneEvent> {
  for await (const event of apiStream("/chats/sync", {}, signal)) {
    yield event as ChatProgressEvent | ChatDoneEvent;
  }
}

export async function purgeGhostChats(): Promise<{
  ok: boolean;
  removed: number;
  checked_live: boolean;
}> {
  return apiFetch(`/chats/purge-ghosts`, { method: "POST" });
}

export type BulkChatAction = "include" | "exclude" | "delete" | "exclude_and_delete";

export async function bulkChatAction(
  chatIds: string[],
  action: BulkChatAction,
): Promise<{
  ok: boolean;
  processed: number;
  messages_deleted: number;
  chunks_deleted: number;
  errors: { chat_id: string; error: string }[];
}> {
  return apiFetch(`/chats/bulk`, {
    method: "POST",
    body: JSON.stringify({ chat_ids: chatIds, action }),
  });
}
