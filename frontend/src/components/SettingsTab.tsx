import React, { useState, useEffect } from "react";
import { toast } from "@/hooks/use-toast";
import {
  AlertTriangle,
  RefreshCw,
  Loader2,
  ChevronDown,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { useModels } from "@/hooks/useModels";
import {
  getSettings,
  saveSettings,
  getTelegramStatus,
  getProviders,
  type Settings,
  type TelegramStatus,
  type ProviderProfile,
} from "@/api/settings";
import { SettingField } from "@/components/settings/SettingField";
import { TelegramAuthTab } from "@/components/TelegramAuthTab";

type Tab = "general" | "telegram";

export function SettingsTab() {
  const [activeTab, setActiveTab] = useState<Tab>("general");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus>({
    state: "uninitialized",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [showReindexWarning, setShowReindexWarning] = useState(false);

  // Local form state (initialized from settings)
  const [localSettings, setLocalSettings] = useState<Partial<Settings>>({});

  // Fetch models for embedding selection (called at top level)
  const embeddingModels = useModels({
    filter: "embedding",
    provider: "ollama", // Embeddings are ALWAYS driven by Ollama
    url: localSettings.ollama_url || settings?.ollama_url,
  });

  // Fetch models for chat selection (called at top level)
  const chatModels = useModels({
    filter: "chat",
    provider: localSettings.chat_provider || settings?.chat_provider,
    url: localSettings.chat_url || settings?.chat_url,
    api_key: localSettings.chat_api_key || settings?.chat_api_key,
  });

  const hardwarePresets = [
    {
      id: "standard",
      label: "Standard",
      desc: "CPU / 16GB RAM",
      settings: {
        top_k: 5,
        context_cap: 2000,
        chunk_target: 750,
        chunk_max: 1000,
        chunk_overlap: 100,
        max_tokens: 4096,
      },
    },
    {
      id: "midrange",
      label: "Mid-Range",
      desc: "6-8GB GPU",
      settings: {
        top_k: 10,
        context_cap: 6000,
        chunk_target: 850,
        chunk_max: 1200,
        chunk_overlap: 150,
        max_tokens: 4096,
      },
    },
    {
      id: "ultimate",
      label: "Ultimate",
      desc: "12GB+ GPU",
      settings: {
        top_k: 15,
        context_cap: 10000,
        chunk_target: 1000,
        chunk_max: 1500,
        chunk_overlap: 250,
        max_tokens: 4096,
      },
    },
  ];

  // Fetch settings and providers on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setError(null);
        const [settingsData, providersData] = await Promise.all([
          getSettings(),
          getProviders(),
        ]);
        setSettings(settingsData);
        setLocalSettings(settingsData);
        setProviders(providersData);
      } catch (err) {
        console.error("Failed to load settings:", err);
        setError(
          "Could not connect to the backend. The database might be busy or the server might be restarting.",
        );
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  const handleSave = async () => {
    if (!settings) return;

    setSaving(true);
    setSaveSuccess(false);

    try {
      // Filter out sensitive fields that haven't changed (still "****")
      const updateData: Partial<Settings> = {};

      for (const [key, value] of Object.entries(localSettings)) {
        if (value === undefined) continue;

        const isSensitiveField = [
          "telegram_api_hash",
          "openrouter_api_key",
          "chat_api_key",
          "api_key",
        ].includes(key);

        if (isSensitiveField) {
          if (value !== "****" && value !== "") {
            (updateData as Record<string, unknown>)[key] = value;
          }
        } else {
          (updateData as Record<string, unknown>)[key] = value;
        }
      }

      await saveSettings(updateData);

      // Refresh settings and providers from server
      const [updatedSettings, updatedProviders] = await Promise.all([
        getSettings(),
        getProviders(),
      ]);
      setSettings(updatedSettings);
      setLocalSettings(updatedSettings);
      setProviders(updatedProviders);

      // Refresh telegram status after saving credentials
      const status = await getTelegramStatus();
      setTelegramStatus(status);

      // Refetch models to update the list for the new provider/URL
      embeddingModels.refetch();
      chatModels.refetch();

      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      console.error("Failed to save settings:", err);
      toast({
        title: "Failed to save settings",
        description:
          err instanceof Error ? err.message : "An unexpected error occurred",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="p-12 flex flex-col items-center justify-center h-full space-y-4">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground animate-pulse">
          Loading settings...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-12 flex flex-col items-center justify-center h-full text-center space-y-4 max-w-md mx-auto">
        <div className="w-12 h-12 bg-destructive/10 text-destructive rounded-full flex items-center justify-center">
          <AlertTriangle className="w-6 h-6" />
        </div>
        <div>
          <h3 className="text-lg font-semibold text-foreground">
            Connection Error
          </h3>
          <p className="text-sm text-muted-foreground mt-1">{error}</p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="px-6 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 transition-all border border-primary/20 shadow-sm"
        >
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-2xl mx-auto pb-12">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Settings</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Configure your AI stack and parameters.
        </p>
      </div>

      {/* Tab selector */}
      <div className="flex gap-1 bg-secondary rounded-lg p-1">
        {[
          { id: "general" as Tab, label: "General" },
          { id: "telegram" as Tab, label: "Telegram" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 px-3 py-2 rounded-md text-sm font-medium transition-all ${activeTab === tab.id
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
              }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* General settings tab */}
      {activeTab === "general" && settings && (
        <>
          {/* Embeddings Section */}
          <section className="space-y-4">
            <h3 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">
              Embeddings
            </h3>
            <div className="bg-card border border-border rounded-lg p-4 space-y-4">
              <SettingField label="Ollama URL">
                <input
                  value={localSettings.ollama_url || ""}
                  onChange={(e) =>
                    setLocalSettings({
                      ...localSettings,
                      ollama_url: e.target.value,
                    })
                  }
                  className="input-field"
                />
              </SettingField>
              <SettingField label="Embedding Model">
                <ModelSelector
                  models={embeddingModels.models}
                  loading={embeddingModels.loading}
                  error={embeddingModels.error}
                  value={
                    localSettings.embedding_model || settings.embedding_model
                  }
                  onChange={(v) => {
                    if (v !== localSettings.embedding_model) {
                      setLocalSettings({
                        ...localSettings,
                        embedding_model: v,
                      });
                      setShowReindexWarning(true);
                    }
                  }}
                  onRefresh={() => { }}
                  placeholder="Select embedding model..."
                />
              </SettingField>

              {showReindexWarning && (
                <div className="flex items-start gap-2 bg-warning/10 border border-warning/20 rounded-md p-3">
                  <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-warning">
                    Changing embedding model requires a full reindex of your
                    database.
                  </p>
                </div>
              )}
            </div>
          </section>

          {/* Assistant Behavior Section - Global Settings */}
          <section className="space-y-4">
            <h3 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">
              Assistant Behavior
            </h3>
            <div className="bg-card border border-border rounded-lg p-4 space-y-5">
              <SettingField
                label="System Prompt"
                helpText="Template variables: {context_text} = retrieved memories, {user_name} = your name, {current_date} = today's date"
              >
                <textarea
                  value={
                    localSettings.system_prompt ?? settings.system_prompt ?? ""
                  }
                  onChange={(e) =>
                    setLocalSettings({
                      ...localSettings,
                      system_prompt: e.target.value,
                    })
                  }
                  className="input-field min-h-[160px] text-xs font-mono leading-relaxed bg-black/20"
                  placeholder="System instructions..."
                />
                <div className="flex flex-col gap-1.5 mt-2">
                  <p className="text-[10px] text-muted-foreground/60 leading-tight italic">
                    Include <code>{"{context_text}"}</code> for memory
                    insertion, <code>{"{user_name}"}</code> for
                    personalization, and <code>{"{current_date}"}</code> for
                    today&apos;s date (used for temporal accuracy).
                  </p>
                </div>
              </SettingField>

              {/* RAG Toggle */}
              <div className="flex items-center justify-between gap-4 py-1 border-y border-border/30 py-4">
                <div className="space-y-0.5">
                  <label className="text-xs font-medium text-muted-foreground whitespace-nowrap">
                    Enable Memory Retrieval (RAG)
                  </label>
                  <p className="text-[10px] text-muted-foreground leading-relaxed">
                    When enabled, the assistant uses your Telegram history to
                    answer questions. Disable for general-purpose chat.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={localSettings.enable_rag ?? settings.enable_rag}
                  onClick={() =>
                    setLocalSettings({
                      ...localSettings,
                      enable_rag: !(
                        localSettings.enable_rag ?? settings.enable_rag
                      ),
                    })
                  }
                  className={`flex-shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${(localSettings.enable_rag ?? settings.enable_rag)
                    ? "bg-primary"
                    : "bg-muted-foreground/30"
                    }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${(localSettings.enable_rag ?? settings.enable_rag)
                      ? "translate-x-6"
                      : "translate-x-1"
                      }`}
                  />
                </button>
              </div>

              <div className="flex items-center justify-between gap-4 py-1 border-b border-border/30 pb-4">
                <div className="space-y-0.5">
                  <label className="text-xs font-medium text-muted-foreground whitespace-nowrap">
                    Enable Deep Reasoning (Thinking)
                  </label>
                  <p className="text-[10px] text-muted-foreground leading-relaxed">
                    When enabled, compatible models (like DeepSeek, Qwen3, GLM)
                    will show their internal &quot;thinking&quot; chain before
                    the answer.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={
                    !!(
                      localSettings.enable_thinking ?? settings.enable_thinking
                    )
                  }
                  onClick={() =>
                    setLocalSettings({
                      ...localSettings,
                      enable_thinking: !(
                        localSettings.enable_thinking ??
                        settings.enable_thinking
                      ),
                    })
                  }
                  className={`flex-shrink-0 relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${localSettings.enable_thinking ?? settings.enable_thinking
                    ? "bg-primary"
                    : "bg-muted-foreground/30"
                    }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${localSettings.enable_thinking ?? settings.enable_thinking
                      ? "translate-x-6"
                      : "translate-x-1"
                      }`}
                  />
                </button>
              </div>

              <SettingField
                label="Noise Filter Keywords"
                helpText="Comma-separated phrases. Any message containing these will be ignored during indexing (e.g., 'Facility Manager, System Log')."
              >
                <input
                  value={localSettings.noise_filter_keywords ?? ""}
                  onChange={(e) =>
                    setLocalSettings({
                      ...localSettings,
                      noise_filter_keywords: e.target.value,
                    })
                  }
                  className="input-field"
                  placeholder="Keywords to ignore..."
                />
              </SettingField>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <SettingField
                  label={`Temperature: ${(localSettings.temperature ?? settings.temperature).toFixed(1)}`}
                >
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={localSettings.temperature ?? settings.temperature}
                    onChange={(e) =>
                      setLocalSettings({
                        ...localSettings,
                        temperature: parseFloat(e.target.value),
                      })
                    }
                    className="w-full accent-primary"
                  />
                  <div className="flex justify-between text-[9px] text-muted-foreground font-mono mt-1">
                    <span>Precise</span>
                    <span>Creative</span>
                  </div>
                </SettingField>

                <SettingField
                  label={`Max Tokens: ${localSettings.max_tokens ?? settings.max_tokens}`}
                >
                  <input
                    type="range"
                    min={256}
                    max={16384}
                    step={256}
                    value={localSettings.max_tokens ?? settings.max_tokens}
                    onChange={(e) =>
                      setLocalSettings({
                        ...localSettings,
                        max_tokens: parseInt(e.target.value),
                      })
                    }
                    className="w-full accent-primary"
                  />
                  <div className="flex justify-between text-[9px] text-muted-foreground font-mono mt-1">
                    <span>256</span>
                    <span>16k</span>
                  </div>
                </SettingField>
              </div>
            </div>
          </section>

          {/* Chat Inference Section */}
          <section className="space-y-4">
            <h3 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">
              Chat Inference
            </h3>
            <div className="bg-card border border-border rounded-lg p-4 space-y-4">
              <SettingField label="Provider">
                <select
                  value={localSettings.chat_provider || settings.chat_provider}
                  onChange={(e) => {
                    const newProviderId = e.target.value;
                    const profile = providers.find(
                      (p) => p.id === newProviderId,
                    );

                    const updates: Partial<Settings> = {
                      chat_provider: newProviderId,
                    };

                    // Profile-based URL and Key loading
                    let targetUrl = localSettings.chat_url;
                    let targetKey = localSettings.chat_api_key;
                    let targetModel = localSettings.chat_model;

                    if (profile) {
                      targetUrl = profile.base_url || targetUrl;
                      targetKey = profile.api_key || "";
                      targetModel = profile.last_model || "";

                      updates.chat_url = targetUrl;
                      updates.chat_api_key = targetKey;
                      updates.chat_model = targetModel;
                    }

                    setLocalSettings({
                      ...localSettings,
                      ...updates,
                    });

                    // Instant preview of models for the new provider/profile
                    chatModels.refetch({
                      provider: newProviderId,
                      url: targetUrl,
                      api_key: targetKey,
                    });
                  }}
                  className="input-field"
                >
                  <option value="ollama">Ollama (Local)</option>
                  <option value="openai">OpenAI</option>
                  <option value="openrouter">OpenRouter (Cloud)</option>
                  <option value="minimax">MiniMax Coding Plan</option>
                  <option value="glmai">Z.AI Coding Plan</option>
                  <option value="custom">Custom Endpoint</option>
                </select>
              </SettingField>

              {/* Chat URL field - shown for all providers but with different labels/placeholders */}
              <SettingField
                label={
                  localSettings.chat_provider === "ollama"
                    ? "Ollama URL"
                    : localSettings.chat_provider === "openai"
                      ? "OpenAI API URL"
                      : localSettings.chat_provider === "openrouter"
                        ? "OpenRouter Override URL"
                        : localSettings.chat_provider === "minimax"
                          ? "MiniMax Coding Plan URL"
                          : localSettings.chat_provider === "glmai"
                            ? "Z.AI Coding Plan URL"
                            : "Endpoint URL"
                }
              >
                <input
                  value={localSettings.chat_url ?? ""}
                  onChange={(e) => {
                    const newUrl = e.target.value;
                    setLocalSettings({
                      ...localSettings,
                      chat_url: newUrl,
                    });
                  }}
                  onBlur={() => {
                    chatModels.refetch({
                      provider: localSettings.chat_provider,
                      url: localSettings.chat_url,
                      api_key: localSettings.chat_api_key,
                    });
                  }}
                  placeholder={
                    localSettings.chat_provider === "openai"
                      ? "https://api.openai.com/v1"
                      : localSettings.chat_provider === "openrouter"
                        ? "https://api.openrouter.ai/api/v1"
                        : localSettings.chat_provider === "minimax"
                          ? "https://api.minimax.io/v1"
                          : localSettings.chat_provider === "glmai"
                            ? "https://api.z.ai/api/coding/paas/v4"
                            : localSettings.chat_provider === "custom"
                              ? "https://..."
                              : ""
                  }
                  className="input-field"
                />
              </SettingField>

              {/* API Key field - shown for everything except Ollama */}
              {localSettings.chat_provider !== "ollama" && (
                <SettingField label="Chat API Key">
                  <div className="relative group">
                    <input
                      type="password"
                      value={
                        localSettings.chat_api_key === "****" ||
                          !localSettings.chat_api_key
                          ? ""
                          : localSettings.chat_api_key
                      }
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          chat_api_key: e.target.value,
                        })
                      }
                      onBlur={() => {
                        if (
                          localSettings.chat_api_key &&
                          localSettings.chat_api_key !== "****"
                        ) {
                          chatModels.refetch({
                            provider: localSettings.chat_provider,
                            url: localSettings.chat_url,
                            api_key: localSettings.chat_api_key,
                          });
                        }
                      }}
                      placeholder={
                        settings.chat_api_key === "****" &&
                          (localSettings.chat_api_key === "****" ||
                            !localSettings.chat_api_key)
                          ? "••••••••"
                          : localSettings.chat_provider === "minimax"
                            ? "sh-..."
                            : "API Key"
                      }
                      className="input-field pr-16"
                    />
                    {/* Show saved badge if the active profile has a key and the local value is empty or same mask */}
                    {(() => {
                      const activeId =
                        localSettings.chat_provider || settings.chat_provider;
                      const profile = providers.find((p) => p.id === activeId);
                      const isSaved = profile?.api_key === "****";
                      const isCurrentLocalSaved =
                        localSettings.chat_api_key === "****" ||
                        !localSettings.chat_api_key;

                      if (isSaved && isCurrentLocalSaved) {
                        return (
                          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-1.5 px-1.5 py-0.5 rounded border border-primary/20 bg-primary/5">
                            <span className="text-[9px] text-primary font-mono uppercase tracking-widest font-bold">
                              Saved
                            </span>
                          </div>
                        );
                      }
                      return null;
                    })()}
                  </div>
                </SettingField>
              )}

              <SettingField label="Chat Model">
                <div className="space-y-2">
                  {chatModels.loading ? (
                    <div className="flex items-center gap-2 p-2 bg-secondary/30 rounded border border-border/50">
                      <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                      <span className="text-xs text-muted-foreground italic">
                        Fetching available models...
                      </span>
                    </div>
                  ) : (
                    <ModelSelector
                      models={chatModels.models}
                      loading={chatModels.loading}
                      error={chatModels.error}
                      value={localSettings.chat_model || ""}
                      onChange={(v) =>
                        setLocalSettings({ ...localSettings, chat_model: v })
                      }
                      onRefresh={() =>
                        chatModels.refetch({
                          provider: localSettings.chat_provider,
                          url: localSettings.chat_url,
                          api_key: localSettings.chat_api_key,
                        })
                      }
                      placeholder={
                        localSettings.chat_provider === "custom"
                          ? "Enter or select model..."
                          : "Select chat model..."
                      }
                      allowManualEntry={true}
                    />
                  )}
                  {localSettings.chat_provider === "openrouter" &&
                    !localSettings.chat_model &&
                    !chatModels.loading && (
                      <p className="text-[10px] text-muted-foreground">
                        {chatModels.error
                          ? "Failed to fetch models. Check URL/API Key."
                          : "Enter your API key to fetch available models."}
                      </p>
                    )}
                  {localSettings.chat_provider === "custom" && (
                    <p className="text-[10px] text-muted-foreground">
                      Most OpenAI-compatible APIs support model listing. If
                      yours doesn't, you can still type the name.
                    </p>
                  )}
                </div>
              </SettingField>

            </div>
          </section>

          {/* API Access Section */}
          <section className="space-y-4">
            <h3 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">
              API Access
            </h3>
            <div className="bg-card border border-border rounded-lg p-4 space-y-4">
              <SettingField label="OpenAI-Compatible API Key (Optional)">
                <div className="relative">
                  <input
                    type="password"
                    value={localSettings.api_key || settings.api_key}
                    onChange={(e) =>
                      setLocalSettings({
                        ...localSettings,
                        api_key: e.target.value,
                      })
                    }
                    placeholder={
                      settings.api_key === "****"
                        ? "Leave empty to keep existing key"
                        : "Optional: set a key to secure your /v1/chat/completions endpoint"
                    }
                    className="input-field"
                  />
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  When set, requests to{" "}
                  <code className="bg-muted px-1 rounded">
                    /v1/chat/completions
                  </code>{" "}
                  must include this as a Bearer token.
                </p>
                <div className="mt-2 p-2 bg-muted/50 rounded border border-border/50 space-y-1">
                  <p className="text-[10px] text-muted-foreground font-medium">
                    Your OpenAI-compatible endpoint:
                  </p>
                  <code className="text-[11px] text-foreground break-all select-all">
                    {window.location.origin}/v1/chat/completions
                  </code>
                  <p className="text-[10px] text-muted-foreground mt-1">
                    Base URL for OpenAI clients:{" "}
                    <code className="bg-muted px-1 rounded select-all">
                      {window.location.origin}/v1
                    </code>
                  </p>
                  <p className="text-[10px] text-muted-foreground mt-1">
                    This is derived from the address you used to reach this UI.
                    If you access LifeQuery from a different machine, use that
                    same address.
                  </p>
                </div>
              </SettingField>
            </div>
          </section>

          {/* Advanced System Parameters */}
          <details className="space-y-4 pt-4 border-t border-border/50">
            <summary className="text-sm font-mono uppercase tracking-wider text-muted-foreground cursor-pointer hover:text-foreground flex items-center gap-2">
              <SlidersHorizontal className="w-4 h-4 text-primary" />
              <span>▼ Advanced System Parameters</span>
            </summary>

            <div className="bg-card border border-border rounded-lg p-5 space-y-8 mt-4">
              <div className="space-y-4">
                <h4 className="text-xs font-bold uppercase tracking-tight text-primary/80 border-b border-border/50 pb-1">
                  Hardware Presets
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {hardwarePresets.map((preset) => (
                    <button
                      key={preset.id}
                      onClick={() =>
                        setLocalSettings((prev) => ({
                          ...prev,
                          ...preset.settings,
                        }))
                      }
                      className="flex flex-col items-center p-3 rounded-lg border border-border hover:border-primary/50 hover:bg-primary/5 transition-all text-center group"
                    >
                      <span className="text-sm font-bold text-foreground group-hover:text-primary">
                        {preset.label}
                      </span>
                      <span className="text-[10px] text-muted-foreground mt-1">
                        {preset.desc}
                      </span>
                    </button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground leading-relaxed italic">
                  Selecting a preset will instantly update the RAG, Chunking,
                  and Max Tokens parameters below. Temperature remains in
                  Assistant Behavior.
                </p>
              </div>

              <div className="space-y-4">
                <h4 className="text-xs font-bold uppercase tracking-tight text-primary/80 border-b border-border/50 pb-1">
                  RAG Retrieval
                </h4>
                <p className="text-[10px] text-muted-foreground leading-relaxed italic">
                  Tweak these to control how many memories are pulled and the
                  size of the conversation window.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <SettingField
                    label={`Top K Results: ${localSettings.top_k ?? settings.top_k}`}
                  >
                    <input
                      type="range"
                      min={1}
                      max={20}
                      step={1}
                      value={localSettings.top_k ?? settings.top_k}
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          top_k: parseInt(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <div className="flex justify-between text-[10px] text-muted-foreground font-mono mt-1">
                      <span>1</span>
                      <span>20</span>
                    </div>
                  </SettingField>
                  <SettingField
                    label={`Context Cap: ${(localSettings.context_cap ?? settings.context_cap).toLocaleString()} tokens`}
                  >
                    <input
                      type="range"
                      min={2000}
                      max={128000}
                      step={1000}
                      value={localSettings.context_cap ?? settings.context_cap}
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          context_cap: parseInt(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <div className="flex justify-between text-[10px] text-muted-foreground font-mono mt-1">
                      <span>2k</span>
                      <span>128k</span>
                    </div>
                  </SettingField>
                </div>
              </div>

              <div className="space-y-4">
                <h4 className="text-xs font-bold uppercase tracking-tight text-primary/80 border-b border-border/50 pb-1">
                  Data Chunking
                </h4>
                <p className="text-[10px] text-warning/80 italic leading-snug">
                  Important: Changing chunking values requires a **Full Reindex**
                  in the Data tab to take effect on existing messages.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <SettingField
                    label={`Target: ${localSettings.chunk_target ?? settings.chunk_target}`}
                  >
                    <input
                      type="range"
                      min={300}
                      max={1200}
                      step={50}
                      value={localSettings.chunk_target ?? settings.chunk_target}
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          chunk_target: parseInt(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <div className="flex justify-between text-[10px] text-muted-foreground font-mono mt-1">
                      <span>300</span>
                      <span>1.2k</span>
                    </div>
                  </SettingField>
                  <SettingField
                    label={`Max: ${localSettings.chunk_max ?? settings.chunk_max}`}
                  >
                    <input
                      type="range"
                      min={800}
                      max={2000}
                      step={100}
                      value={localSettings.chunk_max ?? settings.chunk_max}
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          chunk_max: parseInt(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <div className="flex justify-between text-[10px] text-muted-foreground font-mono mt-1">
                      <span>800</span>
                      <span>2k</span>
                    </div>
                  </SettingField>
                  <SettingField
                    label={`Overlap: ${localSettings.chunk_overlap ?? settings.chunk_overlap}`}
                  >
                    <input
                      type="range"
                      min={0}
                      max={300}
                      step={25}
                      value={localSettings.chunk_overlap ?? settings.chunk_overlap}
                      onChange={(e) =>
                        setLocalSettings({
                          ...localSettings,
                          chunk_overlap: parseInt(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <div className="flex justify-between text-[10px] text-muted-foreground font-mono mt-1">
                      <span>0</span>
                      <span>300</span>
                    </div>
                  </SettingField>
                </div>
              </div>
            </div>
          </details>

          {/* Save Configuration */}
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full bg-primary text-primary-foreground rounded-lg py-3 text-sm font-medium hover:opacity-90 transition-opacity glow-sm disabled:opacity-40 flex items-center justify-center gap-2"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Saving...
              </>
            ) : saveSuccess ? (
              <>✓ Configuration Saved</>
            ) : (
              "Save Configuration"
            )}
          </button>
        </>
      )}

      {/* Telegram settings tab */}
      {activeTab === "telegram" && settings && (
        <TelegramAuthTab
          settings={settings}
          localSettings={localSettings}
          setLocalSettings={setLocalSettings}
          handleSave={handleSave}
          saving={saving}
          saveSuccess={saveSuccess}
          telegramStatus={telegramStatus}
          setTelegramStatus={setTelegramStatus}
        />
      )}
    </div>
  );
}

/* ---- Shared sub-components ---- */

function ModelSelector({
  models,
  loading,
  error,
  value,
  onChange,
  onRefresh,
  placeholder,
  allowManualEntry = false,
}: {
  models: string[];
  loading: boolean;
  error: string | null;
  value: string;
  onChange: (v: string) => void;
  onRefresh: (params?: any) => void;
  placeholder: string;
  allowManualEntry?: boolean;
}) {
  const [isManual, setIsManual] = useState(
    allowManualEntry && !models.includes(value) && value !== "",
  );

  // Auto-switch to list mode if models are found and user hasn't typed anything yet
  useEffect(() => {
    if (models.length > 0 && !value && isManual) {
      setIsManual(false);
    }
  }, [models.length, value, isManual]);

  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <div className="relative flex-1">
          {isManual ? (
            <input
              value={value}
              onChange={(e) => onChange(e.target.value)}
              placeholder="Type model name..."
              className="input-field"
            />
          ) : (
            <>
              <select
                value={value}
                onChange={(e) => {
                  if (e.target.value === "__manual__") {
                    setIsManual(true);
                    onChange("");
                  } else {
                    onChange(e.target.value);
                  }
                }}
                disabled={loading}
                className="input-field appearance-none pr-8"
              >
                <option value="" disabled>
                  {placeholder}
                </option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
                {allowManualEntry && (
                  <option value="__manual__">
                    + Enter model name manually...
                  </option>
                )}
                {!models.includes(value) && value && (
                  <option value={value}>{value}</option>
                )}
              </select>
              <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
            </>
          )}
        </div>

        <div className="flex gap-1">
          {allowManualEntry && (
            <button
              onClick={() => setIsManual(!isManual)}
              className={`flex-shrink-0 w-9 h-9 rounded-md border flex items-center justify-center transition-colors ${isManual
                ? "bg-primary/10 border-primary/30 text-primary"
                : "bg-secondary border-border text-muted-foreground hover:bg-muted"
                }`}
              title={
                isManual ? "Switch to selection list" : "Type name manually"
              }
            >
              <Server className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={() => onRefresh()}
            disabled={loading}
            className="flex-shrink-0 w-9 h-9 rounded-md bg-secondary border border-border flex items-center justify-center hover:bg-muted transition-colors disabled:opacity-40"
            title="Refresh models"
          >
            {loading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-destructive font-mono">{error}</p>}
      {!error && !isManual && models.length > 0 && (
        <p className="text-xs text-muted-foreground font-mono">
          {models.length} models available
        </p>
      )}
      {isManual && (
        <p className="text-[10px] text-muted-foreground italic">
          Manual entry mode active - type exactly as instructed by provider.
        </p>
      )}
    </div>
  );
}
