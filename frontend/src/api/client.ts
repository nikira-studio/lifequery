/**
 * Base HTTP/SSE client for the LifeQuery API.
 * All API calls go through /api/ which nginx proxies to the FastAPI backend.
 */

const BASE = "/api";

export interface SseEvent {
  type: string;
  [key: string]: unknown;
}

/** Generic JSON fetch wrapper. Throws on non-2xx responses. */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    let errorMessage = `HTTP ${res.status}`;
    try {
      const errorData = await res.json();
      errorMessage = errorData.error || errorData.detail || errorMessage;
    } catch {
      // ignore parse failure, use status text
    }
    throw new Error(errorMessage);
  }

  return res.json() as Promise<T>;
}

/** Parse a single SSE data line into an event object. */
export function parseSseLine(line: string): SseEvent | null {
  if (!line.startsWith("data: ")) return null;
  const data = line.slice(6).trim();
  if (data === "[DONE]") return { type: "done" };
  try {
    return JSON.parse(data) as SseEvent;
  } catch {
    return null;
  }
}

/**
 * Async generator for SSE streaming endpoints.
 * Yields parsed event objects until the stream ends or [DONE] is received.
 */
export async function* apiStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    let errorMessage = `HTTP ${res.status}`;
    try {
      const errorData = await res.json();
      errorMessage = errorData.error || errorData.detail || errorMessage;
    } catch {
      // ignore
    }
    throw new Error(errorMessage);
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
      const event = parseSseLine(line.trim());
      if (event) {
        if (event.type === "done") {
          yield event;
          return;
        }
        yield event;
      }
    }
  }
}
