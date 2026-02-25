/**
 * Data tab component for managing Telegram data operations.
 * Handles sync, import, reindex, and chat management.
 */
import React, { useState, useRef, useEffect } from "react";
import { toast } from "@/hooks/use-toast";
import {
  RefreshCw,
  Upload,
  Database,
  CheckCircle2,
  AlertCircle,
  Loader2,
  FileJson,
  X,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Filter,
  HelpCircle,
  User,
  Search,
  History,
  Clock,
  ChevronDown,
  Terminal,
} from "lucide-react";
import {
  fetchStats,
  syncTelegram,
  cancelSync,
  importJson,
  importJsonPath,
  reindexDatabase,
  fetchSyncLogs,
  fetchScannedImports,
  fetchPendingStats,
  processPendingData,
  type Stats,
  type ProgressEvent,
  type DoneEvent,
  type SyncLogEntry,
  type ScannedFile,
} from "@/api/data";
import { getTelegramStatus } from "@/api/settings";
import {
  fetchChats,
  updateChat,
  deleteChat,
  syncChats,
  type Chat,
  type ChatProgressEvent,
  type ChatDoneEvent,
} from "@/api/chats";

type TaskStatus = "idle" | "running" | "success" | "error";

interface TaskState {
  status: TaskStatus;
  message?: string;
  progress?: number;
  totals?: { messages: number; chunks: number };
  lastRun?: string;
}

interface PendingStats {
  unchunked_messages: number;
  unembedded_chunks: number;
  has_pending: boolean;
}

export function DataTab() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [syncState, setSyncState] = useState<TaskState>({ status: "idle" });
  const [importState, setImportState] = useState<TaskState>({ status: "idle" });
  const [reindexState, setReindexState] = useState<TaskState>({
    status: "idle",
  });
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [telegramStatus, setTelegramStatus] = useState<
    "uninitialized" | "needs_auth" | "phone_sent" | "connected" | "error"
  >("uninitialized");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const reindexConfirmRef = useRef<HTMLDialogElement>(null);
  const deleteConfirmRef = useRef<HTMLDialogElement>(null);
  const chatToDeleteRef = useRef<Chat | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatsLoading, setChatsLoading] = useState(false);
  const [chatsFilter, setChatsFilter] = useState<
    "all" | "included" | "excluded"
  >("all");
  const [syncChatsState, setSyncChatsState] = useState<TaskState>({
    status: "idle",
  });
  const [importUsername, setImportUsername] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [syncLogs, setSyncLogs] = useState<SyncLogEntry[]>([]);
  const [showingLogs, setShowingLogs] = useState(false);
  const [useLocalPath, setUseLocalPath] = useState(false);
  const [localPathInput, setLocalPathInput] = useState("");
  const [scannedFiles, setScannedFiles] = useState<ScannedFile[]>([]);
  const [scannedDir, setScannedDir] = useState<string>("");
  const [pendingStats, setPendingStats] = useState<PendingStats | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  // Fetch all initial data in parallel on mount
  useEffect(() => {
    const loadAll = async () => {
      setChatsLoading(true);
      setStatsLoading(true);

      // Fetch items independently; update their respective loading states as they arrive.
      // We don't await the collection to prevent one slow/hanging request from blocking others.
      fetchStats()
        .then(setStats)
        .catch(e => console.error("Stats fetch failed:", e))
        .finally(() => setStatsLoading(false));

      fetchChats()
        .then(data => setChats(data.chats))
        .catch(e => console.error("Chats fetch failed:", e))
        .finally(() => setChatsLoading(false));

      fetchSyncLogs(10).then(setSyncLogs).catch(e => console.error("Logs fetch failed:", e));
      getTelegramStatus().then(data => setTelegramStatus(data.state)).catch(e => console.error("Telegram status fetch failed:", e));

      fetchScannedImports().then(data => {
        setScannedFiles(data.files);
        setScannedDir(data.directory);
      }).catch(e => console.error("Scanned imports fetch failed:", e));

      fetchPendingStats()
        .then(setPendingStats)
        .catch(e => console.error("Pending stats fetch failed:", e));
    };
    loadAll();
  }, []);

  // Poll for chats after connecting — the backend discovers them as a
  // background task after auth, so we retry until they appear.
  useEffect(() => {
    if (telegramStatus !== "connected" || chats.length > 0) return;

    let attempts = 0;
    const maxAttempts = 10;
    const interval = setInterval(async () => {
      attempts++;
      try {
        const data = await fetchChats();
        if (data.chats.length > 0) {
          setChats(data.chats);
          clearInterval(interval);
        }
      } catch {
        // ignore
      }
      if (attempts >= maxAttempts) clearInterval(interval);
    }, 3000);

    return () => clearInterval(interval);
  }, [telegramStatus, chats.length]);

  const refreshStats = async () => {
    try {
      fetchStats().then(setStats).catch(e => console.error("Refresh stats failed:", e));
      fetchPendingStats().then(setPendingStats).catch(e => console.error("Refresh pending stats failed:", e));
    } catch (err) {
      console.error("Failed to trigger refresh stats:", err);
      toast({
        title: "Failed to refresh stats",
        description: err instanceof Error ? err.message : "An unexpected error occurred",
        variant: "destructive",
      });
    }
  };

  const handleSync = async () => {
    if (syncState.status === "running") return;

    setSyncState({ status: "running", message: "Starting sync..." });

    try {
      for await (const event of syncTelegram()) {
        if (event.type === "progress") {
          const progress = event as ProgressEvent;
          setSyncState((prev) => ({
            ...prev,
            message: progress.message,
          }));
        } else if (event.type === "done") {
          const done = event as DoneEvent;
          const cancelled = (done as unknown as { cancelled?: boolean })
            .cancelled;
          setSyncState({
            status: "success",
            message: cancelled
              ? `Stopped — ${done.messages_added} messages synced so far`
              : `Synced ${done.messages_added} messages`,
            lastRun: new Date().toLocaleTimeString(),
          });
          await refreshStats();
        }
      }
    } catch (err) {
      setSyncState({
        status: "error",
        message: err instanceof Error ? err.message : "Sync failed",
      });
    }
  };

  const handleCancelSync = async () => {
    try {
      await cancelSync();
      setSyncState((prev) => ({
        ...prev,
        message: "Stopping after current chat...",
      }));
    } catch {
      // ignore
    }
  };
  const handleProcess = async () => {
    if (isProcessing) return;
    setIsProcessing(true);
    setSyncState({ status: "running", message: "Processing pending data..." });

    try {
      for await (const event of processPendingData()) {
        if (event.type === "progress") {
          const progress = event as ProgressEvent;
          setSyncState((prev) => ({
            ...prev,
            message: progress.message,
          }));
        } else if (event.type === "done") {
          const done = event as any;
          setSyncState({
            status: "success",
            message: `Processed ${done.chunks_created || 0} chunks and ${done.chunks_embedded || 0} embeddings`,
            lastRun: new Date().toLocaleTimeString(),
          });
          await refreshStats();
        }
      }
    } catch (err) {
      setSyncState({
        status: "error",
        message: err instanceof Error ? err.message : "Processing failed",
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleImport = async () => {
    const canStart = (useLocalPath && localPathInput.trim()) || selectedFile;
    if (!canStart || importState.status === "running") return;

    setImportState({
      status: "running",
      message: useLocalPath
        ? "Starting import..."
        : "Uploading file and preparing stream...",
    });

    try {
      const stream = useLocalPath
        ? importJsonPath(localPathInput, importUsername || undefined)
        : importJson(selectedFile!, importUsername || undefined);

      for await (const event of stream) {
        if (event.type === "progress") {
          const progress = event as ProgressEvent;
          setImportState((prev) => ({
            ...prev,
            message: progress.message,
          }));
        } else if (event.type === "done") {
          const done = event as any;
          const count = done.messages_added ?? done.inserted ?? 0;
          setImportState({
            status: "success",
            message: `Imported ${count.toLocaleString()} messages. Run Reindex to make them searchable.`,
            lastRun: new Date().toLocaleTimeString(),
          });
          setSelectedFile(null);
          await refreshStats();
          await refreshChats();
        }
      }

      // If loop finished but status is still "running", it means stream closed without "done" event
      setImportState((prev) => {
        if (prev.status === "running") {
          return {
            status: "idle",
            message: "Import session ended.",
          };
        }
        return prev;
      });
    } catch (err) {
      setImportState({
        status: "error",
        message: err instanceof Error ? err.message : "Import failed",
      });
    }
  };

  const handleReindex = async () => {
    // Show confirmation dialog
    if (reindexState.status === "running") return;
    reindexConfirmRef.current?.showModal();
  };

  const confirmReindex = async () => {
    reindexConfirmRef.current?.close();
    setReindexState({ status: "running", message: "Preparing reindex..." });

    try {
      for await (const event of reindexDatabase()) {
        if (event.type === "progress") {
          const progress = event as ProgressEvent;
          setReindexState((prev) => ({
            ...prev,
            message: progress.message,
          }));
        } else if (event.type === "done") {
          const done = event as DoneEvent;
          setReindexState({
            status: "success",
            message: `Reindexed ${done.chunks_embedded} chunks`,
            lastRun: new Date().toLocaleTimeString(),
          });
          await refreshStats();
        }
      }
    } catch (err) {
      setReindexState({
        status: "error",
        message: err instanceof Error ? err.message : "Reindex failed",
      });
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".json")) {
      setSelectedFile(file);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) setSelectedFile(file);
  };

  const refreshChats = async () => {
    setChatsLoading(true);
    try {
      const data = await fetchChats();
      setChats(data.chats);
    } catch (err) {
      console.error("Failed to refresh chats:", err);
      toast({
        title: "Failed to refresh chats",
        description:
          err instanceof Error ? err.message : "An unexpected error occurred",
        variant: "destructive",
      });
    } finally {
      setChatsLoading(false);
    }
  };

  const handleToggleChat = async (chat: Chat) => {
    try {
      await updateChat(chat.chat_id, !chat.included);
      await refreshChats();
    } catch (err) {
      console.error("Failed to toggle chat:", err);
      setSyncChatsState({
        status: "error",
        message: err instanceof Error ? err.message : "Failed to update chat",
      });
    }
  };

  const handleDeleteChat = (chat: Chat) => {
    chatToDeleteRef.current = chat;
    deleteConfirmRef.current?.showModal();
  };

  const confirmDeleteChat = async () => {
    const chat = chatToDeleteRef.current;
    if (!chat) return;

    deleteConfirmRef.current?.close();
    setSyncChatsState({
      status: "running",
      message: `Deleting ${chat.chat_name}...`,
    });

    try {
      const result = await deleteChat(chat.chat_id);
      setSyncChatsState({
        status: "success",
        message: `Deleted ${result.messages_deleted} messages and ${result.chunks_deleted} chunks`,
        lastRun: new Date().toLocaleTimeString(),
      });
      await refreshChats();
      await refreshStats();
      chatToDeleteRef.current = null;
    } catch (err) {
      setSyncChatsState({
        status: "error",
        message: err instanceof Error ? err.message : "Delete failed",
      });
    }
  };

  const handleSyncChats = async () => {
    if (syncChatsState.status === "running") return;

    setSyncChatsState({ status: "running", message: "Syncing chat list..." });

    try {
      for await (const event of syncChats()) {
        if (event.type === "progress") {
          const progress = event as ChatProgressEvent;
          setSyncChatsState((prev) => ({
            ...prev,
            message: progress.message,
          }));
        } else if (event.type === "done") {
          const done = event as ChatDoneEvent;
          setSyncChatsState({
            status: "success",
            message: `Found ${done.new} new chats, updated ${done.updated}`,
            lastRun: new Date().toLocaleTimeString(),
          });
          await refreshChats();
        }
      }
    } catch (err) {
      setSyncChatsState({
        status: "error",
        message: err instanceof Error ? err.message : "Sync failed",
      });
    }
  };

  const StatusIcon = ({ status }: { status: TaskStatus }) => {
    switch (status) {
      case "running":
        return <Loader2 className="w-4 h-4 animate-spin text-primary" />;
      case "success":
        return <CheckCircle2 className="w-4 h-4 text-success" />;
      case "error":
        return <AlertCircle className="w-4 h-4 text-destructive" />;
      default:
        return null;
    }
  };

  const filteredChats = chats.filter((chat) => {
    const isSearchActive = searchQuery.trim().length > 0;

    const matchesFilter =
      (chatsFilter === "all" &&
        (chat.included || chat.message_count > 0 || isSearchActive)) ||
      (chatsFilter === "included" && chat.included) ||
      (chatsFilter === "excluded" && !chat.included);

    const matchesSearch =
      chat.chat_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      chat.chat_id.includes(searchQuery);

    return matchesFilter && matchesSearch;
  });

  const refreshLogs = async () => {
    try {
      const logs = await fetchSyncLogs(10);
      setSyncLogs(logs);
    } catch (err) {
      console.error("Failed to refresh logs:", err);
      toast({
        title: "Failed to refresh logs",
        description:
          err instanceof Error ? err.message : "An unexpected error occurred",
        variant: "destructive",
      });
    }
  };

  const getChatTypeIcon = (type: string) => {
    switch (type) {
      case "group":
        return "👥";
      case "channel":
        return "📢";
      default:
        return "👤";
    }
  };

  const isTelegramConnected = telegramStatus === "connected";

  return (
    <div className="p-6 space-y-6 max-w-2xl mx-auto">
      <div>
        <h2 className="text-lg font-semibold text-foreground">
          Data Management
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Ingest and manage your Telegram history.
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <p className="text-2xl font-mono font-bold text-foreground">
            {statsLoading ? "..." : stats?.message_count?.toLocaleString() || "0"}
          </p>
          <p className="text-xs text-muted-foreground mt-1 uppercase tracking-wider">
            Messages
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <p className="text-2xl font-mono font-bold text-foreground">
            {statsLoading ? "..." : stats?.chunk_count?.toLocaleString() || "0"}
          </p>
          <div className="flex items-center justify-center gap-2 mt-1">
            <span className="text-[10px] text-success font-medium uppercase tracking-wider">
              {statsLoading ? "..." : stats?.embedded_count?.toLocaleString() || "0"} Embedded
            </span>
          </div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4 text-center">
          <p className="text-2xl font-mono font-bold text-foreground">
            {statsLoading ? "..." : stats?.chat_count?.toLocaleString() || "0"}
          </p>
          <div className="flex items-center justify-center gap-2 mt-1">
            <span className="text-[10px] text-success font-medium">
              {stats?.included_chat_count || 0} IN
            </span>
            <span className="text-[10px] text-muted-foreground font-medium">
              |
            </span>
            <span className="text-[10px] text-destructive font-medium">
              {stats?.excluded_chat_count || 0} OUT
            </span>
          </div>
        </div>
      </div>

      {stats?.last_sync && (
        <div className="px-1 flex justify-between items-center text-[11px] text-muted-foreground font-mono">
          <span>Last sync: {new Date(stats.last_sync).toLocaleString()}</span>
          <span>+{stats.last_sync_added} messages added</span>
        </div>
      )}

      {/* Pending Processing Alert */}
      {pendingStats?.has_pending && syncState.status !== "running" && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-4 flex items-center justify-between gap-4 animate-in fade-in slide-in-from-top-4 duration-500">
          <div className="flex items-start gap-3">
            <div className="p-2 bg-amber-500/20 rounded-full mt-0.5">
              <RefreshCw className="w-4 h-4 text-amber-500" />
            </div>
            <div>
              <p className="text-sm font-semibold text-amber-500">
                Pending Processing
              </p>
              <p className="text-xs text-amber-500/70 mt-0.5 leading-relaxed">
                Found {pendingStats.unchunked_messages > 0 && `${pendingStats.unchunked_messages} new messages`}
                {pendingStats.unchunked_messages > 0 && pendingStats.unembedded_chunks > 0 && " and "}
                {pendingStats.unembedded_chunks > 0 && `${pendingStats.unembedded_chunks} chunks`} waiting to be processed.
              </p>
            </div>
          </div>
          <button
            onClick={handleProcess}
            disabled={isProcessing}
            className="px-3 py-1.5 bg-amber-500 hover:bg-amber-600 disabled:opacity-50 text-white rounded-md text-xs font-semibold whitespace-nowrap transition-colors flex items-center gap-2"
          >
            {isProcessing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Database className="w-3 h-3" />}
            Process Now
          </button>
        </div>
      )}

      {/* Process Monitor */}
      {(syncState.status === "running" ||
        importState.status === "running" ||
        reindexState.status === "running" ||
        syncChatsState.status === "running") && (
          <div className="bg-primary/5 border border-primary/20 rounded-lg p-3 animate-in fade-in slide-in-from-top-4 duration-500">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Loader2 className="w-5 h-5 text-primary animate-spin" />
                <div>
                  <p className="text-sm font-semibold text-primary">
                    Active Background Process
                  </p>
                  <p className="text-xs text-primary/70 font-mono">
                    {reindexState.status === "running"
                      ? reindexState.message || "Reindexing..."
                      : syncState.status === "running"
                        ? syncState.message || "Syncing messages..."
                        : importState.status === "running"
                          ? importState.message || "Importing data..."
                          : syncChatsState.message || "Updating chats..."}
                  </p>
                </div>
              </div>
              <div className="px-2 py-1 bg-primary/10 rounded text-[10px] font-bold text-primary uppercase tracking-wider">
                Working
              </div>
            </div>
          </div>
        )}

      {/* Actions */}
      <div className="space-y-3">
        {/* Sync Telegram */}
        <ActionCard
          icon={<RefreshCw className="w-5 h-5" />}
          title="Sync Now"
          description="Manually fetch latest messages from Telegram (not automatic)"
          state={syncState}
          onRun={handleSync}
          onCancel={
            syncState.status === "running" ? handleCancelSync : undefined
          }
          StatusIcon={StatusIcon}
          disabled={!isTelegramConnected}
          disabledTooltip="Connect Telegram in Settings first"
        />

        {/* Chat Management */}
        <div className="bg-card border border-border rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-primary/10 text-primary">
                <Filter className="w-5 h-5" />
              </div>
              <div>
                <p className="font-medium text-foreground text-sm">
                  Chat Management
                </p>
                <p className="text-xs text-muted-foreground">
                  Control which chats are included in your memory
                </p>
              </div>
            </div>
            <button
              onClick={handleSyncChats}
              disabled={
                syncChatsState.status === "running" || !isTelegramConnected
              }
              className="flex-shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-all disabled:opacity-40"
              title={
                !isTelegramConnected
                  ? "Connect Telegram in Settings first"
                  : undefined
              }
            >
              {syncChatsState.status === "running"
                ? "Syncing..."
                : "Sync Chats"}
            </button>
          </div>
          {syncChatsState.message && syncChatsState.status !== "idle" && (
            <div className="flex items-center gap-1.5 px-1 mb-2">
              <StatusIcon status={syncChatsState.status} />
              <span className="text-xs font-mono text-muted-foreground">
                {syncChatsState.message}
              </span>
            </div>
          )}

          {/* Search and Filters */}
          <div className="flex flex-col gap-3">
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search chats by name or ID..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="input-field pl-10 h-10"
                />
              </div>
              <button
                onClick={refreshChats}
                disabled={chatsLoading}
                className="w-10 h-10 rounded-lg border border-border flex items-center justify-center hover:bg-secondary transition-colors disabled:opacity-50"
                title="Refresh chat list"
              >
                <RefreshCw
                  className={`w-4 h-4 ${chatsLoading ? "animate-spin" : ""}`}
                />
              </button>
            </div>

            {/* Chat Filter Tabs */}
            <div className="flex gap-1 bg-secondary rounded-lg p-1">
              {(["all", "included", "excluded"] as const).map((filter) => (
                <button
                  key={filter}
                  onClick={() => setChatsFilter(filter)}
                  className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${chatsFilter === filter
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                    }`}
                >
                  {filter.charAt(0).toUpperCase() + filter.slice(1)}
                </button>
              ))}
            </div>

            {/* Chat List */}
            <div className="border border-border rounded-lg overflow-hidden">
              {/* Header Row */}
              <div className="grid grid-cols-[1fr_80px_80px] gap-2 px-3 py-2 bg-muted/50 border-b border-border">
                <div className="text-xs font-medium text-muted-foreground uppercase">
                  Chat
                </div>
                <div
                  className="text-xs font-medium text-muted-foreground uppercase text-center"
                  title="Include or exclude from memory search"
                >
                  Include
                </div>
                <div
                  className="text-xs font-medium text-muted-foreground uppercase text-center"
                  title="Remove messages from LifeQuery only (not Telegram)"
                >
                  Delete
                </div>
              </div>

              {/* Chat rows */}
              <div className="max-h-[400px] overflow-y-auto divide-y divide-border">
                {chatsLoading ? (
                  <div className="p-8 text-center">
                    <Loader2 className="w-6 h-6 animate-spin text-primary mx-auto mb-2" />
                    <p className="text-sm text-muted-foreground">
                      Loading chats...
                    </p>
                  </div>
                ) : filteredChats.length === 0 ? (
                  <div className="p-8 text-center">
                    <Filter className="w-6 h-6 text-muted-foreground mx-auto mb-2" />
                    <p className="text-sm text-muted-foreground">
                      {chatsFilter === "all"
                        ? "No chats yet. Sync Telegram or import data to get started."
                        : `No ${chatsFilter} chats`}
                    </p>
                  </div>
                ) : (
                  filteredChats.map((chat) => (
                    <div
                      key={chat.chat_id}
                      className="grid grid-cols-[1fr_80px_80px] gap-2 px-3 py-2 hover:bg-secondary/50 transition-colors items-center"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-primary/10 text-foreground text-lg">
                          {getChatTypeIcon(chat.chat_type)}
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="font-medium text-foreground text-sm truncate">
                              {chat.chat_name}
                            </p>
                            {!chat.included && (
                              <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-xs bg-muted text-muted-foreground">
                                Excluded
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {chat.message_count.toLocaleString()} messages
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => handleToggleChat(chat)}
                        className="flex justify-center p-1 hover:bg-secondary rounded-lg transition-colors"
                        title={
                          chat.included
                            ? "Exclude from memory search"
                            : "Include in memory search"
                        }
                      >
                        {chat.included ? (
                          <ToggleRight className="w-5 h-5 text-primary" />
                        ) : (
                          <ToggleLeft className="w-5 h-5 text-muted-foreground" />
                        )}
                      </button>
                      <button
                        onClick={() => handleDeleteChat(chat)}
                        className="flex justify-center p-1 hover:bg-destructive/10 text-muted-foreground hover:text-destructive rounded-lg transition-colors"
                        title="Remove messages from LifeQuery (not from Telegram)"
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Import Legacy JSON */}
        <div className="bg-card border border-border rounded-lg p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center bg-primary/10 text-primary">
                <Upload className="w-5 h-5" />
              </div>
              <div>
                <p className="font-medium text-foreground text-sm">
                  Import Legacy JSON
                </p>
                <h3 className="text-sm font-medium">
                  Upload Telegram JSON export
                </h3>
              </div>
            </div>

            {/* Toggle between upload and local path */}
            <div className="flex bg-secondary p-0.5 rounded-md">
              <button
                onClick={() => setUseLocalPath(false)}
                className={`px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider transition-all ${!useLocalPath ? "bg-background text-primary shadow-sm" : "text-muted-foreground"}`}
              >
                Upload
              </button>
              <button
                onClick={() => setUseLocalPath(true)}
                className={`px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider transition-all ${useLocalPath ? "bg-background text-primary shadow-sm" : "text-muted-foreground"}`}
              >
                Path
              </button>
            </div>
          </div>

          {!useLocalPath ? (
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                const file = e.dataTransfer.files[0];
                if (file && file.name.endsWith(".json")) {
                  setSelectedFile(file);
                }
              }}
              className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${dragOver
                ? "border-primary bg-primary/5"
                : "border-border hover:border-primary/50 hover:bg-secondary/50"
                }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".json"
                onChange={handleFileSelect}
                className="hidden"
                disabled={importState.status === "running"}
              />
              {selectedFile ? (
                <div className="flex items-center justify-center gap-2">
                  <FileJson className="w-5 h-5 text-primary" />
                  <span className="text-sm font-mono text-foreground truncate max-w-[200px]">
                    {selectedFile.name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    ({(selectedFile.size / 1024 / 1024).toFixed(1)} MB)
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedFile(null);
                    }}
                    className="ml-2 text-muted-foreground hover:text-foreground"
                    disabled={importState.status === "running"}
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <div>
                  <FileJson className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">
                    Drop your{" "}
                    <span className="font-mono text-foreground">
                      result.json
                    </span>{" "}
                    here or click to browse
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="relative group">
                <Terminal className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="text"
                  value={localPathInput}
                  onChange={(e) => setLocalPathInput(e.target.value)}
                  placeholder="Absolute server path (e.g. /app/data/export.json)"
                  className="input-field pl-10 h-11 text-xs"
                  disabled={importState.status === "running"}
                />
              </div>
              <p className="text-[10px] text-muted-foreground italic px-1">
                Use this for large files that hit upload limits. The file must
                be accessible to the backend.
              </p>

              {scannedFiles.length > 0 ? (
                <div className="mt-4 space-y-2">
                  <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground px-1">
                    Available in{" "}
                    <code className="text-[9px] bg-secondary px-1 py-0.5 rounded">
                      {scannedDir}
                    </code>
                  </p>
                  <div className="grid grid-cols-1 gap-1.5">
                    {scannedFiles.map((file) => (
                      <button
                        key={file.path}
                        onClick={() => setLocalPathInput(file.path)}
                        className={`flex items-center justify-between p-2 rounded-lg border transition-all text-left ${localPathInput === file.path
                          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                          : "border-border hover:bg-secondary"
                          }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <FileJson
                            className={`w-3.5 h-3.5 flex-shrink-0 ${localPathInput === file.path ? "text-primary" : "text-muted-foreground"}`}
                          />
                          <span className="text-xs font-mono truncate">
                            {file.name}
                          </span>
                        </div>
                        <span className="text-[10px] text-muted-foreground whitespace-nowrap ml-2">
                          {file.size_mb} MB
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mt-4 p-3 bg-secondary/30 rounded-lg border border-dashed border-border/50">
                  <p className="text-[10px] text-muted-foreground leading-relaxed">
                    <span className="font-medium text-foreground block mb-1">
                      No files found on server.
                    </span>
                    Move your large Telegram JSON exports into your data
                    volume's{" "}
                    <code className="bg-background px-1 rounded">/imports</code>{" "}
                    folder to see them here.
                  </p>
                  <p className="text-[9px] text-muted-foreground/50 mt-2 font-mono">
                    Searching in: {scannedDir}
                  </p>
                </div>
              )}
            </div>
          )}

          {(selectedFile || (useLocalPath && localPathInput)) && (
            <div className="bg-secondary/50 rounded-lg p-3 space-y-3">
              <div className="flex items-center gap-2">
                <User className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                <input
                  type="text"
                  value={importUsername}
                  onChange={(e) => setImportUsername(e.target.value)}
                  placeholder="Your username in this export (optional)"
                  className="flex-1 px-3 py-1.5 text-xs bg-background border border-border rounded text-foreground placeholder:text-muted-foreground focus:outline-none"
                />
              </div>
              <button
                onClick={handleImport}
                disabled={importState.status === "running"}
                className="w-full py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90 transition-all disabled:opacity-40"
              >
                {importState.status === "running"
                  ? "Importing..."
                  : "Run Import"}
              </button>
            </div>
          )}

          {importState.status !== "idle" && (
            <div className="pt-2 border-t border-border mt-2">
              <div className="flex items-center gap-2 mb-2">
                <StatusIcon status={importState.status} />
                <span className="text-xs font-mono text-muted-foreground">
                  {importState.message}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Reindex Card */}
        <ActionCard
          icon={<Database className="w-5 h-5" />}
          title="Reindex Database"
          description="Wipe ChromaDB embeddings and recompute from scratch"
          state={reindexState}
          onRun={handleReindex}
          StatusIcon={StatusIcon}
          destructive
        />

        {/* Operation History */}
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <button
            onClick={() => setShowingLogs(!showingLogs)}
            className="w-full px-4 py-3 flex items-center justify-between hover:bg-secondary transition-colors"
          >
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-primary" />
              <span className="text-sm font-medium">Operation History</span>
            </div>
            <ChevronDown
              className={`w-4 h-4 text-muted-foreground transition-transform ${showingLogs ? "rotate-180" : ""}`}
            />
          </button>

          {showingLogs && (
            <div className="border-t border-border divide-y divide-border">
              {syncLogs.length === 0 ? (
                <div className="p-8 text-center text-sm text-muted-foreground">
                  No history entries found.
                </div>
              ) : (
                syncLogs.map((log) => (
                  <div key={log.id} className="p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                        {log.operation}
                      </span>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        {new Date(log.started_at * 1000).toLocaleString()}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 justify-between text-xs">
                      <div className="flex items-center gap-3">
                        <span className="flex items-center gap-1.5">
                          <CheckCircle2 className="w-3 h-3 text-success" />
                          {log.messages_added}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <Database className="w-3 h-3 text-primary" />
                          {log.chunks_created}
                        </span>
                      </div>
                      <span
                        className={`font-medium ${log.status === "success" ? "text-success" : "text-destructive"}`}
                      >
                        {log.status === "success" ? "Success" : "Failed"}
                      </span>
                    </div>
                    {log.status !== "success" && log.detail && (
                      <p className="text-[10px] text-destructive font-mono mt-1 bg-destructive/5 p-1.5 rounded border border-destructive/10 break-words">
                        {log.detail}
                      </p>
                    )}
                  </div>
                ))
              )}
              <div className="p-3 text-center border-t border-border">
                <button
                  onClick={refreshLogs}
                  className="text-[10px] font-medium text-primary hover:underline uppercase tracking-wider"
                >
                  Refresh History
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Confirmation Dialogs */}
      <dialog
        ref={reindexConfirmRef}
        className="rounded-lg p-6 backdrop:bg-black/50 bg-card border border-border shadow-lg max-w-md"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-warning flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-foreground">
                Reindex Database
              </h3>
              <p className="text-sm text-muted-foreground mt-2">
                This will wipe all existing embeddings and recompute them from
                your messages. This may take several minutes.
              </p>
            </div>
          </div>
          <div className="flex gap-3 justify-end pt-2">
            <button
              onClick={() => reindexConfirmRef.current?.close()}
              className="px-4 py-2 rounded text-sm font-medium hover:bg-secondary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={confirmReindex}
              className="px-4 py-2 rounded text-sm font-medium bg-destructive text-destructive-foreground hover:opacity-90"
            >
              Reindex Now
            </button>
          </div>
        </div>
      </dialog>

      <dialog
        ref={deleteConfirmRef}
        className="rounded-lg p-6 backdrop:bg-black/50 bg-card border border-border shadow-lg max-w-md"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-destructive flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-foreground">Delete Chat</h3>
              <p className="text-sm text-muted-foreground mt-2">
                Are you sure you want to delete{" "}
                <span className="font-medium text-foreground">
                  {chatToDeleteRef.current?.chat_name}
                </span>
                ? This action cannot be undone.
              </p>
            </div>
          </div>
          <div className="flex gap-3 justify-end pt-2">
            <button
              onClick={() => deleteConfirmRef.current?.close()}
              className="px-4 py-2 rounded text-sm font-medium hover:bg-secondary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={confirmDeleteChat}
              className="px-4 py-2 rounded text-sm font-medium bg-destructive text-destructive-foreground hover:opacity-90"
            >
              Delete Chat
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}

function ActionCard({
  icon,
  title,
  description,
  state,
  onRun,
  onCancel,
  StatusIcon,
  disabled,
  disabledTooltip,
  destructive,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  state: TaskState;
  onRun: () => void;
  onCancel?: () => void;
  StatusIcon: React.FC<{ status: TaskStatus }>;
  destructive?: boolean;
  disabled?: boolean;
  disabledTooltip?: string;
}) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 flex items-center gap-4">
      <div
        className={`flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center ${destructive
          ? "bg-destructive/10 text-destructive"
          : "bg-primary/10 text-primary"
          }`}
      >
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-foreground text-sm">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
        {state.message && state.status !== "idle" && (
          <div className="flex items-center gap-1.5 mt-1">
            <StatusIcon status={state.status} />
            <span className="text-xs font-mono text-muted-foreground">
              {state.message}
            </span>
          </div>
        )}
      </div>
      <div className="flex-shrink-0 flex gap-2">
        {onCancel && state.status === "running" && (
          <button
            onClick={onCancel}
            className="px-3 py-2 rounded-lg text-sm font-medium transition-all bg-destructive/10 text-destructive hover:bg-destructive/20"
            title="Stop after current chat finishes"
          >
            Stop
          </button>
        )}
        <button
          onClick={onRun}
          disabled={state.status === "running" || disabled}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-40 ${destructive
            ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
            : "bg-primary/10 text-primary hover:bg-primary/20"
            }`}
          title={disabled && disabledTooltip ? disabledTooltip : undefined}
        >
          {state.status === "running"
            ? "Running..."
            : title === "Sync Now"
              ? "Sync Now"
              : "Run"}
        </button>
      </div>
    </div>
  );
}




