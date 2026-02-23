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

export async function deleteChat(chatId: string): Promise<void> {
  await apiFetch(`/chats/${chatId}`, { method: "DELETE" });
}

export async function* syncChats(
  signal?: AbortSignal,
): AsyncGenerator<ChatProgressEvent | ChatDoneEvent> {
  for await (const event of apiStream("/chats/sync", {}, signal)) {
    yield event as ChatProgressEvent | ChatDoneEvent;
  }
}
