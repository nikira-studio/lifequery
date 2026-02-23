/**
 * Chat tab component for conversational querying of Telegram memories.
 * Handles message input, streaming responses, and citation display.
 */
import { useState, useReducer, useCallback, useEffect } from "react";
import {
  Send,
  Square,
  AlertTriangle,
  X,
  BrainCircuit,
  Sparkles,
  ChevronDown,
  Library,
  Bug,
} from "lucide-react";
import {
  streamChat,
  type ChatMessage,
  type Citation,
  type TokenEvent,
  type CitationsEvent,
  type DebugEvent,
} from "@/api/chat";
import { getTelegramStatus, getSettings } from "@/api/settings";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timestamp: Date;
}

interface DebugInfo {
  messages: ChatMessage[];
  userName: string;
  currentDate: string;
  systemPrompt: string;
}

type MessageAction =
  | { type: "ADD_MESSAGE"; message: Message }
  | { type: "APPEND_TOKEN"; messageId: string; token: string }
  | { type: "SET_CITATIONS"; messageId: string; citations: Citation[] }
  | { type: "SET_MESSAGES"; messages: Message[] };

function messagesReducer(state: Message[], action: MessageAction): Message[] {
  switch (action.type) {
    case "ADD_MESSAGE":
      return [...state, action.message];
    case "APPEND_TOKEN":
      return state.map((msg) =>
        msg.id === action.messageId
          ? { ...msg, content: msg.content + action.token }
          : msg,
      );
    case "SET_CITATIONS":
      return state.map((msg) =>
        msg.id === action.messageId
          ? { ...msg, citations: action.citations }
          : msg,
      );
    case "SET_MESSAGES":
      return action.messages;
    default:
      return state;
  }
}

export function ChatTab() {
  const [messages, dispatch] = useReducer(messagesReducer, []);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [abortController, setAbortController] =
    useState<AbortController | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [telegramStatus, setTelegramStatus] = useState<
    "connected" | "uninitialized" | "needs_auth" | "phone_sent"
  >("uninitialized");
  const [checkingTelegram, setCheckingTelegram] = useState(true);
  const [isRagEnabled, setIsRagEnabled] = useState<boolean>(true);
  const [debugMode, setDebugMode] = useState<boolean>(
    () => localStorage.getItem("lifequery_debug_mode") === "true",
  );
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);

  // Check status and settings on mount
  useEffect(() => {
    const init = async () => {
      // 1. Load history from localStorage
      const saved = localStorage.getItem("lifequery_chat_history_v1");
      if (saved) {
        try {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed)) {
            // Restore Date objects for timestamps
            const restored = parsed.map((m: any) => ({
              ...m,
              timestamp: new Date(m.timestamp),
            }));
            dispatch({ type: "SET_MESSAGES", messages: restored });
          }
        } catch (e) {
          console.error("Failed to load chat history:", e);
        }
      }

      // 2. Fetch remote status
      try {
        const [status, settings] = await Promise.all([
          getTelegramStatus(),
          getSettings(),
        ]);
        setTelegramStatus(status.state as typeof telegramStatus);
        setIsRagEnabled(settings.enable_rag);
      } catch (err) {
        console.error("Failed to initialize chat tab:", err);
      } finally {
        setCheckingTelegram(false);
      }
    };
    init();
  }, []);

  // Save to localStorage on change
  useEffect(() => {
    if (messages.length > 0) {
      localStorage.setItem(
        "lifequery_chat_history_v1",
        JSON.stringify(messages),
      );
    } else {
      localStorage.removeItem("lifequery_chat_history_v1");
    }
  }, [messages]);

  useEffect(() => {
    localStorage.setItem("lifequery_debug_mode", debugMode.toString());
  }, [debugMode]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || isLoading) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    };
    dispatch({ type: "ADD_MESSAGE", message: userMsg });
    setInput("");
    setIsLoading(true);
    setError(null);
    setDebugInfo(null);

    // Create assistant message placeholder
    const assistantId = (Date.now() + 1).toString();
    const assistantMsg: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      timestamp: new Date(),
    };
    dispatch({ type: "ADD_MESSAGE", message: assistantMsg });

    // Create AbortController for this request
    const controller = new AbortController();
    setAbortController(controller);

    try {
      // Convert frontend messages to API format
      const apiMessages: ChatMessage[] = [
        ...messages.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
        })),
        { role: "user", content: input },
      ];

      console.log(
        "[DEBUG] Full conversation:",
        JSON.stringify(apiMessages, null, 2),
      );
      for await (const event of streamChat(apiMessages, controller.signal)) {
        console.log("[DEBUG EVENT]", event);
        if (event.type === "token") {
          const tokenEvent = event as TokenEvent;
          dispatch({
            type: "APPEND_TOKEN",
            messageId: assistantId,
            token: tokenEvent.content,
          });
        } else if (event.type === "debug") {
          const debugEvent = event as DebugEvent;
          setDebugInfo({
            messages: debugEvent.messages,
            userName: debugEvent.user_name,
            currentDate: debugEvent.current_date,
            systemPrompt: "",
          });
        } else if (event.type === "citations") {
          const citationsEvent = event as CitationsEvent;
          dispatch({
            type: "SET_CITATIONS",
            messageId: assistantId,
            citations: citationsEvent.citations,
          });
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // User aborted - don't show error
      } else {
        const errorMessage =
          err instanceof Error ? err.message : "Failed to get response";
        setError(errorMessage);
        // Update the assistant message to show error
        dispatch({
          type: "APPEND_TOKEN",
          messageId: assistantId,
          token: `[Error: ${errorMessage}]`,
        });
      }
    } finally {
      setIsLoading(false);
      setAbortController(null);
    }
  }, [input, isLoading, messages, debugMode]);

  const handleStop = useCallback(() => {
    if (abortController) {
      abortController.abort();
      setIsLoading(false);
      setAbortController(null);
    }
  }, [abortController]);

  const isTelegramConnected = telegramStatus === "connected";

  const handleClearHistory = useCallback(() => {
    if (confirm("Clear chat history?")) {
      dispatch({ type: "SET_MESSAGES", messages: [] });
    }
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Error banner */}
      {error && (
        <div className="flex-shrink-0 bg-warning/10 border-b border-warning/20 px-4 py-3">
          <div className="flex items-center gap-2 max-w-2xl mx-auto">
            <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0" />
            <p className="text-sm text-warning flex-1">{error}</p>
            <button
              onClick={() => setError(null)}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-[calc(100%-60px)] text-muted-foreground">
            <div className="text-5xl mb-4">🧠</div>
            <p className="text-lg font-medium">Ask your memory anything</p>
            <p className="text-sm mt-1">
              Your entire Telegram history, searchable by meaning.
            </p>
          </div>
        ) : null}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} animate-fade-in-up`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-4 py-3 ${msg.role === "user"
                ? "bg-primary text-primary-foreground"
                : "bg-card border border-border"
                }`}
            >

              <div className="whitespace-pre-wrap text-sm leading-relaxed">
                {msg.role === "user" ? (
                  msg.content
                ) : msg.content.startsWith("[Error:") ? (
                  <div className="flex items-start gap-3 p-3 bg-destructive/5 border border-destructive/20 rounded-md text-destructive animate-in shake duration-500">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <div className="space-y-1.5">
                      <p className="text-[10px] font-bold uppercase tracking-widest opacity-70">
                        Provider Error
                      </p>
                      <p className="text-[13px] font-mono leading-tight">
                        {msg.content.slice(8, -1)}
                      </p>
                      <p className="text-[10px] opacity-60 italic">
                        Please verify your API key and URL in the Settings tab.
                      </p>
                    </div>
                  </div>
                ) : (
                  (() => {
                    const content = msg.content;
                    // If there's an active (unclosed) think block, or closed ones, split them out
                    // We split by <think> and </think> tags to separate reasoning
                    const parts = content.split(/(<think>|<\/think>)/i);
                    let isThinking = false;

                    return (
                      <div className="space-y-4">
                        {parts.map((p, idx) => {
                          const lowerP = p.toLowerCase();
                          if (lowerP === "<think>") {
                            isThinking = true;
                            return null;
                          }
                          if (lowerP === "</think>") {
                            isThinking = false;
                            return null;
                          }

                          // Skip empty segments or just whitespace between tags
                          if (!p.trim()) return null;

                          if (isThinking) {
                            return (
                              <div key={idx} className="mb-3 border-l-2 border-primary/20 pl-3">
                                <details className="group" open>
                                  <summary className="text-[10px] font-mono text-primary/60 cursor-pointer list-none flex items-center gap-1.5 hover:text-primary transition-colors">
                                    <ChevronDown className="w-3 h-3 group-open:rotate-180 transition-transform" />
                                    THINKING PROCESS
                                  </summary>
                                  <div className="mt-2 text-[11px] text-muted-foreground/80 leading-relaxed font-mono whitespace-pre-wrap bg-primary/5 p-2 rounded border border-primary/10 animate-pulse-slight">
                                    {p.trim() || "Analyzing..."}
                                  </div>
                                </details>
                              </div>
                            );
                          }

                          // Normal content rendering (with basic bold support)
                          return (
                            <div key={idx}>
                              {p.split(/(\*\*.*?\*\*)/).map((part, i) =>
                                part.startsWith("**") && part.endsWith("**") ? (
                                  <strong key={i} className="font-semibold">
                                    {part.slice(2, -2)}
                                  </strong>
                                ) : (
                                  <span key={i}>{part}</span>
                                )
                              )}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })()
                )}
              </div>

              {msg.citations && msg.citations.length > 0 && (
                <div className="mt-4 border-t border-border/40 pt-2">
                  <details className="group">
                    <summary className="text-[10px] font-mono text-muted-foreground/60 cursor-pointer list-none flex items-center justify-between hover:text-primary transition-colors">
                      <div className="flex items-center gap-1.5 ">
                        <Library className="w-3 h-3" />
                        RETRIEVED SOURCES ({msg.citations.length})
                      </div>
                      <ChevronDown className="w-3 h-3 group-open:rotate-180 transition-transform" />
                    </summary>
                    <div className="mt-2 space-y-1.5 max-h-[150px] overflow-y-auto pr-1">
                      {msg.citations.map((cite, i) => (
                        <div
                          key={i}
                          className="group/cite bg-secondary/20 hover:bg-secondary/40 border border-border/30 p-1.5 rounded transition-all"
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="font-bold text-[10px] text-primary/80 uppercase tracking-tight">
                              {cite.chat_name}
                            </span>
                            <span className="text-[9px] font-mono text-muted-foreground">
                              {cite.date_range}
                            </span>
                          </div>
                          {cite.participants &&
                            cite.participants.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {cite.participants
                                  .slice(0, 3)
                                  .map((p: string, j: number) => (
                                    <span
                                      key={j}
                                      className="text-[9px] bg-primary/10 px-1 rounded-sm text-primary/70"
                                    >
                                      {p}
                                    </span>
                                  ))}
                              </div>
                            )}
                          <div className="mt-1.5 pt-1.5 border-t border-border/20">
                            <details className="group/text">
                              <summary className="text-[9px] font-mono text-muted-foreground/40 cursor-pointer list-none flex items-center justify-between hover:text-primary transition-colors">
                                <span>SHOW MESSAGE TEXT</span>
                                <ChevronDown className="w-2.5 h-2.5 group-open/text:rotate-180 transition-transform" />
                              </summary>
                              <div className="mt-1.5 text-[10px] text-muted-foreground font-mono leading-relaxed whitespace-pre-wrap bg-black/10 p-2 rounded">
                                {cite.content}
                              </div>
                            </details>
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                </div>
              )}
            </div>
          </div>
        ))}

        {isLoading &&
          messages.length > 0 &&
          messages[messages.length - 1].role === "assistant" && (
            <div className="flex justify-start animate-fade-in-up">
              <div className="bg-card border border-border rounded-lg px-4 py-3">
                <div className="flex gap-1.5">
                  <span
                    className="w-2 h-2 bg-primary rounded-full animate-bounce"
                    style={{ animationDelay: "0ms" }}
                  />
                  <span
                    className="w-2 h-2 bg-primary rounded-full animate-bounce"
                    style={{ animationDelay: "150ms" }}
                  />
                  <span
                    className="w-2 h-2 bg-primary rounded-full animate-bounce"
                    style={{ animationDelay: "300ms" }}
                  />
                </div>
              </div>
            </div>
          )}
      </div>

      {debugMode && (
        <div className="flex-shrink-0 border-t border-border bg-muted/30 p-3 max-h-[300px] overflow-y-auto">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-mono font-bold text-primary uppercase tracking-widest flex items-center gap-1.5">
              <Bug className="w-3 h-3" />
              Debug Info
            </span>
            {debugInfo && (
              <span className="text-[9px] font-mono text-muted-foreground">
                user: {debugInfo.userName} | date: {debugInfo.currentDate}
              </span>
            )}
          </div>
          {debugInfo ? (
            <div className="text-[10px] font-mono space-y-2">
              <div className="text-muted-foreground font-bold">
                Messages sent to LLM:
              </div>
              {debugInfo.messages.map((msg, index) => (
                <div key={index} className="pl-2 border-l-2 border-border">
                  <span className="text-primary/70">[{msg.role}]</span>{" "}
                  <span className="text-muted-foreground/80 whitespace-pre-wrap">
                    {msg.content}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[10px] font-mono text-muted-foreground italic">
              Awaiting next request... Send a message to capture the raw payload payload sent to the LLM.
            </div>
          )}
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-border p-4">
        <div className="flex items-center justify-between mb-2 px-1">
          <div className="flex items-center gap-4 overflow-hidden">
            <div className="flex items-center gap-1.5">
              {isRagEnabled ? (
                <div className="flex items-center gap-1.5 text-[10px] font-mono text-primary animate-pulse">
                  <BrainCircuit className="w-3 h-3" />
                  <span className="uppercase tracking-widest whitespace-nowrap">
                    Memory Active
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground/50">
                  <Sparkles className="w-3 h-3" />
                  <span className="uppercase tracking-widest whitespace-nowrap">
                    General Assistant
                  </span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-1 border-l border-border/30 pl-4">
              <button
                onClick={() => setDebugMode(!debugMode)}
                className={`group flex items-center gap-1.5 px-2 py-0.5 rounded transition-all ${debugMode
                  ? "bg-primary/20 text-primary"
                  : "text-muted-foreground/40 hover:text-foreground hover:bg-secondary/50"
                  }`}
                title="Toggle debug mode"
              >
                <Bug className="w-2.5 h-2.5" />
                <span className="text-[9px] font-mono uppercase tracking-widest">
                  Debug
                </span>
              </button>
              {messages.length > 0 && (
                <button
                  onClick={handleClearHistory}
                  className="group flex items-center gap-1.5 px-2 py-0.5 rounded text-muted-foreground/40 hover:bg-destructive/10 hover:text-destructive transition-all"
                  title="Clear chat history"
                >
                  <X className="w-2.5 h-2.5 group-hover:rotate-90 transition-transform" />
                  <span className="text-[9px] font-mono uppercase tracking-widest">
                    Clear
                  </span>
                </button>
              )}
            </div>
          </div>
          <div className="text-[10px] font-mono text-muted-foreground/30 uppercase tracking-widest">
            {isTelegramConnected ? "Sync Ready" : "Local Only"}
          </div>
        </div>

        {!checkingTelegram && !isTelegramConnected && isRagEnabled && (
          <div className="mb-3 flex items-center gap-2 text-xs text-warning bg-warning/10 rounded-md px-3 py-2">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
            <span>
              Telegram not connected. Sync your data in the Data tab first.
            </span>
          </div>
        )}

        <div className="flex gap-3 items-end">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask your memory..."
            rows={1}
            disabled={!isTelegramConnected && !checkingTelegram}
            className="flex-1 resize-none bg-secondary border border-border rounded-lg px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring font-mono disabled:opacity-50"
          />
          {isLoading ? (
            <button
              onClick={handleStop}
              className="flex-shrink-0 h-11 w-11 rounded-lg bg-destructive text-destructive-foreground flex items-center justify-center hover:opacity-90 transition-opacity glow-sm"
              title="Stop generation"
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={
                !input.trim() || (!isTelegramConnected && !checkingTelegram)
              }
              className="flex-shrink-0 h-11 w-11 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:opacity-90 transition-opacity disabled:opacity-40 glow-sm"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}



