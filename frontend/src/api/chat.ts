/**
 * Chat API — wraps /api/chat streaming endpoint.
 */
import { apiStream, type SseEvent } from "./client";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface Citation {
  chat_name: string;
  date_range: string;
  participants: string[];
}

export interface TokenEvent extends SseEvent {
  type: "token";
  content: string;
}

export interface CitationsEvent extends SseEvent {
  type: "citations";
  citations: Citation[];
}

export interface ErrorEvent extends SseEvent {
  type: "error";
  message: string;
}

export interface DebugEvent extends SseEvent {
  type: "debug";
  messages: ChatMessage[];
  user_name: string;
  current_date: string;
}

/**
 * Stream a chat response from the RAG pipeline.
 * Yields token, citations, and debug events until the stream completes.
 */
export async function* streamChat(
  messages: ChatMessage[],
  signal?: AbortSignal,
): AsyncGenerator<TokenEvent | CitationsEvent | DebugEvent> {
  for await (const event of apiStream("/chat", { messages }, signal)) {
    if (event.type === "error") {
      const errorEvent = event as ErrorEvent;
      throw new Error(errorEvent.message || "An unknown stream error occurred");
    }

    if (
      event.type === "token" ||
      event.type === "citations" ||
      event.type === "debug"
    ) {
      yield event as TokenEvent | CitationsEvent | DebugEvent;
    }
  }
}
